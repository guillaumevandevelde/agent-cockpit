"""Tests for the Claude Code statusline rate-limit capture.

Covers ``statusline_state.read_windows`` and the precedence ladder it
creates in ``AnthropicUsageProvider``.

The load-bearing property is the ladder's **direction**: an official
capture must win over the local token estimate, and the absence of a
capture must fall back cleanly rather than erroring or reporting zero.
The Anthropic row spent months reporting a bare token count precisely
because no denominator existed; these tests pin the conditions under
which a real one now does.

The second property is **loud failure**. If Claude Code renames a field,
parsing yields nothing and the row silently reverts to the old estimate
while still looking healthy. That is the one degradation nobody would
notice, so it must log.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.subscriptions.anthropic import AnthropicUsageProvider
from app.services.subscriptions.statusline_state import read_windows

NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)
FUTURE = int((NOW + timedelta(hours=3)).timestamp())
FUTURE_WEEK = int((NOW + timedelta(days=4)).timestamp())
PAST = int((NOW - timedelta(hours=1)).timestamp())


def _capture(tmp_path, rate_limits, *, available=True, name="rate-limits.json"):
    path = tmp_path / name
    path.write_text(json.dumps({
        "captured_at": NOW.isoformat(),
        "subscription_type": "pro",
        "rate_limits_available": available,
        "rate_limits": rate_limits,
    }), encoding="utf-8")
    return path


PRO_LIMITS = {
    "five_hour": {"utilization": 37.5, "resets_at": FUTURE},
    "seven_day": {"utilization": 81.2, "resets_at": FUTURE_WEEK},
    # Pro accounts get null here; only max/team carry a Sonnet window.
    "seven_day_sonnet": None,
}


class TestReadWindows:
    def test_missing_file_is_empty_not_an_error(self, tmp_path):
        assert read_windows(tmp_path / "nope.json", now=NOW) == []

    def test_pro_capture_yields_five_hour_and_weekly(self, tmp_path):
        path = _capture(tmp_path, PRO_LIMITS)
        windows = read_windows(path, now=NOW)
        assert [w.label for w in windows] == ["5h", "weekly"]

    def test_utilization_is_scaled_to_a_fraction(self, tmp_path):
        path = _capture(tmp_path, PRO_LIMITS)
        five_h, weekly = read_windows(path, now=NOW)
        assert five_h.used_fraction == pytest.approx(0.375)
        assert weekly.used_fraction == pytest.approx(0.812)
        assert five_h.verbruikt == pytest.approx(37.5)
        assert five_h.limiet == pytest.approx(100.0)
        assert five_h.eenheid == "%"

    def test_used_percentage_is_the_spelling_the_real_payload_uses(
        self, tmp_path,
    ):
        # Verbatim from the first live capture (CC 2.1.232, 2026-08-14),
        # including the float noise Claude Code actually emitted. The CC
        # binary carries both spellings and the other one — `utilization`
        # — is what its own formatter reads, so committing to that would
        # have produced a permanent, silent "no quota data".
        path = _capture(tmp_path, {
            "five_hour": {"used_percentage": 40, "resets_at": FUTURE},
            "seven_day": {
                "used_percentage": 28.999999999999996, "resets_at": FUTURE_WEEK,
            },
        })
        five_h, weekly = read_windows(path, now=NOW)
        assert five_h.used_fraction == pytest.approx(0.40)
        assert weekly.used_fraction == pytest.approx(0.29)

    def test_utilization_spelling_still_works_as_fallback(self, tmp_path):
        path = _capture(tmp_path, {
            "five_hour": {"utilization": 42.0, "resets_at": FUTURE},
        })
        windows = read_windows(path, now=NOW)
        assert windows[0].used_fraction == pytest.approx(0.42)

    def test_null_top_level_metadata_does_not_block_the_read(self, tmp_path):
        # The live capture had subscription_type and
        # rate_limits_available both null while rate_limits was fully
        # populated. Gating on either would have discarded real data.
        path = tmp_path / "rate-limits.json"
        path.write_text(json.dumps({
            "captured_at": NOW.isoformat(),
            "subscription_type": None,
            "rate_limits_available": None,
            "rate_limits": {
                "five_hour": {"used_percentage": 40, "resets_at": FUTURE},
            },
        }), encoding="utf-8")
        assert len(read_windows(path, now=NOW)) == 1

    def test_null_sonnet_window_is_skipped_not_an_error(self, tmp_path):
        # Pro sends null here. Treating it as malformed would make every
        # Pro capture look broken.
        path = _capture(tmp_path, PRO_LIMITS)
        assert "weekly (Sonnet)" not in [w.label for w in read_windows(path, now=NOW)]

    def test_sonnet_window_is_read_when_present(self, tmp_path):
        path = _capture(tmp_path, {
            **PRO_LIMITS,
            "seven_day_sonnet": {"utilization": 12.0, "resets_at": FUTURE_WEEK},
        })
        assert [w.label for w in read_windows(path, now=NOW)] == [
            "5h", "weekly", "weekly (Sonnet)",
        ]

    def test_expired_window_is_dropped(self, tmp_path):
        path = _capture(tmp_path, {
            "five_hour": {"utilization": 99.0, "resets_at": PAST},
            "seven_day": {"utilization": 20.0, "resets_at": FUTURE_WEEK},
        })
        assert [w.label for w in read_windows(path, now=NOW)] == ["weekly"]

    def test_resets_at_is_epoch_seconds(self, tmp_path):
        path = _capture(tmp_path, PRO_LIMITS)
        five_h, _ = read_windows(path, now=NOW)
        assert five_h.resets_at == datetime.fromtimestamp(FUTURE, tz=UTC)

    def test_account_without_published_limits_is_quietly_empty(
        self, tmp_path, caplog,
    ):
        # rate_limits: null is a legitimate state (API-key auth, some
        # plans). It must not shout in the log on every request.
        path = _capture(tmp_path, None, available=False)
        with caplog.at_level(logging.WARNING):
            assert read_windows(path, now=NOW) == []
        assert caplog.records == []

    def test_unparseable_field_names_log_loudly(self, tmp_path, caplog):
        # The silent-degradation guard: a renamed field must not just
        # quietly drop the row back to the local estimate.
        path = _capture(tmp_path, {
            "five_hour": {"pct_consumed": 50.0, "resets_at": FUTURE},
        })
        with caplog.at_level(logging.WARNING):
            assert read_windows(path, now=NOW) == []
        assert any("yielded no usable window" in r.message for r in caplog.records)
        assert any("five_hour" in str(r.args) for r in caplog.records)

    def test_corrupt_json_logs_and_returns_empty(self, tmp_path, caplog):
        path = tmp_path / "rate-limits.json"
        path.write_text("{ half written", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            assert read_windows(path, now=NOW) == []
        assert any("not valid JSON" in r.message for r in caplog.records)

    def test_boolean_and_negative_percentages_are_rejected(self, tmp_path):
        path = _capture(tmp_path, {
            "five_hour": {"utilization": True, "resets_at": FUTURE},
            "seven_day": {"utilization": -5, "resets_at": FUTURE_WEEK},
        })
        assert read_windows(path, now=NOW) == []


class TestAnthropicLadder:
    """Precedence between the official capture and the local estimate."""

    @staticmethod
    def _service(total_tokens=20_000):
        return SimpleNamespace(
            get_block_usage=AsyncMock(return_value=SimpleNamespace(
                active_block=SimpleNamespace(
                    input_tokens=total_tokens // 2,
                    output_tokens=total_tokens // 2,
                    cache_creation_tokens=0,
                    cache_read_tokens=0,
                    end_time=None,
                ),
            )),
        )

    async def test_official_capture_wins_over_local_estimate(self, tmp_path):
        path = _capture(tmp_path, PRO_LIMITS)
        provider = AnthropicUsageProvider(
            usage_service=self._service(), state_path=path, now=NOW,
        )
        usage = await provider.get_usage()
        assert usage.betrouwbaarheid == "exact"
        assert usage.bron == "statusline:rate_limits"
        # The worst window drives routing: 81.2% weekly beats 37.5% 5h.
        assert usage.drempel_gebruikt == pytest.approx(0.812)
        assert usage.venster_label == "weekly"

    async def test_official_rung_does_not_consult_the_usage_service(
        self, tmp_path,
    ):
        # Parsing ~/.claude/projects is the expensive part of this row;
        # a fresh capture should make it unnecessary.
        service = self._service()
        provider = AnthropicUsageProvider(
            usage_service=service, state_path=_capture(tmp_path, PRO_LIMITS),
            now=NOW,
        )
        await provider.get_usage()
        service.get_block_usage.assert_not_awaited()

    async def test_no_capture_falls_back_to_the_local_estimate(self, tmp_path):
        provider = AnthropicUsageProvider(
            usage_service=self._service(), state_path=tmp_path / "absent.json",
            now=NOW,
        )
        usage = await provider.get_usage()
        assert usage.betrouwbaarheid == "schatting"
        assert usage.bron == "usage_service:active_block"
        assert usage.verbruikt == 20_000
        # No published limit on this rung, so still no fabricated ratio.
        assert usage.drempel_gebruikt is None
        assert usage.limiet is None

    async def test_fully_expired_capture_falls_back(self, tmp_path):
        # Stale official data is worse than a fresh estimate: it looks
        # authoritative while describing a window that already rolled.
        path = _capture(tmp_path, {
            "five_hour": {"utilization": 99.0, "resets_at": PAST},
        })
        provider = AnthropicUsageProvider(
            usage_service=self._service(), state_path=path, now=NOW,
        )
        usage = await provider.get_usage()
        assert usage.betrouwbaarheid == "schatting"
        assert usage.verbruikt == 20_000

    async def test_fallback_still_reports_onbekend_when_service_fails(
        self, tmp_path,
    ):
        service = SimpleNamespace(
            get_block_usage=AsyncMock(side_effect=RuntimeError("boom")),
        )
        provider = AnthropicUsageProvider(
            usage_service=service, state_path=tmp_path / "absent.json", now=NOW,
        )
        usage = await provider.get_usage()
        assert usage.betrouwbaarheid == "onbekend"
        assert usage.bron == "usage_service:fout"
