"""Central CLI capability matrix."""
from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)

CAPABILITY_KEYS = (
    "config",
    "sessions",
    "spawn",
    "resume",
    "fork",
    "mcp",
    "plugins",
    "permissions",
    "commands",
    "agents",
    "skills",
    "hooks",
    "memory",
    "output_styles",
    "statusline",
    "usage",
    "context",
    "doctor",
    "backup",
    "restore",
    "headless_run",
)
SUPPORTED_STATES = {"supported", "read_only", "write_capable"}


def capability(state: str, label: str, reason: str | None = None) -> dict[str, str]:
    result = {"state": state, "label": label}
    if reason:
        result["reason"] = reason
    return result


AGENTIC_CLI_CAPABILITY_MATRIX: dict[str, dict[str, dict[str, str]]] = {
    "claude-code": {
        "config": capability("write_capable", "Configuration", "Claude Code JSON settings can be viewed and edited."),
        "sessions": capability("read_only", "Session History", "Claude Code transcript history is available."),
        "spawn": capability("write_capable", "Spawn Sessions", "Agent Bridge can launch Claude Code sessions."),
        "resume": capability("write_capable", "Resume Sessions", "Claude Code resume is available."),
        "fork": capability("unsupported", "Fork Sessions", "Claude Code fork mode is not exposed."),
        "mcp": capability("write_capable", "MCP Servers", "Claude Code MCP servers can be managed."),
        "plugins": capability("write_capable", "Plugins", "Claude Code plugins can be managed."),
        "permissions": capability("write_capable", "Permissions", "Claude Code trust and permissions can be managed."),
        "commands": capability("write_capable", "Commands", "Claude Code slash commands can be managed."),
        "agents": capability("write_capable", "Agents", "Claude Code agents can be managed."),
        "skills": capability("write_capable", "Skills", "Claude Code skills can be managed."),
        "hooks": capability("write_capable", "Hooks", "Claude Code hooks can be managed."),
        "memory": capability("write_capable", "Memory", "Claude Code memory files can be viewed and edited."),
        "output_styles": capability("write_capable", "Output Styles", "Claude Code output styles can be managed."),
        "statusline": capability("write_capable", "Status Line", "Claude Code status line settings can be managed."),
        "usage": capability("read_only", "Usage", "Claude Code usage data is available read-only."),
        "context": capability("read_only", "Context", "Claude Code context diagnostics are available read-only."),
        "doctor": capability("unsupported", "Doctor", "Claude Code does not expose provider doctor diagnostics."),
        "backup": capability("write_capable", "Backup", "Claude Code backup and restore workflows are available."),
        "restore": capability("write_capable", "Restore", "Claude Code backup restore workflows are available."),
        "headless_run": capability("supported", "Headless Structured Events", "Claude Code exposes a headless structured-event stream via `claude -p --output-format stream-json`, mapped onto the ACP-isomorphic event model."),
    },
    "codex-cli": {
        "config": capability("write_capable", "Configuration", "Safe Codex TOML settings can be viewed and edited."),
        "sessions": capability("write_capable", "Agent Bridge Sessions", "Agent Bridge can discover and launch Codex sessions."),
        "spawn": capability("write_capable", "Spawn Sessions", "Agent Bridge can launch Codex sessions."),
        "resume": capability("write_capable", "Resume Sessions", "Codex resume is available."),
        "fork": capability("write_capable", "Fork Sessions", "Codex fork is available."),
        "mcp": capability("write_capable", "MCP Servers", "Codex MCP servers can be managed through the Codex CLI."),
        "plugins": capability("write_capable", "Plugins", "Codex plugin inventory and CLI-backed install/remove are available."),
        "permissions": capability("unsupported", "Permissions", "Codex trust and permissions use different config semantics."),
        "commands": capability("unsupported", "Commands", "Codex does not expose Claude Code slash commands."),
        "agents": capability("unsupported", "Agents", "Codex does not expose Claude Code agents."),
        "skills": capability("unsupported", "Skills", "Codex skills are not surfaced in this build."),
        "hooks": capability("unsupported", "Hooks", "Codex does not expose Claude Code hooks."),
        "memory": capability("unsupported", "Memory", "Codex memory files are not surfaced in this build."),
        "output_styles": capability("unsupported", "Output Styles", "Codex does not expose Claude Code output styles."),
        "statusline": capability("unsupported", "Status Line", "Codex does not expose Claude Code status line settings."),
        "usage": capability("unsupported", "Usage", "Codex usage data is not available with stable local semantics."),
        "context": capability("unsupported", "Context", "Codex context diagnostics are not available with stable local semantics."),
        "doctor": capability("read_only", "Doctor", "Codex doctor diagnostics are available read-only."),
        "backup": capability("read_only", "Backup", "Codex export-only backups are available."),
        "restore": capability("unsupported", "Restore", "Automatic Codex restore is refused without a stable provider-owned restore API."),
        "headless_run": capability(
            "supported",
            "Headless Structured Events",
            "Claimed `codex exec --json` exposes a headless JSONL event stream mappable onto the ACP-isomorphic event model, but the Codex CLI was not on PATH on this host during the 2026-07-28 audit (see kaart 470d0a90…) — this row remains **unverified**. To upgrade, reproduce `codex --version` + `codex exec --json \"Reply with exactly: OK\"` on a non-TTY pipe against an installed Codex build.",
        ),
    },
    "mimo-code": {
        "config": capability("write_capable", "Configuration", "MiMoCode configuration files can be viewed and edited."),
        "sessions": capability("read_only", "Session History", "MiMoCode session history is available."),
        "spawn": capability("write_capable", "Spawn Sessions", "Agent Bridge can launch MiMoCode sessions."),
        "resume": capability("write_capable", "Resume Sessions", "MiMoCode resume is available."),
        "fork": capability("unsupported", "Fork Sessions", "MiMoCode fork mode is not exposed."),
        "mcp": capability("unsupported", "MCP Servers", "MiMoCode does not expose MCP server management."),
        "plugins": capability("unsupported", "Plugins", "MiMoCode does not expose plugin management."),
        "permissions": capability("unsupported", "Permissions", "MiMoCode uses different permission semantics."),
        "commands": capability("unsupported", "Commands", "MiMoCode does not expose slash commands."),
        "agents": capability("unsupported", "Agents", "MiMoCode does not expose agent management."),
        "skills": capability("write_capable", "Skills", "MiMoCode skills can be managed."),
        "hooks": capability("unsupported", "Hooks", "MiMoCode does not expose hook management."),
        "memory": capability("write_capable", "Memory", "MiMoCode memory files can be viewed and edited."),
        "output_styles": capability("unsupported", "Output Styles", "MiMoCode does not expose output style management."),
        "statusline": capability("unsupported", "Status Line", "MiMoCode does not expose status line settings."),
        "usage": capability("unsupported", "Usage", "MiMoCode usage data is not available."),
        "context": capability("unsupported", "Context", "MiMoCode context diagnostics are not available."),
        "doctor": capability("unsupported", "Doctor", "MiMoCode does not expose provider doctor diagnostics."),
        "backup": capability("read_only", "Backup", "MiMoCode export-only backups are available."),
        "restore": capability("unsupported", "Restore", "Automatic MiMoCode restore is refused without a stable provider-owned restore API."),
        "headless_run": capability("unknown", "Headless Structured Events", "MiMoCode headless structured-event support has not been verified against the ACP-isomorphic event model."),
    },
    "open-code": {
        "config": capability("write_capable", "Configuration", "OpenCode JSON configuration can be viewed and edited."),
        "sessions": capability("read_only", "Session History", "OpenCode session history is available via CLI."),
        "spawn": capability("write_capable", "Spawn Sessions", "Agent Bridge can launch OpenCode sessions."),
        "resume": capability("write_capable", "Resume Sessions", "OpenCode resume is available via --session flag."),
        "fork": capability("write_capable", "Fork Sessions", "OpenCode fork is available via --fork flag."),
        "mcp": capability("write_capable", "MCP Servers", "OpenCode MCP servers can be managed via CLI."),
        "plugins": capability("write_capable", "Plugins", "OpenCode plugins can be managed via CLI."),
        "permissions": capability("write_capable", "Permissions", "OpenCode permissions can be configured via config."),
        "commands": capability("write_capable", "Commands", "OpenCode custom commands can be managed via config and markdown files."),
        "agents": capability("write_capable", "Agents", "OpenCode agents can be managed via CLI and markdown files."),
        "skills": capability("write_capable", "Skills", "OpenCode agent skills can be managed."),
        "hooks": capability("unsupported", "Hooks", "OpenCode does not expose hook management."),
        "memory": capability("write_capable", "Memory", "OpenCode memory via AGENTS.md and instructions."),
        "output_styles": capability("unsupported", "Output Styles", "OpenCode does not expose output style management."),
        "statusline": capability("unsupported", "Status Line", "OpenCode does not expose status line settings."),
        "usage": capability("read_only", "Usage", "OpenCode usage stats are available via CLI."),
        "context": capability("unsupported", "Context", "OpenCode context diagnostics are not available."),
        "doctor": capability("unsupported", "Doctor", "OpenCode does not expose provider doctor diagnostics."),
        "backup": capability("read_only", "Backup", "OpenCode export-only backups are available."),
        "restore": capability("unsupported", "Restore", "Automatic OpenCode restore is refused without a stable provider-owned restore API."),
        "headless_run": capability(
            "supported",
            "Headless Structured Events",
            "OpenCode exposes a headless structured-event stream via its first-party ACP server (`opencode acp`, a top-level command since ≥1.18.x); a full `initialize` → `session/new` → `session/prompt` cycle was measured end-to-end against a non-TTY stdio pipe (see docs/cockpit/acp-transport-opencode-go-nogo.md §2 / §3.2). The alternative `opencode run --format json` route was measured to produce zero bytes on a non-TTY pipe within a 30 s timeout with empty stderr (same doc, §2.5 / §3.4) and is **not** the mechanism the spawn transport uses. (`opencode serve` ships as a separate HTTP/SSE server and is not the pipe-based headless mechanism.)",
        ),
    },
    "copilot-cli": {
        "config": capability("unsupported", "Configuration", "Copilot CLI configuration is detected but not editable in this build."),
        "sessions": capability("write_capable", "Agent Bridge Sessions", "Agent Bridge can discover and launch Copilot CLI sessions."),
        "spawn": capability("write_capable", "Spawn Sessions", "Agent Bridge can launch Copilot CLI sessions."),
        "resume": capability("write_capable", "Resume Sessions", "Copilot CLI resume and continue flags are available."),
        "fork": capability("unsupported", "Fork Sessions", "Copilot CLI does not expose a fork workflow."),
        "mcp": capability("unsupported", "MCP Servers", "Copilot CLI MCP servers are not managed in this build."),
        "plugins": capability("unsupported", "Plugins", "Copilot CLI does not expose plugin management."),
        "permissions": capability("unsupported", "Permissions", "Copilot CLI permissions are launch flags, not a managed page."),
        "commands": capability("unsupported", "Commands", "Copilot CLI does not expose slash commands."),
        "agents": capability("unsupported", "Agents", "Copilot CLI custom agents are not managed in this build."),
        "skills": capability("unsupported", "Skills", "Copilot CLI skills are not surfaced in this build."),
        "hooks": capability("unsupported", "Hooks", "Copilot CLI hooks are not surfaced in this build."),
        "memory": capability("unsupported", "Memory", "Copilot CLI memory configuration is not surfaced in this build."),
        "output_styles": capability("unsupported", "Output Styles", "Copilot CLI output styles are not surfaced in this build."),
        "statusline": capability("unsupported", "Status Line", "Copilot CLI status line settings are not surfaced in this build."),
        "usage": capability("unsupported", "Usage", "Copilot CLI usage data is not available with stable local semantics."),
        "context": capability("unsupported", "Context", "Copilot CLI context diagnostics are not surfaced in this build."),
        "doctor": capability("unsupported", "Doctor", "Copilot CLI does not expose provider doctor diagnostics."),
        "backup": capability("unsupported", "Backup", "Copilot CLI backup/export is not supported in this build."),
        "restore": capability("unsupported", "Restore", "Copilot CLI restore is not supported in this build."),
        "headless_run": capability("unsupported", "Headless Structured Events", "Copilot CLI does not expose a documented headless structured-event mode."),
    },
}


def normalize_capability_matrix(cli_id: str) -> dict[str, dict[str, Any]]:
    matrix = deepcopy(AGENTIC_CLI_CAPABILITY_MATRIX.get(cli_id, {}))
    for key in CAPABILITY_KEYS:
        matrix.setdefault(
            key,
            capability("unknown", key.replace("_", " ").title(), "Capability has not been classified."),
        )
    return matrix


def capability_flags(cli_id: str) -> dict[str, bool]:
    matrix = normalize_capability_matrix(cli_id)
    return {
        key: detail.get("state") in SUPPORTED_STATES
        for key, detail in matrix.items()
    }
