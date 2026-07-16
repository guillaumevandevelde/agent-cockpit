"""Kanban MCP server. The agent talks to this over localhost SSE; only the
backend reaches the store, so the agent never sees DB/sync credentials.

Each tool is a thin wrapper over apply_operation/service, returning plain
dicts (JSON-serializable) for the MCP layer.
"""
import asyncio
import json
import logging
import time

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select, text

from app.kanban import dep_resolver as mcp_kanban_deps
from app.kanban import service
from app.kanban.db import KanbanSessionLocal
from app.kanban.models import KanbanDeliverable
from app.kanban.operations import ClaimRejected, apply_operation
from app.kanban.project_key import resolve_project_key as _resolve_project_key
from app.kanban.schemas import CardResponse, CardSummaryResponse

logger = logging.getLogger(__name__)

mcp = FastMCP("cockpit-kanban")

_NOT_FOUND = "not_found"

# open_gate polls for an answer instead of pushing one — a lightweight choice
# that avoids a callback/websocket channel back into the MCP tool call. 2s is
# imperceptible to a human answering in the UI; 30min default timeout is long
# enough for a human to notice and respond without blocking the tool forever.
_GATE_POLL_INTERVAL_SECONDS = 2
_GATE_DEFAULT_TIMEOUT_SECONDS = 1800


async def _card_dict(s, card) -> dict:
    """JSON-serialisable dict for a card ORM instance, enriched with the
    op-log-derived `done_summary` / `completed_at` / `impediment_status`
    fields so the MCP layer returns the same shape the REST API does
    (see CardResponse).

    All callers have an active session, so we require it as a parameter
    instead of opening a second one. `None` cards short-circuit to the
    `{error: not_found}` payload without touching the session."""
    if card is None:
        return {"error": _NOT_FOUND}
    done_summary, completed_at = await service.enrich_done_info(s, card.id)
    impediment_status = await service.impediment_status_for_card(s, card)
    return CardResponse.model_validate(card).model_copy(update={
        "done_summary": done_summary,
        "completed_at": completed_at,
        "impediment_status": impediment_status,
    }).model_dump(mode="json")


async def _require_card(s, card_id: str):
    """Return the card ORM instance or None. Uses a bare identity lookup so the
    deliverables relationship stays unloaded; the final service.get_card call then
    fetches it fresh via selectinload (avoids stale-cache issues in the same session)."""
    from app.kanban.models import KanbanCard
    return await s.get(KanbanCard, card_id)


@mcp.tool()
async def ping() -> dict:
    """Verify the kanban MCP server is reachable and the database is responsive."""
    async with KanbanSessionLocal() as s:
        await s.execute(text("SELECT 1"))
    return {"ok": True, "server": "cockpit-kanban"}


@mcp.tool()
async def resolve_project_key(project_path: str) -> dict:
    """Resolve a filesystem path to this board's canonical project key.

    Every card lives under a project key derived from `git remote get-url
    origin` (as `git:<host>/<path>`), or a `slug:<name>` fallback when the
    repo has no remote. `list_cards`/`create_card` take that key as a raw,
    unvalidated string — a hand-typed or guessed key (e.g. a display name
    instead of the resolved key) silently creates a new, orphaned bucket
    that's invisible from the real board instead of erroring. Call this
    first with the repo's working directory (e.g. the output of
    `git rev-parse --show-toplevel`) to get the key actually used by
    auto-dispatch, rather than guessing.
    """
    return {"project_key": _resolve_project_key(project_path)}


@mcp.tool()
async def list_cards(project: str, column: str | None = None,
                     compact: bool = False) -> list[dict]:
    """List cards for a project, optionally filtered by column.

    `project` must be the exact project key — use `resolve_project_key` first
    if you're not certain of it. A mistyped or guessed key won't error; it
    just returns an empty (or wrong) list from an unrelated bucket.

    `compact=True` returns the dedupe-friendly per-card shape
    (id, title, column, work_type, rank) and skips the per-card op-log
    enrichments (done_summary, completed_at, impediment_status) so a
    50+ card Backlog stops blowing the MCP token cap during dedupe passes.
    Default False preserves the full CardResponse shape every existing
    agent expects. Backwards-compatible opt-in.
    """
    async with KanbanSessionLocal() as s:
        rows = await service.list_cards(s, project, column, compact=compact)
        if compact:
            return [CardSummaryResponse.model_validate(c).model_dump()
                    for c in rows]
        return [await _card_dict(s, c) for c in rows]


