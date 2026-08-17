"""``run_write_with_retry`` absorbs a brief SQLITE_BUSY overlap between writers.

SQLite takes one writer at a time. When the auto-dispatch tick holds the write
lock past ``sqlite_busy_timeout_ms``, every concurrent REST write used to die
as an unhandled 500 — reproduced against the live backend by holding the lock
for 8s and watching ``POST /kanban/cards`` fail after exactly 5.03s.
"""
import pytest
from sqlalchemy.exc import OperationalError

from app.kanban.db import is_sqlite_locked_error, run_write_with_retry


def _locked_error() -> OperationalError:
    """The exact shape SQLAlchemy raises when aiosqlite reports SQLITE_BUSY."""
    import sqlite3

    return OperationalError(
        "INSERT INTO kanban_ops ...", {}, sqlite3.OperationalError("database is locked"),
    )


def _missing_table_error() -> OperationalError:
    import sqlite3

    return OperationalError(
        "SELECT ...", {}, sqlite3.OperationalError("no such table: kanban_meta"),
    )


def test_locked_error_is_recognised():
    assert is_sqlite_locked_error(_locked_error()) is True


def test_other_operational_errors_are_not_treated_as_locks():
    """A missing table must never be retried — it is a defect, not contention."""
    assert is_sqlite_locked_error(_missing_table_error()) is False
    assert is_sqlite_locked_error(ValueError("database is locked")) is False


@pytest.mark.asyncio
async def test_retries_until_the_lock_clears():
    calls = []

    async def flaky(session):
        calls.append(session)
        if len(calls) < 3:
            raise _locked_error()
        return "committed"

    assert await run_write_with_retry(flaky, label="test") == "committed"
    assert len(calls) == 3, "expected two retries before the successful attempt"
    # Each attempt must get its own session: a session that raised is unusable,
    # and reusing it would replay the failed transaction's state.
    assert len({id(s) for s in calls}) == 3


@pytest.mark.asyncio
async def test_succeeds_without_retrying_when_there_is_no_contention():
    calls = []

    async def clean(session):
        calls.append(session)
        return "ok"

    assert await run_write_with_retry(clean, label="test") == "ok"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_gives_up_after_the_attempt_budget():
    calls = []

    async def always_locked(session):
        calls.append(session)
        raise _locked_error()

    with pytest.raises(OperationalError):
        await run_write_with_retry(always_locked, label="test")
    assert len(calls) == 4, "expected 4 attempts (initial + 3 backoffs)"


@pytest.mark.asyncio
async def test_non_lock_errors_surface_on_the_first_attempt():
    calls = []

    async def broken(session):
        calls.append(session)
        raise _missing_table_error()

    with pytest.raises(OperationalError):
        await run_write_with_retry(broken, label="test")
    assert len(calls) == 1, "a non-lock OperationalError must not be retried"
