import pytest
from pydantic import ValidationError
from app.models.scheduled_message_schemas import ScheduledMessageCreate


def test_once_requires_fire_at():
    with pytest.raises(ValidationError):
        ScheduledMessageCreate(target_project="/x", message="hi", trigger_type="once")


def test_cron_requires_cron_expr():
    with pytest.raises(ValidationError):
        ScheduledMessageCreate(target_project="/x", message="hi", trigger_type="cron")


def test_valid_once_defaults():
    m = ScheduledMessageCreate(
        target_project="/x", message="hi", trigger_type="once",
        fire_at="2026-06-12T09:00:00+02:00",
    )
    assert m.permission_mode == "acceptEdits"
    assert m.on_missing_session == "spawn"
    assert m.when_busy == "wait_until_idle"


def test_valid_cron():
    m = ScheduledMessageCreate(
        target_project="/x", message="hi", trigger_type="cron",
        cron_expr="0 9 * * 1-5",
    )
    assert m.cron_expr == "0 9 * * 1-5"
