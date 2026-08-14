"""MiniMax usage provider — remote API probe.

MiniMax publishes a real quota endpoint for the Token/Coding Plan, so
this provider is ``exact`` rather than an estimate::

    GET https://api.minimax.io/v1/token_plan/remains
    Authorization: Bearer <api key>

Measured against a live Coding Plan key on 2026-08-14. Both the
``api.minimax.io`` and ``www.minimax.io`` hosts answer 200 with an
identical body; we use the ``api.`` host so the quota probe and the
dispatch traffic (``provider_env.MINIMAX_BASE_URL_INTERNATIONAL``) stay
on one hostname.

Response shape (trimmed to what we read)::

    {"model_remains": [
       {"model_name": "general",
        "start_time": 1786701600000, "end_time": 1786719600000,
        "current_interval_remaining_percent": 100,
        "current_interval_status": 1,
        "weekly_end_time": 1786924800000,
        "current_weekly_remaining_percent": 56,
        "current_weekly_status": 1}],
     "base_resp": {"status_code": 0, "status_msg": "success"}}

Three things this shape gets wrong if you read it casually:

1. **It reports what is LEFT, not what is used.** ``remaining_percent``
   of 56 means 44% consumed. We invert at this edge so every
   ``UsageWindow`` leaving a provider means "fraction used" (base.py).
2. **``model_name`` splits unrelated plans.** ``general`` is the
   text/coding quota; ``video`` is a separate product with its own
   windows and was ``status: 3`` on this account. Summing them, or
   taking ``model_remains[0]`` blindly, mixes two subscriptions.
3. **The ``*_count`` fields were all zero** on a live account with 44%
   of the week consumed, so they are not a usable channel. Only the
   percentages carry the signal.

Window durations were verified arithmetically rather than assumed:
``end_time - start_time`` = 18,000,000 ms (exactly 5h) and
``weekly_end_time - weekly_start_time`` = 604,800,000 ms (exactly 7d).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.services.subscriptions.base import (
    SubscriptionUsage,
    SubscriptionUsageProvider,
    UsageWindow,
)

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 5.0

#: International host — mirrors ``provider_env.MINIMAX_BASE_URL_INTERNATIONAL``.
DEFAULT_PROBE_URL = "https://api.minimax.io/v1/token_plan/remains"
#: China host, for accounts on ``minimaxi.com``.
PROBE_URL_CHINA = "https://api.minimaxi.com/v1/token_plan/remains"

#: The text/coding quota. Other entries (e.g. ``video``) are separate
#: products billed against their own windows.
MODEL_NAME_TEXT = "general"

#: Observed value for an active window. Anything else is treated as "no
#: usable signal" rather than guessed at — the ``video`` entry on this
#: account carried ``3`` while unsubscribed, and the rest of the enum is
#: undocumented. Failing to ``onbekend`` here is the safe direction: it
#: falls back to no signal instead of inventing a percentage.
STATUS_ACTIVE = 1


def _epoch_ms_to_dt(value: Any) -> datetime | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _window_from_remaining(
    *,
    label: str,
    remaining_percent: Any,
    status: Any,
    resets_at_ms: Any,
) -> UsageWindow | None:
    """Build one window, or None when this window has no usable signal."""
    if status != STATUS_ACTIVE:
        return None
    if isinstance(remaining_percent, bool) or not isinstance(
        remaining_percent, (int, float)
    ):
        return None
    if not 0 <= remaining_percent <= 100:
        return None
    used_percent = 100.0 - float(remaining_percent)
    return UsageWindow(
        label=label,
        used_fraction=used_percent / 100.0,
        resets_at=_epoch_ms_to_dt(resets_at_ms),
        verbruikt=used_percent,
        limiet=100.0,
        eenheid="%",
    )


class MinimaxUsageProvider(SubscriptionUsageProvider):
    """Remote-API provider voor MiniMax via Claude Code.

    Args:
        api_key: MiniMax API key. Wanneer None kan de probe niet
            authenticeren — return ``onbekend`` onmiddellijk.
        probe_url: quota-endpoint. Defaults to the international host;
            pass ``PROBE_URL_CHINA`` for a ``minimaxi.com`` account.
            Explicit ``None`` disables the probe and returns ``onbekend``.
        subscription_id: stable id, default ``"claude-code:minimax"``.
        subscription_label: human-readable label voor de UI.
    """

    DEFAULT_ID = "claude-code:minimax"
    DEFAULT_LABEL = "Claude Code (MiniMax)"

    def __init__(
        self,
        api_key: str | None,
        probe_url: str | None = DEFAULT_PROBE_URL,
        subscription_id: str = DEFAULT_ID,
        subscription_label: str = DEFAULT_LABEL,
    ):
        self._api_key = api_key
        self._probe_url = probe_url
        self.id = subscription_id
        self.label = subscription_label

    def _no_signal(self, bron: str) -> SubscriptionUsage:
        return SubscriptionUsage(
            subscription_id=self.id,
            subscription_label=self.label,
            beschikbaar=True,
            drempel_gebruikt=None,
            bron=bron,
            betrouwbaarheid="onbekend",
        )

    async def get_usage(self) -> SubscriptionUsage:
        if not self._api_key:
            return self._no_signal("minimax_api:no_credentials")
        if not self._probe_url:
            return self._no_signal("minimax_api:no_probe_url")

        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    self._probe_url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                response.raise_for_status()
        except httpx.HTTPError:
            logger.info(
                "MinimaxUsageProvider: probe failed (probe_url=%s)", self._probe_url,
            )
            return self._no_signal("minimax_api:probe_failed")

        try:
            payload: Any = response.json()
        except ValueError:
            return self._no_signal("minimax_api:probe_unparseable")

        if not isinstance(payload, dict):
            return self._no_signal("minimax_api:probe_unparseable")

        # MiniMax answers 200 for application-level failures too — the
        # verdict lives in ``base_resp.status_code``, not the HTTP status.
        base_resp = payload.get("base_resp")
        if isinstance(base_resp, dict) and base_resp.get("status_code") not in (0, None):
            logger.info(
                "MinimaxUsageProvider: base_resp status_code=%s msg=%s",
                base_resp.get("status_code"), base_resp.get("status_msg"),
            )
            return self._no_signal("minimax_api:probe_error")

        entries = payload.get("model_remains")
        if not isinstance(entries, list):
            return self._no_signal("minimax_api:probe_unparseable")

        entry = next(
            (
                e
                for e in entries
                if isinstance(e, dict) and e.get("model_name") == MODEL_NAME_TEXT
            ),
            None,
        )
        if entry is None:
            return self._no_signal("minimax_api:no_text_plan")

        windows = [
            w
            for w in (
                _window_from_remaining(
                    label="5h",
                    remaining_percent=entry.get("current_interval_remaining_percent"),
                    status=entry.get("current_interval_status"),
                    resets_at_ms=entry.get("end_time"),
                ),
                _window_from_remaining(
                    label="weekly",
                    remaining_percent=entry.get("current_weekly_remaining_percent"),
                    status=entry.get("current_weekly_status"),
                    resets_at_ms=entry.get("weekly_end_time"),
                ),
            )
            if w is not None
        ]
        if not windows:
            return self._no_signal("minimax_api:probe_unparseable")

        return SubscriptionUsage.from_windows(
            subscription_id=self.id,
            subscription_label=self.label,
            bron="minimax_api:token_plan_remains",
            betrouwbaarheid="exact",
            windows=windows,
        )
