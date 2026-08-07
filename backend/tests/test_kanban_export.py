# backend/tests/test_kanban_export.py
"""Tests for the kanban-board export endpoint.

Acceptance: GET /api/v1/kanban/export must serialize a complete project
board — all card fields, deliverables, comments, attachments, columns —
into a lossless JSON shape so the institutional memory of the project
can survive the database itself (kanban card 39d2d54a… / kanban-pro
analyse §4.2). Read-only and project-scoped; one JSON blob per request.
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


@pytest.mark.asyncio
async def test_export_returns_all_card_fields_with_comments_and_deliverables(tmp_path):
    """A board with ≥1 card carrying comments, deliverables, and a dep
    relation must come back with every field populated. This is the
    acceptance-criterion #4 contract from the card."""
    project_path = tmp_path / "x"
    project_path.mkdir()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        # Seed the standard columns via enable so the export's columns
        # list reflects what a real, enabled project looks like. The
        # endpoint intentionally returns ``slug:<slug>`` so the
        # ``project_key`` we use downstream matches.
        r = await ac.post("/api/v1/kanban/enable",
                          json={"project_path": str(project_path),
                                "slug": "P"})
        assert r.status_code == 200, r.text
        project_key = r.json()["project_key"]

        # Parent card with a deliverable + a Done comment + a dependency edge.
        parent = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": project_key, "title": "Parent",
                  "description": "lead card",
                  "labels": ["urgent", "backend"],
                  "work_type": "feature",
                  "metadata": {"facet": "A", "priority": 1}})).json()
        parent_id = parent["id"]

        # Deliverable on the parent.
        d_resp = await ac.post(f"/api/v1/kanban/cards/{parent_id}/deliverables",
            json={"kind": "branch", "ref": "k-feature-x"})
        assert d_resp.status_code == 200, d_resp.text
        deliverable = d_resp.json()["deliverables"][-1]
        deliverable_id = deliverable["id"]

        # Comment on the parent (a normal one AND a Done summary).
        c_resp = await ac.post(f"/api/v1/kanban/cards/{parent_id}/comment",
            json={"text": "first comment on parent"})
        assert c_resp.status_code == 200, c_resp.text
        # Kaart efbb82e6… — REST /move now enforces the summary gate;
        # pass the summary inline so the move lands in Done. The
        # **Summary:** comment is posted by the handler itself; no
        # separate /comment call needed.
        s_resp = await ac.post(f"/api/v1/kanban/cards/{parent_id}/move",
            json={"column": "Done",
                  "summary": "shipped feature X"})
        assert s_resp.status_code == 200, s_resp.text

        # Child card that depends on the parent. Created under a different
        # rank so the parent/child relationship is unambiguous.
        child = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": project_key, "title": "Child",
                  "depends_on": [parent_id],
                  "parent_card_id": parent_id})).json()
        child_id = child["id"]
        await ac.post(f"/api/v1/kanban/cards/{child_id}/comment",
            json={"text": "child note"})

        # Hit the export endpoint.
        r = await ac.get("/api/v1/kanban/export",
                         params={"project_key": project_key})
        assert r.status_code == 200, r.text
        payload = r.json()

        # ---- top-level shape ----
        assert payload["project_key"] == project_key
        assert "exported_at" in payload
        assert payload["format_version"] >= 1
        assert "columns" in payload
        assert "cards" in payload

        # ---- columns round-trip ----
        col_names = {c["name"] for c in payload["columns"]}
        assert {"Backlog", "Done", "Impediment"}.issubset(col_names)

        # ---- both cards present ----
        by_id = {c["id"]: c for c in payload["cards"]}
        assert {parent_id, child_id} == set(by_id.keys())

        # ---- parent card: every field the card spec calls out ----
        p = by_id[parent_id]
        assert p["title"] == "Parent"
        assert p["description"] == "lead card"
        assert p["column"] == "Done"
        assert p["rank"] == parent["rank"]
        assert p["labels"] == ["urgent", "backend"]
        assert p["work_type"] == "feature"
        assert p["metadata"] == {"facet": "A", "priority": 1}
        # The Pydantic schema types `depends_on` as `list[str] | None`,
        # so an empty list serializes as null — same convention as the
        # rest of the kanban API. The presence of the key is what matters
        # for round-tripping.
        assert p["depends_on"] in (None, [])

        # ---- parent card: deliverables come back ----
        assert any(
            d["id"] == deliverable_id and d["kind"] == "branch"
            and d["ref"] == "k-feature-x"
            for d in p["deliverables"]
        )

        # ---- parent card: comments come back, including the **Summary:** one ----
        comment_texts = [c["text"] for c in p["comments"]]
        assert "first comment on parent" in comment_texts
        assert "**Summary:** shipped feature X" in comment_texts

        # ---- child card: depends_on + parent_card_id round-trip ----
        c = by_id[child_id]
        assert c["depends_on"] == [parent_id]
        assert c["parent_card_id"] == parent_id
        assert c["column"] == "Backlog"
        assert any(cm["text"] == "child note" for cm in c["comments"])


@pytest.mark.asyncio
async def test_export_returns_404_for_unknown_project_key():
    """Same guard the rest of the user-facing endpoints apply: a typo'd
    project_key should not silently return an empty board."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get("/api/v1/kanban/export",
                         params={"project_key": "does-not-exist"})
        assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_export_ignores_other_projects(tmp_path):
    """Export must be project-scoped: cards belonging to a different
    project_key must not leak into the response."""
    p_path = tmp_path / "p"
    q_path = tmp_path / "q"
    p_path.mkdir()
    q_path.mkdir()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        # Seed both projects via enable so the col-check + project-key
        # guards are honest (both projects are recognised).
        r = await ac.post("/api/v1/kanban/enable",
                          json={"project_path": str(p_path), "slug": "P"})
        assert r.status_code == 200, r.text
        p_key = r.json()["project_key"]
        r = await ac.post("/api/v1/kanban/enable",
                          json={"project_path": str(q_path), "slug": "Q"})
        assert r.status_code == 200, r.text
        q_key = r.json()["project_key"]

        await ac.post("/api/v1/kanban/cards",
            json={"project_key": p_key, "title": "keep"})
        await ac.post("/api/v1/kanban/cards",
            json={"project_key": q_key, "title": "drop"})

        r = await ac.get("/api/v1/kanban/export",
                         params={"project_key": p_key})
        assert r.status_code == 200, r.text
        titles = [c["title"] for c in r.json()["cards"]]
        assert "keep" in titles
        assert "drop" not in titles
