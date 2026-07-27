"""Tests for ``dispatch._cli_id_for_opencode_provider``.

The helper backs the post-resolution cli_id switch that makes an
``opencode-go`` / ``opencode`` (Zen) column or per-card override spawn
via the OpenCode CLI instead of falling through to claude-code's empty
``build_provider_env``. It must respect an explicit per-card pin
(agent_override / {analyst,executor}_agent_id / card.agent-as-known-CLI)
and must not touch cli_id for claude-code / bedrock / minimax.
"""
from app.kanban.dispatch import _cli_id_for_opencode_provider
from app.services.agentic_cli.provider_env import (
    CLAUDE_CODE_CLI_ID,
    OPEN_CODE_CLI_ID,
    PROVIDER_ANTHROPIC,
    PROVIDER_MINIMAX,
    PROVIDER_OPENCODE_GO,
    PROVIDER_OPENCODE_ZEN,
)


def test_switches_to_open_code_for_opencode_go_when_implicit():
    assert (
        _cli_id_for_opencode_provider(
            CLAUDE_CODE_CLI_ID, PROVIDER_OPENCODE_GO, explicit_cli_chosen=False,
        )
        == OPEN_CODE_CLI_ID
    )


def test_switches_to_open_code_for_opencode_zen_when_implicit():
    assert (
        _cli_id_for_opencode_provider(
            CLAUDE_CODE_CLI_ID, PROVIDER_OPENCODE_ZEN, explicit_cli_chosen=False,
        )
        == OPEN_CODE_CLI_ID
    )


def test_respects_explicit_cli_pin_on_opencode_go_column():
    # A user pinned executor_agent_id="claude-code" on an opencode-go
    # column — that meant claude-code, not "let the column override me".
    assert (
        _cli_id_for_opencode_provider(
            CLAUDE_CODE_CLI_ID, PROVIDER_OPENCODE_GO, explicit_cli_chosen=True,
        )
        == CLAUDE_CODE_CLI_ID
    )


def test_leaves_non_claude_code_cli_id_alone():
    # Already on a non-default CLI (e.g. agent_override="codex-cli" or
    # card.agent="mimo-code" won the resolution) — even if the provider
    # is opencode-go, the explicit-pin already took effect at `_phase_cli_id`
    # time, so this helper is a no-op.
    assert (
        _cli_id_for_opencode_provider(
            "codex-cli", PROVIDER_OPENCODE_GO, explicit_cli_chosen=False,
        )
        == "codex-cli"
    )


def test_leaves_anthropic_provider_alone():
    assert (
        _cli_id_for_opencode_provider(
            CLAUDE_CODE_CLI_ID, PROVIDER_ANTHROPIC, explicit_cli_chosen=False,
        )
        == CLAUDE_CODE_CLI_ID
    )


def test_leaves_minimax_provider_alone():
    assert (
        _cli_id_for_opencode_provider(
            CLAUDE_CODE_CLI_ID, PROVIDER_MINIMAX, explicit_cli_chosen=False,
        )
        == CLAUDE_CODE_CLI_ID
    )


def test_leaves_none_provider_alone():
    assert (
        _cli_id_for_opencode_provider(
            CLAUDE_CODE_CLI_ID, None, explicit_cli_chosen=False,
        )
        == CLAUDE_CODE_CLI_ID
    )


def test_switches_even_when_cli_id_already_open_code():
    # Idempotent — already-open-code on an opencode-zen column stays
    # open-code (would only happen if the user pinned executor_agent_id,
    # which would set explicit_cli_chosen=True; still, the helper itself
    # should be idempotent so it is safe to re-run).
    assert (
        _cli_id_for_opencode_provider(
            OPEN_CODE_CLI_ID, PROVIDER_OPENCODE_ZEN, explicit_cli_chosen=False,
        )
        == OPEN_CODE_CLI_ID
    )