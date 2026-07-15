# backend/tests/test_kanban_session_recovery.py
"""Resume interrupted agent sessions after a host/backend restart."""
import os

import pytest
import pytest_asyncio

from app.kanban import session_recovery as recovery
from app.kanban.operations import apply_operation
from app.kanban.service import get_card
from app.utils.path_utils import convert_path_to_folder_name
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()

PK = "git:example.com/me/repo"


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _make_card(s, title="Task", column="Backlog"):
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None, payload={"title": title, "column": column},
    )
    await s.flush()
    return cid


async def _claim(s, cid, claimant):
    await apply_operation(
        s, op_type="claim", entity_type="card", project_key=PK,
        entity_id=cid, payload={"claimed_by": claimant},
    )


# ---- _recoverable predicate ----------------------------------------------

class _Card:
    def __init__(self, column, claimed_by, transport=None):
        self.column = column
        self.claimed_by = claimed_by
        self.transport = transport


@pytest.mark.parametrize("card, expected", [
    (_Card("engineer", "agent:k-dead-1"), True),
    (_Card("developer", "agent:k-dead-2"), True),
    (_Card("engineer", "agent:k-alive-1"), False),          # live session
    (_Card("Backlog", "agent:k-dead-3"), False),            # fixed column
    (_Card("Done", "agent:k-dead-4"), False),               # fixed column
    (_Card("Impediment", "agent:k-dead-5"), False),         # fixed column
    (_Card("engineer", "me@ui"), False),                    # human claim
    (_Card("engineer", None), False),                       # unclaimed
    (_Card("engineer", "agent:k-dead-6", "sandcastle"), False),  # no local worktree
])
def test_recoverable_predicate(card, expected):
    assert recovery._recoverable(card, {"k-alive-1"}) is expected


# ---- _resolve_resume_target ----------------------------------------------

def test_resolve_resume_target_picks_most_recent_transcript(tmp_path):
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / "k-x-0001"
    worktree.mkdir(parents=True)
    folder = convert_path_to_folder_name(str(worktree))
    folder_dir = tmp_path / "projects" / folder
    folder_dir.mkdir(parents=True)
    old = folder_dir / "1111.jsonl"
    new = folder_dir / "2222.jsonl"
    old.write_text("{}")
    new.write_text("{}")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))

    result = recovery._resolve_resume_target(
        str(repo), "k-x-0001", projects_dir=tmp_path / "projects")
    assert result == ("2222", folder)


def test_resolve_resume_target_none_without_worktree(tmp_path):
    result = recovery._resolve_resume_target(
        str(tmp_path / "repo"), "k-missing", projects_dir=tmp_path / "projects")
    assert result is None


def test_resolve_resume_target_none_without_transcript(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".claude" / "worktrees" / "k-y-0002").mkdir(parents=True)
    result = recovery._resolve_resume_target(
        str(repo), "k-y-0002", projects_dir=tmp_path / "projects")
    assert result is None


# ---- recover_project ------------------------------------------------------

@pytest.mark.asyncio
async def test_recover_project_resumes_dead_session():
    calls = []

    async def fake_redispatch(session, *, card_id, project_path):
        card = await get_card(session, card_id)
        calls.append({
            "card_id": card_id,
            "project_path": project_path,
            "resume_session_id": card.resume_session_id,
            "resume_project_folder": card.resume_project_folder,
        })
        return {"card_id": card_id, "session_name": "k-new-9999"}

    def fake_resolve(project_path, session_name):
        return ("sess-abc", f"-p--claude-worktrees-{session_name}")

    async with KanbanSessionLocal() as s:
        dead = await _make_card(s, title="wip", column="engineer")
        await _claim(s, dead, "agent:k-dead-0001")
        await s.commit()
        recovered = await recovery.recover_project(
            s, project_key=PK, project_path="/p", live_sessions=set(),
            resolve=fake_resolve, redispatch=fake_redispatch,
        )
        await s.commit()

    assert len(recovered) == 1
    assert len(calls) == 1
    assert calls[0]["card_id"] == dead
    assert calls[0]["project_path"] == "/p"
    # Resume fields are persisted BEFORE redispatch reads the card, so the
    # resume transport is selected.
    assert calls[0]["resume_session_id"] == "sess-abc"
    assert calls[0]["resume_project_folder"] == "-p--claude-worktrees-k-dead-0001"


@pytest.mark.asyncio
async def test_recover_project_skips_when_no_resumable_transcript():
    called = []

    async def fake_redispatch(session, *, card_id, project_path):
        called.append(card_id)
        return {"card_id": card_id}

    async with KanbanSessionLocal() as s:
        dead = await _make_card(s, title="wip", column="engineer")
        await _claim(s, dead, "agent:k-dead-0002")
        await s.commit()
        recovered = await recovery.recover_project(
            s, project_key=PK, project_path="/p", live_sessions=set(),
            resolve=lambda p, n: None, redispatch=fake_redispatch,
        )
        await s.commit()
        card = await get_card(s, dead)

    assert recovered == []
    assert called == []
    # The claim is left untouched for the reaper's existing behaviour.
    assert card.claimed_by == "agent:k-dead-0002"
    assert card.resume_session_id is None


