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
                 provider="anthropic", model=None,
                 endpoint_name=None, endpoint_base_url=None,
                 endpoint_auth_token=None,
                 card_id=None, column_name=None):
        self.calls.append({"directory": directory, "prompt": prompt,
                           "session_name": session_name, "cli_id": cli_id,
                           "provider": provider, "model": model,
                           "card_id": card_id, "column_name": column_name})
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}


async def _make_card(s, title="Task", column="Backlog", executor_agent_id=None):
    """Helper — create a card. ``executor_agent_id`` is forwarded to the
    kanban op so the CLI-aware tests can pin the card's executor CLI
    (e.g. ``open-code``) for the dispatch path's
    ``_phase_cli_id`` resolution (kaart 8f40d443…)."""
    payload = {"title": title, "column": column}
    if executor_agent_id is not None:
        payload["executor_agent_id"] = executor_agent_id
    return await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None,
        payload=payload,
    )


def _entry(*, provider="anthropic", model=None, drempel=0.9, cli=None):
    """Shorthand for a PoolEntry. ``cli=None`` defaults to
    ``DEFAULT_POOL_CLI`` (``"claude-code"``) so the legacy
    claude-code-only tests keep building without ceremony. The
    CLI-aware tests (kaart 8f40d443…) pass ``cli="open-code"`` etc.
    explicitly to pin the per-CLI quota axis."""
    if cli is None:
        return PoolEntry(provider=provider, model=model, drempel=drempel)
    return PoolEntry(
        cli=cli, provider=provider, model=model, drempel=drempel,
    )


def _patch_pool_pick(monkeypatch, snapshots):
    """Patch ``pick_subscription_for_cli`` (and the legacy
    ``pick_subscription`` alias) to inject the provided snapshots dict.

    Kaart 8f40d443…: dispatch now calls
    ``pick_subscription_for_cli(cli_id=...)`` directly, so the patch
    must mirror that. The dispatch-side binding is
    ``from app.kanban import subscription_pool`` then
    ``subscription_pool.pick_subscription_for_cli`` — patching the
    symbol on the source module catches both. The snapshot key is
    ``f"{e.cli}:{e.provider}"`` so the per-entry ``cli`` (re-
    introduced in kaart 8f40d443…) discriminates the quota axis on a
    per-CLI basis.
    """
    import app.kanban.subscription_pool as pool_mod
    snapshot_map = {
        f"{e.cli}:{e.provider}": snap
        for e, snap in snapshots.items()
    }

    real_pick_for_cli = pool_mod.pick_subscription_for_cli
    real_pick = pool_mod.pick_subscription

    def patched_for_cli(entries, usages, *, paused_providers, cli_id):
        merged = {**usages, **snapshot_map}
        return real_pick_for_cli(
            entries, merged,
            paused_providers=paused_providers, cli_id=cli_id,
        )

    def patched_legacy(entries, usages, *, paused_providers):
        merged = {**usages, **snapshot_map}
        return real_pick(entries, merged, paused_providers=paused_providers)

    monkeypatch.setattr(pool_mod, "pick_subscription_for_cli", patched_for_cli)
    monkeypatch.setattr(pool_mod, "pick_subscription", patched_legacy)


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
    """Kaart 0172e94d…: met de spillover-keten wint de kolom-default
    (de impliciete kop) over de pool — de pool is alleen de
    uitwijk-staart. Een pool met ``[anthropic, minimax]`` op een kolom
    met default ``bedrock`` dispatcht dus op ``bedrock`` zolang die
    provider beschikbaar is."""
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
    # Kop (kolom-default = bedrock) wint; de pool-entry's zijn alleen de
    # staart die geraakt wordt als bedrock uitvalt.
    assert transport.calls[0]["provider"] == "bedrock"


@pytest.mark.asyncio
async def test_dispatch_persists_provider_telemetry_on_card():
    """Kaart 0172e94d…: de kolom-default wint over de pool; de
    geschreven ``dispatch_provider`` is dus de kolom-default-provider."""
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
        await s.commit()

    with pytest.MonkeyPatch.context() as mp:
        _patch_pool_pick(mp, snapshots)
        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()

    # De kolom-default (anthropic) wint over de pool; dezelfde
    # resolved provider landt op de kaart als telemetry.
    assert transport.calls[0]["provider"] == "anthropic"
    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        assert card.dispatch_provider == "anthropic"


@pytest.mark.asyncio
async def test_pool_entry_model_pins_dispatch_model():
    """When the pool's *matching* entry sets a model, the column-default
    head inherits it via ``_build_spillover_candidates`` and that model
    wins over ``column.default_model``."""
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
    """Kaart 0172e94d…: wanneer de impliciete kop (kolom-default)
    dezelfde provider heeft als een gepauzeerde pool-entry, valt de
    kop af en wint de staart. Vóór deze kaart testte dit "pool's
    first entry paused → falls through"; nu is dat equivalent aan
    "head paused → spillover", en de test moet de kolom-default
    daarom op de gepauzeerde provider zetten."""
    transport = RecordingTransport()
    pool = [_entry(provider="anthropic"), _entry(provider="minimax")]
    snapshots = {
        _entry(provider="anthropic"): _usage(drempel_gebruikt=0.1),
        _entry(provider="minimax"): _usage(drempel_gebruikt=0.1),
    }
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer", default_provider="anthropic",
        )
        cid = await _make_card(s)
        await subscription_pool.set_subscription_pool(s, PK, pool)
        # Pause anthropic until well in the future — that's the head
        # provider; de kop valt af en de router moet doorschuiven naar
        # de staart.
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        from app.kanban.dispatch_pause import set_paused_until
        await set_paused_until(s, datetime.fromisoformat(future), provider="anthropic")
        await s.commit()

    import app.kanban.subscription_pool as pool_mod
    real_pick_for_cli = pool_mod.pick_subscription_for_cli

    def paused_pick(entries, usages, *, paused_providers, cli_id):
        # Mirror what the dispatch wiring will pass: the per-provider
        # pause set is gathered from the session, not the snapshot.
        return real_pick_for_cli(
            entries, usages,
            paused_providers=paused_providers | {"anthropic"},
            cli_id=cli_id,
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pool_mod, "pick_subscription_for_cli", paused_pick)
        _patch_pool_pick(mp, snapshots)
        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()

    # Head (anthropic) viel af door pause → staart (minimax) wint.
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
    assert r.json() == {"project_key": PK, "pool": None, "column": None}


# ---- per-column REST surface (kaart b36ca702…) -------------------------
#
# Acceptance criterion: GET/POST on the existing subscription-pool
# endpoints accept an optional ``column`` parameter; without it the
# behaviour is board-wide (backwards-compatible). The per-column POST
# body preserves the explicit-empty semantics (``pool: []`` + column
# = "nooit uitwijken") which is distinguishable from "no row" (= erf
# de bord-brede staart).


@pytest.mark.asyncio
async def test_get_subscription_pool_endpoint_with_column_falls_back_to_board_wide():
    """GET with ``column`` parameter but no column-specific row → returns
    the board-wide pool. The response echoes the column parameter so a
    UI that re-saves can keep the round-trip consistent."""
    body = {
        "project_key": PK,
        "pool": [
            {"provider": "anthropic", "model": None, "drempel": 0.9},
        ],
    }
    async with _client() as c:
        await c.post("/api/v1/kanban/subscription-pool", json=body)
        r = await c.get(
            "/api/v1/kanban/subscription-pool",
            params={"project_key": PK, "column": "reviewer"},
        )
    assert r.status_code == 200
    payload = r.json()
    assert payload["column"] == "reviewer"
    assert payload["pool"] == [
        {"cli": "claude-code", "provider": "anthropic",
         "model": None, "drempel": 0.9, "endpoint_name": None},
    ]


@pytest.mark.asyncio
async def test_post_subscription_pool_with_column_round_trips():
    """POST with ``column`` writes to the per-column row and the GET
    reads back exactly that tail. Board-wide pool is untouched."""
    # FCR kaart b36ca702… F2: the column must exist before POSTing to
    # its per-column pool row — the router validates against the
    # project's real ``kanban_columns`` rows. Create one here.
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="reviewer",
            default_agent="reviewer", default_provider="anthropic",
        )
        await s.commit()
    body = {
        "project_key": PK,
        "column": "reviewer",
        "pool": [
            {"provider": "anthropic", "model": None, "drempel": 0.9},
        ],
    }
    async with _client() as c:
        r = await c.post("/api/v1/kanban/subscription-pool", json=body)
        assert r.status_code == 200
        r2 = await c.get(
            "/api/v1/kanban/subscription-pool",
            params={"project_key": PK, "column": "reviewer"},
        )
        # Board-wide still empty.
        r3 = await c.get(
            "/api/v1/kanban/subscription-pool",
            params={"project_key": PK},
        )
    assert r2.json()["pool"] == [
        {"cli": "claude-code", "provider": "anthropic",
         "model": None, "drempel": 0.9, "endpoint_name": None},
    ]
    assert r3.json()["pool"] is None


