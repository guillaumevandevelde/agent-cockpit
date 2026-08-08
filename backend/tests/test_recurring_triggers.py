"""Recurring time-trigger: cron → kanban Backlog card.

Mirrors the webhook trigger path (event → ``create_card``), with the clock
as the source instead of an external webhook. See
``docs/cockpit/scheduled-trigger-consolidatie-decision.md`` §5.1–5.2 for the
design rationale.

Coverage shape (acceptance criteria §5):
  * Firing creates exactly one Backlog card in the kanban DB.
  * Disabled triggers never fire.
  * Two fires within the same cron occurrence coalesce into one card.
  * On boot, a trigger whose last fire is older than the previous cron
    occurrence catches up once — not N times for N missed occurrences.
  * REST CRUD lives under /api/v1/recurring-triggers.
"""
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import AsyncSessionLocal, Base, engine
from app.main import app
from app.services.scheduling.scheduler import SchedulerService


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


# ---------- helpers ----------

async def _seed_project(project_key: str) -> None:
    """Insert a minimal ``kanban_cards``-compatible project_key row so that
    the kanban API doesn't 404 the unknown-project-key guard when a trigger
    fires for it (kaart 91c85199).

    A single Backlog card on the project is enough: the auto-dispatcher only
    needs SOMETHING under the project to consider it "exists". We don't
    care about dispatch in this test — only that ``create_card`` succeeds.
    """
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.models import KanbanCard
    async with KanbanSessionLocal() as s:
        s.add(KanbanCard(
            id=f"seed-{project_key}",
            project_key=project_key,
            title="seed",
            column="Backlog",
            rank="seed-rank",
        ))
        await s.commit()


async def _create_trigger(
    *, project_key: str = "P",
    cron_expr: str = "0 9 * * 1",
    timezone: str = "Europe/Brussels",
    enabled: bool = True,
    title: str = "weekly digest",
    work_type: str | None = "chore",
    agent: str | None = None,
    labels: list[str] | None = None,
    last_fired_at: datetime | None = None,
) -> dict:
    payload = {
        "project_key": project_key,
        "cron_expr": cron_expr,
        "timezone": timezone,
        "enabled": enabled,
        "title": title,
        "description": "auto from cron",
        "work_type": work_type,
        "agent": agent,
        "labels": labels,
    }
    async with AsyncSessionLocal() as s:
        from app.models.recurring_trigger import RecurringTrigger
        t = RecurringTrigger(**payload)
        if last_fired_at is not None:
            t.last_fired_at = last_fired_at
        s.add(t)
        await s.commit()
        await s.refresh(t)
        return {"id": t.id, "payload": payload}


# ---------- REST CRUD ----------

@pytest.mark.asyncio
async def test_create_via_api_returns_201_and_persists_row():
    await _seed_project("P")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/recurring-triggers", json={
            "project_key": "P",
            "cron_expr": "0 9 * * 1",
            "timezone": "Europe/Brussels",
            "enabled": True,
            "title": "weekly",
            "description": "auto",
            "work_type": "chore",
        })
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["project_key"] == "P"
        assert data["cron_expr"] == "0 9 * * 1"
        assert data["enabled"] is True
        assert data["id"] > 0
        assert data["last_fired_at"] is None


@pytest.mark.asyncio
async def test_list_via_api_returns_all_triggers():
    await _create_trigger(project_key="A")
    await _create_trigger(project_key="B")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get("/api/v1/recurring-triggers")
        assert r.status_code == 200
        keys = {item["project_key"] for item in r.json()["items"]}
        assert keys == {"A", "B"}


