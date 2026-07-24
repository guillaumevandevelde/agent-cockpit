"""Tests for the auto-dispatch wiring of ``anthropic-compatible`` endpoints.

Card 293d1faa…: ``provider="anthropic-compatible"`` is today accepted by the
storage layer (pool allow-list, REST surface) but the dispatch path builds
its spawn options without ``endpoint_base_url`` / ``endpoint_auth_token``.
``build_provider_env`` then raises ``ValueError`` and the spawn loops
through ``MAX_DISPATCH_FAILURES`` before the card lands in Impediment.

These tests pin the new shared resolution helper and the threading through
the transport. Three carriers are exercised (pool entry, board-wide
override, per-card column_override), each with a resolvable endpoint and
each with the two failure shapes (endpoint name missing / endpoint name
present but credential unresolvable). A separate test pins the
precedence ``override > pool > column_override`` for the endpoint name.

The tests use a ``RecordingTransport`` that captures the kwargs the
dispatcher passes into the worktree transport. The worktree transport's
``_transport`` is itself exercised in the existing ``test_provider_env``
suite for the env it forwards; here we focus on *which* kwargs the
dispatch path populates, since that's the regression surface these cards
prevent.
"""
from __future__ import annotations

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
    yield


PK = "git:example.com/me/repo"


class RecordingTransport:
    """Captures the kwargs the dispatcher passes to ``SpawnTransport``.

    The card only inspects ``provider``/``model``/``endpoint_*`` because
    that's exactly what the spawn-bridge forwards into
    ``SpawnCommandOptions`` and ultimately into the spawned CLI's
    environment. Skipping the other kwargs keeps each test focused on the
    endpoint-resolution regression surface.
    """

    def __init__(self):
        self.calls: list[dict] = []

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
        })
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}


async def _seed_endpoint(
    s,
    name: str,
    *,
    base_url: str = "https://example.com/anthropic",
    model: str = "claude-test-1",
    credential_name: str | None = None,
):
    """Insert a single named endpoint into the registry."""
    await agent_endpoints.upsert_endpoint(s, PK, Endpoint(
        name=name,
        base_url=base_url,
        model=model,
        credential_name=credential_name,
    ))


async def _make_column(s, *, default_provider=None, default_model=None):
    """Create a single ``engineer`` column for a card to land on."""
    return await service.create_column(
        s, project_key=PK, name="engineer",
        default_agent="engineer",
        default_provider=default_provider,
        default_model=default_model,
    )


async def _make_card(s, *, title="Task", column="Backlog", column_overrides=None):
    payload = {"title": title, "column": column}
    if column_overrides is not None:
        payload["column_overrides"] = column_overrides
    return await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None,
        payload=payload,
    )


# ---- happy path: each carrier resolves and threads through ------------------


async def test_dispatch_threads_pool_entry_endpoint_through_transport():
    """A pool-entry pointing at a resolvable endpoint lands its
    ``endpoint_base_url``/``endpoint_auth_token`` on the transport call.

    The endpoint row has no credential_name, matching the
    "ambient environment" pattern documented in
    ``endpoints.py:60`` — the dispatched CLI is expected to find the
    credential in its own ``ANTHROPIC_AUTH_TOKEN`` (caller-resolved
    outside of dispatch). The transport still receives ``endpoint_name``
    for audit logs even when no token is provided.
    """
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _seed_endpoint(s, "router-1", base_url="https://router-1.example/v1")
        await subscription_pool.set_subscription_pool(
            s, PK, [subscription_pool.PoolEntry(
                provider="anthropic-compatible", model=None,
                drempel=0.9, endpoint_name="router-1",
            )],
        )
        await _make_column(s, default_provider="anthropic")
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["provider"] == "anthropic-compatible"
    assert call["endpoint_name"] == "router-1"
    assert call["endpoint_base_url"] == "https://router-1.example/v1"
    assert call["endpoint_auth_token"] is None  # ambient credential


