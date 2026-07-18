"""Tests for ``GET /api/v1/plans/overview`` — the B+C read-only aggregator.

Kanban card 885d0b61 (Optie B, stap 1). The endpoint returns TWO
independent sections: B = ``plan``/``plan_ref`` deliverables on cards
scoped to ``project_key``, C = ``docs/cockpit/*.md`` filesystem index.
No ``spec_doc`` join (that requires a producer for the anchor — separate
analysis card bb1f61aa). The legacy ``kanban_plans`` table/CRUD it
replaced was removed entirely (kanban card 528c5ca2).
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1 import plans as plans_module
from app.kanban.models import KanbanDeliverable
from app.main import app
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.fixture
def patched_project_key(monkeypatch):
    """Make ``resolve_project_key(<path>)`` deterministic.

    Without this, a hand-typed path like ``/tmp/whatever`` would resolve
    to ``slug:whatever`` (or fall back to ``slug:global-plans``), and
    every card we create with a hand-typed ``git:foo`` project_key
    would never appear in the response. The tests assert behaviour, not
    the path resolver's quirks, so we sidestep both.

    Returns a callable ``set(path, key)`` that registers a mapping. A
    default catch-all is registered too so any un-mapped path returns
    ``slug:global-plans`` — matches the legacy fallback contract.
    """
    from app.kanban import project_key as pk_mod

    table: dict[str, str] = {}

    def _fake_resolve(path: str) -> str:
        return table.get(path, "slug:global-plans")

    monkeypatch.setattr(pk_mod, "resolve_project_key", _fake_resolve)
    # The router imports it by name into its own namespace, so we have to
    # patch BOTH the source module (per the test-doubles convention — see
    # ``docs/cockpit/test-doubles-convention.md``) and the binding the
    # router actually reads. Two writes keeps the patch honest from
    # either import path.
    monkeypatch.setattr(plans_module, "resolve_project_key", _fake_resolve)
    return table.__setitem__


async def _create_card(ac: AsyncClient, project_key: str, title: str) -> str:
    """Create a card directly so deliverables can be attached below.

    Goes through ``POST /cards`` to mirror the real mutation pipeline (the
    same one ``add_plan_attachment`` uses internally), so the test exercises
    the same FK + op-log materialisation path the production code does.
    """
    body = {
        "project_key": project_key, "title": title,
        "confirm_new_project": True,
    }
    r = await ac.post("/api/v1/kanban/cards", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _attach_plan_deliverable(card_id: str, kind: str, ref: str) -> str:
    """Insert a ``plan``/``plan_ref`` row directly through the ORM.

    Goes around the public REST surface on purpose: the focus here is the
    *read* endpoint, and the deliverable-attach routes are already covered
    by ``test_api_add_plan_attachment``. This keeps the test cheap and the
    failure surface narrow.
    """
    async with KanbanSessionLocal() as s:
        d = KanbanDeliverable(
            id=f"deliv-{kind}-{card_id[:8]}",
            card_id=card_id, kind=kind, ref=ref,
        )
        s.add(d)
        await s.commit()
        return d.id


# ---------------------------------------------------------------------------
# Empty / happy-path shape tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overview_returns_two_independent_sections(patched_project_key):
    """The response shape is exactly ``{"project_key", "cards", "docs"}``
    with the two sections decoupled — no shared ``join``/``correlation``/
    ``spec_doc`` field at the top level. Cards is project-scoped (so empty
    here); docs is repo-wide (so non-empty on this checkout).
    """
    patched_project_key("/tmp/whatever-does-not-exist",
                        "slug:some-empty")
    async with _client() as ac:
        r = await ac.get("/api/v1/plans/overview", params={
            "project_path": "/tmp/whatever-does-not-exist",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    # Exactly the documented shape; no invented join/correlation field.
    assert set(body.keys()) == {"project_key", "cards", "docs"}
    assert body["cards"] == []
    # C is repo-wide — verify it's a list, not a scalar or dict, and that
    # it carries the doc-level shape (the *contents* are exercised by the
    # docs-specific tests below).
    assert isinstance(body["docs"], list)


@pytest.mark.asyncio
async def test_overview_empty_project_has_empty_card_section_and_populated_docs(
    patched_project_key,
):
    """Empty project → B empty, C populated (docs/cockpit is repo-wide,
    not project-scoped — see plan §8.2 caveat and kanban card bb1f61aa).
    """
    patched_project_key("/tmp/some-empty-project", "slug:some-empty")
    async with _client() as ac:
        r = await ac.get("/api/v1/plans/overview", params={
            "project_path": "/tmp/some-empty-project",
        })
    body = r.json()
    assert body["cards"] == []
    assert len(body["docs"]) > 0
    for d in body["docs"]:
        assert {"path", "title", "modified_at", "size_bytes"} <= set(d.keys())


@pytest.mark.asyncio
async def test_overview_docs_section_indexes_docs_cockpit_root(
    patched_project_key,
):
    """The docs section reads the *real* ``docs/cockpit/`` tree — paths
    are repo-relative and start with the expected prefix. Stable picks
    that have existed across many sessions (the plans decision doc and
    the kanban convention doc are both old enough to be safe fixtures).
    """
    patched_project_key("/tmp/x", "slug:any")
    async with _client() as ac:
        r = await ac.get("/api/v1/plans/overview", params={
            "project_path": "/tmp/x",
        })
    paths = {d["path"] for d in r.json()["docs"]}
    assert "docs/cockpit/plans-feature-decision.md" in paths
    assert "docs/cockpit/kanban-conventions.md" in paths


@pytest.mark.asyncio
async def test_overview_docs_title_is_h1_of_file(patched_project_key):
    """The H1 of a known file becomes its title in the response — keeps
    the docs section a faithful index of the SSOT files, not a re-invented
    one. ``terminology.md`` is the safest fixture (plain ASCII H1, oldest
    doc in the tree).
    """
    patched_project_key("/tmp/x", "slug:any")
    async with _client() as ac:
        r = await ac.get("/api/v1/plans/overview", params={
            "project_path": "/tmp/x",
        })
    by_path = {d["path"]: d for d in r.json()["docs"]}
    term = by_path.get("docs/cockpit/terminology.md")
    assert term is not None
    assert term["title"].startswith("# ")
    assert "Terminology" in term["title"]


# ---------------------------------------------------------------------------
# Section B — card-scoped deliverable listing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overview_card_section_lists_plan_and_plan_ref_deliverables(
    patched_project_key,
):
    """A card carrying both a ``plan`` and a ``plan_ref`` deliverable
    surfaces both rows in B; the ``kind`` field distinguishes them so the
    SPA can render parent (plan) vs. child (plan_ref) differently.
    """
    patched_project_key("/tmp/b1", "git:overview-b-1")
    async with _client() as ac:
        card_id = await _create_card(ac, "git:overview-b-1", "parent card")
        await _attach_plan_deliverable(
            card_id, kind="plan",
            ref="# Plan: aggregator\n\nThe plan body.",
        )
        await _attach_plan_deliverable(
            card_id, kind="plan_ref",
            ref='{"parent_card_id": "x", "plan_deliverable_id": "y"}',
        )

        r = await ac.get("/api/v1/plans/overview", params={
            "project_path": "/tmp/b1",
        })
    body = r.json()
    matching = [c for c in body["cards"] if c["card_id"] == card_id]
    assert len(matching) == 2
    kinds = {row["kind"] for row in matching}
    assert kinds == {"plan", "plan_ref"}


@pytest.mark.asyncio
async def test_overview_card_section_is_project_scoped(patched_project_key):
    """A ``plan`` deliverable on project X must NOT appear in the overview
    of project Y. Cross-project leakage would defeat the per-project
    scoping the rest of the kanban API enforces.
    """
    patched_project_key("/tmp/x", "git:project-x")
    patched_project_key("/tmp/y", "git:project-y")
    async with _client() as ac:
        card_x = await _create_card(ac, "git:project-x", "X parent")
        card_y = await _create_card(ac, "git:project-y", "Y parent")
        await _attach_plan_deliverable(card_x, kind="plan", ref="# X plan")
        await _attach_plan_deliverable(card_y, kind="plan", ref="# Y plan")

        r_x = await ac.get("/api/v1/plans/overview", params={
            "project_path": "/tmp/x",
        })
        r_y = await ac.get("/api/v1/plans/overview", params={
            "project_path": "/tmp/y",
        })

    ids_x = {row["card_id"] for row in r_x.json()["cards"]}
    ids_y = {row["card_id"] for row in r_y.json()["cards"]}
    assert card_x in ids_x
    assert card_y not in ids_x
    assert card_y in ids_y
    assert card_x not in ids_y


@pytest.mark.asyncio
async def test_overview_card_section_excludes_other_deliverable_kinds(
    patched_project_key,
):
    """Only ``plan``/``plan_ref`` show up; ``pr``/``branch``/``commit``/
    ``link``/``note`` (the short-list enum) are not plans and must not
    pollute the B section.
    """
    patched_project_key("/tmp/kinds", "git:overview-kinds")
    async with _client() as ac:
        card_id = await _create_card(ac, "git:overview-kinds", "card")
        async with KanbanSessionLocal() as s:
            for kind, ref in [
                ("pr", "https://github.com/example/pr/1"),
                ("branch", "feature/branch-1"),
                ("commit", "deadbeef"),
                ("link", "https://example.com"),
                ("note", "A note."),
            ]:
                s.add(KanbanDeliverable(
                    id=f"deliv-{kind}-{card_id[:8]}",
                    card_id=card_id, kind=kind, ref=ref,
                ))
            await s.commit()

        r = await ac.get("/api/v1/plans/overview", params={
            "project_path": "/tmp/kinds",
        })
    kinds = {row["kind"] for row in r.json()["cards"]}
    assert kinds.isdisjoint({"pr", "branch", "commit", "link", "note"})


@pytest.mark.asyncio
async def test_overview_card_row_includes_card_title_and_excerpt(
    patched_project_key,
):
    """Each B row carries enough context for the SPA to render a row
    without a follow-up fetch: card_title (so the parent/child card
    surfaces), and a short excerpt from the deliverable ref (so the
    planner-body preview works without dragging the full markdown).
    """
    patched_project_key("/tmp/row", "git:overview-row")
    async with _client() as ac:
        card_id = await _create_card(ac, "git:overview-row", "A parent")
        await _attach_plan_deliverable(
            card_id, kind="plan",
            ref="# A plan\n\nFirst paragraph. Second line.",
        )
        r = await ac.get("/api/v1/plans/overview", params={
            "project_path": "/tmp/row",
        })
    rows = [row for row in r.json()["cards"] if row["card_id"] == card_id]
    assert len(rows) == 1
    row = rows[0]
    assert row["card_title"] == "A parent"
    assert "A plan" in row["excerpt"]
    assert row["created_at"]


# ---------------------------------------------------------------------------
# Independence: deleting one section's source must NOT touch the other.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overview_sections_are_independent(patched_project_key):
    """A card-section row in B is purely a DB projection of kanban tables;
    the docs section reads the filesystem. Forcing the docs helper to
    return ``[]`` must not affect the cards section — and the override
    must actually fire (the test-doubles convention
    ``docs/cockpit/test-doubles-convention.md`` says: assert the double
    fired, otherwise a no-op patch that leaves production behaviour
    intact would silently pass).
    """
    patched_project_key("/tmp/indep", "git:overview-indep")
    calls = {"n": 0}

    def _stub_docs() -> list:
        calls["n"] += 1
        return []

    async with _client() as ac:
        card_id = await _create_card(ac, "git:overview-indep", "card")
        await _attach_plan_deliverable(card_id, kind="plan", ref="# x")

        original = plans_module._list_cockpit_docs
        plans_module._list_cockpit_docs = _stub_docs
        try:
            r = await ac.get("/api/v1/plans/overview", params={
                "project_path": "/tmp/indep",
            })
        finally:
            plans_module._list_cockpit_docs = original

    body = r.json()
    # Stub fired — without this assertion, a forgotten / no-op patch
    # would still match ``body["docs"] == []`` by virtue of the
    # production helper returning more than zero items and *that*
    # being filtered to ``[]`` only on accident.
    assert calls["n"] == 1, "monkey-patch never reached the handler"
    assert body["docs"] == []
    assert any(row["card_id"] == card_id for row in body["cards"])


# ---------------------------------------------------------------------------
# Section C — single-doc fetch (kanban card 9e33a359, Optie B, stap 2)
# ---------------------------------------------------------------------------
#
# The list endpoint deliberately ships only metadata (path / title / mtime
# / size); the detail page opens this endpoint when a user expands a row.
# Tests cover the happy path, the shape contract, missing-file 404s, and
# the path-traversal guard — the four failure modes that need to be
# pinned down before the SPA starts calling this route.


@pytest.mark.asyncio
async def test_overview_doc_returns_full_body(patched_project_key):
    """Happy path: an existing ``docs/cockpit/*.md`` is returned with
    its full content, the same H1 title the list endpoint uses, and the
    same ``modified_at`` / ``size_bytes`` so the detail page can render
    without a follow-up call to the list endpoint.
    """
    patched_project_key("/tmp/x", "slug:any")
    list_r = (await _client().get(
        "/api/v1/plans/overview", params={"project_path": "/tmp/x"},
    ))
    list_body = list_r.json()
    target = next(d for d in list_body["docs"] if d["path"].endswith("kanban-conventions.md"))

    async with _client() as ac:
        r = await ac.get(
            f"/api/v1/plans/overview/docs/{target['path']}",
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"] == target["path"]
    # Title matches the list endpoint so the detail page never shows a
    # different H1 than the row that was clicked.
    assert body["title"] == target["title"]
    assert body["modified_at"] == target["modified_at"]
    assert body["size_bytes"] == target["size_bytes"]
    # Body is the real markdown (not the truncated excerpt).
    assert "# " in body["content"]
    assert "kanban-conventies" in body["content"].lower() or "kanban" in body["content"].lower()


@pytest.mark.asyncio
async def test_overview_doc_404_for_missing_file(patched_project_key, monkeypatch):
    """A path that resolves under docs/cockpit/ but doesn't exist on
    disk returns 404 (not 500, not 200 with empty content) — so the SPA
    can distinguish "doc deleted from the tree" from "boom".
    """
    patched_project_key("/tmp/x", "slug:any")

    from app.api.v1 import plans as plans_module
    real_root = plans_module._COCKPIT_DOCS_DIR

    class _Root:
        def resolve(self_inner):
            return real_root.resolve()
        def is_dir(self_inner):
            return True
        def glob(self_inner, _pat):
            return []
    monkeypatch.setattr(plans_module, "_COCKPIT_DOCS_DIR", _Root())

    async with _client() as ac:
        r = await ac.get(
            "/api/v1/plans/overview/docs/docs/cockpit/no-such-file.md",
        )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_overview_doc_rejects_path_traversal(patched_project_key):
    """A request whose resolved path escapes ``docs/cockpit/`` returns
    400 — never the leaked file. The guard uses ``Path.resolve() +
    relative_to()`` so an attacker can't bypass with ``..`` segments
    or URL-encoded slashes that decode to ``/``.
    """
    patched_project_key("/tmp/x", "slug:any")
    async with _client() as ac:
        # ``..`` traversal — resolves outside docs/cockpit, must 400.
        r1 = await ac.get(
            "/api/v1/plans/overview/docs/docs/cockpit/../README.md",
        )
        assert r1.status_code == 400, r1.text
        # Absolute path attempt — also resolves outside, also 400.
        r2 = await ac.get(
            "/api/v1/plans/overview/docs//etc/passwd",
        )
        assert r2.status_code == 400, r2.text
        # Wrong tree entirely — also 400.
        r3 = await ac.get(
            "/api/v1/plans/overview/docs/backend/requirements.txt",
        )
        assert r3.status_code == 400, r3.text