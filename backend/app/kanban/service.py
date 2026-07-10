"""Read-side queries over the materialized state + op-log activity feed."""
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import selectinload

from app.kanban.dep_resolver import meets_dep_prerequisites
from app.kanban.models import (
    KanbanCard,
    KanbanColumn,
    KanbanGate,
    KanbanOp,
    KanbanWorkTypeMapping,
)


async def list_cards(
    session,
    project_key: str,
    column: str | None = None,
    *,
    ready: bool | None = None,
    blocking: bool | None = None,
):
    """List cards for a project.

    `ready`/`blocking` are independent opt-in filters; both False/Nil means
    "no filter" (preserving the original behaviour for every existing caller).
    They compose as an intersection when both are set.

    `ready` mirrors the dispatcher's own dep check
    (`app.kanban.dep_resolver.meets_dep_prerequisites`) so the API and the
    dispatch tick agree on what is dispatchable. `blocking` answers "which
    cards are still being waited on?" — a card X is blocking when some other
    non-Done card lists X in its `depends_on`.
    """
    stmt = (
        select(KanbanCard)
        .where(KanbanCard.project_key == project_key)
        .options(selectinload(KanbanCard.deliverables))
        .order_by(KanbanCard.rank.asc())
    )
    if column is not None:
        stmt = stmt.where(KanbanCard.column == column)
    rows = list((await session.execute(stmt)).scalars().all())
    if ready is None and blocking is None:
        return rows
    cards_by_id = {c.id: c for c in rows}
    if blocking is True:
        blocking_ids = _blocking_card_ids(rows)
    else:
        blocking_ids = None
    return [
        c for c in rows
        if (ready is None or ready is meets_dep_prerequisites(c, cards_by_id))
        and (blocking is None or blocking is (c.id in (blocking_ids or set())))
    ]


def _blocking_card_ids(cards: list[KanbanCard]) -> set[str]:
    """Card ids that have at least one non-Done dependent in `cards`.

    Self-resolved against the passed-in list rather than a fresh DB read so
    the caller's `ready`/`blocking` filters both see the same snapshot — a
    freshly-Done parent has no in-flight children, so re-reading the DB would
    see an out-of-date view for the rest of this tick."""
    blocking: set[str] = set()
    for card in cards:
        if card.column == "Done":
            # A finished card no longer blocks anything; skip.
            continue
        for parent_id in card.depends_on or ():
            blocking.add(parent_id)
    return blocking


