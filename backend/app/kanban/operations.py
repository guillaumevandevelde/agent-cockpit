"""Single mutation pipeline + materialization.

apply_operation(): assign HLC -> append KanbanOp -> update materialized state.
All writes (REST and MCP) go through here. rematerialize() rebuilds the
materialized tables from the op-log (added in Task E5).

The per-field LWW (_lww_set) and claim/release HLC conditionals below are the
*dormant* CRDT core. With one in-process clock + the lock, every tick dominates,
so the guards never reject a live write; they only matter under HLC-ordered replay
and, eventually, multi-device sync. Frozen on purpose — the sync seam (sync.py) was
pruned. See docs/cockpit/sync-hlc-freeze-vs-prune.md.
"""
import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update

from app.kanban.hlc import HLC, hlc_max
from app.kanban.models import KanbanCard, KanbanDeliverable, KanbanMeta, KanbanOp

logger = logging.getLogger(__name__)


class ClaimRejected(Exception):
    """Raised when a claim loses to an existing earlier claim."""
    def __init__(self, current_owner: str):
        self.current_owner = current_owner
        super().__init__(f"already claimed by {current_owner}")


# One in-process clock per backend. node_id is bound lazily to the device_id.
# The lock serializes clock acquisition + tick so concurrent requests (UI + an
# MCP agent, or two agents) get distinct, monotonic HLCs.
_clock: HLC | None = None
_clock_lock = asyncio.Lock()


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def get_device_id(session) -> str:
    row = await session.get(KanbanMeta, "device_id")
    if row is None:
        row = KanbanMeta(key="device_id", value=uuid.uuid4().hex[:12])
        session.add(row)
        await session.flush()
    return row.value


async def _clock_for(session) -> HLC:
    global _clock
    device_id = await get_device_id(session)
    if _clock is None or _clock.node_id != device_id:
        _clock = HLC(node_id=device_id)
        # Seed past the highest HLC already stored so restarts stay monotonic.
        highest = (await session.execute(select(func.max(KanbanOp.hlc)))).scalar()
        if highest:
            _clock.update(highest)
    return _clock


async def _next_seq(session, device_id: str) -> int:
    n = (await session.execute(
        select(func.count()).select_from(KanbanOp).where(KanbanOp.device_id == device_id)
    )).scalar() or 0
    return n + 1


async def apply_operation(
    session, *, op_type: str, entity_type: str, project_key: str,
    entity_id: str | None, payload: dict,
) -> str:
    """Append an op and fold it into materialized state. Returns entity_id."""
    async with _clock_lock:
        clock = await _clock_for(session)
        device_id = await get_device_id(session)
        hlc = clock.tick()
    entity_id = entity_id or uuid.uuid4().hex
    # Mutations arrive with an empty project_key (callers don't know it); stamp
    # the op with the owning card's key so the op-log stays self-describing and
    # per-project sync/filtering works. Creates carry their own project_key.
    if not project_key:
        owner = await session.get(KanbanCard, entity_id)
        if owner is not None:
            project_key = owner.project_key
    seq = await _next_seq(session, device_id)

    session.add(KanbanOp(
        op_id=uuid.uuid4().hex, device_id=device_id, seq=seq, hlc=hlc,
        project_key=project_key, entity_type=entity_type, entity_id=entity_id,
        op_type=op_type, payload=payload,
    ))
    await session.flush()
    await _materialize(session, op_type=op_type, entity_type=entity_type,
                       project_key=project_key, entity_id=entity_id,
                       payload=payload, hlc=hlc)
    logger.info(
        "kanban op: %s %s %s (project=%s, payload_keys=%s)",
        op_type, entity_type, entity_id, project_key, sorted(payload.keys()),
    )
    return entity_id


def _lww_set(card, field: str, value, hlc: str) -> None:
    """Apply value to card.<field> only if hlc beats the field's current hlc."""
    hlc_attr = f"{field}_hlc"
    current = getattr(card, hlc_attr)
    if hlc_max(current, hlc) == hlc:
        setattr(card, field, value)
        setattr(card, hlc_attr, hlc)


