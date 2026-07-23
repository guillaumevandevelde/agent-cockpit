"""Tests for detecting the vendor/subscription that started a session.

Agent Bridge sessions all run through the same CLI binary (``claude``),
so the only reliable signal for *which vendor/subscription* a running
session talks to is the launched process's environment — see
``agentic_cli/provider_env.py`` for the spawn-time env contract this
mirrors on the read side.
"""
from app.services.agentic_cli import provider_detect


def test_classify_bedrock_from_env():
    assert provider_detect.classify_provider_env(
        {"CLAUDE_CODE_USE_BEDROCK": "1"}, cli_id="claude-code"
    ) == ("bedrock", "Bedrock")


def test_classify_minimax_from_base_url():
    assert provider_detect.classify_provider_env(
        {"ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic"},
        cli_id="claude-code",
    ) == ("minimax", "MiniMax")


def test_classify_anthropic_compatible_from_other_base_url():
    assert provider_detect.classify_provider_env(
        {"ANTHROPIC_BASE_URL": "https://gateway.internal/v1"},
        cli_id="claude-code",
    ) == ("anthropic-compatible", "Anthropic-compatible")


def test_classify_defaults_to_anthropic_for_claude_without_override():
    assert provider_detect.classify_provider_env(
        {}, cli_id="claude-code", cli_display_name="Claude Code"
    ) == ("anthropic", "Anthropic")


def test_classify_falls_back_to_cli_for_non_anthropic_cli():
    # Codex has no ANTHROPIC_* override env → its vendor is the CLI itself,
    # not a fabricated "Anthropic".
    assert provider_detect.classify_provider_env(
        {}, cli_id="codex-cli", cli_display_name="Codex"
    ) == ("codex-cli", "Codex")


def test_bedrock_wins_over_base_url():
    assert provider_detect.classify_provider_env(
        {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
        },
        cli_id="claude-code",
    ) == ("bedrock", "Bedrock")


def test_blank_base_url_is_ignored():
    assert provider_detect.classify_provider_env(
        {"ANTHROPIC_BASE_URL": "   "}, cli_id="claude-code"
    ) == ("anthropic", "Anthropic")


def test_falsy_bedrock_flag_is_ignored():
    assert provider_detect.classify_provider_env(
        {"CLAUDE_CODE_USE_BEDROCK": "0"}, cli_id="claude-code"
    ) == ("anthropic", "Anthropic")


def test_detect_session_provider_reads_process_env(monkeypatch):
    monkeypatch.setattr(
        provider_detect,
        "read_process_env",
        lambda pid: {"ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic"},
    )
    assert provider_detect.detect_session_provider(
        "123", cli_id="claude-code", cli_display_name="Claude Code"
    ) == ("minimax", "MiniMax")


def test_read_process_env_missing_pid_returns_empty():
    # A non-existent pid must never raise — detection is best-effort.
    assert provider_detect.read_process_env("0") == {}


def test_read_process_env_does_not_expose_secrets_as_classification(monkeypatch):
    # Even when the env carries an auth token, only the vendor id/display is
    # returned by detection — the token never leaves the reader.
    monkeypatch.setattr(
        provider_detect,
        "read_process_env",
        lambda pid: {
            "ANTHROPIC_AUTH_TOKEN": "sk-secret",
            "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
        },
    )
    result = provider_detect.detect_session_provider(
        "123", cli_id="claude-code", cli_display_name="Claude Code"
    )
    assert result == ("minimax", "MiniMax")
    assert "sk-secret" not in "".join(result)
