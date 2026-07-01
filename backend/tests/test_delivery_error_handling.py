"""A delivery that raises must never silently kill a schedule.

Regression tests for run_scheduled_delivery: when DeliveryEngine.deliver raises
an unexpected exception, the attempt must record the error and the message must
not get stuck in 'pending_delivery' (which the coalescing guard would then use
to skip every future fire forever).
"""
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, patch

import app.models.sandcastle  # noqa: F401  (register FK target for create_all)
from app.database import Base
from app.models.scheduled_message import ScheduledMessage, DeliveryAttempt
from app.services.scheduling import crud


@pytest_asyncio.fixture
async def temp_sessionmaker():
    """Isolated on-disk sqlite so tests never touch the production DB."""
    db_path = Path(tempfile.mkdtemp()) / "sched_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    with patch.object(crud, "AsyncSessionLocal", sm):
        yield sm
    await engine.dispose()


async def _make_msg(sm, **overrides) -> int:
    fields = dict(
        target_project="/proj", message="go", trigger_type="once",
        fire_at="2026-01-01T00:00:00+00:00", status="scheduled", enabled=True,
    )
    fields.update(overrides)
    async with sm() as s:
        msg = ScheduledMessage(**fields)
        s.add(msg)
        await s.commit()
        await s.refresh(msg)
        return msg.id


async def _attempts(sm, mid):
    async with sm() as s:
        return (await s.execute(
            select(DeliveryAttempt).where(DeliveryAttempt.scheduled_message_id == mid)
        )).scalars().all()


async def _status(sm, mid):
    async with sm() as s:
        return (await s.get(ScheduledMessage, mid)).status


@pytest.mark.asyncio
async def test_once_delivery_exception_records_error_and_is_terminal(temp_sessionmaker):
    sm = temp_sessionmaker
    mid = await _make_msg(sm, trigger_type="once")

    with patch.object(crud._engine, "deliver", side_effect=RuntimeError("boom")):
        await crud.run_scheduled_delivery(mid)

    attempts = await _attempts(sm, mid)
    assert len(attempts) == 1
    assert attempts[0].outcome == "failed"
    assert "boom" in (attempts[0].error or "")
    # Must not be stuck pending_delivery (which would freeze all future fires).
    assert await _status(sm, mid) == "failed"


@pytest.mark.asyncio
async def test_cron_delivery_exception_reschedules_for_retry(temp_sessionmaker):
    sm = temp_sessionmaker
    mid = await _make_msg(sm, trigger_type="cron", cron_expr="* * * * *", fire_at=None)

    with patch.object(crud._engine, "deliver", side_effect=RuntimeError("boom")):
        await crud.run_scheduled_delivery(mid)

    attempts = await _attempts(sm, mid)
    assert len(attempts) == 1
    assert attempts[0].outcome == "failed"
    assert "boom" in (attempts[0].error or "")
    # A cron job must return to 'scheduled' so the next tick can retry, not die.
    assert await _status(sm, mid) == "scheduled"


@pytest.mark.asyncio
async def test_next_fire_runs_after_a_prior_exception(temp_sessionmaker):
    """A transient failure must not permanently disable the coalescing guard."""
    sm = temp_sessionmaker
    mid = await _make_msg(sm, trigger_type="cron", cron_expr="* * * * *", fire_at=None)

    with patch.object(crud._engine, "deliver", side_effect=RuntimeError("boom")):
        await crud.run_scheduled_delivery(mid)

    from app.services.scheduling.delivery import DeliveryResult
    ok = AsyncMock(return_value=DeliveryResult(outcome="success", action="spawned"))
    with patch.object(crud._engine, "deliver", new=ok):
        await crud.run_scheduled_delivery(mid)

    ok.assert_awaited_once()  # the second fire actually attempted delivery
    attempts = await _attempts(sm, mid)
    assert len(attempts) == 2
    assert sorted(a.outcome for a in attempts) == ["failed", "success"]
