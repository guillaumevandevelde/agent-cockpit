"""Honest fallback for subscriptions without a usage signal.

Per ``docs/cockpit/subscription-flexibiliteit-analyse.md`` §2.4 / §6.3:
Codex, Copilot and OpenCode have no clean "remaining quota" surface
today (``codex_usage_context_service.py`` is diagnostics-only, not a
stable metric). Instead of fabricating numbers, the provider returns
``onbekend`` with ``beschikbaar=True`` — the fase 1b router treats a
subscription without a signal as "available until the per-provider pause
catches it", which is exactly what we want here.
"""
from __future__ import annotations

from app.services.subscriptions.base import (
    SubscriptionUsage,
    SubscriptionUsageProvider,
)


class UnknownUsageProvider(SubscriptionUsageProvider):
    """Eerlijke fallback voor CLIs zonder usage-signaal.

    Args:
        subscription_id: stable id zoals ``"codex-cli:codex"``,
            ``"copilot-cli:copilot"``, ``"open-code:open-code"``.
        subscription_label: human-readable label voor de UI.
    """

    def __init__(self, subscription_id: str, subscription_label: str):
        self.id = subscription_id
        self.label = subscription_label

    async def get_usage(self) -> SubscriptionUsage:
        return SubscriptionUsage(
            subscription_id=self.id,
            subscription_label=self.label,
            beschikbaar=True,
            drempel_gebruikt=None,
            bron="geen_signaal",
            betrouwbaarheid="onbekend",
        )