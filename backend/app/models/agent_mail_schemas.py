"""Pydantic schemas for Agent Mail. No team_preset_id/team_slot_id fields —
identity is repo-scoped only, see docs/cockpit/agent-mail-spec.md."""
from datetime import datetime

from pydantic import BaseModel, Field


class MailSessionResponse(BaseModel):
    id: int
    cli: str
    source: str
    session_key: str
    cwd: str | None = None
    tmux_target: str | None = None
    mailbox_status: str
    activity: str | None = None
    last_seen_at: datetime | None = None


class MailMemberResponse(BaseModel):
    id: int
    identity_key: str
    repo_id: str
    repo_path: str
    repo_name: str
    display_name: str
    role: str | None = None
    charter: str | None = None
    status: str
    last_inbox_checked_at: datetime | None = None
    sessions: list[MailSessionResponse] = Field(default_factory=list)


class TeamListResponse(BaseModel):
    members: list[MailMemberResponse]


class MailMemberUpdate(BaseModel):
    display_name: str | None = None
    role: str | None = None
    charter: str | None = None


class MailAgentRegisterRequest(BaseModel):
    source: str
    cli: str = "unknown"
    cwd: str
    session_key: str
    pid: int | None = None


class MailAgentRegisterResponse(BaseModel):
    member: MailMemberResponse
    session: MailSessionResponse


class AgentMailInstallStatus(BaseModel):
    claude_code_hooks: list[str]
    claude_code_hooks_missing: list[str]
    codex_cli_available: bool
    codex_hooks: list[str] = Field(default_factory=list)
    codex_hooks_missing: list[str] = Field(default_factory=list)
    curl_available: bool
    codex_hook_shim_path: str
    python_path: str
    cockpit_url: str
    claude_settings_path: str | None = None
    codex_hooks_path: str | None = None
    mcp_server_hint: str = "Register agent-mail tools via the MCP Server page (Bearer token) — not managed by this installer."


class AgentMailSnippets(BaseModel):
    codex_hooks_snippet: str
    agents_md_snippet: str
