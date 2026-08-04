# backend/tests/test_inception.py
"""Tests for InceptionService.create_project_from_interview.

Drives the cardless inceptie-pipeline (kanban card b9e6365a…,
`docs/cockpit/kaartloze-app-inceptie-decision.md` optie 3): an interactive
interview produces spec + plan + title + description, and that bundle becomes
a brand-new project on the kanban board in one atomic transaction.

Atomicity is the load-bearing property here: a half-registered project (path
created + git init done but Project row missing, OR Project row created but
autodispatch-meta missing) would leave the kanban-DB and the on-disk
filesystem in an inconsistent state. The tests below exercise every step
boundary so a regression to "fail mid-flow and leave detritus behind" is
caught.

The card-carried `create_project_from_intake` route was removed with the
`intake` column (kanban card d0531c12…), along with its column/schema and
promote-validation tests.
"""
from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, or_, select

from app.kanban import dispatch, service
from app.kanban.models import KanbanCard
from app.kanban.schemas import SPEC_DOC_META_KEY
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


# ---- create_project_from_interview: failure modes + atomic rollback -----


@pytest.mark.asyncio
async def test_rollback_when_target_path_already_exists(tmp_path: Path):
    """If the target dir already exists, abort before touching anything else
    — no kanban card, no Project row, autodispatch not flipped."""
    from app.services.inception_service import InceptionService

    target = tmp_path / "already-here"
    target.mkdir()

    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            with pytest.raises(FileExistsError):
                await svc.create_project_from_interview(
                    project_name="X", target_path=str(target),
                    title="X", description="desc",
                    spec_md="# Spec\nbody", plan_md="# Plan\nbody",
                )

    # No kanban card landed anywhere.
    async with KanbanSessionLocal() as s:
        rows = (await s.execute(
            select(KanbanCard).where(KanbanCard.title == "X")
        )).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_rollback_when_project_already_registered(
    tmp_path: Path, monkeypatch
):
    """If a Project row already exists at target_path, abort — don't register
    a duplicate, don't seed, don't move anything. (ProjectService.add_project
    would silently update the existing row's `name` field, which is *worse*
    than a hard error here, so the inception service pre-checks.)"""
    from app.database import AsyncSessionLocal
    from app.models.database import Project
    from app.services.inception_service import InceptionService

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
                await svc.create_project_from_interview(
                    project_name="MyApp", target_path=str(target),
                    title="MyApp", description="desc",
                    spec_md="# Spec\nbody", plan_md="# Plan\nbody",
                )

    # No kanban card with this title exists anywhere.
    async with KanbanSessionLocal() as s:
        rows = (await s.execute(
            select(KanbanCard).where(KanbanCard.title == "MyApp")
        )).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_rollback_when_git_init_fails(
    tmp_path: Path, monkeypatch
):
    """Simulate `git init` failure by monkeypatching subprocess.run for the
    `git init` call. After the failure: target dir is removed, no Project row,
    no kanban card, autodispatch-meta not flipped."""
    from app.services.inception_service import InceptionService

    real_run = subprocess.run
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        if isinstance(cmd, list) and "init" in cmd and "git" in cmd:
            raise subprocess.CalledProcessError(128, cmd, stderr="fatal: bad")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    target = tmp_path / "myapp"

    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            with pytest.raises(RuntimeError, match="git init"):
                await svc.create_project_from_interview(
                    project_name="MyApp", target_path=str(target),
                    title="MyApp", description="desc",
                    spec_md="# Spec\nbody", plan_md="# Plan\nbody",
                )

    # Atomic rollback: target dir gone, no kanban card left behind.
    assert not target.exists()
    async with KanbanSessionLocal() as s:
        rows = (await s.execute(
            select(KanbanCard).where(KanbanCard.title == "MyApp")
        )).scalars().all()
        assert rows == []


# ---- dispatcher integration: new project autodispatch ------------------


