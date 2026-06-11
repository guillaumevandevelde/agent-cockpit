"""Resolve a project directory to a live tmux target, or spawn one."""
import logging
import os

from app.services.agent_bridge.discovery import discover_agent_sessions
from app.services.cc_bridge.spawn import spawn_session

logger = logging.getLogger(__name__)


def permission_flags(permission_mode: str) -> list[str]:
    if permission_mode == "acceptEdits":
        return ["--permission-mode", "acceptEdits"]
    if permission_mode == "bypass":
        return ["--dangerously-skip-permissions"]
    return []  # default


def resolve_target(project_dir: str) -> str | None:
    """Return the tmux_target of a live CC session whose cwd matches project_dir."""
    want = os.path.normpath(project_dir)
    for s in discover_agent_sessions():
        if os.path.normpath(s.get("cwd", "")) == want:
            return s.get("tmux_target")
    return None


def spawn_for(project_dir: str, permission_mode: str) -> str:
    """Spawn a new CC session in project_dir and return its tmux_target."""
    result = spawn_session(
        directory=project_dir,
        mode="plain",
        extra_args=permission_flags(permission_mode),
    )
    return result["tmux_target"]
