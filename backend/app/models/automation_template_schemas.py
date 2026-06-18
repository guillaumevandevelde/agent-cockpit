"""Pydantic schemas for automation templates."""
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field


TriggerType = Literal["cron", "once", "event"]
CategoryType = Literal["review", "monitor", "quality", "deploy", "custom"]


class AutomationTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = None
    category: CategoryType = "general"
    icon: Optional[str] = None
    trigger_type: TriggerType = "cron"
    cron_expr: Optional[str] = None
    message_template: str
    target_projects: Optional[list[str]] = None
    permission_mode: str = "suggest"
    tags: Optional[list[str]] = None


class AutomationTemplateUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = None
    category: Optional[CategoryType] = None
    icon: Optional[str] = None
    trigger_type: Optional[TriggerType] = None
    cron_expr: Optional[str] = None
    message_template: Optional[str] = None
    target_projects: Optional[list[str]] = None
    permission_mode: Optional[str] = None
    enabled: Optional[bool] = None
    tags: Optional[list[str]] = None


class AutomationTemplateResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    category: str
    icon: Optional[str] = None
    trigger_type: str
    cron_expr: Optional[str] = None
    message_template: str
    target_projects: Optional[list[str]] = None
    permission_mode: str
    enabled: bool
    is_builtin: bool
    tags: Optional[list[str]] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
