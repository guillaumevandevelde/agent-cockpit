"""In-memory per-project idle/busy state, fed by CC hook events.

State is keyed by resolved project cwd. A session is 'idle' after a Stop with no
later UserPromptSubmit. Unknown => treated as busy (caller should not assume idle).
"""
import logging
import asyncio
import os


logger = logging.getLogger(__name__)
_IDLE_EVENTS = {"Stop"}
_BUSY_EVENTS = {"UserPromptSubmit", "SessionStart", "Notification"}


def _norm(path: str) -> str:
    return os.path.normpath(path)


class IdleState:
    def __init__(self) -> None:
        self._idle: dict[str, bool] = {}
        self._waiters: dict[str, list[asyncio.Event]] = {}

    def record(self, event: str, cwd: str, session_id: str) -> None:
        key = _norm(cwd)
        if event in _IDLE_EVENTS:
            self._idle[key] = True
            for ev in self._waiters.get(key, []):
                ev.set()
        elif event in _BUSY_EVENTS:
            self._idle[key] = False

    def is_idle(self, cwd: str) -> bool:
        return self._idle.get(_norm(cwd), False)

    async def wait_until_idle(self, cwd: str, timeout_s: float) -> bool:
        key = _norm(cwd)
        if self._idle.get(key, False):
            return True
        ev = asyncio.Event()
        self._waiters.setdefault(key, []).append(ev)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout_s)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            self._waiters.get(key, []).remove(ev)


# Module-level singleton (shared by hook endpoint + delivery engine)
idle_state = IdleState()
