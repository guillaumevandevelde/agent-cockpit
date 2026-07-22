"""Anthropic usage provider — 5h-block estimation from ``UsageService``.

Per ``docs/cockpit/subscription-flexibiliteit-analyse.md`` §2.4 / §6.1:
Anthropic publishes no usage API for Pro/Max. The only honest signal
we have locally is the 5h billing block derived from JSONL logs, scaled
by a user-selected plan-tier limit. Weekly is not published and we do
not fabricate it — the schema returns the 5h-based ratio only.

The output is therefore **always** ``betrouwbaarheid="schatting"`` when
it returns a number (never ``"exact"``); ``"onbekend"`` when there is no
active block, no plan-tier limit, or a non-positive limit.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from app.services.subscriptions.base import (
    SubscriptionUsage,
    SubscriptionUsageProvider,
)

if TYPE_CHECKING:
    from app.services.usage_service import UsageService

logger = logging.getLogger(__name__)

WINDOW_LABEL = "5h rate"

# Best-effort community estimates for each plan tier's 5h-window token
# budget — Anthropic does not publish these numbers for Pro/Max (analyse
# §7.2). Never presented as "exact"; the UI must show a "verify before
# trusting" note alongside whichever tier the user picks (subscriptions.md).
# A user who knows their real number can use the "custom" tier instead of
# trusting these.
ANTHROPIC_PLAN_TIERS: dict[str, dict[str, object]] = {
    "pro": {"label": "Pro", "tokens_5h": 44_000},
    "max_5x": {"label": "Max 5x", "tokens_5h": 220_000},
    "max_20x": {"label": "Max 20x", "tokens_5h": 880_000},
}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class AnthropicUsageProvider(SubscriptionUsageProvider):
    """5h-venster-schatting for Claude Code op het Anthropic-abonnement.

    Args:
        usage_service: instance met ``get_block_usage(active=True)``
            (de ``UsageService`` die de JSONL-logs parseert).
        plan_tier_limit_tokens: door de gebruiker gekozen plan-tier
            limiet in tokens per 5h venster. Wanneer None of <= 0 kan
            de provider geen ratio berekenen — de snapshot gaat terug
            naar ``onbekend`` (subscriptions.md: "verify before trusting",
            geen fabricage).
        subscription_id: stable id, default ``"claude-code:anthropic"``.
        subscription_label: human-readable label voor de UI.
    """

    DEFAULT_ID = "claude-code:anthropic"
    DEFAULT_LABEL = "Claude Code (Anthropic)"

    def __init__(
        self,
        usage_service: UsageService,
        plan_tier_limit_tokens: int | None,
        subscription_id: str = DEFAULT_ID,
        subscription_label: str = DEFAULT_LABEL,
    ):
        self._usage_service = usage_service
        self._plan_tier_limit_tokens = plan_tier_limit_tokens
        self.id = subscription_id
        self.label = subscription_label

    async def get_usage(self) -> SubscriptionUsage:
        if not self._plan_tier_limit_tokens or self._plan_tier_limit_tokens <= 0:
            # Geen zinvolle plan-tier → geen fabricage. De UI toont
            # "limit not published" / "select a plan tier".
            return SubscriptionUsage(
                subscription_id=self.id,
                subscription_label=self.label,
                beschikbaar=True,
                drempel_gebruikt=None,
                bron="geen_plan_tier",
                betrouwbaarheid="onbekend",
            )

        try:
            blocks = await self._usage_service.get_block_usage(
                active=True, subscription_id=self.id
            )
        except Exception:
            # Een crashende UsageService mag de provider niet stillekens
            # iets anders laten suggereren — label onbekend.
            logger.exception(
                "AnthropicUsageProvider: UsageService.get_block_usage raised"
            )
            return SubscriptionUsage(
                subscription_id=self.id,
                subscription_label=self.label,
                beschikbaar=True,
                drempel_gebruikt=None,
                bron="usage_service:fout",
                betrouwbaarheid="onbekend",
            )

        active_block = getattr(blocks, "active_block", None)
        if active_block is None:
            return SubscriptionUsage(
                subscription_id=self.id,
                subscription_label=self.label,
                beschikbaar=True,
                drempel_gebruikt=None,
                bron="usage_service:geen_actief_block",
                betrouwbaarheid="onbekend",
            )

        # cache_read_tokens telt bewust NIET mee: de gecontroleerde meting in
        # docs/cockpit/cache-read-quota-decision.md (Scenario B, w≈0) toont dat
        # cache_read geen abonnementsquotum kost, terwijl het ~96% van het
        # tokenvolume is. Meesommeren overschatte drempel_gebruikt ~20x en
        # pauzeerde subscription_pool-abonnementen veel te vroeg.
        total_tokens = (
            getattr(active_block, "input_tokens", 0)
            + getattr(active_block, "output_tokens", 0)
            + getattr(active_block, "cache_creation_tokens", 0)
        )
        drempel_gebruikt = total_tokens / self._plan_tier_limit_tokens
        return SubscriptionUsage(
            subscription_id=self.id,
            subscription_label=self.label,
            beschikbaar=drempel_gebruikt < 1.0,
            drempel_gebruikt=drempel_gebruikt,
            bron="usage_service:active_block",
            betrouwbaarheid="schatting",
            verbruikt=total_tokens,
            limiet=self._plan_tier_limit_tokens,
            eenheid="tokens",
            venster_label=WINDOW_LABEL,
            reset_op=_parse_iso(getattr(active_block, "end_time", None)),
        )