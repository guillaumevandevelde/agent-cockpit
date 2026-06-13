# backend/tests/test_kanban_api.py
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.kanban.db import KanbanBase, kanban_engine


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with kanban_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.drop_all)
        await conn.run_sync(KanbanBase.metadata.create_all)
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