@pytest.mark.asyncio
async def test_update_via_api_toggles_enabled_and_persists():
    tid = (await _create_trigger(enabled=True))["id"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.patch(f"/api/v1/recurring-triggers/{tid}", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["enabled"] is False


@pytest.mark.asyncio
async def test_delete_via_api_removes_row():
    tid = (await _create_trigger())["id"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.delete(f"/api/v1/recurring-triggers/{tid}")
        assert r.status_code == 200
        r = await ac.get("/api/v1/recurring-triggers")
        assert all(item["id"] != tid for item in r.json()["items"])


# ---------- fire behavior ----------

@pytest.mark.asyncio
async def test_fire_creates_exactly_one_backlog_card():
    await _seed_project("P")
    await _create_trigger(project_key="P")
    from app.services.recurring_triggers import fire_trigger_by_id
    async with AsyncSessionLocal() as s:
        from sqlalchemy import select

        from app.models.recurring_trigger import RecurringTrigger
        row = (await s.execute(select(RecurringTrigger))).scalar_one()
        trigger_id = row.id
    cid = await fire_trigger_by_id(trigger_id)
    assert cid is not None

    from sqlalchemy import select

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.models import KanbanCard
    async with KanbanSessionLocal() as s:
        cards = (await s.execute(
            select(KanbanCard).where(KanbanCard.id != "seed-P")
        )).scalars().all()
    assert len(cards) == 1
    assert cards[0].column == "Backlog"
    assert cards[0].title == "weekly digest"


@pytest.mark.asyncio
async def test_disabled_trigger_does_not_fire():
    await _seed_project("P")
    await _create_trigger(enabled=False)
    from app.services.recurring_triggers import fire_trigger_by_id
    async with AsyncSessionLocal() as s:
        from sqlalchemy import select

        from app.models.recurring_trigger import RecurringTrigger
        row = (await s.execute(select(RecurringTrigger))).scalar_one()
        tid = row.id
    cid = await fire_trigger_by_id(tid)
    assert cid is None

    from sqlalchemy import select

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.models import KanbanCard
    async with KanbanSessionLocal() as s:
        cards = (await s.execute(
            select(KanbanCard).where(KanbanCard.id != "seed-P")
        )).scalars().all()
    assert cards == []


@pytest.mark.asyncio
async def test_double_fire_within_same_occurrence_creates_one_card():
    """Two APScheduler ticks within the same occurrence must coalesce."""
    await _seed_project("P")
    await _create_trigger()
    from app.services.recurring_triggers import fire_trigger_by_id
    async with AsyncSessionLocal() as s:
        from sqlalchemy import select

        from app.models.recurring_trigger import RecurringTrigger
        row = (await s.execute(select(RecurringTrigger))).scalar_one()
        tid = row.id

    cid1 = await fire_trigger_by_id(tid)
    cid2 = await fire_trigger_by_id(tid)
    # Same occurrence → second fire is a no-op → second call returns the
    # same card id (or None, but the user-visible invariant is "no second
    # card"). We accept either as long as the card count is 1.
    assert cid1 is not None

    from sqlalchemy import select

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.models import KanbanCard
    async with KanbanSessionLocal() as s:
        cards = (await s.execute(
            select(KanbanCard).where(KanbanCard.id != "seed-P")
        )).scalars().all()
    assert len(cards) == 1
    _ = cid2  # explicit: coalesced value not asserted


@pytest.mark.asyncio
async def test_inhaal_on_boot_fires_once_when_last_fire_predates_previous_occurrence():
    """Boot-time inhaal: the previous cron occurrence is in the past while
    ``last_fired_at`` is older still. Exactly one catch-up card, not N."""
    await _seed_project("P")
    # Pretend we last fired on 2026-07-27 (Mon). Previous occurrence of
    # ``0 9 * * 1`` Europe/Brussels is Mon 2026-08-03 07:00 UTC. Boot time
    # is Wed 2026-08-05 12:00 UTC — both occurrences are in the past.
    last = datetime(2026, 7, 27, 9, 0, tzinfo=UTC)
    await _create_trigger(last_fired_at=last)
    from app.services.recurring_triggers import run_boot_inhaal
    await run_boot_inhaal(now=datetime(2026, 8, 5, 12, 0, tzinfo=UTC))

    from sqlalchemy import select

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.models import KanbanCard
    async with KanbanSessionLocal() as s:
        cards = (await s.execute(
            select(KanbanCard).where(KanbanCard.id != "seed-P")
        )).scalars().all()
    assert len(cards) == 1, "coalesced catch-up must yield exactly one card"


@pytest.mark.asyncio
async def test_inhaal_does_not_fire_when_last_fire_is_at_or_after_previous_occurrence():
    await _seed_project("P")
    # Last fired Wed 2026-08-05 (same day as boot). Previous cron occurrence
    # of ``0 9 * * 1`` is Mon 2026-08-03 — already covered. No inhaal needed.
    last = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)
    await _create_trigger(last_fired_at=last)
    from app.services.recurring_triggers import run_boot_inhaal
    await run_boot_inhaal(now=datetime(2026, 8, 5, 12, 0, tzinfo=UTC))

    from sqlalchemy import select

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.models import KanbanCard
    async with KanbanSessionLocal() as s:
        cards = (await s.execute(
            select(KanbanCard).where(KanbanCard.id != "seed-P")
        )).scalars().all()
    assert cards == []


# ---------- scheduler integration ----------

def test_scheduler_can_register_and_remove_trigger_job():
    """Mirror of ``test_cron_schedule_and_remove`` for the recurring-trigger
    job-id namespace. The trigger ID is the job id, not the row id."""
    svc = SchedulerService()
    svc.schedule_recurring_trigger(
        trigger_id=42,
        cron_expr="0 9 * * 1",
        tz="Europe/Brussels",
    )
    assert svc.has_recurring_trigger(42) is True
    svc.remove_recurring_trigger(42)
    assert svc.has_recurring_trigger(42) is False
