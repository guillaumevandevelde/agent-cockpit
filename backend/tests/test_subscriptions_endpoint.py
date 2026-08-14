"""Endpoint tests for /api/v1/subscriptions/* — the Subscriptions-pagina's
per-subscription usage view (kaart 9bce091a...).

Acceptance criteria under test:
- one row per **held** subscription — claude-code:{anthropic,minimax},
  codex-cli:codex, open-code:open-code — and no row for a subscription
  nobody owns. ``bedrock``, ``copilot-cli`` and the router row
  ``anthropic-compatible`` were dropped: they could only ever render
  "no signal", so they buried the rows that carry numbers;
- a subscription whose provider is not wired yet still renders an honest
  ``betrouwbaarheid="onbekend"`` row, never a fabricated number;
- the Anthropic row is always ``betrouwbaarheid="schatting"`` when it has
  an active block, never ``"exact"``;
- the Anthropic row reports an absolute token count with no ``limiet`` and
  no ``drempel_gebruikt``: no published quota exists, so no ratio is
  fabricated and the row never flips to "unavailable" on a guess.
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
def _seed_registry():
    """Mirror ``main.lifespan``'s seed so the registry contains the
    realistic default-providers (UnknownUsageProvider for the legacy
    trio + RouterUsageProvider for ``anthropic-compatible``, kaart
    390756e6...). The endpoint loop prefers a registered provider over
    a freshly-constructed UnknownUsageProvider fallback, so this
    fixture is what makes the router-row's ``bron`` /
    ``subscription_label`` show up under test — without it the
    ASGITransport-based client never triggers ``lifespan``, and the
    registry stays empty.

    Self-improve kanban card 7a8788af...: the
    save/clear/seed-defaults/restore dance previously lived inline
    here; it now lives in
    ``app.services.subscriptions.registry.seeded_registry_for_tests``
    so a future endpoint-test (or any test that needs the realistic
    lifespan state) gets it for free without copy-pasting the four
    steps — and without forgetting the restore that would otherwise
    leak the seeded defaults into the next test.
    """
    from app.services.subscriptions import registry as _reg
    with _reg.seeded_registry_for_tests():
        yield


@pytest.fixture(autouse=True)
def _isolated_minimax_key(monkeypatch):
    monkeypatch.setattr(settings, "minimax_api_key", None)
    yield
    monkeypatch.setattr(settings, "minimax_api_key", None)


@pytest.fixture(autouse=True)
def _no_real_opencode_or_codex_state(monkeypatch, tmp_path):
    """Point the opencode and codex providers at empty temp dirs.

    Both read this developer's real data — ``~/.local/share/opencode/
    opencode.db`` (571 MB, $57 of live spend) and ``~/.codex/sessions``.
    Without this the suite's assertions would depend on whoever ran
    opencode last, and would differ between this box and CI.

    Patched **on the consumer modules**, not on their source: both
    providers do ``from ... import name`` at import time, so patching
    ``agentic_cli.open_code`` / ``agentic_cli.codex_cli`` would leave the
    already-bound reference untouched and the test would silently keep
    reading the real paths (CLAUDE.md, "patch where the consumer looks").
    """
    monkeypatch.setattr(
        "app.services.subscriptions.opencode_go._opencode_db_path",
        lambda data_dir=None: tmp_path / "absent" / "opencode.db",
    )
    monkeypatch.setattr(
        "app.services.subscriptions.codex.get_codex_home",
        lambda: tmp_path / "absent-codex",
    )


@pytest.fixture(autouse=True)
def _no_real_disk_scan(monkeypatch):
    # UsageService.get_block_usage() otherwise scans this host's real
    # ~/.claude/projects/**/*.jsonl tree (billions of real tokens per the
    # subscription-verbruik-inzicht-analyse.md §4.2 host measurement) —
    # far too slow for a unit test. No active block -> onbekend, the honest
    # idle state this suite exercises unless a test overrides it.
    monkeypatch.setattr(
        UsageService,
        "get_block_usage",
        AsyncMock(return_value=SimpleNamespace(active_block=None)),
    )


@pytest.mark.asyncio
async def test_usage_lists_one_row_per_held_subscription():
    async with _client() as ac:
        r = await ac.get("/api/v1/subscriptions/usage")
    assert r.status_code == 200, r.text
    ids = {row["subscription_id"] for row in r.json()["subscriptions"]}
    assert ids == {
        "claude-code:anthropic",
        "claude-code:minimax",
        "codex-cli:codex",
        "open-code:open-code",
    }


@pytest.mark.asyncio
async def test_unheld_subscriptions_are_absent_not_empty_rows():
    # Regression guard for the shape this endpoint used to have: seven
    # rows of which six said "no signal". These three describe
    # subscriptions nobody owns — bedrock was never configured,
    # copilot-cli is not a plan we hold, and anthropic-compatible is an
    # endpoint shape rather than a subscription. An empty row for a thing
    # you do not own is clutter, not honesty.
    async with _client() as ac:
        r = await ac.get("/api/v1/subscriptions/usage")
    ids = {row["subscription_id"] for row in r.json()["subscriptions"]}
    for gone in (
        "claude-code:bedrock",
        "claude-code:anthropic-compatible",
        "copilot-cli:copilot",
    ):
        assert gone not in ids


@pytest.mark.asyncio
async def test_providers_without_local_state_are_honestly_onbekend():
    # With no opencode.db and no codex rollouts on disk these rows must
    # say "no signal" rather than report a confident zero — "we cannot
    # see" and "you have used nothing" are different claims, and only
    # one of them is true here.
    async with _client() as ac:
        r = await ac.get("/api/v1/subscriptions/usage")
    rows = {row["subscription_id"]: row for row in r.json()["subscriptions"]}
    assert rows["codex-cli:codex"]["bron"] == "codex_rollout:no_sessions"
    assert rows["open-code:open-code"]["bron"] == "opencode_db:absent"
    for sub_id in ("codex-cli:codex", "open-code:open-code"):
        row = rows[sub_id]
        assert row["betrouwbaarheid"] == "onbekend"
        assert row["drempel_gebruikt"] is None
        assert row["verbruikt"] is None
        assert row["limiet"] is None


@pytest.mark.asyncio
async def test_rows_without_windows_report_an_empty_list():
    # The ``windows`` field must always be present so the frontend can
    # map over it unconditionally — a missing key would make every
    # no-signal row a runtime error in the row component.
    async with _client() as ac:
        r = await ac.get("/api/v1/subscriptions/usage")
    for row in r.json()["subscriptions"]:
        assert row["windows"] == []


@pytest.mark.asyncio
async def test_anthropic_row_without_active_block_is_onbekend():
    async with _client() as ac:
        r = await ac.get("/api/v1/subscriptions/usage")
    rows = {row["subscription_id"]: row for row in r.json()["subscriptions"]}
    assert rows["claude-code:anthropic"]["betrouwbaarheid"] == "onbekend"
    assert rows["claude-code:anthropic"]["limiet"] is None


@pytest.mark.asyncio
async def test_anthropic_row_reports_absolute_usage_and_never_exact(monkeypatch):
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
        r = await ac.get("/api/v1/subscriptions/usage")
    rows = {row["subscription_id"]: row for row in r.json()["subscriptions"]}
    anthropic = rows["claude-code:anthropic"]
    assert anthropic["betrouwbaarheid"] == "schatting"
    assert anthropic["betrouwbaarheid"] != "exact"
    assert anthropic["verbruikt"] == 20_000
    # No published quota exists, so no denominator is reported and the row
    # never flips to "unavailable" on a guessed budget.
    assert anthropic["limiet"] is None
    assert anthropic["drempel_gebruikt"] is None
    assert anthropic["beschikbaar"] is True


@pytest.mark.asyncio
async def test_minimax_row_without_key_is_onbekend_no_fabrication():
    async with _client() as ac:
        r = await ac.get("/api/v1/subscriptions/usage")
    rows = {row["subscription_id"]: row for row in r.json()["subscriptions"]}
    minimax = rows["claude-code:minimax"]
    assert minimax["betrouwbaarheid"] == "onbekend"
    assert minimax["drempel_gebruikt"] is None
