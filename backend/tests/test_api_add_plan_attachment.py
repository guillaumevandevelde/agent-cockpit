"""Tests for the REST `POST /api/v1/kanban/cards/{cid}/plan-attachment` route.

REST mirror of the MCP `add_plan_attachment` tool — the fallback path for
analyst tooling when the kanban MCP server is unreachable (see the
"[problem] worktree-gc verwijdert branch/worktree van actieve analyst-sessie"
postmortem: MCP went silent after the MCP server's cwd was gc'd, and the only
way to land the plan-attachment on the parent card was via the REST endpoint
once it existed).

Each test creates cards through the public POST /cards endpoint (the same
mutation pipeline the MCP layer uses under the hood), then exercises the new
route directly. The fixture auto-resets the kanban test DB so tests stay
isolated.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.kanban.models import KanbanCard
from app.main import app
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _create_card(ac: AsyncClient, **payload) -> str:
    """POST /cards with sensible defaults; return the new card id."""
    body = {"project_key": "git:example", "title": "card"}
    body.update(payload)
    r = await ac.post("/api/v1/kanban/cards", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _load_card_deliverables(card_id: str) -> list:
    """Load a card with deliverables eagerly fetched.

    Lazy-loading a relationship on an async session can't be triggered
    implicitly (no greenlet), so we always selectinload deliverables up front.
    """
    from tests.kanban_test_db import TestSessionLocal
    async with TestSessionLocal() as s:
        return (await s.execute(
            select(KanbanCard)
            .where(KanbanCard.id == card_id)
            .options(selectinload(KanbanCard.deliverables))
        )).scalars().first()


@pytest.mark.asyncio
async def test_rest_add_plan_happy_path_attaches_plan_and_refs():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create_card(ac, title="parent")
        c1 = await _create_card(ac, title="c1", parent_card_id=parent)
        c2 = await _create_card(ac, title="c2", parent_card_id=parent)

        r = await ac.post(
            f"/api/v1/kanban/cards/{parent}/plan-attachment",
            json={"plan_markdown": "# Plan\n\nc1 then c2",
                  "child_card_ids": [c1, c2],
                  "depends_on_graph": {c2: [c1]}},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["parent_card_id"] == parent
        assert set(body["child_card_ids"]) == {c1, c2}
        assert body["plan_deliverable_id"]

        parent_card = await _load_card_deliverables(parent)
        plans = [d for d in parent_card.deliverables if d.kind == "plan"]
        assert len(plans) == 1
        assert plans[0].ref.startswith("# Plan")

        c1_card = await _load_card_deliverables(c1)
        c2_card = await _load_card_deliverables(c2)
        c1_refs = [d for d in c1_card.deliverables if d.kind == "plan_ref"]
        c2_refs = [d for d in c2_card.deliverables if d.kind == "plan_ref"]
        assert len(c1_refs) == 1
        assert len(c2_refs) == 1
        assert list(c2_card.depends_on or []) == [c1]
        assert list(c1_card.depends_on or []) == []


@pytest.mark.asyncio
async def test_rest_add_plan_returns_404_for_missing_parent():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post(
            "/api/v1/kanban/cards/does-not-exist/plan-attachment",
            json={"plan_markdown": "# x",
                  "child_card_ids": ["any"],
                  "depends_on_graph": {}},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_rest_add_plan_rejects_parent_mismatch():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create_card(ac, title="parent")
        other = await _create_card(ac, title="other")
        child = await _create_card(ac, title="child", parent_card_id=other)

        r = await ac.post(
            f"/api/v1/kanban/cards/{parent}/plan-attachment",
            json={"plan_markdown": "# x",
                  "child_card_ids": [child],
                  "depends_on_graph": {}},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["error"] == "parent_mismatch"
        assert r.json()["detail"]["card_id"] == child


@pytest.mark.asyncio
async def test_rest_add_plan_rejects_missing_child():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create_card(ac, title="parent")

        r = await ac.post(
            f"/api/v1/kanban/cards/{parent}/plan-attachment",
            json={"plan_markdown": "# x",
                  "child_card_ids": ["does-not-exist"],
                  "depends_on_graph": {}},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["error"] == "child_not_found"


@pytest.mark.asyncio
async def test_rest_add_plan_rejects_cycle():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create_card(ac, title="parent")
        c1 = await _create_card(ac, title="c1", parent_card_id=parent)

        r = await ac.post(
            f"/api/v1/kanban/cards/{parent}/plan-attachment",
            json={"plan_markdown": "# x",
                  "child_card_ids": [c1],
                  "depends_on_graph": {c1: [c1]}},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["error"] == "cycle_detected"
        assert c1 in r.json()["detail"]["cycle"]


@pytest.mark.asyncio
async def test_rest_add_plan_rejects_too_many_children():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create_card(ac, title="parent")
        # 51 children — over the cap of 50.
        child_ids = [
            await _create_card(ac, title=f"c{i}", parent_card_id=parent)
            for i in range(51)
        ]

        r = await ac.post(
            f"/api/v1/kanban/cards/{parent}/plan-attachment",
            json={"plan_markdown": "# x",
                  "child_card_ids": child_ids,
                  "depends_on_graph": {}},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["error"] == "too_many_children"
        assert r.json()["detail"]["max"] == 50


@pytest.mark.asyncio
async def test_rest_add_plan_omitting_depends_on_graph_is_allowed():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create_card(ac, title="parent")
        c1 = await _create_card(ac, title="c1", parent_card_id=parent)

        # depends_on_graph is optional; omitting it must work.
        r = await ac.post(
            f"/api/v1/kanban/cards/{parent}/plan-attachment",
            json={"plan_markdown": "# x",
                  "child_card_ids": [c1]},
        )
        assert r.status_code == 200, r.text
        c1_card = await _load_card_deliverables(c1)
        assert list(c1_card.depends_on or []) == []