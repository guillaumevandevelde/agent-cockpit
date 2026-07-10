"""Tests for the PATCH /cards/{cid}/plan-attachment REST endpoint.

Each test creates a card through apply_operation (the same mutation pipeline the
REST/MCP layer uses), seeds a `kind=plan` deliverable via add_plan_attachment,
then drives the new PATCH endpoint over httpx. The fixture auto-resets the
kanban test DB so tests stay isolated.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.kanban import mcp_server
from app.kanban.models import KanbanCard
from app.kanban.operations import apply_operation
from tests.kanban_test_db import TestSessionLocal

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    from tests.kanban_test_db import reset_test_tables
    await reset_test_tables()
    yield


async def _seed_parent_with_plan(
    *, project_key: str = "git:example", children: int = 0,
) -> tuple[str, list[str]]:
    """Create a parent card, attach a plan via the MCP tool, return (parent_id, child_ids)."""
    async with KanbanSessionLocal() as s:
        parent_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=project_key, entity_id=None,
            payload={"title": "parent", "column": "Backlog"},
        )
        child_ids: list[str] = []
        for i in range(children):
            cid = await apply_operation(
                s, op_type="create", entity_type="card",
                project_key=project_key, entity_id=None,
                payload={"title": f"c{i}", "column": "Backlog",
                         "parent_card_id": parent_id},
            )
            child_ids.append(cid)
        await s.commit()

    if children:
        await mcp_server.add_plan_attachment(
            card_id=parent_id,
            plan_markdown="# Plan v1\n\nOriginal plan body.",
            child_card_ids=child_ids,
            depends_on_graph={},
        )
    else:
        # A plan on a parent with no children — still allowed by the MCP tool
        # because the child_card_ids list is empty and the graph is empty.
        await mcp_server.add_plan_attachment(
            card_id=parent_id,
            plan_markdown="# Plan v1\n\nOriginal plan body.",
            child_card_ids=[],
            depends_on_graph={},
        )

    return parent_id, child_ids


async def _load_card(s, card_id: str) -> KanbanCard:
    return (await s.execute(
        select(KanbanCard)
        .where(KanbanCard.id == card_id)
        .options(selectinload(KanbanCard.deliverables))
    )).scalars().first()


@pytest.mark.asyncio
async def test_update_plan_attachment_succeeds():
    """Happy path: PATCH returns 200 with the updated card, and the deliverable
    row holds the new markdown."""
    parent_id, _ = await _seed_parent_with_plan(children=2)

    transport = ASGITransport(app=__import__("app.main", fromlist=["app"]).app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.patch(
            f"/api/v1/kanban/cards/{parent_id}/plan-attachment",
            json={"plan_markdown": "# Plan v2\n\nUpdated plan body."},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == parent_id
        plan_deliverables = [d for d in body["deliverables"] if d["kind"] == "plan"]
        assert len(plan_deliverables) == 1
        assert plan_deliverables[0]["ref"] == "# Plan v2\n\nUpdated plan body."


@pytest.mark.asyncio
async def test_update_plan_attachment_returns_404_when_no_plan():
    """If the card has no `kind=plan` deliverable yet, the endpoint must 404
    with a clear message — not silently create one."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "no-plan", "column": "Backlog"},
        )
        await s.commit()

    transport = ASGITransport(app=__import__("app.main", fromlist=["app"]).app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.patch(
            f"/api/v1/kanban/cards/{cid}/plan-attachment",
            json={"plan_markdown": "# x"},
        )
        assert r.status_code == 404, r.text
        assert "plan" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_plan_attachment_persists_content():
    """After PATCH, a fresh read of the card sees the new markdown — the
    previous plan is fully overwritten in place."""
    parent_id, _ = await _seed_parent_with_plan(children=1)

    transport = ASGITransport(app=__import__("app.main", fromlist=["app"]).app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.patch(
            f"/api/v1/kanban/cards/{parent_id}/plan-attachment",
            json={"plan_markdown": "# Plan v2\n\nUpdated."},
        )
        assert r.status_code == 200, r.text

        # GET round-trip — verify the new content survives a reload.
        r = await ac.get(f"/api/v1/kanban/cards/{parent_id}")
        assert r.status_code == 200
        plan_deliverables = [d for d in r.json()["deliverables"] if d["kind"] == "plan"]
        assert len(plan_deliverables) == 1
        assert plan_deliverables[0]["ref"] == "# Plan v2\n\nUpdated."

    # And on the DB row itself, not just via the API.
    async with KanbanSessionLocal() as s:
        parent = await _load_card(s, parent_id)
        plan_rows = [d for d in parent.deliverables if d.kind == "plan"]
        assert len(plan_rows) == 1
        assert plan_rows[0].ref == "# Plan v2\n\nUpdated."
