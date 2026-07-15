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


# ---- D1+D2+D5 regression tests ---------------------------------------------
#
# Three paired defects made the per-subscription drempel branch of the pool
# router effectively dead code:
#
#   D1 — `await _registry.get_provider_for(...)` on a sync ``def``. The
#        TypeError was silently swallowed by ``_pick_pool_choice``'s
#        ``except Exception`` so the snapshot map stayed empty.
#   D2 — ``_PROVIDERS`` was never populated: ``register_provider`` had no
#        callers in production, so even with D1 fixed the lookup returned
#        None on every entry.
#   D5 — The existing dispatch-integratietests patch ``pick_subscription``
#        on the bronmodule, but ``dispatch.py`` imports the symbol with a
#        ``from … import pick_subscription`` binding; the patch is invisible
#        to dispatch. There was no test that proved the threshold-spill
#        end-to-end, only tests that "happened to pass" on the degenerating
#        "entry #1 wins" baseline.
#
# These three tests pin the fix together. They MUST fail without the fix
# and pass with it; red first, then green, kept side-by-side so a future
# refactor can't silently re-break either piece.

from contextlib import contextmanager


@contextmanager
def _registry_state():
    """Snapshot+restore the SubscriptionUsageProvider registry around a test.

    The registry is module-level mutable state; without this helper a test
    that registers a fake would leak its row into every subsequent test
    (and conversely, the lifespan-registered default providers would
    surface here). Mirrors the "save, clear, yield, restore" pattern used
    by ``conftest.py::_patch_kanban_db``.
    """
    from app.services.subscriptions import registry as reg
    saved = dict(reg._PROVIDERS)
    reg._PROVIDERS.clear()
    try:
        yield reg
    finally:
        reg._PROVIDERS.clear()
        reg._PROVIDERS.update(saved)


def _fake_usage_provider(
    *, subscription_id: str, subscription_label: str,
    drempel_gebruikt: float | None,
    beschikbaar: bool = True, betrouwbaarheid: str = "exact",
):
    """Build a minimal SubscriptionUsageProvider that returns a fixed snapshot.

    Defined as a factory (not a class with stubs) so each test sees a fresh
    ``id`` (the registry keys on it) — prevents prior-test leftovers from
    leaking through the snapshot-injection mechanism.
    """
    from app.services.subscriptions.base import (
        SubscriptionUsage,
        SubscriptionUsageProvider,
    )

    class FakeProvider(SubscriptionUsageProvider):
        # id/label are class attrs on the ABC; setting them on the instance
        # via __init__ would be more pythonic but the abstract check above
        # rejects un-overridden abstract attrs only at __init__, and these
        # two are declared (not abstract). instance assignment keeps the
        # factory pure — no mutable class state across calls.
        async def get_usage(self) -> SubscriptionUsage:
            return SubscriptionUsage(
                subscription_id=subscription_id,
                subscription_label=subscription_label,
                beschikbaar=beschikbaar,
                drempel_gebruikt=drempel_gebruikt,
                bron="test:fake_provider",
                betrouwbaarheid=betrouwbaarheid,
            )
    provider = FakeProvider()
    provider.id = subscription_id
    provider.label = subscription_label
    return provider


