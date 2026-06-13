# backend/tests/test_kanban_rematerialize.py
import pytest
import pytest_asyncio
from sqlalchemy import select, delete

from app.kanban.db import KanbanBase, kanban_engine, KanbanSessionLocal
from app.kanban.models import KanbanCard, KanbanDeliverable
from app.kanban.operations import apply_operation, rematerialize


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with kanban_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.drop_all)
        await conn.run_sync(KanbanBase.metadata.create_all)
    yield


@pytest.mark.asyncio
async def test_rematerialize_rebuilds_state_from_oplog():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t", "column": "Backlog"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id=cid, payload={"column": "Doing"})
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="p", entity_id=cid, payload={"kind": "note", "ref": "x"})
        await s.commit()

    # Wipe ONLY the materialized tables, keep the op-log.
    async with KanbanSessionLocal() as s:
        await s.execute(delete(KanbanDeliverable))
        await s.execute(delete(KanbanCard))
        await s.commit()

    async with KanbanSessionLocal() as s:
        await rematerialize(s)
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card is not None and card.column == "Doing"
        delivs = (await s.execute(select(KanbanDeliverable))).scalars().all()
        assert len(delivs) == 1
