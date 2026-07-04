"""Map an Agent Bridge platform selection to process environment variables.

Single source of truth for platform -> env mapping. This module never
resolves or stores secrets: for Bedrock only non-secret configuration
(region, profile name, model id) is set and the AWS SDK credential chain on
the host resolves actual creds; for MiniMax the API key must be resolved by
the caller (e.g. a secrets store) and passed in as ``minimax_api_key``.
"""
from __future__ import annotations
import logging


logger = logging.getLogger(__name__)
PLATFORM_ANTHROPIC = "anthropic"
PLATFORM_BEDROCK = "bedrock"
PLATFORM_MINIMAX = "minimax"

MINIMAX_BASE_URL_INTERNATIONAL = "https://api.minimax.io/anthropic"
MINIMAX_BASE_URL_CHINA = "https://api.minimaxi.com/anthropic"
MINIMAX_DEFAULT_MODEL = "MiniMax-M3[1m]"
MINIMAX_AUTO_COMPACT_WINDOW = "1000000"


def _clean(value: str | None) -> str | None:
    """Trim a value and reject control characters that break env injection."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if "\n" in stripped or "\r" in stripped or "\x00" in stripped:
        raise ValueError("Environment value must not contain newlines or null bytes")
    return stripped


def build_platform_env(
    platform: str | None,
    region: str | None = None,
    aws_profile: str | None = None,
    model: str | None = None,
    minimax_api_key: str | None = None,
    minimax_base_url: str | None = None,
) -> dict[str, str]:
    """Return the env vars for a platform selection (empty for Anthropic).

    ``minimax_api_key`` is the caller-resolved credential (e.g. from a secrets
    store); this function never hardcodes or looks up secrets itself.
    """
    if platform == PLATFORM_BEDROCK:
        env: dict[str, str] = {"CLAUDE_CODE_USE_BEDROCK": "1"}
        cleaned_region = _clean(region)
        if cleaned_region:
            env["AWS_REGION"] = cleaned_region
        cleaned_profile = _clean(aws_profile)
        if cleaned_profile:
            env["AWS_PROFILE"] = cleaned_profile
        cleaned_model = _clean(model)
        if cleaned_model:
            env["ANTHROPIC_MODEL"] = cleaned_model
        return env

    if platform == PLATFORM_MINIMAX:
        # Always set base URL/model explicitly (never conditionally) so a
        # stale ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN inherited from the
        # session's ambient environment can't leak through from a previous
        # platform choice, per MiniMax's own docs warning about conflicts.
        env = {
            "ANTHROPIC_BASE_URL": _clean(minimax_base_url) or MINIMAX_BASE_URL_INTERNATIONAL,
            "ANTHROPIC_MODEL": _clean(model) or MINIMAX_DEFAULT_MODEL,
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": MINIMAX_AUTO_COMPACT_WINDOW,
        }
        cleaned_key = _clean(minimax_api_key)
        if cleaned_key:
            env["ANTHROPIC_AUTH_TOKEN"] = cleaned_key
        return env

    return {}
