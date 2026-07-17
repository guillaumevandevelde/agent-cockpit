"""Spawn and kill Claude Code sessions in tmux.

Legacy CC-Bridge-only spawn path. Kanban dispatch (including resume/take-over
transports) calls ``app.services.runs.spawn.spawn_session`` via
``app.kanban.dispatch.make_resume_transport`` instead — that module is the
canonical spawn path for anything dispatched through the board.
"""
import json
import logging
import shlex
import subprocess
import uuid
from pathlib import Path

from app.services.agentic_cli.provider_env import _record_audit, build_spawn_env
from app.utils.git_ref import sanitize_git_branch_name

logger = logging.getLogger(__name__)

_spawned_sessions: dict[str, dict] = {}

# Default transport when ``runtime`` is omitted. Backward compat for the
# legacy CC Bridge route that doesn't yet pass the new env-injection
# kwargs. See kanban card
# `[security][D] Per-project env-injectie in spawn_session`.
_DEFAULT_RUNTIME = "worktree"


def _project_mcp_config_args(directory: str) -> list[str]:
    """Return ``--strict-mcp-config`` + ``--mcp-config`` flags for ``directory``.

    Pinning MCP servers to the project's own ``.mcp.json`` keeps a host-user's
    global ``~/.claude.json`` MCP entries — and any plugin-discovered MCPs —
    from leaking into dispatched sessions. Without these flags every extra
    tool schema lands in the system prompt of every spawned agent.

    See kanban card ``00fa8325`` / ``docs/cockpit/token-optimization-analysis.md``
    §4 R5. Single source of truth: both this legacy bridge and the newer
    ``agentic_cli/claude_code.build_spawn_command`` import the same helper so a
    security fix can't drift between paths.
    """
    return [
        "--strict-mcp-config",
        "--mcp-config",
        str(Path(directory) / ".mcp.json"),
    ]


def _resolve_project_directory(project_folder: str, session_id: str | None = None) -> str:
    """Resolve a Claude project folder name to the actual project directory.

    Prefer the selected transcript's recorded cwd. Reconstructing the path from
    Claude's folder name is lossy because both slashes and hyphens are encoded as
    hyphens.
    """
    folder_path = Path(project_folder)
    if folder_path.name != project_folder or ".." in folder_path.parts:
        raise ValueError(f"Invalid project folder: '{project_folder}'")

    if session_id:
        transcript = Path.home() / ".claude" / "projects" / project_folder / f"{session_id}.jsonl"
        if transcript.is_file():
            try:
                with transcript.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            cwd = json.loads(line).get("cwd")
                        except json.JSONDecodeError:
                            continue
                        if not cwd:
                            continue
                        resolved = Path(cwd).resolve()
                        if resolved.is_absolute() and ".." not in Path(cwd).parts and resolved.is_dir():
                            return str(resolved)
            except OSError:
                logger.warning("Could not read Claude transcript for directory resolution: %s", transcript)

    decoded = "/" + project_folder.lstrip("-").replace("-", "/")
    resolved = Path(decoded).resolve()
    # Guard against path traversal — must be an existing absolute directory
    if not resolved.is_absolute() or ".." in Path(decoded).parts:
        raise ValueError(f"Invalid project folder: '{project_folder}'")
    if resolved.is_dir():
        return str(resolved)

    raise ValueError(
        f"Could not resolve project directory for '{project_folder}'. "
        f"Please provide the directory path explicitly."
    )


