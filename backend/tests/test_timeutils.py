"""``ensure_aware`` is the one shared coercion for the SQLite naive-read gotcha.

Every ``DateTime(timezone=True)`` column in this repo round-trips back as a
naive ``datetime`` at the Python boundary — comparing one of those against
``datetime.now(UTC)`` raises ``TypeError: can't compare offset-naive and
offset-aware datetimes``. ``ensure_aware`` is the canonical fix so new code
doesn't grow another inline ``if x.tzinfo is None: x = x.replace(tzinfo=UTC)``
guard.
"""
from datetime import UTC, datetime, timedelta, timezone

from app.utils.timeutils import ensure_aware, parse_iso_datetime


def test_naive_datetime_is_coerced_to_utc_aware():
    naive = datetime(2026, 7, 14, 12, 0, 0)
    result = ensure_aware(naive)
    assert result.tzinfo is UTC
    # Wall-clock instant unchanged; only the tzinfo label flips from None to UTC.
    assert result == naive.replace(tzinfo=UTC)


def test_utc_aware_datetime_is_returned_unchanged():
    aware = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)
    result = ensure_aware(aware)
    # Identity-preserving on already-aware inputs — no copy, no allocation.
    assert result is aware


def test_non_utc_aware_datetime_is_returned_unchanged():
    """A non-UTC tzinfo must not be silently re-zoned — that would shift the
    absolute instant the timestamp represents."""
    plus_two = timezone(timedelta(hours=2))
    aware = datetime(2026, 7, 14, 14, 0, 0, tzinfo=plus_two)
    result = ensure_aware(aware)
    assert result is aware
    assert result.tzinfo is plus_two


def test_now_utc_round_trips_through_helper():
    """Sanity check against the footgun that motivated this helper: feeding
    ``now(UTC)`` back through ``ensure_aware`` must not raise or shift."""
    now = datetime.now(UTC)
    assert ensure_aware(now) is now


def test_parse_iso_datetime_none_and_empty_string():
    assert parse_iso_datetime(None) is None
    assert parse_iso_datetime("") is None


def test_parse_iso_datetime_accepts_iso_string_with_z_suffix():
    result = parse_iso_datetime("2026-07-14T12:00:00Z")
    assert result == datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)


def test_parse_iso_datetime_accepts_iso_string_with_offset():
    result = parse_iso_datetime("2026-07-14T12:00:00+00:00")
    assert result == datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)


def test_parse_iso_datetime_accepts_naive_datetime_and_makes_it_aware():
    naive = datetime(2026, 7, 14, 12, 0, 0)
    result = parse_iso_datetime(naive)
    assert result == naive.replace(tzinfo=UTC)


def test_parse_iso_datetime_accepts_aware_datetime_unchanged():
    aware = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)
    result = parse_iso_datetime(aware)
    assert result is aware


def test_parse_iso_datetime_returns_none_for_unparseable_value():
    assert parse_iso_datetime("not-a-date") is None
    assert parse_iso_datetime(12345) is None
