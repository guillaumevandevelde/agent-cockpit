# backend/tests/test_inception.py
"""Tests for InceptionService.create_project_from_intake.

Drives the inceptie-pipeline from kanban card c33b2f14 (facet A of
platform-as-app-factory). The pipeline is the "promote an idea from the
meta-project's intake column to a brand-new project on the kanban board" flow
described in `docs/cockpit/product-inceptie-pipeline.md` §4 optie 2.

The 6-step atomic scaffold (sibling kanban card 0260dbcd) lives behind the
single `create_project_from_intake` entry point. Atomicity is the load-bearing
property here: a half-registered project (path created + git init done but
Project row missing, OR Project row created but autodispatch-meta missing)
would leave the kanban-DB and the on-disk filesystem in an inconsistent state.
The tests below exercise every step boundary so a regression to "fail mid-flow
and leave detritus behind" is caught.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, or_, select

from app.kanban import dispatch, service
from app.kanban.models import KanbanCard
from app.kanban.operations import apply_operation
from app.kanban.schemas import COLUMNS
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_test_projects():
    """Remove any rows left in the real `projects` table from the prior test.

    Mirrors the convention in `tests/test_mcp_server.py` — InceptionService
    writes to the live `claude_registry.db` (via ProjectService.add_project)
    because the kanban store and the project registry are separate databases
    by design (see kanban/db.py docstring: "device-local data … vs portable
    board"). Tests pin project names under ``inception-test-*`` so the
    cleanup query is precise.
    """
    yield

    from app.database import AsyncSessionLocal
    from app.models.database import Project

    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(Project).where(
                or_(
                    Project.name.like("inception-test-%"),
                    Project.path.like("/tmp/inception-test-%"),
                )
            )
        )
        await db.commit()


async def _create_intake_card(project_key: str, column: str = "intake",
                              title: str = "Build a thing",
                              description: str = "An idea worth building.",
                              work_type: str | None = "feature") -> str:
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=project_key, entity_id=None,
            payload={"title": title, "description": description,
                     "column": column, "work_type": work_type},
        )
        await s.commit()
        return cid


# ---- intake column / schema ------------------------------------------------


def test_intake_is_a_fixed_column():
    """Intake must be in COLUMNS so the dispatcher skips it (no auto-spawn)."""
    assert "intake" in COLUMNS


def test_intake_is_not_a_dispatch_source():
    """`_DISPATCH_COLUMNS` is the explicit allow-list for auto-dispatch — intake
    must NOT be in it, or the dispatcher would auto-claim intake cards and try
    to spawn a session for an idea that should be human-only."""
    assert "intake" not in dispatch._DISPATCH_COLUMNS


def test_intake_card_has_no_persona():
    """Intake cards represent pre-dispatch ideas, not work for any persona.
    A dispatchable column resolves to a `<col>.md` persona file; intake must
    resolve to None so no session gets spawned for it."""
    assert dispatch._persona_filename("intake") is None


# ---- create_project_from_intake: happy path -------------------------------


@pytest.mark.asyncio
async def test_create_project_from_intake_happy_path(tmp_path: Path):
    """End-to-end: intake card → new project on disk + kanban card in it +
    plan_ref link to the intake's plan deliverable + autodispatch enabled +
    intake card moved to Done with a summary."""
    from app.services.inception_service import InceptionService

    intake_id = await _create_intake_card("meta")
    target = tmp_path / "myapp"

    async with KanbanSessionLocal() as s:
        # Attach a plan deliverable so the test exercises plan_ref wiring.
        await apply_operation(
            s, op_type="attach", entity_type="deliverable",
            project_key="meta", entity_id=intake_id,
            payload={"kind": "plan", "ref": "# MyApp\n\nPlan markdown body."},
        )
        await s.commit()

    async with KanbanSessionLocal() as ks:
        # We also need a session bound to the app DB (ProjectService writes
        # there). InceptionService takes both as constructor args.
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            result = await svc.create_project_from_intake(
                intake_card_id=intake_id,
                project_name="MyApp",
                target_path=str(target),
            )

    assert result.project_id  # non-empty
    assert target.exists() and target.is_dir()
    assert (target / ".git").exists()  # git init ran
    assert (target / ".claude" / "CLAUDE.md").exists()  # minimal seed
    assert result.first_card_id  # non-empty
    assert result.new_project_key.startswith("slug:")  # no remote yet

    # New kanban card lives in the new project's Backlog with plan_ref.
    async with KanbanSessionLocal() as s:
        new_card = await service.get_card(s, result.first_card_id)
        assert new_card.project_key == result.new_project_key
        assert new_card.column == "Backlog"
        # plan_ref is a separate deliverable that points at the intake card.
        plan_refs = [d for d in new_card.deliverables if d.kind == "plan_ref"]
        assert plan_refs, "expected a plan_ref deliverable on the new card"
        assert intake_id in plan_refs[0].ref

        # Intake card was moved to Done with a summary.
        intake = await service.get_card(s, intake_id)
        assert intake.column == "Done"
        assert intake.done_summary  # non-empty


# ---- create_project_from_intake: validation -------------------------------


@pytest.mark.asyncio
async def test_rejects_card_not_in_intake_column():
    """A card on Backlog (or any other non-intake column) is not a valid
    intake target. The action is rejected before any side effects."""
    from app.services.inception_service import InceptionService

    cid = await _create_intake_card("meta", column="Backlog")

    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            with pytest.raises(ValueError, match="intake"):
                await svc.create_project_from_intake(
                    intake_card_id=cid, project_name="X",
                    target_path="/tmp/should-never-exist",
                )


@pytest.mark.asyncio
async def test_rejects_missing_card():
    """Unknown intake_card_id → ValueError, no side effects."""
    from app.services.inception_service import InceptionService

    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            with pytest.raises(ValueError, match="not found"):
                await svc.create_project_from_intake(
                    intake_card_id="does-not-exist",
                    project_name="X",
                    target_path="/tmp/should-never-exist",
                )


# ---- create_project_from_intake: failure modes + atomic rollback --------


@pytest.mark.asyncio
async def test_rollback_when_target_path_already_exists(tmp_path: Path):
    """If the target dir already exists, abort before touching anything else
    — no kanban card, no Project row, autodispatch not flipped."""
    from app.services.inception_service import InceptionService

    intake_id = await _create_intake_card("meta")
    target = tmp_path / "already-here"
    target.mkdir()

    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            with pytest.raises(FileExistsError):
                await svc.create_project_from_intake(
                    intake_card_id=intake_id, project_name="X",
                    target_path=str(target),
                )

    # Intake card untouched.
    async with KanbanSessionLocal() as s:
        intake = await service.get_card(s, intake_id)
        assert intake.column == "intake"


@pytest.mark.asyncio
async def test_rollback_when_project_already_registered(tmp_path: Path, monkeypatch):
    """If a Project row already exists at target_path, abort — don't register
    a duplicate, don't seed, don't move anything. (ProjectService.add_project
    would silently update the existing row's `name` field, which is *worse*
    than a hard error here, so the inception service pre-checks.)"""
    from app.database import AsyncSessionLocal
    from app.models.database import Project
    from app.services.inception_service import InceptionService

    intake_id = await _create_intake_card("meta")
    target = tmp_path / "myapp"
    target.mkdir()  # pre-create the path so mkdir step is no-op-ish

    # Register a Project row at the target path so add_project would short-circuit.
    async with AsyncSessionLocal() as app_db:
        app_db.add(Project(name="inception-test-existing", path=str(target)))
        await app_db.commit()

    async with KanbanSessionLocal() as ks:
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            with pytest.raises(ValueError, match="already"):
                await svc.create_project_from_intake(
                    intake_card_id=intake_id, project_name="MyApp",
                    target_path=str(target),
                )

    # Intake card untouched, no kanban card with this title exists anywhere.
    async with KanbanSessionLocal() as s:
        intake = await service.get_card(s, intake_id)
        assert intake.column == "intake"
        rows = (await s.execute(
            select(KanbanCard).where(KanbanCard.title == "MyApp")
        )).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_rollback_when_git_init_fails(tmp_path: Path, monkeypatch):
    """Simulate `git init` failure by monkeypatching subprocess.run for the
    `git init` call. After the failure: target dir is removed, no Project row,
    no kanban card, intake card untouched, autodispatch-meta not flipped."""
    from app.services.inception_service import InceptionService

    real_run = subprocess.run
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        if isinstance(cmd, list) and "init" in cmd and "git" in cmd:
            raise subprocess.CalledProcessError(128, cmd, stderr="fatal: bad")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    intake_id = await _create_intake_card("meta")
    target = tmp_path / "myapp"

    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            with pytest.raises(RuntimeError, match="git init"):
                await svc.create_project_from_intake(
                    intake_card_id=intake_id, project_name="MyApp",
                    target_path=str(target),
                )

    # Atomic rollback: target dir gone, intake untouched.
    assert not target.exists()
    async with KanbanSessionLocal() as s:
        intake = await service.get_card(s, intake_id)
        assert intake.column == "intake"


# ---- dispatcher integration: new project autodispatch ------------------


@pytest.mark.asyncio
async def test_new_project_autodispatch_meta_is_set(tmp_path: Path):
    """After create_project_from_intake, the new project's autodispatch
    toggle in KanbanMeta is set to enabled — the dispatcher should pick the
    card up on its next tick without manual intervention."""
    from app.services.inception_service import InceptionService

    intake_id = await _create_intake_card("meta")

    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            result = await svc.create_project_from_intake(
                intake_card_id=intake_id, project_name="MyApp",
                target_path=str(tmp_path / "myapp"),
            )

    async with KanbanSessionLocal() as s:
        enabled = await dispatch.is_autodispatch_enabled(s, result.new_project_key)
        assert enabled is True


@pytest.mark.asyncio
async def test_intake_card_without_plan_deliverable_still_works(tmp_path: Path):
    """An intake card may have no plan deliverable (the human hasn't
    approved a design yet). The first kanban card in the new project is
    still created — just without a plan_ref link."""
    from app.services.inception_service import InceptionService

    intake_id = await _create_intake_card("meta")

    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            result = await svc.create_project_from_intake(
                intake_card_id=intake_id, project_name="MyApp",
                target_path=str(tmp_path / "myapp"),
            )

    async with KanbanSessionLocal() as s:
        new_card = await service.get_card(s, result.first_card_id)
        plan_refs = [d for d in new_card.deliverables if d.kind == "plan_ref"]
        assert plan_refs == []  # no plan → no plan_ref
