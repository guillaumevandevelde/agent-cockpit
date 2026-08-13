"""Tests for the ``PreToolUse`` worktree-write guard.

The guard is ``.claude/hooks/worktree-write-guard.py``; the decision that put
it there is ``docs/cockpit/worktree-isolatie-meting.md`` §5-§7 (kanban card
d19b8fbc4fbe402983e3099cc78f5c8b).

This file also satisfies ``scripts/check-pretooluse-bg-agent-test.sh``, which
demands a background-agent test as soon as ``.claude/settings.json`` carries a
non-empty ``PreToolUse`` list. Two halves of that contract:

* **Payload level (here).** The guard is executed as a subprocess against the
  exact stdin shape Claude Code sends, once for a foreground call and once for
  a subagent call. Deterministic, sub-second, runs in CI.
* **Harness level (measured, not automated).** Whether Claude Code invokes the
  hook at all on the background route is upstream behaviour -- the bypass that
  motivated the CI gate was fixed in CC 2.1.222. That half was measured with a
  real subagent under ``--dangerously-skip-permissions`` on CC 2.1.231 and the
  reproduction command is recorded in ``worktree-isolatie-meting.md`` §7.

``test_negative_control_broken_guard_is_caught`` is the anti-tautology check
required by CLAUDE.md's harness note: it runs the same assertions against a
deliberately broken copy of the guard and fails if they still pass.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / ".claude" / "hooks" / "worktree-write-guard.py"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"

MAIN_CHECKOUT = "/home/agent/project"
WORKTREE = f"{MAIN_CHECKOUT}/.claude/worktrees/k-example-1234"

ALLOW = 0
BLOCK = 2


def _payload(
    *,
    tool_name: str = "Write",
    file_path: str,
    cwd: str = WORKTREE,
    key: str = "file_path",
) -> str:
    return json.dumps(
        {
            "session_id": "s-1",
            "transcript_path": "/dev/null",
            "cwd": cwd,
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": {key: file_path},
        }
    )


def _run(payload: str, guard: Path = GUARD) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(guard)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    ("case", "target"),
    [
        ("repo root", f"{MAIN_CHECKOUT}/docs/cockpit/foo.md"),
        ("sibling worktree", f"{MAIN_CHECKOUT}/.claude/worktrees/other/x.md"),
        ("dotdot escape", f"{WORKTREE}/../../../backend/app/main.py"),
    ],
)
def test_blocks_writes_to_the_shared_checkout(case: str, target: str) -> None:
    result = _run(_payload(file_path=target))
    assert result.returncode == BLOCK, f"{case}: expected block, got {result!r}"
    assert "shared checkout" in result.stderr


@pytest.mark.parametrize(
    ("case", "target"),
    [
        ("relative path", "docs/cockpit/foo.md"),
        ("absolute worktree path", f"{WORKTREE}/docs/cockpit/foo.md"),
        ("scratchpad outside the repo", "/tmp/claude-1000/scratch/notes.md"),
        ("memory dir in $HOME", "/home/agent/.claude/projects/p/memory/x.md"),
        ("ship merge worktree", "/home/agent/.cache/cockpit-ship/wt/x.md"),
    ],
)
def test_allows_legitimate_targets(case: str, target: str) -> None:
    result = _run(_payload(file_path=target))
    assert result.returncode == ALLOW, f"{case}: expected allow, got {result!r}"


@pytest.mark.parametrize(
    "tool_name",
    ["Write", "Edit", "MultiEdit", "NotebookEdit"],
)
def test_covers_every_guarded_edit_tool(tool_name: str) -> None:
    key = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
    target = f"{MAIN_CHECKOUT}/x.ipynb" if key == "notebook_path" else f"{MAIN_CHECKOUT}/x.md"
    result = _run(_payload(tool_name=tool_name, file_path=target, key=key))
    assert result.returncode == BLOCK


def test_bash_is_out_of_scope_by_design() -> None:
    """The ship recipe writes to the shared checkout through Bash on purpose."""
    payload = json.dumps(
        {
            "cwd": WORKTREE,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {MAIN_CHECKOUT} pull --ff-only origin master"},
        }
    )
    assert _run(payload).returncode == ALLOW


@pytest.mark.parametrize(
    ("case", "cwd"),
    [
        ("interactive session in the main checkout", MAIN_CHECKOUT),
        ("ship merge worktree", "/home/agent/.cache/cockpit-ship/ship-merge-x"),
        ("unrelated directory", "/srv/other"),
    ],
)
def test_non_worktree_sessions_are_untouched(case: str, cwd: str) -> None:
    result = _run(_payload(file_path=f"{MAIN_CHECKOUT}/docs/cockpit/foo.md", cwd=cwd))
    assert result.returncode == ALLOW, f"{case}: expected allow, got {result!r}"


@pytest.mark.parametrize(
    ("case", "payload"),
    [
        ("not json", "this is not json"),
        ("json but not an object", "[1, 2, 3]"),
        ("empty", ""),
        ("missing tool_input", json.dumps({"cwd": WORKTREE, "tool_name": "Write"})),
        ("missing cwd", json.dumps({"tool_name": "Write", "tool_input": {"file_path": "/x"}})),
        ("file_path is not a string", json.dumps(
            {"cwd": WORKTREE, "tool_name": "Write", "tool_input": {"file_path": 42}}
        )),
    ],
)
def test_fails_open_on_malformed_input(case: str, payload: str) -> None:
    """A broken guard must never stall a dispatch."""
    assert _run(payload).returncode == ALLOW, case


# --- background-agent route -------------------------------------------------


def test_blocks_on_the_background_agent_route() -> None:
    """A subagent inherits the parent session's worktree cwd.

    Claude Code reports the spawning session's ``cwd`` for tool calls made
    inside a subagent, so the guard sees the same worktree and must reach the
    same verdict. Measured against a real subagent on CC 2.1.231; see
    ``docs/cockpit/worktree-isolatie-meting.md`` §7 for the probe.
    """
    payload = json.dumps(
        {
            "session_id": "s-sub-1",
            "cwd": WORKTREE,
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": f"{MAIN_CHECKOUT}/docs/cockpit/foo.md"},
            "agent_type": "general-purpose",
            "parent_session_id": "s-1",
        }
    )
    result = _run(payload)
    assert result.returncode == BLOCK
    assert "shared checkout" in result.stderr


def test_negative_control_broken_guard_is_caught(tmp_path: Path) -> None:
    """Prove the assertions above are not tautological.

    Removes the containment check from a copy of the guard -- the single most
    likely way for it to regress into an always-allow no-op -- and asserts the
    foreground and background cases both flip to allow. If this test fails, the
    ones above are passing for a reason other than the guard working.
    """
    source = GUARD.read_text(encoding="utf-8")
    needle = "    if _under(target, worktree_root) or not _under(target, main_checkout):\n        return None\n"
    assert needle in source, "guard body changed -- update the negative control"
    broken = tmp_path / "broken-guard.py"
    broken.write_text(source.replace(needle, "    return None\n"), encoding="utf-8")

    foreground = _run(_payload(file_path=f"{MAIN_CHECKOUT}/docs/cockpit/foo.md"), guard=broken)
    background = _run(
        json.dumps(
            {
                "cwd": WORKTREE,
                "tool_name": "Write",
                "tool_input": {"file_path": f"{MAIN_CHECKOUT}/docs/cockpit/foo.md"},
                "agent_type": "general-purpose",
            }
        ),
        guard=broken,
    )
    assert foreground.returncode == ALLOW, "broken guard still blocked -- assertions are tautological"
    assert background.returncode == ALLOW, "broken guard still blocked -- assertions are tautological"


# --- wiring -----------------------------------------------------------------


def test_settings_json_wires_the_guard_to_the_edit_tools() -> None:
    entries = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    commands = [
        hook["command"]
        for entry in entries
        for hook in entry.get("hooks", [])
        if "worktree-write-guard.py" in hook.get("command", "")
    ]
    assert commands, "worktree-write-guard.py is not wired into hooks.PreToolUse"
    matchers = [
        entry["matcher"]
        for entry in entries
        if any("worktree-write-guard.py" in h.get("command", "") for h in entry.get("hooks", []))
    ]
    for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        assert any(tool in m for m in matchers), f"{tool} not covered by the guard matcher"


def test_check_pretooluse_bg_agent_test_gate_is_satisfied() -> None:
    """The CI gate must go green now that PreToolUse is non-empty."""
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "check-pretooluse-bg-agent-test.sh"), "--strict"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "contract met" in result.stdout
