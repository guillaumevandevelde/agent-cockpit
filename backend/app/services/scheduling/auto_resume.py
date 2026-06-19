"""Auto-resume sessions that hit their rate limit.

Detects "You've hit your session limit" notifications, parses the reset time,
and schedules a resume job at that time. When the job fires, spawns a new
session (or resumes) and injects a continuation message.
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.triggers.date import DateTrigger

logger = logging.getLogger(__name__)

# Pattern: "You've hit your session limit · resets 11:10pm (Europe/Brussels)"
_LIMIT_PATTERN = re.compile(
    r"hit your session limit.*?resets\s+(\d{1,2}:\d{2}(?:am|pm)?)\s*\(([^)]+)\)",
    re.IGNORECASE,
)

# Default continuation message when auto-resuming
DEFAULT_RESUME_MESSAGE = "Continue where you left off."


class AutoResumeService:
    def __init__(self) -> None:
        self._scheduled: dict[str, str] = {}  # cwd -> job_id
        self._enabled: dict[str, bool] = {}   # cwd -> enabled

    def is_enabled(self, cwd: str) -> bool:
        return self._enabled.get(cwd, False)

    def set_enabled(self, cwd: str, enabled: bool) -> None:
        self._enabled[cwd] = enabled

    def is_limit_notification(self, message: str | None) -> bool:
        """Check if a notification message indicates a session rate limit."""
        if not message:
            return False
        return "hit your session limit" in message.lower()

    def parse_reset_time(self, message: str | None) -> tuple[datetime, str] | None:
        """Parse reset time and timezone from notification message.

        Returns (datetime, timezone_name) or None if parsing fails.
        """
        if not message:
            return None
        match = _LIMIT_PATTERN.search(message)
        if not match:
            return None

        time_str = match.group(1)
        tz_name = match.group(2)

        try:
            tz = ZoneInfo(tz_name)
        except (KeyError, ValueError):
            logger.warning("Unknown timezone in limit notification: %s", tz_name)
            return None

        # Parse time (12h or 24h format)
        now = datetime.now(tz)
        try:
            if "am" in time_str.lower() or "pm" in time_str.lower():
                # 12h format: "11:10pm"
                parsed = datetime.strptime(time_str.lower(), "%I:%M%p")
                reset_time = now.replace(
                    hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0
                )
            else:
                # 24h format: "23:10"
                parsed = datetime.strptime(time_str, "%H:%M")
                reset_time = now.replace(
                    hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0
                )
        except ValueError:
            logger.warning("Could not parse time '%s' from limit notification", time_str)
            return None

        # If reset time is in the past, it's tomorrow
        if reset_time <= now:
            reset_time += timedelta(days=1)

        return reset_time, tz_name

    def schedule_resume(
        self,
        cwd: str,
        reset_time: datetime,
        tz_name: str,
        message: str = DEFAULT_RESUME_MESSAGE,
        session_id: str | None = None,
        project_folder: str | None = None,
    ) -> str:
        """Schedule an auto-resume job at the given reset time.

        Returns the job_id for tracking.
        """
        from app.services.scheduling.scheduler import scheduler_service

        job_id = f"auto-resume-{hash(cwd) % 100000}"

        # Remove any existing job for this cwd
        if cwd in self._scheduled:
            old_job_id = self._scheduled[cwd]
            try:
                scheduler_service._sched.remove_job(old_job_id)
            except Exception:
                pass

        scheduler_service._sched.add_job(
            self._execute_resume,
            trigger=DateTrigger(run_date=reset_time),
            args=[cwd, message, session_id, project_folder],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=True,
        )

        self._scheduled[cwd] = job_id
        logger.info(
            "Scheduled auto-resume for %s at %s %s (job=%s)",
            cwd, reset_time.isoformat(), tz_name, job_id,
        )
        return job_id

    async def _execute_resume(
        self,
        cwd: str,
        message: str,
        session_id: str | None,
        project_folder: str | None,
    ) -> None:
        """Execute the auto-resume: spawn session and inject message."""
        from app.services.scheduling.delivery import DeliveryEngine
        from app.services.scheduling.session_resolver import resolve_target

        logger.info("Executing auto-resume for %s", cwd)

        engine = DeliveryEngine()

        # Check if there's already a live session
        target = resolve_target(cwd)
        if target is not None:
            # Session exists, just inject the continuation message
            from app.services.scheduling.tmux_inject import send_text
            ok = send_text(target, message)
            if ok:
                logger.info("Auto-resume: injected continuation into existing session %s", target)
            else:
                logger.warning("Auto-resume: failed to inject into session %s", target)
            return

        # No live session, spawn a new one
        try:
            result = await engine.deliver(
                project_dir=cwd,
                message=message,
                permission_mode="acceptEdits",
                on_missing_session="spawn",
                when_busy="send_now",
                timeout_s=60,
                target_kind="session" if session_id else "project",
                target_session_id=session_id,
                project_folder=project_folder,
            )
            if result.outcome == "success":
                logger.info("Auto-resume: spawned and injected for %s", cwd)
            else:
                logger.warning("Auto-resume: delivery failed for %s: %s", cwd, result.error)
        except Exception as e:
            logger.exception("Auto-resume failed for %s: %s", cwd, e)

    def cancel(self, cwd: str) -> bool:
        """Cancel a pending auto-resume for a project."""
        from app.services.scheduling.scheduler import scheduler_service

        if cwd not in self._scheduled:
            return False

        job_id = self._scheduled.pop(cwd)
        try:
            scheduler_service._sched.remove_job(job_id)
            logger.info("Cancelled auto-resume for %s (job=%s)", cwd, job_id)
            return True
        except Exception:
            return False


# Module-level singleton
auto_resume_service = AutoResumeService()
