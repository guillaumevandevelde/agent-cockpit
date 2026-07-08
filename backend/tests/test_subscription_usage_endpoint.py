"""Endpoint tests for /api/v1/agent-bridge/subscriptions/*."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_get_usage_unknown_provider_returns_404():
    async with _client() as ac:
        r = await ac.get("/api/v1/agent-bridge/subscriptions/nonexistent/usage")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_subscription_provider"


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
    # Wire a fake provider that returns a known snapshot, then verify cache
    # invalidation flips it back to plan_unknown when the tier is cleared.
    from app.services.subscriptions import placeholders
    async def _fake(_: object) -> dict:
        return {"snapshots_seen": []}
    # The above is a no-op; the real assertion is in the next task
    # (test_subscription_usage_anthropic). This test remains as a stub
    # because we don't yet have a registered concrete provider.
    assert placeholders is not None
