import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from app.kanban import session_cleanup
from app.kanban.models import KanbanCard
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.mark.asyncio
async def test_cancel_sandcastle_run_cancels_matching_running_run(monkeypatch):
    cancelled = []

    class FakeRun:
        id = 7
        branch = "k-foo-1234"
        status = "running"

    async def fake_find(session_name):
        return FakeRun() if session_name == "k-foo-1234" else None

    async def fake_cancel(run_id):
        cancelled.append(run_id)
        return True

    monkeypatch.setattr(session_cleanup, "_find_running_sandcastle_run", fake_find)
    monkeypatch.setattr(
        session_cleanup.sandcastle_service, "cancel_run", fake_cancel, raising=False
    )

    ok = await session_cleanup._cancel_sandcastle_run("k-foo-1234")
    assert ok is True
    assert cancelled == [7]


@pytest.mark.asyncio
async def test_cancel_sandcastle_run_noop_when_no_run(monkeypatch):
    async def fake_find(session_name):
        return None

    monkeypatch.setattr(session_cleanup, "_find_running_sandcastle_run", fake_find)
    assert await session_cleanup._cancel_sandcastle_run("k-none-0000") is False


# ---- cleanup_session_for_card: tmux-dead handling -------------------------


async def _seed_card(s, session_name: str = "k-test-1234") -> str:
    """Create a card with an agent claim and return its id."""
    from app.kanban.operations import apply_operation

    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key="git:test/repo",
        entity_id=None, payload={"title": "Test", "column": "engineer"},
    )
    await apply_operation(
        s, op_type="claim", entity_type="card", project_key="",
        entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
    )
    await s.flush()
    return cid


@pytest.mark.asyncio
async def test_cleanup_continues_when_tmux_already_dead(monkeypatch):
    """When the tmux session is already dead (agent exited naturally),
    cleanup should still remove the worktree and release the claim."""
    monkeypatch.setattr(session_cleanup, "_kill_tmux_session", lambda _: False)
    monkeypatch.setattr(session_cleanup, "_remove_worktree_at", lambda _a, _b: True)
    monkeypatch.setattr(session_cleanup, "_get_project_path", AsyncMock(return_value="/tmp/test-repo"))

    async with KanbanSessionLocal() as s:
        cid = await _seed_card(s)
        await s.commit()

    result = await session_cleanup.cleanup_session_for_card(cid, "git:test/repo")

    assert result["cleaned"] is True
    assert result["tmux_killed"] is False
    assert result["session_name"] == "k-test-1234"

    # Verify the claim was released
    async with KanbanSessionLocal() as s:
        card = await s.get(KanbanCard, cid)
        assert card is not None
        assert card.claimed_by is None


@pytest.mark.asyncio
async def test_cleanup_releases_claim_even_on_tmux_dead(monkeypatch):
    """The claim should always be released, even if tmux kill fails."""
    monkeypatch.setattr(session_cleanup, "_kill_tmux_session", lambda _: False)
    monkeypatch.setattr(session_cleanup, "_remove_worktree_at", lambda _a, _b: True)
    monkeypatch.setattr(session_cleanup, "_get_project_path", AsyncMock(return_value="/tmp/test-repo"))

    async with KanbanSessionLocal() as s:
        cid = await _seed_card(s)
        await s.commit()

    await session_cleanup.cleanup_session_for_card(cid, "git:test/repo")

    async with KanbanSessionLocal() as s:
        card = await s.get(KanbanCard, cid)
        assert card.claimed_by is None


@pytest.mark.asyncio
async def test_cleanup_removes_worktree_when_tmux_dead(monkeypatch):
    """Worktree should still be removed when tmux session is already dead."""
    removed = []

    def track_remove(session_name, project_path):
        removed.append((session_name, project_path))
        return True

    monkeypatch.setattr(session_cleanup, "_kill_tmux_session", lambda _: False)
    monkeypatch.setattr(session_cleanup, "_remove_worktree_at", track_remove)
    monkeypatch.setattr(session_cleanup, "_get_project_path", AsyncMock(return_value="/tmp/test-repo"))

    async with KanbanSessionLocal() as s:
        cid = await _seed_card(s)
        await s.commit()

    await session_cleanup.cleanup_session_for_card(cid, "git:test/repo")

    assert len(removed) == 1
    assert removed[0] == ("k-test-1234", "/tmp/test-repo")
