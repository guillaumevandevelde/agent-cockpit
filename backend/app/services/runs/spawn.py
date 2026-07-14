"""Spawn and kill agent CLI sessions in tmux."""
from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path

from app.config import settings
from app.services.agentic_cli import get_agentic_cli
from app.services.agentic_cli.base import SpawnCommandOptions
from app.services.agentic_cli.claude_code import ClaudeCodeCli
from app.services.agentic_cli.provider_env import _clean, build_provider_env
from app.services.host_service import build_ssh_base
from app.utils.git_ref import sanitize_git_branch_name

logger = logging.getLogger(__name__)

_spawned_sessions: dict[str, dict] = {}

# Default transport when the caller doesn't pass ``runtime``. Backward
# compat for every existing call-site (tests, dispatcher, REST bridge)
# that omits the new ``runtime`` kwarg. See kanban card
# `[security][D] Per-project env-injectie in spawn_session`.
_DEFAULT_RUNTIME = "worktree"

# Audit-log sink for security-relevant spawn events. Persists one row per
# invocation to the ``security_audit`` table; best-effort by design —
# callers must not depend on this hook returning anything (always
# callable, never raises) and var *names* are the only stable identifier
# (no secret values).
#
# The function is **sync** because it lives in a sync call-site
# (``spawn_session`` runs tmux via subprocess, not in an await chain).
# The actual DB write is dispatched to the running event loop via
# ``asyncio.ensure_future``; if no loop is running (sync tooling,
# test fixtures without an async context), the write is dropped — the
# ``logger.info`` line is the contract for that case.
#
# The function also still emits the structured ``logger.info`` line that
# ``test_runs_spawn_env_isolation`` and the grep-style tooling rely on,
# so an upgrade to a real table doesn't drop the log signal.
def _record_audit(
    project_key: str | None,
    runtime: str | None,
    session_name: str,
    env_var_names: list[str],
    kind: str = "env_inject",
) -> None:
    """Insert one ``security_audit`` row for an env-inject / run-start / run-stop."""
    if not project_key:
        # Without a project_key we have no scope to audit against; skip
        # rather than emit a row that's orphaned in the table.
        return
    logger.info(
        "%s project_key=%s runtime=%s session=%s vars=%s",
        kind,
        project_key,
        runtime or "-",
        session_name,
        sorted(env_var_names),
    )
    try:
        import asyncio

        from app.database import AsyncSessionLocal
        from app.models.security_audit import SecurityAuditKind
        from app.services.security_audit_service import record

        async def _do_record() -> None:
            async with AsyncSessionLocal() as db:
                await record(
                    db,
                    kind=SecurityAuditKind(kind),
                    project_key=project_key,
                    actor="run-service",
                    payload_ref={
                        "session_or_instance": session_name,
                        "runtime": runtime,
                        "env_var_names": sorted(env_var_names),
                    },
                )
                await db.commit()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            # Inside an event loop — schedule without blocking the
            # originating sync call. Tests can monkeypatch this helper
            # directly (see test_runs_spawn_env_isolation) when they
            # need to capture the audit payload without a DB.
            loop.create_task(_do_record())
    except Exception:
        logger.exception(
            "security_audit insert failed (kind=%s project_key=%s)",
            kind,
            project_key,
        )


def _clean_extra_env(extra_env: dict[str, str] | None) -> dict[str, str]:
    """Validate caller-supplied env values; reject control characters.

    Mirrors ``_clean`` in ``provider_env``: any value containing ``\n``,
    ``\r``, or ``\x00`` raises ``ValueError`` before it reaches tmux's
    argv. An empty/None input returns an empty dict.
    """
    if not extra_env:
        return {}
    cleaned: dict[str, str] = {}
    for key, value in extra_env.items():
        if not isinstance(key, str) or not key:
            raise ValueError("Environment key must be a non-empty string")
        if not isinstance(value, str):
            raise ValueError(f"Environment value for {key!r} must be a string")
        # Reuse _clean for the value validation so the rule is one
        # place to audit when tightening later.
        _clean(value)
        cleaned[key] = value
    return cleaned


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


