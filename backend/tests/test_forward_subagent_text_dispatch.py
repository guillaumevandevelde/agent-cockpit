"""Tests for the per-lane ``--forward-subagent-text`` opt-in (kanban card 824e6f8d…).

Three things must be wired for the feature to work end-to-end:

  1. The ``column_overrides[col].forward_subagent_text`` field is validated as
     a bool at the schema boundary (REST + MCP).
  2. The worktree transport captures the flag at factory time and sets
     ``CLAUDE_CODE_FORWARD_SUBAGENT_TEXT=1`` in the spawn env **iff** the
     column override is true. Default un-overridden columns see no env var.
  3. The default factory (``forward_subagent_text: False``) does not set the
     env var, so the historical behaviour is preserved for every column that
     opted out.

This file focuses on (2) and (3) — the env-var wiring. The schema validator
test lives upstream in the column-overrides test file; the renderer test
lives in the frontend. Boundary cases (e.g. ``None`` vs ``False``) are
covered because the public resolver coerces with ``bool(...)``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def captured_env(monkeypatch, tmp_path):
    """Capture the explicit ``extra_env`` the worktree transport passes to
    ``spawn_session``. Mirrors the stub the permission-prompt test uses:
    short-circuit the git worktree chain with a stub subprocess, point
    ``spawn_session`` at a capture dict, then return whatever the transport
    yielded."""
    captured: dict = {}

    repo = tmp_path / "fake-repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    # Stub the spawn subprocess chain so the transport doesn't try to run
    # tmux/git.
    import subprocess
    real_subprocess_run = subprocess.run

    def _fake_subprocess_run(args, *a, **kw):
        if args and args[0] == "git":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return real_subprocess_run(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)

    import app.services.runs.spawn as spawn_mod

    def _capture(cli_id, options, *args, **kwargs):
        captured["extra_env"] = kwargs.get("extra_env", {})
        return {
            "session_name": "test-session",
            "tmux_target": "test-session:0.0",
            "worktree_name": "test-session",
        }

    monkeypatch.setattr(spawn_mod, "spawn_session", _capture)
    monkeypatch.setattr(
        "app.services.runs.spawn._session_name_for",
        lambda directory, preferred=None: "test-session",
    )
    # Ensure the registry permits one extra slot.
    from app.services.scheduling.session_registry import session_registry
    if hasattr(session_registry, "reset_for_tests"):
        session_registry.reset_for_tests()

    yield captured, str(repo)


def test_worktree_transport_sets_forward_subagent_text_env_when_true(captured_env):
    """Acceptance criterion: a column with ``forward_subagent_text: true``
    causes the spawned ``claude`` invocation to have
    ``CLAUDE_CODE_FORWARD_SUBAGENT_TEXT=1`` in its env."""
    captured, repo = captured_env
    from app.kanban.dispatch import make_worktree_transport

    transport = make_worktree_transport(
        skip_permissions=True, forward_subagent_text=True,
    )
    try:
        transport(directory=repo, prompt="hi", session_name="k-forward-on")
    except Exception:
        pass  # The transport may still try other subsystems; we only want extra_env.

    extra_env = captured.get("extra_env", {})
    assert extra_env.get("CLAUDE_CODE_FORWARD_SUBAGENT_TEXT") == "1", (
        f"CLAUDE_CODE_FORWARD_SUBAGENT_TEXT=1 expected when "
        f"forward_subagent_text=True; got extra_env={extra_env!r}"
    )


def test_worktree_transport_omits_forward_subagent_text_env_by_default(captured_env):
    """Acceptance criterion: the default column has the flag off — no
    behavioural change for un-overridden columns. Zero-trip on the env var
    is the only assertion that proves the default stays clobber-free."""
    captured, repo = captured_env
    from app.kanban.dispatch import make_worktree_transport

    transport = make_worktree_transport(skip_permissions=True)
    try:
        transport(directory=repo, prompt="hi", session_name="k-forward-default")
    except Exception:
        pass

    extra_env = captured.get("extra_env", {})
    assert "CLAUDE_CODE_FORWARD_SUBAGENT_TEXT" not in extra_env, (
        f"CLAUDE_CODE_FORWARD_SUBAGENT_TEXT must be absent when the factory "
        f"default is used; got extra_env={extra_env!r}"
    )


def test_resolve_forward_subagent_text_card_helper():
    """The card-level resolver exposes the bool as the worktree transport
    factory expects. The schema already validates the field, so this test
    only pins the coercion invariant (None → False, missing key → False)."""
    from app.kanban.dispatch import _resolve_forward_subagent_text

    card = SimpleNamespace(column="Doing", column_overrides=None)
    assert _resolve_forward_subagent_text(card) is False

    card = SimpleNamespace(column="Doing", column_overrides={})
    assert _resolve_forward_subagent_text(card) is False

    card = SimpleNamespace(column="Doing", column_overrides={"Doing": {}})
    assert _resolve_forward_subagent_text(card) is False

    card = SimpleNamespace(column="Doing", column_overrides={"Doing": {"forward_subagent_text": True}})
    assert _resolve_forward_subagent_text(card) is True

    # Other-column override is not picked up at the target column.
    card = SimpleNamespace(
        column="Doing",
        column_overrides={"Review": {"forward_subagent_text": True}},
    )
    assert _resolve_forward_subagent_text(card) is False


def test_forward_subagent_text_does_not_override_existing_caps_env(captured_env):
    """Both flags (subagent_caps + forward_subagent_text) can coexist on the
    same card. Merging must keep the existing ``CLAUDE_CODE_MAX_*`` env vars
    so a card opting in to both doesn't silently drop the caps."""
    captured, repo = captured_env
    from app.kanban.dispatch import make_worktree_transport

    transport = make_worktree_transport(
        skip_permissions=True,
        subagent_caps_env={"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "2"},
        forward_subagent_text=True,
    )
    try:
        transport(directory=repo, prompt="hi", session_name="k-forward-both")
    except Exception:
        pass

    extra_env = captured.get("extra_env", {})
    assert extra_env.get("CLAUDE_CODE_FORWARD_SUBAGENT_TEXT") == "1"
    assert extra_env.get("CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH") == "2"
