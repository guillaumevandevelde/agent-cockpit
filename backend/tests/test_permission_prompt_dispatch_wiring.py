# backend/tests/test_permission_prompt_dispatch_wiring.py
"""
Dispatch-side tests for the --permission-prompt-tool wiring.

Acceptance criterion AC2 (kanban card 5278a5bd625d45beb6ab7c8bd9b7eb19):
  Dispatch passes --permission-prompt-tool only when skip_permissions=False;
  for meta (skip_permissions=True) the spawn stays unchanged.

Three layers are tested in isolation so a regression in one does not mask
another:

  1. SpawnCommandOptions carries the field and ClaudeCodeCli emits the flag.
  2. make_worktree_transport wires the tool when skip_permissions=False.
  3. make_worktree_transport leaves the spawn untouched when skip_permissions=True
     (the meta-project path — must not change behaviour for the kanban repo).
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

# --- Layer 1 — argv builder emits the flag iff a tool name is set. ---------


def test_claude_code_argv_includes_permission_prompt_tool_when_set(tmp_path):
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.agentic_cli.claude_code import ClaudeCodeCli

    command = ClaudeCodeCli().build_spawn_command(SpawnCommandOptions(
        directory=str(tmp_path),
        mode="plain",
        permission_prompt_tool="mcp__cockpit-kanban__permission_prompt",
    ))
    # The flag lives in argv (no = form) per Anthropic's standard two-arg shape.
    assert "--permission-prompt-tool" in command
    assert "mcp__cockpit-kanban__permission_prompt" in command


def test_claude_code_argv_omits_permission_prompt_tool_when_unset(tmp_path):
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.agentic_cli.claude_code import ClaudeCodeCli

    command = ClaudeCodeCli().build_spawn_command(SpawnCommandOptions(
        directory=str(tmp_path), mode="plain",
    ))
    assert "--permission-prompt-tool" not in command


def test_claude_code_argv_with_skip_permissions_true_does_not_force_permission_prompt(tmp_path):
    """skip_permissions=True is the meta lane: the agent bypasses prompts, so
    no permission-prompt-tool flag should appear. The caller decides; the
    builder does not auto-derive one."""
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.agentic_cli.claude_code import ClaudeCodeCli

    command = ClaudeCodeCli().build_spawn_command(SpawnCommandOptions(
        directory=str(tmp_path), mode="plain",
        skip_permissions=True,
        permission_prompt_tool=None,
    ))
    assert "--dangerously-skip-permissions" in command
    assert "--permission-prompt-tool" not in command


# --- Layer 2 — worktree transport wires the tool only when skip=False. -----


@pytest.fixture
def _patched_dispatch(monkeypatch, tmp_path):
    """Stub out the heavy bits of make_worktree_transport so we can capture
    the SpawnCommandOptions it builds, without touching git/tmux."""
    captured: dict = {}

    # Stub _session_name_for so spawn_session doesn't try to invent one.
    monkeypatch.setattr(
        "app.services.runs.spawn._session_name_for",
        lambda directory, preferred=None: "test-session",
    )
    monkeypatch.setattr(
        "app.services.runs.spawn.subprocess.run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    # Capture the SpawnCommandOptions the transport hands to spawn_session.
    import app.services.runs.spawn as spawn_mod

    def _capture(cli_id, options, *args, **kwargs):
        captured["options"] = options
        captured["cli_id"] = cli_id
        return {
            "session_name": "test-session",
            "tmux_target": "test-session:0.0",
            "worktree_name": "test-session",
        }

    monkeypatch.setattr(spawn_mod, "spawn_session", _capture)

    # Stub git worktree ops — they would fail in tmp_path with no real repo.
    real_subprocess_run = subprocess.run

    def _fake_subprocess_run(args, *a, **kw):
        # Skip git worktree / fetch; let everything else (defensively) call through.
        if args and args[0] == "git":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return real_subprocess_run(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)

    # Make a directory the transport will treat as a repo root.
    repo = tmp_path / "fake-repo"
    repo.mkdir()
    (repo / ".git").mkdir()  # make Path(repo)/.claude/worktrees constructable

    yield captured, str(repo)


@pytest.mark.asyncio
async def test_worktree_transport_wires_permission_prompt_tool_when_skip_false(_patched_dispatch):
    """Product lane: skip_permissions=False → --permission-prompt-tool is set
    on the spawned session so Claude Code can route permission questions back
    to the kanban gate."""
    captured, repo = _patched_dispatch
    from app.kanban.dispatch import make_worktree_transport
    from app.services.scheduling.session_registry import session_registry

    # The transport queries session_registry; in tests we just need it to be
    # permissive about slot count.
    session_registry.reset_for_tests() if hasattr(session_registry, "reset_for_tests") else None

    transport = make_worktree_transport(skip_permissions=False)
    # The transport's signature is (directory, prompt, session_name, ...).
    # We don't want to actually run the worktree subprocess chain — short-
    # circuit by passing an empty prompt + catching any leftover failure.
    try:
        transport(directory=repo, prompt="hi", session_name="k-test-prompt")
    except Exception:
        # The transport may still try to run the git/tmux commands; we've
        # stubbed them, but if a permission check fails, that's not the layer
        # under test. Verify via captured options regardless.
        pass

    opts = captured.get("options")
    assert opts is not None, "transport never called spawn_session"
    assert opts.skip_permissions is False
    assert opts.permission_prompt_tool == (
        "mcp__cockpit-kanban__permission_prompt"
    )


@pytest.mark.asyncio
async def test_worktree_transport_does_not_wire_permission_prompt_when_skip_true(_patched_dispatch):
    """Meta lane: skip_permissions=True → no --permission-prompt-tool. The
    kanban repo itself must keep its current spawn shape; this card must not
    regress dispatch on the meta project."""
    captured, repo = _patched_dispatch
    from app.kanban.dispatch import make_worktree_transport

    transport = make_worktree_transport(skip_permissions=True)
    try:
        transport(directory=repo, prompt="hi", session_name="k-test-meta")
    except Exception:
        pass

    opts = captured.get("options")
    assert opts is not None
    assert opts.skip_permissions is True
    assert opts.permission_prompt_tool is None