def _seed_corrupt_pool_row(s, *, endpoint_name: str) -> None:
    """Write a pool row directly to KanbanMeta that references an
    endpoint name NOT in the registry. Bypasses ``set_subscription_pool``
    validation so the dispatch-time defense-in-depth path can be
    exercised in isolation.

    Stored shape mirrors ``_serialize_entries`` exactly so a future
    deserialise round-trip would produce the same PoolEntry —
    writing it inline here is the documented "legacy row" path for
    storage corruption that the dispatcher is required to refuse.
    """
    import json

    from app.kanban.models import KanbanMeta
    from app.kanban.subscription_pool import (
        DEFAULT_POOL_CLI,
        SUBSCRIPTION_POOL_PREFIX,
    )
    payload = json.dumps([{
        "cli": DEFAULT_POOL_CLI,
        "provider": "anthropic-compatible",
        "model": None,
        "drempel": 0.9,
        "endpoint_name": endpoint_name,
    }])
    s.add(KanbanMeta(
        key=SUBSCRIPTION_POOL_PREFIX + PK,
        value=payload,
    ))


async def test_dispatch_threads_global_override_endpoint_through_transport():
    """A board-wide override carrying ``endpoint_name`` lands the same
    fields on the transport call. The override dict is the same
    shape the board-pin REST surface uses.
    """
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _seed_endpoint(s, "router-2", base_url="https://router-2.example/v1")
        await dispatch.set_active_subscription_override(
            s, PK, {
                "provider": "anthropic-compatible",
                "model": None,
                "endpoint_name": "router-2",
            },
        )
        await _make_column(s, default_provider="anthropic")
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["provider"] == "anthropic-compatible"
    assert call["endpoint_name"] == "router-2"
    assert call["endpoint_base_url"] == "https://router-2.example/v1"


async def test_dispatch_threads_column_override_endpoint_through_transport():
    """A per-card ``column_overrides[col]`` carrying ``endpoint_name``
    propagates to the transport too. This is the carrier with the
    narrowest scope — only that one column on that one card routes to
    the endpoint.
    """
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _seed_endpoint(s, "router-3", base_url="https://router-3.example/v1")
        await _make_column(s, default_provider="anthropic")
        cid = await _make_card(
            s, column_overrides={
                "engineer": {
                    "provider": "anthropic-compatible",
                    "model": None,
                    "endpoint_name": "router-3",
                },
            },
        )
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["provider"] == "anthropic-compatible"
    assert call["endpoint_name"] == "router-3"
    assert call["endpoint_base_url"] == "https://router-3.example/v1"


# ---- precedence: board-override > pool > column_override -------------------


async def test_dispatch_override_beats_pool_beats_column_override_for_endpoint():
    """Pin the explicit precedence from the card description: when more
    than one carrier pins the endpoint, the topmost carrier wins.
    Without this guarantee the dispatcher would non-deterministically
    pick whichever carrier happened to read last.
    """
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _seed_endpoint(s, "alpha", base_url="https://alpha.example/v1")
        await _seed_endpoint(s, "beta", base_url="https://beta.example/v1")
        await _seed_endpoint(s, "gamma", base_url="https://gamma.example/v1")
        await dispatch.set_active_subscription_override(
            s, PK, {
                "provider": "anthropic-compatible",
                "model": None,
                "endpoint_name": "alpha",
            },
        )
        await subscription_pool.set_subscription_pool(
            s, PK, [subscription_pool.PoolEntry(
                provider="anthropic-compatible", model=None,
                drempel=0.9, endpoint_name="beta",
            )],
        )
        await _make_column(s, default_provider="anthropic")
        cid = await _make_card(
            s, column_overrides={
                "engineer": {
                    "provider": "anthropic-compatible",
                    "model": None,
                    "endpoint_name": "gamma",
                },
            },
        )
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()

    assert transport.calls[0]["endpoint_name"] == "alpha"


