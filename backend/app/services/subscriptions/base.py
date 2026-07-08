"""Subscription usage provider base class, dataclasses, and shared cache.

No I/O lives in this module. Concrete providers live in `minimax.py` and
`anthropic.py`. The shared in-process cache is also here because every
provider wants the same TTL semantics.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

Source = Literal["api", "local", "manual"]
ErrorCode = Literal[
    "not_configured",
    "unauthorized",
    "unreachable",
    "malformed",
    "no_endpoint",
    "plan_unknown",
]

CACHE_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class PeriodUsage:
    """A single rate-limited window for a subscription."""

    label: str
    used: float
    limit: float | None
    unit: str
    reset_at: datetime | None
    source: Source
    note: str | None = None


@dataclass(frozen=True)
class SubscriptionUsageSnapshot:
    """One provider's usage at a point in time. Errors are first-class."""

    provider: str
    plan_label: str | None
    periods: tuple[PeriodUsage, ...]
    fetched_at: datetime
    error: str | None = None
    error_code: ErrorCode | None = None


class SubscriptionUsageProvider:
    """Abstract base class. Subclasses set `provider_id` and implement `get_snapshot`."""

    provider_id: str

    def __init__(self):
        if type(self) is SubscriptionUsageProvider:
            raise TypeError(
                "SubscriptionUsageProvider is abstract and cannot be instantiated directly"
            )

    async def get_snapshot(self) -> SubscriptionUsageSnapshot:  # pragma: no cover - abstract
        raise NotImplementedError


# In-process cache: provider_id -> (cached_at, snapshot)
_snapshot_cache: dict[str, tuple[datetime, SubscriptionUsageSnapshot]] = {}
_cache_lock = asyncio.Lock()


async def get_snapshot_cache(provider_id: str) -> SubscriptionUsageSnapshot | None:
    """Return the cached snapshot for `provider_id` if it is still within TTL, else None."""
    async with _cache_lock:
        entry = _snapshot_cache.get(provider_id)
        if entry is None:
            return None
        cached_at, snapshot = entry
        if datetime.now(UTC) - cached_at > CACHE_TTL:
            _snapshot_cache.pop(provider_id, None)
            return None
        return snapshot


async def put_snapshot_cache(snapshot: SubscriptionUsageSnapshot) -> None:
    """Store a fresh snapshot in the cache."""
    async with _cache_lock:
        _snapshot_cache[snapshot.provider] = (datetime.now(UTC), snapshot)


def invalidate_snapshot_cache(provider_id: str) -> None:
    """Synchronous invalidation. Safe to call from sync contexts (FastAPI handlers)."""
    _snapshot_cache.pop(provider_id, None)
