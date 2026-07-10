"""End-to-end integration test for the multi-agent kanban flow (Task 14).

Drives the real ``dispatch_project`` entry point with a stubbed ``_run_card``
that records every spawn and mirrors ``_run_card``'s claim+move side effects so
the dispatcher's tick loop can make its own decisions about picking the next
card (rather than re-picking the same card forever). No real tmux session is
spawned.

  1. Tick 1: analyst spawns on parent (analyst_run_id set by dispatcher).
  2. Analyst ops attach a plan deliverable + plan_ref + depends_on (Task 8 ops).
  3. Parent moves to Done.
  4. Tick 2: child 1 dispatched (executor); child 2 skipped (deps unmet).
  5. Move child 1 to Done.
  6. Tick 3: child 2 dispatched (executor).
"""
import pytest

from app.kanban import dispatch
from app.kanban.models import KanbanCard
from app.kanban.operations import apply_operation


@pytest.mark.asyncio
async def test_multi_agent_flow(monkeypatch):
    """End-to-end multi-agent flow driven through ``dispatch_project``."""
    spawned = []

    async def fake_run_card(session, **kwargs):
        """Record the dispatch and mirror ``_run_card``'s claim + column move.

        The real ``_run_card`` claims the card with ``agent:<session_name>`` and
        moves it from the dispatch column to the target agent column. Those two
        side effects are what stop ``_next_card`` from re-picking the same card
        on the next iteration of the dispatcher's tick loop. The stub omits the
        actual ``spawn_session`` call (no tmux / no worktree) but still returns
        a result dict so ``dispatch_project`` writes ``analyst_run_id`` itself.

        IMPORTANT: the dict MUST mirror the real ``_run_card`` return shape
        (see dispatch.py: ``{"card_id", "session_name", "claimant",
        "source_column", "spawned"}``) — NOT the old ``{"session": ...}`` shape
        that the buggy dispatch tick used to read. Using the old shape would
        silently re-introduce the Critical C1 regression.
        """
        card = kwargs["card"]
        spawned.append((kwargs["phase"], card.id))
        session_name = f"tmux-{kwargs['phase']}-{card.id[:6]}"
        target_col = "analyst" if kwargs["phase"] == "analyst" else "engineer"
        card.claimed_by = dispatch.CLAIMANT_PREFIX + session_name
        card.column = target_col
        # Leave claimed_at undefined for the stub: a real claim op stamps
        # claimed_at server-side, and the integration test only inspects
        # analyst_run_id / spawn order — asserting on the timestamp would
        # couple this test to internal claim-side-effects.
        await session.flush()
        return {
            "card_id": card.id,
            "session_name": session_name,
            "claimant": dispatch.CLAIMANT_PREFIX + session_name,
            "source_column": "Backlog",
            "spawned": True,
        }

    monkeypatch.setattr(dispatch, "_run_card", fake_run_card)

    from tests.kanban_test_db import TestSessionLocal

    KanbanSessionLocal = TestSessionLocal()
    PK = "git:example"

    async with KanbanSessionLocal() as s:
        # --- Setup: parent with analyst + executor agent config -----------
        parent_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "parent", "column": "Backlog",
                     "analyst_agent_id": "claude-code",
                     "executor_agent_id": "mimo-code"},
        )
        await s.commit()

        # --- Tick 1: drive the real dispatcher ----------------------------
        # dispatch_project picks the only Backlog card, sees analyst_agent_id
        # + no analyst_run_id, calls _run_card (faked), then sets
        # analyst_run_id itself. Verifying here proves the dispatcher's own
        # branch ran -- we never assign analyst_run_id by hand.
        await dispatch.dispatch_project(s, project_key=PK, project_path="/tmp/x")
        await s.commit()

        parent = await s.get(KanbanCard, parent_id)
        assert parent.analyst_run_id, "dispatcher did not set analyst_run_id"
        assert spawned == [("analyst", parent_id)]

        # --- Analyst ops: plan deliverable + child plan_refs + depends_on -
        import json as _json

        from sqlalchemy import select

        from app.kanban.models import KanbanDeliverable

        c1 = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "c1", "column": "Backlog",
                     "parent_card_id": parent_id},
        )
        c2 = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "c2", "column": "Backlog",
                     "parent_card_id": parent_id},
        )

        await apply_operation(
            s, op_type="add_plan_attachment", entity_type="deliverable",
            project_key=PK, entity_id=parent_id,
            payload={"plan_markdown": "# Plan\nc1 first, then c2"},
        )
        plan_id = (await s.execute(
            select(KanbanDeliverable)
            .where(KanbanDeliverable.card_id == parent_id,
                   KanbanDeliverable.kind == "plan")
        )).scalars().first().id

        for cid, deps in ((c1, []), (c2, [c1])):
            await apply_operation(
                s, op_type="link_plan_ref", entity_type="deliverable",
                project_key=PK, entity_id=cid,
                payload={
                    "ref_json": _json.dumps({
                        "parent_card_id": parent_id,
                        "plan_deliverable_id": plan_id,
                    }),
                    "depends_on": deps,
                },
            )

        # Move parent to Done (the analyst signals handoff this way once the
        # plan + children are wired up).
        await apply_operation(
            s, op_type="move", entity_type="card",
            project_key=PK, entity_id=parent_id,
            payload={"column": "Done"},
        )
        await s.commit()

        # --- Tick 2: dispatcher should pick c1 (no deps); c2 is gated -----
        await dispatch.dispatch_project(s, project_key=PK, project_path="/tmp/x")
        await s.commit()
        # Drop the analyst/parent claim cleanup latch: now c1 is dispatched.
        assert ("executor", c1) in spawned
        assert ("executor", c2) not in spawned, "c2 must be gated on c1"

        # --- c1 done: clear the way for c2 --------------------------------
        await apply_operation(
            s, op_type="move", entity_type="card",
            project_key=PK, entity_id=c1,
            payload={"column": "Done"},
        )
        await s.commit()

        # --- Tick 3: c2 deps now met -> dispatch ---------------------------
        await dispatch.dispatch_project(s, project_key=PK, project_path="/tmp/x")
        await s.commit()

    # Source of truth: the exact spawn order across all three ticks.
    assert spawned == [
        ("analyst", parent_id),
        ("executor", c1),
        ("executor", c2),
    ]
