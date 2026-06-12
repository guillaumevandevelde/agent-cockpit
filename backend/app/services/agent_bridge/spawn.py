"""Spawn and kill agent provider sessions in tmux."""
from __future__ import annotations

import logging
import re
import shlex
import subprocess
import uuid
from pathlib import Path

from app.services.providers import get_provider
from app.services.providers.base import SpawnCommandOptions
from app.services.providers.claude_code import ClaudeCodeProvider
from app.services.providers.platform_env import build_platform_env

logger = logging.getLogger(__name__)

_spawned_sessions: dict[str, dict] = {}


def _validate_directory(directory: str) -> str:
    dir_path = Path(directory).resolve()
    if not dir_path.is_absolute():
        raise ValueError(f"Directory must be an absolute path: {directory}")
    if ".." in Path(directory).parts:
        raise ValueError(f"Directory must not contain path traversal: {directory}")
    if not dir_path.is_dir():
        raise ValueError(f"Directory does not exist: {directory}")
    return str(dir_path)


def _sanitize_session_name(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "-", raw)[:20].strip("-")


def _running_session_names() -> set[str]:
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return set()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _session_name_for(directory: str, preferred: str | None = None) -> str:
    if preferred:
        base = _sanitize_session_name(preferred)
        if base and base not in _running_session_names():
            return base
        return f"{base or 'session'}-{uuid.uuid4().hex[:4]}"
    basename = Path(directory).name or "project"
    safe_basename = _sanitize_session_name(basename) or "project"
    return f"{safe_basename}-{uuid.uuid4().hex[:4]}"


def spawn_session(provider_id: str, options: SpawnCommandOptions, session_name: str | None = None) -> dict:
    """Spawn a new provider CLI session inside tmux."""
    provider = get_provider(provider_id)
    if isinstance(provider, ClaudeCodeProvider):
        directory = provider.resolve_directory(options)
        options = SpawnCommandOptions(**{**options.__dict__, "directory": directory})

    directory = _validate_directory(options.directory)
    options = SpawnCommandOptions(**{**options.__dict__, "directory": directory})
    preferred = session_name or (options.worktree_name if options.mode == "worktree" else None)
    name = _session_name_for(directory, preferred)
    if provider.id == "claude-code" and options.mode == "worktree" and not options.worktree_name:
        options = SpawnCommandOptions(**{**options.__dict__, "worktree_name": name})
    command = provider.build_spawn_command(options)
    shell_command = " ".join(shlex.quote(part) for part in command)

    platform_env = build_platform_env(
        options.platform,
        region=options.aws_region,
        aws_profile=options.aws_profile,
        model=options.bedrock_model,
    )
    env_flags: list[str] = []
    for key, value in platform_env.items():
        env_flags += ["-e", f"{key}={value}"]

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

    _spawned_sessions[name] = {
        "provider": provider.id,
        "mode": options.mode,
        "directory": directory,
        "worktree_name": options.worktree_name or (name if options.mode == "worktree" else None),
        "platform": options.platform,
    }

    logger.info("Spawned %s session %s in %s (mode=%s)", provider.id, name, directory, options.mode)
    return {
        "provider": provider.id,
        "provider_display_name": provider.display_name,
        "tmux_target": f"{name}:0.0",
        "session_name": name,
    }


def kill_session(session_name: str, cleanup_worktree: bool = False) -> dict:
    """Kill a tmux session and optionally clean up a Claude Code worktree."""
    metadata = _spawned_sessions.get(session_name)
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

    if cleanup_worktree and metadata and metadata.get("provider") == "claude-code" and metadata["mode"] == "worktree":
        worktree_name = metadata.get("worktree_name")
        directory = metadata["directory"]
        if worktree_name:
            try:
                subprocess.run(
                    ["git", "-C", directory, "worktree", "remove", worktree_name, "--force"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception:
                logger.warning("Failed to remove worktree %s in %s", worktree_name, directory)

    _spawned_sessions.pop(session_name, None)
    logger.info("Killed session %s", session_name)
    return {"killed": True}


def rename_session(old_name: str, new_name: str) -> dict:
    """Rename a tmux session, keeping spawn metadata under the new key."""
    sanitized = _sanitize_session_name(new_name)
    if not sanitized:
        raise ValueError("Session name must contain a letter, number, '_' or '-'")
    if sanitized == old_name:
        return {"renamed": True, "session_name": old_name, "tmux_target": f"{old_name}:0.0"}
    if sanitized in _running_session_names():
        raise ValueError(f"A session named '{sanitized}' already exists")

    try:
        result = subprocess.run(
            ["tmux", "rename-session", "-t", old_name, sanitized],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise ValueError(f"tmux rename-session failed: {result.stderr.strip()}")
    except FileNotFoundError:
        raise ValueError("tmux is not installed or not in PATH")
    except subprocess.TimeoutExpired:
        raise ValueError("tmux rename-session timed out")

    metadata = _spawned_sessions.pop(old_name, None)
    if metadata is not None:
        _spawned_sessions[sanitized] = metadata

    logger.info("Renamed session %s -> %s", old_name, sanitized)
    return {"renamed": True, "session_name": sanitized, "tmux_target": f"{sanitized}:0.0"}


def get_spawned_sessions() -> dict[str, dict]:
    return _spawned_sessions
