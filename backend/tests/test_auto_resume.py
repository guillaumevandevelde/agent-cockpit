"""Tests for auto-resume on session limit."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.services.scheduling.auto_resume import AutoResumeService


class TestLimitDetection:
    def test_detects_session_limit_notification(self):
        svc = AutoResumeService()
        msg = "You've hit your session limit · resets 11:10pm (Europe/Brussels)"
        assert svc.is_limit_notification(msg) is True

    def test_ignores_other_notifications(self):
        svc = AutoResumeService()
        assert svc.is_limit_notification("Task completed successfully") is False
        assert svc.is_limit_notification(None) is False
        assert svc.is_limit_notification("") is False

    def test_case_insensitive(self):
        svc = AutoResumeService()
        msg = "YOU'VE HIT YOUR SESSION LIMIT · resets 11:10pm (Europe/Brussels)"
        assert svc.is_limit_notification(msg) is True

    @pytest.mark.parametrize("msg", [
        "API Error: 429 Too Many Requests",
        "Received 429 from upstream provider",
        "Token Plan limit reached for this account",
        "You've hit your usage limit for the day",
        "Request rejected: rate limit exceeded",
        "api error (429) — try again in 5h",
    ])
    def test_detects_minimax_style_rate_limit_notifications(self, msg):
        """Minimax (and similar providers) can report a rate limit via a
        429 / 'Token Plan' / 'API Error' / 'usage limit' / 'request rejected'
        message — the global dispatch pause must catch these too, not only
        the canonical Anthropic 'hit your session limit' wording."""
        svc = AutoResumeService()
        assert svc.is_limit_notification(msg) is True


class TestParseResetTime:
    def test_parses_12h_format(self):
        svc = AutoResumeService()
        msg = "You've hit your session limit · resets 11:10pm (Europe/Brussels)"
        result = svc.parse_reset_time(msg)
        assert result is not None
        reset_time, tz_name = result
        assert tz_name == "Europe/Brussels"
        assert reset_time.hour == 23
        assert reset_time.minute == 10

    def test_parses_24h_format(self):
        svc = AutoResumeService()
        msg = "You've hit your session limit · resets 23:10 (Europe/Brussels)"
        result = svc.parse_reset_time(msg)
        assert result is not None
        reset_time, tz_name = result
        assert reset_time.hour == 23
        assert reset_time.minute == 10

    @pytest.mark.parametrize(
        ("msg", "expected_hour", "expected_minute"),
        [
            (
                "You've hit your session limit · resets 11:10pm (Europe/Brussels)",
                23,
                10,
            ),
            (
                "You've hit your weekly limit · resets 9pm (Europe/Brussels)",
                21,
                0,
            ),
            (
                "You've hit your session limit · resets 8am (Europe/Brussels)",
                8,
                0,
            ),
        ],
    )
    def test_parses_analyzed_reset_time_forms(
        self, msg, expected_hour, expected_minute
    ):
        svc = AutoResumeService()
        result = svc.parse_reset_time(msg)
        assert result is not None
        reset_time, tz_name = result
        assert tz_name == "Europe/Brussels"
        assert reset_time.hour == expected_hour
        assert reset_time.minute == expected_minute

    def test_returns_none_for_invalid_message(self):
        svc = AutoResumeService()
        assert svc.parse_reset_time("No limit info here") is None
        assert svc.parse_reset_time(None) is None

    def test_returns_none_for_unknown_timezone(self):
        svc = AutoResumeService()
        msg = "You've hit your session limit · resets 11:10pm (Invalid/Timezone)"
        assert svc.parse_reset_time(msg) is None


class TestParseDatedResetTime:
    """The weekly-limit wording carries a *date* before the clock time
    ("resets Aug 3, 7pm") because the reset can be days away. Without it the
    parser returned None and every caller fell back to the blind
    FALLBACK_PAUSE_HOURS guess -- see the 2026-08-01 board incident where two
    cards were parked for 5h while their real reset had already passed.
    """

    def test_parses_dated_weekly_reset_time(self):
        svc = AutoResumeService()
        msg = "You've hit your weekly limit · resets Aug 3, 7pm (Europe/Brussels)"
        result = svc.parse_reset_time(msg)
        assert result is not None
        reset_time, tz_name = result
        assert tz_name == "Europe/Brussels"
        assert (reset_time.month, reset_time.day) == (8, 3)
        assert (reset_time.hour, reset_time.minute) == (19, 0)

    def test_parses_dated_reset_time_with_minutes(self):
        svc = AutoResumeService()
        msg = "You've hit your weekly limit · resets Jul 27, 7:30pm (Europe/Brussels)"
        result = svc.parse_reset_time(msg)
        assert result is not None
        reset_time, _tz = result
        assert (reset_time.month, reset_time.day) == (7, 27)
        assert (reset_time.hour, reset_time.minute) == (19, 30)

    def test_dated_reset_in_the_past_is_not_rolled_forward(self):
        """A dated reset that already passed must come back as the real past
        moment -- that is what tells the caller "the limit is over, dispatch
        now". Rolling it to tomorrow (the undated-form behaviour) would park
        the card for another day."""
        svc = AutoResumeService()
        tz = ZoneInfo("Europe/Brussels")
        past = datetime.now(tz) - timedelta(days=2)
        msg = (
            "You've hit your weekly limit · resets "
            f"{past.strftime('%b %-d')}, 9am (Europe/Brussels)"
        )
        result = svc.parse_reset_time(msg)
        assert result is not None
        reset_time, _tz = result
        assert (reset_time.month, reset_time.day) == (past.month, past.day)
        assert reset_time < datetime.now(tz)

    def test_returns_none_for_unparsable_month_name(self):
        svc = AutoResumeService()
        msg = "You've hit your weekly limit · resets Smarch 3, 7pm (Europe/Brussels)"
        assert svc.parse_reset_time(msg) is None


class TestParseResetTimeReferenceClock:
    """``parse_reset_time`` resolves a date-less reset against a reference
    clock, defaulting to "now". The transcript-tail sweep re-reads the *same*
    limit message on every dispatch tick, so it passes the moment the message
    was written instead — otherwise, once the original reset passed, the
    "past clock time means tomorrow" rollover pushed the deadline a full day
    forward at the exact moment the subscription came free. Measured on
    session ``k-update-readme-e85e`` at 2026-07-28T03:20:04Z (kanban card
    ``e279a52b…``).
    """

    def test_rollover_is_relative_to_the_reference_not_the_wall_clock(self):
        svc = AutoResumeService()
        tz = ZoneInfo("UTC")
        # Message written at 03:20, announcing a 05:20 reset.
        written_at = datetime(2026, 7, 28, 3, 20, tzinfo=tz)
        msg = "You've hit your session limit · resets 05:20 (UTC)"

        result = svc.parse_reset_time(msg, now=written_at)
        assert result is not None
        reset_time, _tz = result
        # Same day, no rollover — 05:20 is still ahead of 03:20.
        assert reset_time == datetime(2026, 7, 28, 5, 20, tzinfo=tz)

    def test_reference_past_the_reset_reports_the_past_moment(self):
        """Re-parsed with a reference *after* the reset, the answer is the
        past moment — the caller's signal that the limit already lifted."""
        svc = AutoResumeService()
        tz = ZoneInfo("UTC")
        read_at = datetime(2026, 7, 28, 3, 20, tzinfo=tz)
        msg = "You've hit your session limit · resets 01:00 (UTC)"

        result = svc.parse_reset_time(msg, now=read_at)
        assert result is not None
        reset_time, _tz = result
        # 01:00 already passed at 03:20, so the undated form still rolls to
        # tomorrow — the *caller* anchors `now` to the message to avoid that.
        assert reset_time == datetime(2026, 7, 29, 1, 0, tzinfo=tz)

    def test_default_reference_is_the_wall_clock(self):
        """No ``now`` means "a fresh message, right now" — the hook path."""
        svc = AutoResumeService()
        tz = ZoneInfo("Europe/Brussels")
        past_clock = datetime.now(tz) - timedelta(hours=1)
        msg = (
            "You've hit your session limit · resets "
            f"{past_clock.strftime('%H:%M')} (Europe/Brussels)"
        )
        result = svc.parse_reset_time(msg)
        assert result is not None
        reset_time, _tz = result
        assert reset_time > datetime.now(tz), "a fresh past clock time means tomorrow"


