"""Resolve target -> spawn if needed -> wait until ready/idle -> inject."""
import logging
import time
from dataclasses import dataclass

from app.services.scheduling.idle_state import IdleState, idle_state as default_idle
from app.services.scheduling.session_registry import SessionRegistry, session_registry
from app.services.scheduling.session_resolver import (
    resolve_target, spawn_for, resolve_session_target, resume_spawn_for, AMBIGUOUS,
)
from app.services.scheduling.tmux_inject import send_text, wait_for_pane_ready

logger = logging.getLogger(__name__)


@dataclass
class DeliveryResult:
    outcome: str                       # success | failed | timeout
    action: str | None = None          # used_existing | spawned
    resolved_session: str | None = None
    wait_duration_s: int = 0
    error: str | None = None


class DeliveryEngine:
    def __init__(self, idle_state: IdleState | None = None,
                 registry: SessionRegistry | None = None):
        self.idle = idle_state or default_idle
        self.registry = registry or session_registry

    async def deliver(self, *, project_dir: str, message: str,
                      permission_mode: str = "acceptEdits",
                      on_missing_session: str = "spawn",
                      when_busy: str = "wait_until_idle",
                      timeout_s: float = 1800,
                      target_kind: str = "project",
                      target_session_id: str | None = None,
                      project_folder: str | None = None,
                      spawn_ready_timeout_s: float = 30.0) -> DeliveryResult:
        if target_kind == "session":
            return await self._deliver_session(
                session_id=target_session_id, project_folder=project_folder,
                cwd=project_dir, message=message, permission_mode=permission_mode,
                when_busy=when_busy, timeout_s=timeout_s,
                spawn_ready_timeout_s=spawn_ready_timeout_s,
            )
        target = resolve_target(project_dir)
        wait_start = time.monotonic()
        if target is not None:
            action = "used_existing"
            if when_busy == "wait_until_idle" and not self.idle.is_idle(project_dir):
                became_idle = await self.idle.wait_until_idle(project_dir, timeout_s)
                if not became_idle:
                    return DeliveryResult(outcome="timeout", action=action,
                                          resolved_session=target,
                                          wait_duration_s=int(time.monotonic() - wait_start))
        else:
            if on_missing_session == "skip":
                return DeliveryResult(outcome="failed", error="no live session (skip)")
            try:
                target = spawn_for(project_dir, permission_mode)
            except Exception as e:  # spawn_session raises ValueError
                return DeliveryResult(outcome="failed", error=f"spawn failed: {e}")
            action = "spawned"
            # A freshly spawned claude needs a few seconds to render its TUI; the
            # stale cwd-keyed idle state is meaningless for it, so wait for the
            # pane to actually be ready before injecting (else keystrokes vanish).
            if not await wait_for_pane_ready(target, timeout_s=spawn_ready_timeout_s):
                return DeliveryResult(outcome="timeout", action=action,
                                      resolved_session=target,
                                      wait_duration_s=int(time.monotonic() - wait_start),
                                      error="spawned session never became ready")

        ok = send_text(target, message)
        waited = int(time.monotonic() - wait_start)
        if ok:
            return DeliveryResult(outcome="success", action=action,
                                  resolved_session=target, wait_duration_s=waited)
        return DeliveryResult(outcome="failed", action=action, resolved_session=target,
                              wait_duration_s=waited, error="send-keys failed")

    async def _deliver_session(self, *, session_id: str, project_folder: str | None,
                               cwd: str, message: str, permission_mode: str,
                               when_busy: str, timeout_s: float,
                               spawn_ready_timeout_s: float) -> DeliveryResult:
        target = resolve_session_target(session_id, cwd)
        if target is AMBIGUOUS:
            return DeliveryResult(
                outcome="failed",
                error="ambiguous live sessions in cwd; cannot safely resume",
            )

        wait_start = time.monotonic()
        if target is not None:
            action = "used_existing"
            if when_busy == "wait_until_idle" and not self.registry.is_idle(session_id):
                became_idle = await self.registry.wait_until_idle(session_id, timeout_s)
                if not became_idle:
                    return DeliveryResult(
                        outcome="timeout", action=action, resolved_session=target,
                        wait_duration_s=int(time.monotonic() - wait_start),
                    )
        else:
            try:
                target = resume_spawn_for(session_id, project_folder, cwd, permission_mode)
            except Exception as e:  # resume spawn raises ValueError
                return DeliveryResult(outcome="failed", error=f"resume spawn failed: {e}")
            action = "resumed"
            # A `--resume`d session fires no Stop hook, so wait for its TUI to
            # render rather than guessing with a fixed settle delay.
            if not await wait_for_pane_ready(target, timeout_s=spawn_ready_timeout_s):
                return DeliveryResult(outcome="timeout", action=action,
                                      resolved_session=target,
                                      wait_duration_s=int(time.monotonic() - wait_start),
                                      error="resumed session never became ready")

        ok = send_text(target, message)
        waited = int(time.monotonic() - wait_start)
        if ok:
            return DeliveryResult(outcome="success", action=action,
                                  resolved_session=target, wait_duration_s=waited)
        return DeliveryResult(outcome="failed", action=action, resolved_session=target,
                              wait_duration_s=waited, error="send-keys failed")
