# backend/tests/test_kanban_operations.py
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.kanban.db import KanbanBase, kanban_engine, KanbanSessionLocal
from app.kanban import models


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with kanban_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.drop_all)
        await conn.run_sync(KanbanBase.metadata.create_all)
    yield


@pytest.mark.asyncio
async def test_can_persist_an_op_row():
    async with KanbanSessionLocal() as s:
        s.add(models.KanbanOp(
            op_id="dev-a:1", device_id="dev-a", seq=1, hlc="1:0:dev-a",
            project_key="git:example", entity_type="card", entity_id="c1",
            op_type="create", payload={"title": "x", "column": "Backlog"},
        ))
        await s.commit()
        rows = (await s.execute(select(models.KanbanOp))).scalars().all()
        assert len(rows) == 1
        assert rows[0].payload["title"] == "x"


from app.kanban.operations import apply_operation, get_device_id
from app.kanban.models import KanbanCard


@pytest.mark.asyncio
async def test_create_card_materializes_a_card_row():
    async with KanbanSessionLocal() as s:
        card_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "First", "description": "d", "column": "Backlog"},
        )
        await s.commit()
        card = await s.get(KanbanCard, card_id)
        assert card is not None
        assert card.title == "First"
        assert card.column == "Backlog"
        assert card.title_hlc is not None


@pytest.mark.asyncio
async def test_device_id_is_stable():
    async with KanbanSessionLocal() as s:
        a = await get_device_id(s)
        b = await get_device_id(s)
        await s.commit()
        assert a == b and len(a) > 0


@pytest.mark.asyncio
async def test_move_updates_column_with_lww():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None,
            payload={"title": "t", "column": "Backlog"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id=cid, payload={"column": "Doing"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.column == "Doing"


@pytest.mark.asyncio
async def test_stale_move_is_ignored_by_lww():
    # An op with an older HLC than the field's current HLC must not win.
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        card = await s.get(KanbanCard, cid)
        card.column = "Review"
        card.column_hlc = "9999999999999:00000:dev-z"  # far-future HLC
        await s.flush()
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id=cid, payload={"column": "Done"})
        await s.commit()
        refreshed = await s.get(KanbanCard, cid)
        assert refreshed.column == "Review"  # stale move rejected


@pytest.mark.asyncio
async def test_update_title_and_description():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "old"})
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="p", entity_id=cid,
            payload={"title": "new", "description": "desc"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.title == "new"
        assert card.description == "desc"


from app.kanban.operations import ClaimRejected


@pytest.mark.asyncio
async def test_claim_sets_owner():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="p", entity_id=cid, payload={"claimed_by": "sess1@devA"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.claimed_by == "sess1@devA"


@pytest.mark.asyncio
async def test_second_claim_is_rejected():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="p", entity_id=cid, payload={"claimed_by": "first@devA"})
        with pytest.raises(ClaimRejected):
            await apply_operation(s, op_type="claim", entity_type="card",
                project_key="p", entity_id=cid, payload={"claimed_by": "second@devB"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.claimed_by == "first@devA"


@pytest.mark.asyncio
async def test_release_clears_owner():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="p", entity_id=cid, payload={"claimed_by": "a@d"})
        await apply_operation(s, op_type="release", entity_type="card",
            project_key="p", entity_id=cid, payload={})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.claimed_by is None


from app.kanban.models import KanbanDeliverable, KanbanOp
from sqlalchemy import select as _select


@pytest.mark.asyncio
async def test_attach_deliverable_creates_row():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="p", entity_id=cid,
            payload={"kind": "pr", "ref": "https://github.com/u/r/pull/7"})
        await s.commit()
        rows = (await s.execute(_select(KanbanDeliverable))).scalars().all()
        assert len(rows) == 1
        assert rows[0].kind == "pr"
        assert rows[0].card_id == cid


@pytest.mark.asyncio
async def test_comment_op_is_recorded_in_log():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="p", entity_id=cid, payload={"text": "looks good"})
        await s.commit()
        ops = (await s.execute(
            _select(KanbanOp).where(KanbanOp.op_type == "comment"))).scalars().all()
        assert ops[0].payload["text"] == "looks good"


@pytest.mark.asyncio
async def test_mutation_ops_inherit_card_project_key():
    # Callers pass project_key="" on move/claim/etc; the op-log must still
    # record the owning card's key (needed for per-project sync/filtering).
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="git:proj-a", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=cid, payload={"column": "Doing"})
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="", entity_id=cid, payload={"kind": "note", "ref": "x"})
        await s.commit()
        ops = (await s.execute(_select(KanbanOp))).scalars().all()
        assert {o.project_key for o in ops} == {"git:proj-a"}


@pytest.mark.asyncio
async def test_op_ids_are_unique_across_many_ops():
    async with KanbanSessionLocal() as s:
        for i in range(25):
            await apply_operation(s, op_type="create", entity_type="card",
                project_key="p", entity_id=None, payload={"title": f"c{i}"})
        await s.commit()
        op_ids = [o.op_id for o in (await s.execute(_select(KanbanOp))).scalars().all()]
        assert len(op_ids) == 25
        assert len(set(op_ids)) == 25  # no collisions
