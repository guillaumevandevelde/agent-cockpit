"""Service layer for server-side recurring triggers.

The trigger's *contract*: ``fire_trigger_by_id(trigger_id)`` either creates
exactly one Backlog card and returns its id, or returns ``None`` when the
trigger is disabled, already-fired-for-this-occurrence, or missing. APScheduler
calls it on each cron tick; the inhaal-on-boot path calls it once at startup
to cover missed occurrences.

Coalescing rules — every code path that updates ``last_fired_at`` first reads
the previous occurrence of the cron expression and only writes when
``last_fired_at`` is older than that occurrence. That makes a double-fire
within the same occurrence (two APScheduler ticks, or a tick + the inhaal
landing on the same occurrence) a single card; an entirely later occurrence
starts a new card.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.kanban import service
from app.kanban.db import KanbanSessionLocal
from app.kanban.operations import apply_operation
from app.models.recurring_trigger import RecurringTrigger

logger = logging.getLogger(__name__)


def previous_occurrence(cron_expr: str, tz: str, now: datetime) -> datetime:
    """Return the most recent cron occurrence at-or-before ``now``.

    Uses croniter (already in requirements as a transitive dep of
    apscheduler) for the actual cron parsing; apscheduler's CronTrigger
    only exposes ``get_next_fire_time`` and would need an O(N) walk to
    find the previous one.
    """
    zone = ZoneInfo(tz)
    # croniter works with naive OR aware datetimes. Pin to tz so a
    # naive ``now`` doesn't get reinterpreted as UTC.
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    local_now = now.astimezone(zone)
    it = croniter(cron_expr, local_now)
    prev_local = it.get_prev(datetime)
    return prev_local.astimezone(UTC)


def next_occurrence(cron_expr: str, tz: str, now: datetime) -> datetime:
    zone = ZoneInfo(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    local_now = now.astimezone(zone)
    it = croniter(cron_expr, local_now)
    return it.get_next(datetime).astimezone(UTC)


async def _create_backlog_card_from_trigger(
    s, trigger: RecurringTrigger, metadata_extra: dict | None = None,
) -> str:
    """Create a Backlog card via the same op-log flow the REST/MCP paths use.

    Standalone (no parent_card_id) — a child without a plan_ref would be
    held by ``_awaiting_plan_ref`` and never dispatched (kaart d5b363dd…,
    the po-digest chain that's the canonical example of this bug).
    """
    agent = await service.resolve_create_agent(
        s, trigger.project_key,
        work_type=trigger.work_type, explicit_agent=trigger.agent,
    )
    metadata: dict[str, Any] = {"source": "recurring_trigger", "trigger_id": trigger.id}
    if trigger.metadata_:
        metadata.update(trigger.metadata_)
    if metadata_extra:
        metadata.update(metadata_extra)
    cid = await apply_operation(
        s, op_type="create", entity_type="card",
        project_key=trigger.project_key, entity_id=None,
        payload={
            "title": trigger.title,
            "description": trigger.description,
            "column": "Backlog",
            "agent": agent,
            "work_type": trigger.work_type,
            "labels": trigger.labels,
            "metadata": metadata,
        },
    )
    return cid


async def _should_fire(
    trigger: RecurringTrigger, now: datetime, occurrence: datetime,
) -> bool:
    """True when the trigger can legitimately fire for ``occurrence`` now.

    * disabled → no.
    * ``last_fired_at`` is at-or-after ``occurrence`` → already covered.
    * else → yes (boot inhaal OR a tick that's late enough).

    The coalescing invariant: ``last_fired_at`` moves forward by occurrence,
    so a second tick that lands on the same occurrence is rejected here and
    never creates a second card.
    """
    if not trigger.enabled:
        return False
    if trigger.last_fired_at is None:
        return True
    # last_fired_at stored as naive UTC in SQLite; treat as UTC-aware.
    last = trigger.last_fired_at if trigger.last_fired_at.tzinfo else \
        trigger.last_fired_at.replace(tzinfo=UTC)
    return last < occurrence


async def fire_trigger_by_id(
    trigger_id: int, *, now: datetime | None = None,
) -> str | None:
    """Fire the trigger for its *current* cron occurrence.

    Returns the new card id, or None when the trigger is disabled,
    already-fired-for-this-occurrence, or missing.

    Used by the APScheduler cron callback and by the boot-inhaal sweep.
    """
    if now is None:
        now = datetime.now(UTC)
    async with AsyncSessionLocal() as s:
        trigger = await s.get(RecurringTrigger, trigger_id)
        if trigger is None:
            return None
        occurrence = previous_occurrence(trigger.cron_expr, trigger.timezone, now)
        if not await _should_fire(trigger, now, occurrence):
            return None

        async with KanbanSessionLocal() as ks:
            cid = await _create_backlog_card_from_trigger(
                ks, trigger,
                metadata_extra={"occurrence": occurrence.isoformat()},
            )
            await ks.commit()

        # Mark the occurrence as fired. Persist before returning so a
        # second concurrent fire within the same occurrence sees
        # ``last_fired_at >= occurrence`` and coalesces.
        trigger.last_fired_at = occurrence
        await s.commit()

    logger.info(
        "recurring trigger %s fired → card %s (occurrence %s)",
        trigger_id, cid, occurrence.isoformat(),
    )
    return cid


async def run_boot_inhaal(*, now: datetime | None = None) -> int:
    """Cover missed cron occurrences once at startup.

    Walks every enabled trigger whose ``last_fired_at`` is older than the
    previous occurrence. Fires each at most once. ``fire_trigger_by_id``
    handles coalescing against the freshly-written ``last_fired_at``, so a
    second sweep (e.g. nested startup) is a no-op.

    Returns the number of catch-up cards created. N=0 is the common case:
    the previous occurrence is already covered.
    """
    if now is None:
        now = datetime.now(UTC)
    fired = 0
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(RecurringTrigger).where(RecurringTrigger.enabled == True)  # noqa: E712
        )).scalars().all()
        trigger_ids = [r.id for r in rows]

    for tid in trigger_ids:
        cid = await fire_trigger_by_id(tid, now=now)
        if cid is not None:
            fired += 1
    return fired
