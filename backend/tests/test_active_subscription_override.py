# backend/tests/test_active_subscription_override.py
"""Tests for the board-wide active-subscription-override ("quick win" from
docs/cockpit/subscription-flexibiliteit-analyse.md §5 fase 0 / §8 #1).

Precedence: ``active_subscription_override`` > ``card.column_overrides[col]`` >
``column.default_*``. ``null`` is the no-override state; the dispatcher MUST
behave exactly as before (this is the backward-compat guarantee from the
acceptance criteria).
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.kanban import dispatch, service
from app.kanban.operations import apply_operation
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
    """A real (non-mock) transport that records calls. Same shape as
    test_kanban_dispatch.py's RecordingTransport, duplicated here so this
    file is self-contained and not coupled to that module's helpers."""

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
                           "endpoint_name": endpoint_name,
                           "endpoint_base_url": endpoint_base_url,
                           "endpoint_auth_token": endpoint_auth_token,
                           "card_id": card_id, "column_name": column_name})
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}


async def _make_card(s, title="Task", column="Backlog"):
    return await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None,
        payload={"title": title, "column": column},
    )


def _override(*, provider="anthropic", model=None, endpoint_name=None):
    """Build a clean override dict — explicit about which keys are set so a
    None model survives the JSON round-trip as null (the storage shape).

    ``endpoint_name`` is the third carrier added by kaart 293d1faa…; it
    is ``None`` for every provider except ``"anthropic-compatible"`` so
    legacy tests keep matching without ceremony.
    """
    return {
        "provider": provider,
        "model": model,
        "endpoint_name": endpoint_name,
    }


# ---- storage layer: get / set on KanbanMeta ---------------------------------

@pytest.mark.asyncio
async def test_active_override_defaults_to_none():
    """Backward-compat: a project that never set an override reads back None."""
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_active_subscription_override(s, PK) is None


