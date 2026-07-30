"""Tests for platform -> environment-variable mapping."""
import pytest


def test_anthropic_returns_empty_env():
    from app.services.agentic_cli.provider_env import PROVIDER_ANTHROPIC, build_provider_env

    assert build_provider_env(PROVIDER_ANTHROPIC) == {}


def test_unknown_platform_returns_empty_env():
    from app.services.agentic_cli.provider_env import build_provider_env

    assert build_provider_env("vertex") == {}


def test_bedrock_minimal_sets_use_bedrock_flag():
    from app.services.agentic_cli.provider_env import PROVIDER_BEDROCK, build_provider_env

    assert build_provider_env(PROVIDER_BEDROCK) == {"CLAUDE_CODE_USE_BEDROCK": "1"}


def test_bedrock_with_all_fields():
    from app.services.agentic_cli.provider_env import PROVIDER_BEDROCK, build_provider_env

    env = build_provider_env(
        PROVIDER_BEDROCK,
        region="us-east-1",
        aws_profile="bedrock-prod",
        model="arn:aws:bedrock:us-east-1:123:inference-profile/x",
    )
    assert env == {
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "AWS_REGION": "us-east-1",
        "AWS_PROFILE": "bedrock-prod",
        "ANTHROPIC_MODEL": "arn:aws:bedrock:us-east-1:123:inference-profile/x",
    }


def test_bedrock_skips_blank_and_whitespace_values():
    from app.services.agentic_cli.provider_env import PROVIDER_BEDROCK, build_provider_env

    env = build_provider_env(PROVIDER_BEDROCK, region="  ", aws_profile="", model=None)
    assert env == {"CLAUDE_CODE_USE_BEDROCK": "1"}


def test_bedrock_strips_surrounding_whitespace():
    from app.services.agentic_cli.provider_env import PROVIDER_BEDROCK, build_provider_env

    env = build_provider_env(PROVIDER_BEDROCK, region="  us-west-2  ")
    assert env["AWS_REGION"] == "us-west-2"


def test_bedrock_rejects_newline_in_value():
    from app.services.agentic_cli.provider_env import PROVIDER_BEDROCK, build_provider_env

    with pytest.raises(ValueError):
        build_provider_env(PROVIDER_BEDROCK, region="us-east-1\nFOO=bar")


def test_bedrock_rejects_null_byte_in_value():
    from app.services.agentic_cli.provider_env import PROVIDER_BEDROCK, build_provider_env

    with pytest.raises(ValueError):
        build_provider_env(PROVIDER_BEDROCK, model="bad\x00value")


def test_codex_bedrock_only_sets_shared_aws_env():
    from app.services.agentic_cli.provider_env import PROVIDER_BEDROCK, build_provider_env

    env = build_provider_env(
        PROVIDER_BEDROCK,
        region="us-east-2",
        aws_profile="codex-bedrock",
        model="openai.gpt-5.5",
        cli_id="codex-cli",
    )

    assert env == {
        "AWS_REGION": "us-east-2",
        "AWS_PROFILE": "codex-bedrock",
    }


def test_codex_bedrock_without_region_or_profile_has_no_env():
    from app.services.agentic_cli.provider_env import PROVIDER_BEDROCK, build_provider_env

    assert build_provider_env(PROVIDER_BEDROCK, cli_id="codex-cli") == {}


def test_opencode_bedrock_only_sets_shared_aws_env_not_claude_code_flags():
    from app.services.agentic_cli.provider_env import PROVIDER_BEDROCK, build_provider_env

    env = build_provider_env(
        PROVIDER_BEDROCK,
        region="us-east-2",
        aws_profile="opencode-bedrock",
        model="anthropic.claude-opus-4-8",
        cli_id="open-code",
    )

    assert env == {
        "AWS_REGION": "us-east-2",
        "AWS_PROFILE": "opencode-bedrock",
    }
    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert "ANTHROPIC_MODEL" not in env


def test_copilot_bedrock_only_sets_shared_aws_env_not_claude_code_flags():
    from app.services.agentic_cli.provider_env import PROVIDER_BEDROCK, build_provider_env

    env = build_provider_env(
        PROVIDER_BEDROCK,
        region="us-east-2",
        aws_profile="copilot-bedrock",
        model="some-model",
        cli_id="copilot-cli",
    )

    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert "ANTHROPIC_MODEL" not in env


