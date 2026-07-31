"""Discover agent CLI sessions running in tmux."""
from __future__ import annotations

import logging
import subprocess
from typing import Any

from app.models.constants import SessionStatus
from app.services.agentic_cli import get_agentic_cli, get_agentic_clis
from app.services.agentic_cli.base import AgenticCli
from app.services.agentic_cli.provider_detect import detect_session_provider

logger = logging.getLogger(__name__)

_PANE_FORMAT = (
    "#{session_name}:#{window_index}.#{pane_index}"
    "|#{session_name}"
    "|#{window_name}"
    "|#{pane_id}"
    "|#{pane_current_path}"
    "|#{pane_pid}"
    "|#{pane_current_command}"
)


def _build_session_info_from_parts(
    *,
    target: str,
    session_name: str,
    window_name: str,
    pane_id: str,
    cwd: str,
    pid: str,
    cli: AgenticCli,
) -> dict[str, Any]:
    # Vendor detection is best-effort: a session whose /proc we can't read
    # falls back to the CLI's own identity (Codex → "Codex", etc.) rather
    # than a fabricated "Anthropic" — so this never lies about the
    # subscription the user actually has running.
    provider_id, provider_display_name = detect_session_provider(
        pid, cli_id=cli.id, cli_display_name=cli.display_name
    )
    return {
        "cli": cli.id,
        "cli_display_name": cli.display_name,
        "provider": provider_id,
        "provider_display_name": provider_display_name,
        "tmux_target": target,
        "session_name": session_name,
        "window_name": window_name,
        "pane_id": pane_id,
        "cwd": cwd,
        "pid": pid,
        "status": SessionStatus.ACTIVE,
    }


def discover_agent_sessions(cli_id: str | None = None) -> list[dict[str, Any]]:
    """Find all tmux panes running supported agentic CLIs."""
    clis = [get_agentic_cli(cli_id)] if cli_id else get_agentic_clis()
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", _PANE_FORMAT],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.debug("tmux list-panes failed: %s", result.stderr.strip())
            return []
    except FileNotFoundError:
        logger.debug("tmux not found")
        return []
    except subprocess.TimeoutExpired:
        logger.warning("tmux list-panes timed out")
        return []

    sessions: list[dict[str, Any]] = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|", 6)
        if len(parts) != 7:
            continue
        target, session_name, window_name, pane_id, cwd, pid, command = parts
        for cli in clis:
            if cli.is_process_match(command, pid):
                sessions.append(
                    _build_session_info_from_parts(
                        target=target,
                        session_name=session_name,
                        window_name=window_name,
                        pane_id=pane_id,
                        cwd=cwd,
                        pid=pid,
                        cli=cli,
                    )
                )
                break
    return sessions


def capture_pane_preview(target: str) -> str:
    """Capture the current visible content of a tmux pane."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", target, "-p", "-e"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""

