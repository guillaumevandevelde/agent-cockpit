"""Tests for agent provider registry and Codex detection."""
from types import SimpleNamespace
from unittest.mock import patch


def test_provider_registry_contains_initial_providers():
    from app.services.agentic_cli import get_agentic_cli, get_agentic_clis

    provider_ids = {provider.id for provider in get_agentic_clis()}

    assert provider_ids == {"claude-code", "codex-cli", "copilot-cli", "mimo-code", "open-code"}
    assert get_agentic_cli("claude-code").display_name == "Claude Code"
    assert get_agentic_cli("codex-cli").binary_name == "codex"
    assert get_agentic_cli("open-code").display_name == "OpenCode"
    assert get_agentic_cli("copilot-cli").binary_name == "copilot"


def test_provider_status_includes_central_capability_matrix():
    from app.services.agentic_cli import get_agentic_cli

    claude = get_agentic_cli("claude-code").get_status()
    codex = get_agentic_cli("codex-cli").get_status()

    assert claude["capabilities"]["plugins"] is True
    assert claude["capabilities"]["fork"] is False
    assert claude["capability_matrix"]["plugins"]["state"] == "write_capable"
    assert claude["capability_details"]["plugins"]["state"] == "write_capable"
    assert claude["capability_matrix"]["doctor"]["state"] == "unsupported"
    assert codex["capabilities"]["plugins"] is True
    assert codex["capability_matrix"]["plugins"]["state"] == "write_capable"
    assert codex["capability_details"]["plugins"]["state"] == "write_capable"
    assert codex["capability_matrix"]["mcp"]["state"] == "write_capable"
    assert codex["capability_matrix"]["usage"]["state"] == "unsupported"
    assert codex["capability_matrix"]["doctor"]["state"] == "read_only"


async def test_provider_capabilities_api_returns_matrix():
    from app.api.v1 import providers as providers_api

    response = await providers_api.get_provider_capabilities("codex-cli")

    assert response["provider"] == "codex-cli"
    assert response["capabilities"]["config"] is True
    assert response["capability_matrix"]["config"]["state"] == "write_capable"
    assert response["capability_matrix"]["commands"]["state"] == "unsupported"


def test_codex_process_detection_matches_interactive_binary():
    from app.services.agentic_cli import get_agentic_cli

    provider = get_agentic_cli("codex-cli")

    assert provider.is_process_match("codex", "123") is True
    assert provider.is_process_match("/usr/local/bin/codex", "123") is True
    assert provider.is_process_match("codex-exec-server", "123") is False


def test_codex_process_detection_matches_node_wrapper_descendant():
    from app.services.agentic_cli import get_agentic_cli

    provider = get_agentic_cli("codex-cli")

    with patch("app.services.agentic_cli.base.subprocess.run") as run:
        run.return_value = SimpleNamespace(stdout="456 /usr/local/bin/codex\n")
        assert provider.is_process_match("node", "123") is True


def test_codex_bedrock_spawn_command_sets_model_provider_and_bedrock_model():
    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("codex-cli")
    command = provider.build_spawn_command(
        SpawnCommandOptions(
            directory="/tmp/project",
            mode="plain",
            platform="bedrock",
            bedrock_model="openai.gpt-5.5",
            model="ignored-when-bedrock-model-set",
        )
    )

    assert command == [
        "codex",
        "--cd",
        "/tmp/project",
        "--config",
        'model_provider="amazon-bedrock"',
        "--model",
        "openai.gpt-5.5",
    ]


def test_codex_bedrock_spawn_command_falls_back_to_model_without_bedrock_model():
    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("codex-cli")
    command = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="plain", platform="bedrock", model="fallback-model")
    )

    assert "--config" in command
    assert command[command.index("--model") + 1] == "fallback-model"


def test_codex_anthropic_platform_spawn_command_omits_bedrock_config():
    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("codex-cli")
    command = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="plain", model="gpt-5.1-codex")
    )

    assert "--config" not in command
    assert command == ["codex", "--cd", "/tmp/project", "--model", "gpt-5.1-codex"]


def test_codex_spawn_command_sets_reasoning_effort():
    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("codex-cli")
    command = provider.build_spawn_command(
        SpawnCommandOptions(
            directory="/tmp/project",
            mode="plain",
            model="gpt-5.1-codex",
            reasoning_effort="xhigh",
        )
    )

    assert command == [
        "codex",
        "--cd",
        "/tmp/project",
        "--model",
        "gpt-5.1-codex",
        "--config",
        'model_reasoning_effort="xhigh"',
    ]


def test_codex_spawn_command_omits_reasoning_effort_config_when_unset():
    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("codex-cli")
    command = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="plain", model="gpt-5.1-codex")
    )

    assert not any("model_reasoning_effort" in part for part in command)


