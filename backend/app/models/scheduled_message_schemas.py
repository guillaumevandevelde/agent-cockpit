"""Pydantic schemas for scheduled messages."""
from datetime import datetime, timezone
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, field_serializer, model_validator

TriggerType = Literal["once", "cron"]
PermissionMode = Literal["default", "acceptEdits", "bypass"]
Status = Literal["scheduled", "pending_delivery", "delivered", "failed", "cancelled"]
TargetKind = Literal["project", "session"]


def _as_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """Serialize a naive-UTC datetime as an unambiguous UTC instant.

    Stored timestamps are naive (SQLite drops tzinfo) but always represent UTC.
    Tagging them with an offset stops the browser from reading them as local
    time (which shifted displayed times by the tz offset).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


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

    @field_serializer("fired_at", "delivered_at")
    def _ser_dt(self, dt: Optional[datetime]) -> Optional[str]:
        return _as_utc_iso(dt)


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

    @field_serializer("created_at", "updated_at", "last_fired_at")
    def _ser_dt(self, dt: Optional[datetime]) -> Optional[str]:
        return _as_utc_iso(dt)


class HookEvent(BaseModel):
    """Posted by the CC hook script."""
    event: Literal["UserPromptSubmit", "Stop", "Notification", "SessionStart"]
    session_id: str
    cwd: str
    tmux_pane: Optional[str] = None
    message: Optional[str] = None
