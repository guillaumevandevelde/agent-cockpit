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

import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, or_, select

from app.kanban import dispatch, service
from app.kanban.models import KanbanCard
from app.kanban.operations import apply_operation
from app.kanban.schemas import COLUMNS, SPEC_DOC_META_KEY
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


# Map from "route" label to (method-name, kwargs-builder). The kwargs-builder
# closes over the per-test fixtures (intake_id / project_name / target_path /
# spec_md / plan_md) and produces the dict the inception method needs. Both
# routes share `project_name` + `target_path`; the rest differs.
#
# Parametrising the rollback tests over both routes is what acceptance
# criterion #9 asked for (the new route must inherit the same atomicity
# guarantees as the intake route).
_ROUTE_KWARGS = {
    "intake": "create_project_from_intake",
    "interview": "create_project_from_interview",
}


def _build_kwargs(route: str, *, project_name: str, target_path: str,
                  intake_id: str | None, spec_md: str, plan_md: str,
                  title: str, description: str) -> dict:
    if route == "intake":
        return {
            "intake_card_id": intake_id,
            "project_name": project_name,
            "target_path": target_path,
        }
    if route == "interview":
        return {
            "project_name": project_name,
            "target_path": target_path,
            "title": title,
            "description": description,
            "spec_md": spec_md,
            "plan_md": plan_md,
        }
    raise ValueError(f"unknown route {route!r}")


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

    assert result["project_id"]  # non-empty
    assert target.exists() and target.is_dir()
    assert (target / ".git").exists()  # git init ran
    # CLAUDE.md is a sibling of .claude/, not inside it (BlueprintService
    # convention — see _write_claudemd), and .claude/ itself is seeded.
    assert (target / "CLAUDE.md").exists()  # minimal seed
    assert (target / ".claude").is_dir()
    assert result["first_card_id"]  # non-empty
    assert result["new_project_key"].startswith("slug:")  # no remote yet

    # New kanban card lives in the new project's Backlog with plan_ref.
    async with KanbanSessionLocal() as s:
        new_card = await service.get_card(s, result["first_card_id"])
        assert new_card.project_key == result["new_project_key"]
        assert new_card.column == "Backlog"
        # plan_ref is a separate deliverable that points at the intake card.
        plan_refs = [d for d in new_card.deliverables if d.kind == "plan_ref"]
        assert plan_refs, "expected a plan_ref deliverable on the new card"
        assert intake_id in plan_refs[0].ref

        # Intake card was moved to Done with a summary. `done_summary` is
        # request-time enrichment over the op-log, not an ORM column — read
        # it via enrich_done_info (the same path the API/board uses).
        intake = await service.get_card(s, intake_id)
        assert intake.column == "Done"
        summary, _ = await service.enrich_done_info(s, intake_id)
        assert summary  # non-empty


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


@pytest.mark.parametrize("route", list(_ROUTE_KWARGS))
@pytest.mark.asyncio
async def test_rollback_when_target_path_already_exists(tmp_path: Path, route: str):
    """If the target dir already exists, abort before touching anything else
    — no kanban card, no Project row, autodispatch not flipped. Covers both
    the intake route and the interview route (kaart b9e6365a, AC #9)."""
    from app.services.inception_service import InceptionService

    intake_id = await _create_intake_card("meta")
    target = tmp_path / "already-here"
    target.mkdir()

    kwargs = _build_kwargs(
        route, project_name="X", target_path=str(target),
        intake_id=intake_id,
        spec_md="# Spec\nbody", plan_md="# Plan\nbody",
        title="X", description="desc",
    )
    method_name = _ROUTE_KWARGS[route]

    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            with pytest.raises(FileExistsError):
                await getattr(svc, method_name)(**kwargs)

    # Intake card untouched (the interview route doesn't move it; intake
    # route moves it on success — both still untouched on failure).
    async with KanbanSessionLocal() as s:
        intake = await service.get_card(s, intake_id)
        assert intake.column == "intake"


@pytest.mark.parametrize("route", list(_ROUTE_KWARGS))
@pytest.mark.asyncio
async def test_rollback_when_project_already_registered(
    tmp_path: Path, monkeypatch, route: str
):
    """If a Project row already exists at target_path, abort — don't register
    a duplicate, don't seed, don't move anything. (ProjectService.add_project
    would silently update the existing row's `name` field, which is *worse*
    than a hard error here, so the inception service pre-checks.) Covers both
    routes (kaart b9e6365a, AC #9)."""
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

    kwargs = _build_kwargs(
        route, project_name="MyApp", target_path=str(target),
        intake_id=intake_id,
        spec_md="# Spec\nbody", plan_md="# Plan\nbody",
        title="MyApp", description="desc",
    )
    method_name = _ROUTE_KWARGS[route]

    async with KanbanSessionLocal() as ks:
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            with pytest.raises(ValueError, match="already"):
                await getattr(svc, method_name)(**kwargs)

    # Intake card untouched, no kanban card with this title exists anywhere.
    async with KanbanSessionLocal() as s:
        intake = await service.get_card(s, intake_id)
        assert intake.column == "intake"
        rows = (await s.execute(
            select(KanbanCard).where(KanbanCard.title == "MyApp")
        )).scalars().all()
        assert rows == []


