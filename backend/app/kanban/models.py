"""Kanban ORM models. Two layers:
- KanbanOp: append-only operation log (source of truth + activity feed).
- KanbanCard / KanbanDeliverable: materialized, derived state for fast reads.
- KanbanMeta: small key/value store (device_id, per-project flags).
- AgentIdentity / AgentMessage: inter-agent mail, outside the op-log (see below).
"""
from datetime import datetime, timezone

from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.kanban.db import KanbanBase


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KanbanOp(KanbanBase):
    __tablename__ = "kanban_ops"

    op_id: Mapped[str] = mapped_column(String(128), primary_key=True)  # "<device>:<seq>"
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    hlc: Mapped[str] = mapped_column(String(48), index=True)
    project_key: Mapped[str] = mapped_column(String(512), index=True)
    entity_type: Mapped[str] = mapped_column(String(16))   # card | comment | deliverable
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    op_type: Mapped[str] = mapped_column(String(16))       # create|move|update|claim|release|comment|attach
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class KanbanCard(KanbanBase):
    __tablename__ = "kanban_cards"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(512), index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    column: Mapped[str] = mapped_column(String(32), default="Backlog")
    rank: Mapped[str] = mapped_column(String(64), default="")
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    labels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Per-field HLCs powering last-write-wins. DORMANT with one device: these guards
    # never reject a live write (every new tick dominates) and only do work under
    # HLC-ordered replay. Kept as cheap multi-device insurance, not removed.
    # See docs/cockpit/sync-hlc-freeze-vs-prune.md.
    title_hlc: Mapped[str | None] = mapped_column(String(48), nullable=True)
    description_hlc: Mapped[str | None] = mapped_column(String(48), nullable=True)
    column_hlc: Mapped[str | None] = mapped_column(String(48), nullable=True)
    rank_hlc: Mapped[str | None] = mapped_column(String(48), nullable=True)
    claim_hlc: Mapped[str | None] = mapped_column(String(48), nullable=True)

    deliverables: Mapped[list["KanbanDeliverable"]] = relationship(
        back_populates="card", cascade="all, delete-orphan",
    )


class KanbanDeliverable(KanbanBase):
    __tablename__ = "kanban_deliverables"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    card_id: Mapped[str] = mapped_column(ForeignKey("kanban_cards.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))   # pr|branch|commit|link|note
    ref: Mapped[str] = mapped_column(Text)          # portable reference, never a local path
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    card: Mapped["KanbanCard"] = relationship(back_populates="deliverables")


class KanbanColumn(KanbanBase):
    __tablename__ = "kanban_columns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(512), index=True)
    name: Mapped[str] = mapped_column(String(128))
    rank: Mapped[str] = mapped_column(String(64), default="")
    default_agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class KanbanMeta(KanbanBase):
    __tablename__ = "kanban_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


# --- Agent Mail: inter-agent messaging, deliberately OUTSIDE the op-log ---------
# Like KanbanColumn/KanbanMeta, these are materialized tables with their own
# lifecycle and direct CRUD (no apply_operation). Mail is not card-scoped and is
# not synced — consistent with the frozen sync seam. If sync ever revives, mail
# gets its own seam or a migration. See docs/cockpit/agent-mail-spec.md.


class AgentIdentity(KanbanBase):
    __tablename__ = "agent_identities"
    __table_args__ = (
        UniqueConstraint("project_key", "handle", name="uq_agent_identity"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(512), index=True)
    handle: Mapped[str] = mapped_column(String(64))  # durable per-repo role identity
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_session: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentMessage(KanbanBase):
    __tablename__ = "agent_messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(512), index=True)
    from_handle: Mapped[str] = mapped_column(String(64))
    to_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)  # None = broadcast
    kind: Mapped[str] = mapped_column(String(24))  # context_request|context_response|handoff|note
    subject: Mapped[str] = mapped_column(String(512), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    card_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="unread")  # unread|read|answered
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