@pytest.mark.asyncio
async def test_recover_project_ignores_live_human_and_fixed_columns():
    called = []

    async def fake_redispatch(session, *, card_id, project_path):
        called.append(card_id)
        return {"card_id": card_id}

    async with KanbanSessionLocal() as s:
        alive = await _make_card(s, title="alive", column="engineer")
        await _claim(s, alive, "agent:k-alive-0001")
        human = await _make_card(s, title="human", column="developer")
        await _claim(s, human, "me@ui")
        done = await _make_card(s, title="done", column="Done")
        await _claim(s, done, "agent:k-dead-done")
        await s.commit()
        recovered = await recovery.recover_project(
            s, project_key=PK, project_path="/p",
            live_sessions={"k-alive-0001"},
            resolve=lambda p, n: ("s", "f"), redispatch=fake_redispatch,
        )
        await s.commit()

    assert recovered == []
    assert called == []


@pytest.mark.asyncio
async def test_recover_project_respects_session_budget(monkeypatch):
    """Startup recovery must never resume more sessions than the shared
    hardware-aware session budget allows -- otherwise a project with more dead
    claims than that budget (e.g. via repeated dev-server restarts) would burst
    them all back to life at once. After dropping the per-project cap, the
    budget comes from session_registry.effective_max_sessions."""
    calls = []

    async def fake_redispatch(session, *, card_id, project_path):
        calls.append(card_id)
        return {"card_id": card_id, "session_name": f"k-new-{card_id}"}

    class FakeRegistry:
        effective_max_sessions = 2

        @property
        def session_count(self):
            return 0

    fake_registry = FakeRegistry()
    from app.services.scheduling import session_registry as registry_mod
    monkeypatch.setattr(registry_mod, "session_registry", fake_registry)

    async with KanbanSessionLocal() as s:
        dead_cards = [
            await _make_card(s, title=f"wip-{i}", column="engineer")
            for i in range(3)
        ]
        for i, cid in enumerate(dead_cards):
            await _claim(s, cid, f"agent:k-dead-{i}")
        await s.commit()

        recovered = await recovery.recover_project(
            s, project_key=PK, project_path="/p", live_sessions=set(),
            resolve=lambda p, n: ("s", "f"), redispatch=fake_redispatch,
        )
        await s.commit()

    # Budget is 2 -> only 2 of the 3 dead sessions may be resumed this pass.
    assert len(recovered) == 2
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_recover_project_counts_live_sessions_against_session_budget(monkeypatch):
    """A card whose session is already live still occupies a session-budget
    slot, so recovery must leave that slot out of its budget for resuming dead
    ones."""
    calls = []

    async def fake_redispatch(session, *, card_id, project_path):
        calls.append(card_id)
        return {"card_id": card_id, "session_name": f"k-new-{card_id}"}

    class FakeRegistry:
        effective_max_sessions = 2

        @property
        def session_count(self):
            return 0

    fake_registry = FakeRegistry()
    from app.services.scheduling import session_registry as registry_mod
    monkeypatch.setattr(registry_mod, "session_registry", fake_registry)

    async with KanbanSessionLocal() as s:
        alive = await _make_card(s, title="alive", column="engineer")
        await _claim(s, alive, "agent:k-alive-0001")
        dead_cards = [
            await _make_card(s, title=f"wip-{i}", column="engineer")
            for i in range(2)
        ]
        for i, cid in enumerate(dead_cards):
            await _claim(s, cid, f"agent:k-dead-{i}")
        await s.commit()

        recovered = await recovery.recover_project(
            s, project_key=PK, project_path="/p", live_sessions={"k-alive-0001"},
            resolve=lambda p, n: ("s", "f"), redispatch=fake_redispatch,
        )
        await s.commit()

    # Budget is 2, one slot already taken by the live session -> only 1 free.
    assert len(recovered) == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_recover_project_survives_redispatch_failure():
    async def boom_redispatch(session, *, card_id, project_path):
        raise RuntimeError("spawn exploded")

    async with KanbanSessionLocal() as s:
        a = await _make_card(s, title="a", column="engineer")
        await _claim(s, a, "agent:k-dead-a")
        b = await _make_card(s, title="b", column="engineer")
        await _claim(s, b, "agent:k-dead-b")
        await s.commit()
        # Must not raise even though every redispatch fails.
        recovered = await recovery.recover_project(
            s, project_key=PK, project_path="/p", live_sessions=set(),
            resolve=lambda p, n: ("s", "f"), redispatch=boom_redispatch,
        )
        await s.commit()

    assert recovered == []
