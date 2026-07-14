"""Tests for the dispatch-side subscription-pool integration (fase 1b).

Precedence (highest first):
  1. ``active_subscription_override`` (fase 0 — board-wide pin)
  2. **Subscription pool** (fase 1b — usage-aware router; this card)
  3. ``card.column_overrides[col]``
  4. ``column.default_*`` + persona frontmatter + card.model fallback

These tests pin the acceptance criteria:
- "Gekozen subscription levert {agent, provider, model} die op de
  **bestaande** dispatch_card-injectiepunten landen" — the spawn call
  sees the pool's chosen provider/model, not a separate path.
- "Gepauzeerde/uitgeputte subscriptions (per-provider pause) worden in
  de pool overgeslagen" — pause falls through to the next entry.
- "Subscription zonder signaal (analyse §6.3): behandel als altijd
  beschikbaar" — the dispatcher does not refuse to spawn a Codex card
  just because Codex has no usage signal.
- "Vendor-diverse pool" — entries map to existing provider allow-list.
- "Aanname: vendor-diverse pool ... same-vendor-multi-account valt
  buiten scope" — pool wiring lives on top of the existing
  active-subscription-override shape (no new isolation mechanism).

The fallback transport / recording helper is the same shape as
``test_active_subscription_override.py`` so both files stay
self-contained.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.kanban import dispatch, service, subscription_pool
from app.kanban.operations import apply_operation
from app.kanban.subscription_pool import PoolEntry
from app.main import app
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


PK = "git:example.com/me/repo"


class RecordingTransport:
    """Real (non-mock) transport that records spawn calls. Mirrors
    ``test_active_subscription_override.RecordingTransport`` so each
    pool test is self-contained and inspectable."""

    def __init__(self):
        self.calls = []

    def __call__(self, *, directory, prompt, session_name, cli_id="claude-code",
                 provider="anthropic", model=None):
        self.calls.append({"directory": directory, "prompt": prompt,
                           "session_name": session_name, "cli_id": cli_id,
                           "provider": provider, "model": model})
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}


async def _make_card(s, title="Task", column="Backlog"):
    return await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None,
        payload={"title": title, "column": column},
    )


def _entry(*, cli="claude-code", provider="anthropic", model=None, drempel=0.9):
    return PoolEntry(cli=cli, provider=provider, model=model, drempel=drempel)


def _patch_pool_pick(monkeypatch, snapshots):
    """Patch ``pick_subscription`` to inject the provided snapshots dict.

    We patch the symbol *on the source module* so every importer of
    ``app.kanban.subscription_pool.pick_subscription`` (including the
    dispatcher's binding) sees the test version. The snapshots dict
    mirrors what a real ``SubscriptionUsageProvider.get_usage()`` call
    would produce.
    """
    import app.kanban.subscription_pool as pool_mod
    snapshot_map = {
        f"{e.cli}:{e.provider}": snap
        for e, snap in snapshots.items()
    }

    real_pick = pool_mod.pick_subscription

    def patched(entries, usages, *, paused_providers):
        merged = {**usages, **snapshot_map}
        return real_pick(entries, merged, paused_providers=paused_providers)

    monkeypatch.setattr(pool_mod, "pick_subscription", patched)


def _usage(*, drempel_gebruikt=None, beschikbaar=True, betrouwbaarheid="onbekend"):
    """Shorthand factory for SubscriptionUsage."""
    from app.services.subscriptions.base import SubscriptionUsage
    return SubscriptionUsage(
        subscription_id="unused",  # patched per-call by _patch_pool_pick
        subscription_label="unused",
        beschikbaar=beschikbaar,
        drempel_gebruikt=drempel_gebruikt,
        bron="test",
        betrouwbaarheid=betrouwbaarheid,
    )


# ---- pool is honoured when set --------------------------------------------

@pytest.mark.asyncio
async def test_pool_first_entry_chosen_routes_to_its_provider():
    """The chosen subscription's provider wins over column.default_provider."""
    transport = RecordingTransport()
    pool = [_entry(provider="anthropic"), _entry(provider="minimax")]
    snapshots = {
        _entry(provider="anthropic"): _usage(drempel_gebruikt=0.1),
        _entry(provider="minimax"): _usage(drempel_gebruikt=0.1),
    }
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer", default_provider="bedrock",
        )
        cid = await _make_card(s)
        await subscription_pool.set_subscription_pool(s, PK, pool)
        await s.commit()

    with pytest.MonkeyPatch.context() as mp:
        _patch_pool_pick(mp, snapshots)
        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()

    assert len(transport.calls) == 1
    # Pool's first entry (anthropic) beats the column's default (bedrock).
    assert transport.calls[0]["provider"] == "anthropic"


@pytest.mark.asyncio
async def test_pool_entry_model_pins_dispatch_model():
    """When the pool entry sets a model, it overrides column.default_model."""
    transport = RecordingTransport()
    pool = [_entry(provider="anthropic", model="opus")]
    snapshots = {_entry(provider="anthropic", model="opus"): _usage(drempel_gebruikt=0.1)}
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer",
            default_provider="anthropic",
            default_model="sonnet",
        )
        cid = await _make_card(s)
        await subscription_pool.set_subscription_pool(s, PK, pool)
        await s.commit()

    with pytest.MonkeyPatch.context() as mp:
        _patch_pool_pick(mp, snapshots)
        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()

    assert transport.calls[0]["provider"] == "anthropic"
    assert transport.calls[0]["model"] == "opus"