async def get_card(session, card_id: str):
    stmt = (
        select(KanbanCard)
        .where(KanbanCard.id == card_id)
        .options(selectinload(KanbanCard.deliverables))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def card_activity(session, card_id: str):
    stmt = (
        select(KanbanOp)
        .where(KanbanOp.entity_id == card_id)
        .order_by(KanbanOp.hlc.asc())
    )
    return (await session.execute(stmt)).scalars().all()


# Prefix used by mcp_server.move_card when a card lands on Done — see
# mcp_server._SUMMARY_REQUIRED_COLUMNS ("Done": "Summary"). The Done comment
# is posted as `**Summary:** <text>`; matching on the literal label keeps
# this enrichment decoupled from column state (a card moved back to Backlog
# still surfaces its summary) and from `**Impediment:**` comments (which
# belong to the Impediment column and are not a "done" event).
_DONE_SUMMARY_PREFIX = "**Summary:** "


async def enrich_done_info(session, card_id: str) -> tuple[str | None, datetime | None]:
    """Return the summary text + timestamp of the most recent
    `**Summary:** ...` comment op on this card, or (None, None).

    The enrichment is request-time (not materialized) so a card can move
    between columns without the summary field going stale. The op-log
    stays the source of truth, and a rematerialize rebuild reproduces the
    same answer because the comment op itself is what carries the text.
    """
    stmt = (
        select(KanbanOp)
        .where(KanbanOp.entity_id == card_id)
        .where(KanbanOp.op_type == "comment")
        .where(KanbanOp.payload["text"].as_string().like(f"{_DONE_SUMMARY_PREFIX}%"))
        .order_by(KanbanOp.hlc.desc())
        .limit(1)
    )
    op = (await session.execute(stmt)).scalar_one_or_none()
    if op is None:
        return None, None
    text = op.payload.get("text") or ""
    return text[len(_DONE_SUMMARY_PREFIX):], op.created_at


async def list_project_ops(session, project_key: str):
    """All op-log entries for a project's cards. Ops carry project_key="" for
    move/claim/comment (set by the router), so we join by card id instead."""
    cards = await list_cards(session, project_key)
    ids = [c.id for c in cards]
    if not ids:
        return cards, []
    stmt = select(KanbanOp).where(KanbanOp.entity_id.in_(ids))
    ops = (await session.execute(stmt)).scalars().all()
    return cards, ops


# Column management


async def list_columns(session, project_key: str):
    stmt = (
        select(KanbanColumn)
        .where(KanbanColumn.project_key == project_key)
        .order_by(KanbanColumn.rank.asc())
    )
    return (await session.execute(stmt)).scalars().all()


async def get_column(session, column_id: str):
    return await session.get(KanbanColumn, column_id)


async def create_column(session, project_key: str, name: str,
                        rank: str | None = None, default_agent: str | None = None,
                        default_platform: str | None = None,
                        max_sessions: int | None = None):
    col = KanbanColumn(
        id=uuid.uuid4().hex,
        project_key=project_key,
        name=name,
        rank=rank or uuid.uuid4().hex,
        default_agent=default_agent,
        default_platform=default_platform,
        max_sessions=max_sessions,
    )
    session.add(col)
    await session.flush()
    return col


async def update_column(session, column_id: str, **kwargs):
    col = await session.get(KanbanColumn, column_id)
    if col is None:
        return None
    for k, v in kwargs.items():
        if v is not None:
            setattr(col, k, v)
    col.updated_at = datetime.now(UTC)
    await session.flush()
    return col


async def delete_column(session, column_id: str) -> bool:
    col = await session.get(KanbanColumn, column_id)
    if col is None:
        return False
    await session.delete(col)
    await session.flush()
    return True


async def get_column_default_agent(session, project_key: str, column_name: str) -> str | None:
    """Look up the default agent for a column name within a project."""
    stmt = (
        select(KanbanColumn)
        .where(KanbanColumn.project_key == project_key)
        .where(KanbanColumn.name == column_name)
    )
    col = (await session.execute(stmt)).scalar_one_or_none()
    return col.default_agent if col else None


async def get_column_default_platform(session, project_key: str, column_name: str) -> str | None:
    """Look up the default platform (anthropic | bedrock | minimax) for a column
    name within a project. None means no override — the dispatcher falls back to
    the Anthropic subscription."""
    stmt = (
        select(KanbanColumn)
        .where(KanbanColumn.project_key == project_key)
        .where(KanbanColumn.name == column_name)
    )
    col = (await session.execute(stmt)).scalar_one_or_none()
    return col.default_platform if col else None


async def list_pending_cards(session, project_key: str) -> list[KanbanCard]:
    """Unclaimed cards in Backlog or To Resume — candidates for dispatch.

    To Resume cards carry a `resume_session_id` set by the limit-recovery path,
    so dispatching them goes through the resume transport (`get_transport_for_card`)
    instead of spawning a fresh session.
    """
    stmt = (
        select(KanbanCard)
        .where(KanbanCard.project_key == project_key)
        .where(KanbanCard.column.in_(("Backlog", "To Resume")))
        .where(KanbanCard.claimed_by.is_(None))
        .options(selectinload(KanbanCard.deliverables))
        .order_by(KanbanCard.rank.asc())
    )
    return (await session.execute(stmt)).scalars().all()


async def list_orphaned_cards(session, project_key: str) -> list[KanbanCard]:
    """Cards on agent columns (not Backlog/Dispatch/Impediment/Done) that are unclaimed."""
    from app.kanban.schemas import COLUMNS
    stmt = (
        select(KanbanCard)
        .where(KanbanCard.project_key == project_key)
        .where(KanbanCard.claimed_by.is_(None))
        .where(~KanbanCard.column.in_(COLUMNS))
        .options(selectinload(KanbanCard.deliverables))
        .order_by(KanbanCard.rank.asc())
    )
    return (await session.execute(stmt)).scalars().all()


async def sync_agent_columns(session, project_key: str, agents: list[str]) -> None:
    """Sync agent columns with the list of agents for a project.

    Creates columns for agents that don't have one yet.
    Does not remove columns for agents that are no longer in the list
    (to preserve card references).
    """
    from app.kanban.schemas import COLUMNS

    existing = await list_columns(session, project_key)
    existing_names = {c.name for c in existing}

    # Find the rank of "Done" column to insert agent columns before it
    done_rank = "9999"
    for col in existing:
        if col.name == "Done":
            done_rank = col.rank
            break

    # Create columns for agents that don't have one yet
    for i, agent_name in enumerate(agents):
        if agent_name not in existing_names:
            # Insert before Done: rank between last fixed column and Done
            rank = f"{int(done_rank) - len(agents) + i:04d}" if done_rank != "9999" else f"0{len(COLUMNS) + i:03d}"
            await create_column(session, project_key, name=agent_name, rank=rank, default_agent=agent_name)

    await session.flush()


async def ensure_analyst_column(session, project_key: str) -> bool:
    """Idempotent: create the 'analyst' kanban_columns row for this project
    if one doesn't already exist. Returns True iff a new column was created.

    Called from PATCH /cards/{cid} when analyst_agent_id is set, so the
    dispatcher can move a multi-agent card to the analyst column AND the
    UI renders the column immediately. Without this, the card lands in a
    phantom column (string set on the card but no kanban_columns row) that
    doesn't show up in the board.
    """
    existing = await list_columns(session, project_key)
    if any(c.name == "analyst" for c in existing):
        return False
    # Same rank policy as sync_agent_columns: insert before Done.
    from app.kanban.schemas import COLUMNS
    done_rank = "9999"
    for col in existing:
        if col.name == "Done":
            done_rank = col.rank
            break
    rank = f"{int(done_rank) - 1:04d}" if done_rank != "9999" else f"0{len(COLUMNS):03d}"
    await create_column(session, project_key, name="analyst",
                       rank=rank, default_agent="analyst")
    await session.flush()
    return True


# Decision gates


async def create_gate(session, card_id: str, project_key: str,
                      question: str, options: list[str]) -> KanbanGate:
    gate = KanbanGate(
        id=uuid.uuid4().hex,
        card_id=card_id,
        project_key=project_key,
        question=question,
        options=options,
        status="open",
    )
    session.add(gate)
    await session.flush()
    return gate


async def get_gate(session, gate_id: str) -> KanbanGate | None:
    return await session.get(KanbanGate, gate_id)


async def list_gates(session, card_id: str) -> list[KanbanGate]:
    stmt = (
        select(KanbanGate)
        .where(KanbanGate.card_id == card_id)
        .order_by(KanbanGate.created_at.asc())
    )
    return (await session.execute(stmt)).scalars().all()


async def answer_gate(session, gate_id: str, answer: str) -> KanbanGate | None:
    """Record the human's answer. Idempotent: answering an already-answered
    gate again is a no-op that returns the existing (first) answer, so a
    double-click in the UI can't silently overwrite what was already recorded."""
    gate = await session.get(KanbanGate, gate_id)
    if gate is None:
        return None
    if gate.status == "answered":
        return gate
    if answer not in gate.options:
        raise ValueError("answer must be one of the gate's options")
    gate.answer = answer
    gate.status = "answered"
    gate.answered_at = datetime.now(UTC)
    await session.flush()
    return gate


# Work-type → persona mapping (per-project)


async def list_work_type_mappings(session, project_key: str) -> list[KanbanWorkTypeMapping]:
    """All overrides for a project, in stable work_type order.

    Missing work_types are not returned — the caller is expected to merge
    these with `WORK_TYPE_PERSONA_DEFAULTS` for a complete picture (see
    `work_type_mapping_for_project` and the GET endpoint).
    """
    stmt = (
        select(KanbanWorkTypeMapping)
        .where(KanbanWorkTypeMapping.project_key == project_key)
        .order_by(KanbanWorkTypeMapping.work_type.asc())
    )
    return (await session.execute(stmt)).scalars().all()


async def work_type_mapping_for_project(
    session, project_key: str
) -> dict[str, str]:
    """Return a complete {work_type: persona} map for the project, merging
    stored overrides on top of `WORK_TYPE_PERSONA_DEFAULTS`. The result always
    contains every entry in `WORK_TYPES` — callers can read directly without
    handling KeyError.
    """
    from app.kanban.schemas import WORK_TYPE_PERSONA_DEFAULTS, WORK_TYPES

    merged = dict(WORK_TYPE_PERSONA_DEFAULTS)
    for row in await list_work_type_mappings(session, project_key):
        merged[row.work_type] = row.persona
    # Defensive: drop any legacy row whose work_type is no longer in WORK_TYPES
    # (the API rejects new ones), so the response is always schema-conformant.
    return {wt: merged[wt] for wt in WORK_TYPES if wt in merged}


async def resolve_create_agent(
    session, project_key: str, *,
    work_type: str | None, explicit_agent: str | None,
) -> str | None:
    """Pick the agent/persona for a newly created card.

    Explicit `agent` wins over `work_type` — it is the highest-priority
    routing hint per docs/cockpit/work-type-routing-analysis.md §2B, and the
    dispatcher already reads `card.agent` before any column-derived persona.

    Otherwise, when `work_type` is set, look up the per-project persona
    mapping (or `WORK_TYPE_PERSONA_DEFAULTS` fallback) so the very first
    dispatch lands on the right persona without the user having to fill in
    `agent` by hand.

    Returns None when neither is set: `card.agent` stays empty and the
    dispatcher's column-derived fallback decides.

    Empty/whitespace-only `explicit_agent` is treated as "not set" so a
    frontend that posts `agent: ""` (e.g. an unselected dropdown) does not
    block the work_type mapping.
    """
    if explicit_agent and explicit_agent.strip():
        # Strip so the dispatcher's persona lookup sees the bare name
        # ("engineer" matches .claude/agents/engineer.md; " engineer " does not).
        return explicit_agent.strip()
    if work_type:
        return await get_work_type_persona(session, project_key, work_type)
    return None


async def get_work_type_persona(
    session, project_key: str, work_type: str
) -> str:
    """Resolve which persona a card with `work_type` should use for this
    project. Falls back to `WORK_TYPE_PERSONA_DEFAULTS` when no override
    exists, and to a safe `"engineer"` if the work_type itself is unknown
    (e.g. a card predates the enum). Always returns a non-empty string so
    the dispatcher can rely on it without None-checks.
    """
    from app.kanban.schemas import WORK_TYPE_PERSONA_DEFAULTS

    row = (await session.execute(
        select(KanbanWorkTypeMapping)
        .where(KanbanWorkTypeMapping.project_key == project_key)
        .where(KanbanWorkTypeMapping.work_type == work_type)
    )).scalar_one_or_none()
    if row is not None:
        return row.persona
    return WORK_TYPE_PERSONA_DEFAULTS.get(work_type, "engineer")


async def upsert_work_type_mapping(
    session, project_key: str, work_type: str, persona: str
) -> KanbanWorkTypeMapping:
    """Insert or update the (project_key, work_type) row.

    Uses SQLite's ``INSERT ... ON CONFLICT DO UPDATE`` so the service is
    atomic at the DB level (no read-then-write race). The unique constraint
    is on (project_key, work_type) — see the table migration in db.py.
    """
    from app.kanban.schemas import WORK_TYPES

    if work_type not in WORK_TYPES:
        raise ValueError(
            f"work_type must be one of {WORK_TYPES}, got {work_type!r}"
        )
    if not persona or not persona.strip():
        raise ValueError("persona must be a non-empty string")
    now = datetime.now(UTC)
    new_id = uuid.uuid4().hex
    stmt = sqlite_insert(KanbanWorkTypeMapping).values(
        id=new_id, project_key=project_key, work_type=work_type,
        persona=persona, created_at=now, updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_key", "work_type"],
        set_={"persona": persona, "updated_at": now},
    )
    await session.execute(stmt)
    await session.flush()
    row = (await session.execute(
        select(KanbanWorkTypeMapping)
        .where(KanbanWorkTypeMapping.project_key == project_key)
        .where(KanbanWorkTypeMapping.work_type == work_type)
    )).scalar_one()
    return row


async def bulk_replace_work_type_mappings(
    session, project_key: str, mappings: list[dict],
) -> list[KanbanWorkTypeMapping]:
    """Replace the full per-project mapping in one call.

    `mappings` is a list of `{"work_type": str, "persona": str}` items.
    Missing work_types are *not* deleted — they fall back to the default —
    so callers can post just the rows they care about. To clear an override,
    set the persona to the default value for that work_type.
    """
    for m in mappings:
        await upsert_work_type_mapping(
            session, project_key=project_key,
            work_type=m["work_type"], persona=m["persona"],
        )
    return await list_work_type_mappings(session, project_key)


async def delete_work_type_mapping(
    session, project_key: str, work_type: str
) -> bool:
    """Remove an override. The next `get_work_type_persona` call will return
    the default. Returns True iff a row was actually removed.
    """
    from sqlalchemy import delete as sql_delete

    result = await session.execute(
        sql_delete(KanbanWorkTypeMapping)
        .where(KanbanWorkTypeMapping.project_key == project_key)
        .where(KanbanWorkTypeMapping.work_type == work_type)
    )
    await session.flush()
    return result.rowcount > 0
