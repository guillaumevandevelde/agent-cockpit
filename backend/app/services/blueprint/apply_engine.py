"""BlueprintApplyEngine — orchestrate a `Blueprint` into a project's
`.claude/` via existing per-project CRUD services.

The engine is the "motor" facet B (kanban card
`b0d44c8d226e43f7b286f8712f0a87d6`) that facet A's ``BlueprintService.apply``
ultimately delegates to. Where the existing implementation in
``app.services.blueprint.BlueprintService`` writes files itself, this
engine takes the same end-state and achieves it through the project's
existing CRUD primitives:

- ``ConfigService.update_settings(scope="project", project_path=<staging>, ...)``
  for ``.claude/settings.json``.
- ``AgentService.create_agent(agent=<AgentCreate>, project_path=<staging>)``
  for each project-scoped agent.
- ``SkillsRegistryService.install_skill(source=..., skill_names=[...],
  global_install=False, project_path=<staging>)`` for each skill.
- ``CommandService.create_command(command=<SlashCommandCreate>,
  project_path=<staging>)`` for each command.

Two artefacts lack a dedicated CRUD service in this repo (statusline,
``CLAUDE.md``) and are written directly into staging.

Atomicity / idempotency / rollback
----------------------------------

Every CRUD call is given a **staging** path
``<project>/.claude.staging-<uuid>/`` as its ``project_path``, so writes
land under staging rather than the project's real ``.claude/``. Once
every declared field is staged successfully the engine promotes each
staged file into the project's ``.claude/`` (and ``CLAUDE.md`` next to
it) at the file level — see ``_commit`` for why the engine doesn't
replace the whole ``.claude/`` tree in a single ``rename``. A failure
halfway through ``rm -rf``'s the staging tree; nothing in
``<project>/.claude/`` was ever touched.

Idempotency is enforced per item: before each write the engine inspects
the **final** target path. Identical content is a no-op (recorded as
``skipped`` with reason ``exists_with_identical_content``); differing
content blocks the write unless ``force=True`` is passed on the engine
call.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import Blueprint

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


@dataclass
class BlueprintAuditSkipped:
    """One entry in ``AuditResult.skipped`` — a target that was left untouched.

    ``target`` is the project-relative path (e.g.
    ``.claude/settings.json``, ``CLAUDE.md``). ``reason`` is a stable
    tag (``exists_with_identical_content`` vs.
    ``exists_with_different_content``), never a free-form string.
    """

    target: str
    reason: str


@dataclass
class BlueprintAuditError:
    """One entry in ``AuditResult.errors`` — an item the engine refused
    to materialise due to bad blueprint input or a failed CRUD call.
    """

    target: str
    message: str


@dataclass
class AuditResult:
    """Return value of :meth:`BlueprintApplyEngine.apply`.

    Three lists are populated:

    - ``written_files`` — project-relative paths of files the engine
      successfully staged and committed.
    - ``skipped`` — items the engine declined to overwrite (per-item
      idempotency, recorded with the reason tag).
    - ``errors`` — items the engine refused to materialise due to
      bad blueprint input or a failed CRUD call.

    A blueprint that applies cleanly has empty ``skipped`` and
    ``errors`` lists.
    """

    blueprint_name: str
    project_path: str
    written_files: list[str] = field(default_factory=list)
    skipped: list[BlueprintAuditSkipped] = field(default_factory=list)
    errors: list[BlueprintAuditError] = field(default_factory=list)


class BlueprintApplyError(Exception):
    """Raised when a write step fails partway through apply.

    The staging directory is removed before this exception propagates,
    so no half-written ``.claude/`` ever lands on the project.
    ``stage`` is the name of the apply step that triggered the failure
    (``"settings"``, ``"agents"``, ``"skills"``, ``"commands"``,
    ``"statusline"``, ``"claudemd"``, ``"commit"``); ``cause`` is the
    underlying exception.
    """

    def __init__(self, stage: str, cause: Exception):
        super().__init__(f"blueprint apply failed at stage {stage!r}: {cause}")
        self.stage = stage
        self.cause = cause


# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_SETTINGS_REL_PATH = ".claude/settings.json"
_STATUSLINE_REL_PATH = ".claude/statusline"
_CLAUDEMD_REL_PATH = "CLAUDE.md"


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class BlueprintApplyEngine:
    """Stateless orchestrator — instantiate once per apply call.

    The engine has no mutable state of its own; a single instance is
    safe to reuse across applies to different projects. All settings
    live on the :class:`Blueprint` argument.
    """

    def apply(
        self,
        project_path: str,
        blueprint: Blueprint,
        *,
        force: bool = False,
    ) -> AuditResult:
        """Materialise ``blueprint`` into ``project_path``'s ``.claude/``.

        Steps (each routed through staging; on any failure the staging
        tree is ``rm -rf``'d so the project never sees a half-written
        ``.claude/``):

        1. Create ``<project>/.claude.staging-<uuid>/`` and prepare its
           internal ``.claude/`` subdir.
        2. ``ConfigService.update_settings(scope="project",
           settings=<dict>, project_path=<staging>)``.
        3. For each ``BlueprintAgent``: ``AgentService.create_agent(
           <AgentCreate(scope="project")>, project_path=<staging>)``.
        4. For each ``BlueprintSkill``: ``SkillsRegistryService.install_skill(
           source=<source>, skill_names=[<name>], global_install=False,
           project_path=<staging>)``.
        5. For each ``BlueprintCommand``: ``CommandService.create_command(
           <SlashCommandCreate(scope="project")>, project_path=<staging>)``.
        6. Write statusline body to ``<staging>/.claude/statusline``.
        7. Write ``claudemd`` body to ``<staging>/CLAUDE.md``.
        8. Validate all writes (settings parses as JSON, expected files
           are on disk).
        9. Promote staging to project: rename ``<staging>/.claude`` →
           ``<project>/.claude``; move ``<staging>/CLAUDE.md`` →
           ``<project>/CLAUDE.md``.

        Idempotency: per-item content check against the **final** target
        before each write; identical content is a no-op recorded in
        ``AuditResult.skipped`` with ``reason="exists_with_identical_content"``;
        conflicting content blocks the write with ``reason="exists_with_different_content"``
        unless ``force=True`` is passed.

        Returns:
            ``AuditResult`` describing what was written, skipped, and
            what errors the engine refused to produce.

        Raises:
            BlueprintApplyError: a write step raised; the staging tree
                is removed before this propagates.
        """
        project = Path(project_path).expanduser().resolve()
        audit = AuditResult(
            blueprint_name=blueprint.name or "<unnamed>",
            project_path=str(project),
        )

        # Scrub any prior-crash staging dirs (``.claude.staging-*``)
        # under the project root. A half-finished apply from a prior
        # run shouldn't influence a fresh one — its contents may be
        # inconsistent (e.g. settings written but agents not).
        for stale in project.glob(".claude.staging-*"):
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)

        # Per-call staging directory: a uuid makes concurrent applies on
        # the same project (rare, but possible in tests and CI) safe.
        # The staging dir lives INSIDE the project (the card specifies
        # ``<project_path>/.claude.staging-<uuid>/``) so cleanup on
        # rollback doesn't leave orphans at the project boundary.
        staging = project / f".claude.staging-{uuid.uuid4().hex}"
        staging.mkdir(parents=False, exist_ok=False)
        staging_claude = staging / ".claude"
        staging_claude.mkdir(parents=False, exist_ok=False)

        try:
            self._run_stage("settings", lambda:
                self._apply_settings(project, staging, blueprint, audit, force=force))
            self._run_stage("agents", lambda:
                self._apply_agents(project, staging, blueprint, audit, force=force))
            self._run_stage("skills", lambda:
                self._apply_skills(staging, blueprint, audit))
            self._run_stage("commands", lambda:
                self._apply_commands(staging, blueprint, audit, force=force))
            self._run_stage("statusline", lambda:
                self._apply_statusline(project, staging, blueprint, audit, force=force))
            self._run_stage("claudemd", lambda:
                self._apply_claudemd(project, staging, blueprint, audit, force=force))
            if audit.written_files:
                self._run_stage("validate", lambda:
                    self._validate(staging))
                self._run_stage("commit", lambda:
                    self._commit(project, staging))
            else:
                # Nothing landed in staging — every per-item check decided
                # to skip, so we tear staging down without touching the
                # project's `.claude/`.
                shutil.rmtree(staging, ignore_errors=True)
        except BlueprintApplyError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise BlueprintApplyError(stage="apply", cause=exc) from exc

        return audit

    # -- step helpers -----------------------------------------------------

    def _apply_settings(
        self,
        project: Path,
        staging: Path,
        blueprint: Blueprint,
        audit: AuditResult,
        *,
        force: bool,
    ) -> None:
        """Route ``.claude/settings.json`` through ``ConfigService.update_settings``.

        Content equality is decided against the **final** path
        (``<project>/.claude/settings.json``): identical content is a
        no-op (no CRUD call); different content blocks the write
        unless ``force`` is set.
        """
        settings_dict = blueprint.settings.to_dict()
        target_final = project / ".claude" / "settings.json"

        if not self._can_write(target_final, settings_dict, audit, force=force,
                               target_label=_SETTINGS_REL_PATH):
            return

        # Lazy import: keep the engine importable even when CRUD
        # modules aren't available (e.g. packaging smoke tests).
        from ..config_service import ConfigService

        ConfigService().update_settings(
            scope="project",
            settings=settings_dict,
            project_path=str(staging),
        )
        # Double-check — the CRUD may have declined silently. If the
        # staged file isn't present, treat it as a write failure.
        if not (staging / ".claude" / "settings.json").is_file():
            raise BlueprintApplyError(
                stage="settings",
                cause=RuntimeError(
                    "ConfigService.update_settings did not produce "
                    ".claude/settings.json in staging"
                ),
            )
        audit.written_files.append(_SETTINGS_REL_PATH)

    def _apply_agents(
        self,
        project: Path,
        staging: Path,
        blueprint: Blueprint,
        audit: AuditResult,
        *,
        force: bool,
    ) -> None:
        """Route each :class:`BlueprintAgent` through ``AgentService.create_agent``.

        The blueprint-level shape (``name``, ``model_default``,
        ``tools``) is translated into the CRUD-level ``AgentCreate``
        shape (mandatory ``scope="project"``, ``prompt`` stub body).
        """
        if not blueprint.agents:
            return

        # Lazy imports: keep the module importable in isolation.
        from ...models.schemas import AgentCreate
        from ..agent_service import AgentService

        for agent in blueprint.agents:
            target_rel = f".claude/agents/{agent.name}.md"
            target_final = project / ".claude" / "agents" / f"{agent.name}.md"
            stub_body = (
                f"# {agent.name}\n\n"
                "Add agent instructions for Claude Code here.\n"
            )
            if not self._can_write(
                target_final, stub_body, audit, force=force,
                target_label=target_rel,
            ):
                continue

            agent_create = AgentCreate(
                name=agent.name,
                scope="project",
                description=None,
                tools=agent.tools or None,
                model=agent.model_default,
                prompt=stub_body,
            )
            AgentService.create_agent(
                agent_create,
                project_path=str(staging),
            )
            audit.written_files.append(target_rel)

    def _apply_skills(
        self,
        staging: Path,
        blueprint: Blueprint,
        audit: AuditResult,
    ) -> None:
        """Route each :class:`BlueprintSkill` through
        ``SkillsRegistryService.install_skill``.

        The MVP does not gate skills on idempotency: ``install_skill``
        itself handles re-installs and emits its own success/failure
        logs, and the registry lives outside the project tree. The
        engine records the skill name in ``audit`` (with a
        ``.claude/skills/<name>`` prefix) only when ``install_skill``
        reports success.
        """
        if not blueprint.skills:
            return

        from ..skills_registry_service import SkillsRegistryService

        for skill in blueprint.skills:
            result = SkillsRegistryService.install_skill(
                source=skill.source,
                skill_names=[skill.name],
                global_install=False,
                project_path=str(staging),
            )
            success = bool(result.get("success"))
            if success:
                audit.written_files.append(
                    f".claude/skills/{skill.name}/SKILL.md"
                )
            else:
                audit.errors.append(
                    BlueprintAuditError(
                        target=f".claude/skills/{skill.name}",
                        message=result.get("message", "install_skill failed"),
                    )
                )

    def _apply_commands(
        self,
        staging: Path,
        blueprint: Blueprint,
        audit: AuditResult,
        *,
        force: bool,
    ) -> None:
        """Route each blueprint command through ``CommandService.create_command``.

        ``BlueprintCommand`` (if present) is a thin wrapper around the
        standard ``SlashCommandCreate`` shape; missing fields fall back
        to safe defaults so an incomplete CRUD input doesn't reject the
        blueprint wholesale.
        """
        commands = getattr(blueprint, "commands", None) or []
        if not commands:
            return

        from ...models.schemas import SlashCommandCreate
        from ..command_service import CommandService

        for cmd in commands:
            target_rel = f".claude/commands/{cmd.name}.md"
            target_staged = staging / ".claude" / "commands" / f"{cmd.name}.md"
            stub_body = getattr(cmd, "content", None) or (
                f"# {cmd.name}\n\n"
                "Add slash-command instructions for Claude Code here.\n"
            )
            if not self._can_write(
                target_staged, stub_body, audit, force=force,
                target_label=target_rel,
            ):
                continue

            create = SlashCommandCreate(
                name=cmd.name,
                scope="project",
                description=getattr(cmd, "description", None),
                allowed_tools=getattr(cmd, "allowed_tools", None),
                content=stub_body,
            )
            CommandService.create_command(
                create,
                project_path=str(staging),
            )
            audit.written_files.append(target_rel)

    def _apply_statusline(
        self,
        project: Path,
        staging: Path,
        blueprint: Blueprint,
        audit: AuditResult,
        *,
        force: bool,
    ) -> None:
        """Write ``.claude/statusline`` directly.

        No CRUD service exists for statusline in this repo (the
        ``statusline_service`` module focuses on the runtime command,
        not the project artefact), so the engine writes the file
        directly into staging.
        """
        if not blueprint.statusline:
            return

        target_final = project / ".claude" / "statusline"
        target_staged = staging / ".claude" / "statusline"

        if not self._can_write(
            target_final, blueprint.statusline, audit, force=force,
            target_label=_STATUSLINE_REL_PATH,
        ):
            return

        target_staged.parent.mkdir(parents=True, exist_ok=True)
        target_staged.write_text(blueprint.statusline)
        audit.written_files.append(_STATUSLINE_REL_PATH)

    def _apply_claudemd(
        self,
        project: Path,
        staging: Path,
        blueprint: Blueprint,
        audit: AuditResult,
        *,
        force: bool,
    ) -> None:
        """Write ``CLAUDE.md`` (sibling of ``.claude/``).

        ``CLAUDE.md`` is a Claude Code convention — a sibling of the
        ``.claude/`` folder, not a child of it. The engine writes it
        into the *root* of the staging directory so ``_commit`` can
        promote it next to ``<project>/.claude/``.
        """
        if not blueprint.claudemd:
            return

        target_final = project / "CLAUDE.md"
        target_staged = staging / "CLAUDE.md"

        if not self._can_write(
            target_final, blueprint.claudemd, audit, force=force,
            target_label=_CLAUDEMD_REL_PATH,
        ):
            return

        target_staged.write_text(blueprint.claudemd)
        audit.written_files.append(_CLAUDEMD_REL_PATH)

    # -- commit / validation --------------------------------------------

    def _validate(self, staging: Path) -> None:
        """Best-effort sanity check on the staged tree.

        Mirrors the card's acceptance criterion (validate every write)
        without claiming guarantees we can't make: we re-parse
        ``settings.json`` (so a JSON corruption surfaces here as a
        loud error instead of a committed-bad-file). Anything deeper
        would couple us to the CRUD service internals.
        """
        settings = staging / ".claude" / "settings.json"
        if settings.is_file():
            try:
                json.loads(settings.read_text())
            except json.JSONDecodeError as exc:
                raise BlueprintApplyError(
                    stage="validate-settings",
                    cause=exc,
                ) from exc

    def _commit(
        self,
        project: Path,
        staging: Path,
    ) -> None:
        """Promote ``staging`` onto ``project`` at the file level.

        The card's per-item idempotency (``exists_with_different_content``
        → don't overwrite unless ``force=True``) rules out a wholesale
        atomic-replace of ``<project>/.claude/``: an existing
        ``.claude/`` with user-edited files we deliberately skipped
        would be clobbered. Instead, every staged file is promoted
        individually — new files land inside ``<project>/.claude/`` via
        ``mkdir + rename``; pre-existing files (only ever reached with
        ``force=True`` on this path) are unlinked and replaced
        atomically. ``CLAUDE.md`` moves next to ``.claude/`` the same
        way.

        Promoting individual files costs us the all-or-nothing
        atomicity of a single ``rename`` of ``<staging>/.claude``. The
        compensating control is the per-item idempotency check *plus*
        the upstream rollback behaviour (a halfway-failing apply
        ``rm -rf``'s staging before it ever touched
        ``<project>/.claude/``).
        """
        staging_claude = staging / ".claude"
        if staging_claude.is_dir():
            for staged_path in sorted(staging_claude.rglob("*")):
                if not staged_path.is_file():
                    continue
                rel = staged_path.relative_to(staging_claude)
                final = project / ".claude" / rel
                final.parent.mkdir(parents=True, exist_ok=True)
                if final.exists():
                    final.unlink()
                os.rename(staged_path, final)
            # Tidy up the now-empty staging .claude/ tree.
            shutil.rmtree(staging_claude, ignore_errors=True)

        claudemd_staged = staging / "CLAUDE.md"
        claudemd_final = project / "CLAUDE.md"
        if claudemd_staged.exists():
            if claudemd_final.exists():
                claudemd_final.unlink()
            os.rename(claudemd_staged, claudemd_final)

        shutil.rmtree(staging, ignore_errors=True)

    # -- per-item idempotency --------------------------------------------

    def _run_stage(self, stage: str, fn):
        """Run ``fn`` and convert any non-typed exception into a
        :class:`BlueprintApplyError` tagged with ``stage``.

        The orchestration loop above uses this so each step's
        exception is wrapped with the *name of the step that failed*
        (``"settings"``, ``"agents"``, ``"skills"``, …) rather than the
        generic ``"apply"`` tag a single top-level try/except would
        produce.
        """
        try:
            fn()
        except BlueprintApplyError:
            raise
        except Exception as exc:
            logger.exception("blueprint apply failed at stage %r", stage)
            raise BlueprintApplyError(stage=stage, cause=exc) from exc

    def _can_write(
        self,
        target_final: Path,
        proposed_body: str | dict[str, Any],
        audit: AuditResult,
        *,
        force: bool,
        target_label: str,
    ) -> bool:
        """Decide whether a write may proceed.

        Reads ``target_final`` from its **final** (committed) location
        — not the staging path, so the per-item check sees the
        operator-edited artefact on the project. Returns True iff the
        engine should attempt the write. Records the skip in
        ``audit`` (with ``target_label`` as the project-relative path)
        so callers see what was held back.
        """
        if not target_final.exists():
            return True

        existing_text = target_final.read_text()
        if isinstance(proposed_body, dict):
            try:
                existing_text = json.dumps(
                    json.loads(existing_text), indent=2, sort_keys=True,
                )
            except json.JSONDecodeError:
                # Existing file isn't JSON; fall back to byte-level.
                pass
            proposed_text = json.dumps(
                proposed_body, indent=2, sort_keys=True,
            )
        else:
            proposed_text = proposed_body

        if existing_text == proposed_text:
            audit.skipped.append(BlueprintAuditSkipped(
                target=target_label,
                reason="exists_with_identical_content",
            ))
            logger.info(
                "blueprint apply: skipping %s — identical content",
                target_final,
            )
            return False

        if not force:
            audit.skipped.append(BlueprintAuditSkipped(
                target=target_label,
                reason="exists_with_different_content",
            ))
            logger.info(
                "blueprint apply: skipping %s — different content and "
                "force=False",
                target_final,
            )
            return False

        return True
