"""Shared time helpers.

The single shared coercion for the SQLite + SQLAlchemy naive-read footgun:
``DateTime(timezone=True)`` columns read back as **naive** ``datetime`` at the
Python boundary, so any comparison against ``datetime.now(UTC)`` raises
``TypeError: can't compare offset-naive and offset-aware datetimes``. Use
``ensure_aware`` instead of growing another inline ``if x.tzinfo is None: x =
x.replace(tzinfo=UTC)`` guard at every call site.
"""
from __future__ import annotations

from datetime import UTC, datetime


def ensure_aware(dt: datetime) -> datetime:
    """Return ``dt`` with a UTC tzinfo, or unchanged if already tz-aware.

    Identity-preserving on already-aware inputs — no copy, no allocation — so
    callers can use this as a drop-in coercion on hot paths (op-log scans,
    freshness windows). A non-UTC tzinfo is returned untouched: silently
    re-zoning would shift the absolute instant the timestamp represents,
    which is the opposite of what a "make aware" helper should do.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def parse_iso_datetime(value: str | datetime | None) -> datetime | None:
    """Parse a PATCHable timestamp column's stored value into an aware datetime.

    Columns that round-trip through the kanban op-log's JSON ``payload``
    (e.g. ``KanbanCard.scheduled_at``, ``dispatch_started_at`` — see the
    note on ``KanbanCard`` in ``app/kanban/models.py``) are stored as
    ISO-8601 strings rather than ``DateTime`` columns, so every consumer
    ends up parsing the same shape. Accepts either the string form (a
    freshly-read DB row) or a ``datetime`` (an in-memory ORM attribute
    that hasn't round-tripped yet) so callers don't need to branch.
    Returns ``None`` for ``None``, empty string, or an unparseable value.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return ensure_aware(value)
    try:
        return ensure_aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except (ValueError, TypeError):
        return None