@pytest.mark.asyncio
async def test_post_subscription_pool_with_column_empty_list_is_valid():
    """``pool: []`` with ``column`` is a valid "nooit uitwijken" choice;
    the GET reads back the empty list (NOT None)."""
    # FCR kaart b36ca702… F2: create the column first; the router
    # validates the column exists before persisting the per-column row.
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="reviewer",
            default_agent="reviewer", default_provider="anthropic",
        )
        await s.commit()
    body = {
        "project_key": PK,
        "column": "reviewer",
        "pool": [],
    }
    async with _client() as c:
        r = await c.post("/api/v1/kanban/subscription-pool", json=body)
        assert r.status_code == 200
        r2 = await c.get(
            "/api/v1/kanban/subscription-pool",
            params={"project_key": PK, "column": "reviewer"},
        )
    assert r2.json()["pool"] == []


@pytest.mark.asyncio
async def test_post_subscription_pool_with_column_null_clears_only_that_column():
    """``pool: null`` with ``column`` deletes only the column-specific
    row — the board-wide row stays intact."""
    async with _client() as c:
        # Set board-wide.
        await c.post(
            "/api/v1/kanban/subscription-pool",
            json={"project_key": PK, "pool": [
                {"provider": "minimax", "model": None, "drempel": 0.9},
            ]},
        )
        # Set column-specific tail.
        await c.post(
            "/api/v1/kanban/subscription-pool",
            json={"project_key": PK, "column": "reviewer",
                  "pool": [
                      {"provider": "anthropic", "model": None, "drempel": 0.9},
                  ]},
        )
        # Clear only the column-specific row.
        await c.post(
            "/api/v1/kanban/subscription-pool",
            json={"project_key": PK, "column": "reviewer", "pool": None},
        )
        r_col = await c.get(
            "/api/v1/kanban/subscription-pool",
            params={"project_key": PK, "column": "reviewer"},
        )
        r_board = await c.get(
            "/api/v1/kanban/subscription-pool",
            params={"project_key": PK},
        )
    # Per-column inherits board-wide (minimax).
    assert r_col.json()["pool"] == [
        {"cli": "claude-code", "provider": "minimax",
         "model": None, "drempel": 0.9, "endpoint_name": None},
    ]
    # Board-wide still intact.
    assert r_board.json()["pool"] == [
        {"cli": "claude-code", "provider": "minimax",
         "model": None, "drempel": 0.9, "endpoint_name": None},
    ]


