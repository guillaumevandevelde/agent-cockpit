"""Registry + discovery behaviour for Agent Mail.

Adapted from upstream claude-deck's agent_mail_service.py: identity is
repo-scoped only (no team-preset/slot integration — see
docs/cockpit/agent-mail-spec.md), tmux delivery reuses
app.services.scheduling.tmux_inject instead of a private subprocess call,
and pane discovery reuses app.services.runs.discovery instead of a
private scanner.

Mailbox state, message send/receive, and request lifecycles were removed
2026-08-15 (kaart ``46930d26…``); what stays is the roster layer.
"""
import logging
import os
import subprocess
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_mail import MailAgentSession, MailTeamMember
from app.models.agent_mail_schemas import (
    MailAgentRegisterRequest,
    MailMemberResponse,
    MailSessionResponse,
)
from app.services.runs.discovery import discover_agent_sessions
from app.utils.repo_utils import derive_repo_identity

logger = logging.getLogger(__name__)

HEARTBEAT_TTL_SECONDS = 180
MCP_HEARTBEAT_TTL_SECONDS = 3600
OBSERVED_TTL_SECONDS = 300


class AgentMailService:
    """Registry + discovery behaviour for Agent Mail."""

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
        session.cli = request.cli
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
            session.cli = info.get("cli") or info.get("provider") or "unknown"
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
        of the same CLI via PID ancestry, so one logical agent doesn't get
        two member rows (a hook-registered session plus a tmux-observed one)."""
        cwd = str(info.get("cwd") or "")
        cli = str(info.get("cli") or info.get("provider") or "unknown")
        try:
            pid = int(info.get("pid") or 0) or None
        except (TypeError, ValueError):
            pid = None

        if pid is not None:
            now = datetime.utcnow()
            result = await db.execute(
                select(MailAgentSession).where(
                    MailAgentSession.source != "observed",
                    MailAgentSession.cli == cli,
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
        await db.delete(member)

    def _session_response(self, session: MailAgentSession, now: datetime) -> MailSessionResponse:
        return MailSessionResponse(
            id=session.id, cli=session.cli, source=session.source,
            session_key=session.session_key, cwd=session.cwd, tmux_target=session.tmux_target,
            mailbox_status=self._effective_status(session, now), activity=session.activity,
            last_seen_at=session.last_seen_at,
        )

    async def _gc_stale_repo_members(self, db: AsyncSession) -> int:
        """Delete MailTeamMember rows whose ``repo_path`` no longer exists on disk.

        ``_get_or_create_repo_member`` hands out a permanent identity to any
        cwd that ever registers, including ephemeral ones (pytest tmp_path,
        scratchpad dirs, manual probes) -- nothing ever reclaims them once the
        directory is gone, so they accumulate as roster/prompt noise (see
        docs/cockpit/spawn-test-bridge-sessions-analyse.md bevinding 7). A real
        repo's working directory always exists, so this never touches it.
        """
        members = (await db.execute(select(MailTeamMember))).scalars().all()
        stale = [m for m in members if not os.path.isdir(m.repo_path)]
        for member in stale:
            await db.delete(member)
        if stale:
            await db.commit()
        return len(stale)

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
            responses.append(MailMemberResponse(
                id=member.id, identity_key=member.identity_key, repo_id=member.repo_id,
                repo_path=member.repo_path, repo_name=member.repo_name, display_name=member.display_name,
                role=member.role, charter=member.charter, status=status,
                last_inbox_checked_at=member.last_inbox_checked_at,
                sessions=session_responses,
            ))
        responses.sort(key=lambda m: (m.status != "connected", m.display_name.lower()))
        return responses

    async def build_session_start_context(
        self, db: AsyncSession, member_id: int, session_key: str | None = None,
    ) -> str:
        # This builds the "Team: ..." roster injected into every dispatched
        # session's first prompt -- the one place a stale identity is
        # guaranteed to be re-read at a low, natural cadence (once per
        # session start), so it's also where the GC runs.
        await self._gc_stale_repo_members(db)
        member = await db.get(MailTeamMember, member_id)
        if member is None:
            return ""
        team = await self.list_team(db)
        others = [c for c in team if c.id != member_id]

        lines = ["[Agent Cockpit Agent Mail]"]
        role = f" ({member.role})" if member.role else ""
        lines.append(f'You are "{member.display_name}"{role} - repo: {member.repo_name}.')
        if member.charter:
            lines.append(f"Charter: {member.charter}")
        if others:
            roster = " | ".join(
                f"{c.display_name} ({c.role or c.repo_name}, {c.status})" for c in others[:8]
            )
            lines.append(f"Team: {roster}")
        return "\n".join(lines)


agent_mail_service = AgentMailService()
