"""ORM model for server-side recurring triggers.

A trigger is a small persisted object that the scheduler reads on boot and
keeps in memory thereafter. Firing creates a kanban Backlog card via the
same op-log flow the REST/MCP paths use (``app.kanban.operations``),
so the existing auto-dispatcher picks it up with no parallel spawn
mechanism. Mirrors ``webhook_triggers`` — same idea, clock as source
instead of an event.

See ``docs/cockpit/scheduled-trigger-consolidatie-decision.md`` §5.1–5.2.
"""
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RecurringTrigger(Base):
    __tablename__ = "recurring_triggers"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Target project key on the kanban board. Card lives in this project's
    # Backlog so the existing auto-dispatcher claims + spawns it.
    project_key: Mapped[str] = mapped_column(String(512), index=True)
    cron_expr: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Brussels")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Card template — what gets created when the trigger fires.
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    work_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    labels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    # The last *real* occurrence the trigger fired for. ``last_fired_at``
    # advances by cron occurrence (not by APScheduler tick), so two ticks
    # that both land on the same coalesced occurrence only update this
    # once and the second one becomes a no-op. None means "never fired";
    # the inhaal-on-boot logic distinguishes "never" from "fired long ago"
    # by checking whether the previous occurrence is later than this value.
    last_fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow,
    )

    def __init__(self, **kwargs: Any) -> None:
        # Accept both ``metadata`` (API/JSON spelling) and ``metadata_``
        # (the Python attribute name; the SQL column is also ``metadata``
        # but Python's `metadata` is reserved on Declarative Base).
        if "metadata" in kwargs and "metadata_" not in kwargs:
            kwargs["metadata_"] = kwargs.pop("metadata")
        super().__init__(**kwargs)
