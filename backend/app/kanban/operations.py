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
import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update

from app.kanban.hlc import HLC, hlc_max
from app.kanban.models import (
    KanbanAttachment,
    KanbanCard,
    KanbanDeliverable,
    KanbanGate,
    KanbanMeta,
    KanbanOp,
)

logger = logging.getLogger(__name__)


# Column transitions that end the agent session. Both Done (work shipped)
# and Impediment (work blocked on a human, session ends per
# mcp_server.report_impediment) trigger the same kill-the-tmux-and-cleanup
# pipeline. See kanban card 28b578ba for the Impediment gap. A bare
# `release` op without a column change is intentionally NOT here — that
# would silently kill in-flight work on every user-typed release from the
# UI.
#
# `Awaiting Subtasks` is also here even though it's not a card-lifecycle
# terminal state: a parent with children is redirected there ON its Done
# move (mcp_server.move_card, docs/cockpit/analyse-levenscyclus-decision.md
# §3) — the analyst/executor session that called move_card(Done) is still
# exiting at that moment and needs its tmux session killed + worktree
# removed + claim released exactly like a real Done, or every decomposed
# parent leaks a live session. The later Awaiting Subtasks → Done auto-close
# (service.close_parent_if_all_children_done) has old_column already in
# this set, so cleanup correctly does NOT fire a second time — there's no
# live session left on a parked card.
_TERMINAL_CLEANUP_COLUMNS = frozenset({"Done", "Impediment", "Awaiting Subtasks"})

# Circuit breaker for claim->release churn: a card that gets claimed and
# released this many times in a row *without* ever landing on Done/Impediment
# is auto-flagged (moved to Impediment) instead of being handed back to
# auto-dispatch forever. Distinct from dispatch.MAX_DISPATCH_FAILURES, which
# only catches dead/crashed spawns — this catches sessions that ran, did
# something, and released cleanly without finishing the card (kanban card
# a70a9272: six claim/release cycles, zero terminal moves, dispatch_failures
# stayed 0 throughout).
MAX_RELEASE_WITHOUT_TERMINAL_MOVE = 2


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


async def attach_plan(
    session, *, parent_card_id: str, plan_markdown: str,
    child_card_ids: list[str],
    depends_on_graph: dict[str, list[str]] | None = None,
) -> tuple[str, dict[str, str]]:
    """Persist a plan on a parent card and wire a ``plan_ref`` to each child.

    The compound op sequence behind ``add_plan_attachment``: one
    ``add_plan_attachment`` op on the parent, then one ``link_plan_ref`` op per
    child. Validation (parent/child existence, parent_mismatch, cycle
    detection, child cap) stays with the caller — this helper only writes, and
    the caller commits.

    Wiring the ``plan_ref`` is not optional for a card that carries a
    ``parent_card_id``: ``dispatch._awaiting_plan_ref`` holds such a child out
    of dispatch until the deliverable exists, and that hold is silent (the card
    looks unclaimed and unstarted but never dispatches). Any code path that
    creates a child card must therefore also call this.

    Returns ``(plan_deliverable_id, {child_card_id: plan_ref_deliverable_id})``.

    The MCP tool (``mcp_server.add_plan_attachment``) and the REST mirror
    (``api/v1/kanban/router.add_plan_attachment``) still carry their own copies
    of this sequence with transport-specific error shapes; they can migrate
    onto this helper without behaviour change.
    """
    deps = depends_on_graph or {}
    parent = await session.get(KanbanCard, parent_card_id)
    project_key = parent.project_key if parent is not None else ""
    await apply_operation(
        session, op_type="add_plan_attachment", entity_type="deliverable",
        project_key=project_key, entity_id=parent_card_id,
        payload={"plan_markdown": plan_markdown},
    )
    plan_deliverable_id = (await session.execute(
        select(KanbanDeliverable)
        .where(KanbanDeliverable.card_id == parent_card_id,
               KanbanDeliverable.kind == "plan")
        .order_by(KanbanDeliverable.created_at.desc())
    )).scalars().first().id

    plan_refs: dict[str, str] = {}
    for child_id in child_card_ids:
        await apply_operation(
            session, op_type="link_plan_ref", entity_type="deliverable",
            project_key=project_key, entity_id=child_id,
            payload={"ref_json": json.dumps({
                "parent_card_id": parent_card_id,
                "plan_deliverable_id": plan_deliverable_id,
            }), "depends_on": list(deps.get(child_id, []) or [])},
        )
        plan_refs[child_id] = (await session.execute(
            select(KanbanDeliverable)
            .where(KanbanDeliverable.card_id == child_id,
                   KanbanDeliverable.kind == "plan_ref")
            .order_by(KanbanDeliverable.created_at.desc())
        )).scalars().first().id
    return plan_deliverable_id, plan_refs


