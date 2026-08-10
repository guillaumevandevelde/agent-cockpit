# backend/tests/test_kanban_auto_close_on_rest_move.py
"""Auto-close parent after a child reaches Done via the REST move endpoint.

Kaart `85a06bc7…` — the MCP `move_card` walks the `parent_card_id` chain via
`service.try_close_ancestors` after a genuine Done move. The REST mirror at
`POST /api/v1/kanban/cards/{cid}/move` skipped that walk: a child dragged to
Done in the UI stranded its parked parent in `Awaiting Subtasks` forever.
Two parents on the live board (`4e69915f`, `75c0952f`) accumulated stranded
parked time because of exactly this gap before being manually closed.

This file pins the fix from the *REST* path's perspective:

1. Single-level: parent parked, child → Done via REST → parent auto-closes
2. Nested: grandparent → parent → child, all parked/mid → child → Done via
   REST → parent auto-closes → grandparent auto-closes too (chain walk)
3. Non-Done moves don't fire the walk (regression guard: only genuine Done
   triggers auto-close, same as MCP)

The walk lives in `service.try_close_ancestors` so the MCP and REST paths
share one place — a third caller (Clear Done / single-card delete) already
uses it from `router.py`. The test below is the canary: a regression that
removes the REST call re-fails these.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _create(ac, project_key, title, *, column=None, parent_card_id=None,
                  confirm=False):
    body = {"project_key": project_key, "title": title}
    if confirm:
        body["confirm_new_project"] = True
    if column is not None:
        body["column"] = column
    if parent_card_id is not None:
        body["parent_card_id"] = parent_card_id
    r = await ac.post("/api/v1/kanban/cards", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _column(ac, card_id):
    r = await ac.get(f"/api/v1/kanban/cards/{card_id}")
    assert r.status_code == 200, r.text
    return r.json()["column"]


@pytest.mark.asyncio
async def test_rest_move_child_to_done_auto_closes_parked_parent():
    """Acceptance #1 — parent parked in `Awaiting Subtasks`, child in an
    agent column. The UI drag-to-Done is the REST `POST /cards/{cid}/move`
    with `column="Done"` and a non-empty summary (gate enforced on both
    paths). After the move the parent must auto-close to Done with the
    same per-child roll-up the MCP path posts. Without the fix the parent
    stays parked and the test fails at the column assertion.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create(ac, "P", "parked-parent",
                               column="Awaiting Subtasks", confirm=True)
        child = await _create(ac, "P", "child",
                              column="Doing", parent_card_id=parent)

        # UI drag: child → Done via REST. summary is required by the shared
        # gate (kaart efbb82e6…), same wire shape as MCP.
        r = await ac.post(f"/api/v1/kanban/cards/{child}/move",
                          json={"column": "Done",
                                "summary": "child shipped"})
        assert r.status_code == 200, r.text
        assert (await _column(ac, child)) == "Done"

        # The bug: parent stayed parked. The fix: parent auto-closed.
        assert (await _column(ac, parent)) == "Done", (
            "REST move-to-Done must fire the parent auto-close walk the "
            "MCP path already runs (service.try_close_ancestors). "
            f"Parent column was {(await _column(ac, parent))!r}"
        )


@pytest.mark.asyncio
async def test_rest_move_child_to_done_walks_chain_closing_grandparent():
    """Acceptance #3 — three-level nested decomposition: grandparent →
    parent → child. Child reaches Done via REST. Parent auto-closes (its
    own parked check passes), then the grandparent's parked check fires
    too. Same chain walk the MCP path does for genuine Done moves
    (`mcp_server.move_card:599-600` → `service.try_close_ancestors`).
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        grandparent = await _create(ac, "P", "grandparent",
                                    column="Awaiting Subtasks", confirm=True)
        parent = await _create(ac, "P", "parent",
                               column="Awaiting Subtasks",
                               parent_card_id=grandparent)
        child = await _create(ac, "P", "child",
                              column="Doing", parent_card_id=parent)

        r = await ac.post(f"/api/v1/kanban/cards/{child}/move",
                          json={"column": "Done",
                                "summary": "leaf shipped"})
        assert r.status_code == 200, r.text

        # Parent auto-closes first.
        assert (await _column(ac, parent)) == "Done", (
            "Mid-level parked parent must auto-close after its only child "
            "reaches Done via REST."
        )
        # Then the grandparent — the chain walk must reach it.
        assert (await _column(ac, grandparent)) == "Done", (
            "Grandparent must auto-close after the mid-level parent "
            "cascades up — REST path must walk the same chain the MCP "
            "path walks via service.try_close_ancestors."
        )


@pytest.mark.asyncio
async def test_rest_move_child_to_done_leaves_non_parked_parent_alone():
    """Regression guard — acceptance criterion #2 / divergence rule. When
    the parent is in an agent column (NOT parked in `Awaiting Subtasks`),
    a child reaching Done via REST must NOT silently close that parent.
    `close_parent_if_all_children_done` is parked-only, so the walk
    stops at the first non-parked parent. This pins the same invariant
    the delete path already tests (test_kanban_auto_close_on_delete.py)
    but from the REST move side.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        # Grandparent parked — could in principle close later.
        grandparent = await _create(ac, "P", "parked-grandparent",
                                    column="Awaiting Subtasks", confirm=True)
        # Mid-level parent in an agent column — not parked, work in flight.
        parent = await _create(ac, "P", "in-flight-parent",
                               column="engineer",
                               parent_card_id=grandparent)
        child = await _create(ac, "P", "child",
                              column="Doing", parent_card_id=parent)

        r = await ac.post(f"/api/v1/kanban/cards/{child}/move",
                          json={"column": "Done",
                                "summary": "child shipped"})
        assert r.status_code == 200, r.text

        # Mid-level parent: still in agent column, not silently closed.
        assert (await _column(ac, parent)) == "engineer", (
            "Non-parked parent must NOT be auto-closed by REST move-to-Done."
        )
        # Grandparent: also untouched — its only child is still in flight.
        assert (await _column(ac, grandparent)) == "Awaiting Subtasks", (
            "Grandparent must not close while its child is still in flight."
        )


@pytest.mark.asyncio
async def test_rest_move_child_to_backlog_does_not_fire_auto_close():
    """Regression guard — non-Done moves must NOT fire the auto-close
    walk. The MCP path only walks after `final_column == "Done"`. The
    REST path mirrors that intent: parking in `Awaiting Subtasks`,
    moving to `Doing`, or any other column must leave the parked parent
    exactly where it was. Pins that the walk is gated on `column == "Done"`,
    not on every move.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create(ac, "P", "parked-parent",
                               column="Awaiting Subtasks", confirm=True)
        child = await _create(ac, "P", "child",
                              column="Doing", parent_card_id=parent)

        # Move to a non-Done column. The walk must NOT fire.
        r = await ac.post(f"/api/v1/kanban/cards/{child}/move",
                          json={"column": "Backlog"})
        assert r.status_code == 200, r.text
        assert (await _column(ac, child)) == "Backlog"

        # Parent still parked.
        assert (await _column(ac, parent)) == "Awaiting Subtasks", (
            "Non-Done moves must not fire the auto-close walk."
        )