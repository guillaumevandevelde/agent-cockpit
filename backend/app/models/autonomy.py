"""ORM model for agent autonomy settings."""
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AutonomyProfile(Base):
    """Per-session or global autonomy profile.

    Modes:
      - plan:     read-only, agent can inspect but not modify
      - suggest:  agent proposes changes, user approves each
      - auto:     full autonomy, agent auto-approves all actions
    """

    __tablename__ = "autonomy_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    mode: Mapped[str] = mapped_column(String(16), default="suggest")  # plan | suggest | auto
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_tools: Mapped[list | None] = mapped_column(JSON, nullable=True)
    denied_tools: Mapped[list | None] = mapped_column(JSON, nullable=True)
    max_file_size_kb: Mapped[int | None] = mapped_column(nullable=True)
    require_approval_for: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
