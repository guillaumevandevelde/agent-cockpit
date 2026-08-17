"""The dispatch tick must not hold the SQLite write lock across its slow phase.

Root cause this pins down: SQLite serialises writers, and `dispatch_project`
writes early (stale-claim reaping, the liveness detectors, `_persist_holds`,
`_escalate_overdue_plan_ref`) but used to commit only at the very end of the
tick. The single write lock was therefore held through the per-card resolution
loop and all of `_run_card`'s pre-spawn work — tens of seconds in which every
other writer on the board fails.

Observed consequence: `POST /kanban/cards` exhausted its 5s `busy_timeout` and
returned an unhandled 500, five times inside one two-minute tick
(`logs/backend/run-20260817-082951-3592-0.log`). Reproduced against the live
backend by holding the lock for 8s: the request failed after exactly 5.03s.

The probe below asks the only question that matters, from a genuinely separate
connection: *can another writer get in?*
"""
import asyncio
import sqlite3

import pytest

from app.kanban import dispatch
from app.kanban.operations import apply_operation
from tests.kanban_test_db import TestSessionLocal, _db_path

PK = "git:example"


def _write_lock_is_free() -> bool:
    """True when no other connection holds the SQLite write lock.

    A short `timeout` keeps this fast in *both* directions: when the lock is
    held (the bug) it gives up in 0.2s instead of sitting out the 5s
    `busy_timeout`, so a regression fails quickly rather than looking like a
    hung test.
    """
    con = sqlite3.connect(_db_path, timeout=0.2, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("ROLLBACK")
        return True
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return False
        raise
    finally:
        con.close()


@pytest.mark.asyncio
async def test_tick_releases_the_write_lock_before_the_spawn_phase(monkeypatch):
    """By the time the tick reaches `_run_card`, other writers must be able in.

    `_run_card` is where the slow work lives (persona resolution, ceremony
    profile, prior-branch git, prompt injectors, plan lookup, a 2s endpoint
    HTTP probe, then a ~37s spawn). Probing at its entry proves the tick
    handed off without the lock still in its pocket.
    """
    observed: list[bool] = []

    async def fake_run_card(session, **kwargs):
        # Probe from a worker thread, the way a real competing writer arrives
        # (another request, the MCP server). Running the blocking sqlite3 call
        # on the event-loop thread instead would stall aiosqlite's own result
        # delivery, which deadlocks the very connection we are asking about.
        observed.append(await asyncio.to_thread(_write_lock_is_free))
        card = kwargs["card"]
        # Claim the card the way the real `_run_card` does. The tick's loop
        # re-fetches the board after every successful dispatch, so a stub that
        # leaves the card unclaimed makes it pick the same card forever.
        await apply_operation(
            session, op_type="claim", entity_type="card", project_key=PK,
            entity_id=card.id, payload={"claimed_by": f"agent:tmux-{card.id}"},
        )
        return {
            "card_id": card.id,
            "session_name": f"tmux-{card.id}",
            "claimant": f"agent:tmux-{card.id}",
            "source_column": "Backlog",
            "spawned": True,
        }

    monkeypatch.setattr(dispatch, "_run_card", fake_run_card)

    SessionLocal = TestSessionLocal()
    async with SessionLocal() as s:
        await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={"title": "dispatch me", "column": "Backlog"},
        )
        # A second, held card so the sweep phase has hold state to write —
        # without a writer up front, the tick would trivially "pass" while
        # never taking the lock at all.
        await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={
                "title": "held by a dangling dep",
                "column": "Backlog",
                "depends_on": ["0" * 32],
            },
        )
        await s.commit()

        await dispatch.dispatch_project(s, project_key=PK, project_path="/tmp/none")
        await s.commit()

    assert observed, "_run_card was never reached; the fixture no longer dispatches"
    assert all(observed), (
        "the dispatch tick still held the SQLite write lock when it entered "
        "_run_card — concurrent board writes (POST /kanban/cards) will 500 "
        "for the whole spawn window"
    )


@pytest.mark.asyncio
async def test_sweep_phase_writes_are_durable_after_the_tick(monkeypatch):
    """Committing mid-tick must persist the sweep's work, not drop it.

    The mid-tick commits are only safe if each phase's effect stands on its
    own; this asserts the hold state written before the commit survives.
    """
    async def fake_run_card(session, **kwargs):
        card = kwargs["card"]
        return {
            "card_id": card.id,
            "session_name": f"tmux-{card.id}",
            "claimant": f"agent:tmux-{card.id}",
            "source_column": "Backlog",
            "spawned": True,
        }

    monkeypatch.setattr(dispatch, "_run_card", fake_run_card)

    from app.kanban.models import KanbanCard

    SessionLocal = TestSessionLocal()
    async with SessionLocal() as s:
        held_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={
                "title": "held by a dangling dep",
                "column": "Backlog",
                "depends_on": ["0" * 32],
            },
        )
        await s.commit()
        await dispatch.dispatch_project(s, project_key=PK, project_path="/tmp/none")
        await s.commit()

    # Fresh session: reads the committed row, not this session's identity map.
    async with SessionLocal() as s2:
        held = await s2.get(KanbanCard, held_id)
        assert held.held_reason, (
            "the sweep phase's hold state was lost — mid-tick commit dropped it"
        )
