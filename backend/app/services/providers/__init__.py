"""Provider registry."""
from __future__ import annotations

from app.services.providers.base import AgentProvider
from app.services.providers.claude_code import ClaudeCodeProvider
from app.services.providers.codex_cli import CodexCliProvider
from app.services.providers.mimo_code import MiMoCodeProvider
from app.services.providers.open_code import OpenCodeProvider

_PROVIDERS: dict[str, AgentProvider] = {
    "claude-code": ClaudeCodeProvider(),
    "codex-cli": CodexCliProvider(),
    "mimo-code": MiMoCodeProvider(),
    "open-code": OpenCodeProvider(),
}


def get_providers() -> list[AgentProvider]:
    return list(_PROVIDERS.values())


def get_provider(provider_id: str) -> AgentProvider:
    try:
        return _PROVIDERS[provider_id]
    except KeyError as exc:
        raise ValueError(f"Unknown provider: {provider_id}") from exc

