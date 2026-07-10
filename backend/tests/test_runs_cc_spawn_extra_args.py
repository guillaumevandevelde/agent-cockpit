"""Task 6b: spawn_session forwards extra_args into the tmux command."""
import app.services.runs.spawn as sp


def test_spawn_includes_extra_args(monkeypatch):
    captured = {}

    class _Result:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    sp.spawn_session(directory="/tmp", mode="plain",
                     extra_args=["--permission-mode", "acceptEdits"])
    # tmux new-session ... '<shell_command>' — last element is the joined command
    shell_command = captured["cmd"][-1]
    assert "--permission-mode acceptEdits" in shell_command


def test_spawn_sanitizes_dirty_worktree_name(monkeypatch, tmp_path):
    captured = {}

    class _Result:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    result = sp.spawn_session(directory=str(tmp_path), mode="worktree",
                              worktree_name="feature/foo bar")

    shell_command = captured["cmd"][-1]
    assert "--worktree feature/foo-bar" in shell_command
    assert result["worktree_name"] == "feature/foo-bar"
    assert result["worktree_name_adjusted"] is True
    stored = sp.get_spawned_sessions()[result["session_name"]]
    assert stored["worktree_name"] == "feature/foo-bar"
