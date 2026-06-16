# backend/tests/test_kanban_dispatch.py
import pytest
import pytest_asyncio

from app.kanban.db import KanbanBase, kanban_engine, KanbanSessionLocal
from app.kanban.operations import apply_operation
from app.kanban.service import get_card
from app.kanban import dispatch


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with kanban_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.drop_all)
        await conn.run_sync(KanbanBase.metadata.create_all)
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
    assert "git-ship" in prompt
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
