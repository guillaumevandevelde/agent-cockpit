"""``POST /kanban/cards`` must survive brief write-lock contention.

Root cause it guards: SQLite serialises writers, and the auto-dispatch tick
used to hold one transaction across its whole resolution phase. Any card
created in that window died with an unhandled 500 —
``sqlite3.OperationalError: database is locked``, five times inside a single
two-minute tick in ``logs/backend/run-20260817-082951-3592-0.log``.

Exhausting the retry budget means a writer is holding the lock for far longer
than any writer should, which is a bug in *that* writer — so the honest answer
is a 503 the caller can retry, not a 500 that reads like a board defect.
"""
import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

from app.main import app

_CARD = {"project_key": "P", "title": "Card under lock", "confirm_new_project": True}


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _locked_error() -> OperationalError:
    return OperationalError(
        "INSERT INTO kanban_ops ...", {}, sqlite3.OperationalError("database is locked"),
    )


@pytest.mark.asyncio
async def test_create_card_retries_past_a_transient_lock(monkeypatch):
    """One locked attempt, then success — the caller still gets its 201."""
    import app.api.v1.kanban.router as router

    calls = {"n": 0}
    real = router.apply_operation

    async def flaky_apply_operation(session, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _locked_error()
        return await real(session, **kwargs)

    # Patch the *consumer's* binding: router.py does
    # `from app.kanban.operations import apply_operation`, so patching the
    # source module would leave this call site pointing at the original
    # (docs/cockpit/test-doubles-convention.md).
    monkeypatch.setattr(router, "apply_operation", flaky_apply_operation)

    async with _client() as ac:
        r = await ac.post("/api/v1/kanban/cards", json=_CARD)

    assert r.status_code == 201, r.text
    assert calls["n"] == 2, "the double must have fired twice (one lock, one success)"
    assert r.json()["title"] == "Card under lock"


@pytest.mark.asyncio
async def test_create_card_creates_exactly_one_card_after_a_retry(monkeypatch):
    """A retried attempt must not leave a half-created card behind."""
    import app.api.v1.kanban.router as router

    calls = {"n": 0}
    real = router.apply_operation

    async def flaky_apply_operation(session, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _locked_error()
        return await real(session, **kwargs)

    monkeypatch.setattr(router, "apply_operation", flaky_apply_operation)

    async with _client() as ac:
        assert (await ac.post("/api/v1/kanban/cards", json=_CARD)).status_code == 201
        r = await ac.get("/api/v1/kanban/cards", params={"project_key": "P"})

    titles = [c["title"] for c in r.json()["items"]]
    assert titles.count("Card under lock") == 1, titles


@pytest.mark.asyncio
async def test_create_card_returns_503_when_the_lock_never_clears(monkeypatch):
    """A permanently locked board is retryable (503), not a defect (500)."""
    import app.api.v1.kanban.router as router

    calls = {"n": 0}

    async def always_locked(session, **kwargs):
        calls["n"] += 1
        raise _locked_error()

    monkeypatch.setattr(router, "apply_operation", always_locked)

    async with _client() as ac:
        r = await ac.post("/api/v1/kanban/cards", json=_CARD)

    assert r.status_code == 503, r.text
    assert calls["n"] == 4, "expected the full attempt budget before giving up"
    assert "busy" in r.json()["detail"]


@pytest.mark.asyncio
async def test_create_card_does_not_retry_a_real_defect(monkeypatch):
    """A non-lock OperationalError must surface immediately, not 4x."""
    import app.api.v1.kanban.router as router

    calls = {"n": 0}

    async def broken(session, **kwargs):
        calls["n"] += 1
        raise OperationalError(
            "SELECT ...", {}, sqlite3.OperationalError("no such table: kanban_meta"),
        )

    monkeypatch.setattr(router, "apply_operation", broken)

    async with _client() as ac:
        with pytest.raises(OperationalError):
            await ac.post("/api/v1/kanban/cards", json=_CARD)

    assert calls["n"] == 1, "a genuine schema error must not be retried"
