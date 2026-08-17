"""Tests for the per-worktree lease + observed_owner stored in KanbanMeta.

Covers the hard-pattern replacement for the pre-lease ``worktree-gc`` heuristic
(see kanban card a2268cd2… + ``docs/cockpit/fork-strategy-claude-deck-316.md``
§4.3). The key contracts:

- ``set_worktree_lease`` writes both rows in a single transaction.
- ``get_worktree_lease`` returns ``None`` when either row is missing or when
  the expiry is malformed — a half-written lease must never block cleanup.
- ``clear_worktree_lease`` deletes both rows and is idempotent.
- ``is_live`` is the single source of truth for "should this worktree be left
  alone" — past expiry is False.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.kanban import lease
from app.kanban.lease import (
    WORKTREE_LEASE_PREFIX,
    WORKTREE_LEASE_TTL_SECONDS,
    WORKTREE_OWNER_PREFIX,
    WorktreeLease,
)
from app.kanban.models import KanbanMeta
from tests.kanban_test_db import TestSessionLocal, reset_test_tables


KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.mark.asyncio
async def test_set_worktree_lease_writes_expiry_and_owner():
    now = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    written = await lease.set_worktree_lease(
        "k-test-1234", "card:abc", ttl_seconds=3600, now=now,
    )

    assert written.owner == "card:abc"
    assert written.expires_at == now + timedelta(seconds=3600)
    assert written.is_live(now=now) is True

    async with KanbanSessionLocal() as session:
        expiry_row = await session.get(KanbanMeta, "worktree_lease:k-test-1234")
        owner_row = await session.get(KanbanMeta, "worktree_owner:k-test-1234")
    assert expiry_row is not None
    assert owner_row is not None
    assert owner_row.value == "card:abc"
    # ISO-8601 round-trip; the parse helper in the module accepts it back.
    assert datetime.fromisoformat(expiry_row.value) == now + timedelta(seconds=3600)


@pytest.mark.asyncio
async def test_set_worktree_lease_overwrites_existing_lease():
    """A re-dispatch is allowed to claim the same worktree name."""
    now = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    await lease.set_worktree_lease(
        "k-test-1234", "dispatch:old", ttl_seconds=60, now=now,
    )
    later = now + timedelta(minutes=30)
    rewritten = await lease.set_worktree_lease(
        "k-test-1234", "card:new", ttl_seconds=7200, now=later,
    )

    assert rewritten.owner == "card:new"
    assert rewritten.expires_at == later + timedelta(seconds=7200)
    fetched = await lease.get_worktree_lease("k-test-1234", now=later)
    assert fetched is not None
    assert fetched.owner == "card:new"


@pytest.mark.asyncio
async def test_get_worktree_lease_returns_none_for_missing_expiry():
    assert await lease.get_worktree_lease("k-never-written") is None


@pytest.mark.asyncio
async def test_get_worktree_lease_returns_none_when_owner_row_missing():
    """Half-written lease: expiry without owner.

    A reader cannot tell who holds the worktree, so the cleanup module
    must behave as if no lease was written. The dataclass repr would
    still expose the expiry, so the writer-not-applied guard is on the
    reader side (not the writer).
    """
    async with KanbanSessionLocal() as session:
        session.add(KanbanMeta(
            key="worktree_lease:k-test-1234",
            value="2026-08-18T10:00:00+00:00",
        ))
        await session.commit()

    assert await lease.get_worktree_lease("k-test-1234") is None


@pytest.mark.asyncio
async def test_get_worktree_lease_returns_none_for_malformed_expiry():
    async with KanbanSessionLocal() as session:
        session.add(KanbanMeta(key="worktree_lease:k-test-1234", value="not-a-date"))
        session.add(KanbanMeta(key="worktree_owner:k-test-1234", value="card:abc"))
        await session.commit()

    assert await lease.get_worktree_lease("k-test-1234") is None


@pytest.mark.asyncio
async def test_lease_is_live_until_expiry_then_dead():
    now = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    lease_entry = WorktreeLease(
        owner="card:abc",
        expires_at=now + timedelta(seconds=60),
    )
    assert lease_entry.is_live(now=now) is True
    assert lease_entry.is_live(now=now + timedelta(seconds=30)) is True
    assert lease_entry.is_live(now=now + timedelta(seconds=61)) is False
    assert lease_entry.is_live(now=now + timedelta(hours=1)) is False


@pytest.mark.asyncio
async def test_clear_worktree_lease_removes_both_rows():
    await lease.set_worktree_lease(
        "k-test-1234", "card:abc", ttl_seconds=3600,
    )

    async with KanbanSessionLocal() as session:
        assert await session.get(KanbanMeta, "worktree_lease:k-test-1234") is not None
        assert await session.get(KanbanMeta, "worktree_owner:k-test-1234") is not None

    await lease.clear_worktree_lease("k-test-1234")

    async with KanbanSessionLocal() as session:
        assert await session.get(KanbanMeta, "worktree_lease:k-test-1234") is None
        assert await session.get(KanbanMeta, "worktree_owner:k-test-1234") is None


@pytest.mark.asyncio
async def test_clear_worktree_lease_is_idempotent_on_missing_rows():
    # No prior lease exists. Clearing must not raise.
    await lease.clear_worktree_lease("k-never-written")


@pytest.mark.asyncio
async def test_list_worktree_leases_returns_only_parseable_pairs():
    now = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    await lease.set_worktree_lease("k-a", "dispatch:a", ttl_seconds=60, now=now)
    await lease.set_worktree_lease("k-b", "card:b", ttl_seconds=120, now=now)

    # Half-written lease: must be SKIPPED, not crashed.
    async with KanbanSessionLocal() as session:
        session.add(KanbanMeta(
            key="worktree_lease:k-half",
            value=(now + timedelta(seconds=60)).isoformat(),
        ))
        await session.commit()

    # Malformed expiry: must be SKIPPED, not crashed.
    async with KanbanSessionLocal() as session:
        session.add(KanbanMeta(
            key="worktree_lease:k-broken",
            value="not-a-date",
        ))
        session.add(KanbanMeta(key="worktree_owner:k-broken", value="card:x"))
        await session.commit()

    leases = await lease.list_worktree_leases()
    assert sorted(leases.keys()) == ["k-a", "k-b"]
    assert leases["k-a"].owner == "dispatch:a"
    assert leases["k-b"].owner == "card:b"


@pytest.mark.asyncio
async def test_set_worktree_lease_rejects_blank_inputs():
    now = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        await lease.set_worktree_lease("", "card:abc", now=now)
    with pytest.raises(ValueError):
        await lease.set_worktree_lease("k-test", "", now=now)
    with pytest.raises(ValueError):
        await lease.set_worktree_lease(
            "k-test", "card:abc", ttl_seconds=0, now=now,
        )


def test_default_ttl_is_24h():
    """The default TTL must cover a normal dispatch plus a comfortable buffer.

    Hardcoded test — a regression that drops the TTL to e.g. 1 hour would
    promote "kill -9 during a long ship" to a frequent false-positive for
    worktree-gc reaping. 24h is the documented value in module docstring.
    """
    assert WORKTREE_LEASE_TTL_SECONDS == 24 * 60 * 60


def test_module_prefixes_match_docstring():
    """If the prefixes change, every consumer (gc script, tests) breaks.

    Pinned here so the contract is grep-visible from one place.
    """
    assert WORKTREE_LEASE_PREFIX == "worktree_lease:"
    assert WORKTREE_OWNER_PREFIX == "worktree_owner:"
