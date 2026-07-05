import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app.services.scheduling.scheduler import SchedulerService


@pytest.mark.asyncio
async def test_once_job_fires_and_calls_delivery():
    svc = SchedulerService()
    svc.start()
    fired = asyncio.Event()

    async def fake_run(message_id):
        fired.set()

    with patch.object(svc, "_run_delivery", side_effect=fake_run):
        fire_at = (datetime.now(UTC) + timedelta(seconds=0.3)).isoformat()
        svc.schedule_once(message_id=1, fire_at_iso=fire_at)
        await asyncio.wait_for(fired.wait(), timeout=3)
    svc.shutdown()


def test_cron_schedule_and_remove():
    # No start() needed: add_job stores the job even when the scheduler is idle.
    svc = SchedulerService()
    svc.schedule_cron(message_id=2, cron_expr="0 9 * * 1-5", tz="Europe/Brussels")
    assert svc.has_job(2) is True
    svc.remove(2)
    assert svc.has_job(2) is False


def test_auto_backup_schedule_and_remove():
    svc = SchedulerService()
    svc.schedule_auto_backup(time_of_day="03:30", tz="Europe/Brussels")
    assert svc.has_auto_backup() is True
    svc.remove_auto_backup()
    assert svc.has_auto_backup() is False
