"""Endpoint tests for /api/v1/agent-bridge/subscriptions/*."""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.config import settings
from app.database import AsyncSessionLocal
from app.main import app
from app.models.database import SubscriptionPref


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.fixture(autouse=True)
def _isolate_minimax_api_key(monkeypatch):
    """Ensure settings.minimax_api_key is None at the start and end of each test.

    The minimax unconfigured test depends on this being None; the
    minimax mocked-httpx tests will override per-test.
    """
    monkeypatch.setattr(settings, "minimax_api_key", None)
    yield
    monkeypatch.setattr(settings, "minimax_api_key", None)


@pytest_asyncio.fixture(autouse=True)
async def _reset_subscription_prefs():
    """Clear subscription_prefs table before each test.

    Some tests (e.g. test_plan_tier_put_then_get_round_trips) write rows
    that must not leak into later tests. This makes the suite
    deterministic regardless of run order.
    """
    async with AsyncSessionLocal() as db:
        await db.execute(delete(SubscriptionPref))
        await db.commit()
    yield


@pytest.mark.asyncio
async def test_get_usage_unknown_provider_returns_404():
    async with _client() as ac:
        r = await ac.get("/api/v1/agent-bridge/subscriptions/nonexistent/usage")
    assert r.status_code == 404
    # The global 404 handler in app/main.py returns a plain string detail.
    # We don't assert the structured detail.code here because the global
    # handler masks it — that's a pre-existing concern unrelated to this PR.
    assert r.json()["detail"] == "Not Found"


@pytest.mark.asyncio
async def test_get_usage_anthropic_unknown_plan_returns_plan_unknown():
    async with _client() as ac:
        r = await ac.get("/api/v1/agent-bridge/subscriptions/anthropic/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "anthropic"
    assert body["plan_label"] is None
    assert body["periods"] == []
    assert body["error_code"] == "plan_unknown"


@pytest.mark.asyncio
async def test_get_usage_minimax_unconfigured_returns_not_configured():
    async with _client() as ac:
        r = await ac.get("/api/v1/agent-bridge/subscriptions/minimax/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "minimax"
    assert body["error_code"] == "not_configured"
    assert body["periods"] == []


@pytest.mark.asyncio
async def test_plan_tier_get_unset_returns_null():
    async with _client() as ac:
        r = await ac.get("/api/v1/agent-bridge/subscriptions/anthropic/plan-tier")
    assert r.status_code == 200
    assert r.json() == {"tier": None}


@pytest.mark.asyncio
async def test_plan_tier_put_then_get_round_trips():
    async with _client() as ac:
        r = await ac.put(
            "/api/v1/agent-bridge/subscriptions/anthropic/plan-tier",
            json={"tier": "max_5x"},
        )
        assert r.status_code == 200
        assert r.json() == {"tier": "max_5x"}

        r2 = await ac.get("/api/v1/agent-bridge/subscriptions/anthropic/plan-tier")
        assert r2.json() == {"tier": "max_5x"}


@pytest.mark.asyncio
async def test_plan_tier_put_rejects_unknown_tier():
    async with _client() as ac:
        r = await ac.put(
            "/api/v1/agent-bridge/subscriptions/anthropic/plan-tier",
            json={"tier": "platinum"},
        )
    assert r.status_code == 400
    assert "platinum" in r.text


@pytest.mark.asyncio
async def test_plan_tier_put_invalidates_cached_snapshot(monkeypatch):
    """After PUT, the next /usage call must NOT return the cached pre-PUT snapshot."""
    async with _client() as ac:
        r = await ac.put(
            "/api/v1/agent-bridge/subscriptions/anthropic/plan-tier",
            json={"tier": "max_5x"},
        )
        assert r.status_code == 200

        # First /usage call populates the cache.
        r = await ac.get("/api/v1/agent-bridge/subscriptions/anthropic/usage")
        assert r.status_code == 200
        first = r.json()
        assert first["error_code"] is None
        assert any(p["label"] == "5h rate" for p in first["periods"])

        # PUT a different tier -> should invalidate cache.
        r = await ac.put(
            "/api/v1/agent-bridge/subscriptions/anthropic/plan-tier",
            json={"tier": "pro"},
        )
        assert r.status_code == 200

        # Next /usage call should reflect pro (plan_label=pro) not max_5x.
        r = await ac.get("/api/v1/agent-bridge/subscriptions/anthropic/usage")
        assert r.json()["plan_label"] == "pro"
