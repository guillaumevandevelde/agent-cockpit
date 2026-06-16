# backend/tests/test_kanban_shipmode.py
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.kanban_test_db import TestSessionLocal, reset_test_tables
from app.kanban import dispatch
from app.main import app

pytestmark = pytest.mark.asyncio

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


PK = "git:example.com/me/repo"


async def test_ship_mode_defaults_to_pull_request():
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_ship_mode(s, PK) == "pull-request"


async def test_set_and_get_ship_mode():
    async with KanbanSessionLocal() as s:
        await dispatch.set_ship_mode(s, PK, "direct")
        await s.commit()
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_ship_mode(s, PK) == "direct"


async def test_update_ship_mode():
    async with KanbanSessionLocal() as s:
        await dispatch.set_ship_mode(s, PK, "direct")
        await s.commit()
    async with KanbanSessionLocal() as s:
        await dispatch.set_ship_mode(s, PK, "pull-request")
        await s.commit()
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_ship_mode(s, PK) == "pull-request"


async def test_set_ship_mode_rejects_unknown():
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await dispatch.set_ship_mode(s, PK, "yolo")


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_get_shipmode_endpoint_defaults():
    async with await _client() as c:
        r = await c.get("/api/v1/kanban/shipmode", params={"project_key": PK})
    assert r.status_code == 200
    assert r.json() == {"project_key": PK, "mode": "pull-request"}


async def test_post_shipmode_endpoint_sets_value():
    async with await _client() as c:
        r = await c.post("/api/v1/kanban/shipmode", json={"project_key": PK, "mode": "direct"})
        assert r.status_code == 200
        assert r.json() == {"project_key": PK, "mode": "direct"}
        r2 = await c.get("/api/v1/kanban/shipmode", params={"project_key": PK})
    assert r2.json()["mode"] == "direct"


async def test_post_shipmode_rejects_unknown():
    async with await _client() as c:
        r = await c.post("/api/v1/kanban/shipmode", json={"project_key": PK, "mode": "yolo"})
    assert r.status_code == 422
