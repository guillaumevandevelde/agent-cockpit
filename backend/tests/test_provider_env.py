"""Tests for ``build_spawn_env`` in ``app.services.agentic_cli.provider_env``.

This is the single entry point for explicit-env construction in
``services/runs/spawn.py`` (agent-bridge) and ``services/runs/cc_spawn.py``
(legacy CC-bridge). It replaces the two near-identical 60-line blocks that
duplicated the contract — see kanban card ``b5c71e0c`` + the
``[self-improve] spawn.py + cc_spawn.py duplicate the explicit-env
construction; extract build_spawn_env()`` follow-up.
"""
import pytest


def test_build_spawn_env_collision_precedence_extras_provider_cockpit():
    """On a key collision the precedence is ``extras < provider_env < cockpit_*``.

    The contract: caller-supplied secrets (``extra_env``) lose to provider
    env (Bedrock/MiniMax), which loses to cockpit-injected context vars
    (``COCKPIT_PROJECT_KEY`` / ``COCKPIT_RUNTIME``). Cockpit vars are the
    most authoritative — if a project secret is *named* ``COCKPIT_PROJECT_KEY``
    we want the runtime's value (the agent's project context) to win.
    """
    from app.services.agentic_cli.provider_env import build_spawn_env

    # Each layer supplies a colliding value for one key.
    extra_env = {"SHARED": "from-extras"}
    provider_env = {"SHARED": "from-provider"}
    env, names = build_spawn_env(
        provider_env=provider_env,
        extra_env=extra_env,
        project_key="git:example.com/repo",
        runtime="worktree",
    )

    assert env["SHARED"] == "from-provider", (
        "provider_env must override extras on collision (extras are caller-"
        "resolved secrets; the provider's env wins so a stale secret can't "
        "downgrade the provider's CLI config)"
    )

    # The cockpit vars win over the provider on the COCKPIT_* keys.
    env_cockpit_override, _ = build_spawn_env(
        provider_env={"COCKPIT_PROJECT_KEY": "from-provider", "X": "1"},
        extra_env={},
        project_key="git:example.com/repo",
        runtime="worktree",
    )
    assert env_cockpit_override["COCKPIT_PROJECT_KEY"] == "git:example.com/repo", (
        "cockpit_* must override provider_env — the runtime's project key "
        "is the source of truth, never the provider's accidental duplicate"
    )
    assert env_cockpit_override["X"] == "1"

    # The names list is sorted so audit + tmux argv can both consume one
    # canonical ordering.
    assert names == sorted(names)


def test_build_spawn_env_omits_cockpit_vars_when_not_supplied():
    """``project_key=None`` and ``runtime=None`` must not appear in the env."""
    from app.services.agentic_cli.provider_env import (
        CLAUDE_CODE_BASELINE_ENV,
        build_spawn_env,
    )

    env, names = build_spawn_env(
        provider_env={},
        extra_env={"FOO": "bar"},
        project_key=None,
        runtime=None,
    )
    # cli_id defaults to claude-code, so the CLI baseline is present too.
    assert env == {"FOO": "bar", **CLAUDE_CODE_BASELINE_ENV}
    assert "COCKPIT_PROJECT_KEY" not in env
    assert "COCKPIT_RUNTIME" not in env
    assert names == sorted(["FOO", *CLAUDE_CODE_BASELINE_ENV])


def test_build_spawn_env_disables_claude_api_skill_for_claude_code_only():
    """Every Claude Code spawn gets ``CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL=1``.

    The bundled ``claude-api`` skill inlines ~212k tokens in one tool
    result and its trigger fires on any prompt naming ``claude-*`` — which
    on this board means nearly every card — so a dispatched session died
    on turn one with ``invalid_request: Prompt is too long``. The var must
    reach the spawned process's env (the CLI gates skill registration on
    it at startup), for *every* provider, not just the endpoint-setting
    ones, and must not leak to CLIs that don't understand it.
    """
    from app.services.agentic_cli.provider_env import (
        CODEX_CLI_ID,
        OPEN_CODE_CLI_ID,
        build_spawn_env,
    )

    for provider_env in ({}, {"ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic"}):
        env, _ = build_spawn_env(
            provider_env=provider_env,
            extra_env=None,
            project_key="git:example.com/repo",
            runtime="worktree",
            cli_id="claude-code",
        )
        assert env["CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL"] == "1", (
            "the baseline must apply on every provider — plain Anthropic "
            "returns an empty provider_env, so a provider-keyed fix would "
            "miss the most common spawn"
        )

    for other_cli in (CODEX_CLI_ID, OPEN_CODE_CLI_ID):
        env, _ = build_spawn_env(
            provider_env={},
            extra_env=None,
            project_key=None,
            runtime=None,
            cli_id=other_cli,
        )
        assert "CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL" not in env, (
            f"{other_cli} has no such skill; a Claude-Code-specific var must "
            "not be injected into another CLI's environment"
        )


def test_build_spawn_env_baseline_is_overridable_by_extras():
    """The CLI baseline sits at the bottom of the precedence chain."""
    from app.services.agentic_cli.provider_env import build_spawn_env

    env, _ = build_spawn_env(
        provider_env={},
        extra_env={"CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL": "0"},
        project_key=None,
        runtime=None,
        cli_id="claude-code",
    )
    assert env["CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL"] == "0"


def test_build_spawn_env_rejects_control_chars_in_extra_env():
    """Newline/null-byte values raise ValueError — same rule as ``_clean``."""
    from app.services.agentic_cli.provider_env import build_spawn_env

    with pytest.raises(ValueError, match="Environment value must not contain"):
        build_spawn_env(
            provider_env={},
            extra_env={"BAD": "sk_live\nFOO=bar"},
            project_key="git:example.com/repo",
            runtime="worktree",
        )

    with pytest.raises(ValueError, match="Environment value must not contain"):
        build_spawn_env(
            provider_env={},
            extra_env={"BAD": "sk_live\x00FOO"},
            project_key="git:example.com/repo",
            runtime="worktree",
        )


def test_build_spawn_env_rejects_empty_key_and_non_string_value():
    """Empty keys and non-string values raise ValueError — same guard as spawn.py."""
    from app.services.agentic_cli.provider_env import build_spawn_env

    with pytest.raises(ValueError, match="Environment key must be a non-empty string"):
        build_spawn_env(
            provider_env={},
            extra_env={"": "value"},
            project_key=None,
            runtime=None,
        )

    with pytest.raises(ValueError, match="must be a string"):
        build_spawn_env(
            provider_env={},
            extra_env={"K": 123},  # type: ignore[dict-item]
            project_key=None,
            runtime=None,
        )
