# backend/tests/test_spawn_worktree_cleanup.py
import pytest
import pytest_asyncio

from app.kanban.models import KanbanCard
from app.kanban.operations import apply_operation
from app.services.runs import spawn as spawnmod
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


def test_kill_session_removes_dispatcher_worktree(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(cmd)
        class R:
            returncode = 0
            stderr = ""
        return R()

    monkeypatch.setattr(spawnmod.subprocess, "run", fake_run)
    spawnmod._spawned_sessions["k-test-1234"] = {
        "provider": "claude-code",
        "mode": "plain",
        "directory": str(tmp_path / "wt"),
        "worktree_name": None,
        "worktree_path": str(tmp_path / "wt"),
        "repo_path": str(tmp_path / "repo"),
        "platform": "anthropic",
    }
    spawnmod.kill_session("k-test-1234", cleanup_worktree=True)
    removes = [c for c in calls if "worktree" in c and "remove" in c]
    assert removes, "expected a git worktree remove call"
    assert str(tmp_path / "wt") in removes[0]
    assert "-C" in removes[0] and str(tmp_path / "repo") in removes[0]


@pytest.mark.asyncio
async def test_kill_session_does_not_delete_kanban_card(monkeypatch, tmp_path):
    """Kanban card a64bab6719fb4297b0ec2ffe4c063334 — when an agent bridge
    session is killed via the runs bridge (DELETE /api/v1/runs/sessions/{target}
    or its cc-bridge sibling), the kanban card claimed by that session must
    stay on the board. The bridge kill is a tmux + worktree lifecycle event;
    the card itself is the operator's source-of-truth and only moves to a
    terminal column via the explicit ``move_card`` action.
    """
    session_name = "k-bridge-a64b"
    card_id: str | None = None

    async with KanbanSessionLocal() as s:
        card_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example/me/repo", entity_id=None,
            payload={"title": "Bridge session claim target", "column": "Backlog"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key="",
            entity_id=card_id,
            payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()

    def fake_run(cmd, *a, **k):
        class R:
            returncode = 0
            stderr = ""
        return R()

    monkeypatch.setattr(spawnmod.subprocess, "run", fake_run)
    spawnmod._spawned_sessions[session_name] = {
        "provider": "claude-code",
        "mode": "plain",
        "directory": str(tmp_path / "wt"),
        "worktree_name": None,
        "worktree_path": str(tmp_path / "wt"),
        "repo_path": str(tmp_path / "repo"),
        "platform": "anthropic",
    }

    result = spawnmod.kill_session(session_name, cleanup_worktree=False)
    assert result["killed"] is True

    # The card is the operator-facing source of truth: a tmux kill from the
    # bridge UI must never silently drop the row it was attached to.
    async with KanbanSessionLocal() as s:
        card = await s.get(KanbanCard, card_id)
        assert card is not None, "kanban card must survive an agent bridge session kill"
        assert card.id == card_id
