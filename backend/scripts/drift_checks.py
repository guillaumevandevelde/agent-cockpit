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


# --- spec-drift helpers (spec-ssot Fase 2) -----------------------------------
# Mechanical model: spec-driven-development Fase 2 (§6 in
# docs/cockpit/spec-driven-development-analysis.md) — a signal-only drift
# check that compares a card's `metadata.spec_doc` against the functional
# surface touched by its closing merge. This is the same shape as
# `check_openapi_snapshot.py` (live spec vs committed snapshot), but with a
# prose spec (path in `docs/cockpit/`) and the merge-commit diff as the
# "live" side. It is INTENTIONALLY signal-only — drift is recorded as
# advice on the weekly drift report and a `[spec-update]` backlog card,
# not as a build-blocking gate. See analysis §4-5 (table row C).

# What counts as "functional". A pragmatic path-prefix heuristic — a merge
# that touches any of these prefixes is presumed to change behaviour the
# linked spec should describe. Excludes `backend/scripts/`, `backend/tests/`,
# `docs/`, `frontend/src/components/ui/` (shadcn primitives) etc., because
# those are tooling/test/doc surfaces where drift is much less likely to
# change the user-visible behaviour the spec describes.
DEFAULT_FUNCTIONAL_GLOBS: tuple[str, ...] = (
    "backend/app/",
    "frontend/src/features/",
    "frontend/src/lib/",
)

# What counts as "spec". Only `docs/cockpit/` is the canonical tree after
# Fase 0 consolidation (see docs/cockpit/spec-driven-development-fase-0-
# decision.md); other doc trees (`docs/superpowers/`, `docs/plans/`) are
# either workoutput or legacy and would only add false positives.
DEFAULT_SPEC_GLOBS: tuple[str, ...] = ("docs/cockpit/",)


class SpecDriftFinding:
    """One card's spec-drift report.

    `changed_functional_paths` and `changed_spec_paths` are the path lists
    from `git diff --name-only` against the merge's first parent; they keep
    the a/b prefix stripped via `parse_diff_path_list`.
    """

    __slots__ = (
        "card_id",
        "changed_functional_paths",
        "changed_spec_paths",
        "spec_doc",
    )

    def __init__(
        self,
        card_id: str,
        spec_doc: str,
        changed_functional_paths: list[str],
        changed_spec_paths: list[str],
    ) -> None:
        self.card_id = card_id
        self.spec_doc = spec_doc
        self.changed_functional_paths = changed_functional_paths
        self.changed_spec_paths = changed_spec_paths

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SpecDriftFinding):
            return NotImplemented
        return (
            self.card_id == other.card_id
            and self.spec_doc == other.spec_doc
            and self.changed_functional_paths == other.changed_functional_paths
            and self.changed_spec_paths == other.changed_spec_paths
        )

    def __repr__(self) -> str:
        return (
            f"SpecDriftFinding(card_id={self.card_id!r}, spec_doc={self.spec_doc!r}, "
            f"functional={self.changed_functional_paths!r}, spec={self.changed_spec_paths!r})"
        )


def parse_diff_path_list(raw: str) -> list[str]:
    """Parse `git diff --name-only` output.

    Strips the `a/` / `b/` prefixes that `git diff` prepends and skips blank
    lines. Returns paths in the same order git emitted them (the CLI sorts
    by working-tree-relative path, which is what consumers expect)."""
    paths: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("a/") or line.startswith("b/"):
            line = line[2:]
        paths.append(line)
    return paths


def _paths_matching(paths: list[str], prefixes: tuple[str, ...]) -> list[str]:
    return [p for p in paths if any(p.startswith(prefix) for prefix in prefixes)]


def _diff_name_only(repo_root: Path, parent_sha: str, merge_sha: str) -> list[str]:
    """Return repo-relative paths changed between two commits.

    `git diff <parent> <merge> --name-only` is the merge-vs-parent diff that
    isolates the work landed by the merge. Empty output (or a non-existent
    SHA) yields an empty list — callers can treat that as 'no change'."""
    try:
        out = subprocess.check_output(
            [
                "git",
                "-C",
                str(repo_root),
                "diff",
                "--name-only",
                parent_sha,
                merge_sha,
            ],
            text=True,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError:
        return []
    return parse_diff_path_list(out)


def _first_parent(repo_root: Path, merge_sha: str) -> str | None:
    """Return the first parent SHA of a merge commit, or None for non-merges
    or unknown SHAs."""
    try:
        out = subprocess.check_output(
            [
                "git",
                "-C",
                str(repo_root),
                "rev-parse",
                f"{merge_sha}^1",
            ],
            text=True,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError:
        return None
    return out.strip() or None


def find_spec_drift_for_card(
    repo_root: Path,
    card_id: str,
    spec_doc: str,
    merge_sha: str,
    functional_glob: tuple[str, ...] = DEFAULT_FUNCTIONAL_GLOBS,
    spec_glob: tuple[str, ...] = DEFAULT_SPEC_GLOBS,
) -> list[SpecDriftFinding]:
    """Return a single-element list when the card's merge touched functional
    paths but not its linked spec-doc; otherwise an empty list.

    Drift = the merge's functional surface grew without the spec-doc growing
    alongside it. We can't tell from a path list whether the *content* of the
    spec-doc needed an update — that requires an LLM (Fase 3). But a merge
    that touched zero `spec_glob` paths is a confident-enough signal to
    surface as advice, matching Fase 1's "plan-attachment counts as the spec
    by default" stance: the prose spec should be touched when the linked
    code changed.

    Special cases (all return empty list, never raise):
      * `spec_doc` is a URL (no repo path to compare against) — drift can't
        be checked mechanically.
      * `merge_sha` doesn't resolve or has no first parent — caller filters
        out before we get here, but defensive.
      * merge has zero functional paths in its diff — nothing to flag.
    """
    if spec_doc.startswith(("http://", "https://")):
        return []

    parent = _first_parent(repo_root, merge_sha)
    if parent is None:
        return []

    changed = _diff_name_only(repo_root, parent, merge_sha)
    if not changed:
        return []

    functional = _paths_matching(changed, functional_glob)
    spec = _paths_matching(changed, spec_glob)
    if not functional:
        return []
    if spec_doc in spec:
        return []

    return [SpecDriftFinding(
        card_id=card_id,
        spec_doc=spec_doc,
        changed_functional_paths=functional,
        changed_spec_paths=spec,
    )]
