"""Tests for ``OpencodeGoUsageProvider`` — local spend vs published caps.

opencode Go is the one subscription of the four whose denominator is a
**published constant** rather than something a vendor reports back:
$12 per 5h, $30 per week, $60 per month
(https://opencode.ai/docs/go/#usage-limits). The provider's whole job is
to sum what opencode recorded spending and divide.

What these tests pin, in order of how badly each would hurt if wrong:

- **Provider isolation.** ``opencode.db`` holds messages from every
  provider the user has configured. Summing a free-model message into
  the Go total inflates a cap the user is measured against. This account
  has 1,256 non-Go assistant messages against 2,199 Go ones, so the bug
  would be large and silent.
- **Window boundaries.** A message one millisecond outside a window must
  not count toward it, or the 5h figure drifts upward forever.
- **Honest labelling.** ``schatting``, never ``exact`` — the cost is
  opencode's own arithmetic and the windows are rolling because the
  billing anchor is unknowable.
- **Overspend is representable.** Go's "Use balance" option lets spend
  pass a cap instead of blocking, so >100% must survive to the UI rather
  than being clamped into a comfortable lie.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.services.subscriptions.opencode_go import (
    GO_LIMITS,
    OpencodeGoUsageProvider,
)

NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)


def _make_db(tmp_path, messages):
    """Build a minimal opencode.db with the real ``message`` schema.

    Only the columns the provider reads are created; ``data`` holds the
    same JSON shape opencode writes (verified against the live DB on
    2026-08-14).
    """
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE message ("
        "id TEXT, session_id TEXT, time_created INTEGER, "
        "time_updated INTEGER, data TEXT)"
    )
    for i, (age, provider_id, cost) in enumerate(messages):
        ts = int((NOW - age).timestamp() * 1000)
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            (
                f"msg{i}", "s1", ts, ts,
                json.dumps({
                    "role": "assistant",
                    "providerID": provider_id,
                    "modelID": "minimax-m3",
                    "cost": cost,
                    "tokens": {"input": 10, "output": 20,
                               "cache": {"read": 0, "write": 0}},
                    "time": {"created": ts},
                }),
            ),
        )
    con.commit()
    con.close()
    return db


def _provider(tmp_path):
    return OpencodeGoUsageProvider(data_dir=tmp_path, now=NOW)


def _by_label(usage):
    return {w.label: w for w in usage.windows}


class TestOpencodeGoUsageProvider:
    async def test_missing_db_is_onbekend_not_zero(self, tmp_path):
        # A missing DB means "we cannot see", not "you have spent
        # nothing" — reporting 0% would read as a healthy lane.
        usage = await _provider(tmp_path).get_usage()
        assert usage.betrouwbaarheid == "onbekend"
        assert usage.drempel_gebruikt is None
        assert usage.bron == "opencode_db:absent"

    async def test_all_three_published_windows_are_reported(self, tmp_path):
        _make_db(tmp_path, [(timedelta(hours=1), "opencode-go", 1.0)])
        usage = await _provider(tmp_path).get_usage()
        assert [w.label for w in usage.windows] == ["5h", "weekly", "monthly"]
        assert [w.limiet for w in usage.windows] == [12.0, 30.0, 60.0]
        assert {w.eenheid for w in usage.windows} == {"$"}

    async def test_published_caps_match_the_docs(self):
        # If opencode changes its plan these constants must be updated
        # deliberately, not drift silently against a stale comment.
        assert GO_LIMITS == (
            ("5h", 18_000, 12.0),
            ("weekly", 604_800, 30.0),
            ("monthly", 2_592_000, 60.0),
        )

    async def test_spend_divided_by_cap_gives_the_fraction(self, tmp_path):
        # $6 inside the 5h window is exactly half of the $12 cap.
        _make_db(tmp_path, [
            (timedelta(hours=1), "opencode-go", 4.0),
            (timedelta(hours=2), "opencode-go", 2.0),
        ])
        usage = await _provider(tmp_path).get_usage()
        five_h = _by_label(usage)["5h"]
        assert five_h.verbruikt == pytest.approx(6.0)
        assert five_h.used_fraction == pytest.approx(0.5)

    async def test_other_providers_never_count_against_the_go_cap(self, tmp_path):
        # The free-model rows share this table. Counting them would
        # measure the user against a cap they are not spending toward.
        _make_db(tmp_path, [
            (timedelta(hours=1), "opencode-go", 3.0),
            (timedelta(hours=1), "opencode", 99.0),
            (timedelta(hours=1), "anthropic", 99.0),
        ])
        usage = await _provider(tmp_path).get_usage()
        assert _by_label(usage)["5h"].verbruikt == pytest.approx(3.0)

    async def test_messages_outside_a_window_are_excluded(self, tmp_path):
        # 6h ago is outside the 5h window but inside week and month.
        _make_db(tmp_path, [(timedelta(hours=6), "opencode-go", 9.0)])
        usage = await _provider(tmp_path).get_usage()
        w = _by_label(usage)
        assert w["5h"].verbruikt == pytest.approx(0.0)
        assert w["weekly"].verbruikt == pytest.approx(9.0)
        assert w["monthly"].verbruikt == pytest.approx(9.0)

    async def test_window_boundary_is_inclusive_at_the_edge(self, tmp_path):
        # Exactly 5h old must still count; an off-by-one here leaks spend
        # out of the window on every tick.
        _make_db(tmp_path, [(timedelta(hours=5), "opencode-go", 1.0)])
        usage = await _provider(tmp_path).get_usage()
        assert _by_label(usage)["5h"].verbruikt == pytest.approx(1.0)

    async def test_drempel_gebruikt_is_the_worst_window(self, tmp_path):
        # $9 is 75% of the 5h cap but only 15% of the monthly one. The
        # router must see 0.75 — the window that actually constrains.
        _make_db(tmp_path, [(timedelta(hours=1), "opencode-go", 9.0)])
        usage = await _provider(tmp_path).get_usage()
        assert usage.drempel_gebruikt == pytest.approx(0.75)
        assert usage.venster_label == "5h"

    async def test_overspend_past_the_cap_survives(self, tmp_path):
        # "Use balance" lets Go spend past a cap instead of blocking, so
        # 125% is a real state. Clamping it to 1.0 would hide it.
        _make_db(tmp_path, [(timedelta(hours=1), "opencode-go", 15.0)])
        usage = await _provider(tmp_path).get_usage()
        assert _by_label(usage)["5h"].used_fraction == pytest.approx(1.25)
        assert usage.drempel_gebruikt == pytest.approx(1.25)
        # Still "available": the real backstop is the provider pause on
        # an actual rate-limit event, not our arithmetic.
        assert usage.beschikbaar is True

    async def test_label_is_schatting_never_exact(self, tmp_path):
        # The cost is opencode's own computation over rolling windows
        # with no known billing anchor — that is not an exact figure.
        _make_db(tmp_path, [(timedelta(hours=1), "opencode-go", 1.0)])
        usage = await _provider(tmp_path).get_usage()
        assert usage.betrouwbaarheid == "schatting"
        assert usage.bron == "opencode_db:message_cost"

    async def test_rolling_windows_report_no_reset_instant(self, tmp_path):
        # A trailing window has no reset moment. Inventing one would be
        # the same decoration this feature exists to remove.
        _make_db(tmp_path, [(timedelta(hours=1), "opencode-go", 1.0)])
        usage = await _provider(tmp_path).get_usage()
        assert all(w.resets_at is None for w in usage.windows)

    async def test_malformed_and_boolean_costs_are_skipped(self, tmp_path):
        # ``True`` would sum as 1.0 through a naive isinstance(int) check.
        db = _make_db(tmp_path, [(timedelta(hours=1), "opencode-go", 2.0)])
        con = sqlite3.connect(db)
        ts = int((NOW - timedelta(hours=1)).timestamp() * 1000)
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            ("bad", "s1", ts, ts, "not json at all"),
        )
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            ("boolcost", "s1", ts, ts,
             json.dumps({"providerID": "opencode-go", "cost": True})),
        )
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            ("nullcost", "s1", ts, ts,
             json.dumps({"providerID": "opencode-go", "cost": None})),
        )
        con.commit()
        con.close()
        usage = await _provider(tmp_path).get_usage()
        assert _by_label(usage)["5h"].verbruikt == pytest.approx(2.0)

    async def test_zero_spend_is_a_real_zero_not_a_missing_signal(self, tmp_path):
        # An idle-but-readable DB is a measurement: 0% used, schatting.
        _make_db(tmp_path, [])
        usage = await _provider(tmp_path).get_usage()
        assert usage.betrouwbaarheid == "schatting"
        assert usage.drempel_gebruikt == pytest.approx(0.0)
