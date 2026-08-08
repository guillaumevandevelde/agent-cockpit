"""Edge cases for the shared SchedulerService.

The once/cron scheduled-message jobs that used to be covered here were
retired with the scheduled-messages feature — see
``docs/cockpit/scheduled-trigger-consolidatie-decision.md`` §5.2.
``schedule_recurring_trigger`` now carries the only cron-style entry the
service registers for a kaart creation.
"""
import pytest

from app.services.scheduling.scheduler import SchedulerService


def test_schedule_recurring_trigger_rejects_invalid_expression():
    svc = SchedulerService()
    with pytest.raises(ValueError):
        svc.schedule_recurring_trigger(
            trigger_id=1, cron_expr="not a cron", tz="Europe/Brussels",
        )


def test_schedule_recurring_trigger_rejects_wrong_field_count():
    svc = SchedulerService()
    with pytest.raises(ValueError):
        # only four fields instead of five
        svc.schedule_recurring_trigger(
            trigger_id=1, cron_expr="0 9 * *", tz="Europe/Brussels",
        )


def test_schedule_recurring_trigger_rejects_unknown_timezone():
    svc = SchedulerService()
    with pytest.raises(Exception):
        svc.schedule_recurring_trigger(
            trigger_id=1, cron_expr="0 9 * * *", tz="Mars/Olympus",
        )
