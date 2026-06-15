"""In-memory per-session pane map + idle state, fed by CC hook events.

Keyed by Claude session_id (not cwd), so concurrent sessions in one working
copy are tracked independently. Idle == a Stop with no later busy event.
"""
import asyncio

_IDLE_EVENTS = {"Stop"}
_BUSY_EVENTS = {"UserPromptSubmit", "SessionStart", "Notification"}


class SessionRegistry:
    def __init__(self) -> None:
        self._panes: dict[str, str] = {}
        self._idle: dict[str, bool] = {}
        self._waiters: dict[str, list[asyncio.Event]] = {}

    def record(self, event: str, session_id: str, cwd: str,
               tmux_pane: str | None = None) -> None:
        if tmux_pane:
            self._panes[session_id] = tmux_pane
        if event in _IDLE_EVENTS:
            self._idle[session_id] = True
            for ev in self._waiters.get(session_id, []):
                ev.set()
        elif event in _BUSY_EVENTS:
            self._idle[session_id] = False

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
        except asyncio.TimeoutError:
            return False
        finally:
            self._waiters.get(session_id, []).remove(ev)


# Module-level singleton (shared by hook endpoint + delivery engine)
session_registry = SessionRegistry()
