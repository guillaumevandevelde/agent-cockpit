"""Edge cases for scheduled messages: invalid cron expressions, bad timezones,
malformed fire-at timestamps, past trigger times, and malformed Pydantic input."""
import pytest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from app.services.scheduling.scheduler import SchedulerService
from app.models.scheduled_message_schemas import ScheduledMessageCreate


def test_schedule_cron_rejects_invalid_expression():
    svc = SchedulerService()
    with pytest.raises(ValueError):
        svc.schedule_cron(message_id=1, cron_expr="not a cron", tz="Europe/Brussels")


def test_schedule_cron_rejects_wrong_field_count():
    svc = SchedulerService()
    with pytest.raises(ValueError):
        # only four fields instead of five
        svc.schedule_cron(message_id=1, cron_expr="0 9 * *", tz="Europe/Brussels")


def test_schedule_cron_rejects_unknown_timezone():
    svc = SchedulerService()
    with pytest.raises(Exception):
        svc.schedule_cron(message_id=1, cron_expr="0 9 * * *", tz="Mars/Olympus")


def test_schedule_once_rejects_malformed_timestamp():
    svc = SchedulerService()
    with pytest.raises(ValueError):
        svc.schedule_once(message_id=1, fire_at_iso="not-a-timestamp")


def test_schedule_once_in_the_past_still_registers_job():
    # A fire_at already in the past must not crash; misfire_grace_time lets the
    # job still deliver on the next scheduler pass instead of being dropped.
    svc = SchedulerService()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    svc.schedule_once(message_id=42, fire_at_iso=past)
    assert svc.has_job(42) is True
    svc.remove(42)


def test_create_rejects_unknown_trigger_type():
    with pytest.raises(ValidationError):
        ScheduledMessageCreate(
            target_project="/x", message="hi", trigger_type="hourly",
        )


def test_create_rejects_invalid_permission_mode():
    with pytest.raises(ValidationError):
        ScheduledMessageCreate(
            target_project="/x", message="hi", trigger_type="once",
            fire_at="2026-06-12T09:00:00+02:00", permission_mode="root",
        )
