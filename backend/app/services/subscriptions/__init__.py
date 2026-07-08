"""Subscription usage provider registry."""
from __future__ import annotations

from app.services.subscriptions.base import (
    ErrorCode,
    PeriodUsage,
    SubscriptionUsageProvider,
    SubscriptionUsageSnapshot,
    get_snapshot_cache,
    invalidate_snapshot_cache,
    put_snapshot_cache,
)

__all__ = [
    "ErrorCode",
    "PeriodUsage",
    "SubscriptionUsageProvider",
    "SubscriptionUsageSnapshot",
    "get_snapshot_cache",
    "invalidate_snapshot_cache",
    "put_snapshot_cache",
    "register_usage_provider",
    "get_usage_provider",
]


# Map of provider_id -> SubscriptionUsageProvider instance.
# Populated lazily by tasks 4 and 5 so each task's test suite can wire up
# only the providers that task exercises.
_PROVIDERS: dict[str, SubscriptionUsageProvider] = {}


def register_usage_provider(provider: SubscriptionUsageProvider) -> None:
    """Called by task 4 / task 5 from their module-level imports."""
    _PROVIDERS[provider.provider_id] = provider


def get_usage_provider(provider_id: str) -> SubscriptionUsageProvider:
    try:
        return _PROVIDERS[provider_id]
    except KeyError as exc:
        raise ValueError(f"Unknown subscription usage provider: {provider_id}") from exc


# Import order matters: placeholders register first, then concrete providers
# overwrite. Last-registration-wins semantics of `register_usage_provider`.
from app.services.subscriptions import placeholders  # noqa: F401, E402
from app.services.subscriptions import minimax  # noqa: F401, E402
