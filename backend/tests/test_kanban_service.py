# backend/tests/test_kanban_service.py
import pytest
import pytest_asyncio

from tests.kanban_test_db import TestSessionLocal, reset_test_tables
from app.kanban.operations import apply_operation
from app.kanban import service

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.mark.asyncio
async def test_list_cards_filters_by_project_and_column():
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None, payload={"title": "a1", "column": "Todo"})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None, payload={"title": "a2", "column": "Done"})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="B", entity_id=None, payload={"title": "b1", "column": "Todo"})
        await s.commit()
        all_a = await service.list_cards(s, "A")
        assert {c.title for c in all_a} == {"a1", "a2"}
        todo_a = await service.list_cards(s, "A", column="Todo")
        assert {c.title for c in todo_a} == {"a1"}


@pytest.mark.asyncio
async def test_card_activity_returns_oplog_for_card():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None, payload={"title": "a"})
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="A", entity_id=cid, payload={"text": "hi"})
        await s.commit()
        feed = await service.card_activity(s, cid)
        assert [e.op_type for e in feed] == ["create", "comment"]


from app.kanban import sync as sync_mod


@pytest.mark.asyncio
async def test_ops_since_returns_ops_after_cursor():
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "a"})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "b"})
        await s.commit()
        first_two = await sync_mod.ops_since(s, cursor=None)
        assert len(first_two) == 2
        after = await sync_mod.ops_since(s, cursor=first_two[0].hlc)
        assert len(after) == 1
        assert after[0].payload["title"] == "b"


@pytest.mark.asyncio
async def test_ingest_foreign_ops_then_rematerialize():
    foreign = {
        "op_id": "devB:1", "device_id": "devB", "seq": 1,
        "hlc": "9999999999999:00000:devB", "project_key": "p",
        "entity_type": "card", "entity_id": "extern1", "op_type": "create",
        "payload": {"title": "fromB", "column": "Backlog"},
    }
    async with KanbanSessionLocal() as s:
        await sync_mod.ingest_ops(s, [foreign])
        await s.commit()
        from app.kanban.models import KanbanCard
        card = await s.get(KanbanCard, "extern1")
        assert card is not None and card.title == "fromB"


@pytest.mark.asyncio
async def test_ingest_is_idempotent():
    foreign = {
        "op_id": "devB:1", "device_id": "devB", "seq": 1,
        "hlc": "9999999999999:00000:devB", "project_key": "p",
        "entity_type": "card", "entity_id": "extern1", "op_type": "create",
        "payload": {"title": "fromB"},
    }
    async with KanbanSessionLocal() as s:
        await sync_mod.ingest_ops(s, [foreign])
        await sync_mod.ingest_ops(s, [foreign])  # second time = no-op
        await s.commit()
        from sqlalchemy import select, func
        from app.kanban.models import KanbanOp
        n = (await s.execute(select(func.count()).select_from(KanbanOp))).scalar()
        assert n == 1
