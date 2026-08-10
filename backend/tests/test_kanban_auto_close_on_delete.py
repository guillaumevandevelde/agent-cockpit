# backend/tests/test_kanban_auto_close_on_delete.py
"""Auto-close parent after a child is removed via delete or Clear Done.

Kaart `400d6a77…` — a parent parked in `Awaiting Subtasks` whose last child
gets removed never re-evaluates. ``close_parent_if_all_children_done`` short-
circuited on `not children or any(...): return False`, so the parent sat
there forever even though there was nothing left to wait for. Five parents
on the live board accumulated 6 weeks of stranded parked time before being
manually closed.

This test pins the fix from the *REST* path's perspective:

1. ``DELETE /api/v1/kanban/cards/{cid}`` — single card delete
2. ``POST /api/v1/kanban/clear-column`` — Clear Done sweep over a whole
   column (the routine end-of-life path that produced the historical rows)
3. Nested-decomposition chain walk — closing a parent must walk up to its
   own parent, the same way ``mcp_server.move_card`` already does

The roll-up posted on the parent must explicitly say the children are gone,
otherwise an operator landing on the Done-banner would assume there was
never any work.
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


async def _summary_texts(ac, card_id):
    r = await ac.get(f"/api/v1/kanban/cards/{card_id}/activity")
    return [
        e["payload"].get("text", "")
        for e in r.json()
        if e["op_type"] == "comment"
        and (e["payload"].get("text") or "").startswith("**Summary:**")
    ]


@pytest.mark.asyncio
async def test_delete_child_auto_closes_parked_parent_with_zero_children():
    """Acceptance #1+#4 (single delete) — parent parked in `Awaiting
    Subtasks`, one child in `Done`. Deleting the child leaves the parent
    with zero children, so the auto-close must fire and move the parent
    to `Done` with a `**Summary:**` roll-up that mentions the children
    are gone."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create(ac, "P", "parked-parent",
                               column="Awaiting Subtasks", confirm=True)
        child = await _create(ac, "P", "child",
                              column="Done", parent_card_id=parent)

        # Delete the child — this is the regression trigger.
        r = await ac.delete(f"/api/v1/kanban/cards/{child}")
        assert r.status_code == 204

        # Parent auto-closed to Done.
        r = await ac.get(f"/api/v1/kanban/cards/{parent}")
        assert r.status_code == 200
        assert r.json()["column"] == "Done", (
            f"Parked parent with zero children must auto-close; "
            f"parent column was {r.json()['column']!r}"
        )

        # Roll-up mentions children are gone (acceptance #3).
        rollups = await _summary_texts(ac, parent)
        assert len(rollups) == 1, rollups
        body = rollups[0][len("**Summary:** "):]
        assert any(p in body for p in (
            "kinderen zijn al verwijderd",
            "kinderen al van het bord",
            "kind-kaarten waren al",
            "kinderen waren al",
            "geen kinderen meer",
        )), (
            f"Roll-up must explain children are gone; body was:\n{body}"
        )


@pytest.mark.asyncio
async def test_clear_done_column_auto_closes_parked_parent_when_child_was_done():
    """Acceptance #1+#4 (clear-column) — the routine end-of-life sweep
    path. Parent parked in `Awaiting Subtasks`, child in `Done`. Clearing
    the `Done` column removes the child; the auto-close must then fire
    on the now-childless parent. This is the exact path that produced
    the historical stuck-parents on the live board (kaart `400d6a77…`):
    analysts used to clear their Done column and the parent was stranded.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create(ac, "P", "parked-parent",
                               column="Awaiting Subtasks", confirm=True)
        await _create(ac, "P", "done-child",
                      column="Done", parent_card_id=parent)

        r = await ac.post("/api/v1/kanban/clear-column",
                          json={"project_key": "P", "column": "Done"})
        assert r.status_code == 200
        assert r.json()["cleared"] == 1

        # Parent auto-closed to Done.
        r = await ac.get(f"/api/v1/kanban/cards/{parent}")
        assert r.status_code == 200
        assert r.json()["column"] == "Done", (
            f"Parent parked in Awaiting Subtasks must auto-close when "
            f"its only Done child is cleared; parent column was "
            f"{r.json()['column']!r}"
        )

        # Roll-up mentions children are gone.
        rollups = await _summary_texts(ac, parent)
        assert len(rollups) == 1, rollups
        body = rollups[0][len("**Summary:** "):]
        assert any(p in body for p in (
            "kinderen zijn al verwijderd",
            "kinderen al van het bord",
            "kind-kaarten waren al",
            "kinderen waren al",
            "geen kinderen meer",
        )), body


@pytest.mark.asyncio
async def test_delete_child_walks_up_chain_closing_grandparent_too():
    """Acceptance #1 (chain walk) — three-level decomposition: grandparent
    (Awaiting Subtasks) → parent (Awaiting Subtasks) → child (Done). Deleting
    the child must close the parent, and closing the parent must then
    close the grandparent. This mirrors the walk ``mcp_server.move_card``
    already does for genuine Done moves — the delete path must walk the
    same way so nested decomposition never strands a mid-level parent.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        grandparent = await _create(ac, "P", "grandparent",
                                    column="Awaiting Subtasks", confirm=True)
        parent = await _create(ac, "P", "parent",
                               column="Awaiting Subtasks",
                               parent_card_id=grandparent)
        child = await _create(ac, "P", "child",
                              column="Done", parent_card_id=parent)

        r = await ac.delete(f"/api/v1/kanban/cards/{child}")
        assert r.status_code == 204

        r = await ac.get(f"/api/v1/kanban/cards/{parent}")
        assert r.json()["column"] == "Done", (
            "Parent must auto-close after its only Done child is deleted."
        )
        r = await ac.get(f"/api/v1/kanban/cards/{grandparent}")
        assert r.json()["column"] == "Done", (
            "Grandparent must auto-close after its child (the now-closed "
            "parent) cascades up the chain — same walk mcp_server.move_card "
            "does for genuine Done moves."
        )


@pytest.mark.asyncio
async def test_delete_non_parked_parent_does_not_close_its_own_parent():
    """Guard rail: deleting a child whose parent is in an agent column
    (not parked) must not silently move that parent to Done. The parked-
    only invariant from `close_parent_if_all_children_done` is the
    canoniek check; this REST test re-pins it for the delete path so a
    future refactor that forgets the parked gate breaks here first.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        # Grandparent is parked — could in principle be closed.
        grandparent = await _create(ac, "P", "grandparent-parked",
                                    column="Awaiting Subtasks", confirm=True)
        # Mid-level parent is in an agent column, NOT parked.
        parent = await _create(ac, "P", "parent-in-flight",
                               column="analyst", parent_card_id=grandparent)
        # Done child of the in-flight parent.
        child = await _create(ac, "P", "child",
                              column="Done", parent_card_id=parent)

        r = await ac.delete(f"/api/v1/kanban/cards/{child}")
        assert r.status_code == 204

        # Mid-level parent: still in agent column, NOT closed.
        r = await ac.get(f"/api/v1/kanban/cards/{parent}")
        assert r.json()["column"] == "analyst", (
            "Non-parked parent must NOT be auto-closed by child delete."
        )
        # Grandparent: also untouched (its child is still in flight).
        r = await ac.get(f"/api/v1/kanban/cards/{grandparent}")
        assert r.json()["column"] == "Awaiting Subtasks", (
            "Grandparent must not close while its child is still in flight."
        )
