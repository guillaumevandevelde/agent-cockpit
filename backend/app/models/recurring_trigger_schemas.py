"""Pydantic schemas for recurring triggers."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_serializer, model_validator

from app.utils.timeutils import ensure_aware


def _as_utc_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return ensure_aware(dt).isoformat()


class RecurringTriggerCreate(BaseModel):
    project_key: str
    cron_expr: str
    timezone: str = "Europe/Brussels"
    enabled: bool = True
    title: str
    description: str = ""
    work_type: Literal["analysis", "feature", "bug", "chore"] | None = None
    agent: str | None = None
    labels: list[str] | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_cron(self):
        if not self.cron_expr or not self.cron_expr.strip():
            raise ValueError("cron_expr is required")
        # Cheap sanity check: a 5-field cron expression. Croniter will
        # raise a more useful error if the fields are invalid values;
        # we only fail fast on obviously wrong shapes here.
        if len(self.cron_expr.split()) not in (5, 6):
            raise ValueError("cron_expr must be a 5- or 6-field expression")
        return self


class RecurringTriggerUpdate(BaseModel):
    cron_expr: str | None = None
    timezone: str | None = None
    enabled: bool | None = None
    title: str | None = None
    description: str | None = None
    work_type: Literal["analysis", "feature", "bug", "chore"] | None = None
    agent: str | None = None
    labels: list[str] | None = None


class RecurringTriggerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_key: str
    cron_expr: str
    timezone: str
    enabled: bool
    title: str
    description: str
    work_type: str | None
    agent: str | None
    labels: list[str] | None
    last_fired_at: datetime | None

    @field_serializer("last_fired_at")
    def _ser_dt(self, dt: datetime | None) -> str | None:
        return _as_utc_iso(dt)
