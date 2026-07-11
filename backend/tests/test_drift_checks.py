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
    SpecDriftFinding,
    claude_md_age_in_merges,
    collect_personas_from_routing,
    find_mismatched_personas,
    find_missing_feature_docs,
    find_spec_drift_for_card,
    list_agent_names,
    parse_diff_path_list,
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


# --- check 4: spec-drift signal (spec-ssot Fase 2) --------------------------


def test_parse_diff_path_list_strips_a_b_prefixes():
    """`git diff --name-only` output includes a/ and b/ prefixes; strip them."""
    raw = "a/backend/app/main.py\nb/docs/cockpit/foo.md\n"
    assert parse_diff_path_list(raw) == ["backend/app/main.py", "docs/cockpit/foo.md"]


def test_parse_diff_path_list_skips_blank_lines():
    raw = "a/backend/app/main.py\n\nb/docs/cockpit/foo.md\n\n"
    assert parse_diff_path_list(raw) == ["backend/app/main.py", "docs/cockpit/foo.md"]


def test_parse_diff_path_list_empty_input_returns_empty_list():
    assert parse_diff_path_list("") == []
    assert parse_diff_path_list("\n\n") == []


def test_find_spec_drift_flags_functional_diff_without_spec_doc_update(
    tmp_path: Path, isolated_git_env
):
    """A card links to docs/cockpit/foo.md, but its merge touched backend/app/
    without touching the spec-doc → flag."""
    _init_repo_with_master(tmp_path)
    # baseline commit that creates the spec-doc
    (tmp_path / "docs" / "cockpit").mkdir(parents=True)
    (tmp_path / "docs" / "cockpit" / "foo.md").write_text("# foo\n")
    _git(tmp_path, "add", "docs/cockpit/foo.md")
    _git(tmp_path, "commit", "-q", "-m", "init foo spec")

    # feature branch that touches only functional code
    _git(tmp_path, "checkout", "-q", "-b", "feat")
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "backend" / "app" / "main.py").write_text("def hello():\n    return 1\n")
    _git(tmp_path, "add", "backend/app/main.py")
    _git(tmp_path, "commit", "-q", "-m", "add main")
    _git(tmp_path, "checkout", "-q", "master")
    merge_sha = _merge_with_sha(tmp_path, "feat", "merge feat")

    findings = find_spec_drift_for_card(
        tmp_path,
        card_id="card-1",
        spec_doc="docs/cockpit/foo.md",
        merge_sha=merge_sha,
        functional_glob=("backend/app/", "frontend/src/"),
        spec_glob=("docs/cockpit/",),
    )

    assert len(findings) == 1
    assert findings[0].card_id == "card-1"
    assert findings[0].spec_doc == "docs/cockpit/foo.md"
    assert "backend/app/main.py" in findings[0].changed_functional_paths
    assert findings[0].changed_spec_paths == []


def test_find_spec_drift_clean_when_spec_doc_updated_alongside(
    tmp_path: Path, isolated_git_env
):
    """When the merge touched the spec-doc, no drift is flagged."""
    _init_repo_with_master(tmp_path)
    (tmp_path / "docs" / "cockpit").mkdir(parents=True)
    (tmp_path / "docs" / "cockpit" / "foo.md").write_text("# foo\n")
    _git(tmp_path, "add", "docs/cockpit/foo.md")
    _git(tmp_path, "commit", "-q", "-m", "init foo spec")

    _git(tmp_path, "checkout", "-q", "-b", "feat")
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "backend" / "app" / "main.py").write_text("def hello():\n    return 1\n")
    (tmp_path / "docs" / "cockpit" / "foo.md").write_text("# foo updated\n")
    _git(tmp_path, "add", "backend/app/main.py", "docs/cockpit/foo.md")
    _git(tmp_path, "commit", "-q", "-m", "add main + update spec")
    _git(tmp_path, "checkout", "-q", "master")
    merge_sha = _merge_with_sha(tmp_path, "feat", "merge feat")

    findings = find_spec_drift_for_card(
        tmp_path,
        card_id="card-2",
        spec_doc="docs/cockpit/foo.md",
        merge_sha=merge_sha,
        functional_glob=("backend/app/", "frontend/src/"),
        spec_glob=("docs/cockpit/",),
    )

    assert findings == []


def test_find_spec_drift_clean_when_only_spec_doc_changed(
    tmp_path: Path, isolated_git_env
):
    """A pure spec-doc change (no functional path touched) → no drift."""
    _init_repo_with_master(tmp_path)
    (tmp_path / "docs" / "cockpit").mkdir(parents=True)
    (tmp_path / "docs" / "cockpit" / "foo.md").write_text("# foo\n")
    _git(tmp_path, "add", "docs/cockpit/foo.md")
    _git(tmp_path, "commit", "-q", "-m", "init foo spec")

    _git(tmp_path, "checkout", "-q", "-b", "spec-only")
    (tmp_path / "docs" / "cockpit" / "foo.md").write_text("# foo elaborated\n")
    _git(tmp_path, "add", "docs/cockpit/foo.md")
    _git(tmp_path, "commit", "-q", "-m", "elaborate spec")
    _git(tmp_path, "checkout", "-q", "master")
    merge_sha = _merge_with_sha(tmp_path, "spec-only", "merge spec")

    findings = find_spec_drift_for_card(
        tmp_path,
        card_id="card-3",
        spec_doc="docs/cockpit/foo.md",
        merge_sha=merge_sha,
        functional_glob=("backend/app/", "frontend/src/"),
        spec_glob=("docs/cockpit/",),
    )

    assert findings == []


