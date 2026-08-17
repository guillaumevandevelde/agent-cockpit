"""Per-worktree lease + observed_owner stored in KanbanMeta.

Why this exists
---------------
A dispatched agent session creates a git worktree at
``<project>/.claude/worktrees/<session_name>`` and removes it again when the
card moves to Done/Impediment via :func:`session_cleanup.cleanup_session_for_card`.
Either side can fail: a hard ``kill -9`` of the agent process skips the
cleanup path entirely, and a host crash skips both. The worktree is then
left behind and only ``scripts/worktree-gc.sh`` can reclaim it.

The pre-lease ``worktree-gc`` heuristic (active kanban claim + clean + merged
into master) is best-effort: an analyst-only session never commits, so its
branch is trivially "merged+clean" from the moment it is created, and a
cooperative ``Done``-release is exactly the window in which a kill -9 leaves
behind a worktree that looks collectable. The lease replaces that heuristic
with a hard pattern: every worktree records its owner + an expiry. The
gc script and the cleanup module both consult the lease; only the timestamp
matters.

Key shape
---------
Two ``KanbanMeta`` rows per worktree, both keyed by the worktree name:

- ``worktree_lease:<worktree_name>`` → ISO-8601 expiry timestamp (UTC),
  e.g. ``2026-08-18T14:00:00+00:00``.
- ``worktree_owner:<worktree_name>`` → opaque owner string, e.g.
  ``dispatch:k-test-1234`` or ``card:a2268cd2…``. Free-form; the lease
  reader does not parse it, only echoes it for diagnostics.

The pair is written together and cleared together. ``clear_worktree_lease``
deletes both rows in a single transaction so a partial failure cannot leave
an owner row pointing at a missing expiry.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select

from app.kanban.db import KanbanSessionLocal
from app.kanban.models import KanbanMeta

logger = logging.getLogger(__name__)


#: Prefix for the KanbanMeta key carrying the lease expiry.
WORKTREE_LEASE_PREFIX = "worktree_lease:"

#: Prefix for the KanbanMeta key carrying the observed owner.
WORKTREE_OWNER_PREFIX = "worktree_owner:"

#: Default lease TTL when the caller does not override. 24h covers a normal
#: dispatched session that ships, plus a comfortable buffer for stderr lag,
#: operator response time, and a single overnight restart. The cockpit
#: completion path clears the lease via :func:`clear_worktree_lease` well
#: before this expires; the TTL exists only for the kill -9 / crash path.
WORKTREE_LEASE_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class WorktreeLease:
    """Live lease record returned by :func:`get_worktree_lease`.

    ``expires_at`` is always timezone-aware (UTC). ``is_live`` is the
    single source of truth for "should this worktree be left alone":
    ``expires_at > now``. ``owner`` is opaque and only meant for diagnostics.
    """

    owner: str
    expires_at: datetime

    def is_live(self, now: Optional[datetime] = None) -> bool:
        current = now if now is not None else _utcnow()
        return self.expires_at > current


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _lease_key(worktree_name: str) -> str:
    return f"{WORKTREE_LEASE_PREFIX}{worktree_name}"


def _owner_key(worktree_name: str) -> str:
    return f"{WORKTREE_OWNER_PREFIX}{worktree_name}"


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp produced by :func:`datetime.isoformat`.

    Accepts the trailing ``+00:00`` form used by ``datetime.isoformat()``;
    a naive value is treated as UTC. Raises ``ValueError`` on malformed
    input — the caller is responsible for handling the bad-row case.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def set_worktree_lease(
    worktree_name: str,
    owner: str,
    *,
    ttl_seconds: int = WORKTREE_LEASE_TTL_SECONDS,
    now: Optional[datetime] = None,
) -> WorktreeLease:
    """Write a lease + observed_owner for ``worktree_name``.

    Both rows are written in a single transaction so a partial failure cannot
    leave an owner row pointing at a missing expiry. If a previous lease
    exists, it is overwritten — the caller is the dispatch path and is
    authorised to claim the worktree.

    Returns the lease that was written, with the resolved expiry. Callers
    that need the value for logging should use this return rather than
    recomputing.
    """
    if not worktree_name:
        raise ValueError("worktree_name is required")
    if not owner:
        raise ValueError("owner is required")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")

    base = now if now is not None else _utcnow()
    expires_at = base + timedelta(seconds=ttl_seconds)

    async with KanbanSessionLocal() as session:
        for key, value in (
            (_lease_key(worktree_name), expires_at.isoformat()),
            (_owner_key(worktree_name), owner),
        ):
            row = await session.get(KanbanMeta, key)
            if row is None:
                session.add(KanbanMeta(key=key, value=value))
            else:
                row.value = value
        await session.commit()

    return WorktreeLease(owner=owner, expires_at=expires_at)


async def get_worktree_lease(
    worktree_name: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[WorktreeLease]:
    """Return the lease for ``worktree_name`` if both rows exist and parse.

    Returns ``None`` when either the expiry or the owner row is missing
    (a half-written lease is treated as no lease — the cleanup module
    would otherwise see an owner without an expiry and then mis-interpret
    the absence as a clear). Returns ``None`` on a malformed expiry
    string and logs a warning: the row is best deleted by the next
    legitimate cleanup, and a broken lease must never block git cleanup.
    """
    if not worktree_name:
        return None

    async with KanbanSessionLocal() as session:
        expiry_row = await session.get(KanbanMeta, _lease_key(worktree_name))
        if expiry_row is None:
            return None
        owner_row = await session.get(KanbanMeta, _owner_key(worktree_name))
        if owner_row is None:
            logger.warning(
                "Worktree lease for %s has expiry but no owner row; "
                "treating as no lease so cleanup can proceed",
                worktree_name,
            )
            return None

    try:
        expires_at = _parse_iso(expiry_row.value)
    except (TypeError, ValueError):
        logger.warning(
            "Worktree lease for %s has malformed expiry %r; "
            "treating as no lease so cleanup can proceed",
            worktree_name, expiry_row.value,
        )
        return None

    return WorktreeLease(owner=owner_row.value, expires_at=expires_at)


async def clear_worktree_lease(worktree_name: str) -> None:
    """Delete both lease rows for ``worktree_name`` in a single transaction.

    Idempotent — a missing row is not an error. Called from
    :func:`session_cleanup.cleanup_session_for_card` and from
    :func:`scripts/worktree-gc.sh` after a successful removal, so the
    next gc run does not see a stale lease pointing at a directory that
    no longer exists.
    """
    if not worktree_name:
        return

    async with KanbanSessionLocal() as session:
        await session.execute(
            delete(KanbanMeta).where(
                KanbanMeta.key.in_(
                    [_lease_key(worktree_name), _owner_key(worktree_name)]
                )
            )
        )
        await session.commit()


async def list_worktree_leases() -> dict[str, WorktreeLease]:
    """Return a snapshot of every worktree lease currently in the meta store.

    Used by the gc script's fallback path and by diagnostics. Only includes
    leases whose expiry row + owner row both parse cleanly; the caller
    decides what to do with stale rows (typically they are already gone
    via :func:`clear_worktree_lease` once the worktree is removed).
    """
    async with KanbanSessionLocal() as session:
        rows = (await session.execute(
            select(KanbanMeta).where(
                KanbanMeta.key.like(f"{WORKTREE_LEASE_PREFIX}%")
            )
        )).scalars().all()

        result: dict[str, WorktreeLease] = {}
        for expiry_row in rows:
            wt_name = expiry_row.key[len(WORKTREE_LEASE_PREFIX):]
            owner_row = await session.get(KanbanMeta, _owner_key(wt_name))
            if owner_row is None:
                continue
            try:
                expires_at = _parse_iso(expiry_row.value)
            except (TypeError, ValueError):
                continue
            result[wt_name] = WorktreeLease(
                owner=owner_row.value, expires_at=expires_at
            )
        return result
