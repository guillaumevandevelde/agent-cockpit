# backend/tests/test_kanban_service.py
import pytest
import pytest_asyncio

from app.kanban import service
from app.kanban.operations import apply_operation
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

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


@pytest.mark.asyncio
async def test_column_default_platform_roundtrip():
    async with KanbanSessionLocal() as s:
        col = await service.create_column(
            s, project_key="A", name="engineer", default_agent="engineer",
            default_platform="minimax",
        )
        await s.commit()
        assert col.default_platform == "minimax"
        assert await service.get_column_default_platform(s, "A", "engineer") == "minimax"


@pytest.mark.asyncio
async def test_column_default_platform_missing_column_returns_none():
    async with KanbanSessionLocal() as s:
        assert await service.get_column_default_platform(s, "A", "no-such-column") is None


@pytest.mark.asyncio
async def test_update_column_can_set_default_platform():
    async with KanbanSessionLocal() as s:
        col = await service.create_column(s, project_key="A", name="engineer")
        await s.commit()
        updated = await service.update_column(s, col.id, default_platform="minimax")
        await s.commit()
        assert updated.default_platform == "minimax"

# NOTE: the sync-seam tests (ops_since / ingest_ops convergence + idempotent replay)
# were removed when sync.py was pruned. See docs/cockpit/sync-hlc-freeze-vs-prune.md.
# Idempotent HLC-ordered replay of the *local* op-log stays covered by
# test_kanban_rematerialize.py.