def spawn_session(
    directory: str,
    mode: str = "plain",
    worktree_name: str | None = None,
    session_id: str | None = None,
    project_folder: str | None = None,
    skip_permissions: bool = False,
    extra_args: list[str] | None = None,
    *,
    project_key: str | None = None,
    runtime: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict:
    """Spawn a new Claude Code session inside a tmux session.

    Args:
        directory: Absolute path to the working directory.
        mode: One of "plain", "worktree", or "resume".
        worktree_name: Name for the worktree (mode="worktree" only).
        session_id: Claude session ID to resume (mode="resume" only).
        project_folder: Claude project folder name (for resume mode directory resolution).
        skip_permissions: Append --dangerously-skip-permissions flag.
        project_key: Optional project key (``git:host/path`` or ``slug:name``)
            — when supplied, gets injected as ``COCKPIT_PROJECT_KEY``.
        runtime: Transport hint (``worktree|sandcastle|headless|host``) —
            when supplied, gets injected as ``COCKPIT_RUNTIME``. Defaults
            to ``"worktree"`` for backward compat with existing callers.
        extra_env: Explicit env vars to inject (``-e KEY=VALUE``) — caller-
            resolved per-project secrets land here once follow-up #4 lands.
            The backend's ``os.environ`` is **never** merged in.

    Returns:
        Dict with tmux_target and session_name.

    Raises:
        ValueError: For invalid arguments.
    """
    # For resume mode, derive directory from project_folder if not provided
    if mode == "resume" and (not directory or not directory.strip()) and project_folder:
        directory = _resolve_project_directory(project_folder, session_id)

    # Validate directory — resolve to canonical path to prevent traversal attacks
    dir_path = Path(directory).resolve()
    if not dir_path.is_absolute():
        raise ValueError(f"Directory must be an absolute path: {directory}")
    if ".." in Path(directory).parts:
        raise ValueError(f"Directory must not contain path traversal: {directory}")
    if not dir_path.is_dir():
        raise ValueError(f"Directory does not exist: {directory}")
    # Use the resolved canonical path from here on
    directory = str(dir_path)

    # A user-supplied worktree name becomes a git branch — coerce it to a valid
    # ref before it reaches `--worktree`, and report whether it had to change.
    worktree_name_adjusted = False
    if mode == "worktree" and worktree_name:
        sanitized = sanitize_git_branch_name(worktree_name)
        worktree_name_adjusted = sanitized != worktree_name
        worktree_name = sanitized

    # Generate tmux session name including project directory basename
    import re
    dir_basename = dir_path.name or "project"
    # Sanitize: tmux disallows dots and colons in session names
    safe_basename = re.sub(r"[^a-zA-Z0-9_-]", "-", dir_basename)[:20]
    name = f"{safe_basename}-{uuid.uuid4().hex[:4]}"

    # Build command
    command = ["claude"]

    if mode == "plain":
        pass
    elif mode == "worktree":
        wt_name = worktree_name or name
        command += ["--worktree", wt_name]
    elif mode == "resume":
        if not session_id:
            raise ValueError("session_id is required for resume mode")
        command += ["--resume", session_id]
    else:
        raise ValueError(f"Unknown mode: {mode}")

    # Pin MCP servers to the project-`.mcp.json` only. Shared helper with
    # the agent-bridge ``claude_code.py:build_spawn_command`` so both paths
    # can't drift — see kanban card `00fa8325`.
    command += _project_mcp_config_args(directory)

    if skip_permissions:
        command.append("--dangerously-skip-permissions")

    if extra_args:
        command += extra_args

    # Build the explicit env dict for the spawned tmux session. Single
    # entry point lives in ``provider_env.build_spawn_env`` — shares the
    # extras-cleaning + cockpit-injection contract with the agent-bridge
    # ``spawn.py`` so a security fix lands in both. No provider env here
    # (the legacy CC-bridge doesn't have a provider abstraction).
    effective_runtime = runtime if runtime is not None else _DEFAULT_RUNTIME
    spawn_env = build_spawn_env(
        provider_env={},
        extra_env=extra_env,
        project_key=project_key,
        runtime=effective_runtime,
    )

    env_flags: list[str] = []
    for key, value in spawn_env.env.items():
        env_flags += ["-e", f"{key}={value}"]

    _record_audit(
        project_key=project_key,
        runtime=effective_runtime,
        session_name=name,
        env_var_names=list(spawn_env.env.keys()),
    )

    # Spawn tmux session — tmux passes shell_command to $SHELL -c, so quote args
    shell_command = " ".join(shlex.quote(part) for part in command)
    try:
        result = subprocess.run(
            ["tmux", "new-session", "-d", "-s", name, "-c", directory, *env_flags, shell_command],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise ValueError(f"tmux new-session failed: {result.stderr.strip()}")
    except FileNotFoundError:
        raise ValueError("tmux is not installed or not in PATH")
    except subprocess.TimeoutExpired:
        raise ValueError("tmux new-session timed out")

    # Store metadata
    _spawned_sessions[name] = {
        "mode": mode,
        "directory": directory,
        "worktree_name": worktree_name or (name if mode == "worktree" else None),
        "project_key": project_key,
        "runtime": effective_runtime,
        "env_var_names": spawn_env.names,
    }

    logger.info("Spawned session %s in %s (mode=%s)", name, directory, mode)
    return {
        "tmux_target": f"{name}:0.0",
        "session_name": name,
        "worktree_name": _spawned_sessions[name]["worktree_name"],
        "worktree_name_adjusted": worktree_name_adjusted,
    }


def kill_session(session_name: str, cleanup_worktree: bool = False) -> dict:
    """Kill a tmux session and optionally clean up its worktree.

    Args:
        session_name: The tmux session name to kill.
        cleanup_worktree: Remove the git worktree if applicable.

    Returns:
        Dict with killed status and optional error.
    """
    metadata = _spawned_sessions.get(session_name)

    # Kill the tmux session
    try:
        result = subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {"killed": False, "error": result.stderr.strip()}
    except FileNotFoundError:
        return {"killed": False, "error": "tmux is not installed or not in PATH"}
    except subprocess.TimeoutExpired:
        return {"killed": False, "error": "tmux kill-session timed out"}

    # Clean up worktree if requested
    if cleanup_worktree and metadata and metadata["mode"] == "worktree":
        wt_name = metadata.get("worktree_name")
        directory = metadata["directory"]
        if wt_name:
            try:
                subprocess.run(
                    ["git", "-C", directory, "worktree", "remove", wt_name, "--force"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                logger.info("Removed worktree %s in %s", wt_name, directory)
            except Exception:
                logger.warning(
                    "Failed to remove worktree %s in %s", wt_name, directory
                )

    # Remove from tracked sessions
    _spawned_sessions.pop(session_name, None)

    logger.info("Killed session %s", session_name)
    return {"killed": True}


def get_spawned_sessions() -> dict[str, dict]:
    """Return all sessions spawned by Deck."""
    return _spawned_sessions
