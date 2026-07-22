"""Tests for the global auto-dispatch pause triggered by Claude usage-limit hits."""
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from app.kanban import dispatch_pause
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

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
    future = datetime.now(UTC) + timedelta(minutes=10)
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
    past = datetime.now(UTC) - timedelta(minutes=1)
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
    future = datetime.now(UTC) + timedelta(minutes=10)
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


# ---- per-provider pause (kanban-limit feature foundation) -------------------


@pytest.mark.asyncio
async def test_set_paused_until_per_provider():
    """set_paused_until(provider=X) writes a per-provider key, leaves the
    legacy global key and other providers' keys untouched, and
    get_paused_until(provider=X) reads it back."""
    minimax = datetime.now(UTC) + timedelta(minutes=10)
    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_paused_until(s, minimax, provider="minimax")
        await s.commit()

    async with KanbanSessionLocal() as s:
        # Per-provider round-trips.
        stored_minimax = await dispatch_pause.get_paused_until(
            s, provider="minimax"
        )
        assert stored_minimax is not None
        assert abs((stored_minimax - minimax).total_seconds()) < 1
        # Other provider slots are untouched.
        assert await dispatch_pause.get_paused_until(
            s, provider="anthropic"
        ) is None
        assert await dispatch_pause.get_paused_until(
            s, provider="bedrock"
        ) is None
        # The legacy global key must not have been written.
        assert await dispatch_pause.get_paused_until(s) is None


