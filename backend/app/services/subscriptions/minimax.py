"""MiniMax usage provider.

Calls the MiniMax usage/balance endpoint(s) discovered by the Task 1 probe.
Maps the response into the abstract SubscriptionUsageSnapshot. Returns
an error-annotated snapshot (never raises) for any failure mode.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import settings
from app.services.subscriptions import register_usage_provider
from app.services.subscriptions.base import (
    ErrorCode,
    PeriodUsage,
    SubscriptionUsageProvider,
    SubscriptionUsageSnapshot,
)

logger = logging.getLogger(__name__)

# These were the endpoints the Task 1 probe tried. The probe output is the
# ground truth; adjust this list if the probe revealed a different working URL.
CANDIDATE_PATHS = ("/v1/usage", "/v1/account/usage", "/v1/account/balance")
BASE_URL = "https://api.minimax.io/anthropic"
REQUEST_TIMEOUT_SECONDS = 5.0


class MinimaxUsageProvider(SubscriptionUsageProvider):
    provider_id = "minimax"

    async def get_snapshot(self) -> SubscriptionUsageSnapshot:
        if not settings.minimax_api_key:
            return SubscriptionUsageSnapshot(
                provider=self.provider_id,
                plan_label=None,
                periods=(),
                fetched_at=datetime.now(UTC),
                error="MiniMax API key is not configured.",
                error_code="not_configured",
            )

        base = (settings.minimax_base_url or BASE_URL).rstrip("/")
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            for path in CANDIDATE_PATHS:
                url = base + path
                try:
                    resp = await client.get(
                        url,
                        headers={
                            "Authorization": f"Bearer {settings.minimax_api_key}",
                            "Accept": "application/json",
                        },
                    )
                except (httpx.RequestError, asyncio.TimeoutError) as exc:
                    logger.warning("minimax: %s unreachable: %s", url, exc)
                    return self._snap(error_code="unreachable", error=f"MiniMax unreachable: {exc}")

                if resp.status_code == 401:
                    return self._snap(error_code="unauthorized", error="MiniMax rejected the API key.")
                if 500 <= resp.status_code < 600:
                    return self._snap(error_code="unreachable", error=f"MiniMax returned {resp.status_code}.")
                if resp.status_code == 404:
                    continue  # try next candidate
                if resp.status_code != 200:
                    return self._snap(error_code="unreachable", error=f"MiniMax returned {resp.status_code}.")

                try:
                    body: Any = resp.json()
                except (ValueError, httpx.HTTPError) as exc:
                    logger.warning("minimax: malformed JSON from %s: %s", url, exc)
                    return self._snap(error_code="malformed", error="MiniMax returned non-JSON.")

                periods = self._map_periods(body)
                return SubscriptionUsageSnapshot(
                    provider=self.provider_id,
                    plan_label=None,
                    periods=tuple(periods),
                    fetched_at=datetime.now(UTC),
                )

        # All candidates 404'd.
        return self._snap(
            error_code="no_endpoint",
            error="MiniMax did not expose a usage endpoint at the candidates tried.",
        )

    @staticmethod
    def _map_periods(body: Any) -> list[PeriodUsage]:
        """Translate whatever shape the probe found into a list of PeriodUsage.

        No response shape captured by Task 1 probe; this mapping is a best-guess
        based on common REST conventions and should be updated when real
        response shape is known. Example expected shape:

            body = [{"label":"5h","used":1000,"limit":5000,"unit":"tokens","reset_at":"..."}]

        If the probe revealed a different shape (single object, nested),
        adjust here. Default fallback: no periods, so the card renders
        a "no data" state instead of guessing.
        """
        if isinstance(body, list):
            out: list[PeriodUsage] = []
            for row in body:
                if not isinstance(row, dict):
                    continue
                out.append(
                    PeriodUsage(
                        label=str(row.get("label", "?")),
                        used=float(row.get("used", 0)),
                        limit=float(row["limit"]) if row.get("limit") is not None else None,
                        unit=str(row.get("unit", "tokens")),
                        reset_at=_parse_iso(row.get("reset_at")),
                        source="api",
                    )
                )
            return out
        if isinstance(body, dict):
            # If the probe revealed a different shape (single object, nested),
            # adjust here. Default fallback: no periods, so the card renders
            # a "no data" state instead of guessing.
            return []
        return []

    @staticmethod
    def _snap(error_code: ErrorCode, error: str) -> SubscriptionUsageSnapshot:
        return SubscriptionUsageSnapshot(
            provider="minimax",
            plan_label=None,
            periods=(),
            fetched_at=datetime.now(UTC),
            error=error,
            error_code=error_code,
        )


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        # Accept "Z" suffix.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


register_usage_provider(MinimaxUsageProvider())
