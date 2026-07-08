"""Anthropic usage provider.

Reads local Claude Code JSONL via UsageService and the user-selected plan
tier from SubscriptionPref. No remote calls — Anthropic does not publish
a public usage API for Pro/Max tiers.

ANTHROPIC_PLAN_LIMITS values MUST be verified against current Anthropic
plan docs before this module ships. See Task 6 commit message for the
verification sources; values that cannot be verified are set to `None`
(not a guess).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.subscriptions.base import (
    PeriodUsage,
    SubscriptionUsageProvider,
    SubscriptionUsageSnapshot,
)
from app.services.subscriptions.storage import VALID_TIERS, get_pref
from app.services.usage_service import UsageService

logger = logging.getLogger(__name__)


# Plan-tier token limits. Keys MUST match VALID_TIERS exactly.
# After verification against Anthropic's plan docs (July 2026), Anthropic
# only publishes relative limits ("5x more usage than Pro") — not raw token
# caps. Per the spec's honesty requirement, every tier has limit=None so the
# card renders "limit not published by Anthropic" rather than a guess.
ANTHROPIC_PLAN_LIMITS: dict[str, dict[str, int | None]] = {
    "pro":      {"5h_tokens": None, "weekly_tokens": None},  # VERIFY: no source available
    "max_5x":   {"5h_tokens": None, "weekly_tokens": None},  # VERIFY: no source available
    "max_20x":  {"5h_tokens": None, "weekly_tokens": None},  # VERIFY: no source available
    "team":     {"5h_tokens": None, "weekly_tokens": None},  # VERIFY: no source available
}


def valid_tiers() -> set[str]:
    return set(VALID_TIERS)


class AnthropicUsageProvider(SubscriptionUsageProvider):
    provider_id = "anthropic"

    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_snapshot(self) -> SubscriptionUsageSnapshot:
        tier_raw = await get_pref(self._db, "anthropic", "plan_tier")
        if tier_raw is None or tier_raw not in VALID_TIERS:
            return SubscriptionUsageSnapshot(
                provider=self.provider_id,
                plan_label=None,
                periods=(),
                fetched_at=datetime.now(UTC),
                error="Pick an Anthropic plan tier to see your usage.",
                error_code="plan_unknown",
            )

        limits = ANTHROPIC_PLAN_LIMITS[tier_raw]
        svc = UsageService(self._db)
        entries = await svc.get_all_usage_entries(None)

        # 5h rate period.
        blocks = await svc.identify_session_blocks(entries)
        active = next((b for b in blocks if b.is_active), None)
        if active is not None:
            used_5h = (
                active.input_tokens
                + active.output_tokens
                + active.cache_creation_tokens
                + active.cache_read_tokens
            )
            five_h_period = PeriodUsage(
                label="5h rate",
                used=float(used_5h),
                limit=float(limits["5h_tokens"]) if limits["5h_tokens"] is not None else None,
                unit="tokens",
                reset_at=datetime.fromisoformat(active.end_time),
                source="local",
                note="Based on local JSONL; reflects usage, not Anthropic's server-side counter.",
            )
        else:
            five_h_period = PeriodUsage(
                label="5h rate",
                used=0.0,
                limit=float(limits["5h_tokens"]) if limits["5h_tokens"] is not None else None,
                unit="tokens",
                reset_at=datetime.now(UTC) + timedelta(hours=5),
                source="local",
                note="No active 5h block in local JSONL.",
            )

        # Weekly period.
        weekly_total, weekly_reset = await svc.aggregate_weekly(entries)
        weekly_period = PeriodUsage(
            label="Weekly",
            used=float(weekly_total),
            limit=float(limits["weekly_tokens"]) if limits["weekly_tokens"] is not None else None,
            unit="tokens",
            reset_at=weekly_reset,
            source="local",
            note="Based on local JSONL; reflects usage, not Anthropic's server-side counter.",
        )

        return SubscriptionUsageSnapshot(
            provider=self.provider_id,
            plan_label=tier_raw,
            periods=(five_h_period, weekly_period),
            fetched_at=datetime.now(UTC),
        )


def build_anthropic_provider(db: AsyncSession) -> AnthropicUsageProvider:
    """Public factory used by the endpoint in Task 7.

    AnthropicUsageProvider needs a per-request AsyncSession, so it does NOT
    participate in the singleton registry like MinimaxUsageProvider does.
    The endpoint in Task 7 calls this factory directly.
    """
    return AnthropicUsageProvider(db=db)