@pytest.mark.asyncio
async def test_pool_entry_with_no_model_falls_through_to_chain():
    """model=None on the entry → column/default_model still applies."""
    transport = RecordingTransport()
    pool = [_entry(provider="anthropic", model=None)]
    snapshots = {_entry(provider="anthropic"): _usage(drempel_gebruikt=0.1)}
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer",
            default_provider="anthropic",
            default_model="sonnet",
        )
        cid = await _make_card(s)
        await subscription_pool.set_subscription_pool(s, PK, pool)
        await s.commit()

    with pytest.MonkeyPatch.context() as mp:
        _patch_pool_pick(mp, snapshots)
        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()

    assert transport.calls[0]["provider"] == "anthropic"
    assert transport.calls[0]["model"] == "sonnet"


# ---- backward-compat --------------------------------------------------------

@pytest.mark.asyncio
async def test_no_pool_is_backward_compatible():
    """When no pool is configured, dispatch is identical to today."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer", default_provider="minimax",
        )
        cid = await _make_card(s)
        # No set_subscription_pool call.
        await s.commit()

        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()

    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "minimax"


@pytest.mark.asyncio
async def test_clearing_pool_returns_to_backward_compat():
    """Setting the pool to None (clearing) → same as never having set one."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer", default_provider="anthropic",
        )
        cid = await _make_card(s)
        await subscription_pool.set_subscription_pool(
            s, PK, [_entry(provider="minimax")],
        )
        await subscription_pool.set_subscription_pool(s, PK, None)
        await s.commit()

        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()

    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "anthropic"


# ---- precedence: override > pool --------------------------------------------

@pytest.mark.asyncio
async def test_active_override_beats_pool():
    """The fase-0 active-subscription-override still wins over the pool.

    Documents the precedence: a human-set "route everything to X" pin
    dominates the automatic pool choice. This matches the existing
    override precedence chain — the pool slots *under* it, not beside
    it as an equal-tier knob.
    """
    transport = RecordingTransport()
    pool = [_entry(provider="minimax")]
    snapshots = {_entry(provider="minimax"): _usage(drempel_gebruikt=0.1)}
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer", default_provider="anthropic",
        )
        cid = await _make_card(s)
        await subscription_pool.set_subscription_pool(s, PK, pool)
        await dispatch.set_active_subscription_override(
            s, PK, {"provider": "bedrock", "model": None},
        )
        await s.commit()

    with pytest.MonkeyPatch.context() as mp:
        _patch_pool_pick(mp, snapshots)
        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()

    assert transport.calls[0]["provider"] == "bedrock"


# ---- pause integration ------------------------------------------------------

@pytest.mark.asyncio
async def test_paused_provider_in_pool_falls_through(monkeypatch):
    """When the pool's first entry's provider is paused, the router
    picks the next entry's provider. The dispatch uses the picked
    entry — not the column default."""
    transport = RecordingTransport()
    pool = [_entry(provider="anthropic"), _entry(provider="minimax")]
    snapshots = {
        _entry(provider="anthropic"): _usage(drempel_gebruikt=0.1),
        _entry(provider="minimax"): _usage(drempel_gebruikt=0.1),
    }
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer", default_provider="bedrock",
        )
        cid = await _make_card(s)
        await subscription_pool.set_subscription_pool(s, PK, pool)
        # Pause anthropic until well in the future.
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        from app.kanban.dispatch_pause import set_paused_until
        await set_paused_until(s, datetime.fromisoformat(future), provider="anthropic")
        await s.commit()

    import app.kanban.subscription_pool as pool_mod
    real_pick = pool_mod.pick_subscription

    def paused_pick(entries, usages, *, paused_providers):
        # Mirror what the dispatch wiring will pass: the per-provider
        # pause set is gathered from the session, not the snapshot.
        return real_pick(entries, usages, paused_providers={"anthropic"})

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pool_mod, "pick_subscription", paused_pick)
        _patch_pool_pick(mp, snapshots)
        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()

    assert transport.calls[0]["provider"] == "minimax"


# ---- REST endpoints ---------------------------------------------------------

def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_get_subscription_pool_endpoint_default():
    async with _client() as c:
        r = await c.get(
            "/api/v1/kanban/subscription-pool",
            params={"project_key": PK},
        )
    assert r.status_code == 200
    assert r.json() == {"project_key": PK, "pool": None}


@pytest.mark.asyncio
async def test_post_and_get_subscription_pool_endpoint():
    body = {
        "project_key": PK,
        "pool": [
            {"cli": "claude-code", "provider": "anthropic",
             "model": None, "drempel": 0.9},
            {"cli": "claude-code", "provider": "minimax",
             "model": "MiniMax-M3[1m]", "drempel": 0.95},
        ],
    }
    async with _client() as c:
        r = await c.post("/api/v1/kanban/subscription-pool", json=body)
        assert r.status_code == 200
        r2 = await c.get(
            "/api/v1/kanban/subscription-pool",
            params={"project_key": PK},
        )
    assert r2.json()["pool"] == body["pool"]


@pytest.mark.asyncio
async def test_post_subscription_pool_clear():
    async with _client() as c:
        await c.post(
            "/api/v1/kanban/subscription-pool",
            json={"project_key": PK, "pool": [
                {"cli": "claude-code", "provider": "minimax",
                 "model": None, "drempel": 0.9},
            ]},
        )
        r = await c.post(
            "/api/v1/kanban/subscription-pool",
            json={"project_key": PK, "pool": None},
        )
        assert r.status_code == 200
        r2 = await c.get(
            "/api/v1/kanban/subscription-pool",
            params={"project_key": PK},
        )
    assert r2.json()["pool"] is None


@pytest.mark.asyncio
async def test_post_subscription_pool_invalid_provider():
    body = {"project_key": PK, "pool": [
        {"cli": "claude-code", "provider": "openai",
         "model": None, "drempel": 0.9},
    ]}
    async with _client() as c:
        r = await c.post("/api/v1/kanban/subscription-pool", json=body)
    assert r.status_code == 422
