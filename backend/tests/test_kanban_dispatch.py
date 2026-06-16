# backend/tests/test_kanban_dispatch.py
import pytest
import pytest_asyncio

from tests.kanban_test_db import TestSessionLocal, reset_test_tables
from app.kanban.operations import apply_operation
from app.kanban.service import get_card
from app.kanban import dispatch

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


PK = "git:example.com/me/repo"


async def _make_card(s, title="Task", column="Todo"):
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None, payload={"title": title, "column": column},
    )
    await s.flush()
    return cid


class RecordingTransport:
    """A real (non-mock) transport that records calls and returns a session."""
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, *, directory, prompt, session_name):
        self.calls.append({"directory": directory, "prompt": prompt, "session_name": session_name})
        if self.fail:
            raise RuntimeError("tmux exploded")
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}


# ---- enablement (device-local, KanbanMeta-backed) -------------------------

@pytest.mark.asyncio
async def test_autodispatch_disabled_by_default():
    async with KanbanSessionLocal() as s:
        assert await dispatch.is_autodispatch_enabled(s, PK) is False


@pytest.mark.asyncio
async def test_set_and_list_autodispatch():
    async with KanbanSessionLocal() as s:
        await dispatch.set_autodispatch(s, PK, True)
        await s.commit()
        assert await dispatch.is_autodispatch_enabled(s, PK) is True
        assert PK in await dispatch.list_autodispatch_projects(s)
        await dispatch.set_autodispatch(s, PK, False)
        await s.commit()
        assert await dispatch.is_autodispatch_enabled(s, PK) is False
        assert PK not in await dispatch.list_autodispatch_projects(s)


# ---- prompt ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_prompt_includes_persona_card_and_shipmode():
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="Build widget")
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=cid, payload={"description": "Make it blue"})
        await s.flush()
        card = await get_card(s, cid)
    prompt = dispatch.build_card_prompt(
        card, persona="You are the Developer agent.", ship_mode="direct",
    )
    assert "You are the Developer agent." in prompt
    assert "Build widget" in prompt
    assert "Make it blue" in prompt
    assert "Ship mode: direct" in prompt
    assert "cockpit-kanban" in prompt


def test_card_prompt_without_persona_still_works():
    class _C:
        title = "T"
        description = ""
    prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="pull-request")
    assert "Ship mode: pull-request" in prompt
    assert "# T" in prompt


# ---- dispatch_project: the core ------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_claims_moves_to_doing_and_spawns():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/home/me/repo", transport=transport,
        )
        await s.commit()
        card = await get_card(s, cid)
    assert result is not None
    assert card.column == "Doing"
    assert card.claimed_by.startswith("agent:")
    assert len(transport.calls) == 1
    # claimant label == the spawned session name
    assert transport.calls[0]["session_name"] == card.claimed_by.split("agent:", 1)[1]
    assert transport.calls[0]["directory"] == "/home/me/repo"


@pytest.mark.asyncio
async def test_dispatch_picks_first_todo_by_rank():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        first = await _make_card(s, title="A")
        await _make_card(s, title="B")
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        card = await get_card(s, first)
    assert card.column == "Doing"        # first card got picked
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_dispatch_skips_when_project_already_busy():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # a card already being worked by an agent
        busy = await _make_card(s, title="busy", column="Doing")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy, payload={"claimed_by": "agent:k-x-0001"},
        )
        await _make_card(s, title="waiting", column="Todo")
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
    assert result is None
    assert transport.calls == []


@pytest.mark.asyncio
async def test_dispatch_no_todo_cards_is_a_noop():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="done", column="Done")
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
    assert result is None
    assert transport.calls == []


@pytest.mark.asyncio
async def test_dispatch_skips_already_claimed_todo_card():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="claimed")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "someone@else"},
        )
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
    assert result is None
    assert transport.calls == []


@pytest.mark.asyncio
async def test_spawn_failure_releases_and_returns_card_to_todo():
    transport = RecordingTransport(fail=True)
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        with pytest.raises(RuntimeError):
            await dispatch.dispatch_project(
                s, project_key=PK, project_path="/p", transport=transport,
            )
        await s.commit()
        card = await get_card(s, cid)
    assert card.column == "Todo"          # compensated back
    assert card.claimed_by is None        # claim released
    assert len(transport.calls) == 1


# ---- project_key -> local path matching -----------------------------------

