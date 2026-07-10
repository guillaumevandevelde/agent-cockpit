"""Agentic CLI registry — one entry per agentic coding CLI (claude-code, codex-cli, ...)."""
from __future__ import annotations

from app.services.agentic_cli.base import AgenticCli
from app.services.agentic_cli.claude_code import ClaudeCodeCli
from app.services.agentic_cli.codex_cli import CodexCli
from app.services.agentic_cli.copilot_cli import CopilotCli
from app.services.agentic_cli.mimo_code import MiMoCodeCli
from app.services.agentic_cli.open_code import OpenCodeCli

_AGENTIC_CLIS: dict[str, AgenticCli] = {
    "claude-code": ClaudeCodeCli(),
    "codex-cli": CodexCli(),
    "copilot-cli": CopilotCli(),
    "mimo-code": MiMoCodeCli(),
    "open-code": OpenCodeCli(),
}


def get_agentic_clis() -> list[AgenticCli]:
    return list(_AGENTIC_CLIS.values())


def get_agentic_cli(cli_id: str) -> AgenticCli:
    try:
        return _AGENTIC_CLIS[cli_id]
    except KeyError as exc:
        raise ValueError(f"Unknown CLI: {cli_id}") from exc