class TestResolveYear:
    """The notification carries no year, so the month/day pair is resolved
    against the nearest occurrence -- otherwise a Dec 31 -> Jan 1 reset lands
    364 days in the past."""

    def test_picks_current_year_for_a_nearby_date(self):
        from app.services.scheduling.auto_resume import _resolve_year
        now = datetime(2026, 8, 3, 12, 0, tzinfo=ZoneInfo("Europe/Brussels"))
        assert _resolve_year(8, 3, now) == 2026

    def test_rolls_forward_across_new_year(self):
        from app.services.scheduling.auto_resume import _resolve_year
        now = datetime(2026, 12, 31, 22, 0, tzinfo=ZoneInfo("Europe/Brussels"))
        assert _resolve_year(1, 1, now) == 2027

    def test_rolls_back_across_new_year(self):
        from app.services.scheduling.auto_resume import _resolve_year
        now = datetime(2027, 1, 1, 2, 0, tzinfo=ZoneInfo("Europe/Brussels"))
        assert _resolve_year(12, 31, now) == 2026


class TestEnabledState:
    def test_default_disabled(self):
        svc = AutoResumeService()
        assert svc.is_enabled("/some/path") is False

    def test_enable_disable(self):
        svc = AutoResumeService()
        svc.set_enabled("/project", True)
        assert svc.is_enabled("/project") is True
        svc.set_enabled("/project", False)
        assert svc.is_enabled("/project") is False

    def test_per_project_isolation(self):
        svc = AutoResumeService()
        svc.set_enabled("/project-a", True)
        assert svc.is_enabled("/project-a") is True
        assert svc.is_enabled("/project-b") is False


