"""Tests for the ``spec`` deliverable-kind.

Companion of ``plan``: brainstorming output (design docs from the
``brainstorming`` skill, inception specs, …) is a first-class artefact on a
card, distinct from the analyst plan-attachment. Stored via the existing
``attach_deliverable`` MCP tool + REST endpoint with ``kind="spec"`` and a
markdown body in ``ref``.

Two routes:
- MCP ``mcp_server.attach_deliverable(card_id, "spec", markdown)``
- REST ``POST /api/v1/kanban/cards/{cid}/deliverables`` body
  ``{"kind": "spec", "ref": markdown}``

Both share the same op-log + materialized table; the MCP path's test is the
authoritative contract, the REST path's test catches the AttachRequest
schema going out of sync.

Backwards compatibility: ``pr|branch|commit|link|note|plan|plan_ref`` already
round-trip through ``attach_deliverable`` — the empty-ref guard we add is a
*new* validation, not a tightening of an existing one.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.kanban import mcp_server
from app.kanban.models import KanbanCard
from app.kanban.operations import apply_operation
from app.main import app
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _load_card(s, card_id: str) -> KanbanCard:
    """Load a card with deliverables eagerly fetched (async session can't lazy-load)."""
    return (await s.execute(
        select(KanbanCard)
        .where(KanbanCard.id == card_id)
        .options(selectinload(KanbanCard.deliverables))
    )).scalars().first()


@pytest.mark.asyncio
async def test_attach_spec_via_mcp_persists_markdown_body():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "design brainstorming", "column": "Backlog"},
        )
        await s.commit()

    spec_body = (
        "# Spec\n\n"
        "## Problem\nBrainstorming output is invisible on the board.\n"
        "## Approach\nFirst-class deliverable, distinct from plan.\n"
    )
    result = await mcp_server.attach_deliverable(cid, "spec", spec_body)
    assert "error" not in result, result
    assert result["id"] == cid

    async with KanbanSessionLocal() as s:
        card = await _load_card(s, cid)
        specs = [d for d in card.deliverables if d.kind == "spec"]
        assert len(specs) == 1
        assert specs[0].ref == spec_body


@pytest.mark.asyncio
async def test_attach_spec_via_rest_endpoint_persists_markdown_body():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "design brainstorming", "column": "Backlog"},
        )
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        resp = await c.post(
            f"/api/v1/kanban/cards/{cid}/deliverables",
            json={"kind": "spec", "ref": "# Spec\n\nbody"},
        )
    assert resp.status_code == 200, resp.text

    async with KanbanSessionLocal() as s:
        card = await _load_card(s, cid)
        specs = [d for d in card.deliverables if d.kind == "spec"]
        assert len(specs) == 1
        assert specs[0].ref == "# Spec\n\nbody"


@pytest.mark.asyncio
async def test_attach_spec_with_empty_ref_is_rejected():
    """``ref`` must be a non-empty markdown body — the canonical 'non-allowed
    reference' case. ``branch`` and the other URL-style kinds already pass
    non-empty refs; ``spec`` is the markdown-body kind, so an empty body is
    unambiguously wrong (it'd render as a blank spec card with no design)."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "t", "column": "Backlog"},
        )
        await s.commit()

    result = await mcp_server.attach_deliverable(cid, "spec", "")
    # Mirror the MCP tool's error-dict contract — explicit error key, no
    # row written, no commit.
    assert result.get("error") == "invalid_ref"

    async with KanbanSessionLocal() as s:
        card = await _load_card(s, cid)
        assert not [d for d in card.deliverables if d.kind == "spec"]


@pytest.mark.asyncio
async def test_attach_spec_via_rest_with_empty_ref_is_rejected():
    transport = ASGITransport(app=app)
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "t", "column": "Backlog"},
        )
        await s.commit()
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        resp = await c.post(
            f"/api/v1/kanban/cards/{cid}/deliverables",
            json={"kind": "spec", "ref": ""},
        )
    # Pydantic validation rejects at the schema layer; FastAPI returns 422.
    assert resp.status_code == 422

    async with KanbanSessionLocal() as s:
        card = await _load_card(s, cid)
        assert not [d for d in card.deliverables if d.kind == "spec"]


@pytest.mark.asyncio
async def test_attach_plan_via_mcp_persists_on_childless_card():
    """``kind="plan"`` on a childless (intake) card is the intake-correct
    route — ``add_plan_attachment`` requires ``child_card_ids`` and rejects
    a parent with no children, so a plan deliverable on an intake card must
    come through ``attach_deliverable``. Locks that contract so a future
    ``_materialize`` change can't silently break it.
    """
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "intake plan", "column": "intake"},
        )
        await s.commit()

    plan_body = (
        "# Plan\n\n"
        "## Goal\nLand a plan deliverable on a childless intake card.\n"
        "## Approach\nUse attach_deliverable — add_plan_attachment needs kids.\n"
    )
    result = await mcp_server.attach_deliverable(cid, "plan", plan_body)
    assert "error" not in result, result
    assert result["id"] == cid

    async with KanbanSessionLocal() as s:
        card = await _load_card(s, cid)
        plans = [d for d in card.deliverables if d.kind == "plan"]
        assert len(plans) == 1
        assert plans[0].ref == plan_body


@pytest.mark.asyncio
async def test_attach_plan_via_rest_endpoint_persists_on_childless_card():
    """REST mirror of ``test_attach_plan_via_mcp_persists_on_childless_card``
    — catches the ``AttachRequest`` schema drifting out of sync with the MCP
    tool's accepted kinds."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "intake plan", "column": "intake"},
        )
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        resp = await c.post(
            f"/api/v1/kanban/cards/{cid}/deliverables",
            json={"kind": "plan", "ref": "# Plan\n\nbody"},
        )
    assert resp.status_code == 200, resp.text

    async with KanbanSessionLocal() as s:
        card = await _load_card(s, cid)
        plans = [d for d in card.deliverables if d.kind == "plan"]
        assert len(plans) == 1
        assert plans[0].ref == "# Plan\n\nbody"
