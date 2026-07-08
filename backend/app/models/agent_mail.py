"""ORM models for Agent Mail — cross-session messaging between arbitrary
Claude Code / Codex CLI processes. Identity is one durable MailTeamMember
per repo (no team-preset/slot integration — see docs/cockpit/agent-mail-spec.md
for why that upstream extension is out of scope)."""
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MailTeamMember(Base):
    """Durable per-repo identity. Git worktrees of the same repo share one row."""

    __tablename__ = "mail_team_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    identity_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    repo_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    repo_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    charter: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_inbox_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class MailAgentSession(Base):
    """Ephemeral session (hook heartbeat, MCP connection, or tmux-observed pane)
    attached to a durable member."""

    __tablename__ = "mail_agent_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("mail_team_members.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # hook | mcp | observed
    session_key: Mapped[str] = mapped_column(String(256), unique=True, index=True, nullable=False)
    cwd: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    tmux_target: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pane_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mailbox_status: Mapped[str] = mapped_column(String(16), default="connected", nullable=False)
    activity: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class MailExternalActor(Base):
    """Durable identity for a local, bearer-token-authenticated external tool
    (e.g. OpenClaw) that talks to the external orchestration API."""

    __tablename__ = "mail_external_actors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_key: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(80), default="external_tool", nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MailMessage(Base):
    __tablename__ = "mail_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_root_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("mail_messages.id", ondelete="CASCADE"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    sender_member_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("mail_team_members.id", ondelete="SET NULL"), nullable=True
    )
    sender_actor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("mail_external_actors.id", ondelete="SET NULL"), nullable=True
    )
    recipient_member_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("mail_team_members.id", ondelete="CASCADE"), index=True, nullable=True
    )
    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    request_status: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)


class MailReceipt(Base):
    __tablename__ = "mail_receipts"
    __table_args__ = (UniqueConstraint("message_id", "member_id", name="uix_mail_receipt_message_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("mail_messages.id", ondelete="CASCADE"), index=True, nullable=False
    )
    member_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("mail_team_members.id", ondelete="CASCADE"), index=True, nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