class TestDefaultResumeMessage:
    """The default nudge text is short ("OK") on purpose — a long imperative
    like "Continue where you left off." looks like a workprompt to Claude and
    burns input-tokens on a routine auto-resume. Operators that want a longer
    message (e.g. to give the session context) override per-cwd via
    ``set_message``. Pinning the default length here guards against a future
    refactor widening the constant back to an imperative sentence."""

    def test_default_is_short(self):
        from app.services.scheduling.auto_resume import DEFAULT_RESUME_MESSAGE

        # 1-3 words, per card 9c7ef6b1… acceptance criteria.
        assert len(DEFAULT_RESUME_MESSAGE.split()) <= 3
        assert len(DEFAULT_RESUME_MESSAGE) < 30


class TestResumeMessageOverride:
    """Per-cwd override on the auto-resume message — the canonical way for
    an operator to give the resumed session context (or to switch to their
    own short acknowledgement). Analog of ``set_enabled`` / ``is_enabled``."""

    def test_get_message_returns_default_when_unset(self):
        svc = AutoResumeService()
        from app.services.scheduling.auto_resume import DEFAULT_RESUME_MESSAGE
        assert svc.get_message("/some/path") == DEFAULT_RESUME_MESSAGE

    def test_set_message_roundtrip(self):
        svc = AutoResumeService()
        svc.set_message("/project", "Resume on prior task")
        assert svc.get_message("/project") == "Resume on prior task"

    def test_per_cwd_isolation(self):
        svc = AutoResumeService()
        svc.set_message("/project-a", "Custom A")
        assert svc.get_message("/project-a") == "Custom A"
        assert svc.get_message("/project-b") == svc.get_message("/project-b")
        # project-b keeps the default (not project-a's value).
        from app.services.scheduling.auto_resume import DEFAULT_RESUME_MESSAGE
        assert svc.get_message("/project-b") == DEFAULT_RESUME_MESSAGE


