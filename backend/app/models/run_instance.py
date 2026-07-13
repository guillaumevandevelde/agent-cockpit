"""ORM model for ad-hoc app runs (sandboxed spawn of a built app).

Distinct from ``SandcastleRun`` which is an *agent* run inside a
sandbox — these records describe a *user command* (typically
``uvicorn`` / ``node`` / ``npm run dev``) that we launch in a
container (or subprocess fallback) on a random ``127.0.0.1`` port so a
preview pane can load the URL and prove the build actually works.
"""
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AppRun(Base):
    """One sandboxed invocation of a built app."""

    __tablename__ = "app_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_path: Mapped[str] = mapped_column(String(1024), index=True)
    # Stable, URL-safe identifier for the API surface (``/runs/app/<id>``).
    # Short uuid4 hex so it's both opaque enough to not collide with
    # other instances and short enough to type / paste into curl.
    instance_id: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    command: Mapped[list] = mapped_column(JSON)  # argv list (no env values)
    env_keys: Mapped[list] = mapped_column(JSON, default=list)  # env *names* only
    port: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(String(256))
    health_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # pending | starting | healthy | unhealthy | failed | stopped
    status: Mapped[str] = mapped_column(String(20), default="pending")
    transport: Mapped[str] = mapped_column(String(16))  # container | subprocess
    container_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)