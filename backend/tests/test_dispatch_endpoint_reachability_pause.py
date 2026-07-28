"""Tests for the unreachable-endpoint pause merge in dispatch.

Card 424c23d4… (``docs/cockpit/litellm-sidecar-lifecycle-decision.md``
§3): when an ``anthropic-compatible`` pool entry's endpoint fails to
answer, the pool router must treat the provider as paused so the pool
picks the next entry instead of looping through ``MAX_DISPATCH_FAILURES``
on a dead proxy. **Explicit pins** on the same provider stay
fail-closed — they walk the existing spawn-failure path, NOT through
this pause merge.

The merge lives in ``_paused_providers_for_pool`` (dispatch.py). Two
test groups exercise it:

1. Unit-test the merge: a pool whose compatible entry points at a
   non-answering endpoint adds ``anthropic-compatible`` to the paused
   set; a pool whose entry is reachable does NOT pause anything.
2. End-to-end: the dispatch path picks the next pool entry (Anthropic)
   when the compatible proxy is dead; with an explicit pin, the spawn
   is still attempted (the merge is NOT consulted on the explicit-pin
   path) and the existing failure loop takes over.

The probe is a module-level seam: tests monkeypatch
``dispatch._probe_endpoint_reachable`` so no real HTTP is ever
performed.
"""
from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio

from app.kanban import dispatch, service, subscription_pool
from app.kanban.operations import apply_operation
from app.services.agentic_cli import endpoints as agent_endpoints
from app.services.agentic_cli.endpoints import Endpoint
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    # Drop any cached probe result from a previous test in this file —
    # the cache is process-local and would otherwise let one test pollute
    # the next (especially when stubbing the probe function).
    if hasattr(dispatch, "_endpoint_reach_cache"):
        dispatch._endpoint_reach_cache.clear()
    yield
    if hasattr(dispatch, "_endpoint_reach_cache"):
        dispatch._endpoint_reach_cache.clear()


# ---- recording transport (the kwargs the dispatcher passes through) ----------