class TestScheduleResumeMessagePropagation:
    """``schedule_resume`` resolves the message in this order:

    1. explicit ``message`` argument (call-site override),
    2. per-cwd override via ``get_message(cwd)``,
    3. ``DEFAULT_RESUME_MESSAGE``.

    The explicit-override path stays so a caller like the session-hooks
    router can hand-craft a one-off nudge (kanban card 9c7ef6b1…
    acceptance criteria). The per-cwd path is the new way operators pick
    a project-specific default without forking the call site."""

    def _capture_scheduled_args(self, svc, **kwargs):
        mock_sched = MagicMock()
        with patch(
            "app.services.scheduling.scheduler.scheduler_service", mock_sched
        ):
            svc.schedule_resume(
                cwd=kwargs.pop("cwd", "/project"),
                reset_time=kwargs.pop(
                    "reset_time",
                    datetime.now(ZoneInfo("UTC")) + timedelta(hours=1),
                ),
                tz_name=kwargs.pop("tz_name", "UTC"),
                **kwargs,
            )
            # args=[cwd, message, session_id, project_folder]
            return mock_sched._sched.add_job.call_args.kwargs["args"]

    def test_explicit_message_overrides_per_cwd_setting(self):
        svc = AutoResumeService()
        svc.set_message("/project", "Per-cwd default")
        args = self._capture_scheduled_args(
            svc, cwd="/project", message="explicit override"
        )
        assert args[1] == "explicit override"

    def test_no_message_picks_up_per_cwd_setting(self):
        svc = AutoResumeService()
        svc.set_message("/project", "Per-cwd default")
        args = self._capture_scheduled_args(svc, cwd="/project")
        assert args[1] == "Per-cwd default"

    def test_no_message_no_override_picks_default(self):
        svc = AutoResumeService()
        from app.services.scheduling.auto_resume import DEFAULT_RESUME_MESSAGE
        args = self._capture_scheduled_args(svc, cwd="/project")
        assert args[1] == DEFAULT_RESUME_MESSAGE


class TestScheduleResume:
    def test_schedule_creates_job(self):
        svc = AutoResumeService()
        reset_time = datetime.now(ZoneInfo("Europe/Brussels")) + timedelta(hours=1)

        mock_sched = MagicMock()
        with patch("app.services.scheduling.scheduler.scheduler_service", mock_sched):
            job_id = svc.schedule_resume(
                cwd="/project",
                reset_time=reset_time,
                tz_name="Europe/Brussels",
            )
            assert job_id.startswith("auto-resume-")
            mock_sched._sched.add_job.assert_called_once()

    def test_cancel_removes_job(self):
        svc = AutoResumeService()
        svc._scheduled["/project"] = "test-job-id"

        mock_sched = MagicMock()
        with patch("app.services.scheduling.scheduler.scheduler_service", mock_sched):
            result = svc.cancel("/project")
            assert result is True
            assert "/project" not in svc._scheduled

    def test_cancel_nonexistent_returns_false(self):
        svc = AutoResumeService()
        assert svc.cancel("/nonexistent") is False


class TestClassifyNotification:
    """classify_notification() splits a Notification payload into limit /
    needs_input / completed / other so the hook router can branch on intent
    instead of string-sniffing `message` in two places."""

    def test_limit_classification_preserves_existing_behaviour(self):
        """Existing `is_limit_notification` cases must keep classifying as
        'limit' so the rate-limit → To Resume + dispatch-pause path is
        untouched by the new bucket."""
        svc = AutoResumeService()
        assert svc.classify_notification(
            message="You've hit your session limit · resets 11:10pm (Europe/Brussels)",
        ) == "limit"
        assert svc.classify_notification(
            message="API Error: 429 Too Many Requests",
        ) == "limit"
        assert svc.classify_notification(
            message="Token Plan limit reached for this account",
        ) == "limit"

    @pytest.mark.parametrize("msg", [
        "You've hit your session limit · resets 11:10pm (Europe/Brussels)",
        "API Error: Request rejected (429) · Token Plan usage limit reached: …",
        "You've hit your session limit · resets 11:10am (Europe/Brussels)",
        "You've hit your session limit · resets 9pm (Europe/Brussels)",
        "You've hit your session limit · resets 8am (Europe/Brussels)",
        "You've hit your weekly limit · resets 9pm (Europe/Brussels)",
    ])
    def test_detects_literal_analyzed_limit_forms(self, msg):
        svc = AutoResumeService()
        assert svc.classify_notification(message=msg) == "limit"

    def test_needs_input_via_notification_type(self):
        """When Claude Code 2.1.198+ forwards a structured notification_type,
        that's authoritative — message can be anything (the template string
        varies per release and per label)."""
        svc = AutoResumeService()
        assert svc.classify_notification(
            notification_type="agent_needs_input",
            message="some background-agent label needs your input",
        ) == "needs_input"

    def test_needs_input_via_message_substring_fallback(self):
        """Older hook payloads (no notification_type) still hit the new bucket
        via the canonical '<label> needs your input' wording — same substring
        shape as the existing limit match."""
        svc = AutoResumeService()
        assert svc.classify_notification(
            message="background-agent needs your input",
        ) == "needs_input"
        assert svc.classify_notification(
            message="My Agent needs your input: waiting on tool X",
        ) == "needs_input"

    def test_completed_via_notification_type(self):
        svc = AutoResumeService()
        assert svc.classify_notification(
            notification_type="agent_completed",
            message="background-agent finished",
        ) == "completed"
        # Outcome='failure' still surfaces under agent_completed — the
        # completed bucket means "the agent finished its lifecycle", not
        # necessarily "it succeeded"; the operator decides from the message.
        assert svc.classify_notification(
            notification_type="agent_completed",
            message="background-agent failed",
        ) == "completed"

    def test_completed_via_message_substring_fallback(self):
        svc = AutoResumeService()
        assert svc.classify_notification(message="background-agent finished") == "completed"
        assert svc.classify_notification(message="background-agent failed") == "completed"

    def test_other_for_unrelated_notifications(self):
        """permission_prompt / idle_prompt / auth_success / elicitation_*
        must NOT be misclassified as needs_input or completed — they
        classify as 'other' so the router drops them silently, same as
        today."""
        svc = AutoResumeService()
        assert svc.classify_notification(
            notification_type="permission_prompt",
            message="Claude needs your input",
        ) == "other"
        assert svc.classify_notification(
            notification_type="idle_prompt",
            message="Claude is waiting",
        ) == "other"
        assert svc.classify_notification(
            notification_type="auth_success",
            message="logged in",
        ) == "other"
        assert svc.classify_notification(
            notification_type="elicitation_dialog",
            message="Claude needs your input",
        ) == "other"

    def test_other_for_empty_or_missing_payload(self):
        svc = AutoResumeService()
        assert svc.classify_notification() == "other"
        assert svc.classify_notification(message=None) == "other"
        assert svc.classify_notification(message="") == "other"
        assert svc.classify_notification(notification_type="") == "other"


