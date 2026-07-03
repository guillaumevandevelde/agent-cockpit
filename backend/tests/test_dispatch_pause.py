"""Tests for the global auto-dispatch pause triggered by Claude usage-limit hits."""
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from tests.kanban_test_db import TestSessionLocal, reset_test_tables
from app.kanban import dispatch_pause

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.mark.asyncio
async def test_not_paused_by_default():
    async with KanbanSessionLocal() as s:
        assert await dispatch_pause.is_dispatch_paused(s) is False
        assert await dispatch_pause.get_paused_until(s) is None


@pytest.mark.asyncio
async def test_future_deadline_pauses_dispatch():
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_paused_until(s, future)
        await s.commit()

    async with KanbanSessionLocal() as s:
        assert await dispatch_pause.is_dispatch_paused(s) is True
        stored = await dispatch_pause.get_paused_until(s)
        assert stored is not None
        assert abs((stored - future).total_seconds()) < 1


@pytest.mark.asyncio
async def test_expired_deadline_self_clears_and_unpauses():
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_paused_until(s, past)
        await s.commit()

    async with KanbanSessionLocal() as s:
        assert await dispatch_pause.is_dispatch_paused(s) is False

    async with KanbanSessionLocal() as s:
        # The stale row must actually be gone, not just reported as unpaused,
        # so a later set_paused_until(None) test isn't papering over a leak.
        assert await dispatch_pause.get_paused_until(s) is None


@pytest.mark.asyncio
async def test_set_paused_until_none_clears_an_active_pause():
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_paused_until(s, future)
        await s.commit()

    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_paused_until(s, None)
        await s.commit()

    async with KanbanSessionLocal() as s:
        assert await dispatch_pause.is_dispatch_paused(s) is False
        assert await dispatch_pause.get_paused_until(s) is None


@pytest.mark.asyncio
async def test_naive_timezone_aware_input_is_normalised_to_utc():
    """set_paused_until must handle a tz-aware datetime in a non-UTC zone (as
    produced by auto_resume_service.parse_reset_time) without crashing or
    losing the instant it represents."""
    from zoneinfo import ZoneInfo

    brussels = datetime.now(ZoneInfo("Europe/Brussels")) + timedelta(hours=1)
    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_paused_until(s, brussels)
        await s.commit()

    async with KanbanSessionLocal() as s:
        stored = await dispatch_pause.get_paused_until(s)
        assert stored is not None
        assert abs((stored - brussels).total_seconds()) < 1
