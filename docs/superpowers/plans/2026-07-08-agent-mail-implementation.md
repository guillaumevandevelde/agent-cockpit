# Agent Mail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (this session executes inline, not via subagent-driven-development — see `docs/cockpit/agent-mail-spec.md` for why). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port upstream claude-deck's Agent Mail (cross-session messaging between arbitrary Claude Code/Codex CLI processes) into Claude Cockpit, adapted to reuse this fork's existing MCP server, tmux-nudge, and pane-discovery infrastructure instead of upstream's separate transport layer.

**Architecture:** New SQLAlchemy models on the main `Base` (`mail_team_members`, `mail_agent_sessions`, `mail_external_actors`, `mail_messages`, `mail_receipts`) with a service-layer singleton (`agent_mail_service`) that upstream already designed well — ported near-verbatim but with all Agent-Team-preset/slot fields stripped (out of scope, see spec). REST API + MCP tools + Claude Code/Codex hooks + an authenticated external orchestration API sit on top. Frontend is an 11-file feature under `frontend/src/features/agent-mail/`.

**Tech Stack:** FastAPI, async SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Pydantic v2, `mcp.server.fastmcp.FastMCP`, pytest + pytest-asyncio, React 19 + TypeScript + shadcn/ui.

**Reference source:** Upstream files at commit `c246726` are checked out read-only at `/tmp/claude-1000/-home-vdvgu-claude-cockpit--claude-worktrees-k-upstream-sync-9af0/d8bfe5ae-13ba-4089-acce-48e90fcff7c0/scratchpad/upstream_src/` (backend files at top level, frontend files under `frontend/`). Every task below either gives complete adapted code inline, or — for large mechanical frontend ports — names the exact scratchpad source file plus the exact patch to apply.

## Global Constraints

- No `team_preset_id`/`team_slot_id`/`participant_kind` fields anywhere — same-repo multi-participant is out of scope (see `docs/cockpit/agent-mail-spec.md`). One `MailTeamMember` per repo.
- No `6e1546f` (Codex plan snapshots) — unrelated feature, not touched.
- No separate Agent-Mail-specific MCP transport: tools register on the existing shared `app/mcp_server` (`mcp` singleton from `app.mcp_server.server`), reachable at the existing Bearer-token-authed `/api/v1/mcp-server`. Never create a `codex mcp add` install path or a stdio shim server for Claude Code.
- Reuse `app/services/scheduling/tmux_inject.py::send_text()` for the tmux nudge (not a new `subprocess.run` implementation).
- Reuse `app/services/agent_bridge/discovery.py::discover_agent_sessions()` for tmux pane scanning (not a new scanner).
- Nudge eligibility is `provider in {"claude-code", "codex-cli"}` from the start (upstream's corrected, post-`5d83b1d` behavior) — never gate on Codex only.
- New tables via `Base.metadata.create_all` only — no Alembic, no manual `_ensure_*` migration guards (only new tables, no ALTERs on existing ones).
- Backend tests run from **this worktree's** `backend/` directory only (`cd backend && source venv/bin/activate && pytest`) — never the main checkout.
- Frontend: after any change, `npm run build` must be clean before considering a frontend task done (dist is what's served).
- Follow existing per-feature schema-file convention (`scheduled_message_schemas.py`, not adding to the shared `models/schemas.py`): new file `backend/app/models/agent_mail_schemas.py`.

---

### Task 1: Repo identity helper

**Files:**
- Create: `backend/app/utils/repo_utils.py`
- Test: `backend/tests/test_repo_utils.py`

**Interfaces:**
- Produces: `derive_repo_identity(cwd: str) -> dict` with keys `repo_id: str`, `repo_root: str`, `repo_name: str`. Used by Task 4 onward.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_repo_utils.py
import subprocess

from app.utils.repo_utils import derive_repo_identity


def test_plain_directory_falls_back_to_realpath(tmp_path):
    ident = derive_repo_identity(str(tmp_path))
    assert ident["repo_root"] == str(tmp_path.resolve())
    assert ident["repo_name"] == tmp_path.name
    assert len(ident["repo_id"]) == 16


def test_git_worktrees_share_repo_id(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "wt-branch", str(worktree)],
        cwd=repo, check=True, capture_output=True,
    )

    assert derive_repo_identity(str(repo))["repo_id"] == derive_repo_identity(str(worktree))["repo_id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_repo_utils.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.utils.repo_utils'`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/utils/repo_utils.py
"""Derive a stable repository identity from a working directory."""
import hashlib
import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def derive_repo_identity(cwd: str) -> dict:
    """Return a stable repo identity for a working directory.

    Git worktrees share a common git directory, so hashing that path maps
    worktrees of the same repository to the same repo_id. Plain directories
    fall back to their normalized absolute path.
    """
    norm = os.path.realpath(os.path.expanduser(cwd or "."))
    anchor = norm
    repo_root = norm
    try:
        result = subprocess.run(
            ["git", "-C", norm, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            anchor = os.path.realpath(result.stdout.strip())
            repo_root = os.path.dirname(anchor) if anchor.endswith(f"{os.sep}.git") else anchor
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("git common-dir lookup failed for %s: %s", norm, exc)

    return {
        "repo_id": hashlib.sha1(anchor.encode("utf-8")).hexdigest()[:16],
        "repo_root": repo_root,
        "repo_name": os.path.basename(repo_root) or repo_root,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_repo_utils.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/utils/repo_utils.py backend/tests/test_repo_utils.py
git commit -m "feat(agent-mail): add repo identity helper"
```

---

### Task 2: Agent Mail ORM models

**Files:**
- Create: `backend/app/models/agent_mail.py`
- Modify: `backend/app/main.py` (register model import for `create_all`)
- Test: `backend/tests/test_agent_mail_model.py`

**Interfaces:**
- Consumes: `app.database.Base` (`DeclarativeBase` from `app/database.py`).
- Produces: ORM classes `MailTeamMember`, `MailAgentSession`, `MailExternalActor`, `MailMessage`, `MailReceipt`. Field names below are exact and used by every later backend task — do not rename.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_agent_mail_model.py
import pytest

from app.database import AsyncSessionLocal, Base, engine
from app.models.agent_mail import (
    MailAgentSession,
    MailExternalActor,
    MailMessage,
    MailReceipt,
    MailTeamMember,
)


@pytest.mark.asyncio
async def test_create_member_session_message_receipt():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as s:
        member = MailTeamMember(
            identity_key="repo:abc123",
            repo_id="abc123",
            repo_path="/home/x/repo",
            repo_name="repo",
            display_name="repo",
        )
        s.add(member)
        await s.commit()
        await s.refresh(member)
        assert member.id is not None
        assert member.created_at is not None

        session = MailAgentSession(
            member_id=member.id,
            source="hook",
            session_key="cc:sess-1",
            provider="claude-code",
        )
        s.add(session)
        await s.commit()
        await s.refresh(session)
        assert session.mailbox_status == "connected"

        actor = MailExternalActor(
            actor_key="openclaw",
            display_name="OpenClaw",
            token_hash="hash",
        )
        s.add(actor)
        await s.commit()
        await s.refresh(actor)
        assert actor.kind == "external_tool"

        message = MailMessage(
            kind="message",
            sender_member_id=member.id,
            recipient_member_id=member.id,
            body_markdown="hi",
        )
        s.add(message)
        await s.commit()
        await s.refresh(message)
        assert message.request_status is None

        receipt = MailReceipt(message_id=message.id, member_id=member.id)
        s.add(receipt)
        await s.commit()
        await s.refresh(receipt)
        assert receipt.read_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_mail_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.agent_mail'`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/models/agent_mail.py
"""ORM models for Agent Mail — cross-session messaging between arbitrary
Claude Code / Codex CLI processes. Identity is one durable MailTeamMember
per repo (no team-preset/slot integration — see docs/cockpit/agent-mail-spec.md
for why that upstream extension is out of scope)."""
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MailTeamMember(Base):
    """Durable per-repo identity. Git worktrees of the same repo share one row."""

    __tablename__ = "mail_team_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    identity_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    repo_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    repo_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    charter: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_inbox_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class MailAgentSession(Base):
    """Ephemeral session (hook heartbeat, MCP connection, or tmux-observed pane)
    attached to a durable member."""

    __tablename__ = "mail_agent_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("mail_team_members.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # hook | mcp | observed
    session_key: Mapped[str] = mapped_column(String(256), unique=True, index=True, nullable=False)
    cwd: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    tmux_target: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pane_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mailbox_status: Mapped[str] = mapped_column(String(16), default="connected", nullable=False)
    activity: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class MailExternalActor(Base):
    """Durable identity for a local, bearer-token-authenticated external tool
    (e.g. OpenClaw) that talks to the external orchestration API."""

    __tablename__ = "mail_external_actors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_key: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(80), default="external_tool", nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MailMessage(Base):
    __tablename__ = "mail_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_root_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("mail_messages.id", ondelete="CASCADE"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    sender_member_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("mail_team_members.id", ondelete="SET NULL"), nullable=True
    )
    sender_actor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("mail_external_actors.id", ondelete="SET NULL"), nullable=True
    )
    recipient_member_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("mail_team_members.id", ondelete="CASCADE"), index=True, nullable=True
    )
    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    request_status: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)


class MailReceipt(Base):
    __tablename__ = "mail_receipts"
    __table_args__ = (UniqueConstraint("message_id", "member_id", name="uix_mail_receipt_message_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("mail_messages.id", ondelete="CASCADE"), index=True, nullable=False
    )
    member_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("mail_team_members.id", ondelete="CASCADE"), index=True, nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
```

Register the module in `backend/app/main.py` next to the other `# noqa: F401` model imports (find the block starting `import app.models.host`):

```python
import app.models.agent_mail  # noqa: F401  (register tables for create_all)
import app.models.host  # noqa: F401  (register tables for create_all)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_mail_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/agent_mail.py backend/app/main.py backend/tests/test_agent_mail_model.py
git commit -m "feat(agent-mail): add ORM models (member/session/actor/message/receipt)"
```

---

### Task 3: Pydantic schemas

**Files:**
- Create: `backend/app/models/agent_mail_schemas.py`
- Test: `backend/tests/test_agent_mail_schemas.py`

**Interfaces:**
- Consumes: nothing (pure Pydantic).
- Produces: `MAIL_MESSAGE_KINDS`, `MAIL_REQUEST_KINDS` constants; `MailSessionResponse`, `MailMemberResponse`, `TeamListResponse`, `MailMemberUpdate`, `MailMessageCreate`, `MailMessageResponse`, `MailThreadResponse`, `MailInboxResponse`, `MailExternalActorCreate`, `MailExternalActorResponse`, `MailExternalActorCreateResponse`, `ExternalAgentMailMessageRequest`, `ExternalAgentMailContextRequest`, `ExternalAgentMailHandoffRequest`, `ExternalAgentMailDeliveryRecipient`, `ExternalAgentMailSendResponse`, `ExternalAgentMailRequestStatus`, `MailAgentRegisterRequest`, `MailAgentRegisterResponse`, `AgentMailInstallStatus`, `AgentMailSnippets`. Used by every later backend task.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_agent_mail_schemas.py
from app.models.agent_mail_schemas import (
    MAIL_MESSAGE_KINDS,
    MAIL_REQUEST_KINDS,
    MailAgentRegisterRequest,
    MailMessageCreate,
)


def test_message_kinds():
    assert MAIL_MESSAGE_KINDS == ["message", "broadcast", "context_request", "handoff", "answer"]
    assert MAIL_REQUEST_KINDS == ["context_request", "handoff"]


def test_message_create_defaults_to_message_kind():
    req = MailMessageCreate(body_markdown="hi")
    assert req.kind == "message"
    assert req.recipient_member_id is None


def test_register_request_requires_cwd_and_session_key():
    req = MailAgentRegisterRequest(source="hook", cwd="/repo", session_key="cc:1")
    assert req.provider == "unknown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_mail_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/models/agent_mail_schemas.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_mail_schemas.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/agent_mail_schemas.py backend/tests/test_agent_mail_schemas.py
git commit -m "feat(agent-mail): add Pydantic schemas"
```

---

### Task 4: Service layer — identity, registration, heartbeat

**Files:**
- Create: `backend/app/services/agent_mail_service.py`
- Test: `backend/tests/agent_mail/test_registration.py`
- Test: `backend/tests/agent_mail/__init__.py` (empty, new test package)

**Interfaces:**
- Consumes: `MailTeamMember`, `MailAgentSession` (Task 2), `MailAgentRegisterRequest` (Task 3), `derive_repo_identity` (Task 1).
- Produces: module-level singleton `agent_mail_service = AgentMailService()` with methods `get_or_create_repo_member(db, cwd) -> MailTeamMember`, `register_session(db, request: MailAgentRegisterRequest) -> tuple[MailTeamMember, MailAgentSession]`, `heartbeat_session(db, session_key, activity=None) -> MailAgentSession | None`, `mark_session_offline(db, session_key) -> None`, `heartbeat_member_mcp_session(db, member_id) -> None`, `_effective_status(session, now) -> str`, `_pid_is_running(pid) -> bool`. Tasks 5-8 add more methods to this same class/file — do not create a second service class.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/agent_mail/__init__.py
```

```python
# backend/tests/agent_mail/test_registration.py
import pytest

from app.database import AsyncSessionLocal, Base, engine
from app.models.agent_mail_schemas import MailAgentRegisterRequest
from app.services.agent_mail_service import agent_mail_service


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_register_session_creates_member_and_session(tmp_path):
    async with AsyncSessionLocal() as s:
        req = MailAgentRegisterRequest(
            source="hook", provider="claude-code",
            cwd=str(tmp_path), session_key="cc:sess-1",
        )
        member, session = await agent_mail_service.register_session(s, req)
        assert member.repo_name == tmp_path.name
        assert session.session_key == "cc:sess-1"
        assert session.mailbox_status == "connected"


@pytest.mark.asyncio
async def test_register_session_same_cwd_reuses_member(tmp_path):
    async with AsyncSessionLocal() as s:
        req1 = MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:1")
        req2 = MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:2")
        member1, _ = await agent_mail_service.register_session(s, req1)
        member2, _ = await agent_mail_service.register_session(s, req2)
        assert member1.id == member2.id


@pytest.mark.asyncio
async def test_heartbeat_updates_last_seen_and_activity(tmp_path):
    async with AsyncSessionLocal() as s:
        req = MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:hb")
        _, session = await agent_mail_service.register_session(s, req)
        first_seen = session.last_seen_at

        updated = await agent_mail_service.heartbeat_session(s, "cc:hb", activity="edited foo.py")
        assert updated is not None
        assert updated.activity == "edited foo.py"
        assert updated.last_seen_at >= first_seen


@pytest.mark.asyncio
async def test_mark_session_offline(tmp_path):
    async with AsyncSessionLocal() as s:
        req = MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:off")
        await agent_mail_service.register_session(s, req)
        await agent_mail_service.mark_session_offline(s, "cc:off")

        from sqlalchemy import select
        from app.models.agent_mail import MailAgentSession
        row = (await s.execute(
            select(MailAgentSession).where(MailAgentSession.session_key == "cc:off")
        )).scalar_one()
        assert row.mailbox_status == "offline"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_registration.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.agent_mail_service'`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/services/agent_mail_service.py
"""Registry, messaging, and delivery-context behavior for Agent Mail.

Adapted from upstream claude-deck's agent_mail_service.py: identity is
repo-scoped only (no team-preset/slot integration — see
docs/cockpit/agent-mail-spec.md), tmux delivery reuses
app.services.scheduling.tmux_inject instead of a private subprocess call,
and pane discovery reuses app.services.agent_bridge.discovery instead of a
private scanner.
"""
import logging
import os
import subprocess
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_mail import MailAgentSession, MailExternalActor, MailMessage, MailReceipt, MailTeamMember
from app.models.agent_mail_schemas import (
    MAIL_MESSAGE_KINDS,
    MAIL_REQUEST_KINDS,
    MailAgentRegisterRequest,
    MailInboxResponse,
    MailMemberResponse,
    MailMessageCreate,
    MailMessageResponse,
    MailSessionResponse,
    MailThreadResponse,
)
from app.services.agent_bridge.discovery import discover_agent_sessions
from app.services.scheduling.tmux_inject import send_text
from app.utils.repo_utils import derive_repo_identity

logger = logging.getLogger(__name__)

HEARTBEAT_TTL_SECONDS = 180
MCP_HEARTBEAT_TTL_SECONDS = 3600
OBSERVED_TTL_SECONDS = 300
STALE_REQUEST_MINUTES = 15
AUTO_NUDGE_COOLDOWN_SECONDS = 30
TMUX_WAKE_PROVIDERS = {"claude-code", "codex-cli"}
INBOX_CHECK_PROMPT = (
    "Claude Cockpit Agent Mail: please call `agent_mail_check_inbox(unread_only=False)` now, "
    "then answer any pending context requests or handoffs before continuing."
)


class AgentMailService:
    """Registry, messaging, and delivery-context behavior for Agent Mail."""

    def __init__(self) -> None:
        self._last_auto_nudge_at: dict[int, datetime] = {}

    def _repo_member_values(self, cwd: str) -> dict[str, str]:
        ident = derive_repo_identity(cwd)
        return {
            "identity_key": f"repo:{ident['repo_id']}",
            "repo_id": ident["repo_id"],
            "repo_path": ident["repo_root"],
            "repo_name": ident["repo_name"],
            "display_name": ident["repo_name"],
        }

    async def _get_or_create_repo_member(self, db: AsyncSession, cwd: str) -> MailTeamMember:
        values = self._repo_member_values(cwd)
        result = await db.execute(
            select(MailTeamMember).where(MailTeamMember.identity_key == values["identity_key"])
        )
        member = result.scalar_one_or_none()
        if member is None:
            member = MailTeamMember(**values)
            try:
                async with db.begin_nested():
                    db.add(member)
                    await db.flush()
            except IntegrityError:
                result = await db.execute(
                    select(MailTeamMember).where(MailTeamMember.identity_key == values["identity_key"])
                )
                member = result.scalar_one()
        else:
            member.repo_id = values["repo_id"]
            member.repo_path = values["repo_path"]
            member.repo_name = values["repo_name"]
            member.updated_at = datetime.utcnow()
        return member

    async def get_or_create_repo_member(self, db: AsyncSession, cwd: str) -> MailTeamMember:
        return await self._get_or_create_repo_member(db, cwd)

    async def register_session(
        self, db: AsyncSession, request: MailAgentRegisterRequest
    ) -> tuple[MailTeamMember, MailAgentSession]:
        member = await self._get_or_create_repo_member(db, request.cwd)
        result = await db.execute(
            select(MailAgentSession).where(MailAgentSession.session_key == request.session_key)
        )
        session = result.scalar_one_or_none()
        if session is None:
            session = MailAgentSession(
                member_id=member.id, source=request.source, session_key=request.session_key,
            )
            db.add(session)
        session.member_id = member.id
        session.provider = request.provider
        session.cwd = request.cwd
        session.pid = request.pid
        session.mailbox_status = "connected"
        session.last_seen_at = datetime.utcnow()
        await db.commit()
        await db.refresh(member)
        await db.refresh(session)
        return member, session

    async def heartbeat_session(
        self, db: AsyncSession, session_key: str, activity: Optional[str] = None
    ) -> Optional[MailAgentSession]:
        result = await db.execute(
            select(MailAgentSession).where(MailAgentSession.session_key == session_key)
        )
        session = result.scalar_one_or_none()
        if session is None:
            return None
        session.last_seen_at = datetime.utcnow()
        session.mailbox_status = "connected" if session.source != "observed" else "observed"
        if activity:
            session.activity = activity[:200]
        await db.commit()
        return session

    async def mark_session_offline(self, db: AsyncSession, session_key: str) -> None:
        result = await db.execute(
            select(MailAgentSession).where(MailAgentSession.session_key == session_key)
        )
        session = result.scalar_one_or_none()
        if session is not None:
            session.mailbox_status = "offline"
            await db.commit()

    async def heartbeat_member_mcp_session(self, db: AsyncSession, member_id: int) -> None:
        """Refresh the newest MCP session for a member when an MCP tool calls in."""
        result = await db.execute(
            select(MailAgentSession)
            .where(MailAgentSession.member_id == member_id, MailAgentSession.source == "mcp")
            .order_by(MailAgentSession.last_seen_at.desc())
            .limit(1)
        )
        session = result.scalar_one_or_none()
        if session is None:
            return
        session.last_seen_at = datetime.utcnow()
        session.mailbox_status = "connected"
        await db.commit()

    def _pid_is_running(self, pid: Optional[int]) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False

    def _effective_status(self, session: MailAgentSession, now: datetime) -> str:
        if session.source == "mcp" and session.pid:
            if not self._pid_is_running(session.pid):
                return "offline"
            if session.mailbox_status == "offline":
                return "connected"
        if session.mailbox_status == "offline":
            return "offline"
        if session.source == "observed":
            ttl = OBSERVED_TTL_SECONDS
        elif session.source == "mcp":
            ttl = MCP_HEARTBEAT_TTL_SECONDS
        else:
            ttl = HEARTBEAT_TTL_SECONDS
        if session.last_seen_at < now - timedelta(seconds=ttl):
            if session.source == "mcp" and session.pid:
                return "connected"
            return "offline"
        return session.mailbox_status


agent_mail_service = AgentMailService()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_registration.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_mail_service.py backend/tests/agent_mail/
git commit -m "feat(agent-mail): add service layer identity/registration/heartbeat"
```

---

### Task 5: Service layer — tmux discovery sync

**Files:**
- Modify: `backend/app/services/agent_mail_service.py` (add methods to `AgentMailService`, add module-level PID helpers)
- Test: `backend/tests/agent_mail/test_discovery_sync.py`

**Interfaces:**
- Consumes: `discover_agent_sessions()` from `app.services.agent_bridge.discovery` — returns `list[dict]` with keys `pane_id`, `cwd`, `provider`, `tmux_target`, `pid` (confirmed shape from `backend/app/services/agent_bridge/discovery.py::_build_session_info_from_parts`).
- Produces: `sync_observed_sessions(db) -> None`, `_session_can_nudge(session, now) -> bool` (needed by Task 7 too).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/agent_mail/test_discovery_sync.py
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.database import AsyncSessionLocal, Base, engine
from app.models.agent_mail import MailAgentSession
from app.services.agent_mail_service import agent_mail_service
from sqlalchemy import select


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_sync_observed_sessions_creates_member_and_session(tmp_path):
    discovered = [{
        "pane_id": "%1", "cwd": str(tmp_path), "provider": "claude-code",
        "tmux_target": "sess:0.0", "pid": 12345,
    }]
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=discovered):
        async with AsyncSessionLocal() as s:
            await agent_mail_service.sync_observed_sessions(s)

            row = (await s.execute(
                select(MailAgentSession).where(MailAgentSession.session_key == "tmux:%1")
            )).scalar_one()
            assert row.source == "observed"
            assert row.provider == "claude-code"
            assert row.tmux_target == "sess:0.0"
            assert row.mailbox_status == "observed"


@pytest.mark.asyncio
async def test_sync_removes_stale_observed_sessions(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[
        {"pane_id": "%1", "cwd": str(tmp_path), "provider": "claude-code", "tmux_target": "s:0.0", "pid": 1},
    ]):
        async with AsyncSessionLocal() as s:
            await agent_mail_service.sync_observed_sessions(s)

    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[]):
        async with AsyncSessionLocal() as s:
            await agent_mail_service.sync_observed_sessions(s)
            remaining = (await s.execute(select(MailAgentSession))).scalars().all()
            assert remaining == []


def test_session_can_nudge_requires_observed_wake_provider_and_tmux_target():
    now = datetime.utcnow()
    good = MailAgentSession(
        source="observed", provider="claude-code", tmux_target="s:0.0",
        mailbox_status="observed", last_seen_at=now, session_key="tmux:%1", member_id=1,
    )
    assert agent_mail_service._session_can_nudge(good, now) is True

    wrong_provider = MailAgentSession(
        source="observed", provider="unknown", tmux_target="s:0.0",
        mailbox_status="observed", last_seen_at=now, session_key="tmux:%2", member_id=1,
    )
    assert agent_mail_service._session_can_nudge(wrong_provider, now) is False

    not_observed = MailAgentSession(
        source="hook", provider="claude-code", tmux_target="s:0.0",
        mailbox_status="connected", last_seen_at=now, session_key="cc:1", member_id=1,
    )
    assert agent_mail_service._session_can_nudge(not_observed, now) is False

    stale = MailAgentSession(
        source="observed", provider="codex-cli", tmux_target="s:0.0",
        mailbox_status="observed", last_seen_at=now - timedelta(seconds=999), session_key="tmux:%3", member_id=1,
    )
    assert agent_mail_service._session_can_nudge(stale, now) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_discovery_sync.py -v`
Expected: FAIL with `AttributeError: 'AgentMailService' object has no attribute 'sync_observed_sessions'`

- [ ] **Step 3: Write the implementation**

Add the import at the top of `backend/app/services/agent_mail_service.py` (already listed in Task 4's import block: `from app.services.agent_bridge.discovery import discover_agent_sessions`). Insert these methods into `AgentMailService`, right after `heartbeat_member_mcp_session`:

```python
    async def sync_observed_sessions(self, db: AsyncSession) -> None:
        """Upsert Agent Bridge tmux discoveries as observed sessions."""
        try:
            discovered = discover_agent_sessions()
        except Exception as exc:
            logger.warning("agent bridge discovery failed: %s", exc)
            return
        active_observed_keys: set[str] = set()
        for info in discovered:
            pane_id = info.get("pane_id")
            cwd = info.get("cwd")
            if not pane_id or not cwd:
                continue
            session_key = f"tmux:{pane_id}"
            active_observed_keys.add(session_key)
            result = await db.execute(
                select(MailAgentSession).where(MailAgentSession.session_key == session_key)
            )
            session = result.scalar_one_or_none()
            member = await self._member_for_observed_session(db, info)
            if session is None:
                session = MailAgentSession(member_id=member.id, source="observed", session_key=session_key)
                db.add(session)
            session.member_id = member.id
            session.provider = info.get("provider", "unknown")
            session.cwd = cwd
            session.tmux_target = info.get("tmux_target")
            session.pane_id = pane_id
            try:
                session.pid = int(info.get("pid") or 0) or None
            except (TypeError, ValueError):
                session.pid = None
            session.mailbox_status = "observed"
            session.last_seen_at = datetime.utcnow()
        await self._remove_stale_observed_sessions(db, active_observed_keys)
        await db.commit()

    async def _member_for_observed_session(self, db: AsyncSession, info: dict) -> MailTeamMember:
        """Match an observed tmux pane to an already-registered hook/MCP session
        of the same provider via PID ancestry, so one logical agent doesn't get
        two member rows (a hook-registered session plus a tmux-observed one)."""
        cwd = str(info.get("cwd") or "")
        provider = str(info.get("provider") or "unknown")
        try:
            pid = int(info.get("pid") or 0) or None
        except (TypeError, ValueError):
            pid = None

        if pid is not None:
            now = datetime.utcnow()
            result = await db.execute(
                select(MailAgentSession).where(
                    MailAgentSession.source != "observed",
                    MailAgentSession.provider == provider,
                    MailAgentSession.pid.is_not(None),
                    MailAgentSession.last_seen_at >= now - timedelta(seconds=HEARTBEAT_TTL_SECONDS),
                ).order_by(MailAgentSession.last_seen_at.desc())
            )
            for registered in result.scalars().all():
                if not registered.pid or not self._pids_related(pid, int(registered.pid)):
                    continue
                if self._registered_session_matches_observed(registered, info, now):
                    member = await db.get(MailTeamMember, registered.member_id)
                    if member is not None:
                        return member

        return await self._get_or_create_repo_member(db, cwd)

    def _pids_related(self, left_pid: int, right_pid: int) -> bool:
        return (
            left_pid == right_pid
            or self._pid_is_descendant(left_pid, right_pid)
            or self._pid_is_descendant(right_pid, left_pid)
        )

    def _pid_is_descendant(self, child_pid: int, ancestor_pid: int) -> bool:
        current = child_pid
        visited: set[int] = set()
        for _ in range(8):
            if current == ancestor_pid:
                return True
            if current in visited:
                return False
            visited.add(current)
            try:
                result = subprocess.run(
                    ["ps", "-o", "ppid=", "-p", str(current)], capture_output=True, text=True, timeout=1,
                )
            except (OSError, subprocess.SubprocessError):
                return False
            if result.returncode != 0:
                return False
            try:
                current = int(result.stdout.strip() or "0")
            except ValueError:
                return False
            if current <= 1:
                return False
        return False

    def _registered_session_matches_observed(self, session: MailAgentSession, info: dict, now: datetime) -> bool:
        cwd = str(info.get("cwd") or "")
        if not session.cwd or not cwd:
            return False
        try:
            if derive_repo_identity(session.cwd)["repo_id"] != derive_repo_identity(cwd)["repo_id"]:
                return False
        except Exception:
            if os.path.realpath(session.cwd) != os.path.realpath(cwd):
                return False
        if session.last_seen_at < now - timedelta(seconds=HEARTBEAT_TTL_SECONDS):
            return False
        return self._effective_status(session, now) != "offline"

    async def _remove_stale_observed_sessions(self, db: AsyncSession, active_observed_keys: set[str]) -> None:
        """Drop tmux-only sessions no longer discoverable, and empty auto-created members."""
        result = await db.execute(select(MailAgentSession).where(MailAgentSession.source == "observed"))
        affected_member_ids: set[int] = set()
        for session in result.scalars().all():
            if session.session_key in active_observed_keys:
                continue
            affected_member_ids.add(session.member_id)
            await db.delete(session)
        if not affected_member_ids:
            return
        await db.flush()
        for member_id in affected_member_ids:
            await self._remove_empty_observed_member(db, member_id)

    async def _remove_empty_observed_member(self, db: AsyncSession, member_id: int) -> None:
        """Remove auto-observed members only when they have no durable user/mail state."""
        member = await db.get(MailTeamMember, member_id)
        if member is None:
            return
        if member.role or member.charter or member.display_name != member.repo_name:
            return
        session_count = (await db.execute(
            select(func.count()).select_from(MailAgentSession).where(MailAgentSession.member_id == member_id)
        )).scalar_one()
        if session_count:
            return
        message_count = (await db.execute(
            select(func.count()).select_from(MailMessage).where(
                or_(MailMessage.sender_member_id == member_id, MailMessage.recipient_member_id == member_id)
            )
        )).scalar_one()
        receipt_count = (await db.execute(
            select(func.count()).select_from(MailReceipt).where(MailReceipt.member_id == member_id)
        )).scalar_one()
        if message_count or receipt_count:
            return
        await db.delete(member)

    def _session_can_nudge(self, session: MailAgentSession, now: datetime) -> bool:
        return bool(
            session.source == "observed"
            and session.provider in TMUX_WAKE_PROVIDERS
            and session.tmux_target
            and self._effective_status(session, now) == "observed"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_discovery_sync.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_mail_service.py backend/tests/agent_mail/test_discovery_sync.py
git commit -m "feat(agent-mail): sync tmux-observed sessions via agent_bridge discovery"
```

---

### Task 6: Service layer — messaging

**Files:**
- Modify: `backend/app/services/agent_mail_service.py` (add methods)
- Test: `backend/tests/agent_mail/test_messaging.py`

**Interfaces:**
- Produces: `send_message(db, request: MailMessageCreate, *, auto_nudge=True, sender_actor_id=None) -> MailMessageResponse`, `counts_for_member(db, member_id) -> tuple[int, int]`, `delivery_counts_for_member(db, member_id) -> tuple[int, int, int, int]`, `get_inbox(db, member_id, unread_only=False, mark_read=False, limit=50, refresh_mcp_session=False) -> MailInboxResponse`, `mark_read(db, message_id, member_id) -> None`, `ack_message(db, message_id, member_id) -> None`, `get_thread(db, root_id, for_member_id=None) -> MailThreadResponse`, `list_root_messages(db, limit=100) -> list[MailMessageResponse]`, `recipient_ids_for_message(db, message_id) -> set[int]`. `send_message` calls `self.auto_nudge_members(...)` which Task 7 adds — stub it out temporarily as a no-op is NOT needed since Task 7 lands before this is exercised end-to-end; write `send_message` calling `self.auto_nudge_members` now (it will `AttributeError` until Task 7 lands, which is fine — Step 4 below passes `auto_nudge=False` in every test to avoid that path).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/agent_mail/test_messaging.py
import pytest

from app.database import AsyncSessionLocal, Base, engine
from app.models.agent_mail_schemas import MailAgentRegisterRequest, MailMessageCreate
from app.services.agent_mail_service import agent_mail_service


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _two_members(s, tmp_path):
    m1, _ = await agent_mail_service.register_session(
        s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path / "a"), session_key="cc:a")
    )
    m2, _ = await agent_mail_service.register_session(
        s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path / "b"), session_key="cc:b")
    )
    return m1, m2


