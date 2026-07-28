"""CITemplateService — render and apply GitHub-Actions workflow templates.

A *CI template* is a Jinja2 file under ``backend/app/services/ci_templates/``
(``python-strict.yml.j2``, ``node-strict.yml.j2``, ``minimal.yml.j2``) that
materialises a `.github/workflows/<profile>.yml` file at the target project.
Today the only profiles are these three; new ones are added by dropping a
``.j2`` file into the templates directory and appending a `CITemplateInfo`
entry in `_PROFILES` below — no service edit needed.

This service is the *facet-D CITemplateService* referenced by kanban card
`c66a93a20c0a` (follow-up #7 of
``docs/cockpit/veilig-bouwen-en-uitleveren.md`` §6). A future sibling card
(`dceb60ab5352`) will call ``apply()`` from `RepoBootstrapService` at project
birth, with the profile chosen by `BootstrapPolicy`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

logger = logging.getLogger(__name__)


class CITemplateError(Exception):
    """Base class for CITemplateService errors."""


class CITemplateProfileUnknown(CITemplateError):
    """Raised when a profile name has no registered entry in `_PROFILES`."""


class CITemplateRenderFailed(CITemplateError):
    """Raised when Jinja2 fails to render a template (missing parameter, syntax error)."""

    def __init__(self, profile: str, original: Exception):
        super().__init__(f"failed to render CI template {profile!r}: {original}")
        self.profile = profile
        self.original = original


@dataclass(frozen=True)
class CITemplateInfo:
    """Catalog entry for one CI profile.

    `filename` is the on-disk name of the rendered workflow
    (``.github/workflows/<profile>.yml``); the template source file is the
    same stem with ``.yml.j2`` appended.
    """

    name: str
    description: str
    filename: str
    parameters: tuple[str, ...]


@dataclass
class CITemplateApplyResult:
    """Return value of `CITemplateService.apply`.

    `written_file` is the relative path that was (or would be) written;
    `None` when the call was a no-op (idempotency skip).
    """

    profile: str
    project_path: str
    written_file: str | None
    skipped_existing: bool
    force: bool


#: Catalog of supported CI profiles. Kept as a module-level tuple so it's
#: trivially testable and a future CLI / REST / docs surface can iterate it
#: without re-reading the templates directory.
_PROFILES: tuple[CITemplateInfo, ...] = (
    CITemplateInfo(
        name="python-strict",
        description=(
            "Strict Python CI: ruff + pytest, parameterised by python-version "
            "and the path to requirements-dev.txt. Mirrors this repo's backend "
            "job in .github/workflows/quality.yml."
        ),
        filename="python-strict.yml",
        parameters=("python_version", "requirements_dev_path"),
    ),
    CITemplateInfo(
        name="node-strict",
        description=(
            "Strict Node CI: npm ci + lint + test + build, parameterised by "
            "node-version. Mirrors this repo's frontend job in "
            ".github/workflows/quality.yml."
        ),
        filename="node-strict.yml",
        parameters=("node_version",),
    ),
    CITemplateInfo(
        name="minimal",
        description=(
            "Lightweight hello-world pipeline: one job that prints a greeting. "
            "Use as a starting point for projects that want CI without a "
            "language-specific toolchain."
        ),
        filename="minimal.yml",
        parameters=(),
    ),
)


_TEMPLATES_DIR = Path(__file__).parent
_DEFAULT_PYTHON_VERSION = "3.11"
_DEFAULT_NODE_VERSION = "22"
_DEFAULT_REQUIREMENTS_DEV_PATH = "requirements-dev.txt"


class CITemplateService:
    """Render Jinja2 CI templates and apply them to a project's ``.github/workflows/``.

    Stateless and thread-safe (Jinja2 `Environment` is per-instance and
    read-only after construction). All filesystem writes are local to
    `project_path`; no network, no subprocess, no shell.
    """

    def __init__(self, templates_dir: Path | None = None) -> None:
        self._templates_dir = templates_dir or _TEMPLATES_DIR
        # `StrictUndefined` makes an undeclared template variable a render
        # error rather than silently rendering as empty string — that's the
        # desired behaviour for parametric templates: typos must surface.
        # `autoescape=False` is deliberate: these templates render YAML CI
        # workflows (`*.yml.j2`), not HTML. HTML-escaping would rewrite `&`,
        # `<` and quotes into entities inside the generated workflow and
        # corrupt it. No untrusted value reaches an HTML sink here.
        self._env = Environment(  # nosec B701
            loader=FileSystemLoader(str(self._templates_dir)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,
        )

    # -- catalog ----------------------------------------------------------

    def list_templates(self) -> list[CITemplateInfo]:
        """Return all registered profiles, sorted by name for stable output."""
        return sorted(_PROFILES, key=lambda p: p.name)

    def get_template(self, profile: str) -> CITemplateInfo:
        """Look up one profile by name. Raises CITemplateProfileUnknown."""
        for entry in _PROFILES:
            if entry.name == profile:
                return entry
        raise CITemplateProfileUnknown(f"unknown CI profile: {profile!r}")

    # -- rendering --------------------------------------------------------

    @staticmethod
    def _defaults_for(profile: CITemplateInfo) -> dict[str, Any]:
        """Default values for the parameters a profile declares.

        Centralised so the REST surface and `apply()` can both pass
        `params={}` and get a useful render (matching the default values
        baked into each `.j2` template via this same dict).
        """
        defaults: dict[str, dict[str, Any]] = {
            "python-strict": {
                "python_version": _DEFAULT_PYTHON_VERSION,
                "requirements_dev_path": _DEFAULT_REQUIREMENTS_DEV_PATH,
            },
            "node-strict": {
                "node_version": _DEFAULT_NODE_VERSION,
            },
            "minimal": {},
        }
        return defaults.get(profile.name, {})

    def _render(self, profile: str, **params: Any) -> str:
        """Render one profile to a YAML string. Raises CITemplateError.

        Public to tests via the leading underscore convention used elsewhere
        in the codebase (e.g. BlueprintService's `_atomic_replace_into`).
        Bound method (not `@staticmethod`) because it consults `self._env`.
        Call as ``svc._render(profile, **params)`` or
        ``CITemplateService()._render(profile, **params)``.
        """
        info = self.get_template(profile)
        merged = {**self._defaults_for(info), **params}
        try:
            template = self._env.get_template(f"{info.name}.yml.j2")
            return template.render(**merged)
        except CITemplateProfileUnknown:
            raise
        except TemplateError as e:
            raise CITemplateRenderFailed(profile, e) from e

    # -- apply ------------------------------------------------------------

    def apply(
        self,
        project_path: str,
        profile: str,
        *,
        force: bool = False,
        **params: Any,
    ) -> CITemplateApplyResult:
        """Render ``profile`` and write it to ``<project>/.github/workflows/<profile>.yml``.

        Idempotency: if the destination already exists, the call returns a
        `CITemplateApplyResult` with `skipped_existing=True` and
        `written_file=None` *unless* ``force=True`` — which overwrites and
        records the override in the audit log line.

        Returns:
            CITemplateApplyResult describing what happened.

        Raises:
            CITemplateProfileUnknown: ``profile`` is not a registered name.
            CITemplateRenderFailed: a Jinja2 render error (missing param, syntax).
        """
        info = self.get_template(profile)
        rendered = self._render(profile, **params)

        project = Path(project_path)
        workflows_dir = project / ".github" / "workflows"
        target = workflows_dir / info.filename
        target_rel = str(target.relative_to(project))

        if target.exists() and not force:
            logger.info(
                "CI template %r: %s already exists, skipping (force=False)",
                profile, target_rel,
            )
            return CITemplateApplyResult(
                profile=profile,
                project_path=str(project),
                written_file=None,
                skipped_existing=True,
                force=False,
            )

        workflows_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered)

        audit_kwargs = {"force": force} if force else {}
        logger.info(
            "CI template %r applied to %s: wrote %s%s",
            profile, project, target_rel,
            " force=True" if force else "",
            extra=audit_kwargs or None,
        )
        return CITemplateApplyResult(
            profile=profile,
            project_path=str(project),
            written_file=target_rel,
            skipped_existing=False,
            force=force,
        )


__all__ = [
    "CITemplateApplyResult",
    "CITemplateError",
    "CITemplateInfo",
    "CITemplateProfileUnknown",
    "CITemplateRenderFailed",
    "CITemplateService",
]