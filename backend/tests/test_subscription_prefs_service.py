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