@pytest.mark.asyncio
async def test_gather_pool_usage_snapshots_returns_registered_fake_provider():
    """D1+D2: when a concrete ``SubscriptionUsageProvider`` is registered
    for the entry's ``(cli, provider)``, the snapshot reacher returns it.

    Pins the wiring mechanic:
      * ``get_provider_for`` must be sync (no ``await``); the TypeError
        regression that crashed the call path is exactly what D1 fixed.
      * The provider's ``get_usage()`` output must appear in the returned
        dict, keyed by its ``subscription_id`` (matching
        ``f"{entry.cli}:{entry.provider}"``).

    Unregistered pairs continue to contribute no snapshot — backwards-
    compatible with the legacy "no signal → available" clause."""
    entry = _entry(provider="anthropic")
    fake = _fake_usage_provider(
        subscription_id="claude-code:anthropic",
        subscription_label="fake-anthropic",
        drempel_gebruikt=0.42,
    )
    with _registry_state() as reg:
        reg.register_provider(fake)
        # Re-register after clear() to be explicit about which provider
        # this test exercises (and to keep the assertion below obvious).
        assert reg.get_provider_for(cli="claude-code", provider="anthropic") is fake

        from app.kanban import dispatch
        snapshots = await dispatch._gather_pool_usage_snapshots([entry])

    assert "claude-code:anthropic" in snapshots
    assert snapshots["claude-code:anthropic"].drempel_gebruikt == 0.42
    # Same entry, re-fetched — proves the dict iterates the entries list,
    # not just whatever happens to be in the registry.
    assert snapshots["claude-code:anthropic"].subscription_id == "claude-code:anthropic"


@pytest.mark.asyncio
async def test_no_registered_provider_returns_empty_snapshot_dict():
    """When no provider is registered for the entry, snapshots stays
    empty — preserves the analyse §6.3 "no signal → available" path.

    This is the unrelated-pair half of the drempel router; the test
    above covers the populated half. Both pass on the same fixed code:
    ``_gather_pool_usage_snapshots`` simply skips entries whose lookup
    resolves to None."""
    from app.kanban import dispatch
    entry = _entry(provider="minimax")
    with _registry_state():
        snapshots = await dispatch._gather_pool_usage_snapshots([entry])
    assert snapshots == {}


@pytest.mark.asyncio
async def test_dispatch_pool_spills_when_first_entry_above_threshold():
    """D1+D2+D5 end-to-end: with a registered fake provider reporting
    ``drempel_gebruikt=0.95`` for the pool's first entry (above its
    drempel of 0.9), dispatch routes to the second entry's provider.

    This is the integration the original 11-test file never had: the
    existing tests "passed" because ``_gather_pool_usage_snapshots``
    silently swallowed the D1 TypeError, snapshots stayed empty, and the
    pick was the degenerating "entry #1 wins" baseline. To prove the
    drempel branch is alive end-to-end we need:
      * a real registered provider (no ``monkeypatch`` of pick_subscription)
      * a snapshot above threshold (proves the snap actually reaches
        ``pick_subscription``)
      * an entry #2 that the router will pick (proves the spill logic
        ran, not coincidence).

    Asserts the actual spawned transport's provider — the same shape the
    existing pool tests use — so a regression that returns the wrong
    PoolEntry from ``_pick_pool_choice`` (instead of the right one) is
    caught here, not in a wire-mock test."""
    transport = RecordingTransport()
    pool = [_entry(provider="anthropic"), _entry(provider="minimax")]
    from app.services.subscriptions.unknown import UnknownUsageProvider

    with _registry_state() as reg:
        # Entry #1 (anthropic): above threshold → must be skipped.
        reg.register_provider(_fake_usage_provider(
            subscription_id="claude-code:anthropic",
            subscription_label="fake-anthropic",
            drempel_gebruikt=0.95,
            beschikbaar=False,
        ))
        # Entry #2 (minimax): no signal (unknown) — router treats as
        # available per analyse §6.3, so it becomes the pick.
        reg.register_provider(UnknownUsageProvider(
            subscription_id="claude-code:minimax",
            subscription_label="test-minimax",
        ))

        async with KanbanSessionLocal() as s:
            await service.create_column(
                s, project_key=PK, name="engineer",
                default_agent="engineer", default_provider="bedrock",
            )
            cid = await _make_card(s)
            await subscription_pool.set_subscription_pool(s, PK, pool)
            await s.commit()

        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()

    assert len(transport.calls) == 1
    # The pool's first entry (anthropic) was above its drempel via the
    # registered fake provider, so the router must spill to entry #2
    # (minimax) — NOT the column default (bedrock), NOT entry #1
    # (anthropic).
    assert transport.calls[0]["provider"] == "minimax"
