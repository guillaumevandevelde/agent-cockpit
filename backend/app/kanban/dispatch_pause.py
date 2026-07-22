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
    ``dispatch_paused_manual:<provider>`` -- operator-toggled indefinite pause
        per subscription. Distinct from the time-based slots above: the value
        is a boolean flag (``"1"`` = paused), not a deadline, because the
        operator expects "pause all anthropic dispatches until I say
        otherwise" rather than "until <future timestamp>". An auto-tripped
        time-based limit and a manual operator override can coexist on the
        same provider; either one being active keeps dispatch off (see
        ``dispatch.py:_dispatch_project``).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.kanban.models import KanbanMeta

_GLOBAL_PAUSE_KEY = "dispatch_paused_until"
_PROVIDER_PAUSE_PREFIX = "dispatch_paused_until:"
_MANUAL_PAUSE_PREFIX = "dispatch_paused_manual:"


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
    """Wipe every pause slot -- the legacy global key, every per-provider
    time-based key, and every per-provider manual key. Used by the
    manual-resume DELETE route so a single operator click un-freezes the
    whole device, regardless of whether the pause was triggered by an
    auto-tripped usage limit, an operator toggle, or both."""
    # Pull all pause rows in one query so we don't trigger a per-key
    # round-trip and so a brand-new provider (one whose slot was created by
    # another writer mid-call) is still cleared.
    stmt = select(KanbanMeta).where(
        (KanbanMeta.key == _GLOBAL_PAUSE_KEY)
        | KanbanMeta.key.like(f"{_PROVIDER_PAUSE_PREFIX}%")
        | KanbanMeta.key.like(f"{_MANUAL_PAUSE_PREFIX}%")
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


# ---- manual per-subscription pause (kaart f056b2888a…) ----------------------
#
# Independent from the time-based per-provider pause above: an operator who
# clicks "pause anthropic dispatches" expects the gate to stay off until
# they unpause it (no deadline), so the value is a boolean flag rather than
# a deadline. The auto-tripped time-based pause and the manual operator
# override can coexist on the same provider -- e.g. a usage-limit hit on
# anthropic plus an operator who paused bedrock "while we're at it" -- and
# either being active keeps dispatch off (see ``dispatch.py``).


def _manual_key_for(provider: str) -> str:
    """Resolve the KanbanMeta key for a manual pause on ``provider``."""
    return f"{_MANUAL_PAUSE_PREFIX}{provider}"


async def is_manually_paused(session, provider: str) -> bool:
    """True while the operator has toggled on an indefinite pause for
    ``provider``. Distinct from ``is_dispatch_paused(provider=...)``, which
    consults the time-based slot only -- the dispatch gate combines both."""
    row = await session.get(KanbanMeta, _manual_key_for(provider))
    return row is not None and row.value == "1"


async def set_manual_pause(session, provider: str, paused: bool) -> None:
    """Toggle the manual pause for ``provider``. ``paused=True`` writes the
    key (value ``"1"``); ``paused=False`` deletes it. Idempotent: setting an
    already-set provider to ``True`` is a no-op, and clearing an unset
    provider is a no-op. The time-based per-provider pause slot is
    independent and untouched by this call."""
    key = _manual_key_for(provider)
    row = await session.get(KanbanMeta, key)
    if not paused:
        if row is not None:
            await session.delete(row)
            await session.flush()
        return
    if row is None:
        session.add(KanbanMeta(key=key, value="1"))
    elif row.value != "1":
        row.value = "1"
    await session.flush()


async def list_manually_paused_providers(session) -> list[str]:
    """Names of providers whose manual pause is currently active. Used by
    the GET route to populate ``manually_paused_providers`` so the frontend
    banner can surface "anthropic is paused until you turn it back on"
    distinctly from the time-based limit message."""
    stmt = select(KanbanMeta).where(
        KanbanMeta.key.like(f"{_MANUAL_PAUSE_PREFIX}%")
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        row.key[len(_MANUAL_PAUSE_PREFIX):]
        for row in rows
        if row.value == "1"
    ]