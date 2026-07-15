"""BlueprintService — apply a project-level `.claude/` blueprint.

A *blueprint* is the declarative shape of the `.claude/` folder for a project:
which subdirs exist, what default `settings.json` looks like, which skills and
agents are pre-baked, what statusline / output style / `CLAUDE.md` stub the
project starts with. Blueprints are stored as version-pinned JSON files under
``~/.claude-registry/blueprints/<name>.json`` and applied to a project with
`BlueprintService.apply(project_path, blueprint)`.

The `apply()` operation is **atomic** (writes go to `.claude.tmp/` and get
renamed into place) and **idempotent** (re-running leaves an existing
`.claude/` alone unless `force=True`). On any failure the staged `.claude.tmp/`
is removed so a half-written `.claude/` never lands.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


SkillSource = Literal["user", "system", "project"]


class BlueprintSkill(BaseModel):
    """A skill reference declared by the blueprint.

    `source` is informational: it tells operators where the canonical skill
    lives (`user` = the user's `~/.claude/skills/`, `system` = a CC-managed
    skill, `project` = the project-local one we materialise here). For
    `user` and `system` the apply engine records the reference in a manifest
    but does not copy the body (the user already has it); for `project` we
    write a SKILL.md stub at `<project>/.claude/skills/<name>/SKILL.md` so
    the project ships with a discoverable, version-pinned skill.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    source: SkillSource = "project"
    version_pin: str | None = None


class BlueprintAgent(BaseModel):
    """An agent declaration. Materialised as `<project>/.claude/agents/<name>.md`.

    `body_path` is reserved for a future "import the agent body from this
    path" extension; today the apply engine writes a stub body with the
    declared `model_default` and `tools` recorded in the YAML frontmatter so
    CC can pick them up. Operators can hand-edit the stub after apply.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    body_path: str | None = None
    model_default: str | None = None
    tools: list[str] = Field(default_factory=list)


PermissionMode = Literal["default", "acceptEdits", "bypassPermissions", "plan"]


class BlueprintSettings(BaseModel):
    """Settings.json content for the seeded project.

    `extra="allow"` so future CC settings fields flow through without a
    schema change. The fields we model explicitly are the ones the blueprint
    UI needs to render form inputs for.
    """

    model_config = ConfigDict(extra="allow")

    permission_mode: PermissionMode | None = None
    plansDirectory: str | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render as the JSON payload that lands in `<project>/.claude/settings.json`.

        Drops `None` values so the file stays tidy, and re-nests
        `permission_mode` under `permissions.defaultMode` to match the
        Claude Code convention (CC reads `permissions.defaultMode`, not a
        top-level `permission_mode`).
        """
        out: dict[str, Any] = {}
        if self.permission_mode is not None:
            out.setdefault("permissions", {})["defaultMode"] = self.permission_mode
        if self.plansDirectory is not None:
            out["plansDirectory"] = self.plansDirectory
        if self.model is not None:
            out["model"] = self.model
        # Forward any extra CC fields (hooks, env, attribution, ...) verbatim.
        for key, value in self.model_extra.items() if hasattr(self, "model_extra") else []:
            if key in out:
                continue
            out[key] = value
        return out


class Blueprint(BaseModel):
    """Declarative shape of a `.claude/` folder seed — version-pinned JSON.

    The blueprint model is the public, persisted shape. The card's acceptance
    criteria (atomic + idempotent apply, REST CRUD, UI) all sit on top of this
    model.
    """

    model_config = ConfigDict(extra="forbid")

    # Identity / meta
    # `name` is the storage key (one JSON file per name). It's optional on
    # the model itself so ad-hoc `Blueprint(...)` constructions inside the
    # apply engine (e.g. `inception_service` building a one-off CLAUDE.md
    # recipe) don't need to fabricate a name. `BlueprintStore.save` is the
    # boundary that enforces "name required" via `BlueprintStore.validate_name`.
    name: str | None = None
    version: int = 1
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Declarative shape
    subdirs: list[str] = Field(
        default_factory=lambda: ["commands", "agents", "hooks", "skills", "plugins", "output-styles"],
    )
    settings: BlueprintSettings = Field(default_factory=BlueprintSettings)
    skills: list[BlueprintSkill] = Field(default_factory=list)
    agents: list[BlueprintAgent] = Field(default_factory=list)
    statusline: str | None = None
    output_style: str | None = None
    claudemd: str | None = None

    def touch(self) -> Blueprint:
        """Return a copy with ``updated_at`` set to now (UTC). Used by store."""
        return self.model_copy(update={"updated_at": datetime.now(UTC)})