class RecordingTransport:
    """Captures the kwargs the dispatcher hands to ``SpawnTransport``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        directory,
        prompt,
        session_name,
        cli_id="claude-code",
        provider="anthropic",
        model=None,
        endpoint_name=None,
        endpoint_base_url=None,
        endpoint_auth_token=None,
        card_id=None,
        column_name=None,
    ):
        self.calls.append({
            "directory": directory,
            "session_name": session_name,
            "cli_id": cli_id,
            "provider": provider,
            "model": model,
            "endpoint_name": endpoint_name,
            "endpoint_base_url": endpoint_base_url,
            "endpoint_auth_token": endpoint_auth_token,
            "card_id": card_id,
            "column_name": column_name,
        })
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}


# ---- helpers ----------------------------------------------------------------


async def _seed_endpoint(s, name: str, *, base_url: str, model: str = "claude-test-1"):
    await agent_endpoints.upsert_endpoint(
        s, "git:example.com/me/repo", Endpoint(
            name=name, base_url=base_url, model=model,
        ),
    )


async def _make_column(s, *, name: str = "engineer", default_provider: str | None = "anthropic"):
    return await service.create_column(
        s, project_key="git:example.com/me/repo", name=name,
        default_agent=name,
        default_provider=default_provider,
    )


async def _make_card(s, *, title: str = "T", column: str = "Backlog", column_overrides: dict | None = None):
    payload: dict[str, Any] = {"title": title, "column": column}
    if column_overrides is not None:
        payload["column_overrides"] = column_overrides
    return await apply_operation(
        s, op_type="create", entity_type="card",
        project_key="git:example.com/me/repo", entity_id=None,
        payload=payload,
    )


@pytest.fixture
def stub_probe(monkeypatch):
    """Replace ``dispatch._endpoint_probe_uncached`` (the inner raw
    probe) with a stub keyed by base_url. Tests register URLs as
    reachable / unreachable; any URL not registered raises ``KeyError``
    so a probe that wasn't expected is loud.

    We patch the *inner* seam, not the public ``_probe_endpoint_reachable``
    wrapper, so the wrapper's exception-swallow contract stays
    exercised end-to-end (see
    ``test_paused_providers_for_pool_probe_failure_treats_as_available``).
    """
    state: dict[str, bool] = {}

    async def _fake(url: str) -> bool:
        return state[url]

    monkeypatch.setattr(dispatch, "_endpoint_probe_uncached", _fake)
    return state


# ---- unit tests on _paused_providers_for_pool ------------------------------


@pytest.mark.asyncio
async def test_paused_providers_for_pool_pauses_unreachable_compatible(
    stub_probe,
):
    """An ``anthropic-compatible`` pool entry whose endpoint fails to
    answer causes ``_paused_providers_for_pool`` to add
    ``"anthropic-compatible"`` to the paused set. The pool router uses
    this set as a hard skip list (``subscription_pool.PoolEntry``'s
    membership is keyed by provider), so the next entry wins."""
    stub_probe["http://dead-router.example/v1"] = False

    async with KanbanSessionLocal() as s:
        await _seed_endpoint(s, "router-dead", base_url="http://dead-router.example/v1")
        await subscription_pool.set_subscription_pool(s, "git:example.com/me/repo", [
            subscription_pool.PoolEntry(
                provider="anthropic-compatible", model=None,
                drempel=0.9, endpoint_name="router-dead",
            ),
        ])
        await s.commit()

        paused = await dispatch._paused_providers_for_pool(
            s, project_key="git:example.com/me/repo",
        )

    assert "anthropic-compatible" in paused


@pytest.mark.asyncio
async def test_paused_providers_for_pool_does_not_pause_reachable_compatible(
    stub_probe,
):
    """A pool entry whose endpoint answers the probe stays available —
    the merge does NOT pre-emptively pause a healthy proxy."""
    stub_probe["http://live-router.example/v1"] = True

    async with KanbanSessionLocal() as s:
        await _seed_endpoint(s, "router-live", base_url="http://live-router.example/v1")
        await subscription_pool.set_subscription_pool(s, "git:example.com/me/repo", [
            subscription_pool.PoolEntry(
                provider="anthropic-compatible", model=None,
                drempel=0.9, endpoint_name="router-live",
            ),
        ])
        await s.commit()

        paused = await dispatch._paused_providers_for_pool(
            s, project_key="git:example.com/me/repo",
        )

    assert "anthropic-compatible" not in paused


@pytest.mark.asyncio
async def test_paused_providers_for_pool_probe_failure_treats_as_available(
    monkeypatch,
):
    """When the raw probe itself raises (e.g. timeout, DNS failure,
    OSError), the wrapper ``_probe_endpoint_reachable`` must swallow
    the exception and return ``True`` ("available") so a broken probe
    doesn't wedge dispatch. We patch the *inner* seam
    (``_endpoint_probe_uncached``) to raise, and assert that the
    wrapper's fail-soft contract holds end-to-end."""
    async def _raising(url: str) -> bool:
        raise RuntimeError("simulated probe crash")

    monkeypatch.setattr(dispatch, "_endpoint_probe_uncached", _raising)

    async with KanbanSessionLocal() as s:
        await _seed_endpoint(s, "router-live", base_url="http://live.example/v1")
        await subscription_pool.set_subscription_pool(s, "git:example.com/me/repo", [
            subscription_pool.PoolEntry(
                provider="anthropic-compatible", model=None,
                drempel=0.9, endpoint_name="router-live",
            ),
        ])
        await s.commit()

        paused = await dispatch._paused_providers_for_pool(
            s, project_key="git:example.com/me/repo",
        )

    # Probe raised → treated as available → provider NOT in paused set.
    assert "anthropic-compatible" not in paused