async def _materialize(session, *, op_type, entity_type, project_key,
                       entity_id, payload, hlc) -> None:
    if entity_type == "card" and op_type == "create":
        existing = await session.get(KanbanCard, entity_id)
        if existing is None:  # idempotent: re-applying create is a no-op
            session.add(KanbanCard(
                id=entity_id, project_key=project_key,
                title=payload.get("title", ""),
                description=payload.get("description", ""),
                column=payload.get("column", "Backlog"),
                rank=payload.get("rank", hlc),
                priority=payload.get("priority"), labels=payload.get("labels"),
                work_type=payload.get("work_type"),
                agent=payload.get("agent"),
                transport=payload.get("transport"),
                resume_session_id=payload.get("resume_session_id"),
                resume_project_folder=payload.get("resume_project_folder"),
                scheduled_at=payload.get("scheduled_at"),
                analyst_agent_id=payload.get("analyst_agent_id"),
                executor_agent_id=payload.get("executor_agent_id"),
                parent_card_id=payload.get("parent_card_id"),
                analyst_run_id=payload.get("analyst_run_id"),
                depends_on=payload.get("depends_on"),
                # ORM attribute is `meta` (SQLAlchemy reserves `metadata`); the
                # API/JSON contract and the DB column are both `metadata`.
                meta=payload.get("metadata"),
                title_hlc=hlc, description_hlc=hlc, column_hlc=hlc, rank_hlc=hlc,
            ))
            await session.flush()
        return
    if entity_type == "card" and op_type in ("move", "update"):
        card = await session.get(KanbanCard, entity_id)
        if card is None:
            return
        if op_type == "move":
            old_column = card.column
            if "column" in payload:
                _lww_set(card, "column", payload["column"], hlc)
            if payload.get("rank") is not None:
                _lww_set(card, "rank", payload["rank"], hlc)
            # When card moves to Done, schedule session cleanup
            new_column = payload.get("column")
            if new_column == "Done" and old_column != "Done":
                from app.kanban.session_cleanup import on_card_moved_to_done
                on_card_moved_to_done(entity_id, project_key)
            # A card leaving an agent column back into a fixed one (e.g. a UI
            # drag-drop to Backlog) without an explicit release would otherwise
            # keep its `agent:` claim forever: _next_card requires unclaimed,
            # and the stale-claim reaper skips fixed columns on purpose (so a
            # human `claim_card` reservation on a Backlog card isn't disturbed).
            # That combination makes the card invisible to auto-dispatch for
            # good. Done is excluded here since session_cleanup already
            # releases the claim for that transition.
            from app.kanban.schemas import COLUMNS
            if (new_column in COLUMNS and new_column != "Done"
                    and old_column not in COLUMNS
                    and (card.claimed_by or "").startswith("agent:")):
                card.claimed_by = None
                card.claimed_at = None
                card.claim_hlc = hlc
        else:  # update
            for f in ("title", "description"):
                if f in payload and payload[f] is not None:
                    _lww_set(card, f, payload[f], hlc)
            for f in ("priority", "labels", "work_type", "agent", "transport",
                      "resume_session_id", "resume_project_folder", "scheduled_at",
                      "dispatch_failures",
                      "analyst_agent_id", "executor_agent_id", "parent_card_id",
                      "analyst_run_id", "depends_on"):
                if f in payload:
                    setattr(card, f, payload[f])
            # ORM attribute is `meta` (not `metadata` — reserved by SQLAlchemy's
            # Declarative base). The op-log and API payload both carry the
            # `metadata` key; this mapping is the one place that translates.
            if "metadata" in payload:
                card.meta = payload["metadata"]
        card.updated_at = _utcnow()
        await session.flush()
        return
    if entity_type == "card" and op_type == "claim":
        # Existence check first — preserves the existing "claim on missing card
        # is silently ignored" contract. We deliberately use a column-only SELECT
        # rather than `session.get(KanbanCard, ...)` so the session's identity
        # map is not populated with a stale snapshot that could mask concurrent
        # commits if the same session issues another claim later.
        exists = await session.scalar(
            select(KanbanCard.id).where(KanbanCard.id == entity_id)
        )
        if exists is None:
            return
        # Reject empty claimants so frontend `!c.claimed_by` and backend
        # `claimed_by.is_(None)` never diverge on the same card.
        if not payload.get("claimed_by"):
            raise ValueError("claimed_by must be a non-empty string")
        # Atomic conditional claim: a single UPDATE guarded by `claimed_by IS NULL`
        # is the only way to prevent the TOCTOU window between two concurrent
        # `claim_card` calls that each loaded the card before either committed.
        # The previous read-check-write pattern in Python was racy: a session's
        # identity-map object can hold a stale `claimed_by is None` snapshot, so
        # two claimants would both pass the in-Python check and the second
        # commit would silently overwrite the first. The rowcount check here
        # matches first-wins semantics — the existing claim keeps the card.
        now = _utcnow()
        result = await session.execute(
            update(KanbanCard)
            .where(KanbanCard.id == entity_id)
            .where(KanbanCard.claimed_by.is_(None))
            .values(
                claimed_by=payload["claimed_by"],
                claimed_at=now,
                claim_hlc=hlc,
                updated_at=now,
            )
        )
        if result.rowcount == 0:
            # Someone else already owns the card. Fetch the current owner from
            # the DB for the error message — do NOT trust the identity-map
            # object that was already loaded above (it is stale on purpose).
            current_owner = await session.scalar(
                select(KanbanCard.claimed_by).where(KanbanCard.id == entity_id)
            )
            raise ClaimRejected(current_owner or "unknown")
        return

    if entity_type == "card" and op_type == "delete":
        card = await session.get(KanbanCard, entity_id)
        if card is not None:
            logger.info(
                "deleting card %s %r (column=%s, claimed_by=%s)",
                entity_id, card.title, card.column, card.claimed_by,
            )
            await session.execute(
                delete(KanbanDeliverable).where(KanbanDeliverable.card_id == entity_id)
            )
            await session.delete(card)
            await session.flush()
        return
    if entity_type == "card" and op_type == "release":
        card = await session.get(KanbanCard, entity_id)
        if card is None:
            return
        if hlc_max(card.claim_hlc, hlc) == hlc:  # release must be newer than the claim
            card.claimed_by = None
            card.claimed_at = None
            card.claim_hlc = hlc
            card.updated_at = _utcnow()
            await session.flush()
        return
    if entity_type == "deliverable" and op_type == "attach":
        session.add(KanbanDeliverable(
            id=uuid.uuid4().hex, card_id=entity_id,
            kind=payload["kind"], ref=payload["ref"],
        ))
        await session.flush()
        return
    if entity_type == "deliverable" and op_type == "add_plan_attachment":
        session.add(KanbanDeliverable(
            id=uuid.uuid4().hex, card_id=entity_id,
            kind="plan", ref=payload["plan_markdown"],
        ))
        # Persist the JSON graph as part of the same deliverable row (we
        # already stored the markdown above; use the JSON column on KanbanCard
        # to mark on each child via the follow-up ops).
        await session.flush()
        return
    if entity_type == "deliverable" and op_type == "link_plan_ref":
        session.add(KanbanDeliverable(
            id=uuid.uuid4().hex, card_id=entity_id,
            kind="plan_ref",
            ref=payload["ref_json"],
        ))
        # Set the per-child depends_on column for fast dispatcher reads.
        card = await session.get(KanbanCard, entity_id)
        if card is not None and "depends_on" in payload:
            card.depends_on = payload["depends_on"]
            await session.flush()
        return
    # comment ops are pure log entries; nothing to materialize.


async def rematerialize(session) -> None:
    """Rebuild materialized tables by replaying the op-log in HLC order.
    Safe to run anytime; also the basis for sync replay. ClaimRejected is
    swallowed here so an already-owned card keeps its first claimant.
    """
    from sqlalchemy import delete
    await session.execute(delete(KanbanDeliverable))
    await session.execute(delete(KanbanCard))
    await session.flush()
    ops = (await session.execute(
        select(KanbanOp).order_by(KanbanOp.hlc.asc())
    )).scalars().all()
    for op in ops:
        try:
            await _materialize(
                session, op_type=op.op_type, entity_type=op.entity_type,
                project_key=op.project_key, entity_id=op.entity_id,
                payload=op.payload, hlc=op.hlc,
            )
        except ClaimRejected:
            pass
