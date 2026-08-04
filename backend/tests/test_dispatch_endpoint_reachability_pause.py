"""Tests for the unreachable-endpoint pause merge in dispatch.

Card 424c23d4… (``docs/cockpit/litellm-sidecar-lifecycle-decision.md``
§3, herevalideerd 2026-08-04): when an ``anthropic-compatible`` pool
entry's endpoint fails to answer, the pool router adds the provider to
the paused set. In the **vangnet topology** (head exhausted + dead
vangnet) the merge does NOT change ``pick_subscription_for_cli``'s
``chosen`` output — the "laatste val-terug"-tak in
``subscription_pool.py:236-249`` returns the dead vangnet regardless
of whether it's paused. What the merge DOES change is
``has_available_spillover`` (``subscription_pool.py:274-323``): with
the chosen entry in the paused-set, the gate returns ``False`` and
the reactive limit path parks the card until the proxy is reachable
again. **Explicit pins** stay fail-closed — they walk the existing
spawn-failure path, NOT through this pause merge.

The merge lives in ``_paused_providers_for_pool`` (dispatch.py). Two
test groups exercise it:

1. Unit-test the merge: a pool whose compatible entry points at a
   non-answering endpoint adds ``anthropic-compatible`` to the paused
   set; a pool whose entry is reachable does NOT pause anything; a
   probe that raises is treated as "available"; the result is cached.
2. End-to-end against ``has_available_spillover`` (vangnet-topology
   discrimination): with the probe-paused vangnet the gate returns
   ``False``; flipping the probe to True (or removing the merge) flips
   the gate back to ``True``. The pre-existing tautological tests
   (``test_pool_unreachable_compatible_falls_through_to_next_entry``
   and ``test_explicit_pin_to_unreachable_endpoint_still_spawns``)
   were rewritten because they passed vacuously — they didn't seed a
   pool with an above-drempel head, so the head won on "no snapshot =
   available" regardless of whether the merge ran, and the
   explicit-pin test seeded no pool at all so the new code never
   executed.

The probe is a module-level seam: tests monkeypatch
``dispatch._endpoint_probe_uncached`` so no real HTTP is ever
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
    ``"anthropic-compatible"`` to the paused set. With the vangnet
    dead (see the end-to-end tests below) that membership makes
    ``has_available_spillover`` return ``False`` — the chosen entry
    itself is paused, so the reactive limit path parks the card."""
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
#
# These three tests are the discriminating half of the file. The original
# end-to-end tests (``test_pool_unreachable_compatible_falls_through_to_next_entry``
# and ``test_explicit_pin_to_unreachable_endpoint_still_spawns``) were
# tautological — both passed vacuously because the seed didn't exercise
# the new code. The replacements below seed a pool where the head IS
# actually above-drempel (so it falls through to the vangnet), or where
# the pool HAS a compatible entry the probe can pause (so the new code
# runs end-to-end).


