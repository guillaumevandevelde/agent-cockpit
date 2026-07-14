"""MiniMax usage provider — remote API probe.

Per ``docs/cockpit/subscriptions.md``: when the probe finds a usable
endpoint, ship the structured payload as ``exact``. Otherwise ship an
honest empty state — **no fabrication**. Today we don't know the
canonical MiniMax usage endpoint, so a fresh deployment without an
explicit ``probe_url`` returns ``onbekend``; once we discover the
endpoint (or the user configures one) the provider becomes ``exact``.

Output values stay in a 0-1 ``remaining_ratio``; we convert to
``drempel_gebruikt = 1 - remaining_ratio`` so all providers share the
same "fraction consumed" semantics in the snapshot — but we never
combine values across providers downstream (analyse §6.2).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.services.subscriptions.base import (
    SubscriptionUsage,
    SubscriptionUsageProvider,
)

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 5.0


class MinimaxUsageProvider(SubscriptionUsageProvider):
    """Remote-API provider voor MiniMax via Claude Code.

    Args:
        api_key: MiniMax API key. Wanneer None kan de probe niet
            authenticeren — return ``onbekend`` onmiddellijk.
        probe_url: optionele URL van een endpoint dat een
            ``{"remaining_ratio": float}`` payload teruggeeft. Wanneer
            None heeft de provider geen manier om MiniMax te bevragen
            en geeft een eerlijke ``onbekend`` terug.
        subscription_id: stable id, default ``"claude-code:minimax"``.
        subscription_label: human-readable label voor de UI.
    """

    DEFAULT_ID = "claude-code:minimax"
    DEFAULT_LABEL = "Claude Code (MiniMax)"

    def __init__(
        self,
        api_key: str | None,
        probe_url: str | None,
        subscription_id: str = DEFAULT_ID,
        subscription_label: str = DEFAULT_LABEL,
    ):
        self._api_key = api_key
        self._probe_url = probe_url
        self.id = subscription_id
        self.label = subscription_label

    async def get_usage(self) -> SubscriptionUsage:
        if not self._api_key:
            return SubscriptionUsage(
                subscription_id=self.id,
                subscription_label=self.label,
                beschikbaar=True,
                drempel_gebruikt=None,
                bron="minimax_api:no_credentials",
                betrouwbaarheid="onbekend",
            )
        if not self._probe_url:
            # subscriptions.md: "geen fabricage". We kennen vandaag geen
            # canonische MiniMax usage-endpoint — eerlijk leeg laten
            # tot iemand er een ontdekt / configureert.
            return SubscriptionUsage(
                subscription_id=self.id,
                subscription_label=self.label,
                beschikbaar=True,
                drempel_gebruikt=None,
                bron="minimax_api:no_probe_url",
                betrouwbaarheid="onbekend",
            )

        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    self._probe_url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                response.raise_for_status()
        except httpx.HTTPError:
            # Transport / status-fout. Onbruikbaar signaal — onbekend.
            logger.info(
                "MinimaxUsageProvider: probe failed (probe_url=%s)", self._probe_url,
            )
            return SubscriptionUsage(
                subscription_id=self.id,
                subscription_label=self.label,
                beschikbaar=True,
                drempel_gebruikt=None,
                bron="minimax_api:probe_failed",
                betrouwbaarheid="onbekend",
            )

        try:
            payload: Any = response.json()
        except ValueError:
            # 200 met onleesbare body — endpoint bestaat maar levert geen
            # usage-payload die wij kunnen interpreteren.
            return SubscriptionUsage(
                subscription_id=self.id,
                subscription_label=self.label,
                beschikbaar=True,
                drempel_gebruikt=None,
                bron="minimax_api:probe_unparseable",
                betrouwbaarheid="onbekend",
            )

        if not isinstance(payload, dict):
            return SubscriptionUsage(
                subscription_id=self.id,
                subscription_label=self.label,
                beschikbaar=True,
                drempel_gebruikt=None,
                bron="minimax_api:probe_unparseable",
                betrouwbaarheid="onbekend",
            )

        remaining = payload.get("remaining_ratio")
        if not isinstance(remaining, (int, float)) or remaining < 0:
            return SubscriptionUsage(
                subscription_id=self.id,
                subscription_label=self.label,
                beschikbaar=True,
                drempel_gebruikt=None,
                bron="minimax_api:probe_unparseable",
                betrouwbaarheid="onbekend",
            )

        # Clamp to 0..1 so a stale "remaining=1.2" snapshot can't flip
        # beschikbaar to True while the UI is rendering "1.0 used".
        clamped = max(0.0, min(1.0, float(remaining)))
        drempel_gebruikt = 1.0 - clamped
        return SubscriptionUsage(
            subscription_id=self.id,
            subscription_label=self.label,
            beschikbaar=drempel_gebruikt < 1.0,
            drempel_gebruikt=drempel_gebruikt,
            bron="minimax_api:probe",
            betrouwbaarheid="exact",
        )