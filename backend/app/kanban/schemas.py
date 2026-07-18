"""Pydantic schemas + the fixed column set."""
from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

# Fixed kanban columns. Cards on a fixed column are never auto-dispatched
# (dispatch pulls from `_DISPATCH_COLUMNS` in dispatch.py, which is the
# explicit "Backlog" + "To Resume" pair). The `intake` column was added
# for the inceptie-pipeline (facet A of platform-as-app-factory,
# `docs/cockpit/product-inceptie-pipeline.md` §4 optie 2): humans put an
# idea on `intake` and the inceptie-action promotes it to a new project
# via `create_project_from_intake`. See sibling kanban card c33b2f14.
#
# When you change this list: the project-level column sync on
# POST /api/v1/kanban/enable creates any missing `kanban_columns` rows.
# Projects enabled BEFORE a new name was added here remain stale until
# either re-enabled or until the matching `ensure_<name>_column` helper
# in service.py runs. See docs/cockpit/kanban-conventions.md §1 for the
# full convention map and scripts/check-kanban-conventions.sh for the
# validator that catches the "stale column" bug class.
COLUMNS = ["intake", "Backlog", "Impediment", "Awaiting Subtasks", "Done", "To Resume"]
DELIVERABLE_KINDS = ["pr", "branch", "commit", "link", "note"]  # the short enum clients validate against; `plan`, `plan_ref`, `spec` are wired by their own tools — see docs/cockpit/kanban-conventions.md §3

# Machine-readable card → spec-doc link (spec-driven-development Fase 1). A
# functional card names the canonical `docs/cockpit/` doc it implements/updates
# by storing its repo-relative path (or a URL) under `card.metadata[SPEC_DOC_META_KEY]`.
# Reuses the existing free-form `metadata` bag — NO new datamodel — so Fase 2
# drift-detection has a single, unambiguous anchor to read. Analyst plan-attachments
# count as the spec by definition, so a card that carries a plan deliverable needs
# no explicit link. See docs/cockpit/spec-driven-development-analysis.md §6 (Fase 1).
SPEC_DOC_META_KEY = "spec_doc"
# Structured routing hint for auto-dispatch. Distinct from `labels` (free-form
# tags). Add new values here AND in frontend/src/features/kanban/types.ts
# WORK_TYPES. See docs/cockpit/work-type-routing-analysis.md §2A.
WORK_TYPES = ["analysis", "feature", "bug", "chore"]

# Work-type → persona routing (see docs/cockpit/work-type-routing-analysis.md §2A).
# `analysis` always routes to the analyst persona so cards marked as pure
# analysis go to the persona specialised in producing a plan/child-cards, while
# `feature` / `bug` / `chore` go to the engineer persona by default. Per-project
# overrides live in `kanban_work_type_mappings`; missing rows fall back to this
# default map (see `get_work_type_persona` in service.py).
WORK_TYPE_PERSONA_DEFAULTS: dict[str, str] = {
    "analysis": "analyst",
    "feature": "engineer",
    "bug": "engineer",
    "chore": "engineer",
}


class DeliverableResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    ref: str
    created_at: datetime


class AttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    filename: str
    mime_type: str
    size_bytes: int
    created_at: datetime


