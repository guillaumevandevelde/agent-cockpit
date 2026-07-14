"""Tests for the subscription-pool storage layer (KanbanMeta wrapper).

Mirrors the shape of ``test_active_subscription_override.py`` — same
project-keyed key-value row pattern so the existing fast-read path
(`KanbanMeta` keyed by ``subscription_pool:<project_key>``) carries the
pool without a schema migration.

What this pins:
- a project with no pool configured reads back None (the dispatch path
  falls back to today's behaviour — backward-compat guarantee);
- a stored pool round-trips through JSON via KanbanMeta;
- setting to None clears the row;
- invalid entries are rejected up front so a corrupt row never reaches
  ``pick_subscription`` (validation lives at the storage boundary, not
  inside the pure router);
- unknown providers are rejected (the same allow-list the
  active-subscription-override enforces — keeping the contract
  consistent across both pin-shape knobs).
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from app.kanban import dispatch, subscription_pool
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


PK = "git:example.com/me/repo"


def _valid_pool():
    """A pool that matches the allow-list; tests mutate it to exercise
    invalid shapes."""
    return [
        subscription_pool.PoolEntry(
            cli="claude-code", provider="anthropic", model=None, drempel=0.9,
        ),
        subscription_pool.PoolEntry(
            cli="claude-code", provider="minimax", model="MiniMax-M3[1m]",
            drempel=0.95,
        ),
    ]


# ---- storage layer ----------------------------------------------------------

@pytest.mark.asyncio
async def test_pool_defaults_to_none():
    """Backward-compat: a project that never set a pool reads back None."""
    async with KanbanSessionLocal() as s:
        assert await subscription_pool.get_subscription_pool(s, PK) is None


@pytest.mark.asyncio
async def test_set_and_get_pool_round_trips():
    """Stored pool round-trips through KanbanMeta without shape loss."""
    entries = _valid_pool()
    async with KanbanSessionLocal() as s:
        await subscription_pool.set_subscription_pool(s, PK, entries)
        await s.commit()
    async with KanbanSessionLocal() as s:
        got = await subscription_pool.get_subscription_pool(s, PK)
    assert got == entries


@pytest.mark.asyncio
async def test_set_pool_to_none_clears_it():
    """Setting to None removes the row so a follow-up read sees no pool."""
    async with KanbanSessionLocal() as s:
        await subscription_pool.set_subscription_pool(s, PK, _valid_pool())
        await s.commit()
        await subscription_pool.set_subscription_pool(s, PK, None)
        await s.commit()
    async with KanbanSessionLocal() as s:
        assert await subscription_pool.get_subscription_pool(s, PK) is None


@pytest.mark.asyncio
async def test_set_pool_overwrites_previous():
    async with KanbanSessionLocal() as s:
        await subscription_pool.set_subscription_pool(s, PK, _valid_pool())
        await s.commit()
        await subscription_pool.set_subscription_pool(
            s, PK,
            [subscription_pool.PoolEntry(
                cli="claude-code", provider="bedrock", model=None, drempel=0.8,
            )],
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        got = await subscription_pool.get_subscription_pool(s, PK)
    assert got is not None
    assert len(got) == 1
    assert got[0].provider == "bedrock"


# ---- validation at the storage boundary ------------------------------------

@pytest.mark.asyncio
async def test_set_pool_rejects_empty_pool():
    """An empty list is rejected — same shape as 'set to None' (= clear),
    and accepting it would silently turn the dispatcher into a no-op
    while the UI keeps showing the user's last saved pool."""
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await subscription_pool.set_subscription_pool(s, PK, [])


@pytest.mark.asyncio
async def test_set_pool_rejects_unknown_provider():
    """Unknown provider → 422 via the API path. Mirror the
    active-subscription-override allow-list to keep both knobs
    consistent (analyse §2.1: providers are an enum-like set)."""
    bad = [subscription_pool.PoolEntry(
        cli="claude-code", provider="openai", model=None, drempel=0.9,
    )]
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await subscription_pool.set_subscription_pool(s, PK, bad)


@pytest.mark.asyncio
async def test_set_pool_rejects_out_of_range_drempel():
    """Drempel must be in (0, 1]. 0 disables the entry (always "above
    threshold") and >1 disables the spillover entirely. Reject up front
    so the router never sees a confusing value."""
    bad = [subscription_pool.PoolEntry(
        cli="claude-code", provider="anthropic", model=None, drempel=0,
    )]
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await subscription_pool.set_subscription_pool(s, PK, bad)
    bad2 = [subscription_pool.PoolEntry(
        cli="claude-code", provider="anthropic", model=None, drempel=1.5,
    )]
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await subscription_pool.set_subscription_pool(s, PK, bad2)


@pytest.mark.asyncio
async def test_set_pool_rejects_empty_cli():
    """cli must be non-empty — used to build the ``{cli}:{provider}``
    subscription_id used by ``SubscriptionUsageProvider.id``. An empty
    cli would make every lookup miss silently."""
    bad = [subscription_pool.PoolEntry(
        cli="", provider="anthropic", model=None, drempel=0.9,
    )]
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await subscription_pool.set_subscription_pool(s, PK, bad)


# ---- the storage row is independent from the override row -------------------

@pytest.mark.asyncio
async def test_pool_and_override_coexist():
    """Setting the pool must not touch the active-subscription-override
    row (and vice versa) — they're independent knobs in the precedence
    chain (override > pool > column defaults; see dispatch wiring)."""
    async with KanbanSessionLocal() as s:
        await dispatch.set_active_subscription_override(
            s, PK, {"provider": "minimax", "model": None},
        )
        await subscription_pool.set_subscription_pool(s, PK, _valid_pool())
        await s.commit()
    async with KanbanSessionLocal() as s:
        override = await dispatch.get_active_subscription_override(s, PK)
        pool = await subscription_pool.get_subscription_pool(s, PK)
    assert override == {"provider": "minimax", "model": None}
    assert pool == _valid_pool()