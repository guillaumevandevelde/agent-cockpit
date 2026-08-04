"""Codex CLI implementation."""
from __future__ import annotations

import json
import logging
import os
from itertools import islice
from pathlib import Path

from app.services.agentic_cli.base import (
    AgenticCli,
    ResumeTarget,
    SpawnCommandOptions,
    argv0_name,
    has_binary_descendant,
)
from app.services.agentic_cli.provider_env import PROVIDER_BEDROCK

logger = logging.getLogger(__name__)

CODEX_BEDROCK_MODEL_PROVIDER = 'model_provider="amazon-bedrock"'

def get_codex_home() -> Path:
    """Return CODEX_HOME, defaulting to ~/.codex."""
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def _read_session_meta(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in islice(handle, 20):
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("type") != "session_meta":
                    continue
                payload = item.get("payload")
                return payload if isinstance(payload, dict) else None
    except OSError:
        return None
    return None


class CodexCli(AgenticCli):
    id = "codex-cli"
    display_name = "Codex"
    binary_name = "codex"
    version_args = ("--version",)
    supports_resume_resolution = True

    def resolve_resume_target(
        self,
        worktree_path: Path,
        *,
        data_dir: Path | None = None,
    ) -> ResumeTarget | None:
        if not worktree_path.is_dir():
            return None
        sessions_dir = (data_dir or get_codex_home()) / "sessions"
        if not sessions_dir.is_dir():
            return None

        candidates: list[tuple[float, Path]] = []
        for path in sessions_dir.rglob("rollout-*.jsonl"):
            try:
                candidates.append((path.stat().st_mtime, path))
            except OSError:
                continue

        resolved_worktree = worktree_path.resolve()
        for _, path in sorted(candidates, key=lambda item: item[0], reverse=True):
            metadata = _read_session_meta(path)
            if metadata is None or metadata.get("parent_thread_id"):
                continue
            cwd = metadata.get("cwd")
            if not isinstance(cwd, str):
                continue
            candidate_cwd = Path(cwd).expanduser()
            if not candidate_cwd.is_absolute():
                continue
            try:
                matches = candidate_cwd.resolve() == resolved_worktree
            except OSError:
                continue
            if not matches:
                continue
            session_id = metadata.get("id") or metadata.get("session_id")
            if not session_id:
                continue
            session_id = str(session_id)
            if not path.name.endswith(f"-{session_id}.jsonl"):
                continue
            return session_id, str(resolved_worktree)
        return None

    def get_backup_policy(self) -> dict:
        return {
            "provider": self.id,
            "export_supported": True,
            "automatic_restore_supported": False,
            "restore_mode": "manual_review",
            "included": [
                "config.toml with secret-like assignments redacted",
                "*.config.toml profile files with secret-like assignments redacted",
                "rules/*.rules files with secret-like assignments redacted",
                "redacted provider inventory metadata",
            ],
            "excluded": [
                "auth.json",
                "history.jsonl",
                "models_cache.json",
                "*.sqlite and related SQLite sidecar files",
                "raw cache payloads and prompt text",
            ],
            "restore_refusal_reasons": [
                "Codex auth, history, cache, and local state are intentionally excluded from exports.",
                "Automatic restore could overwrite active Codex state without a stable provider-owned restore API.",
            ],
        }

    def get_config_paths(self, project_path: str | None = None) -> dict:
        home = get_codex_home()
        return {
            "root": str(home),
            "user_config": str(home / "config.toml"),
            "auth": str(home / "auth.json"),
            "history": str(home / "history.jsonl"),
            "models_cache": str(home / "models_cache.json"),
            "rules": str(home / "rules"),
        }

    def is_process_match(self, command: str, pid: str) -> bool:
        name = argv0_name(command)
        if name == "codex":
            return True
        if name in {"codex-exec-server", "codex-cli"}:
            return False
        if name == "node":
            return has_binary_descendant(
                pid,
                {"codex"},
                excluded_names={"codex-exec-server"},
            )
        return False

    def build_spawn_command(self, options: SpawnCommandOptions) -> list[str]:
        if options.mode not in {"plain", "resume", "fork"}:
            raise ValueError(f"Unsupported Codex mode: {options.mode}")

        command = ["codex", "--cd", options.directory]
        if options.provider == PROVIDER_BEDROCK:
            command += ["--config", CODEX_BEDROCK_MODEL_PROVIDER]

        effective_model = (
            options.bedrock_model
            if options.provider == PROVIDER_BEDROCK and options.bedrock_model
            else options.model
        )
        if effective_model:
            command += ["--model", effective_model]
        if options.reasoning_effort:
            command += ["--config", f'model_reasoning_effort="{options.reasoning_effort}"']
        if options.profile:
            command += ["--profile", options.profile]
        if options.profile_v2:
            command += ["--profile-v2", options.profile_v2]
        if options.sandbox:
            command += ["--sandbox", options.sandbox]
        if options.approval_policy:
            command += ["--ask-for-approval", options.approval_policy]
        if options.search:
            command.append("--search")
        if options.no_alt_screen:
            command.append("--no-alt-screen")
        if options.dangerously_bypass_approvals_and_sandbox:
            command.append("--dangerously-bypass-approvals-and-sandbox")

        if options.mode in {"resume", "fork"}:
            command.append(options.mode)
            if options.use_last:
                command.append("--last")
            elif options.session_id:
                command.append(options.session_id)
            else:
                raise ValueError(f"session_id or use_last is required for Codex {options.mode} mode")

        if options.prompt:
            command.append(options.prompt)
        return command

    def get_allowed_cli_commands(self) -> list[str]:
        return ["doctor", "mcp", "plugin", "features"]
