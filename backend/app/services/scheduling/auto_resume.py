"""Auto-resume sessions that hit their rate limit.

Detects "You've hit your session limit" notifications, parses the reset time,
and schedules a resume job at that time. When the job fires, spawns a new
session (or resumes) and injects a continuation message.
"""
import logging
import re
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from apscheduler.triggers.date import DateTrigger

logger = logging.getLogger(__name__)

# Pattern: "You've hit your session or weekly limit · resets 11:10pm (Europe/Brussels)"
#
# The weekly variant prefixes the clock time with a date -- "resets Aug 3, 7pm
# (Europe/Brussels)" -- because a weekly reset can be days away. That optional
# `<month> <day>,` group is what the pattern grew for; without it the whole
# message failed to parse and every caller fell back to the blind
# FALLBACK_PAUSE_HOURS guess (see `parse_reset_time`).
# Both gaps are BOUNDED (`.{0,40}?`, not `.*?`). The message is provider- and
# CLI-supplied, so it is untrusted input; with unbounded lazy gaps `search`
# did O(n) work at each of O(n) start positions, i.e. quadratic
# (py/polynomial-redos, alert 251 — measured 0.32s at 4k reps, 1.35s at 8k).
# Bounding caps per-position work at a constant and makes the search linear.
# 40 is far wider than the real gaps ("session" = 7, " · " = 3); see
# TestLimitPatternRedos in tests/test_auto_resume.py.
_LIMIT_PATTERN = re.compile(
    r"hit your .{0,40}? limit.{0,40}?resets\s+"
    r"(?:(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+)?"
    r"(?P<time>\d{1,2}(?::\d{2})?(?:am|pm)?)\s*\((?P<tz>[^)]+)\)",
    re.IGNORECASE,
)

# How far a dated reset may sit from "now" before we assume the notification
# means the neighbouring year (Dec 31 -> Jan 1 and back). Half a year: a limit
# reset is always days away, never months.
_YEAR_ROLLOVER_WINDOW_DAYS = 180


