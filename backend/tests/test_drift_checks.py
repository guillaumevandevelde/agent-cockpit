"""Unit tests for the pure helpers used by the drift-report signal scripts.

The three CLI scripts under `backend/scripts/check_{features_docs,agent_roles,
claude_md_age}.py` are thin wrappers; the logic lives in
`scripts.drift_checks`. Tests drive those helpers directly so we don't have to
shell out to Python in CI.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from scripts.drift_checks import (
    claude_md_age_in_merges,
    collect_personas_from_routing,
    find_mismatched_personas,
    find_missing_feature_docs,
    list_agent_names,
)

# --- check 1: features -> docs mapping ---------------------------------------


def test_find_missing_feature_docs_reports_features_without_doc(tmp_path: Path):
    features = tmp_path / "features"
    docs = tmp_path / "docs"
    features.mkdir()
    docs.mkdir()
    (features / "agents").mkdir()
    (features / "kanban").mkdir()
    (features / "orphaned").mkdir()
    (docs / "agents.md").write_text("# agents\n")
    (docs / "kanban.md").write_text("# kanban\n")
    # 'orphaned' has no corresponding docs/features/orphaned.md

    missing = find_missing_feature_docs(features, docs, aliases={})

    assert missing == ["orphaned"]


def test_find_missing_feature_docs_respects_aliases(tmp_path: Path):
    features = tmp_path / "features"
    docs = tmp_path / "docs"
    features.mkdir()
    docs.mkdir()
    (features / "agents").mkdir()
    (features / "skills").mkdir()
    (docs / "agents-skills.md").write_text("# agents & skills\n")
    aliases = {
        "agents": "agents-skills.md",
        "skills": "agents-skills.md",
    }

    missing = find_missing_feature_docs(features, docs, aliases=aliases)

    assert missing == []


def test_find_missing_feature_docs_sorted_for_stable_output(tmp_path: Path):
    features = tmp_path / "features"
    docs = tmp_path / "docs"
    features.mkdir()
    (features / "zeta").mkdir()
    (features / "alpha").mkdir()
    (features / "mu").mkdir()
    # none of them have docs

    missing = find_missing_feature_docs(features, docs, aliases={})

    assert missing == ["alpha", "mu", "zeta"]


def test_find_missing_feature_docs_ignores_files_at_features_root(tmp_path: Path):
    """A stray file under features/ is not a feature folder and should be skipped."""
    features = tmp_path / "features"
    docs = tmp_path / "docs"
    features.mkdir()
    docs.mkdir()
    (features / "real").mkdir()
    (features / "stray.txt").write_text("not a feature")
    (docs / "real.md").write_text("# real\n")

    missing = find_missing_feature_docs(features, docs, aliases={})

    assert missing == []


# --- check 2: persona role consistency ---------------------------------------


def test_list_agent_names_reads_frontmatter_name(tmp_path: Path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "engineer.md").write_text(
        "---\nname: 'engineer'\ndescription: 'does things'\n---\nbody\n"
    )
    (agents / "analyst.md").write_text(
        '---\nname: "analyst"\ndescription: "plans things"\n---\nbody\n'
    )
    # A file without a name: frontmatter should be ignored
    (agents / "scratch.md").write_text("---\ndescription: 'no name here'\n---\nbody\n")

    assert list_agent_names(agents) == {"analyst", "engineer"}


def test_collect_personas_from_routing_extracts_keys_and_values(tmp_path: Path):
    router = tmp_path / "router.py"
    router.write_text(
        textwrap.dedent(
            """
            _IMPEDIMENT_AGENTS = {
                "analyst": ["engineer"],
                "engineer": ["analyst"],
            }
            """
        )
    )
    schemas = tmp_path / "schemas.py"
    schemas.write_text(
        textwrap.dedent(
            """
            WORK_TYPE_PERSONA_DEFAULTS: dict[str, str] = {
                "analysis": "analyst",
                "feature": "engineer",
            }
            """
        )
    )

    assert collect_personas_from_routing(router, schemas) == {"analyst", "engineer"}


def test_collect_personas_from_routing_handles_singular_assignment(tmp_path: Path):
    """Some persona dicts are assigned to a single key (e.g., 'analyst': 'engineer')."""
    router = tmp_path / "router.py"
    router.write_text(
        textwrap.dedent(
            """
            _OTHER = {
                "analyst": "engineer",
            }
            """
        )
    )
    schemas = tmp_path / "schemas.py"
    schemas.write_text("WORK_TYPES = ['analysis']\n")

    assert collect_personas_from_routing(router, schemas) == {"analyst", "engineer"}


def test_find_mismatched_personas_returns_only_missing():
    used = {"analyst", "engineer", "ghost"}
    available = {"analyst", "engineer"}

    assert find_mismatched_personas(used, available) == ["ghost"]


def test_find_mismatched_personas_empty_when_all_match():
    assert find_mismatched_personas({"analyst"}, {"analyst", "engineer"}) == []


# --- check 3: CLAUDE.md age vs merge count -----------------------------------


@pytest.fixture
def isolated_git_env(monkeypatch):
    """Strip GIT_* env vars so the test's tmp_path git repo is authoritative.

    Mirrors the conftest.py `_isolate_git_env` pattern — without this, a test
    that runs inside a real git checkout (or under a pre-push hook) can
    accidentally commit onto the real repo's HEAD.
    """
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)
    yield


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo_with_master(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "master")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")


def test_claude_md_age_in_merges_returns_zero_when_no_merges_after_file(
    tmp_path: Path, isolated_git_env
):
    _init_repo_with_master(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# claude\n")
    _git(tmp_path, "add", "CLAUDE.md")
    _git(tmp_path, "commit", "-q", "-m", "init CLAUDE.md")
    # Add a non-merge commit after CLAUDE.md
    (tmp_path / "x.txt").write_text("x")
    _git(tmp_path, "add", "x.txt")
    _git(tmp_path, "commit", "-q", "-m", "non-merge after")

    count, stale = claude_md_age_in_merges(tmp_path, "CLAUDE.md", threshold=20)

    assert count == 0
    assert stale is False


def test_claude_md_age_in_merges_counts_merge_commits_after_file(
    tmp_path: Path, isolated_git_env
):
    _init_repo_with_master(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# claude\n")
    _git(tmp_path, "add", "CLAUDE.md")
    _git(tmp_path, "commit", "-q", "-m", "init CLAUDE.md")
    # Create a feature branch, add a commit, merge it back with --no-ff
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    (tmp_path / "x.txt").write_text("x")
    _git(tmp_path, "add", "x.txt")
    _git(tmp_path, "commit", "-q", "-m", "feature work")
    _git(tmp_path, "checkout", "-q", "master")
    _git(tmp_path, "merge", "--no-ff", "-q", "feature", "-m", "merge feature")

    count, stale = claude_md_age_in_merges(tmp_path, "CLAUDE.md", threshold=20)

    assert count == 1
    assert stale is False


def test_claude_md_age_in_merges_flags_stale_above_threshold(
    tmp_path: Path, isolated_git_env
):
    _init_repo_with_master(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# claude\n")
    _git(tmp_path, "add", "CLAUDE.md")
    _git(tmp_path, "commit", "-q", "-m", "init CLAUDE.md")
    # Create 3 merge commits; threshold = 2
    for i in range(3):
        _git(tmp_path, "checkout", "-q", "-b", f"f{i}")
        (tmp_path / f"f{i}.txt").write_text(str(i))
        _git(tmp_path, "add", f"f{i}.txt")
        _git(tmp_path, "commit", "-q", "-m", f"work {i}")
        _git(tmp_path, "checkout", "-q", "master")
        _git(tmp_path, "merge", "--no-ff", "-q", f"f{i}", "-m", f"merge {i}")

    count, stale = claude_md_age_in_merges(tmp_path, "CLAUDE.md", threshold=2)

    assert count == 3
    assert stale is True


def test_claude_md_age_in_merges_missing_file_returns_zero(
    tmp_path: Path, isolated_git_env
):
    _init_repo_with_master(tmp_path)
    (tmp_path / "README.md").write_text("# readme\n")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-q", "-m", "init")

    count, stale = claude_md_age_in_merges(tmp_path, "CLAUDE.md", threshold=20)

    assert count == 0
    assert stale is False
