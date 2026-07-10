"""Pydantic schemas for scheduled messages."""
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_serializer, model_validator

TriggerType = Literal["once", "cron"]
PermissionMode = Literal["default", "acceptEdits", "bypass"]
Status = Literal["scheduled", "pending_delivery", "delivered", "failed", "cancelled"]
TargetKind = Literal["project", "session", "sandcastle"]


def _as_utc_iso(dt: datetime | None) -> str | None:
    """Serialize a naive-UTC datetime as an unambiguous UTC instant.

    Stored timestamps are naive (SQLite drops tzinfo) but always represent UTC.
    Tagging them with an offset stops the browser from reading them as local
    time (which shifted displayed times by the tz offset).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


class ScheduledMessageCreate(BaseModel):
    target_project: str
    message: str
    trigger_type: TriggerType
    fire_at: str | None = None       # ISO8601, for once
    cron_expr: str | None = None     # for cron
    timezone: str = "Europe/Brussels"
    permission_mode: PermissionMode = "acceptEdits"
    on_missing_session: Literal["spawn", "skip"] = "spawn"
    when_busy: Literal["wait_until_idle", "send_now"] = "wait_until_idle"
    target_kind: TargetKind = "project"
    target_session_id: str | None = None
    project_folder: str | None = None
    session_preview: str | None = None
    sandcastle_config_id: int | None = None

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
        if self.target_kind == "sandcastle" and not self.sandcastle_config_id:
            raise ValueError(
                "sandcastle_config_id is required for target_kind=sandcastle"
            )
        return self


class BulkDeleteRequest(BaseModel):
    ids: list[int]


class ScheduledMessageUpdate(BaseModel):
    message: str | None = None
    fire_at: str | None = None
    cron_expr: str | None = None
    permission_mode: PermissionMode | None = None
    enabled: bool | None = None


class DeliveryAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    fired_at: datetime
    resolved_session: str | None = None
    action: str | None = None
    wait_duration_s: int | None = None
    delivered_at: datetime | None = None
    outcome: str | None = None
    error: str | None = None

    @field_serializer("fired_at", "delivered_at")
    def _ser_dt(self, dt: datetime | None) -> str | None:
        return _as_utc_iso(dt)


class ScheduledMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_project: str
    message: str
    trigger_type: TriggerType
    fire_at: str | None = None
    cron_expr: str | None = None
    timezone: str
    permission_mode: PermissionMode
    enabled: bool
    status: Status
    target_kind: TargetKind = "project"
    target_session_id: str | None = None
    project_folder: str | None = None
    session_preview: str | None = None
    sandcastle_config_id: int | None = None
    created_at: datetime
    updated_at: datetime
    last_fired_at: datetime | None = None

    @field_serializer("created_at", "updated_at", "last_fired_at")
    def _ser_dt(self, dt: datetime | None) -> str | None:
        return _as_utc_iso(dt)


class HookEvent(BaseModel):
    """Posted by the CC hook script."""
    event: Literal["UserPromptSubmit", "Stop", "Notification", "SessionStart"]
    session_id: str
    cwd: str
    tmux_pane: str | None = None
    message: str | None = None
    # Claude Code 2.1.198+ carries an explicit notification_type on the
    # Notification event for the new background-agent subtypes
    # (agent_needs_input / agent_completed). Older hook scripts only set
    # `message`, so this stays optional and the router falls back to
    # substring matching on `message` when it's absent.
    notification_type: str | None = None