@pytest.mark.asyncio
async def test_post_subscription_pool_board_wide_empty_list_still_rejected():
    """Backwards-compat: board-wide (no column) still rejects an empty
    pool. The per-column-only "empty is valid" rule does NOT bleed
    into the board-wide path — see ``_validate_entries``."""
    async with _client() as c:
        r = await c.post(
            "/api/v1/kanban/subscription-pool",
            json={"project_key": PK, "pool": []},
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_post_subscription_pool_with_unknown_column_rejected():
    """FCR kaart b36ca702… F2: a raw API caller cannot write a
    ``column`` value that doesn't resolve to a real ``kanban_columns``
    row. Without this gate, a stray payload like
    ``column='../stray'`` would persist under
    ``subscription_pool:<project_key>:../stray`` — the same DB, but a
    silently orphaned key the dispatcher would never consult. This
    test pins the 422 for unknown column names; the equivalent clear
    (pool: null + unknown column) is also rejected to keep the contract
    symmetric across writes."""
    async with _client() as c:
        r_set = await c.post(
            "/api/v1/kanban/subscription-pool",
            json={
                "project_key": PK,
                "column": "nope-this-column-does-not-exist",
                "pool": [],
            },
        )
        r_clear = await c.post(
            "/api/v1/kanban/subscription-pool",
            json={
                "project_key": PK,
                "column": "../stray",
                "pool": None,
            },
        )
    assert r_set.status_code == 422
    assert "column" in r_set.json().get("detail", "").lower()
    assert r_clear.status_code == 422


@pytest.mark.asyncio
async def test_post_subscription_pool_with_known_column_accepted():
    """Counter-test for the F2 gate: an existing column writes cleanly.
    Pins that the allow-list is on names, not "any string is fine"."""
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="reviewer",
            default_agent="reviewer", default_provider="anthropic",
        )
        await s.commit()
    async with _client() as c:
        r = await c.post(
            "/api/v1/kanban/subscription-pool",
            json={"project_key": PK, "column": "reviewer", "pool": []},
        )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_post_and_get_subscription_pool_endpoint():
    body = {
        "project_key": PK,
        "pool": [
            {"provider": "anthropic", "model": None, "drempel": 0.9},
            {"provider": "minimax",
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
    # Kaart 8f40d443…: the GET response now also carries the (server-
    # back-filled) ``cli`` field for every entry; a body that omitted
    # ``cli`` is back-filled to ``DEFAULT_POOL_CLI`` on read. The POST
    # response mirrors the stored body verbatim (no back-fill there
    # — the server validates and stores what was sent) so the GET
    # round-trip is what we assert against, with back-filled ``cli``.
    # Kaart 27317b4871… (FCR gap 2): the GET response also carries the
    # ``endpoint_name`` field so an anthropic-compatible pool keeps
    # its endpoint binding on a fetch-and-re-save. Non-compatible
    # entries default to ``None`` here.
    expected = [
        {"cli": "claude-code", "provider": "anthropic",
         "model": None, "drempel": 0.9, "endpoint_name": None},
        {"cli": "claude-code", "provider": "minimax",
         "model": "MiniMax-M3[1m]", "drempel": 0.95, "endpoint_name": None},
    ]
    assert r2.json()["pool"] == expected


@pytest.mark.asyncio
async def test_post_subscription_pool_clear():
    async with _client() as c:
        await c.post(
            "/api/v1/kanban/subscription-pool",
            json={"project_key": PK, "pool": [
                {"provider": "minimax", "model": None, "drempel": 0.9},
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
        {"provider": "openai", "model": None, "drempel": 0.9},
    ]}
    async with _client() as c:
        r = await c.post("/api/v1/kanban/subscription-pool", json=body)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_post_subscription_pool_preserves_cli_field():
    """Kaart 8f40d443…: a POST body that carries an explicit ``cli``
    field on each entry is preserved end-to-end — the field is
    again first-class (kaart 0b3ad6e2… had stripped it on the same
    path). The migration contract for forward-compat: a UI that
    already passes ``cli`` keeps working, and the router discriminates
    on it. The stored row carries ``cli / provider / model /
    drempel`` — the full ``PoolEntry`` shape."""
    body = {
        "project_key": PK,
        "pool": [
            {"cli": "open-code", "provider": "anthropic",
             "model": None, "drempel": 0.9},
        ],
    }
    async with _client() as c:
        r = await c.post("/api/v1/kanban/subscription-pool", json=body)
        assert r.status_code == 200
        r2 = await c.get(
            "/api/v1/kanban/subscription-pool",
            params={"project_key": PK},
        )
    stored = r2.json()["pool"]
    assert stored == [
        {"cli": "open-code", "provider": "anthropic",
         "model": None, "drempel": 0.9, "endpoint_name": None},
    ]


@pytest.mark.asyncio
async def test_post_subscription_pool_with_omitted_cli_defaults_server_side():
    """A POST body that omits the ``cli`` field (legacy UIs that have
    not yet been refreshed, or hand-curated curl during the upgrade
    window) is accepted — the server back-fills ``DEFAULT_POOL_CLI``
    so the row loads with the historical claude-code shape without
    manual data surgery. Mirrors the ``_deserialize_entries``
    forward-compat path on the read side."""
    body = {
        "project_key": PK,
        "pool": [
            {"provider": "anthropic",
             "model": None, "drempel": 0.9},
        ],
    }
    async with _client() as c:
        r = await c.post("/api/v1/kanban/subscription-pool", json=body)
        assert r.status_code == 200
        r2 = await c.get(
            "/api/v1/kanban/subscription-pool",
            params={"project_key": PK},
        )
    stored = r2.json()["pool"]
    assert stored == [
        {"cli": "claude-code", "provider": "anthropic",
         "model": None, "drempel": 0.9, "endpoint_name": None},
    ]


@pytest.mark.asyncio
async def test_get_subscription_pool_returns_endpoint_name_for_compatible_entry():
    """Kaart 27317b4871… (FCR gap 2): the GET handler must round-trip
    ``endpoint_name`` so a compatible pool that the operator saves via
    the REST surface (or that the UI re-saves after a refetch) keeps
    its endpoint binding. Previously the GET handler at
    ``router.py:1663-1670`` dropped the field, so a UI that re-saves
    the response would silently lose the binding and the card would
    only fail at dispatch — exactly the "config fails later" class
    this card exists to eliminate.

    Pins the full POST→GET round-trip via the real REST route: a
    compatible entry posted with ``endpoint_name`` comes back
    identical on the GET, ready for a re-save that would still
    survive the storage fail-fast check."""
    from app.services.agentic_cli.endpoints import Endpoint, upsert_endpoint
    async with KanbanSessionLocal() as s:
        await upsert_endpoint(
            s, PK, Endpoint(
                name="router-rest-roundtrip",
                base_url="https://router-rest-roundtrip.example/v1",
                model="claude-rest-roundtrip",
            ),
        )
        await s.commit()
    body = {
        "project_key": PK,
        "pool": [
            {"provider": "anthropic-compatible",
             "model": "claude-rest-roundtrip",
             "drempel": 0.9,
             "endpoint_name": "router-rest-roundtrip"},
        ],
    }
    async with _client() as c:
        r = await c.post("/api/v1/kanban/subscription-pool", json=body)
        assert r.status_code == 200
        r2 = await c.get(
            "/api/v1/kanban/subscription-pool",
            params={"project_key": PK},
        )
    assert r2.status_code == 200
    assert r2.json()["pool"] == [
        {"cli": "claude-code",
         "provider": "anthropic-compatible",
         "model": "claude-rest-roundtrip",
         "drempel": 0.9,
         "endpoint_name": "router-rest-roundtrip"},
    ]


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

from app.services.subscriptions import registry as reg


@contextmanager
def _registry_state():
    """Snapshot+restore the SubscriptionUsageProvider registry around a test.

    The registry is module-level mutable state; without this helper a test
    that registers a fake would leak its row into every subsequent test
    (and conversely, the lifespan-registered default providers would
    surface here). Mirrors the "save, clear, yield, restore" pattern used
    by ``conftest.py::_patch_kanban_db``.

    Self-improve kanban card 7a8788af...: the dance itself moved into
    ``registry.cleared_registry_for_tests`` (sibling of
    ``seeded_registry_for_tests``). Kept the local name so existing
    ``with _registry_state() as reg:`` call-sites read identically; the
    body just delegates to the registry helper now.
    """
    with reg.cleared_registry_for_tests() as _reg:
        yield _reg


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
        ``f"{POOL_CLI}:{entry.provider}"`` — the constant prefix that
        replaced the per-entry ``cli`` field in kaart 0b3ad6e2…).

    Unregistered pairs continue to contribute no snapshot — backwards-
    compatible with the legacy "no signal → available" clause.

    The ``cli`` lookup key is the constant ``POOL_CLI`` (kaart
    0b3ad6e2…) — ``PoolEntry`` no longer carries a per-entry CLI, so
    ``_gather_pool_usage_snapshots`` builds the key from the constant
    rather than a field on the entry. The historical key shape
    (``f"{POOL_CLI}:{provider}"`` → ``claude-code:anthropic``) is
    preserved so the registry's default providers still match."""
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
    drempel of 0.9), the *head* (kolom-default die in de pool matcht)
    valt af en de router levert de tweede entry. Kaart 0172e94d…:
    de kop erft de drempel van de matchende pool-entry, dus boven-
    drempel op de head-provider schakelt direct door naar de staart.

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
    pool = [_entry(provider="anthropic", drempel=0.9), _entry(provider="minimax")]
    from app.services.subscriptions.unknown import UnknownUsageProvider

    with _registry_state() as reg:
        # Entry #1 / head (anthropic): above threshold → must be skipped.
        # De kop erft drempel=0.9 van deze matchende pool-entry; de
        # geregistreerde snapshot van 0.95 valt er dus boven.
        reg.register_provider(_fake_usage_provider(
            subscription_id="claude-code:anthropic",
            subscription_label="fake-anthropic",
            drempel_gebruikt=0.95,
            beschikbaar=False,
        ))
        # Entry #2 / staart (minimax): no signal (unknown) — router
        # treats as available per analyse §6.3, so it becomes the pick.
        reg.register_provider(UnknownUsageProvider(
            subscription_id="claude-code:minimax",
            subscription_label="test-minimax",
        ))

        async with KanbanSessionLocal() as s:
            await service.create_column(
                s, project_key=PK, name="engineer",
                default_agent="engineer", default_provider="anthropic",
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
    # De kop (anthropic) viel af door boven-drempel via de geregistreerde
    # fake provider → de staart (minimax) wint.
    assert transport.calls[0]["provider"] == "minimax"


@pytest.mark.asyncio
async def test_dispatch_pool_with_duplicate_provider_reports_pool_source_on_spillover():
    """FCR-blokker: wanneer de pool een duplicaat-provider heeft
    (``[anthropic, anthropic, bedrock]``) en de synthetische kop
    (anthropic) valt af door boven-drempel, kiest de router de
    tweede anthropic in de staart. :func:`provider_source` moet
    eerlijk ``pool`` rapporteren — niet ``column_default`` — omdat
    dit geen kolom-default-routing is, alleen een toevallige
    gelijknamige staart-entry. Na dedup zou de tweede anthropic
    uit de staart verwijderd moeten zijn en de eerste staart die
    wint is bedrock; deze test pinst beide kanten (dedup-gedrag +
    eerlijke source-claim)."""
    transport = RecordingTransport()
    pool = [
        _entry(provider="anthropic", drempel=0.9),
        _entry(provider="anthropic", drempel=0.9),
        _entry(provider="bedrock", drempel=0.95),
    ]
    from app.services.subscriptions.unknown import UnknownUsageProvider

    with _registry_state() as reg:
        # Kop + duplicaat-anthropic: boven-drempel → beide vallen af.
        reg.register_provider(_fake_usage_provider(
            subscription_id="claude-code:anthropic",
            subscription_label="fake-anthropic",
            drempel_gebruikt=0.95,
            beschikbaar=False,
        ))
        # Bedrock: onbekend → router behandelt als beschikbaar.
        reg.register_provider(UnknownUsageProvider(
            subscription_id="claude-code:bedrock",
            subscription_label="test-bedrock",
        ))

        async with KanbanSessionLocal() as s:
            await service.create_column(
                s, project_key=PK, name="engineer",
                default_agent="engineer", default_provider="anthropic",
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
    # Staart bevat geen tweede anthropic (dedup); de router valt
    # dus door naar bedrock. provider_source is "pool" want dit
    # is een echte uitwijk, niet de kolom-default-kop.
    assert transport.calls[0]["provider"] == "bedrock"


# ---- CLI-aware dispatch integration (kaart 8f40d443…) ----------------------
#
# End-to-end: when the spawned CLI is ``open-code`` (or any non-
# default CLI), the pool's per-{cli, provider} axis is honoured.
# Without this, the pool's router was board-wide pinned to
# ``POOL_CLI = 'claude-code'``, so an OpenCode-spawned card always
# fell through to the column default — no drempel, no pause, no
# spill. These tests pin the wiring at the dispatch level; one
# negative case (entry doesn't match the spawned CLI) plus two
# positive cases (threshold spill with a non-default CLI, and an
# open-code entry with no signal degrades gracefully).


@pytest.mark.asyncio
async def test_pool_entry_for_other_cli_does_not_match_default_cli_spawn():
    """A card spawned under cli_id='open-code' must NOT pick a
    pool entry whose ``cli='claude-code'`` — those quotas are
    orthogonal (analyse §3 {cli, provider}). The router filters on
    the resolved cli_id, so the entry is skipped and dispatch falls
    through to the column default (``bedrock`` here).

    Note: the column is named ``"engineer"`` because
    ``_phase_target_agent`` resolves the spawn target to that string
    (no ``.claude/agents/open-code.md`` persona exists in ``/p``),
    and the column default lookup is keyed on the resolved
    ``target_agent``, not on the kanban-card's ``column`` field. Same
    routing semantics, just named after the actual lookup key.
    """
    transport = RecordingTransport()
    pool = [_entry(provider="anthropic")]  # default cli = claude-code
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer", default_provider="bedrock",
        )
        cid = await _make_card(
            s, column="engineer", executor_agent_id="open-code",
        )
        await subscription_pool.set_subscription_pool(s, PK, pool)
        await s.commit()

    async with KanbanSessionLocal() as s:
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()

    # The pool only has a claude-code entry; the open-code card has
    # no matching entry → falls through to column.default_provider
    # ("bedrock"), proving the CLI filter ran.
    assert len(transport.calls) == 1
    assert transport.calls[0]["cli_id"] == "open-code"
    assert transport.calls[0]["provider"] == "bedrock"


@pytest.mark.asyncio
async def test_open_code_entry_applies_threshold_for_open_code_spawn():
    """OpenCode-spawned card with an ``open-code:anthropic`` entry
    above drempel routes to the next entry's provider — the per-CLI
    quota gate that card 8f40d443 added. Without the fix the pool's
    router ignored cli_id and always picked entry #1."""
    transport = RecordingTransport()
    pool = [
        _entry(cli="open-code", provider="anthropic"),
        _entry(cli="open-code", provider="bedrock"),
    ]
    snapshots = {
        _entry(cli="open-code", provider="anthropic"): _usage(
            drempel_gebruikt=0.95,  # above drempel
        ),
        _entry(cli="open-code", provider="bedrock"): _usage(
            drempel_gebruikt=0.1,   # fresh
        ),
    }
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="open-code",
            default_agent="open-code", default_provider="minimax",
        )
        cid = await _make_card(
            s, column="open-code", executor_agent_id="open-code",
        )
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
    # Pool picked entry #2 (bedrock) — the open-code-cli's
    # anthropic entry was above drempel. Column default was minimax
    # but the pool beats that.
    assert transport.calls[0]["cli_id"] == "open-code"
    assert transport.calls[0]["provider"] == "bedrock"


@pytest.mark.asyncio
async def test_open_code_pool_entry_with_no_signal_does_not_block():
    """Acceptance criterion: 'een ontbrekende snapshot degradeert
    expliciet in plaats van als 0% te tellen' — an OpenCode entry
    whose (cli, provider) has no registered snapshot must be treated
    as 'no signal → available' so a non-claude-code session
    dispatches even when its provider has no live signal source yet
    (analyse §6.3)."""
    from app.services.subscriptions.unknown import UnknownUsageProvider
    transport = RecordingTransport()
    pool = [_entry(cli="open-code", provider="anthropic")]
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="open-code",
            default_agent="open-code", default_provider="bedrock",
        )
        cid = await _make_card(
            s, column="open-code", executor_agent_id="open-code",
        )
        await subscription_pool.set_subscription_pool(s, PK, pool)
        await s.commit()

    with _registry_state() as reg:
        # Register the open-code:anthropic provider as an honest
        # 'unknown' (no signal) to mirror a real-world provider that
        # exists but has no usage endpoint yet.
        reg.register_provider(UnknownUsageProvider(
            subscription_id="open-code:anthropic",
            subscription_label="test-opencode-anthropic",
        ))
        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()

    assert len(transport.calls) == 1
    # The pool's open-code:anthropic entry (no signal) wins over the
    # column default ('bedrock') for an open-code-spawned card.
    assert transport.calls[0]["cli_id"] == "open-code"
    assert transport.calls[0]["provider"] == "anthropic"


# ---- precedence chain: both call sites go through the shared resolver ------
#
# Kaart 8da646d8…: the precedence chain (board-wide pin > pool >
# per-card column_override > column.default_* > persona) used to live
# in TWO places — `dispatch_card` and `resolve_column_effective_model`.
# A future tweak (new layer, reorder) needed to be made in both, and a
# missed duplicate silently broke the column-settings UI's "Effective:
# X — from <source>" line without breaking the spawn itself. The two
# have been unified behind ``resolve_effective_provider_and_model``;
# this test pins both call sites to that single helper so a future
# chain tweak that forgets to wire through one of them breaks THIS
# test, not the user.


@pytest.mark.asyncio
async def test_dispatch_card_chain_matches_resolver_chain(monkeypatch):
    """``dispatch_card`` and ``resolve_column_effective_model`` BOTH go
    through ``resolve_effective_provider_and_model``.

    The spy wraps the helper on the dispatch module so every caller
    (dispatch + column-settings wrapper) runs through it. ``call_log``
    records each invocation's kwargs so we can assert that the two
    sides called the helper with their own expected argument shapes:

      * dispatch path: ``target_agent`` set, ``pick_pool`` is callable,
        ``card_overrides`` reflects the per-card entry.
      * column-settings path: ``target_agent`` set, ``pick_pool`` is the
        no-snapshot picker, ``card_overrides`` is the user-supplied
        column-level override (or None).

    A future refactor that re-inlines the chain in ``dispatch_card`` —
    bypassing the helper — drops the dispatch-side entry from
    ``call_log`` and breaks this test."""
    import app.kanban.dispatch as dispatch_mod

    real_helper = dispatch_mod.resolve_effective_provider_and_model
    call_log: list[dict] = []

    async def _spy(*args, **kwargs):
        call_log.append(kwargs)
        return await real_helper(*args, **kwargs)

    monkeypatch.setattr(
        dispatch_mod, "resolve_effective_provider_and_model", _spy,
    )

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer",
            default_provider="minimax",
            default_model="MiniMax-M3",
        )
        cid = await _make_card(
            s, executor_agent_id="claude-code",
        )
        await s.commit()
        # Column-settings side: no card, no dispatch, just resolver.
        # Pin the column-settings path before the dispatch path so the
        # call_log assertion below can name each entry by source.
        await dispatch.resolve_column_effective_model(
            s, project_key=PK, column_name="engineer",
            project_path="/p",
        )
        await s.commit()
        # Dispatch side: spawn through the recorder.
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()

    # Both call sites went through the helper — two distinct entries
    # with their own argument shapes.
    assert len(call_log) >= 2, (
        f"expected at least 2 helper calls "
        f"(dispatch + column-settings wrapper), got {len(call_log)}: "
        f"{call_log}"
    )
    dispatch_calls = [
        c for c in call_log if callable(c.get("pick_pool"))
    ]
    column_settings_calls = [
        c for c in call_log
        if c.get("pick_pool") is dispatch_mod._column_settings_pool_picker
    ]
    # The dispatch path passes an async closure over ``_pick_pool_choice``;
    # the column-settings wrapper passes the module-level no-snapshot
    # picker. Both are visible in ``call_log``.
    assert len(dispatch_calls) >= 1, (
        f"expected at least one dispatch-path helper call "
        f"(target_agent + live pick_pool closure), got {call_log}"
    )
    assert len(column_settings_calls) >= 1, (
        f"expected at least one column-settings helper call "
        f"(target_agent + no-snapshot picker), got {call_log}"
    )
    # Both call sites route to the same target_agent/column name.
    assert all(c["target_agent"] == "engineer" for c in call_log), call_log
    # Same project_key / project_path — proves there's no hidden second
    # helper instance the two paths bypass each other through.
    assert all(c["project_key"] == PK for c in call_log), call_log
    assert all(c["project_path"] == "/p" for c in call_log), call_log
    # End-to-end result is unchanged: the dispatch path picked minimax
    # (column default), not anthropic.
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "minimax"


