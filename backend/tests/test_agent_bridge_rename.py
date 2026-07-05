"""Tests for renaming an Agent Bridge tmux session."""
from types import SimpleNamespace


def test_rename_session_renames_tmux_and_moves_metadata(monkeypatch):
    from app.services.agent_bridge import spawn

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_running_session_names", lambda: {"old-name"})
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()
    spawn.get_spawned_sessions()["old-name"] = {"provider": "claude-code", "mode": "worktree"}

    result = spawn.rename_session("old-name", "New Name!")

    assert result == {"renamed": True, "session_name": "New-Name", "tmux_target": "New-Name:0.0"}
    assert calls[0] == ["tmux", "rename-session", "-t", "old-name", "New-Name"]
    assert "old-name" not in spawn.get_spawned_sessions()
    assert spawn.get_spawned_sessions()["New-Name"]["mode"] == "worktree"


def test_rename_session_rejects_empty_name(monkeypatch):
    import pytest

    from app.services.agent_bridge import spawn

    monkeypatch.setattr(spawn, "_running_session_names", lambda: set())

    with pytest.raises(ValueError):
        spawn.rename_session("old-name", "---")


def test_rename_session_rejects_collision(monkeypatch):
    import pytest

    from app.services.agent_bridge import spawn

    monkeypatch.setattr(spawn, "_running_session_names", lambda: {"old-name", "taken"})

    with pytest.raises(ValueError):
        spawn.rename_session("old-name", "taken")
