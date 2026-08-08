"""Claude Code CLI implementation."""
from __future__ import annotations

import logging
from pathlib import Path

from app.services.agentic_cli.base import (
    AgenticCli,
    ResumeTarget,
    SpawnCommandOptions,
    argv0_name,
    has_binary_descendant,
)
from app.services.runs.cc_spawn import _project_mcp_config_args, _resolve_project_directory
from app.utils.path_utils import (
    ClaudePathUtils,
    convert_path_to_folder_name,
    get_claude_projects_dir,
)

logger = logging.getLogger(__name__)


class ClaudeCodeCli(AgenticCli):
    id = "claude-code"
    display_name = "Claude Code"
    binary_name = "claude"
    supports_resume_resolution = True
    supports_transcript_resolution = True

    def resolve_transcript_file(
        self,
        worktree_path: Path,
        *,
        data_dir: Path | None = None,
    ) -> Path | None:
        if not worktree_path.is_dir():
            return None
        project_folder = convert_path_to_folder_name(str(worktree_path))
        folder = (data_dir or get_claude_projects_dir()) / project_folder
        if not folder.is_dir():
            return None
        transcripts = sorted(
            folder.glob("*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return transcripts[0] if transcripts else None

    def resolve_resume_target(
        self,
        worktree_path: Path,
        *,
        data_dir: Path | None = None,
    ) -> ResumeTarget | None:
        transcript = self.resolve_transcript_file(worktree_path, data_dir=data_dir)
        if transcript is None:
            return None
        return transcript.stem, transcript.parent.name

    def get_backup_policy(self) -> dict:
        return {
            "provider": self.id,
            "export_supported": True,
            "automatic_restore_supported": True,
            "restore_mode": "automatic",
        }

    def get_config_paths(self, project_path: str | None = None) -> dict:
        paths = ClaudePathUtils()
        result = {
            "root": str(Path.home() / ".claude"),
            "user_settings": str(paths.get_user_settings_json()),
            "user_settings_local": str(paths.get_user_settings_local_json()),
            "user_claude": str(paths.get_user_claude_json()),
        }
        if project_path:
            project = Path(project_path)
            result.update({
                "project_settings": str(project / ".claude" / "settings.json"),
                "project_settings_local": str(project / ".claude" / "settings.local.json"),
                "project_mcp": str(project / ".mcp.json"),
                "project_memory": str(project / "CLAUDE.md"),
            })
        return result

    def is_process_match(self, command: str, pid: str) -> bool:
        name = argv0_name(command)
        if name == "claude":
            return True
        if name == "node":
            return has_binary_descendant(pid, {"claude"})
        return False

    def build_spawn_command(self, options: SpawnCommandOptions) -> list[str]:
        command = ["claude"]

        if options.mode == "plain":
            pass
        elif options.mode == "worktree":
            if not options.worktree_name:
                raise ValueError("worktree_name is required for Claude Code worktree mode")
            command += ["--worktree", options.worktree_name]
        elif options.mode == "resume":
            if not options.session_id:
                raise ValueError("session_id is required for Claude Code resume mode")
            command += ["--resume", options.session_id]
        else:
            raise ValueError(f"Unsupported Claude Code mode: {options.mode}")

        # Pin MCP servers to the project-`.mcp.json` only — see
        # `_project_mcp_config_args` for the rationale. ``repo_path`` is the
        # repo-root fallback used when the launch cwd (a fresh worktree) has
        # no ``.mcp.json`` of its own — the external product-project case,
        # where ``POST /enable`` wrote an untracked ``.mcp.json`` into the
        # repo-root (kaart ``3672c073…``).
        command += _project_mcp_config_args(options.directory, options.repo_path)

        if options.skip_permissions:
            command.append("--dangerously-skip-permissions")
        if options.permission_prompt_tool:
            # Carries the MCP tool name Claude Code will invoke when an
            # `ask`-classified permission needs answering. Only set by the
            # dispatcher when skip_permissions=False (the product lane);
            # meta keeps the historical bypass and emits no flag here.
            command += ["--permission-prompt-tool", options.permission_prompt_tool]
        if options.model:
            command += ["--model", options.model]
        if options.prompt:
            command.append(options.prompt)
        return command

    def resolve_directory(self, options: SpawnCommandOptions) -> str:
        # For resume, the launch directory is fully determined by the session's
        # recorded cwd — never the directory the picker was browsing. This also
        # makes worktree sessions resume in their own worktree.
        if options.mode == "resume" and options.project_folder:
            return _resolve_project_directory(options.project_folder, options.session_id)
        return options.directory

    def get_allowed_cli_commands(self) -> list[str]:
        return ["mcp", "config", "plugin"]
