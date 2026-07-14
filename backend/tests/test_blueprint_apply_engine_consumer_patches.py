"""Consumer-side patching exemplar for ``BlueprintApplyEngine``.

Kanban card 4bee45e2ccb541bdadbc0beddab0ff3a closed: ``apply_engine.py`` now
imports its CRUD dependencies at module top, so patching follows the same
convention as every other service in this repo — patch at the **consumer**
module path (``app.services.blueprint.apply_engine``), not the source module
the class lives in. This file is the post-fix reference for that pattern:

- A test author following only the rest of the repo's testing style
  (top-level ``from X import Y`` → patch ``module.X``) writes the obvious
  ``patch("app.services.blueprint.apply_engine.ConfigService")`` and it
  works on the first try, with no source-diving into ``apply_engine.py``
  to figure out a special-case patch path.

It also adds coverage cases the original test file does not exercise (all
listed agents in a single blueprint, idempotency across re-applies, the
JSON-validation stage's failure path).
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
)


def _project(tmp_path: Path, name: str = "proj") -> Path:
    p = tmp_path / name
    p.mkdir()
    return p


def _bp_with_two_agents_and_a_skill() -> Blueprint:
    """A blueprint exercising settings, two agents, one skill, statusline
    and CLAUDE.md — wider coverage than the original test file's
    single-agent blueprint. (Note: ``_apply_skills`` does not gate on
    idempotency — it always invokes ``install_skill`` so the registry
    can decide; see the docstring in ``apply_engine.py``. Tests below
    factor around that.)
    """
    return Blueprint(
        name="full",
        settings=BlueprintSettings(permission_mode="plan", model="opus"),
        agents=[
            BlueprintAgent(name="planner", model_default="opus", tools=["Read"]),
            BlueprintAgent(name="builder", model_default="sonnet", tools=["Write"]),
        ],
        skills=[BlueprintSkill(name="frontend", source="project")],
        statusline='#!/bin/sh\necho opus\n',
        claudemd="# project\n",
    )


# ---------------------------------------------------------------------------
# Convention demo — every patch targets the consumer module
# ---------------------------------------------------------------------------


def test_consumer_side_patches_land_on_every_crud_path(tmp_path):
    """Writing a fresh test for ``BlueprintApplyEngine``, the natural
    patching pattern (``patch("...apply_engine.<Class>.<method>")``) hits
    every CRUD service the engine routes through. If the imports regress
    to lazy inline ``from ..X import Y``, every patch in this test starts
    silently missing the call site and ``mock_calls`` comes back empty —
    no source-diving required to diagnose.
    """
    project = _project(tmp_path)

    # Each lambda records that the engine reached it via its expected call
    # shape. The real services are not imported or invoked — keeping the
    # test hermetic and fast.
    config_calls: list[Any] = []
    agent_calls: list[Any] = []
    skill_calls: list[Any] = []

    def fake_update_settings(*, scope, settings, project_path):
        config_calls.append({"scope": scope, "project_path": project_path})
        target = Path(project_path) / ".claude" / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(settings))
        return {"success": True, "path": str(target)}

    def fake_create_agent(agent, project_path):
        agent_calls.append((agent.name, project_path))
        target = (
            Path(project_path) / ".claude" / "agents" / f"{agent.name}.md"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(agent.prompt or "")
        return MagicMock(name=agent.name)

    def fake_install_skill(*, source, skill_names, global_install, project_path):
        skill_calls.append({"names": skill_names, "project_path": project_path})
        return {"success": True, "message": "ok"}

    # The patch targets — EVERY ONE of these is the consumer module path
    # ``app.services.blueprint.apply_engine``. If a test author writes
    # this verbatim, it works.
    with patch(
        "app.services.blueprint.apply_engine.ConfigService",
        autospec=True,
    ) as mock_cfg, patch(
        "app.services.blueprint.apply_engine.AgentService",
    ) as mock_agent, patch(
        "app.services.blueprint.apply_engine.SkillsRegistryService",
    ) as mock_skill:
        mock_cfg.return_value.update_settings.side_effect = fake_update_settings
        mock_agent.create_agent.side_effect = fake_create_agent
        mock_skill.install_skill.side_effect = fake_install_skill

        audit = BlueprintApplyEngine().apply(
            project_path=str(project),
            blueprint=_bp_with_two_agents_and_a_skill(),
        )

    # One settings write, two agents, one skill — every CRUD path was
    # reached.
    assert len(config_calls) == 1
    assert [n for n, _ in agent_calls] == ["planner", "builder"]
    assert skill_calls and skill_calls[0]["names"] == ["frontend"]

    # Every CRUD was called with a **staging** project_path — never the
    # real project root, so a failure during apply can't trash the
    # project's `.claude/`.
    staging_marker = ".claude.staging-"
    assert all(
        staging_marker in Path(c["project_path"]).name for c in config_calls
    )
    assert all(
        staging_marker in Path(p).name for _, p in agent_calls
    )
    assert all(
        staging_marker in Path(c["project_path"]).name for c in skill_calls
    )

    # Final files (post-commit) are on disk at the real project root.
    assert (project / ".claude" / "settings.json").is_file()
    assert (project / ".claude" / "agents" / "planner.md").is_file()
    assert (project / ".claude" / "agents" / "builder.md").is_file()
    assert isinstance(audit, AuditResult)
    assert audit.errors == []


# ---------------------------------------------------------------------------
# Idempotency: a second apply sees identical content everywhere
# ---------------------------------------------------------------------------


def test_second_apply_is_pure_skip_audit(tmp_path):
    """A second ``apply`` over a freshly-applied project must NOT call any
    settings/agents CRUD — every per-item content check is
    ``exists_with_identical_content`` and the audit's ``skipped`` lists
    every target. (``install_skill`` *is* always invoked regardless; the
    registry owns re-install semantics — see ``_apply_skills`` docstring.)
    """
    project = _project(tmp_path)
    bp = _bp_with_two_agents_and_a_skill()

    with patch(
        "app.services.blueprint.apply_engine.ConfigService",
        autospec=True,
    ) as mock_cfg, patch(
        "app.services.blueprint.apply_engine.AgentService",
    ) as mock_agent, patch(
        "app.services.blueprint.apply_engine.SkillsRegistryService",
    ) as mock_skill:
        def settings_write(*, scope, settings, project_path):
            target = Path(project_path) / ".claude" / "settings.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(settings))
            return {"success": True, "path": str(target)}

        def agent_write(agent, project_path):
            target = (
                Path(project_path) / ".claude" / "agents" / f"{agent.name}.md"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(agent.prompt or "")
            return MagicMock(name=agent.name)

        def skill_write(*, source, skill_names, global_install, project_path):
            return {"success": True, "message": "ok"}

        mock_cfg.return_value.update_settings.side_effect = settings_write
        mock_agent.create_agent.side_effect = agent_write
        mock_skill.install_skill.side_effect = skill_write

        BlueprintApplyEngine().apply(project_path=str(project), blueprint=bp)

        # Reset call counts for the idempotency-gated services only.
        mock_cfg.return_value.update_settings.reset_mock()
        mock_agent.create_agent.reset_mock()

        # Re-apply over a populated project.
        audit = BlueprintApplyEngine().apply(
            project_path=str(project), blueprint=bp,
        )

    # Settings + agents CRUD was NOT fired on the second pass.
    mock_cfg.return_value.update_settings.assert_not_called()
    mock_agent.create_agent.assert_not_called()

    # Every idempotency-gated target was skipped (skills aren't gated,
    # but the skill is the one that survives into written_files).
    skip_targets = {s.target for s in audit.skipped}
    assert ".claude/settings.json" in skip_targets
    assert ".claude/agents/planner.md" in skip_targets
    assert ".claude/agents/builder.md" in skip_targets
    # Gated writes left NO entry in written_files; only the un-gated skill
    # lands there.
    gated_writes = [
        f for f in audit.written_files
        if not f.endswith("skills/frontend/SKILL.md")
    ]
    assert gated_writes == []
    assert ".claude/skills/frontend/SKILL.md" in audit.written_files


# ---------------------------------------------------------------------------
# JSON-validation stage: corrupt staged settings raises a typed error
# ---------------------------------------------------------------------------


def test_corrupt_staged_settings_raises_blueprint_apply_error(tmp_path):
    """If a CRUD call bypasses its own validation and writes invalid JSON
    into staging, the engine's ``_validate`` step must surface that as a
    ``BlueprintApplyError`` (stage ``"validate-settings"``) rather than
    promoting a known-bad file into the project's ``.claude/``.
    """
    project = _project(tmp_path)

    bp = Blueprint(
        name="corrupt",
        settings=BlueprintSettings(permission_mode="default"),
    )

    def write_broken_settings(*, scope, settings, project_path):
        """Write syntactically-invalid JSON to staging."""
        target = Path(project_path) / ".claude" / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{not valid json")
        return {"success": True, "path": str(target)}

    with patch(
        "app.services.blueprint.apply_engine.ConfigService",
        autospec=True,
    ) as mock_cfg:
        mock_cfg.return_value.update_settings.side_effect = write_broken_settings
        with pytest.raises(BlueprintApplyError) as exc_info:
            BlueprintApplyEngine().apply(
                project_path=str(project), blueprint=bp,
            )

    # The error stages clearly — not the generic "apply" tag.
    assert exc_info.value.stage == "validate-settings"
    assert isinstance(exc_info.value.cause, json.JSONDecodeError)

    # No half-written `.claude/` ever landed at the real project root.
    assert not (project / ".claude").exists()