@pytest.mark.asyncio
async def test_set_and_get_active_override():
    """Stored override round-trips through the KanbanMeta key-value table."""
    async with KanbanSessionLocal() as s:
        await dispatch.set_active_subscription_override(
            s, PK, _override(provider="minimax", model="MiniMax-M3[1m]"),
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_active_subscription_override(
            s, PK,
        ) == _override(provider="minimax", model="MiniMax-M3[1m]")


@pytest.mark.asyncio
async def test_set_active_override_to_none_clears_it():
    """Setting to None removes the row so a future read sees no override."""
    async with KanbanSessionLocal() as s:
        await dispatch.set_active_subscription_override(
            s, PK, _override(provider="minimax"),
        )
        await s.commit()
        await dispatch.set_active_subscription_override(s, PK, None)
        await s.commit()
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_active_subscription_override(s, PK) is None


@pytest.mark.asyncio
async def test_set_active_override_overwrites_previous():
    async with KanbanSessionLocal() as s:
        await dispatch.set_active_subscription_override(
            s, PK, _override(provider="minimax"),
        )
        await s.commit()
        await dispatch.set_active_subscription_override(
            s, PK, _override(provider="bedrock"),
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_active_subscription_override(
            s, PK,
        ) == _override(provider="bedrock")


# ---- anthropic-compatible: endpoint_name carrier + fail-fast --------------
#
# Card 293d1faa…: the override dict gains an optional ``endpoint_name``
# field. When the provider is ``anthropic-compatible`` and no
# endpoint_name is set, the storage layer must refuse — a row that
# dispatch would have to abandon in MAX_DISPATCH_FAILURES is silently
# broken at write time. The dispatch helper does its own fail-fast too
# (see ``test_dispatch_compatible_endpoint.py``), but storage-time
# validation means the API can return a 422 in place of letting the
# operator watch their card migrate to Impediment.


@pytest.mark.asyncio
async def test_set_override_accepts_anthropic_compatible_provider_with_endpoint():
    """The allow-list grows to include ``anthropic-compatible`` so the
    override can route to a named endpoint. Mirrors the pool decision in
    ``test_subscription_pool_storage.py``."""
    async with KanbanSessionLocal() as s:
        from app.services.agentic_cli.endpoints import Endpoint, upsert_endpoint
        await upsert_endpoint(
            s, PK, Endpoint(
                name="router-ov", base_url="https://router-ov.example/v1",
                model="claude-test-ov",
            ),
        )
        await dispatch.set_active_subscription_override(
            s, PK, {
                "provider": "anthropic-compatible",
                "model": None,
                "endpoint_name": "router-ov",
            },
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_active_subscription_override(s, PK) == {
            "provider": "anthropic-compatible",
            "model": None,
            "endpoint_name": "router-ov",
        }


@pytest.mark.asyncio
async def test_set_override_rejects_compatible_without_endpoint_name():
    """``anthropic-compatible`` provider without ``endpoint_name`` is
    rejected at storage so the dispatcher never has to fail at
    dispatch time."""
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await dispatch.set_active_subscription_override(
                s, PK, {
                    "provider": "anthropic-compatible",
                    "model": None,
                },
            )


@pytest.mark.asyncio
async def test_set_override_rejects_compatible_with_unknown_endpoint_name():
    """Endpoint name present but not in the registry is rejected so the
    saved row is always dispatchable end-to-end."""
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await dispatch.set_active_subscription_override(
                s, PK, {
                    "provider": "anthropic-compatible",
                    "model": None,
                    "endpoint_name": "missing",
                },
            )


# ---- dispatch precedence ---------------------------------------------------

@pytest.mark.asyncio
async def test_global_override_beats_column_default_provider():
    """Fase-0 acceptance criterion: a board-wide override routes to that
    subscription, ignoring column.default_provider."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer", default_provider="anthropic",
        )
        cid = await _make_card(s)
        await dispatch.set_active_subscription_override(
            s, PK, _override(provider="minimax"),
        )
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "minimax"


@pytest.mark.asyncio
async def test_global_override_beats_column_default_model():
    """Fase-0 acceptance criterion: column.default_model loses to the override's
    model when one is supplied."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer", default_model="sonnet",
        )
        cid = await _make_card(s)
        await dispatch.set_active_subscription_override(
            s, PK, _override(provider="minimax", model="MiniMax-M3[1m]"),
        )
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "minimax"
    assert transport.calls[0]["model"] == "MiniMax-M3[1m]"


@pytest.mark.asyncio
async def test_null_override_is_backward_compatible():
    """When the override is unset (None), dispatch falls through to column
    defaults exactly as it does today. This is the backward-compat clause
    from the acceptance criteria — verifier of the regression-prevention
    contract."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer", default_provider="minimax",
        )
        cid = await _make_card(s)
        # No set_active_subscription_override call.
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "minimax"


@pytest.mark.asyncio
async def test_global_override_with_no_model_lets_column_default_apply():
    """The override can set provider only — the model falls through to
    column.default_model. Same shape as the column-override rule, just one
    level higher in the precedence (acceptance criteria: provider + model?,
    model is optional)."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer",
            default_agent="engineer", default_model="sonnet",
        )
        cid = await _make_card(s)
        await dispatch.set_active_subscription_override(
            s, PK, _override(provider="bedrock", model=None),
        )
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "bedrock"
    assert transport.calls[0]["model"] == "sonnet"


@pytest.mark.asyncio
async def test_null_override_preserves_dispatch_baseline():
    """Same backward-compat guarantee but against the default Anthropic
    fallback: when nothing is set anywhere, the dispatch is anthropic / no
    model — exact pre-feature behaviour."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "anthropic"
    assert transport.calls[0]["model"] is None


# ---- REST API --------------------------------------------------------------

def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_get_subscription_override_endpoint_default():
    async with _client() as c:
        r = await c.get(
            "/api/v1/kanban/subscription-override",
            params={"project_key": PK},
        )
    assert r.status_code == 200
    assert r.json() == {"project_key": PK, "override": None}


@pytest.mark.asyncio
async def test_post_and_get_subscription_override_endpoint():
    body = {"project_key": PK,
            "override": {"provider": "minimax", "model": "MiniMax-M3[1m]",
                         "endpoint_name": None}}
    async with _client() as c:
        r = await c.post(
            "/api/v1/kanban/subscription-override", json=body,
        )
        assert r.status_code == 200
        r2 = await c.get(
            "/api/v1/kanban/subscription-override",
            params={"project_key": PK},
        )
    assert r2.json()["override"] == body["override"]


@pytest.mark.asyncio
async def test_post_subscription_override_clear():
    """Clearing the override is a normal POST with `null`, not a separate
    DELETE — matches how project-wide meta-toggles like shipmode and
    autodispatch are flipped from the same endpoint."""
    async with _client() as c:
        await c.post(
            "/api/v1/kanban/subscription-override",
            json={"project_key": PK,
                  "override": {"provider": "minimax", "model": None,
                               "endpoint_name": None}},
        )
        r = await c.post(
            "/api/v1/kanban/subscription-override",
            json={"project_key": PK, "override": None},
        )
        assert r.status_code == 200
        r2 = await c.get(
            "/api/v1/kanban/subscription-override",
            params={"project_key": PK},
        )
    assert r2.json()["override"] is None
