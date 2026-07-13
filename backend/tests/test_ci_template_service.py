"""Tests for CITemplateService — the .github/workflows/ seeding engine.

Acceptance criteria from kanban card `c66a93a20c0a` (facet D, follow-up #7 of
``docs/cockpit/veilig-bouwen-en-uitleveren.md`` §6):
- `list_templates()` returns all three profiles
- `apply()` writes a workflow file under `.github/workflows/`
- `apply()` is idempotent: rerunning on an existing file is a no-op unless
  `force=True`
- `apply()` honours `force` (overwrites + records it in the audit)
- `apply()` raises `CITemplateError` for an unknown profile
- Each rendered template is *valid YAML* (parseable by `yaml.safe_load`)
- Parameter substitution works (`python_version`, `node_version`, …)
"""
from __future__ import annotations

import logging

import pytest
import yaml

from app.services.ci_templates import (
    CITemplateApplyResult,
    CITemplateError,
    CITemplateInfo,
    CITemplateService,
)

# ---------------------------------------------------------------------------
# list_templates
# ---------------------------------------------------------------------------


def test_list_templates_returns_all_three_profiles():
    profiles = {info.name for info in CITemplateService().list_templates()}
    assert profiles == {"python-strict", "node-strict", "minimal"}


def test_list_templates_returns_citemplate_info_objects():
    info = CITemplateService().list_templates()
    assert len(info) == 3
    for entry in info:
        assert isinstance(entry, CITemplateInfo)
        assert entry.name and entry.description and entry.filename


# ---------------------------------------------------------------------------
# render (private) — every template produces parseable YAML with the right
# parameter substitution
# ---------------------------------------------------------------------------


def test_render_python_strict_substitutes_parameters():
    out = CITemplateService()._render(  # noqa: SLF001 — intentional white-box
        "python-strict",
        python_version="3.12",
        requirements_dev_path="requirements-dev.txt",
    )
    parsed = yaml.safe_load(out)
    # Smoke: top-level workflow shape is intact. Note: `on:` is a YAML 1.1
    # boolean literal so PyYAML parses it as the key `True` — GitHub Actions
    # tolerates both the raw `on:` and the alias. We accept either.
    assert isinstance(parsed, dict)
    assert "jobs" in parsed
    assert "backend" in parsed["jobs"]
    assert "on" in parsed or True in parsed  # YAML 1.1 normalises `on:` → True
    # Parameter substitution lands in the workflow text.
    assert "3.12" in out
    assert "requirements-dev.txt" in out


def test_render_node_strict_substitutes_node_version():
    out = CITemplateService()._render(  # noqa: SLF001
        "node-strict",
        node_version="20",
    )
    parsed = yaml.safe_load(out)
    assert "jobs" in parsed
    assert "frontend" in parsed["jobs"]
    assert "20" in out


def test_render_minimal_produces_valid_yaml():
    out = CITemplateService()._render("minimal")
    parsed = yaml.safe_load(out)
    assert isinstance(parsed, dict)
    assert "jobs" in parsed


def test_render_unknown_profile_raises():
    with pytest.raises(CITemplateError, match="unknown CI profile"):
        CITemplateService()._render("nope")  # noqa: SLF001


# ---------------------------------------------------------------------------
# apply — writes the workflow file under .github/workflows/
# ---------------------------------------------------------------------------


def test_apply_writes_workflow_file(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    result = CITemplateService().apply(
        str(project), "python-strict",
        python_version="3.11",
        requirements_dev_path="requirements-dev.txt",
    )

    wf = project / ".github" / "workflows" / "python-strict.yml"
    assert wf.is_file()
    # Rendered file is valid YAML.
    parsed = yaml.safe_load(wf.read_text())
    assert "jobs" in parsed
    # Audit reports exactly the written file.
    assert isinstance(result, CITemplateApplyResult)
    assert result.profile == "python-strict"
    assert result.project_path == str(project)
    assert result.written_file == ".github/workflows/python-strict.yml"
    assert result.skipped_existing is False
    assert result.force is False


def test_apply_creates_missing_parent_dirs(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    CITemplateService().apply(str(project), "minimal")

    assert (project / ".github" / "workflows" / "minimal.yml").is_file()


# ---------------------------------------------------------------------------
# idempotency + force
# ---------------------------------------------------------------------------


def test_apply_is_idempotent_on_existing_workflow(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    svc = CITemplateService()
    svc.apply(str(project), "minimal")
    first = (project / ".github" / "workflows" / "minimal.yml").read_text()

    result = svc.apply(str(project), "minimal")
    assert result.skipped_existing is True
    assert result.written_file is None
    # Same on-disk content — nothing rewritten.
    assert (project / ".github" / "workflows" / "minimal.yml").read_text() == first


def test_apply_force_overwrites_existing_workflow(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    svc = CITemplateService()
    svc.apply(str(project), "minimal")
    # Tamper so we can detect the overwrite.
    wf = project / ".github" / "workflows" / "minimal.yml"
    wf.write_text("tampered: yes\n")

    result = svc.apply(str(project), "minimal", force=True)
    assert result.skipped_existing is False
    assert result.written_file == ".github/workflows/minimal.yml"
    assert result.force is True
    assert "tampered" not in wf.read_text()


def test_apply_records_force_in_audit_log(tmp_path, caplog):
    project = tmp_path / "proj"
    project.mkdir()

    svc = CITemplateService()
    svc.apply(str(project), "minimal")  # prime the file

    with caplog.at_level(logging.INFO, logger="app.services.ci_templates"):
        svc.apply(str(project), "minimal", force=True)

    # The audit log line explicitly mentions `force=True`.
    assert any(
        "force=True" in rec.message for rec in caplog.records
    ), f"expected audit log line containing force=True, got: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------


def test_apply_unknown_profile_raises(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    with pytest.raises(CITemplateError, match="unknown CI profile"):
        CITemplateService().apply(str(project), "nope")