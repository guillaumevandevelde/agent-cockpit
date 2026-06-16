# backend/tests/test_spawn_worktree_cleanup.py
from app.services.agent_bridge import spawn as spawnmod


def test_kill_session_removes_dispatcher_worktree(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(cmd)
        class R:
            returncode = 0
            stderr = ""
        return R()

    monkeypatch.setattr(spawnmod.subprocess, "run", fake_run)
    spawnmod._spawned_sessions["k-test-1234"] = {
        "provider": "claude-code",
        "mode": "plain",
        "directory": str(tmp_path / "wt"),
        "worktree_name": None,
        "worktree_path": str(tmp_path / "wt"),
        "repo_path": str(tmp_path / "repo"),
        "platform": "anthropic",
    }
    spawnmod.kill_session("k-test-1234", cleanup_worktree=True)
    removes = [c for c in calls if "worktree" in c and "remove" in c]
    assert removes, "expected a git worktree remove call"
    assert str(tmp_path / "wt") in removes[0]
    assert "-C" in removes[0] and str(tmp_path / "repo") in removes[0]
