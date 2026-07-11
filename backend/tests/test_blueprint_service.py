"""Tests for BlueprintService — the .claude/ seeding engine.

Acceptance criteria from facet A sibling #4 (card 395590d7) and the
`create_project_from_intake` card (0260dbcd):
- `apply()` creates settings.json, the declared subdirs, optional CLAUDE.md
- `apply()` is atomic: a write failure leaves the project without a
  half-written `.claude/`
- `apply()` is idempotent: rerunning on a populated `.claude/` is a no-op
- `apply(force=True)` overwrites
"""
import json

import pytest

from app.services.blueprint import (
    AuditResult,
    Blueprint,
    BlueprintApplyFailed,
    BlueprintService,
    apply_blueprint,
)


def test_apply_writes_settings_and_subdirs(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    audit = apply_blueprint(str(project))

    claude = project / ".claude"
    assert claude.is_dir()
    settings = claude / "settings.json"
    assert settings.is_file()
    assert json.loads(settings.read_text()) == {"permissions": {"defaultMode": "default"}}
    # Subdirs declared by the default blueprint all materialise with a .gitkeep
    for name in ["commands", "agents", "hooks", "skills", "plugins", "output-styles"]:
        sub = claude / name
        assert sub.is_dir(), f"missing subdir {name}"
        assert (sub / ".gitkeep").exists()
    # Default blueprint ships a CLAUDE.md stub, so the audit lists both
    # the in-claude config and the sibling CLAUDE.md file.
    assert audit.written_files == [".claude/settings.json", "CLAUDE.md"]
    assert audit.skipped_existing is False


def test_apply_writes_claudemd_when_supplied(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    bp = Blueprint(claudemd="Hello\nProject context\n")
    BlueprintService(bp).apply(str(project))

    assert (project / "CLAUDE.md").read_text() == "Hello\nProject context\n"


def test_apply_skips_claudemd_when_none(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    BlueprintService(Blueprint(claudemd=None)).apply(str(project))

    assert not (project / "CLAUDE.md").exists()


def test_apply_is_idempotent_on_populated_claude(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    BlueprintService().apply(str(project))
    settings_after_first = (project / ".claude" / "settings.json").read_text()

    audit2 = apply_blueprint(str(project))
    # Same content; nothing rewritten, no leftover `.claude.tmp` left behind.
    assert (project / ".claude" / "settings.json").read_text() == settings_after_first
    assert not (project / ".claude.tmp").exists()
    assert audit2.skipped_existing is True
    # Nothing recorded as "written" on the second call.
    assert audit2.written_files == []


def test_apply_on_already_populated_claude_skips_silently(tmp_path):
    """Idempotent on a populated `.claude/`: silently skip, leave user
    content untouched. Distinct from `force=True`, which overwrites."""
    project = tmp_path / "proj"
    project.mkdir()

    pre = project / ".claude"
    pre.mkdir()
    (pre / "settings.json").write_text("user-owned")

    audit = apply_blueprint(str(project))
    assert audit.skipped_existing is True
    assert (pre / "settings.json").read_text() == "user-owned"


def test_apply_on_empty_claude_dir_still_seeds(tmp_path):
    """An empty `.claude/` marker doesn't count as "populated" — first apply
    still seeds the directory. This matters when a user manually creates
    `.claude/` with `mkdir` before invoking `apply`."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".claude").mkdir()

    audit = apply_blueprint(str(project))
    assert audit.skipped_existing is False
    assert (project / ".claude" / "settings.json").is_file()


def test_apply_force_overwrites(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    BlueprintService().apply(str(project))
    (project / ".claude" / "settings.json").write_text("user-owned")

    BlueprintService().apply(str(project), force=True)
    assert json.loads((project / ".claude" / "settings.json").read_text()) == {
        "permissions": {"defaultMode": "default"},
    }


def test_apply_cleans_up_on_write_failure(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()

    # Force a failure after the subdirs land but before commit. The tmp
    # staging dir must be cleaned up so a half-written `.claude/` never
    # lands at the final location.
    real_subdir = BlueprintService._create_subdirs

    def boom(self, staged_dir, audit):
        real_subdir(self, staged_dir, audit)
        raise RuntimeError("simulated post-stage crash")

    monkeypatch.setattr(BlueprintService, "_create_subdirs", boom)

    with pytest.raises(BlueprintApplyFailed) as exc_info:
        apply_blueprint(str(project))

    assert exc_info.value.step == "stage"
    assert not (project / ".claude").exists()
    assert not (project / ".claude.tmp").exists()


def test_apply_cleans_up_stray_tmp_dir_from_prior_crash(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".claude.tmp").mkdir()
    (project / ".claude.tmp" / "leftover.txt").write_text("stale")

    apply_blueprint(str(project))

    assert (project / ".claude").is_dir()
    assert not (project / ".claude.tmp").exists()
    assert not (project / ".claude" / "leftover.txt").exists()


def test_apply_returns_audit_result_dataclass(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    audit = apply_blueprint(str(project))
    assert isinstance(audit, AuditResult)
    assert audit.project_path == str(project)
    assert ".claude/settings.json" in audit.written_files
