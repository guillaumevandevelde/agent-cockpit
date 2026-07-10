"""Pydantic schemas for Agent Mail. No team_preset_id/team_slot_id fields —
identity is repo-scoped only, see docs/cockpit/agent-mail-spec.md."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

MAIL_MESSAGE_KINDS = ["message", "broadcast", "context_request", "handoff", "answer"]
MAIL_REQUEST_KINDS = ["context_request", "handoff"]


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
    unread_count: int = 0
    pending_count: int = 0
    unseen_pending_count: int = 0
    stale_pending_count: int = 0
    can_nudge: bool = False
    wake_methods: list[str] = Field(default_factory=list)
    wake_state: str = "delivered_waiting"
    last_inbox_checked_at: datetime | None = None
    sessions: list[MailSessionResponse] = Field(default_factory=list)


class TeamListResponse(BaseModel):
    members: list[MailMemberResponse]


class MailMemberUpdate(BaseModel):
    display_name: str | None = None
    role: str | None = None
    charter: str | None = None


class MailMessageCreate(BaseModel):
    kind: str = "message"
    sender_member_id: int | None = None
    recipient_member_id: int | None = None
    thread_root_id: int | None = None
    subject: str | None = None
    body_markdown: str
    payload: dict[str, Any] | None = None


class MailMessageResponse(BaseModel):
    id: int
    thread_root_id: int | None = None
    kind: str
    sender_member_id: int | None = None
    sender_actor_id: int | None = None
    sender_type: str = "director"
    sender_actor_kind: str | None = None
    sender_name: str
    recipient_member_id: int | None = None
    subject: str | None = None
    body_markdown: str
    payload: dict[str, Any] | None = None
    request_status: str | None = None
    is_stale: bool = False
    read_at: datetime | None = None
    acked_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MailThreadResponse(BaseModel):
    root: MailMessageResponse
    replies: list[MailMessageResponse] = Field(default_factory=list)


class MailInboxResponse(BaseModel):
    member_id: int
    unread_count: int
    pending_count: int
    messages: list[MailMessageResponse] = Field(default_factory=list)


class MailExternalActorCreate(BaseModel):
    actor_key: str
    display_name: str
    kind: str = "external_tool"
    description: str | None = None


class MailExternalActorResponse(BaseModel):
    id: int
    actor_key: str
    display_name: str
    kind: str
    description: str | None = None
    created_at: datetime
    last_used_at: datetime | None = None


class MailExternalActorCreateResponse(BaseModel):
    actor: MailExternalActorResponse
    token: str


class ExternalAgentMailMessageRequest(BaseModel):
    recipient_member_id: int | None = None
    subject: str | None = None
    body_markdown: str
    payload: dict[str, Any] | None = None


class ExternalAgentMailContextRequest(BaseModel):
    recipient_member_id: int
    subject: str | None = None
    body_markdown: str
    why_needed: str | None = None
    files_or_symbols: list[str] = Field(default_factory=list)


class ExternalAgentMailHandoffRequest(BaseModel):
    recipient_member_id: int
    subject: str | None = None
    body_markdown: str
    files: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class ExternalAgentMailDeliveryRecipient(BaseModel):
    member_id: int
    member_name: str
    receipt_created: bool = True
    status: str
    wake_state: str
    wake_attempted: bool = False
    wake_succeeded: bool = False
    wake_method: str | None = None
    wake_error: str | None = None


class ExternalAgentMailSendResponse(BaseModel):
    actor: MailExternalActorResponse
    message: MailMessageResponse
    delivery_state: str
    recipients: list[ExternalAgentMailDeliveryRecipient] = Field(default_factory=list)


class ExternalAgentMailRequestStatus(BaseModel):
    message_id: int
    kind: str
    request_status: str | None = None
    is_stale: bool = False
    answered: bool = False
    acknowledged: bool = False
    root: MailMessageResponse
    replies: list[MailMessageResponse] = Field(default_factory=list)


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