@pytest.mark.asyncio
async def test_set_paused_until_per_provider_does_not_touch_legacy_key():
    """An existing global (provider=None) pause must survive a per-provider
    write -- the two keys are independent slots."""
    future = datetime.now(UTC) + timedelta(minutes=10)
    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_paused_until(s, future)
        await dispatch_pause.set_paused_until(
            s, future + timedelta(hours=1), provider="minimax"
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        # Legacy pause is the original `future`, not the per-provider value.
        legacy = await dispatch_pause.get_paused_until(s)
        assert legacy is not None
        assert abs((legacy - future).total_seconds()) < 1
        # And the per-provider slot has its own value.
        per_provider = await dispatch_pause.get_paused_until(
            s, provider="minimax"
        )
        assert per_provider is not None
        assert abs((per_provider - (future + timedelta(hours=1))).total_seconds()) < 1


@pytest.mark.asyncio
async def test_is_dispatch_paused_per_provider():
    """is_dispatch_paused(provider=X) only consults that provider's key --
    a global pause does NOT show up as a per-provider pause (and vice versa).
    Self-clear behaviour is preserved on the per-provider path."""
    future = datetime.now(UTC) + timedelta(minutes=10)
    past = datetime.now(UTC) - timedelta(minutes=1)

    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_paused_until(s, future, provider="minimax")
        await s.commit()

    async with KanbanSessionLocal() as s:
        # The per-provider pause is active.
        assert await dispatch_pause.is_dispatch_paused(
            s, provider="minimax"
        ) is True
        # But it does not leak into a different provider's view.
        assert await dispatch_pause.is_dispatch_paused(
            s, provider="anthropic"
        ) is False
        # And the legacy global pause is unaffected.
        assert await dispatch_pause.is_dispatch_paused(s) is False

    # Self-clear on the per-provider path: an expired per-provider pause
    # must be wiped from its own slot when is_dispatch_paused runs.
    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_paused_until(s, past, provider="bedrock")
        await s.commit()

    async with KanbanSessionLocal() as s:
        assert await dispatch_pause.is_dispatch_paused(
            s, provider="bedrock"
        ) is False

    async with KanbanSessionLocal() as s:
        # The stale per-provider row is gone -- not just reported unpaused.
        assert await dispatch_pause.get_paused_until(
            s, provider="bedrock"
        ) is None


@pytest.mark.asyncio
async def test_clear_all_pauses_clears_legacy_and_per_provider():
    """clear_all_pauses wipes both the legacy key and every per-provider key,
    regardless of whether each one was active or expired."""
    future = datetime.now(UTC) + timedelta(minutes=10)
    past = datetime.now(UTC) - timedelta(minutes=1)

    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_paused_until(s, future)  # legacy global
        await dispatch_pause.set_paused_until(
            s, future, provider="anthropic"
        )
        await dispatch_pause.set_paused_until(
            s, future, provider="bedrock"
        )
        # An expired per-provider entry must also be cleared.
        await dispatch_pause.set_paused_until(
            s, past, provider="minimax"
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        await dispatch_pause.clear_all_pauses(s)
        await s.commit()

    async with KanbanSessionLocal() as s:
        assert await dispatch_pause.get_paused_until(s) is None
        assert await dispatch_pause.get_paused_until(
            s, provider="anthropic"
        ) is None
        assert await dispatch_pause.get_paused_until(
            s, provider="bedrock"
        ) is None
        assert await dispatch_pause.get_paused_until(
            s, provider="minimax"
        ) is None
        # list_paused_providers must also report nothing.
        assert await dispatch_pause.list_paused_providers(s) == []


@pytest.mark.asyncio
async def test_list_paused_providers_returns_only_active_providers():
    """list_paused_providers returns exactly the provider names whose
    per-provider key is in the future; expired entries don't count and are
    wiped from the slot."""
    future = datetime.now(UTC) + timedelta(minutes=10)
    past = datetime.now(UTC) - timedelta(minutes=1)

    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_paused_until(
            s, future, provider="anthropic"
        )
        await dispatch_pause.set_paused_until(
            s, future, provider="bedrock"
        )
        await dispatch_pause.set_paused_until(
            s, past, provider="minimax"
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        # Before self-clear: only the two active providers are listed.
        active = await dispatch_pause.list_paused_providers(s)
        assert sorted(active) == ["anthropic", "bedrock"]

        # Trigger self-clear of the expired entry by reading the slot.
        assert await dispatch_pause.is_dispatch_paused(
            s, provider="minimax"
        ) is False

    async with KanbanSessionLocal() as s:
        # The expired entry was wiped; listing still returns only the active
        # ones and the same ones.
        assert sorted(
            await dispatch_pause.list_paused_providers(s)
        ) == ["anthropic", "bedrock"]


@pytest.mark.asyncio
async def test_clear_one_per_provider_does_not_touch_others():
    """set_paused_until(provider=X, when=None) clears ONLY X's slot -- it is
    not a global 'clear everything'."""
    future = datetime.now(UTC) + timedelta(minutes=10)
    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_paused_until(
            s, future, provider="anthropic"
        )
        await dispatch_pause.set_paused_until(
            s, future, provider="bedrock"
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_paused_until(s, None, provider="anthropic")
        await s.commit()

    async with KanbanSessionLocal() as s:
        assert await dispatch_pause.get_paused_until(
            s, provider="anthropic"
        ) is None
        # bedrock is untouched.
        assert await dispatch_pause.get_paused_until(
            s, provider="bedrock"
        ) is not None
        # And the legacy slot, if there was one, is also untouched.
        assert await dispatch_pause.get_paused_until(s) is None


# ---- manual per-subscription pause (kaart f056b2888a…) ---------------------
#
# The auto-pause above is time-based (deadline stored in the value). The manual
# pause is a separate boolean flag -- an operator clicks "pause all anthropic
# dispatches until I say otherwise" and we hold dispatch off indefinitely.
# Distinct KanbanMeta key (`dispatch_paused_manual:<provider>`) so the time-
# based auto-pause and the indefinite manual pause stay independent slots.


@pytest.mark.asyncio
async def test_manual_pause_default_off_per_provider():
    """is_manually_paused returns False for every provider by default; the
    manual-pause slot is independent of the legacy / per-provider time-based
    slots."""
    async with KanbanSessionLocal() as s:
        assert await dispatch_pause.is_manually_paused(s, "anthropic") is False
        assert await dispatch_pause.is_manually_paused(s, "bedrock") is False
        assert await dispatch_pause.is_manually_paused(s, "minimax") is False


@pytest.mark.asyncio
async def test_set_manual_pause_round_trips_and_is_independent_per_provider():
    """set_manual_pause(paused=True) writes only that provider's slot;
    other providers stay unpaused."""
    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_manual_pause(s, "anthropic", True)
        await s.commit()

    async with KanbanSessionLocal() as s:
        assert await dispatch_pause.is_manually_paused(s, "anthropic") is True
        assert await dispatch_pause.is_manually_paused(s, "bedrock") is False
        assert await dispatch_pause.is_manually_paused(s, "minimax") is False


@pytest.mark.asyncio
async def test_set_manual_pause_false_clears_only_that_provider():
    """set_manual_pause(paused=False) is the toggle-off path: clears only
    the named provider's slot. Other providers' manual pauses are untouched,
    and the time-based slots are untouched too."""
    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_manual_pause(s, "anthropic", True)
        await dispatch_pause.set_manual_pause(s, "bedrock", True)
        # A coexisting time-based per-provider pause must survive a manual-toggle-off.
        await dispatch_pause.set_paused_until(
            s, datetime.now(UTC) + timedelta(minutes=10), provider="minimax"
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_manual_pause(s, "anthropic", False)
        await s.commit()

    async with KanbanSessionLocal() as s:
        assert await dispatch_pause.is_manually_paused(s, "anthropic") is False
        # bedrock manual pause is preserved.
        assert await dispatch_pause.is_manually_paused(s, "bedrock") is True
        # And the time-based per-provider pause on minimax is preserved.
        assert (
            await dispatch_pause.get_paused_until(s, provider="minimax")
            is not None
        )


@pytest.mark.asyncio
async def test_manual_pause_does_not_collide_with_time_based_pause():
    """Setting a manual pause for provider X must not touch its time-based
    per-provider slot, and vice versa. The two slots coexist for the same
    provider (e.g. an auto-tripped limit AND a manual operator override on
    the same subscription) and either being active must not mask the other
    in the read helpers."""
    future = datetime.now(UTC) + timedelta(minutes=10)
    async with KanbanSessionLocal() as s:
        # Time-based on anthropic, manual on bedrock.
        await dispatch_pause.set_paused_until(s, future, provider="anthropic")
        await dispatch_pause.set_manual_pause(s, "bedrock", True)
        await s.commit()

    async with KanbanSessionLocal() as s:
        # Manual pause query on anthropic is False (only time-based is set).
        assert await dispatch_pause.is_manually_paused(s, "anthropic") is False
        # And the time-based query on bedrock is None (only manual is set).
        assert (
            await dispatch_pause.get_paused_until(s, provider="bedrock")
            is None
        )
        # Each helper sees its own slot.
        assert await dispatch_pause.is_dispatch_paused(
            s, provider="anthropic"
        ) is True
        assert await dispatch_pause.is_manually_paused(s, "bedrock") is True


@pytest.mark.asyncio
async def test_list_manually_paused_providers_returns_active_set():
    """list_manually_paused_providers returns the provider names whose
    manual-pause slot is set (value != "1" treated as 'off')."""
    async with KanbanSessionLocal() as s:
        await dispatch_pause.set_manual_pause(s, "anthropic", True)
        await dispatch_pause.set_manual_pause(s, "bedrock", True)
        await s.commit()

    async with KanbanSessionLocal() as s:
        assert sorted(
            await dispatch_pause.list_manually_paused_providers(s)
        ) == ["anthropic", "bedrock"]


@pytest.mark.asyncio
async def test_list_manually_paused_providers_empty_by_default():
    """No manual pauses means an empty list, not None or a sentinel."""
    async with KanbanSessionLocal() as s:
        assert (
            await dispatch_pause.list_manually_paused_providers(s) == []
        )


@pytest.mark.asyncio
async def test_clear_all_pauses_also_clears_manual_per_provider():
    """clear_all_pauses wipes every pause slot including the manual
    per-provider keys -- the existing "Resume auto-dispatch now" button must
    un-freeze a manually-paused subscription as well as a time-based one."""
    future = datetime.now(UTC) + timedelta(minutes=10)

    async with KanbanSessionLocal() as s:
        # Time-based slots across legacy + two providers.
        await dispatch_pause.set_paused_until(s, future)  # legacy global
        await dispatch_pause.set_paused_until(s, future, provider="anthropic")
        # Manual slots across two providers.
        await dispatch_pause.set_manual_pause(s, "anthropic", True)
        await dispatch_pause.set_manual_pause(s, "bedrock", True)
        await s.commit()

    async with KanbanSessionLocal() as s:
        await dispatch_pause.clear_all_pauses(s)
        await s.commit()

    async with KanbanSessionLocal() as s:
        # Time-based slots all cleared.
        assert await dispatch_pause.get_paused_until(s) is None
        assert (
            await dispatch_pause.get_paused_until(s, provider="anthropic")
            is None
        )
        # Manual slots all cleared.
        assert await dispatch_pause.is_manually_paused(s, "anthropic") is False
        assert await dispatch_pause.is_manually_paused(s, "bedrock") is False
        # And the listing helpers reflect the clean state.
        assert await dispatch_pause.list_paused_providers(s) == []
        assert (
            await dispatch_pause.list_manually_paused_providers(s) == []
        )
