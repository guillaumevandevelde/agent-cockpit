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
from sqlalchemy import func, select, text

from app.kanban import dep_resolver as mcp_kanban_deps
from app.kanban import service
from app.kanban.db import KanbanSessionLocal
from app.kanban.models import KanbanCard, KanbanDeliverable
from app.kanban.operations import (
    ClaimRejected,
    _cleanup_after_commit,
    apply_operation,
    release_card_claim,
)
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

# permission_prompt serves Claude Code's ``--permission-prompt-tool`` flag and
# has a *stricter* timeout contract than open_gate: an unanswered permission
# prompt holds a worktree + a card claim, and 30 minutes of nothing is not
# acceptable for a mid-run question. Five minutes is a reasonable starting
# ceiling — long enough for a human to context-switch, short enough that a
# forgotten prompt self-resolves to deny before EOD. See
# ``docs/cockpit/approval-privilege-separation-analyse.md`` §5 path 3.
_PERMISSION_PROMPT_DEFAULT_TIMEOUT_SECONDS = 300

# report_impediment's `options` must be exactly this many entries when
# supplied. Kaart 4279448c revisit: the Impediment UI used to pad a
# shorter agent-supplied list up to 4 buttons with a synthetic "Other" filler
# — the human rejected that as not "steeds 4 keuzes" (always 4 choices);
# the 4 buttons must all be agent-proposed. Enforcing the count here, at the
# only place a gate is created for the Impediment column, means the UI never
# has to invent a filler again.
_IMPEDIMENT_OPTION_COUNT = 4

# The fully-qualified MCP tool name Claude Code will pass back via
# ``--permission-prompt-tool``. Format is ``mcp__<server>__<tool>``; the server
# name ``cockpit-kanban`` matches the FastMCP("cockpit-kanban") below and the
# tool name ``permission_prompt`` matches the function name registered via
# ``@mcp.tool()`` further down in this file. Centralising it here keeps the
# dispatch wire-up and the gate producer in lockstep — change one and the
# other goes out of sync visibly.
PERMISSION_PROMPT_TOOL_NAME = "mcp__cockpit-kanban__permission_prompt"


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


async def _unknown_project_key_error(s, project: str, *, for_create: bool) -> dict:
    known = sorted(await service.known_project_keys(s))
    sample = known[:10]
    hint = (
        "Call resolve_project_key(project_path) to get the exact key for an "
        "existing project. If this is genuinely the first card for a "
        "brand-new project, "
        + ("pass confirm_new_project=True." if for_create
           else "use create_card(..., confirm_new_project=True) to create "
                "its first card, then list_cards will see it.")
    )
    return {
        "error": "unknown_project_key",
        "project": project,
        "message": (
            f"No existing cards or columns found for project key {project!r} "
            f"— this is likely a typo or a guessed key. {hint}"
        ),
        "known_project_keys_sample": sample,
    }


@mcp.tool()
async def list_cards(project: str, column: str | None = None,
                     compact: bool = False) -> list[dict] | dict:
    """List cards for a project, optionally filtered by column.

    `project` must be the exact project key — use `resolve_project_key` first
    if you're not certain of it. An unrecognized key (no existing cards or
    columns) returns `{"error": "unknown_project_key", ...}` instead of
    silently returning an empty list from an unrelated (or mistyped) bucket
    — see kanban card 91c85199 for the incident this prevents. If the
    project is genuinely brand-new, create its first card via
    `create_card(..., confirm_new_project=True)` first.

    `compact=True` returns the dedupe-friendly per-card shape
    (id, title, column, work_type, rank) and skips the per-card op-log
    enrichments (done_summary, completed_at, impediment_status) so a
    50+ card Backlog stops blowing the MCP token cap during dedupe passes.
    Default False preserves the full CardResponse shape every existing
    agent expects. Backwards-compatible opt-in.
    """
    async with KanbanSessionLocal() as s:
        known = await service.known_project_keys(s)
        if project not in known:
            return await _unknown_project_key_error(s, project, for_create=False)
        rows = await service.list_cards(s, project, column, compact=compact)
        if compact:
            return [CardSummaryResponse.model_validate(c).model_dump()
                    for c in rows]
        return [await _card_dict(s, c) for c in rows]