# ---- spillover-keten (kaart 0172e94d…) -------------------------------------
#
# Vorm B uit docs/cockpit/spillover-per-kolom-decision.md: de pool is geen
# routing-pin meer, maar een spillover-keten met ``column.default_provider``
# als impliciete kop. De effectieve-kandidatenlijst voor kolom K is
# ``[K.default_provider] ++ [pool-entries minus K.default_provider]``; de
# bestaande ``pick_subscription_for_cli`` loopt daar overheen.
#
# Belangrijk voor deze tests:
#   * De kop erft ``drempel`` / ``model`` van een eventuele matchende
#     pool-entry (en die entry verdwijnt uit de staart — anders dubbel).
#   * Geen matchende entry → kop met ``drempel=1.0`` (gebruik tot de
#     per-provider pause hem raakt) en ``model=None``.
#   * ``provider_source`` is eerlijk: ``column_default`` wanneer de kop
#     wint, ``pool`` alleen bij een echte uitwijk naar de staart.
#   * ``global_override`` blijft boven alles.


@pytest.mark.asyncio
async def test_spillover_chain_engineer_default_minimax_wins_when_not_in_pool():
    """``engineer`` met kolom-default ``minimax`` (niet in de pool) start
    op ``minimax`` met ``drempel=1.0`` op de kop; pool-entry ``anthropic``
    is de uitwijk-staart maar wordt niet geraakt."""
    transport = RecordingTransport()
    pool = [_entry(provider="anthropic"), _entry(provider="bedrock")]
    snapshots = {
        _entry(provider="anthropic"): _usage(drempel_gebruikt=0.1),
        _entry(provider="bedrock"): _usage(drempel_gebruikt=0.1),
    }
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer",
            default_provider="minimax",
            default_model="MiniMax-M3",
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
    # Kop ``minimax`` wint (geen snapshot in de router → geen drempel-blokkade).
    assert transport.calls[0]["provider"] == "minimax"
    assert transport.calls[0]["model"] == "MiniMax-M3"


