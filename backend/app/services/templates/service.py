"""TemplateService — starter-content catalog for repo bootstrap.

A *template* is the **starter code** (folder structure + source files + CI/
config) a freshly-bootstrapped repo starts with. It is orthogonal to a
*blueprint* (the `.claude/` configuration — see `BlueprintService`): the same
React app can be born from any blueprint. `RepoBootstrapService` spreads a
template on top of the empty git repo created earlier in the bootstrap chain
(see docs/cockpit/repo-provisioning-bootstrap.md §3.1 / §4.1).

Templates live as data directories next to this module. Each template file is
stored with a trailing ``.tmpl`` suffix, for two reasons:

1. **Tooling isolation.** `pytest -q`, `ruff check app tests` and
   `bandit -r app` all recurse from ``backend/``. A raw ``tests/test_smoke.py``
   or a ``app/main.py`` containing ``{{ project_name }}`` placeholders would be
   collected / linted / fail to parse as part of *this* repo's suite. A
   ``.tmpl`` suffix is invisible to all three.
2. **Explicitness.** The suffix marks a file as template source, not a real
   source file of the cockpit backend.

`render()` strips the ``.tmpl`` suffix and substitutes ``{{ var }}`` placeholders
when materialising the target. The rendered output is therefore a real,
compilable project with clean filenames.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

_TEMPLATES_DIR = Path(__file__).parent
_INDEX_FILE = _TEMPLATES_DIR / "TEMPLATES_INDEX.json"
_TMPL_SUFFIX = ".tmpl"
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class TemplateDescriptor(BaseModel):
    """Metadata describing a single template, sourced from TEMPLATES_INDEX.json."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    dir: str = Field(min_length=1)


def _substitute(text: str, vars: dict[str, str]) -> str:
    """Replace every ``{{ name }}`` placeholder with ``vars[name]`` (blank if absent)."""
    return _PLACEHOLDER.sub(lambda m: str(vars.get(m.group(1), "")), text)


class TemplateService:
    """Lists and renders starter-content templates onto a target path."""

    def __init__(self, templates_dir: Path | None = None) -> None:
        self._dir = templates_dir or _TEMPLATES_DIR
        self._index_file = self._dir / _INDEX_FILE.name

    def _load_index(self) -> list[TemplateDescriptor]:
        raw = json.loads(self._index_file.read_text())
        return [TemplateDescriptor(**entry) for entry in raw["templates"]]

    def list_templates(self) -> list[TemplateDescriptor]:
        return self._load_index()

    def _descriptor(self, name: str) -> TemplateDescriptor:
        for t in self._load_index():
            if t.name == name:
                return t
        raise KeyError(f"unknown template: {name!r}")

    def render(
        self,
        template_name: str,
        target_path: Path | str,
        *,
        vars: dict[str, str],
        overwrite: bool = False,
        template_version: str | None = None,
    ) -> None:
        """Render ``template_name`` into ``target_path`` with ``{{ var }}`` substitution.

        Refuses to overwrite existing files unless ``overwrite=True`` (raises
        ``FileExistsError``). Passing ``template_version`` pins the render to a
        specific version and raises ``ValueError`` on mismatch; ``None`` means
        latest (the only version currently shipped per template).
        """
        descriptor = self._descriptor(template_name)
        if template_version is not None and template_version != descriptor.version:
            raise ValueError(
                f"template {template_name!r} has version {descriptor.version}, "
                f"requested {template_version}"
            )

        source_root = self._dir / descriptor.dir
        target_root = Path(target_path)

        for src in sorted(source_root.rglob("*")):
            if src.is_dir():
                continue
            rel = src.relative_to(source_root)
            rel_str = rel.as_posix()
            if rel_str.endswith(_TMPL_SUFFIX):
                rel_str = rel_str[: -len(_TMPL_SUFFIX)]
            dest = target_root / rel_str
            if dest.exists() and not overwrite:
                raise FileExistsError(f"refusing to overwrite {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(_substitute(src.read_text(), vars))
