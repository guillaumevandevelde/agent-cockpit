"""In-memory per-session pane map + idle state, fed by CC hook events.

Keyed by Claude session_id (not cwd), so concurrent sessions in one working
copy are tracked independently. Idle == a Stop with no later busy event.

Enforces hardware-aware session limits via memory_monitor.
"""
import asyncio
import logging

from app.services.memory_monitor import get_memory_status_cached

logger = logging.getLogger(__name__)

_IDLE_EVENTS = {"Stop"}
_BUSY_EVENTS = {"UserPromptSubmit", "SessionStart", "Notification"}


class SessionRegistry:
    def __init__(self, max_sessions: int | None = None) -> None:
        self._panes: dict[str, str] = {}
        self._idle: dict[str, bool] = {}
        self._waiters: dict[str, list[asyncio.Event]] = {}
        self._external: set[str] = set()  # non-tmux sessions (e.g. sandcastle runs)
        self._max_sessions_override = max_sessions

    @property
    def effective_max_sessions(self) -> int:
        """Get the effective max sessions, considering override and hardware limits."""
        if self._max_sessions_override is not None:
            return self._max_sessions_override
        status = get_memory_status_cached()
        return status.estimated_max_sessions

    @property
    def session_count(self) -> int:
        """Number of tracked sessions, tmux panes plus external reservations."""
        return len(self._panes) + len(self._external)

    def can_add_session(self) -> bool:
        """Check if we can track another session without exceeding limits."""
        return self.session_count < self.effective_max_sessions

    def reserve_external(self, key: str) -> None:
        """Reserve a slot for a non-tmux session (e.g. a sandcastle container run).

        Sandcastle agents run in containers, not tmux, so the hook-fed pane map never
        sees them — yet they consume memory just the same. Reserving here makes them
        count against the shared budget so `can_add_session()` is honest. Idempotent."""
        self._external.add(key)

    def release_external(self, key: str) -> None:
        """Release a previously reserved external slot. No-op for unknown keys."""
        self._external.discard(key)

    def record(self, event: str, session_id: str, cwd: str,
               tmux_pane: str | None = None) -> bool:
        """Record a session event.
        
        Returns True if the event was recorded, False if rejected due to limits.
        """
        # Check limits for new sessions
        if session_id not in self._panes and not self.can_add_session():
            status = get_memory_status_cached()
            logger.warning(
                f"Session limit reached ({self.session_count}/{self.effective_max_sessions}). "
                f"Memory: {status.usage_percent:.0%} used, "
                f"{status.available_bytes / (1024*1024):.0f}MB available. "
                f"Rejecting session {session_id[:8]}..."
            )
            return False

        if tmux_pane:
            self._panes[session_id] = tmux_pane
        if event in _IDLE_EVENTS:
            self._idle[session_id] = True
            for ev in self._waiters.get(session_id, []):
                ev.set()
        elif event in _BUSY_EVENTS:
            self._idle[session_id] = False
        return True

    def cleanup_stale_sessions(self, max_idle_seconds: int = 3600) -> int:
        """Remove sessions that have been idle for too long.
        
        Returns the number of sessions removed.
        """
        import time
        time.monotonic()
        # Sessions only get idle flag set on Stop events, so we track that
        # For now, just report the count - actual cleanup needs timestamp tracking
        return 0

    def pane_for(self, session_id: str) -> str | None:
        return self._panes.get(session_id)

    def is_idle(self, session_id: str) -> bool:
        return self._idle.get(session_id, False)

    async def wait_until_idle(self, session_id: str, timeout_s: float) -> bool:
        if self._idle.get(session_id, False):
            return True
        ev = asyncio.Event()
        self._waiters.setdefault(session_id, []).append(ev)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout_s)
            return True
        except TimeoutError:
            return False
        finally:
            self._waiters.get(session_id, []).remove(ev)


# Module-level singleton (shared by hook endpoint + delivery engine)
session_registry = SessionRegistry()
