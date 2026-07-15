"""Service for Presence Dashboard — event processing and session aggregation.

Uses hardware-aware limits via memory_monitor for dynamic resource management.
"""
import asyncio
import json
import logging
import os
import re
import time
from datetime import UTC, datetime, timedelta

from fastapi import WebSocket
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.constants import SessionStatus
from app.models.database import PresenceEvent, PresenceSession
from app.models.schemas import PresenceSessionResponse
from app.services.memory_monitor import get_dynamic_limits, get_memory_status_cached
from app.utils.timeutils import ensure_aware

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections for live presence updates."""

    def __init__(self, max_connections: int = 50):
        self.active_connections: list[WebSocket] = []
        self._max_connections = max_connections

    @property
    def effective_max_connections(self) -> int:
        """Dynamic max based on memory status."""
        status = get_memory_status_cached()
        if status.is_critical:
            return max(5, self._max_connections // 4)
        elif status.is_warning:
            return max(10, self._max_connections // 2)
        return self._max_connections

    async def connect(self, ws: WebSocket) -> bool:
        """Connect a WebSocket. Returns False if limit reached."""
        if len(self.active_connections) >= self.effective_max_connections:
            return False
        await ws.accept()
        self.active_connections.append(ws)
        return True

    def disconnect(self, ws: WebSocket):
        if ws in self.active_connections:
            self.active_connections.remove(ws)

    async def broadcast(self, message: str):
        disconnected = []
        for ws in self.active_connections:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)


manager = ConnectionManager()

IDLE_TIMEOUT_MINUTES = 15
BUCKET_COUNT = 30
FILE_EDIT_TOOLS = {"Write", "Edit", "MultiEdit"}
EVENT_RETENTION_DAYS = 7
IDLE_CHECK_INTERVAL_SECONDS = 30
STOPPED_SESSION_RETENTION_MINUTES = 5

# Guarded by _maintenance_lock to prevent concurrent double-execution
_maintenance_lock = asyncio.Lock()
_last_idle_check: float = 0.0
_last_prune: float = 0.0


class PresenceService:
    """Processes webhook events and maintains aggregated session state."""

    async def process_event(self, payload: dict, db: AsyncSession) -> PresenceSessionResponse:
        now = datetime.now(UTC)
        session_id = payload["session_id"]
        event_type = payload.get("hook_event_name", "Unknown")

        # Check session limit for new sessions
        result = await db.execute(
            select(func.count()).select_from(PresenceSession)
        )
        session_count = result.scalar() or 0
        limits = get_dynamic_limits()

        # Check if this is a new session
        existing = await db.execute(
            select(PresenceSession).where(PresenceSession.session_id == session_id)
        )
        is_new = existing.scalar_one_or_none() is None

        if is_new and session_count >= limits.max_active_sessions:
            # Memory pressure: reject new session, log warning
            status = get_memory_status_cached()
            logger.warning(
                f"Presence session limit reached ({session_count}/{limits.max_active_sessions}). "
                f"Memory: {status.usage_percent:.0%} used. "
                f"Rejecting new session {session_id[:8]}..."
            )
            # Don't persist the event: with no session created it would be an
            # orphan, which the ON DELETE CASCADE FK on presence_events forbids.
            # Return a minimal response
            return PresenceSessionResponse(
                session_id=session_id,
                label=None,
                project_path=payload.get("cwd"),
                tmux_pane=payload.get("tmux_pane"),
                status=SessionStatus.ACTIVE,
                status_text="Rejected: session limit",
                last_narrative=None,
                last_narrative_at=None,
                modified_files=[],
                last_user_prompt=None,
                last_command=None,
                last_command_exit=None,
                activity_buckets=[0] * BUCKET_COUNT,
                total_events=0,
                error_count=0,
                started_at=now.isoformat(),
                last_event_at=now.isoformat(),
                ended_at=None,
            )

        # Store raw event in the same transaction as the session update
        db.add(PresenceEvent(
            session_id=session_id,
            event_type=event_type,
            tool_name=payload.get("tool_name"),
            tool_input=payload.get("tool_input"),
            tool_result=payload.get("tool_result"),
            message=payload.get("message"),
            cwd=payload.get("cwd"),
            timestamp=now,
            received_at=now,
        ))

        # Upsert presence session
        result = await db.execute(
            select(PresenceSession).where(PresenceSession.session_id == session_id)
        )
        session = result.scalar_one_or_none()

        if session is None:
            session = PresenceSession(
                session_id=session_id,
                status=SessionStatus.ACTIVE,
                started_at=now,
                last_event_at=now,
                total_events=0,
                error_count=0,
                activity_buckets=[0] * BUCKET_COUNT,
                bucket_start=now,
                modified_files=[],
            )
            db.add(session)

        # Derive project_path / label from cwd
        cwd = payload.get("cwd")
        if cwd and not session.project_path:
            session.project_path = cwd
        if cwd and not session.label:
            base_label = self._derive_label(cwd)
            session.label = await self._assign_unique_label(base_label, session_id, db)

        # Tmux pane id (from the hook's $TMUX_PANE) — the exact join key to the
        # Agent Bridge. Only set when present so a later event without it (e.g. a
        # non-tmux event) doesn't clear it.
        pane = payload.get("tmux_pane")
        if pane:
            session.tmux_pane = pane

        # Update based on event type
        if event_type == "Notification":
            msg = payload.get("message")
            if msg:
                # Skip generic "waiting" notifications — redundant with Stop event
                waiting_phrases = ("waiting for your input", "waiting for input")
                if not any(p in msg.lower() for p in waiting_phrases):
                    session.last_narrative = msg
                    session.last_narrative_at = now
                    session.status_text = msg.replace("\n", " ").strip()[:120]

        elif event_type == "PostToolUse":
            tool_name = payload.get("tool_name", "")
            tool_input = payload.get("tool_input") or {}
            tool_result = payload.get("tool_result") or {}

            if tool_name in FILE_EDIT_TOOLS:
                file_path = tool_input.get("file_path") or tool_input.get("path")
                if file_path:
                    op = "created" if tool_name == "Write" else "modified"
                    files = list(session.modified_files or [])
                    files = [f for f in files if self._get_file_path(f) != file_path]
                    files.append({"path": file_path, "op": op})
                    session.modified_files = files[-10:]
                    basename = os.path.basename(file_path)
                    session.status_text = f"Edited {basename}"
                else:
                    session.status_text = f"Used tool: {tool_name}"
            elif tool_name == "Bash":
                cmd = tool_input.get("command", "")
                session.last_command = cmd[:500] if cmd else None
                exit_code = self._extract_exit_code(tool_result)
                session.last_command_exit = exit_code
                if exit_code and exit_code != 0:
                    session.error_count = (session.error_count or 0) + 1
                    session.status = SessionStatus.ERROR
                session.status_text = "Ran command"
            else:
                session.status_text = f"Used tool: {tool_name}"

        elif event_type == "PreToolUse":
            tool_name = payload.get("tool_name", "unknown")
            session.status_text = f"Running tool: {tool_name}..."

        elif event_type == "UserPromptSubmit":
            msg = payload.get("user_prompt") or payload.get("message")
            if msg:
                session.last_user_prompt = msg.strip()[:500]
            session.status_text = "Processing user message..."

        elif event_type == "SubagentStart":
            session.status_text = "Started subagent"

        elif event_type == "SubagentStop":
            session.status_text = "Subagent completed"

        elif event_type == "Stop":
            session.status = SessionStatus.STOPPED
            session.status_text = "Waiting for input"

        elif event_type == "SessionEnd":
            session.status = SessionStatus.STOPPED
            session.ended_at = now
            session.status_text = "Session ended"

        elif event_type == "SessionStart":
            session.status = SessionStatus.ACTIVE
            session.ended_at = None
            session.started_at = now
            session.total_events = 0
            session.error_count = 0
            session.modified_files = []
            session.last_narrative = None
            session.last_user_prompt = None
            session.last_command = None
            session.last_command_exit = None
            session.activity_buckets = [0] * BUCKET_COUNT
            session.bucket_start = now
            session.status_text = "Session started"

        # Common updates for all events
        session.last_event_at = now
        session.total_events = (session.total_events or 0) + 1

        # Reactivate if we get an event for a stopped/idle session (except passive events)
        if event_type not in ("Stop", "SessionEnd", "Notification", "SubagentStop") and session.status in (SessionStatus.IDLE, SessionStatus.STOPPED):
            session.status = SessionStatus.ACTIVE
            session.ended_at = None

        # Update activity buckets
        self._update_activity_buckets(session, now)

        await db.flush()

        # Throttled maintenance (idle check + event pruning)
        await self._maybe_run_maintenance(db, now)

        return self._to_response(session)

    async def get_all_sessions(self, db: AsyncSession) -> list[PresenceSessionResponse]:
        now = datetime.now(UTC)
        await self._mark_idle_sessions(db, now)

        result = await db.execute(
            select(PresenceSession).order_by(PresenceSession.last_event_at.desc())
        )
        sessions = result.scalars().all()
        return [self._to_response(s) for s in sessions]

    async def update_label(self, session_id: str, label: str, db: AsyncSession) -> PresenceSessionResponse | None:
        result = await db.execute(
            select(PresenceSession).where(PresenceSession.session_id == session_id)
        )
        session = result.scalar_one_or_none()
        if not session:
            return None
        session.label = label
        await db.flush()
        return self._to_response(session)

    async def remove_session(self, session_id: str, db: AsyncSession) -> bool:
        result = await db.execute(
            delete(PresenceSession).where(PresenceSession.session_id == session_id)
        )
        await db.flush()
        return result.rowcount > 0

    async def clear_all_sessions(self, db: AsyncSession) -> int:
        result = await db.execute(select(func.count()).select_from(PresenceSession))
        count = result.scalar() or 0
        await db.execute(delete(PresenceSession))
        await db.flush()
        return count

    async def _maybe_run_maintenance(self, db: AsyncSession, now: datetime):
        """Run idle check, event pruning, and stopped-session cleanup, throttled."""
        global _last_idle_check, _last_prune

        current = time.monotonic()
        run_idle = False
        run_prune = False

        async with _maintenance_lock:
            if current - _last_idle_check >= IDLE_CHECK_INTERVAL_SECONDS:
                _last_idle_check = current
                run_idle = True
            if current - _last_prune >= 3600:
                _last_prune = current
                run_prune = True

        if run_idle:
            await self._mark_idle_sessions(db, now)
            await self._remove_completed_sessions(db, now)
        if run_prune:
            await self._prune_old_events(db, now)

    async def _mark_idle_sessions(self, db: AsyncSession, now: datetime):
        cutoff = now - timedelta(minutes=IDLE_TIMEOUT_MINUTES)
        result = await db.execute(
            select(PresenceSession).where(
                PresenceSession.status == SessionStatus.ACTIVE,
                PresenceSession.last_event_at < cutoff,
            )
        )
        for session in result.scalars().all():
            session.status = SessionStatus.IDLE
            session.status_text = None

    async def _remove_completed_sessions(self, db: AsyncSession, now: datetime):
        """Auto-remove STOPPED sessions older than retention threshold."""
        cutoff = now - timedelta(minutes=STOPPED_SESSION_RETENTION_MINUTES)
        result = await db.execute(
            select(PresenceSession).where(
                PresenceSession.status == SessionStatus.STOPPED,
                PresenceSession.last_event_at < cutoff,
            )
        )
        sessions = result.scalars().all()
        for session in sessions:
            await db.delete(session)
        if sessions:
            await db.flush()
            for session in sessions:
                msg = json.dumps({"type": "session_remove", "session_id": session.session_id})
                await manager.broadcast(msg)

    async def _prune_old_events(self, db: AsyncSession, now: datetime):
        """Delete presence events older than retention period.
        
        Retention is dynamically adjusted based on memory status.
        """
        limits = get_dynamic_limits()
        cutoff = now - timedelta(hours=limits.event_retention_hours)
        await db.execute(
            delete(PresenceEvent).where(PresenceEvent.timestamp < cutoff)
        )

        # Also enforce max event count
        result = await db.execute(
            select(func.count()).select_from(PresenceEvent)
        )
        event_count = result.scalar() or 0
        if event_count > limits.max_presence_events:
            # Delete oldest events beyond the limit
            excess = event_count - limits.max_presence_events
            subq = (
                select(PresenceEvent.id)
                .order_by(PresenceEvent.timestamp.asc())
                .limit(excess)
                .scalar_subquery()
            )
            await db.execute(
                delete(PresenceEvent).where(PresenceEvent.id.in_(subq))
            )

        await db.flush()

    def _update_activity_buckets(self, session: PresenceSession, now: datetime):
        buckets = list(session.activity_buckets or [0] * BUCKET_COUNT)
        bucket_start = session.bucket_start

        if not bucket_start:
            session.bucket_start = now
            session.activity_buckets = [0] * (BUCKET_COUNT - 1) + [1]
            return

        # Make bucket_start timezone-aware if it isn't
        bucket_start = ensure_aware(bucket_start)

        offset = int((now - bucket_start).total_seconds() / 60)

        if offset >= BUCKET_COUNT:
            shift = offset - BUCKET_COUNT + 1
            buckets = buckets[shift:] + [0] * shift
            session.bucket_start = bucket_start + timedelta(minutes=shift)
            offset = BUCKET_COUNT - 1

        if 0 <= offset < len(buckets):
            buckets[offset] = buckets[offset] + 1

        session.activity_buckets = buckets

    def _derive_label(self, cwd: str) -> str:
        return os.path.basename(cwd.rstrip("/"))

    async def _assign_unique_label(self, base_label: str, session_id: str, db: AsyncSession) -> str:
        """Append a short session_id suffix if another session already uses this label."""
        result = await db.execute(
            select(PresenceSession).where(
                PresenceSession.label == base_label,
                PresenceSession.session_id != session_id,
            )
        )
        existing = result.scalars().all()
        if existing:
            for s in existing:
                s.label = f"{base_label} ({s.session_id[:6]})"
            return f"{base_label} ({session_id[:6]})"
        return base_label

    def _get_file_path(self, entry: str | dict) -> str:
        """Extract path from either a string (legacy) or dict (new format)."""
        if isinstance(entry, str):
            return entry
        return entry.get("path", "")

    def _extract_exit_code(self, tool_result: dict) -> int | None:
        if not tool_result:
            return None
        if "exit_code" in tool_result:
            return tool_result["exit_code"]
        content = tool_result.get("content", "")
        if isinstance(content, str) and "exit code" in content.lower():
            match = re.search(r'exit code[:\s]+(\d+)', content, re.IGNORECASE)
            if match:
                return int(match.group(1))
        if tool_result.get("is_error"):
            return 1
        return 0

    def _to_response(self, session: PresenceSession) -> PresenceSessionResponse:
        return PresenceSessionResponse(
            session_id=session.session_id,
            label=session.label,
            project_path=session.project_path,
            tmux_pane=session.tmux_pane,
            status=session.status,
            status_text=session.status_text,
            last_narrative=session.last_narrative,
            last_narrative_at=session.last_narrative_at.isoformat() if session.last_narrative_at else None,
            modified_files=session.modified_files,
            last_user_prompt=session.last_user_prompt,
            last_command=session.last_command,
            last_command_exit=session.last_command_exit,
            activity_buckets=session.activity_buckets,
            total_events=session.total_events or 0,
            error_count=session.error_count or 0,
            started_at=session.started_at.isoformat() if session.started_at else datetime.now(UTC).isoformat(),
            last_event_at=session.last_event_at.isoformat() if session.last_event_at else datetime.now(UTC).isoformat(),
            ended_at=session.ended_at.isoformat() if session.ended_at else None,
        )
