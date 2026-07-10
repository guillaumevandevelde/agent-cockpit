"""Compatibility wrappers for agent bridge discovery."""
from __future__ import annotations

import logging

from app.models.constants import SessionStatus
from app.services.agent_bridge.discovery import (
    capture_pane_preview,  # noqa: F401  (re-exported for cc_bridge.router)
    discover_agent_sessions,
)
from app.services.agentic_cli import get_agentic_cli

logger = logging.getLogger(__name__)

def _is_claude_code(command: str, pid: str = "") -> bool:
    """Check if a tmux pane is running Claude Code."""
    return get_agentic_cli("claude-code").is_process_match(command, pid)


def _build_session_info(pane, window, session) -> dict:
    """Build a legacy session dict from libtmux-like objects used in tests."""
    return {
        "cli": "claude-code",
        "cli_display_name": "Claude Code",
        "tmux_target": f"{session.session_name}:{window.window_index}.{pane.pane_index}",
        "session_name": session.session_name,
        "window_name": window.window_name,
        "pane_id": pane.pane_id,
        "cwd": pane.pane_current_path,
        "pid": pane.pane_pid,
        "status": SessionStatus.ACTIVE,
    }


def discover_cc_sessions() -> list[dict]:
    """Find all supported agent panes.

    The CC Bridge route remains for compatibility, but discovery is now mixed
    CLI-aware so Claude Code and Codex sessions appear together.
    """
    return discover_agent_sessions()
