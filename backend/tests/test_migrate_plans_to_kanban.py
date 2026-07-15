"""Tests for the migration script that moves ``~/.claude/plans/*.md``
into the kanban-DB ``kanban_plans`` table.

The script is intentionally standalone (calls ``KanbanSessionLocal``
directly, not the FastAPI app) so it can be run from a shell with
``python -m backend.scripts.migrate_plans_to_kanban`` even when the
backend isn't running.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.kanban.models import KanbanPlan
from scripts.migrate_plans_to_kanban import (
    collect_plan_files,
    derive_title,
    migrate_one,
    run_migration,
    slug_from_filename,
)
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


# ---- pure helpers ---------------------------------------------------------


def test_slug_from_filename_strips_md():
    assert slug_from_filename(Path("my-plan.md")) == "my-plan"


def test_slug_from_filename_keeps_non_md():
    assert slug_from_filename(Path("notes.txt")) == "notes.txt"


def test_derive_title_handles_plan_prefix():
    assert derive_title("# Plan: Build it\n\nbody") == "Build it"


def test_derive_title_handles_em_dash_prefix():
    assert derive_title("# Plan — Fix bug\n\nbody") == "Fix bug"


def test_derive_title_falls_back_when_no_heading():
    assert derive_title("just body text\n\nmore body") == "(untitled)"


def test_collect_plan_files_finds_md_files(tmp_path):
    (tmp_path / "alpha.md").write_text("# Plan: Alpha")
    (tmp_path / "beta.md").write_text("# Plan: Beta")
    (tmp_path / "ignore.txt").write_text("not a plan")

    files = collect_plan_files(tmp_path)
    names = sorted(p.name for p in files)
    assert names == ["alpha.md", "beta.md"]


def test_collect_plan_files_returns_empty_when_dir_missing(tmp_path):
    assert collect_plan_files(tmp_path / "does-not-exist") == []


# ---- single-file migration -------------------------------------------------


@pytest.mark.asyncio
async def test_migrate_one_inserts_row_with_project_key(tmp_path):
    plan_file = tmp_path / "alpha.md"
    plan_file.write_text("# Plan: Alpha\n\ndo the thing.")

    async with KanbanSessionLocal() as s:
        result = await migrate_one(
            s, plan_file, project_key="proj-a", source_root=tmp_path,
        )
        await s.commit()

    assert result["status"] == "inserted"
    assert result["slug"] == "alpha"
    assert result["project_key"] == "proj-a"

    async with KanbanSessionLocal() as s:
        row = (await s.execute(
            select(KanbanPlan).where(KanbanPlan.slug == "alpha")
        )).scalar_one()
        assert row.content == "# Plan: Alpha\n\ndo the thing."
        assert row.title == "Alpha"


@pytest.mark.asyncio
async def test_migrate_one_skips_duplicate_idempotently(tmp_path):
    plan_file = tmp_path / "alpha.md"
    plan_file.write_text("# Plan: Alpha\n\nfirst version")

    async with KanbanSessionLocal() as s:
        first = await migrate_one(
            s, plan_file, project_key="proj-a", source_root=tmp_path,
        )
        await s.commit()
    assert first["status"] == "inserted"

    # Mutate the source file but keep the slug the same — second migration
    # should NOT overwrite the existing row (idempotent by (project_key, slug)).
    plan_file.write_text("# Plan: Alpha\n\nsecond version, must NOT overwrite")

    async with KanbanSessionLocal() as s:
        second = await migrate_one(
            s, plan_file, project_key="proj-a", source_root=tmp_path,
        )
        await s.commit()
    assert second["status"] == "skipped"
    assert second["reason"] == "already exists"

    async with KanbanSessionLocal() as s:
        row = (await s.execute(
            select(KanbanPlan).where(KanbanPlan.slug == "alpha")
        )).scalar_one()
        assert row.content == "# Plan: Alpha\n\nfirst version"


@pytest.mark.asyncio
async def test_migrate_one_dry_run_does_not_persist(tmp_path):
    plan_file = tmp_path / "ghost.md"
    plan_file.write_text("# Plan: Ghost\n\nnever persisted")

    async with KanbanSessionLocal() as s:
        result = await migrate_one(
            s, plan_file, project_key="proj-a", source_root=tmp_path,
            dry_run=True,
        )
        await s.commit()
    assert result["status"] == "would_insert"

    async with KanbanSessionLocal() as s:
        rows = (await s.execute(select(KanbanPlan))).scalars().all()
    assert rows == []


# ---- whole-tree migration --------------------------------------------------


@pytest.mark.asyncio
async def test_run_migration_processes_all_files(tmp_path):
    (tmp_path / "a.md").write_text("# Plan: A")
    (tmp_path / "b.md").write_text("# Plan: B")
    (tmp_path / "c.md").write_text("# Plan: C")

    async with KanbanSessionLocal() as s:
        report = await run_migration(
            source_dir=tmp_path,
            project_key="proj-x",
            session=s,
        )

    assert report["inserted"] == 3
    assert report["skipped"] == 0
    assert sorted(report["slugs"]) == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_run_migration_is_idempotent_on_rerun(tmp_path):
    (tmp_path / "stable.md").write_text("# Plan: Stable\n\nv1")
    (tmp_path / "evolving.md").write_text("# Plan: Evolving\n\nv1")

    async with KanbanSessionLocal() as s:
        first = await run_migration(
            source_dir=tmp_path, project_key="p", session=s,
        )
        await s.commit()
    assert first["inserted"] == 2

    # Mutate one file before the second run.
    (tmp_path / "evolving.md").write_text("# Plan: Evolving\n\nv2")
    async with KanbanSessionLocal() as s:
        second = await run_migration(
            source_dir=tmp_path, project_key="p", session=s,
        )
    assert second["inserted"] == 0
    assert second["skipped"] == 2

    # Existing rows survive unchanged.
    async with KanbanSessionLocal() as s:
        rows = (await s.execute(
            select(KanbanPlan).order_by(KanbanPlan.slug)
        )).scalars().all()
    contents = {r.slug: r.content for r in rows}
    assert contents["evolving"].endswith("v1")  # NOT v2


@pytest.mark.asyncio
async def test_run_migration_dry_run_persists_nothing(tmp_path):
    (tmp_path / "x.md").write_text("# Plan: X")

    async with KanbanSessionLocal() as s:
        report = await run_migration(
            source_dir=tmp_path, project_key="p", session=s, dry_run=True,
        )

    assert report["inserted"] == 0
    assert report["would_insert"] == 1
    async with KanbanSessionLocal() as s:
        rows = (await s.execute(select(KanbanPlan))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_run_migration_handles_empty_dir(tmp_path):
    async with KanbanSessionLocal() as s:
        report = await run_migration(
            source_dir=tmp_path, project_key="p", session=s,
        )
    assert report == {
        "inserted": 0, "skipped": 0, "would_insert": 0,
        "errors": [], "slugs": [],
    }


@pytest.mark.asyncio
async def test_run_migration_handles_missing_dir(tmp_path):
    """A missing source dir is treated as a no-op, not an error: the
    migration script is meant to be safe to run any time, even before any
    plans have been written to ``~/.claude/plans/``."""
    async with KanbanSessionLocal() as s:
        report = await run_migration(
            source_dir=tmp_path / "absent", project_key="p", session=s,
        )
    assert report["inserted"] == 0
    assert report["errors"] == []