"""Task 6b: spawn_session forwards extra_args into the tmux command.

Targets the LEGACY cc-bridge spawn (`runs.cc_spawn`), which is what still takes
the flat `directory=/mode=/extra_args=` kwargs and is still routed from
`api/v1/cc_bridge/router.py` and `scheduling/session_resolver.py`. The newer
`runs.spawn.spawn_session` takes `(cli_id, SpawnCommandOptions)` and has no
`extra_args` escape hatch — permissions moved to the structured
`skip_permissions` / `permission_prompt_tool` fields.

`cc_spawn` is imported inside each test rather than at module scope:
`agentic_cli.claude_code` imports back from `cc_spawn`, so importing it as the
very first module in a fresh interpreter hits that cycle
("partially initialized module"). Deferring to call time lets the normal import
order settle first — the same pattern the other runs tests use.
"""


class _Result:
    returncode = 0
    stderr = ""


def test_spawn_includes_extra_args(monkeypatch):
    import app.services.runs.cc_spawn as sp

    captured = {}

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
    import app.services.runs.cc_spawn as sp

    captured = {}

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