@mcp.tool()
async def get_card(card_id: str) -> dict:
    """Get a single card with its deliverables."""
    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, card_id)
        if card is None:
            logger.debug("get_card: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        return await _card_dict(s, card)


@mcp.tool()
async def create_card(project: str, title: str, description: str = "",
                      column: str = "Backlog",
                      work_type: str | None = None,
                      agent: str | None = None,
                      parent_card_id: str | None = None,
                      depends_on: list[str] | None = None,
                      metadata: dict | None = None) -> dict:
    """Create a new card (agents may decompose work into subtask cards).

    `project` must be the exact project key — use `resolve_project_key` first
    if you're not certain of it. A mistyped or guessed key won't error; it
    silently creates a new, orphaned bucket that auto-dispatch never sees.

    `work_type` (analysis | feature | bug | chore) and `agent` are optional
    routing hints. When `work_type` is set and `agent` is not, the
    work_type → persona mapping auto-fills `agent` (mirrors the REST
    create_card path post-commit-80e139e). Explicit `agent` wins, same as the
    REST contract.

    `parent_card_id` lets the analyst workflow create child cards that
    `add_plan_attachment` will accept — that tool rejects any child whose
    `parent_card_id` doesn't already match (`{"error": "parent_mismatch"}`),
    so without this parameter the analyst had to PATCH the card after
    creation as a workaround.

    `depends_on` is the list of sibling card ids that must reach a terminal
    state before this card becomes dispatchable — see
    `docs/cockpit/multi-agent-kanban.md` for the DAG semantics. Useful for
    retroactive wiring: when a dependency is discovered after the cards are
    already created (so `add_plan_attachment`'s `depends_on_graph` is no
    longer an option), or when the sibling-only deps are needed outside the
    analyst plan-attachment flow. The same field is honoured by the REST
    `CardCreate` schema (`backend/app/kanban/schemas.py:147`) and flows
    through `apply_operation` to the `KanbanCard.depends_on` column.

    `metadata` is a free-form key/value bag (JSON-serialized) for
    integration-specific data that doesn't deserve its own field — external
    IDs, workflow provenance, last-seen upstream commit sha, etc. Stored as
    a JSON column on the card and round-tripped unchanged on read.
    """
    async with KanbanSessionLocal() as s:
        # Auto-fill `agent` from the work_type mapping so MCP-created cards
        # don't recreate the regression from kanban card 9cf106e7 ("Card with
        # analysis work type got picked up by an engineer"): without this, a
        # card created via MCP with work_type='analysis' landed with
        # agent=None and the dispatcher routed it to the hardcoded 'engineer'
        # fallback. See service.resolve_create_agent and
        # docs/cockpit/work-type-routing-analysis.md §2B.
        resolved_agent = await service.resolve_create_agent(
            s, project, work_type=work_type, explicit_agent=agent,
        )
        # Note the asymmetry: `work_type` is the raw caller input (None if
        # the caller didn't set it), while `agent` is the *resolved* value
        # (possibly derived from work_type via resolve_create_agent, not the
        # caller's explicit input). This is the same pattern the REST
        # create_card path uses (router.py:204) — see the
        # `resolve_create_agent` docstring for the priority order. The
        # op-log stores the resolved agent so `rematerialize()` replay
        # reproduces the same routing decision — the user's explicit `agent`
        # input alone wouldn't, since the explicit-vs-derived distinction
        # is lost once the create op is folded into the materialized row.
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key=project, entity_id=None,
            payload={"title": title, "description": description,
                     "column": column, "work_type": work_type,
                     "agent": resolved_agent,
                     "parent_card_id": parent_card_id,
                     "depends_on": depends_on,
                     "metadata": metadata})
        await s.commit()
        card = await service.get_card(s, cid)
        logger.info("create_card: %s in %s (%s, work_type=%s, agent=%s)",
                    cid, project, column, work_type, resolved_agent)
        return await _card_dict(s, card)


