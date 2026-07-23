"""Tests for mixed-provider tmux discovery."""
from types import SimpleNamespace
from unittest.mock import patch


def test_discover_agent_sessions_returns_mixed_providers():
    from app.services.runs.discovery import discover_agent_sessions

    tmux_output = "\n".join([
        "claudeproj:0.0|claudeproj|main|%1|/repo/a|111|claude",
        "codexproj:0.0|codexproj|main|%2|/repo/b|222|codex",
        "shell:0.0|shell|main|%3|/repo/c|333|bash",
    ])

    with patch("app.services.runs.discovery.subprocess.run") as run:
        run.return_value = SimpleNamespace(returncode=0, stdout=tmux_output, stderr="")
        # Fake pids so /proc reads fall through to the CLI fallback.
        with patch(
            "app.services.runs.discovery.detect_session_provider",
            return_value=("anthropic", "Anthropic"),
        ) as detect:
            sessions = discover_agent_sessions()

    assert [session["cli"] for session in sessions] == ["claude-code", "codex-cli"]
    assert sessions[0]["cli_display_name"] == "Claude Code"
    assert sessions[1]["cli_display_name"] == "Codex"
    # vendor detection was invoked per session with the right cli context
    detect.assert_called()
    assert [session["provider"] for session in sessions] == ["anthropic", "anthropic"]
    assert [session["provider_display_name"] for session in sessions] == [
        "Anthropic",
        "Anthropic",
    ]


def test_discover_agent_sessions_can_filter_provider():
    from app.services.runs.discovery import discover_agent_sessions

    tmux_output = "\n".join([
        "claudeproj:0.0|claudeproj|main|%1|/repo/a|111|claude",
        "codexproj:0.0|codexproj|main|%2|/repo/b|222|codex",
    ])

    with patch("app.services.runs.discovery.subprocess.run") as run:
        run.return_value = SimpleNamespace(returncode=0, stdout=tmux_output, stderr="")
        with patch(
            "app.services.runs.discovery.detect_session_provider",
            return_value=("anthropic", "Anthropic"),
        ):
            sessions = discover_agent_sessions("codex-cli")

    assert len(sessions) == 1
    assert sessions[0]["cli"] == "codex-cli"


def test_discover_agent_sessions_reports_minimax_when_env_says_so():
    from app.services.runs.discovery import discover_agent_sessions

    tmux_output = (
        "claudeproj:0.0|claudeproj|main|%1|/repo/a|111|claude"
    )

    def fake_detect(pid, *, cli_id, cli_display_name):
        return ("minimax", "MiniMax")

    with patch("app.services.runs.discovery.subprocess.run") as run:
        run.return_value = SimpleNamespace(returncode=0, stdout=tmux_output, stderr="")
        with patch(
            "app.services.runs.discovery.detect_session_provider",
            side_effect=fake_detect,
        ):
            sessions = discover_agent_sessions()

    assert sessions[0]["provider"] == "minimax"
    assert sessions[0]["provider_display_name"] == "MiniMax"
    # CLI axis is unaffected by vendor detection.
    assert sessions[0]["cli"] == "claude-code"
    assert sessions[0]["cli_display_name"] == "Claude Code"
