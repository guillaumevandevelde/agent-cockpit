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
COLUMNS = ["intake", "Backlog", "Impediment", "Done", "To Resume"]
DELIVERABLE_KINDS = ["pr", "branch", "commit", "link", "note"]

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