async def release_card_claim(session, *, card_id: str, project_key: str) -> None:
    """Release a claim and track claim->release churn (kanban card 49626139).

    Only for the *bare* release entry points — the `release_card` MCP tool
    and the REST release endpoint — where a session or a human released the
    claim with no accompanying column change. Those are exactly the calls a
    correctly-behaving agent should never make (personas call `move_card` to
    Done/Impediment, or `report_impediment`, both of which change the column
    before/without a separate bare release); a repeat of this bare pattern is
    the a70a9272 churn signature: claimed, released, no progress, no crash.

    Deliberately NOT wired into every `apply_operation(op_type="release")`
    call site: dispatch.py's own release calls (dead-claim reaper, stuck-session
    reaper, redispatch, pause-to-"To Resume") already have their own circuit
    breaker (`dispatch_failures` / MAX_DISPATCH_FAILURES) or represent a
    legitimate multi-session continuation — counting them here too would trip
    this breaker earlier than (and in conflict with) the existing one.
    """
    await apply_operation(session, op_type="release", entity_type="card",
        project_key=project_key, entity_id=card_id, payload={})

    card = await session.get(KanbanCard, card_id)
    if card is None or card.column in _TERMINAL_CLEANUP_COLUMNS:
        return
    card.release_without_terminal_move = (card.release_without_terminal_move or 0) + 1
    await session.flush()
    churn = card.release_without_terminal_move
    if churn < MAX_RELEASE_WITHOUT_TERMINAL_MOVE:
        return

    await apply_operation(
        session, op_type="move", entity_type="card",
        project_key=project_key, entity_id=card_id, payload={"column": "Impediment"},
    )
    await apply_operation(
        session, op_type="comment", entity_type="comment",
        project_key=project_key, entity_id=card_id,
        payload={"text": (
            f"**Impediment:** Auto-flagged after {churn} consecutive "
            "claim->release cycles with no terminal move (Done/Impediment) — "
            "looks like a churn loop, not real progress. Needs human triage "
            "before this is dispatched again."
        )},
    )
    logger.warning(
        "card %s auto-flagged to Impediment after %d claim/release cycles "
        "without a terminal move", card_id, churn,
    )


