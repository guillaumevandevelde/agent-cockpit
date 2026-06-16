"""MiMoCode provider implementation."""
from __future__ import annotations

import os
from pathlib import Path

from app.services.providers.base import (
    AgentProvider,
    SpawnCommandOptions,
    argv0_name,
    has_binary_descendant,
)


def get_mimo_home() -> Path:
    """Return MIMO_HOME, defaulting to ~/.mimocode."""
    return Path(os.environ.get("MIMO_HOME", Path.home() / ".mimocode")).expanduser()


class MiMoCodeProvider(AgentProvider):
    id = "mimo-code"
    display_name = "MiMoCode"
    binary_name = "mimo"
    version_args = ("--version",)

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
        command = ["mimo"]

        if options.mode == "plain":
            pass
        elif options.mode == "resume":
            if not options.session_id:
                raise ValueError("session_id is required for MiMoCode resume mode")
            command += ["--resume", options.session_id]
        else:
            raise ValueError(f"Unsupported MiMoCode mode: {options.mode}")

        if options.prompt:
            command.append(options.prompt)
        return command

    def get_allowed_cli_commands(self) -> list[str]:
        return ["config", "memory", "skills"]