def _resolve_year(month: int, day: int, now: datetime) -> int:
    """Pick the year for a year-less `<month> <day>` from a limit notification.

    Anthropic's wording carries no year, so "Jan 1" seen on Dec 31 means *next*
    year and "Dec 31" seen on Jan 1 means *last* year. Returns the year whose
    month/day lands nearest `now`, preferring the current year on a tie.
    """
    for year in (now.year, now.year + 1, now.year - 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue  # e.g. Feb 29 in a non-leap year
        if abs((candidate - now.date()).days) <= _YEAR_ROLLOVER_WINDOW_DAYS:
            return year
    return now.year

# Default continuation message when auto-resuming
DEFAULT_RESUME_MESSAGE = "Continue where you left off."

# Conservative dispatch-pause duration when a limit notification is recognized
# but its reset time can't be parsed (e.g. a weekly/model cap with different
# wording). Same as usage_service.UsageService.SESSION_DURATION_HOURS, the
# length of a Claude billing block -- guessing too short just re-triggers the
# same spin-and-burn loop the pause exists to prevent.
FALLBACK_PAUSE_HOURS = 5

# Notification classification for the Claude Code Notification hook (Claude Code
# 2.1.198+, Jul 2026). The new `agent_needs_input` and `agent_completed`
# notification_type values are for background agents run via `claude agents` —
# they let external systems surface a "needs attention" badge or detect a
# finished session that the human/engineer hasn't moved to Done yet.
NotificationKind = Literal["limit", "needs_input", "completed", "other"]


class AutoResumeService:
    def __init__(self) -> None:
        self._scheduled: dict[str, str] = {}  # cwd -> job_id
        self._enabled: dict[str, bool] = {}   # cwd -> enabled

    def is_enabled(self, cwd: str) -> bool:
        return self._enabled.get(cwd, False)

    def set_enabled(self, cwd: str, enabled: bool) -> None:
        self._enabled[cwd] = enabled

    def is_limit_notification(self, message: str | None) -> bool:
        """Check if a notification message indicates a session rate limit.

        Recognises the canonical "hit your session limit" and "hit your weekly
        limit" wording plus provider-specific alternatives: a Minimax subscription can also
        report a 429 / "Token Plan" limit, an "API Error" with "429",
        or a "request rejected" / "usage limit" notice — all of which
        mean the same thing operationally: every session on this device
        will keep hitting the wall, and the global dispatch pause should
        kick in. Without these alternates the hook path silently dropped
        Minimax limit notifications, so the reaper's capture-pane scan was
        the only thing still catching them — see kanban card
        "Backend: reaper detecteert+ruimt stuck sessies op".
        """
        return self.classify_notification(message=message) == "limit"

    def classify_notification(
        self,
        *,
        message: str | None = None,
        notification_type: str | None = None,
    ) -> NotificationKind:
        """Bucket a Notification hook payload so the router can branch on intent.

        The Claude Code Notification hook (CC 2.1.198+, Jul 2026) carries an
        explicit `notification_type` for the new background-agent subtypes
        ``agent_needs_input`` and ``agent_completed`` (template strings:
        ``"<label> needs your input"`` and ``"<label> finished"`` / ``"failed"``).
        Pre-2.1.198 payloads only have a free-text ``message``, so this also
        substring-matches that wording as a fallback for older hook scripts
        and for provider-specific variants.

        Returns one of:
          - ``"limit"`` — rate-limit hit; router triggers the global dispatch
            pause + ``To Resume`` move (existing behaviour).
          - ``"needs_input"`` — background agent is waiting on the user;
            router posts a card activity comment so the operator can see the
            card needs attention.
          - ``"completed"`` — background agent finished (succeeded or failed);
            router posts a card activity comment. The card is NOT auto-moved
            to Done; the explicit human/engineer move stays authoritative.
          - ``"other"`` — anything else (permission_prompt, idle_prompt,
            auth_success, elicitation_*, empty/unknown payloads); router
            drops these silently, same as today.

        Limit detection runs first: a limit hit must not be shadowed by the
        needs_input branch even if a provider ever merges both into a single
        payload.

        When ``notification_type`` is set, it takes precedence over the
        message-substring fallback for the agent buckets — otherwise a
        permission_prompt / elicitation_dialog (whose template also contains
        "Claude needs your input") would be misclassified as needs_input.
        """
        text = (message or "").lower()
        if "hit your session limit" in text or "hit your weekly limit" in text or any(
            needle in text
            for needle in (
                "api error",
                "429",
                "token plan",
                "usage limit",
                "request rejected",
            )
        ):
            return "limit"

        if notification_type == "agent_needs_input":
            return "needs_input"
        if notification_type == "agent_completed":
            return "completed"
        # Any explicit non-agent notification_type (permission_prompt,
        # idle_prompt, auth_success, elicitation_*) means the structured
        # field is authoritative — don't let a "Claude needs your input"
        # substring in the message flip us into the needs_input bucket.
        if notification_type:
            return "other"

        if "needs your input" in text:
            return "needs_input"
        if "finished" in text or "failed" in text:
            # CC's background-agent completion template is "<label> finished"
            # on success and "<label> failed" on failure. Both surface under
            # agent_completed; the operator reads the message to see which.
            return "completed"

        return "other"

    def parse_reset_time(
        self, message: str | None, *, now: datetime | None = None,
    ) -> tuple[datetime, str] | None:
        """Parse reset time and timezone from notification message.

        Handles both wordings: the undated session-limit form ("resets 11:10pm
        (Europe/Brussels)") and the dated weekly form ("resets Aug 3, 7pm
        (Europe/Brussels)").

        A *dated* reset is returned as-is even when it already passed -- that
        past timestamp is the signal that the limit is over and dispatch may
        resume immediately. Only the undated form rolls a past clock time to
        tomorrow, because there the date is genuinely unknown.

        ``now`` is the reference clock the undated form is resolved against,
        defaulting to the wall clock. Callers that re-read an *old* message
        (the transcript-tail sweep sees the same limit on every dispatch tick)
        pass the moment the message was written, so the rollover answers "was
        this reset later than the message" instead of "later than right now" --
        without that, re-parsing a message after its own reset silently rolls
        the deadline a full day forward. See kanban card ``e279a52b…``.

        Returns (datetime, timezone_name) or None if parsing fails.
        """
        if not message:
            return None
        match = _LIMIT_PATTERN.search(message)
        if not match:
            return None

        time_str = match.group("time")
        tz_name = match.group("tz")
        month_str = match.group("month")
        day_str = match.group("day")

        try:
            tz = ZoneInfo(tz_name)
        except (KeyError, ValueError):
            logger.warning("Unknown timezone in limit notification: %s", tz_name)
            return None

        # Parse time (12h or 24h format)
        reference = datetime.now(tz) if now is None else now.astimezone(tz)
        try:
            if "am" in time_str.lower() or "pm" in time_str.lower():
                # 12h format: "11:10pm" or "9pm"
                format_string = "%I:%M%p" if ":" in time_str else "%I%p"
                parsed = datetime.strptime(time_str.lower(), format_string)
                reset_time = reference.replace(
                    hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0
                )
            else:
                # 24h format: "23:10"
                parsed = datetime.strptime(time_str, "%H:%M")
                reset_time = reference.replace(
                    hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0
                )
        except ValueError:
            logger.warning("Could not parse time '%s' from limit notification", time_str)
            return None

        if month_str is not None and day_str is not None:
            try:
                month = datetime.strptime(month_str[:3].title(), "%b").month
            except ValueError:
                logger.warning(
                    "Could not parse month '%s' from limit notification", month_str
                )
                return None
            day = int(day_str)
            try:
                reset_time = reset_time.replace(
                    year=_resolve_year(month, day, reference), month=month, day=day
                )
            except ValueError:
                logger.warning(
                    "Invalid reset date '%s %s' in limit notification",
                    month_str, day_str,
                )
                return None
            # No rollover: the notification told us the exact date. A reset in
            # the past means the limit already lifted -- report it as such.
            return reset_time, tz_name

        # If the reset clock time already passed relative to the reference, the
        # notification must mean tomorrow. Note this is relative to `reference`,
        # not the wall clock: re-parsing an old message at "now" is what rolled
        # a still-valid deadline a full day forward (kanban card e279a52b…).
        if reset_time <= reference:
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
