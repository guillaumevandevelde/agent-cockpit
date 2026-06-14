"""Pydantic schemas for scheduled messages."""
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, model_validator

TriggerType = Literal["once", "cron"]
PermissionMode = Literal["default", "acceptEdits", "bypass"]
Status = Literal["scheduled", "pending_delivery", "delivered", "failed", "cancelled"]
TargetKind = Literal["project", "session"]


class ScheduledMessageCreate(BaseModel):
    target_project: str
    message: str
    trigger_type: TriggerType
    fire_at: Optional[str] = None       # ISO8601, for once
    cron_expr: Optional[str] = None     # for cron
    timezone: str = "Europe/Brussels"
    permission_mode: PermissionMode = "acceptEdits"
    on_missing_session: Literal["spawn", "skip"] = "spawn"
    when_busy: Literal["wait_until_idle", "send_now"] = "wait_until_idle"
    target_kind: TargetKind = "project"
    target_session_id: Optional[str] = None
    project_folder: Optional[str] = None
    session_preview: Optional[str] = None

    @model_validator(mode="after")
    def _check_trigger(self):
        if self.trigger_type == "once" and not self.fire_at:
            raise ValueError("fire_at is required for trigger_type=once")
        if self.trigger_type == "cron" and not self.cron_expr:
            raise ValueError("cron_expr is required for trigger_type=cron")
        if self.target_kind == "session" and (
            not self.target_session_id or not self.project_folder
        ):
            raise ValueError(
                "target_session_id and project_folder are required for target_kind=session"
            )
        return self


class ScheduledMessageUpdate(BaseModel):
    message: Optional[str] = None
    fire_at: Optional[str] = None
    cron_expr: Optional[str] = None
    permission_mode: Optional[PermissionMode] = None
    enabled: Optional[bool] = None


class DeliveryAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    fired_at: datetime
    resolved_session: Optional[str] = None
    action: Optional[str] = None
    wait_duration_s: Optional[int] = None
    delivered_at: Optional[datetime] = None
    outcome: Optional[str] = None
    error: Optional[str] = None


class ScheduledMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_project: str
    message: str
    trigger_type: TriggerType
    fire_at: Optional[str] = None
    cron_expr: Optional[str] = None
    timezone: str
    permission_mode: PermissionMode
    enabled: bool
    status: Status
    target_kind: TargetKind = "project"
    target_session_id: Optional[str] = None
    project_folder: Optional[str] = None
    session_preview: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    last_fired_at: Optional[datetime] = None


class HookEvent(BaseModel):
    """Posted by the CC hook script."""
    event: Literal["UserPromptSubmit", "Stop", "Notification", "SessionStart"]
    session_id: str
    cwd: str
