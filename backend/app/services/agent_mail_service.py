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
from typing import List, Optional

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
