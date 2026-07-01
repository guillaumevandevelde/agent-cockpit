# backend/tests/test_kanban_api.py
import json

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.mark.asyncio
async def test_create_list_move_card():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "Build X"})
        assert r.status_code == 201, r.text
        cid = r.json()["id"]

        r = await ac.get("/api/v1/kanban/cards", params={"project_key": "P"})
        assert any(c["id"] == cid for c in r.json()["items"])

        r = await ac.post(f"/api/v1/kanban/cards/{cid}/move", json={"column": "Doing"})
        assert r.status_code == 200
        assert r.json()["column"] == "Doing"


@pytest.mark.asyncio
async def test_reorder_cards_sets_rank_order():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        ids = []
        for title in ("A", "B", "C"):
            r = await ac.post("/api/v1/kanban/cards",
                json={"project_key": "P", "title": title, "column": "Backlog"})
            ids.append(r.json()["id"])

        # Reverse the order: C, B, A
        reordered = list(reversed(ids))
        r = await ac.post("/api/v1/kanban/cards/reorder",
            json={"project_key": "P", "column": "Backlog", "ordered_ids": reordered})
        assert r.status_code == 200, r.text

        r = await ac.get("/api/v1/kanban/cards",
            params={"project_key": "P", "column": "Backlog"})
        got = [c["id"] for c in r.json()["items"]]
        assert got == reordered
        ranks = [c["rank"] for c in r.json()["items"]]
        assert ranks == sorted(ranks)


@pytest.mark.asyncio
async def test_reorder_ignores_unknown_ids_and_keeps_column():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        a = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "A", "column": "Backlog"})).json()["id"]
        b = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "B", "column": "Backlog"})).json()["id"]

        r = await ac.post("/api/v1/kanban/cards/reorder",
            json={"project_key": "P", "column": "Backlog",
                  "ordered_ids": [b, "does-not-exist", a]})
        assert r.status_code == 200, r.text

        r = await ac.get("/api/v1/kanban/cards",
            params={"project_key": "P", "column": "Backlog"})
        items = r.json()["items"]
        assert [c["id"] for c in items] == [b, a]
        assert all(c["column"] == "Backlog" for c in items)


@pytest.mark.asyncio
async def test_claim_conflict_returns_409():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "t"})).json()["id"]
        r1 = await ac.post(f"/api/v1/kanban/cards/{cid}/claim",
            json={"claimed_by": "first@d"})
        assert r1.status_code == 200
        r2 = await ac.post(f"/api/v1/kanban/cards/{cid}/claim",
            json={"claimed_by": "second@d"})
        assert r2.status_code == 409, r2.text


@pytest.mark.asyncio
async def test_enable_writes_mcp_entry(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/enable",
            json={"project_path": str(tmp_path)})
        assert r.status_code == 200, r.text
        assert r.json()["project_key"]
        mcp_file = tmp_path / ".mcp.json"
        assert mcp_file.exists()
        assert "cockpit-kanban" in mcp_file.read_text()


@pytest.mark.asyncio
async def test_enable_mcp_url_derives_from_request(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://example.test") as ac:
        r = await ac.post("/api/v1/kanban/enable",
            json={"project_path": str(tmp_path)})
        assert r.status_code == 200, r.text
    data = json.loads((tmp_path / ".mcp.json").read_text())
    url = data["mcpServers"]["cockpit-kanban"]["url"]
    assert url == "http://example.test/kanban-mcp/sse"


@pytest.mark.asyncio
async def test_enable_mcp_url_honours_public_base_url(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "public_base_url", "https://cockpit.example.com")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/enable",
            json={"project_path": str(tmp_path)})
        assert r.status_code == 200, r.text
    data = json.loads((tmp_path / ".mcp.json").read_text())
    url = data["mcpServers"]["cockpit-kanban"]["url"]
    assert url == "https://cockpit.example.com/kanban-mcp/sse"


@pytest.mark.asyncio
async def test_max_sessions_defaults_to_4():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/v1/kanban/max-sessions", params={"project_key": "p1"})
        assert r.status_code == 200
        assert r.json()["max_sessions"] == 4


@pytest.mark.asyncio
async def test_set_max_sessions_roundtrip():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/max-sessions",
                          json={"project_key": "p1", "max_sessions": 3})
        assert r.status_code == 200
        g = await ac.get("/api/v1/kanban/max-sessions", params={"project_key": "p1"})
        assert g.json()["max_sessions"] == 3


@pytest.mark.asyncio
async def test_set_max_sessions_rejects_zero():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/max-sessions",
                          json={"project_key": "p1", "max_sessions": 0})
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_transport_defaults_worktree_and_roundtrips():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/v1/kanban/transport", params={"project_key": "p2"})
        assert r.json()["transport"] == "worktree"
        s = await ac.post("/api/v1/kanban/transport",
                          json={"project_key": "p2", "transport": "sandcastle"})
        assert s.status_code == 200
        g = await ac.get("/api/v1/kanban/transport", params={"project_key": "p2"})
        assert g.json()["transport"] == "sandcastle"


@pytest.mark.asyncio
async def test_transport_rejects_unknown():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/transport",
                          json={"project_key": "p2", "transport": "podman"})
        assert r.status_code == 422
