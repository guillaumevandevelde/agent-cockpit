"""APScheduler wrapper: schedules once/cron jobs that trigger delivery."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_KANBAN_DISPATCH_JOB_ID = "kanban-dispatch"
_STALE_DETECTION_JOB_ID = "portfolio-stale-detection"
_AUTO_BACKUP_JOB_ID = "auto-backup"
_RECURRING_TRIGGER_JOB_PREFIX = "recurring-trigger-"


def _job_id(message_id: int) -> str:
    return f"sched-msg-{message_id}"


def _recurring_trigger_job_id(trigger_id: int) -> str:
    return f"{_RECURRING_TRIGGER_JOB_PREFIX}{trigger_id}"


class SchedulerService:
    def __init__(self) -> None:
        self._sched = AsyncIOScheduler()

    def start(self) -> None:
        if not self._sched.running:
            self._sched.start()

    def shutdown(self) -> None:
        if self._sched.running:
            self._sched.shutdown(wait=False)

    def has_job(self, message_id: int) -> bool:
        return self._sched.get_job(_job_id(message_id)) is not None

    def remove(self, message_id: int) -> None:
        job = self._sched.get_job(_job_id(message_id))
        if job:
            job.remove()

    def schedule_once(self, message_id: int, fire_at_iso: str) -> None:
        self._sched.add_job(
            self._run_delivery,
            trigger=DateTrigger(run_date=datetime.fromisoformat(fire_at_iso)),
            args=[message_id], id=_job_id(message_id), replace_existing=True,
            misfire_grace_time=3600, coalesce=True,
        )

    def schedule_cron(self, message_id: int, cron_expr: str, tz: str) -> None:
        self._sched.add_job(
            self._run_delivery,
            trigger=CronTrigger.from_crontab(cron_expr, timezone=ZoneInfo(tz)),
            args=[message_id], id=_job_id(message_id), replace_existing=True,
            misfire_grace_time=3600, coalesce=True, max_instances=1,
        )

    async def _run_delivery(self, message_id: int) -> None:
        # Imported here to avoid a circular import at module load.
        from app.services.scheduling.crud import run_scheduled_delivery
        try:
            await run_scheduled_delivery(message_id)
        except Exception:
            logger.exception("delivery failed for message %s", message_id)

    def schedule_kanban_dispatch(self, interval_seconds: int = 10) -> None:
        """Poll the kanban board for unclaimed Todo cards on enabled projects."""
        self._sched.add_job(
            self._run_kanban_dispatch,
            trigger=IntervalTrigger(seconds=interval_seconds),
            id=_KANBAN_DISPATCH_JOB_ID, replace_existing=True,
            coalesce=True, max_instances=1, misfire_grace_time=interval_seconds,
        )

    async def _run_kanban_dispatch(self) -> None:
        from app.kanban.dispatch import run_dispatch_tick
        try:
            await run_dispatch_tick()
        except Exception:
            logger.exception("kanban dispatch tick failed")

    def schedule_stale_detection(self, interval_minutes: int = 30) -> None:
        """Periodically flag autodispatch projects whose Backlog has stalled."""
        self._sched.add_job(
            self._run_stale_detection,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id=_STALE_DETECTION_JOB_ID, replace_existing=True,
            coalesce=True, max_instances=1, misfire_grace_time=interval_minutes * 60,
        )

    async def _run_stale_detection(self) -> None:
        from app.kanban.stale_detection import run_stale_detection_tick
        try:
            await run_stale_detection_tick()
        except Exception:
            logger.exception("stale-project detection tick failed")

    def has_auto_backup(self) -> bool:
        return self._sched.get_job(_AUTO_BACKUP_JOB_ID) is not None

    def remove_auto_backup(self) -> None:
        job = self._sched.get_job(_AUTO_BACKUP_JOB_ID)
        if job:
            job.remove()

    # --- recurring-trigger (cron → kanban card) jobs ---

    def has_recurring_trigger(self, trigger_id: int) -> bool:
        return self._sched.get_job(_recurring_trigger_job_id(trigger_id)) is not None

    def remove_recurring_trigger(self, trigger_id: int) -> None:
        job = self._sched.get_job(_recurring_trigger_job_id(trigger_id))
        if job:
            job.remove()

    def schedule_recurring_trigger(
        self, trigger_id: int, cron_expr: str, tz: str,
    ) -> None:
        """Register a cron job whose callback fires the trigger.

        ``misfire_grace_time=3600`` is APScheduler's safety net within a
        running process; it does NOT survive process death. The
        process-death case (the gemiste maandag of 2026-08-03) is handled
        by ``services.recurring_triggers.run_boot_inhaal`` instead.
        """
        self._sched.add_job(
            self._run_recurring_trigger,
            trigger=CronTrigger.from_crontab(cron_expr, timezone=ZoneInfo(tz)),
            args=[trigger_id],
            id=_recurring_trigger_job_id(trigger_id),
            replace_existing=True,
            misfire_grace_time=3600, coalesce=True, max_instances=1,
        )

    async def _run_recurring_trigger(self, trigger_id: int) -> None:
        from app.services.recurring_triggers import fire_trigger_by_id
        try:
            await fire_trigger_by_id(trigger_id)
        except Exception:
            logger.exception("recurring trigger %s failed", trigger_id)

    def schedule_auto_backup(self, time_of_day: str, tz: str) -> None:
        """Schedule a daily automatic backup at HH:MM in the given timezone."""
        hour, minute = (int(part) for part in time_of_day.split(":"))
        self._sched.add_job(
            self._run_auto_backup,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=ZoneInfo(tz)),
            id=_AUTO_BACKUP_JOB_ID, replace_existing=True,
            coalesce=True, max_instances=1, misfire_grace_time=3600,
        )

    async def _run_auto_backup(self) -> None:
        from app.services.auto_backup_service import run_auto_backup_job
        try:
            await run_auto_backup_job()
        except Exception:
            logger.exception("automatic backup job failed")


# Module-level singleton
scheduler_service = SchedulerService()
