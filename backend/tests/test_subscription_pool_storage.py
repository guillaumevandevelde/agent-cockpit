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
    invalid shapes. The legacy ``cli`` field was dropped in kaart
    0b3ad6e2…."""
    return [
        subscription_pool.PoolEntry(
            provider="anthropic", model=None, drempel=0.9,
        ),
        subscription_pool.PoolEntry(
            provider="minimax", model="MiniMax-M3[1m]",
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
                provider="bedrock", model=None, drempel=0.8,
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
        provider="openai", model=None, drempel=0.9,
    )]
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await subscription_pool.set_subscription_pool(s, PK, bad)


@pytest.mark.asyncio
async def test_set_pool_accepts_anthropic_compatible_provider():
    """The data-driven ``anthropic-compatible`` branch (see
    ``app/services/agentic_cli/endpoints.py``) is on the allow-list so a
    pool entry can point at a named endpoint row.
    """
    entries = [subscription_pool.PoolEntry(
        provider="anthropic-compatible", model=None, drempel=0.9,
    )]
    async with KanbanSessionLocal() as s:
        await subscription_pool.set_subscription_pool(s, PK, entries)
        await s.commit()
    async with KanbanSessionLocal() as s:
        got = await subscription_pool.get_subscription_pool(s, PK)
    assert got == entries


@pytest.mark.asyncio
async def test_set_pool_rejects_out_of_range_drempel():
    """Drempel must be in (0, 1]. 0 disables the entry (always "above
    threshold") and >1 disables the spillover entirely. Reject up front
    so the router never sees a confusing value."""
    bad = [subscription_pool.PoolEntry(
        provider="anthropic", model=None, drempel=0,
    )]
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await subscription_pool.set_subscription_pool(s, PK, bad)
    bad2 = [subscription_pool.PoolEntry(
        provider="anthropic", model=None, drempel=1.5,
    )]
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await subscription_pool.set_subscription_pool(s, PK, bad2)


# ---- the cli field was removed (card 0b3ad6e2…) -------------------------
#
# `PoolEntry.cli` was dropped because the dispatcher never consumed it
# (analysis §3 D3) and the CLI is board-wide pinned to ``claude-code``
# (analysis §2.3). The pool's snapshot lookup key uses the ``POOL_CLI``
# constant instead — see subscription_pool.py.

@pytest.mark.asyncio
async def test_pool_entry_no_longer_accepts_cli_kwarg():
    """PoolEntry's surface is now ``(provider, model, drempel)`` — the
    legacy ``cli`` field is gone (analysis §3 D3 + card 0b3ad6e2…).

    Constructing with ``cli=...`` must raise TypeError so a caller that
    is still passing it (e.g. a stale UI bundle) fails loudly instead of
    silently losing the field."""
    with pytest.raises(TypeError):
        subscription_pool.PoolEntry(
            cli="claude-code", provider="anthropic", model=None, drempel=0.9,
        )


def test_deserialize_tolerates_legacy_cli_field():
    """A row whose JSON still contains ``cli`` (from before this chore)
    must round-trip cleanly — the field is silently stripped on read so
    the dispatcher never wedges on a legacy KanbanMeta row.

    Pins the migration contract: a stored row written by a pre-fix
    build must still load without manual data surgery. See card
    0b3ad6e2… acceptance criterion #3.

    This test is synchronous but lives under the module-level
    ``pytestmark = pytest.mark.asyncio`` (so it shares the async DB
    fixtures with the rest of the file). pytest-asyncio emits a warning
    about that — it's harmless and the test is wired correctly."""
    import json as _json
    legacy_row = _json.dumps([
        {"cli": "claude-code", "provider": "anthropic",
         "model": None, "drempel": 0.9},
        {"cli": "claude-code", "provider": "minimax",
         "model": "MiniMax-M3[1m]", "drempel": 0.95},
    ])
    result = subscription_pool._deserialize_entries(legacy_row)
    assert result is not None
    assert len(result) == 2
    assert result[0].provider == "anthropic"
    assert result[0].model is None
    assert result[0].drempel == 0.9
    assert result[1].provider == "minimax"
    assert result[1].model == "MiniMax-M3[1m]"
    assert result[1].drempel == 0.95


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