def test_mimo_bedrock_only_sets_shared_aws_env_not_claude_code_flags():
    from app.services.agentic_cli.provider_env import PROVIDER_BEDROCK, build_provider_env

    env = build_provider_env(
        PROVIDER_BEDROCK,
        region="us-east-2",
        model="some-model",
        cli_id="mimo-code",
    )

    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert "ANTHROPIC_MODEL" not in env


def test_claude_code_bedrock_still_sets_claude_flags_by_default():
    from app.services.agentic_cli.provider_env import PROVIDER_BEDROCK, build_provider_env

    env = build_provider_env(PROVIDER_BEDROCK, model="arn:aws:bedrock:us-east-1:123:x")

    assert env == {
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "ANTHROPIC_MODEL": "arn:aws:bedrock:us-east-1:123:x",
    }


def test_minimax_minimal_uses_international_defaults():
    from app.services.agentic_cli.provider_env import (
        MINIMAX_AUTO_COMPACT_WINDOW,
        MINIMAX_BASE_URL_INTERNATIONAL,
        MINIMAX_DEFAULT_MODEL,
        MINIMAX_MAX_CONTEXT_TOKENS,
        PROVIDER_MINIMAX,
        build_provider_env,
    )

    env = build_provider_env(PROVIDER_MINIMAX)
    assert env == {
        "ANTHROPIC_BASE_URL": MINIMAX_BASE_URL_INTERNATIONAL,
        "ANTHROPIC_MODEL": MINIMAX_DEFAULT_MODEL,
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": MINIMAX_AUTO_COMPACT_WINDOW,
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": MINIMAX_MAX_CONTEXT_TOKENS,
    }


def test_minimax_with_api_key_sets_auth_token():
    from app.services.agentic_cli.provider_env import PROVIDER_MINIMAX, build_provider_env

    env = build_provider_env(PROVIDER_MINIMAX, minimax_api_key="sk-test-key")
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test-key"


def test_minimax_blank_api_key_is_omitted():
    from app.services.agentic_cli.provider_env import PROVIDER_MINIMAX, build_provider_env

    env = build_provider_env(PROVIDER_MINIMAX, minimax_api_key="   ")
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_minimax_base_url_is_configurable_for_china_region():
    from app.services.agentic_cli.provider_env import (
        MINIMAX_BASE_URL_CHINA,
        PROVIDER_MINIMAX,
        build_provider_env,
    )

    env = build_provider_env(PROVIDER_MINIMAX, minimax_base_url=MINIMAX_BASE_URL_CHINA)
    assert env["ANTHROPIC_BASE_URL"] == MINIMAX_BASE_URL_CHINA


def test_minimax_model_override():
    from app.services.agentic_cli.provider_env import PROVIDER_MINIMAX, build_provider_env

    env = build_provider_env(PROVIDER_MINIMAX, model="MiniMax-M3")
    assert env["ANTHROPIC_MODEL"] == "MiniMax-M3"


def test_minimax_strips_surrounding_whitespace():
    from app.services.agentic_cli.provider_env import PROVIDER_MINIMAX, build_provider_env

    env = build_provider_env(
        PROVIDER_MINIMAX,
        minimax_api_key="  sk-test-key  ",
        minimax_base_url="  https://api.minimax.io/anthropic  ",
    )
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test-key"
    assert env["ANTHROPIC_BASE_URL"] == "https://api.minimax.io/anthropic"


def test_minimax_rejects_newline_in_api_key():
    from app.services.agentic_cli.provider_env import PROVIDER_MINIMAX, build_provider_env

    with pytest.raises(ValueError):
        build_provider_env(PROVIDER_MINIMAX, minimax_api_key="sk-test\nFOO=bar")


def test_minimax_rejects_null_byte_in_base_url():
    from app.services.agentic_cli.provider_env import PROVIDER_MINIMAX, build_provider_env

    with pytest.raises(ValueError):
        build_provider_env(PROVIDER_MINIMAX, minimax_base_url="bad\x00value")


def test_minimax_env_never_includes_bedrock_keys():
    from app.services.agentic_cli.provider_env import PROVIDER_MINIMAX, build_provider_env

    env = build_provider_env(PROVIDER_MINIMAX)
    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert "AWS_REGION" not in env
    assert "AWS_PROFILE" not in env


