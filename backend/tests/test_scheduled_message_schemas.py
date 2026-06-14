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


def test_session_target_requires_session_id_and_folder():
    with pytest.raises(ValidationError):
        ScheduledMessageCreate(
            target_project="/proj", message="hi", trigger_type="once",
            fire_at="2026-01-01T00:00:00", target_kind="session",
        )


def test_session_target_valid():
    m = ScheduledMessageCreate(
        target_project="/proj", message="hi", trigger_type="once",
        fire_at="2026-01-01T00:00:00", target_kind="session",
        target_session_id="abc-123", project_folder="-home-guillaume-proj",
    )
    assert m.target_kind == "session"
    assert m.target_session_id == "abc-123"


def test_project_target_defaults():
    m = ScheduledMessageCreate(
        target_project="/proj", message="hi", trigger_type="once",
        fire_at="2026-01-01T00:00:00",
    )
    assert m.target_kind == "project"
