"""Pydantic schemas + the fixed column set."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

COLUMNS = ["Backlog", "Impediment", "Done"]
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
    priority: Optional[str] = None
    labels: Optional[list] = None
    agent: Optional[str] = None
    transport: Optional[str] = None  # worktree | sandcastle | auto (null)
    claimed_by: Optional[str] = None
    claimed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    deliverables: list[DeliverableResponse] = []


class CardCreate(BaseModel):
    project_key: str
    title: str
    description: str = ""
    column: str = "Backlog"
    priority: Optional[str] = None
    labels: Optional[list] = None
    agent: Optional[str] = None
    transport: Optional[str] = None  # worktree | sandcastle | auto (null)


class CardUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    column: Optional[str] = None
    priority: Optional[str] = None
    labels: Optional[list] = None
    agent: Optional[str] = None
    transport: Optional[str] = None  # worktree | sandcastle | auto (null)


class MoveRequest(BaseModel):
    column: str
    rank: Optional[str] = None


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
    slug: Optional[str] = None  # override when no git remote


class AutodispatchRequest(BaseModel):
    project_key: str
    enabled: bool


class ShipModeRequest(BaseModel):
    project_key: str
    mode: str


class SkipPermissionsRequest(BaseModel):
    project_key: str
    enabled: bool


class DispatchRequest(BaseModel):
    project_path: str
    agent: Optional[str] = None  # override: use this agent instead of card's agent


class RedispatchRequest(BaseModel):
    project_path: str
    agent: Optional[str] = None  # override: use this agent instead of card's current agent


# Column management schemas


class ColumnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_key: str
    name: str
    rank: str
    default_agent: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ColumnCreate(BaseModel):
    project_key: str
    name: str
    rank: Optional[str] = None
    default_agent: Optional[str] = None


class ColumnUpdate(BaseModel):
    name: Optional[str] = None
    rank: Optional[str] = None
    default_agent: Optional[str] = None


class ColumnClearRequest(BaseModel):
    project_key: str
    column: str


class ImpedimentResolveRequest(BaseModel):
    """Request to resolve an impediment."""
    project_path: str
    target_agent: Optional[str] = None  # override auto-detection


# Agent performance dashboard schemas


class AgentStat(BaseModel):
    agent: str
    tasks: int
    completed: int
    failed: int
    in_progress: int
    success_rate: Optional[float] = None
    avg_duration_seconds: Optional[float] = None
    median_duration_seconds: Optional[float] = None
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
    success_rate: Optional[float] = None
    avg_duration_seconds: Optional[float] = None


class FailureStat(BaseModel):
    agent: Optional[str] = None
    reason: str
    count: int


class AgentStatsResponse(BaseModel):
    project_key: str
    totals: StatsTotals
    agents: list[AgentStat]
    common_failures: list[FailureStat]
    tokens_available: bool
