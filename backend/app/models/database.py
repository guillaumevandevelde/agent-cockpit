"""SQLAlchemy database models."""
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.database import Base
from app.models.constants import SessionStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    """Project model for tracking Claude Code project directories."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_accessed: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Backup(Base):
    """Backup model for storing configuration backup metadata."""

    __tablename__ = "backups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=True)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(String, nullable=False)  # "full", "user", "project"
    project_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    is_automatic: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )


class AutoBackupSettings(Base):
    """Singleton settings row for scheduled automatic backups.

    Only the row with id=1 is used; the service creates it on first access.
    """

    __tablename__ = "auto_backup_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    scope: Mapped[str] = mapped_column(String, default="user", nullable=False)  # "user" or "full"
    project_path: Mapped[str | None] = mapped_column(String, nullable=True)
    time_of_day: Mapped[str] = mapped_column(String, default="03:00", nullable=False)  # "HH:MM"
    timezone: Mapped[str] = mapped_column(String, default="UTC", nullable=False)
    retention_days: Mapped[int] = mapped_column(
        Integer, default=settings.default_backup_retention_days, nullable=False
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str | None] = mapped_column(String, nullable=True)
    last_backup_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Marketplace(Base):
    """Marketplace model for plugin marketplace configurations."""

    __tablename__ = "marketplaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    last_synced: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class SessionCache(Base):
    """Cache for session metadata to avoid re-parsing JSONL files."""

    __tablename__ = "session_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    project_folder: Mapped[str] = mapped_column(String, index=True, nullable=False)
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(String, nullable=False)
    modified_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    total_messages: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tool_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    file_hash: Mapped[str] = mapped_column(String, nullable=False)


class UsageCache(Base):
    """Cache for usage aggregation data to avoid re-parsing JSONL files."""

    __tablename__ = "usage_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    cache_type: Mapped[str] = mapped_column(String, index=True, nullable=False)  # daily, session, monthly, block, summary
    project_path: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)  # Aggregated usage data
    cached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    file_hash: Mapped[str | None] = mapped_column(String, nullable=True)  # For cache invalidation


class MCPServerCache(Base):
    """Cache for MCP server connection status and tools."""

    __tablename__ = "mcp_server_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_name: Mapped[str] = mapped_column(String, index=True, nullable=False)
    server_scope: Mapped[str] = mapped_column(String, index=True, nullable=False)
    is_connected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    mcp_server_name: Mapped[str | None] = mapped_column(String, nullable=True)
    mcp_server_version: Mapped[str | None] = mapped_column(String, nullable=True)
    tools: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tool_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    resources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    prompts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    resource_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    capabilities: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    config_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint('server_name', 'server_scope', name='uix_server_name_scope'),
    )


class PresenceEvent(Base):
    """Raw event log from Claude Code HTTP hooks."""

    __tablename__ = "presence_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("presence_sessions.session_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String, nullable=True)
    tool_input: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    message: Mapped[str | None] = mapped_column(String, nullable=True)
    cwd: Mapped[str | None] = mapped_column(String, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class PresenceSession(Base):
    """Aggregated per-session state for the Presence Dashboard."""

    __tablename__ = "presence_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    project_path: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default=SessionStatus.ACTIVE, nullable=False, index=True)
    status_text: Mapped[str | None] = mapped_column(String, nullable=True)
    last_narrative: Mapped[str | None] = mapped_column(String, nullable=True)
    last_narrative_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    modified_files: Mapped[list | None] = mapped_column(JSON, nullable=True)
    last_command: Mapped[str | None] = mapped_column(String, nullable=True)
    last_command_exit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    activity_buckets: Mapped[list | None] = mapped_column(JSON, nullable=True)
    bucket_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total_events: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    last_event_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_user_prompt: Mapped[str | None] = mapped_column(String, nullable=True)
    tmux_pane: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    # Deleting a session cascade-deletes its raw events. passive_deletes lets
    # the DB-level ON DELETE CASCADE do the work (so bulk DELETEs are covered
    # too) instead of the ORM loading and nulling children.
    events: Mapped[list["PresenceEvent"]] = relationship(
        "PresenceEvent",
        primaryjoin="PresenceSession.session_id == PresenceEvent.session_id",
        foreign_keys="PresenceEvent.session_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
