"""Parent-delete also cleans up the child's plan_ref deliverable.

Sibling test to ``test_kanban_dep_delete_guard.py``: deleting a parent used to
clear ``parent_card_id`` on each child (so the dispatch gate stops holding the
child out) but left the child's ``kind='plan_ref'`` deliverable dangling. The
ref still pointed at the now-gone parent, so the next sweep saw a permanent
``dangling_parent`` row and every subsequent run started with known noise.

This test seeds two parent→child trees with plan_refs, deletes one parent, and
asserts:

1. the deleted parent's plan_refs are gone (the child is dispatched again);
2. the *other* tree's plan_refs are untouched (control — the cleanup is
   scoped to refs pointing at the deleted parent only, not every plan_ref in
   the DB);
3. the audit comment posted on each affected child mentions *both* that the
   parent link was cleared AND that the plan_ref deliverable was removed, so
   the activity feed stays complete.

The Clear-Done path is exercised too: ``/clear-column`` is the routine
end-of-life sweep that produced the historical rows on the live board.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.kanban.models import KanbanCard, KanbanDeliverable
from app.main import app
from tests.kanban_test_db import TestSessionLocal, reset_test_tables


KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _create(ac, title, *, parent_card_id=None, column="Backlog",
                  confirm=False):
    body = {"project_key": "P", "title": title, "column": column}
    if confirm:
        body["confirm_new_project"] = True
    if parent_card_id is not None:
        body["parent_card_id"] = parent_card_id
    r = await ac.post("/api/v1/kanban/cards", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _add_plan(ac, parent_id, child_ids, *, depends_on_graph=None,
                    plan_md="# plan"):
    r = await ac.post(
        f"/api/v1/kanban/cards/{parent_id}/plan-attachment",
        json={"plan_markdown": plan_md,
              "child_card_ids": list(child_ids),
              **({"depends_on_graph": depends_on_graph} if depends_on_graph else {})},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _deliverables(card_id):
    """Return (kind, ref) tuples for deliverables on ``card_id``.

    An async session can't lazy-load relationships, so any helper that reads
    ``card.deliverables`` after the request returns must use a fresh query
    with ``selectinload`` (see ``tests/kanban_test_db.py`` and the existing
    ``test_api_add_plan_attachment.py`` for the same pattern).
    """
    async with KanbanSessionLocal() as s:
        card = (await s.execute(
            select(KanbanCard)
            .where(KanbanCard.id == card_id)
            .options(selectinload(KanbanCard.deliverables))
        )).scalars().first()
    return [(d.kind, d.ref) for d in (card.deliverables if card else [])]


async def _comments(ac, card_id):
    r = await ac.get(f"/api/v1/kanban/cards/{card_id}/activity")
    return [e["payload"].get("text", "") for e in r.json()
            if e["op_type"] == "comment"]


@pytest.mark.asyncio
async def test_delete_parent_drops_plan_ref_pointing_at_that_parent():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create(ac, "parent", column="Done", confirm=True)
        child = await _create(ac, "child", parent_card_id=parent)
        await _add_plan(ac, parent, [child])

        # pre-condition: child carries exactly one plan_ref pointing at parent
        before = await _deliverables(child)
        assert len(before) == 1
        assert before[0][0] == "plan_ref"
        assert f'"parent_card_id": "{parent}"' in before[0][1]

        r = await ac.delete(f"/api/v1/kanban/cards/{parent}")
        assert r.status_code == 204

        # Child lives on, parent_card_id cleared, its plan_ref is GONE
        r = await ac.get(f"/api/v1/kanban/cards/{child}")
        assert r.status_code == 200
        assert r.json()["parent_card_id"] is None
        assert await _deliverables(child) == []


@pytest.mark.asyncio
async def test_delete_parent_leaves_plan_refs_pointing_at_other_parents_intact():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        victim = await _create(ac, "victim", column="Done", confirm=True)
        surviving = await _create(ac, "survivor", confirm=True)
        victim_child = await _create(ac, "vc", parent_card_id=victim)
        surviving_child = await _create(ac, "sc", parent_card_id=surviving)
        await _add_plan(ac, victim, [victim_child])
        await _add_plan(ac, surviving, [surviving_child])

        r = await ac.delete(f"/api/v1/kanban/cards/{victim}")
        assert r.status_code == 204

        # The cleanup is scoped to refs whose parent_card_id matches the
        # deleted parent — the surviving tree's plan_ref must be untouched.
        assert await _deliverables(victim_child) == []
        surviving_refs = await _deliverables(surviving_child)
        assert len(surviving_refs) == 1
        assert surviving_refs[0][0] == "plan_ref"
        assert surviving in surviving_refs[0][1]


@pytest.mark.asyncio
async def test_delete_parent_audit_comment_mentions_plan_ref_cleanup():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create(ac, "parent", column="Done", confirm=True)
        child = await _create(ac, "child", parent_card_id=parent)
        await _add_plan(ac, parent, [child])

        r = await ac.delete(f"/api/v1/kanban/cards/{parent}")
        assert r.status_code == 204

        texts = await _comments(ac, child)
        parent_notes = [t for t in texts if t.startswith("**Parent removed:** ")]
        assert len(parent_notes) == 1, texts
        note = parent_notes[0]
        assert parent in note
        # The cleanup is invisible to the operator without an explicit mention;
        # leaving the row would have been the alternative and is what the
        # sweeper used to surface post-hoc.
        assert "plan_ref" in note or "plan attachment" in note, note


@pytest.mark.asyncio
async def test_clear_done_column_drops_plan_refs_on_orphaned_children():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create(ac, "parent", column="Done", confirm=True)
        child = await _create(ac, "child", parent_card_id=parent)
        await _add_plan(ac, parent, [child])

        r = await ac.post("/api/v1/kanban/clear-column",
                          json={"project_key": "P", "column": "Done"})
        assert r.status_code == 200
        assert r.json()["cleared"] == 1

        # Clear-Done routes through the same orphan_children_on_delete repair;
        # without that, every routine end-of-life sweep would pile up
        # dangling_parent rows in the sweeper.
        assert (await ac.get(f"/api/v1/kanban/cards/{parent}")).status_code == 404
        assert await _deliverables(child) == []


@pytest.mark.asyncio
async def test_delete_parent_with_no_plan_ref_deliverable_is_safe():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        # Parent has a child, but no plan attachment — the cleanup branch
        # must be a no-op rather than crash on an empty query result.
        parent = await _create(ac, "parent", column="Done", confirm=True)
        child = await _create(ac, "child", parent_card_id=parent)

        r = await ac.delete(f"/api/v1/kanban/cards/{parent}")
        assert r.status_code == 204

        r = await ac.get(f"/api/v1/kanban/cards/{child}")
        assert r.status_code == 200
        assert r.json()["parent_card_id"] is None
        assert await _deliverables(child) == []