@pytest.mark.asyncio
async def test_new_project_autodispatch_meta_is_set(tmp_path: Path):
    """The new project's autodispatch toggle in KanbanMeta is actually
    written — with an opted-in policy the dispatcher picks the first card up
    on its next tick without manual intervention. (The default-off case is
    covered by ``test_birth_reflects_bootstrap_policy``.)"""
    from app.services.bootstrap_policy import BootstrapPolicy
    from app.services.inception_service import InceptionService

    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            result = await svc.create_project_from_interview(
                project_name="MyApp", target_path=str(tmp_path / "myapp"),
                title="MyApp", description="desc",
                spec_md="# Spec\nbody", plan_md="# Plan\nbody",
                policy=BootstrapPolicy(autodispatch_default=True),
            )

    async with KanbanSessionLocal() as s:
        enabled = await dispatch.is_autodispatch_enabled(s, result["new_project_key"])
        assert enabled is True


# ---- BootstrapPolicy wiring --------------------------------------------


@pytest.mark.asyncio
async def test_birth_reflects_bootstrap_policy(tmp_path: Path):
    """A newly-birthed project's autodispatch + LICENSE reflect the injected
    ``BootstrapPolicy``, not ad-hoc code-path defaults (bootstrap-policy.md §1.1
    autodispatch, §1.6 MIT license, §1.5 no CI at birth, §1.3 first-commit)."""
    from app.kanban.project_key import resolve_project_key
    from app.services.bootstrap_policy import BootstrapPolicy
    from app.services.inception_service import InceptionService

    target = tmp_path / "myapp"
    policy = BootstrapPolicy(
        autodispatch_default=False,
        license="MIT",
        copyright_holder="Acme Inc",
    )

    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            await svc.create_project_from_interview(
                project_name="MyApp", target_path=str(target),
                title="MyApp", description="desc",
                spec_md="# Spec\nbody", plan_md="# Plan\nbody",
                policy=policy,
            )

    new_project_key = resolve_project_key(str(target))

    # §1.1 — autodispatch reflects the policy value (False).
    async with KanbanSessionLocal() as s:
        enabled = await dispatch.is_autodispatch_enabled(s, new_project_key)
        assert enabled is False

    # §1.6 — LICENSE written from the policy (MIT body + the policy's holder).
    license_body = (target / "LICENSE").read_text()
    assert "MIT License" in license_body
    assert "Acme Inc" in license_body

    # §1.5 — no CI copied at birth (ci_bootstrap default False).
    assert not (target / ".github").exists()

    # §1.3 — the birth tree is captured in a first commit (repo is branchable).
    head = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    assert head.returncode == 0 and head.stdout.strip()


@pytest.mark.asyncio
async def test_birth_with_license_none_writes_no_license_file(tmp_path: Path):
    """``policy.license is None`` (proprietary/internal) → no LICENSE file, but the
    project is still birthed and committed."""
    from app.services.bootstrap_policy import BootstrapPolicy
    from app.services.inception_service import InceptionService

    target = tmp_path / "myapp"

    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            await svc.create_project_from_interview(
                project_name="MyApp", target_path=str(target),
                title="MyApp", description="desc",
                spec_md="# Spec\nbody", plan_md="# Plan\nbody",
                policy=BootstrapPolicy(license=None),
            )

    assert not (target / "LICENSE").exists()
    assert (target / ".git").exists()


# ---- create_project_from_interview: cardless birth route -----------------
#
# Kaart b9e6365a… (inceptie kaartloze geboorte): the interview route lands
# spec + plan as repo files before the first commit and sets the first card's
# `metadata[SPEC_DOC_META_KEY]` to the spec path. The rollback tests above
# cover the shared atomicity guarantees.


