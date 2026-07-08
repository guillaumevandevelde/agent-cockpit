"""Placeholder providers used by Task 3 only.

Task 4 (Minimax) and Task 5 (Anthropic) overwrite the entries in
_PROVIDERS via `register_usage_provider` at import time. Until those
tasks land, both providers return their empty-state snapshot:
- anthropic: `plan_unknown`
- minimax: `not_configured`
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.config import settings
from app.services.subscriptions import register_usage_provider
from app.services.subscriptions.base import (
    SubscriptionUsageProvider,
    SubscriptionUsageSnapshot,
)


class PlaceholderAnthropicProvider(SubscriptionUsageProvider):
    provider_id = "anthropic"

    async def get_snapshot(self) -> SubscriptionUsageSnapshot:
        return SubscriptionUsageSnapshot(
            provider=self.provider_id,
            plan_label=None,
            periods=(),
            fetched_at=datetime.now(UTC),
            error="Pick an Anthropic plan tier to see your usage.",
            error_code="plan_unknown",
        )


class PlaceholderMinimaxProvider(SubscriptionUsageProvider):
    provider_id = "minimax"

    async def get_snapshot(self) -> SubscriptionUsageSnapshot:
        if not settings.minimax_api_key:
            return SubscriptionUsageSnapshot(
                provider=self.provider_id,
                plan_label=None,
                periods=(),
                fetched_at=datetime.now(UTC),
                error="MiniMax API key not configured.",
                error_code="not_configured",
            )
        return SubscriptionUsageSnapshot(
            provider=self.provider_id,
            plan_label=None,
            periods=(),
            fetched_at=datetime.now(UTC),
            error="MiniMax usage endpoint not yet wired up.",
            error_code="no_endpoint",
        )


register_usage_provider(PlaceholderAnthropicProvider())
register_usage_provider(PlaceholderMinimaxProvider())
