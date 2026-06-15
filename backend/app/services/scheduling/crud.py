"""DB CRUD + the function APScheduler calls on fire."""
from datetime import datetime, timezone

from app.database import AsyncSessionLocal
from app.models.scheduled_message import ScheduledMessage, DeliveryAttempt
from app.services.scheduling.delivery import DeliveryEngine

_engine = DeliveryEngine()


async def run_scheduled_delivery(message_id: int) -> None:
    """Called by the scheduler when a job fires."""
    async with AsyncSessionLocal() as s:
        msg = await s.get(ScheduledMessage, message_id)
        if not msg or not msg.enabled:
            return
        # Coalescing: skip if a previous delivery is still pending.
        if msg.status == "pending_delivery":
            return
        msg.status = "pending_delivery"
        msg.last_fired_at = datetime.now(timezone.utc)
        attempt = DeliveryAttempt(scheduled_message_id=msg.id, fired_at=msg.last_fired_at)
        s.add(attempt)
        await s.commit()
        await s.refresh(attempt)

        res = await _engine.deliver(
            project_dir=msg.target_project, message=msg.message,
            permission_mode=msg.permission_mode,
            on_missing_session=msg.on_missing_session, when_busy=msg.when_busy,
            target_kind=msg.target_kind or "project",
            target_session_id=msg.target_session_id,
            project_folder=msg.project_folder,
        )

        attempt.outcome = res.outcome
        attempt.action = res.action
        attempt.resolved_session = res.resolved_session
        attempt.wait_duration_s = res.wait_duration_s
        attempt.error = res.error
        attempt.delivered_at = datetime.now(timezone.utc) if res.outcome == "success" else None
        # once -> terminal; cron -> back to scheduled for next run
        if msg.trigger_type == "cron":
            msg.status = "scheduled"
        else:
            msg.status = "delivered" if res.outcome == "success" else "failed"
        await s.commit()
