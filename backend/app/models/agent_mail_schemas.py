"""Pydantic schemas for Agent Mail. No team_preset_id/team_slot_id fields —
identity is repo-scoped only, see docs/cockpit/agent-mail-spec.md."""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

MAIL_MESSAGE_KINDS = ["message", "broadcast", "context_request", "handoff", "answer"]
MAIL_REQUEST_KINDS = ["context_request", "handoff"]


class MailSessionResponse(BaseModel):
    id: int
    provider: str
    source: str
    session_key: str
    cwd: Optional[str] = None
    tmux_target: Optional[str] = None
    mailbox_status: str
    activity: Optional[str] = None
    last_seen_at: Optional[datetime] = None


class MailMemberResponse(BaseModel):
    id: int
    identity_key: str
    repo_id: str
    repo_path: str
    repo_name: str
    display_name: str
    role: Optional[str] = None
    charter: Optional[str] = None
    status: str
    unread_count: int = 0
    pending_count: int = 0
    unseen_pending_count: int = 0
    stale_pending_count: int = 0
    can_nudge: bool = False
    wake_methods: list[str] = Field(default_factory=list)
    wake_state: str = "delivered_waiting"
    last_inbox_checked_at: Optional[datetime] = None
    sessions: list[MailSessionResponse] = Field(default_factory=list)


class TeamListResponse(BaseModel):
    members: list[MailMemberResponse]


class MailMemberUpdate(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    charter: Optional[str] = None


class MailMessageCreate(BaseModel):
    kind: str = "message"
    sender_member_id: Optional[int] = None
    recipient_member_id: Optional[int] = None
    thread_root_id: Optional[int] = None
    subject: Optional[str] = None
    body_markdown: str
    payload: Optional[dict[str, Any]] = None


class MailMessageResponse(BaseModel):
    id: int
    thread_root_id: Optional[int] = None
    kind: str
    sender_member_id: Optional[int] = None
    sender_actor_id: Optional[int] = None
    sender_type: str = "director"
    sender_actor_kind: Optional[str] = None
    sender_name: str
    recipient_member_id: Optional[int] = None
    subject: Optional[str] = None
    body_markdown: str
    payload: Optional[dict[str, Any]] = None
    request_status: Optional[str] = None
    is_stale: bool = False
    read_at: Optional[datetime] = None
    acked_at: Optional[datetime] = None
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
    description: Optional[str] = None


class MailExternalActorResponse(BaseModel):
    id: int
    actor_key: str
    display_name: str
    kind: str
    description: Optional[str] = None
    created_at: datetime
    last_used_at: Optional[datetime] = None


class MailExternalActorCreateResponse(BaseModel):
    actor: MailExternalActorResponse
    token: str


class ExternalAgentMailMessageRequest(BaseModel):
    recipient_member_id: Optional[int] = None
    subject: Optional[str] = None
    body_markdown: str
    payload: Optional[dict[str, Any]] = None


class ExternalAgentMailContextRequest(BaseModel):
    recipient_member_id: int
    subject: Optional[str] = None
    body_markdown: str
    why_needed: Optional[str] = None
    files_or_symbols: list[str] = Field(default_factory=list)


class ExternalAgentMailHandoffRequest(BaseModel):
    recipient_member_id: int
    subject: Optional[str] = None
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
    wake_method: Optional[str] = None
    wake_error: Optional[str] = None


class ExternalAgentMailSendResponse(BaseModel):
    actor: MailExternalActorResponse
    message: MailMessageResponse
    delivery_state: str
    recipients: list[ExternalAgentMailDeliveryRecipient] = Field(default_factory=list)


class ExternalAgentMailRequestStatus(BaseModel):
    message_id: int
    kind: str
    request_status: Optional[str] = None
    is_stale: bool = False
    answered: bool = False
    acknowledged: bool = False
    root: MailMessageResponse
    replies: list[MailMessageResponse] = Field(default_factory=list)


class MailAgentRegisterRequest(BaseModel):
    source: str
    provider: str = "unknown"
    cwd: str
    session_key: str
    pid: Optional[int] = None


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
    claude_settings_path: Optional[str] = None
    codex_hooks_path: Optional[str] = None
    mcp_server_hint: str = "Register agent-mail tools via the MCP Server page (Bearer token) — not managed by this installer."


class AgentMailSnippets(BaseModel):
    codex_hooks_snippet: str
    agents_md_snippet: str
