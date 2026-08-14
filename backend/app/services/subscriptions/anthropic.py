"""Anthropic usage provider — absolute 5h-block usage from ``UsageService``.

Per ``docs/cockpit/subscription-flexibiliteit-analyse.md`` §2.4 / §6.1:
Anthropic publishes no usage API for Pro/Max. The only honest signal we
have locally is the 5h billing block derived from JSONL logs.

**Superseded in part, 2026-08-14.** The premise below — "Anthropic
publishes no usage API for Pro/Max" — was true of *APIs* and is still
true of them, but Claude Code itself now hands the official
``rate_limits`` (5h + 7d utilization with reset times) to whatever
renders the statusline. ``statusline_state.read_windows`` reads the
capture of that blob, and it takes precedence over everything described
here. The local estimate below is the fallback for when no capture
exists — an unwrapped statusline, or no session since the window rolled.

This provider used to scale that number by a user-picked plan-tier limit
and report a percentage. That was removed: a measurement on this machine
found every non-empty 5h block exceeding even the Max 20x community
estimate (peak 3.47M against an assumed 880k budget), so no published
tier produced an honest denominator. The percentage was decorative, and
worse, a ratio above 1.0 set ``beschikbaar=False`` and made
``subscription_pool`` pause the lane on a fabricated limit.

So the provider now reports the **absolute** token count for the active
block and leaves ``limiet`` / ``drempel_gebruikt`` as ``None``. The pool
reads a missing ratio as "available" (see ``_is_above_threshold``), which
is correct — the real backstop is the per-provider pause fired by actual
rate-limit events, not a guessed budget.

``betrouwbaarheid`` stays ``"schatting"``: the count is summed from local
JSONL logs, so it measures what this machine logged, not what Anthropic
billed. ``"onbekend"`` when there is no active block at all.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.services.subscriptions.base import (
    SubscriptionUsage,
    SubscriptionUsageProvider,
)
from app.services.subscriptions.statusline_state import read_windows

if TYPE_CHECKING:
    from app.services.usage_service import UsageService

logger = logging.getLogger(__name__)

WINDOW_LABEL = "5h rate"

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
        subscription_id: stable id, default ``"claude-code:anthropic"``.
        subscription_label: human-readable label voor de UI.
    """

    DEFAULT_ID = "claude-code:anthropic"
    DEFAULT_LABEL = "Claude Code (Anthropic)"

    def __init__(
        self,
        usage_service: UsageService,
        subscription_id: str = DEFAULT_ID,
        subscription_label: str = DEFAULT_LABEL,
        state_path: Path | None = None,
        now: datetime | None = None,
    ):
        self._usage_service = usage_service
        self.id = subscription_id
        self.label = subscription_label
        self._state_path = state_path
        self._now = now

    async def get_usage(self) -> SubscriptionUsage:
        # Rung 1: Anthropic's own numbers, captured from the statusline.
        # This is the only rung with a real denominator — the fallback
        # below has no published limit to divide by, which is the whole
        # reason this row used to report a bare token count.
        official = read_windows(self._state_path, now=self._now)
        if official:
            return SubscriptionUsage.from_windows(
                subscription_id=self.id,
                subscription_label=self.label,
                bron="statusline:rate_limits",
                betrouwbaarheid="exact",
                windows=official,
            )

        # Rung 2/3: the pre-existing local estimate, unchanged. Reached
        # whenever no fresh capture exists — no statusline wrapper
        # installed, no session rendered since the last window rolled
        # over, or an account that publishes no limits.
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
        return SubscriptionUsage(
            subscription_id=self.id,
            subscription_label=self.label,
            # No published limit means no honest "full" signal. The pool's
            # real backstop is the per-provider rate-limit pause.
            beschikbaar=True,
            drempel_gebruikt=None,
            bron="usage_service:active_block",
            betrouwbaarheid="schatting",
            verbruikt=total_tokens,
            limiet=None,
            eenheid="tokens",
            venster_label=WINDOW_LABEL,
            reset_op=_parse_iso(getattr(active_block, "end_time", None)),
        )