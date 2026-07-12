"""Per-provider auto-dispatch pause when Claude Code hits its account-wide
usage limit.

A "hit your session limit" notification means every session on this device hits
the same wall for the rest of the reset window, not just the one that reported
it. Without a global pause, the dispatch tick (``dispatch.run_dispatch_tick``)
would keep respawning cards out of "To Resume" every ~10s, each new session
immediately hitting the limit again and bouncing right back to "To Resume" --
which looks, from the board, exactly like auto-dispatch stalling even though
it's actually spinning and burning the account's remaining requests.

Persisted in ``KanbanMeta`` (not in-memory) so a backend restart during the
pause window doesn't immediately resume dispatch and re-trigger the loop.

Keys:
    ``dispatch_paused_until`` -- legacy global pause (one device-wide deadline).
    ``dispatch_paused_until:<provider>`` -- per-provider pause (the kanban-limit
        feature splits the single global slot into independent slots per
        Claude provider -- ``anthropic``, ``bedrock``, ``minimax`` -- so a
        bedrock-side outage does not freeze anthropic traffic).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.kanban.models import KanbanMeta

_GLOBAL_PAUSE_KEY = "dispatch_paused_until"
_PROVIDER_PAUSE_PREFIX = "dispatch_paused_until:"


def _key_for(provider: str | None) -> str:
    """Resolve the KanbanMeta key for the given provider (None -> global slot)."""
    if provider is None:
        return _GLOBAL_PAUSE_KEY
    return f"{_PROVIDER_PAUSE_PREFIX}{provider}"


async def get_paused_until(session, *, provider: str | None = None) -> datetime | None:
    """The UTC deadline auto-dispatch is paused until for ``provider``, or None
    if that slot is unset. ``provider=None`` reads the legacy global slot."""
    row = await session.get(KanbanMeta, _key_for(provider))
    if row is None:
        return None
    try:
        return datetime.fromisoformat(row.value)
    except ValueError:
        return None


async def set_paused_until(
    session, when: datetime | None, *, provider: str | None = None
) -> None:
    """Pause auto-dispatch for ``provider`` until ``when`` (stored as UTC), or
    clear that slot when ``when`` is None. Slots are independent: clearing the
    global slot does NOT touch per-provider slots and vice versa.

    ``provider=None`` writes/clears the legacy global slot -- the one callers
    that gate dispatch globally (e.g. ``dispatch.run_dispatch_tick``) read.
    """
    row = await session.get(KanbanMeta, _key_for(provider))
    if when is None:
        if row is not None:
            await session.delete(row)
            await session.flush()
        return
    value = when.astimezone(UTC).isoformat()
    if row is None:
        session.add(KanbanMeta(key=_key_for(provider), value=value))
    else:
        row.value = value
    await session.flush()


async def is_dispatch_paused(session, *, provider: str | None = None) -> bool:
    """True while the auto-dispatch tick should skip the ``provider`` slot.

    Self-clearing: once the stored deadline has passed, clears the row and
    returns False, so the very next tick (not a separately scheduled job) picks
    dispatch back up automatically after the usage limit resets.

    Per-provider lookups only consult the per-provider key -- a global pause
    does not show up as a per-provider pause (and vice versa). Callers that
    gate the whole tick continue to pass ``provider=None``.
    """
    paused_until = await get_paused_until(session, provider=provider)
    if paused_until is None:
        return False
    if datetime.now(UTC) >= paused_until:
        # Commit directly (not just flush): callers only use this session for the
        # pause check itself, so nothing else depends on it staying uncommitted,
        # and without a commit here the stale row would linger in KanbanMeta
        # forever -- still functionally harmless (the wall-clock check below would
        # keep returning False) but a permanent bit of clutter.
        await set_paused_until(session, None, provider=provider)
        await session.commit()
        return False
    return True


async def clear_all_pauses(session) -> None:
    """Wipe every pause slot -- both the legacy global key and every
    per-provider key. Used by the manual-resume DELETE route so a single
    operator click un-freezes the whole device, regardless of which providers
    currently have a per-provider deadline."""
    # Pull all pause rows in one query so we don't trigger a per-key
    # round-trip and so a brand-new provider (one whose slot was created by
    # another writer mid-call) is still cleared.
    stmt = select(KanbanMeta).where(
        (KanbanMeta.key == _GLOBAL_PAUSE_KEY)
        | KanbanMeta.key.like(f"{_PROVIDER_PAUSE_PREFIX}%")
    )
    rows = (await session.execute(stmt)).scalars().all()
    for row in rows:
        await session.delete(row)
    await session.flush()


async def list_paused_providers(session) -> list[str]:
    """Names of providers whose per-provider pause is currently active
    (deadline in the future). Used by the GET route to populate the
    ``paused_providers`` field of the dispatch-pause response.

    Expired per-provider slots are not listed -- they are stale rows waiting
    for the next ``is_dispatch_paused(provider=...)`` call to self-clear them
    (this helper is read-only and does not trigger that housekeeping).
    """
    now = datetime.now(UTC)
    stmt = select(KanbanMeta).where(
        KanbanMeta.key.like(f"{_PROVIDER_PAUSE_PREFIX}%")
    )
    rows = (await session.execute(stmt)).scalars().all()
    result: list[str] = []
    for row in rows:
        try:
            deadline = datetime.fromisoformat(row.value)
        except ValueError:
            # Unparseable value -- treat as no pause rather than crashing the
            # listing endpoint. The next is_dispatch_paused tick on that slot
            # will self-clear it via the wall-clock check.
            continue
        if deadline > now:
            result.append(row.key[len(_PROVIDER_PAUSE_PREFIX):])
    return result