@pytest.mark.parametrize("route", list(_ROUTE_KWARGS))
@pytest.mark.asyncio
async def test_rollback_when_git_init_fails(
    tmp_path: Path, monkeypatch, route: str
):
    """Simulate `git init` failure by monkeypatching subprocess.run for the
    `git init` call. After the failure: target dir is removed, no Project row,
    no kanban card, intake card untouched, autodispatch-meta not flipped.
    Covers both routes (kaart b9e6365a, AC #9)."""
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

    kwargs = _build_kwargs(
        route, project_name="MyApp", target_path=str(target),
        intake_id=intake_id,
        spec_md="# Spec\nbody", plan_md="# Plan\nbody",
        title="MyApp", description="desc",
    )
    method_name = _ROUTE_KWARGS[route]

    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            with pytest.raises(RuntimeError, match="git init"):
                await getattr(svc, method_name)(**kwargs)

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

    intake_id = await _create_intake_card("meta")
    target = tmp_path / "myapp"
    policy = BootstrapPolicy(
        autodispatch_default=False,        # override the intake opt-in
        license="MIT",
        copyright_holder="Acme Inc",
    )

    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            await svc.create_project_from_intake(
                intake_card_id=intake_id, project_name="MyApp",
                target_path=str(target), policy=policy,
            )

    new_project_key = resolve_project_key(str(target))

    # §1.1 — autodispatch reflects the policy value (False), not the hardcoded True.
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

    intake_id = await _create_intake_card("meta")
    target = tmp_path / "myapp"

    async with KanbanSessionLocal() as ks:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as app_db:
            svc = InceptionService(ks, app_db)
            await svc.create_project_from_intake(
                intake_card_id=intake_id, project_name="MyApp",
                target_path=str(target),
                policy=BootstrapPolicy(license=None),
            )

    assert not (target / "LICENSE").exists()
    assert (target / ".git").exists()


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
        new_card = await service.get_card(s, result["first_card_id"])
        plan_refs = [d for d in new_card.deliverables if d.kind == "plan_ref"]
        assert plan_refs == []  # no plan → no plan_ref


# ---- create_project_from_interview: cardless birth route -----------------
#
# Kaart b9e6365a… (inceptie kaartloze geboorte): the interview route lands
# spec + plan as repo files before the first commit, sets the first card's
# `metadata[SPEC_DOC_META_KEY]` to the spec path, and skips the intake-card
# move-to-Done (no intake card on this route). Every rollback test above is
# parametrised over both routes so the same atomicity guarantees apply.


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

    The interview route validates the payload *in place of* looking up an
    intake card — there's no card to fall back on, so an empty spec/plan
    would render a half-born project with no design. Refuse loudly."""
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
    are captured in the first commit. The first commit message no longer
    embeds the intake-card-id placeholder (AC #6)."""
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

    # AC #6: the first-commit message reflects the interview route
    # (no `{intake_card_id}` placeholder would survive — the intake-card
    # value would be missing). Verify the message fits the new format.
    log = subprocess.run(
        ["git", "-C", str(target), "log", "-1", "--pretty=%s"],
        capture_output=True, text=True, check=True,
    )
    msg = log.stdout.strip()
    assert "{intake_card_id}" not in msg, (
        f"interview-route commit message must not interpolate the intake id; got {msg!r}"
    )
    assert "My App" in msg or "MyApp" in msg, (
        f"interview-route commit message should name the project; got {msg!r}"
    )


@pytest.mark.asyncio
async def test_first_card_carries_spec_doc_metadata(tmp_path: Path):
    """AC #4: first card gets ``metadata[SPEC_DOC_META_KEY]`` =
    repo-relative path to the design doc, and the title/description come
    from the payload (no intake card to inherit from). AC #5: no plan_ref
    deliverable on the first card."""
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
        # AC #4 — title + description from payload, not an intake card.
        assert new_card.title == "Custom title from interview"
        assert new_card.description == "Custom description from interview."
        # AC #4 — metadata carries the spec-doc link.
        assert new_card.meta is not None
        assert new_card.meta.get(SPEC_DOC_META_KEY) == expected_spec_rel
        # AC #5 — no plan_ref deliverable on the new card.
        plan_refs = [d for d in new_card.deliverables if d.kind == "plan_ref"]
        assert plan_refs == [], "interview route must NOT wire a plan_ref"
