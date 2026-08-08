"""REST CRUD for server-side recurring triggers.

Lives under ``/recurring-triggers``. Distinct from
``/scheduled-messages`` (tmux-inject) — those are two different mechanisms,
even though the names rhyme. The decision doc §5.1 is the source of truth.
"""
import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.recurring_trigger import RecurringTrigger
from app.models.recurring_trigger_schemas import (
    RecurringTriggerCreate,
    RecurringTriggerResponse,
    RecurringTriggerUpdate,
)
from app.services.scheduling.scheduler import scheduler_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recurring-triggers", tags=["Recurring Triggers"])


def _register(trigger: RecurringTrigger) -> None:
    """Sync APScheduler state with the persisted trigger row.

    Mirrors ``scheduled_messages.router._register`` but for the cron-only
    recurring-trigger namespace: there's no once/cron split, and the
    scheduler's job-id prefix is different to keep the namespaces
    disjoint (``sched-msg-`` vs ``recurring-``).
    """
    scheduler_service.remove_recurring_trigger(trigger.id)
    if trigger.enabled:
        scheduler_service.schedule_recurring_trigger(
            trigger.id, trigger.cron_expr, trigger.timezone,
        )


@router.post(
    "", response_model=RecurringTriggerResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create(payload: RecurringTriggerCreate):
    async with AsyncSessionLocal() as s:
        trigger = RecurringTrigger(**payload.model_dump())
        s.add(trigger)
        await s.commit()
        await s.refresh(trigger)
        _register(trigger)
        return RecurringTriggerResponse.model_validate(trigger)


@router.get("", response_model=dict)
async def list_all():
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(RecurringTrigger).order_by(RecurringTrigger.id.desc())
        )).scalars().all()
    return {"items": [RecurringTriggerResponse.model_validate(r) for r in rows]}


@router.patch(
    "/{tid}", response_model=RecurringTriggerResponse,
)
async def update(tid: int, payload: RecurringTriggerUpdate):
    async with AsyncSessionLocal() as s:
        trigger = await s.get(RecurringTrigger, tid)
        if not trigger:
            raise HTTPException(404, "not found")
        for k, v in payload.model_dump(exclude_unset=True).items():
            setattr(trigger, k, v)
        await s.commit()
        await s.refresh(trigger)
        _register(trigger)
        return RecurringTriggerResponse.model_validate(trigger)


@router.delete("/{tid}")
async def delete(tid: int):
    async with AsyncSessionLocal() as s:
        trigger = await s.get(RecurringTrigger, tid)
        if not trigger:
            raise HTTPException(404, "not found")
        scheduler_service.remove_recurring_trigger(tid)
        await s.delete(trigger)
        await s.commit()
        return {"deleted": True}
