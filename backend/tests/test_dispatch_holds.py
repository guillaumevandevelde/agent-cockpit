"""Dispatcher-level tests for persisted hold state.

Two properties, both of which the board depended on and neither of which was
observable before:

1. The tick records *why* it passed a card over, so a held card stops being
   indistinguishable from one that was never a candidate.
2. The plan-ref gate does not follow a card into an agent column. That leak is
   what made three finished-but-unreviewed cards invisible to the reviewer's
   orphan-rescue for five days.
"""
import pytest

from app.kanban import dispatch
from app.kanban.db import KanbanSessionLocal
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


async def _holds_by_id(s):
    return {c.id: (c.held_reason, c.held_since, c.held_blocker) for c in await list_cards(s, PK)}


@pytest.mark.asyncio
async def test_persist_holds_records_reason_blocker_and_clock():
    async with KanbanSessionLocal() as s:
        parent = await _card(s, title="parent", column="Backlog")
        child = await _card(s, title="child", column="Backlog", depends_on=[parent])
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        await dispatch._persist_holds(s, cards, {c.id for c in cards})
        await s.commit()

    async with KanbanSessionLocal() as s:
        holds = await _holds_by_id(s)

    reason, since, blocker = holds[child]
    assert reason == "dependent"
    assert blocker == [parent]
    # The clock is the point: an unclaimed card had no notion of elapsed time.
    assert since is not None
    # An unblocked card carries no hold.
    assert holds[parent][0] is None


@pytest.mark.asyncio
async def test_persist_holds_keeps_the_clock_running_across_ticks():
    """A hold that persists must keep its original timestamp, or a stuck card
    looks perpetually fresh and never reads as stuck."""
    async with KanbanSessionLocal() as s:
        parent = await _card(s, title="parent", column="Backlog")
        await _card(s, title="child", column="Backlog", depends_on=[parent])
        await s.commit()

    for _ in range(2):
        async with KanbanSessionLocal() as s:
            cards = await list_cards(s, PK)
            await dispatch._persist_holds(s, cards, {c.id for c in cards})
            await s.commit()
        async with KanbanSessionLocal() as s:
            stamps = [h[1] for h in (await _holds_by_id(s)).values() if h[0]]

    assert len(set(stamps)) == 1


@pytest.mark.asyncio
async def test_persist_holds_clears_the_hold_once_the_blocker_is_done():
    async with KanbanSessionLocal() as s:
        parent = await _card(s, title="parent", column="Backlog")
        child = await _card(s, title="child", column="Backlog", depends_on=[parent])
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        await dispatch._persist_holds(s, cards, {c.id for c in cards})
        await apply_operation(
            s, op_type="move", entity_type="card", project_key=PK,
            entity_id=parent, payload={"column": "Done"},
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        await dispatch._persist_holds(s, cards, {c.id for c in cards})
        await s.commit()

    async with KanbanSessionLocal() as s:
        holds = await _holds_by_id(s)
    assert holds[child] == (None, None, None)


@pytest.mark.asyncio
async def test_next_card_rescues_an_executed_card_stranded_in_an_agent_column():
    """The reviewer regression, end to end.

    A child that was executed (branch deliverable, sitting in `reviewer`) but
    never received a plan_ref must be selectable by the orphan-rescue arm. The
    phase-blind gate excluded it there, so it waited on a plan it no longer had
    any use for while the reviewer never saw it.
    """
    async with KanbanSessionLocal() as s:
        parent = await _card(s, title="parent", column="Awaiting Subtasks")
        child = await _card(s, title="child", column="reviewer", parent_card_id=parent)
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        picked = dispatch._next_card(cards, {c.id for c in cards})

    assert picked is not None, "executed card in an agent column stayed invisible"
    assert picked.id == child


@pytest.mark.asyncio
async def test_next_card_still_holds_a_planless_child_in_a_board_column():
    """The guard the gate was written for must survive the scoping: a freshly
    created child in Backlog still waits for its analyst's plan."""
    async with KanbanSessionLocal() as s:
        parent = await _card(s, title="parent", column="Awaiting Subtasks")
        await _card(s, title="child", column="Backlog", parent_card_id=parent)
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        picked = dispatch._next_card(cards, {c.id for c in cards})

    assert picked is None
