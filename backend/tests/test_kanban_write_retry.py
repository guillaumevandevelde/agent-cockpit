"""Tests for ``run_write_with_retry`` — the kanban write-lock retry vangnet.

Pin the contract documented in
``docs/cockpit/kanban-write-retry-vangnet-decision.md`` §4 kind-2:

* Retry only ``sqlalchemy.exc.OperationalError`` whose ``str(exc.orig)``
  contains ``"database is locked"``.
* Bounded retries (default 3) and bounded total wait (default 2s).
* Fresh session per attempt (the coro_factory runs each retry).
* Non-lock ``OperationalError`` (schema-mismatch etc.) is *not* retried.
* ``ClaimRejected`` bubbles up unchanged.

The tests use a tight ``backoff_base_ms=1, total_budget_ms=100`` to keep the
suite fast — the bound itself is tested elsewhere in the matrix (kind-1).
"""
import sqlite3
import unittest.mock as mock

import pytest
from sqlalchemy.exc import OperationalError

from app.kanban.db import run_write_with_retry
from app.kanban.operations import ClaimRejected


def _locked_error() -> OperationalError:
    """Construct an OperationalError whose wrapped sqlite3 cause is ``database is locked``."""
    return OperationalError(
        "INSERT INTO foo ...", {},
        sqlite3.OperationalError("database is locked"),
    )


def _other_error() -> OperationalError:
    """Construct an OperationalError whose wrapped cause is *not* a lock error."""
    return OperationalError(
        "INSERT INTO foo ...", {},
        sqlite3.OperationalError("no such table: bar"),
    )


@pytest.mark.asyncio
async def test_retries_lock_then_succeeds():
    """First attempt raises lock-OperationalError; second attempt succeeds."""
    attempts = 0

    async def coro_factory():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _locked_error()
        return "ok"

    result = await run_write_with_retry(
        coro_factory, backoff_base_ms=1, total_budget_ms=100,
    )
    assert result == "ok"
    assert attempts == 2


@pytest.mark.asyncio
async def test_exhausted_retries_raise_last_error():
    """All attempts raise lock-OperationalError; final raises after max_retries+1."""
    attempts = 0

    async def coro_factory():
        nonlocal attempts
        attempts += 1
        raise _locked_error()

    with pytest.raises(OperationalError) as exc_info:
        await run_write_with_retry(
            coro_factory, max_retries=3, backoff_base_ms=1, total_budget_ms=100,
        )
    # 1 initial call + 3 retries = 4 total attempts.
    assert attempts == 4
    # The raised error is the (last) lock-OperationalError, not a wrapped one.
    assert "database is locked" in str(exc_info.value.orig)


@pytest.mark.asyncio
async def test_non_lock_operational_error_not_retried():
    """OperationalError whose cause is not "database is locked" bubbles up immediately."""
    attempts = 0

    async def coro_factory():
        nonlocal attempts
        attempts += 1
        raise _other_error()

    with pytest.raises(OperationalError) as exc_info:
        await run_write_with_retry(
            coro_factory, backoff_base_ms=1, total_budget_ms=100,
        )
    assert attempts == 1
    assert "no such table" in str(exc_info.value.orig)


@pytest.mark.asyncio
async def test_fresh_session_per_attempt():
    """Each retry must invoke the coro_factory afresh; the previous attempt's
    session must not be reachable from the next attempt's session.

    Pins the ``opent per poging een verse KanbanSessionLocal()`` invariant
    from §4 kind-2: the wrapper must not cache the previous session
    anywhere that would leak pre-mutation state into the next attempt.
    """
    sessions: list[mock.Mock] = []

    async def coro_factory():
        # Each call builds a fresh mock session. The helper must call us
        # again on the next attempt — it must not retain this object.
        session = mock.Mock()
        sessions.append(session)
        if len(sessions) < 3:
            raise _locked_error()
        return "ok"

    result = await run_write_with_retry(
        coro_factory, max_retries=5, backoff_base_ms=1, total_budget_ms=500,
    )
    assert result == "ok"
    assert len(sessions) == 3
    # Each attempt must have produced a distinct session object.
    assert sessions[0] is not sessions[1]
    assert sessions[1] is not sessions[2]
    assert sessions[0] is not sessions[2]


@pytest.mark.asyncio
async def test_claim_rejected_bubbles_up_unchanged():
    """ClaimRejected is business-logic, not lock contention; bubbles up unchanged."""
    attempts = 0

    async def coro_factory():
        nonlocal attempts
        attempts += 1
        raise ClaimRejected("actor-A")

    with pytest.raises(ClaimRejected) as exc_info:
        await run_write_with_retry(
            coro_factory, backoff_base_ms=1, total_budget_ms=100,
        )
    assert attempts == 1
    assert exc_info.value.current_owner == "actor-A"


@pytest.mark.asyncio
async def test_total_budget_stops_retries_even_if_retries_remain():
    """When the total wait would exceed ``total_budget_ms``, the wrapper stops
    and raises the last lock-OperationalError — even if retries remain.

    Pin the bound: with max_retries=10 and ``total_budget_ms=1``, the wrapper
    must not block forever waiting for retries that the budget has already
    exhausted.
    """
    attempts = 0

    async def coro_factory():
        nonlocal attempts
        attempts += 1
        raise _locked_error()

    with pytest.raises(OperationalError):
        await run_write_with_retry(
            coro_factory,
            max_retries=10,
            backoff_base_ms=50,
            total_budget_ms=1,
        )
    # The wrapper should bail well before 11 attempts given the 1ms budget.
    assert attempts < 11
