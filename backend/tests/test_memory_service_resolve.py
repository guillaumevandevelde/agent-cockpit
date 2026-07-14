"""Tests for MemoryService keyword- and path-trigger resolution.

Covers the new trigger model: a rule without triggers applies always;
a rule with `paths` and/or `keywords` in its frontmatter applies when ANY of
its triggers matches (glob path match against touched files, case-insensitive
keyword match against the prompt). The resolver is the heart of the
OpenHands-style path/keyword skills layer added on top of Cockpit's
existing CLAUDE.md + .claude/rules/ memory layer.
"""
from __future__ import annotations

from pathlib import Path

from app.services.memory_service import MemoryService


def _write_rule(rules_dir: Path, name: str, body: str, frontmatter: str | None = None) -> Path:
    """Write a rule file under ``rules_dir`` and return its path."""
    rules_dir.mkdir(parents=True, exist_ok=True)
    path = rules_dir / f"{name}.md"
    if frontmatter:
        path.write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")
    else:
        path.write_text(f"{body}\n", encoding="utf-8")
    return path


def _project_root(tmp_path: Path) -> str:
    """Return a project root that contains an empty .claude/ for MemoryService."""
    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
    return str(tmp_path)


def test_resolve_keyword_trigger_matches_when_keyword_in_prompt(tmp_path):
    project_path = _project_root(tmp_path)
    _write_rule(
        tmp_path / ".claude" / "rules",
        "deploy",
        "Always run the deploy script before pushing.",
        frontmatter="keywords:\n  - deploy\n",
    )

    result = MemoryService.resolve_applicable_rules(
        project_path=project_path,
        prompt="Please deploy the staging service",
        touched_files=[],
    )

    names = [r["name"] for r in result["matched_rules"]]
    assert "deploy" in names
    matched = next(r for r in result["matched_rules"] if r["name"] == "deploy")
    assert "keyword:deploy" in matched["matched_triggers"]


def test_resolve_keyword_trigger_does_not_match_when_absent(tmp_path):
    project_path = _project_root(tmp_path)
    _write_rule(
        tmp_path / ".claude" / "rules",
        "deploy",
        "Always run the deploy script.",
        frontmatter="keywords:\n  - deploy\n",
    )

    result = MemoryService.resolve_applicable_rules(
        project_path=project_path,
        prompt="Just review the docs please",
        touched_files=[],
    )

    names = [r["name"] for r in result["matched_rules"]]
    assert "deploy" not in names
    unmatched_names = [r["name"] for r in result["unmatched_rules"]]
    assert "deploy" in unmatched_names


def test_resolve_keyword_match_is_case_insensitive(tmp_path):
    project_path = _project_root(tmp_path)
    _write_rule(
        tmp_path / ".claude" / "rules",
        "deploy",
        "Always deploy carefully.",
        frontmatter="keywords:\n  - DEPLOY\n",
    )

    result = MemoryService.resolve_applicable_rules(
        project_path=project_path,
        prompt="please deploy the build",
        touched_files=[],
    )

    names = [r["name"] for r in result["matched_rules"]]
    assert "deploy" in names


def test_resolve_keyword_string_form(tmp_path):
    """Single-string keyword form must also work (not just list)."""
    project_path = _project_root(tmp_path)
    _write_rule(
        tmp_path / ".claude" / "rules",
        "deploy",
        "Run deploy.",
        frontmatter="keywords: deploy\n",
    )

    result = MemoryService.resolve_applicable_rules(
        project_path=project_path,
        prompt="we need to deploy now",
        touched_files=[],
    )

    assert any(r["name"] == "deploy" for r in result["matched_rules"])


def test_resolve_path_trigger_matches_glob_against_touched_files(tmp_path):
    project_path = _project_root(tmp_path)
    _write_rule(
        tmp_path / ".claude" / "rules",
        "python-style",
        "Use async SQLAlchemy.",
        frontmatter="paths:\n  - backend/**/*.py\n",
    )

    result = MemoryService.resolve_applicable_rules(
        project_path=project_path,
        prompt="please review this file",
        touched_files=["backend/app/services/foo.py"],
    )

    names = [r["name"] for r in result["matched_rules"]]
    assert "python-style" in names
    matched = next(r for r in result["matched_rules"] if r["name"] == "python-style")
    assert any(t.startswith("path:") for t in matched["matched_triggers"])


