"""Endpoint tests for /api/v1/subscriptions/* — the Subscriptions-pagina's
per-subscription usage view (kaart 9bce091a...).

Acceptance criteria under test:
- one row per known subscription (claude-code:{anthropic,minimax,bedrock},
  codex-cli:codex, copilot-cli:copilot, open-code:open-code);
- Codex/Copilot/OpenCode/Bedrock (no usable local signal) render an honest
  ``betrouwbaarheid="onbekend"`` row, never a fabricated number;
- the Anthropic row is always ``betrouwbaarheid="schatting"`` once a plan
  tier is set, never ``"exact"``;
- the plan-tier endpoints round-trip and reject unknown tiers / a
  non-positive custom limit.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app
from app.services.usage_service import UsageService


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.fixture(autouse=True)
def _isolated_minimax_key(monkeypatch):
    monkeypatch.setattr(settings, "minimax_api_key", None)
    yield
    monkeypatch.setattr(settings, "minimax_api_key", None)


@pytest.fixture(autouse=True)
def _no_real_disk_scan(monkeypatch):
    # UsageService.get_block_usage() otherwise scans this host's real
    # ~/.claude/projects/**/*.jsonl tree (billions of real tokens per the
    # subscription-verbruik-inzicht-analyse.md §4.2 host measurement) —
    # far too slow for a unit test. No active block -> onbekend, which is
    # exactly the "no plan tier yet" honest state this suite exercises
    # unless a test overrides it.
    monkeypatch.setattr(
        UsageService,
        "get_block_usage",
        AsyncMock(return_value=SimpleNamespace(active_block=None)),
    )


@pytest.fixture(autouse=True)
async def _reset_plan_tier():
    # Clear any plan-tier pref left by a previous test in this module.
    async with _client() as ac:
        await ac.put("/api/v1/subscriptions/anthropic/plan-tier", json={"tier": None})
    yield
    async with _client() as ac:
        await ac.put("/api/v1/subscriptions/anthropic/plan-tier", json={"tier": None})


@pytest.mark.asyncio
async def test_usage_lists_one_row_per_known_subscription():
    async with _client() as ac:
        r = await ac.get("/api/v1/subscriptions/usage")
    assert r.status_code == 200, r.text
    ids = {row["subscription_id"] for row in r.json()["subscriptions"]}
    assert ids == {
        "claude-code:anthropic",
        "claude-code:minimax",
        "claude-code:bedrock",
        "codex-cli:codex",
        "copilot-cli:copilot",
        "open-code:open-code",
    }


@pytest.mark.asyncio
async def test_no_signal_subscriptions_are_honestly_onbekend():
    async with _client() as ac:
        r = await ac.get("/api/v1/subscriptions/usage")
    rows = {row["subscription_id"]: row for row in r.json()["subscriptions"]}
    for sub_id in ("claude-code:bedrock", "codex-cli:codex", "copilot-cli:copilot", "open-code:open-code"):
        row = rows[sub_id]
        assert row["betrouwbaarheid"] == "onbekend"
        assert row["drempel_gebruikt"] is None
        assert row["verbruikt"] is None
        assert row["limiet"] is None


@pytest.mark.asyncio
async def test_anthropic_row_without_plan_tier_is_onbekend():
    async with _client() as ac:
        r = await ac.get("/api/v1/subscriptions/usage")
    rows = {row["subscription_id"]: row for row in r.json()["subscriptions"]}
    assert rows["claude-code:anthropic"]["betrouwbaarheid"] == "onbekend"
    assert rows["claude-code:anthropic"]["limiet"] is None


@pytest.mark.asyncio
async def test_anthropic_row_with_plan_tier_is_schatting_never_exact(monkeypatch):
    active_block = SimpleNamespace(
        input_tokens=10_000,
        output_tokens=10_000,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        end_time=None,
    )
    monkeypatch.setattr(
        UsageService,
        "get_block_usage",
        AsyncMock(return_value=SimpleNamespace(active_block=active_block)),
    )
    async with _client() as ac:
        put = await ac.put(
            "/api/v1/subscriptions/anthropic/plan-tier", json={"tier": "max_5x"}
        )
        assert put.status_code == 200, put.text
        r = await ac.get("/api/v1/subscriptions/usage")
    rows = {row["subscription_id"]: row for row in r.json()["subscriptions"]}
    anthropic = rows["claude-code:anthropic"]
    assert anthropic["betrouwbaarheid"] == "schatting"
    assert anthropic["betrouwbaarheid"] != "exact"
    assert anthropic["limiet"] == 220_000
    assert anthropic["verbruikt"] == 20_000


@pytest.mark.asyncio
async def test_minimax_row_without_key_is_onbekend_no_fabrication():
    async with _client() as ac:
        r = await ac.get("/api/v1/subscriptions/usage")
    rows = {row["subscription_id"]: row for row in r.json()["subscriptions"]}
    minimax = rows["claude-code:minimax"]
    assert minimax["betrouwbaarheid"] == "onbekend"
    assert minimax["drempel_gebruikt"] is None


@pytest.mark.asyncio
async def test_plan_tier_get_unset_returns_null():
    async with _client() as ac:
        r = await ac.get("/api/v1/subscriptions/anthropic/plan-tier")
    assert r.status_code == 200
    assert r.json() == {"tier": None, "custom_limit_tokens": None}


@pytest.mark.asyncio
async def test_plan_tier_put_then_get_round_trips():
    async with _client() as ac:
        put = await ac.put(
            "/api/v1/subscriptions/anthropic/plan-tier", json={"tier": "pro"}
        )
        assert put.status_code == 200
        assert put.json() == {"tier": "pro", "custom_limit_tokens": None}
        get = await ac.get("/api/v1/subscriptions/anthropic/plan-tier")
        assert get.json() == {"tier": "pro", "custom_limit_tokens": None}


@pytest.mark.asyncio
async def test_plan_tier_put_rejects_unknown_tier():
    async with _client() as ac:
        r = await ac.put(
            "/api/v1/subscriptions/anthropic/plan-tier", json={"tier": "platinum"}
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_plan_tier_put_custom_requires_positive_limit():
    async with _client() as ac:
        r = await ac.put(
            "/api/v1/subscriptions/anthropic/plan-tier",
            json={"tier": "custom", "custom_limit_tokens": None},
        )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_plan_tier_put_custom_round_trips():
    async with _client() as ac:
        put = await ac.put(
            "/api/v1/subscriptions/anthropic/plan-tier",
            json={"tier": "custom", "custom_limit_tokens": 77_000},
        )
        assert put.status_code == 200, put.text
        assert put.json() == {"tier": "custom", "custom_limit_tokens": 77_000}


@pytest.mark.asyncio
async def test_plan_tiers_options_endpoint_exposes_constants():
    async with _client() as ac:
        r = await ac.get("/api/v1/subscriptions/anthropic/plan-tiers")
    assert r.status_code == 200
    keys = {tier["key"] for tier in r.json()["tiers"]}
    assert keys == {"pro", "max_5x", "max_20x"}