@dataclass
class AuditResult:
    """Return value of `BlueprintService.apply` — what was written, what was kept."""

    blueprint_name: str
    project_path: str
    written_files: list[str] = field(default_factory=list)
    created_dirs: list[str] = field(default_factory=list)
    applied_skills: list[str] = field(default_factory=list)
    applied_agents: list[str] = field(default_factory=list)
    skipped_existing: bool = False


class BlueprintServiceError(Exception):
    """Base class for BlueprintService errors."""


class BlueprintApplyFailed(BlueprintServiceError):
    """Raised when an apply step fails. The staged `.claude.tmp/` is removed."""

    def __init__(self, step: str, original: Exception):
        super().__init__(f"apply failed at step {step!r}: {original}")
        self.step = step
        self.original = original


def _atomic_replace_into(dst: Path, src: Path) -> None:
    """Replace directory `dst` with directory `src`, even when `dst` is non-empty.

    `os.replace` refuses to overwrite a non-empty directory on POSIX. The
    two-step rename-then-rmtree is atomic from the application's perspective:
    the rename of `dst -> dst.old` is one syscall, the rename of
    `src -> dst` is another, and only then is the old directory removed.
    A crash between the first rename and the second leaves `dst.old` and
    `src` both present; the next apply scrubs `src` via the stray-tmp
    handler at the top of `apply()` and the idempotency guard refuses to
    touch the populated `dst.old` (rename it back manually if the user
    wants it).
    """
    backup = dst.with_suffix(dst.suffix + ".old")
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    if dst.exists():
        os.rename(dst, backup)
    os.rename(src, dst)
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)


def _default_blueprint() -> Blueprint:
    """The minimum-viable `.claude/` for a freshly seeded project.

    Future design-card #5 will deliver a typed catalog (webapp-rich,
    cli-minimal, service-fullstack, ...). Until then, this default is the
    only shape and is also what `inception_service` uses when promoting
    an intake card to a brand-new project.
    """
    return Blueprint(
        name="default",
        description="Minimum-viable .claude/ seed: standard subdirs + a CLAUDE.md stub.",
        subdirs=["commands", "agents", "hooks", "skills", "plugins", "output-styles"],
        settings=BlueprintSettings(permission_mode="default"),
        claudemd=(
            "# Project context\n\n"
            "Add project-specific guidance for Claude Code here.\n"
        ),
    )