async def test_dispatch_pool_beats_column_override_for_endpoint_when_no_override():
    """Drop the override; the pool takes precedence over the column_override."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _seed_endpoint(s, "beta", base_url="https://beta.example/v1")
        await _seed_endpoint(s, "gamma", base_url="https://gamma.example/v1")
        await subscription_pool.set_subscription_pool(
            s, PK, [subscription_pool.PoolEntry(
                provider="anthropic-compatible", model=None,
                drempel=0.9, endpoint_name="beta",
            )],
        )
        await _make_column(s, default_provider="anthropic")
        cid = await _make_card(
            s, column_overrides={
                "engineer": {
                    "provider": "anthropic-compatible",
                    "model": None,
                    "endpoint_name": "gamma",
                },
            },
        )
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()
    assert transport.calls[0]["endpoint_name"] == "beta"


async def test_dispatch_column_override_endpoint_used_when_alone():
    """Drop both the override and the pool; the column_override is the
    last carrier above the (currently-None) column.default_endpoint_name."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _seed_endpoint(s, "gamma", base_url="https://gamma.example/v1")
        await _make_column(s, default_provider="anthropic")
        cid = await _make_card(
            s, column_overrides={
                "engineer": {
                    "provider": "anthropic-compatible",
                    "model": None,
                    "endpoint_name": "gamma",
                },
            },
        )
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()
    assert transport.calls[0]["endpoint_name"] == "gamma"


# ---- endpoint-model fallback -----------------------------------------------


async def test_dispatch_uses_endpoint_model_when_pin_has_no_model():
    """A pool pin with ``model=None`` falls through to the endpoint's own
    ``model`` — same fall-through ``runs/router.py:543`` already enforces
    for the interactive path. The dispatch path must agree so the spawned
    CLI sees a non-empty ``ANTHROPIC_MODEL`` and ``build_provider_env``
    never raises on an empty model.
    """
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _seed_endpoint(
            s, "router-4",
            base_url="https://router-4.example/v1",
            model="claude-from-endpoint-4",
        )
        await subscription_pool.set_subscription_pool(
            s, PK, [subscription_pool.PoolEntry(
                provider="anthropic-compatible", model=None,
                drempel=0.9, endpoint_name="router-4",
            )],
        )
        await _make_column(s, default_provider="anthropic")
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()

    call = transport.calls[0]
    assert call["provider"] == "anthropic-compatible"
    assert call["model"] == "claude-from-endpoint-4"


# ---- fail-fast at dispatch time --------------------------------------------


async def test_dispatch_raises_when_endpoint_name_unknown():
    """Defense-in-depth: a row referencing a name that's missing from
    the registry is rejected at dispatch time even if the storage
    validation was bypassed (legacy row written by hand, schema
    downgrade, manual KanbanMeta edit, …). The companion test
    ``test_set_pool_rejects_compatible_with_unknown_endpoint_name``
    pins the storage-side rejection — this one pins the dispatch
    layer doesn't silently fall through.
    """
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Bypass storage validation: write a row directly to KanbanMeta
        # that references an endpoint name NOT in the registry.
        _seed_corrupt_pool_row(s, endpoint_name="nope")
        await _make_column(s, default_provider="anthropic")
        cid = await _make_card(s)
        await s.commit()

    with pytest.raises(ValueError):
        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
    assert transport.calls == []


async def test_set_pool_rejects_unresolvable_credential_at_save_time():
    """Gap 5 (kaart 27317b4871…): pool save with an endpoint whose
    ``credential_name`` is something the backend cannot resolve is
    rejected at save time by ``set_subscription_pool`` calling
    ``resolve_compatible_endpoint`` itself — the operator sees the
    exact error at the REST boundary instead of waiting for the
    dispatch loop to fail through ``MAX_DISPATCH_FAILURES``.

    Companion to ``test_dispatch_raises_when_credential_unresolvable``,
    which pins the defence-in-depth path that still rejects a row
    that bypassed the storage validation (legacy / manual KanbanMeta
    edit). Both are required: save-time rejection is the happy path,
    defence-in-depth catches the corruption-bypass case.
    """
    from app.services.agentic_cli.endpoints import upsert_endpoint
    from app.services.agentic_cli.endpoints import Endpoint
    async with KanbanSessionLocal() as s:
        await upsert_endpoint(
            s, PK, Endpoint(
                name="router-5", base_url="https://router-5.example/v1",
                model="m", credential_name="missing-secret-name",
            ),
        )
        await s.commit()
    with pytest.raises(ValueError, match="missing-secret-name"):
        async with KanbanSessionLocal() as s:
            await subscription_pool.set_subscription_pool(
                s, PK, [subscription_pool.PoolEntry(
                    provider="anthropic-compatible", model=None,
                    drempel=0.9, endpoint_name="router-5",
                )],
            )


