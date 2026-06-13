import uuid
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import select

from app.database import Base, engine, AsyncSessionLocal
from app.models.database import PresenceEvent
from app.services.presence_service import PresenceService


def _sid() -> str:
    """Unique session id per run — presence state persists in a shared DB."""
    return f"sess-dur-{uuid.uuid4().hex}"


@pytest.mark.asyncio
async def test_stop_after_prompt_yields_duration():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sid = _sid()
    service = PresenceService()
    async with AsyncSessionLocal() as db:
        await service.process_event(
            {"session_id": sid, "hook_event_name": "UserPromptSubmit", "cwd": "/home/guillaume/dev/a"},
            db,
        )
        resp = await service.process_event(
            {"session_id": sid, "hook_event_name": "Stop", "cwd": "/home/guillaume/dev/a"},
            db,
        )
        await db.commit()
    assert resp.last_turn_duration_s is not None
    assert resp.last_turn_duration_s >= 0


@pytest.mark.asyncio
async def test_stop_without_prompt_yields_none():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sid = _sid()
    service = PresenceService()
    async with AsyncSessionLocal() as db:
        resp = await service.process_event(
            {"session_id": sid, "hook_event_name": "Stop", "cwd": "/home/guillaume/dev/b"},
            db,
        )
        await db.commit()
    assert resp.last_turn_duration_s is None


@pytest.mark.asyncio
async def test_backdated_prompt_yields_large_duration():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sid = _sid()
    service = PresenceService()
    async with AsyncSessionLocal() as db:
        await service.process_event(
            {"session_id": sid, "hook_event_name": "UserPromptSubmit", "cwd": "/home/guillaume/dev/c"},
            db,
        )
        await db.flush()
        result = await db.execute(
            select(PresenceEvent).where(
                PresenceEvent.session_id == sid,
                PresenceEvent.event_type == "UserPromptSubmit",
            )
        )
        ev = result.scalars().first()
        ev.timestamp = datetime.now(timezone.utc) - timedelta(seconds=12)
        await db.flush()
        resp = await service.process_event(
            {"session_id": sid, "hook_event_name": "Stop", "cwd": "/home/guillaume/dev/c"},
            db,
        )
        await db.commit()
    assert resp.last_turn_duration_s is not None
    assert resp.last_turn_duration_s >= 11
