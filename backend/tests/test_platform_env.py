"""Tests for platform -> environment-variable mapping."""
import pytest


def test_anthropic_returns_empty_env():
    from app.services.providers.platform_env import PLATFORM_ANTHROPIC, build_platform_env

    assert build_platform_env(PLATFORM_ANTHROPIC) == {}


def test_unknown_platform_returns_empty_env():
    from app.services.providers.platform_env import build_platform_env

    assert build_platform_env("vertex") == {}


def test_bedrock_minimal_sets_use_bedrock_flag():
    from app.services.providers.platform_env import PLATFORM_BEDROCK, build_platform_env

    assert build_platform_env(PLATFORM_BEDROCK) == {"CLAUDE_CODE_USE_BEDROCK": "1"}


def test_bedrock_with_all_fields():
    from app.services.providers.platform_env import PLATFORM_BEDROCK, build_platform_env

    env = build_platform_env(
        PLATFORM_BEDROCK,
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
    from app.services.providers.platform_env import PLATFORM_BEDROCK, build_platform_env

    env = build_platform_env(PLATFORM_BEDROCK, region="  ", aws_profile="", model=None)
    assert env == {"CLAUDE_CODE_USE_BEDROCK": "1"}


def test_bedrock_strips_surrounding_whitespace():
    from app.services.providers.platform_env import PLATFORM_BEDROCK, build_platform_env

    env = build_platform_env(PLATFORM_BEDROCK, region="  us-west-2  ")
    assert env["AWS_REGION"] == "us-west-2"


def test_bedrock_rejects_newline_in_value():
    from app.services.providers.platform_env import PLATFORM_BEDROCK, build_platform_env

    with pytest.raises(ValueError):
        build_platform_env(PLATFORM_BEDROCK, region="us-east-1\nFOO=bar")


def test_bedrock_rejects_null_byte_in_value():
    from app.services.providers.platform_env import PLATFORM_BEDROCK, build_platform_env

    with pytest.raises(ValueError):
        build_platform_env(PLATFORM_BEDROCK, model="bad\x00value")


def test_minimax_minimal_uses_international_defaults():
    from app.services.providers.platform_env import (
        MINIMAX_AUTO_COMPACT_WINDOW,
        MINIMAX_BASE_URL_INTERNATIONAL,
        MINIMAX_DEFAULT_MODEL,
        PLATFORM_MINIMAX,
        build_platform_env,
    )

    env = build_platform_env(PLATFORM_MINIMAX)
    assert env == {
        "ANTHROPIC_BASE_URL": MINIMAX_BASE_URL_INTERNATIONAL,
        "ANTHROPIC_MODEL": MINIMAX_DEFAULT_MODEL,
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": MINIMAX_AUTO_COMPACT_WINDOW,
    }


def test_minimax_with_api_key_sets_auth_token():
    from app.services.providers.platform_env import PLATFORM_MINIMAX, build_platform_env

    env = build_platform_env(PLATFORM_MINIMAX, minimax_api_key="sk-test-key")
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test-key"


def test_minimax_blank_api_key_is_omitted():
    from app.services.providers.platform_env import PLATFORM_MINIMAX, build_platform_env

    env = build_platform_env(PLATFORM_MINIMAX, minimax_api_key="   ")
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_minimax_base_url_is_configurable_for_china_region():
    from app.services.providers.platform_env import (
        MINIMAX_BASE_URL_CHINA,
        PLATFORM_MINIMAX,
        build_platform_env,
    )

    env = build_platform_env(PLATFORM_MINIMAX, minimax_base_url=MINIMAX_BASE_URL_CHINA)
    assert env["ANTHROPIC_BASE_URL"] == MINIMAX_BASE_URL_CHINA


def test_minimax_model_override():
    from app.services.providers.platform_env import PLATFORM_MINIMAX, build_platform_env

    env = build_platform_env(PLATFORM_MINIMAX, model="MiniMax-M3")
    assert env["ANTHROPIC_MODEL"] == "MiniMax-M3"


def test_minimax_strips_surrounding_whitespace():
    from app.services.providers.platform_env import PLATFORM_MINIMAX, build_platform_env

    env = build_platform_env(
        PLATFORM_MINIMAX,
        minimax_api_key="  sk-test-key  ",
        minimax_base_url="  https://api.minimax.io/anthropic  ",
    )
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test-key"
    assert env["ANTHROPIC_BASE_URL"] == "https://api.minimax.io/anthropic"


def test_minimax_rejects_newline_in_api_key():
    from app.services.providers.platform_env import PLATFORM_MINIMAX, build_platform_env

    with pytest.raises(ValueError):
        build_platform_env(PLATFORM_MINIMAX, minimax_api_key="sk-test\nFOO=bar")


def test_minimax_rejects_null_byte_in_base_url():
    from app.services.providers.platform_env import PLATFORM_MINIMAX, build_platform_env

    with pytest.raises(ValueError):
        build_platform_env(PLATFORM_MINIMAX, minimax_base_url="bad\x00value")


def test_minimax_env_never_includes_bedrock_keys():
    from app.services.providers.platform_env import PLATFORM_MINIMAX, build_platform_env

    env = build_platform_env(PLATFORM_MINIMAX)
    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert "AWS_REGION" not in env
    assert "AWS_PROFILE" not in env
