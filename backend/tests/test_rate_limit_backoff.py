"""Per-provider exponential backoff for unparseable rate-limit messages
(kanban card b106def4…, R3 of the subscription-auto-release-analyse).

When a limit message has no parseable reset time (e.g. the MiniMax "Token
Plan usage limit reached" wording) the reactive path falls back from the
parsed deadline to a guess. The legacy guess was a fixed 5 h — a blind
constant that is either too short (subscription recovers in 10 minutes) or
too long (we wait hours for nothing). This module replaces the guess with a
per-provider backoff: the first fresh limit pauses for a short window, each
subsequent fresh limit doubles it, capped at an explicit maximum.

The dedupe is the sister concern: ``rate_limit_signals`` recognises a
re-detection of the *same* message as already-handled, so the backoff is
only ever updated for messages that pass the dedupe gate (genuinely fresh
limits). See kanban card e279a52b… for the dedupe half.
"""
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from app.kanban.models import KanbanMeta
from app.kanban.rate_limit_backoff import (
    BACKOFF_SEQUENCE,
    IDLE_RESET_AFTER,
    get_backoff,
    prune_idle_backoffs,
    record_backoff,
    reset_backoff,
)
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


# ---- backoff state unit tests ----------------------------------------------


@pytest.mark.asyncio
async def test_get_backoff_returns_none_when_unrecorded():
    async with KanbanSessionLocal() as s:
        assert await get_backoff(s, "minimax") is None


@pytest.mark.asyncio
async def test_record_backoff_starts_at_attempt_one():
    async with KanbanSessionLocal() as s:
        state = await record_backoff(s, "minimax")
        await s.commit()
        assert state.attempt == 1
    async with KanbanSessionLocal() as s:
        stored = await get_backoff(s, "minimax")
        assert stored is not None
        assert stored.attempt == 1


@pytest.mark.asyncio
async def test_record_backoff_increments_existing_counter():
    async with KanbanSessionLocal() as s:
        await record_backoff(s, "minimax")
        await s.commit()
    async with KanbanSessionLocal() as s:
        second = await record_backoff(s, "minimax")
        await s.commit()
        assert second.attempt == 2
    async with KanbanSessionLocal() as s:
        third = await record_backoff(s, "minimax")
        await s.commit()
        assert third.attempt == 3


@pytest.mark.asyncio
async def test_backoff_is_per_provider():
    """Anthropic and MiniMax each have their own counter — a burst on one
    provider must not bleed into the other's window."""
    async with KanbanSessionLocal() as s:
        await record_backoff(s, "minimax")
        await record_backoff(s, "minimax")
        await record_backoff(s, "anthropic")
        await s.commit()
    async with KanbanSessionLocal() as s:
        minimax = await get_backoff(s, "minimax")
        anthropic = await get_backoff(s, "anthropic")
        assert minimax is not None and minimax.attempt == 2
        assert anthropic is not None and anthropic.attempt == 1


@pytest.mark.asyncio
async def test_reset_backoff_drops_the_counter():
    async with KanbanSessionLocal() as s:
        await record_backoff(s, "minimax")
        await record_backoff(s, "minimax")
        await s.commit()
    async with KanbanSessionLocal() as s:
        await reset_backoff(s, "minimax")
        await s.commit()
    async with KanbanSessionLocal() as s:
        assert await get_backoff(s, "minimax") is None
    # Resetting again is a no-op, not an error.
    async with KanbanSessionLocal() as s:
        await reset_backoff(s, "minimax")
        await s.commit()


@pytest.mark.asyncio
async def test_reset_does_not_touch_other_providers():
    async with KanbanSessionLocal() as s:
        await record_backoff(s, "minimax")
        await record_backoff(s, "anthropic")
        await s.commit()
    async with KanbanSessionLocal() as s:
        await reset_backoff(s, "minimax")
        await s.commit()
    async with KanbanSessionLocal() as s:
        assert await get_backoff(s, "minimax") is None
        assert (await get_backoff(s, "anthropic")).attempt == 1


