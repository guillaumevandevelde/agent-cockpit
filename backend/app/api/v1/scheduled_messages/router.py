"""REST API for scheduled messages + CC hook ingest."""
import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.scheduled_message import DeliveryAttempt, ScheduledMessage
from app.models.scheduled_message_schemas import (
    BulkDeleteRequest,
    DeliveryAttemptResponse,
    HookEvent,
    ScheduledMessageCreate,
    ScheduledMessageResponse,
    ScheduledMessageUpdate,
)
from app.services.scheduling.auto_resume import auto_resume_service
from app.services.scheduling.hook_installer import get_hooks_status, install_missing_hooks
from app.services.scheduling.idle_state import idle_state
from app.services.scheduling.scheduler import scheduler_service
from app.services.scheduling.session_registry import session_registry
from app.services.scheduling.session_signals import session_signals

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scheduled-messages", tags=["Scheduled Messages"])


def _register(msg: ScheduledMessage) -> None:
    if not msg.enabled:
        scheduler_service.remove(msg.id)
        return
    if msg.trigger_type == "once" and msg.fire_at:
        scheduler_service.schedule_once(msg.id, msg.fire_at)
    elif msg.trigger_type == "cron" and msg.cron_expr:
        scheduler_service.schedule_cron(msg.id, msg.cron_expr, msg.timezone)


@router.post("", response_model=ScheduledMessageResponse, status_code=status.HTTP_201_CREATED)
async def create(payload: ScheduledMessageCreate):
    async with AsyncSessionLocal() as s:
        msg = ScheduledMessage(**payload.model_dump())
        s.add(msg)
        await s.commit()
        await s.refresh(msg)
        _register(msg)
        return ScheduledMessageResponse.model_validate(msg)


@router.get("")
async def list_all():
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(ScheduledMessage).order_by(ScheduledMessage.id.desc())
        )).scalars().all()
        return {"items": [ScheduledMessageResponse.model_validate(m) for m in rows]}


@router.get("/{mid}/attempts", response_model=list[DeliveryAttemptResponse])
async def attempts(mid: int):
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.scheduled_message_id == mid)
            .order_by(DeliveryAttempt.id.desc())
        )).scalars().all()
        return rows


@router.patch("/{mid}", response_model=ScheduledMessageResponse)
async def update(mid: int, payload: ScheduledMessageUpdate):
    async with AsyncSessionLocal() as s:
        msg = await s.get(ScheduledMessage, mid)
        if not msg:
            raise HTTPException(404, "not found")
        for k, v in payload.model_dump(exclude_unset=True).items():
            setattr(msg, k, v)
        await s.commit()
        await s.refresh(msg)
        _register(msg)
        return ScheduledMessageResponse.model_validate(msg)


_TERMINAL_STATUSES = ("delivered", "failed", "cancelled")


@router.delete("/history")
async def delete_history():
    """Delete all messages in terminal states (delivered, failed, cancelled)."""
    async with AsyncSessionLocal() as s:
        id_rows = await s.execute(
            select(ScheduledMessage.id)
            .where(ScheduledMessage.status.in_(_TERMINAL_STATUSES))
        )
        ids = id_rows.scalars().all()
        if ids:
            await s.execute(
                sa_delete(DeliveryAttempt)
                .where(DeliveryAttempt.scheduled_message_id.in_(ids))
            )
            await s.execute(
                sa_delete(ScheduledMessage)
                .where(ScheduledMessage.id.in_(ids))
            )
            await s.commit()
    for mid in ids:
        scheduler_service.remove(mid)
    return {"deleted": len(ids)}


@router.post("/bulk-delete")
async def bulk_delete(payload: BulkDeleteRequest):
    """Delete an arbitrary set of messages (any status) by id."""
    if not payload.ids:
        return {"deleted": 0}
    async with AsyncSessionLocal() as s:
        id_rows = await s.execute(
            select(ScheduledMessage.id)
            .where(ScheduledMessage.id.in_(payload.ids))
        )
        ids = id_rows.scalars().all()
        if ids:
            await s.execute(
                sa_delete(DeliveryAttempt)
                .where(DeliveryAttempt.scheduled_message_id.in_(ids))
            )
            await s.execute(
                sa_delete(ScheduledMessage)
                .where(ScheduledMessage.id.in_(ids))
            )
            await s.commit()
    for mid in ids:
        scheduler_service.remove(mid)
    return {"deleted": len(ids)}


@router.delete("/{mid}")
async def delete(mid: int):
    async with AsyncSessionLocal() as s:
        msg = await s.get(ScheduledMessage, mid)
        if not msg:
            raise HTTPException(404, "not found")
        scheduler_service.remove(mid)
        await s.delete(msg)
        await s.commit()
        return {"deleted": True}


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

            # Auto-resume: schedule a resume job for the scheduled-messages feature,
            # for projects that opted in explicitly (independent of the kanban path).
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
    """Whether the four CC hooks that feed this pipeline are installed.

    Without them, ``/hook-event`` above is never called, so limit detection
    and auto-resume are silently dead — see
    docs/cockpit/analyse-sessie-limieten-claude-code.md.
    """
    events = get_hooks_status()
    return {"events": events, "installed": all(events.values())}


@router.post("/hooks-install")
async def hooks_install():
    """Additively install any missing scheduling hooks in ~/.claude/settings.json."""
    events = install_missing_hooks()
    return {"events": events, "installed": all(events.values())}