def test_codex_bedrock_spawn_command_combines_model_provider_and_reasoning_effort_config():
    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("codex-cli")
    command = provider.build_spawn_command(
        SpawnCommandOptions(
            directory="/tmp/project",
            mode="plain",
            platform="bedrock",
            bedrock_model="openai.gpt-5.5",
            reasoning_effort="xhigh",
        )
    )

    assert command == [
        "codex",
        "--cd",
        "/tmp/project",
        "--config",
        'model_provider="amazon-bedrock"',
        "--model",
        "openai.gpt-5.5",
        "--config",
        'model_reasoning_effort="xhigh"',
    ]


def test_opencode_spawn_command_rejects_reasoning_effort():
    import pytest

    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("open-code")
    with pytest.raises(ValueError):
        provider.build_spawn_command(
            SpawnCommandOptions(directory="/tmp/project", mode="plain", reasoning_effort="xhigh")
        )


def test_opencode_spawn_command_without_reasoning_effort_still_works():
    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("open-code")
    command = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="plain", model="claude-opus-4-8")
    )

    assert command[-2:] == ["--model", "claude-opus-4-8"]


def test_copilot_status_and_capabilities():
    from app.services.agentic_cli import get_agentic_cli

    provider = get_agentic_cli("copilot-cli")
    status = provider.get_status()

    assert status["display_name"] == "GitHub Copilot CLI"
    assert status["capability_matrix"]["sessions"]["state"] == "write_capable"
    assert status["capability_matrix"]["config"]["state"] == "unsupported"
    assert status["capabilities"]["sessions"] is True
    assert status["capabilities"]["config"] is False


def test_copilot_process_detection():
    from app.services.agentic_cli import get_agentic_cli

    provider = get_agentic_cli("copilot-cli")

    assert provider.is_process_match("copilot", "123") is True
    assert provider.is_process_match("/usr/local/bin/copilot", "123") is True
    assert provider.is_process_match("copilot-language-server", "123") is False


def test_copilot_process_detection_matches_node_wrapper_descendant():
    from app.services.agentic_cli import get_agentic_cli

    provider = get_agentic_cli("copilot-cli")

    with patch("app.services.agentic_cli.base.subprocess.run") as run:
        run.return_value = SimpleNamespace(stdout="789 /usr/local/bin/copilot\n")
        assert provider.is_process_match("node", "123") is True


def test_copilot_spawn_command_new_session():
    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("copilot-cli")
    command = provider.build_spawn_command(
        SpawnCommandOptions(
            directory="/tmp/project",
            mode="plain",
            model="claude-sonnet-5",
            agent="reviewer",
            context_tier="large",
            reasoning_effort="high",
            plan=True,
            remote=False,
            allow_all=True,
            no_ask_user=True,
            prompt="do the thing",
        )
    )

    assert command == [
        "copilot",
        "-C",
        "/tmp/project",
        "--model",
        "claude-sonnet-5",
        "--agent",
        "reviewer",
        "--context",
        "large",
        "--effort",
        "high",
        "--plan",
        "--no-remote",
        "--allow-all",
        "--no-ask-user",
        "-i",
        "do the thing",
    ]


def test_copilot_spawn_command_resume_uses_continue_when_use_last():
    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("copilot-cli")
    command = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="resume", use_last=True)
    )

    assert command == ["copilot", "-C", "/tmp/project", "--continue"]


def test_copilot_spawn_command_resume_uses_session_id():
    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("copilot-cli")
    command = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="resume", session_id="abc123")
    )

    assert command == ["copilot", "-C", "/tmp/project", "--resume=abc123"]


def test_copilot_spawn_command_resume_requires_session_id_or_use_last():
    import pytest

    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("copilot-cli")
    with pytest.raises(ValueError):
        provider.build_spawn_command(SpawnCommandOptions(directory="/tmp/project", mode="resume"))


def test_copilot_spawn_command_rejects_unsupported_mode():
    import pytest

    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("copilot-cli")
    with pytest.raises(ValueError):
        provider.build_spawn_command(SpawnCommandOptions(directory="/tmp/project", mode="worktree"))


def test_claude_code_spawn_command_includes_model_flag_when_set():
    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("claude-code")
    command = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="plain", model="opus", prompt="do the thing")
    )

    assert command == ["claude", "--model", "opus", "do the thing"]


def test_claude_code_spawn_command_omits_model_flag_when_unset():
    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("claude-code")
    command = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="plain", prompt="do the thing")
    )

    assert "--model" not in command
    assert command == ["claude", "do the thing"]


def test_claude_code_spawn_command_includes_model_flag_across_modes():
    from app.services.agentic_cli import get_agentic_cli
    from app.services.agentic_cli.base import SpawnCommandOptions

    provider = get_agentic_cli("claude-code")

    worktree_command = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="worktree",
                            worktree_name="k-feature-a1b2", model="sonnet")
    )
    assert worktree_command == ["claude", "--worktree", "k-feature-a1b2", "--model", "sonnet"]

    resume_command = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="resume",
                            session_id="sess-123", model="haiku")
    )
    assert resume_command == ["claude", "--resume", "sess-123", "--model", "haiku"]
