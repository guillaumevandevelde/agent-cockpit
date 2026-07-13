"""Tests for BlueprintApplyEngine — the orchestrator that materialises a
`Blueprint` into a project's `.claude/` by routing through existing CRUD
services (ConfigService, AgentService, SkillsRegistryService,
CommandService) plus direct file writes for statusline + CLAUDE.md.

Acceptance criteria from kanban card
`b0d44c8d226e43f7b286f8712f0a87d6` (facet B sibling #4):

- Happy path with all fields filled → `AuditResult.written` lists each
  written item; the corresponding files are on disk.
- Existing `.claude/settings.json` with conflicting content is NOT
  overwritten; recorded in `skipped` with reason.
- Existing agent file with identical content → skip + log.
- A write failure halfway through (e.g. an agent `create` raises) →
  raise `BlueprintApplyError`; NO files land in `<project>/.claude/`.
- `force=True` overwrites conflicting content with audit trail.
- Existing CRUD functions are called **without** signature changes.

Note: the engine routes writes through the project-level CRUD services.
To keep tests fast and avoid subprocess / `npx skills add` we **monkeypatch
the underlying services** rather than spinning up real ones.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.blueprint import (
    Blueprint,
    BlueprintAgent,
    BlueprintSettings,
    BlueprintSkill,
)
from app.services.blueprint.apply_engine import (
    AuditResult,
    BlueprintApplyEngine,
    BlueprintApplyError,
    BlueprintAuditSkipped,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path) -> Path:
    """Create an empty project directory and return its path."""
    project = tmp_path / "proj"
    project.mkdir()
    return project


def _make_blueprint_with_agents() -> Blueprint:
    """A blueprint that exercises settings, agents, statusline, and CLAUDE.md.

    Skills and commands are intentionally left out so the happy-path test
    focuses on the parts that aren't routed through `npx skills add` /
    command-CRUD (those are covered by separate tests).
    """
    return Blueprint(
        name="with-agents",
        settings=BlueprintSettings(permission_mode="plan", model="opus"),
        agents=[
            BlueprintAgent(
                name="planner",
                model_default="opus",
                tools=["Read", "Glob"],
            ),
        ],
        statusline='#!/bin/sh\necho opus\n',
        claudemd="Hello\nProject context\n",
    )


# ---------------------------------------------------------------------------
# Acceptance #1 — happy path with all fields filled
# ---------------------------------------------------------------------------


def test_apply_happy_path_writes_all_artifacts(tmp_path):
    project = _make_project(tmp_path)

    audit = BlueprintApplyEngine().apply(
        project_path=str(project),
        blueprint=_make_blueprint_with_agents(),
    )

    # settings.json — on disk at the final path (not staging)
    settings_path = project / ".claude" / "settings.json"
    assert settings_path.is_file()
    on_disk = json.loads(settings_path.read_text())
    assert on_disk["permissions"]["defaultMode"] == "plan"
    assert on_disk["model"] == "opus"

    # agent — routed via AgentService.create_agent; the resulting file lives
    # under `.claude/agents/<name>.md` next to the project.
    agent_path = project / ".claude" / "agents" / "planner.md"
    assert agent_path.is_file()

    # statusline — direct file write at .claude/statusline (no CRUD service).
    statusline_path = project / ".claude" / "statusline"
    assert statusline_path.is_file()
    assert statusline_path.read_text().startswith("#!/bin/sh")

    # CLAUDE.md — sibling of .claude/, NOT inside it.
    claudemd_path = project / "CLAUDE.md"
    assert claudemd_path.is_file()
    assert claudemd_path.read_text() == "Hello\nProject context\n"

    # No staging leftovers.
    assert not any(project.glob(".claude.staging-*"))

    # Audit reflects what landed.
    assert isinstance(audit, AuditResult)
    assert audit.blueprint_name == "with-agents"
    assert ".claude/settings.json" in audit.written_files
    assert ".claude/agents/planner.md" in audit.written_files
    assert ".claude/statusline" in audit.written_files
    assert "CLAUDE.md" in audit.written_files
    assert audit.errors == []
    assert audit.skipped == []


def test_apply_staging_dir_is_promoted_atomically_at_end(tmp_path):
    """During apply, files live under a `.claude.staging-<uuid>/` directory.
    Once every CRUD step succeeded, that staging tree is renamed into
    `<project>/.claude/` so the final state has no `.claude.staging-*`
    dir anywhere on disk.
    """
    project = _make_project(tmp_path)

    # We don't have visibility mid-apply without monkey-patching, so we
    # capture the directory the engine wrote to by intercepting CRUD.
    captured: dict[str, Any] = {}

    def fake_update_settings(*, scope, settings, project_path):  # noqa: D401
        captured["settings_project_path"] = project_path
        # Reflect what the real call would have done: write settings.json
        # under `<project_path>/.claude/settings.json`.
        target = Path(project_path) / ".claude" / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(settings))
        return {"success": True, "path": str(target)}

    bp = _make_blueprint_with_agents()
    with patch(
        "app.services.config_service.ConfigService",
        autospec=True,
    ) as mock_cfg:
        mock_cfg.return_value.update_settings.side_effect = fake_update_settings
        # Suppress other services — happy path doesn't touch them.
        audit = BlueprintApplyEngine().apply(
            project_path=str(project),
            blueprint=bp,
        )

    # The CRUD was called with a *staging* project_path — never with the
    # real `<project>/.claude/`. Writes only landed there during apply.
    staging_path_used = captured["settings_project_path"]
    assert staging_path_used != str(project)
    assert ".claude.staging-" in Path(staging_path_used).name

    # And after apply, the staging dir is gone (atomic promotion done).
    assert not any(Path(staging_path_used).parent.glob(".claude.staging-*"))
    assert (project / ".claude" / "settings.json").is_file()
    assert audit.errors == []


# ---------------------------------------------------------------------------
# Acceptance #2 — existing settings.json with conflicting content not overwritten
# ---------------------------------------------------------------------------


def test_apply_existing_settings_with_conflict_is_skipped(tmp_path):
    project = _make_project(tmp_path)
    claude = project / ".claude"
    claude.mkdir()
    conflicting = '{"permissions": {"defaultMode": "bypassPermissions"}}'
    (claude / "settings.json").write_text(conflicting)

    bp = Blueprint(
        name="conflicting",
        settings=BlueprintSettings(permission_mode="plan"),
    )
    audit = BlueprintApplyEngine().apply(
        project_path=str(project),
        blueprint=bp,
    )

    # File untouched on disk — the engine refused to overwrite.
    assert (claude / "settings.json").read_text() == conflicting

    # And the conflict is recorded in `audit.skipped`.
    assert any(
        s.target == ".claude/settings.json"
        and s.reason == "exists_with_different_content"
        for s in audit.skipped
    ), audit.skipped


# ---------------------------------------------------------------------------
# Acceptance #3 — existing agent file with identical content is skipped
# ---------------------------------------------------------------------------


def test_apply_existing_identical_agent_is_skipped(tmp_path):
    project = _make_project(tmp_path)
    agents_dir = project / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    # Write the exact stub body the engine would have produced via
    # AgentService.create_agent — so content equality holds and the
    # engine must skip the write, never firing the CRUD call.
    body = "# planner\n\nAdd agent instructions for Claude Code here.\n"
    (agents_dir / "planner.md").write_text(body)

    bp = Blueprint(
        name="identical-agent",
        agents=[BlueprintAgent(name="planner", model_default="opus", tools=["Read"])],
    )

    # Stub out AgentService.create_agent to never fire — if it does fire,
    # the stub would refuse ("Agent already exists") which we don't want
    # for this test; identical content should be detected *before* CRUD.
    with patch(
        "app.services.agent_service.AgentService.create_agent",
        return_value=MagicMock(name="planner"),
    ) as create_agent:
        audit = BlueprintApplyEngine().apply(
            project_path=str(project),
            blueprint=bp,
        )

    # CRUD was NOT called for the matching identical agent.
    create_agent.assert_not_called()

    # Audit shows the agent skipped with the "identical" reason, not
    # "exists_with_different_content".
    skip = next(
        (s for s in audit.skipped if s.target == ".claude/agents/planner.md"),
        None,
    )
    assert skip is not None
    assert skip.reason == "exists_with_identical_content"


# ---------------------------------------------------------------------------
# Acceptance #4 — write failure mid-pipeline triggers rollback
# ---------------------------------------------------------------------------


def test_apply_rolls_back_when_agent_create_raises(tmp_path):
    project = _make_project(tmp_path)

    bp = Blueprint(
        name="mid-failure",
        settings=BlueprintSettings(permission_mode="default"),
        agents=[BlueprintAgent(name="broken", model_default="opus")],
    )

    settings_writes: list[Path] = []

    def fake_update_settings(*, scope, settings, project_path):
        target = Path(project_path) / ".claude" / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(settings))
        settings_writes.append(target)
        return {"success": True, "path": str(target)}

    def boom_create_agent(*args, **kwargs):
        raise RuntimeError("simulated agent-create failure")

    with patch(
        "app.services.config_service.ConfigService",
        autospec=True,
    ) as mock_cfg, patch(
        "app.services.agent_service.AgentService.create_agent",
        side_effect=boom_create_agent,
    ):
        mock_cfg.return_value.update_settings.side_effect = fake_update_settings
        with pytest.raises(BlueprintApplyError) as exc_info:
            BlueprintApplyEngine().apply(
                project_path=str(project),
                blueprint=bp,
            )

    # The error stages clearly.
    assert exc_info.value.stage == "agents"
    assert isinstance(exc_info.value.cause, RuntimeError)

    # NO file ever landed at the final `.claude/` path.
    assert not (project / ".claude").exists()

    # The settings write happened in staging, but that staging dir is
    # gone (rm -rf'd on failure).
    if settings_writes:
        for staged in settings_writes:
            assert not staged.exists()


# ---------------------------------------------------------------------------
# Acceptance #5 — force=True overwrites conflicting content with audit trail
# ---------------------------------------------------------------------------


def test_apply_force_overwrites_conflicting_settings(tmp_path):
    project = _make_project(tmp_path)
    claude = project / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text("{}")

    bp = Blueprint(
        name="force",
        settings=BlueprintSettings(permission_mode="plan"),
    )

    def fake_update_settings(*, scope, settings, project_path):
        target = Path(project_path) / ".claude" / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(settings))
        return {"success": True, "path": str(target)}

    with patch(
        "app.services.config_service.ConfigService",
        autospec=True,
    ) as mock_cfg:
        mock_cfg.return_value.update_settings.side_effect = fake_update_settings
        audit = BlueprintApplyEngine().apply(
            project_path=str(project),
            blueprint=bp,
            force=True,
        )

    # File overwritten with the new blueprint content.
    on_disk = json.loads((claude / "settings.json").read_text())
    assert on_disk["permissions"]["defaultMode"] == "plan"

    # Audit shows the write, NOT a skip.
    assert ".claude/settings.json" in audit.written_files
    assert not any(
        s.target == ".claude/settings.json" for s in audit.skipped
    )


# ---------------------------------------------------------------------------
# Per-field nil behavior
# ---------------------------------------------------------------------------


def test_apply_with_only_settings_writes_nothing_else(tmp_path):
    """A minimal blueprint (only settings, no agents/skills/commands/
    statusline/CLAUDE.md) only writes settings.json."""
    project = _make_project(tmp_path)

    bp = Blueprint(
        name="minimal",
        settings=BlueprintSettings(permission_mode="default"),
    )
    audit = BlueprintApplyEngine().apply(
        project_path=str(project),
        blueprint=bp,
    )

    assert audit.written_files == [".claude/settings.json"]
    assert audit.skipped == []
    assert audit.errors == []
    assert not (project / "CLAUDE.md").exists()
    assert not (project / ".claude" / "statusline").exists()


def test_apply_skips_claudemd_when_none(tmp_path):
    project = _make_project(tmp_path)

    bp = Blueprint(
        name="no-md",
        settings=BlueprintSettings(permission_mode="default"),
        claudemd=None,
    )
    audit = BlueprintApplyEngine().apply(
        project_path=str(project),
        blueprint=bp,
    )

    assert not (project / "CLAUDE.md").exists()
    assert "CLAUDE.md" not in audit.written_files


def test_apply_skips_statusline_when_none(tmp_path):
    project = _make_project(tmp_path)

    bp = Blueprint(
        name="no-statusline",
        settings=BlueprintSettings(permission_mode="default"),
    )
    audit = BlueprintApplyEngine().apply(
        project_path=str(project),
        blueprint=bp,
    )

    assert not (project / ".claude" / "statusline").exists()
    assert not any(f.endswith("statusline") for f in audit.written_files)


# ---------------------------------------------------------------------------
# Stray staging dir from prior crash
# ---------------------------------------------------------------------------


def test_apply_cleans_stray_staging_dir_from_prior_crash(tmp_path):
    project = _make_project(tmp_path)
    stale = project / ".claude.staging-deadbeef"
    stale.mkdir()
    (stale / "leftover.txt").write_text("stale")

    bp = Blueprint(
        name="after-crash",
        settings=BlueprintSettings(permission_mode="default"),
    )
    BlueprintApplyEngine().apply(
        project_path=str(project),
        blueprint=bp,
    )

    # Stale staging dir was scrubbed; the new apply produced a clean state.
    assert not stale.exists()
    assert (project / ".claude" / "settings.json").is_file()


# ---------------------------------------------------------------------------
# Engine shape
# ---------------------------------------------------------------------------


def test_engine_returns_audit_result_with_written_and_skipped_lists(tmp_path):
    """The audit's `written_files`, `skipped`, and `errors` lists are
    the canonical interface for callers; this pins their presence and
    shape so downstream UI work can rely on it.
    """
    project = _make_project(tmp_path)

    bp = Blueprint(
        name="shape",
        settings=BlueprintSettings(permission_mode="default"),
    )
    audit = BlueprintApplyEngine().apply(
        project_path=str(project),
        blueprint=bp,
    )

    assert isinstance(audit, AuditResult)
    assert isinstance(audit.written_files, list)
    assert isinstance(audit.skipped, list)
    assert isinstance(audit.errors, list)
    for entry in audit.skipped:
        assert isinstance(entry, BlueprintAuditSkipped)
        assert entry.reason  # never empty — each skip has a reason tag


def test_engine_skips_actual_skill_install_when_blueprint_has_no_skills(tmp_path):
    """If the blueprint declares zero skills, `install_skill` is never
    called — important for tests that need to avoid `npx skills add`
    going over the network.
    """
    project = _make_project(tmp_path)

    bp = Blueprint(
        name="no-skills",
        settings=BlueprintSettings(permission_mode="default"),
    )
    with patch(
        "app.services.skills_registry_service.SkillsRegistryService.install_skill",
    ) as install_skill:
        BlueprintApplyEngine().apply(
            project_path=str(project),
            blueprint=bp,
        )

    install_skill.assert_not_called()


def test_engine_does_not_create_command_when_blueprint_has_no_commands(tmp_path):
    project = _make_project(tmp_path)

    bp = Blueprint(
        name="no-commands",
        settings=BlueprintSettings(permission_mode="default"),
    )
    with patch(
        "app.services.command_service.CommandService.create_command",
    ) as create_command:
        BlueprintApplyEngine().apply(
            project_path=str(project),
            blueprint=bp,
        )

    create_command.assert_not_called()


def test_skill_install_calls_registry_with_correct_args(tmp_path):
    """A skill declared in the blueprint invokes `install_skill` with
    global_install=False and the per-skill name list. The `project_path`
    must point at the engine's **staging** path, never the real project
    root, so a failed install doesn't trash real artefacts.
    """
    project = _make_project(tmp_path)

    bp = Blueprint(
        name="with-skill",
        settings=BlueprintSettings(permission_mode="default"),
        skills=[
            BlueprintSkill(name="frontend", source="project"),
        ],
    )

    captured: dict[str, Any] = {}

    def fake_install_skill(*, source, skill_names, global_install, project_path):
        captured["source"] = source
        captured["skill_names"] = skill_names
        captured["global_install"] = global_install
        captured["project_path"] = project_path
        return {"success": True, "message": "ok"}

    with patch(
        "app.services.skills_registry_service.SkillsRegistryService.install_skill",
        side_effect=fake_install_skill,
    ):
        BlueprintApplyEngine().apply(
            project_path=str(project),
            blueprint=bp,
        )

    assert captured["global_install"] is False
    assert captured["skill_names"] == ["frontend"]
    assert ".claude.staging-" in Path(captured["project_path"]).name