def _prompt_file_shell_command(command: list[str], prompt: str) -> tuple[str, str]:
    """Deliver ``prompt`` to the tmux pane via a temp file instead of inlining it.

    tmux caps a single ``new-session`` command at ~16KB (its imsg buffer) and
    rejects anything larger with ``command too long``. Rendered card prompts —
    especially executor child cards that prepend an 8KB+ ``PLAN CONTEXT`` section
    to an already ~11KB base prompt — routinely blow past that, which made
    ``spawn_session`` raise and the kanban dispatcher loop the card into
    Impediment with only a generic "check the logs" comment. Writing the prompt to
    a temp file and having the pane's shell ``cat`` it back keeps the tmux command
    line tiny while claude still receives the full prompt (subject only to
    ARG_MAX, ~2MB). The pane removes the file as soon as it has read it; the
    caller unlinks it if the spawn never reaches the pane.

    The prompt token is substituted in place wherever the CLI put it (trailing
    positional for claude/codex/mimo, ``--prompt``/``-i`` for opencode/copilot),
    so this works regardless of prompt placement. ``$(cat …)`` strips any trailing
    newline from the delivered prompt, which is immaterial for an agent prompt.
    """
    fd, path = tempfile.mkstemp(prefix="cck-prompt-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(prompt)
    parts = [
        '"$CCK_PROMPT"' if part == prompt else shlex.quote(part)
        for part in command
    ]
    qpath = shlex.quote(path)
    shell_command = (
        f'CCK_PROMPT="$(cat {qpath})"; rm -f {qpath}; exec ' + " ".join(parts)
    )
    return shell_command, path


def _unlink_prompt_file(path: str | None) -> None:
    """Best-effort remove a prompt temp file after a spawn that never ran the pane."""
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def _spawn_session_remote(
    host_data: dict,
    cli_display_name: str,
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
    cli_id: str,
    options: SpawnCommandOptions,
    session_name: str | None = None,
    host_data: dict | None = None,
    *,
    project_key: str | None = None,
    runtime: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict:
    """Spawn a new agentic CLI session inside tmux.

    When *host_data* is provided the session is spawned on that remote host
    via SSH instead of locally.

    The spawned tmux session receives an **explicit env** built from:

    1. ``extra_env`` — caller-resolved secrets (today a dict; once
       follow-up #4 lands this is where ``SecretStore.get(project_key, ...)``
       results land).
    2. Provider env from ``build_provider_env`` (Bedrock/AWS_REGION or
       MiniMax creds).
    3. ``COCKPIT_PROJECT_KEY=<project_key>`` when supplied.
    4. ``COCKPIT_RUNTIME=<runtime>`` when supplied (defaults to
       ``"worktree"`` for backward compat with existing callers).

    The backend's ``os.environ`` is **never** merged into the spawn —
    this is the security fix the card mandates. Host vars (e.g.
    ``AWS_*``, ``GITHUB_TOKEN``) only reach the agent if the caller
    explicitly passed them via ``extra_env`` or the provider-env builder.
    """
    cli = get_agentic_cli(cli_id)
    if isinstance(cli, ClaudeCodeCli):
        directory = cli.resolve_directory(options)
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
    if cli.id == "claude-code" and options.mode == "worktree" and not options.worktree_name:
        options = SpawnCommandOptions(**{**options.__dict__, "worktree_name": name})
    command = cli.build_spawn_command(options)
    shell_command = " ".join(shlex.quote(part) for part in command)

    provider_env = build_provider_env(
        options.provider,
        region=options.aws_region,
        aws_profile=options.aws_profile,
        model=options.bedrock_model,
        minimax_api_key=settings.minimax_api_key,
        minimax_base_url=options.minimax_base_url or settings.minimax_base_url,
        cli_id=cli.id,
    )

    # Build the explicit env dict for the spawned tmux session.
    # NO ``os.environ.update`` — every var must come from an explicit,
    # auditable input. Order: extras (project secrets) < provider env
    # < cockpit context vars; later writes win on collision (which is
    # fine: ``COCKPIT_*`` won't collide with provider/secrets vars).
    effective_runtime = runtime if runtime is not None else _DEFAULT_RUNTIME
    cleaned_extras = _clean_extra_env(extra_env)
    merged_env: dict[str, str] = {}
    merged_env.update(cleaned_extras)
    merged_env.update(provider_env)
    if project_key is not None:
        merged_env["COCKPIT_PROJECT_KEY"] = project_key
    if effective_runtime is not None:
        merged_env["COCKPIT_RUNTIME"] = effective_runtime

    env_flags: list[str] = []
    for key, value in merged_env.items():
        env_flags += ["-e", f"{key}={value}"]

    _record_audit(
        project_key=project_key,
        runtime=effective_runtime,
        session_name=name,
        env_var_names=list(merged_env.keys()),
    )

    if host_data:
        # Remote still inlines the command over SSH — a separate transport with
        # its own arg limits; the tmux ~16KB imsg cap is a local-socket concern.
        _spawn_session_remote(host_data, cli.display_name, name, directory, shell_command, env_flags)
    else:
        # Deliver the prompt via a temp file so a large prompt never overflows
        # tmux's ~16KB command-line limit ("command too long"). See
        # _prompt_file_shell_command.
        prompt_file: str | None = None
        if options.prompt:
            shell_command, prompt_file = _prompt_file_shell_command(command, options.prompt)
        try:
            result = subprocess.run(
                ["tmux", "new-session", "-d", "-s", name, "-c", directory, *env_flags, shell_command],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                raise ValueError(f"tmux new-session failed: {result.stderr.strip()}")
        except FileNotFoundError as exc:
            _unlink_prompt_file(prompt_file)
            raise ValueError("tmux is not installed or not in PATH") from exc
        except subprocess.TimeoutExpired as exc:
            _unlink_prompt_file(prompt_file)
            raise ValueError("tmux new-session timed out") from exc
        except Exception:
            # tmux returned non-zero (or any other failure): the pane never ran,
            # so it will not remove the prompt file — clean it up here.
            _unlink_prompt_file(prompt_file)
            raise

    _spawned_sessions[name] = {
        "cli": cli.id,
        "mode": options.mode,
        "directory": directory,
        "worktree_name": options.worktree_name or (name if options.mode == "worktree" else None),
        "worktree_path": options.worktree_path,
        "repo_path": options.repo_path,
        "provider": options.provider,
        "host_id": host_data["id"] if host_data else None,
        "host_alias": host_data["alias"] if host_data else None,
        "project_key": project_key,
        "runtime": effective_runtime,
        "env_var_names": sorted(merged_env.keys()),
    }

    logger.info(
        "%s %s session %s in %s (mode=%s)",
        "Remotely spawned" if host_data else "Spawned",
        cli.id, name, directory, options.mode,
    )

    display_name = f"{cli.display_name} ({host_data['alias']})" if host_data else cli.display_name
    return {
        "cli": cli.id,
        "cli_display_name": display_name,
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

    if cleanup_worktree and metadata and metadata.get("cli") == "claude-code" and metadata["mode"] == "worktree":
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
