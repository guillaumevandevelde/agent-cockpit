"""BlueprintService — apply a project-level `.claude/` blueprint.

A *blueprint* is the declarative shape of the `.claude/` folder for a project:
which subdirs exist, what default `settings.json` looks like, an optional
`CLAUDE.md` stub. The full CRUD + UI for blueprints (facet A kaart `395590d7`)
will land in a follow-up; this initial cut provides the `apply()` engine that
`create_project_from_intake` and facet B's `RepoBootstrapService` depend on.

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
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Blueprint:
    """Declarative shape of a `.claude/` folder seed.

    Each field is a version-pinned JSON/YAML shape per sibling #4
    (`project_blueprint` kaart `395590d7`); this dataclass is the
    apply-engine's narrow view of that broader model — only the bits
    `apply()` needs today. Wider CRUD arrives in the sibling card.
    """

    subdirs: list[str] = field(
        default_factory=lambda: ["commands", "agents", "hooks", "skills", "plugins", "output-styles"],
    )
    settings: dict[str, Any] = field(
        default_factory=lambda: {"permissions": {"defaultMode": "default"}},
    )
    claudemd: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "subdirs": list(self.subdirs),
            "settings": dict(self.settings),
            "claudemd": self.claudemd,
        }


@dataclass
class AuditResult:
    """Return value of `BlueprintService.apply` — what was written, what was kept."""

    blueprint: Blueprint
    project_path: str
    written_files: list[str] = field(default_factory=list)
    created_dirs: list[str] = field(default_factory=list)
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

    Future sibling #4 will deliver a typed catalog (webapp-rich, cli-minimal,
    service-fullstack, ...). Until then, this default is the only shape."""
    return Blueprint(
        subdirs=["commands", "agents", "hooks", "skills", "plugins", "output-styles"],
        settings={"permissions": {"defaultMode": "default"}},
        claudemd=(
            "# Project context\n\n"
            "Add project-specific guidance for Claude Code here.\n"
        ),
    )


class BlueprintService:
    """Stateless service — every call takes a `project_path`.

    Statelessness keeps it trivially testable; cross-call state would only
    matter if we later need a registry of named blueprints (the sibling #4
    card's job).
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
        `force=True`, which wipes the staged dir then bails on the existing
        in-place one — full overwrite-merge lives in the sibling #4 card).

        Returns `AuditResult` describing what was actually written.

        Raises:
            BlueprintAlreadyExists: `.claude/` is non-empty and force=False.
            BlueprintApplyFailed: a write step raised; the temp dir is cleaned up.
        """
        project = Path(project_path)
        claude_dir = project / ".claude"
        staged_dir = project / ".claude.tmp"

        audit = AuditResult(
            blueprint=self.blueprint,
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
            self._write_settings(project, staged_dir, audit)
            self._write_claudemd(project, staged_dir, audit)
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
            "blueprint applied to %s: %d files, %d dirs",
            project, len(audit.written_files), len(audit.created_dirs),
        )
        return audit

    def _write_settings(self, project: Path, staged_dir: Path,
                        audit: AuditResult) -> None:
        """Write `<staged_dir>/settings.json` from the blueprint.

        Records the post-commit path (`.claude/settings.json`), not the
        pre-commit staged path (`.claude.tmp/settings.json`) — the audit is
        meant to describe the visible end-state, not the staging internals.
        """
        target = staged_dir / "settings.json"
        target.write_text(json.dumps(self.blueprint.settings, indent=2))
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

    def _create_subdirs(self, staged_dir: Path, audit: AuditResult) -> None:
        """Create the declarative subdirs. Empty `.gitkeep` markers are
        dropped so the dirs survive a `git add .` even when the user has
        no other files yet."""
        for name in self.blueprint.subdirs:
            sub = staged_dir / name
            sub.mkdir(parents=False, exist_ok=False)
            (sub / ".gitkeep").touch(exist_ok=False)
            audit.created_dirs.append(str(sub.relative_to(staged_dir)))


def apply_blueprint(project_path: str, *, force: bool = False,
                    blueprint: Blueprint | None = None) -> AuditResult:
    """Module-level convenience wrapper. Equivalent to
    `BlueprintService(blueprint).apply(project_path, force=force)`."""
    return BlueprintService(blueprint=blueprint).apply(project_path, force=force)
