"""Spawn and kill agent provider sessions in tmux."""
from __future__ import annotations

import logging
import re
import shlex
import subprocess
import uuid
from pathlib import Path

from app.services.host_service import build_ssh_base
from app.services.providers import get_provider
from app.services.providers.base import SpawnCommandOptions
from app.services.providers.claude_code import ClaudeCodeProvider
from app.services.providers.platform_env import build_platform_env
from app.utils.git_ref import sanitize_git_branch_name

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


def _spawn_session_remote(
    host_data: dict,
    provider_display_name: str,
    name: str,
    directory: str,
    shell_command: str,
    env_flags: list[str],
) -> None:
    """Run tmux new-session on a remote host via SSH."""
    tmux_cmd = [
        "tmux", "new-session", "-d", "-s", name, "-c", directory,
        *env_flags, shell_command,
    ]
    ssh_cmd = build_ssh_base(host_data) + tmux_cmd
    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise ValueError(
                f"Remote tmux new-session failed on {host_data['alias']}: "
                f"{result.stderr.strip()}"
            )
    except FileNotFoundError:
        raise ValueError("ssh is not installed or not in PATH")
    except subprocess.TimeoutExpired:
        raise ValueError("Remote tmux new-session timed out")
    logger.info(
        "Spawned remote session %s on %s (dir=%s)",
        name, host_data["alias"], directory,
    )


def spawn_session(
    provider_id: str,
    options: SpawnCommandOptions,
    session_name: str | None = None,
    host_data: dict | None = None,
) -> dict:
    """Spawn a new provider CLI session inside tmux.

    When *host_data* is provided the session is spawned on that remote host
    via SSH instead of locally.
    """
    provider = get_provider(provider_id)
    if isinstance(provider, ClaudeCodeProvider):
        directory = provider.resolve_directory(options)
        options = SpawnCommandOptions(**{**options.__dict__, "directory": directory})

    directory = _validate_directory(options.directory)
    options = SpawnCommandOptions(**{**options.__dict__, "directory": directory})

    # A user-supplied worktree name becomes a git branch — coerce it to a valid
    # ref before it reaches `--worktree`, and report whether it had to change.
    worktree_name_adjusted = False
    if options.mode == "worktree" and options.worktree_name:
        sanitized = sanitize_git_branch_name(options.worktree_name)
        worktree_name_adjusted = sanitized != options.worktree_name
        options = SpawnCommandOptions(**{**options.__dict__, "worktree_name": sanitized})

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

    if host_data:
        _spawn_session_remote(host_data, provider.display_name, name, directory, shell_command, env_flags)
    else:
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
        "worktree_path": options.worktree_path,
        "repo_path": options.repo_path,
        "platform": options.platform,
        "host_id": host_data["id"] if host_data else None,
        "host_alias": host_data["alias"] if host_data else None,
    }

    logger.info(
        "%s %s session %s in %s (mode=%s)",
        "Remotely spawned" if host_data else "Spawned",
        provider.id, name, directory, options.mode,
    )

    display_name = f"{provider.display_name} ({host_data['alias']})" if host_data else provider.display_name
    return {
        "provider": provider.id,
        "provider_display_name": display_name,
        "tmux_target": f"{name}:0.0",
        "session_name": name,
        "worktree_name": _spawned_sessions[name]["worktree_name"],
        "worktree_name_adjusted": worktree_name_adjusted,
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

    if (
        cleanup_worktree
        and metadata
        and metadata.get("worktree_path")
        and metadata.get("repo_path")
    ):
        try:
            subprocess.run(
                ["git", "-C", metadata["repo_path"], "worktree", "remove",
                 metadata["worktree_path"], "--force"],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            logger.warning("Failed to remove dispatcher worktree %s",
                           metadata["worktree_path"])

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
