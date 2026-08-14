"""Tests for ``CodexUsageProvider`` — rate limits from codex's rollout.

Codex writes its own quota snapshots to
``~/.codex/sessions/**/rollout-*.jsonl``, so this provider reads a file
the CLI already maintains. The fixtures below reproduce the exact event
shape captured from a live ChatGPT Go account on 2026-08-14.

The assertion that matters most is the labelling one. On that account
``primary`` was a **30-day** window with ``secondary: null`` — not the
5h + weekly pair the Codex docs describe for Plus/Pro. Any code that
reads "primary" as "the session window" mislabels a monthly figure, so
the label has to come from ``window_minutes``.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.services.subscriptions.codex import CodexUsageProvider

NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)
FUTURE = int((NOW + timedelta(days=30)).timestamp())
PAST = int((NOW - timedelta(days=1)).timestamp())

#: Verbatim from the live Go account (2026-08-14): one monthly window,
#: no secondary, no credits.
GO_RATE_LIMITS = {
    "limit_id": "codex",
    "limit_name": None,
    "primary": {
        "used_percent": 0.0,
        "window_minutes": 43200,
        "resets_at": FUTURE,
    },
    "secondary": None,
    "credits": {"has_credits": False, "unlimited": False, "balance": None},
    "individual_limit": None,
    "spend_control_reached": None,
    "plan_type": "go",
    "rate_limit_reached_type": None,
}


def _write_rollout(tmp_path, rate_limits, *, name="rollout-a.jsonl", extra_lines=()):
    day = tmp_path / "sessions" / "2026" / "08" / "14"
    day.mkdir(parents=True, exist_ok=True)
    path = day / name
    lines = [
        json.dumps({"type": "session_meta", "payload": {"id": "s1"}}),
        *extra_lines,
    ]
    if rate_limits is not None:
        lines.append(json.dumps({
            "timestamp": "2026-08-14T12:16:33.294Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {"total_tokens": 13502}},
                "rate_limits": rate_limits,
            },
        }))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _provider(tmp_path):
    return CodexUsageProvider(data_dir=tmp_path, now=NOW)


class TestCodexUsageProvider:
    async def test_no_sessions_dir_is_onbekend(self, tmp_path):
        usage = await _provider(tmp_path).get_usage()
        assert usage.betrouwbaarheid == "onbekend"
        assert usage.bron == "codex_rollout:no_sessions"

    async def test_rollout_without_token_count_is_onbekend(self, tmp_path):
        _write_rollout(tmp_path, None)
        usage = await _provider(tmp_path).get_usage()
        assert usage.betrouwbaarheid == "onbekend"
        assert usage.bron == "codex_rollout:no_snapshot"
        assert usage.drempel_gebruikt is None

    async def test_go_plan_reports_one_monthly_window(self, tmp_path):
        # The measured Go shape: a single 30-day window. Asserting two
        # windows here would encode the Plus/Pro docs over the account
        # we actually hold.
        _write_rollout(tmp_path, GO_RATE_LIMITS)
        usage = await _provider(tmp_path).get_usage()
        assert usage.betrouwbaarheid == "exact"
        assert usage.bron == "codex_rollout:token_count"
        assert [w.label for w in usage.windows] == ["monthly"]

    async def test_label_comes_from_window_minutes_not_position(self, tmp_path):
        # Same payload shape, a 5h primary. If the label were derived
        # from "primary" this would still say monthly.
        limits = {
            **GO_RATE_LIMITS,
            "primary": {
                "used_percent": 12.0, "window_minutes": 300,
                "resets_at": FUTURE,
            },
        }
        _write_rollout(tmp_path, limits)
        usage = await _provider(tmp_path).get_usage()
        assert [w.label for w in usage.windows] == ["5h"]

    async def test_secondary_window_is_read_when_present(self, tmp_path):
        limits = {
            **GO_RATE_LIMITS,
            "primary": {
                "used_percent": 10.0, "window_minutes": 300,
                "resets_at": FUTURE,
            },
            "secondary": {
                "used_percent": 80.0, "window_minutes": 10080,
                "resets_at": FUTURE,
            },
        }
        _write_rollout(tmp_path, limits)
        usage = await _provider(tmp_path).get_usage()
        assert [w.label for w in usage.windows] == ["5h", "weekly"]
        # The worst window drives routing: 80% weekly, not 10% session.
        assert usage.drempel_gebruikt == pytest.approx(0.8)
        assert usage.venster_label == "weekly"

    async def test_used_percent_is_scaled_to_a_fraction(self, tmp_path):
        limits = {
            **GO_RATE_LIMITS,
            "primary": {
                "used_percent": 44.0, "window_minutes": 43200,
                "resets_at": FUTURE,
            },
        }
        _write_rollout(tmp_path, limits)
        usage = await _provider(tmp_path).get_usage()
        window = usage.windows[0]
        assert window.used_fraction == pytest.approx(0.44)
        assert window.verbruikt == pytest.approx(44.0)
        assert window.limiet == pytest.approx(100.0)
        assert window.eenheid == "%"

    async def test_resets_at_is_read_as_seconds_not_milliseconds(self, tmp_path):
        # codex uses epoch SECONDS here while opencode and MiniMax use
        # milliseconds; a shared helper would put this reset in the year
        # 58,000 and make every snapshot look permanently fresh.
        _write_rollout(tmp_path, GO_RATE_LIMITS)
        usage = await _provider(tmp_path).get_usage()
        assert usage.windows[0].resets_at == datetime.fromtimestamp(FUTURE, tz=UTC)

    async def test_expired_window_is_stale_not_reported_as_current(self, tmp_path):
        # Past its reset the percentage describes a finished period.
        limits = {
            **GO_RATE_LIMITS,
            "primary": {
                "used_percent": 95.0, "window_minutes": 43200,
                "resets_at": PAST,
            },
        }
        _write_rollout(tmp_path, limits)
        usage = await _provider(tmp_path).get_usage()
        assert usage.betrouwbaarheid == "onbekend"
        assert usage.bron == "codex_rollout:stale"
        assert usage.drempel_gebruikt is None

    async def test_stale_window_is_dropped_but_fresh_sibling_survives(self, tmp_path):
        limits = {
            **GO_RATE_LIMITS,
            "primary": {
                "used_percent": 95.0, "window_minutes": 300, "resets_at": PAST,
            },
            "secondary": {
                "used_percent": 30.0, "window_minutes": 10080,
                "resets_at": FUTURE,
            },
        }
        _write_rollout(tmp_path, limits)
        usage = await _provider(tmp_path).get_usage()
        assert [w.label for w in usage.windows] == ["weekly"]
        assert usage.drempel_gebruikt == pytest.approx(0.3)

    async def test_last_snapshot_in_a_file_wins(self, tmp_path):
        # Rollouts are append-only; an earlier line is an older reading.
        early = json.dumps({
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    **GO_RATE_LIMITS,
                    "primary": {
                        "used_percent": 1.0, "window_minutes": 43200,
                        "resets_at": FUTURE,
                    },
                },
            },
        })
        limits = {
            **GO_RATE_LIMITS,
            "primary": {
                "used_percent": 77.0, "window_minutes": 43200,
                "resets_at": FUTURE,
            },
        }
        _write_rollout(tmp_path, limits, extra_lines=(early,))
        usage = await _provider(tmp_path).get_usage()
        assert usage.drempel_gebruikt == pytest.approx(0.77)

    async def test_newest_rollout_file_wins(self, tmp_path):
        import os
        old = _write_rollout(tmp_path, {
            **GO_RATE_LIMITS,
            "primary": {
                "used_percent": 5.0, "window_minutes": 43200,
                "resets_at": FUTURE,
            },
        }, name="rollout-old.jsonl")
        new = _write_rollout(tmp_path, {
            **GO_RATE_LIMITS,
            "primary": {
                "used_percent": 60.0, "window_minutes": 43200,
                "resets_at": FUTURE,
            },
        }, name="rollout-new.jsonl")
        os.utime(old, (1, 1))
        os.utime(new, (10_000, 10_000))
        usage = await _provider(tmp_path).get_usage()
        assert usage.drempel_gebruikt == pytest.approx(0.6)

    async def test_malformed_lines_do_not_break_the_read(self, tmp_path):
        _write_rollout(
            tmp_path, GO_RATE_LIMITS,
            extra_lines=("not json", '{"rate_limits": broken}', "{}"),
        )
        usage = await _provider(tmp_path).get_usage()
        assert usage.betrouwbaarheid == "exact"

    async def test_unknown_window_minutes_gets_a_literal_label(self, tmp_path):
        # Better an ugly "720m" than silently calling it "weekly".
        limits = {
            **GO_RATE_LIMITS,
            "primary": {
                "used_percent": 5.0, "window_minutes": 720,
                "resets_at": FUTURE,
            },
        }
        _write_rollout(tmp_path, limits)
        usage = await _provider(tmp_path).get_usage()
        assert usage.windows[0].label == "720m"