@pytest.mark.asyncio
async def test_unreadable_row_degrades_to_no_backoff():
    """A row from an older schema or a hand-edit must never crash a dispatch
    tick — it degrades to "no backoff yet", which re-runs the reaction with
    the initial window. Same convention as ``rate_limit_signals``."""
    async with KanbanSessionLocal() as s:
        s.add(KanbanMeta(key="rate_limit_backoff:minimax", value="not json"))
        await s.commit()
    async with KanbanSessionLocal() as s:
        assert await get_backoff(s, "minimax") is None


@pytest.mark.asyncio
async def test_backoff_survives_session_reset():
    """The whole point of persisting in KanbanMeta: a supervisor restart or
    ``cockpit.sh restart`` empties in-memory state, but the backoff counter
    must still be readable from a fresh session on the same data."""
    async with KanbanSessionLocal() as s:
        await record_backoff(s, "minimax")
        await record_backoff(s, "minimax")
        await s.commit()

    # Brand-new session — same backing store, fresh identity-map.
    async with KanbanSessionLocal() as s:
        stored = await get_backoff(s, "minimax")
        assert stored is not None
        assert stored.attempt == 2


@pytest.mark.asyncio
async def test_prune_drops_idle_counters():
    """A counter that hasn't moved in a long time is reset, the closest
    backstop to "success" we can observe without instrumenting spawn. The
    test writes an old ``armed_at`` directly to bypass ``record_backoff``'s
    default ``now``."""
    async with KanbanSessionLocal() as s:
        # Fresh counter — must NOT be pruned.
        await record_backoff(s, "anthropic")
        # Stale counter — primed via a hand-written row so we control its age.
        long_ago = datetime.now(UTC) - IDLE_RESET_AFTER - timedelta(minutes=1)
        s.add(KanbanMeta(
            key="rate_limit_backoff:minimax",
            value=(
                '{"attempt": 4, "armed_at": "'
                + long_ago.astimezone(UTC).isoformat()
                + '"}'
            ),
        ))
        await s.commit()

    async with KanbanSessionLocal() as s:
        removed = await prune_idle_backoffs(s)
        await s.commit()
        assert removed == 1, "only the stale counter is dropped"
        assert await get_backoff(s, "minimax") is None
        assert (await get_backoff(s, "anthropic")).attempt == 1


@pytest.mark.asyncio
async def test_backoff_sequence_is_strictly_doubling_until_cap():
    """The acceptance criterion is "exponential up to a cap". Pin the exact
    sequence so a future "let's tweak the numbers" refactor cannot silently
    turn the backoff into a constant or a linear ramp."""
    assert BACKOFF_SEQUENCE[0] == 120, "initial window = 2 min"
    for i in range(1, len(BACKOFF_SEQUENCE) - 1):
        assert BACKOFF_SEQUENCE[i] == 2 * BACKOFF_SEQUENCE[i - 1], (
            f"step {i}: expected {2 * BACKOFF_SEQUENCE[i - 1]} "
            f"got {BACKOFF_SEQUENCE[i]}"
        )
    assert BACKOFF_SEQUENCE[-1] <= 3600, "cap is at most 1 hour"


@pytest.mark.asyncio
async def test_window_for_attempt_clamps_at_sequence_end():
    """Pure function: given an attempt count, return the window to use. The
    dispatcher uses this to translate a counter to a pause duration. Past
    the cap the window stops growing — the spec says "exponential up to a
    cap", not "unbounded"."""
    from app.kanban.rate_limit_backoff import window_for_attempt

    assert window_for_attempt(1) == 120
    assert window_for_attempt(2) == 240
    assert window_for_attempt(len(BACKOFF_SEQUENCE)) == BACKOFF_SEQUENCE[-1]
    assert window_for_attempt(len(BACKOFF_SEQUENCE) + 5) == BACKOFF_SEQUENCE[-1]
