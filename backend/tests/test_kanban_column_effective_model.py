# backend/tests/test_kanban_column_effective_model.py
"""Tests for the column-level effective-model precedence surface.

Kaart 1782fa43…: a kanban column's ``default_provider`` / ``default_model``
setting is silently overridden by higher-precedence layers — board-wide
subscription-override and subscription-pool (see
docs/cockpit/subscription-flexibiliteit-analyse.md §4). The column-settings
UI now surfaces the resolved effective model + the precedence level that
won via ``dispatch.resolve_column_effective_model`` (and the
``/columns/<id>/effective-model`` endpoint). These tests pin the resolution
chain so the surface never silently regresses to "column default" when a
real override or pool is in play.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import pytest_asyncio

from app.kanban import dispatch, subscription_pool
from app.kanban.service import create_column
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()
PK = "git:example.com/me/repo"


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


def _make_pool(entries: list[dict]) -> list[subscription_pool.PoolEntry]:
    """Build a PoolEntry list from a plain-dict shape (``provider``/``model``/``drempel``)."""
    return [
        subscription_pool.PoolEntry(
            provider=e["provider"], model=e.get("model"), drempel=e.get("drempel", 0.95),
        )
        for e in entries
    ]


# ---- _resolve_model_source: the per-level label helper ----------------------


def test_resolve_model_source_precedence_labels():
    # column_override > card_model > column_default > persona > none
    assert dispatch._resolve_model_source("m5", None, None, None) == "column_override"
    assert dispatch._resolve_model_source(None, "opus", None, None) == "card_model"
    assert dispatch._resolve_model_source(None, None, "sonnet", None) == "column_default"
    assert dispatch._resolve_model_source(None, None, None, "haiku", provider="anthropic") == "persona"
    assert dispatch._resolve_model_source(None, None, None, "haiku", provider="minimax") == "none"
    assert dispatch._resolve_model_source(None, None, None, None) == "none"


def test_resolve_model_source_column_default_dropped_on_higher_layer_provider_switch():
    # Mirrors _effective_model: the column_default label falls through when a
    # higher layer (global_override/pool) pinned a provider that differs from
    # column.default_provider, so the UI never reports "column_default" for a
    # model that was actually dropped.
    assert dispatch._resolve_model_source(
        None, None, "opus", None, provider="minimax",
        column_default_provider="anthropic", provider_pinned_by_higher_layer=True,
    ) == "none"
    # Same provider -> label stays column_default.
    assert dispatch._resolve_model_source(
        None, None, "MiniMax-M3", None, provider="minimax",
        column_default_provider="minimax", provider_pinned_by_higher_layer=True,
    ) == "column_default"
    # No higher-layer pin -> unchanged.
    assert dispatch._resolve_model_source(
        None, None, "opus", None, provider="bedrock",
        column_default_provider=None, provider_pinned_by_higher_layer=False,
    ) == "column_default"


# ---- resolve_column_effective_model: full chain ----------------------------


@pytest.mark.asyncio
async def test_resolve_column_effective_model_column_default():
    """No override, no pool, no card context → column.default_* wins."""
    async with KanbanSessionLocal() as s:
        await create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax", default_model="MiniMax-M3",
        )
        info = await dispatch.resolve_column_effective_model(
            s, project_key=PK, column_name="engineer", project_path="/p",
        )
    assert info["provider"] == "minimax"
    assert info["model"] == "MiniMax-M3"
    assert info["provider_source"] == "column_default"
    assert info["model_source"] == "column_default"


@pytest.mark.asyncio
async def test_resolve_column_effective_model_global_override_wins():
    """A board-wide subscription-override pins both provider AND model."""
    async with KanbanSessionLocal() as s:
        await create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax", default_model="MiniMax-M3",
        )
        await s.commit()
    # Set the active override AFTER create_column so the create-time audit
    # log doesn't carry the override noise.
    from app.kanban.dispatch import set_active_subscription_override
    async with KanbanSessionLocal() as s:
        await set_active_subscription_override(
            s, project_key=PK, override={"provider": "anthropic", "model": "opus"},
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        info = await dispatch.resolve_column_effective_model(
            s, project_key=PK, column_name="engineer", project_path="/p",
        )
    # Board-wide override beats column default on both axes.
    assert info["provider"] == "anthropic"
    assert info["model"] == "opus"
    assert info["provider_source"] == "global_override"
    assert info["model_source"] == "global_override"


@pytest.mark.asyncio
async def test_resolve_column_effective_model_pool_wins():
    """Kaart 0172e94d…: met de spillover-keten wint de kolom-default
    (de impliciete kop) over de pool. Een pool-entry ``minimax`` op
    een kolom met default ``anthropic`` levert dus ``anthropic``,
    tenzij de anthropic-kop uitvalt — dan pas wordt ``minimax`` geraakt."""
    async with KanbanSessionLocal() as s:
        await create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="anthropic", default_model="sonnet",
        )
        await subscription_pool.set_subscription_pool(
            s, PK, _make_pool([
                {"provider": "minimax", "model": "MiniMax-M3", "drempel": 0.95},
            ]),
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        info = await dispatch.resolve_column_effective_model(
            s, project_key=PK, column_name="engineer", project_path="/p",
        )
    # Kop = kolom-default; provider_source is eerlijk ``column_default``.
    assert info["provider"] == "anthropic"
    assert info["model"] == "sonnet"
    assert info["provider_source"] == "column_default"
    assert info["model_source"] == "column_default"


@pytest.mark.asyncio
async def test_resolve_column_effective_model_pool_provider_only():
    """Kaart 0172e94d…: kolom-default ``minimax`` matcht een pool-entry
    zonder model; de kop erft drempel, maar ``model=None`` valt door
    naar ``column.default_model``. provider_source blijft eerlijk
    ``column_default`` omdat de kop won (niet de pool-pin)."""
    async with KanbanSessionLocal() as s:
        await create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax", default_model="MiniMax-M3",
        )
        await subscription_pool.set_subscription_pool(
            s, PK, _make_pool([
                {"provider": "minimax", "model": None, "drempel": 0.95},
            ]),
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        info = await dispatch.resolve_column_effective_model(
            s, project_key=PK, column_name="engineer", project_path="/p",
        )
    assert info["provider"] == "minimax"
    assert info["model"] == "MiniMax-M3"
    assert info["provider_source"] == "column_default"
    assert info["model_source"] == "column_default"


@pytest.mark.asyncio
async def test_resolve_column_effective_model_global_override_provider_only_drops_column_model():
    """AC (bug 98064955…): analyst column (anthropic/opus) + a board-wide override
    of {provider: minimax, model: null} drops the column model alias — it does NOT
    leak to the MiniMax spawn. The resolver returns model=None; the provider env
    then fills the MiniMax provider-native default (MiniMax-M3)."""
    async with KanbanSessionLocal() as s:
        await create_column(
            s, project_key=PK, name="analyst", default_agent="analyst",
            default_provider="anthropic", default_model="opus",
        )
        await s.commit()
    from app.kanban.dispatch import set_active_subscription_override
    async with KanbanSessionLocal() as s:
        await set_active_subscription_override(
            s, project_key=PK, override={"provider": "minimax", "model": None},
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        info = await dispatch.resolve_column_effective_model(
            s, project_key=PK, column_name="analyst", project_path="/p",
        )
    assert info["provider"] == "minimax"
    assert info["model"] is None            # opus dropped, not leaked
    assert info["provider_source"] == "global_override"
    assert info["model_source"] == "none"
    # The provider-native default (MiniMax-M3) is what actually spawns.
    from app.services.agentic_cli.provider_env import (
        MINIMAX_DEFAULT_MODEL,
        build_provider_env,
    )
    env = build_provider_env(provider=info["provider"], model=info["model"])
    assert env["ANTHROPIC_MODEL"] == MINIMAX_DEFAULT_MODEL == "MiniMax-M3"


@pytest.mark.asyncio
async def test_resolve_column_effective_model_pool_provider_only_drops_column_model():
    """Same drop via a subscription-pool/spillover choice: a pool entry
    {provider: minimax, model: None} that switches the provider away from the
    column default drops the column model alias.

    The column deliberately sets `default_model` WITHOUT `default_provider`.
    Under vorm B (decision 2026-07-23, spillover-per-kolom-decision.md /
    kaart 2688bf80) a column's `default_provider` becomes the *implicit head*
    of the spillover chain and therefore wins over the pool — see the sibling
    `test_resolve_column_effective_model_pool_wins`. With no default_provider
    there is no head, so the pool entry is what the router lands on, which is
    the only way this UI path reaches a provider switch at all
    (`_column_settings_pool_picker` passes no usage and no pauses, so a head
    can never fall away here). The `opus`-leaks-into-a-MiniMax-spawn defect
    that same decision filed is what the drop below prevents.
    """
    async with KanbanSessionLocal() as s:
        await create_column(
            s, project_key=PK, name="analyst", default_agent="analyst",
            default_model="opus",
        )
        await subscription_pool.set_subscription_pool(
            s, PK, _make_pool([
                {"provider": "minimax", "model": None, "drempel": 0.95},
            ]),
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        info = await dispatch.resolve_column_effective_model(
            s, project_key=PK, column_name="analyst", project_path="/p",
        )
    assert info["provider"] == "minimax"
    assert info["model"] is None
    assert info["provider_source"] == "pool"
    assert info["model_source"] == "none"


@pytest.mark.asyncio
async def test_resolve_column_effective_model_card_model_applies():
    """A per-card model (no override) lands as card_model source."""
    async with KanbanSessionLocal() as s:
        await create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_model="sonnet",
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        info = await dispatch.resolve_column_effective_model(
            s, project_key=PK, column_name="engineer", project_path="/p",
            card_model="opus",
        )
    assert info["model"] == "opus"
    assert info["model_source"] == "card_model"


@pytest.mark.asyncio
async def test_resolve_column_effective_model_column_override_applies():
    """A per-card column_override for this column wins over column.default_model."""
    async with KanbanSessionLocal() as s:
        await create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_model="sonnet",
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        info = await dispatch.resolve_column_effective_model(
            s, project_key=PK, column_name="engineer", project_path="/p",
            column_override={"model": "MiniMax-M3", "provider": "minimax"},
        )
    assert info["provider"] == "minimax"
    assert info["model"] == "MiniMax-M3"
    assert info["provider_source"] == "column_override"
    assert info["model_source"] == "column_override"


@pytest.mark.asyncio
async def test_resolve_column_effective_model_global_override_dominates_pool():
    """The global override outranks the pool — they're documented as 1 > 2."""
    async with KanbanSessionLocal() as s:
        await create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="anthropic", default_model="sonnet",
        )
        await subscription_pool.set_subscription_pool(
            s, PK, _make_pool([
                {"provider": "minimax", "model": "MiniMax-M3", "drempel": 0.95},
            ]),
        )
        await s.commit()
    from app.kanban.dispatch import set_active_subscription_override
    async with KanbanSessionLocal() as s:
        await set_active_subscription_override(
            s, project_key=PK, override={"provider": "bedrock", "model": "claude-haiku-4-5"},
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        info = await dispatch.resolve_column_effective_model(
            s, project_key=PK, column_name="engineer", project_path="/p",
        )
    assert info["provider"] == "bedrock"
    assert info["model"] == "claude-haiku-4-5"
    assert info["provider_source"] == "global_override"
    assert info["model_source"] == "global_override"


# ---- spillover_chain (kaart 7411d25e…) --------------------------------------
#
# The UI surfaces the full spillover chain (``[head] ++ [tail]``) so an
# operator can answer "why is my card stuck?" without reading the dispatch
# resolver source. These tests pin the chain shape on every resolve path
# so a future chain-rewrite (new layer, re-order, dedup change) is caught
# at the unit-test layer, not as a silent "spillover UI says empty" in
# production.


@pytest.mark.asyncio
async def test_spillover_chain_column_default_only():
    """No pool → the chain is just the column-default head (length 1).
    The UI renders a length-1 chain as 'no spillover configured'."""
    async with KanbanSessionLocal() as s:
        await create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax", default_model="MiniMax-M3",
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        info = await dispatch.resolve_column_effective_model(
            s, project_key=PK, column_name="engineer", project_path="/p",
        )
    assert info["spillover_chain"] == ["minimax"]


@pytest.mark.asyncio
async def test_spillover_chain_with_pool_dedupes_head():
    """A pool that *also* lists the column-default provider dedupes the
    head — the tail never re-contains the column default."""
    async with KanbanSessionLocal() as s:
        await create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="anthropic", default_model="sonnet",
        )
        await subscription_pool.set_subscription_pool(
            s, PK, _make_pool([
                # First entry matches the column default — must NOT appear
                # in the tail (otherwise the chain reads "anthropic → anthropic → …").
                {"provider": "anthropic", "model": "haiku", "drempel": 0.9},
                {"provider": "minimax", "model": "MiniMax-M3", "drempel": 0.95},
            ]),
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        info = await dispatch.resolve_column_effective_model(
            s, project_key=PK, column_name="engineer", project_path="/p",
        )
    assert info["spillover_chain"] == ["anthropic", "minimax"]


@pytest.mark.asyncio
async def test_spillover_chain_no_column_default_uses_pool_head():
    """When the column has no ``default_provider`` the pool's first
    entry becomes the implicit head, deduped against the tail (the
    shape mirrors ``_build_spillover_candidates``)."""
    async with KanbanSessionLocal() as s:
        await create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
        )
        await subscription_pool.set_subscription_pool(
            s, PK, _make_pool([
                {"provider": "anthropic", "model": None, "drempel": 0.9},
                {"provider": "minimax", "model": "MiniMax-M3", "drempel": 0.95},
            ]),
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        info = await dispatch.resolve_column_effective_model(
            s, project_key=PK, column_name="engineer", project_path="/p",
        )
    assert info["spillover_chain"] == ["anthropic", "minimax"]


@pytest.mark.asyncio
async def test_spillover_chain_no_column_default_no_pool_synthetic_head():
    """No default + no pool → synthetic anthropic head so the chain is
    non-empty. The UI's length-1 branch is the visual contract for
    'no spillover configured', so a length-0 chain would break the
    per-column row rendering."""
    async with KanbanSessionLocal() as s:
        await create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        info = await dispatch.resolve_column_effective_model(
            s, project_key=PK, column_name="engineer", project_path="/p",
        )
    # Synthetic head from ``_build_spillover_candidates`` — anthropic is the
    # chain-end default for the resolver, so an empty column/pool produces
    # ``["anthropic"]`` rather than ``[]``.
    assert info["spillover_chain"] == ["anthropic"]


@pytest.mark.asyncio
async def test_spillover_chain_preserved_under_global_override():
    """A board-wide override pins the *winner* but the chain itself is a
    routing-topology artifact, not a routing-decision artifact — it must
    remain visible so an operator can still see 'if I drop the override,
    my column will spill to minimax'. The UI relies on this in the
    'override active, pool greyed out' state."""
    async with KanbanSessionLocal() as s:
        await create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="anthropic", default_model="sonnet",
        )
        await subscription_pool.set_subscription_pool(
            s, PK, _make_pool([
                {"provider": "minimax", "model": "MiniMax-M3", "drempel": 0.95},
            ]),
        )
        await s.commit()
    from app.kanban.dispatch import set_active_subscription_override
    async with KanbanSessionLocal() as s:
        await set_active_subscription_override(
            s, project_key=PK, override={"provider": "bedrock", "model": None},
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        info = await dispatch.resolve_column_effective_model(
            s, project_key=PK, column_name="engineer", project_path="/p",
        )
    assert info["provider"] == "bedrock"
    assert info["spillover_chain"] == ["anthropic", "minimax"]


# ---- minimax model discovery ------------------------------------------------


def _write_jsonl(path, rows):
    """Write `rows` (each a dict) as one JSON object per line at `path`."""
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


@pytest.mark.asyncio
async def test_get_cached_minimax_model_options_returns_seed_when_never_refreshed():
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_cached_minimax_model_options(s) == list(
            dispatch.MINIMAX_MODEL_OPTIONS_SEED
        )


@pytest.mark.asyncio
async def test_discover_minimax_models_sync_reads_jsonl_with_minimax_prefix(tmp_path, monkeypatch):
    """The discovery path collects unique ``minimax-``-prefixed values from JSONL."""
    import app.kanban.dispatch as dispatch_module
    project_dir = tmp_path / ".claude" / "projects" / "proj1"
    project_dir.mkdir(parents=True)
    jsonl = project_dir / "session.jsonl"
    _write_jsonl(jsonl, [
        {"type": "assistant", "message": {"model": "MiniMax-M3"}},
        {"type": "user", "message": {"model": "ignored"}},  # non-assistant rows skipped
        {"type": "assistant", "message": {"model": "MiniMax-M2.7"}},
        {"type": "assistant", "message": {"model": "claude-sonnet-5"}},  # wrong prefix
        {"type": "assistant", "message": {"model": "MiniMax-M3"}},  # duplicate
        {"type": "assistant", "message": {}},  # no model key
    ])
    monkeypatch.setattr(dispatch_module, "_discover_minimax_models_sync_glob", lambda: [str(jsonl)])
    models = dispatch_module._discover_minimax_models_sync()
    assert models == ["MiniMax-M3", "MiniMax-M2.7"]


@pytest.mark.asyncio
async def test_discover_minimax_models_sync_falls_back_to_seed_when_empty(tmp_path, monkeypatch):
    """No JSONLs → seed (MiniMax-M3) — never an empty list."""
    import app.kanban.dispatch as dispatch_module
    monkeypatch.setattr(dispatch_module, "_discover_minimax_models_sync_glob", lambda: [])
    models = dispatch_module._discover_minimax_models_sync()
    assert models == list(dispatch_module.MINIMAX_MODEL_OPTIONS_SEED)


@pytest.mark.asyncio
async def test_refresh_minimax_model_options_caches_discovered_list():
    """A successful scan persists its result; the next read returns it."""
    async with KanbanSessionLocal() as s:
        with patch.object(dispatch, "_discover_minimax_models_sync",
                          return_value=["MiniMax-M3", "MiniMax-M2.7"]):
            options = await dispatch.refresh_minimax_model_options(s)
            await s.commit()
        assert options == ["MiniMax-M3", "MiniMax-M2.7"]
        assert await dispatch.get_cached_minimax_model_options(s) == ["MiniMax-M3", "MiniMax-M2.7"]


@pytest.mark.asyncio
async def test_refresh_minimax_model_options_empty_does_not_clobber_cache():
    """Mirrors the claude-code refresh path: an empty result is *not* a wipe."""
    async with KanbanSessionLocal() as s:
        with patch.object(dispatch, "_discover_minimax_models_sync",
                          return_value=["MiniMax-M3"]):
            await dispatch.refresh_minimax_model_options(s)
            await s.commit()
        with patch.object(dispatch, "_discover_minimax_models_sync", return_value=[]):
            options = await dispatch.refresh_minimax_model_options(s)
            await s.commit()
        assert options == []
        # Cache still has the previous good list.
        assert await dispatch.get_cached_minimax_model_options(s) == ["MiniMax-M3"]