@pytest.mark.asyncio
async def test_paused_providers_for_pool_caches_probe_results(stub_probe):
    """The probe is cached: the second call within TTL reuses the
    first call's verdict instead of issuing another HTTP call. We
    assert it via a side-channel — patch the inner seam to a counting
    function and check the count stays at 1 across two calls when
    neither probe has expired."""
    counter = {"calls": 0}
    cached = stub_probe  # alias for readability in the line below

    async def _counting(url: str) -> bool:
        counter["calls"] += 1
        return cached[url]

    dispatch._endpoint_probe_uncached = _counting
    cached["http://cached.example/v1"] = False

    async with KanbanSessionLocal() as s:
        await _seed_endpoint(s, "router-cached", base_url="http://cached.example/v1")
        await subscription_pool.set_subscription_pool(s, "git:example.com/me/repo", [
            subscription_pool.PoolEntry(
                provider="anthropic-compatible", model=None,
                drempel=0.9, endpoint_name="router-cached",
            ),
        ])
        await s.commit()

        await dispatch._paused_providers_for_pool(
            s, project_key="git:example.com/me/repo",
        )
        await dispatch._paused_providers_for_pool(
            s, project_key="git:example.com/me/repo",
        )

    assert counter["calls"] == 1


# ---- end-to-end tests on the dispatch path ---------------------------------


@pytest.mark.asyncio
async def test_pool_unreachable_compatible_falls_through_to_next_entry(
    stub_probe,
):
    """End-to-end: a pool ordering whose first entry is a dead
    ``anthropic-compatible`` proxy and whose second entry is healthy
    anthropic must dispatch on anthropic, NOT on the dead provider —
    the pool router sees the compatible entry as paused."""
    stub_probe["http://dead-router.example/v1"] = False

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_column(s, default_provider="anthropic")
        await _seed_endpoint(s, "router-dead", base_url="http://dead-router.example/v1")
        # Column-default is anthropic; pool's compatible entry is the
        # spillover-fallback that should be tried first then skipped on
        # the dead proxy.
        await subscription_pool.set_subscription_pool(s, "git:example.com/me/repo", [
            subscription_pool.PoolEntry(
                provider="anthropic-compatible", model=None,
                drempel=0.9, endpoint_name="router-dead",
            ),
        ])
        cid = await _make_card(s)
        await s.commit()

        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()

    # The pool skipped the dead compatible; the column-default (anthropic)
    # was the one in priority position above, so the resolved provider is
    # anthropic — never the dead one.
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "anthropic"
    assert transport.calls[0]["endpoint_name"] is None


@pytest.mark.asyncio
async def test_explicit_pin_to_unreachable_endpoint_still_spawns(
    stub_probe,
):
    """Explicit pin (column_overrides → anthropic-compatible) on a dead
    endpoint must NOT be skipped by the new pause merge — the explicit
    pin path doesn't consult ``_paused_providers_for_pool``. The
    transport is called with the pinned provider; the spawn itself will
    then fail (we don't need to simulate that here — we only assert the
    merge doesn't pre-empt the spawn).

    This is the "fail-closed" branch from the card acceptance criteria:
    the explicit pin walks the existing ``MAX_DISPATCH_FAILURES`` →
    Impediment path, not the new "skip the provider" path.
    """
    stub_probe["http://dead-router.example/v1"] = False

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _seed_endpoint(s, "router-dead", base_url="http://dead-router.example/v1")
        await _make_column(s, default_provider="anthropic")
        cid = await _make_card(s, column_overrides={
            "engineer": {
                "provider": "anthropic-compatible",
                "model": None,
                "endpoint_name": "router-dead",
            },
        })
        await s.commit()

        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()

    # The transport IS called: explicit pins bypass the pool-pause merge.
    # The spawn itself will then fail downstream (CLI cannot connect to
    # the dead proxy); that's covered by the existing MAX_DISPATCH_FAILURES
    # test, not by this card.
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["provider"] == "anthropic-compatible"
    assert call["endpoint_name"] == "router-dead"
    assert call["endpoint_base_url"] == "http://dead-router.example/v1"
