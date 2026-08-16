# backend/tests/test_kanban_router_move_parent_parking.py
"""REST parent-parking on `POST /cards/{cid}/move`.

Kaart `eb75b599…` — the MCP `move_card` redirects a Done move to
`Awaiting Subtasks` when the card has ≥1 child (decision doc
`analyse-levenscyclus-decision.md` §3, §3.1: parent-generic, not gated on
`work_type`). The REST mirror at `POST /api/v1/kanban/cards/{cid}/move`
was divergent: a parent dragged to Done in the UI landed in Done directly
and its parked-children auto-close walk never fired. A human operator
got a board state where a parent sat green in Done while every child
still waited in `Backlog` or an agent column — incoherent.

This file pins the fix from the *pre-Done* side of REST, the symmetrical
counterpart of `test_kanban_auto_close_on_rest_move.py` which covers the
*post-Done-child* auto-close walk. Both files now read as one regression
suite for the REST Done → Awaiting Subtasks → child Done → parent Done
round-trip.

Acceptance criteria pinned here (kaart eb75b599… AC #1, #3, #4):

1. REST Done for a parent with ≥1 child lands in `Awaiting Subtasks`.
2. REST Done for a leaf (no children) still lands in Done (regression
   guard — parking must not fire when there is nothing to wait for).
3. After parking, a child → Done via REST triggers the existing
   `service.try_close_ancestors` walk and the parent auto-closes. The
   redirect is idempotent against the walk (AC #3).
4. Nested chain (grandparent → parent → child): each level that has
   pending descendants parks; the chain walk closes them in order.
5. Non-Done moves do not redirect (regression guard).
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
async def test_rest_done_with_children_redirects_to_awaiting_subtasks():
    """Acceptance #1 — parent in `Backlog` with one child. A REST
    `POST /cards/{cid}/move` with `column="Done"` and a non-empty summary
    must land the parent in `Awaiting Subtasks`, not `Done`. Mirrors the
    MCP redirect at `mcp_server.move_card:518-522`. Without the fix the
    parent sits in Done and the children never auto-close it; with the
    fix this assertion passes."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create(ac, "P", "parked-parent",
                               column="Backlog", confirm=True)
        await _create(ac, "P", "child",
                      column="Backlog", parent_card_id=parent)

        r = await ac.post(f"/api/v1/kanban/cards/{parent}/move",
                          json={"column": "Done",
                                "summary": "parent parked (REST)"})
        assert r.status_code == 200, r.text

        assert (await _column(ac, parent)) == "Awaiting Subtasks", (
            "REST Done for a parent with ≥1 child must redirect to "
            "`Awaiting Subtasks` (mirror MCP move_card redirect, "
            "decision doc §3)."
        )


