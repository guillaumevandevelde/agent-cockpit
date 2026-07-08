"""Pydantic schemas + the fixed column set."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict

COLUMNS = ["Backlog", "Impediment", "Done", "To Resume"]
DELIVERABLE_KINDS = ["pr", "branch", "commit", "link", "note"]


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
    agent: str | None = None
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
    deliverables: list[DeliverableResponse] = []


class CardCreate(BaseModel):
    project_key: str
    title: str
    description: str = ""
    column: str = "Backlog"
    priority: str | None = None
    labels: list | None = None
    agent: str | None = None
    transport: str | None = None  # worktree | sandcastle | auto (null)
    resume_session_id: str | None = None
    resume_project_folder: str | None = None
    scheduled_at: str | None = None
    analyst_agent_id: str | None = None
    executor_agent_id: str | None = None
    parent_card_id: str | None = None
    depends_on: list[str] | None = None


class CardUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    column: str | None = None
    priority: str | None = None
    labels: list | None = None
    agent: str | None = None
    transport: str | None = None  # worktree | sandcastle | auto (null)
    resume_session_id: str | None = None
    resume_project_folder: str | None = None
    scheduled_at: str | None = None
    analyst_agent_id: str | None = None
    executor_agent_id: str | None = None
    parent_card_id: str | None = None
    analyst_run_id: str | None = None
    depends_on: list[str] | None = None


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


class AttachRequest(BaseModel):
    kind: str
    ref: str


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


class MaxSessionsRequest(BaseModel):
    project_key: str
    max_sessions: int


class DefaultTransportRequest(BaseModel):
    project_key: str
    transport: str


class DispatchRequest(BaseModel):
    project_path: str
    agent: str | None = None  # override: use this agent instead of card's agent


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
    default_platform: str | None = None
    created_at: datetime
    updated_at: datetime


class ColumnCreate(BaseModel):
    project_key: str
    name: str
    rank: str | None = None
    default_agent: str | None = None
    default_platform: str | None = None


class ColumnUpdate(BaseModel):
    name: str | None = None
    rank: str | None = None
    default_agent: str | None = None
    default_platform: str | None = None


class ColumnClearRequest(BaseModel):
    project_key: str
    column: str


class ImpedimentResolveRequest(BaseModel):
    """Request to resolve an impediment."""
    project_path: str
    target_agent: str | None = None  # override auto-detection


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
