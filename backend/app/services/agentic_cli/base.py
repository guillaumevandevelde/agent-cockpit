"""Agentic CLI abstraction — base class shared by every supported CLI."""
from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.agentic_cli.capabilities import capability_flags, normalize_capability_matrix

logger = logging.getLogger(__name__)

_MAX_TREE_DEPTH = 4


@dataclass(frozen=True)
class SpawnCommandOptions:
    """CLI-neutral launch options used by the tmux bridge."""

    directory: str
    mode: str = "plain"
    worktree_name: str | None = None
    worktree_path: str | None = None
    repo_path: str | None = None
    session_id: str | None = None
    project_folder: str | None = None
    skip_permissions: bool = False
    # MCP tool name (e.g. ``mcp__cockpit-kanban__permission_prompt``) that
    # Claude Code should invoke when a permission decision is required. When
    # set AND ``skip_permissions=False``, the cli-builder emits
    # ``--permission-prompt-tool <name>`` so Claude Code's permission system
    # has an answer channel under autonomous dispatch (analysis doc
    # ``docs/cockpit/approval-privilege-separation-analyse.md`` §4).
    permission_prompt_tool: str | None = None
    prompt: str | None = None
    model: str | None = None
    profile: str | None = None
    profile_v2: str | None = None
    sandbox: str | None = None
    approval_policy: str | None = None
    search: bool | None = None
    no_alt_screen: bool = False
    dangerously_bypass_approvals_and_sandbox: bool = False
    use_last: bool = False
    provider: str = "anthropic"
    aws_region: str | None = None
    aws_profile: str | None = None
    bedrock_model: str | None = None
    minimax_base_url: str | None = None
    # Data-driven Anthropic-compatible endpoint (see
    # ``app.services.agentic_cli.endpoints``): when ``provider ==
    # "anthropic-compatible"`` the API layer resolves the named endpoint
    # row and threads base_url + auth_token through here, so the
    # provider-env builder never has to touch the DB. ``endpoint_name``
    # is the slug from the registry; it stays here for audit logs.
    endpoint_name: str | None = None
    endpoint_base_url: str | None = None
    endpoint_auth_token: str | None = None
    host_id: int | None = None
    agent: str | None = None
    context_tier: str | None = None
    reasoning_effort: str | None = None
    plan: bool = False
    remote: bool | None = None
    allow_all: bool = False
    no_ask_user: bool = False


ResumeTarget = tuple[str, str | None]