@pytest.mark.asyncio
async def test_rest_done_without_children_still_lands_in_done():
    """Regression guard — a leaf card (zero children) must NOT redirect:
    there is nothing to wait for, so parking would be wrong. Same leaf
    invariant the MCP path already upholds (`mcp_server.move_card:519`
    gates on `card_has_children`)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        leaf = await _create(ac, "P", "leaf", column="Backlog", confirm=True)

        r = await ac.post(f"/api/v1/kanban/cards/{leaf}/move",
                          json={"column": "Done",
                                "summary": "leaf shipped"})
        assert r.status_code == 200, r.text
        assert (await _column(ac, leaf)) == "Done", (
            "REST Done for a card with zero children must NOT park — "
            "the redirect is parent-generic, gated on ≥1 child."
        )


@pytest.mark.asyncio
async def test_rest_parked_parent_auto_closes_when_child_dones():
    """Acceptance #4 (single-level round-trip) — REST Done parks the
    parent (AC #1), then the child reaches Done via REST, and the
    existing `service.try_close_ancestors` walk closes the parent.
    The redirect must be idempotent against the walk (AC #3): no
    double-close, no orphaned parent left parked."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create(ac, "P", "parked-parent",
                               column="Backlog", confirm=True)
        child = await _create(ac, "P", "child",
                              column="Backlog", parent_card_id=parent)

        # Step 1: park the parent via REST Done (children present).
        r = await ac.post(f"/api/v1/kanban/cards/{parent}/move",
                          json={"column": "Done",
                                "summary": "parent parked"})
        assert r.status_code == 200, r.text
        assert (await _column(ac, parent)) == "Awaiting Subtasks"

        # Step 2: child → Done via REST. The auto-close walk fires.
        r = await ac.post(f"/api/v1/kanban/cards/{child}/move",
                          json={"column": "Done",
                                "summary": "child shipped"})
        assert r.status_code == 200, r.text
        assert (await _column(ac, child)) == "Done"

        # Step 3: parent auto-closed by the walk.
        assert (await _column(ac, parent)) == "Done", (
            "After parking + child-Done, the parent must auto-close via "
            "service.try_close_ancestors (AC #3 idempotency)."
        )


@pytest.mark.asyncio
async def test_rest_parked_grandparent_walks_through_parent_to_done():
    """Acceptance #4 (nested round-trip) — three-level chain:
    grandparent → parent → child. Each parent with pending descendants
    parks on its REST Done; the chain walk closes them in order after
    the leaf reaches Done."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        grandparent = await _create(ac, "P", "grandparent",
                                    column="Backlog", confirm=True)
        parent = await _create(ac, "P", "parent",
                               column="Backlog",
                               parent_card_id=grandparent)
        child = await _create(ac, "P", "child",
                              column="Backlog", parent_card_id=parent)

        # Park grandparent first (its only direct child is parent, which
        # is still in Backlog → grandparent parks).
        r = await ac.post(f"/api/v1/kanban/cards/{grandparent}/move",
                          json={"column": "Done",
                                "summary": "grandparent parked"})
        assert r.status_code == 200, r.text
        assert (await _column(ac, grandparent)) == "Awaiting Subtasks"

        # Park parent (its child is still in Backlog → parent parks).
        r = await ac.post(f"/api/v1/kanban/cards/{parent}/move",
                          json={"column": "Done",
                                "summary": "parent parked"})
        assert r.status_code == 200, r.text
        assert (await _column(ac, parent)) == "Awaiting Subtasks"

        # Leaf → Done. Walk fires: parent closes, then grandparent.
        r = await ac.post(f"/api/v1/kanban/cards/{child}/move",
                          json={"column": "Done",
                                "summary": "leaf shipped"})
        assert r.status_code == 200, r.text
        assert (await _column(ac, parent)) == "Done", (
            "Mid-level parent must auto-close after its only child "
            "reaches Done via REST."
        )
        assert (await _column(ac, grandparent)) == "Done", (
            "Grandparent must auto-close after the mid-level parent "
            "cascades up — REST path must walk the same chain the MCP "
            "path walks via service.try_close_ancestors."
        )


@pytest.mark.asyncio
async def test_rest_done_with_already_done_children_auto_closes_in_place():
    """Edge case (kaart eb75b599… review-round 2) — a parent whose
    children are *already* Done before the parent is moved must NOT
    strand in `Awaiting Subtasks`. The auto-close walk the MCP path
    runs on a genuine Done (`mcp_server.move_card:604-605`) only fires
    on `final_column == "Done"`; the REST parking redirect makes
    `final_column` `Awaiting Subtasks` whenever ≥1 child exists, so
    `close_parent_if_all_children_done` is never called on the just-
    parked card itself. Result on master: the parent parks, the walk
    runs against `card.parent_card_id` (None for a top-level parent),
    and the parked card waits forever for a child-Done that already
    happened. Fix: after the parking redirect, call
    `service.try_close_ancestors(card_id)` — its first iteration sees
    the freshly parked card, finds every child Done, and moves the
    parent to Done in the same transaction. Mirrors the MCP-side
    auto-close invariant without re-introducing a Done round-trip
    through Awaiting Subtasks as visible activity-feed noise."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create(ac, "P", "all-done-parent",
                               column="Backlog", confirm=True)
        child = await _create(ac, "P", "child",
                              column="Backlog", parent_card_id=parent)

        # Ship the child first — it lands in Done directly (no children
        # of its own, so no parking redirect).
        r = await ac.post(f"/api/v1/kanban/cards/{child}/move",
                          json={"column": "Done",
                                "summary": "child shipped first"})
        assert r.status_code == 200, r.text
        assert (await _column(ac, child)) == "Done"

        # Now drag the parent to Done. Children are all Done already;
        # the parent must end up in Done, NOT in `Awaiting Subtasks`.
        r = await ac.post(f"/api/v1/kanban/cards/{parent}/move",
                          json={"column": "Done",
                                "summary": "parent shipped after child"})
        assert r.status_code == 200, r.text
        assert (await _column(ac, parent)) == "Done", (
            "Parent with all-Done children must NOT park in Awaiting "
            "Subtasks — `service.try_close_ancestors` must run on the "
            "just-parked card itself so the in-place close fires "
            "(AC #3 idempotency, review-round 2)."
        )


@pytest.mark.asyncio
async def test_rest_done_parking_does_not_redirect_non_done_moves():
    """Regression guard — only `column == "Done"` may trigger the
    parking redirect. A REST move to any other column must land the
    card where the client asked. Pins the same gate the MCP path
    enforces (`mcp_server.move_card:519`)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create(ac, "P", "parked-parent",
                               column="Backlog", confirm=True)
        await _create(ac, "P", "child",
                      column="Backlog", parent_card_id=parent)

        # Move to a non-Done column. Must NOT park — only Done redirects.
        r = await ac.post(f"/api/v1/kanban/cards/{parent}/move",
                          json={"column": "Doing"})
        assert r.status_code == 200, r.text
        assert (await _column(ac, parent)) == "Doing", (
            "Non-Done moves must not trigger the parent-parking redirect."
        )