@mcp.tool()
async def claim_card(card_id: str, claimed_by: str) -> dict:
    """Claim a card. Returns the card, or {error: already_claimed, owner} or {error: not_found}."""
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("claim_card: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        try:
            await apply_operation(s, op_type="claim", entity_type="card",
                project_key="", entity_id=card_id, payload={"claimed_by": claimed_by})
        except ClaimRejected as e:
            return {"error": "already_claimed", "owner": e.current_owner}
        await s.commit()
        logger.info("claim_card: %s claimed by %s", card_id, claimed_by)
        return await _card_dict(s, await service.get_card(s, card_id))


# Landing a card here without a word of what happened makes the board useless as a
# record: Done just says "it's done", Impediment just says "it's stuck". Requiring
# `summary` on move_card into either one guarantees every card that reaches a
# terminal column carries a human-readable account of the work, regardless of which
# coding agent/provider drove the session — enforced once here rather than per-agent
# prompt text that could be skipped. report_impediment already gets this for free
# (its mandatory `question` arg is posted the same way), so this only closes the gap
# for the Done path and for a raw move_card("Impediment", ...) bypassing that tool.
_SUMMARY_REQUIRED_COLUMNS = {"Done": "Summary", "Impediment": "Impediment"}


@mcp.tool()
async def move_card(card_id: str, column: str, summary: str | None = None) -> dict:
    """Move a card to a different column.

    Moving into "Done" or "Impediment" requires `summary` — a short account of the
    work that was done (or why it couldn't be finished). It's posted to the card's
    activity feed as a comment so the outcome is visible without opening a
    transcript. Returns {"error": "summary_required"} without moving the card if
    summary is missing/blank for those two columns.
    """
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("move_card: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}

        label = _SUMMARY_REQUIRED_COLUMNS.get(column)
        summary = (summary or "").strip()
        if label and not summary:
            return {
                "error": "summary_required",
                "message": (
                    f'Add a `summary` describing the work done before moving to "{column}" '
                    "— it's posted to the card's activity feed."
                ),
            }

        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=card_id, payload={"column": column})
        if label:
            await apply_operation(s, op_type="comment", entity_type="comment",
                project_key="", entity_id=card_id,
                payload={"text": f"**{label}:** {summary}"})
        await s.commit()
        logger.info("move_card: %s → %s", card_id, column)
        return await _card_dict(s, await service.get_card(s, card_id))


@mcp.tool()
async def update_card(card_id: str, title: str | None = None,
                      description: str | None = None,
                      depends_on: list[str] | None = None,
                      metadata: dict | None = None) -> dict:
    """Update a card's title, description, depends_on, and/or metadata bag.

    Same "skip-when-None" semantics as the existing title/description paths:
    None means "don't touch". To clear an existing value via MCP, leave the
    field at its current value or use the REST PATCH endpoint, which can
    distinguish "field absent" from "field set to null" via exclude_unset.

    `depends_on` replaces the card's sibling-dep list — pass a `list[str]`
    to set, leave `None` to keep the current value. The dispatcher uses
    this list to gate dispatch on the named cards reaching a terminal
    state. Mirrors the REST `PATCH /api/v1/kanban/cards/{cid}` path's
    handling of `CardUpdate.depends_on` (see
    `backend/app/api/v1/kanban/router.py:329-360` →
    `apply_operation("update")` → `_materialize` setting
    `card.depends_on = payload["depends_on"]`).
    """
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("update_card: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        payload = {k: v for k, v in {"title": title, "description": description,
                                     "depends_on": depends_on,
                                     "metadata": metadata}.items()
                   if v is not None}
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=card_id, payload=payload)
        await s.commit()
        return await _card_dict(s, await service.get_card(s, card_id))


@mcp.tool()
async def comment(card_id: str, text: str) -> dict:
    """Add a comment to a card's activity feed."""
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=card_id, payload={"text": text})
        await s.commit()
        logger.info("comment: on %s", card_id)
        return {"ok": True}


