"""Resolve a project directory to a live tmux target, or spawn one."""
import logging
import os

from app.services.agent_bridge.discovery import discover_agent_sessions
from app.services.cc_bridge.spawn import spawn_session
from app.services.scheduling.session_registry import session_registry

logger = logging.getLogger(__name__)

# Sentinel: the registry is cold and >1 live claude pane shares the cwd, so we
# cannot safely tell which one is the target — refuse rather than risk a fork.
AMBIGUOUS = object()


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


def resolve_session_target(session_id: str, cwd: str):
    """Resolve a specific session to its live tmux target.

    Returns the tmux_target of the session's live pane, None if it has exited,
    or AMBIGUOUS when the registry is cold and the cwd has >1 live claude pane.
    """
    sessions = discover_agent_sessions()
    pane_id = session_registry.pane_for(session_id)
    if pane_id:
        for s in sessions:
            if s.get("pane_id") == pane_id:
                return s.get("tmux_target")
        return None  # we knew the pane; it's gone -> exited
    want = os.path.normpath(cwd)
    matches = [s for s in sessions if os.path.normpath(s.get("cwd", "")) == want]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0].get("tmux_target")
    return AMBIGUOUS


def resume_spawn_for(session_id: str, project_folder: str, cwd: str,
                     permission_mode: str) -> str:
    """Relaunch a specific session with `claude --resume <id>` and return its target."""
    result = spawn_session(
        directory=cwd,
        mode="resume",
        session_id=session_id,
        project_folder=project_folder,
        extra_args=permission_flags(permission_mode),
    )
    return result["tmux_target"]
