# backend/tests/test_kanban_mcp.py
import pytest
import pytest_asyncio

from app.kanban.db import KanbanBase, kanban_engine
from app.kanban import mcp_server as m


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with kanban_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.drop_all)
        await conn.run_sync(KanbanBase.metadata.create_all)
    yield


@pytest.mark.asyncio
async def test_create_then_list_then_claim():
    created = await m.create_card("P", "Do the thing", "details")
    cid = created["id"]
    listed = await m.list_cards("P")
    assert any(c["id"] == cid for c in listed)
    claimed = await m.claim_card(cid, "sess1@devA")
    assert claimed["claimed_by"] == "sess1@devA"


@pytest.mark.asyncio
async def test_claim_conflict_returns_error_dict():
    cid = (await m.create_card("P", "t", ""))["id"]
    await m.claim_card(cid, "first@d")
    result = await m.claim_card(cid, "second@d")
    assert result["error"] == "already_claimed"
    assert result["owner"] == "first@d"