@mcp.tool()
async def get_card(card_id: str) -> dict:
    """Get a single card with its deliverables.

    `card_id` accepts either the full 32-char hex id (every existing caller
    does this) or a unique prefix of ≥8 chars — the convention used in
    every doc, comment, and persona prompt (`068845bd…`, `8b3ce64c…`,
    …). The prefix path was added because every cross-reference in this
    codebase uses the shortened form, and the previous exact-only lookup
    forced a full board-fetch + manual prefix filter to recover. The
    behavior matrix:

    - Full 32-char id → that card (existing behavior, unchanged).
    - Unique prefix of ≥8 chars → that card.
    - Prefix shorter than 8 chars → ``{"error": "prefix_too_short",
      "min_length": 8, …}`` (refuse up-front; collision rate climbs
      sharply below the floor on a real-sized board).
    - Prefix that matches ≥2 cards → ``{"error": "ambiguous_card_id",
      "matches": [<full ids>]};`` never silently picks one.
    - No match → ``{"error": "not_found", "message": "…prefixes of ≥8 chars
      are accepted"}`` so the caller doesn't doubt the reference itself.
    """
    async with KanbanSessionLocal() as s:
        result = await service.resolve_card_by_id_or_prefix(s, card_id)
        kind = result["kind"]
        if kind == "exact" or kind == "prefix":
            return await _card_dict(s, result["card"])
        if kind == "prefix_too_short":
            logger.debug("get_card: %s rejected — prefix too short",
                         card_id)
            return {
                "error": "prefix_too_short",
                "card_id": card_id,
                "min_length": result["min_length"],
                "message": (
                    f"card_id prefix must be at least "
                    f"{result['min_length']} characters to keep the "
                    f"collision probability negligible; got "
                    f"{len(card_id)}. Pass the full 32-char id instead, "
                    f"or extend the prefix."
                ),
            }
        if kind == "ambiguous":
            logger.info("get_card: %s ambiguous — %d matches",
                        card_id, len(result["matches"]))
            return {
                "error": "ambiguous_card_id",
                "card_id": card_id,
                "matches": result["matches"],
                "message": (
                    f"card_id prefix {card_id!r} matched "
                    f"{len(result['matches'])} cards. Pass the full "
                    f"32-char id (one of `matches`) instead."
                ),
            }
        # kind == "not_found"
        logger.debug("get_card: %s not found", card_id)
        return {
            "error": _NOT_FOUND,
            "card_id": card_id,
            "message": (
                f"No card matches {card_id!r}. The id may be a typo, the "
                f"card may have been deleted, or the prefix is too short — "
                f"a prefix of ≥{service.CARD_ID_PREFIX_MIN_LENGTH} chars "
                f"is accepted if the rest of the id is correct."
            ),
        }


@mcp.tool()
async def create_card(project: str, title: str, description: str = "",
                      column: str = "Backlog",
                      work_type: str | None = None,
                      agent: str | None = None,
                      parent_card_id: str | None = None,
                      depends_on: list[str] | None = None,
                      labels: list[str] | None = None,
                      metadata: dict | None = None,
                      scheduled_at: str | None = None,
                      confirm_new_project: bool = False) -> dict:
    """Create a new card (agents may decompose work into subtask cards).

    `project` must be the exact project key — use `resolve_project_key` first
    if you're not certain of it. A `project` with no existing cards or
    columns is refused with `{"error": "unknown_project_key", ...}` unless
    `confirm_new_project=True` — this is what closes the "silent orphan
    bucket" half of the incident in kanban card 91c85199 (the other half is
    `list_cards` returning a false-empty list for the same reason). Pass
    `confirm_new_project=True` only when you deliberately mean to create the
    very first card of a brand-new project; for an existing project, a
    mistyped key should error, not quietly succeed elsewhere.

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

    `labels` is the free-form label list rendered on the card (see
    `CardItem.tsx:234`). At create time the value is stored verbatim on the
    new card — there is no previous list to merge with. Omit (or pass
    `None`) to leave the column NULL; pass `[]` to start the card with an
    explicit empty label list. Mirrors `CardCreate.labels`
    (`schemas.py:179`) and round-trips through the create op-log the same
    way `depends_on` does.

    `scheduled_at` is an optional ISO-8601 timestamp (UTC or with offset,
    e.g. ``"2026-08-04T07:00:00+00:00"``); when set, auto-dispatch ignores
    the card until that time (see `dep_resolver.is_due`). This is the
    cadence-chain "successor must sleep until next Monday" knob — without
    it, a `create_card`-followed-by-`PATCH` workflow used to silently land
    the successor as immediately dispatchable (kanban card
    `c7367319b9d245bdbd4cdc2ddc93e134`). An unparseable value is rejected
    with `{"error": "invalid_scheduled_at", "message": …, "card_id": None}`
    so a typo can't reintroduce the bug. Same shape as `CardCreate.scheduled_at`
    in `backend/app/kanban/schemas.py:355`; round-trips through `apply_operation`
    to the `KanbanCard.scheduled_at` column.

    `metadata` is a free-form key/value bag (JSON-serialized) for
    integration-specific data that doesn't deserve its own field — external
    IDs, workflow provenance, last-seen upstream commit sha, etc. Stored as
    a JSON column on the card and round-tripped unchanged on read.
    """
    async with KanbanSessionLocal() as s:
        if not confirm_new_project:
            known = await service.known_project_keys(s)
            if project not in known:
                return await _unknown_project_key_error(s, project, for_create=True)
        # `scheduled_at` validation — the REST `CardCreate` schema only types
        # it as `str | None`, so a typo would otherwise land verbatim and be
        # silently fail-opened by dep_resolver.is_due (which treats an
        # unparseable timestamp as "due now"). That's exactly the half of the
        # cadence-chain incident that motivated this gate (kanban card
        # `c7367319b9d245bdbd4cdc2ddc93e134`): a session that read the create
        # response never noticed the field had been dropped, and the dispatch
        # tick claimed the successor within 10s. We catch it here so the
        # caller sees the error before the card is even created.
        if scheduled_at is not None:
            from datetime import datetime as _dt
            try:
                _dt.fromisoformat(scheduled_at.replace("Z", "+00:00"))
            except (ValueError, TypeError, AttributeError):
                logger.info("create_card: rejected invalid scheduled_at=%r in %s",
                            scheduled_at, project)
                return {
                    "error": "invalid_scheduled_at",
                    "message": (
                        f"scheduled_at must be an ISO-8601 timestamp "
                        f"(e.g. '2026-08-04T07:00:00+00:00'); got {scheduled_at!r}"
                    ),
                    "card_id": None,
                }
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
                     "labels": labels,
                     "metadata": metadata,
                     "scheduled_at": scheduled_at})
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


