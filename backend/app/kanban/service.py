"""Read-side queries over the materialized state + op-log activity feed."""
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
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
    compact: bool = False,
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

    `compact=True` skips the deliverables eager-load — the
    selectinload(KanbanCard.deliverables) is the single biggest chunk of
    payload weight on a 50+ card board (full response was 126KB on a
    48-card Backlog). The caller is expected to serialize via
    CardSummaryResponse (id, title, column, work_type, rank) and not touch
    the relationship. Default False preserves the prior behaviour for
    every existing caller (REST + MCP `_card_dict`).
    """
    stmt = (
        select(KanbanCard)
        .where(KanbanCard.project_key == project_key)
        .order_by(KanbanCard.rank.asc())
    )
    if not compact:
        stmt = stmt.options(selectinload(KanbanCard.deliverables))
    if column is not None:
        stmt = stmt.where(KanbanCard.column == column)
    rows = list((await session.execute(stmt)).scalars().all())
    if ready is None and blocking is None:
        return rows
    cards_by_id = {c.id: c for c in rows}
    blocking_ids = _blocking_card_ids(rows) if blocking is True else None
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


# Markers used by `impediment_status_for_card` to classify why an Impediment
# card is there. The `[dispatch-failure]` prefix is written by
# `dispatch._move_to_impediment_after_repeated_failures` — keep the producer
# and consumer in sync. The `**Impediment:** ` and `**Resolution:** ` labels
# are the canonical labels for those columns / actions; see
# `mcp_server._SUMMARY_REQUIRED_COLUMNS` + the report_impediment / resolve
# flows for the producers.
_DISPATCH_FAILURE_COMMENT_PREFIX = "[dispatch-failure]"
_IMPEDIMENT_QUESTION_PREFIX = "**Impediment:** "
_RESOLUTION_ANSWER_PREFIX = "**Resolution:** "


async def impediment_status_for_card(session, card) -> str | None:
    """Classify why an Impediment card is there, for the board UI.

    Returns ``None`` when the card is not on the Impediment column — the
    field is null on the wire for every other column so existing consumers
    stay backwards-compatible. For Impediment cards, returns one of:

      * ``"needs_answer"``  – an open KanbanGate, or the latest matching
        comment is an ``**Impediment:**`` question without a later
        ``**Resolution:**`` answer.
      * ``"dispatch_failed"`` – the latest matching comment is the
        ``[dispatch-failure]`` auto-move comment posted by
        ``dispatch._move_to_impediment_after_repeated_failures`` after
        ``MAX_DISPATCH_FAILURES`` consecutive spawn failures. The
        recommended remedy is a **Redispatch**, not a human answer.
      * ``"resolved"`` – the latest matching comment is a
        ``**Resolution:**`` answer. This is a transient state (the card
        sits on Impediment briefly between the human picking/typing an
        answer and the operator clicking "Resolve impediment" to dispatch
        the resumed session); the UI uses it to distinguish "answer was
        recorded but card hasn't moved yet" from a bare no-question state.
      * ``"no_question"`` – the card sits on Impediment but neither a
        ``**Impediment:**`` comment nor an open gate exists. Typical for a
        bare move (e.g. the `fab0719c` go/no-go card).

    Walks the activity feed newest-first so the most recent signal wins —
    e.g. a later ``**Impediment:**`` question re-opens the card after a
    prior ``**Resolution:**`` answer.

    See kanban card `c5eb6f89` ("Onderscheid dispatch-failure-impediment
    van human-decision-impediment op het bord") — the field that surfaces
    this classification is ``CardResponse.impediment_status``.
    """
    if card.column != "Impediment":
        return None

    # Open KanbanGate is the strongest "needs answer" signal — even a later
    # `**Resolution:**` comment doesn't close an open gate (the gate
    # transitions via its own answer endpoint, not via a comment). Frontend
    # mirrors this: the gate's choice buttons stay live until the gate's
    # status flips, regardless of any answer-comment posted alongside.
    open_gate_count = (
        await session.execute(
            select(func.count())
            .select_from(KanbanGate)
            .where(KanbanGate.card_id == card.id)
            .where(KanbanGate.status == "open")
        )
    ).scalar_one()
    if open_gate_count > 0:
        return "needs_answer"

    # Walk the comment op-log newest-first so the most recent signal wins.
    stmt = (
        select(KanbanOp)
        .where(KanbanOp.entity_id == card.id)
        .where(KanbanOp.op_type == "comment")
        .order_by(KanbanOp.hlc.desc())
    )
    for op in (await session.execute(stmt)).scalars().all():
        text = op.payload.get("text") or ""
        if text.startswith(_DISPATCH_FAILURE_COMMENT_PREFIX):
            return "dispatch_failed"
        if text.startswith(_IMPEDIMENT_QUESTION_PREFIX):
            return "needs_answer"
        if text.startswith(_RESOLUTION_ANSWER_PREFIX):
            # The latest resolution wins: the impediment is no longer pending.
            return "resolved"
    return "no_question"


# Prefix for the comment posted on the *original* Done card when a human
# requests a review of already-shipped work. Deliberately distinct from
# `_DONE_SUMMARY_PREFIX` ("**Summary:** ") and the `**Impediment:** ` label so
# `enrich_done_info`'s Summary scan never mistakes a review request for the
# card's Done summary. The mirror of the Done/Impediment "prefixed comment"
# convention, one column further along the workflow (see request_review).
_REVIEW_REQUESTED_PREFIX = "**Review requested:** "


# Prefix for the comment posted when a human reopens a Done card with a
# rebuttal ("Weerleg & heropen"). Distinct from `_REVIEW_REQUESTED_PREFIX`
# (a review spawns a sibling analysis card; a reopen moves the *same* card
# back to Backlog) and distinct from `_DONE_SUMMARY_PREFIX` so
# `enrich_done_info` never reads a reopen as the Done summary. Matched
# verbatim by dispatch.extract_revisit_question to re-inject the latest
# rebuttal into the prompt when the card is re-picked.
_REVISIT_PREFIX = "**Revisit:** "


class CardNotInDone(Exception):
    """request_review / reopen_card target isn't currently in the Done column.
    Carries the card's actual column so callers can surface it
    (REST → 409, MCP → error dict)."""
    def __init__(self, column: str):
        self.column = column
        super().__init__(f"card is in {column!r}, not Done")


def _review_description(note: str, done_summary: str | None,
                        deliverables) -> str:
    """Build the review card's description: the human's doubt + the original
    card's Done summary + its deliverable refs, so the analyst has full context
    for triage without a second lookup."""
    parts = [note.strip()]
    parts.append(f"**Original summary:** {done_summary.strip() if done_summary else '(none)'}")
    if deliverables:
        refs = "\n".join(f"- {d.kind}: {d.ref}" for d in deliverables)
        parts.append(f"**Deliverables:**\n{refs}")
    else:
        parts.append("**Deliverables:** (none)")
    return "\n\n".join(parts)


async def request_review(session, card_id: str, note: str):
    """Flag doubt on a completed card and route it to the analyst for triage.

    Reuses three existing mechanisms rather than inventing a new "review agent":
    1. Posts a `**Review requested:** <note>` comment on the *original* card so
       the doubt stays in that card's audit trail (mirrors the Done/Impediment
       prefixed-comment convention; the prefix is distinct so `enrich_done_info`
       never reads it as the Done summary).
    2. Creates a *new* Backlog card `Review: <title>` with `work_type="analysis"`
       (auto-routes to the analyst persona via WORK_TYPE_PERSONA_DEFAULTS) whose
       description carries the note + the original's done_summary + deliverable
       refs, and `metadata.reviewed_card_id` linking back to the original. A new
       card, not a reopen, so the original's done_summary/completed_at stay intact.

    Returns the new review card, or None when `card_id` doesn't exist. Raises
    CardNotInDone when the card exists but isn't in Done (the check runs before
    any op, so a rejected call leaves the board untouched).
    """
    from app.kanban.operations import apply_operation

    card = await get_card(session, card_id)
    if card is None:
        return None
    if card.column != "Done":
        raise CardNotInDone(card.column)

    await apply_operation(session, op_type="comment", entity_type="comment",
        project_key="", entity_id=card_id,
        payload={"text": f"{_REVIEW_REQUESTED_PREFIX}{note}"})

    done_summary, _ = await enrich_done_info(session, card_id)
    description = _review_description(note, done_summary, card.deliverables)
    agent = await resolve_create_agent(
        session, card.project_key, work_type="analysis", explicit_agent=None,
    )
    new_id = await apply_operation(session, op_type="create", entity_type="card",
        project_key=card.project_key, entity_id=None,
        payload={"title": f"Review: {card.title}", "description": description,
                 "column": "Backlog", "work_type": "analysis", "agent": agent,
                 "metadata": {"reviewed_card_id": card_id}})
    return await get_card(session, new_id)


# Hard cap on the length of a reopen note we accept from REST/MCP. Anything
# bigger is almost certainly an accidental paste of a long log/doc; let the
# caller trim. Mirrors how the CommentRequest schema doesn't enforce a length
# today (kept simple on purpose — this matches `request_review`).
# (No explicit cap here on purpose; routers/mcp_server pass the note as-is.)


async def reopen_card(session, card_id: str, note: str):
    """Reopen a completed card with a rebuttal ("Weerleg & heropen").

    Mirrors `request_review`'s "Done column only" contract for the *first*
    reopen, but additionally accepts a card already in `Backlog` so a
    second human rebuttal can sharpen a previously-reopened decision
    without having to wait for the dispatch tick to move the card back to
    Done (the card just needs the latest `**Revisit:**` comment to be
    extractable when dispatch picks it up — see
    `dispatch.extract_revisit_question`).

    Behaviour matrix:

    - Card in `Done` → post the Revisit comment + move to `Backlog`.
    - Card in `Backlog` (already reopened once) → post the Revisit
      comment only. The dispatch tick already has the card on its list;
      the new comment becomes the latest `**Revisit:**` and gets injected
      into the next prompt.
    - Card in any other column → raise `CardNotInDone`. Active cards
      shouldn't be reopened while in flight.

    The `request_review` contract enforced `column == "Done"` because the
    review flow spawns a sibling card; reopen doesn't, so the constraint
    here is relaxed to the union of Done + Backlog. A reviewer who has
    seen the first rebuttal but wants to push back harder should not have
    to wait for the next dispatch cycle to amend the rebuttal.

    Resume-handling (`resume_session_id`/`resume_project_folder`) lives in
    the dispatch layer, because that layer is the only one with a reliable
    project_path (the service-level API would need to thread it through to
    call `session_recovery._resolve_resume_target`). Doing it at dispatch time
    keeps the service contract minimal: this routine only mutates board state.

    Returns the reloaded card on success. None when the card id is unknown.
    Raises `CardNotInDone` when the card exists but isn't in Done or
    Backlog — the check runs before any op, so a rejected call leaves the
    board untouched.
    """
    from app.kanban.operations import apply_operation

    card = await get_card(session, card_id)
    if card is None:
        return None
    if card.column not in ("Done", "Backlog"):
        raise CardNotInDone(card.column)

    await apply_operation(session, op_type="comment", entity_type="comment",
        project_key="", entity_id=card_id,
        payload={"text": f"{_REVISIT_PREFIX}{note}"})

    # Only re-move to Backlog when the card was in Done — repeated reopen
    # calls on an already-Backlog card just append a sharper Revisit
    # comment; the dispatch tick already has the card on its list and the
    # new comment becomes the one it injects.
    if card.column == "Done":
        await apply_operation(session, op_type="move", entity_type="card",
            project_key="", entity_id=card_id, payload={"column": "Backlog"})

    return await get_card(session, card_id)


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
                        default_provider: str | None = None,
                        default_model: str | None = None,
                        max_sessions: int | None = None):
    col = KanbanColumn(
        id=uuid.uuid4().hex,
        project_key=project_key,
        name=name,
        rank=rank or uuid.uuid4().hex,
        default_agent=default_agent,
        default_provider=default_provider,
        default_model=default_model,
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


async def get_column_default_provider(session, project_key: str, column_name: str) -> str | None:
    """Look up the default provider (anthropic | bedrock | minimax) for a column
    name within a project. None means no override — the dispatcher falls back to
    the Anthropic subscription."""
    stmt = (
        select(KanbanColumn)
        .where(KanbanColumn.project_key == project_key)
        .where(KanbanColumn.name == column_name)
    )
    col = (await session.execute(stmt)).scalar_one_or_none()
    return col.default_provider if col else None


async def get_column_default_model(session, project_key: str, column_name: str) -> str | None:
    """Look up the default model for a column name within a project. None means
    no override -- resolution falls through to the persona frontmatter's
    `model:` field, then to no --model flag at all (platform default)."""
    stmt = (
        select(KanbanColumn)
        .where(KanbanColumn.project_key == project_key)
        .where(KanbanColumn.name == column_name)
    )
    col = (await session.execute(stmt)).scalar_one_or_none()
    return col.default_model if col else None


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


async def ensure_intake_column(session, project_key: str) -> bool:
    """Idempotent: create the 'intake' kanban_columns row for this project
    if one doesn't already exist. Returns True iff a new column was created.

    The inceptie-pipeline (kanban card c33b2f14 / facet A of
    platform-as-app-factory) puts idea-cards on the meta-project's `intake`
    column before they're promoted to a new project via
    `create_project_from_intake`. For projects that enabled kanban before
    `intake` was added to `COLUMNS` (schemas.py), this helper back-fills the
    kanban_columns row so the column renders on the board. For new projects
    it stays out of the way — the row is created lazily the first time an
    intake card is created OR the project is re-enabled (which iterates
    `COLUMNS` and creates any missing entries).
    """
    existing = await list_columns(session, project_key)
    if any(c.name == "intake" for c in existing):
        return False
    # Insert at the top of the board (rank=0) so intake is the leftmost column,
    # matching the natural flow "intake → Backlog → … → Done".
    rank = "0000"
    for col in existing:
        try:
            if int(col.rank) >= int(rank):
                # Shift everyone else down by 1. Bump-only-if-conflict keeps
                # the existing rank order stable when intake wasn't there.
                col.rank = f"{int(col.rank) + 1:04d}"
        except (TypeError, ValueError):
            # Non-numeric ranks (uuid4 hex) — leave alone; intake is fine
            # sitting at rank=0000 since the order is dominated by created_at
            # ties anyway.
            pass
    await create_column(session, project_key, name="intake", rank=rank)
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


async def latest_gate_answer(session, card_id: str) -> str | None:
    """Return the chosen answer from the most recent *answered* gate on this
    card, or None when no gate exists yet, no gate has been answered, or the
    card has only an open (still-pending) gate.

    Used by ``resolve_impediment`` to splice the human's pick into the
    resumed session's ``impediment_question`` so the new agent sees both the
    original ask *and* the decision in one block. Returns None for the
    legacy free-text impediment path (no gate ever opened), keeping
    backwards-compatible callers (no chosen-answer line in the prompt).
    """
    from app.kanban.models import KanbanGate

    stmt = (
        select(KanbanGate)
        .where(KanbanGate.card_id == card_id)
        .where(KanbanGate.status == "answered")
        .order_by(KanbanGate.answered_at.desc())
        .limit(1)
    )
    gate = (await session.execute(stmt)).scalars().first()
    if gate is None:
        return None
    return gate.answer


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


# Prefix for the comment posted when a card's work_type is changed to one whose
# persona no longer matches a still-pinned `agent`. `resolve_create_agent`'s
# "explicit agent wins over work_type" rule is intentional and unchanged (the
# dispatcher reads card.agent first, and dispatch.py's fallback_persona matches
# a valid persona name directly), so a stale agent silently beats the new
# work_type mapping. This comment makes that otherwise-invisible routing
# decision show up on the board. See the "[problem] update_card laat work_type
# wijzigen zonder agent te her-resolven" card.
_ROUTING_MISMATCH_PREFIX = "**Routing mismatch:** "


async def work_type_agent_mismatch_comment(
    session, project_key: str, *,
    new_work_type: str | None, current_agent: str | None,
) -> str | None:
    """Return a board comment when `new_work_type` maps to a persona that
    differs from the card's pinned `current_agent`, else None.

    Only informational — dispatch keeps honouring the explicit `agent`. Returns
    None when there is no conflict: no agent pinned, no (or cleared) work_type,
    or the mapped persona already equals the pinned agent.
    """
    agent = (current_agent or "").strip()
    if not agent or not new_work_type:
        return None
    persona = await get_work_type_persona(session, project_key, new_work_type)
    if persona == agent:
        return None
    return (
        f'{_ROUTING_MISMATCH_PREFIX}work_type="{new_work_type}" maps to persona '
        f'"{persona}", but this card\'s agent is pinned to "{agent}" — dispatch '
        f'will use "{agent}". Clear or change `agent` to route by work_type.'
    )


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
