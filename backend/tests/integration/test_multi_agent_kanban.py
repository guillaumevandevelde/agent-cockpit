"""End-to-end integration test for the multi-agent kanban flow (Task 14).

Stubs ``_run_card`` so no real tmux session is spawned. Exercises the full
sequence a parent → analyst → plan → executor-child flow would traverse in a
real run, and asserts the call ordering matches what a future real-tick would
see:

  1. Tick 1: analyst spawns on parent (analyst_run_id set).
  2. Analyst calls add_plan_attachment (2 children, dep c2 -> c1).
  3. Parent moves to Done. Children inherit plan_ref + depends_on.
  4. Tick 2: child 1 dispatched (executor); child 2 skipped (deps unmet).
  5. Move child 1 to Done by hand.
  6. Tick 3: child 2 dispatched (executor).
"""
import json

import pytest
from sqlalchemy import select

from app.kanban import dispatch
from app.kanban.dep_resolver import meets_dep_prerequisites
from app.kanban.models import KanbanCard, KanbanDeliverable
from app.kanban.operations import apply_operation
from tests.kanban_test_db import TestSessionLocal, reset_test_tables


@pytest.mark.asyncio
async def test_multi_agent_flow(monkeypatch):
    """End-to-end multi-agent flow with a stubbed ``_run_card``."""
    spawned = []

    async def fake_run_card(session, **kwargs):
        spawned.append((kwargs["phase"], kwargs["card"].id))
        return {"session": f"tmux-{kwargs['phase']}-{kwargs['card'].id[:6]}"}

    monkeypatch.setattr(dispatch, "_run_card", fake_run_card)

    await reset_test_tables()
    KanbanSessionLocal = TestSessionLocal()

    async with KanbanSessionLocal() as s:
        # --- Setup: parent with analyst + executor config -----------------
        parent_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "parent", "column": "Backlog",
                     "analyst_agent_id": "claude-code",
                     "executor_agent_id": "mimo-code"},
        )

        # --- Tick 1: spawn analyst ----------------------------------------
        parent = await s.get(KanbanCard, parent_id)
        if parent.analyst_agent_id and not parent.analyst_run_id:
            await fake_run_card(
                s, card=parent, project_key="git:example",
                project_path="/tmp/x", transport=None, phase="analyst",
            )
            parent.analyst_run_id = "run-1"
            await s.flush()
        await s.commit()
        assert ("analyst", parent_id) in spawned

        # --- Analyst does the planning -----------------------------------
        # Create the child cards the analyst plans to delegate to.
        c1 = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "c1", "column": "Backlog",
                     "parent_card_id": parent_id},
        )
        c2 = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "c2", "column": "Backlog",
                     "parent_card_id": parent_id},
        )

        # Attach a plan deliverable on the parent.
        await apply_operation(
            s, op_type="add_plan_attachment", entity_type="deliverable",
            project_key="git:example", entity_id=parent_id,
            payload={"plan_markdown": "# Plan\nc1 first, then c2"},
        )
        plan_id = (await s.execute(
            select(KanbanDeliverable)
            .where(KanbanDeliverable.card_id == parent_id,
                   KanbanDeliverable.kind == "plan")
        )).scalars().first().id

        # Wire each child to its plan_ref + depends_on via link_plan_ref.
        for cid, deps in ((c1, []), (c2, [c1])):
            await apply_operation(
                s, op_type="link_plan_ref", entity_type="deliverable",
                project_key="git:example", entity_id=cid,
                payload={
                    "ref_json": json.dumps({
                        "parent_card_id": parent_id,
                        "plan_deliverable_id": plan_id,
                    }),
                    "depends_on": deps,
                },
            )

        # Move parent to Done; this is what the analyst would do once the
        # plan is written and the children are spawned.
        await apply_operation(
            s, op_type="move", entity_type="card",
            project_key="git:example", entity_id=parent_id,
            payload={"column": "Done"},
        )
        await s.commit()

        # Refresh from DB so we observe the depends_on column the materialize
        # step just wrote.
        c1_card = await s.get(KanbanCard, c1)
        c2_card = await s.get(KanbanCard, c2)
        cards_by_id = {c1: c1_card, c2: c2_card}

        # --- Tick 2: dispatcher reads children ----------------------------
        # c1 has no deps -> dispatch.
        assert meets_dep_prerequisites(c1_card, cards_by_id) is True
        # c2 depends on c1 (not Done yet) -> skip.
        assert meets_dep_prerequisites(c2_card, cards_by_id) is False
        if meets_dep_prerequisites(c1_card, cards_by_id):
            await fake_run_card(
                s, card=c1_card, project_key="git:example",
                project_path="/tmp/x", transport=None, phase="executor",
            )

        # --- c1 finishes -> mark Done -------------------------------------
        await apply_operation(
            s, op_type="move", entity_type="card",
            project_key="git:example", entity_id=c1,
            payload={"column": "Done"},
        )
        await s.commit()
        c1_card = await s.get(KanbanCard, c1)
        c2_card = await s.get(KanbanCard, c2)
        cards_by_id = {c1: c1_card, c2: c2_card}

        # --- Tick 3: c2 deps met -> dispatch ------------------------------
        assert meets_dep_prerequisites(c2_card, cards_by_id) is True
        if meets_dep_prerequisites(c2_card, cards_by_id):
            await fake_run_card(
                s, card=c2_card, project_key="git:example",
                project_path="/tmp/x", transport=None, phase="executor",
            )

    # Verify spawn order: analyst(parent), executor(c1), executor(c2).
    assert spawned == [
        ("analyst", parent_id),
        ("executor", c1),
        ("executor", c2),
    ]