def test_resolve_path_trigger_does_not_match_when_no_touched_file_matches(tmp_path):
    project_path = _project_root(tmp_path)
    _write_rule(
        tmp_path / ".claude" / "rules",
        "python-style",
        "Use async SQLAlchemy.",
        frontmatter="paths:\n  - backend/**/*.py\n",
    )

    result = MemoryService.resolve_applicable_rules(
        project_path=project_path,
        prompt="anything",
        touched_files=["frontend/src/index.ts", "README.md"],
    )

    names = [r["name"] for r in result["matched_rules"]]
    assert "python-style" not in names


def test_resolve_rule_with_no_triggers_always_matches(tmp_path):
    """A rule with neither paths nor keywords is always-on (CLAUDE.md style)."""
    project_path = _project_root(tmp_path)
    _write_rule(tmp_path / ".claude" / "rules", "general", "Be concise.")

    result = MemoryService.resolve_applicable_rules(
        project_path=project_path,
        prompt="hello",
        touched_files=[],
    )

    names = [r["name"] for r in result["matched_rules"]]
    assert "general" in names
    matched = next(r for r in result["matched_rules"] if r["name"] == "general")
    assert matched["matched_triggers"] == ["always"]


def test_resolve_multiple_triggers_are_or_combined(tmp_path):
    """A rule with both `paths` and `keywords` matches if EITHER fires."""
    project_path = _project_root(tmp_path)
    _write_rule(
        tmp_path / ".claude" / "rules",
        "backend-deploy",
        "Backend deploy checklist.",
        frontmatter="paths:\n  - backend/**/*.py\nkeywords:\n  - deploy\n",
    )

    # Path matches, keyword doesn't
    r1 = MemoryService.resolve_applicable_rules(
        project_path=project_path,
        prompt="just review",
        touched_files=["backend/app/x.py"],
    )
    names1 = [r["name"] for r in r1["matched_rules"]]
    assert "backend-deploy" in names1

    # Keyword matches, path doesn't
    r2 = MemoryService.resolve_applicable_rules(
        project_path=project_path,
        prompt="please deploy",
        touched_files=["frontend/x.ts"],
    )
    names2 = [r["name"] for r in r2["matched_rules"]]
    assert "backend-deploy" in names2

    # Neither matches
    r3 = MemoryService.resolve_applicable_rules(
        project_path=project_path,
        prompt="review docs",
        touched_files=["README.md"],
    )
    names3 = [r["name"] for r in r3["matched_rules"]]
    assert "backend-deploy" not in names3


def test_list_rules_includes_keywords(tmp_path):
    """list_rules should surface keywords so the frontend can display them."""
    project_path = _project_root(tmp_path)
    _write_rule(
        tmp_path / ".claude" / "rules",
        "deploy",
        "Run deploy.",
        frontmatter="keywords:\n  - deploy\n  - release\n",
    )

    rules = MemoryService.list_rules(project_path)
    assert len(rules) == 1
    assert sorted(rules[0]["keywords"]) == ["deploy", "release"]
    # paths still works (backwards compatible)
    assert rules[0]["scoped_paths"] == []


def test_create_rule_writes_keywords_to_frontmatter(tmp_path):
    project_path = _project_root(tmp_path)
    result = MemoryService.create_rule(
        project_path=project_path,
        name="deploy",
        content="Run deploy carefully.",
        paths=["backend/**/*.py"],
        keywords=["deploy", "release"],
        description="Deployment checklist",
    )
    assert result["success"] is True

    rules = MemoryService.list_rules(project_path)
    deploy = next(r for r in rules if r["name"] == "deploy")
    assert sorted(deploy["keywords"]) == ["deploy", "release"]
    assert deploy["scoped_paths"] == ["backend/**/*.py"]
    assert deploy["description"] == "Deployment checklist"


def test_resolve_includes_relative_path_and_content_preview(tmp_path):
    project_path = _project_root(tmp_path)
    _write_rule(
        tmp_path / ".claude" / "rules",
        "deploy",
        "Run deploy.\nMore details here.",
        frontmatter="keywords:\n  - deploy\n",
    )

    result = MemoryService.resolve_applicable_rules(
        project_path=project_path,
        prompt="deploy please",
        touched_files=[],
    )

    matched = next(r for r in result["matched_rules"] if r["name"] == "deploy")
    assert matched["relative_path"] == "deploy.md"
    assert matched["path"].endswith("deploy.md")
    assert "Run deploy." in matched["content_preview"]


def test_resolve_returns_empty_when_rules_dir_missing(tmp_path):
    project_path = _project_root(tmp_path)
    # No rules dir exists
    result = MemoryService.resolve_applicable_rules(
        project_path=project_path,
        prompt="anything",
        touched_files=["backend/foo.py"],
    )
    assert result["matched_rules"] == []
    assert result["unmatched_rules"] == []