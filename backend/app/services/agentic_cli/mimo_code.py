"""MiMoCode CLI implementation."""
from __future__ import annotations

import logging
import os
import re
import shutil
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

logger = logging.getLogger(__name__)

def get_mimo_home() -> Path:
    """Return the legacy MIMO_HOME path used by existing Cockpit settings."""
    return Path(os.environ.get("MIMO_HOME", Path.home() / ".mimocode")).expanduser()


def get_mimo_data_home() -> Path:
    """Return MiMoCode's runtime data directory from its current XDG contract."""
    mimocode_home = os.environ.get("MIMOCODE_HOME")
    if mimocode_home:
        return Path(mimocode_home).expanduser() / "data"
    xdg_data = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    ).expanduser()
    return xdg_data / "mimocode"


def _find_mimo_binary() -> str | None:
    """Find mimo binary, checking PATH and MIMO_HOME/bin."""
    binary_path = shutil.which("mimo")
    if binary_path:
        return binary_path
    mimo_home = get_mimo_home()
    local_bin = mimo_home / "bin" / "mimo"
    if local_bin.is_file():
        return str(local_bin)
    return None


class MiMoCodeCli(AgenticCli):
    id = "mimo-code"
    display_name = "MiMoCode"
    binary_name = "mimo"
    version_args = ("--version",)
    supports_resume_resolution = True

    def resolve_resume_target(
        self,
        worktree_path: Path,
        *,
        data_dir: Path | None = None,
    ) -> ResumeTarget | None:
        database = (data_dir or get_mimo_data_home()) / "mimocode.db"
        return _resolve_sqlite_resume_target(database, worktree_path)

    def get_backup_policy(self) -> dict:
        return {
            "provider": self.id,
            "export_supported": True,
            "automatic_restore_supported": False,
            "restore_mode": "manual_review",
            "included": [
                "config files with secret-like assignments redacted",
                "memory files",
                "skill definitions",
            ],
            "excluded": [
                "auth tokens",
                "session history",
                "cache files",
            ],
            "restore_refusal_reasons": [
                "MiMoCode auth and local state are intentionally excluded from exports.",
                "Automatic restore could overwrite active MiMoCode state without a stable provider-owned restore API.",
            ],
        }

    def get_config_paths(self, project_path: str | None = None) -> dict:
        home = get_mimo_home()
        paths = {
            "root": str(home),
            "config": str(home / "config.json"),
            "memory": str(home / "memory"),
            "skills": str(home / "skills"),
            "sessions": str(home / "sessions"),
        }
        if project_path:
            project = Path(project_path)
            paths.update({
                "project_config": str(project / ".mimo" / "config.json"),
                "project_memory": str(project / ".mimo" / "MEMORY.md"),
            })
        return paths

    def is_process_match(self, command: str, pid: str) -> bool:
        name = argv0_name(command)
        if name == "mimo":
            return True
        if name == "node":
            return has_binary_descendant(pid, {"mimo"})
        return False

    def build_spawn_command(self, options: SpawnCommandOptions) -> list[str]:
        binary = _find_mimo_binary() or "mimo"
        command = [binary]

        if options.mode == "plain":
            pass
        elif options.mode == "resume":
            if not options.session_id:
                raise ValueError("session_id is required for MiMoCode resume mode")
            command += ["--session", options.session_id]
        else:
            raise ValueError(f"Unsupported MiMoCode mode: {options.mode}")

        if options.prompt:
            command += ["--prompt", options.prompt]
        return command

    def get_allowed_cli_commands(self) -> list[str]:
        return ["config", "memory", "skills"]

    def get_version(self) -> str | None:
        binary_path = _find_mimo_binary()
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
        binary_path = _find_mimo_binary()
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
