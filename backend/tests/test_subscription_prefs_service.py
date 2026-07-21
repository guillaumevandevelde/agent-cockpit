"""Tests for the subscription_prefs singleton service (Anthropic plan tier).

Kaart 9bce091a...: the Subscriptions-pagina needs a user-chosen plan tier
to turn Anthropic's local 5h-block token sum into a ratio — Anthropic
publishes no usage API for Pro/Max (docs/cockpit/subscriptions.md).
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.services import subscription_prefs_service as svc


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_or_create_defaults_to_no_tier(db):
    prefs = await svc.get_or_create_prefs(db)
    assert prefs.anthropic_plan_tier is None
    assert prefs.anthropic_custom_limit_tokens is None


@pytest.mark.asyncio
async def test_set_known_tier_persists(db):
    prefs = await svc.set_anthropic_plan_tier(db, tier="max_5x", custom_limit_tokens=None)
    assert prefs.anthropic_plan_tier == "max_5x"
    assert prefs.anthropic_custom_limit_tokens is None
    reloaded = await svc.get_or_create_prefs(db)
    assert reloaded.anthropic_plan_tier == "max_5x"


@pytest.mark.asyncio
async def test_set_custom_tier_requires_positive_limit(db):
    with pytest.raises(ValueError):
        await svc.set_anthropic_plan_tier(db, tier="custom", custom_limit_tokens=None)
    with pytest.raises(ValueError):
        await svc.set_anthropic_plan_tier(db, tier="custom", custom_limit_tokens=0)


@pytest.mark.asyncio
async def test_set_custom_tier_persists_limit(db):
    prefs = await svc.set_anthropic_plan_tier(db, tier="custom", custom_limit_tokens=123_456)
    assert prefs.anthropic_plan_tier == "custom"
    assert prefs.anthropic_custom_limit_tokens == 123_456


@pytest.mark.asyncio
async def test_set_unknown_tier_rejected(db):
    with pytest.raises(ValueError):
        await svc.set_anthropic_plan_tier(db, tier="platinum", custom_limit_tokens=None)


@pytest.mark.asyncio
async def test_clear_tier_with_none(db):
    await svc.set_anthropic_plan_tier(db, tier="pro", custom_limit_tokens=None)
    prefs = await svc.set_anthropic_plan_tier(db, tier=None, custom_limit_tokens=None)
    assert prefs.anthropic_plan_tier is None
    assert prefs.anthropic_custom_limit_tokens is None


class TestResolvePlanTierLimit:
    """No fabrication: an unresolvable tier must return None, not a guess."""

    def test_known_tier_resolves_to_constant(self):
        limit = svc.resolve_anthropic_plan_tier_limit("max_20x", None)
        assert limit == 880_000

    def test_custom_tier_resolves_to_custom_value(self):
        limit = svc.resolve_anthropic_plan_tier_limit("custom", 55_555)
        assert limit == 55_555

    def test_custom_tier_without_value_resolves_to_none(self):
        assert svc.resolve_anthropic_plan_tier_limit("custom", None) is None

    def test_no_tier_resolves_to_none(self):
        assert svc.resolve_anthropic_plan_tier_limit(None, None) is None

    def test_unknown_tier_resolves_to_none(self):
        assert svc.resolve_anthropic_plan_tier_limit("platinum", None) is None


class TestSyncAnthropicProviderRegistration:
    """``sync_anthropic_provider_registration`` (kaart d404a11f...) is the
    call site that finally uses the stored plan-tier pref to register a
    *real* ``AnthropicUsageProvider`` into the pool-router's registry —
    replacing the honest ``UnknownUsageProvider`` stub
    ``register_default_providers`` seeds at startup, so
    ``pick_subscription``'s drempel branch stops being structurally dead
    (docs/cockpit/subscription-verbruik-inzicht-analyse.md §4.3/§6)."""

    @pytest.fixture(autouse=True)
    def _isolated_registry(self):
        # Self-improve kanban card 7a8788af...: the
        # save/clear/restore dance moved to
        # ``registry.cleared_registry_for_tests`` — keeps the "clean
        # registry" shape this class wants (no seed defaults;
        # register exactly what the test needs) without re-implementing
        # the dance. Sibling to ``seeded_registry_for_tests`` for
        # tests that want the lifespan-mirror state.
        from app.services.subscriptions import registry as reg
        with reg.cleared_registry_for_tests():
            yield

    @pytest.mark.asyncio
    async def test_no_tier_registers_honest_stub(self, db):
        from app.services.subscriptions import registry as reg
        from app.services.subscriptions.unknown import UnknownUsageProvider

        await svc.sync_anthropic_provider_registration(db)

        provider = reg.get_provider_for(cli="claude-code", provider="anthropic")
        assert isinstance(provider, UnknownUsageProvider)
        usage = await provider.get_usage()
        assert usage.betrouwbaarheid == "onbekend"
        assert usage.beschikbaar is True

    @pytest.mark.asyncio
    async def test_configured_tier_registers_real_provider(self, db):
        from app.services.subscriptions import registry as reg
        from app.services.subscriptions.anthropic import AnthropicUsageProvider

        await svc.set_anthropic_plan_tier(db, tier="max_5x", custom_limit_tokens=None)
        await svc.sync_anthropic_provider_registration(db)

        provider = reg.get_provider_for(cli="claude-code", provider="anthropic")
        assert isinstance(provider, AnthropicUsageProvider)
        assert provider._plan_tier_limit_tokens == 220_000

    @pytest.mark.asyncio
    async def test_clearing_tier_reverts_registry_to_stub(self, db):
        from app.services.subscriptions import registry as reg
        from app.services.subscriptions.unknown import UnknownUsageProvider

        await svc.set_anthropic_plan_tier(db, tier="pro", custom_limit_tokens=None)
        await svc.sync_anthropic_provider_registration(db)
        await svc.set_anthropic_plan_tier(db, tier=None, custom_limit_tokens=None)
        await svc.sync_anthropic_provider_registration(db)

        provider = reg.get_provider_for(cli="claude-code", provider="anthropic")
        assert isinstance(provider, UnknownUsageProvider)

    @pytest.mark.asyncio
    async def test_registered_real_provider_makes_pick_subscription_skip_on_threshold(
        self, db, monkeypatch,
    ):
        """The dead branch this card revives: with a REAL registered
        ``AnthropicUsageProvider`` (not a test fake) reporting usage above
        a pool entry's drempel, ``pick_subscription`` skips it — end to
        end through the same registry ``dispatch._gather_pool_usage_snapshots``
        reads."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.kanban.subscription_pool import PoolEntry, pick_subscription
        from app.services.subscriptions import registry as reg
        from app.services.usage_service import UsageService

        # 40_000 tokens against the 44_000-token "pro" 5h limit -> ~0.91 drempel.
        active_block = SimpleNamespace(
            input_tokens=30_000, output_tokens=10_000,
            cache_creation_tokens=0, cache_read_tokens=0, end_time=None,
        )
        monkeypatch.setattr(
            UsageService, "get_block_usage",
            AsyncMock(return_value=SimpleNamespace(active_block=active_block)),
        )

        await svc.set_anthropic_plan_tier(db, tier="pro", custom_limit_tokens=None)
        await svc.sync_anthropic_provider_registration(db)

        provider = reg.get_provider_for(cli="claude-code", provider="anthropic")
        usage = await provider.get_usage()
        assert usage.drempel_gebruikt == pytest.approx(40_000 / 44_000)

        entries = [
            PoolEntry(provider="anthropic", model=None, drempel=0.5),
            PoolEntry(provider="minimax", model=None, drempel=0.9),
        ]
        chosen = pick_subscription(
            entries, {"claude-code:anthropic": usage}, paused_providers=set(),
        )
        # 0.91 >= the entry's own 0.5 drempel -> must spill to minimax.
        assert chosen.provider == "minimax"