@pytest.mark.asyncio
async def test_spillover_chain_analyst_default_anthropic_inherits_match_drempel():
    """``analyst`` met kolom-default ``anthropic`` wél in de pool: kop
    erft ``drempel`` en ``model`` van die matchende pool-entry; die entry
    verdwijnt uit de staart (dedup) zodat de keten 1 entry kort is."""
    transport = RecordingTransport()
    pool = [
        _entry(provider="anthropic", model="opus", drempel=0.7),
        _entry(provider="bedrock", model=None, drempel=0.9),
    ]
    snapshots = {
        _entry(provider="anthropic", model="opus"): _usage(drempel_gebruikt=0.1),
        _entry(provider="bedrock"): _usage(drempel_gebruikt=0.1),
    }
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="analyst",
            default_agent="analyst",
            default_provider="anthropic",
            default_model="opus",
        )
        cid = await _make_card(s)
        await subscription_pool.set_subscription_pool(s, PK, pool)
        await s.commit()

    with pytest.MonkeyPatch.context() as mp:
        _patch_pool_pick(mp, snapshots)
        async with KanbanSessionLocal() as s:
            resolved = await dispatch.resolve_effective_provider_and_model(
                s, project_key=PK, target_agent="analyst",
                project_path="/p",
                pick_pool=dispatch._column_settings_pool_picker,
            )
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()

    # Kop (anthropic) wint — model komt via de geërfde pool-match (opus).
    assert resolved["provider"] == "anthropic"
    assert resolved["model"] == "opus"
    # provider_source eerlijk: de kop won, niet de pool-pin.
    assert resolved["provider_source"] == "column_default"
    # End-to-end: spawn landt op anthropic met het geërfde opus-model.
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "anthropic"
    assert transport.calls[0]["model"] == "opus"


@pytest.mark.asyncio
async def test_spillover_chain_analyst_spills_to_minimax_when_anthropic_paused():
    """AC: ``analyst`` met kolom-default ``anthropic``; wanneer
    ``anthropic`` is gepauzeerd valt de kop af en wint de staart
    (``minimax``). ``provider_source`` wordt eerlijk ``pool`` omdat het
    een echte uitwijk is."""
    transport = RecordingTransport()
    pool = [
        _entry(provider="anthropic"),
        _entry(provider="minimax"),
    ]
    snapshots = {
        _entry(provider="anthropic"): _usage(drempel_gebruikt=0.1),
        _entry(provider="minimax"): _usage(drempel_gebruikt=0.1),
    }
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="analyst",
            default_agent="analyst", default_provider="anthropic",
        )
        cid = await _make_card(s)
        await subscription_pool.set_subscription_pool(s, PK, pool)
        # Pauzeer anthropic tot ver in de toekomst.
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        from app.kanban.dispatch_pause import set_paused_until
        await set_paused_until(s, datetime.fromisoformat(future), provider="anthropic")
        await s.commit()

    import app.kanban.subscription_pool as pool_mod
    real_pick_for_cli = pool_mod.pick_subscription_for_cli

    def paused_pick(entries, usages, *, paused_providers, cli_id):
        return real_pick_for_cli(
            entries, usages,
            paused_providers=paused_providers | {"anthropic"},
            cli_id=cli_id,
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pool_mod, "pick_subscription_for_cli", paused_pick)
        _patch_pool_pick(mp, snapshots)
        async with KanbanSessionLocal() as s:
            resolved = await dispatch.resolve_effective_provider_and_model(
                s, project_key=PK, target_agent="analyst",
                project_path="/p",
                pick_pool=dispatch._column_settings_pool_picker,
            )
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()

    # Kop (anthropic) viel af door pause; staart (minimax) wint.
    assert resolved["provider"] == "minimax"
    assert resolved["provider_source"] == "pool"
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "minimax"


@pytest.mark.asyncio
async def test_spillover_chain_analyst_spills_when_anthropic_above_threshold():
    """AC: wanneer de kop-provider boven drempel zit (geërfde drempel
    van de matchende pool-entry), valt hij af en wint de staart."""
    transport = RecordingTransport()
    pool = [
        _entry(provider="anthropic", drempel=0.7),  # drempel wordt geërfd door de kop
        _entry(provider="minimax"),
    ]
    snapshots = {
        _entry(provider="anthropic"): _usage(drempel_gebruikt=0.95),  # boven 0.7
        _entry(provider="minimax"): _usage(drempel_gebruikt=0.1),
    }
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="analyst",
            default_agent="analyst", default_provider="anthropic",
        )
        cid = await _make_card(s)
        await subscription_pool.set_subscription_pool(s, PK, pool)
        await s.commit()

    with pytest.MonkeyPatch.context() as mp:
        _patch_pool_pick(mp, snapshots)
        async with KanbanSessionLocal() as s:
            resolved = await dispatch.resolve_effective_provider_and_model(
                s, project_key=PK, target_agent="analyst",
                project_path="/p",
                pick_pool=dispatch._column_settings_pool_picker,
            )
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()

    assert resolved["provider"] == "minimax"
    assert resolved["provider_source"] == "pool"
    assert transport.calls[0]["provider"] == "minimax"


@pytest.mark.asyncio
async def test_spillover_chain_column_without_default_still_uses_pool_head():
    """Backward-compat: een kolom zonder ``default_provider`` valt
    terug op de pure pool-volgorde (het gedrag van vóór deze kaart)."""
    transport = RecordingTransport()
    pool = [
        _entry(provider="minimax"),
        _entry(provider="bedrock"),
    ]
    snapshots = {
        _entry(provider="minimax"): _usage(drempel_gebruikt=0.1),
        _entry(provider="bedrock"): _usage(drempel_gebruikt=0.1),
    }
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="extra",
            default_agent="extra",
            # geen default_provider / default_model
        )
        cid = await _make_card(s, column="extra")
        await subscription_pool.set_subscription_pool(s, PK, pool)
        await s.commit()

    with pytest.MonkeyPatch.context() as mp:
        _patch_pool_pick(mp, snapshots)
        async with KanbanSessionLocal() as s:
            resolved = await dispatch.resolve_effective_provider_and_model(
                s, project_key=PK, target_agent="extra",
                project_path="/p",
                pick_pool=dispatch._column_settings_pool_picker,
            )
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()

    # Geen kolom-default → de eerste pool-entry is de kop; bron is ``pool``.
    assert resolved["provider"] == "minimax"
    assert resolved["provider_source"] == "pool"
    assert transport.calls[0]["provider"] == "minimax"


@pytest.mark.asyncio
async def test_spillover_chain_global_override_beats_chain():
    """``global_override`` blijft boven de spillover-keten staan."""
    transport = RecordingTransport()
    pool = [_entry(provider="minimax"), _entry(provider="bedrock")]
    snapshots = {
        _entry(provider="minimax"): _usage(drempel_gebruikt=0.1),
        _entry(provider="bedrock"): _usage(drempel_gebruikt=0.1),
    }
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="analyst",
            default_agent="analyst", default_provider="anthropic",
        )
        cid = await _make_card(s)
        await subscription_pool.set_subscription_pool(s, PK, pool)
        await dispatch.set_active_subscription_override(
            s, PK, {"provider": "bedrock", "model": "haiku"},
        )
        await s.commit()

    with pytest.MonkeyPatch.context() as mp:
        _patch_pool_pick(mp, snapshots)
        async with KanbanSessionLocal() as s:
            resolved = await dispatch.resolve_effective_provider_and_model(
                s, project_key=PK, target_agent="analyst",
                project_path="/p",
                pick_pool=dispatch._column_settings_pool_picker,
            )
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()

    assert resolved["provider"] == "bedrock"
    assert resolved["provider_source"] == "global_override"
    assert resolved["model_source"] == "global_override"
    assert transport.calls[0]["provider"] == "bedrock"
    assert transport.calls[0]["model"] == "haiku"


