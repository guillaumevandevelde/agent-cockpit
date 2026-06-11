import pytest

from app.database import Base, engine, AsyncSessionLocal
from app.models.scheduled_message import ScheduledMessage, DeliveryAttempt


@pytest.mark.asyncio
async def test_create_scheduled_message():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as s:
        msg = ScheduledMessage(
            target_project="/home/guillaume/dev/x",
            message="run tests",
            trigger_type="once",
            fire_at="2026-06-12T09:00:00+02:00",
            permission_mode="acceptEdits",
        )
        s.add(msg)
        await s.commit()
        await s.refresh(msg)
        assert msg.id is not None
        assert msg.status == "scheduled"
        assert msg.enabled is True
        assert msg.on_missing_session == "spawn"
        assert msg.when_busy == "wait_until_idle"

        attempt = DeliveryAttempt(scheduled_message_id=msg.id)
        s.add(attempt)
        await s.commit()
        await s.refresh(attempt)
        assert attempt.id is not None
