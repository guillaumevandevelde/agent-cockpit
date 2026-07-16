"""Tests for the add_plan_attachment MCP tool (Task 8).

Each test creates cards through apply_operation (the same mutation pipeline the
REST/MCP layer uses), then exercises the add_plan_attachment tool directly. The
fixture auto-resets the kanban test DB so tests stay isolated.
"""
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.kanban import mcp_server
from app.kanban.models import KanbanCard
from app.kanban.operations import apply_operation
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _load_card(s, card_id: str) -> KanbanCard:
    """Load a card with deliverables eagerly fetched.

    Lazy-loading a relationship on an async session can't be triggered implicitly
    (no greenlet), so we always selectinload deliverables up front.
    """
    return (await s.execute(
        select(KanbanCard)
        .where(KanbanCard.id == card_id)
        .options(selectinload(KanbanCard.deliverables))
    )).scalars().first()


@pytest.mark.asyncio
async def test_add_plan_happy_path_attaches_plan_and_refs():
    async with KanbanSessionLocal() as s:
        parent_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "parent", "column": "Backlog"},
        )
        c1 = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "c1", "column": "Backlog",
                     "parent_card_id": parent_id},
        )
        c2 = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "c2", "column": "Backlog",
                     "parent_card_id": parent_id},
        )
        await s.commit()

    result = await mcp_server.add_plan_attachment(
        card_id=parent_id,
        plan_markdown="# Plan\n\nc1 then c2",
        child_card_ids=[c1, c2],
        depends_on_graph={c2: [c1]},
    )
    assert "error" not in result, result
    assert result["parent_card_id"] == parent_id
    assert set(result["child_card_ids"]) == {c1, c2}

    async with KanbanSessionLocal() as s:
        parent = await _load_card(s, parent_id)
        plan_deliverables = [d for d in parent.deliverables if d.kind == "plan"]
        assert len(plan_deliverables) == 1
        assert plan_deliverables[0].ref.startswith("# Plan")

        c1_card = await _load_card(s, c1)
        c2_card = await _load_card(s, c2)
        c1_refs = [d for d in c1_card.deliverables if d.kind == "plan_ref"]
        c2_refs = [d for d in c2_card.deliverables if d.kind == "plan_ref"]
        assert len(c1_refs) == 1
        assert len(c2_refs) == 1

        # The c2 child's depends_on column should reflect the graph.
        assert list(c2_card.depends_on or []) == [c1]
        # c1 had no deps in the graph.
        assert list(c1_card.depends_on or []) == []


@pytest.mark.asyncio
async def test_add_plan_rejects_parent_mismatch():
    async with KanbanSessionLocal() as s:
        parent_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "p", "column": "Backlog"},
        )
        other_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "o", "column": "Backlog"},
        )
        child_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "c", "column": "Backlog",
                     "parent_card_id": other_id},
        )
        await s.commit()

    result = await mcp_server.add_plan_attachment(
        card_id=parent_id,
        plan_markdown="# x",
        child_card_ids=[child_id],
        depends_on_graph={},
    )
    assert result["error"] == "parent_mismatch"


@pytest.mark.asyncio
async def test_add_plan_rejects_missing_child():
    async with KanbanSessionLocal() as s:
        parent_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "p", "column": "Backlog"},
        )
        await s.commit()

    result = await mcp_server.add_plan_attachment(
        card_id=parent_id,
        plan_markdown="# x",
        child_card_ids=["does-not-exist"],
        depends_on_graph={},
    )
    assert result["error"] == "child_not_found"


@pytest.mark.asyncio
async def test_add_plan_rejects_cycle():
    async with KanbanSessionLocal() as s:
        parent_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "p", "column": "Backlog"},
        )
        c1 = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "c1", "column": "Backlog",
                     "parent_card_id": parent_id},
        )
        await s.commit()

    result = await mcp_server.add_plan_attachment(
        card_id=parent_id,
        plan_markdown="# x",
        child_card_ids=[c1],
        depends_on_graph={c1: [c1]},
    )
    assert result["error"] == "cycle_detected"
    assert c1 in result["cycle"]


@pytest.mark.asyncio
async def test_add_plan_attachment_rejects_empty_children():
    async with KanbanSessionLocal() as s:
        parent_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "p", "column": "Backlog"},
        )
        await s.commit()

    result = await mcp_server.add_plan_attachment(
        card_id=parent_id, plan_markdown="# x",
        child_card_ids=[], depends_on_graph={},
    )
    assert result["error"] == "no_children"
    assert "attach_deliverable(kind='plan')" in result["message"]

    async with KanbanSessionLocal() as s:
        parent = await _load_card(s, parent_id)
        assert [d for d in parent.deliverables if d.kind == "plan"] == []


@pytest.mark.asyncio
async def test_add_plan_rejects_too_many_children():
    async with KanbanSessionLocal() as s:
        parent_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "p", "column": "Backlog"},
        )
        children = []
        for i in range(51):
            cid = await apply_operation(
                s, op_type="create", entity_type="card",
                project_key="git:example", entity_id=None,
                payload={"title": f"c{i}", "column": "Backlog",
                         "parent_card_id": parent_id},
            )
            children.append(cid)
        await s.commit()

    result = await mcp_server.add_plan_attachment(
        card_id=parent_id, plan_markdown="# x",
        child_card_ids=children, depends_on_graph={},
    )
    assert result["error"] == "too_many_children"
    assert result["max"] == 50