"""Regression tests for the ``awaiting_plan_ref`` deadline + escalation.

The race window between an analyst's step 3 (create_card) and step 4
(add_plan_attachment) is genuinely seconds-wide — but the hold had no
upper bound, so a crashed analyst run parked its children indefinitely
without a single signal. The card-five-stuck-for-16-days report (kanban
kaart 2341a40e…) is the originating incident.

Two regressions must hold simultaneously:

  1. A child whose hold has been ``awaiting_plan_ref`` for longer than
     ``dep_resolver.PLAN_REF_DEADLINE_SECONDS`` is escalated exactly once.
  2. A child whose ``plan_ref`` lands within the deadline (the normal
     race) is never escalated.

The deadline itself lives in ``dep_resolver`` so the threshold has one
home; the escalation itself posts a comment + sets an idempotent
metadata marker — light-touch, self-healing, and visible from the
activity feed without moving the card off Backlog.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.kanban import dispatch
from app.kanban.db import KanbanSessionLocal
from app.kanban.dep_resolver import (
    HOLD_AWAITING_PLAN_REF,
    PLAN_REF_DEADLINE_SECONDS,
)
from app.kanban.operations import apply_operation
from app.kanban.service import list_cards

PK = "git:example.com/me/repo"


async def _card(s, title="Task", column="Backlog", **payload):
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None, payload={"title": title, "column": column},
    )
    if payload:
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload=payload,
        )
    await s.flush()
    return cid


async def _comments_for(s, card_id):
    """Activity-feed comments live in ``kanban_ops`` (entity_type='comment',
    op_type='comment') — the same wire the dispatcher posts through. Reading
    from the table directly keeps the test independent of any
    service-layer helper that may grow behind it."""
    rows = (await s.execute(
        text(
            "SELECT payload FROM kanban_ops "
            "WHERE entity_type='comment' AND entity_id=:cid "
            "ORDER BY hlc"
        ),
        {"cid": card_id},
    )).fetchall()
    return [json.loads(r[0])["text"] for r in rows]


async def _card_meta(s, card_id):
    raw = (await s.execute(
        text("SELECT metadata FROM kanban_cards WHERE id=:cid"),
        {"cid": card_id},
    )).fetchone()[0]
    if raw is None:
        return None
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


async def _force_hold(s, card_id, reason, *, held_since: datetime | None):
    """Stamp a card's hold state directly so the test does not depend on
    the tick running first. Independent of ``_persist_holds``'s
    "only stamp on change" logic so we can simulate an overdue card
    without sleeping."""
    stamp = held_since.isoformat() if held_since else None
    await s.execute(
        text(
            "UPDATE kanban_cards "
            "SET held_reason=:r, held_since=:s, held_blocker=:b "
            "WHERE id=:cid"
        ),
        {"r": reason, "s": stamp, "b": None, "cid": card_id},
    )
    await s.commit()


@pytest.mark.asyncio
async def test_overdue_hold_escalates_exactly_once():
    """A child whose awaiting_plan_ref hold has been on for longer than the
    deadline gets a single escalation comment + marker; subsequent ticks
    do NOT re-post until the hold resolves."""
    async with KanbanSessionLocal() as s:
        parent = await _card(s, title="parent", column="Awaiting Subtasks")
        child = await _card(s, title="child", column="Backlog", parent_card_id=parent)
        await s.commit()

    overdue = datetime.now(UTC) - timedelta(seconds=PLAN_REF_DEADLINE_SECONDS + 60)
    async with KanbanSessionLocal() as s:
        await _force_hold(s, child, HOLD_AWAITING_PLAN_REF, held_since=overdue)
        cards = await list_cards(s, PK)
        targets = [c for c in cards if c.id == child]

        # First tick: must escalate.
        first = await dispatch._escalate_overdue_plan_ref(
            s, project_key=PK, cards=targets,
        )
        await s.commit()
    assert first == [child], "first tick must escalate the overdue child"

    async with KanbanSessionLocal() as s:
        comments = await _comments_for(s, child)
        marker = await _card_meta(s, child)
    assert len(comments) == 1, "exactly one escalation comment"
    assert comments[0].startswith("**Plan overdue:**"), (
        "comment uses the conventional bold-prefix so the activity-feed "
        "reader can spot plan-overdue events at a glance; got "
        f"{comments[0]!r}"
    )
    assert "plan_ref" in comments[0] or "plan attachment" in comments[0]
    assert marker is not None and marker.get("plan_ref_overdue_at"), (
        "idempotency marker set so the next tick does not re-post"
    )

    # Second tick (within the same hold): must NOT re-post.
    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        targets = [c for c in cards if c.id == child]
        second = await dispatch._escalate_overdue_plan_ref(
            s, project_key=PK, cards=targets,
        )
        await s.commit()
    assert second == [], "second tick within the same hold is a no-op"

    async with KanbanSessionLocal() as s:
        comments = await _comments_for(s, child)
    assert len(comments) == 1, "still exactly one comment after the second tick"


@pytest.mark.asyncio
async def test_hold_within_race_window_does_not_escalate():
    """The original create_card → add_plan_attachment race is seconds wide.
    A hold that is younger than the deadline is the *normal* dispatchable
    pipeline, not the regression — must not escalate."""
    async with KanbanSessionLocal() as s:
        parent = await _card(s, title="parent", column="Awaiting Subtasks")
        child = await _card(s, title="child", column="Backlog", parent_card_id=parent)
        await s.commit()

    fresh = datetime.now(UTC) - timedelta(seconds=5)
    async with KanbanSessionLocal() as s:
        await _force_hold(s, child, HOLD_AWAITING_PLAN_REF, held_since=fresh)
        cards = await list_cards(s, PK)
        targets = [c for c in cards if c.id == child]
        result = await dispatch._escalate_overdue_plan_ref(
            s, project_key=PK, cards=targets,
        )
        await s.commit()

    assert result == [], "fresh hold is the normal race window, not overdue"
    async with KanbanSessionLocal() as s:
        comments = await _comments_for(s, child)
    assert comments == []


@pytest.mark.asyncio
async def test_top_level_card_without_plan_ref_does_not_escalate():
    """A top-level card (no parent_card_id) never carries a plan_ref by
    design; the hold never fires for it, and the escalation must skip it
    regardless of how old its other holds are."""
    async with KanbanSessionLocal() as s:
        # No parent_card_id → no plan_ref is expected.
        card_id = await _card(s, title="standalone", column="Backlog")
        await s.commit()

    # Stale unrelated hold to prove the escalator keys on reason, not age.
    stale = datetime.now(UTC) - timedelta(days=30)
    async with KanbanSessionLocal() as s:
        await _force_hold(s, card_id, "dependent", held_since=stale)
        cards = await list_cards(s, PK)
        targets = [c for c in cards if c.id == card_id]
        result = await dispatch._escalate_overdue_plan_ref(
            s, project_key=PK, cards=targets,
        )
        await s.commit()

    assert result == [], "non-awaiting_plan_ref holds never escalate here"


@pytest.mark.asyncio
async def test_hold_resolving_clears_the_overdue_marker():
    """When the plan_ref finally arrives, the hold clears. The marker must
    not survive into the next hold cycle — a *new* overdue event on the
    same card (e.g. parent re-runs without attaching) re-escalates."""
    async with KanbanSessionLocal() as s:
        parent = await _card(s, title="parent", column="Awaiting Subtasks")
        child = await _card(s, title="child", column="Backlog", parent_card_id=parent)
        await s.commit()

    overdue = datetime.now(UTC) - timedelta(seconds=PLAN_REF_DEADLINE_SECONDS + 60)
    async with KanbanSessionLocal() as s:
        await _force_hold(s, child, HOLD_AWAITING_PLAN_REF, held_since=overdue)
        cards = await list_cards(s, PK)
        await dispatch._escalate_overdue_plan_ref(
            s, project_key=PK, cards=[c for c in cards if c.id == child],
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        marker = await _card_meta(s, child)
    assert marker.get("plan_ref_overdue_at"), "pre-condition: marker set"

    # Plan_ref finally arrives: link the plan_ref deliverable, then run
    # the tick path so the hold re-classifies to None. The marker must
    # NOT survive into the next hold cycle.
    async with KanbanSessionLocal() as s:
        await apply_operation(
            s, op_type="link_plan_ref", entity_type="deliverable",
            project_key=PK, entity_id=child,
            payload={"ref_json": json.dumps({"parent_card_id": parent})},
        )
        await s.commit()
        # Expire the session so the pre-link card's `deliverables`
        # relationship gets re-populated by the next list_cards — mirrors
        # the CLAUDE.md gotcha around `_reload` (selectinload does NOT
        # re-populate a relationship already loaded on an instance in the
        # identity-map, so without this the child would still appear
        # planless to classify_hold).
        s.expire_all()
        cards = await list_cards(s, PK)
        await dispatch._persist_holds(s, cards, {c.id for c in cards})
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        await dispatch._escalate_overdue_plan_ref(
            s, project_key=PK, cards=[c for c in cards if c.id == child],
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        marker = await _card_meta(s, child)
        comments = await _comments_for(s, child)
    assert not marker or marker.get("plan_ref_overdue_at") is None, (
        "marker cleared once the hold resolves"
    )
    assert len(comments) == 1, "no extra comment for a now-resolved hold"