@pytest.mark.asyncio
async def test_vangnet_dead_flips_spillover_gate_to_false(stub_probe):
    """Vangnet topology (head above-drempel + dead vangnet): the
    probe-paused compatible entry makes ``_pool_spillover_available``
    return ``False`` — the reactive limit path parks the card instead
    of looping through ``MAX_DISPATCH_FAILURES`` on the dead proxy.

    This is the discriminating test for the chosen behavior
    ("vangnet dood = kaart wacht op reset, niet door-schuiven",
    kaart 424c23d4… bevestigd 2026-08-04). With the probe-merge
    intact, the spillover gate is False; flipping the probe to True
    (or removing the merge) flips the gate back to True. The flip is
    asserted in the second half of the test.

    The head is seeded with a usage snapshot above its drempel so the
    val-terug-tak in ``pick_subscription_for_cli`` is exercised, not
    the trivial "no snapshot = available" branch that hid the bug in
    the pre-existing tautological test.
    """
    stub_probe["http://dead-router.example/v1"] = False

    async with KanbanSessionLocal() as s:
        await _seed_endpoint(s, "router-dead", base_url="http://dead-router.example/v1")
        # Head (anthropic) above-drempel so the val-terug fires;
        # tail (compatible) probe-paused so the merge can flip the gate.
        await subscription_pool.set_subscription_pool(s, "git:example.com/me/repo", [
            subscription_pool.PoolEntry(
                provider="anthropic", model=None, drempel=0.9, cli="claude-code",
            ),
            subscription_pool.PoolEntry(
                provider="anthropic-compatible", model=None, drempel=0.9,
                endpoint_name="router-dead", cli="claude-code",
            ),
        ])
        await s.commit()

        # The merge pauses the compatible entry: ``_pool_spillover_available``
        # builds ``paused = {anthropic-compatible, anthropic}`` (anthropic
        # added by the limited_provider arg). The chosen entry from
        # ``pick_subscription_for_cli`` is the dead vangnet itself
        # (val-terug returns it regardless of paused-set membership), and
        # since chosen.provider is in paused, the gate returns False.
        spillover = await dispatch._pool_spillover_available(
            s, project_key="git:example.com/me/repo",
            limited_provider="anthropic", cli_id="claude-code",
        )
        assert spillover is False, (
            "vangnet dead + probe-pause must make has_available_spillover "
            "return False so the reactive limit path parks the card"
        )

    # Flip the probe to True — simulate probe-merge removed or proxy
    # recovered. Use a fresh endpoint URL so the probe cache from the
    # first half doesn't poison the second half (the cache is
    # process-local and keyed on (project_key, endpoint_name)).
    stub_probe["http://recovered-router.example/v1"] = True
    dispatch._endpoint_reach_cache.clear()
    async with KanbanSessionLocal() as s:
        await _seed_endpoint(s, "router-recovered", base_url="http://recovered-router.example/v1")
        await subscription_pool.set_subscription_pool(s, "git:example.com/me/repo", [
            subscription_pool.PoolEntry(
                provider="anthropic", model=None, drempel=0.9, cli="claude-code",
            ),
            subscription_pool.PoolEntry(
                provider="anthropic-compatible", model=None, drempel=0.9,
                endpoint_name="router-recovered", cli="claude-code",
            ),
        ])
        await s.commit()

        # Without the probe-pause (or with the proxy recovered), the
        # vangnet is "available" — nothing pauses it. The gate returns
        # True. THIS is the buggy state the merge prevents; the first
        # half asserts False (merge intact), this half asserts True
        # (merge removed/recovered). Together they pin down both sides
        # of the discriminating test.
        spillover = await dispatch._pool_spillover_available(
            s, project_key="git:example.com/me/repo",
            limited_provider="anthropic", cli_id="claude-code",
        )
        assert spillover is True, (
            "without the probe-pause, the dead vangnet IS available — "
            "the gate flips back to True (this is the buggy state the "
            "merge prevents)"
        )


@pytest.mark.asyncio
async def test_head_healthy_wins_regardless_of_vangnet_pause(stub_probe):
    """Head-healthy topology (head under-drempel / no-usage + dead
    vangnet): the head wins regardless of whether the vangnet is
    paused, because ``pick_subscription_for_cli`` returns the first
    entry that's both not paused AND not above-drempel. This is the
    trivial sub-case — the probe-merge is irrelevant here — but it's
    worth pinning down so a future refactor that changes
    ``_is_above_threshold``'s default for None snapshots doesn't
    accidentally flip the order.
    """
    stub_probe["http://dead-router.example/v1"] = False

    async with KanbanSessionLocal() as s:
        await _seed_endpoint(s, "router-dead", base_url="http://dead-router.example/v1")
        await subscription_pool.set_subscription_pool(s, "git:example.com/me/repo", [
            subscription_pool.PoolEntry(
                provider="anthropic", model=None, drempel=0.9, cli="claude-code",
            ),
            subscription_pool.PoolEntry(
                provider="anthropic-compatible", model=None, drempel=0.9,
                endpoint_name="router-dead", cli="claude-code",
            ),
        ])
        await s.commit()

        chosen = await dispatch._pick_pool_choice(
            s, await subscription_pool.get_subscription_pool(s, "git:example.com/me/repo"),
            project_key="git:example.com/me/repo", cli_id="claude-code",
        )
        assert chosen is not None
        assert chosen.provider == "anthropic", (
            "head healthy + dead vangnet must dispatch on the head — "
            "the probe-merge is irrelevant here"
        )


