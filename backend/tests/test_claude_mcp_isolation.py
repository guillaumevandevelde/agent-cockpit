"""R5: Enforce project-scoped MCP config at spawn time.

Kanban card `00fa8325` / `docs/cockpit/token-optimization-analysis.md` §4 R5.
Without these flags a host-user's global MCP servers (anything they add to
``~/.claude.json``) silently leak into every dispatched session — every
extra tool schema costs tokens and risk. The card mandates:

1. The spawn path must pass ``--strict-mcp-config`` so only explicit
   ``--mcp-config`` files are loaded.
2. The project-``--mcp-config`` must be the project's own ``.mcp.json``.
3. ``cockpit-kanban`` must remain reachable (acceptance criterion #4) —
   the repo's ``.mcp.json`` is the canonical source.
"""
import json
import os
from pathlib import Path


def _last_token(argv: list[str]) -> str:
    """The shell command is the last element of the tmux new-session argv."""
    return argv[-1]


def _argv_tokens(shell_command: str) -> list[str]:
    """Split the tmux shell_command into its argv tokens.

    shlex.split treats the entire string as a shell expression; for our
    fixed-shape ``claude ...`` invocations that's exactly what we want
    (each flag is a separate token, no shell metacharacters to worry
    about at this layer).
    """
    import shlex
    return shlex.split(shell_command)


def test_claude_build_spawn_command_passes_strict_mcp_config():
    """``--strict-mcp-config`` must be in every Claude Code spawn command."""
    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("claude-code")
    command = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="plain", prompt="hi")
    )

    assert "--strict-mcp-config" in command, (
        f"claude spawn is missing --strict-mcp-config; without it the host's "
        f"~/.claude.json MCP servers leak into dispatched sessions. cmd={command}"
    )


def test_claude_build_spawn_command_passes_project_mcp_config():
    """``--mcp-config`` must point to the project's ``.mcp.json`` (absolute)."""
    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("claude-code")
    command = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="plain", prompt="hi")
    )

    assert "--mcp-config" in command, (
        f"--strict-mcp-config without --mcp-config means zero MCP servers; "
        f"we'd break cockpit-kanban. cmd={command}"
    )
    idx = command.index("--mcp-config")
    mcp_path = command[idx + 1]
    # Absolute path so cwd changes (worktree, remote host) don't break resolution.
    assert os.path.isabs(mcp_path), f"--mcp-config must be absolute, got {mcp_path!r}"
    assert mcp_path == "/tmp/project/.mcp.json", (
        f"--mcp-config should target the project .mcp.json; got {mcp_path!r}"
    )


def test_claude_strict_mcp_config_present_across_all_modes():
    """Plain, worktree, and resume must all carry the isolation flags."""
    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("claude-code")

    plain_cmd = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="plain")
    )
    worktree_cmd = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="worktree",
                            worktree_name="k-feature-a1b2")
    )
    resume_cmd = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="resume", session_id="sess-1")
    )

    for label, cmd in (("plain", plain_cmd), ("worktree", worktree_cmd), ("resume", resume_cmd)):
        assert "--strict-mcp-config" in cmd, f"{label} cmd missing --strict-mcp-config: {cmd}"
        assert "--mcp-config" in cmd, f"{label} cmd missing --mcp-config: {cmd}"


def test_spawn_session_forwards_strict_mcp_config_into_tmux_command(monkeypatch, tmp_path):
    """End-to-end: spawn_session's tmux new-session argv carries the flags."""
    import app.services.runs.spawn as spawn

    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(spawn, "_session_name_for", lambda directory, preferred=None: "r-abcd")
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()

    from app.services.agentic_cli.base import SpawnCommandOptions
    spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="plain"),
    )

    shell_command = captured["cmd"][-1]
    tokens = _argv_tokens(shell_command)
    assert "--strict-mcp-config" in tokens, (
        f"spawn_session's tmux argv lacks --strict-mcp-config; full={shell_command}"
    )
    assert "--mcp-config" in tokens
    idx = tokens.index("--mcp-config")
    assert tokens[idx + 1] == str(tmp_path / ".mcp.json")


def test_cc_spawn_session_forwards_strict_mcp_config(monkeypatch, tmp_path):
    """Legacy CC bridge: same isolation property."""
    import app.services.runs.cc_spawn as cc_spawn

    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(cc_spawn.subprocess, "run", fake_run)
    cc_spawn.get_spawned_sessions().clear()

    cc_spawn.spawn_session(directory=str(tmp_path), mode="plain")

    shell_command = captured["cmd"][-1]
    tokens = _argv_tokens(shell_command)
    assert "--strict-mcp-config" in tokens, (
        f"cc_spawn argv lacks --strict-mcp-config; full={shell_command}"
    )
    assert "--mcp-config" in tokens
    idx = tokens.index("--mcp-config")
    assert tokens[idx + 1] == str(tmp_path / ".mcp.json")


def test_repo_mcp_json_exposes_cockpit_kanban():
    """Acceptance #4: the project-``.mcp.json`` must still wire up ``cockpit-kanban``.

    We verify the actual file in the worktree root (where Claude Code reads it
    from when the session cwd is the repo) so that if anyone ever strips the
    kanban entry without realising dispatched sessions depend on it, this test
    flags it.
    """
    mcp_path = Path(__file__).resolve().parents[2] / ".mcp.json"
    data = json.loads(mcp_path.read_text(encoding="utf-8"))
    servers = data.get("mcpServers") or {}
    assert "cockpit-kanban" in servers, (
        f"{mcp_path} must keep the cockpit-kanban entry — without it dispatched "
        f"sessions can't reach the kanban MCP and every card breaks. servers={list(servers)}"
    )