"""Contract tests for SubscriptionUsageSnapshot / PeriodUsage / base API."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.subscriptions.base import (
    ErrorCode,
    PeriodUsage,
    SubscriptionUsageProvider,
    SubscriptionUsageSnapshot,
)


def test_period_usage_is_frozen():
    p = PeriodUsage(label="5h rate", used=1000, limit=44_000, unit="tokens",
                    reset_at=None, source="local")
    with pytest.raises(Exception):
        p.used = 2000  # type: ignore[misc]


def test_snapshot_default_error_is_none():
    now = datetime.now(UTC)
    s = SubscriptionUsageSnapshot(provider="anthropic", plan_label="pro", periods=(), fetched_at=now)
    assert s.error is None
    assert s.error_code is None


def test_error_code_values_are_exactly_the_six():
    assert set(ErrorCode.__args__) == {  # type: ignore[attr-defined]
        "not_configured", "unauthorized", "unreachable",
        "malformed", "no_endpoint", "plan_unknown",
    }


def test_subscription_usage_provider_is_abstract():
    with pytest.raises(TypeError):
        SubscriptionUsageProvider()  # type: ignore[abstract]