def _resolve_sqlite_resume_target(
    database_path: Path,
    worktree_path: Path,
) -> tuple[str, str] | None:
    """Resolve the newest active session recorded for one exact worktree."""
    if not database_path.is_file() or not worktree_path.is_dir():
        return None

    resolved_worktree = worktree_path.resolve()
    try:
        connection = sqlite3.connect(
            f"{database_path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=1,
        )
        try:
            row = connection.execute(
                "SELECT id FROM session WHERE directory = ? "
                "AND parent_id IS NULL AND time_archived IS NULL "
                "ORDER BY time_updated DESC LIMIT 1",
                (str(resolved_worktree),),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.warning(
            "could not read resume session store %s",
            database_path,
            exc_info=True,
        )
        return None

    if row is None:
        return None
    return str(row[0]), str(resolved_worktree)


def argv0_name(command: str) -> str:
    """Return the executable basename from a command or argv0 string."""
    if not command:
        return ""
    return Path(command.strip().split()[0]).name.lower()


def has_binary_descendant(
    pid: str,
    binary_names: set[str],
    *,
    excluded_names: set[str] | None = None,
    _depth: int = 0,
    _visited: set[str] | None = None,
) -> bool:
    """Walk a process tree looking for a descendant executable name."""
    if _depth > _MAX_TREE_DEPTH or not pid.isdigit():
        return False
    if _visited is None:
        _visited = set()
    if pid in _visited:
        return False
    _visited.add(pid)

    excluded_names = excluded_names or set()
    try:
        result = subprocess.run(
            ["pgrep", "-a", "-P", pid],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return False

    for line in result.stdout.strip().splitlines():
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        child_pid, cmdline = parts
        name = argv0_name(cmdline)
        if name in binary_names and name not in excluded_names:
            return True
        if has_binary_descendant(
            child_pid,
            binary_names,
            excluded_names=excluded_names,
            _depth=_depth + 1,
            _visited=_visited,
        ):
            return True
    return False


class AgenticCli(ABC):
    """Base class for CLI-specific behavior."""

    id: str
    display_name: str
    binary_name: str
    version_args: tuple[str, ...] = ("--version",)
    supports_resume_resolution: bool = False
    # True when this CLI exposes a per-worktree transcript file the dispatch
    # reaper can use as a "mid-session detector owns the limit" signal. Only
    # Claude Code does today; the rest of the lanes return None and let the
    # reaper fall through to its alive-skip branch (no false-positive pane
    # substring-scan). Mirrors ``supports_resume_resolution`` — a future CLI
    # that ships a session store we can glob for just sets the flag and
    # overrides ``resolve_transcript_file``.
    supports_transcript_resolution: bool = False

    def resolve_resume_target(
        self,
        worktree_path: Path,
        *,
        data_dir: Path | None = None,
    ) -> ResumeTarget | None:
        """Find this CLI's session for ``worktree_path`` when supported."""
        return None

    def resolve_transcript_file(
        self,
        worktree_path: Path,
        *,
        data_dir: Path | None = None,
    ) -> Path | None:
        """Return the worktree's transcript file path when supported, else None.

        Lifted onto the base class for symmetry with ``resolve_resume_target``
        so per-CLI routing in the reaper can read the same shape from every
        adapter without special-casing Claude. The default returns None —
        the reaper treats "no transcript signal" as "skip the pane scan
        fallback" rather than as a green light to fire it.
        """
        return None

    def resolve_directory(self, options: SpawnCommandOptions) -> str:
        """Resolve an opaque resume target back to its original directory."""
        if options.mode == "resume" and options.project_folder:
            candidate = Path(options.project_folder).expanduser()
            if candidate.is_absolute():
                resolved = candidate.resolve()
                if not resolved.is_dir():
                    raise ValueError(f"Resume directory does not exist: '{candidate}'")
                return str(resolved)
        return options.directory

    def get_capabilities(self) -> dict[str, bool]:
        """Return backward-compatible feature support flags."""
        return capability_flags(self.id)

    def get_capability_matrix(self) -> dict[str, dict[str, Any]]:
        """Return detailed capability metadata."""
        return normalize_capability_matrix(self.id)

    def get_capability_details(self) -> dict[str, dict[str, str]]:
        """Return richer capability metadata for UI/action state."""
        return self.get_capability_matrix()

    @abstractmethod
    def get_config_paths(self, project_path: str | None = None) -> dict[str, Any]:
        """Return important config paths for this CLI."""

    @abstractmethod
    def is_process_match(self, command: str, pid: str) -> bool:
        """Return True when a tmux pane belongs to this CLI."""

    @abstractmethod
    def build_spawn_command(self, options: SpawnCommandOptions) -> list[str]:
        """Build the CLI command for a tmux session."""

    @abstractmethod
    def get_allowed_cli_commands(self) -> list[str]:
        """Return safe command names exposed by this CLI's API."""

    def get_backup_policy(self) -> dict[str, Any] | None:
        """Return backup/export/restore policy metadata, if defined."""
        return None

    def get_version(self) -> str | None:
        """Read the installed CLI version, if available."""
        binary_path = shutil.which(self.binary_name)
        if not binary_path:
            return None

        try:
            result = subprocess.run(
                [binary_path, *self.version_args],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None

        output = f"{result.stdout}\n{result.stderr}"
        match = re.search(r"(\d+\.\d+\.\d+(?:[-+][\w.]+)?)", output)
        return match.group(1) if match else output.strip().splitlines()[0] if output.strip() else None

    def get_status(self) -> dict[str, Any]:
        """Return install/version status suitable for the API."""
        binary_path = shutil.which(self.binary_name)
        version = self.get_version() if binary_path else None
        return {
            "id": self.id,
            "display_name": self.display_name,
            "binary_name": self.binary_name,
            "installed": binary_path is not None,
            "binary_path": binary_path,
            "version": version,
            "capabilities": self.get_capabilities(),
            "capability_matrix": self.get_capability_matrix(),
            "capability_details": self.get_capability_details(),
            "config_paths": self.get_config_paths(),
            "backup_policy": self.get_backup_policy(),
        }