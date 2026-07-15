"""Tests for KanbanPlanService — kanban-DB-backed plan CRUD.

Replaces the legacy file-backed PlanService for the /api/v1/plans endpoint
(kanban card 727470a8 / docs/cockpit/00-orientation.md §3 drie-bomen-regel).
The new service scopes plans by project_key so plans live in the kanban DB
alongside cards, satisfying the "single canonical storage" rule.

Tests run against the shared kanban test DB (tests/kanban_test_db.py via
conftest). Each test gets a fresh schema, so the tests are independent and
order-independent.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.kanban.models import KanbanPlan
from app.services.kanban_plan_service import KanbanPlanService
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


# ---- slug validation -----------------------------------------------------


def test_normalize_slug_rejects_path_traversal():
    with pytest.raises(ValueError):
        KanbanPlanService.normalize_slug("../etc/passwd")


def test_normalize_slug_rejects_slash():
    with pytest.raises(ValueError):
        KanbanPlanService.normalize_slug("sub/dir/foo")


def test_normalize_slug_accepts_simple_string():
    assert KanbanPlanService.normalize_slug("my-plan") == "my-plan"


def test_normalize_slug_strips_md_extension():
    assert KanbanPlanService.normalize_slug("my-plan.md") == "my-plan"


def test_normalize_slug_truncates_to_max_length():
    long = "a" * 300
    out = KanbanPlanService.normalize_slug(long)
    assert len(out) <= 256


# ---- extract_title / metadata helpers -------------------------------------


def test_extract_title_strips_plan_prefix():
    assert KanbanPlanService.extract_title("# Plan: Build the widget") == "Build the widget"


def test_extract_title_handles_em_dash_prefix():
    assert KanbanPlanService.extract_title("# Plan — Fix the leak") == "Fix the leak"


def test_extract_title_falls_back_to_untitled():
    assert KanbanPlanService.extract_title("just some prose\n\nwithout a heading") == "(untitled)"


def test_extract_excerpt_skips_title_line():
    content = "# Plan: Title\n\nFirst body line.\nMore body."
    out = KanbanPlanService.extract_excerpt(content)
    assert "First body line" in out
    assert "Title" not in out.split("\n")[0]


# ---- CRUD: create + list + get + update + delete -------------------------


@pytest.mark.asyncio
async def test_create_plan_persists_with_project_key_fk():
    async with KanbanSessionLocal() as s:
        plan = await KanbanPlanService.create_plan(
            s, project_key="proj-a", slug="build-widget",
            content="# Plan: Build widget\n\nDo the widget.",
        )
        await s.commit()

    assert plan["project_key"] == "proj-a"
    assert plan["slug"] == "build-widget"
    assert plan["filename"] == "build-widget.md"
    assert plan["title"] == "Build widget"
    assert plan["size_bytes"] == len(
        b"# Plan: Build widget\n\nDo the widget."
    )

    async with KanbanSessionLocal() as s:
        row = (await s.execute(
            select(KanbanPlan).where(KanbanPlan.slug == "build-widget")
        )).scalar_one()
        assert row.project_key == "proj-a"
        assert row.content.startswith("# Plan:")


@pytest.mark.asyncio
async def test_create_plan_duplicate_slug_raises():
    async with KanbanSessionLocal() as s:
        await KanbanPlanService.create_plan(
            s, project_key="proj-a", slug="dup", content="one",
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError, match="already exists"):
            await KanbanPlanService.create_plan(
                s, project_key="proj-a", slug="dup", content="two",
            )


@pytest.mark.asyncio
async def test_create_plan_same_slug_different_project_is_allowed():
    """(project_key, slug) is the unique key — same slug in different projects is fine."""
    async with KanbanSessionLocal() as s:
        await KanbanPlanService.create_plan(
            s, project_key="proj-a", slug="shared", content="a",
        )
        await KanbanPlanService.create_plan(
            s, project_key="proj-b", slug="shared", content="b",
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        rows = (await s.execute(
            select(KanbanPlan).where(KanbanPlan.slug == "shared")
        )).scalars().all()
    assert len(rows) == 2
    assert {r.project_key for r in rows} == {"proj-a", "proj-b"}


@pytest.mark.asyncio
async def test_list_plans_returns_only_project_scoped_rows():
    async with KanbanSessionLocal() as s:
        await KanbanPlanService.create_plan(
            s, project_key="proj-a", slug="a1", content="# Plan: A1\n\nfoo",
        )
        await KanbanPlanService.create_plan(
            s, project_key="proj-a", slug="a2", content="# Plan: A2\n\nbar",
        )
        await KanbanPlanService.create_plan(
            s, project_key="proj-b", slug="b1", content="# Plan: B1\n\nbaz",
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        plans = await KanbanPlanService.list_plans(s, "proj-a")
    slugs = {p["slug"] for p in plans}
    assert slugs == {"a1", "a2"}


@pytest.mark.asyncio
async def test_list_plans_sorted_by_updated_desc():
    async with KanbanSessionLocal() as s:
        await KanbanPlanService.create_plan(
            s, project_key="p", slug="older", content="older",
        )
        await s.commit()
        await KanbanPlanService.create_plan(
            s, project_key="p", slug="newer", content="newer",
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        plans = await KanbanPlanService.list_plans(s, "p")
    assert plans[0]["slug"] == "newer"
    assert plans[-1]["slug"] == "older"


@pytest.mark.asyncio
async def test_get_plan_returns_detail_with_metadata():
    async with KanbanSessionLocal() as s:
        await KanbanPlanService.create_plan(
            s, project_key="p", slug="getme",
            content="# Plan: Get me\n\n## Step 1\nDo it.",
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        plan = await KanbanPlanService.get_plan(s, "p", "getme")
    assert plan is not None
    assert plan["slug"] == "getme"
    assert plan["title"] == "Get me"
    assert "Step 1" in plan["headings"]
    assert plan["linked_sessions"] == []


@pytest.mark.asyncio
async def test_get_plan_returns_none_for_missing():
    async with KanbanSessionLocal() as s:
        plan = await KanbanPlanService.get_plan(s, "p", "nope")
    assert plan is None


@pytest.mark.asyncio
async def test_update_plan_bumps_updated_at_and_overwrites_content():
    async with KanbanSessionLocal() as s:
        await KanbanPlanService.create_plan(
            s, project_key="p", slug="upd",
            content="# Plan: Original\n\nv1 short",
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        before = await KanbanPlanService.get_plan(s, "p", "upd")

    async with KanbanSessionLocal() as s:
        updated = await KanbanPlanService.update_plan(
            s, project_key="p", slug="upd",
            content=(
                "# Plan: Original\n\n"
                "v2 content with substantially more prose "
                "so that the byte count is strictly larger."
            ),
        )
        await s.commit()

    assert updated is not None
    assert "v2 content" in updated["content"]
    assert updated["size_bytes"] > before["size_bytes"]


@pytest.mark.asyncio
async def test_update_plan_returns_none_for_missing():
    async with KanbanSessionLocal() as s:
        result = await KanbanPlanService.update_plan(
            s, project_key="p", slug="ghost", content="x",
        )
    assert result is None


@pytest.mark.asyncio
async def test_delete_plan_returns_true_then_false():
    async with KanbanSessionLocal() as s:
        await KanbanPlanService.create_plan(
            s, project_key="p", slug="del", content="bye",
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        first = await KanbanPlanService.delete_plan(s, "p", "del")
        await s.commit()
    assert first is True

    async with KanbanSessionLocal() as s:
        again = await KanbanPlanService.delete_plan(s, "p", "del")
    assert again is False


@pytest.mark.asyncio
async def test_search_plans_finds_matches_in_content():
    async with KanbanSessionLocal() as s:
        await KanbanPlanService.create_plan(
            s, project_key="p", slug="alpha",
            content="# Plan: Alpha\n\nDiscusses the widget.",
        )
        await KanbanPlanService.create_plan(
            s, project_key="p", slug="beta",
            content="# Plan: Beta\n\nDiscusses the cog.",
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        results = await KanbanPlanService.search_plans(s, "p", "widget")
    assert len(results) == 1
    assert results[0]["slug"] == "alpha"


@pytest.mark.asyncio
async def test_search_plans_case_insensitive():
    async with KanbanSessionLocal() as s:
        await KanbanPlanService.create_plan(
            s, project_key="p", slug="ci",
            content="# Plan: CI\n\nALLCAPS marker line.",
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await KanbanPlanService.search_plans(s, "p", "allcaps")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_search_plans_returns_empty_for_no_match():
    async with KanbanSessionLocal() as s:
        await KanbanPlanService.create_plan(
            s, project_key="p", slug="z", content="zzz",
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await KanbanPlanService.search_plans(s, "p", "nopenope")
    assert results == []


@pytest.mark.asyncio
async def test_get_plan_stats_aggregates_by_project():
    async with KanbanSessionLocal() as s:
        await KanbanPlanService.create_plan(
            s, project_key="p", slug="a", content="AAA",
        )
        await KanbanPlanService.create_plan(
            s, project_key="p", slug="b", content="BBBBB",
        )
        # different project — must not show up in p's stats
        await KanbanPlanService.create_plan(
            s, project_key="q", slug="q", content="CCCC",
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        stats = await KanbanPlanService.get_plan_stats(s, "p")
    assert stats["total_plans"] == 2
    assert stats["total_size_bytes"] == 3 + 5


@pytest.mark.asyncio
async def test_get_plan_stats_empty_project_returns_zeros():
    async with KanbanSessionLocal() as s:
        stats = await KanbanPlanService.get_plan_stats(s, "empty-project")
    assert stats == {
        "total_plans": 0,
        "oldest_date": None,
        "newest_date": None,
        "total_size_bytes": 0,
    }


# ---- get_plan_sessions (slug scan; back-compat with old PlanService) -----


@pytest.mark.asyncio
async def test_get_plan_sessions_filters_by_slug(monkeypatch, tmp_path):
    """get_plan_sessions is unchanged from the old PlanService — it scans
    JSONL files for the slug field and returns matching session metadata.
    The point of this test is to pin the contract so the kanban-shaped
    service keeps the back-compat shape that the existing API consumers
    (PlanDetailPage) depend on.

    Uses tmp_path to fabricate a fake ``~/.claude/projects`` tree.
    """
    import json

    from app.services import kanban_plan_service

    projects_dir = tmp_path / "projects" / "proj-foo"
    projects_dir.mkdir(parents=True)

    session_id = "abc123def456"
    jsonl_path = projects_dir / f"{session_id}.jsonl"
    entries = [
        {"slug": "build-widget", "timestamp": "2026-07-01T10:00:00Z",
         "gitBranch": "main"},
        {"slug": "other-plan", "timestamp": "2026-07-01T11:00:00Z",
         "gitBranch": "main"},
        {"slug": "build-widget", "timestamp": "2026-07-02T09:00:00Z",
         "gitBranch": "feat/widgets"},
    ]
    jsonl_path.write_text("\n".join(json.dumps(e) for e in entries))

    monkeypatch.setattr(kanban_plan_service, "get_claude_projects_dir",
                        lambda: tmp_path / "projects")

    sessions = KanbanPlanService.get_plan_sessions("build-widget")
    assert len(sessions) == 1
    s = sessions[0]
    assert s["session_id"] == session_id
    assert s["first_seen"] == "2026-07-01T10:00:00Z"
    assert s["last_seen"] == "2026-07-02T09:00:00Z"


@pytest.mark.asyncio
async def test_get_plan_sessions_returns_empty_for_no_matches(monkeypatch, tmp_path):
    from app.services import kanban_plan_service

    projects_dir = tmp_path / "projects" / "proj-bar"
    projects_dir.mkdir(parents=True)
    session_id = "xyz"
    (projects_dir / f"{session_id}.jsonl").write_text(
        '{"slug": "different-plan", "timestamp": "2026-07-01T10:00:00Z"}\n'
    )

    monkeypatch.setattr(kanban_plan_service, "get_claude_projects_dir",
                        lambda: tmp_path / "projects")

    sessions = KanbanPlanService.get_plan_sessions("nothing-here")
    assert sessions == []