async def test_dispatch_raises_when_credential_unresolvable():
    """Defence-in-depth: a row referencing a credential that the
    backend can't resolve still raises at dispatch time when the
    storage validation was bypassed (legacy row written by hand,
    schema downgrade, manual KanbanMeta edit, …). The companion
    test ``test_set_pool_rejects_unresolvable_credential_at_save_time``
    pins the storage-side rejection — this one pins the dispatch
    layer doesn't silently fall through when a corruption-bypass
    row sneaks in.
    """
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _seed_endpoint(
            s, "router-5", base_url="https://router-5.example/v1",
            credential_name="missing-secret-name",
        )
        # Bypass the storage validator (which now rejects the bad
        # carrier at save time) — write the pool row directly to
        # KanbanMeta so the dispatch path still has to defend.
        _seed_corrupt_pool_row(s, endpoint_name="router-5")
        await _make_column(s, default_provider="anthropic")
        cid = await _make_card(s)
        await s.commit()

    with pytest.raises(ValueError):
        async with KanbanSessionLocal() as s:
            await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
    assert transport.calls == []


# ---- non-regression: anthropic provider untouched -------------------------


async def test_dispatch_keeps_anthropic_default_when_no_compatible_pin():
    """Baseline: when nothing pins ``anthropic-compatible``, the
    default-provider chain resolves to ``anthropic`` and the transport
    receives no endpoint fields. This guards against the new threading
    step accidentally populating endpoint fields for the default
    provider path — the regression surface if someone wires the
    resolution unconditionally.
    """
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_column(s, default_provider="anthropic")
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["provider"] == "anthropic"
    assert call["endpoint_name"] is None
    assert call["endpoint_base_url"] is None
    assert call["endpoint_auth_token"] is None


# ---- kaart 27317b4871… FCR-gap regression pins ------------------------------
#
# The original card's acceptance criteria called out three concrete
# regression surfaces whose regression tests were either missing or
# only captured transport kwargs without verifying the env dict they
# ultimately produced. Pin them here so a future refactor can't drop
# the carrier without breaking a test.


async def test_sandcastle_rejects_anthropic_compatible_with_endpoint_kwargs():
    """Gap 1: the sandcastle transport refuses an
    ``anthropic-compatible`` + endpoint_* combo with a clear
    ``ValueError`` instead of silently billing the wrong upstream.
    Surface this at the dispatch boundary so the caller can either
    fall back to the worktree transport or strip the endpoint_name
    on the card / pool / override."""
    from app.kanban.dispatch import sandcastle_transport
    with pytest.raises(ValueError, match="sandcastle"):
        sandcastle_transport(
            directory="/p", prompt="x", session_name="s",
            provider="anthropic-compatible",
            endpoint_name="router-x",
            endpoint_base_url="https://router-x.example/v1",
            endpoint_auth_token="sk-test",
        )


