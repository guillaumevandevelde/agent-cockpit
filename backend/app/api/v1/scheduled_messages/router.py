"""REST API for scheduled messages + CC hook ingest."""
import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, delete as sa_delete

from app.database import AsyncSessionLocal
from app.models.scheduled_message import ScheduledMessage, DeliveryAttempt
from app.models.scheduled_message_schemas import (
    ScheduledMessageCreate, ScheduledMessageUpdate, ScheduledMessageResponse,
    DeliveryAttemptResponse, HookEvent,
)
from app.services.scheduling.auto_resume import auto_resume_service
from app.services.scheduling.idle_state import idle_state
from app.services.scheduling.session_registry import session_registry
from app.services.scheduling.scheduler import scheduler_service

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

    if ev.event == "Notification" and auto_resume_service.is_limit_notification(ev.message):
        # Kanban-dispatched session hit its usage limit and is stuck open: move its
        # card to "To Resume" and kill the tmux session now, rather than leaving it
        # dangling until a human notices. No-op for non-kanban sessions.
        from app.kanban.dispatch import move_limited_session_to_resume
        try:
            await move_limited_session_to_resume(ev.cwd)
        except Exception:
            logger.exception("failed to move kanban card to To Resume for %s", ev.cwd)

        parsed = auto_resume_service.parse_reset_time(ev.message)

        # The usage limit is account-wide: every session hits the same wall for
        # the rest of the reset window. Pause the whole auto-dispatch tick (every
        # project on this device) until then, so it doesn't keep respawning "To
        # Resume" cards every ~10s only to immediately re-hit the same limit.
        if parsed:
            reset_time, _tz_name = parsed
            from app.kanban.db import KanbanSessionLocal
            from app.kanban.dispatch_pause import set_paused_until
            try:
                async with KanbanSessionLocal() as ks:
                    await set_paused_until(ks, reset_time)
                    await ks.commit()
            except Exception:
                logger.exception("failed to set global dispatch pause for %s", ev.cwd)

        # Auto-resume: schedule a resume job for the scheduled-messages feature,
        # for projects that opted in explicitly (independent of the kanban path).
        if parsed and auto_resume_service.is_enabled(ev.cwd):
            reset_time, tz_name = parsed
            auto_resume_service.schedule_resume(
                cwd=ev.cwd,
                reset_time=reset_time,
                tz_name=tz_name,
                session_id=ev.session_id,
            )

    return {"ok": True}
