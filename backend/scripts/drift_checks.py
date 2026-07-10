"""Pure helpers for the signal-only drift checks wired into
`.github/workflows/drift-report.yml`. The three CLI scripts
(`check_features_docs.py`, `check_agent_roles.py`, `check_claude_md_age.py`)
are thin wrappers that format these into a markdown block the workflow
appends to `$GITHUB_STEP_SUMMARY`.

Each helper exits conceptually with a status (ok vs drifted) but never raises
on drift — the workflow is signal-only, not a gate.
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

# Frontmatter `name:` line inside a `.claude/agents/*.md` file. Tolerates
# single or double quotes around the value.
_AGENT_NAME_RE = re.compile(r"^name:\s*['\"]?([A-Za-z0-9_\-]+)['\"]?\s*$", re.MULTILINE)


def find_missing_feature_docs(
    features_dir: Path,
    docs_dir: Path,
    aliases: dict[str, str],
) -> list[str]:
    """Return feature folder names that have neither a 1:1 doc nor an aliased doc.

    A folder under `features_dir/` is considered documented when either:
      * `docs_dir/<feature>.md` exists, or
      * `aliases[feature]` resolves to a file under `docs_dir/` that exists.

    The output is sorted for stable diffs and stable CI logs.
    """
    if not features_dir.exists():
        return []
    folders = sorted(p.name for p in features_dir.iterdir() if p.is_dir())
    missing: list[str] = []
    for name in folders:
        own_doc = docs_dir / f"{name}.md"
        if own_doc.exists():
            continue
        alias_name = aliases.get(name)
        if alias_name:
            alias_doc = docs_dir / alias_name
            if alias_doc.exists():
                continue
        missing.append(name)
    return missing


def list_agent_names(agents_dir: Path) -> set[str]:
    """Return the set of `name:` values from `.claude/agents/*.md` frontmatter.

    Files with no top-of-file YAML frontmatter are ignored. The scan is
    intentionally regex-based rather than full YAML parsing because the agent
    files only ever have a single quoted scalar on the `name:` line.
    """
    if not agents_dir.exists():
        return set()
    names: set[str] = set()
    for path in sorted(agents_dir.glob("*.md")):
        # Only inspect the YAML frontmatter block (between leading --- fences)
        text = path.read_text()
        m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        if not m:
            continue
        match = _AGENT_NAME_RE.search(m.group(1))
        if match:
            names.add(match.group(1))
    return names


def collect_personas_from_routing(router_path: Path, schemas_path: Path) -> set[str]:
    """Collect every persona string used in the routing tables.

    Walks `_IMPEDIMENT_AGENTS` keys and values in
    `backend/app/api/v1/kanban/router.py` plus every value in
    `WORK_TYPE_PERSONA_DEFAULTS` from `backend/app/kanban/schemas.py`.
    """
    personas: set[str] = set()

    router_text = router_path.read_text()
    tree = ast.parse(router_text)
    for node in ast.walk(tree):
        # Match assignments like `_IMPEDIMENT_AGENTS = {...}` or `_OTHER = {...}`
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id.isupper() for t in node.targets):
            continue
        value = node.value
        if not isinstance(value, ast.Dict):
            continue
        for k, v in zip(value.keys, value.values, strict=True):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                personas.add(k.value)
            for child in ast.walk(v) if isinstance(v, ast.AST) else []:
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    personas.add(child.value)

    schemas_text = schemas_path.read_text()
    schemas_tree = ast.parse(schemas_text)
    for node in ast.walk(schemas_tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "WORK_TYPE_PERSONA_DEFAULTS" for t in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for _, v in zip(node.value.keys, node.value.values, strict=True):
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                personas.add(v.value)
    return personas


def find_mismatched_personas(used: set[str], available: set[str]) -> list[str]:
    """Return sorted list of persona names used in code but not defined as agents."""
    return sorted(used - available)


def claude_md_age_in_merges(
    repo_root: Path,
    relative_file_path: str,
    threshold: int,
) -> tuple[int, bool]:
    """Count merge commits on the default branch that landed AFTER the file's last
    modification. Returns `(count, stale)` where `stale` is `count > threshold`.

    `relative_file_path` is resolved relative to the repo root (e.g. `CLAUDE.md`).
    If the file is missing or has no commits, returns `(0, False)`.
    """
    last_sha = _last_touching_commit(repo_root, relative_file_path)
    if last_sha is None:
        return (0, False)
    count_str = subprocess.check_output(
        [
            "git",
            "-C",
            str(repo_root),
            "log",
            "--merges",
            "--first-parent",
            "--format=%H",
            f"{last_sha}..HEAD",
        ],
        text=True,
    ).strip()
    count = 0 if not count_str else len(count_str.splitlines())
    return (count, count > threshold)


def _last_touching_commit(repo_root: Path, relative_file_path: str) -> str | None:
    """Return the SHA of the most recent commit touching `relative_file_path`,
    or `None` if no commit in history touches the path."""
    out = subprocess.check_output(
        [
            "git",
            "-C",
            str(repo_root),
            "log",
            "--format=%H",
            "-1",
            "--",
            relative_file_path,
        ],
        text=True,
    ).strip()
    return out or None