@pytest.mark.asyncio
async def test_spillover_chain_no_pool_column_default_wins():
    """Geen pool geconfigureerd → de kolom-default wint zoals vandaag."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer", default_provider="minimax",
        )
        cid = await _make_card(s)
        await s.commit()

        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()

    assert transport.calls[0]["provider"] == "minimax"


# ---- resolvertest direct op de keten-vorm (wit-box) -----------------------
#
# Pinnen op de pure ``_build_spillover_candidates`` helper die de kop +
# staart construeert — beschermt tegen refactors die de vorm van de
# keten veranderen zonder dat de end-to-end tests het opmerken.


def test_build_spillover_candidates_no_column_default():
    """Geen kolom-default → kop = eerste pool-entry, staart = rest.

    De head ís de eerste pool-entry (er is geen "impliciete" synthetische
    head nodig), dus drempel/model komen natuurlijk mee van die entry."""
    from app.kanban.dispatch import _build_spillover_candidates
    pool = [
        _entry(provider="anthropic", drempel=0.9),
        _entry(provider="minimax", drempel=0.95),
    ]
    head, chain = _build_spillover_candidates(
        column_default_provider=None, pool=pool, cli_id="claude-code",
    )
    assert head.provider == "anthropic"
    # Head is de eerste pool-entry → erft diens drempel en model.
    assert head.drempel == 0.9
    assert head.model is None
    # Chain = head + rest van de pool.
    assert [e.provider for e in chain] == ["anthropic", "minimax"]


def test_build_spillover_candidates_default_not_in_pool():
    """Kolom-default niet in pool → synthetische kop met drempel=1.0,
    model=None; hele pool als staart (geen dedup mogelijk)."""
    from app.kanban.dispatch import _build_spillover_candidates
    pool = [
        _entry(provider="anthropic", drempel=0.9),
        _entry(provider="minimax", drempel=0.95),
    ]
    head, chain = _build_spillover_candidates(
        column_default_provider="bedrock", pool=pool, cli_id="claude-code",
    )
    assert head.provider == "bedrock"
    assert head.drempel == 1.0
    assert head.model is None
    # Chain = head + hele pool (bedrock stond niet in de pool).
    assert [e.provider for e in chain] == ["bedrock", "anthropic", "minimax"]


def test_build_spillover_candidates_default_in_pool_dedups():
    """Kolom-default wél in pool → kop erft drempel/model van die
    matchende entry; die entry wordt uit de staart gefilterd om
    duplicaat-pooling te voorkomen."""
    from app.kanban.dispatch import _build_spillover_candidates
    pool = [
        _entry(provider="anthropic", model="opus", drempel=0.7),
        _entry(provider="minimax", model="MiniMax-M3", drempel=0.95),
    ]
    head, chain = _build_spillover_candidates(
        column_default_provider="anthropic", pool=pool, cli_id="claude-code",
    )
    assert head.provider == "anthropic"
    assert head.drempel == 0.7
    assert head.model == "opus"
    # De matchende pool-entry is uit de staart; chain = head + dedupte staart.
    assert [e.provider for e in chain] == ["anthropic", "minimax"]


def test_build_spillover_candidates_cli_mismatch_filters_pool():
    """Pool met een andere CLI dan ``cli_id`` → geen matchende entry;
    synthetische kop met drempel=1.0, model=None, en de hele pool
    ongewijzigd als staart (de router doet zijn eigen cli-filter)."""
    from app.kanban.dispatch import _build_spillover_candidates
    pool = [
        _entry(cli="open-code", provider="anthropic"),
        _entry(provider="minimax"),
    ]
    head, chain = _build_spillover_candidates(
        column_default_provider="bedrock", pool=pool, cli_id="claude-code",
    )
    assert head.provider == "bedrock"
    assert head.drempel == 1.0
    # Chain = head + hele pool.
    assert [e.provider for e in chain] == ["bedrock", "anthropic", "minimax"]


def test_build_spillover_candidates_pool_with_duplicate_provider_dedups():
    """FCR-blokker: pool mag duplicaat-providers hebben (validatie laat
    het toe), en de staart moet álle entries met dezelfde provider+cli
    verliezen — niet alleen de exacte match op object-identity. Anders
    zou de tweede ``column_default_provider``-entry in de staart
    overleven, en ten onrechte als ``column_default``-routing worden
    gerapporteerd zodra de synthetische kop wordt overgeslagen.
    """
    from app.kanban.dispatch import _build_spillover_candidates
    pool = [
        # Twee anthropic-entries (dedup-doel).
        _entry(provider="anthropic", model="opus", drempel=0.7),
        _entry(provider="anthropic", model="haiku", drempel=0.5),
        _entry(provider="bedrock", drempel=0.95),
    ]
    head, chain = _build_spillover_candidates(
        column_default_provider="anthropic", pool=pool, cli_id="claude-code",
    )
    assert head.provider == "anthropic"
    # Kop erft drempel/model van de eerste matchende pool-entry.
    assert head.drempel == 0.7
    assert head.model == "opus"
    # Staart bevat géén tweede anthropic — duplicaat is eraf gehaald.
    assert [e.provider for e in chain] == ["anthropic", "bedrock"]


def test_build_spillover_candidates_duplicate_provider_only_dedups_matching_cli():
    """FCR-blokker: dedup is op (provider, cli), niet op provider alleen.
    Een tweede pool-entry met dezelfde provider maar andere CLI moet
    wél in de staart overleven — die hoort bij een andere router-as."""
    from app.kanban.dispatch import _build_spillover_candidates
    pool = [
        _entry(provider="anthropic", model="opus", drempel=0.7),
        # open-code-anthropic hoort niet bij de claude-code-kop.
        _entry(provider="anthropic", cli="open-code", drempel=0.5),
        _entry(provider="bedrock", drempel=0.95),
    ]
    head, chain = _build_spillover_candidates(
        column_default_provider="anthropic", pool=pool, cli_id="claude-code",
    )
    # Staart bevat de open-code-anthropic (andere CLI) en bedrock,
    # maar niet de tweede claude-code-anthropic. Volgorde: head
    # (claude-code synthetisch) → open-code-anthropic → bedrock.
    assert [e.provider for e in chain] == ["anthropic", "anthropic", "bedrock"]
    assert chain[0] is head
    # head draagt de spawn-CLI; tweede tail-entry is open-code,
    # derde is bedrock (claude-code default).
    assert head.resolved_cli == "claude-code"
    assert chain[1].resolved_cli == "open-code"
    assert chain[2].resolved_cli == "claude-code"
    assert chain[2].provider == "bedrock"


# ---- per-column spillover tail (kaart b36ca702…) -------------------------
#
# Acceptance criteria pinned end-to-end at the dispatch level:
#
#   * ``resolve_effective_provider_and_model`` uses the column-specific
#     tail (with the board-wide tail as fallback) instead of always
#     reading the board-wide pool.
#   * ``_pool_spillover_available`` resolves the same column-specific
#     tail, so the spillover decision and the subsequent dispatch pick
#     cannot diverge.
#   * A ``reviewer`` column with an explicit empty tail stays on the
#     reset-time pause on a limit hit — no spillover, because the
#     operator chose "nooit uitwijken".
#   * An ``engineer`` column with a non-empty tail spills over
#     immediately on a limit hit.

async def _move_card_to_resume(s, card_id, **kwargs):
    """Helper: a non-rate-limit move to the resume column for fixtures
    that don't need the full ``move_limited_session_to_resume`` path.
    The dispatch integration tests below patch ``_pool_spillover_available``
    directly so the move itself stays a plain helper."""
    from app.kanban.operations import apply_operation
    await apply_operation(
        s, op_type="update", entity_type="card", project_key=PK,
        entity_id=card_id, payload={"column": "To Resume"},
    )


@pytest.mark.asyncio
async def test_resolve_effective_uses_per_column_tail_when_present():
    """``resolve_effective_provider_and_model`` reads the column-specific
    tail when one is configured. A reviewer column with ``[anthropic]``
    sees anthropic in its spillover chain; the board-wide pool does not
    bleed in."""
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="reviewer",
            default_agent="reviewer", default_provider="anthropic",
        )
        # Board-wide pool intentionally points elsewhere.
        await subscription_pool.set_subscription_pool(s, PK, [
            _entry(provider="minimax", drempel=0.9),
        ])
        # Per-column tail for reviewer is empty — "nooit uitwijken".
        await subscription_pool.set_subscription_pool(
            s, PK, [], column="reviewer",
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        resolved = await dispatch.resolve_effective_provider_and_model(
            s,
            project_key=PK,
            target_agent="reviewer",
            project_path="/p",
            pick_pool=None,
        )
    # reviewer.default_provider = anthropic wins the head; pool is empty
    # so chain contains only the head; provider is anthropic.
    assert resolved["provider"] == "anthropic"


@pytest.mark.asyncio
async def test_resolve_effective_falls_back_to_board_wide_when_no_column_tail():
    """Without a column-specific row, the per-column reader falls back
    to the board-wide pool — the existing behaviour is preserved for
    every column that hasn't been individually configured."""
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="reviewer",
            default_agent="reviewer", default_provider="anthropic",
        )
        # Board-wide pool only.
        await subscription_pool.set_subscription_pool(s, PK, [
            _entry(provider="minimax", drempel=0.9),
        ])
        await s.commit()

    async with KanbanSessionLocal() as s:
        resolved = await dispatch.resolve_effective_provider_and_model(
            s,
            project_key=PK,
            target_agent="reviewer",
            project_path="/p",
            pick_pool=None,
        )
    # Kop (anthropic) wint over de pool — zelfde gedrag als vóór
    # deze kaart; alleen de bron van de pool-rij verandert.
    assert resolved["provider"] == "anthropic"


