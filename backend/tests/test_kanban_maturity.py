# backend/tests/test_kanban_maturity.py
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.kanban import dispatch
from app.kanban.operations import apply_operation
from app.kanban.service import get_card
from app.main import app
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

pytestmark = pytest.mark.asyncio

PK = "git:example.com/me/repo"

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _make_card(s, title="Task", column="Todo", agent=None):
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None, payload={"title": title, "column": column, "agent": agent},
    )
    await s.flush()
    return cid


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class RecordingTransport:
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


# ---- A: delete -------------------------------------------------------------

async def test_delete_op_removes_card_and_deliverables():
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="", entity_id=cid, payload={"kind": "note", "ref": "x"})
        await s.commit()
        await apply_operation(s, op_type="delete", entity_type="card",
            project_key="", entity_id=cid, payload={})
        await s.commit()
        assert await get_card(s, cid) is None


async def test_delete_endpoint_removes_card():
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
    async with await _client() as c:
        r = await c.delete(f"/api/v1/kanban/cards/{cid}")
    assert r.status_code == 204
    async with KanbanSessionLocal() as s:
        assert await get_card(s, cid) is None


# ---- B: per-card agent -----------------------------------------------------

async def test_create_and_update_set_agent():
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, agent="kanban-analyst")
        await s.flush()
        assert (await get_card(s, cid)).agent == "kanban-analyst"
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=cid, payload={"agent": "kanban-developer"})
        await s.flush()
        assert (await get_card(s, cid)).agent == "kanban-developer"


async def test_card_response_exposes_agent():
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, agent="kanban-developer")
        await s.commit()
    async with await _client() as c:
        r = await c.get(f"/api/v1/kanban/cards/{cid}")
    assert r.status_code == 200
    assert r.json()["agent"] == "kanban-developer"


async def test_list_agents_endpoint(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "kanban-developer.md").write_text("dev")
    (agents / "kanban-analyst.md").write_text("analyst")
    (agents / "notes.txt").write_text("ignored")
    async with await _client() as c:
        r = await c.get("/api/v1/kanban/agents", params={"project_path": str(tmp_path)})
    assert r.status_code == 200
    assert r.json()["agents"] == ["kanban-analyst", "kanban-developer"]


async def test_list_agents_missing_dir_is_empty(tmp_path):
    async with await _client() as c:
        r = await c.get("/api/v1/kanban/agents", params={"project_path": str(tmp_path)})
    assert r.json()["agents"] == []


async def test_persona_for_card_prefers_explicit_agent(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "analyst.md").write_text("ANALYST BODY")
    (agents / "custom.md").write_text("CUSTOM BODY")

    class _Card:
        agent = "custom"
    # column default would be the developer persona, but the explicit agent wins
    assert dispatch._persona_for_card(str(tmp_path), _Card(), "developer") == "CUSTOM BODY"

    class _NoAgent:
        agent = None
    assert dispatch._persona_for_card(str(tmp_path), _NoAgent(), "analyst") == "ANALYST BODY"


# ---- B: manual dispatch ----------------------------------------------------

async def test_dispatch_card_uses_per_card_agent_and_spawns(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "custom.md").write_text("You are the Custom agent.")
    t = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, column="Backlog", agent="custom")  # not auto-dispatchable
        await s.commit()
    async with KanbanSessionLocal() as s:
        res = await dispatch.dispatch_card(
            s, card_id=cid, project_path=str(tmp_path), transport=t)
        await s.commit()
        card = await get_card(s, cid)
    assert res is not None
    assert card.column == "custom"
    assert card.claimed_by.startswith("agent:")
    assert "You are the Custom agent." in t.calls[0]["prompt"]


async def test_dispatch_card_missing_returns_none(tmp_path):
    t = RecordingTransport()
    async with KanbanSessionLocal() as s:
        res = await dispatch.dispatch_card(
            s, card_id="does-not-exist", project_path=str(tmp_path), transport=t)
    assert res is None
    assert t.calls == []
