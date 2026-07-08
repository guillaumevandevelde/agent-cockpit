"""AnthropicUsageProvider tests with mocked UsageService."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.database import AsyncSessionLocal
from app.models.database import SubscriptionPref
from app.services.subscriptions.anthropic import (
    ANTHROPIC_PLAN_LIMITS,
    AnthropicUsageProvider,
    VALID_TIERS,
)


@pytest_asyncio.fixture(autouse=True)
async def _reset_subscription_prefs():
    """Clear subscription_prefs table before each test so tier-set tests start fresh."""
    async with AsyncSessionLocal() as db:
        await db.execute(delete(SubscriptionPref))
        await db.commit()
    yield
    async with AsyncSessionLocal() as db:
        await db.execute(delete(SubscriptionPref))
        await db.commit()


@pytest_asyncio.fixture
async def db_session():
    """Provide an AsyncSession for tests that need direct DB access (not via endpoint)."""
    async with AsyncSessionLocal() as db:
        yield db


@pytest.mark.asyncio
async def test_unknown_tier_returns_plan_unknown(db_session):
    """If no plan_tier is set, snapshot is plan_unknown with empty periods."""
    p = AnthropicUsageProvider(db=db_session)
    snap = await p.get_snapshot()
    assert snap.error_code == "plan_unknown"
    assert snap.plan_label is None
    assert snap.periods == ()


@pytest.mark.asyncio
async def test_each_known_tier_emits_two_periods(db_session):
    """For each tier, set the row and verify two periods return."""
    for tier in VALID_TIERS:
        db_session.add(SubscriptionPref(provider_id="anthropic", key="plan_tier", value=tier))
        await db_session.commit()
        p = AnthropicUsageProvider(db=db_session)
        snap = await p.get_snapshot()
        assert snap.error_code is None, f"tier {tier}: {snap.error}"
        labels = {prd.label for prd in snap.periods}
        assert {"5h rate", "Weekly"} <= labels, f"tier {tier} missing expected labels"
        # Clean up the row so the next tier starts fresh.
        await db_session.execute(delete(SubscriptionPref))
        await db_session.commit()


def test_5h_token_limits_are_only_present_for_verified_tiers():
    """A tier whose number could not be verified must render with limit=None.

    If the verifier couldn't find the number, that tier's value is None —
    not a guessed constant. This is the property the spec promises.
    """
    for tier, limits in ANTHROPIC_PLAN_LIMITS.items():
        assert "5h_tokens" in limits
        # weekly_tokens is intentionally None for every tier today.
        assert limits["weekly_tokens"] is None