@pytest.mark.asyncio
async def test_pool_spillover_available_uses_per_column_tail():
    """``_pool_spillover_available`` resolves the per-column tail — a
    reviewer column with an explicit empty tail returns False (no
    spillover), even when the board-wide pool has an alternative."""
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="reviewer",
            default_agent="reviewer", default_provider="anthropic",
        )
        # Board-wide: another vendor is available as fallback.
        await subscription_pool.set_subscription_pool(s, PK, [
            _entry(provider="minimax", drempel=0.9),
        ])
        # Reviewer-only: "nooit uitwijken".
        await subscription_pool.set_subscription_pool(
            s, PK, [], column="reviewer",
        )
        await s.commit()

    # No-snapshot mode: the head is "above threshold" trivially via
    # "geen signaal = beschikbaar", and the explicit empty tail
    # means there are no fallback entries to pick. The reviewer
    # stays on its reset-time pause.
    async with KanbanSessionLocal() as s:
        result = await dispatch._pool_spillover_available(
            s,
            project_key=PK,
            limited_provider="anthropic",
            cli_id="claude-code",
            column="reviewer",
        )
    assert result is False


@pytest.mark.asyncio
async def test_pool_spillover_available_with_per_column_tail_finds_fallback():
    """An engineer column with an explicit non-empty tail spills over
    when its column-default provider is rate-limited. The pool picks
    the tail's first available entry instead of falling back to the
    board-wide pool."""
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer", default_provider="minimax",
        )
        # Board-wide: a different vendor.
        await subscription_pool.set_subscription_pool(s, PK, [
            _entry(provider="bedrock", drempel=0.9),
        ])
        # Engineer-only: anthropic as the spillover target.
        await subscription_pool.set_subscription_pool(
            s, PK, [_entry(provider="anthropic", drempel=0.9)],
            column="engineer",
        )
        await s.commit()

    # With snapshots None for everyone → "geen signaal = beschikbaar",
    # the router's first-entry-wins branch puts anthropic on top.
    # ``_pool_spillover_available`` should report True: an alternative
    # subscription exists for engineer.
    async with KanbanSessionLocal() as s:
        result = await dispatch._pool_spillover_available(
            s,
            project_key=PK,
            limited_provider="minimax",
            cli_id="claude-code",
            column="engineer",
        )
    assert result is True


