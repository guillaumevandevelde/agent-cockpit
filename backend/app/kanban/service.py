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
    KanbanDeliverable,
    KanbanGate,
    KanbanOp,
    KanbanWorkTypeMapping,
)
from app.utils.timeutils import ensure_aware


def is_analyst_leaf_spike(card) -> bool:
    """True when the card is routed to the analyst column but does NOT have
    a multi-agent decomposition pipeline attached.

    Routing detection: ``work_type='analysis'`` (the structured routing hint
    maps analysis → analyst by default — see ``WORK_TYPE_PERSONA_DEFAULTS``)
    OR ``card.agent='analyst'`` (legacy/manual override that picks the
    analyst column regardless of work_type). Either signals that the card's
    target_agent resolved to "analyst" and its persona body is analyst.md
    (or the hardcoded ``ANALYST_PROMPT`` fallback).

    Without this distinction, ``build_card_prompt`` emitted both the
    analyst persona ("Verboden: geen Write/Edit") AND the executor ship
    workflow ("write doc + commit + ship + attach branch + move THIS kaart
    naar Done") for the same card. The leaf analyst spike is a single
    deliverable, not a multi-agent decomposition — there's no parent to
    split, no child-cards to plan — so the standard analyst prohibitions
    are inapplicable. See kanban card a9c27beeb63e427a9c14ad98fa8380fe.

    Lives here (and not in ``dispatch.py``) so non-dispatch consumers
    (e.g. ``mcp_server.move_card`` enforcing the analysis-outcome gate
    from ``docs/cockpit/analysis-outcome-contract-decision.md`` §5) can
    share the exact same predicate without an import cycle: dispatch.py
    imports service.py, but service.py never imports dispatch.py.

    Note: this helper only checks routing; the phase check (executor vs.
    analyst) is applied in ``build_card_prompt``. A real analyst card
    (``analyst_agent_id`` set, ``analyst_run_id`` not set, ``phase ==
    'analyst'``) is consistent — persona + analyst session-end workflow
    are both planning-only, no contradiction — so it does not need the
    override.
    """
    work_type = getattr(card, "work_type", None)
    agent = getattr(card, "agent", None)
    return work_type == "analysis" or agent == "analyst"


async def known_project_keys(session) -> set[str]:
    """Distinct project keys with existing state: a card, or a column (the
    latter seeded by `POST /kanban/enable` before any card exists).

    Used by the MCP `list_cards`/`create_card` tools to catch a mistyped or
    guessed `project` argument before it silently returns an empty list from
    an unrelated bucket, or creates an orphaned card in one auto-dispatch
    never sees. See kanban card 91c85199 for the incident that prompted
    this — a wrong `project` string looked exactly like a valid, empty
    project instead of erroring.
    """
    card_keys = (await session.execute(
        select(KanbanCard.project_key).distinct()
    )).scalars().all()
    column_keys = (await session.execute(
        select(KanbanColumn.project_key).distinct()
    )).scalars().all()
    return set(card_keys) | set(column_keys)


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
        stmt = stmt.options(
            selectinload(KanbanCard.deliverables),
            selectinload(KanbanCard.attachments),
        )
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


def _dependents_by_parent(cards: list[KanbanCard]) -> dict[str, list[KanbanCard]]:
    """Map each parent card id → the non-Done cards that list it in
    `depends_on`. Single detection seam shared by `_blocking_card_ids`
    (needs only the key set) and the delete-guard `strip_dangling_deps_on_delete`
    (needs the dependent cards themselves, to strip + comment on them)."""
    dependents: dict[str, list[KanbanCard]] = {}
    for card in cards:
        if card.column == "Done":
            # A finished card no longer blocks anything; skip.
            continue
        for parent_id in card.depends_on or ():
            dependents.setdefault(parent_id, []).append(card)
    return dependents


def _blocking_card_ids(cards: list[KanbanCard]) -> set[str]:
    """Card ids that have at least one non-Done dependent in `cards`.

    Self-resolved against the passed-in list rather than a fresh DB read so
    the caller's `ready`/`blocking` filters both see the same snapshot — a
    freshly-Done parent has no in-flight children, so re-reading the DB would
    see an out-of-date view for the rest of this tick."""
    return set(_dependents_by_parent(cards).keys())


