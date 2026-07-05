"""Global auto-dispatch pause when Claude Code hits its account-wide usage limit.

A "hit your session limit" notification means every session on this device hits
the same wall for the rest of the reset window, not just the one that reported it.
Without a global pause, the dispatch tick (``dispatch.run_dispatch_tick``) would
keep respawning cards out of "To Resume" every ~10s, each new session immediately
hitting the limit again and bouncing right back to "To Resume" -- which looks,
from the board, exactly like auto-dispatch stalling even though it's actually
spinning and burning the account's remaining requests.

Persisted in ``KanbanMeta`` (not in-memory) so a backend restart during the pause
window doesn't immediately resume dispatch and re-trigger the loop.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.kanban.models import KanbanMeta

_PAUSE_KEY = "dispatch_paused_until"


async def get_paused_until(session) -> datetime | None:
    """The UTC deadline auto-dispatch is paused until, or None if not paused."""
    row = await session.get(KanbanMeta, _PAUSE_KEY)
    if row is None:
        return None
    try:
        return datetime.fromisoformat(row.value)
    except ValueError:
        return None


async def set_paused_until(session, when: datetime | None) -> None:
    """Pause auto-dispatch until ``when`` (stored as UTC), or clear when None."""
    row = await session.get(KanbanMeta, _PAUSE_KEY)
    if when is None:
        if row is not None:
            await session.delete(row)
            await session.flush()
        return
    value = when.astimezone(UTC).isoformat()
    if row is None:
        session.add(KanbanMeta(key=_PAUSE_KEY, value=value))
    else:
        row.value = value
    await session.flush()


async def is_dispatch_paused(session) -> bool:
    """True while the auto-dispatch tick should be skipped entirely.

    Self-clearing: once the stored deadline has passed, clears the row and
    returns False, so the very next tick (not a separately scheduled job) picks
    dispatch back up automatically after the usage limit resets.
    """
    paused_until = await get_paused_until(session)
    if paused_until is None:
        return False
    if datetime.now(UTC) >= paused_until:
        # Commit directly (not just flush): callers only use this session for the
        # pause check itself, so nothing else depends on it staying uncommitted,
        # and without a commit here the stale row would linger in KanbanMeta
        # forever -- still functionally harmless (the wall-clock check below would
        # keep returning False) but a permanent bit of clutter.
        await set_paused_until(session, None)
        await session.commit()
        return False
    return True