@mcp.tool()
async def set_card_gate(card_id: str, gated_on: str | None) -> dict:
    """Set or clear a card's business-trigger gate.

    The gate is a free-form string written to ``card.metadata["gated_on"]``;
    ``dispatch._is_gated`` reads it on every dispatch tick and holds the card
    out of auto-dispatch while it is non-empty. Independent of
    ``depends_on`` (the kanban-card DAG) and ``scheduled_at`` (clock-based
    hold) — see ``docs/cockpit/kanban-conventions.md`` §4.

    Pass ``gated_on=None`` (or an empty string) to lift the gate: the next
    tick picks the card up normally. Passing a string sets the gate and the
    operator's reason is recorded verbatim — keep it short and machine-
    legible so future operators (and ``flag-problem`` audits) can grep for
    the trigger, e.g. ``"second-executor-provider-onboarded"`` rather than
    free-form prose.

    Use this rather than a bare ``update_card(metadata={...})`` because the
    tool posts an audit comment with ``**Gate:** set/cleared`` so the gate's
    history is visible in the activity feed and the op-log replay doesn't
    silently lose the intent. ``update_card`` will still work for raw
    metadata edits; this tool is the canonical, opinionated path.
    """
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            return {"error": _NOT_FOUND, "card_id": card_id}

        # Normalize: empty string and None both mean "clear the gate", same
        # contract as _is_gated's fail-open behaviour on empty values.
        new_value = (gated_on or "").strip() or None
        existing_meta = dict(card.meta or {})
        if new_value is None:
            existing_meta.pop("gated_on", None)
            action = "cleared"
        else:
            existing_meta["gated_on"] = new_value
            action = "set"

        await apply_operation(
            s, op_type="update", entity_type="card",
            project_key="", entity_id=card_id,
            payload={"metadata": existing_meta},
        )
        await apply_operation(
            s, op_type="comment", entity_type="comment",
            project_key="", entity_id=card_id,
            payload={"text": f"**Gate:** {action} via set_card_gate"
                              + (f" — {new_value}" if new_value else "")},
        )
        await s.commit()
        logger.info("set_card_gate: %s on %s", action, card_id)
        return await _card_dict(s, await service.get_card(s, card_id))


