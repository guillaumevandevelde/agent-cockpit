"""Durable record of the rate-limit signals dispatch has already reacted to.

Sister module to ``dispatch_pause``: that one stores *the pause*, this one
stores *the signal that armed it*, so a re-detection of the very same limit
message can be recognised as already-handled instead of arming a second pause.

Why a second store instead of the in-memory ``session_signals`` registry: a
rate-limited session writes nothing new to its transcript, so the same limit
message stays at the tail for as long as the limit lasts, and
``detect_transcript_rate_limits`` re-reads it on every dispatch tick (≈10 s).
The in-memory registry dedupes that fine *while the backend keeps running*,
but it is a plain process-local dict — a supervisor restart, a
``cockpit.sh restart`` or a ``uvicorn --reload`` empties it. The next tick
then treats the old message as brand new and re-arms the pause, which is
exactly the production bug this store closes (kanban card ``e279a52b…``):

    * unparseable reset time (MiniMax "Token Plan") → the
      ``+ FALLBACK_PAUSE_HOURS`` fallback slides forward with every tick and
      the pause never expires (8u36m onafgebroken her-armeren, gemeten
      2026-07-23→30);
    * parseable reset time (Anthropic "resets 05:20pm") →
      ``parse_reset_time``'s "past clock time means tomorrow" rollover pushes
      the deadline +24 u at the exact moment the limit lifts (gemeten op
      sessie ``k-update-readme-e85e`` om 2026-07-28T03:20:04Z).

Keys: ``rate_limit_signal:<tmux session name>``. Session names are minted at
≤ 20 chars (``dispatch._mint_session_name``), so the key stays well inside
``KanbanMeta.key``'s ``String(64)``.

Value: a JSON object ``{"digest", "observed_at", "pause_until"}``. Only the
message *digest* is stored, not the message itself — the identity check is an
equality test, the raw text is already available from the transcript, and a
bounded value keeps the meta row small.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.kanban.models import KanbanMeta

logger = logging.getLogger(__name__)

_PREFIX = "rate_limit_signal:"

# How long a stored signal stays around before the sweep garbage-collects it.
# Longer than the longest real limit window (the weekly cap) so a live limit
# is never pruned out from under the dedupe gate, short enough that dead rows
# don't accumulate indefinitely.
_SIGNAL_TTL = timedelta(days=7)


def _key_for(session_name: str) -> str:
    return f"{_PREFIX}{session_name}"


def message_digest(message: str | None) -> str:
    """Stable, bounded identity for a limit message.

    Two detections of the *same* limit produce the same digest; a genuinely
    new limit (different wording, different reset time) produces a different
    one and is therefore handled as a fresh event.
    """
    return hashlib.sha256((message or "").encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class HandledSignal:
    """A limit signal dispatch has already reacted to.

    ``observed_at`` is when the limit message itself was written (the
    transcript entry's timestamp when we have it, else the moment of first
    detection) — *not* the moment of the most recent re-detection. Keeping it
    pinned to the first observation is what makes the age check stable: a
    message re-read hours later is still judged against its own age.

    ``pause_until`` is the deadline that was armed, or ``None`` when the
    signal was recognised as already expired and no pause was set.
    """

    digest: str
    observed_at: datetime
    pause_until: datetime | None


def _parse_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def get_handled_signal(session, session_name: str) -> HandledSignal | None:
    """The stored signal for ``session_name``, or None when nothing is stored.

    Returns None (rather than raising) for a row whose value predates this
    schema or was hand-edited into something unparseable — an unreadable
    record must degrade to "not handled yet", never to a crashing dispatch
    tick.
    """
    row = await session.get(KanbanMeta, _key_for(session_name))
    if row is None:
        return None
    try:
        payload = json.loads(row.value)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    digest = payload.get("digest")
    observed_at = _parse_dt(payload.get("observed_at"))
    if not isinstance(digest, str) or observed_at is None:
        return None
    return HandledSignal(
        digest=digest,
        observed_at=observed_at,
        pause_until=_parse_dt(payload.get("pause_until")),
    )


async def is_signal_handled(session, session_name: str, message: str | None) -> bool:
    """True iff this exact limit message was already reacted to for this session."""
    stored = await get_handled_signal(session, session_name)
    return stored is not None and stored.digest == message_digest(message)


async def record_handled_signal(
    session,
    session_name: str,
    message: str | None,
    *,
    observed_at: datetime,
    pause_until: datetime | None,
) -> None:
    """Record that ``message`` has been reacted to for ``session_name``.

    Last-write-wins on purpose, the mirror image of ``record_limit``'s
    first-write-wins: this row is only ever written after the dedupe gate
    decided the signal is *new*, so an overwrite means a genuinely different
    limit and the newer one is the one that matters.
    """
    value = json.dumps({
        "digest": message_digest(message),
        "observed_at": observed_at.astimezone(UTC).isoformat(),
        "pause_until": (
            pause_until.astimezone(UTC).isoformat() if pause_until else None
        ),
    })
    key = _key_for(session_name)
    row = await session.get(KanbanMeta, key)
    if row is None:
        session.add(KanbanMeta(key=key, value=value))
    else:
        row.value = value
    await session.flush()


async def clear_handled_signal(session, session_name: str) -> None:
    """Forget the stored signal for ``session_name``.

    Called when the session recovered on its own, so the *next* genuine limit
    under the same name is handled as a fresh event even if it happens to
    carry identical wording.
    """
    row = await session.get(KanbanMeta, _key_for(session_name))
    if row is not None:
        await session.delete(row)
        await session.flush()


async def prune_handled_signals(session, *, older_than: timedelta = _SIGNAL_TTL) -> int:
    """Drop stored signals whose message is older than ``older_than``.

    Session names are single-use (``k-<slug>-<4 hex>``), so without this the
    table would accumulate one dead row per rate-limited session forever.

    The cutoff is the *age of the limit message*, not "is this session still
    claimed": the sweep that calls this runs once per project, and a
    claimant-set filter would have each project's sweep deleting every other
    project's rows. Age is project-agnostic and safe to run from anywhere.
    A week is comfortably longer than the longest real limit window (the
    weekly cap), so a row past it can no longer describe a live limit.

    Returns the number of rows removed.
    """
    cutoff = datetime.now(UTC) - older_than
    stmt = select(KanbanMeta).where(KanbanMeta.key.like(f"{_PREFIX}%"))
    rows = (await session.execute(stmt)).scalars().all()
    removed = 0
    for row in rows:
        stored = await get_handled_signal(session, row.key[len(_PREFIX):])
        if stored is not None and stored.observed_at > cutoff:
            continue
        # Unreadable rows are pruned too -- they can never satisfy the dedupe
        # gate anyway, so keeping them only grows the table.
        await session.delete(row)
        removed += 1
    if removed:
        await session.flush()
    return removed