# The summary/outcome gate lives in ``service.enforce_move_gate`` (kaart
# efbb82e6…) so both this MCP tool and its REST mirror share the same
# validation + error contract. The MCP call here is a thin wrapper that
# translates the exception into the JSON-RPC dict shape. The constants
# ``SUMMARY_REQUIRED_COLUMNS`` / ``OUTCOMES`` / ``OUTCOME_LABELS`` now
# live alongside the gate in ``app.kanban.service``.


@mcp.tool()
async def move_card(card_id: str, column: str,
                    summary: str | None = None,
                    outcome: str | None = None) -> dict:
    """Move a card to a different column.

    Moving into "Done" or "Impediment" requires `summary` — a short account of the
    work that was done (or why it couldn't be finished). It's posted to the card's
    activity feed as a comment so the outcome is visible without opening a
    transcript. Returns {"error": "summary_required"} without moving the card if
    summary is missing/blank for those two columns.

    Product-taal voor `summary` (kaart `4358fe0a…` + kaart `8b3ce64c…`):
    volg de verplichte **drie-delen-vorm** uit
    `docs/cockpit/kanban-conventions.md` §5 — één **Uitkomst**-zin die
    leidt met **één zin productbetekenis** (wat kan de product owner nu
    doen / zien / beslissen dat voorheen niet kon), gevolgd door 2-4
    bullets met de engineering-detail (bestanden, commit, endpoint-
    namen), en optioneel een **Rest / nazicht**-sectie. Daarboven
    gelden de drie proces-regels: **geen proces-meta** in deze
    mens-gerichte samenvatting (geen FCR-uitslag, geen
    session-retro-uitkomst, geen dedup-boekhouding, geen
    audit-log-archeologie — die horen in de activity-feed), **jargon =
    naam + waarom** (een interne component noem je alleen met wat 'ie
    voor de lezer betekent), en lead-with-product-meaning in elke
    openingszin. Voorbeeld: niet "POST /usage/subscription +
    SubscriptionUsageCard.tsx", wél "Product owner kan nu het
    abonnementsverbruik zien op de Usage-pagina (POST /usage/subscription
    + SubscriptionUsageCard.tsx)". Een kale engineering-summary voldoet
    aan deze gate maar niet aan de product-taal-conventie — die lees je
    volledig in `docs/cockpit/kanban-conventions.md` §5.

    For analysis cards (`work_type='analysis'` or `agent='analyst'`) moving to
    `Done`, `outcome` is also required and must be one of:
    ``decomposed`` (verified against ≥1 child card with
    `parent_card_id == card.id`), ``not_feasible`` (canonical label
    `not-feasible` is appended), ``no_action_needed`` (canonical label
    `no-action-needed` is appended), or ``filed_standalone`` (a cadence
    trigger that filed Backlog cards WITHOUT parentage — verified against
    `card.metadata.filed_card_ids`; see §9 of the decision doc below).
    A `**Outcome:** <value> — <summary>` comment is posted in every case;
    `not_feasible` and `no_action_needed` additionally append a canonical
    label. Failure modes are refused without moving the card and return
    one of `{"error": "outcome_required"}`,
    `{"error": "invalid_outcome", "allowed": [...]}`,
    `{"error": "no_children"}` or `{"error": "no_filed_cards"}`.
    Backwards-compatible for non-analysis cards — `outcome` is ignored
    unless both the column is `Done` and `service.is_analyst_leaf_spike(card)`
    is true. See `docs/cockpit/analysis-outcome-contract-decision.md` for
    the rationale.

    Parent-parking: any card (not just analysis cards) moving to `Done`
    while it has ≥1 child card (`parent_card_id == card.id`) lands in
    `Awaiting Subtasks` instead — the returned `column` reflects that. It
    auto-closes to `Done` once every child card reaches `Done`, which also
    walks up a chain of nested parents. See
    `docs/cockpit/analyse-levenscyclus-decision.md` §3.

    Routing-to-Impediment gate (kaart b8e3ac8b… decision A): moving a card
    straight to `Impediment` via `move_card` is rejected — fire
    `report_impediment` instead, which is the only call that opens a
    KanbanGate and renders the 4-button picker. `move_card(column="Impediment")`
    posted a `**Impediment:** <summary>` comment but never opened a gate,
    so `ResolveImpedimentControl` rendered `hasChoiceRow === false` —
    the exact blank 0-button screen the product owner complained about
    for three rounds. Even a perfectly-written `summary` on this path is
    refused; the issue is the missing gate, not the missing prose.
    """
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("move_card: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}

        # Shared gate (kaart efbb82e6…). The same validation runs on the
        # REST mirror of this tool — ``enforce_move_gate`` is the single
        # source of truth so the two callers can't drift. On refusal we
        # translate the exception back to the MCP wire shape
        # (``{"error": code, "message": …, "allowed": [...]}``); on
        # success we get back the cleaned inputs the rest of this
        # function already needs.
        try:
            cleaned_summary, cleaned_outcome = await service.enforce_move_gate(
                s, card, column, summary, outcome
            )
        except service.MoveGateRejected as e:
            logger.info("move_card: %s rejected — %s", card_id, e.error_code)
            out: dict = {"error": e.error_code, "message": e.message}
            if e.allowed is not None:
                out["allowed"] = e.allowed
            return out
        summary = cleaned_summary
        outcome = cleaned_outcome

        # ``summary`` and ``outcome`` were reassigned to the cleaned values
        # returned by ``enforce_move_gate`` earlier in this function. The
        # routing-to-Impediment gate (kaart b8e3ac8b… decision A) is also
        # enforced upstream and cannot reach here — so no second check is
        # needed.

        # Parent-parking (docs/cockpit/analyse-levenscyclus-decision.md §3):
        # a Done move for a card with ≥1 child doesn't actually leave the
        # board — it parks in `Awaiting Subtasks` until every child reaches
        # Done. Parent-generic (§3.1: "heeft kinderen", not
        # work_type=='analysis') and shares this interception point with
        # the outcome gate above (§6) — outcome='decomposed' already
        # verified ≥1 child exists, so that path always redirects here too.
        final_column = column
        if column == "Done" and await service.card_has_children(s, card_id):
            final_column = "Awaiting Subtasks"
            if card.project_key:
                await service.ensure_awaiting_subtasks_column(s, card.project_key)

        # Independent reviewer gate (docs/cockpit/reviewer-agent-decision.md,
        # REVISED 2026-07-18): when the project has a `reviewer` column, a card
        # reaching *genuine* Done is first routed through the reviewer for an
        # independent, board-enforced feature-compliance + consistency check —
        # the engineer cannot skip it because the redirect happens here, not in
        # the persona prompt. The card's agent is flipped to `reviewer` so the
        # dispatcher spawns the reviewer persona (a `reviewer` column alone
        # isn't enough — `_phase_target_agent` reads `card.agent` first), and
        # the persona that did the work is stashed so a rejection resumes *it*.
        # Excluded: the reviewer's own Done move (else it loops forever) and
        # analysis cards (their outcome contract + child cards are the review
        # surface). Parent cards already parked in Awaiting Subtasks above never
        # reach `final_column == "Done"`, so they're excluded too.
        gated_to_review = False
        if (final_column == "Done"
                and card.agent != service.REVIEWER_COLUMN
                and not service.is_analyst_leaf_spike(card)
                and await service.reviewer_column_exists(s, card.project_key)):
            gated_to_review = True
            final_column = service.REVIEWER_COLUMN
            from app.kanban.schemas import COLUMNS
            return_agent = (
                card.column if card.column not in COLUMNS
                else (card.agent or "engineer")
            )
            new_meta = dict(card.meta or {})
            new_meta[service.REVIEW_RETURN_AGENT_KEY] = return_agent
            await apply_operation(s, op_type="update", entity_type="card",
                project_key="", entity_id=card_id,
                payload={"agent": service.REVIEWER_COLUMN, "metadata": new_meta})

        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=card_id, payload={"column": final_column})
        # Shared side-effects (kaart efbb82e6…). ``apply_move_summary_comment``
        # only fires when the destination column requires one (Done). The
        # outcome helper does its own label-append + ``**Outcome:**`` comment
        # — same place the REST mirror now uses. Materialising BEFORE commit
        # is intentional: a partial state (label set, card not moved, or vice
        # versa) cannot land on disk.
        #
        # Note: the Summary comment + outcome side-effects fire based on
        # ``column == "Done"`` (the operator's intent), not ``final_column``
        # — a parent card with children still parks in Awaiting Subtasks but
        # the **Outcome:** comment lands too, so the activity feed captures
        # the analyst's intent regardless of where the card actually ended
        # up. This matches the pre-refactor behaviour and the existing test
        # ``test_move_analysis_card_to_done_decomposed_with_children_is_allowed``.
        is_analysis_done = (
            column == "Done" and service.is_analyst_leaf_spike(card)
        )
        await service.apply_move_summary_comment(
            s, card_id, column, summary
        )
        if is_analysis_done and outcome is not None:
            await service.apply_outcome_side_effects(
                s, card, outcome, summary
            )

        if gated_to_review:
            await apply_operation(s, op_type="comment", entity_type="comment",
                project_key="", entity_id=card_id,
                payload={"text": (
                    "**Review:** Routed to the reviewer for an independent "
                    "feature-compliance + consistency check before Done. The "
                    "reviewer either approves (→ Done) or reports an impediment "
                    "explaining why it's not in order."
                )})
            # The engineer's session finished its work by issuing this move, so
            # tear it down (tmux + worktree + claim release) exactly as a real
            # Done would. Releasing the claim also turns the card into an
            # unclaimed orphan in the reviewer column, which the dispatcher then
            # re-picks up as a fresh, cleared-context reviewer session.
            _cleanup_after_commit(s, card_id, card.project_key, card.claimed_by)

        # Auto-close (§3.2): this card actually reached Done (not parked) —
        # if it's someone's child, check whether that parent can now close
        # too, and walk up the chain for nested decomposition.
        if final_column == "Done" and card.parent_card_id:
            pid = card.parent_card_id
            while pid:
                closed = await service.close_parent_if_all_children_done(s, pid)
                if not closed:
                    break
                grandparent = await s.get(KanbanCard, pid)
                pid = grandparent.parent_card_id if grandparent else None

        await s.commit()
        logger.info("move_card: %s → %s", card_id, final_column)
        return await _card_dict(s, await service.get_card(s, card_id))


