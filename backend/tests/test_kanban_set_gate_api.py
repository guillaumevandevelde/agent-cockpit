"""REST mirror of the MCP ``set_card_gate`` tool — see
``tests/test_kanban_mcp.py::test_set_card_gate_*`` for the MCP-side
regressions. The REST endpoint is a thin wrapper over the same business
logic, but the contract here is different: HTTP status codes, JSON
shape, and the kanban-board filter pipeline that the dispatcher reads
on the next tick.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.kanban import service
from app.kanban.dispatch import _is_gated
from app.kanban.service import card_activity
from app.main import app
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest_asyncio.fixture
async def _client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        yield ac


async def _create_card(client, title="gated") -> str:
    r = await client.post(
        "/api/v1/kanban/cards",
        json={"project_key": "P", "title": title, "description": ""},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_set_gate_writes_metadata_gated_on(_client):
    cid = await _create_card(_client)
    r = await _client.post(
        f"/api/v1/kanban/cards/{cid}/set-gate",
        json={"gated_on": "second-executor-provider-onboarded"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["metadata"]["gated_on"] == "second-executor-provider-onboarded"
    # The dispatcher picks the new state up on the next tick. Mirrors the
    # MCP-side smoke test: round-trip the value through the kanban DB and
    # confirm `_is_gated` (the predicate the dispatcher reads) sees it.
    from app.kanban.db import KanbanSessionLocal
    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, cid)
        assert _is_gated(card) is True


@pytest.mark.asyncio
async def test_set_gate_clear_with_null_lifts_the_gate(_client):
    cid = await _create_card(_client)
    await _client.post(
        f"/api/v1/kanban/cards/{cid}/set-gate",
        json={"gated_on": "trigger-x"},
    )
    from app.kanban.db import KanbanSessionLocal
    async with KanbanSessionLocal() as s:
        assert _is_gated(await service.get_card(s, cid)) is True

    r = await _client.post(
        f"/api/v1/kanban/cards/{cid}/set-gate",
        json={"gated_on": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Null removes the key entirely (not stored as JSON null) — see MCP test.
    assert "gated_on" not in (body["metadata"] or {})
    async with KanbanSessionLocal() as s:
        assert _is_gated(await service.get_card(s, cid)) is False


@pytest.mark.asyncio
async def test_set_gate_empty_string_lifts_the_gate(_client):
    cid = await _create_card(_client)
    await _client.post(
        f"/api/v1/kanban/cards/{cid}/set-gate",
        json={"gated_on": "trigger-x"},
    )
    # Empty string → fail-open clear (mirrors the MCP path).
    r = await _client.post(
        f"/api/v1/kanban/cards/{cid}/set-gate",
        json={"gated_on": ""},
    )
    assert r.status_code == 200, r.text
    assert "gated_on" not in (r.json()["metadata"] or {})


@pytest.mark.asyncio
async def test_set_gate_posts_audit_comment(_client):
    """Every set/clear call posts a `**Gate:** ...` comment on the activity
    feed so the gate's history is visible without inspecting metadata. This
    is the same contract as the MCP tool — the REST endpoint is a thin
    HTTP wrapper over identical business logic."""
    cid = await _create_card(_client)
    await _client.post(
        f"/api/v1/kanban/cards/{cid}/set-gate",
        json={"gated_on": "trigger-x"},
    )
    await _client.post(
        f"/api/v1/kanban/cards/{cid}/set-gate",
        json={"gated_on": None},
    )
    from app.kanban.db import KanbanSessionLocal
    async with KanbanSessionLocal() as s:
        activity = await card_activity(s, cid)
    gate_comments = [
        op for op in activity
        if op.op_type == "comment"
        and op.payload.get("text", "").startswith("**Gate:**")
    ]
    assert len(gate_comments) == 2, (
        f"expected one set + one cleared audit comment, got {len(gate_comments)}: "
        f"{[c.payload.get('text') for c in gate_comments]}"
    )


@pytest.mark.asyncio
async def test_set_gate_returns_404_for_missing_card(_client):
    r = await _client.post(
        "/api/v1/kanban/cards/does-not-exist/set-gate",
        json={"gated_on": "trigger-x"},
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_set_gate_preserves_other_metadata_keys(_client):
    cid = await _create_card(_client)
    # Seed unrelated metadata via the PATCH endpoint.
    await _client.patch(
        f"/api/v1/kanban/cards/{cid}",
        json={"metadata": {"external_ref": "JIRA-123", "owner": "team-x"}},
    )
    # Set + clear a gate; the unrelated keys must survive both.
    await _client.post(
        f"/api/v1/kanban/cards/{cid}/set-gate",
        json={"gated_on": "trigger-y"},
    )
    r = await _client.post(
        f"/api/v1/kanban/cards/{cid}/set-gate",
        json={"gated_on": None},
    )
    md = r.json()["metadata"] or {}
    assert "gated_on" not in md
    assert md.get("external_ref") == "JIRA-123"
    assert md.get("owner") == "team-x"
