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

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_mail import (
    MailAgentSession,
    MailExternalActor,
    MailMessage,
    MailReceipt,
    MailTeamMember,
)
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
        self, db: AsyncSession, session_key: str, activity: str | None = None
    ) -> MailAgentSession | None:
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

    def _pid_is_running(self, pid: int | None) -> bool:
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

    async def send_message(
        self,
        db: AsyncSession,
        request: MailMessageCreate,
        *,
        auto_nudge: bool = True,
        sender_actor_id: int | None = None,
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
        self, db: AsyncSession, sender_member_id: int | None, sender_actor_id: int | None,
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
        self, db: AsyncSession, message: MailMessage, for_member_id: int | None
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
        self, db: AsyncSession, root_id: int, for_member_id: int | None = None
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

    async def list_root_messages(self, db: AsyncSession, limit: int = 100) -> list[MailMessageResponse]:
        roots = (await db.execute(
            select(MailMessage).where(MailMessage.thread_root_id.is_(None))
            .order_by(MailMessage.created_at.desc()).limit(limit)
        )).scalars().all()
        return [await self._message_response(db, root, for_member_id=None) for root in roots]

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

    def _session_response(self, session: MailAgentSession, now: datetime) -> MailSessionResponse:
        return MailSessionResponse(
            id=session.id, provider=session.provider, source=session.source,
            session_key=session.session_key, cwd=session.cwd, tmux_target=session.tmux_target,
            mailbox_status=self._effective_status(session, now), activity=session.activity,
            last_seen_at=session.last_seen_at,
        )

    async def list_team(self, db: AsyncSession) -> list[MailMemberResponse]:
        now = datetime.utcnow()
        members = (await db.execute(select(MailTeamMember))).scalars().all()
        sessions = (await db.execute(select(MailAgentSession))).scalars().all()
        by_member: dict[int, list[MailAgentSession]] = {}
        for session in sessions:
            by_member.setdefault(session.member_id, []).append(session)

        responses: list[MailMemberResponse] = []
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

    async def build_prompt_submit_context(self, db: AsyncSession, member_id: int) -> str | None:
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


agent_mail_service = AgentMailService()