def _slugify(name: str) -> str:
    """Mirror the service's slug derivation so the tests can compute the
    expected file paths without re-implementing the rule."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


async def _run_interview_happy_path(
    tmp_path: Path, *, project_name: str = "My App",
    title: str | None = None, description: str | None = None,
    spec_md: str | None = None, plan_md: str | None = None,
):
    """Drive a happy-path interview birth and return (target, result, today)."""
    from app.services.inception_service import InceptionService

    target = tmp_path / "myapp"
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    kwargs = {
        "project_name": project_name,
        "target_path": str(target),
        "title": title if title is not None else project_name,
        "description": description if description is not None else "An idea worth building.",
        "spec_md": spec_md if spec_md is not None else "# Spec\n\nspec body.",
        "plan_md": plan_md if plan_md is not None else "# Plan\n\nplan body.",
    }
    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            result = await svc.create_project_from_interview(**kwargs)
    return target, result, today


@pytest.mark.parametrize("label,spec_md,plan_md", [
    ("empty_spec",  "",          "# Plan\nbody"),
    ("empty_plan",  "# Spec\nbody", ""),
    ("empty_both",  "",          ""),
])
@pytest.mark.asyncio
async def test_rejects_empty_spec_or_plan(
    tmp_path: Path, label: str, spec_md: str, plan_md: str
):
    """AC #2: empty spec_md or plan_md → ValueError, no side effects.

    The payload *is* the contract — there's no card to fall back on, so an
    empty spec/plan would render a half-born project with no design. Refuse
    loudly."""
    from app.services.inception_service import InceptionService

    target = tmp_path / "myapp"

    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            with pytest.raises(ValueError, match="spec_md|plan_md|empty"):
                await svc.create_project_from_interview(
                    project_name="MyApp",
                    target_path=str(target),
                    title="MyApp",
                    description="desc",
                    spec_md=spec_md,
                    plan_md=plan_md,
                )

    # Nothing on disk.
    assert not target.exists()


@pytest.mark.asyncio
async def test_spec_and_plan_land_in_repo_and_are_committed(tmp_path: Path):
    """AC #3: spec + plan land as repo files at the dated/slugged paths and
    are captured in the first commit, and the commit message names the
    project (AC #6)."""
    target, result, today = await _run_interview_happy_path(
        tmp_path, project_name="My App",
    )

    slug = _slugify("My App")
    spec_path = target / "docs" / "specs" / f"{today}-{slug}-design.md"
    plan_path = target / "docs" / "plans" / f"{today}-{slug}-plan.md"

    assert spec_path.exists(), f"expected {spec_path} to exist"
    assert plan_path.exists(), f"expected {plan_path} to exist"
    # Body content round-trips.
    assert "spec body" in spec_path.read_text()
    assert "plan body" in plan_path.read_text()

    # The first commit must include docs/ — `git ls-files` lists tracked
    # files; both spec + plan should appear.
    ls = subprocess.run(
        ["git", "-C", str(target), "ls-files"],
        capture_output=True, text=True, check=True,
    )
    assert str(spec_path.relative_to(target)) in ls.stdout
    assert str(plan_path.relative_to(target)) in ls.stdout

    # No pending changes against HEAD (the first commit captured them all).
    status = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    )
    assert status.stdout.strip() == "", (
        f"expected clean tree after first commit, got:\n{status.stdout}"
    )

    # AC #6: the first-commit message reflects the interview route and
    # names the project.
    log = subprocess.run(
        ["git", "-C", str(target), "log", "-1", "--pretty=%s"],
        capture_output=True, text=True, check=True,
    )
    msg = log.stdout.strip()
    assert "My App" in msg or "MyApp" in msg, (
        f"interview-route commit message should name the project; got {msg!r}"
    )


@pytest.mark.asyncio
async def test_first_card_carries_spec_doc_metadata(tmp_path: Path):
    """AC #4: first card gets ``metadata[SPEC_DOC_META_KEY]`` =
    repo-relative path to the design doc, and the title/description come
    from the payload. AC #5: no plan_ref deliverable on the first card."""
    target, result, today = await _run_interview_happy_path(
        tmp_path, project_name="My App",
        title="Custom title from interview",
        description="Custom description from interview.",
    )

    slug = _slugify("My App")
    expected_spec_rel = f"docs/specs/{today}-{slug}-design.md"

    async with KanbanSessionLocal() as s:
        new_card = await service.get_card(s, result["first_card_id"])
        assert new_card is not None
        # AC #4 — title + description from the payload.
        assert new_card.title == "Custom title from interview"
        assert new_card.description == "Custom description from interview."
        # AC #4 — metadata carries the spec-doc link.
        assert new_card.meta is not None
        assert new_card.meta.get(SPEC_DOC_META_KEY) == expected_spec_rel
        # AC #5 — no plan_ref deliverable on the new card.
        plan_refs = [d for d in new_card.deliverables if d.kind == "plan_ref"]
        assert plan_refs == [], "interview route must NOT wire a plan_ref"