@pytest.mark.asyncio
async def test_send_direct_message_creates_receipt_for_recipient(tmp_path):
    async with AsyncSessionLocal() as s:
        m1, m2 = await _two_members(s, tmp_path)
        msg = await agent_mail_service.send_message(
            s, MailMessageCreate(sender_member_id=m1.id, recipient_member_id=m2.id, body_markdown="hi"),
            auto_nudge=False,
        )
        assert msg.sender_name == m1.display_name
        inbox = await agent_mail_service.get_inbox(s, m2.id)
        assert inbox.unread_count == 1
        assert inbox.messages[0].body_markdown == "hi"


@pytest.mark.asyncio
async def test_broadcast_reaches_all_other_members(tmp_path):
    async with AsyncSessionLocal() as s:
        m1, m2 = await _two_members(s, tmp_path)
        await agent_mail_service.send_message(
            s, MailMessageCreate(kind="broadcast", sender_member_id=m1.id, body_markdown="hello team"),
            auto_nudge=False,
        )
        inbox = await agent_mail_service.get_inbox(s, m2.id)
        assert inbox.unread_count == 1


@pytest.mark.asyncio
async def test_answer_marks_context_request_answered(tmp_path):
    async with AsyncSessionLocal() as s:
        m1, m2 = await _two_members(s, tmp_path)
        req = await agent_mail_service.send_message(
            s, MailMessageCreate(
                kind="context_request", sender_member_id=m1.id, recipient_member_id=m2.id, body_markdown="?",
            ), auto_nudge=False,
        )
        assert req.request_status == "pending"

        await agent_mail_service.send_message(
            s, MailMessageCreate(
                kind="answer", sender_member_id=m2.id, thread_root_id=req.id, body_markdown="answer",
            ), auto_nudge=False,
        )
        thread = await agent_mail_service.get_thread(s, req.id)
        assert thread.root.request_status == "answered"
        assert thread.replies[0].kind == "answer"


@pytest.mark.asyncio
async def test_mark_read_and_ack_message(tmp_path):
    async with AsyncSessionLocal() as s:
        m1, m2 = await _two_members(s, tmp_path)
        msg = await agent_mail_service.send_message(
            s, MailMessageCreate(
                kind="handoff", sender_member_id=m1.id, recipient_member_id=m2.id, body_markdown="take over",
            ), auto_nudge=False,
        )
        await agent_mail_service.mark_read(s, msg.id, m2.id)
        await agent_mail_service.ack_message(s, msg.id, m2.id)

        thread = await agent_mail_service.get_thread(s, msg.id, for_member_id=m2.id)
        assert thread.root.read_at is not None
        assert thread.root.acked_at is not None
        assert thread.root.request_status == "acknowledged"


