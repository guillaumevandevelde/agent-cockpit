"""OpenCode CLI implementation."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

from app.services.agentic_cli.base import (
    AgenticCli,
    ResumeTarget,
    SpawnCommandOptions,
    _resolve_sqlite_resume_target,
    argv0_name,
    has_binary_descendant,
)
from app.services.agentic_cli.provider_env import (
    OPEN_CODE_ENDPOINT_PROVIDER_ID,
    PROVIDER_COMPATIBLE,
    PROVIDER_MINIMAX,
    PROVIDER_OPENCODE_GO,
    PROVIDER_OPENCODE_ZEN,
)

logger = logging.getLogger(__name__)


# Tool-part statuses that count as "resolved" for the resume gate. Anything
# outside this set (most importantly ``pending`` and ``running``) means the
# tool call's result has not arrived — replaying the conversation would hit
# the same in-flight call and hang again. Mirrors the opencode types:
# ``state.status in {"completed", "error"}`` is the resolved pair.
_OPENCODE_RESOLVED_TOOL_STATUSES = frozenset({"completed", "error"})


def _opencode_db_path(data_dir: Path | None = None) -> Path:
    """Absolute path to the live opencode.db (``data_dir/opencode.db``).

    Centralised so every helper below reads from the same file regardless of
    who calls it (``resolve_resume_target``, ``last_session_write``,
    ``can_resume_safely``). The default ``data_dir`` is the live one — see
    ``get_opencode_data_home``; tests pass a fake one.
    """
    return (data_dir or get_opencode_data_home()) / "opencode.db"


def last_session_write(
    worktree_path: str, *, data_dir: Path | None = None,
) -> int | None:
    """Wall-clock millisecond timestamp of the newest write across every
    unarchived session for ``worktree_path``, or ``None`` when no session
    matches (or the DB is unreadable).

    Used by the ACP idle-liveness detector
    (``backend/app/kanban/dispatch.py::check_acp_idle_liveness``) to
    answer "is this session still doing anything?" without consulting the
    CLI process — a session whose subprocess is alive but the agent is
    hung on a subagent call that died (kaart 2fa8d501…,
    ``prompt_async failed cause=Die(ProviderModelNotFoundError)``) keeps
    its process up but stops writing to ``time_updated``, so a silence
    detector that consults this column catches it where every existing
    liveness source misses it.

    Mirrors ``resolve_resume_target``'s read-only + WAL-aware contract:
    ``mode=ro`` URI, ``uri=True``, ``timeout=1`` so a hung SQLite writer
    can't stall the dispatch tick.
    """
    db_path = _opencode_db_path(data_dir)
    if not db_path.is_file():
        return None
    try:
        connection = sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=1,
        )
        try:
            row = connection.execute(
                # time_archived NULL means "live" in the opencode schema;
                # time_updated is in milliseconds (Unix epoch).
                "SELECT MAX(time_updated) FROM session "
                "WHERE directory = ? AND time_archived IS NULL",
                (str(Path(worktree_path).resolve()),),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.warning(
            "could not read last-write timestamp from %s", db_path,
            exc_info=True,
        )
        return None
    if row is None or row[0] is None:
        return None
    return int(row[0])


def can_resume_safely(
    session_id: str, *, data_dir: Path | None = None,
) -> bool:
    """True when resuming ``session_id`` won't replay a pending tool call.

    Reads the last ``part`` for ``session_id`` from opencode.db. A part of
    ``type=tool`` whose ``state.status`` is *not* in
    ``{"completed", "error"}`` (``pending`` or ``running``) means the
    agent's tool invocation never received a result — replaying the
    conversation would hit the same in-flight call and hang again (kaart
    2fa8d501…: the opencode CLI keeps the parent session alive but the
    subagent it spawned died with a defect, leaving the tool call
    permanently unresolved).

    Fails **open** (returns ``True``) on any read error: a transient
    SQLite hiccup must not block a resume the operator explicitly asked
    for. Same defensive contract as ``_live_sandcastle_sessions`` and the
    rest of the liveness detector family.

    Non-tool parts (``step-finish``, ``step-start``, text, snapshot, …)
    are always safe to resume — there's no in-flight call to reproduce.
    """
    db_path = _opencode_db_path(data_dir)
    if not db_path.is_file():
        return True
    try:
        connection = sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=1,
        )
        try:
            row = connection.execute(
                "SELECT data FROM part WHERE session_id = ? "
                "ORDER BY time_updated DESC, id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.warning(
            "could not read resume-safety state from %s for session %s",
            db_path, session_id, exc_info=True,
        )
        return True
    if row is None:
        return True
    try:
        part = json.loads(row[0])
    except (TypeError, ValueError):
        # Malformed JSON: fail open — refusing a resume on a parse error
        # would be a silent regression for the operator, and the worst
        # outcome of "resume into a malformed last part" is "the agent
        # sees a slightly odd conversation tail", not "the agent hangs".
        return True
    if not isinstance(part, dict):
        return True
    if part.get("type") != "tool":
        return True
    state = part.get("state")
    if not isinstance(state, dict):
        # Tool part without a state object: be conservative — if the
        # status is unreadable we don't know if it resolved. Refuse, so
        # we don't reproduce the bug class.
        return False
    status = state.get("status")
    return status in _OPENCODE_RESOLVED_TOOL_STATUSES

def get_opencode_home() -> Path:
    """Return OPENCODE_CONFIG_DIR, defaulting to ~/.config/opencode."""
    return Path(
        os.environ.get(
            "OPENCODE_CONFIG_DIR",
            Path.home() / ".config" / "opencode",
        )
    ).expanduser()


def get_opencode_data_home() -> Path:
    """Return OPENCODE data directory, defaulting to ~/.local/share/opencode."""
    return Path(
        os.environ.get(
            "XDG_DATA_HOME",
            Path.home() / ".local" / "share",
        )
    ).expanduser() / "opencode"


def _find_opencode_binary() -> str | None:
    """Find opencode binary, checking PATH."""
    return shutil.which("opencode")


class OpenCodeCli(AgenticCli):
    id = "open-code"
    display_name = "OpenCode"
    binary_name = "opencode"
    version_args = ("--version",)
    supports_resume_resolution = True

    def resolve_resume_target(
        self,
        worktree_path: Path,
        *,
        data_dir: Path | None = None,
    ) -> ResumeTarget | None:
        database = (data_dir or get_opencode_data_home()) / "opencode.db"
        return _resolve_sqlite_resume_target(database, worktree_path)

    def last_session_write(
        self, worktree_path: str, *, data_dir: Path | None = None,
    ) -> int | None:
        """Thin wrapper around the module-level helper — see that docstring
        for the full rationale. Lives on the class so the reaper can call it
        polymorphically alongside ``resolve_transcript_file`` /
        ``resolve_resume_target`` on the other CLIs."""
        return last_session_write(worktree_path, data_dir=data_dir)

    def can_resume_safely(
        self, session_id: str, *, data_dir: Path | None = None,
    ) -> bool:
        """Thin wrapper around the module-level helper — see that docstring
        for the full rationale. Lives on the class for the same reason as
        ``last_session_write``."""
        return can_resume_safely(session_id, data_dir=data_dir)

    def get_backup_policy(self) -> dict:
        return {
            "provider": self.id,
            "export_supported": True,
            "automatic_restore_supported": False,
            "restore_mode": "manual_review",
            "included": [
                "opencode.json config with secret-like assignments redacted",
                "AGENTS.md instructions",
                "agent definitions from .opencode/agents/",
                "skill definitions from .opencode/skills/",
                "command definitions from .opencode/commands/",
            ],
            "excluded": [
                "auth.json credentials",
                "session database",
                "cache files",
                "snapshot data",
            ],
            "restore_refusal_reasons": [
                "OpenCode auth and local state are intentionally excluded from exports.",
                "Automatic restore could overwrite active OpenCode state without a stable provider-owned restore API.",
            ],
        }

    def get_config_paths(self, project_path: str | None = None) -> dict:
        home = get_opencode_home()
        data = get_opencode_data_home()
        paths = {
            "root": str(home),
            "global_config": str(home / "opencode.json"),
            "auth": str(data / "auth.json"),
            "agents_dir": str(home / "agents"),
            "commands_dir": str(home / "commands"),
            "skills_dir": str(home / "skills"),
            "plugins_dir": str(home / "plugins"),
        }
        if project_path:
            project = Path(project_path)
            paths.update({
                "project_config": str(project / "opencode.json"),
                "project_agents_dir": str(project / ".opencode" / "agents"),
                "project_commands_dir": str(project / ".opencode" / "commands"),
                "project_skills_dir": str(project / ".opencode" / "skills"),
                "project_plugins_dir": str(project / ".opencode" / "plugins"),
                "project_instructions": str(project / "AGENTS.md"),
            })
        return paths

    def is_process_match(self, command: str, pid: str) -> bool:
        name = argv0_name(command)
        if name == "opencode":
            return True
        if name == "node":
            return has_binary_descendant(pid, {"opencode"})
        return False

    def build_spawn_command(self, options: SpawnCommandOptions) -> list[str]:
        if options.reasoning_effort:
            raise ValueError(
                "OpenCode does not support pinning a reasoning/thinking effort "
                "(no --variant or --effort flag exists in the CLI)"
            )

        binary = _find_opencode_binary() or "opencode"
        command = [binary]

        if options.mode == "plain":
            pass
        elif options.mode == "resume":
            if not options.session_id:
                raise ValueError("session_id is required for OpenCode resume mode")
            command += ["--session", options.session_id]
        elif options.mode == "fork":
            if not options.session_id:
                raise ValueError("session_id is required for OpenCode fork mode")
            command += ["--session", options.session_id, "--fork"]
        else:
            raise ValueError(f"Unsupported OpenCode mode: {options.mode}")

        if options.model:
            # ``--model`` always expects ``provider/model`` form in
            # OpenCode — a bare model id raises
            # ``ProviderModelNotFoundError``. The provider segment is
            # the OpenCode CLI catalog id: either the fixed injected
            # id for ``anthropic-compatible``/``minimax`` endpoints
            # (see ``provider_env._build_opencode_endpoint_env``) or
            # the built-in catalog id for the OpenCode-hosted
            # subscriptions ``opencode-go`` / ``opencode`` (Zen).
            # When the caller passes ``provider/model`` directly
            # (e.g. ``zai-coding-plan/glm-5.2`` for OpenCode's other
            # built-in providers), ``prefix`` stays None and the raw
            # value flows through unchanged.
            if options.provider in (PROVIDER_MINIMAX, PROVIDER_COMPATIBLE):
                prefix = OPEN_CODE_ENDPOINT_PROVIDER_ID
            elif options.provider == PROVIDER_OPENCODE_GO:
                prefix = PROVIDER_OPENCODE_GO
            elif options.provider == PROVIDER_OPENCODE_ZEN:
                prefix = PROVIDER_OPENCODE_ZEN
            else:
                prefix = None
            if prefix is None:
                command += ["--model", options.model]
            else:
                command += ["--model", f"{prefix}/{options.model}"]
        if options.prompt:
            command += ["--prompt", options.prompt]
        return command

    def get_allowed_cli_commands(self) -> list[str]:
        return ["session", "mcp", "agent", "models", "auth", "stats"]

    def get_version(self) -> str | None:
        binary_path = _find_opencode_binary()
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

    def get_status(self) -> dict:
        binary_path = _find_opencode_binary()
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