def test_sandcastle_accepts_anthropic_compatible_without_endpoint_kwargs():
    """The sandcastle reject only fires when ``endpoint_*`` kwargs are
    set; passing ``provider='anthropic-compatible'`` alone is still
    accepted (the sandcastle config's ``agent_provider`` is then
    used — same as today). The actual sandbox-scheduling path is
    covered by other sandcastle tests in the suite; here we only
    prove the rejection branch isn't taken for the
    no-endpoint-kwargs case.
    """
    from app.kanban.dispatch import (
        PROVIDER_COMPATIBLE, sandcastle_transport,
    )
    assert PROVIDER_COMPATIBLE == "anthropic-compatible"
    with pytest.raises(ValueError) as exc_info:
        sandcastle_transport(
            directory="/p", prompt="x", session_name="s",
            provider=PROVIDER_COMPATIBLE,
        )
    # The endpoint-kwarg reject message names "sandcastle" + "endpoint_*";
    # a different ValueError (e.g. the sandbox scheduler complaining
    # about a missing sandcastle config in /p) means the endpoint-kwarg
    # reject branch was correctly skipped.
    assert "endpoint_name" not in str(exc_info.value)
    assert "endpoint_base_url" not in str(exc_info.value)
    assert "endpoint_auth_token" not in str(exc_info.value)


def test_card_create_rejects_anthropic_compatible_column_override_without_endpoint():
    """Gap 3: ``CardCreate`` validates ``column_overrides[col]`` and
    rejects ``provider='anthropic-compatible'`` without a non-empty
    ``endpoint_name`` so a UI typo can't sneak a partial carrier in."""
    from app.kanban.schemas import CardCreate
    with pytest.raises(Exception, match="endpoint_name"):
        CardCreate(
            project_key=PK,
            title="x",
            column_overrides={
                "Backlog": {"provider": "anthropic-compatible"},
            },
        )


def test_card_update_rejects_anthropic_compatible_column_override_without_endpoint():
    """Gap 3: the same validator runs on the PATCH path."""
    from app.kanban.schemas import CardUpdate
    with pytest.raises(Exception, match="endpoint_name"):
        CardUpdate(
            column_overrides={
                "Backlog": {"provider": "anthropic-compatible"},
            },
        )


def test_card_create_accepts_anthropic_compatible_with_endpoint_name():
    """Happy path: the validator only fires on the missing endpoint_name
    case; a complete carrier passes through unchanged."""
    from app.kanban.schemas import CardCreate
    payload = CardCreate(
        project_key=PK,
        title="x",
        column_overrides={
            "Backlog": {
                "provider": "anthropic-compatible",
                "endpoint_name": "router-y",
            },
        },
    )
    assert payload.column_overrides["Backlog"]["endpoint_name"] == "router-y"


def test_validate_default_provider_rejects_anthropic_compatible():
    """Gap 4: ``_validate_default_provider`` rejects
    ``PROVIDER_COMPATIBLE`` at the column level until
    ``KanbanColumn.default_endpoint_name`` lands (the column model has
    no migration path today, so the combo is un-dispatchable)."""
    from app.kanban import service
    with pytest.raises(ValueError, match="default_endpoint_name"):
        service._validate_default_provider("anthropic-compatible")


def test_validate_default_provider_accepts_known_non_compatible():
    """Companion to the reject test: the other allow-list providers
    still pass."""
    from app.kanban import service
    # None clears the pin — explicit escape hatch.
    service._validate_default_provider(None)
    # Non-compatible values still validate.
    service._validate_default_provider("anthropic")
    service._validate_default_provider("bedrock")
    service._validate_default_provider("minimax")


async def test_apply_operation_rejects_anthropic_compatible_column_override_without_endpoint():
    """Gap 3: the planning-pipeline ``apply_operation`` path that
    bypasses the ``CardCreate`` validator still rejects the bad
    carrier — pin that the in-process emitter path doesn't drift."""
    from app.kanban.models import KanbanCard
    from app.kanban.operations import apply_operation
    card_id = "card-1"
    async with KanbanSessionLocal() as s:
        s.add(KanbanCard(
            id=card_id, project_key=PK, title="x", column="Backlog",
            rank="r", title_hlc="r", description_hlc="r",
            column_hlc="r", rank_hlc="r",
        ))
        await s.commit()
    with pytest.raises(ValueError, match="endpoint_name"):
        async with KanbanSessionLocal() as s:
            await apply_operation(
                s,
                op_type="update", entity_type="card", project_key=PK,
                entity_id=card_id,
                payload={"column_overrides": {
                    "Backlog": {"provider": "anthropic-compatible"},
                }},
            )
