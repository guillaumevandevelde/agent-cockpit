"""ORM models for sandcastle integration."""
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SandcastleConfig(Base):
    """Per-project sandcastle configuration."""
    __tablename__ = "sandcastle_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_path: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    sandbox_provider: Mapped[str] = mapped_column(String(32), default="no-sandbox")  # docker | podman | vercel | no-sandbox
    agent_provider: Mapped[str] = mapped_column(String(32), default="claude-code")  # claude-code | codex-cli | open-code
    model: Mapped[str | None] = mapped_column(String(128), nullable=True, default="sonnet")
    branch_strategy: Mapped[str] = mapped_column(String(32), default="merge-to-head")  # head | merge-to-head | branch
    docker_image: Mapped[str | None] = mapped_column(String(256), nullable=True)
    max_iterations: Mapped[int] = mapped_column(Integer, default=1)
    idle_timeout_seconds: Mapped[int] = mapped_column(Integer, default=600)
    permission_mode: Mapped[str] = mapped_column(String(32), default="acceptEdits")
    # --- resource / network hardening (see sandcastle_service._container_security_flags) ---
    memory_limit_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)  # container RAM cap; None = unbounded
    cpu_quota: Mapped[float | None] = mapped_column(Float, nullable=True)  # 1.0 = 1 core (docker --cpus); None = unbounded
    pids_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)  # max PIDs; None = unbounded
    read_only_rootfs: Mapped[bool] = mapped_column(Boolean, default=False)  # --read-only + tmpfs for writable paths
    network_mode: Mapped[str] = mapped_column(String(16), default="bridge")  # none | bridge | restricted
    egress_allowlist: Mapped[list | None] = mapped_column(JSON, nullable=True)  # stored intent; enforced by egress-proxy spoor
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class SandcastleRun(Base):
    """Record of a sandcastle agent run."""
    __tablename__ = "sandcastle_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_path: Mapped[str] = mapped_column(String(1024), index=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("sandcastle_configs.id"))
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | running | completed | failed | cancelled
    branch: Mapped[str | None] = mapped_column(String(256), nullable=True)
    commits: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{sha: "..."}]
    stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log_file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # structured output