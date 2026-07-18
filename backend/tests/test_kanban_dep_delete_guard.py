# backend/tests/test_kanban_dep_delete_guard.py
"""Dep-aware guard on card delete + Clear-Done.

Root-cause fix from docs/cockpit/dangling-depends-on-analyse.md §1.2/§4:
deleting a card that appears in the `depends_on` of a non-Done card must strip
it out of that dependent (+ audit comment), so the fail-closed dep-resolver
never turns a satisfied dependency into a permanent, invisible block.
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


async def _create(ac, project_key, title, *, column=None, depends_on=None,
                  confirm=False):
    body = {"project_key": project_key, "title": title}
    if confirm:
        body["confirm_new_project"] = True
    if column is not None:
        body["column"] = column
    if depends_on is not None:
        body["depends_on"] = depends_on
    r = await ac.post("/api/v1/kanban/cards", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_delete_card_strips_dep_and_comments_on_non_done_dependent():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create(ac, "P", "parent", column="Done", confirm=True)
        child = await _create(ac, "P", "child", column="Backlog",
                              depends_on=[parent])

        r = await ac.delete(f"/api/v1/kanban/cards/{parent}")
        assert r.status_code == 204

        # parent gone, child survives with the dep stripped
        assert (await ac.get(f"/api/v1/kanban/cards/{parent}")).status_code == 404
        r = await ac.get(f"/api/v1/kanban/cards/{child}")
        assert r.status_code == 200
        assert parent not in (r.json().get("depends_on") or [])

        # an audit comment explaining the removal is posted on the dependent
        r = await ac.get(f"/api/v1/kanban/cards/{child}/activity")
        texts = [e["payload"].get("text", "") for e in r.json()
                 if e["op_type"] == "comment"]
        assert any(t.startswith("**Dependency removed:** ") and parent in t
                   for t in texts), texts


@pytest.mark.asyncio
async def test_delete_card_without_dependents_leaves_siblings_untouched():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        target = await _create(ac, "P", "target", column="Done", confirm=True)
        # sibling depends on something else entirely — must be untouched
        sibling = await _create(ac, "P", "sibling", column="Backlog",
                                depends_on=["some-other-id"])

        r = await ac.delete(f"/api/v1/kanban/cards/{target}")
        assert r.status_code == 204

        r = await ac.get(f"/api/v1/kanban/cards/{sibling}")
        assert r.status_code == 200
        assert r.json().get("depends_on") == ["some-other-id"]

        # no dep-removed comment on a card that never depended on the target
        r = await ac.get(f"/api/v1/kanban/cards/{sibling}/activity")
        texts = [e["payload"].get("text", "") for e in r.json()
                 if e["op_type"] == "comment"]
        assert not any(t.startswith("**Dependency removed:** ") for t in texts)


@pytest.mark.asyncio
async def test_clear_done_strips_dep_from_backlog_dependent():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = await _create(ac, "P", "done parent", column="Done",
                               confirm=True)
        child = await _create(ac, "P", "backlog child", column="Backlog",
                              depends_on=[parent])

        r = await ac.post("/api/v1/kanban/clear-column",
                          json={"project_key": "P", "column": "Done"})
        assert r.status_code == 200
        assert r.json()["cleared"] == 1

        # no dangling dep left behind: the deleted Done parent is stripped
        assert (await ac.get(f"/api/v1/kanban/cards/{parent}")).status_code == 404
        r = await ac.get(f"/api/v1/kanban/cards/{child}")
        assert r.status_code == 200
        assert parent not in (r.json().get("depends_on") or [])
