"""InceptionService — promotes an intake card on the meta-project to a
brand-new project on the kanban board.

Drives the inceptie-pipeline from kanban card c33b2f14 (facet A of
platform-as-app-factory; see `docs/cockpit/product-inceptie-pipeline.md` §4
optie 2). The pipeline is "kaart aanmaken → plan + kinderen → kinderen staan
in het nieuwe project" — the chicken-and-egg from §2.3 of the same doc, where
a parent card lived on the meta-project but its work belonged in a project
that didn't exist yet.

The 6-step atomic scaffold runs in a strict order, and any failure between
steps rolls back the filesystem (rm -rf the target dir) and the kanban-DB
(deletes any partial kanban card, undoes the Project row, flips autodispatch
back off, and reverts the intake-card move) so the system is never left
half-registered.

This module is *the* canonical entry point for "an idea on the meta-project
becomes a new project" — sibling kanban card 0260dbcd added the matching MCP
actie + REST endpoint on top of the same logic, and sibling kanban card
395590d landed `BlueprintService.apply()` which step 4 now delegates to.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.kanban import dispatch
from app.kanban import service as kanban_service
from app.kanban.models import KanbanCard
from app.kanban.operations import apply_operation
from app.kanban.project_key import resolve_project_key
from app.models.database import Project
from app.models.schemas import ProjectCreate
from app.services.blueprint import Blueprint, BlueprintService, BlueprintServiceError
from app.services.project_service import ProjectService

logger = logging.getLogger(__name__)


# Marker deliverable kind used to wire a child card back to its parent's plan
# in the multi-agent kanban flow (see `operations._materialize` "link_plan_ref"
# branch). Reused here so the new project's first kanban card is "the plan
# that came from the intake card" in the same canonical sense as a child card
# born from `add_plan_attachment`.
PLAN_REF_KIND = "plan_ref"


@dataclass
class InceptionResult:
    """Public result of `create_project_from_intake`. Returned as a dict by
    the REST/MCP layers after dataclass-asdict; kept as a dataclass so the
    fields are type-checked at the call site."""
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

    async def create_project_from_intake(
        self, *, intake_card_id: str, project_name: str, target_path: str,
    ) -> dict:
        """See module docstring for the 6-step contract.

        Raises:
            ValueError: card not found, or card is not in the intake column.
            FileExistsError: target_path already exists on disk.
            RuntimeError: git init failed.
        """
        target = Path(target_path).resolve()

        # ---- step 1: validate intake card ---------------------------------
        intake = await self.kanban.get(KanbanCard, intake_card_id)
        if intake is None:
            raise ValueError(f"intake card {intake_card_id} not found")
        if intake.column != "intake":
            raise ValueError(
                f"card {intake_card_id} is in column {intake.column!r}, "
                f"not 'intake'; only intake cards can be promoted"
            )

        # ---- step 1b: check no Project row already at target_path ---------
        # ProjectService.add_project silently UPDATES the existing row's
        # `name` field, which would mask the inception in the UI ("project
        # named 'inception-test-*' suddenly has the new name"). Pre-check
        # here makes that explicit.
        existing = (await self.app.execute(
            select(Project).where(Project.path == str(target))
        )).scalar_one_or_none()
        if existing is not None:
            raise ValueError(
                f"project at {target} is already registered "
                f"(name={existing.name!r}); refusing to clobber"
            )

        # ---- step 2: mkdir --------------------------------------------------
        try:
            target.mkdir(parents=False)
        except FileExistsError:
            raise FileExistsError(
                f"target path {target} already exists; refusing to clobber"
            )

        # Tracked state for rollback. Each step appends an async cleanup
        # closure so we can unwind in reverse on failure. The closures run
        # *after* both session rollbacks, so they're operating on clean
        # transactions and can use the same session objects directly.
        rollback_actions: list = []
        try:
            # Idempotent: if the meta-project was enabled before `intake`
            # was added to `COLUMNS`, the kanban_columns row isn't there yet.
            # Back-fill so the column renders on the board — without this,
            # the intake card disappears from view as soon as it lands on
            # the column. The product-side project doesn't need an intake
            # column at birth; its first kanban card lives in Backlog.
            await kanban_service.ensure_intake_column(self.kanban, intake.project_key)

            rollback_actions.append(_rmtarget_factory(target))

            # ---- step 3: git init ------------------------------------------
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

            # ---- step 4: blueprint-apply (.claude/ + CLAUDE.md seed) -------
            # Delegates to BlueprintService.apply (sibling kanban card
            # 395590d) which writes settings.json + the standard subdirs
            # atomically. A failure here unwinds via the rmtarget closure
            # registered in step 2. The `claudemd` field carries the
            # inceptie-provenance note so an operator can distinguish this
            # project from a manually-cloned repo later.
            try:
                BlueprintService(
                    Blueprint(claudemd=(
                        f"# {project_name}\n\n"
                        f"Born from an inceptie-pipeline promotion (intake "
                        f"card `{intake_card_id}`).\n"
                    )),
                ).apply(str(target))
            except BlueprintServiceError as exc:
                raise RuntimeError(
                    f"blueprint apply failed at {target}: {exc}"
                ) from exc

            # ---- step 5: ProjectService.add_project ------------------------
            # add_project commits internally; rollback below handles the
            # failure case via _delete_project (separate path because we
            # can't rollback what's already committed in this session).
            project_service = ProjectService(self.app)
            project = await project_service.add_project(ProjectCreate(
                name=project_name, path=str(target),
            ))
            rollback_actions.append(
                _delete_project_factory(project.id)
            )

            # ---- step 6: resolve new project_key + flip autodispatch -------
            # set_autodispatch only flushes — safe to rollback via the
            # kanban session's transaction.
            new_project_key = resolve_project_key(str(target))
            await dispatch.set_autodispatch(self.kanban, new_project_key, True)
            rollback_actions.append(
                _set_autodispatch_factory(new_project_key, False)
            )

            # ---- step 7: create first kanban card in new project's Backlog
            # Carry over title + description from the intake card. If the
            # intake card carries a plan deliverable, attach plan_ref so
            # the new card's first-class kanban-DB plan link points back at
            # the intake's plan (sibling kanban card 727470a will replace
            # this stop-gap with a proper kanban-DB plans table; today plans
            # live as kind=plan deliverables, so the wire-up is identical).
            first_card_id = await apply_operation(
                self.kanban, op_type="create", entity_type="card",
                project_key=new_project_key, entity_id=None,
                payload={
                    "title": intake.title,
                    "description": intake.description,
                    "column": "Backlog",
                    # Carry the same metadata so the new card inherits
                    # spec-doc / integration tags from the intake card.
                    "metadata": intake.meta,
                },
            )
            rollback_actions.append(
                _delete_card_factory(first_card_id)
            )

            # plan_ref wiring: link the new card back to the intake card
            # so `dispatch` / `mcp` can trace "this came from intake X".
            # Uses the existing `link_plan_ref` op-type so the materialised
            # shape is identical to child-cards born from `add_plan_attachment`
            # in the multi-agent flow — no new schema.
            import json
            await apply_operation(
                self.kanban, op_type="link_plan_ref", entity_type="deliverable",
                project_key=new_project_key, entity_id=first_card_id,
                payload={
                    "ref_json": json.dumps({
                        "source_card_id": intake_card_id,
                        "source_project_key": intake.project_key,
                    }),
                },
            )

            # ---- step 8: move intake card to Done with summary ------------
            await apply_operation(
                self.kanban, op_type="move", entity_type="card",
                project_key=intake.project_key, entity_id=intake_card_id,
                payload={
                    "column": "Done",
                    "rank": uuid.uuid4().hex,  # last-write-wins; matters only for ordering
                },
            )
            await apply_operation(
                self.kanban, op_type="comment", entity_type="comment",
                project_key=intake.project_key, entity_id=intake_card_id,
                payload={
                    # The `**Summary:**` prefix is the canonical done-summary
                    # marker (see `mcp_server._SUMMARY_REQUIRED_COLUMNS` and
                    # `service.enrich_done_info`). Posting in that format
                    # means the intake card surfaces `done_summary` on the
                    # board for the close-to-Done transition, mirroring what
                    # `move_card(card_id, "Done", summary=...)` does for an
                    # agent-driven card.
                    "text": (
                        f"**Summary:** Promoted to project `{project_name}` "
                        f"(project_key={new_project_key}, "
                        f"first kanban card `{first_card_id}`)"
                    ),
                },
            )

            await self.kanban.commit()

            logger.info(
                "inception: intake %s → project %s (%s) + card %s",
                intake_card_id, project_name, new_project_key, first_card_id,
            )

            return {
                "project_id": project.id,
                "new_project_key": new_project_key,
                "first_card_id": first_card_id,
            }

        except Exception:
            # Atomic rollback. The kanban session's transaction holds steps
            # 6-8 (set_autodispatch + create card + plan_ref + move intake);
            # rolling it back unwinds them. The Project row (step 5) was
            # committed by add_project, so we explicitly delete it. The
            # filesystem (steps 2-4) is unwound by rmtarget. Cleanups run
            # in REVERSE order so the state unwinds like a stack. A cleanup
            # that itself raises is logged but doesn't mask the original.
            try:
                await self.kanban.rollback()
            except Exception:
                logger.exception("inception rollback of kanban session failed")
            for cleanup in reversed(rollback_actions):
                try:
                    await cleanup(self)
                except Exception:
                    logger.exception("inception rollback cleanup failed")
            raise


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