# --- PROVIDER_COMPATIBLE ("anthropic-compatible") ---------------------------
#
# Data-driven branch: the endpoint (base_url + model) and the credential come
# from configuration the caller already resolved. The contract mirrors MiniMax
# — always set ANTHROPIC_BASE_URL/ANTHROPIC_MODEL explicitly, never
# conditionally, so a stale ambient env value can't leak through.


def test_compatible_minimal_sets_base_url_and_model():
    from app.services.agentic_cli.provider_env import (
        PROVIDER_COMPATIBLE,
        build_provider_env,
    )

    env = build_provider_env(
        PROVIDER_COMPATIBLE,
        base_url="https://api.groq.com/anthropic",
        model="llama-3.3-70b",
    )
    assert env == {
        "ANTHROPIC_BASE_URL": "https://api.groq.com/anthropic",
        "ANTHROPIC_MODEL": "llama-3.3-70b",
    }


def test_compatible_with_auth_token_sets_anthropic_auth_token():
    from app.services.agentic_cli.provider_env import (
        PROVIDER_COMPATIBLE,
        build_provider_env,
    )

    env = build_provider_env(
        PROVIDER_COMPATIBLE,
        base_url="https://api.example.com",
        model="model-x",
        auth_token="sk-test-token",
    )
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test-token"
    assert env["ANTHROPIC_BASE_URL"] == "https://api.example.com"
    assert env["ANTHROPIC_MODEL"] == "model-x"


def test_compatible_missing_base_url_raises():
    from app.services.agentic_cli.provider_env import (
        PROVIDER_COMPATIBLE,
        build_provider_env,
    )

    with pytest.raises(ValueError):
        build_provider_env(PROVIDER_COMPATIBLE, model="m")


def test_compatible_blank_base_url_raises():
    from app.services.agentic_cli.provider_env import (
        PROVIDER_COMPATIBLE,
        build_provider_env,
    )

    with pytest.raises(ValueError):
        build_provider_env(
            PROVIDER_COMPATIBLE, base_url="   ", model="m",
        )


def test_compatible_missing_model_raises():
    from app.services.agentic_cli.provider_env import (
        PROVIDER_COMPATIBLE,
        build_provider_env,
    )

    with pytest.raises(ValueError):
        build_provider_env(
            PROVIDER_COMPATIBLE, base_url="https://api.example.com",
        )


def test_compatible_blank_auth_token_is_omitted():
    """A missing/blank key is not an error: the env var simply isn't set
    (matches the MiniMax convention — never raises on a missing secret
    because the caller may legitimately let the host env provide it)."""
    from app.services.agentic_cli.provider_env import (
        PROVIDER_COMPATIBLE,
        build_provider_env,
    )

    env = build_provider_env(
        PROVIDER_COMPATIBLE,
        base_url="https://api.example.com",
        model="m",
        auth_token="   ",
    )
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_compatible_strips_surrounding_whitespace():
    from app.services.agentic_cli.provider_env import (
        PROVIDER_COMPATIBLE,
        build_provider_env,
    )

    env = build_provider_env(
        PROVIDER_COMPATIBLE,
        base_url="  https://api.example.com  ",
        model="  m  ",
        auth_token="  tok  ",
    )
    assert env["ANTHROPIC_BASE_URL"] == "https://api.example.com"
    assert env["ANTHROPIC_MODEL"] == "m"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "tok"


def test_compatible_rejects_newline_in_base_url():
    from app.services.agentic_cli.provider_env import (
        PROVIDER_COMPATIBLE,
        build_provider_env,
    )

    with pytest.raises(ValueError):
        build_provider_env(
            PROVIDER_COMPATIBLE,
            base_url="https://api.example.com\nFOO=bar",
            model="m",
        )


def test_compatible_env_never_includes_bedrock_or_minimax_keys():
    from app.services.agentic_cli.provider_env import (
        PROVIDER_COMPATIBLE,
        build_provider_env,
    )

    env = build_provider_env(
        PROVIDER_COMPATIBLE,
        base_url="https://api.example.com",
        model="m",
        auth_token="tok",
    )
    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert "AWS_REGION" not in env
    assert "AWS_PROFILE" not in env
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in env
