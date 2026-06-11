"""Resolve target -> spawn if needed -> wait until idle -> inject."""
import logging
import time
from dataclasses import dataclass

from app.services.scheduling.idle_state import IdleState, idle_state as default_idle
from app.services.scheduling.session_resolver import resolve_target, spawn_for
from app.services.scheduling.tmux_inject import send_text

logger = logging.getLogger(__name__)


@dataclass
class DeliveryResult:
    outcome: str                       # success | failed | timeout
    action: str | None = None          # used_existing | spawned
    resolved_session: str | None = None
    wait_duration_s: int = 0
    error: str | None = None


class DeliveryEngine:
    def __init__(self, idle_state: IdleState | None = None):
        self.idle = idle_state or default_idle

    async def deliver(self, *, project_dir: str, message: str,
                      permission_mode: str = "acceptEdits",
                      on_missing_session: str = "spawn",
                      when_busy: str = "wait_until_idle",
                      timeout_s: float = 1800) -> DeliveryResult:
        target = resolve_target(project_dir)
        action = "used_existing"
        if target is None:
            if on_missing_session == "skip":
                return DeliveryResult(outcome="failed", error="no live session (skip)")
            try:
                target = spawn_for(project_dir, permission_mode)
                action = "spawned"
            except Exception as e:  # spawn_session raises ValueError
                return DeliveryResult(outcome="failed", error=f"spawn failed: {e}")

        wait_start = time.monotonic()
        if when_busy == "wait_until_idle" and not self.idle.is_idle(project_dir):
            became_idle = await self.idle.wait_until_idle(project_dir, timeout_s)
            if not became_idle:
                return DeliveryResult(outcome="timeout", action=action,
                                      resolved_session=target,
                                      wait_duration_s=int(time.monotonic() - wait_start))

        ok = send_text(target, message)
        waited = int(time.monotonic() - wait_start)
        if ok:
            return DeliveryResult(outcome="success", action=action,
                                  resolved_session=target, wait_duration_s=waited)
        return DeliveryResult(outcome="failed", action=action, resolved_session=target,
                              wait_duration_s=waited, error="send-keys failed")
