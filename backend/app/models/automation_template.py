"""ORM model for automation templates."""
from datetime import datetime, timezone

from sqlalchemy import String, Text, Boolean, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AutomationTemplate(Base):
    """Pre-built workflow template combining scheduled messages, rules, and actions."""

    __tablename__ = "automation_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(32), default="general")  # review, monitor, quality, deploy, custom
    icon: Mapped[str | None] = mapped_column(String(32), nullable=True)  # lucide icon name
    trigger_type: Mapped[str] = mapped_column(String(16), default="cron")  # cron | once | event
    cron_expr: Mapped[str | None] = mapped_column(String(120), nullable=True)
    message_template: Mapped[str] = mapped_column(Text)
    target_projects: Mapped[list | None] = mapped_column(JSON, nullable=True)  # list of project paths or ["*"] for all
    permission_mode: Mapped[str] = mapped_column(String(20), default="suggest")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