@pytest.mark.asyncio
async def test_answer_requires_pending_context_request_addressed_to_sender(tmp_path):
    async with AsyncSessionLocal() as s:
        m1, m2 = await _two_members(s, tmp_path)
        note = await agent_mail_service.send_message(
            s, MailMessageCreate(sender_member_id=m1.id, recipient_member_id=m2.id, body_markdown="note"),
            auto_nudge=False,
        )
        with pytest.raises(ValueError, match="answer messages can only resolve context requests"):
            await agent_mail_service.send_message(
                s, MailMessageCreate(
                    kind="answer", sender_member_id=m2.id, thread_root_id=note.id, body_markdown="x",
                ), auto_nudge=False,
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_messaging.py -v`
Expected: FAIL with `AttributeError: 'AgentMailService' object has no attribute 'send_message'`

- [ ] **Step 3: Write the implementation**

Add `Optional` and `List` to the existing `typing` import line if not already present (`from typing import List, Optional`). Insert these methods into `AgentMailService`, after `_session_can_nudge` (end of Task 5's additions):

```python
    async def send_message(
        self,
        db: AsyncSession,
        request: MailMessageCreate,
        *,
        auto_nudge: bool = True,
        sender_actor_id: Optional[int] = None,
    ) -> MailMessageResponse:
        if request.kind not in MAIL_MESSAGE_KINDS:
            raise ValueError(f"Invalid message kind: {request.kind}")
        if request.sender_member_id is not None and sender_actor_id is not None:
            raise ValueError("messages cannot have both sender_member_id and sender_actor_id")
        if request.kind == "answer" and request.thread_root_id is None:
            raise ValueError("answer messages require thread_root_id")
        if request.kind == "answer":
            root = await db.get(MailMessage, request.thread_root_id)
            if root is None:
                raise ValueError("answer messages require an existing thread root")
            if root.kind != "context_request":
                raise ValueError("answer messages can only resolve context requests")
            if root.recipient_member_id != request.sender_member_id:
                raise ValueError("only the context request recipient can answer it")
        if request.kind in MAIL_REQUEST_KINDS and request.recipient_member_id is None:
            raise ValueError(f"{request.kind} requires recipient_member_id")

        message = MailMessage(
            thread_root_id=request.thread_root_id,
            kind=request.kind,
            sender_member_id=request.sender_member_id,
            sender_actor_id=sender_actor_id,
            recipient_member_id=request.recipient_member_id,
            subject=request.subject,
            body_markdown=request.body_markdown,
            payload=request.payload,
            request_status="pending" if request.kind in MAIL_REQUEST_KINDS else None,
        )
        db.add(message)
        await db.flush()

        recipients: set[int] = set()
        if request.recipient_member_id is not None:
            recipients.add(request.recipient_member_id)
        elif request.thread_root_id is not None:
            root = await db.get(MailMessage, request.thread_root_id)
            if root is not None:
                for member_id in (root.sender_member_id, root.recipient_member_id):
                    if member_id is not None and member_id != request.sender_member_id:
                        recipients.add(member_id)
        else:
            members = (await db.execute(select(MailTeamMember))).scalars().all()
            recipients = {member.id for member in members if member.id != request.sender_member_id}

        for member_id in recipients:
            db.add(MailReceipt(message_id=message.id, member_id=member_id))

        if request.kind == "answer":
            root = await db.get(MailMessage, request.thread_root_id)
            if root is not None and root.request_status == "pending":
                root.request_status = "answered"

        await db.commit()
        await db.refresh(message)
        if auto_nudge:
            await self.auto_nudge_members(db, recipients)
        return await self._message_response(db, message, for_member_id=None)

    async def _sender_identity(
        self, db: AsyncSession, sender_member_id: Optional[int], sender_actor_id: Optional[int],
    ) -> tuple[str, str, str | None]:
        if sender_actor_id is not None:
            actor = await db.get(MailExternalActor, sender_actor_id)
            if actor is not None:
                return actor.display_name, "external_actor", actor.kind
            return "unknown external actor", "external_actor", None
        if sender_member_id is None:
            return "Director", "director", None
        member = await db.get(MailTeamMember, sender_member_id)
        return (member.display_name if member else "unknown", "member", None)

    async def _message_response(
        self, db: AsyncSession, message: MailMessage, for_member_id: Optional[int]
    ) -> MailMessageResponse:
        read_at = acked_at = None
        if for_member_id is not None:
            result = await db.execute(
                select(MailReceipt).where(
                    MailReceipt.message_id == message.id, MailReceipt.member_id == for_member_id,
                )
            )
            receipt = result.scalar_one_or_none()
            if receipt is not None:
                read_at, acked_at = receipt.read_at, receipt.acked_at
        is_stale = (
            message.kind in MAIL_REQUEST_KINDS
            and message.request_status == "pending"
            and message.created_at < datetime.utcnow() - timedelta(minutes=STALE_REQUEST_MINUTES)
        )
        sender_name, sender_type, sender_actor_kind = await self._sender_identity(
            db, message.sender_member_id, message.sender_actor_id,
        )
        return MailMessageResponse(
            id=message.id, thread_root_id=message.thread_root_id, kind=message.kind,
            sender_member_id=message.sender_member_id, sender_actor_id=message.sender_actor_id,
            sender_type=sender_type, sender_actor_kind=sender_actor_kind, sender_name=sender_name,
            recipient_member_id=message.recipient_member_id, subject=message.subject,
            body_markdown=message.body_markdown, payload=message.payload,
            request_status=message.request_status, is_stale=is_stale,
            read_at=read_at, acked_at=acked_at, created_at=message.created_at,
        )

    async def counts_for_member(self, db: AsyncSession, member_id: int) -> tuple[int, int]:
        unread = (await db.execute(
            select(func.count()).select_from(MailReceipt).where(
                MailReceipt.member_id == member_id, MailReceipt.read_at.is_(None),
            )
        )).scalar_one()
        pending = (await db.execute(
            select(func.count()).select_from(MailMessage).where(
                MailMessage.recipient_member_id == member_id,
                MailMessage.kind.in_(MAIL_REQUEST_KINDS),
                MailMessage.request_status == "pending",
            )
        )).scalar_one()
        return unread, pending

    async def delivery_counts_for_member(self, db: AsyncSession, member_id: int) -> tuple[int, int, int, int]:
        unread, pending = await self.counts_for_member(db, member_id)
        unseen_pending = (await db.execute(
            select(func.count()).select_from(MailMessage)
            .join(MailReceipt, MailReceipt.message_id == MailMessage.id)
            .where(
                MailReceipt.member_id == member_id, MailReceipt.read_at.is_(None),
                MailMessage.kind.in_(MAIL_REQUEST_KINDS), MailMessage.request_status == "pending",
            )
        )).scalar_one()
        stale_cutoff = datetime.utcnow() - timedelta(minutes=STALE_REQUEST_MINUTES)
        stale_pending = (await db.execute(
            select(func.count()).select_from(MailMessage).where(
                MailMessage.recipient_member_id == member_id,
                MailMessage.kind.in_(MAIL_REQUEST_KINDS), MailMessage.request_status == "pending",
                MailMessage.created_at < stale_cutoff,
            )
        )).scalar_one()
        return unread, pending, unseen_pending, stale_pending

    async def get_inbox(
        self, db: AsyncSession, member_id: int, unread_only: bool = False,
        mark_read: bool = False, limit: int = 50, refresh_mcp_session: bool = False,
    ) -> MailInboxResponse:
        if refresh_mcp_session:
            await self.heartbeat_member_mcp_session(db, member_id)
        query = (
            select(MailMessage, MailReceipt)
            .join(MailReceipt, MailReceipt.message_id == MailMessage.id)
            .where(MailReceipt.member_id == member_id)
            .order_by(MailMessage.created_at.desc())
            .limit(limit)
        )
        if unread_only:
            query = query.where(MailReceipt.read_at.is_(None))
        rows = (await db.execute(query)).all()
        messages = []
        now = datetime.utcnow()
        if mark_read:
            member = await db.get(MailTeamMember, member_id)
            if member is not None:
                member.last_inbox_checked_at = now
        for message, receipt in rows:
            if mark_read and receipt.read_at is None:
                receipt.read_at = now
            messages.append(await self._message_response(db, message, for_member_id=member_id))
        if mark_read:
            await db.commit()
        unread, pending = await self.counts_for_member(db, member_id)
        return MailInboxResponse(member_id=member_id, unread_count=unread, pending_count=pending, messages=messages)

    async def recipient_ids_for_message(self, db: AsyncSession, message_id: int) -> set[int]:
        rows = (await db.execute(
            select(MailReceipt.member_id).where(MailReceipt.message_id == message_id)
        )).scalars().all()
        return set(rows)

    async def mark_read(self, db: AsyncSession, message_id: int, member_id: int) -> None:
        result = await db.execute(
            select(MailReceipt).where(MailReceipt.message_id == message_id, MailReceipt.member_id == member_id)
        )
        receipt = result.scalar_one_or_none()
        if receipt is not None and receipt.read_at is None:
            receipt.read_at = datetime.utcnow()
            await db.commit()

    async def ack_message(self, db: AsyncSession, message_id: int, member_id: int) -> None:
        result = await db.execute(
            select(MailReceipt).where(MailReceipt.message_id == message_id, MailReceipt.member_id == member_id)
        )
        receipt = result.scalar_one_or_none()
        if receipt is None:
            return
        now = datetime.utcnow()
        receipt.read_at = receipt.read_at or now
        receipt.acked_at = receipt.acked_at or now

        message = await db.get(MailMessage, message_id)
        if (
            message is not None and message.kind == "handoff" and message.thread_root_id is None
            and message.recipient_member_id == member_id and message.request_status == "pending"
        ):
            message.request_status = "acknowledged"
        if message is not None and message.kind == "answer" and message.thread_root_id:
            root = await db.get(MailMessage, message.thread_root_id)
            if root is not None and root.sender_member_id == member_id and root.request_status == "answered":
                root.request_status = "acknowledged"
        await db.commit()

    async def get_thread(
        self, db: AsyncSession, root_id: int, for_member_id: Optional[int] = None
    ) -> MailThreadResponse:
        root = await db.get(MailMessage, root_id)
        if root is None:
            raise ValueError(f"Message {root_id} not found")
        replies = (await db.execute(
            select(MailMessage).where(MailMessage.thread_root_id == root_id).order_by(MailMessage.created_at.asc())
        )).scalars().all()
        return MailThreadResponse(
            root=await self._message_response(db, root, for_member_id),
            replies=[await self._message_response(db, reply, for_member_id) for reply in replies],
        )

    async def list_root_messages(self, db: AsyncSession, limit: int = 100) -> List[MailMessageResponse]:
        roots = (await db.execute(
            select(MailMessage).where(MailMessage.thread_root_id.is_(None))
            .order_by(MailMessage.created_at.desc()).limit(limit)
        )).scalars().all()
        return [await self._message_response(db, root, for_member_id=None) for root in roots]
```

- [ ] **Step 4: Run test to verify it passes**

`send_message` calls `self.auto_nudge_members(...)` only when `auto_nudge=True`; every test above passes `auto_nudge=False`, so Task 7 (which defines `auto_nudge_members`) does not need to exist yet for these to pass.

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_messaging.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_mail_service.py backend/tests/agent_mail/test_messaging.py
git commit -m "feat(agent-mail): add messaging (send/inbox/thread/ack)"
```

---

### Task 7: Service layer — wakeability

**Files:**
- Modify: `backend/app/services/agent_mail_service.py` (add methods)
- Test: `backend/tests/agent_mail/test_wakeability.py`

**Interfaces:**
- Consumes: `send_text(tmux_target: str, text: str) -> bool` from `app.services.scheduling.tmux_inject` (already imported in Task 4's header).
- Produces: `_nudge_session_for_member(db, member_id, now) -> MailAgentSession | None`, `_wake_member(db, member_id, now) -> dict | None`, `auto_nudge_members(db, member_ids: set[int]) -> list[dict]`, `queue_inbox_check(db, member_id) -> dict`, `wake_members_with_results(db, member_ids: set[int]) -> dict[int, dict]`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/agent_mail/test_wakeability.py
from unittest.mock import patch

import pytest

from app.database import AsyncSessionLocal, Base, engine
from app.models.agent_mail import MailAgentSession
from app.models.agent_mail_schemas import MailAgentRegisterRequest
from app.services.agent_mail_service import agent_mail_service


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _observed_member(s, tmp_path, provider="claude-code"):
    member, _ = await agent_mail_service.register_session(
        s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:x")
    )
    session = MailAgentSession(
        member_id=member.id, source="observed", session_key="tmux:%1",
        provider=provider, tmux_target="sess:0.0", mailbox_status="observed",
    )
    s.add(session)
    await s.commit()
    return member


@pytest.mark.asyncio
async def test_queue_inbox_check_sends_tmux_text(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[]), \
         patch("app.services.agent_mail_service.send_text", return_value=True) as mock_send:
        async with AsyncSessionLocal() as s:
            member = await _observed_member(s, tmp_path)
            result = await agent_mail_service.queue_inbox_check(s, member.id)
            assert result["method"] == "tmux"
            assert result["target"] == "sess:0.0"
            mock_send.assert_called_once()
            assert mock_send.call_args[0][0] == "sess:0.0"


@pytest.mark.asyncio
async def test_queue_inbox_check_raises_when_not_wakeable(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[]):
        async with AsyncSessionLocal() as s:
            member, _ = await agent_mail_service.register_session(
                s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:offline")
            )
            with pytest.raises(ValueError, match="No Agent Mail wake path"):
                await agent_mail_service.queue_inbox_check(s, member.id)


@pytest.mark.asyncio
async def test_auto_nudge_respects_cooldown(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[]), \
         patch("app.services.agent_mail_service.send_text", return_value=True) as mock_send:
        async with AsyncSessionLocal() as s:
            member = await _observed_member(s, tmp_path)
            nudged1 = await agent_mail_service.auto_nudge_members(s, {member.id})
            nudged2 = await agent_mail_service.auto_nudge_members(s, {member.id})
            assert len(nudged1) == 1
            assert len(nudged2) == 0  # cooldown
            assert mock_send.call_count == 1


@pytest.mark.asyncio
async def test_wake_members_with_results_reports_offline(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[]):
        async with AsyncSessionLocal() as s:
            member, _ = await agent_mail_service.register_session(
                s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:y")
            )
            results = await agent_mail_service.wake_members_with_results(s, {member.id})
            assert results[member.id]["wake_attempted"] is False
            assert results[member.id]["wake_succeeded"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_wakeability.py -v`
Expected: FAIL with `AttributeError: 'AgentMailService' object has no attribute 'queue_inbox_check'`

- [ ] **Step 3: Write the implementation**

Insert these methods into `AgentMailService`, after `list_root_messages` (end of Task 6's additions):

```python
    async def _nudge_session_for_member(
        self, db: AsyncSession, member_id: int, now: datetime,
    ) -> MailAgentSession | None:
        result = await db.execute(
            select(MailAgentSession).where(
                MailAgentSession.member_id == member_id,
                MailAgentSession.source == "observed",
                MailAgentSession.provider.in_(sorted(TMUX_WAKE_PROVIDERS)),
                MailAgentSession.tmux_target.is_not(None),
            ).order_by(MailAgentSession.last_seen_at.desc())
        )
        return next(
            (c for c in result.scalars().all() if self._session_can_nudge(c, now)), None,
        )

    def _send_tmux_inbox_check(self, session: MailAgentSession) -> dict[str, str]:
        if not session.tmux_target:
            raise ValueError("No live tmux session is available for this member")
        if not send_text(session.tmux_target, INBOX_CHECK_PROMPT):
            raise ValueError("tmux send-keys failed")
        return {"target": session.tmux_target, "prompt": INBOX_CHECK_PROMPT}

    async def _wake_member(self, db: AsyncSession, member_id: int, now: datetime) -> dict[str, str] | None:
        session = await self._nudge_session_for_member(db, member_id, now)
        if session is not None:
            result = self._send_tmux_inbox_check(session)
            return {"method": "tmux", **result}
        return None

    async def auto_nudge_members(self, db: AsyncSession, member_ids: set[int]) -> list[dict[str, str | int]]:
        """Best-effort delivery wakeup for visible tmux-observed recipients."""
        if not member_ids:
            return []
        await self.sync_observed_sessions(db)
        now = datetime.utcnow()
        nudged: list[dict[str, str | int]] = []
        cooldown_cutoff = now - timedelta(seconds=AUTO_NUDGE_COOLDOWN_SECONDS)
        for member_id in sorted(member_ids):
            last_nudge_at = self._last_auto_nudge_at.get(member_id)
            if last_nudge_at is not None and last_nudge_at > cooldown_cutoff:
                continue
            try:
                result = await self._wake_member(db, member_id, now)
            except ValueError as exc:
                logger.debug("agent mail auto-nudge failed for member %s: %s", member_id, exc)
                continue
            if result is None:
                continue
            self._last_auto_nudge_at[member_id] = now
            nudged.append({"member_id": member_id, **result})
        return nudged

    async def wake_members_with_results(
        self, db: AsyncSession, member_ids: set[int],
    ) -> dict[int, dict[str, str | bool]]:
        if not member_ids:
            return {}
        await self.sync_observed_sessions(db)
        now = datetime.utcnow()
        results: dict[int, dict[str, str | bool]] = {}
        for member_id in sorted(member_ids):
            try:
                result = await self._wake_member(db, member_id, now)
            except ValueError as exc:
                results[member_id] = {"wake_attempted": True, "wake_succeeded": False, "wake_error": str(exc)}
                continue
            if result is None:
                results[member_id] = {"wake_attempted": False, "wake_succeeded": False}
                continue
            results[member_id] = {
                "wake_attempted": True, "wake_succeeded": True, "wake_method": str(result.get("method") or ""),
            }
        return results

    async def queue_inbox_check(self, db: AsyncSession, member_id: int) -> dict[str, str]:
        await self.sync_observed_sessions(db)
        now = datetime.utcnow()
        result = await self._wake_member(db, member_id, now)
        if result is None:
            raise ValueError("No Agent Mail wake path is available for this member")
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_wakeability.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_mail_service.py backend/tests/agent_mail/test_wakeability.py
git commit -m "feat(agent-mail): add tmux wakeability via scheduling.tmux_inject"
```

---

### Task 8: Service layer — team roster and prompt-context builders

**Files:**
- Modify: `backend/app/services/agent_mail_service.py` (add methods)
- Test: `backend/tests/agent_mail/test_team_and_context.py`

**Interfaces:**
- Produces: `_session_response(session, now) -> MailSessionResponse`, `list_team(db) -> list[MailMemberResponse]`, `build_session_start_context(db, member_id, session_key=None) -> str`, `build_prompt_submit_context(db, member_id) -> str | None`. This is the last set of methods added to `AgentMailService` — after this task the module ends with `agent_mail_service = AgentMailService()`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/agent_mail/test_team_and_context.py
from unittest.mock import patch

import pytest

from app.database import AsyncSessionLocal, Base, engine
from app.models.agent_mail_schemas import MailAgentRegisterRequest, MailMessageCreate
from app.services.agent_mail_service import agent_mail_service


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_list_team_reports_status_and_counts(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[]):
        async with AsyncSessionLocal() as s:
            member, _ = await agent_mail_service.register_session(
                s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:1")
            )
            team = await agent_mail_service.list_team(s)
            assert len(team) == 1
            assert team[0].status == "connected"
            assert team[0].wake_state == "delivered_waiting"


@pytest.mark.asyncio
async def test_build_session_start_context_includes_identity_and_inbox(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[]):
        async with AsyncSessionLocal() as s:
            m1, _ = await agent_mail_service.register_session(
                s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path / "a"), session_key="cc:a")
            )
            m2, _ = await agent_mail_service.register_session(
                s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path / "b"), session_key="cc:b")
            )
            await agent_mail_service.send_message(
                s, MailMessageCreate(sender_member_id=m1.id, recipient_member_id=m2.id, body_markdown="hi"),
                auto_nudge=False,
            )
            context = await agent_mail_service.build_session_start_context(s, m2.id)
            assert m2.display_name in context
            assert "1 unread" in context


@pytest.mark.asyncio
async def test_build_prompt_submit_context_none_when_empty_inbox(tmp_path):
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=[]):
        async with AsyncSessionLocal() as s:
            member, _ = await agent_mail_service.register_session(
                s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:solo")
            )
            assert await agent_mail_service.build_prompt_submit_context(s, member.id) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_team_and_context.py -v`
Expected: FAIL with `AttributeError: 'AgentMailService' object has no attribute 'list_team'`

- [ ] **Step 3: Write the implementation**

Insert these methods into `AgentMailService`, after `queue_inbox_check` (end of Task 7's additions), and before the closing `agent_mail_service = AgentMailService()` line:

```python
    def _session_response(self, session: MailAgentSession, now: datetime) -> MailSessionResponse:
        return MailSessionResponse(
            id=session.id, provider=session.provider, source=session.source,
            session_key=session.session_key, cwd=session.cwd, tmux_target=session.tmux_target,
            mailbox_status=self._effective_status(session, now), activity=session.activity,
            last_seen_at=session.last_seen_at,
        )

    async def list_team(self, db: AsyncSession) -> List[MailMemberResponse]:
        now = datetime.utcnow()
        members = (await db.execute(select(MailTeamMember))).scalars().all()
        sessions = (await db.execute(select(MailAgentSession))).scalars().all()
        by_member: dict[int, list[MailAgentSession]] = {}
        for session in sessions:
            by_member.setdefault(session.member_id, []).append(session)

        responses: List[MailMemberResponse] = []
        for member in members:
            member_sessions = by_member.get(member.id, [])
            session_responses = [self._session_response(s, now) for s in member_sessions]
            statuses = {s.mailbox_status for s in session_responses}
            if "connected" in statuses:
                status = "connected"
            elif "observed" in statuses:
                status = "observed"
            else:
                status = "offline"
            unread, pending, unseen_pending, stale_pending = await self.delivery_counts_for_member(db, member.id)
            wake_methods = ["tmux"] if any(self._session_can_nudge(s, now) for s in member_sessions) else []
            if status == "offline":
                wake_state = "offline"
            elif wake_methods:
                wake_state = "wakeable"
            else:
                wake_state = "delivered_waiting"
            responses.append(MailMemberResponse(
                id=member.id, identity_key=member.identity_key, repo_id=member.repo_id,
                repo_path=member.repo_path, repo_name=member.repo_name, display_name=member.display_name,
                role=member.role, charter=member.charter, status=status,
                unread_count=unread, pending_count=pending, unseen_pending_count=unseen_pending,
                stale_pending_count=stale_pending, can_nudge=bool(wake_methods), wake_methods=wake_methods,
                wake_state=wake_state, last_inbox_checked_at=member.last_inbox_checked_at,
                sessions=session_responses,
            ))
        responses.sort(key=lambda m: (m.status != "connected", m.display_name.lower()))
        return responses

    async def build_session_start_context(
        self, db: AsyncSession, member_id: int, session_key: str | None = None,
    ) -> str:
        member = await db.get(MailTeamMember, member_id)
        if member is None:
            return ""
        team = await self.list_team(db)
        me = next((c for c in team if c.id == member_id), None)
        others = [c for c in team if c.id != member_id]

        lines = ["[Claude Cockpit Agent Mail]"]
        role = f" ({member.role})" if member.role else ""
        lines.append(f'You are "{member.display_name}"{role} - repo: {member.repo_name}.')
        if member.charter:
            lines.append(f"Charter: {member.charter}")
        if others:
            roster = " | ".join(
                f"{c.display_name} ({c.role or c.repo_name}, {c.status})" for c in others[:8]
            )
            lines.append(f"Team: {roster}")
        if me is not None and (me.unread_count or me.pending_count):
            lines.append(
                f"Inbox: {me.unread_count} unread, {me.pending_count} pending request(s) awaiting your answer."
            )
        lines.append(
            "Coordinate via MCP tools: agent_mail_check_inbox, agent_mail_request_context, "
            "agent_mail_send_message, agent_mail_create_handoff."
        )
        return "\n".join(lines)

    async def build_prompt_submit_context(self, db: AsyncSession, member_id: int) -> Optional[str]:
        unread, pending = await self.counts_for_member(db, member_id)
        if not unread and not pending:
            return None
        parts = []
        if unread:
            parts.append(f"{unread} unread message(s)")
        if pending:
            parts.append(f"{pending} pending request(s)")
        return (
            f"[Agent Mail] You have {' and '.join(parts)}. Call agent_mail_check_inbox when convenient."
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/ -v`
Expected: PASS (all tests across Tasks 4-8, ~17 tests total)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_mail_service.py backend/tests/agent_mail/test_team_and_context.py
git commit -m "feat(agent-mail): add team roster and session-start/prompt-submit context builders"
```

---

### Task 9: Internal REST API — core routes

**Files:**
- Create: `backend/app/api/v1/agent_mail.py`
- Modify: `backend/app/api/v1/router.py` (register the new router)
- Test: `backend/tests/agent_mail/test_api.py`

**Interfaces:**
- Consumes: `agent_mail_service` (Tasks 4-8), schemas (Task 3).
- Produces: `router: APIRouter` mounted at `/api/v1/agent-mail` with routes `GET /team`, `PATCH /members/{id}`, `POST /messages`, `GET /messages`, `GET /messages/{id}/thread`, `POST /messages/{id}/read`, `POST /messages/{id}/ack`, `POST /members/{id}/queue-inbox-check`, `POST /agent/register`, `GET /agent/inbox`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/agent_mail/test_api.py
import pytest
from httpx import ASGITransport, AsyncClient

from app.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_register_then_send_and_read_message(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post("/api/v1/agent-mail/agent/register", json={
            "source": "hook", "provider": "claude-code",
            "cwd": str(tmp_path / "a"), "session_key": "cc:a",
        })
        assert r1.status_code == 200
        member_a = r1.json()["member"]["id"]

        r2 = await client.post("/api/v1/agent-mail/agent/register", json={
            "source": "hook", "provider": "claude-code",
            "cwd": str(tmp_path / "b"), "session_key": "cc:b",
        })
        member_b = r2.json()["member"]["id"]

        r3 = await client.post("/api/v1/agent-mail/messages", json={
            "sender_member_id": member_a, "recipient_member_id": member_b, "body_markdown": "hi",
        })
        assert r3.status_code == 200
        message_id = r3.json()["id"]

        r4 = await client.get("/api/v1/agent-mail/agent/inbox", params={"member_id": member_b})
        assert r4.json()["unread_count"] == 1

        r5 = await client.post(f"/api/v1/agent-mail/messages/{message_id}/read", json={"member_id": member_b})
        assert r5.status_code == 200

        r6 = await client.get("/api/v1/agent-mail/team")
        assert len(r6.json()["members"]) == 2


@pytest.mark.asyncio
async def test_update_member_role_and_charter(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post("/api/v1/agent-mail/agent/register", json={
            "source": "hook", "cwd": str(tmp_path), "session_key": "cc:x",
        })
        member_id = r1.json()["member"]["id"]
        r2 = await client.patch(f"/api/v1/agent-mail/members/{member_id}", json={
            "role": "reviewer", "charter": "reviews PRs",
        })
        assert r2.status_code == 200
        assert r2.json()["role"] == "reviewer"


@pytest.mark.asyncio
async def test_queue_inbox_check_400_when_not_wakeable(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post("/api/v1/agent-mail/agent/register", json={
            "source": "hook", "cwd": str(tmp_path), "session_key": "cc:z",
        })
        member_id = r1.json()["member"]["id"]
        r2 = await client.post(f"/api/v1/agent-mail/members/{member_id}/queue-inbox-check")
        assert r2.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_api.py -v`
Expected: FAIL with 404 (route not mounted) — `assert r1.status_code == 200` fails.

- [ ] **Step 3: Write the implementation**

```python
# backend/app/api/v1/agent_mail.py
"""Agent Mail endpoints: team roster, messages, agent registration, hooks."""
import logging
import os
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent_mail import MailTeamMember
from app.models.agent_mail_schemas import (
    MailAgentRegisterRequest,
    MailAgentRegisterResponse,
    MailInboxResponse,
    MailMemberResponse,
    MailMemberUpdate,
    MailMessageCreate,
    MailMessageResponse,
    MailThreadResponse,
    TeamListResponse,
)
from app.services.agent_mail_service import agent_mail_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/team", response_model=TeamListResponse)
async def get_team(sync: bool = True, db: AsyncSession = Depends(get_db)):
    if sync:
        await agent_mail_service.sync_observed_sessions(db)
    return TeamListResponse(members=await agent_mail_service.list_team(db))


@router.patch("/members/{member_id}", response_model=MailMemberResponse)
async def update_member(member_id: int, update: MailMemberUpdate, db: AsyncSession = Depends(get_db)):
    member = await db.get(MailTeamMember, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if update.display_name is not None:
        member.display_name = update.display_name.strip() or member.display_name
    if update.role is not None:
        member.role = update.role.strip() or None
    if update.charter is not None:
        member.charter = update.charter.strip() or None
    member.updated_at = datetime.utcnow()
    await db.commit()
    members = await agent_mail_service.list_team(db)
    found = next((c for c in members if c.id == member_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return found


@router.post("/messages", response_model=MailMessageResponse)
async def send_message(request: MailMessageCreate, db: AsyncSession = Depends(get_db)):
    try:
        return await agent_mail_service.send_message(db, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/messages", response_model=list[MailMessageResponse])
async def list_messages(db: AsyncSession = Depends(get_db)):
    return await agent_mail_service.list_root_messages(db)


@router.get("/messages/{message_id}/thread", response_model=MailThreadResponse)
async def get_thread(message_id: int, member_id: Optional[int] = None, db: AsyncSession = Depends(get_db)):
    try:
        return await agent_mail_service.get_thread(db, message_id, for_member_id=member_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/messages/{message_id}/read")
async def mark_read(message_id: int, body: dict[str, Any] = Body(...), db: AsyncSession = Depends(get_db)):
    await agent_mail_service.mark_read(db, message_id, int(body["member_id"]))
    return {"ok": True}


@router.post("/messages/{message_id}/ack")
async def ack_message(message_id: int, body: dict[str, Any] = Body(...), db: AsyncSession = Depends(get_db)):
    await agent_mail_service.ack_message(db, message_id, int(body["member_id"]))
    return {"ok": True}


@router.post("/members/{member_id}/queue-inbox-check")
async def queue_inbox_check(member_id: int, db: AsyncSession = Depends(get_db)):
    try:
        result = await agent_mail_service.queue_inbox_check(db, member_id)
        return {"ok": True, **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/agent/register", response_model=MailAgentRegisterResponse)
async def register_agent(request: MailAgentRegisterRequest, db: AsyncSession = Depends(get_db)):
    member, session = await agent_mail_service.register_session(db, request)
    members = await agent_mail_service.list_team(db)
    member_resp = next(c for c in members if c.id == member.id)
    session_resp = next(c for c in member_resp.sessions if c.session_key == session.session_key)
    return MailAgentRegisterResponse(member=member_resp, session=session_resp)


@router.get("/agent/inbox", response_model=MailInboxResponse)
async def agent_inbox(
    member_id: int, unread_only: bool = False, mark_read: bool = False,
    limit: int = 50, db: AsyncSession = Depends(get_db),
):
    return await agent_mail_service.get_inbox(
        db, member_id, unread_only=unread_only, mark_read=mark_read, limit=limit, refresh_mcp_session=True,
    )
```

Register the router in `backend/app/api/v1/router.py`. Add the import next to `from .agents import router as agents_router`:

```python
from .agent_mail import router as agent_mail_router
```

Add the include next to `router.include_router(agents_router, tags=["Agents"])`:

```python
router.include_router(agent_mail_router, prefix="/agent-mail", tags=["Agent Mail"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_api.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/agent_mail.py backend/app/api/v1/router.py backend/tests/agent_mail/test_api.py
git commit -m "feat(agent-mail): add internal REST API core routes"
```

---

### Task 10: Internal REST API — Claude Code / Codex lifecycle hooks

**Files:**
- Modify: `backend/app/api/v1/agent_mail.py` (add hook routes)
- Test: `backend/tests/agent_mail/test_hooks_api.py`

**Interfaces:**
- Produces: `POST /agent-mail/hooks/session-start`, `POST /agent-mail/hooks/user-prompt-submit`, `POST /agent-mail/hooks/session-end`, `POST /agent-mail/hooks/post-tool-use`. Each accepts the raw JSON a Claude Code/Codex hook passes on stdin and returns `{}` or `{"hookSpecificOutput": {"hookEventName": ..., "additionalContext": ...}}` — the shape Claude Code's hook runner injects into session context.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/agent_mail/test_hooks_api.py
import pytest
from httpx import ASGITransport, AsyncClient

from app.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_session_start_hook_registers_and_returns_context(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/agent-mail/hooks/session-start", json={
            "session_id": "abc123", "cwd": str(tmp_path), "provider": "claude-code",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "Agent Mail" in body["hookSpecificOutput"]["additionalContext"]


@pytest.mark.asyncio
async def test_session_start_hook_missing_cwd_returns_empty():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/agent-mail/hooks/session-start", json={"session_id": "x"})
        assert r.status_code == 200
        assert r.json() == {}


@pytest.mark.asyncio
async def test_session_end_hook_marks_offline(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/v1/agent-mail/hooks/session-start", json={
            "session_id": "end-me", "cwd": str(tmp_path),
        })
        r = await client.post("/api/v1/agent-mail/hooks/session-end", json={"session_id": "end-me"})
        assert r.status_code == 200

        team = (await client.get("/api/v1/agent-mail/team")).json()["members"]
        session = team[0]["sessions"][0]
        assert session["mailbox_status"] == "offline"


@pytest.mark.asyncio
async def test_post_tool_use_hook_records_activity(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/v1/agent-mail/hooks/session-start", json={
            "session_id": "edit-me", "cwd": str(tmp_path),
        })
        r = await client.post("/api/v1/agent-mail/hooks/post-tool-use", json={
            "session_id": "edit-me", "cwd": str(tmp_path),
            "tool_input": {"file_path": "/repo/foo.py"},
        })
        assert r.status_code == 200
        team = (await client.get("/api/v1/agent-mail/team")).json()["members"]
        assert team[0]["sessions"][0]["activity"] == "edited foo.py"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_hooks_api.py -v`
Expected: FAIL with 404 (routes don't exist)

- [ ] **Step 3: Write the implementation**

Append to `backend/app/api/v1/agent_mail.py`, after `agent_inbox`:

```python
def _hook_provider(payload: dict) -> str:
    provider = str(payload.get("provider") or "claude-code")
    return provider if provider in {"claude-code", "codex-cli"} else "unknown"


def _hook_session_key(payload: dict) -> Optional[str]:
    session_id = payload.get("session_id")
    if not session_id:
        return None
    prefix = "cc" if _hook_provider(payload) == "claude-code" else "codex"
    return f"{prefix}:{session_id}"


async def _register_from_hook(db: AsyncSession, payload: dict):
    session_key = _hook_session_key(payload)
    cwd = payload.get("cwd")
    if not session_key or not cwd:
        return None, None
    return await agent_mail_service.register_session(
        db,
        MailAgentRegisterRequest(
            source="hook", provider=_hook_provider(payload), cwd=cwd,
            session_key=session_key, pid=payload.get("pid"),
        ),
    )


@router.post("/hooks/session-start")
async def hook_session_start(payload: dict[str, Any] = Body(...), db: AsyncSession = Depends(get_db)):
    try:
        member, session = await _register_from_hook(db, payload)
        if member is None:
            return {}
        context = await agent_mail_service.build_session_start_context(
            db, member.id, session.session_key if session is not None else None,
        )
        if not context:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}
    except Exception as exc:
        logger.warning("session-start hook failed: %s", exc)
        return {}


@router.post("/hooks/user-prompt-submit")
async def hook_user_prompt_submit(payload: dict[str, Any] = Body(...), db: AsyncSession = Depends(get_db)):
    try:
        session_key = _hook_session_key(payload)
        if session_key is None:
            return {}
        session = await agent_mail_service.heartbeat_session(db, session_key)
        if session is None:
            _, session = await _register_from_hook(db, payload)
            if session is None:
                return {}
        context = await agent_mail_service.build_prompt_submit_context(db, session.member_id)
        if context is None:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}
    except Exception as exc:
        logger.warning("user-prompt-submit hook failed: %s", exc)
        return {}


@router.post("/hooks/session-end")
async def hook_session_end(payload: dict[str, Any] = Body(...), db: AsyncSession = Depends(get_db)):
    try:
        session_key = _hook_session_key(payload)
        if session_key is not None:
            await agent_mail_service.mark_session_offline(db, session_key)
    except Exception as exc:
        logger.warning("session-end hook failed: %s", exc)
    return {}


@router.post("/hooks/post-tool-use")
async def hook_post_tool_use(payload: dict[str, Any] = Body(...), db: AsyncSession = Depends(get_db)):
    try:
        session_key = _hook_session_key(payload)
        if session_key is None:
            return {}
        activity = None
        file_path = (payload.get("tool_input") or {}).get("file_path")
        if file_path:
            activity = f"edited {os.path.basename(str(file_path))}"
        session = await agent_mail_service.heartbeat_session(db, session_key, activity=activity)
        if session is None:
            await _register_from_hook(db, payload)
            if activity:
                await agent_mail_service.heartbeat_session(db, session_key, activity=activity)
    except Exception as exc:
        logger.warning("post-tool-use hook failed: %s", exc)
    return {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_hooks_api.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/agent_mail.py backend/tests/agent_mail/test_hooks_api.py
git commit -m "feat(agent-mail): add Claude Code/Codex lifecycle hook endpoints"
```

---

### Task 11: MCP tools on the shared server

**Files:**
- Create: `backend/app/mcp_server/tools/agent_mail.py`
- Modify: `backend/app/mcp_server/tools/__init__.py` (register)
- Test: `backend/tests/agent_mail/test_mcp_tools.py`

**Interfaces:**
- Consumes: `agent_mail_service` (Tasks 4-8), `AsyncSessionLocal` from `app.database` (same pattern as `tools/scheduled.py`).
- Produces: `register_agent_mail_tools(mcp: FastMCP) -> None` registering 8 tools: `agent_mail_whoami`, `agent_mail_list_team`, `agent_mail_check_inbox`, `agent_mail_send_message`, `agent_mail_reply`, `agent_mail_ack_message`, `agent_mail_request_context`, `agent_mail_create_handoff`. Every tool takes `cwd: str` and `session_key: str` as its first two args (there is no per-call identity threading from the Bearer token yet, matching the spec's documented, accepted spoofability trade-off) — the caller (an agent) passes its own working directory and a stable session key it picked, e.g. its Claude Code `session_id`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/agent_mail/test_mcp_tools.py
import json

import pytest

from app.database import Base, engine
from app.mcp_server.server import mcp


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_whoami_registers_and_returns_member(tmp_path):
    result = await mcp.call_tool(
        "agent_mail_whoami", {"cwd": str(tmp_path), "session_key": "cc:test-1"},
    )
    body = json.loads(result[0].text if hasattr(result[0], "text") else result[1]["result"])
    assert body["member"]["repo_name"] == tmp_path.name
    assert body["unread_count"] == 0


@pytest.mark.asyncio
async def test_send_message_then_check_inbox(tmp_path):
    r1 = await mcp.call_tool("agent_mail_whoami", {"cwd": str(tmp_path / "a"), "session_key": "cc:a"})
    body1 = json.loads(r1[0].text if hasattr(r1[0], "text") else r1[1]["result"])
    r2 = await mcp.call_tool("agent_mail_whoami", {"cwd": str(tmp_path / "b"), "session_key": "cc:b"})
    body2 = json.loads(r2[0].text if hasattr(r2[0], "text") else r2[1]["result"])
    member_a, member_b = body1["member"]["id"], body2["member"]["id"]

    r3 = await mcp.call_tool("agent_mail_send_message", {
        "cwd": str(tmp_path / "a"), "session_key": "cc:a",
        "to_member_id": member_b, "body": "hi from a",
    })
    send_body = json.loads(r3[0].text if hasattr(r3[0], "text") else r3[1]["result"])
    assert send_body["ok"] is True

    r4 = await mcp.call_tool("agent_mail_check_inbox", {
        "cwd": str(tmp_path / "b"), "session_key": "cc:b", "unread_only": True,
    })
    inbox_body = json.loads(r4[0].text if hasattr(r4[0], "text") else r4[1]["result"])
    assert inbox_body["unread_count"] == 1
    assert inbox_body["messages"][0]["body_markdown"] == "hi from a"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_mcp_tools.py -v`
Expected: FAIL — `ToolError`/`ValueError: Unknown tool: agent_mail_whoami` (FastMCP raises when a tool name isn't registered; if step 1's `result[0].text` parsing shape differs from your installed `mcp` package version, run one call manually first with `python -c` to confirm the return shape, then adjust the test's unpacking — do not guess).

- [ ] **Step 3: Write the implementation**

```python
# backend/app/mcp_server/tools/agent_mail.py
"""MCP tools for Agent Mail — cross-session messaging.

Identity is an explicit (cwd, session_key) argument pair, same trade-off as
this fork's other local MCP tools (e.g. kanban's claim_card(claimed_by)):
the shared MCP server doesn't thread the caller's Bearer-token identity down
into individual tool calls yet, so this is spoofable but acceptable in the
local single-user trust model. See docs/cockpit/agent-mail-spec.md.
"""
import json

from mcp.server.fastmcp import FastMCP

from app.database import AsyncSessionLocal
from app.models.agent_mail_schemas import MailAgentRegisterRequest, MailMessageCreate
from app.services.agent_mail_service import agent_mail_service


def register_agent_mail_tools(mcp: FastMCP) -> None:
    """Register Agent Mail MCP tools."""

    async def _whoami(cwd: str, session_key: str) -> tuple[int, dict]:
        async with AsyncSessionLocal() as db:
            member, _ = await agent_mail_service.register_session(
                db, MailAgentRegisterRequest(source="mcp", cwd=cwd, session_key=session_key),
            )
            unread, pending = await agent_mail_service.counts_for_member(db, member.id)
            return member.id, {
                "member": {
                    "id": member.id, "display_name": member.display_name,
                    "repo_name": member.repo_name, "role": member.role,
                },
                "unread_count": unread, "pending_count": pending,
            }

    @mcp.tool()
    async def agent_mail_whoami(cwd: str, session_key: str) -> str:
        """Register/refresh your Agent Mail session and return your identity + inbox counts.

        Args:
            cwd: Your current working directory (repo root or subdirectory).
            session_key: A stable key for this session (e.g. your Claude Code session_id).
        """
        _, body = await _whoami(cwd, session_key)
        return json.dumps(body, indent=2)

    @mcp.tool()
    async def agent_mail_list_team(cwd: str, session_key: str) -> str:
        """List all Agent Mail team members visible on this machine."""
        await _whoami(cwd, session_key)
        async with AsyncSessionLocal() as db:
            await agent_mail_service.sync_observed_sessions(db)
            team = await agent_mail_service.list_team(db)
            return json.dumps([{
                "id": m.id, "display_name": m.display_name, "role": m.role,
                "repo_name": m.repo_name, "status": m.status,
            } for m in team], indent=2)

    @mcp.tool()
    async def agent_mail_check_inbox(cwd: str, session_key: str, unread_only: bool = True, limit: int = 20) -> str:
        """Check your Agent Mail inbox. Marks fetched messages as read.

        Args:
            unread_only: If true, only return unread messages.
            limit: Maximum number of messages to return.
        """
        member_id, _ = await _whoami(cwd, session_key)
        async with AsyncSessionLocal() as db:
            inbox = await agent_mail_service.get_inbox(
                db, member_id, unread_only=unread_only, mark_read=True, limit=limit,
            )
            return inbox.model_dump_json(indent=2)

    @mcp.tool()
    async def agent_mail_send_message(cwd: str, session_key: str, to_member_id: int, body: str, subject: str = "") -> str:
        """Send a direct message to another Agent Mail team member.

        Args:
            to_member_id: The recipient's member id (from agent_mail_list_team).
            body: Markdown message body.
            subject: Optional short subject line.
        """
        member_id, _ = await _whoami(cwd, session_key)
        async with AsyncSessionLocal() as db:
            try:
                msg = await agent_mail_service.send_message(
                    db, MailMessageCreate(
                        sender_member_id=member_id, recipient_member_id=to_member_id,
                        subject=subject or None, body_markdown=body,
                    ),
                )
            except ValueError as exc:
                return json.dumps({"ok": False, "error": str(exc)})
            return json.dumps({"ok": True, "message_id": msg.id})

    @mcp.tool()
    async def agent_mail_reply(cwd: str, session_key: str, thread_root_id: int, body: str) -> str:
        """Reply in a thread. Automatically sent as an 'answer' if the thread root
        is a pending context_request addressed to you, otherwise as a plain message.

        Args:
            thread_root_id: The id of the root message of the thread.
            body: Markdown reply body.
        """
        member_id, _ = await _whoami(cwd, session_key)
        async with AsyncSessionLocal() as db:
            root = await agent_mail_service.get_thread(db, thread_root_id)
            is_pending_request_to_me = (
                root.root.kind == "context_request"
                and root.root.request_status == "pending"
                and root.root.recipient_member_id == member_id
            )
            kind = "answer" if is_pending_request_to_me else "message"
            try:
                msg = await agent_mail_service.send_message(
                    db, MailMessageCreate(
                        sender_member_id=member_id, thread_root_id=thread_root_id,
                        kind=kind, body_markdown=body,
                    ),
                )
            except ValueError as exc:
                return json.dumps({"ok": False, "error": str(exc)})
            return json.dumps({"ok": True, "message_id": msg.id, "kind": kind})

    @mcp.tool()
    async def agent_mail_ack_message(cwd: str, session_key: str, message_id: int) -> str:
        """Acknowledge a message you've received (closes handoff/answer lifecycle)."""
        member_id, _ = await _whoami(cwd, session_key)
        async with AsyncSessionLocal() as db:
            await agent_mail_service.ack_message(db, message_id, member_id)
            return json.dumps({"ok": True})

    @mcp.tool()
    async def agent_mail_request_context(
        cwd: str, session_key: str, to_member_id: int, topic: str,
        why_needed: str = "", files_or_symbols: list[str] | None = None,
    ) -> str:
        """Ask another team member for specific context. They reply via agent_mail_reply.

        Args:
            to_member_id: The member id to ask.
            topic: Short topic (used as the message subject).
            why_needed: Why you need this context.
            files_or_symbols: Relevant files or symbols, if known.
        """
        member_id, _ = await _whoami(cwd, session_key)
        async with AsyncSessionLocal() as db:
            try:
                msg = await agent_mail_service.send_message(
                    db, MailMessageCreate(
                        sender_member_id=member_id, recipient_member_id=to_member_id,
                        kind="context_request", subject=topic[:120],
                        body_markdown=why_needed or topic,
                        payload={"why_needed": why_needed, "files_or_symbols": files_or_symbols or []},
                    ),
                )
            except ValueError as exc:
                return json.dumps({"ok": False, "error": str(exc)})
            return json.dumps({"ok": True, "message_id": msg.id})

    @mcp.tool()
    async def agent_mail_create_handoff(
        cwd: str, session_key: str, to_member_id: int, summary: str,
        files: list[str] | None = None, next_steps: list[str] | None = None,
    ) -> str:
        """Hand off work to another team member.

        Args:
            to_member_id: The member id to hand off to.
            summary: What you did / what's next.
            files: Files touched, if relevant.
            next_steps: Concrete next steps for the recipient.
        """
        member_id, _ = await _whoami(cwd, session_key)
        body_lines = [f"## Handoff\n{summary}"]
        if files:
            body_lines.append("\n### Files\n" + "\n".join(f"- {f}" for f in files))
        if next_steps:
            body_lines.append("\n### Next steps\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(next_steps)))
        async with AsyncSessionLocal() as db:
            try:
                msg = await agent_mail_service.send_message(
                    db, MailMessageCreate(
                        sender_member_id=member_id, recipient_member_id=to_member_id,
                        kind="handoff", subject=f"Handoff: {summary[:100]}",
                        body_markdown="\n".join(body_lines),
                        payload={"files": files or [], "next_steps": next_steps or []},
                    ),
                )
            except ValueError as exc:
                return json.dumps({"ok": False, "error": str(exc)})
            return json.dumps({"ok": True, "message_id": msg.id})
```

Register in `backend/app/mcp_server/tools/__init__.py`:

```python
"""MCP tools package — registers all tools on the server."""
from mcp.server.fastmcp import FastMCP

from .agent_mail import register_agent_mail_tools
from .config import register_config_tools
from .mcp import register_mcp_tools
from .projects import register_project_tools
from .scheduled import register_scheduled_tools
from .sessions import register_session_tools


def register_all_tools(mcp: FastMCP) -> None:
    """Register all MCP tools on the given server."""
    register_session_tools(mcp)
    register_scheduled_tools(mcp)
    register_mcp_tools(mcp)
    register_config_tools(mcp)
    register_project_tools(mcp)
    register_agent_mail_tools(mcp)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_mcp_tools.py -v`
Expected: PASS (2 tests). If the assertion about `result[0].text` shape is wrong for the installed `mcp` SDK version, adjust the unpacking to match — check how `handle_mcp_post`'s `tools/call` branch in `backend/app/api/v1/mcp_server.py` unpacks `mcp.call_tool(...)` results (it does `for item in result: if hasattr(item, "text"): ...`) and mirror that exactly in the test.

- [ ] **Step 5: Commit**

```bash
git add backend/app/mcp_server/tools/agent_mail.py backend/app/mcp_server/tools/__init__.py backend/tests/agent_mail/test_mcp_tools.py
git commit -m "feat(agent-mail): register MCP tools on the shared Cockpit MCP server"
```

**Note:** per this fork's known pattern (memory: new MCP tools need a backend restart before a *live* running server reflects them — `mcp.call_tool` in a fresh test process always sees the current code, so this only matters when manually verifying against `cockpit.sh` later in Task 22).

---

### Task 12: Claude Code hook installer

**Files:**
- Create: `backend/app/services/agent_mail/__init__.py` (empty)
- Create: `backend/app/services/agent_mail/hook_script.py`
- Create: `backend/app/services/agent_mail/hook_installer.py`
- Test: `backend/tests/test_agent_mail_hook_installer.py`

**Interfaces:**
- Produces: `settings_hooks_block(port: int = 8000) -> dict` (hook_script.py), `get_hooks_status(port: int = 8000) -> dict[str, bool]`, `install_missing_hooks(port: int = 8000) -> dict[str, bool]` (hook_installer.py). Mirrors `app/services/scheduling/hook_installer.py`'s additive-merge shape exactly (same idempotency guarantees), but for the 4 Agent Mail events and the `/api/v1/agent-mail/hooks/{slug}` URL, and with a matcher on `PostToolUse`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_agent_mail_hook_installer.py
import json

from app.services.agent_mail import hook_installer

ALL_EVENTS = {"SessionStart", "UserPromptSubmit", "SessionEnd", "PostToolUse"}


def _patch_settings_file(monkeypatch, path):
    monkeypatch.setattr(hook_installer, "get_claude_user_settings_file", lambda: path)


def test_status_all_missing_when_no_settings_file(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    _patch_settings_file(monkeypatch, settings_file)

    assert hook_installer.get_hooks_status() == {event: False for event in ALL_EVENTS}


def test_install_writes_all_four_hooks_with_post_tool_use_matcher(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    _patch_settings_file(monkeypatch, settings_file)

    status = hook_installer.install_missing_hooks()

    assert status == {event: True for event in ALL_EVENTS}
    written = json.loads(settings_file.read_text())
    assert set(written["hooks"]) == ALL_EVENTS
    for event in ALL_EVENTS:
        commands = [h["command"] for g in written["hooks"][event] for h in g["hooks"]]
        assert any("agent-mail/hooks/" in c for c in commands)
    post_tool_use_group = written["hooks"]["PostToolUse"][0]
    assert post_tool_use_group["matcher"] == "Edit|Write|MultiEdit|NotebookEdit"
    assert "matcher" not in written["hooks"]["SessionStart"][0]


def test_install_is_idempotent_and_preserves_unrelated_hooks(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "/some/other.sh"}]}]},
    }))
    _patch_settings_file(monkeypatch, settings_file)

    hook_installer.install_missing_hooks()
    hook_installer.install_missing_hooks()

    written = json.loads(settings_file.read_text())
    session_start_commands = [h["command"] for g in written["hooks"]["SessionStart"] for h in g["hooks"]]
    assert "/some/other.sh" in session_start_commands
    agent_mail_commands = [c for c in session_start_commands if "agent-mail/hooks/" in c]
    assert len(agent_mail_commands) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_mail_hook_installer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.agent_mail'`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/services/agent_mail/__init__.py
