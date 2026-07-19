"""Terminal-move cleanup must survive the synchronous claim-clear race.

Regression for kanban card 7b63463e. `_materialize` has two independent
pieces of code that react to a move into a fixed column:

1. `_cleanup_after_commit` schedules the real tmux-kill / worktree-remove /
   claim-release async after commit; and
2. a synchronous "claim-clear on move into any fixed column except Done" block
   that sets `card.claimed_by = None` *in the same transaction*.

For a Done→non-Done terminal move (`Impediment`, and since card 04f7c427 also
`Awaiting Subtasks`) (2) wiped `claimed_by` before (1)'s fire-time DB read, so
cleanup saw `claimed_by=None`, returned `{"error": "no_agent_session"}`, and
never killed the tmux session or removed the worktree.

Unlike `test_kanban_session_cleanup_triggers.py`, these tests do **not** mock
`cleanup_session_for_card` — they let the real cleanup run and spy on the leaf
`_kill_tmux_session` / `_remove_worktree_at` calls, so the raced fresh read is
actually exercised. The fix captures `claimed_by` at schedule time and threads
it through, so the leaf calls fire with the correct session name.
"""
import asyncio

import pytest
import pytest_asyncio

from app.kanban import session_cleanup
from app.kanban.db import KanbanSessionLocal
from app.kanban.operations import apply_operation
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.fixture
def cleanup_spies(monkeypatch):
    """Record the leaf tmux/worktree calls and short-circuit their subprocesses.

    Patched on the `session_cleanup` module — the consumer — because
    `cleanup_session_for_card` calls these as module-level names. Sandcastle
    lookup is forced to "no run" so cleanup takes the tmux path, and the
    project path is stubbed so worktree removal is reached.
    """
    killed: list[str] = []
    removed: list[tuple[str, str]] = []

    def fake_kill(session_name: str) -> bool:
        killed.append(session_name)
        return True

    def fake_remove(session_name: str, project_path: str) -> bool:
        removed.append((session_name, project_path))
        return True

    async def no_sandcastle(_session_name: str) -> bool:
        return False

    async def fake_path(_project_key: str) -> str:
        return "/fake/project"

    monkeypatch.setattr(session_cleanup, "_kill_tmux_session", fake_kill)
    monkeypatch.setattr(session_cleanup, "_remove_worktree_at", fake_remove)
    monkeypatch.setattr(session_cleanup, "_cancel_sandcastle_run", no_sandcastle)
    monkeypatch.setattr(session_cleanup, "resolve_project_path", fake_path)
    return killed, removed


async def _wait_for(collected: list, *, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not collected and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
@pytest.mark.parametrize("column", ["Impediment", "Awaiting Subtasks"])
async def test_terminal_move_from_agent_column_kills_tmux_and_worktree(
    cleanup_spies, column
):
    """A card claimed in an agent column and moved to a non-Done terminal
    column must still have its real tmux session killed and worktree removed
    with the *claimed* session name — not silently skipped because the
    synchronous claim-clear wiped `claimed_by` before the cleanup read it."""
    killed, removed = cleanup_spies
    session_name = "k-test-race-1234"

    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:test/repo", entity_id=None,
            payload={"title": "X", "description": "", "column": "Doing"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card",
            project_key="", entity_id=cid,
            payload={"claimed_by": f"agent:{session_name}"},
        )
        await apply_operation(
            s, op_type="move", entity_type="card",
            project_key="", entity_id=cid, payload={"column": column},
        )
        await s.commit()

    await _wait_for(killed)

    assert killed == [session_name], killed
    assert removed == [(session_name, "/fake/project")], removed


@pytest.mark.asyncio
async def test_synchronous_claim_clear_still_wipes_claimed_by(cleanup_spies):
    """The fix keeps the synchronous claim-clear (which prevents an orphaned
    claim on a reopenable fixed column) — cleanup no longer depends on it."""
    killed, _removed = cleanup_spies

    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:test/repo", entity_id=None,
            payload={"title": "X", "description": "", "column": "Doing"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card",
            project_key="", entity_id=cid,
            payload={"claimed_by": "agent:k-test-race-5678"},
        )
        await apply_operation(
            s, op_type="move", entity_type="card",
            project_key="", entity_id=cid, payload={"column": "Impediment"},
        )
        await s.commit()

    await _wait_for(killed)

    from app.kanban.models import KanbanCard
    async with KanbanSessionLocal() as s:
        # The synchronous clear ran in the move transaction; cleanup's real
        # tmux-kill still fired via the captured claim.
        assert (await s.get(KanbanCard, cid)).claimed_by is None
    assert killed == ["k-test-race-5678"], killed
