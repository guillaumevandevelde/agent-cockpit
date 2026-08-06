"""Per-provider exponential backoff for unparseable rate-limit messages
(kanban card b106def4…, R3 of ``subscription-auto-release-analyse``).

Sister module to ``rate_limit_signals``: that one dedupes a re-detection of
the *same* in-transcript message so the same event never re-arms the pause.
This one scales the *next* pause when a genuinely fresh limit comes in
without a parseable reset time. Kaart e279a52b… (R1) measured that a
re-detection of the same MiniMax "Token Plan" message re-armed the pause
~10 s/tick; kaart b106def4… (R3) is the orthogonal concern that the
*initial* pause for an unparseable message is also a blind 5h guess.

The MiniMax "Token Plan usage limit reached" wording carries no parseable
reset time, so ``parse_reset_time`` returns None and ``handle_rate_limit_signal``
falls back to a fixed ``FALLBACK_PAUSE_HOURS = 5``. A Token Plan limit often
resets in 1-3 minutes, so 5h wastes hours; a particularly long one might
need an hour. The backoff replaces the guess: the first fresh limit pauses
for a short window, each subsequent fresh limit on the same provider doubles
it, capped at an explicit maximum. A successful retry (the session
recovered — its transcript no longer shows the limit) resets the counter
to the initial window so the cycle starts clean.

Keys: ``rate_limit_backoff:<provider>``. One row per provider (per device),
in the kanban DB. Independent from the dedupe record at
``rate_limit_signal:<session>``: the dedupe is per-session-per-message
(re-detection of the *same* message), the backoff is per-provider
(consecutive *different* events on the same provider). They are written
from the same call site (``handle_rate_limit_signal``) but only the
backoff path runs when ``parse_reset_time`` returns None.

Value: a JSON object ``{"attempt": int, "armed_at": ISO8601}``.
``attempt`` is the count of *backoff-armed* fresh limits since the last
reset; the next fresh limit uses
``BACKOFF_SEQUENCE[min(attempt, len-1)]`` to compute its window.
``armed_at`` is when the most recent increment landed, used by the idle
sweep to age the counter out so a "always-armed" counter doesn't outlive
a long quiet period without a successful retry.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.kanban.models import KanbanMeta

logger = logging.getLogger(__name__)

_PREFIX = "rate_limit_backoff:"

# Backoff sequence in seconds. Starts short (a Token Plan limit often resets
# in 1-3 minutes), doubles per fresh limit, capped at one hour. Past the cap
# every further fresh limit uses the same hour — the spec says "exponential
# up to a cap", not unbounded: at some point waiting longer for an
# unspecified reset is roughly equivalent to giving up.
BACKOFF_SEQUENCE: list[int] = [120, 240, 480, 960, 1920, 3600]

# After this long with no new fresh limit, the counter resets to 0 — a quiet
# period is the closest thing to "success" we can observe without
# instrumenting the spawn path. A successful retry is also reset explicitly
# via ``reset_backoff``; this is the backstop for the case where no session
# ever recovered (e.g. the provider was paused for an operator reason and
# is now back).
IDLE_RESET_AFTER = timedelta(hours=2)


def _key_for(provider: str) -> str:
    return f"{_PREFIX}{provider}"


def window_for_attempt(attempt: int) -> int:
    """The window in seconds to use for the ``attempt``-th fresh limit.

    Indexes into ``BACKOFF_SEQUENCE`` (1-based to match ``record_backoff``'s
    returned counter). Past the end the window stops growing — that's the
    "explicit cap" from the card.
    """
    if attempt < 1:
        return BACKOFF_SEQUENCE[0]
    idx = min(attempt - 1, len(BACKOFF_SEQUENCE) - 1)
    return BACKOFF_SEQUENCE[idx]


@dataclass(frozen=True)
class BackoffState:
    """The per-provider backoff counter and when it was last bumped.

    ``attempt`` is the count of *backoff-armed* fresh limits since the last
    reset. The next fresh limit on this provider pauses for
    ``window_for_attempt(attempt)`` seconds, then this record is incremented
    to ``attempt + 1``.

    ``armed_at`` is when the most recent increment landed (UTC). The idle
    sweep compares it against ``IDLE_RESET_AFTER`` so a counter that has
    not moved in a long time is reset to 0 — a quiet period is treated as
    a soft "success".
    """

    attempt: int
    armed_at: datetime


def _parse_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def get_backoff(session, provider: str) -> BackoffState | None:
    """The stored backoff state for ``provider``, or None when nothing is stored.

    Returns None (rather than raising) for a row whose value predates this
    schema or was hand-edited into something unparseable — an unreadable
    record must degrade to "no backoff yet", which re-runs the reaction
    with the initial window. Same convention as
    ``rate_limit_signals.get_handled_signal``.
    """
    row = await session.get(KanbanMeta, _key_for(provider))
    if row is None:
        return None
    try:
        payload = json.loads(row.value)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    attempt = payload.get("attempt")
    armed_at = _parse_dt(payload.get("armed_at"))
    if not isinstance(attempt, int) or armed_at is None:
        return None
    return BackoffState(attempt=attempt, armed_at=armed_at)


async def record_backoff(
    session, provider: str, *, now: datetime | None = None,
) -> BackoffState:
    """Bump the backoff counter for ``provider`` and return the NEW state.

    Reads the existing counter (or starts at 0), increments by 1, and writes
    it back. The returned state reflects the new (post-increment) value, so
    the caller can use ``window_for_attempt(state.attempt - 1)`` to read the
    window that was just used and ``window_for_attempt(state.attempt)`` to
    read the window the *next* fresh limit will use.

    Last-write-wins on purpose, the mirror image of ``record_handled_signal``:
    this row is only ever written after the dedupe gate decided the signal
    is *new*, so an overwrite means a genuinely different limit and the
    newer counter is the one that matters.
    """
    now = now or datetime.now(UTC)
    current = await get_backoff(session, provider)
    new_attempt = (current.attempt if current else 0) + 1
    payload = json.dumps({
        "attempt": new_attempt,
        "armed_at": now.astimezone(UTC).isoformat(),
    })
    key = _key_for(provider)
    row = await session.get(KanbanMeta, key)
    if row is None:
        session.add(KanbanMeta(key=key, value=payload))
    else:
        row.value = payload
    await session.flush()
    return BackoffState(attempt=new_attempt, armed_at=now)


async def reset_backoff(session, provider: str) -> None:
    """Drop the backoff counter for ``provider``.

    Called when a session on this provider cleared its limit (the recovery
    path from R1): a working session is the closest thing to "success" the
    system can observe without instrumenting the spawn path. Also called by
    the idle sweep when ``armed_at`` is older than ``IDLE_RESET_AFTER``.
    """
    row = await session.get(KanbanMeta, _key_for(provider))
    if row is not None:
        await session.delete(row)
        await session.flush()


async def prune_idle_backoffs(
    session, *, older_than: timedelta = IDLE_RESET_AFTER,
) -> int:
    """Reset counters whose last increment is older than ``older_than``.

    Backstop for the case where ``reset_backoff`` was never called (no
    session ever recovered for any session on this provider) but the limit
    cleared anyway — e.g. the provider was paused for an operator reason
    and is now back. The cutoff is the age of the *last increment*, not
    "is this session still claimed": the sweep that calls this runs once
    per project, and a claimant-set filter would have each project's
    sweep deleting every other project's rows. Age is project-agnostic
    and safe to run from anywhere. Two hours is comfortably longer than
    the longest backoff window (one hour), so a row past it is no longer
    describing a live backoff.

    Returns the number of providers whose counter was reset.
    """
    cutoff = datetime.now(UTC) - older_than
    stmt = select(KanbanMeta).where(KanbanMeta.key.like(f"{_PREFIX}%"))
    rows = (await session.execute(stmt)).scalars().all()
    removed = 0
    for row in rows:
        state = await get_backoff(session, row.key[len(_PREFIX):])
        if state is None:
            continue
        if state.armed_at > cutoff:
            continue
        await session.delete(row)
        removed += 1
    if removed:
        await session.flush()
    return removed