```

```python
# backend/app/services/agent_mail/hook_script.py
"""Render the CC hook commands that POST Agent Mail lifecycle events to the
backend and print the response (Claude Code injects a command hook's stdout
as hookSpecificOutput.additionalContext when it parses as the expected JSON
shape). Mirrors app.services.scheduling.hook_script's structure."""

POST_TOOL_USE_MATCHER = "Edit|Write|MultiEdit|NotebookEdit"

MAIL_HOOK_EVENTS = {
    "SessionStart": "session-start",
    "UserPromptSubmit": "user-prompt-submit",
    "SessionEnd": "session-end",
    "PostToolUse": "post-tool-use",
}


def render_hook_command(slug: str, port: int = 8000) -> str:
    url = f"http://127.0.0.1:{port}/api/v1/agent-mail/hooks/{slug}"
    return (
        f"curl -s -f --connect-timeout 0.25 -m 1 -X POST {url} "
        "-H 'Content-Type: application/json' --data-binary @- 2>/dev/null || true"
    )


def settings_hooks_block(port: int = 8000) -> dict:
    """Return a dict to merge into ~/.claude/settings.json 'hooks'."""
    def entry(event: str, slug: str):
        group: dict = {"hooks": [{"type": "command", "command": render_hook_command(slug, port)}]}
        if event == "PostToolUse":
            group["matcher"] = POST_TOOL_USE_MATCHER
        return [group]
    return {event: entry(event, slug) for event, slug in MAIL_HOOK_EVENTS.items()}
```

```python
# backend/app/services/agent_mail/hook_installer.py
"""Install and verify the Agent Mail lifecycle hooks in ~/.claude/settings.json.

Additive, idempotent merge — mirrors app.services.scheduling.hook_installer
exactly, but for Agent Mail's 4 events and URL marker.
"""
import json
import logging

from app.services.agent_mail.hook_script import settings_hooks_block
from app.utils.path_utils import get_claude_user_settings_file

logger = logging.getLogger(__name__)

_HOOK_EVENT_MARKER = "agent-mail/hooks/"


def _event_has_hook_command(event_groups: list | None) -> bool:
    for group in event_groups or []:
        if not isinstance(group, dict):
            continue
        entries = group["hooks"] if "hooks" in group and isinstance(group["hooks"], list) else [group]
        for entry in entries:
            if isinstance(entry, dict) and _HOOK_EVENT_MARKER in str(entry.get("command", "")):
                return True
    return False


def _read_hooks_section() -> dict:
    settings_file = get_claude_user_settings_file()
    if not settings_file.exists():
        return {}
    try:
        return json.loads(settings_file.read_text()).get("hooks", {})
    except (OSError, json.JSONDecodeError):
        logger.warning("could not parse %s while checking agent mail hooks", settings_file)
        return {}


def get_hooks_status(port: int = 8000) -> dict[str, bool]:
    """Return which of the four Agent Mail hook events are already installed."""
    hooks_section = _read_hooks_section()
    return {event: _event_has_hook_command(hooks_section.get(event)) for event in settings_hooks_block(port)}


def install_missing_hooks(port: int = 8000) -> dict[str, bool]:
    """Additively merge any missing Agent Mail hooks into ~/.claude/settings.json."""
    settings_file = get_claude_user_settings_file()
    settings_file.parent.mkdir(parents=True, exist_ok=True)

    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text())
        except (OSError, json.JSONDecodeError):
            settings = {}
    else:
        settings = {}

    hooks_section = settings.setdefault("hooks", {})
    block = settings_hooks_block(port)
    changed = False

    for event, groups in block.items():
        existing = hooks_section.setdefault(event, [])
        if not _event_has_hook_command(existing):
            existing.extend(groups)
            changed = True

    if changed:
        settings_file.write_text(json.dumps(settings, indent=2))

    return {event: True for event in block}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_mail_hook_installer.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_mail/ backend/tests/test_agent_mail_hook_installer.py
