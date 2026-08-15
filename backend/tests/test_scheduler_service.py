"""Tests for the shared SchedulerService used by kanban-dispatch, recurring
triggers, stale detection, and the auto-backup job. The once/cron
scheduled-message jobs were retired with the scheduled-messages feature
(see docs/cockpit/scheduled-trigger-consolidatie-decision.md §5.2) — they
lived here too.
"""

from app.services.scheduling.scheduler import SchedulerService


def test_recurring_trigger_schedule_and_remove():
    """``schedule_recurring_trigger`` is the canonical "cron -> kanban card"
    job. Without ``start()`` ``add_job`` still stores the entry — we exercise
    add/remove here and leave end-to-end firing to the live boot path."""
    svc = SchedulerService()
    svc.schedule_recurring_trigger(
        trigger_id=2, cron_expr="0 9 * * 1-5", tz="Europe/Brussels",
    )
    assert svc.has_recurring_trigger(2) is True
    svc.remove_recurring_trigger(2)
    assert svc.has_recurring_trigger(2) is False


def test_auto_backup_schedule_and_remove():
    svc = SchedulerService()
    svc.schedule_auto_backup(time_of_day="03:30", tz="Europe/Brussels")
    assert svc.has_auto_backup() is True
    svc.remove_auto_backup()
    assert svc.has_auto_backup() is False
