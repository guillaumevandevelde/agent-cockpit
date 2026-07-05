import subprocess
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

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


def _git(*args, cwd):
    import os
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.test",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.test"}
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
        capture_output=True, text=True, env=env)


def _make_project_with_worktree(tmp_path, session_name="k-test-1234",
                                 ahead=1, dirty=False, merged=False):
    """Build a throwaway git repo at <tmp_path>/repo with a worktree at
    .claude/worktrees/<session_name> on its own branch, ``ahead`` commits past
    master. Returns the repo path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    (repo / "README.md").write_text("hello\n")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)

    worktree = repo / ".claude" / "worktrees" / session_name
    worktree.parent.mkdir(parents=True)
    _git("worktree", "add", "-b", "feature", str(worktree), cwd=repo)

    for i in range(ahead):
        (worktree / f"change{i}.txt").write_text("x\n")
        _git("add", f"change{i}.txt", cwd=worktree)
        _git("commit", "-m", f"change {i}", cwd=worktree)

    if merged:
        _git("merge", "--no-ff", "feature", cwd=repo)

    if dirty:
        (worktree / "untracked.txt").write_text("scratch\n")

    return repo


@pytest.mark.asyncio
async def test_find_worktree_unmerged_warning_detects_unmerged_commits(monkeypatch, tmp_path):
    repo = _make_project_with_worktree(tmp_path, ahead=2)
    monkeypatch.setattr(session_cleanup, "_get_project_path", AsyncMock(return_value=str(repo)))

    async with KanbanSessionLocal() as s:
        cid = await _seed_card(s)
        await s.commit()
        card = await s.get(KanbanCard, cid)
        warning = await session_cleanup.find_worktree_unmerged_warning(card)

    assert warning is not None
    assert warning["branch"] == "feature"
    assert warning["default_branch"] == "master"
    assert warning["ahead"] == 2
    assert warning["dirty"] is False


@pytest.mark.asyncio
async def test_find_worktree_unmerged_warning_none_when_merged_and_clean(monkeypatch, tmp_path):
    repo = _make_project_with_worktree(tmp_path, ahead=1, merged=True)
    monkeypatch.setattr(session_cleanup, "_get_project_path", AsyncMock(return_value=str(repo)))

    async with KanbanSessionLocal() as s:
        cid = await _seed_card(s)
        await s.commit()
        card = await s.get(KanbanCard, cid)
        warning = await session_cleanup.find_worktree_unmerged_warning(card)

    assert warning is None


@pytest.mark.asyncio
async def test_find_worktree_unmerged_warning_flags_dirty_even_when_merged(monkeypatch, tmp_path):
    repo = _make_project_with_worktree(tmp_path, ahead=1, merged=True, dirty=True)
    monkeypatch.setattr(session_cleanup, "_get_project_path", AsyncMock(return_value=str(repo)))

    async with KanbanSessionLocal() as s:
        cid = await _seed_card(s)
        await s.commit()
        card = await s.get(KanbanCard, cid)
        warning = await session_cleanup.find_worktree_unmerged_warning(card)

    assert warning is not None
    assert warning["ahead"] == 0
    assert warning["dirty"] is True


@pytest.mark.asyncio
async def test_find_worktree_unmerged_warning_none_without_worktree(monkeypatch):
    monkeypatch.setattr(session_cleanup, "_get_project_path", AsyncMock(return_value=None))

    async with KanbanSessionLocal() as s:
        cid = await _seed_card(s)
        await s.commit()
        card = await s.get(KanbanCard, cid)
        warning = await session_cleanup.find_worktree_unmerged_warning(card)

    assert warning is None