@mcp.tool()
async def update_card(card_id: str, title: str | None = None,
                      description: str | None = None,
                      depends_on: list[str] | None = None,
                      labels: list[str] | None = None,
                      metadata: dict | None = None) -> dict:
    """Update a card's title, description, depends_on, labels, and/or metadata bag.

    Same "skip-when-None" semantics as the existing title/description paths:
    None means "don't touch". To clear an existing value via MCP, pass the
    empty list `[]` for list-typed fields (labels / depends_on) — `None`
    is the "field absent" sentinel and leaves the stored value alone. For
    the REST PATCH endpoint, `exclude_unset` distinguishes "absent" from
    "set to null" on the wire.

    `depends_on` replaces the card's sibling-dep list — pass a `list[str]`
    to set, leave `None` to keep the current value. The dispatcher uses
    this list to gate dispatch on the named cards reaching a terminal
    state. Mirrors the REST `PATCH /api/v1/kanban/cards/{cid}` path's
    handling of `CardUpdate.depends_on` (see
    `backend/app/api/v1/kanban/router.py:329-360` →
    `apply_operation("update")` → `_materialize` setting
    `card.depends_on = payload["depends_on"]`).

    `labels` replaces the card's label list — **not** append. Pass
    `labels=["urgent", "backend"]` to set those two labels and drop any
    others; pass `labels=[]` to clear the labels column; leave `None` to
    keep the existing list. This matches `CardUpdate.labels`
    (`schemas.py:200`) and the existing `depends_on` replace semantics
    so an agent can use the same mental model for both list-typed fields.
    """
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("update_card: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        payload = {k: v for k, v in {"title": title, "description": description,
                                     "depends_on": depends_on,
                                     "labels": labels,
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
    but may also be posted through this same endpoint. The **correct route**
    for `plan` on a *childless* card is this tool — `add_plan_attachment`
    requires `child_card_ids` and rejects a card with no children, so a
    childless card that wants to carry a plan deliverable must use
    `attach_deliverable(kind="plan", ref=<markdown body>)` here.
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
    """Release a claim on a card.

    A bare release with no accompanying move to Done/Impediment is tracked as
    claim->release churn (kanban card 49626139); repeating it without ever
    finishing the card auto-flags it to Impediment for human triage instead of
    letting auto-dispatch keep re-claiming it forever.
    """
    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("release_card: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        await release_card_claim(s, card_id=card_id, project_key=card.project_key)
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

    When supplied, `options` must contain exactly 4 entries — the Impediment
    UI always renders 4 choice buttons, and all 4 must be your own proposed
    answers (kaart 4279448c revisit: a UI-injected "Other" filler used to pad
    a shorter list, which the human rejected). If you have fewer than 4
    genuine alternatives, add plausible ones yourself (even a deliberately
    weaker one) to reach 4, or omit `options` entirely to ask a free-text
    question instead. Supplying 1-3 options is rejected with
    `error: "invalid_option_count"` and the call has no effect — retry with
    exactly 4 or none.

    When `options` is supplied a KanbanGate row is also created in status="open",
    so the kanban UI can render choice buttons on the card in the Impediment
    column. The chosen answer replaces the question in the resumed prompt.

    This is the **standard question flow for all agents** — every human-decision
    request goes here, not through the blocking `open_gate` tool, which would
    keep this session (and its worktree) alive until a human happens to answer.

    Product-taal voor `options` (kaart `4358fe0a…` + kaart `8b3ce64c…`):
    `options` drukken **producttrade-offs** uit, geen
    implementatie-forks. De product owner beslist op gevolg, niet op
    techniek. Voorbeeld: niet "APScheduler of Celery"; wél "A: sneller
    live, meer onderhoud later — B: trager live, minder onderhoud". De
    `question` zelf omschrijft ook liever het product-fork ("Hoe lossen
    we de scheduler-trap op?") dan het techniek-fork ("Welke scheduler
    kiezen we?"). En net als bij de Done-`summary` gelden de drie
    proces-regels voor élke mens-gerichte tekst: **geen proces-meta**
    in de vraag (geen FCR-uitslag, geen audit-log-archeologie — die
    horen in de activity-feed), **jargon = naam + waarom** (een interne
    component noem je alleen met wat 'ie voor de lezer betekent), en
    lead-with-product-meaning in elke openingszin. De
    product-taal-conventie volledig: lees
    `docs/cockpit/kanban-conventions.md` §5.

    Backwards compatible: omitting `options` keeps the legacy free-text path
    (no KanbanGate created).
    """
    if options is not None and len(options) != _IMPEDIMENT_OPTION_COUNT:
        logger.info(
            "report_impediment: %s rejected — options=%d (need exactly %d)",
            card_id, len(options), _IMPEDIMENT_OPTION_COUNT,
        )
        return {
            "error": "invalid_option_count",
            "message": (
                f"options must contain exactly {_IMPEDIMENT_OPTION_COUNT} "
                f"entries (got {len(options)}). Supply "
                f"{_IMPEDIMENT_OPTION_COUNT} agent-proposed choices, or omit "
                "`options` entirely to ask a free-text question instead."
            ),
            "card_id": card_id,
        }

    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("report_impediment: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}

        # Reviewer-gate return routing: when a reviewer rejects a card it
        # stashed a `review_return_agent` (the persona that produced the work)
        # on redirect into the reviewer column. Restore that agent before the
        # move so the human's impediment answer resumes *that* persona (the
        # engineer, to fix the work) instead of re-running the reviewer against
        # unchanged code. Only fires for reviewer-agent cards; every other
        # caller is untouched. See docs/cockpit/reviewer-agent-decision.md.
        return_agent = (card.meta or {}).get(service.REVIEW_RETURN_AGENT_KEY)
        if card.agent == service.REVIEWER_COLUMN and return_agent:
            new_meta = {
                k: v for k, v in (card.meta or {}).items()
                if k != service.REVIEW_RETURN_AGENT_KEY
            }
            await apply_operation(s, op_type="update", entity_type="card",
                project_key="", entity_id=card_id,
                payload={"agent": return_agent, "metadata": new_meta})

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
async def permission_prompt(card_id: str, tool_name: str,
                            tool_input: dict,
                            timeout_seconds: int = _PERMISSION_PROMPT_DEFAULT_TIMEOUT_SECONDS) -> dict:
    """Open a permission-prompt gate and block until a human approves or denies.

    Producer-side wiring for Claude Code's ``--permission-prompt-tool`` flag
    (kaart 5278a5bd…). When the dispatch layer spawns a Claude session with
    ``--permission-prompt-tool mcp__cockpit-kanban__permission_prompt``, every
    permission decision that requires human input is routed to this tool. We
    reuse the existing ``KanbanGate`` primitive (no new gate-shaped datamodel)
    and return Claude Code's expected ``{"behavior": "allow"}`` /
    ``{"behavior": "deny", "message": "..."}`` shape so the model can react
    inline — a denial becomes a tool-error in the run, a timeout closes
    fail-closed to deny, and a successful approval lets Claude Code execute
    the underlying call on its normal path.

    The four paths from analysis doc §5:

      1. *Allow* — human picks ``allow``: returns ``{"behavior": "allow",
         "gate_id": ...}``. The originating tool call runs.
      2. *Deny* — human picks ``deny``: returns ``{"behavior": "deny",
         "message": ..., "gate_id": ...}``. Claude Code surfaces the message
         as a tool-error in the run; the agent adapts, picks another route,
         or escalates via ``report_impediment``. **Run continues; session
         lives** (analysis §5 path 2).
      3. *Timeout* — nobody answers within ``timeout_seconds``: returns
         ``{"behavior": "deny", "message": "no human response within Xs,
         defaulting to deny", "gate_id": ...}``. **Fail-closed** — a stalled
         permission prompt holds a worktree + a card claim; an unanswered
         prompt must self-resolve to deny rather than leave the gate open the
         way ``open_gate`` does for product decisions. Distinct from
         ``open_gate``'s timeout-on-purpose (analysis §5 path 3).
      4. *Approved action fails* — handled by Claude Code itself; this tool
         only decides *whether* the underlying call runs, not *whether it
         succeeds*. A failure surfaces as a normal ``tool_result`` error in
         the run (analysis §3.3 / §5 path 4).

    Invariant (AC4): none of the four paths stalls or kills a session. The
    session keeps running after the answer is delivered; the claim is not
    released; the card is not moved. ``report_impediment`` is for product
    decisions (sessie eindigt); this is for permission decisions (sessie
    blijft). See analysis doc §4 for the rolverdeling.

    Layering — runs *after* the platform Inference-hooks layer (Anthropic
    Enterprise beta, aug 2026). Inference hooks beoordelen de hele governed
    prompt server-side vóór inferentie; CC 2.1.222's auto-mode classifier
    beoordeelt de tool-call voordat die dit gate bereikt; deze tool geeft
    de lokale mens het laatste woord op één tool-call. Een Inference-hook-deny
    stopt de tool-call niet eens — de hele prompt sterft eerder. Een deny hier
    stopt alleen de specifieke tool-call die de classifier als ``ask`` heeft
    gemarkeerd. Drie lagen chainen, ze concurreren niet. Zie
    ``docs/cockpit/inference-hooks-vs-permission-prompt.md`` voor de
    composability-tabel en de afbakening.

    The gate renders in the kanban-UI via ``gate.question`` (Markdown), which
    carries both the tool name and a JSON dump of the args so a human can
    see exactly which call they're being asked to approve.

    Args:
        card_id: The card on whose behalf the prompt is being opened.
        tool_name: The Claude Code tool name (e.g. ``Write``, ``Bash``).
        tool_input: The args Claude Code is about to pass to that tool.
        timeout_seconds: Bound on how long to wait for the human. Default
            300s (analysis §5 path 3). Distinct from ``open_gate``'s 1800s.

    Returns Claude Code's ``--permission-prompt-tool`` contract shape, never
    a bare dict with no ``behavior`` key (which Claude Code would treat as
    a parse error and re-prompt on).
    """
    import json as _json

    # Render the gate question so the UI shows tool + args. JSON keeps the
    # args readable; the tool name gets a fenced code span so it stands out
    # in the Markdown render. The question stays short enough to fit on one
    # screen in the CardDrawer.
    try:
        args_json = _json.dumps(tool_input, sort_keys=True, default=str)
    except (TypeError, ValueError):
        args_json = repr(tool_input)
    question = (
        f"**Permission requested** — Claude Code wants to call ``{tool_name}``.\n\n"
        f"```json\n{args_json}\n```"
    )

    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("permission_prompt: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        gate = await service.create_gate(s, card_id=card_id,
            project_key=card.project_key,
            question=question, options=["allow", "deny"])
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=card_id,
            payload={"text": f"**Permission prompt:** `{tool_name}` — awaiting human decision."})
        await s.commit()
        gate_id = gate.id
        logger.info("permission_prompt: %s opened on %s (tool=%s)",
                    gate_id, card_id, tool_name)

    # Poll for the answer. Same lightweight loop as open_gate (2s normally,
    # 0.01s in tests via the autouse fixture in test_permission_prompt_tool.py).
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        await asyncio.sleep(_GATE_POLL_INTERVAL_SECONDS)
        async with KanbanSessionLocal() as s:
            current = await service.get_gate(s, gate_id)
            if current is not None and current.status == "answered":
                answer = current.answer
                logger.info("permission_prompt: %s answered: %s", gate_id, answer)
                if answer == "allow":
                    return {"behavior": "allow", "gate_id": gate_id}
                # answer == "deny" (options are fixed; answer_gate validates).
                return {
                    "behavior": "deny",
                    "message": "denied by human reviewer",
                    "gate_id": gate_id,
                }

    # Path 3 — fail-closed timeout. Deliberate divergence from open_gate's
    # "stay open" semantics: an unanswered permission prompt must self-resolve
    # to deny (analysis §5 path 3), or the session stalls permanently.
    logger.info("permission_prompt: %s timed out after %ss — fail-closed to deny",
                gate_id, timeout_seconds)
    return {
        "behavior": "deny",
        "message": (
            f"no human response within {timeout_seconds}s, "
            "defaulting to deny (fail-closed)"
        ),
        "gate_id": gate_id,
    }


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
    from datetime import UTC, datetime, timedelta

    from app.utils.timeutils import ensure_aware

    # Resume-race guard window: stamps a near-future `scheduled_at` so the
    # same dispatch-sweep pass that races the write defers the card via the
    # existing `_is_due` gate. Without this guard, an in-flight dispatch tick
    # whose `list_cards` read predates this commit dispatches the card with
    # the worktree transport (brand-new worktree + session) — exactly
    # defeating the operator's intent. See kanban card
    # `[self-improve] set_resume races a fresh auto-dispatch`.
    #
    # Small (2s) on purpose: just enough to outlive the dispatch tick that
    # raced the write, not so long that a same-tick operator action gets
    # delayed perceptibly. The companion re-read in `dispatch._run_card`
    # catches the converse race (`set_resume` landing between the reaper's
    # list refresh and `_run_card`), so this guard is defense-in-depth, not
    # a replacement.
    RESUME_RACE_GUARD_S = 2

    async with KanbanSessionLocal() as s:
        card = await _require_card(s, card_id)
        if card is None:
            logger.debug("set_resume: %s not found", card_id)
            return {"error": _NOT_FOUND, "card_id": card_id}
        payload: dict = {"resume_session_id": session_id}
        if project_folder is not None:
            payload["resume_project_folder"] = project_folder

        # Don't overwrite an existing future `scheduled_at` — that schedule
        # is intentional (e.g. a reaper fallback set it to "next hour"
        # because no resumable worktree was found) and the operator's resume
        # stamp should layer on top without re-scheduling.
        existing = card.scheduled_at
        needs_guard = True
        if existing:
            try:
                if ensure_aware(datetime.fromisoformat(existing)) > datetime.now(UTC):
                    needs_guard = False
            except ValueError:
                # Unparseable scheduled_at — let _is_due's fail-open handle it
                # and stamp the guard as usual.
                pass
        if needs_guard:
            payload["scheduled_at"] = (
                datetime.now(UTC) + timedelta(seconds=RESUME_RACE_GUARD_S)
            ).isoformat()

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
            # The MCP-tool entry-point vs. the REST/UI one is the operator's
            # primary triage question (see kaart [self-improve] Redispatch-
            # trigger-bron onzichtbaar). A static `mcp` label discriminates
            # that; plumbing the per-call `Context.session_id` through to
            # disambiguate concurrent MCP callers can come later if anyone
            # needs it — the activity-feed contract is "what entry-point",
            # not "which caller".
            caller_source="mcp",
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

    Requires at least one child — for a childless card, use
    `attach_deliverable(kind="plan")` instead.

    Returns on success:
        {"parent_card_id": "...", "plan_deliverable_id": "...",
         "child_card_ids": [...],
         "plan_refs": {child_card_id: plan_ref_deliverable_id, ...}}.

    The ``plan_refs`` map echoes the freshly wired ``plan_ref`` deliverable id
    per child so the caller can verify the write landed without re-fetching
    each child. Mirrors the REST `POST /cards/{id}/plan-attachment` response
    shape (backend/app/kanban/schemas.py::AddPlanAttachmentResponse) — same
    op-log, same validation, same return.

    On failure returns an error dict:
        {error: "not_found"} / {error: "no_children"} / {error: "parent_mismatch"} /
        {error: "child_not_found"} / {error: "cycle_detected", cycle: [...]} /
        {error: "too_many_children", max: 50}.
    """
    if not child_card_ids:
        return {"error": "no_children", "card_id": card_id,
                "message": "add_plan_attachment requires at least one child card; "
                            "for childless cards use attach_deliverable(kind='plan')."}

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
        plan_refs: dict[str, str] = {}
        for cid in child_card_ids:
            await apply_operation(
                s, op_type="link_plan_ref", entity_type="deliverable",
                project_key=project_key, entity_id=cid,
                payload={"ref_json": json.dumps({
                    "parent_card_id": card_id,
                    "plan_deliverable_id": plan_deliverable_id,
                }), "depends_on": list(deps.get(cid, []) or [])},
            )
            # Capture the freshly wired plan_ref deliverable id so the caller
            # can verify the write landed without re-fetching the child card.
            plan_ref_id = (
                await s.execute(
                    select(KanbanDeliverable)
                    .where(KanbanDeliverable.card_id == cid,
                           KanbanDeliverable.kind == "plan_ref")
                    .order_by(KanbanDeliverable.created_at.desc())
                )
            ).scalars().first().id
            plan_refs[cid] = plan_ref_id
        await s.commit()
        return {
            "parent_card_id": card_id,
            "plan_deliverable_id": plan_deliverable_id,
            "child_card_ids": list(child_card_ids),
            "plan_refs": plan_refs,
        }


@mcp.tool()
async def create_project_from_interview(
    project_name: str,
    target_path: str,
    title: str,
    description: str,
    spec_md: str,
    plan_md: str,
) -> dict:
    """Cardless inceptie-flow (kanban card b9e6365a…,
    `docs/cockpit/kaartloze-app-inceptie-decision.md` optie 3): an
    interactive interview produces spec + plan + title + description, and
    that bundle becomes a brand-new project on the kanban board in one
    atomic transaction.

    This is the only birth route — no meta-board card is involved. The
    first kanban card in the new project carries ``metadata["spec_doc"]``
    pointing at the design doc so the spec-driven-development pipeline can
    trace it.

    The action is atomic: any failure rolls back filesystem (rm -rf the
    target dir), the Project row, the autodispatch-meta, and the partial
    first kanban card so the system is never left half-registered. Empty
    ``spec_md`` or ``plan_md`` is rejected before any side effect.

    Steps:
      1. Validate ``spec_md`` / ``plan_md`` are non-empty.
      2. mkdir + pre-check ``Project`` row.
      3. `git init --initial-branch=main <target_path>`.
      4. `BlueprintService.apply()` for `.claude/` + CLAUDE.md seed.
      5. Write LICENSE + ``docs/specs/<date>-<slug>-design.md`` +
         ``docs/plans/<date>-<slug>-plan.md``; capture in one first commit.
      6. `ProjectService.add_project(name, target_path)`.
      7. `KanbanMeta:autodispatch:<new_project_key>` =
         `policy.autodispatch_default` (default off — security-default-deny;
         the route has no human-in-the-loop, so the caller must opt in via
         an explicit policy if they want a born-running dispatcher).
      8. Create the first kanban card in the new project's Backlog (title +
         description from the payload; ``metadata[spec_doc]`` set).

    Args:
        project_name: The new project's display name (and `Project.name`).
            Also feeds the slug for the spec/plan file paths.
        target_path: Absolute filesystem path for the new project. Must not
            exist yet; the action refuses to clobber.
        title: Title for the first kanban card in the new project's Backlog.
        description: Description for the first kanban card.
        spec_md: Markdown body of the design doc (non-empty). Lands at
            ``docs/specs/<YYYY-MM-DD>-<slug>-design.md`` before the first
            commit.
        plan_md: Markdown body of the implementation plan (non-empty). Lands
            at ``docs/plans/<YYYY-MM-DD>-<slug>-plan.md``.

    Returns:
        On success: `{"project_id": int, "new_project_key": str,
        "first_card_id": str}`. The new project is reachable as a kanban
        bucket; the dispatcher picks the first card up on its next tick
        (autodispatch follows the ``BootstrapPolicy`` default — off, since
        there's no human-in-the-loop on this route).

        On failure: `{"error": "<reason>", ...}`. Nothing was registered —
        the action's own rollback ran.
    """
    from app.database import AsyncSessionLocal
    from app.services.inception_service import InceptionService

    try:
        async with KanbanSessionLocal() as ks, AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            result = await svc.create_project_from_interview(
                project_name=project_name,
                target_path=target_path,
                title=title,
                description=description,
                spec_md=spec_md,
                plan_md=plan_md,
            )
        return result
    except ValueError as e:
        return {"error": "validation_failed", "message": str(e)}
    except FileExistsError as e:
        return {"error": "target_path_exists", "message": str(e)}
    except RuntimeError as e:
        return {"error": "scaffold_failed", "message": str(e)}
