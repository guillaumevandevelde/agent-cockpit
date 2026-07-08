"""OpenCode provider implementation."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from app.services.providers.base import (
    AgentProvider,
    SpawnCommandOptions,
    argv0_name,
    has_binary_descendant,
)

logger = logging.getLogger(__name__)

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


class OpenCodeProvider(AgentProvider):
    id = "open-code"
    display_name = "OpenCode"
    binary_name = "opencode"
    version_args = ("--version",)

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
            command += ["--model", options.model]
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
