# backend/tests/test_kanban_shipmode.py
import pytest
import pytest_asyncio

from app.kanban.db import KanbanBase, kanban_engine, KanbanSessionLocal
from app.kanban import dispatch

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with kanban_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.drop_all)
        await conn.run_sync(KanbanBase.metadata.create_all)
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