git commit -m "feat(agent-mail): add Claude Code hook installer (additive settings.json merge)"
```

---

### Task 13: Codex CLI hooks.json editor + hook shim script

**Files:**
- Create: `backend/app/services/agent_mail/codex_hooks.py`
- Create: `backend/app/services/agent_mail/codex_hook_shim.py`
- Test: `backend/tests/test_agent_mail_codex_hooks.py`

**Interfaces:**
- Consumes: `get_codex_home()` from `app.services.providers.codex_cli` (already exists in this fork).
- Produces: `codex_hooks_path() -> Path`, `installed_codex_hooks() -> list[str]`, `install_codex_hooks() -> None`, `uninstall_codex_hooks() -> bool` (returns whether anything changed). `codex_hook_shim.py` is a standalone script (not imported by the backend — invoked as a subprocess via `sys.executable <path> --cockpit-url ... --provider codex-cli --event <slug>`) that reads the hook JSON payload from stdin and POSTs it to `{cockpit_url}/api/v1/agent-mail/hooks/{event}`, printing the response body verbatim.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_agent_mail_codex_hooks.py
import json

from app.services.agent_mail import codex_hooks


def _patch_codex_home(monkeypatch, path):
    monkeypatch.setattr(codex_hooks, "get_codex_home", lambda: path)


def test_no_hooks_file_reports_nothing_installed(tmp_path, monkeypatch):
    _patch_codex_home(monkeypatch, tmp_path)
    assert codex_hooks.installed_codex_hooks() == []


def test_install_writes_session_start_and_prompt_submit(tmp_path, monkeypatch):
    _patch_codex_home(monkeypatch, tmp_path)

    codex_hooks.install_codex_hooks()

    doc = json.loads((tmp_path / "hooks.json").read_text())
    assert set(doc["hooks"].keys()) == {"SessionStart", "UserPromptSubmit"}
    assert doc["hooks"]["SessionStart"][0]["matcher"] == "startup|resume|clear|compact"
    command = doc["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "codex_hook_shim.py" in command
    assert "--event" in command and "session-start" in command

    assert codex_hooks.installed_codex_hooks() == ["SessionStart", "UserPromptSubmit"]


def test_install_is_idempotent(tmp_path, monkeypatch):
    _patch_codex_home(monkeypatch, tmp_path)

    codex_hooks.install_codex_hooks()
    codex_hooks.install_codex_hooks()

    doc = json.loads((tmp_path / "hooks.json").read_text())
    assert len(doc["hooks"]["SessionStart"]) == 1


def test_uninstall_removes_managed_hooks_but_keeps_others(tmp_path, monkeypatch):
    _patch_codex_home(monkeypatch, tmp_path)
    (tmp_path / "hooks.json").write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "/other/script.sh"}]}]},
    }))

    codex_hooks.install_codex_hooks()
    changed = codex_hooks.uninstall_codex_hooks()

    assert changed is True
    doc = json.loads((tmp_path / "hooks.json").read_text())
    remaining = [h["command"] for g in doc["hooks"]["SessionStart"] for h in g["hooks"]]
    assert remaining == ["/other/script.sh"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_mail_codex_hooks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.agent_mail.codex_hooks'`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/services/agent_mail/codex_hook_shim.py
"""Standalone script invoked by Codex CLI's hooks.json (needs a real argv,
not a shell one-liner, unlike Claude Code's command hooks). Reads the hook
JSON payload from stdin, POSTs it to Cockpit's Agent Mail hook endpoint, and
prints the JSON response verbatim so Codex can consume it as hook output.
Never raises — a Cockpit outage must not block the CLI.

Standalone on purpose: no `app.*` imports, so it runs correctly even when
invoked outside this repo's Python environment.
"""
import argparse
import json
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cockpit-url", required=True)
    parser.add_argument("--provider", default="codex-cli")
    parser.add_argument("--event", required=True)
    args = parser.parse_args()

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    payload.setdefault("provider", args.provider)

    url = f"{args.cockpit_url}/api/v1/agent-mail/hooks/{args.event}"
    try:
        response = httpx.post(url, json=payload, timeout=httpx.Timeout(connect=0.25, read=1.0, write=1.0, pool=0.25))
        if response.status_code < 400:
            sys.stdout.write(response.text)
    except httpx.HTTPError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

```python
# backend/app/services/agent_mail/codex_hooks.py
"""Edit ~/.codex/hooks.json to install/uninstall Agent Mail lifecycle hooks.

No MCP registration here — Codex connects to Cockpit's shared MCP server
the same generic way any other Codex MCP integration in this fork does.
This module only wires the SessionStart/UserPromptSubmit lifecycle hooks,
which need a real shim executable (see codex_hook_shim.py) because Codex's
hooks.json requires an argv, not a curl one-liner.
"""
import json
import shlex
import sys
from pathlib import Path

from app.config import settings
from app.services.providers.codex_cli import get_codex_home

CODEX_MAIL_HOOK_EVENTS = {"SessionStart": "session-start", "UserPromptSubmit": "user-prompt-submit"}
_HOOK_SHIM_MARKER = "codex_hook_shim.py"


def cockpit_base_url() -> str:
    return f"http://127.0.0.1:{settings.port}"


def hook_shim_path() -> str:
    return str(Path(__file__).resolve().with_name("codex_hook_shim.py"))


def codex_hooks_path() -> Path:
    return get_codex_home() / "hooks.json"


def _expected_matcher(event: str) -> str | None:
    return "startup|resume|clear|compact" if event == "SessionStart" else None


def _hook_command(slug: str) -> str:
    return " ".join([
        shlex.quote(sys.executable), shlex.quote(hook_shim_path()),
        "--cockpit-url", shlex.quote(cockpit_base_url()),
        "--provider", "codex-cli", "--event", shlex.quote(slug),
    ])


def _hook_entry(event: str, slug: str) -> dict:
    entry: dict = {
        "hooks": [{"type": "command", "command": _hook_command(slug), "statusMessage": "Checking Agent Mail", "timeout": 2}],
    }
    matcher = _expected_matcher(event)
    if matcher is not None:
        entry["matcher"] = matcher
    return entry


def _load_doc() -> dict:
    path = codex_hooks_path()
    if not path.exists():
        return {"hooks": {}}
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.setdefault("hooks", {})
    return doc


def _write_doc(doc: dict) -> None:
    path = codex_hooks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _is_managed(hook: object) -> bool:
    return isinstance(hook, dict) and isinstance(hook.get("command"), str) and _HOOK_SHIM_MARKER in hook["command"]


def _prune_managed_hooks(doc: dict) -> bool:
    changed = False
    hooks = doc.setdefault("hooks", {})
    for event in list(hooks.keys()):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept_groups.append(group)
                continue
            kept_hooks = [h for h in group["hooks"] if not _is_managed(h)]
            if len(kept_hooks) != len(group["hooks"]):
                changed = True
            if kept_hooks:
                kept_groups.append({**group, "hooks": kept_hooks})
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)
    return changed


def _group_is_current(group: object, event: str, slug: str) -> bool:
    if not isinstance(group, dict) or group.get("matcher") != _expected_matcher(event):
        return False
    hooks = group.get("hooks")
    if not isinstance(hooks, list):
        return False
    return any(
        isinstance(h, dict) and h.get("type") == "command" and isinstance(h.get("command"), str)
        and _HOOK_SHIM_MARKER in h["command"] and f"--event {shlex.quote(slug)}" in h["command"]
        for h in hooks
    )


def installed_codex_hooks() -> list[str]:
    doc = _load_doc()
    hooks = doc.get("hooks", {})
    return sorted(
        event for event, slug in CODEX_MAIL_HOOK_EVENTS.items()
        if isinstance(hooks.get(event), list) and any(_group_is_current(g, event, slug) for g in hooks[event])
    )


def install_codex_hooks() -> None:
    doc = _load_doc()
    _prune_managed_hooks(doc)
    hooks = doc.setdefault("hooks", {})
    for event, slug in CODEX_MAIL_HOOK_EVENTS.items():
        hooks.setdefault(event, []).append(_hook_entry(event, slug))
    _write_doc(doc)


def uninstall_codex_hooks() -> bool:
    doc = _load_doc()
    changed = _prune_managed_hooks(doc)
    if changed:
        _write_doc(doc)
    return changed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_mail_codex_hooks.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_mail/codex_hooks.py backend/app/services/agent_mail/codex_hook_shim.py backend/tests/test_agent_mail_codex_hooks.py
git commit -m "feat(agent-mail): add Codex CLI hooks.json installer and hook shim script"
```

**Note:** `httpx` is confirmed present (`backend/requirements.txt:httpx>=0.28.1`).

---

### Task 14: Install status/apply/uninstall REST endpoints

**Files:**
- Create: `backend/app/services/agent_mail/install_status.py`
- Modify: `backend/app/api/v1/agent_mail.py` (add install routes)
- Test: `backend/tests/test_agent_mail_install_api.py`

**Interfaces:**
- Consumes: `hook_installer` (Task 12), `codex_hooks` (Task 13), `AgentMailInstallStatus`/`AgentMailSnippets` (Task 3).
- Produces: `get_install_status() -> AgentMailInstallStatus`, `get_snippets() -> AgentMailSnippets`; REST routes `GET /agent-mail/install/status`, `POST /agent-mail/install/claude-code/apply`, `POST /agent-mail/install/claude-code/uninstall`, `POST /agent-mail/install/codex/apply`, `POST /agent-mail/install/codex/uninstall`, `GET /agent-mail/install/snippets`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_agent_mail_install_api.py
import pytest
from httpx import ASGITransport, AsyncClient