def test_find_spec_drift_ignores_unrelated_paths(
    tmp_path: Path, isolated_git_env
):
    """A merge that touches only paths OUTSIDE functional_glob → no drift."""
    _init_repo_with_master(tmp_path)
    (tmp_path / "docs" / "cockpit").mkdir(parents=True)
    (tmp_path / "docs" / "cockpit" / "foo.md").write_text("# foo\n")
    _git(tmp_path, "add", "docs/cockpit/foo.md")
    _git(tmp_path, "commit", "-q", "-m", "init foo spec")

    _git(tmp_path, "checkout", "-q", "-b", "infra")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "scrub.py").write_text("#!/usr/bin/env python\n")
    _git(tmp_path, "add", "scripts/scrub.py")
    _git(tmp_path, "commit", "-q", "-m", "add script")
    _git(tmp_path, "checkout", "-q", "master")
    merge_sha = _merge_with_sha(tmp_path, "infra", "merge infra")

    findings = find_spec_drift_for_card(
        tmp_path,
        card_id="card-4",
        spec_doc="docs/cockpit/foo.md",
        merge_sha=merge_sha,
        functional_glob=("backend/app/", "frontend/src/"),
        spec_glob=("docs/cockpit/",),
    )

    assert findings == []


def test_find_spec_drift_handles_url_spec_doc_as_no_drift_signal(
    tmp_path: Path, isolated_git_env
):
    """A URL spec-doc (not a repo path) has no local file to check → no signal.

    Per Fase 1 schema (`SPEC_DOC_META_KEY`) a spec_doc can be a URL when the
    authoritative spec lives outside the repo (e.g. an external doc). Without
    a local file we can't mechanically detect drift; the script reports this
    as 'out-of-scope', not as drift."""
    _init_repo_with_master(tmp_path)
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "backend" / "app" / "main.py").write_text("x = 1\n")
    _git(tmp_path, "add", "backend/app/main.py")
    _git(tmp_path, "commit", "-q", "-m", "init")
    merge_sha = _git_revparse(tmp_path, "HEAD")

    findings = find_spec_drift_for_card(
        tmp_path,
        card_id="card-5",
        spec_doc="https://example.com/spec.md",
        merge_sha=merge_sha,
        functional_glob=("backend/app/", "frontend/src/"),
        spec_glob=("docs/cockpit/",),
    )

    assert findings == []


def test_find_spec_drift_handles_missing_merge_sha_gracefully(
    tmp_path: Path, isolated_git_env
):
    """An invalid merge SHA returns no findings (not a crash) — caller filters
    non-existent cards out before we ever get here, but defensive anyway."""
    _init_repo_with_master(tmp_path)
    # No merge commits; pass the init SHA but make it look like a 'merge' that
    # is also empty.
    findings = find_spec_drift_for_card(
        tmp_path,
        card_id="card-6",
        spec_doc="docs/cockpit/missing.md",
        merge_sha="0" * 40,
        functional_glob=("backend/app/",),
        spec_glob=("docs/cockpit/",),
    )

    assert findings == []


# --- helpers used only by the new tests -------------------------------------


def _git_revparse(cwd: Path, ref: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(cwd), "rev-parse", ref], text=True
    ).strip()


def _merge_with_sha(cwd: Path, branch: str, message: str) -> str:
    """Merge branch back to master with --no-ff and return the merge SHA."""
    subprocess.run(
        ["git", "-C", str(cwd), "merge", "--no-ff", "-q", branch, "-m", message],
        check=True,
        capture_output=True,
    )
    return _git_revparse(cwd, "HEAD")


def test_render_summary_ok_when_no_findings():
    from scripts.check_spec_drift import render_summary

    body, status = render_summary([])

    assert status == "ok"
    assert "**Status:** ok" in body
    assert "[spec-update]" not in body


def test_render_summary_lists_drifts_with_paths():
    from scripts.check_spec_drift import render_summary

    findings = [
        SpecDriftFinding(
            card_id="c1",
            spec_doc="docs/cockpit/foo.md",
            changed_functional_paths=["backend/app/main.py", "frontend/src/features/x/foo.tsx"],
            changed_spec_paths=[],
        ),
        SpecDriftFinding(
            card_id="c2",
            spec_doc="docs/cockpit/bar.md",
            changed_functional_paths=["backend/app/svc.py"] * 7,
            changed_spec_paths=[],
        ),
    ]

    body, status = render_summary(findings)

    assert status == "drifted: 2"
    assert "**Status:** drifted" in body
    assert "[spec-update]" in body
    assert "`c1` → `docs/cockpit/foo.md`" in body
    assert "+2 more" in body  # 7 paths, only 5 listed inline