class TestLimitPatternRedos:
    """`_LIMIT_PATTERN` runs against notification text that arrives from the
    CLI/provider, i.e. input this process does not control (CodeQL
    py/polynomial-redos, alert 251).

    Both `.*?` gaps in the pattern used to be unbounded, so `search` did
    O(n) work at each of O(n) start positions -- quadratic. Measured before
    the fix: 4000 reps 0.32s, 8000 reps 1.35s (4x for 2x input). A single
    long notification could stall the dispatch loop that parses it.

    The gaps are now bounded, which caps per-start-position work at a
    constant and makes the whole search linear.
    """

    # Both blow-up shapes CodeQL reported for this pattern.
    @pytest.mark.parametrize(
        ("label", "build"),
        [
            ("hit-your-reps", lambda n: "hit your " * n),
            ("limit-reps", lambda n: "hit your " + " limit" * n),
        ],
    )
    def test_adversarial_input_stays_linear(self, label, build):
        import time

        svc = AutoResumeService()

        def elapsed(n):
            payload = build(n)
            start = time.perf_counter()
            svc.parse_reset_time(payload)
            return time.perf_counter() - start

        # Doubling the input must not quadruple the time. A quadratic
        # pattern lands near 4.0; a linear one near 2.0. 3.0 separates them
        # without being flaky on a loaded shared runner.
        base = max(elapsed(8000), 1e-4)
        doubled = elapsed(16000)
        assert doubled / base < 3.0, (
            f"{label}: {doubled:.3f}s vs {base:.3f}s "
            f"(ratio {doubled / base:.1f}) suggests super-linear backtracking"
        )

    def test_adversarial_input_completes_promptly(self):
        """Absolute ceiling, independent of the ratio check above: the
        pre-fix pattern needed ~1.35s for 8000 reps."""
        import time

        svc = AutoResumeService()
        payload = "hit your " * 20000
        start = time.perf_counter()
        assert svc.parse_reset_time(payload) is None
        assert time.perf_counter() - start < 0.5

    def test_realistic_gaps_still_parse(self):
        """The bound must stay wide enough for the real wording, including
        the dated weekly form."""
        svc = AutoResumeService()
        for msg in (
            "You've hit your session limit · resets 11:10pm (Europe/Brussels)",
            "You've hit your weekly limit · resets Aug 3, 7pm (Europe/Brussels)",
        ):
            assert svc.parse_reset_time(msg) is not None, msg