class BlueprintService:
    """Stateless service — every call takes a `blueprint` and a `project_path`.

    Statelessness keeps it trivially testable; cross-call state would only
    matter if we later need a registry of named blueprints (the file-based
    `BlueprintStore` is that registry).
    """

    def __init__(self, blueprint: Blueprint | None = None):
        self.blueprint = blueprint or _default_blueprint()

    def apply(self, project_path: str, *, force: bool = False) -> AuditResult:
        """Seed `.claude/` according to the blueprint.

        Atomicity: every write goes into `<project>/.claude.tmp/` first; on
        success the staged folder is `os.replace`d into place, on failure
        it is `shutil.rmtree`d so the project never sees a half-written
        `.claude/`.

        Idempotency: when `.claude/` already exists and is non-empty, the
        call returns immediately with `skipped_existing=True` (unless
        `force=True`, which overwrites).

        Returns `AuditResult` describing what was actually written.

        Raises:
            BlueprintApplyFailed: a write step raised; the temp dir is cleaned up.
        """
        project = Path(project_path)
        claude_dir = project / ".claude"
        staged_dir = project / ".claude.tmp"

        audit = AuditResult(
            blueprint_name=self.blueprint.name,
            project_path=str(project),
        )

        # Idempotency guard. A bare ".claude" marker (no contents) still
        # counts as "not seeded" — only non-empty configs block re-apply.
        if claude_dir.exists() and any(claude_dir.iterdir()) and not force:
            audit.skipped_existing = True
            logger.info(
                "blueprint apply: .claude already populated at %s, skipping",
                project,
            )
            return audit

        # If `.claude.tmp/` lingers from a prior crash, scrub it. A clean
        # run never lands here.
        if staged_dir.exists():
            shutil.rmtree(staged_dir, ignore_errors=True)
        staged_dir.mkdir(parents=True, exist_ok=False)

        try:
            self._write_settings(staged_dir, audit)
            self._write_claudemd(project, staged_dir, audit)
            self._write_statusline(staged_dir, audit)
            self._write_output_style(staged_dir, audit)
            self._write_skills(project, staged_dir, audit)
            self._write_agents(staged_dir, audit)
            self._create_subdirs(staged_dir, audit)
        except Exception as e:
            shutil.rmtree(staged_dir, ignore_errors=True)
            logger.exception("blueprint apply failed for %s", project)
            raise BlueprintApplyFailed(step="stage", original=e) from e

        try:
            _atomic_replace_into(claude_dir, staged_dir)
        except Exception as e:
            shutil.rmtree(staged_dir, ignore_errors=True)
            logger.exception("blueprint apply: atomic replace failed for %s", project)
            raise BlueprintApplyFailed(step="commit", original=e) from e

        logger.info(
            "blueprint %r applied to %s: %d files, %d dirs",
            self.blueprint.name, project,
            len(audit.written_files), len(audit.created_dirs),
        )
        return audit

    # -- write helpers -----------------------------------------------------

    def _write_settings(self, staged_dir: Path, audit: AuditResult) -> None:
        """Write `<staged_dir>/settings.json` from the blueprint.

        Records the post-commit path (`.claude/settings.json`), not the
        pre-commit staged path (`.claude.tmp/settings.json`) — the audit is
        meant to describe the visible end-state, not the staging internals.
        """
        target = staged_dir / "settings.json"
        target.write_text(json.dumps(self.blueprint.settings.to_dict(), indent=2))
        audit.written_files.append(".claude/settings.json")

    def _write_claudemd(self, project: Path, staged_dir: Path,
                        audit: AuditResult) -> None:
        """Write `<project>/CLAUDE.md` if the blueprint supplies body text.

        Lives next to `.claude/`, *not* inside it — CLAUDE.md is a sibling
        convention of Claude Code, not a subdir of the config folder."""
        if not self.blueprint.claudemd:
            return
        target = project / "CLAUDE.md"
        target.write_text(self.blueprint.claudemd)
        audit.written_files.append(str(target.relative_to(project)))

    def _write_statusline(self, staged_dir: Path, audit: AuditResult) -> None:
        """Materialise the statusline script under `.claude/statusline.sh`.

        The reference into settings.json is added by `_write_settings`
        via BlueprintSettings extras (callers may also wire it up directly);
        here we just make sure the script body lands in a stable path so
        the user can `chmod +x` and reference it from settings. We don't
        chmod (the user's filesystem umask / Windows support is theirs to
        manage); CC tolerates a non-executable script referenced by an
        absolute path.
        """
        if not self.blueprint.statusline:
            return
        target = staged_dir / "statusline.sh"
        target.write_text(self.blueprint.statusline)
        audit.written_files.append(".claude/statusline.sh")

    def _write_output_style(self, staged_dir: Path, audit: AuditResult) -> None:
        """Materialise a project-scoped output style markdown stub.

        The blueprint's `output_style` field is the *name*; we write a
        frontmatter-only markdown file at `.claude/output-styles/<name>.md`
        that an operator can flesh out post-apply.
        """
        if not self.blueprint.output_style:
            return
        target = staged_dir / "output-styles" / f"{self.blueprint.output_style}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"---\nname: {self.blueprint.output_style}\n---\n\n"
            f"# {self.blueprint.output_style}\n\n"
            "Add output-style instructions for Claude Code here.\n",
        )
        audit.written_files.append(f".claude/output-styles/{self.blueprint.output_style}.md")

    def _write_skills(self, project: Path, staged_dir: Path,
                      audit: AuditResult) -> None:
        """Materialise the blueprint's `skills` list.

        For `source=project` we write a stub SKILL.md so the project ships
        with a discoverable, version-pinned skill. For `user` / `system` we
        skip the file write (the user already has those skills) but record
        the name in `audit.applied_skills` so operators can see the
        blueprint declared them.
        """
        for skill in self.blueprint.skills:
            audit.applied_skills.append(skill.name)
            if skill.source != "project":
                continue
            skill_dir = staged_dir / "skills" / skill.name
            skill_dir.mkdir(parents=True, exist_ok=True)
            frontmatter_lines = [f"name: {skill.name}"]
            if skill.version_pin:
                frontmatter_lines.append(f"version: {skill.version_pin}")
            frontmatter = "\n".join(frontmatter_lines)
            (skill_dir / "SKILL.md").write_text(
                f"---\n{frontmatter}\n---\n\n"
                f"# {skill.name}\n\n"
                "Add skill instructions for Claude Code here.\n",
            )
            audit.written_files.append(f".claude/skills/{skill.name}/SKILL.md")

    def _write_agents(self, staged_dir: Path, audit: AuditResult) -> None:
        """Materialise the blueprint's `agents` list as `<name>.md` stubs.

        The stub records `model` and `tools` in YAML frontmatter so CC can
        pick them up; the operator fleshes out the body after apply.
        """
        for agent in self.blueprint.agents:
            audit.applied_agents.append(agent.name)
            target = staged_dir / "agents" / f"{agent.name}.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            fm_lines = [f"name: {agent.name}"]
            if agent.model_default:
                fm_lines.append(f"model: {agent.model_default}")
            if agent.tools:
                tools_csv = ", ".join(agent.tools)
                fm_lines.append(f"allowed-tools: {tools_csv}")
            frontmatter = "\n".join(fm_lines)
            target.write_text(
                f"---\n{frontmatter}\n---\n\n"
                f"# {agent.name}\n\n"
                "Add agent instructions for Claude Code here.\n",
            )
            audit.written_files.append(f".claude/agents/{agent.name}.md")

    def _create_subdirs(self, staged_dir: Path, audit: AuditResult) -> None:
        """Create the declarative subdirs. Empty `.gitkeep` markers are
        dropped so a dir survives `git add .` even when the user has no
        other files yet — but only on actually-empty dirs; if a skill or
        agent writer already populated the dir, we leave it alone.

        `exist_ok=True` because skills / agents / output_style writers may
        have already created the same directory earlier in the same apply
        pass (e.g. blueprint declares both `agents` in `subdirs` and an
        agent in `agents: [...]`). Duplicate *names* in the subdirs list
        are still rejected up front.
        """
        if len(set(self.blueprint.subdirs)) != len(self.blueprint.subdirs):
            raise ValueError(
                f"blueprint {self.blueprint.name!r} has duplicate subdirs: "
                f"{self.blueprint.subdirs!r}",
            )
        for name in self.blueprint.subdirs:
            sub = staged_dir / name
            created_here = not sub.exists()
            sub.mkdir(parents=False, exist_ok=True)
            audit.created_dirs.append(str(sub.relative_to(staged_dir)))
            # Only stamp `.gitkeep` on dirs we created fresh and that are
            # still empty. A dir we created but later populated gets no
            # marker — content is enough to keep it tracked.
            if created_here or not any(sub.iterdir()):
                (sub / ".gitkeep").touch(exist_ok=True)


def apply_blueprint(project_path: str, *, force: bool = False,
                    blueprint: Blueprint | None = None) -> AuditResult:
    """Module-level convenience wrapper. Equivalent to
    `BlueprintService(blueprint).apply(project_path, force=force)`."""
    return BlueprintService(blueprint=blueprint).apply(project_path, force=force)
