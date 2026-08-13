"""Hook ingest and auto-resume endpoints.

The old ``/scheduled-messages/`` prefix also carried the Claude Code
lifecycle-hook ingest (``/hook-event``), the hook install/status pair, and
the auto-resume toggle. None of those were scheduled-messages concerns;
they sit on top of the shared session substrate (``idle_state``,
``session_registry``, ``session_signals``, ``auto_resume``) and feed
kanban-dispatch. The endpoints moved here so the URL line
up with what they do, and the scheduled-messages feature itself could
be deleted (see ``docs/cockpit/scheduled-trigger-consolidatie-decision.md``
§5.2).
"""
import logging

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.scheduling.auto_resume import auto_resume_service
from app.services.scheduling.hook_installer import get_hooks_status, install_missing_hooks
from app.services.scheduling.idle_state import idle_state
from app.services.scheduling.session_registry import session_registry
from app.services.scheduling.session_signals import session_signals

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/session-hooks", tags=["Session Hooks"])


class HookEvent(BaseModel):
    """Posted by the CC hook script."""
    event: str
    session_id: str
    cwd: str
    tmux_pane: str | None = None
    message: str | None = None
    notification_type: str | None = None


@router.post("/hook-event")
async def hook_event(ev: HookEvent):
    idle_state.record(ev.event, cwd=ev.cwd, session_id=ev.session_id)
    session_registry.record(ev.event, session_id=ev.session_id, cwd=ev.cwd,
                            tmux_pane=ev.tmux_pane)

    # Feed the structured-signal pipeline (see ``session_signals.py``) so the
    # reaper's pane scrape and the delivery engine's readiness poll can be
    # replaced with typed lookups. SessionStart is recorded immediately; the
    # limit-signal branch lives inside the Notification block below so we
    # classify exactly once and reuse the result for both the kanban move +
    # dispatch-pause path and the structured-signal registry.
    if ev.event == "SessionStart":
        session_signals.record_started(ev.cwd)

    if ev.event == "Notification":
        kind = auto_resume_service.classify_notification(
            message=ev.message, notification_type=ev.notification_type,
        )

        if kind == "limit":
            # Kanban-dispatched session hit its usage limit and is stuck open: pause
            # the affected provider and move its card to "To Resume", killing the
            # tmux session now rather than leaving it dangling until a human
            # notices. Shared with the transcript-tail sweep
            # (`detect_transcript_rate_limits`) so a limit reaches the exact same
            # reaction regardless of which channel noticed it first -- see
            # `handle_rate_limit_signal`'s docstring.
            from app.kanban.dispatch import handle_rate_limit_signal
            try:
                await handle_rate_limit_signal(ev.cwd, ev.message or "", source="hook")
            except Exception:
                logger.exception("failed to handle rate-limit signal for %s", ev.cwd)

            # Auto-resume: schedule a resume job for the projects that opted
            # in explicitly (independent of the kanban path).
            parsed = auto_resume_service.parse_reset_time(ev.message)
            if parsed and auto_resume_service.is_enabled(ev.cwd):
                reset_time, tz_name = parsed
                auto_resume_service.schedule_resume(
                    cwd=ev.cwd,
                    reset_time=reset_time,
                    tz_name=tz_name,
                    session_id=ev.session_id,
                )

        elif kind in ("needs_input", "completed"):
            # New background-agent subtypes (Claude Code 2.1.198+, Jul 2026).
            # Surface the event as a kanban activity comment so the operator
            # sees "agent waiting" / "agent finished" on the card, but do NOT
            # move the card — the explicit human/engineer move to Done stays
            # authoritative (matches the rate-limit design where
            # `move_limited_session_to_resume` is the only auto-move and
            # only on a real rate-limit hit). No-op for non-kanban sessions.
            from app.kanban.dispatch import post_agent_status_comment
            text = (
                "Session is waiting for input"
                if kind == "needs_input"
                else "Session reported completion"
            )
            try:
                await post_agent_status_comment(ev.cwd, text)
            except Exception:
                logger.exception(
                    "failed to post agent-status comment to kanban card for %s",
                    ev.cwd,
                )

        # else "other" → drop silently (permission_prompt / idle_prompt /
        # auth_success / elicitation_* / unknown payloads), same as before.

    return {"ok": True}


@router.get("/hooks-status")
async def hooks_status():
    """Per-event install status for the CC hooks that feed this pipeline.

    Each event is one of ``missing`` (no entry), ``stale`` (an entry exists
    but its command differs from what the current renderer emits), or
    ``installed`` (exact match). Without them, ``/hook-event`` above is never
    called, so limit detection and auto-resume are silently dead.

    The aggregate ``installed`` flag is only true when every event is
    ``installed``; ``stale`` events are listed under ``stale`` so the
    frontend/installer can offer a "reinstall" action.
    """
    events = get_hooks_status()
    stale = [event for event, status in events.items() if status == "stale"]
    return {
        "events": events,
        "stale": stale,
        "installed": all(status == "installed" for status in events.values()),
    }


@router.post("/hooks-install")
async def hooks_install():
    """Additively install any missing session-hooks in ~/.claude/settings.json.

    Reinstalling the event for which the entry was ``stale`` is the
    expected flow: ``install_missing_hooks`` only appends missing events, so
    to clear a ``stale`` status the client should first drop the affected
    event from ``~/.claude/settings.json`` (or use the equivalent admin
    helper) and then call this endpoint.
    """
    events = install_missing_hooks()
    stale = [event for event, status in events.items() if status == "stale"]
    return {
        "events": events,
        "stale": stale,
        "installed": all(status == "installed" for status in events.values()),
    }


# --- Auto-resume endpoints ---

@router.get("/auto-resume/{cwd:path}")
async def get_auto_resume(cwd: str):
    """Check if auto-resume is enabled for a project."""
    return {
        "cwd": cwd,
        "enabled": auto_resume_service.is_enabled(cwd),
    }


@router.post("/auto-resume/{cwd:path}")
async def set_auto_resume(cwd: str, enabled: bool = True):
    """Enable or disable auto-resume for a project."""
    auto_resume_service.set_enabled(cwd, enabled)
    return {
        "cwd": cwd,
        "enabled": enabled,
    }


@router.delete("/auto-resume/{cwd:path}")
async def cancel_auto_resume(cwd: str):
    """Cancel a pending auto-resume for a project."""
    cancelled = auto_resume_service.cancel(cwd)
    return {"cwd": cwd, "cancelled": cancelled}