from app.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_install_status_reports_missing_when_nothing_installed(tmp_path, monkeypatch):
    from app.services.agent_mail import hook_installer, codex_hooks
    monkeypatch.setattr(hook_installer, "get_claude_user_settings_file", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(codex_hooks, "get_codex_home", lambda: tmp_path / "codex")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/agent-mail/install/status")
        assert r.status_code == 200
        body = r.json()
        assert set(body["claude_code_hooks_missing"]) == {"SessionStart", "UserPromptSubmit", "SessionEnd", "PostToolUse"}


@pytest.mark.asyncio
async def test_apply_claude_code_requires_confirmation(tmp_path, monkeypatch):
    from app.services.agent_mail import hook_installer
    monkeypatch.setattr(hook_installer, "get_claude_user_settings_file", lambda: tmp_path / "settings.json")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/agent-mail/install/claude-code/apply", json={})
        assert r.status_code == 400

        r2 = await client.post("/api/v1/agent-mail/install/claude-code/apply", json={"confirmed": True})
        assert r2.status_code == 200
        assert r2.json()["claude_code_hooks_missing"] == []


@pytest.mark.asyncio
async def test_snippets_endpoint_returns_codex_hook_snippet():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/agent-mail/install/snippets")
        assert r.status_code == 200
        assert "codex_hook_shim.py" in r.json()["codex_hooks_snippet"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_mail_install_api.py -v`
Expected: FAIL with 404 (routes don't exist)

- [ ] **Step 3: Write the implementation**

```python
# backend/app/services/agent_mail/install_status.py
"""Aggregate Agent Mail install status across Claude Code hooks and Codex hooks.
No MCP-installed flags here — MCP wiring is a generic Cockpit concern (see
the MCP Server page), not managed by this installer."""
import shutil
import sys

from app.config import settings
from app.models.agent_mail_schemas import AgentMailInstallStatus, AgentMailSnippets
from app.services.agent_mail import codex_hooks, hook_installer
from app.services.agent_mail.hook_script import MAIL_HOOK_EVENTS
from app.utils.path_utils import get_claude_user_settings_file


def cockpit_base_url() -> str:
    return f"http://127.0.0.1:{settings.port}"


def codex_cli_available() -> bool:
    try:
        from app.services.cli_executor import ProviderCLIExecutor
        return ProviderCLIExecutor("codex-cli").binary_path is not None
    except Exception:
        return False


async def get_install_status() -> AgentMailInstallStatus:
    installed = [event for event, ok in hook_installer.get_hooks_status().items() if ok]
    missing = [event for event in MAIL_HOOK_EVENTS if event not in installed]
    codex_installed = codex_hooks.installed_codex_hooks()
    codex_missing = [event for event in codex_hooks.CODEX_MAIL_HOOK_EVENTS if event not in codex_installed]
    return AgentMailInstallStatus(
        claude_code_hooks=sorted(installed),
        claude_code_hooks_missing=missing,
        codex_cli_available=codex_cli_available(),
        codex_hooks=codex_installed,
        codex_hooks_missing=codex_missing,
        curl_available=shutil.which("curl") is not None,
        codex_hook_shim_path=codex_hooks.hook_shim_path(),
        python_path=sys.executable,
        cockpit_url=cockpit_base_url(),
        claude_settings_path=str(get_claude_user_settings_file()),
        codex_hooks_path=str(codex_hooks.codex_hooks_path()),
    )


async def apply_claude_code_install() -> AgentMailInstallStatus:
    hook_installer.install_missing_hooks()
    return await get_install_status()


async def uninstall_claude_code() -> AgentMailInstallStatus:
    import json
    settings_file = get_claude_user_settings_file()
    if settings_file.exists():
        doc = json.loads(settings_file.read_text())
        hooks = doc.get("hooks", {})
        for event in list(hooks.keys()):
            hooks[event] = [
                g for g in hooks[event]
                if not any("agent-mail/hooks/" in h.get("command", "") for h in g.get("hooks", []))
            ]
            if not hooks[event]:
                hooks.pop(event)
        settings_file.write_text(json.dumps(doc, indent=2))
    return await get_install_status()


async def apply_codex_install() -> AgentMailInstallStatus:
    if not codex_cli_available():
        raise ValueError("Codex CLI is not available on this machine")
    codex_hooks.install_codex_hooks()
    return await get_install_status()


async def uninstall_codex() -> AgentMailInstallStatus:
    codex_hooks.uninstall_codex_hooks()
    return await get_install_status()


def get_snippets() -> AgentMailSnippets:
    hooks_snippet = (
        f'{{\n'
        f'  "hooks": {{\n'
        f'    "SessionStart": [{{"matcher": "startup|resume|clear|compact", "hooks": '
        f'[{{"type": "command", "command": "{sys.executable} {codex_hooks.hook_shim_path()} '
        f'--cockpit-url {cockpit_base_url()} --provider codex-cli --event session-start"}}]}}]\n'
        f'  }}\n'
        f'}}\n'
    )
    agents_md = (
        "## Claude Cockpit Agent Mail\n"
        "You are part of a local agent team coordinated through Claude Cockpit.\n"
        "- Call `agent_mail_whoami` once when you start working to register and learn your role.\n"
        "- Call `agent_mail_check_inbox` before starting major tasks and after finishing one.\n"
        "- Use `agent_mail_request_context` to ask another repo's agent a question, and\n"
        "  `agent_mail_create_handoff` to hand work over.\n"
    )
    return AgentMailSnippets(codex_hooks_snippet=hooks_snippet, agents_md_snippet=agents_md)
```

Append to `backend/app/api/v1/agent_mail.py`, after the hook routes (end of Task 10's additions), with new imports added at the top (`AgentMailInstallStatus`, `AgentMailSnippets` from `app.models.agent_mail_schemas`, and `from app.services.agent_mail import install_status`):

```python
def _require_confirmed(body: dict[str, Any] | None) -> None:
    if not body or not body.get("confirmed"):
        raise HTTPException(status_code=400, detail='Pass {"confirmed": true} to mutate config')


@router.get("/install/status", response_model=AgentMailInstallStatus)
async def install_status_route():
    return await install_status.get_install_status()


@router.post("/install/claude-code/apply", response_model=AgentMailInstallStatus)
async def install_claude_code(body: dict[str, Any] | None = Body(default=None)):
    _require_confirmed(body)
    return await install_status.apply_claude_code_install()


@router.post("/install/claude-code/uninstall", response_model=AgentMailInstallStatus)
async def uninstall_claude_code_route(body: dict[str, Any] | None = Body(default=None)):
    _require_confirmed(body)
    return await install_status.uninstall_claude_code()


@router.post("/install/codex/apply", response_model=AgentMailInstallStatus)
async def install_codex(body: dict[str, Any] | None = Body(default=None)):
    _require_confirmed(body)
    try:
        return await install_status.apply_codex_install()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/install/codex/uninstall", response_model=AgentMailInstallStatus)
async def uninstall_codex_route(body: dict[str, Any] | None = Body(default=None)):
    _require_confirmed(body)
    return await install_status.uninstall_codex()


@router.get("/install/snippets", response_model=AgentMailSnippets)
async def install_snippets():
    return install_status.get_snippets()
```

Note: import the module as `from app.services.agent_mail import install_status` (not `from app.services.agent_mail.install_status import ...`) to avoid a name clash with the route function `install_status_route`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_mail_install_api.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_mail/install_status.py backend/app/api/v1/agent_mail.py backend/tests/test_agent_mail_install_api.py
git commit -m "feat(agent-mail): add install status/apply/uninstall/snippets REST endpoints"
```

---

### Task 15: External orchestration service (token auth + rate limiting)

**Files:**
- Create: `backend/app/services/external_agent_mail_service.py`
- Test: `backend/tests/agent_mail/test_external_service.py`

**Interfaces:**
- Consumes: `MailExternalActor` (Task 2), `agent_mail_service` (Tasks 4-8), external schemas (Task 3).
- Produces: `external_agent_mail_service = ExternalAgentMailService()`, exceptions `ExternalAgentMailAuthError`, `ExternalAgentMailRateLimitError`. This is a near-verbatim port of upstream — no team-slot coupling exists in this file at all (confirmed by reading the full upstream source), so copy it directly with only the import paths changed (`app.models.database` → `app.models.agent_mail`, `app.models.schemas` → `app.models.agent_mail_schemas`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/agent_mail/test_external_service.py
import pytest

from app.database import AsyncSessionLocal, Base, engine
from app.models.agent_mail_schemas import (
    ExternalAgentMailContextRequest,
    MailAgentRegisterRequest,
    MailExternalActorCreate,
)
from app.services.agent_mail_service import agent_mail_service
from app.services.external_agent_mail_service import (
    ExternalAgentMailAuthError,
    ExternalAgentMailRateLimitError,
    external_agent_mail_service,
)


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_create_actor_then_authenticate(tmp_path):
    async with AsyncSessionLocal() as s:
        created = await external_agent_mail_service.create_actor(
            s, MailExternalActorCreate(actor_key="openclaw", display_name="OpenClaw"),
        )
        actor = await external_agent_mail_service.authenticate_actor(s, created.token)
        assert actor.actor_key == "openclaw"

        with pytest.raises(ExternalAgentMailAuthError):
            await external_agent_mail_service.authenticate_actor(s, "wrong-token")


@pytest.mark.asyncio
async def test_send_context_request_reports_delivery_state(tmp_path):
    async with AsyncSessionLocal() as s:
        member, _ = await agent_mail_service.register_session(
            s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:1"),
        )
        created = await external_agent_mail_service.create_actor(
            s, MailExternalActorCreate(actor_key="tool", display_name="Tool"),
        )
        actor = await external_agent_mail_service.authenticate_actor(s, created.token)

        response = await external_agent_mail_service.send_context_request(
            s, actor, ExternalAgentMailContextRequest(
                recipient_member_id=member.id, body_markdown="need context", why_needed="testing",
            ),
        )
        assert response.message.sender_type == "external_actor"
        assert response.delivery_state in {"stored_offline", "delivered_waiting", "stored"}


@pytest.mark.asyncio
async def test_rate_limit_after_30_messages(tmp_path):
    async with AsyncSessionLocal() as s:
        member, _ = await agent_mail_service.register_session(
            s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:1"),
        )
        created = await external_agent_mail_service.create_actor(
            s, MailExternalActorCreate(actor_key="spammer", display_name="Spammer"),
        )
        actor = await external_agent_mail_service.authenticate_actor(s, created.token)
        for _ in range(30):
            external_agent_mail_service.check_send_rate_limit(actor.id)
        with pytest.raises(ExternalAgentMailRateLimitError):
            external_agent_mail_service.check_send_rate_limit(actor.id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_external_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.external_agent_mail_service'`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/services/external_agent_mail_service.py
"""External local orchestration surface for Agent Mail — token-authenticated
facade for same-machine tools (e.g. OpenClaw) that don't run through the
Cockpit MCP server. Ported near-verbatim from upstream (no team-slot
coupling existed in this file)."""
import asyncio
import hashlib
import hmac
import re
import secrets
from collections import deque
from datetime import datetime, timedelta
from typing import Deque

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_mail import MailExternalActor, MailMessage, MailTeamMember
from app.models.agent_mail_schemas import (
    ExternalAgentMailContextRequest,
    ExternalAgentMailDeliveryRecipient,
    ExternalAgentMailHandoffRequest,
    ExternalAgentMailMessageRequest,
    ExternalAgentMailRequestStatus,
    ExternalAgentMailSendResponse,
    MailExternalActorCreate,
    MailExternalActorCreateResponse,
    MailExternalActorResponse,
    MailMessageCreate,
    MailThreadResponse,
    TeamListResponse,
)
from app.services.agent_mail_service import agent_mail_service

ACTOR_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{2,80}$")
EXTERNAL_RATE_LIMIT_MAX_MESSAGES = 30
EXTERNAL_RATE_LIMIT_WINDOW_SECONDS = 60
EXTERNAL_WAIT_MAX_SECONDS = 30
EXTERNAL_WAIT_POLL_SECONDS = 0.5
_REQUEST_RECIPIENT = object()


class ExternalAgentMailAuthError(ValueError):
    """Raised when a bearer token cannot be mapped to an external actor."""


class ExternalAgentMailRateLimitError(ValueError):
    """Raised when an external actor exceeds the local message rate limit."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("External Agent Mail rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


class ExternalAgentMailService:
    """Token-bound external actor helpers for Agent Mail orchestration."""

    def __init__(self) -> None:
        self._send_windows: dict[int, Deque[datetime]] = {}

    def _hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def actor_response(self, actor: MailExternalActor) -> MailExternalActorResponse:
        return MailExternalActorResponse(
            id=actor.id, actor_key=actor.actor_key, display_name=actor.display_name,
            kind=actor.kind, description=actor.description,
            created_at=actor.created_at, last_used_at=actor.last_used_at,
        )

    async def create_actor(self, db: AsyncSession, request: MailExternalActorCreate) -> MailExternalActorCreateResponse:
        actor_key = request.actor_key.strip()
        display_name = request.display_name.strip()
        kind = request.kind.strip() or "external_tool"
        description = request.description.strip() if request.description else None
        if not ACTOR_KEY_PATTERN.match(actor_key):
            raise ValueError("actor_key must be 2-80 chars using letters, numbers, _, ., :, or -")
        if not display_name:
            raise ValueError("display_name is required")
        if len(kind) > 80:
            raise ValueError("kind must be 80 chars or less")

        token = secrets.token_urlsafe(32)
        result = await db.execute(select(MailExternalActor).where(MailExternalActor.actor_key == actor_key))
        actor = result.scalar_one_or_none()
        if actor is None:
            actor = MailExternalActor(
                actor_key=actor_key, display_name=display_name, kind=kind,
                description=description, token_hash=self._hash_token(token),
            )
            db.add(actor)
        else:
            actor.display_name = display_name
            actor.kind = kind
            actor.description = description
            actor.token_hash = self._hash_token(token)
        await db.commit()
        await db.refresh(actor)
        return MailExternalActorCreateResponse(actor=self.actor_response(actor), token=token)

    async def authenticate_actor(self, db: AsyncSession, token: str | None) -> MailExternalActor:
        if not token:
            raise ExternalAgentMailAuthError("Missing bearer token")
        hashed = self._hash_token(token)
        result = await db.execute(select(MailExternalActor))
        for actor in result.scalars().all():
            if hmac.compare_digest(actor.token_hash, hashed):
                actor.last_used_at = datetime.utcnow()
                await db.commit()
                await db.refresh(actor)
                return actor
        raise ExternalAgentMailAuthError("Invalid bearer token")

    def check_send_rate_limit(self, actor_id: int) -> None:
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=EXTERNAL_RATE_LIMIT_WINDOW_SECONDS)
        window = self._send_windows.setdefault(actor_id, deque())
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= EXTERNAL_RATE_LIMIT_MAX_MESSAGES:
            retry_after = max(1, int((window[0] + timedelta(seconds=EXTERNAL_RATE_LIMIT_WINDOW_SECONDS) - now).total_seconds()))
            raise ExternalAgentMailRateLimitError(retry_after)
        window.append(now)

    async def list_members(self, db: AsyncSession) -> TeamListResponse:
        await agent_mail_service.sync_observed_sessions(db)
        return TeamListResponse(members=await agent_mail_service.list_team(db))

    async def send_message(
        self, db: AsyncSession, actor: MailExternalActor, request: ExternalAgentMailMessageRequest, *,
        kind: str = "message", recipient_member_id: int | None | object = _REQUEST_RECIPIENT,
        thread_root_id: int | None = None, subject: str | None = None,
        body_markdown: str | None = None, payload: dict | None = None,
    ) -> ExternalAgentMailSendResponse:
        self.check_send_rate_limit(actor.id)
        resolved_recipient_id = (
            request.recipient_member_id if recipient_member_id is _REQUEST_RECIPIENT else recipient_member_id
        )
        if resolved_recipient_id is not None:
            await self._require_member(db, int(resolved_recipient_id))
        message_request = MailMessageCreate(
            kind=kind, recipient_member_id=resolved_recipient_id, thread_root_id=thread_root_id,
            subject=subject if subject is not None else request.subject,
            body_markdown=body_markdown if body_markdown is not None else request.body_markdown,
            payload=payload if payload is not None else request.payload,
        )
        message = await agent_mail_service.send_message(db, message_request, auto_nudge=False, sender_actor_id=actor.id)
        recipient_ids = await agent_mail_service.recipient_ids_for_message(db, message.id)
        wake_results = await agent_mail_service.wake_members_with_results(db, recipient_ids)
        recipients = await self._delivery_recipients(db, recipient_ids, wake_results)
        return ExternalAgentMailSendResponse(
            actor=self.actor_response(actor), message=message,
            delivery_state=self._delivery_state(recipients), recipients=recipients,
        )

    async def send_direct_message(self, db, actor, request: ExternalAgentMailMessageRequest) -> ExternalAgentMailSendResponse:
        if request.recipient_member_id is None:
            raise ValueError("recipient_member_id is required")
        return await self.send_message(db, actor, request, kind="message")

    async def send_broadcast(self, db, actor, request: ExternalAgentMailMessageRequest) -> ExternalAgentMailSendResponse:
        return await self.send_message(db, actor, request, kind="broadcast", recipient_member_id=None)

    async def send_context_request(self, db, actor, request: ExternalAgentMailContextRequest) -> ExternalAgentMailSendResponse:
        payload = {"why_needed": request.why_needed, "files_or_symbols": request.files_or_symbols}
        return await self.send_message(
            db, actor,
            ExternalAgentMailMessageRequest(
                recipient_member_id=request.recipient_member_id, subject=request.subject,
                body_markdown=request.body_markdown,
            ), kind="context_request", payload=payload,
        )

    async def send_handoff(self, db, actor, request: ExternalAgentMailHandoffRequest) -> ExternalAgentMailSendResponse:
        payload = {"files": request.files, "next_steps": request.next_steps}
        return await self.send_message(
            db, actor,
            ExternalAgentMailMessageRequest(
                recipient_member_id=request.recipient_member_id, subject=request.subject,
                body_markdown=request.body_markdown,
            ), kind="handoff", payload=payload,
        )

    async def reply_in_thread(self, db, actor, root_id: int, request: ExternalAgentMailMessageRequest) -> ExternalAgentMailSendResponse:
        root = await db.get(MailMessage, root_id)
        if root is None:
            raise ValueError("Thread root not found")
        if root.sender_actor_id != actor.id:
            raise ValueError("External actors can only reply in threads they created")
        return await self.send_message(
            db, actor, request, kind="message", recipient_member_id=root.recipient_member_id,
            thread_root_id=root_id, subject=request.subject, body_markdown=request.body_markdown, payload=request.payload,
        )

    async def thread(self, db, actor, message_id: int) -> MailThreadResponse:
        thread = await agent_mail_service.get_thread(db, message_id)
        self._require_actor_owns_thread(thread, actor)
        return thread

    async def request_status(self, db, actor, message_id: int) -> ExternalAgentMailRequestStatus:
        thread = await self.thread(db, actor, message_id)
        root = thread.root
        if root.kind not in {"context_request", "handoff"}:
            raise ValueError("Message is not a request")
        answered = root.request_status in {"answered", "acknowledged"} or any(r.kind == "answer" for r in thread.replies)
        acknowledged = root.request_status == "acknowledged"
        return ExternalAgentMailRequestStatus(
            message_id=root.id, kind=root.kind, request_status=root.request_status,
            is_stale=root.is_stale, answered=answered, acknowledged=acknowledged,
            root=root, replies=thread.replies,
        )

    async def wait_for_request_status(self, db, actor, message_id: int, timeout_seconds: int) -> ExternalAgentMailRequestStatus:
        timeout = max(0, min(timeout_seconds, EXTERNAL_WAIT_MAX_SECONDS))
        deadline = datetime.utcnow() + timedelta(seconds=timeout)
        status = await self.request_status(db, actor, message_id)
        while status.request_status == "pending" and not status.answered and not status.is_stale and datetime.utcnow() < deadline:
            await db.rollback()
            db.expire_all()
            await asyncio.sleep(EXTERNAL_WAIT_POLL_SECONDS)
            status = await self.request_status(db, actor, message_id)
        return status

    async def acknowledge_external_request(self, db, actor, message_id: int) -> ExternalAgentMailRequestStatus:
        root = await db.get(MailMessage, message_id)
        if root is None:
            raise ValueError("Message not found")
        if root.sender_actor_id != actor.id:
            raise ValueError("External actors can only acknowledge requests they created")
        if root.kind not in {"context_request", "handoff"}:
            raise ValueError("Message is not a request")
        if root.request_status == "answered":
            root.request_status = "acknowledged"
            await db.commit()
        return await self.request_status(db, actor, message_id)

    async def _delivery_recipients(self, db, recipient_ids: set[int], wake_results: dict) -> list[ExternalAgentMailDeliveryRecipient]:
        members = {m.id: m for m in (await agent_mail_service.list_team(db))}
        recipients: list[ExternalAgentMailDeliveryRecipient] = []
        for member_id in sorted(recipient_ids):
            member = members.get(member_id)
            if member is None:
                db_member = await db.get(MailTeamMember, member_id)
                member_name = db_member.display_name if db_member is not None else f"Member {member_id}"
                wake_state = "offline"
            else:
                member_name = member.display_name
                wake_state = member.wake_state
            wake = wake_results.get(member_id, {})
            wake_attempted = bool(wake.get("wake_attempted", False))
            wake_succeeded = bool(wake.get("wake_succeeded", False))
            if wake_succeeded:
                status = "wake_succeeded"
            elif wake_attempted:
                status = "wake_failed"
            elif wake_state == "offline":
                status = "stored_offline"
            elif wake_state == "delivered_waiting":
                status = "delivered_waiting"
            else:
                status = "stored"
            recipients.append(ExternalAgentMailDeliveryRecipient(
                member_id=member_id, member_name=member_name, status=status, wake_state=wake_state,
                wake_attempted=wake_attempted, wake_succeeded=wake_succeeded,
                wake_method=str(wake["wake_method"]) if "wake_method" in wake else None,
                wake_error=str(wake["wake_error"]) if "wake_error" in wake else None,
            ))
        return recipients

    async def _require_member(self, db: AsyncSession, member_id: int) -> None:
        if await db.get(MailTeamMember, member_id) is None:
            raise ValueError(f"Recipient member {member_id} not found")

    def _require_actor_owns_thread(self, thread: MailThreadResponse, actor: MailExternalActor) -> None:
        if thread.root.sender_actor_id != actor.id:
            raise PermissionError("External actors can only read threads they created")

    def _delivery_state(self, recipients: list[ExternalAgentMailDeliveryRecipient]) -> str:
        if not recipients:
            return "stored_no_recipients"
        statuses = {r.status for r in recipients}
        if "wake_succeeded" in statuses:
            return "wake_succeeded"
        if "wake_failed" in statuses:
            return "wake_failed"
        if "delivered_waiting" in statuses:
            return "delivered_waiting"
        if "stored_offline" in statuses:
            return "stored_offline"
        return "stored"


external_agent_mail_service = ExternalAgentMailService()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_external_service.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/external_agent_mail_service.py backend/tests/agent_mail/test_external_service.py
git commit -m "feat(agent-mail): add external orchestration service (token auth + rate limit)"
```

---

### Task 16: External orchestration REST API

**Files:**
- Create: `backend/app/api/v1/external_agent_mail.py`
- Modify: `backend/app/api/v1/router.py` (register)
- Test: `backend/tests/agent_mail/test_external_api.py`

**Interfaces:**
- Produces: `router: APIRouter` mounted at `/api/v1/external/agent-mail` with routes `POST /actors`, `GET /actors/me`, `GET /members`, `POST /messages`, `POST /broadcasts`, `POST /context-requests`, `POST /handoffs`, `POST /threads/{id}/replies`, `GET /threads/{id}`, `GET /requests/{id}/status`, `GET /requests/{id}/wait`, `POST /requests/{id}/ack`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/agent_mail/test_external_api.py
import pytest
from httpx import ASGITransport, AsyncClient

from app.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_create_actor_requires_loopback():
    transport = ASGITransport(app=app, client=(("1.2.3.4", 12345)))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/external/agent-mail/actors", json={
            "actor_key": "remote", "display_name": "Remote",
        })
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_full_actor_lifecycle_and_context_request(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post("/api/v1/agent-mail/agent/register", json={
            "source": "hook", "cwd": str(tmp_path), "session_key": "cc:1",
        })
        member_id = r1.json()["member"]["id"]

        r2 = await client.post("/api/v1/external/agent-mail/actors", json={
            "actor_key": "openclaw", "display_name": "OpenClaw",
        })
        assert r2.status_code == 200
        token = r2.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        r3 = await client.get("/api/v1/external/agent-mail/actors/me", headers=headers)
        assert r3.status_code == 200
        assert r3.json()["actor_key"] == "openclaw"

        r4 = await client.post("/api/v1/external/agent-mail/context-requests", headers=headers, json={
            "recipient_member_id": member_id, "body_markdown": "need context", "why_needed": "testing",
        })
        assert r4.status_code == 200
        message_id = r4.json()["message"]["id"]

        r5 = await client.get(f"/api/v1/external/agent-mail/requests/{message_id}/status", headers=headers)
        assert r5.status_code == 200
        assert r5.json()["request_status"] == "pending"


@pytest.mark.asyncio
async def test_cross_actor_thread_access_forbidden(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post("/api/v1/agent-mail/agent/register", json={
            "source": "hook", "cwd": str(tmp_path), "session_key": "cc:1",
        })
        member_id = r1.json()["member"]["id"]

        actor1 = (await client.post("/api/v1/external/agent-mail/actors", json={
            "actor_key": "actor1", "display_name": "A1",
        })).json()
        actor2 = (await client.post("/api/v1/external/agent-mail/actors", json={
            "actor_key": "actor2", "display_name": "A2",
        })).json()

        r2 = await client.post(
            "/api/v1/external/agent-mail/messages",
            headers={"Authorization": f"Bearer {actor1['token']}"},
            json={"recipient_member_id": member_id, "body_markdown": "hi"},
        )
        message_id = r2.json()["message"]["id"]

        r3 = await client.get(
            f"/api/v1/external/agent-mail/threads/{message_id}",
            headers={"Authorization": f"Bearer {actor2['token']}"},
        )
        assert r3.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_external_api.py -v`
Expected: FAIL with 404 (router not mounted). If `ASGITransport(client=...)` isn't accepted by the installed `httpx` version, check `httpx.ASGITransport.__init__`'s signature (`python -c "import httpx, inspect; print(inspect.signature(httpx.ASGITransport.__init__))"`) and adjust — do not guess the kwarg name.

- [ ] **Step 3: Write the implementation**

```python
# backend/app/api/v1/external_agent_mail.py
"""External local Agent Mail orchestration endpoints — bearer-token
authenticated, for same-machine tools that aren't first-party Cockpit
integrations (e.g. OpenClaw). Ported near-verbatim from upstream."""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent_mail import MailExternalActor
from app.models.agent_mail_schemas import (
    ExternalAgentMailContextRequest,
    ExternalAgentMailHandoffRequest,
    ExternalAgentMailMessageRequest,
    ExternalAgentMailRequestStatus,
    ExternalAgentMailSendResponse,
    MailExternalActorCreate,
    MailExternalActorCreateResponse,
    MailExternalActorResponse,
    MailThreadResponse,
    TeamListResponse,
)
from app.services.external_agent_mail_service import (
    ExternalAgentMailAuthError,
    ExternalAgentMailRateLimitError,
    external_agent_mail_service,
)

router = APIRouter()


def _bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return authorization[len("Bearer "):].strip() or None


def _is_loopback_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost", "test", "testclient"}


async def external_actor(
    authorization: Optional[str] = Header(default=None), db: AsyncSession = Depends(get_db),
) -> MailExternalActor:
    try:
        return await external_agent_mail_service.authenticate_actor(db, _bearer_token(authorization))
    except ExternalAgentMailAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _rate_limit_response(exc: ExternalAgentMailRateLimitError) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={"code": "external_agent_mail_rate_limited", "message": str(exc), "retry_after_seconds": exc.retry_after_seconds},
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )


@router.post("/actors", response_model=MailExternalActorCreateResponse)
async def create_external_actor(request: Request, actor_request: MailExternalActorCreate, db: AsyncSession = Depends(get_db)):
    if not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="External actor tokens can only be created locally")
    try:
        return await external_agent_mail_service.create_actor(db, actor_request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/actors/me", response_model=MailExternalActorResponse)
async def get_external_actor_me(actor: MailExternalActor = Depends(external_actor)):
    return external_agent_mail_service.actor_response(actor)


@router.get("/members", response_model=TeamListResponse)
async def list_external_agent_mail_members(actor: MailExternalActor = Depends(external_actor), db: AsyncSession = Depends(get_db)):
    return await external_agent_mail_service.list_members(db)


@router.post("/messages", response_model=ExternalAgentMailSendResponse)
async def send_external_agent_mail_message(
    request: ExternalAgentMailMessageRequest, actor: MailExternalActor = Depends(external_actor), db: AsyncSession = Depends(get_db),
):
    try:
        return await external_agent_mail_service.send_direct_message(db, actor, request)
    except ExternalAgentMailRateLimitError as exc:
        raise _rate_limit_response(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/broadcasts", response_model=ExternalAgentMailSendResponse)
async def send_external_agent_mail_broadcast(
    request: ExternalAgentMailMessageRequest, actor: MailExternalActor = Depends(external_actor), db: AsyncSession = Depends(get_db),
):
    try:
        return await external_agent_mail_service.send_broadcast(db, actor, request)
    except ExternalAgentMailRateLimitError as exc:
        raise _rate_limit_response(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/context-requests", response_model=ExternalAgentMailSendResponse)
async def send_external_agent_mail_context_request(
    request: ExternalAgentMailContextRequest, actor: MailExternalActor = Depends(external_actor), db: AsyncSession = Depends(get_db),
):
    try:
        return await external_agent_mail_service.send_context_request(db, actor, request)
    except ExternalAgentMailRateLimitError as exc:
        raise _rate_limit_response(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/handoffs", response_model=ExternalAgentMailSendResponse)
async def send_external_agent_mail_handoff(
    request: ExternalAgentMailHandoffRequest, actor: MailExternalActor = Depends(external_actor), db: AsyncSession = Depends(get_db),
):
    try:
        return await external_agent_mail_service.send_handoff(db, actor, request)
    except ExternalAgentMailRateLimitError as exc:
        raise _rate_limit_response(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/threads/{message_id}/replies", response_model=ExternalAgentMailSendResponse)
async def reply_external_agent_mail_thread(
    message_id: int, request: ExternalAgentMailMessageRequest,
    actor: MailExternalActor = Depends(external_actor), db: AsyncSession = Depends(get_db),
):
    try:
        return await external_agent_mail_service.reply_in_thread(db, actor, message_id, request)
    except ExternalAgentMailRateLimitError as exc:
        raise _rate_limit_response(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/threads/{message_id}", response_model=MailThreadResponse)
async def get_external_agent_mail_thread(message_id: int, actor: MailExternalActor = Depends(external_actor), db: AsyncSession = Depends(get_db)):
    try:
        return await external_agent_mail_service.thread(db, actor, message_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/requests/{message_id}/status", response_model=ExternalAgentMailRequestStatus)
async def get_external_agent_mail_request_status(message_id: int, actor: MailExternalActor = Depends(external_actor), db: AsyncSession = Depends(get_db)):
    try:
        return await external_agent_mail_service.request_status(db, actor, message_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/requests/{message_id}/wait", response_model=ExternalAgentMailRequestStatus)
async def wait_external_agent_mail_request_status(
    message_id: int, timeout_seconds: int = 30, actor: MailExternalActor = Depends(external_actor), db: AsyncSession = Depends(get_db),
):
    try:
        return await external_agent_mail_service.wait_for_request_status(db, actor, message_id, timeout_seconds)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/requests/{message_id}/ack", response_model=ExternalAgentMailRequestStatus)
async def ack_external_agent_mail_request(
    message_id: int, response: Response, actor: MailExternalActor = Depends(external_actor), db: AsyncSession = Depends(get_db),
):
    try:
        response.status_code = 200
        return await external_agent_mail_service.acknowledge_external_request(db, actor, message_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

Register in `backend/app/api/v1/router.py`. Add the import next to `from .agent_mail import router as agent_mail_router`:

```python
from .external_agent_mail import router as external_agent_mail_router
```

Add the include next to `router.include_router(agent_mail_router, prefix="/agent-mail", tags=["Agent Mail"])`:

```python
router.include_router(external_agent_mail_router, prefix="/external/agent-mail", tags=["External Agent Mail"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/agent_mail/test_external_api.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/external_agent_mail.py backend/app/api/v1/router.py backend/tests/agent_mail/test_external_api.py
git commit -m "feat(agent-mail): add external orchestration REST API"
```

---

### Task 17: Frontend types, API client, and utils

**Files:**
- Create: `frontend/src/types/agentMail.ts`
- Create: `frontend/src/features/agent-mail/api.ts`
- Create: `frontend/src/features/agent-mail/utils.ts`

**Interfaces:**
- Consumes: `apiClient`/`buildEndpoint` from `@/lib/api` (existing).
- Produces: all types/functions later frontend tasks import. No `team_preset_id`/`team_slot_id`/`participant_kind` fields (backend doesn't emit them — Task 3). `AgentMailInstallStatus` drops `claude_code_mcp_installed`/`codex_mcp_installed`/`shim_path`/`deck_url`/`claude_mcp_config_path` and adds `codex_hook_shim_path`/`cockpit_url`/`mcp_server_hint` (Task 3/14's schema). `AgentMailSnippets` fields are `codex_hooks_snippet`/`agents_md_snippet` (Task 14), not `codex_config_toml`/`codex_agents_md`.

- [ ] **Step 1: There is no failing-test step for this task** — these are pure type/fetch-wrapper files with no independent runtime behavior to unit test; they're exercised transitively by Task 18's build. Proceed straight to implementation.

- [ ] **Step 2: Write `frontend/src/types/agentMail.ts`**

```typescript
export type MailMessageKind = 'message' | 'broadcast' | 'context_request' | 'handoff' | 'answer'
export type MailRequestStatus = 'pending' | 'answered' | 'acknowledged'
export type MailMemberStatus = 'connected' | 'observed' | 'offline'
export type MailSessionSource = 'hook' | 'mcp' | 'observed' | string
export type MailWakeMethod = 'tmux' | string
export type MailWakeState = 'wakeable' | 'delivered_waiting' | 'offline' | string

export interface MailSessionResponse {
  id: number
  provider: string
  source: MailSessionSource
  session_key: string
  cwd?: string | null
  tmux_target?: string | null
  mailbox_status: MailMemberStatus | string
  activity?: string | null
  last_seen_at?: string | null
}

export interface MailMemberResponse {
  id: number
  identity_key: string
  repo_id: string
  repo_path: string
  repo_name: string
  display_name: string
  role?: string | null
  charter?: string | null
  status: MailMemberStatus
  unread_count: number
  pending_count: number
  unseen_pending_count: number
  stale_pending_count: number
  can_nudge: boolean
  wake_methods?: MailWakeMethod[]
  wake_state?: MailWakeState
  last_inbox_checked_at?: string | null
  sessions: MailSessionResponse[]
}

export interface TeamListResponse {
  members: MailMemberResponse[]
}

export interface MailMemberUpdate {
  display_name?: string
  role?: string | null
  charter?: string | null
}

export interface MailMessageCreate {
  kind?: MailMessageKind
  sender_member_id?: number | null
  recipient_member_id?: number | null
  thread_root_id?: number | null
  subject?: string | null
  body_markdown: string
  payload?: Record<string, unknown> | null
}

export interface MailMessageResponse {
  id: number
  thread_root_id?: number | null
  kind: MailMessageKind
  sender_member_id?: number | null
  sender_actor_id?: number | null
  sender_type?: 'director' | 'member' | 'external_actor' | string
  sender_actor_kind?: string | null
  sender_name: string
  recipient_member_id?: number | null
  subject?: string | null
  body_markdown: string
  payload?: Record<string, unknown> | null
  request_status?: MailRequestStatus | null
  is_stale: boolean
  read_at?: string | null
  acked_at?: string | null
  created_at: string
}

export interface MailThreadResponse {
  root: MailMessageResponse
  replies: MailMessageResponse[]
}

export interface MailInboxResponse {
  member_id: number
  unread_count: number
  pending_count: number
  messages: MailMessageResponse[]
}

export interface AgentMailInstallStatus {
  claude_code_hooks: string[]
  claude_code_hooks_missing: string[]
  codex_cli_available: boolean
  codex_hooks: string[]
  codex_hooks_missing: string[]
  curl_available: boolean
  codex_hook_shim_path: string
  python_path: string
  cockpit_url: string
  claude_settings_path?: string | null
  codex_hooks_path?: string | null
  mcp_server_hint: string
}

export interface AgentMailSnippets {
  codex_hooks_snippet: string
  agents_md_snippet: string
}
```

- [ ] **Step 3: Write `frontend/src/features/agent-mail/api.ts`**

```typescript
import { apiClient, buildEndpoint } from '@/lib/api'
import type {
  AgentMailInstallStatus,
  AgentMailSnippets,
  MailInboxResponse,
  MailMemberResponse,
  MailMemberUpdate,
  MailMessageCreate,
  MailMessageResponse,
  MailThreadResponse,
  TeamListResponse,
} from '@/types/agentMail'

export function fetchAgentMailTeam(sync = true): Promise<TeamListResponse> {
  return apiClient<TeamListResponse>(buildEndpoint('agent-mail/team', { sync }))
}

export function updateAgentMailMember(memberId: number, update: MailMemberUpdate): Promise<MailMemberResponse> {
  return apiClient<MailMemberResponse>(`agent-mail/members/${memberId}`, {
    method: 'PATCH',
    body: JSON.stringify(update),
  })
}

export function sendAgentMailMessage(message: MailMessageCreate): Promise<MailMessageResponse> {
  return apiClient<MailMessageResponse>('agent-mail/messages', {
    method: 'POST',
    body: JSON.stringify(message),
  })
}

export function fetchAgentMailMessages(): Promise<MailMessageResponse[]> {
  return apiClient<MailMessageResponse[]>('agent-mail/messages')
}

export function fetchAgentMailThread(messageId: number, memberId?: number): Promise<MailThreadResponse> {
  return apiClient<MailThreadResponse>(
    buildEndpoint(`agent-mail/messages/${messageId}/thread`, { member_id: memberId })
  )
}

export function fetchAgentMailInbox(memberId: number, unreadOnly = false): Promise<MailInboxResponse> {
  return apiClient<MailInboxResponse>(
    buildEndpoint('agent-mail/agent/inbox', { member_id: memberId, unread_only: unreadOnly })
  )
}

export function markAgentMailRead(messageId: number, memberId: number): Promise<{ ok: boolean }> {
  return apiClient<{ ok: boolean }>(`agent-mail/messages/${messageId}/read`, {
    method: 'POST',
    body: JSON.stringify({ member_id: memberId }),
  })
}

export function queueAgentMailInboxCheck(
  memberId: number
): Promise<{ ok: boolean; method?: string; target?: string; prompt?: string }> {
  return apiClient<{ ok: boolean; method?: string; target?: string; prompt?: string }>(
    `agent-mail/members/${memberId}/queue-inbox-check`,
    { method: 'POST' }
  )
}

export function ackAgentMailMessage(messageId: number, memberId: number): Promise<{ ok: boolean }> {
  return apiClient<{ ok: boolean }>(`agent-mail/messages/${messageId}/ack`, {
    method: 'POST',
    body: JSON.stringify({ member_id: memberId }),
  })
}

export function fetchAgentMailInstallStatus(): Promise<AgentMailInstallStatus> {
  return apiClient<AgentMailInstallStatus>('agent-mail/install/status')
}

export function applyClaudeCodeAgentMailInstall(): Promise<AgentMailInstallStatus> {
  return apiClient<AgentMailInstallStatus>('agent-mail/install/claude-code/apply', {
    method: 'POST',
    body: JSON.stringify({ confirmed: true }),
  })
}

export function uninstallClaudeCodeAgentMail(): Promise<AgentMailInstallStatus> {
  return apiClient<AgentMailInstallStatus>('agent-mail/install/claude-code/uninstall', {
    method: 'POST',
    body: JSON.stringify({ confirmed: true }),
  })
}

export function applyCodexAgentMailInstall(): Promise<AgentMailInstallStatus> {
  return apiClient<AgentMailInstallStatus>('agent-mail/install/codex/apply', {
    method: 'POST',
    body: JSON.stringify({ confirmed: true }),
  })
}

export function uninstallCodexAgentMail(): Promise<AgentMailInstallStatus> {
  return apiClient<AgentMailInstallStatus>('agent-mail/install/codex/uninstall', {
    method: 'POST',
    body: JSON.stringify({ confirmed: true }),
  })
}

export function fetchAgentMailSnippets(): Promise<AgentMailSnippets> {
  return apiClient<AgentMailSnippets>('agent-mail/install/snippets')
}
```

- [ ] **Step 4: Write `frontend/src/features/agent-mail/utils.ts`**

Copy `/tmp/claude-1000/-home-vdvgu-claude-cockpit--claude-worktrees-k-upstream-sync-9af0/d8bfe5ae-13ba-4089-acce-48e90fcff7c0/scratchpad/upstream_src/frontend/utils.ts` to `frontend/src/features/agent-mail/utils.ts` verbatim — it has zero team_* or MCP-shim references, only needs a branding text swap:

- Line `if (state === 'wakeable') return 'Claude Deck has a wake path for this member.'` → `if (state === 'wakeable') return 'Claude Cockpit has a wake path for this member.'`
- Line `if (state === 'delivered_waiting') return 'Messages are delivered, but Claude Deck cannot wake this visible agent session.'` → `if (state === 'delivered_waiting') return 'Messages are delivered, but Claude Cockpit cannot wake this visible agent session.'`

No other changes — the file compiles as-is against the Task 17-Step-2 types (`MailMemberResponse`/`MailMessageResponse`/etc. field shapes match exactly since those two types didn't change any field `utils.ts` touches).

- [ ] **Step 5: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -i "agent-mail\|agentMail"`
Expected: no output (no errors referencing these 3 new files) — some errors are expected at this point from later tasks' components not existing yet; only check there are no errors *inside* the 3 files just written.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/agentMail.ts frontend/src/features/agent-mail/api.ts frontend/src/features/agent-mail/utils.ts
git commit -m "feat(agent-mail): add frontend types, API client, and label utils"
```

---

### Task 18: MemberEditDialog, RequestsTab (CLICKABLE_CARD), ThreadDialog (MarkdownPreviewToggle)

**Files:**
- Create: `frontend/src/features/agent-mail/MemberEditDialog.tsx`
- Create: `frontend/src/features/agent-mail/RequestsTab.tsx`
- Create: `frontend/src/features/agent-mail/ThreadDialog.tsx`

These three have zero `team_*`/`deck_`/`shim_path` references upstream (confirmed by grep) — copy verbatim from the scratchpad and apply only the listed patches. No independent unit test — verified by Task 23's `npm run build`.

- [ ] **Step 1: Copy `MemberEditDialog.tsx` and apply the markdown-toggle fixup**

Copy `/tmp/claude-1000/-home-vdvgu-claude-cockpit--claude-worktrees-k-upstream-sync-9af0/d8bfe5ae-13ba-4089-acce-48e90fcff7c0/scratchpad/upstream_src/frontend/MemberEditDialog.tsx` to `frontend/src/features/agent-mail/MemberEditDialog.tsx`. It has a `charter` field currently rendered as a plain shadcn `Textarea` — per the spec, swap it for this fork's `MarkdownPreviewToggle` (used identically elsewhere, e.g. `CardEditDialog.tsx`):

- Replace the `import { Textarea } from '@/components/ui/textarea'` line with `import { MarkdownPreviewToggle } from '@/components/shared/MarkdownPreviewToggle'` (the file's only `Textarea` usage is the charter field, so the import is fully replaced, not just supplemented).
- Replace the `<Textarea id="agent-mail-charter" value={charter} ... />` block (bound to the confirmed `charter`/`setCharter` state, declared as `const [charter, setCharter] = useState('')`) with:
  ```tsx
  <MarkdownPreviewToggle value={charter} onChange={setCharter} minHeight="100px" />
  ```
  Keep the surrounding `<Label htmlFor="agent-mail-charter">Charter</Label>` and its wrapper `<div>` as-is; only the input control changes.

- [ ] **Step 2: Copy `RequestsTab.tsx` and apply the CLICKABLE_CARD fixup**

Copy `/tmp/claude-1000/-home-vdvgu-claude-cockpit--claude-worktrees-k-upstream-sync-9af0/d8bfe5ae-13ba-4089-acce-48e90fcff7c0/scratchpad/upstream_src/frontend/RequestsTab.tsx` to `frontend/src/features/agent-mail/RequestsTab.tsx`. Each message renders as `<Card key={message.id} className="rounded-lg"><CardContent className="p-4">...<Button size="sm" variant="outline" onClick={() => onOpenThread(message)}>Thread</Button></CardContent></Card>` (confirmed exact structure). Per the spec, make the whole card clickable using this fork's `CLICKABLE_CARD` convention, matching the exact idiom already used in `frontend/src/features/kanban/components/CardItem.tsx`:

- Add the import: `import { CLICKABLE_CARD } from '@/lib/constants'`
- Change the `<Card key={message.id} className="rounded-lg">` element to:
  ```tsx
  <Card
    key={message.id}
    className={`${CLICKABLE_CARD} rounded-lg`}
    role="button"
    tabIndex={0}
    onClick={() => onOpenThread(message)}
    onKeyDown={(e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        onOpenThread(message)
      }
    }}
  >
  ```
- Remove the standalone `<Button size="sm" variant="outline" onClick={() => onOpenThread(message)}>Thread</Button>` (now redundant). The three quick-compose buttons above the list (`onCompose('context_request')` etc.) are outside these cards and are unaffected.

- [ ] **Step 3: Copy `ThreadDialog.tsx` and apply the markdown-toggle + RefreshButton fixups**

Copy `/tmp/claude-1000/-home-vdvgu-claude-cockpit--claude-worktrees-k-upstream-sync-9af0/d8bfe5ae-13ba-4089-acce-48e90fcff7c0/scratchpad/upstream_src/frontend/ThreadDialog.tsx` to `frontend/src/features/agent-mail/ThreadDialog.tsx`. Two fixups:

1. The reply form's body `<Textarea value={replyBody} ... />` (bound to the confirmed `replyBody`/`setReplyBody` state, declared as `const [replyBody, setReplyBody] = useState('')`) → replace with `<MarkdownPreviewToggle value={replyBody} onChange={setReplyBody} minHeight="120px" />` (add the same import as Step 1). The message-rendering side already correctly uses `<MarkdownRenderer content={message.body_markdown} />` via the internal `MessageBlock` component — leave that untouched, it needs no fix.
2. The manual refresh `<Button>` (icon `RefreshCw`, text "Refresh") duplicates the shared `RefreshButton` component. Replace it with `<RefreshButton onClick={onRefreshClicked} loading={refreshing} />` using this file's existing refresh handler/loading-state names, and add `import { RefreshButton } from '@/components/shared/RefreshButton'`. Remove the now-unused `RefreshCw` import from `lucide-react` if nothing else in the file uses it.

- [ ] **Step 4: TypeScript check**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -iE "MemberEditDialog|RequestsTab|ThreadDialog"`
Expected: no output. Fix any type errors before moving on (common cause: a prop name in the copied file doesn't match what `AgentMailPage.tsx` — written in Task 19 — passes; if this task lands before Task 19, ignore prop-mismatch errors from `AgentMailPage.tsx` not existing yet and only fix errors *inside* these three files).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/agent-mail/MemberEditDialog.tsx frontend/src/features/agent-mail/RequestsTab.tsx frontend/src/features/agent-mail/ThreadDialog.tsx
git commit -m "feat(agent-mail): add MemberEditDialog, RequestsTab, ThreadDialog (markdown + clickable-card fixups)"
```

---

### Task 19: TeamTab and ComposeDialog

**Files:**
- Create: `frontend/src/features/agent-mail/TeamTab.tsx`
- Create: `frontend/src/features/agent-mail/ComposeDialog.tsx`

- [ ] **Step 1: Copy `TeamTab.tsx` and remove team-slot UI**

Copy `/tmp/claude-1000/-home-vdvgu-claude-cockpit--claude-worktrees-k-upstream-sync-9af0/d8bfe5ae-13ba-4089-acce-48e90fcff7c0/scratchpad/upstream_src/frontend/TeamTab.tsx` to `frontend/src/features/agent-mail/TeamTab.tsx`. `MailMemberResponse`/`MailSessionResponse` (Task 17) no longer have `team_preset_name`/`team_slot_name`/`participant_kind`, so these must be removed (TypeScript will error on each until they are):

- Line ~124-125 (search-string builder): delete the two lines appending `member.team_preset_name ?? ''` and `member.team_slot_name ?? ''`.
- Line ~183-195 (member card team badge block): delete the whole `{member.participant_kind === 'team_slot' && (...)}` badge block and the `{member.team_preset_name && (...)}` block that follows it.
- Line ~285-292 (session row team badge): delete the `{session.team_preset_name && (...)}` block.

Everything else in this file — the search input, status `Select`, the `sameRuntime`/`hasConnectedReplacement`/`displaySessions` session-dedup logic — is unchanged and has no upstream-specific coupling; keep it as-is.

- [ ] **Step 2: Copy `ComposeDialog.tsx`, remove the team badge, apply the markdown-toggle fixup**

Copy `/tmp/claude-1000/-home-vdvgu-claude-cockpit--claude-worktrees-k-upstream-sync-9af0/d8bfe5ae-13ba-4089-acce-48e90fcff7c0/scratchpad/upstream_src/frontend/ComposeDialog.tsx` to `frontend/src/features/agent-mail/ComposeDialog.tsx`:

- Line ~49: delete `const team = member.team_preset_name ? \`, ${member.team_preset_name}\` : ''` and remove `${team}` from whatever recipient-label template string uses it a few lines below.
- Lines ~77/~82 ("Claude Deck cannot wake..." delivery-warning strings): replace `Claude Deck` with `Claude Cockpit` in both.
- Per the spec's fixup: the main message body field is bound to the confirmed `body`/`setBody` state (`const [body, setBody] = useState('')`, sent as `body_markdown: body`). Replace its `<Textarea value={body} onChange={(event) => setBody(event.target.value)} ... />` with `<MarkdownPreviewToggle value={body} onChange={setBody} minHeight="140px" />`. Leave the `why_needed`/`files`/`next_steps` fields (the other 4 `Textarea` usages in the file) as plain `Textarea` with the existing "one per line" `splitLines()` convention — those aren't markdown-rendered on the read side, so they don't need the toggle.
- Add `import { MarkdownPreviewToggle } from '@/components/shared/MarkdownPreviewToggle'`; remove the `Textarea` import only if nothing else in the file still uses it (the why_needed/files/next_steps fields still do — keep it).

- [ ] **Step 3: TypeScript check**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -iE "TeamTab|ComposeDialog"`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/agent-mail/TeamTab.tsx frontend/src/features/agent-mail/ComposeDialog.tsx
git commit -m "feat(agent-mail): add TeamTab and ComposeDialog (drop team-slot UI, markdown fixup)"
```

---

### Task 20: InstallTab

**Files:**
- Create: `frontend/src/features/agent-mail/InstallTab.tsx`

Upstream's version tracks MCP-registration status (`claude_code_mcp_installed`, `codex_mcp_installed`, `codex mcp add`) which this port dropped (MCP wiring is now a generic Cockpit MCP Server concern — Task 3/14's `AgentMailInstallStatus` has no such fields). Given the density of changes, this is the full adapted file rather than a line patch.

- [ ] **Step 1: Write the implementation**

```tsx
// frontend/src/features/agent-mail/InstallTab.tsx
import { CheckCircle2, Clipboard, Plug, RefreshCw, ShieldCheck, Terminal, Trash2, Unplug } from 'lucide-react'
import { toast } from 'sonner'
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import type { AgentMailInstallStatus, AgentMailSnippets } from '@/types/agentMail'
import { useState } from 'react'

type InstallActionKey = 'claude-apply' | 'claude-uninstall' | 'codex-apply' | 'codex-uninstall'

interface InstallTabProps {
  status: AgentMailInstallStatus | null
  snippets: AgentMailSnippets | null
  loading: boolean
  onRefresh: () => Promise<void>
  onApplyClaudeCode: () => Promise<void>
  onUninstallClaudeCode: () => Promise<void>
  onApplyCodex: () => Promise<void>
  onUninstallCodex: () => Promise<void>
}

function InstalledBadge({ installed }: { installed: boolean }) {
  return installed ? (
    <Badge variant="outline" className="border-emerald-300 text-emerald-700">installed</Badge>
  ) : (
    <Badge variant="outline">not installed</Badge>
  )
}

function PathLine({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="min-w-0 text-sm">
      <span className="text-muted-foreground">{label}: </span>
      <code className="break-all rounded bg-muted px-1 py-0.5 text-xs">{value || 'unavailable'}</code>
    </div>
  )
}

export function InstallTab({
  status, snippets, loading, onRefresh,
  onApplyClaudeCode, onUninstallClaudeCode, onApplyCodex, onUninstallCodex,
}: InstallTabProps) {
  const [confirming, setConfirming] = useState<InstallActionKey | null>(null)
  const [running, setRunning] = useState(false)

  const copyText = async (text: string, label: string) => {
    await navigator.clipboard.writeText(text)
    toast.success(`${label} copied`)
  }

  const actionDetails = (() => {
    if (!status || !confirming) return null
    if (confirming === 'claude-apply') {
      return {
        title: 'Install Agent Mail hooks for Claude Code',
        description: 'A backup is attempted before this user-scope config change is made.',
        mutations: [
          `${status.claude_settings_path || '~/.claude/settings.json'}: add four Agent Mail curl command hooks`,
        ],
        run: onApplyClaudeCode,
      }
    }
    if (confirming === 'claude-uninstall') {
      return {
        title: 'Remove Agent Mail hooks from Claude Code',
        description: 'A backup is attempted before the managed hooks are removed.',
        mutations: [`${status.claude_settings_path || '~/.claude/settings.json'}: remove Agent Mail hook commands`],
        run: onUninstallClaudeCode,
      }
    }
    if (confirming === 'codex-apply') {
      return {
        title: 'Install Agent Mail hooks for Codex',
        description: 'A backup is attempted before Claude Cockpit installs the Codex lifecycle hooks.',
        mutations: [
          `${status.codex_hooks_path || '~/.codex/hooks.json'}: add Agent Mail SessionStart and UserPromptSubmit hooks`,
          `Hook shim: ${status.python_path} ${status.codex_hook_shim_path}`,
        ],
        run: onApplyCodex,
      }
    }
    if (confirming === 'codex-uninstall') {
      return {
        title: 'Remove Agent Mail hooks from Codex',
        description: 'A backup is attempted before Claude Cockpit removes the managed hooks.',
        mutations: [`${status.codex_hooks_path || '~/.codex/hooks.json'}: remove Agent Mail hook commands`],
        run: onUninstallCodex,
      }
    }
    return null
  })()

  const runConfirmed = async () => {
    if (!actionDetails) return
    setRunning(true)
    try {
      await actionDetails.run()
      setConfirming(null)
    } finally {
      setRunning(false)
    }
  }

  if (!status) {
    return (
      <Card className="rounded-lg border-dashed">
        <CardContent className="py-12 text-center text-sm text-muted-foreground">
          {loading ? 'Loading install status...' : 'Install status unavailable.'}
        </CardContent>
      </Card>
    )
  }

  const claudeHooksInstalled = status.claude_code_hooks_missing.length === 0
  const codexHooksInstalled = status.codex_hooks_missing.length === 0

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">{status.mcp_server_hint}</p>
        <Button variant="outline" size="sm" onClick={onRefresh} disabled={loading}>
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card className="rounded-lg">
          <CardHeader>
            <div className="flex items-start justify-between gap-3">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Plug className="h-5 w-5" />
                  Claude Code
                </CardTitle>
                <CardDescription>Hooks deliver mailbox state into context at session start and each prompt.</CardDescription>
              </div>
              <InstalledBadge installed={claudeHooksInstalled} />
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {claudeHooksInstalled && (
              <Alert className="border-emerald-300 bg-emerald-50/60 dark:bg-emerald-950/20">
                <CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                <AlertTitle>Claude Code hooks installed</AlertTitle>
                <AlertDescription>
                  Restart or resume Claude Code sessions so the hooks are loaded. Register an MCP token on the
                  MCP Server page so agents can call the mail tools.
                </AlertDescription>
              </Alert>
            )}
            <div className="space-y-2">
              <PathLine label="Hooks file" value={status.claude_settings_path} />
              <PathLine label="Cockpit URL" value={status.cockpit_url} />
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge variant={status.curl_available ? 'secondary' : 'destructive'}>
                curl {status.curl_available ? 'available' : 'missing'}
              </Badge>
              <Badge variant="outline" className={claudeHooksInstalled ? 'border-emerald-300 text-emerald-700' : ''}>
                hooks {claudeHooksInstalled ? 'installed' : `${status.claude_code_hooks.length}/4 installed`}
              </Badge>
              {status.claude_code_hooks_missing.length > 0 && (
                <Badge variant="outline" className="border-amber-300 text-amber-700">
                  missing {status.claude_code_hooks_missing.length}
                </Badge>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" onClick={() => setConfirming('claude-apply')} disabled={claudeHooksInstalled}>
                <ShieldCheck className="mr-2 h-4 w-4" />
                Install
              </Button>
              <Button size="sm" variant="outline" onClick={() => setConfirming('claude-uninstall')} disabled={!claudeHooksInstalled}>
                <Unplug className="mr-2 h-4 w-4" />
                Uninstall
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card className="rounded-lg">
          <CardHeader>
            <div className="flex items-start justify-between gap-3">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Terminal className="h-5 w-5" />
                  Codex CLI
                </CardTitle>
                <CardDescription>Hooks remind Codex at turn boundaries; MCP tools send and read mail.</CardDescription>
              </div>
              <InstalledBadge installed={codexHooksInstalled} />
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {codexHooksInstalled && (
              <Alert className="border-blue-300 bg-blue-50/60 dark:bg-blue-950/20">
                <Terminal className="h-4 w-4 text-blue-600 dark:text-blue-400" />
                <AlertTitle>Codex hooks installed</AlertTitle>
                <AlertDescription>
                  Restart or resume Codex sessions so hooks are loaded. Register an MCP token on the MCP Server
                  page so this Codex session can call the mail tools.
                </AlertDescription>
              </Alert>
            )}
            <div className="space-y-2">
              <PathLine label="Hooks file" value={status.codex_hooks_path} />
              <PathLine label="Hook shim" value={status.codex_hook_shim_path} />
              <PathLine label="Python" value={status.python_path} />
              <PathLine label="Cockpit URL" value={status.cockpit_url} />
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge variant={status.codex_cli_available ? 'secondary' : 'destructive'}>
                Codex CLI {status.codex_cli_available ? 'available' : 'missing'}
              </Badge>
              <Badge variant="outline" className={codexHooksInstalled ? 'border-emerald-300 text-emerald-700' : ''}>
                hooks {codexHooksInstalled ? 'installed' : `${status.codex_hooks.length}/2 installed`}
              </Badge>
              {status.codex_hooks_missing.length > 0 && (
                <Badge variant="outline" className="border-amber-300 text-amber-700">
                  missing {status.codex_hooks_missing.length}
                </Badge>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" onClick={() => setConfirming('codex-apply')} disabled={!status.codex_cli_available || codexHooksInstalled}>
                <ShieldCheck className="mr-2 h-4 w-4" />
                Install
              </Button>
              <Button size="sm" variant="outline" onClick={() => setConfirming('codex-uninstall')} disabled={!codexHooksInstalled}>
                <Trash2 className="mr-2 h-4 w-4" />
                Remove
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      {snippets && (
        <div className="grid gap-4 xl:grid-cols-2">
          <Card className="rounded-lg">
            <CardHeader>
              <CardTitle>Codex hooks.json snippet</CardTitle>
              <CardDescription>Manual fallback if the Install button can't reach Codex config.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Textarea value={snippets.codex_hooks_snippet} readOnly rows={8} className="font-mono text-xs" />
              <Button size="sm" variant="outline" onClick={() => copyText(snippets.codex_hooks_snippet, 'Hooks snippet')}>
                <Clipboard className="mr-2 h-4 w-4" />
                Copy
              </Button>
            </CardContent>
          </Card>
          <Card className="rounded-lg">
            <CardHeader>
              <CardTitle>AGENTS.md snippet</CardTitle>
              <CardDescription>Suggested instruction block for Codex projects.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Textarea value={snippets.agents_md_snippet} readOnly rows={8} className="font-mono text-xs" />
              <Button size="sm" variant="outline" onClick={() => copyText(snippets.agents_md_snippet, 'AGENTS.md snippet')}>
                <Clipboard className="mr-2 h-4 w-4" />
                Copy
              </Button>
            </CardContent>
          </Card>
        </div>
      )}

      <AlertDialog open={Boolean(confirming)} onOpenChange={(open) => !open && setConfirming(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{actionDetails?.title}</AlertDialogTitle>
            <AlertDialogDescription>{actionDetails?.description}</AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2 rounded-lg border bg-muted/40 p-3 text-sm">
            {actionDetails?.mutations.map((mutation) => (
              <div key={mutation} className="break-words">{mutation}</div>
            ))}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={running}>Cancel</AlertDialogCancel>
            <Button onClick={runConfirmed} disabled={running}>{running ? 'Applying...' : 'Confirm'}</Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
```

- [ ] **Step 2: TypeScript check**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -i InstallTab`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/agent-mail/InstallTab.tsx
git commit -m "feat(agent-mail): add InstallTab (hooks-only, no MCP registration status)"
```

---

### Task 21: AgentMailHelpDialog and AgentMailPage

**Files:**
- Create: `frontend/src/features/agent-mail/AgentMailHelpDialog.tsx`
- Create: `frontend/src/features/agent-mail/AgentMailPage.tsx`

- [ ] **Step 1: Write `AgentMailHelpDialog.tsx`**

Full adapted file (renamed `deck_*` tools to `agent_mail_*`, dropped the Agent-Team-slot MVP-limits sentence since same-repo participants is out of scope, dropped the "MCP server" framing for Claude Code since that's now generic Cockpit MCP wiring):

```tsx
// frontend/src/features/agent-mail/AgentMailHelpDialog.tsx
import { BookOpen, CheckCircle2, Inbox, Plug, ShieldAlert, Terminal } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { MODAL_SIZES } from '@/lib/constants'

interface AgentMailHelpDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

function HelpSection({ icon: Icon, title, children }: {
  icon: typeof BookOpen
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="rounded-lg border p-4">
      <div className="mb-3 flex items-center gap-2">
        <Icon className="h-4 w-4 text-muted-foreground" />
        <h3 className="text-sm font-semibold">{title}</h3>
      </div>
      <div className="space-y-2 text-sm leading-6 text-muted-foreground">{children}</div>
    </section>
  )
}

export function AgentMailHelpDialog({ open, onOpenChange }: AgentMailHelpDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader>
          <DialogTitle>Agent Mail setup</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <HelpSection icon={Plug} title="Required agent configuration">
            <p>
              Agents need two things: an MCP token (MCP Server page) so they can call the mail
              tools, and lifecycle hooks (Install tab) so mailbox state and reminders get injected
              into their session context automatically.
            </p>
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline">Claude Code: MCP token + hooks</Badge>
              <Badge variant="outline">Codex CLI: MCP token + hooks</Badge>
            </div>
          </HelpSection>

          <HelpSection icon={CheckCircle2} title="First run checklist">
            <ol className="list-decimal space-y-1 pl-5">
              <li>Create an MCP token on the MCP Server page and wire it into Claude Code/Codex config.</li>
              <li>Use the Install tab to add the lifecycle hooks.</li>
              <li>Restart or resume the affected agent sessions.</li>
              <li>Have each agent call <code>agent_mail_whoami</code> once from its repo.</li>
              <li>Ask agents to check <code>agent_mail_check_inbox</code> before and after major work.</li>
            </ol>
          </HelpSection>

          <HelpSection icon={Terminal} title="Non-tmux delivery">
            <p>
              Claude Code and Codex sessions outside tmux can receive mail through MCP, but
              Claude Cockpit cannot wake their visible terminal session yet. Those messages stay
              unread until the agent checks its inbox or reaches a hook boundary.
            </p>
          </HelpSection>

          <HelpSection icon={Inbox} title="What agents can do">
            <p>
              Agents can request context from another repo's agent, create handoffs, reply in
              threads, acknowledge answers, and list the team. The useful tools are
              <code> agent_mail_request_context</code>, <code> agent_mail_create_handoff</code>,
              <code> agent_mail_reply</code>, and <code> agent_mail_ack_message</code>.
            </p>
          </HelpSection>

          <HelpSection icon={ShieldAlert} title="Current limits">
            <p>
              Visibility is machine-global — every local participant is visible to every other
              participant. Identity is one participant per repository (git worktrees of the same
              repo share it); multiple simultaneous agents in the exact same repo currently share
              one mailbox.
            </p>
          </HelpSection>
        </div>
      </DialogContent>
    </Dialog>
  )
}
```

- [ ] **Step 2: Write `AgentMailPage.tsx`**

Full adapted file (readiness now checks hooks only, since `claude_code_mcp_installed`/`codex_mcp_installed` no longer exist; setup-notice copy updated to mention the MCP Server page; `deck_whoami` → `agent_mail_whoami`):

```tsx
// frontend/src/features/agent-mail/AgentMailPage.tsx
import { useCallback, useEffect, useMemo, useState } from 'react'
import { BookOpen, Inbox, Mail, MessageSquarePlus, Plug, RefreshCw, Users } from 'lucide-react'
import { toast } from 'sonner'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { RefreshButton } from '@/components/shared/RefreshButton'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type {
  AgentMailInstallStatus,
  AgentMailSnippets,
  MailMemberResponse,
  MailMemberUpdate,
  MailMessageCreate,
  MailMessageKind,
  MailMessageResponse,
} from '@/types/agentMail'
import {
  applyClaudeCodeAgentMailInstall,
  applyCodexAgentMailInstall,
  fetchAgentMailInstallStatus,
  fetchAgentMailMessages,
  fetchAgentMailSnippets,
  fetchAgentMailTeam,
  queueAgentMailInboxCheck,
  sendAgentMailMessage,
  uninstallClaudeCodeAgentMail,
  uninstallCodexAgentMail,
  updateAgentMailMember,
} from './api'
import { AgentMailHelpDialog } from './AgentMailHelpDialog'
import { ComposeDialog, type ComposePreset } from './ComposeDialog'
import { InstallTab } from './InstallTab'
import { MemberEditDialog } from './MemberEditDialog'
import { RequestsTab, type RequestKindFilter, type RequestStatusFilter } from './RequestsTab'
import { TeamTab, type TeamStatusFilter } from './TeamTab'
import { ThreadDialog } from './ThreadDialog'

const OPERATIONAL_POLL_INTERVAL_MS = 5000

export function AgentMailPage() {
  const [members, setMembers] = useState<MailMemberResponse[]>([])
  const [messages, setMessages] = useState<MailMessageResponse[]>([])
  const [installStatus, setInstallStatus] = useState<AgentMailInstallStatus | null>(null)
  const [snippets, setSnippets] = useState<AgentMailSnippets | null>(null)
  const [loading, setLoading] = useState(true)
  const [installLoading, setInstallLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('team')
  const [teamSearch, setTeamSearch] = useState('')
  const [teamStatus, setTeamStatus] = useState<TeamStatusFilter>('all')
  const [requestSearch, setRequestSearch] = useState('')
  const [requestKind, setRequestKind] = useState<RequestKindFilter>('all')
  const [requestStatus, setRequestStatus] = useState<RequestStatusFilter>('all')
  const [editingMember, setEditingMember] = useState<MailMemberResponse | null>(null)
  const [composeOpen, setComposeOpen] = useState(false)
  const [composePreset, setComposePreset] = useState<ComposePreset | null>(null)
  const [threadMessage, setThreadMessage] = useState<MailMessageResponse | null>(null)
  const [helpOpen, setHelpOpen] = useState(false)
  const [nudgingMemberId, setNudgingMemberId] = useState<number | null>(null)

  const loadOperationalData = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true)
    try {
      const [team, roots] = await Promise.all([fetchAgentMailTeam(true), fetchAgentMailMessages()])
      setMembers(team.members)
      setMessages(roots)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to load Agent Mail')
    } finally {
      if (showLoading) setLoading(false)
    }
  }, [])

  const loadInstallData = useCallback(async () => {
    setInstallLoading(true)
    try {
      const [status, snippetData] = await Promise.all([fetchAgentMailInstallStatus(), fetchAgentMailSnippets()])
      setInstallStatus(status)
      setSnippets(snippetData)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to load install status')
    } finally {
      setInstallLoading(false)
    }
  }, [])

  const refreshAll = useCallback(async () => {
    await Promise.all([loadOperationalData(), loadInstallData()])
  }, [loadInstallData, loadOperationalData])

  useEffect(() => {
    let cancelled = false
    queueMicrotask(() => { if (!cancelled) void refreshAll() })
    return () => { cancelled = true }
  }, [refreshAll])

  useEffect(() => {
    const timer = window.setInterval(() => { void loadOperationalData(false) }, OPERATIONAL_POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [loadOperationalData])

  const stats = useMemo(() => {
    const connected = members.filter((m) => m.status === 'connected').length
    const observed = members.filter((m) => m.status === 'observed').length
    const unread = members.reduce((t, m) => t + m.unread_count, 0)
    const pending = members.reduce((t, m) => t + m.pending_count, 0)
    const unseenPending = members.reduce((t, m) => t + m.unseen_pending_count, 0)
    const stalePending = members.reduce((t, m) => t + m.stale_pending_count, 0)
    return { connected, observed, unread, pending, unseenPending, stalePending }
  }, [members])

  const claudeReady = Boolean(installStatus && installStatus.claude_code_hooks_missing.length === 0)
  const codexReady = Boolean(installStatus && installStatus.codex_hooks_missing.length === 0 && installStatus.codex_cli_available)
  const hasConfiguredIntegration = claudeReady || codexReady
  const showSetupNotice = !installLoading && (!hasConfiguredIntegration || members.length === 0)

  const openCompose = (kind: Exclude<MailMessageKind, 'answer'> = 'message', member?: MailMemberResponse) => {
    setComposePreset({
      kind,
      recipient_member_id: member?.id ?? null,
      subject: kind === 'handoff' && member ? `Handoff: ${member.display_name}` : undefined,
    })
    setComposeOpen(true)
  }

  const handleUpdateMember = async (memberId: number, update: MailMemberUpdate) => {
    try {
      await updateAgentMailMember(memberId, update)
      await loadOperationalData()
      toast.success('Participant updated')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to update participant')
      throw error
    }
  }

  const handleSendMessage = async (message: MailMessageCreate) => {
    try {
      await sendAgentMailMessage(message)
      await loadOperationalData()
      toast.success('Message sent')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to send message')
      throw error
    }
  }

  const handleQueueInboxCheck = async (member: MailMemberResponse) => {
    setNudgingMemberId(member.id)
    try {
      const result = await queueAgentMailInboxCheck(member.id)
      await loadOperationalData(false)
      const method = result.method ? `via ${result.method}` : 'via tmux'
      toast.success(`Queued inbox check for ${member.display_name} ${method}`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to queue inbox check')
    } finally {
      setNudgingMemberId(null)
    }
  }

  const runInstallAction = async (action: () => Promise<AgentMailInstallStatus>, label: string) => {
    try {
      const status = await action()
      setInstallStatus(status)
      await loadInstallData()
      await loadOperationalData(false)
      toast.success(label)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Install action failed')
      throw error
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-3xl font-bold">
            <Mail className="h-8 w-8" />
            Agent Mail
          </h1>
          <p className="mt-1 text-muted-foreground">
            Coordinate local agent sessions through structured context requests and handoffs.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <RefreshButton onClick={refreshAll} loading={loading || installLoading} />
          <Button variant="outline" onClick={() => setHelpOpen(true)}>
            <BookOpen className="mr-2 h-4 w-4" />
            How it works
          </Button>
          <Button onClick={() => openCompose('context_request')}>
            <MessageSquarePlus className="mr-2 h-4 w-4" />
            New request
          </Button>
        </div>
      </div>

      {showSetupNotice && (
        <Alert className="border-amber-300 bg-amber-50/60 dark:bg-amber-950/20">
          <Plug className="h-4 w-4" />
          <AlertTitle>{!hasConfiguredIntegration ? 'Agent setup required' : 'No agents registered yet'}</AlertTitle>
          <AlertDescription>
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <p>
                {!hasConfiguredIntegration
                  ? 'Install the Agent Mail hooks for Claude Code or Codex, and create an MCP token on the MCP Server page, before agents can send, receive, or answer mailbox requests.'
                  : 'Start or resume an agent in a repository, then have it call agent_mail_whoami once so Claude Cockpit can attach it to a participant.'}
              </p>
              <div className="flex shrink-0 flex-wrap gap-2">
                <Button size="sm" variant="outline" onClick={() => setHelpOpen(true)}>
                  <BookOpen className="mr-2 h-4 w-4" />
                  Setup notes
                </Button>
                {!hasConfiguredIntegration && (
                  <Button size="sm" onClick={() => setActiveTab('install')}>
                    <Plug className="mr-2 h-4 w-4" />
                    Open Install
                  </Button>
                )}
              </div>
            </div>
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-4 md:grid-cols-4">
        <Card className="rounded-lg">
          <CardHeader className="pb-3">
            <CardDescription>Participants</CardDescription>
            <CardTitle className="flex items-center gap-2 text-3xl">
              <Users className="h-5 w-5 text-muted-foreground" />
              {members.length}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card className="rounded-lg">
          <CardHeader className="pb-3">
            <CardDescription>Connected</CardDescription>
            <CardTitle className="text-3xl">{stats.connected}</CardTitle>
          </CardHeader>
        </Card>
        <Card className="rounded-lg">
          <CardHeader className="pb-3">
            <CardDescription>Observed only</CardDescription>
            <CardTitle className="text-3xl">{stats.observed}</CardTitle>
          </CardHeader>
        </Card>
        <Card className="rounded-lg">
          <CardHeader className="pb-3">
            <CardDescription>Inbox load</CardDescription>
            <CardTitle className="flex flex-wrap items-center gap-2 text-base">
              <Badge variant="secondary">{stats.unread} unread</Badge>
              <Badge variant="outline" className={stats.pending ? 'border-amber-300 text-amber-700' : ''}>
                {stats.pending} pending
              </Badge>
              {stats.unseenPending > 0 && (
                <Badge variant="outline" className="border-blue-300 text-blue-700">{stats.unseenPending} unseen</Badge>
              )}
              {stats.stalePending > 0 && (
                <Badge variant="outline" className="border-amber-300 text-amber-700">{stats.stalePending} stale</Badge>
              )}
            </CardTitle>
          </CardHeader>
        </Card>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid h-auto w-full grid-cols-3 md:w-[520px]">
          <TabsTrigger value="team" className="gap-2"><Users className="h-4 w-4" />Team</TabsTrigger>
          <TabsTrigger value="requests" className="gap-2"><Inbox className="h-4 w-4" />Requests</TabsTrigger>
          <TabsTrigger value="install" className="gap-2"><RefreshCw className="h-4 w-4" />Install</TabsTrigger>
        </TabsList>
        <TabsContent value="team" className="mt-4">
          <TeamTab
            members={members} loading={loading} repoSearch={teamSearch} statusFilter={teamStatus}
            onRepoSearchChange={setTeamSearch} onStatusFilterChange={setTeamStatus}
            onEdit={setEditingMember} onCompose={openCompose} onQueueInboxCheck={handleQueueInboxCheck}
            nudgingMemberId={nudgingMemberId}
          />
        </TabsContent>
        <TabsContent value="requests" className="mt-4">
          <RequestsTab
            messages={messages} members={members} loading={loading}
            search={requestSearch} kindFilter={requestKind} statusFilter={requestStatus}
            onSearchChange={setRequestSearch} onKindFilterChange={setRequestKind} onStatusFilterChange={setRequestStatus}
            onOpenThread={setThreadMessage} onCompose={openCompose}
          />
        </TabsContent>
        <TabsContent value="install" className="mt-4">
          <InstallTab
            status={installStatus} snippets={snippets} loading={installLoading} onRefresh={loadInstallData}
            onApplyClaudeCode={() => runInstallAction(applyClaudeCodeAgentMailInstall, 'Claude Code hooks installed')}
            onUninstallClaudeCode={() => runInstallAction(uninstallClaudeCodeAgentMail, 'Claude Code hooks removed')}
            onApplyCodex={() => runInstallAction(applyCodexAgentMailInstall, 'Codex hooks installed')}
            onUninstallCodex={() => runInstallAction(uninstallCodexAgentMail, 'Codex hooks removed')}
          />
        </TabsContent>
      </Tabs>

      <MemberEditDialog
        member={editingMember} open={Boolean(editingMember)}
        onOpenChange={(open) => !open && setEditingMember(null)} onSave={handleUpdateMember}
      />
      <ComposeDialog open={composeOpen} members={members} preset={composePreset} onOpenChange={setComposeOpen} onSend={handleSendMessage} />
      <ThreadDialog
        message={threadMessage} members={members} open={Boolean(threadMessage)}
        onOpenChange={(open) => !open && setThreadMessage(null)} onChanged={loadOperationalData}
      />
      <AgentMailHelpDialog open={helpOpen} onOpenChange={setHelpOpen} />
    </div>
  )
}
```

- [ ] **Step 3: Wire routing and sidebar navigation**

In `frontend/src/App.tsx`, this fork lazy-loads every page (`const XPage = lazy(() => import(...).then((m) => ({ default: m.XPage })))`) — add the same pattern next to `PresencePage` (line 30):

```tsx
const AgentMailPage = lazy(() => import('./features/agent-mail/AgentMailPage').then((m) => ({ default: m.AgentMailPage })))
```

Add the route inside the existing layout `<Route>` block, next to the `presence` route (line 70):

```tsx
<Route path="agent-mail" element={<AgentMailPage />} />
```

In `frontend/src/lib/navigation.ts`, add `Mail` to the `lucide-react` import list, and add a nav entry next to the existing `{ name: 'Presence', href: '/presence', icon: Radio }` entry (line 61) in the same `commonNavigation` group — do **not** remove the Presence entry, unlike upstream, since Presence is still a live feature in this fork:

```tsx
{ name: 'Agent Mail', href: '/agent-mail', icon: Mail },
```

- [ ] **Step 4: TypeScript check and full build**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json`
Expected: no errors anywhere in `frontend/src/features/agent-mail/`, `frontend/src/types/agentMail.ts`, `App.tsx`, or `navigation.ts`. Fix any remaining prop-shape mismatches between the components written in Tasks 18-21 now that they're all wired together.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/agent-mail/AgentMailHelpDialog.tsx frontend/src/features/agent-mail/AgentMailPage.tsx frontend/src/App.tsx frontend/src/lib/navigation.ts
git commit -m "feat(agent-mail): add AgentMailPage, help dialog, and route/nav wiring"
```

---

### Task 22: Full verification sweep and manual MCP tool check

**Files:** none created — verification only.

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && source venv/bin/activate && pytest -q`
Expected: all tests pass, including the pre-existing suite (not just `tests/agent_mail/` and the new `test_agent_mail_*`/`test_repo_utils.py` files). Fix any regressions before continuing — do not skip or delete a failing pre-existing test to make this pass.

- [ ] **Step 2: Run ruff**

Run: `cd backend && source venv/bin/activate && ruff check app/ tests/`
Expected: clean. Fix any lint findings in the new files.

- [ ] **Step 3: Run frontend lint and build**

Run: `cd frontend && npm run lint && npm run build`
Expected: both clean. `npm run build` failing means `frontend/dist` (what's actually served) doesn't reflect these changes — do not consider this task done until it's green.

- [ ] **Step 4: Start the dev stack and manually verify the MCP tools are live**

New MCP tools need a backend restart before a *running* server reflects them (known pattern in this fork).

```bash
./scripts/cockpit.sh restart
./scripts/cockpit.sh status
```

Then verify the 8 new tools are registered by hitting the MCP server directly (create a token first via the MCP Server page or `POST /api/v1/mcp-server/tokens`, or reuse an existing one):

```bash
curl -s -X POST http://localhost:8000/api/v1/mcp-server \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 -m json.tool | grep agent_mail
```

Expected: all 8 `agent_mail_*` tool names appear.

- [ ] **Step 5: Manual UI smoke test**

Open `http://localhost:8000/agent-mail` in a browser. Confirm: the page loads without console errors, the Team/Requests/Install tabs render, the Install tab's Claude Code panel shows "not installed" with a working Install button (clicking it, confirming, and re-checking status), and the sidebar still shows both "Presence" and "Agent Mail" entries. Register two members via `curl -X POST /api/v1/agent-mail/agent/register` with two different `cwd`s and confirm both appear in the Team tab after a refresh.

- [ ] **Step 6: Commit any fixes found during verification**

If Steps 1-5 surfaced fixes, commit them now with a message describing what was wrong (e.g. `fix(agent-mail): correct X found during verification sweep`).

---

### Task 23: Documentation and ship

**Files:**
- Modify: `docs/cockpit/agent-mail-spec.md` (mark implemented, note any deviations discovered during implementation)

- [ ] **Step 1: Update the spec doc**

Read `docs/cockpit/agent-mail-spec.md` and add a short "Implementation notes" section at the end recording anything that changed from the plan during actual implementation (e.g. exact `mcp` SDK tool-result unpacking shape found in Task 11, any `httpx.ASGITransport` kwarg adjustments from Task 16, any TeamTab/ComposeDialog state-variable names that differed from the plan's assumption). If nothing deviated, add a one-line note saying so.

- [ ] **Step 2: Commit the doc update**

```bash
git add docs/cockpit/agent-mail-spec.md
git commit -m "docs(agent-mail): record implementation notes"
```

- [ ] **Step 3: Follow this repo's session-end ship workflow**

Per this repo's `CLAUDE.md` Git Workflow section and the card's ship mode (**direct**): sync with `git fetch origin`, confirm `cd backend && pytest -q` and `cd frontend && npm run lint && npm run build` are green (already true from Task 22, but re-check after Task 23's doc commit since nothing code-related should have changed), then merge to master and push:

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
git checkout master
git merge --no-ff "$BRANCH"
git push origin HEAD:master
git checkout "$BRANCH"
```

Then attach the deliverable and move the kanban card to Done per the engineer role's session-end workflow (`attach_deliverable` with `kind="branch"`, `move_card` to `"Done"`).

---