# Prefix for the audit comment posted on a dependent when its dependency source
# card is deleted. Deliberately distinct from every consumer-read prefix in
# docs/cockpit/kanban-conventions.md §2 (`**Summary:** `, `**Impediment:** `,
# `**Resolution:** `, …) so no reader mistakes it for one of those — it is
# purely documentary for the activity feed, like `**Promoted to project:** `.
_DEP_REMOVED_PREFIX = "**Dependency removed:** "


async def strip_dangling_deps_on_delete(session, card_id: str) -> list[str]:
    """Guard run before a card is deleted (single delete *or* `clear_column` /
    "Clear Done"): strip the doomed card out of the `depends_on` of every
    non-Done card that lists it, and post an audit comment on each such
    dependent.

    Without this, the fail-closed dep-resolver (`meets_dep_prerequisites`)
    turns a *satisfied* dependency (parent in Done) into a permanent, invisible
    fail-closed block the moment its source card is deleted — exactly the trap
    that stranded 4 Backlog cards when "Clear Done" removed their finished
    parent. See docs/cockpit/dangling-depends-on-analyse.md §1.2/§4.

    Reuses the `_dependents_by_parent` detection seam (of which
    `_blocking_card_ids` is the key-set view). Returns the ids of the
    dependents that were updated, for logging/tests."""
    from app.kanban.operations import apply_operation

    card = await session.get(KanbanCard, card_id)
    if card is None:
        return []
    cards = await list_cards(session, card.project_key, compact=True)
    dependents = _dependents_by_parent(cards).get(card_id, [])
    updated: list[str] = []
    for dep in dependents:
        new_deps = [d for d in (dep.depends_on or ()) if d != card_id]
        await apply_operation(
            session, op_type="update", entity_type="card",
            project_key="", entity_id=dep.id,
            payload={"depends_on": new_deps},
        )
        await apply_operation(
            session, op_type="comment", entity_type="comment",
            project_key="", entity_id=dep.id,
            payload={"text": (
                f"{_DEP_REMOVED_PREFIX}dependency `{card_id}` "
                f"({card.title!r}) was deleted from the board, so it has been "
                f"stripped from this card's depends_on. The dependency source no "
                f"longer exists; leaving the id in place would fail-closed the "
                f"dep-resolver and block this card permanently."
            )},
        )
        updated.append(dep.id)
    return updated