class CardUsageModelBreakdown(BaseModel):
    """Per-model token totals — mirrors the breakdown shape used by
    `/api/v1/usage/sessions` so the frontend can reuse the existing
    breakdown renderer on the card detail panel."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0


class CardUsageResponse(BaseModel):
    """Aggregate token usage for one dispatched card (kanban card 8a2ad986).

    Derived lazily from Claude Code's per-session JSONL transcripts by
    `app.services.dispatch_usage_service.get_card_usage` — the spawned
    session never sees a new tool/turn, so its own token bill is
    unaffected (acceptance criterion #4).
    """

    session_id: str | None = None  # resolved from JSONL stem; None until transcript appears
    recorded_model: str | None = None  # the model the dispatcher recorded at spawn time
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    first_activity: datetime | None = None
    last_activity: datetime | None = None
    model_breakdowns: list[CardUsageModelBreakdown] = []


class RunLedgerTaskStep(BaseModel):
    """Spine step 1 — what the card asked for. Always available: both
    fields already live on the card row (docs/cockpit/run-ledger-decision.md §5)."""
    title: str
    description: str


class RunLedgerContextStep(BaseModel):
    """Spine step 2 — the dispatch prompt the model received.

    `prompt` is reconstructed at request time via `dispatch.build_card_prompt`
    (deterministic, no persistence needed) rather than persisted at dispatch
    time — see decision doc §5 step 2 ("implementatie-keuze voor de
    executor"). The persona preamble is intentionally omitted (it's a large
    static file, not per-run context); `phase`/`ship_mode` and any
    impediment/revisit sections are the parts that vary per run.
    """
    available: bool
    prompt: str | None = None
    phase: str | None = None
    ship_mode: str | None = None
    impediment_question: str | None = None
    impediment_answer: str | None = None
    revisit_question: str | None = None


class RunLedgerFileChange(BaseModel):
    path: str
    insertions: int = 0
    deletions: int = 0


class RunLedgerFilesStep(BaseModel):
    """Spine step 3 — diffstat of the card's `branch` deliverable against
    `origin/master`. Best-effort: no branch deliverable, an unregistered
    project path, or a pruned/merged-away branch ref all yield
    `available=False` + a `note`, never a 500 (decision doc §5 step 3)."""
    available: bool
    branch: str | None = None
    files: list[RunLedgerFileChange] = []
    files_changed: int = 0
    insertions_total: int = 0
    deletions_total: int = 0
    note: str | None = None


class RunLedgerTestsStep(BaseModel):
    """Spine step 4 — verify/CI outcome. `status`/`last_line` come from the
    `iteration-loop` skill's local, gitignored progress file, which is
    routinely gone by the time a card is Done (worktree-gc removes it on
    merge) — that's expected, not an error; `ci_url` (the `pr` deliverable
    ref, if any) is the durable surface. Best-effort (decision doc §5 step 4)."""
    available: bool
    status: str | None = None
    iteration_count: int | None = None
    last_line: str | None = None
    ci_url: str | None = None
    note: str | None = None


class RunLedgerOutcomeStep(BaseModel):
    """Spine step 5 — what was accepted + which model did it. `outcome_text`
    is the newest matching `**Outcome:**`/`**Summary:**`/`**Resolution:**`/
    `**Impediment:**` comment on the activity feed (decision doc §5 step 5);
    `outcome_source` names which prefix matched. Tokens are NOT re-derived
    here — see `RunLedger.usage_url`."""
    column: str
    outcome_text: str | None = None
    outcome_source: str | None = None
    model: str | None = None
    completed_at: datetime | None = None


class RunLedger(BaseModel):
    """`GET /kanban/cards/{cid}/run-ledger` response — the
    task → context → files → tests → outcome+model spine stitched from
    existing durable sources, no new data flow
    (docs/cockpit/run-ledger-decision.md)."""
    card_id: str
    task: RunLedgerTaskStep
    context: RunLedgerContextStep
    files: RunLedgerFilesStep
    tests: RunLedgerTestsStep
    outcome: RunLedgerOutcomeStep
    usage_url: str


class CardSummaryResponse(BaseModel):
    """Compact per-card projection used by `list_cards(compact=True)` so a
    50+ card board stops blowing the MCP token cap during dedupe passes
    (kanban self-improve card d789eb2e…). Only the fields a dedupe pass
    needs to identify / compare cards: id, title, column, work_type, rank.
    No description, deliverables, labels, metadata, or op-log-derived
    enrichments — those are the weight. Backwards-compatible opt-in; the
    full CardResponse shape stays the default."""
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    column: str
    work_type: str | None = None
    rank: str


class CardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_key: str
    title: str
    description: str
    column: str
    rank: str
    priority: str | None = None
    labels: list | None = None
    work_type: str | None = None
    agent: str | None = None
    model: str | None = None
    # Per-agent-column model+provider override:
    #   { "<column-name>": {"model": str|null, "provider": str|null} }
    column_overrides: dict | None = None
    transport: str | None = None  # worktree | sandcastle | auto (null)
    resume_session_id: str | None = None
    resume_project_folder: str | None = None
    scheduled_at: str | None = None  # ISO8601; auto-dispatch ignores the card until this time
    dispatch_failures: int = 0
    release_without_terminal_move: int = 0
    # Per-dispatch telemetry breadcrumbs (kanban card 8a2ad986). All four
    # are written by dispatch._run_card immediately after a successful
    # spawn and read by GET /kanban/cards/{cid}/usage. ISO8601 string to
    # match `scheduled_at` (op-log payload goes through SQLite's JSON
    # column — datetime objects don't round-trip).
    dispatch_started_at: str | None = None
    dispatch_session_id: str | None = None
    dispatch_project_folder: str | None = None
    dispatch_model: str | None = None
    dispatch_provider: str | None = None
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    analyst_agent_id: str | None = None
    executor_agent_id: str | None = None
    parent_card_id: str | None = None
    analyst_run_id: str | None = None
    depends_on: list[str] | None = None
    # Aliased because the SQLAlchemy ORM attribute is `meta` (Declarative API
    # reserves `metadata` on the base class). Field name in JSON stays
    # `metadata` so the API contract matches the kanban card spec; pydantic
    # reads `card.meta` when materializing CardResponse from an ORM instance.
    metadata: dict | None = Field(
        default=None, validation_alias=AliasChoices("meta", "metadata"),
    )
    deliverables: list[DeliverableResponse] = []
    # Screenshots/images attached to the card and injected (by absolute path)
    # into the dispatch prompt so the spawned session can Read them. Populated
    # from the eager-loaded KanbanCard.attachments relationship.
    attachments: list[AttachmentResponse] = []
    # Enrichment derived from the op-log at request time: the summary text
    # + timestamp of the most recent "**Summary:** ..." comment op on this
    # card (the comment that mcp_server.move_card posts when a card lands
    # in Done). Both null when no such op exists. See service.enrich_done_info.
    done_summary: str | None = None
    completed_at: datetime | None = None
    # Classification of *why* the card is in the Impediment column, so the
    # board UI can render a different affordance per cause (operator needs
    # to know "needs an answer" vs. "needs a Redispatch" vs. "no question
    # set"). One of ``"needs_answer"`` / ``"dispatch_failed"`` /
    # ``"resolved"`` / ``"no_question"``; ``None`` for cards outside
    # Impediment. Derived at request time from open KanbanGate rows + the
    # op-log comment feed — see service.impediment_status_for_card.
    impediment_status: str | None = None


class CardCreate(BaseModel):
    project_key: str
    title: str
    description: str = ""
    column: str = "Backlog"
    priority: str | None = None
    labels: list | None = None
    work_type: str | None = None
    agent: str | None = None
    model: str | None = None
    column_overrides: dict | None = None
    transport: str | None = None  # worktree | sandcastle | auto (null)
    resume_session_id: str | None = None
    resume_project_folder: str | None = None
    scheduled_at: str | None = None
    analyst_agent_id: str | None = None
    executor_agent_id: str | None = None
    parent_card_id: str | None = None
    depends_on: list[str] | None = None
    metadata: dict | None = None
    # Explicit opt-in for the first card of a brand-new project. The REST
    # `POST /kanban/cards` handler refuses an unknown `project_key` (the same
    # guard the MCP `create_card` tool applies via `known_project_keys`) — a
    # typo or guessed key used to silently create an orphaned card in a
    # bucket auto-dispatch never sees. Pass `confirm_new_project=True` ONLY
    # when you genuinely mean to create the very first card of a brand-new
    # project; for an existing project, a mistyped key should error, not
    # quietly succeed elsewhere. The normal onboarding path is
    # `POST /kanban/enable` (which seeds columns and makes the key known), so
    # this opt-in is rarely needed. Mirrors MCP `confirm_new_project`.
    confirm_new_project: bool = False


class CardUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    column: str | None = None
    priority: str | None = None
    labels: list | None = None
    work_type: str | None = None
    agent: str | None = None
    model: str | None = None
    column_overrides: dict | None = None
    transport: str | None = None  # worktree | sandcastle | auto (null)
    resume_session_id: str | None = None
    resume_project_folder: str | None = None
    scheduled_at: str | None = None
    analyst_agent_id: str | None = None
    executor_agent_id: str | None = None
    parent_card_id: str | None = None
    analyst_run_id: str | None = None
    depends_on: list[str] | None = None
    metadata: dict | None = None
    # Per-dispatch telemetry breadcrumbs (kanban card 8a2ad986). In
    # practice these are set by dispatch.py, not via PATCH; exposing them
    # so the schema matches the underlying row shape.
    dispatch_started_at: str | None = None
    dispatch_session_id: str | None = None
    dispatch_project_folder: str | None = None
    dispatch_model: str | None = None
    dispatch_provider: str | None = None


class MoveRequest(BaseModel):
    column: str
    rank: str | None = None


class ReorderRequest(BaseModel):
    project_key: str
    column: str
    ordered_ids: list[str]


class ClaimRequest(BaseModel):
    claimed_by: str  # "<session-id>@<device>" or a human label


class CommentRequest(BaseModel):
    text: str


class SetGateRequest(BaseModel):
    """REST mirror of the MCP ``set_card_gate`` tool.

    Mirrors the schema's "MachineReadable gate" contract — ``gated_on=None``
    (or an empty string) lifts the gate; any non-empty string is the opaque
    trigger reason and is written verbatim to ``card.metadata["gated_on"]``
    so future operators and ``flag-problem`` audits can grep for it.

    Distinct from ``CommentRequest``: this writes through ``apply_operation``
    so the change lands on the op-log (and is preserved across device
    replays) rather than acting as a free-text note. The handler also posts
    a ``**Gate:** set/cleared`` activity-feed comment so the gate's
    history is visible without inspecting metadata.
    """
    gated_on: str | None = None


class ReviewRequest(BaseModel):
    """Flag doubt on a completed card and route it to the analyst for triage."""
    note: str


class ReopenRequest(BaseModel):
    """Weerleg & heropen: reopen a Done card with a rebuttal note. The note
    is posted as `**Revisit:**` on the card and the card moves back to
    Backlog so dispatch picks it up. Mirrors `ReviewRequest`'s shape — same
    Done-column gate, distinct semantic."""
    note: str


class AttachRequest(BaseModel):
    kind: str
    # ``spec`` is a markdown-body kind (companion of the analyst ``plan``);
    # an empty body would render as a blank spec card on the board. The
    # other kinds (pr/branch/commit/link/note) also have non-empty refs by
    # construction, so the guard is universally safe.
    ref: str = Field(..., min_length=1)


class UpdatePlanAttachmentRequest(BaseModel):
    plan_markdown: str


class AddPlanAttachmentRequest(BaseModel):
    """REST mirror of the MCP `add_plan_attachment` tool.

    The analyst-fase flow posts this from the UI / scripted REST client as a
    fallback when the MCP layer is unreachable (e.g. cwd of the MCP server was
    removed out from under it by an overzealous `worktree-gc.sh` — see the
    "[problem] worktree-gc verwijdert branch/worktree van actieve analyst-sessie"
    postmortem). The MCP and REST paths share the same op-log; either entry
    point produces an identical board state.
    """
    plan_markdown: str
    child_card_ids: list[str]
    depends_on_graph: dict[str, list[str]] | None = None


class AddPlanAttachmentResponse(BaseModel):
    parent_card_id: str
    plan_deliverable_id: str
    child_card_ids: list[str]


class ActivityEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    hlc: str
    op_type: str
    entity_type: str
    payload: dict
    created_at: datetime


class EnableRequest(BaseModel):
    project_path: str
    slug: str | None = None  # override when no git remote


class AutodispatchRequest(BaseModel):
    project_key: str
    enabled: bool


class ShipModeRequest(BaseModel):
    project_key: str
    mode: str


class SkipPermissionsRequest(BaseModel):
    project_key: str
    enabled: bool


class DefaultTransportRequest(BaseModel):
    project_key: str
    transport: str


class ActiveSubscriptionOverrideRequest(BaseModel):
    """Body for POST /api/v1/kanban/subscription-override.

    ``override`` is the board-wide pin (fase 0 of
    docs/cockpit/subscription-flexibiliteit-analyse.md): routes every dispatch
    onto one subscription regardless of column/card defaults. ``None`` clears
    the pin so dispatch falls back to today's per-column behaviour. The model
    field is optional — a provider-only pin lets the column-default model
    still apply (mirrors the per-card column_override shape).
    """
    project_key: str
    override: dict | None


class SubscriptionPoolEntry(BaseModel):
    """One entry in the subscription pool (fase 1b).

    The pool always routes through the single supported CLI (see
    ``subscription_pool.POOL_CLI``); the ``cli`` field that earlier
    builds carried was dropped in kaart 0b3ad6e2… because the
    dispatcher never consumed it (analysis §3 D3). ``provider`` is the
    vendor the CLI authenticates against (validated against the same
    allow-list as the active-subscription-override). ``model`` is
    optional — ``None`` leaves the dispatch precedence chain (column
    default / card model / persona) to fill it in. ``drempel`` is the
    fraction (0..1] at which the router considers this entry "full"
    and spills to the next entry.

    Legacy payloads that still include a ``cli`` field on each entry
    are accepted by the migration shim in
    ``subscription_pool._deserialize_entries``; the field is silently
    stripped on read so a stored row written by a pre-fix build still
    loads without manual data surgery."""
    provider: str
    model: str | None = None
    drempel: float


class SubscriptionPoolRequest(BaseModel):
    """Body for POST /api/v1/kanban/subscription-pool.

    ``pool`` is the ordered list of entries (priority order = list
    order — first entry is the preferred subscription). ``None``
    clears the pool so dispatch falls back to today's per-column
    behaviour (the backward-compat clause)."""
    project_key: str
    pool: list[SubscriptionPoolEntry] | None = None

    @property
    def entries(self) -> list | None:
        """Materialise the validated ``SubscriptionPoolEntry`` list as
        a list of ``PoolEntry`` so the storage layer can persist it
        without re-parsing the request shape. Returns None when the
        caller asked to clear the pool."""
        from app.kanban.subscription_pool import PoolEntry
        if self.pool is None:
            return None
        return [
            PoolEntry(
                provider=e.provider,
                model=e.model, drempel=e.drempel,
            )
            for e in self.pool
        ]


class DispatchRequest(BaseModel):
    project_path: str
    agent: str | None = None  # override: use this agent instead of card's agent


class CreateProjectFromIntakeRequest(BaseModel):
    """Body for POST /api/v1/kanban/projects/from-intake.

    Drives the inceptie-pipeline (facet A of platform-as-app-factory,
    `docs/cockpit/product-inceptie-pipeline.md` §4 optie 2): an intake card
    on the meta-project becomes a brand-new project on the kanban board in
    one atomic transaction. See kanban card c33b2f14."""
    intake_card_id: str
    project_name: str
    target_path: str  # absolute path; mkdir + git init land here


class CreateProjectFromIntakeResponse(BaseModel):
    """Return shape after a successful inceptie-pipeline promotion."""
    project_id: int
    new_project_key: str
    first_card_id: str


class RedispatchRequest(BaseModel):
    project_path: str
    agent: str | None = None  # override: use this agent instead of card's current agent


class TakeOverRequest(BaseModel):
    project_path: str


# Column management schemas


class ColumnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_key: str
    name: str
    rank: str
    default_agent: str | None = None
    default_provider: str | None = None
    default_model: str | None = None
    max_sessions: int | None = None
    created_at: datetime
    updated_at: datetime


class ColumnCreate(BaseModel):
    project_key: str
    name: str
    rank: str | None = None
    default_agent: str | None = None
    default_provider: str | None = None
    default_model: str | None = None
    max_sessions: int | None = None


class ColumnUpdate(BaseModel):
    name: str | None = None
    rank: str | None = None
    default_agent: str | None = None
    default_provider: str | None = None
    default_model: str | None = None
    max_sessions: int | None = None


class ColumnClearRequest(BaseModel):
    project_key: str
    column: str


class ImpedimentResolveRequest(BaseModel):
    """Request to resolve an impediment."""
    project_path: str
    target_agent: str | None = None  # override auto-detection
    # A human's answer/decision on the blocker. When present it's posted as a
    # durable `**Resolution:**` comment and injected into the resumed session's
    # `## IMPEDIMENT` prompt section — the reliable channel for a human's answer
    # to reach the next dispatched session.
    answer: str | None = None


# Decision gates


class GateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    card_id: str
    project_key: str
    question: str
    options: list[str]
    status: str
    answer: str | None = None
    created_at: datetime
    answered_at: datetime | None = None


class GateOpenRequest(BaseModel):
    question: str
    options: list[str]


class GateAnswerRequest(BaseModel):
    answer: str


# Agent performance dashboard schemas


class AgentStat(BaseModel):
    agent: str
    tasks: int
    completed: int
    failed: int
    in_progress: int
    success_rate: float | None = None
    avg_duration_seconds: float | None = None
    median_duration_seconds: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0


class StatsTotals(BaseModel):
    total_tasks: int
    completed: int
    failed: int
    in_progress: int
    success_rate: float | None = None
    avg_duration_seconds: float | None = None


class FailureStat(BaseModel):
    agent: str | None = None
    reason: str
    count: int


class AgentStatsResponse(BaseModel):
    project_key: str
    totals: StatsTotals
    agents: list[AgentStat]
    common_failures: list[FailureStat]
    tokens_available: bool


# Work-type → persona routing (per-project)


class WorkTypeMappingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_key: str
    work_type: str
    persona: str
    created_at: datetime
    updated_at: datetime


class WorkTypeMappingUpdate(BaseModel):
    """Single work_type → persona override for a project."""
    work_type: str
    persona: str


class WorkTypeMappingBulk(BaseModel):
    """Bulk replace the per-project mapping.

    Any work_type from `WORK_TYPES` that's missing here is reset to the
    default (see `WORK_TYPE_PERSONA_DEFAULTS`). To clear an override, send the
    default persona for that work_type; there is no separate "delete" — a row
    with the default persona is functionally identical to no row.
    """
    project_key: str
    mappings: list[WorkTypeMappingUpdate]