def test_match_project_paths_maps_enabled_keys_to_local_paths():
    keys = {"git:h/a", "git:h/b"}
    paths = ["/x/a", "/x/b", "/x/c"]
    fake_key_of = {"/x/a": "git:h/a", "/x/b": "git:h/b", "/x/c": "git:h/c"}.get
    out = dispatch.match_project_paths(keys, paths, key_of=fake_key_of)
    assert out == {"git:h/a": "/x/a", "git:h/b": "/x/b"}


@pytest.mark.asyncio
async def test_dispatch_picks_analysis_with_analyst_persona(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "kanban-analyst.md").write_text("You are the Analyst.")
    t = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="Investigate", column="Analysis")
        await s.commit()
    async with KanbanSessionLocal() as s:
        res = await dispatch.dispatch_project(
            s, project_key=PK, project_path=str(tmp_path), transport=t)
        await s.commit()
    assert res is not None
    assert "You are the Analyst." in t.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_dispatch_prefers_todo_over_analysis(tmp_path):
    t = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="A-card", column="Analysis")
        await _make_card(s, title="T-card", column="Todo")
        await s.commit()
    async with KanbanSessionLocal() as s:
        res = await dispatch.dispatch_project(
            s, project_key=PK, project_path=str(tmp_path), transport=t)
        await s.commit()
    assert res is not None
    assert "T-card" in t.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_dispatch_injects_ship_mode(tmp_path):
    t = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await dispatch.set_ship_mode(s, PK, "direct")
        await _make_card(s, title="T-card", column="Todo")
        await s.commit()
    async with KanbanSessionLocal() as s:
        await dispatch.dispatch_project(
            s, project_key=PK, project_path=str(tmp_path), transport=t)
        await s.commit()
    assert "Ship mode: direct" in t.calls[0]["prompt"]


def test_worktree_transport_creates_from_origin_master(monkeypatch, tmp_path):
    ran = []

    def fake_run(cmd, *a, **k):
        ran.append(cmd)
        class R:
            returncode = 0
            stderr = ""
        return R()

    captured = {}

    def fake_spawn(provider_id, options, session_name=None):
        captured["provider"] = provider_id
        captured["options"] = options
        captured["session_name"] = session_name
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}

    import app.kanban.dispatch as d
    monkeypatch.setattr(d.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "app.services.agent_bridge.spawn.spawn_session", fake_spawn)

    res = d.worktree_transport(
        directory=str(tmp_path), prompt="hi", session_name="k-proj-abcd")

    fetches = [c for c in ran if "fetch" in c]
    adds = [c for c in ran if "worktree" in c and "add" in c]
    assert fetches and adds
    assert "origin/master" in adds[0]
    opts = captured["options"]
    assert opts.mode == "plain"
    assert opts.skip_permissions is True
    assert opts.repo_path == str(tmp_path)
    assert opts.worktree_path == opts.directory
    assert res["session_name"] == "k-proj-abcd"


def test_worktree_transport_removes_worktree_when_spawn_fails(monkeypatch, tmp_path):
    ran = []

    def fake_run(cmd, *a, **k):
        ran.append(cmd)
        class R:
            returncode = 0
            stderr = ""
        return R()

    def fake_spawn(provider_id, options, session_name=None):
        raise RuntimeError("tmux exploded")

    import app.kanban.dispatch as d
    monkeypatch.setattr(d.subprocess, "run", fake_run)
    monkeypatch.setattr("app.services.agent_bridge.spawn.spawn_session", fake_spawn)

    with pytest.raises(RuntimeError):
        d.worktree_transport(
            directory=str(tmp_path), prompt="hi", session_name="k-proj-abcd")

    removes = [c for c in ran if "worktree" in c and "remove" in c]
    assert removes, "expected the orphaned worktree to be removed on spawn failure"


def test_mint_session_name_fits_tmux_sanitizer_limit():
    # a long project name must still yield a <=20-char session name, otherwise the
    # tmux-bridge sanitizer truncates it and cleanup/claimant labels diverge.
    name = dispatch._mint_session_name("/home/me/a-very-long-repository-name-here")
    assert len(name) <= 20
    assert name.startswith("k-")


@pytest.mark.asyncio
async def test_spawn_failure_returns_analysis_card_to_analysis():
    transport = RecordingTransport(fail=True)
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="Investigate", column="Analysis")
        await s.commit()
        with pytest.raises(RuntimeError):
            await dispatch.dispatch_project(
                s, project_key=PK, project_path="/p", transport=transport,
            )
        await s.commit()
        card = await get_card(s, cid)
    assert card.column == "Analysis"      # compensated back to its source column
    assert card.claimed_by is None