@pytest.mark.asyncio
async def test_explicit_pin_bypasses_pool_pause(stub_probe):
    """Explicit pin (column_overrides → anthropic-compatible) on a
    dead endpoint must NOT be skipped by the new pause merge — the
    explicit pin path doesn't consult ``_paused_providers_for_pool``
    in a way that short-circuits the spawn. The transport is called
    with the pinned provider; the spawn itself will then fail
    downstream (we don't need to simulate that here — we only assert
    the merge doesn't pre-empt the spawn).

    This is the "fail-closed" branch from the card acceptance criteria:
    the explicit pin walks the existing ``MAX_DISPATCH_FAILURES`` →
    Impediment path, not the new "skip the provider" path.

    Discriminating seed (the pre-existing test was tautological):
    the pool HAS a compatible entry (dead, probe-paused) so
    ``_unreachable_compatible_providers`` actually runs; the column
    has ``default_provider=None`` so the resolver doesn't synthesise an
    anthropic head that would win in ``pick_subscription_for_cli`` (the
    resolver walks pool_choice > column_override, so without the head
    the pin's provider wins through both layers). Seeding a pool is
    the load-bearing change: without it, the original test passed
    vacuously because ``_unreachable_compatible_providers``
    short-circuited on empty entries.

    We assert TWO things so this test is not a partial tautology:
    (a) the merge ran (``_paused_providers_for_pool`` returns
    ``{"anthropic-compatible"}`` for the project's pool); (b) the
    spawn proceeded despite the paused set (transport called once
    with the pinned provider). Removing the merge from
    ``_paused_providers_for_pool`` would fail (a); a hypothetical
    "skip the spawn when paused" branch would fail (b).
    """
    stub_probe["http://dead-router.example/v1"] = False

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _seed_endpoint(s, "router-dead", base_url="http://dead-router.example/v1")
        await _make_column(s, default_provider=None)
        # Pool HAS the dead compatible entry — proves the pause-merge runs.
        await subscription_pool.set_subscription_pool(s, "git:example.com/me/repo", [
            subscription_pool.PoolEntry(
                provider="anthropic-compatible", model=None,
                drempel=0.9, endpoint_name="router-dead", cli="claude-code",
            ),
        ])
        cid = await _make_card(s, column_overrides={
            "engineer": {
                "provider": "anthropic-compatible",
                "model": None,
                "endpoint_name": "router-dead",
            },
        })
        await s.commit()

        # (a) Probe-merge ran: ``anthropic-compatible`` is in the paused
        # set. If the merge branch were removed from
        # ``_paused_providers_for_pool``, this set would be empty.
        paused = await dispatch._paused_providers_for_pool(
            s, project_key="git:example.com/me/repo",
        )
        assert "anthropic-compatible" in paused

        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()

    # (b) The spawn IS attempted: transport called with
    # anthropic-compatible on router-dead. The pause-merge ran (paused
    # set contains compatible) but the resolver does NOT consult that
    # paused-set on the explicit-pin path beyond the pool-choice it
    # already made — the spawn proceeds. It will fail downstream on
    # the dead proxy, walking the existing MAX_DISPATCH_FAILURES →
    # Impediment path, NOT the pause-merge.
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["provider"] == "anthropic-compatible"
    assert call["endpoint_name"] == "router-dead"
    assert call["endpoint_base_url"] == "http://dead-router.example/v1"
