"""Pydantic schemas + the fixed column set."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

COLUMNS = ["Backlog", "Analysis", "Todo", "Doing", "Review", "Done"]
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


class CardUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    labels: Optional[list] = None
    agent: Optional[str] = None


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


class DispatchRequest(BaseModel):
    project_path: str