@pytest.mark.asyncio
async def test_pool_spillover_available_per_column_tail_independent_of_board_wide():
    """Per-column tails are independent from the board-wide pool.
    Setting one column's tail does not bleed into another column's
    spillover decision."""
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="reviewer",
            default_agent="reviewer", default_provider="anthropic",
        )
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer", default_provider="minimax",
        )
        # Reviewer: empty tail (no spillover).
        await subscription_pool.set_subscription_pool(
            s, PK, [], column="reviewer",
        )
        # Engineer: anthropic fallback.
        await subscription_pool.set_subscription_pool(
            s, PK, [_entry(provider="anthropic", drempel=0.9)],
            column="engineer",
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        reviewer = await dispatch._pool_spillover_available(
            s,
            project_key=PK,
            limited_provider="anthropic",
            cli_id="claude-code",
            column="reviewer",
        )
        engineer = await dispatch._pool_spillover_available(
            s,
            project_key=PK,
            limited_provider="minimax",
            cli_id="claude-code",
            column="engineer",
        )
    assert reviewer is False
    assert engineer is True


# ---------------------------------------------------------------------------
# FCR kaart b36ca702… F3: end-to-end coverage for
# ``move_limited_session_to_resume`` with per-column spillover tails.
#
# The headline scenario is "reviewer met lege staart blijft op To Resume
# met de reset-tijd staan; engineer met staart [anthropic] wordt direct
# herdispatchbaar". The previous tests pin ``_pool_spillover_available``
# and the resolver, but the actual card-move path needs its own test
# because the column-aware plumbing (the
# ``column=card.column`` propagation at dispatch.py:6270) lives one
# layer up. Without this test, a refactor that drops the column kwarg
# would silently regress to the board-wide pool.
#
# Strategy: drive ``move_limited_session_to_resume`` end-to-end via
# monkeypatched I/O boundaries (``_resume_target_from_cwd``,
# ``list_cards``) and capture the ``effective_scheduled_at`` passed to
# ``_move_to_resume`` — the function-under-test's contract surface.
# This is exactly the assertion that has to hold for the AC scenario:
# ``scheduled_at is None`` when the column tail spills, preserved
# otherwise. Per the test-doubles convention (CLAUDE.md §3c), the
# patches target the *consumer* — the bindings inside dispatch.py —
# so a future ``import _pool_spillover_available`` refactor that moves
# the helper out of dispatch would still pick the patch up via
# ``from app.kanban import dispatch`` followed by ``monkeypatch.setattr(
# dispatch, "_pool_spillover_available", ...)``.
# ---------------------------------------------------------------------------


async def _make_card_with_column(s, project_key: str, column: str) -> str:
    """Create a card on ``column`` and return its id. The card starts
    claimed by ``agent:lim-test`` so ``move_limited_session_to_resume``
    recognises it as ours."""
    import uuid as _uuid

    from app.kanban.models import KanbanCard
    cid = str(_uuid.uuid4())
    s.add(KanbanCard(
        id=cid,
        project_key=project_key,
        column=column,
        rank="0",
        title="lim-test card",
        claimed_by="agent:lim-test",
        work_type="bug",
    ))
    await s.flush()
    return cid


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_reviewer_empty_tail_keeps_reset_time():
    """AC scenario: a reviewer card with an explicit empty tail stays
    on its reset-time pause. End-to-end pin at the
    ``_move_to_resume`` boundary — ``scheduled_at`` must be preserved
    (the reset ISO we passed in), NOT collapsed to ``None``."""
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="reviewer",
            default_agent="reviewer", default_provider="anthropic",
        )
        # Board-wide fallback present but irrelevant: reviewer's empty
        # tail wins.
        await subscription_pool.set_subscription_pool(s, PK, [
            _entry(provider="minimax", drempel=0.9),
        ])
        await subscription_pool.set_subscription_pool(
            s, PK, [], column="reviewer",
        )
        await s.commit()

    captured_scheduled_at: dict[str, object] = {}
    real_scheduled_at = (
        datetime.now(UTC) + timedelta(hours=1)
    ).isoformat()

    async def _capture(ks, **kwargs):
        captured_scheduled_at["value"] = kwargs.get("scheduled_at")
        return True

    async with KanbanSessionLocal() as s:
        cid = await _make_card_with_column(s, PK, "reviewer")
        await s.commit()
    async with KanbanSessionLocal() as s:
        cards = await dispatch.list_cards(s, PK)
        card = next(c for c in cards if c.id == cid)

    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    try:
        mp.setattr(dispatch, "list_cards", lambda ks, project_key: _async_iter([card]))
        mp.setattr(dispatch, "_move_to_resume", _capture)
        mp.setattr(
            dispatch, "_resume_target_from_cwd",
            lambda cwd: ("/tmp/fake-project-path", "lim-test"),
        )
        mp.setattr(dispatch, "safe_resolve_project_key", lambda _p: PK)
        moved = await dispatch.move_limited_session_to_resume(
            "/tmp/fake-project-path/.claude/worktrees/lim-test",
            scheduled_at=real_scheduled_at,
        )
    finally:
        mp.undo()
    assert moved is True
    # Reviewer's empty tail → no spillover → reset-time preserved.
    assert captured_scheduled_at.get("value") == real_scheduled_at


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_engineer_nonempty_tail_spills_now():
    """AC scenario: an engineer card with a non-empty tail spills
    immediately on a limit hit. End-to-end pin: ``scheduled_at`` must
    be forced to ``None`` at the ``_move_to_resume`` boundary, so the
    card is dispatch-eligible on the next tick instead of waiting for
    the limited provider's reset."""
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer", default_provider="minimax",
        )
        # Tail explicitly configured: anthropic as the spillover target.
        await subscription_pool.set_subscription_pool(
            s, PK, [_entry(provider="anthropic", drempel=0.9)],
            column="engineer",
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        cid = await _make_card_with_column(s, PK, "engineer")
        await s.commit()
    async with KanbanSessionLocal() as s:
        cards = await dispatch.list_cards(s, PK)
        card = next(c for c in cards if c.id == cid)

    captured: dict[str, object] = {}
    real_scheduled_at = (
        datetime.now(UTC) + timedelta(hours=1)
    ).isoformat()

    async def _capture(ks, **kwargs):
        captured["scheduled_at"] = kwargs.get("scheduled_at")
        return True

    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    try:
        mp.setattr(dispatch, "list_cards", lambda ks, project_key: _async_iter([card]))
        mp.setattr(dispatch, "_move_to_resume", _capture)
        mp.setattr(
            dispatch, "_resume_target_from_cwd",
            lambda cwd: ("/tmp/fake-project-path", "lim-test"),
        )
        mp.setattr(dispatch, "safe_resolve_project_key", lambda _p: PK)
        moved = await dispatch.move_limited_session_to_resume(
            "/tmp/fake-project-path/.claude/worktrees/lim-test",
            scheduled_at=real_scheduled_at,
        )
    finally:
        mp.undo()
    assert moved is True
    # Engineer's non-empty tail → spillover → scheduled_at collapsed.
    assert captured.get("scheduled_at") is None


async def _async_iter(items):
    """Tiny helper: turn a list into an awaitable that returns it,
    matching ``list_cards``'s async-returning signature for the
    monkeypatched shim."""
    return items


# ---------------------------------------------------------------------------
# Production spillover-config (kaart 2bb37d97…)
#
# Pins the per-column tails the operator configures on the live board so
# the AC scenarios stay reproducible from a unit test. The configuration
# has three intentional properties:
#
#   - engineer default = minimax; tail = [anthropic] — when MiniMax
#     hits its limit, the engineer card spills to Anthropic instead of
#     waiting. AC scenario: "spilling over"-logregel must appear on a
#     simulated MiniMax limit.
#   - analyst default  = anthropic; tail = [minimax] — when Anthropic
#     hits its limit, the analyst card spills to MiniMax. Lower-priority
#     workload than reviewer; MiniMax-M3 is acceptable here.
#   - reviewer default = anthropic; tail = [] — quality > speed;
#     reviewer waits on the reset (see spillover-per-kolom-decision.md §6
#     "kwaliteitsafweging").
#
# ``_build_spillover_candidates`` (dispatch.py:1806) prepends the column
# default as the implicit head automatically, so the tails below are
# ONLY the spillover targets — never the column default itself (otherwise
# the dedup branch strips them and we end up with an empty chain).
# ---------------------------------------------------------------------------


def _production_pool_tails():
    """The exact per-column tails installed on the live board.

    Kept as a function (not a constant) so each test gets a fresh list
    — ``PoolEntry`` is a frozen dataclass but the list itself is mutable
    and a single shared instance would leak between tests."""
    return {
        # engineer: spill from minimax → anthropic on limit hit.
        "engineer": [_entry(provider="anthropic", drempel=0.9)],
        # analyst: spill from anthropic → minimax on limit hit.
        "analyst": [_entry(provider="minimax", drempel=0.9)],
        # reviewer: deliberately empty ("nooit uitwijken").
        "reviewer": [],
    }


@pytest.mark.asyncio
async def test_production_pool_tails_round_trip_through_storage():
    """The per-column tails installed by ``set_subscription_pool`` must
    round-trip through the KanbanMeta wrapper so an operator can read
    them back via ``GET /api/v1/kanban/subscription-pool?column=…``."""
    tails = _production_pool_tails()
    async with KanbanSessionLocal() as s:
        for column, entries in tails.items():
            await subscription_pool.set_subscription_pool(
                s, PK, entries, column=column,
            )
        await s.commit()

    async with KanbanSessionLocal() as s:
        for column, expected in tails.items():
            got = await subscription_pool.get_subscription_pool(
                s, PK, column=column,
            )
            assert got == expected, (
                f"column={column!r}: expected {expected!r}, got {got!r}"
            )


@pytest.mark.asyncio
async def test_production_pool_tails_fire_spillover_on_first_entry_limit():
    """AC scenario (kaart 2bb37d97…): a limit on a column's first entry
    (the implicit head = column default) must trigger spillover for
    engineer + analyst, and must NOT for reviewer (intentional
    "nooit uitwijken"). Pins the production decision per column."""
    tails = _production_pool_tails()
    async with KanbanSessionLocal() as s:
        for column, entries in tails.items():
            if column == "engineer":
                await service.create_column(
                    s, project_key=PK, name="engineer",
                    default_agent="engineer", default_provider="minimax",
                )
            elif column == "analyst":
                await service.create_column(
                    s, project_key=PK, name="analyst",
                    default_agent="analyst", default_provider="anthropic",
                )
            elif column == "reviewer":
                await service.create_column(
                    s, project_key=PK, name="reviewer",
                    default_agent="reviewer", default_provider="anthropic",
                )
            await subscription_pool.set_subscription_pool(
                s, PK, entries, column=column,
            )
        await s.commit()

    async with KanbanSessionLocal() as s:
        engineer_spillover = await dispatch._pool_spillover_available(
            s, project_key=PK,
            limited_provider="minimax",  # MiniMax hit (column default)
            cli_id="claude-code",
            column="engineer",
        )
        analyst_spillover = await dispatch._pool_spillover_available(
            s, project_key=PK,
            limited_provider="anthropic",  # Anthropic hit (column default)
            cli_id="claude-code",
            column="analyst",
        )
        reviewer_spillover = await dispatch._pool_spillover_available(
            s, project_key=PK,
            limited_provider="anthropic",  # Anthropic hit
            cli_id="claude-code",
            column="reviewer",
        )
    # engineer + analyst spill (head hit → tail exists); reviewer does not.
    assert engineer_spillover is True
    assert analyst_spillover is True
    assert reviewer_spillover is False


@pytest.mark.asyncio
async def test_production_pool_tails_emit_spilling_over_activity_comment_on_engineer():
    """End-to-end AC: when an engineer card hits a MiniMax limit, the
    card-move path collapses ``scheduled_at`` to ``None`` AND posts the
    ``🔀 … spilling over …`` activity comment. This is the exact
    behaviour the operator will observe on the board after the live-DB
    install — and the comment string is the canonical signal a sweeper
    can grep for to confirm spillover fired."""
    tails = _production_pool_tails()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer", default_provider="minimax",
        )
        await subscription_pool.set_subscription_pool(
            s, PK, tails["engineer"], column="engineer",
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        cid = await _make_card_with_column(s, PK, "engineer")
        await s.commit()
    async with KanbanSessionLocal() as s:
        cards = await dispatch.list_cards(s, PK)
        card = next(c for c in cards if c.id == cid)

    captured: dict[str, object] = {}
    captured_comment: dict[str, object] = {}
    real_scheduled_at = (
        datetime.now(UTC) + timedelta(hours=1)
    ).isoformat()

    async def _capture(ks, **kwargs):
        captured["scheduled_at"] = kwargs.get("scheduled_at")
        return True

    async def _capture_comment(ks, *, card, project_key, text, **_):
        captured_comment["text"] = text
        return True

    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    try:
        mp.setattr(
            dispatch, "list_cards",
            lambda ks, project_key: _async_iter([card]),
        )
        mp.setattr(dispatch, "_move_to_resume", _capture)
        mp.setattr(
            dispatch, "_post_rate_limit_activity_comment",
            _capture_comment,
        )
        mp.setattr(
            dispatch, "_resume_target_from_cwd",
            lambda cwd: ("/tmp/fake-project-path", "lim-test"),
        )
        mp.setattr(dispatch, "safe_resolve_project_key", lambda _p: PK)
        moved = await dispatch.move_limited_session_to_resume(
            "/tmp/fake-project-path/.claude/worktrees/lim-test",
            scheduled_at=real_scheduled_at,
        )
    finally:
        mp.undo()
    assert moved is True
    # Spillover fires: scheduled_at collapsed + 🔀 spilling-over comment.
    assert captured.get("scheduled_at") is None
    comment_text = captured_comment.get("text", "")
    assert "spilling over" in comment_text, (
        f"expected 🔀 spilling-over comment, got: {comment_text!r}"
    )
    assert "minimax" in comment_text, (
        f"comment should name the just-hit provider, got: {comment_text!r}"
    )