@mcp.tool()
async def request_review(card_id: str, note: str) -> dict:
    """Flag doubt on a *completed* (Done) card and route it to the analyst for triage.

    Posts a `**Review requested:** <note>` comment on the original card, then
    creates a new Backlog card `Review: <title>` with work_type="analysis" (which
    auto-routes to the analyst persona) linked back via metadata.reviewed_card_id.
    The analyst then judges whether the doubt is founded — closing the review card
    with an explanation, or decomposing it into rework child cards.

    Returns the new review card, or {error: not_found} if the card is missing, or
    {error: not_in_done, column: <col>} if the card isn't currently in Done.
    """
    async with KanbanSessionLocal() as s:
        try:
            card = await service.request_review(s, card_id, note)
        except service.CardNotInDone as e:
            return {"error": "not_in_done", "column": e.column}
        if card is None:
            logger.debug("request_review: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        await s.commit()
        logger.info("request_review: %s → review card %s", card_id, card.id)
        return await _card_dict(s, card)


@mcp.tool()
async def reopen_card(card_id: str, note: str) -> dict:
    """Weerleg & heropen: reopen a *completed* (Done) card with a rebuttal.

    Posts a `**Revisit:** <note>` comment on the original card and moves the
    *same* card back to Backlog so the dispatcher re-picks it. Distinct from
    `request_review`: a review spawns a sibling analysis card; a reopen moves
    the existing card back into the dispatch queue. The dispatcher injects the
    rebuttal into the spawned session's prompt via a `## REVISIT` section
    (mirroring `## IMPEDIMENT`), so the next agent sees both the rebuttal and
    the previous decision's summary + deliverable refs. When the original
    session transcript is still on disk, the dispatcher also resumes that
    transcript instead of starting fresh.

    Returns the reopened card, or {error: not_found} if the card is missing,
    or {error: not_in_done, column: <col>} if the card isn't currently in Done.
    """
    async with KanbanSessionLocal() as s:
        try:
            card = await service.reopen_card(s, card_id, note)
        except service.CardNotInDone as e:
            return {"error": "not_in_done", "column": e.column}
        if card is None:
            logger.debug("reopen_card: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        await s.commit()
        logger.info("reopen_card: %s reopened", card_id)
        return await _card_dict(s, card)


@mcp.tool()
async def attach_deliverable(card_id: str, kind: str, ref: str) -> dict:
    """Bind a deliverable (pr|branch|commit|link|note|spec|plan|plan_ref) as a portable reference.

    `spec` is the brainstorming/design-doc companion to `plan`: the
    `brainstorming` skill writes a design-doc, and `attach_deliverable` lets
    it land on the card as a first-class artefact, distinct from the
    analyst plan-attachment. `ref` is the markdown body. Empty `ref` is
    rejected — spec is a markdown body, an empty body would render as a
    blank spec card.

    `plan`/`plan_ref` are wired by their own tools (`add_plan_attachment`),
    but may also be posted through this same endpoint. The
    **intake-correct route** for `plan` on a *childless* card is this tool —
    `add_plan_attachment` requires `child_card_ids` and rejects a card with
    no children, so an intake card that wants to carry a plan deliverable
    must use `attach_deliverable(kind="plan", ref=<markdown body>)` here.
    """
    if not ref:
        return {"error": "invalid_ref", "card_id": card_id,
                "message": "ref must be a non-empty string"}
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("attach_deliverable: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="", entity_id=card_id, payload={"kind": kind, "ref": ref})
        await s.commit()
        logger.info("attach_deliverable: %s kind=%s ref=%s", card_id, kind, ref)
        return await _card_dict(s, await service.get_card(s, card_id))


@mcp.tool()
async def release_card(card_id: str) -> dict:
    """Release a claim on a card."""
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("release_card: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        await apply_operation(s, op_type="release", entity_type="card",
            project_key="", entity_id=card_id, payload={})
        await s.commit()
        logger.info("release_card: %s", card_id)
        return await _card_dict(s, await service.get_card(s, card_id))


@mcp.tool()
async def report_impediment(card_id: str, question: str,
                            options: list[str] | None = None) -> dict:
    """Report an impediment on a card. Moves it to Impediment column with a clear
    question and (optionally) structured candidate answers.

    Use this when you need a human decision: you provide a `question`, plus an
    optional `options` list of structured choices the human can pick from in the
    UI. The card is moved to Impediment and the claim is released — this tool
    does NOT block on an answer; the session ends here. The dispatch loop will
    pick the card back up later; the resume prompt will receive the chosen
    option (or the raw question when no options were supplied) via the existing
    `**Impediment:**` comment + `impediment_question` channel
    (dispatch.build_card_prompt + router.resolve_impediment).

    When `options` is supplied a KanbanGate row is also created in status="open",
    so the kanban UI can render choice buttons on the card in the Impediment
    column. The chosen answer replaces the question in the resumed prompt.

    This is the **standard question flow for all agents** — every human-decision
    request goes here, not through the blocking `open_gate` tool, which would
    keep this session (and its worktree) alive until a human happens to answer.

    Backwards compatible: omitting `options` keeps the legacy free-text path
    (no KanbanGate created).
    """
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("report_impediment: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}

        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=card_id, payload={"column": "Impediment"})

        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=card_id,
            payload={"text": f"**Impediment:** {question}"})

        # When structured options are supplied, also open a gate so the UI can
        # render choice buttons. The gate carries the options + the question
        # verbatim; answer_gate records the human's pick, which resolve_impediment
        # then splices into the resumed session's prompt instead of the raw
        # question text. See service.create_gate / answer_gate / GateResponse.
        if options:
            await service.create_gate(s, card_id=card_id,
                project_key=card.project_key,
                question=question, options=options)

        await apply_operation(s, op_type="release", entity_type="card",
            project_key="", entity_id=card_id, payload={})

        await s.commit()
        logger.info("report_impediment: %s — %s (options=%d)",
                    card_id, question[:80], len(options or []))
        return await _card_dict(s, await service.get_card(s, card_id))


@mcp.tool()
async def open_gate(card_id: str, question: str, options: list[str],
                    timeout_seconds: int = _GATE_DEFAULT_TIMEOUT_SECONDS) -> dict:
    """Open an in-browser decision gate and block until a human answers it.

    Renders `question` (Markdown) with `options` as choice buttons in the
    Kanban UI, and logs it to the card's activity feed. Unlike
    report_impediment, this does NOT release the claim or end the session —
    it simply waits (polling) for the human's pick, then returns it so the
    run can continue inline. Use this for a single decision that shouldn't
    interrupt the flow; use report_impediment when you're truly stuck and
    need to hand off to another agent.

    Args:
        card_id: The card to attach the gate to.
        question: Markdown-rendered question shown to the human.
        options: The structured choices offered (at least one).
        timeout_seconds: How long to wait before giving up (default 30 min).

    Returns {"answer": <chosen option>, "gate_id": ...} once answered, or
    {"error": "timeout", "gate_id": ...} if nobody answers in time — the gate
    stays open, so a human can still answer it later via the UI/API.
    """
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("open_gate: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        gate = await service.create_gate(s, card_id=card_id, project_key=card.project_key,
            question=question, options=options)
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=card_id,
            payload={"text": f"**Gate:** {question}"})
        await s.commit()
        gate_id = gate.id
        logger.info("open_gate: %s opened on %s", gate_id, card_id)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        await asyncio.sleep(_GATE_POLL_INTERVAL_SECONDS)
        async with KanbanSessionLocal() as s:
            current = await service.get_gate(s, gate_id)
            if current is not None and current.status == "answered":
                logger.info("open_gate: %s answered: %s", gate_id, current.answer)
                return {"answer": current.answer, "gate_id": gate_id}

    logger.info("open_gate: %s timed out after %ss", gate_id, timeout_seconds)
    return {"error": "timeout", "gate_id": gate_id}


@mcp.tool()
async def set_resume(card_id: str, session_id: str,
                     project_folder: str | None = None) -> dict:
    """Mark this card to resume an existing Claude session on next dispatch.

    When session_id is set, the next dispatch uses ``claude --resume session_id``
    in the session's original working directory instead of creating a new worktree.
    This lets a new engineer session continue exactly where a context-limited
    session left off.

    Args:
        card_id: The card to tag.
        session_id: The Claude session UUID to resume (from ~/.claude/projects/…/*.jsonl).
        project_folder: Encoded folder name (e.g. "-home-user-repo") that maps to
            ~/.claude/projects/<folder>/.  Inferred from the session file when omitted.
    """
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("set_resume: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        payload: dict = {"resume_session_id": session_id}
        if project_folder is not None:
            payload["resume_project_folder"] = project_folder
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=card_id, payload=payload)
        await s.commit()
        return await _card_dict(s, await service.get_card(s, card_id))


@mcp.tool()
async def redispatch_card(card_id: str, project_path: str, agent: str | None = None) -> dict:
    """Release a stuck card and re-dispatch it with a fresh session.

    Use this when a card is stuck on an agent column (e.g., the agent crashed,
    got stuck, or you want to restart work with a fresh session). This will:
    1. Kill the existing tmux session (if any)
    2. Release the claim
    3. Spawn a new session with the same (or different) agent

    Args:
        card_id: The ID of the card to redispatch
        project_path: The project path for spawning the session
        agent: Optional agent override (uses card's current agent if not specified)
    """
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("redispatch_card: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}

    from app.kanban import dispatch as dispatch_mod

    async with KanbanSessionLocal() as s:
        result = await dispatch_mod.redispatch_card(
            s, card_id=card_id, project_path=project_path,
            agent_override=agent,
        )
        await s.commit()

        if result is None:
            return {"error": _NOT_FOUND, "card_id": card_id}

        logger.info("redispatch_card: %s → session %s", card_id, result.get("session_name"))
        return {
            "ok": True,
            "card_id": card_id,
            "session_name": result.get("session_name"),
        }


MAX_CHILDREN_PER_PLAN = 50


@mcp.tool()
async def add_plan_attachment(
    card_id: str,
    plan_markdown: str,
    child_card_ids: list[str],
    depends_on_graph: dict[str, list[str]] | None = None,
) -> dict:
    """Persist a plan on a parent card and wire `plan_ref` deliverables to each child.

    Args:
        card_id: The parent card id. Must be the parent of every id in
            `child_card_ids` (i.e. each child's `parent_card_id` equals this).
        plan_markdown: The plan as a markdown document.
        child_card_ids: The list of child cards the analyst is delegating to.
        depends_on_graph: A dict {child_card_id: [parent_card_ids_this_depends_on]}
            describing the dependency DAG. Must be acyclic. Each child gets its
            own `depends_on` column set to that list.

    Returns the parent card on success, or an error dict:
        {error: "not_found"} / {error: "parent_mismatch"} /
        {error: "child_not_found"} / {error: "cycle_detected", cycle: [...]} /
        {error: "too_many_children", max: 50}.
    """
    if len(child_card_ids) > MAX_CHILDREN_PER_PLAN:
        return {"error": "too_many_children", "max": MAX_CHILDREN_PER_PLAN}

    deps = depends_on_graph or {}
    cycle = mcp_kanban_deps.detect_cycle(
        {c: list(deps.get(c, []) or []) for c in child_card_ids}
    )
    if cycle is not None:
        return {"error": "cycle_detected", "cycle": cycle}

    async with KanbanSessionLocal() as s:
        from app.kanban.models import KanbanCard
        parent = await s.get(KanbanCard, card_id)
        if parent is None:
            return {"error": _NOT_FOUND, "card_id": card_id}

        # Validate every child exists + has this parent.
        for cid in child_card_ids:
            child = await s.get(KanbanCard, cid)
            if child is None:
                return {"error": "child_not_found", "card_id": cid}
            if child.parent_card_id != card_id:
                return {"error": "parent_mismatch",
                        "card_id": cid, "expected_parent": card_id}

        # Materialize the plan deliverable on the parent.
        project_key = parent.project_key
        await apply_operation(
            s, op_type="add_plan_attachment", entity_type="deliverable",
            project_key=project_key, entity_id=card_id,
            payload={"plan_markdown": plan_markdown},
        )
        plan_deliverable_id = (
            await s.execute(
                select(KanbanDeliverable)
                .where(KanbanDeliverable.card_id == card_id,
                       KanbanDeliverable.kind == "plan")
                .order_by(KanbanDeliverable.created_at.desc())
            )
        ).scalars().first().id

        # Link plan_ref on each child + fan out depends_on.
        for cid in child_card_ids:
            await apply_operation(
                s, op_type="link_plan_ref", entity_type="deliverable",
                project_key=project_key, entity_id=cid,
                payload={"ref_json": json.dumps({
                    "parent_card_id": card_id,
                    "plan_deliverable_id": plan_deliverable_id,
                }), "depends_on": list(deps.get(cid, []) or [])},
            )
        await s.commit()
        return {
            "parent_card_id": card_id,
            "plan_deliverable_id": plan_deliverable_id,
            "child_card_ids": list(child_card_ids),
        }


@mcp.tool()
async def create_project_from_intake(
    intake_card_id: str,
    project_name: str,
    target_path: str,
) -> dict:
    """Promote an intake card on the meta-project to a brand-new project on
    the kanban board.

    Drives the inceptie-pipeline (kanban card c33b2f14, facet A of
    platform-as-app-factory — `docs/cockpit/product-inceptie-pipeline.md`
    §4 optie 2). The action is atomic: any failure between the 6 steps
    rolls back filesystem + kanban-DB + Project row + autodispatch-meta
    so the system is never left half-registered. The intake card lands on
    Done with a `**Promoted to project:** …` comment when the action
    succeeds.

    Steps:
      1. Validate the card is in the `intake` column on its current project.
      2. mkdir `target_path` (refuses to clobber).
      3. `git init --initial-branch=main <target_path>`.
      4. Write minimal `.claude/CLAUDE.md` (placeholder until sibling kanban
         card 395590d lands `BlueprintService.apply()`).
      5. `ProjectService.add_project(name, target_path)`.
      6. `KanbanMeta:autodispatch:<new_project_key>` = enabled.
      7. Create the first kanban card in the new project's Backlog (carrying
         over the intake card's title + description + metadata; with a
         `plan_ref` deliverable linking back to the intake card).
      8. Move the intake card to Done with a `**Promoted to project:** …`
         comment so the meta-project's activity feed shows the birth.

    Args:
        intake_card_id: The id of the intake card to promote. Must be in the
            `intake` column — cards on Backlog/Doing/etc. are rejected.
        project_name: The new project's display name (and `Project.name`).
        target_path: Absolute filesystem path for the new project. Must not
            exist yet; the action refuses to clobber.

    Returns:
        On success: `{"project_id": int, "new_project_key": str,
        "first_card_id": str}`. The new project is reachable as a kanban
        bucket and the dispatcher will pick up the first card on its next
        tick (autodispatch is enabled).

        On failure: `{"error": "<reason>", ...}`. Nothing was registered —
        the action's own rollback ran.
    """
    from app.database import AsyncSessionLocal
    from app.services.inception_service import InceptionService

    try:
        async with KanbanSessionLocal() as ks, AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            result = await svc.create_project_from_intake(
                intake_card_id=intake_card_id,
                project_name=project_name,
                target_path=target_path,
            )
        return result
    except ValueError as e:
        return {"error": "validation_failed", "message": str(e)}
    except FileExistsError as e:
        return {"error": "target_path_exists", "message": str(e)}
    except RuntimeError as e:
        return {"error": "scaffold_failed", "message": str(e)}
