"""InceptionService — promotes an idea to a brand-new project on the kanban
board via ``create_project_from_interview``: the cardless interview flow
(kanban card b9e6365a…, ``docs/cockpit/kaartloze-app-inceptie-decision.md``
optie 3). Spec + plan land as repo files before the first commit and the
first kanban card carries ``metadata[SPEC_DOC_META_KEY]`` pointing at the
design doc.

The route runs a filesystem + kanban-DB + Project-row + autodispatch-meta
atomic scaffold. Any failure between steps rolls back filesystem (rm -rf
the target dir) and kanban-DB (deletes any partial kanban card, undoes the
Project row, flips autodispatch back off). The system is never left
half-registered.

This module is *the* canonical entry point for "an idea becomes a new
project" — sibling kanban card 0260dbcd added the matching MCP actie + REST
endpoint on top of the same logic, and sibling kanban card 395590d landed
``BlueprintService.apply()`` which step 4 now delegates to.

The card-carried ``create_project_from_intake`` route was removed with the
``intake`` column (kanban card d0531c12…); the interview route is now the
only way in.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.kanban import dispatch
from app.kanban.models import KanbanCard
from app.kanban.operations import apply_operation
from app.kanban.project_key import resolve_project_key
from app.kanban.schemas import SPEC_DOC_META_KEY
from app.models.database import Project
from app.models.schemas import ProjectCreate
from app.services.blueprint import Blueprint, BlueprintService, BlueprintServiceError
from app.services.bootstrap_policy import (
    COCKPIT_DEFAULT_POLICY,
    INTERVIEW_FIRST_COMMIT_MESSAGE,
    BootstrapPolicy,
    render_license,
)
from app.services.project_service import ProjectService

logger = logging.getLogger(__name__)


@dataclass
class InceptionResult:
    """Public result of `create_project_from_interview`. Returned as a dict
    by the REST/MCP layers after dataclass-asdict; kept as a dataclass so
    the fields are type-checked at the call site."""
    project_id: int
    new_project_key: str
    first_card_id: str


class InceptionService:
    """Drives the 6-step atomic scaffold. Constructor takes the kanban session
    and the app-DB session as injected dependencies — production callers
    (REST/MCP) open them per-request, tests inject the test-DB sessions."""

    def __init__(self, kanban_session, app_session):
        self.kanban = kanban_session
        self.app = app_session

    async def create_project_from_interview(
        self, *, project_name: str, target_path: str,
        title: str, description: str,
        spec_md: str, plan_md: str,
        policy: BootstrapPolicy | None = None,
    ) -> dict:
        """Cardless inceptie-flow (kanban card b9e6365a…,
        ``docs/cockpit/kaartloze-app-inceptie-decision.md`` optie 3): an
        interactive interview produces spec + plan + title + description,
        and that bundle becomes a brand-new project on the kanban board in
        one atomic transaction.

        The scaffold is mkdir → git init → blueprint apply → LICENSE + spec
        + plan in first commit → ProjectService.add_project → autodispatch
        on → first kanban card. Three route-specific properties:

        * Step 1 validates the payload (spec_md / plan_md non-empty).
        * Spec + plan land as repo files at
          ``docs/specs/<YYYY-MM-DD>-<slug>-design.md`` and
          ``docs/plans/<YYYY-MM-DD>-<slug>-plan.md`` (slug derived from
          ``project_name``) *before* the first commit, so the commit
          captures them.
        * First kanban card carries ``metadata[SPEC_DOC_META_KEY]`` = the
          repo-relative spec path. ``title`` / ``description`` come from
          the payload.

        ``policy`` defaults to ``COCKPIT_DEFAULT_POLICY`` (autodispatch off
        — there's no human-in-the-loop on this route). The interview
        bootstrap-commit message is ``INTERVIEW_FIRST_COMMIT_MESSAGE``.

        Raises:
            ValueError: empty ``spec_md`` / ``plan_md``, or a project
                already registered at ``target_path``.
            FileExistsError: ``target_path`` already exists on disk.
            RuntimeError: git init / blueprint / first commit failed.
        """
        policy = policy or COCKPIT_DEFAULT_POLICY
        target = Path(target_path).resolve()

        # ---- step 1: validate the payload -------------------------------
        # De kaartloze route heeft geen kaart-guards — de payload *is* het
        # contract. Lege spec/plan zou een half-gebouwd project opleveren
        # met geen design en geen plan; wij wijzen dat luid af.
        if not spec_md or not spec_md.strip():
            raise ValueError(
                "spec_md is empty; the interview route requires a non-empty "
                "design document"
            )
        if not plan_md or not plan_md.strip():
            raise ValueError(
                "plan_md is empty; the interview route requires a non-empty "
                "implementation plan"
            )

        # ---- step 1b: check no Project row already at target_path ----
        # ProjectService.add_project zou de bestaande naam stiekem
        # overschrijven ("project X heet ineens anders"), dus we
        # pre-checken hier expliciet.
        existing = (await self.app.execute(
            select(Project).where(Project.path == str(target))
        )).scalar_one_or_none()
        if existing is not None:
            raise ValueError(
                f"project at {target} is already registered "
                f"(name={existing.name!r}); refusing to clobber"
            )

        # ---- step 2: mkdir ------------------------------------------------
        try:
            target.mkdir(parents=False)
        except FileExistsError:
            raise FileExistsError(
                f"target path {target} already exists; refusing to clobber"
            )

        rollback_actions: list = []
        try:
            rollback_actions.append(_rmtarget_factory(target))

            # ---- step 3: git init -----------------------------------------
            try:
                subprocess.run(
                    ["git", "init", "--initial-branch=main", str(target)],
                    capture_output=True, text=True, timeout=15, check=True,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                    FileNotFoundError) as exc:
                raise RuntimeError(
                    f"git init failed at {target}: {exc}"
                ) from exc

            # ---- step 4: blueprint-apply (.claude/ + CLAUDE.md seed) ------
            # De CLAUDE.md note draagt de inceptie-provenance zodat een
            # operator dit project later van een handmatige clone kan
            # onderscheiden.
            try:
                BlueprintService(
                    Blueprint(claudemd=(
                        f"# {project_name}\n\n"
                        f"Born from an interactive interview "
                        f"(inceptie-pipeline, cardless route).\n"
                    )),
                ).apply(str(target))
            except BlueprintServiceError as exc:
                raise RuntimeError(
                    f"blueprint apply failed at {target}: {exc}"
                ) from exc

            # ---- step 4b: LICENSE + spec + plan + first commit ----------
            # §1.6: write the policy's LICENSE.
            # Spec + plan: schrijf ze als repo-files zodat de eerste commit
            # ze oppakt via ``git add .``. De slug is afgeleid van
            # ``project_name``; de datum is UTC vandaag.
            self._write_license(target, policy, project_name)
            spec_rel_path, plan_rel_path = self._write_spec_and_plan(
                target, project_name=project_name,
                spec_md=spec_md, plan_md=plan_md,
            )
            if policy.first_commit_content == "template":
                first_commit_message = INTERVIEW_FIRST_COMMIT_MESSAGE.format(
                    project_name=project_name,
                )
                self._first_commit(target, first_commit_message)

            # ---- step 5: ProjectService.add_project ------------------------
            project_service = ProjectService(self.app)
            project = await project_service.add_project(ProjectCreate(
                name=project_name, path=str(target),
            ))
            rollback_actions.append(
                _delete_project_factory(project.id)
            )

            # ---- step 6: resolve new project_key + flip autodispatch ------
            new_project_key = resolve_project_key(str(target))
            await dispatch.set_autodispatch(
                self.kanban, new_project_key, policy.autodispatch_default
            )
            rollback_actions.append(
                _set_autodispatch_factory(new_project_key, False)
            )

            # ---- step 7: create first kanban card with spec_doc link -----
            # ``title`` / ``description`` komen uit de payload;
            # ``metadata[SPEC_DOC_META_KEY]`` wijst naar het repo-relatieve
            # pad van het design-doc zodat spec-driven-development Fase 2 de
            # card aan de spec kan koppelen. Geen ``plan_ref`` deliverable —
            # traceability loopt via ``spec_doc`` (zie
            # ``docs/cockpit/kaartloze-app-inceptie-decision.md`` optie 3).
            first_card_id = await apply_operation(
                self.kanban, op_type="create", entity_type="card",
                project_key=new_project_key, entity_id=None,
                payload={
                    "title": title,
                    "description": description,
                    "column": "Backlog",
                    "metadata": {SPEC_DOC_META_KEY: spec_rel_path},
                },
            )
            rollback_actions.append(
                _delete_card_factory(first_card_id)
            )

            # De activiteit-feed van deze nieuwe kaart is de canonieke
            # "wat gebeurde er"-bron.

            await self.kanban.commit()

            logger.info(
                "inception: interview → project %s (%s) + card %s (spec_doc=%s, plan=%s)",
                project_name, new_project_key, first_card_id,
                spec_rel_path, plan_rel_path,
            )

            return asdict(InceptionResult(
                project_id=project.id,
                new_project_key=new_project_key,
                first_card_id=first_card_id,
            ))

        except Exception:
            # Atomic rollback. De kanban session's transaction houdt stappen
            # 6-7 (set_autodispatch + create first card); rollback unwindt
            # ze. De Project row is door add_project gecommit;
            # ``_delete_project_factory`` ruimt 'm. Filesystem stappen 2-4b
            # zijn door rmtarget gedekt.
            try:
                await self.kanban.rollback()
            except Exception:
                logger.exception("inception-from-interview rollback of kanban session failed")
            for cleanup in reversed(rollback_actions):
                try:
                    await cleanup(self)
                except Exception:
                    logger.exception("inception-from-interview rollback cleanup failed")
            raise

    # ------------------------------------------------------------------
    # BootstrapPolicy step helpers (LICENSE + first commit)
    # ------------------------------------------------------------------

    def _write_license(
        self, target: Path, policy: BootstrapPolicy, project_name: str
    ) -> None:
        """Write ``LICENSE`` from ``policy`` (§1.6), or nothing when disabled."""
        holder = policy.copyright_holder or self._git_user_name() or project_name
        body = render_license(policy, holder=holder, year=datetime.now(UTC).year)
        if body is not None:
            (target / "LICENSE").write_text(body)

    def _write_spec_and_plan(
        self, target: Path, *, project_name: str,
        spec_md: str, plan_md: str,
    ) -> tuple[str, str]:
        """Write the interview-route spec + plan into the repo before the
        first commit.

        Files land at ``docs/specs/<YYYY-MM-DD>-<slug>-design.md`` and
        ``docs/plans/<YYYY-MM-DD>-<slug>-plan.md`` where ``<slug>`` is the
        ``project_name`` lower-cased and slug-ified (any non-alphanumeric
        run collapses to a single dash; leading/trailing dashes stripped).

        Returns the *repo-relative* paths so the caller can store the spec
        path in the first card's ``metadata[SPEC_DOC_META_KEY]`` without
        re-deriving the slug.
        """
        slug = _slugify(project_name)
        if not slug:
            # Edge case: project_name stripped to empty by the slug rule
            # (e.g. "!!!" → ""). Fall back to a literal token so the path
            # is well-formed and a follow-up operator can rename the file.
            slug = "project"
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        spec_rel = f"docs/specs/{today}-{slug}-design.md"
        plan_rel = f"docs/plans/{today}-{slug}-plan.md"
        (target / spec_rel).parent.mkdir(parents=True, exist_ok=True)
        (target / plan_rel).parent.mkdir(parents=True, exist_ok=True)
        (target / spec_rel).write_text(spec_md)
        (target / plan_rel).write_text(plan_md)
        return spec_rel, plan_rel

    def _first_commit(
        self, target: Path, message: str,
    ) -> None:
        """Configure a local git identity and capture the birth tree in one commit.

        ``message`` is the fully-formatted commit message (the interview
        route formats ``INTERVIEW_FIRST_COMMIT_MESSAGE``). The birthed repo
        has no committer
        configured (the ad-hoc ``git init`` in step 3 does not touch
        ``--global``), so we pin a per-repo dummy identity — never the user's
        machine-wide config — just for the bootstrap commit.
        """
        try:
            subprocess.run(
                ["git", "-C", str(target), "config", "user.name", "Repo Bootstrap"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            subprocess.run(
                ["git", "-C", str(target), "config", "user.email",
                 "repo-bootstrap@localhost"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            subprocess.run(
                ["git", "-C", str(target), "add", "."],
                capture_output=True, text=True, timeout=10, check=True,
            )
            subprocess.run(
                ["git", "-C", str(target), "commit", "-m", message],
                capture_output=True, text=True, timeout=15, check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                FileNotFoundError) as exc:
            raise RuntimeError(
                f"first commit failed at {target}: {exc}"
            ) from exc

    @staticmethod
    def _git_user_name() -> str | None:
        """Best-effort ``git config user.name`` for the LICENSE copyright holder."""
        try:
            result = subprocess.run(
                ["git", "config", "user.name"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        name = result.stdout.strip()
        return name or None


# ---- rollback helpers ------------------------------------------------------


def _slugify(name: str) -> str:
    """Lower-case + collapse non-alphanumeric runs to a single dash + strip.

    Used to derive the spec/plan file slug from ``project_name``. Mirrors
    the convention in ``docs/cockpit/kaartloze-app-inceptie-decision.md``
    (optie 3) — same shape as the dispatch-side ``resolve_project_key``
    fallback (``slug:<token>``).
    """
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ---- rollback helpers ------------------------------------------------------
#
# Each helper is a factory returning an async cleanup closure that takes the
# InceptionService instance (so the cleanup has access to the rolled-back
# sessions). Factories capture the per-step parameters (path / id / project_key)
# so the closures themselves stay small.


def _rmtarget_factory(target: Path):
    async def _cleanup(self: InceptionService) -> None:
        # Filesystem op — sync, no session needed.
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
    return _cleanup


def _delete_project_factory(project_id: int):
    async def _cleanup(self: InceptionService) -> None:
        proj = await self.app.get(Project, project_id)
        if proj is not None:
            await self.app.delete(proj)
            await self.app.commit()
    return _cleanup


def _set_autodispatch_factory(project_key: str, enabled: bool):
    async def _cleanup(self: InceptionService) -> None:
        await dispatch.set_autodispatch(self.kanban, project_key, enabled)
        # Already flushed by set_autodispatch; commit only when changing
        # the on-disk-visible state during rollback.
        await self.kanban.commit()
    return _cleanup


def _delete_card_factory(card_id: str):
    async def _cleanup(self: InceptionService) -> None:
        card = await self.kanban.get(KanbanCard, card_id)
        if card is not None:
            # ORM relationship cascades to deliverables (FK on
            # kanban_deliverables.card_id + relationship cascade).
            await self.kanban.delete(card)
            await self.kanban.commit()
    return _cleanup