def _cleanup_after_commit(session, card_id: str, project_key: str,
                          claimed_by: str | None = None) -> None:
    """Fire terminal-column session cleanup only once the move is committed.

    The cleanup kills the tmux session hosting the MCP client that issued this
    very move. Firing it here-and-now (pre-commit) let that kill race the
    caller's own `await ... commit()`: the client died, its in-flight request
    task was cancelled, and the transaction rolled back — so the tmux session
    was gone but the card never left its agent column. The dispatcher re-claimed
    the still-claimed card and respawned it, forever (card a70a9272 burned 26
    cycles this way; the move op appears in the log but never in the DB).

    Deferring to `after_commit` keeps the ordering that made the old code look
    correct — cleanup still happens, and only for a move that actually landed.

    `claimed_by` is captured here (at schedule time, before the synchronous
    claim-clear below wipes it) and threaded through to the cleanup, so a
    Done→non-Done terminal move (`Impediment`/`Awaiting Subtasks`) no longer
    reads `claimed_by=None` at fire time and silently skips the tmux-kill /
    worktree-remove (kanban card 7b63463e).
    """
    from sqlalchemy import event

    from app.kanban.session_cleanup import on_card_moved_to_done

    loop = asyncio.get_running_loop()
    sync_session = getattr(session, "sync_session", session)

    @event.listens_for(sync_session, "after_commit", once=True)
    def _fire(_sess) -> None:  # pragma: no cover - thin scheduling shim
        # `after_commit` runs in SQLAlchemy's greenlet; hop back onto the loop
        # before touching asyncio so cleanup scheduling never depends on
        # whether a running loop is visible from that context.
        loop.call_soon(on_card_moved_to_done, card_id, project_key, claimed_by)


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
            # kaart 27317b4871… (FCR gap 3): the planning pipeline
            # (analyst → executor split) emits ``column_overrides``
            # payloads that don't pass through the ``CardCreate``
            # pydantic validator. Validate here so a misconfigured
            # override can't sneak through the planning side after the
            # REST surface is closed. ``_validate_column_overrides_value``
            # raises ``ValueError`` which the caller surfaces as the
            # same 422-style failure the REST path produces.
            column_overrides_raw = payload.get("column_overrides")
            from app.kanban.schemas import (
                _validate_column_overrides_value,
            )
            column_overrides = _validate_column_overrides_value(
                column_overrides_raw,
            )
            session.add(KanbanCard(
                id=entity_id, project_key=project_key,
                title=payload.get("title", ""),
                description=payload.get("description", ""),
                column=payload.get("column", "Backlog"),
                rank=payload.get("rank", hlc),
                priority=payload.get("priority"), labels=payload.get("labels"),
                work_type=payload.get("work_type"),
                agent=payload.get("agent"),
                model=payload.get("model"),
                column_overrides=column_overrides,
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
            # When card moves to a session-ending terminal column, schedule
            # session cleanup. Done already triggered this — Impediment did
            # not, so report_impediment sessions stayed alive after the move
            # (kanban card 28b578ba). Both transitions mean "the session ends
            # here" per dispatch._build_ship_instructions and
            # mcp_server.report_impediment's explicit "session ends here"
            # contract. Only column transitions trigger this — a bare `release`
            # op on an agent column is a separate decision and must not, or
            # every user-typed release would silently kill in-flight work.
            new_column = payload.get("column")
            if (new_column in _TERMINAL_CLEANUP_COLUMNS
                    and old_column not in _TERMINAL_CLEANUP_COLUMNS):
                # Capture claimed_by now — the synchronous claim-clear below
                # wipes it in this same transaction, so a fresh read at fire
                # time would lose the session name (kanban card 7b63463e).
                _cleanup_after_commit(session, entity_id, project_key,
                                      card.claimed_by)
            # A terminal move means the card actually finished this round —
            # forgive whatever claim/release churn preceded it so a later
            # reopen starts the circuit breaker fresh.
            if new_column in _TERMINAL_CLEANUP_COLUMNS and card.release_without_terminal_move:
                card.release_without_terminal_move = 0
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
            for f in ("priority", "labels", "work_type", "agent", "model",
                      "transport",
                      "resume_session_id", "resume_project_folder", "scheduled_at",
                      "dispatch_failures",
                      "dispatch_started_at", "dispatch_session_id",
                      "pending_spawn_session",
                      "dispatch_project_folder", "dispatch_model",
                      "dispatch_provider",
                      "analyst_agent_id", "executor_agent_id", "parent_card_id",
                      "analyst_run_id", "depends_on"):
                if f in payload:
                    setattr(card, f, payload[f])
            # kaart 27317b4871… (FCR gap 3): validate
            # ``column_overrides`` on the op-log update path too, so
            # the planning pipeline + the LWW op-log replay both reject
            # anthropic-compatible carriers that lack ``endpoint_name``
            # (the REST surface already enforces this through the
            # ``CardUpdate`` pydantic validator; this is the parallel
            # defence for the in-process emitter).
            if "column_overrides" in payload:
                from app.kanban.schemas import (
                    _validate_column_overrides_value,
                )
                card.column_overrides = _validate_column_overrides_value(
                    payload["column_overrides"],
                )
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
            await session.execute(
                delete(KanbanAttachment).where(KanbanAttachment.card_id == entity_id)
            )
            await session.execute(
                delete(KanbanGate).where(KanbanGate.card_id == entity_id)
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
    if entity_type == "deliverable" and op_type == "update_plan_attachment":
        # Overwrite the markdown on the most recent `kind=plan` deliverable
        # for this card. The router checks that a plan exists before issuing
        # this op; if none is found here (raced delete, or HLC replay with a
        # missing prior op), the rowcount is 0 and we leave the state alone.
        # Single UPDATE keeps the deliverable row id stable so child
        # `plan_ref` rows still resolve to the same id on the next dispatch.
        result = await session.execute(
            update(KanbanDeliverable)
            .where(KanbanDeliverable.card_id == entity_id)
            .where(KanbanDeliverable.kind == "plan")
            .where(KanbanDeliverable.id == (
                select(KanbanDeliverable.id)
                .where(KanbanDeliverable.card_id == entity_id)
                .where(KanbanDeliverable.kind == "plan")
                .order_by(KanbanDeliverable.created_at.desc())
                .limit(1)
                .scalar_subquery()
            ))
            .values(ref=payload["plan_markdown"])
        )
        if result.rowcount == 0:
            logger.warning(
                "update_plan_attachment: no plan deliverable for card %s "
                "(race or replay with missing prior op?)", entity_id,
            )
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
    if entity_type == "attachment" and op_type == "attach":
        session.add(KanbanAttachment(
            id=payload["id"], card_id=entity_id,
            filename=payload.get("filename") or "",
            mime_type=payload.get("mime_type") or "",
            size_bytes=payload.get("size_bytes") or 0,
            storage_path=payload["storage_path"],
        ))
        await session.flush()
        return
    if entity_type == "attachment" and op_type == "detach":
        await session.execute(
            delete(KanbanAttachment)
            .where(KanbanAttachment.card_id == entity_id)
            .where(KanbanAttachment.id == payload["id"])
        )
        return
    # comment ops are pure log entries; nothing to materialize.


async def rematerialize(session) -> None:
    """Rebuild materialized tables by replaying the op-log in HLC order.
    Safe to run anytime; also the basis for sync replay. ClaimRejected is
    swallowed here so an already-owned card keeps its first claimant.
    """
    from sqlalchemy import delete
    await session.execute(delete(KanbanAttachment))
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
