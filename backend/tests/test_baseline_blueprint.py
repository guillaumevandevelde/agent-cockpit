"""Tests for the `cockpit-baseline` blueprint (facet B card, §4.2).

Acceptance criteria:
- `BaselineBlueprint.load()` returns a `Blueprint` with the universal skills
  (>=7 entries) and empty agents.
- The YAML is human-readable (<= 80 lines in MVP).
- `BlueprintService(BaselineBlueprint.load()).apply(project)` actually writes
  the skills into `.claude/skills/`, and is idempotent.
"""
from pathlib import Path

from app.services.blueprint import Blueprint, BlueprintService
from app.services.blueprint.baseline import BaselineBlueprint

# The universal skills every product-project must inherit at birth.
EXPECTED_SKILLS = {
    "flag-problem",
    "context-map",
    "session-retro",
    "git-ship",
    "verification-before-completion",
    "brainstorming",
    "writing-plans",
    "using-git-worktrees",
}


def test_load_returns_blueprint():
    bp = BaselineBlueprint.load()
    assert isinstance(bp, Blueprint)
    assert bp.name == "cockpit-baseline"


def test_load_has_expected_universal_skills():
    bp = BaselineBlueprint.load()
    names = {s.name for s in bp.skills}
    assert len(bp.skills) >= 7
    assert EXPECTED_SKILLS.issubset(names)


def test_load_has_no_project_agents():
    """Baseline criterion: no project-owned agents — inherit CC defaults."""
    bp = BaselineBlueprint.load()
    assert bp.agents == []


def test_settings_are_minimal_defaults():
    bp = BaselineBlueprint.load()
    assert bp.settings.permission_mode == "default"
    assert bp.settings.plansDirectory == "~/.claude/plans"


def test_yaml_is_human_readable():
    yaml_path = BaselineBlueprint.yaml_path
    assert isinstance(yaml_path, Path)
    line_count = len(yaml_path.read_text(encoding="utf-8").splitlines())
    assert line_count <= 80, f"baseline YAML is {line_count} lines, expected <= 80"


def test_apply_writes_skills_into_claude_dir(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    audit = BlueprintService(BaselineBlueprint.load()).apply(str(project))

    skills_dir = project / ".claude" / "skills"
    assert skills_dir.is_dir()
    for name in EXPECTED_SKILLS:
        skill_md = skills_dir / name / "SKILL.md"
        assert skill_md.is_file(), f"missing seeded skill {name}"
    assert set(EXPECTED_SKILLS).issubset(set(audit.applied_skills))
    assert audit.skipped_existing is False


def test_apply_is_idempotent(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    BlueprintService(BaselineBlueprint.load()).apply(str(project))
    audit2 = BlueprintService(BaselineBlueprint.load()).apply(str(project))

    assert audit2.skipped_existing is True
    assert audit2.written_files == []
    assert not (project / ".claude.tmp").exists()