async def get_card(session, card_id: str):
    stmt = (
        select(KanbanCard)
        .where(KanbanCard.id == card_id)
        .options(
            selectinload(KanbanCard.deliverables),
            selectinload(KanbanCard.attachments),
        )
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

    The new card is tagged `priority="high"` so `dispatch._next_card` picks it
    up before ordinary rank-FIFO Backlog cards: a human in the loop is blocked
    on an answer, and waiting behind 20+ unrelated cards (the worst observed
    wait was 1u43m) pushes them to reopen the source card as the costliest
    possible corrective action (a full Opus re-analysis). High priority uses
    the existing `_PRIORITY_RANK = {"high": 3, ...}` machinery — no new sort
    path needed.

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
                 "priority": "high",
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


# Name of the independent-reviewer agent column. Its mere existence for a
# project is the activation switch for the pre-Done review gate (see
# `reviewer_column_exists` + `mcp_server.move_card`): a project without this
# column behaves exactly as before — full backwards-compat for every other
# board. The column is created the ordinary way (`sync_agent_columns` picks up
# `.claude/agents/reviewer.md`), so "assign the reviewer to a column" =
# "sync the agent columns once reviewer.md exists". See
# `docs/cockpit/reviewer-agent-decision.md`.
REVIEWER_COLUMN = "reviewer"

# Card-metadata key holding the persona that produced the work, stashed when a
# card is redirected into the reviewer column so a rejection routes the resume
# back to that persona (the engineer) instead of re-running the reviewer.
REVIEW_RETURN_AGENT_KEY = "review_return_agent"


async def reviewer_column_exists(session, project_key: str) -> bool:
    """True when this project has a `reviewer` agent column — the activation
    switch for the independent, board-enforced pre-Done review gate. No column
    → feature off (a card moving to Done behaves exactly as it did before this
    feature existed)."""
    if not project_key:
        return False
    stmt = (
        select(KanbanColumn.id)
        .where(KanbanColumn.project_key == project_key)
        .where(KanbanColumn.name == REVIEWER_COLUMN)
    )
    return (await session.execute(stmt)).first() is not None


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


async def ensure_awaiting_subtasks_column(session, project_key: str) -> bool:
    """Idempotent: create the 'Awaiting Subtasks' kanban_columns row for
    this project if one doesn't already exist. Returns True iff a new
    column was created.

    Called lazily from the move_card parking path (mcp_server.move_card)
    the first time a card actually parks there, so projects that enabled
    kanban before this column existed still render it — mirrors
    `ensure_analyst_column`/`ensure_intake_column`. Rank net vóór `Done`
    (docs/cockpit/analyse-levenscyclus-decision.md §3).
    """
    existing = await list_columns(session, project_key)
    if any(c.name == "Awaiting Subtasks" for c in existing):
        return False
    from app.kanban.schemas import COLUMNS
    done_rank = "9999"
    for col in existing:
        if col.name == "Done":
            done_rank = col.rank
            break
    rank = f"{int(done_rank) - 1:04d}" if done_rank != "9999" else f"0{len(COLUMNS):03d}"
    await create_column(session, project_key, name="Awaiting Subtasks", rank=rank)
    await session.flush()
    return True


async def card_has_children(session, card_id: str) -> bool:
    """True if ≥1 card has `parent_card_id == card_id`, regardless of the
    children's own column. Parent-generic — not gated on `work_type`
    (decision doc §3.1)."""
    count = (await session.execute(
        select(func.count()).select_from(KanbanCard)
        .where(KanbanCard.parent_card_id == card_id)
    )).scalar_one()
    return count > 0


async def close_parent_if_all_children_done(session, parent_id: str) -> bool:
    """If `parent_id` is currently parked in `Awaiting Subtasks` and every
    card with `parent_card_id == parent_id` is now in `Done`, move the
    parent to `Done` with a `**Summary:**` comment. Returns True iff the
    parent was closed.

    Only closes a parent that is actually parked — a parent still in an
    agent column (analysis in progress) or in `Impediment` is left alone.
    Callers walk the `parent_card_id` chain to handle nested decomposition
    (a closed parent may itself be someone's child).
    """
    parent = await session.get(KanbanCard, parent_id)
    if parent is None or parent.column != "Awaiting Subtasks":
        return False
    sibling_columns = (await session.execute(
        select(KanbanCard.column).where(KanbanCard.parent_card_id == parent_id)
    )).scalars().all()
    if not sibling_columns or any(c != "Done" for c in sibling_columns):
        return False
    from app.kanban.operations import apply_operation
    await apply_operation(session, op_type="move", entity_type="card",
        project_key="", entity_id=parent_id, payload={"column": "Done"})
    await apply_operation(session, op_type="comment", entity_type="comment",
        project_key="", entity_id=parent_id,
        payload={"text": (
            "**Summary:** All subtasks reached Done — auto-closed from "
            "Awaiting Subtasks."
        )})
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


# ---------------------------------------------------------------------------
# PO-wachtrij: "Wacht op jou" — finite, sortable list of human-blocked items.
# ---------------------------------------------------------------------------
#
# Background (kanban card c7ea21b0…):
#
# The product owner wants a single, sorted list of everything that is blocked
# on a human decision, instead of having to scan multiple columns + the
# metadata for gate/review/plan_ref edges. Four detection categories, in
# priority order when ties collide on wait_seconds (older wins regardless):
#
#   1. impediment_needs_answer — card on Impediment column with an open
#      question (impediment_status_for_card == 'needs_answer'). Reason text
#      is the latest `**Impediment:**` comment.
#   2. gate_open — any KanbanGate with status='open'. (Gates are usually on
#      Impediment cards; this branch handles the column-independent case.)
#      Reason text is the gate's question.
#   3. review_requested — card whose metadata.reviewed_card_id is set, i.e.
#      a review-card sibling that the analyst needs to triage.
#   4. awaiting_plan_ref — child card (has parent_card_id) without a
#      kind='plan_ref' deliverable. Dispatcher holds it out until the
#      analyst's add_plan_attachment lands; if the analyst stalls, only the
#      human can unblock.
#
# All four reuse already-existing signals — no new column, no new workflow
# concept, no new persisted state. The wachtrij is a *view* on top of the
# existing board. See docs/cockpit/product-owner-volgbaarheid-analyse.md
# §2b + §4.1 + §5 (kaart B) for the framing.


async def _latest_impediment_question(session, card_id: str) -> str | None:
    """Latest `**Impediment:** <text>` comment on the card, newest-first.

    Mirrors the resolution walker in `impediment_status_for_card`: the most
    recent `**Impediment:**` comment wins, and a later `**Resolution:**`
    flips the status out of `needs_answer` (so this function is only called
    when the status is already `needs_answer`).
    """
    stmt = (
        select(KanbanOp)
        .where(KanbanOp.entity_id == card_id)
        .where(KanbanOp.op_type == "comment")
        .order_by(KanbanOp.hlc.desc())
    )
    for op in (await session.execute(stmt)).scalars().all():
        text = op.payload.get("text") or ""
        if text.startswith(_IMPEDIMENT_QUESTION_PREFIX):
            return text[len(_IMPEDIMENT_QUESTION_PREFIX):]
    return None


async def _latest_review_requested_note(session, card_id: str) -> str | None:
    """Latest `**Review requested:** <note>` comment on the card."""
    stmt = (
        select(KanbanOp)
        .where(KanbanOp.entity_id == card_id)
        .where(KanbanOp.op_type == "comment")
        .order_by(KanbanOp.hlc.desc())
    )
    for op in (await session.execute(stmt)).scalars().all():
        text = op.payload.get("text") or ""
        if text.startswith(_REVIEW_REQUESTED_PREFIX):
            return text[len(_REVIEW_REQUESTED_PREFIX):]
    return None


def _first_paragraph(text: str) -> str:
    """First paragraph of a multi-paragraph string, trimmed.

    Used to surface the human's note from the review-card description,
    where `_review_description` plants the note as paragraph 0 before the
    `**Original summary:**` and `**Deliverables:**` blocks.
    """
    if not text:
        return ""
    # Split on double newline (markdown paragraph separator); strip
    # surrounding whitespace; cap so a sprawling description doesn't blow up
    # the wachtrij line.
    for chunk in text.split("\n\n"):
        chunk = chunk.strip()
        if chunk:
            return chunk[:280]
    return ""


def _now() -> datetime:
    return datetime.now(UTC)


def _wait_seconds(created_at: datetime, now: datetime) -> int:
    """Seconds since `created_at`. Both sides are normalized to UTC via
    `ensure_aware` so a stored naive datetime (SQLite's default) doesn't
    trip `can't subtract offset-naive and offset-aware datetimes`."""
    return max(0, int((now - ensure_aware(created_at)).total_seconds()))


async def po_wachtrij(session, project_key: str) -> list[dict]:
    """Return the PO-facing "wacht op jou" list for `project_key`.

    Each item:

      * ``card_id``           — the card the human must look at
      * ``card_title``        — convenience for the UI
      * ``card_column``       — current column (Backlog / Impediment / …)
      * ``kind``              — one of impediment_needs_answer | gate_open |
                                review_requested | awaiting_plan_ref
      * ``reason``            — short human-readable snippet of the question
                                / note / "plan not yet attached"
      * ``created_at``        — ISO-8601 timestamp the item entered the queue
                                (op-log for comments/gates/review; card
                                created_at for awaiting_plan_ref children)
      * ``wait_seconds``      — now − created_at; integer seconds

    Sorted oldest-first (longest wait at top) so the most-urgent
    decision surfaces first.

    The function is read-only: no commit, no op-log write. The caller (REST
    router, future MCP tool) wraps it in its own session and may run it
    concurrently — the only writes inside this function are session.flush()
    calls inside helper predicates, and the request-review path that already
    ran the comment ops.
    """
    now = _now()
    items: list[dict] = []

    # Pull all candidate cards for the project in one round-trip. We
    # deliberately skip the heavy selectinload(deliverables/attachments) from
    # list_cards — the wachtrij reader only needs the per-card gate count
    # and (for awaiting_plan_ref) whether a plan_ref deliverable exists;
    # both are fast point-queries keyed on card.id below.
    cards = await list_cards(session, project_key, compact=True)

    # Open gates for *any* card in the project, regardless of column —
    # one batched query so a project with 100+ gates still costs O(1)
    # round-trips.
    open_gate_rows = (await session.execute(
        select(KanbanGate)
        .where(KanbanGate.project_key == project_key)
        .where(KanbanGate.status == "open")
    )).scalars().all()
    open_gate_by_card: dict[str, KanbanGate] = {}
    for g in open_gate_rows:
        # If multiple open gates exist on the same card (rare), keep the
        # most recent — matches `impediment_status_for_card`'s "newest
        # wins" semantics.
        prev = open_gate_by_card.get(g.card_id)
        if prev is None or g.created_at > prev.created_at:
            open_gate_by_card[g.card_id] = g

    for card in cards:
        # An open gate is the strongest "needs human" signal: it renders in
        # the UI as choice buttons regardless of which column the card is
        # on. Emit it as `gate_open` and skip the impediment-status branch
        # below (gate wins, otherwise the same card would appear twice).
        gate = open_gate_by_card.get(card.id)
        if gate is not None:
            items.append({
                "card_id": card.id,
                "card_title": card.title,
                "card_column": card.column,
                "kind": "gate_open",
                "reason": gate.question,
                "created_at": ensure_aware(gate.created_at).isoformat(),
                "wait_seconds": _wait_seconds(gate.created_at, now),
            })
            continue

        # Impediment lane: card is parked on Impediment AND still has an
        # open question (the `**Resolution:**` hasn't landed yet, or a
        # later `**Impediment:**` re-opened it).
        if card.column == "Impediment":
            status = await impediment_status_for_card(session, card)
            if status == "needs_answer":
                question = await _latest_impediment_question(
                    session, card.id
                ) or "(geen vraagtekst — open impedimen zonder commentaar)"
                # `created_at` for an impediment is when the question was
                # last asked; that's the moment the human started waiting.
                created_at = card.updated_at or card.created_at
                items.append({
                    "card_id": card.id,
                    "card_title": card.title,
                    "card_column": card.column,
                    "kind": "impediment_needs_answer",
                    "reason": question,
                    "created_at": ensure_aware(created_at).isoformat(),
                    "wait_seconds": _wait_seconds(created_at, now),
                })
                continue

        # Review card: the analyst needs the human's doubt re-investigated.
        # Detected via metadata.reviewed_card_id, set by request_review. The
        # `**Review requested:**` comment is posted on the *original* Done
        # card (see request_review), not on the review card itself — but
        # `_review_description` plants the note as the first paragraph of
        # the review card's description, so we read from there.
        meta = getattr(card, "meta", None) or {}
        reviewed_card_id = meta.get("reviewed_card_id")
        if reviewed_card_id:
            note = _first_paragraph(card.description or "")
            if not note:
                note = "(review zonder notitie)"
            created_at = card.updated_at or card.created_at
            items.append({
                "card_id": card.id,
                "card_title": card.title,
                "card_column": card.column,
                "kind": "review_requested",
                "reason": note,
                "created_at": ensure_aware(created_at).isoformat(),
                "wait_seconds": _wait_seconds(created_at, now),
            })
            continue

        # Child awaiting plan_ref: parent_card_id set, no kind='plan_ref'
        # deliverable on the card yet. The dispatcher holds these out; if
        # the analyst's add_plan_attachment never lands, only the human
        # can chase it.
        if getattr(card, "parent_card_id", None):
            has_plan_ref = (await session.execute(
                select(func.count())
                .select_from(KanbanDeliverable)
                .where(KanbanDeliverable.card_id == card.id)
                .where(KanbanDeliverable.kind == "plan_ref")
            )).scalar_one()
            if not has_plan_ref:
                created_at = card.updated_at or card.created_at
                items.append({
                    "card_id": card.id,
                    "card_title": card.title,
                    "card_column": card.column,
                    "kind": "awaiting_plan_ref",
                    "reason": (
                        "Wacht op plan van analyst "
                        f"(parent {card.parent_card_id[:8]})"
                    ),
                    "created_at": ensure_aware(created_at).isoformat(),
                    "wait_seconds": _wait_seconds(created_at, now),
                })

    # Oldest-first so the longest-waiting item is on top.
    items.sort(key=lambda x: x["wait_seconds"], reverse=True)
    return items
