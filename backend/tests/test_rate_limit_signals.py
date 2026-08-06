"""Durable rate-limit signal store (kanban card e279a52b…, revisit).

The in-memory ``session_signals`` registry deduped re-detections fine while
the backend stayed up, but it is emptied by every restart — and after such a
reset the same transcript tail read as a brand-new limit and re-armed the
pause at the exact moment the subscription came free. These tests cover the
KanbanMeta-backed record that closes that gap.
"""
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from app.kanban.models import KanbanMeta
from app.kanban.rate_limit_signals import (
    clear_handled_signal,
    get_handled_signal,
    is_signal_handled,
    message_digest,
    prune_handled_signals,
    record_handled_signal,
)
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()

MESSAGE = "You've hit your session limit · resets 11:10pm (Europe/Brussels)"


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.mark.asyncio
async def test_unrecorded_session_is_not_handled():
    async with KanbanSessionLocal() as s:
        assert await is_signal_handled(s, "k-none-0001", MESSAGE) is False
        assert await get_handled_signal(s, "k-none-0001") is None


@pytest.mark.asyncio
async def test_recorded_signal_round_trips():
    observed_at = datetime(2026, 7, 28, 3, 20, 0, tzinfo=UTC)
    pause_until = datetime(2026, 7, 28, 5, 20, 0, tzinfo=UTC)
    async with KanbanSessionLocal() as s:
        await record_handled_signal(
            s, "k-store-0001", MESSAGE,
            observed_at=observed_at, pause_until=pause_until,
        )
        await s.commit()

    # A fresh session — the point of the store is that it outlives the process
    # that wrote it, so nothing may depend on in-session identity-map state.
    async with KanbanSessionLocal() as s:
        stored = await get_handled_signal(s, "k-store-0001")
        assert stored is not None
        assert stored.digest == message_digest(MESSAGE)
        assert stored.observed_at == observed_at
        assert stored.pause_until == pause_until
        assert await is_signal_handled(s, "k-store-0001", MESSAGE) is True


@pytest.mark.asyncio
async def test_a_different_message_is_not_deduped():
    """The gate is per-message, not per-session: after a recovery the next
    genuine limit must run the full reaction."""
    async with KanbanSessionLocal() as s:
        await record_handled_signal(
            s, "k-store-0002", MESSAGE,
            observed_at=datetime.now(UTC), pause_until=None,
        )
        await s.commit()
        other = "You've hit your weekly limit · resets 9pm (Europe/Brussels)"
        assert await is_signal_handled(s, "k-store-0002", other) is False


@pytest.mark.asyncio
async def test_expired_signal_is_recorded_without_a_pause():
    """The age guard records the signal with ``pause_until=None`` so a later
    read can tell "expired, deliberately not paused" from "never seen"."""
    async with KanbanSessionLocal() as s:
        await record_handled_signal(
            s, "k-store-0003", MESSAGE,
            observed_at=datetime.now(UTC), pause_until=None,
        )
        await s.commit()
        stored = await get_handled_signal(s, "k-store-0003")
        assert stored is not None
        assert stored.pause_until is None
        assert await is_signal_handled(s, "k-store-0003", MESSAGE) is True


@pytest.mark.asyncio
async def test_clear_forgets_the_signal():
    async with KanbanSessionLocal() as s:
        await record_handled_signal(
            s, "k-store-0004", MESSAGE,
            observed_at=datetime.now(UTC), pause_until=None,
        )
        await clear_handled_signal(s, "k-store-0004")
        await s.commit()
        assert await get_handled_signal(s, "k-store-0004") is None
    # Clearing something that isn't there must be a no-op, not an error.
    async with KanbanSessionLocal() as s:
        await clear_handled_signal(s, "k-store-0004")
        await s.commit()


@pytest.mark.asyncio
async def test_unreadable_row_degrades_to_not_handled():
    """A row from an older schema or a hand-edit must never crash a dispatch
    tick — it degrades to "not handled yet", which re-runs the reaction."""
    async with KanbanSessionLocal() as s:
        s.add(KanbanMeta(key="rate_limit_signal:k-store-0005", value="not json"))
        await s.commit()
    async with KanbanSessionLocal() as s:
        assert await get_handled_signal(s, "k-store-0005") is None
        assert await is_signal_handled(s, "k-store-0005", MESSAGE) is False


@pytest.mark.asyncio
async def test_prune_drops_old_signals_and_keeps_recent_ones():
    async with KanbanSessionLocal() as s:
        await record_handled_signal(
            s, "k-old-0001", MESSAGE,
            observed_at=datetime.now(UTC) - timedelta(days=30), pause_until=None,
        )
        await record_handled_signal(
            s, "k-new-0001", MESSAGE,
            observed_at=datetime.now(UTC), pause_until=None,
        )
        s.add(KanbanMeta(key="rate_limit_signal:k-junk-0001", value="{"))
        await s.commit()

    async with KanbanSessionLocal() as s:
        removed = await prune_handled_signals(s)
        await s.commit()
        assert removed == 2, "the stale row and the unreadable row both go"
        assert await get_handled_signal(s, "k-old-0001") is None
        assert await get_handled_signal(s, "k-new-0001") is not None


@pytest.mark.asyncio
async def test_prune_leaves_other_meta_keys_alone():
    """The store shares ``kanban_meta`` with the dispatch pause and the
    autodispatch toggles — the sweep must only ever touch its own prefix."""
    async with KanbanSessionLocal() as s:
        s.add(KanbanMeta(key="dispatch_paused_until:anthropic", value="whatever"))
        await record_handled_signal(
            s, "k-old-0002", MESSAGE,
            observed_at=datetime.now(UTC) - timedelta(days=30), pause_until=None,
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        await prune_handled_signals(s)
        await s.commit()
        assert await s.get(KanbanMeta, "dispatch_paused_until:anthropic") is not None
