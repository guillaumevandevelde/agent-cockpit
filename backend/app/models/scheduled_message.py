"""ORM models for scheduled messages."""
from datetime import datetime, timezone

from sqlalchemy import String, Text, Boolean, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ScheduledMessage(Base):
    __tablename__ = "scheduled_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_project: Mapped[str] = mapped_column(String(1024))
    message: Mapped[str] = mapped_column(Text)
    trigger_type: Mapped[str] = mapped_column(String(16))  # once | cron
    fire_at: Mapped[str | None] = mapped_column(String(40), nullable=True)      # ISO8601, once
    cron_expr: Mapped[str | None] = mapped_column(String(120), nullable=True)   # cron
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Brussels")
    permission_mode: Mapped[str] = mapped_column(String(20), default="acceptEdits")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    on_missing_session: Mapped[str] = mapped_column(String(12), default="spawn")
    when_busy: Mapped[str] = mapped_column(String(16), default="wait_until_idle")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attempts: Mapped[list["DeliveryAttempt"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    scheduled_message_id: Mapped[int] = mapped_column(ForeignKey("scheduled_messages.id"))
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_session: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str | None] = mapped_column(String(16), nullable=True)  # used_existing | spawned
    wait_duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(12), nullable=True)  # success | failed | timeout
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    message: Mapped["ScheduledMessage"] = relationship(back_populates="attempts")
