# backend/tests/test_kanban_dispatch.py
import pytest
import pytest_asyncio

from tests.kanban_test_db import TestSessionLocal, reset_test_tables
from app.kanban.operations import apply_operation
from app.kanban.service import get_card, list_cards
from app.kanban import dispatch, service
from app.kanban.models import KanbanCard

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


PK = "git:example.com/me/repo"


async def _make_card(s, title="Task", column="Backlog", priority=None):
    payload = {"title": title, "column": column}
    if priority is not None:
        payload["priority"] = priority
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None, payload=payload,
    )
    await s.flush()
    return cid


class RecordingTransport:
    """A real (non-mock) transport that records calls and returns a session."""
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, *, directory, prompt, session_name, provider_id="claude-code"):
        self.calls.append({"directory": directory, "prompt": prompt,
                           "session_name": session_name, "provider_id": provider_id})
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
    assert card.column == "engineer"
    assert card.claimed_by.startswith("agent:")
    assert len(transport.calls) == 1
    # claimant label == the spawned session name
    assert transport.calls[0]["session_name"] == card.claimed_by.split("agent:", 1)[1]
    assert transport.calls[0]["directory"] == "/home/me/repo"


@pytest.mark.asyncio
async def test_dispatch_defaults_to_claude_code_provider():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider_id"] == "claude-code"


@pytest.mark.asyncio
async def test_dispatch_threads_card_provider_to_transport():
    """A provider id chosen in the UI selects the spawned CLI, but must not be
    mistaken for a persona/column (there is no `mimo-code` agent column)."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"agent": "mimo-code"},
        )
        await s.commit()
        result = await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
            agent_override="mimo-code",
        )
        await s.commit()
        card = await get_card(s, cid)
    assert result is not None
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider_id"] == "mimo-code"
    assert card.column == "engineer"  # provider id is NOT used as the column


@pytest.mark.asyncio
async def test_persona_override_still_routes_to_persona_column():
    """A non-provider agent_override (a persona name) keeps acting as the column."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
            agent_override="developer",
        )
        await s.commit()
        card = await get_card(s, cid)
    assert card.column == "developer"
    assert transport.calls[0]["provider_id"] == "claude-code"


@pytest.mark.asyncio
async def test_dispatch_picks_first_todo_by_rank():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        first = await _make_card(s, title="A")
        await _make_card(s, title="B")
        await dispatch.set_max_sessions(s, PK, 1)
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        card = await get_card(s, first)
    assert card.column == "engineer"        # first card got picked
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_dispatch_skips_when_project_already_busy():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # a card already being worked by an agent
        busy = await _make_card(s, title="busy", column="developer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy, payload={"claimed_by": "agent:k-x-0001"},
        )
        await _make_card(s, title="waiting", column="Backlog")
        await dispatch.set_max_sessions(s, PK, 1)
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
    assert result is None
    assert transport.calls == []


@pytest.mark.asyncio
async def test_dispatch_card_bypasses_busy_cap():
    """Manual dispatch_card runs a card even while the project is busy."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        busy = await _make_card(s, title="busy", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy, payload={"claimed_by": "agent:k-x-0001"},
        )
        target = await _make_card(s, title="manual", column="Backlog")
        await s.commit()
        result = await dispatch.dispatch_card(
            s, card_id=target, project_path="/p", transport=transport,
        )
        await s.commit()
        card = await get_card(s, target)
    assert result is not None
    assert card.column == "engineer"
    assert len(transport.calls) == 1


# ---- per-project session cap ----------------------------------------------

def _bare_card(column, claimed_by):
    c = KanbanCard(id="x", project_key=PK, title="t", description="",
                   column=column, rank="1")
    c.claimed_by = claimed_by
    return c


def test_active_session_count_counts_agent_claims_in_agent_columns():
    cards = [
        _bare_card("engineer", "agent:a"),
        _bare_card("review", "agent:b"),
        _bare_card("Backlog", "agent:c"),   # fixed column: excluded
        _bare_card("Done", "agent:d"),       # fixed column: excluded
        _bare_card("engineer", "me@ui"),     # human claim: excluded
        _bare_card("engineer", None),         # unclaimed: excluded
    ]
    assert dispatch._active_session_count(cards) == 2


@pytest.mark.asyncio
async def test_get_max_sessions_defaults_to_3():
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_max_sessions(s, PK) == dispatch.DEFAULT_MAX_SESSIONS
        assert dispatch.DEFAULT_MAX_SESSIONS == 3


@pytest.mark.asyncio
async def test_set_then_get_max_sessions_roundtrips():
    async with KanbanSessionLocal() as s:
        await dispatch.set_max_sessions(s, PK, 2)
        await s.commit()
        assert await dispatch.get_max_sessions(s, PK) == 2


@pytest.mark.asyncio
async def test_dispatch_fills_up_to_cap_then_stops():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await dispatch.set_max_sessions(s, PK, 2)
        for i in range(4):
            await _make_card(s, title=f"c{i}", column="Backlog")
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert result is not None
    assert len(transport.calls) == 2  # fills exactly the 2 free slots in one tick


@pytest.mark.asyncio
async def test_dispatch_freed_slot_is_reusable():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await dispatch.set_max_sessions(s, PK, 1)
        busy = await _make_card(s, title="busy", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy, payload={"claimed_by": "agent:k-x-0001"},
        )
        await _make_card(s, title="waiting", column="Backlog")
        await s.commit()
        # cap full -> no dispatch
        assert await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport) is None
        # free the slot, then exactly one dispatches
        await apply_operation(
            s, op_type="release", entity_type="card", project_key=PK,
            entity_id=busy, payload={})
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport)
        await s.commit()
    assert result is not None
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_retry_queued_cards_respects_per_project_cap(monkeypatch):
    """Retrying memory-queued cards must honour the per-project session cap.

    Regression: the retry path dispatched every retryable card, checking only the
    hardware/memory limit, so queued cards could push a project past its cap (e.g.
    3 running from the normal loop + 3 retried = 6 sessions)."""
    from types import SimpleNamespace
    from app.services.scheduling.pending_queue import PendingQueue
    import app.services.scheduling.pending_queue as pq_mod
    import app.kanban.db as kdb

    fresh_queue = PendingQueue()
    monkeypatch.setattr(pq_mod, "pending_queue", fresh_queue)
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)
    monkeypatch.setattr(
        dispatch, "get_memory_status_cached",
        lambda: SimpleNamespace(is_critical=False, usage_percent=0.1),
    )

    transport = RecordingTransport()
    ids = []
    async with KanbanSessionLocal() as s:
        await dispatch.set_max_sessions(s, PK, 2)
        for i in range(4):
            ids.append(await _make_card(s, title=f"q{i}", column="Backlog"))
        await s.commit()

    for cid in ids:
        fresh_queue.enqueue(card_id=cid, project_key=PK, project_path="/p")

    await dispatch._retry_queued_cards(transport)

    assert len(transport.calls) == 2  # never exceeds the cap of 2
    assert fresh_queue.size == 2       # the over-cap cards stay queued for later
    # A cap hold is not a failed dispatch, so it must not burn retry budget.
    assert all(c.retry_count == 0 for c in fresh_queue._queue.values())


@pytest.mark.asyncio
async def test_get_default_transport_defaults_to_worktree():
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_default_transport(s, PK) == "worktree"


@pytest.mark.asyncio
async def test_set_then_get_default_transport_roundtrips(monkeypatch):
    async def _noop(project_key, enabled):
        return None
    monkeypatch.setattr(dispatch, "_sync_sandcastle_enabled", _noop)
    async with KanbanSessionLocal() as s:
        await dispatch.set_default_transport(s, PK, "sandcastle")
        await s.commit()
        assert await dispatch.get_default_transport(s, PK) == "sandcastle"


@pytest.mark.asyncio
async def test_set_default_transport_rejects_unknown():
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await dispatch.set_default_transport(s, PK, "podman")


@pytest.mark.asyncio
async def test_get_transport_for_project_uses_meta_sandcastle(monkeypatch):
    async def _noop(project_key, enabled):
        return None
    monkeypatch.setattr(dispatch, "_sync_sandcastle_enabled", _noop)
    monkeypatch.setattr(dispatch, "_safe_resolve_key", lambda p: PK)
    async with KanbanSessionLocal() as s:
        await dispatch.set_default_transport(s, PK, "sandcastle")
        await s.commit()
    t = await dispatch.get_transport_for_project("/any/path")
    assert t is dispatch.sandcastle_transport


@pytest.mark.asyncio
async def test_get_transport_for_project_defaults_worktree(monkeypatch):
    monkeypatch.setattr(dispatch, "_safe_resolve_key", lambda p: PK)
    t = await dispatch.get_transport_for_project("/any/path")
    assert t is not dispatch.sandcastle_transport  # a worktree transport callable


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
    assert card.column == "Backlog"       # compensated back
    assert card.claimed_by is None        # claim released
    assert len(transport.calls) == 1


# ---- stale-claim reaping (tmux-liveness) ----------------------------------

@pytest.mark.asyncio
async def test_reaps_dead_agent_claim_in_doing_and_dispatches_next():
    # A session died without moving its card out of Doing. With no matching live
    # tmux session, the stale claim is released so the next Todo card dispatches.
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        dead = await _make_card(s, title="orphaned", column="developer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=dead, payload={"claimed_by": "agent:k-dead-0001"},
        )
        waiting = await _make_card(s, title="waiting", column="Backlog")
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
            live_sessions=set(),  # no live sessions -> the agent claim is stale
        )
        await s.commit()
        dead_card = await get_card(s, dead)
        waiting_card = await get_card(s, waiting)
    assert dead_card.claimed_by is None       # stale claim reaped
    assert dead_card.column == "developer"        # orphan left for a human to re-rank
    assert result is not None                 # cap freed -> next card dispatched
    assert waiting_card.column == "engineer"
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_live_agent_claim_in_doing_still_blocks():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        busy = await _make_card(s, title="busy", column="developer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy, payload={"claimed_by": "agent:k-alive-0001"},
        )
        await _make_card(s, title="waiting", column="Backlog")
        await dispatch.set_max_sessions(s, PK, 1)
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
            live_sessions={"k-alive-0001"},  # session is still alive
        )
        await s.commit()
        busy_card = await get_card(s, busy)
    assert result is None                          # still busy
    assert busy_card.claimed_by == "agent:k-alive-0001"
    assert transport.calls == []


@pytest.mark.asyncio
async def test_reaper_ignores_human_ui_claims():
    # A human-claimed (me@ui) Doing card is never reaped, even with no live sessions.
    async with KanbanSessionLocal() as s:
        human = await _make_card(s, title="human WIP", column="developer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=human, payload={"claimed_by": "me@ui"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK), live_sessions=set())
        await s.commit()
        card = await get_card(s, human)
    assert reaped == 0
    assert card.claimed_by == "me@ui"


@pytest.mark.asyncio
async def test_reaper_spares_live_sandcastle_claim_without_tmux():
    # A sandcastle-dispatched card has no tmux session, so tmux liveness can never
    # vouch for it. As long as its sandcastle run is active (its session name is in
    # sandcastle_live), the claim must NOT be reaped — otherwise the auto-dispatcher
    # releases and re-spawns it every tick.
    async with KanbanSessionLocal() as s:
        sc = await _make_card(s, title="sandcastle WIP", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=sc, payload={"claimed_by": "agent:k-sc-0001"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions=set(), sandcastle_live={"k-sc-0001"},
        )
        await s.commit()
        card = await get_card(s, sc)
    assert reaped == 0
    assert card.claimed_by == "agent:k-sc-0001"


@pytest.mark.asyncio
async def test_reaper_reaps_dead_sandcastle_claim():
    # When the sandcastle run is gone (not in sandcastle_live) and there is no tmux
    # session either, the stale claim is reaped like any other dead session.
    async with KanbanSessionLocal() as s:
        sc = await _make_card(s, title="sandcastle dead", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=sc, payload={"claimed_by": "agent:k-sc-dead"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions=set(), sandcastle_live=set(),
        )
        await s.commit()
        card = await get_card(s, sc)
    assert reaped == 1
    assert card.claimed_by is None


@pytest.mark.asyncio
async def test_dispatch_without_live_sessions_does_not_reap():
    # The default (live_sessions=None) preserves the old behavior: an agent claim
    # blocks the cap, because we never reap without a liveness snapshot.
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        busy = await _make_card(s, title="busy", column="developer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy, payload={"claimed_by": "agent:k-x-0001"},
        )
        await _make_card(s, title="waiting", column="Backlog")
        await dispatch.set_max_sessions(s, PK, 1)
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport)
        await s.commit()
        busy_card = await get_card(s, busy)
    assert result is None
    assert busy_card.claimed_by == "agent:k-x-0001"
    assert transport.calls == []


def test_live_sessions_parses_names(monkeypatch):
    import app.kanban.dispatch as d

    class R:
        returncode = 0
        stdout = "k-a-1\nk-b-2\n"
        stderr = ""
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: R())
    assert d._live_sessions() == {"k-a-1", "k-b-2"}


def test_live_sessions_empty_set_when_no_server(monkeypatch):
    import app.kanban.dispatch as d

    class R:
        returncode = 1
        stdout = ""
        stderr = "no server running on /tmp/tmux-1000/default"
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: R())
    assert d._live_sessions() == set()


def test_live_sessions_none_on_ambiguous_tmux_error(monkeypatch):
    # An ambiguous failure must yield None (skip reaping), never an empty set,
    # so a transient tmux hiccup can't release live claims.
    import app.kanban.dispatch as d

    class R:
        returncode = 2
        stdout = ""
        stderr = "tmux: unexpected error talking to server"
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: R())
    assert d._live_sessions() is None


def test_live_sessions_none_when_tmux_missing(monkeypatch):
    import app.kanban.dispatch as d

    def boom(*a, **k):
        raise FileNotFoundError("tmux")
    monkeypatch.setattr(d.subprocess, "run", boom)
    assert d._live_sessions() is None


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
    (agents / "analyst.md").write_text("You are the Analyst.")
    t = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="Investigate", column="Backlog")
        await apply_operation(s, op_type="update", entity_type="card",
            project_key=PK, entity_id=cid, payload={"agent": "analyst"})
        await s.commit()
    async with KanbanSessionLocal() as s:
        res = await dispatch.dispatch_project(
            s, project_key=PK, project_path=str(tmp_path), transport=t)
        await s.commit()
    assert res is not None
    assert "You are the Analyst." in t.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_dispatch_provider_id_falls_back_to_engineer(tmp_path):
    """card.agent = provider ID (e.g. 'mimo-code') must not create a non-existent column."""
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "engineer.md").write_text("You are the Engineer.")
    t = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="Task", column="Backlog")
        await apply_operation(s, op_type="update", entity_type="card",
            project_key=PK, entity_id=cid, payload={"agent": "mimo-code"})
        await s.commit()
    async with KanbanSessionLocal() as s:
        res = await dispatch.dispatch_project(
            s, project_key=PK, project_path=str(tmp_path), transport=t)
        await s.commit()
    assert res is not None
    card = await get_card(s, cid)
    assert card.column == "engineer"
    assert "You are the Engineer." in t.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_dispatch_prefers_todo_over_analysis(tmp_path):
    t = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="A-card", column="Backlog")
        await _make_card(s, title="T-card", column="Backlog")
        await s.commit()
    async with KanbanSessionLocal() as s:
        res = await dispatch.dispatch_project(
            s, project_key=PK, project_path=str(tmp_path), transport=t)
        await s.commit()
    assert res is not None
    assert "A-card" in t.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_dispatch_injects_ship_mode(tmp_path):
    t = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await dispatch.set_ship_mode(s, PK, "direct")
        await _make_card(s, title="T-card", column="Backlog")
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


def test_mint_session_name_uses_card_title():
    # Card title should be used for clarity when available.
    name = dispatch._mint_session_name("/home/me/project", card_title="Fix login bug")
    assert len(name) <= 20
    assert name.startswith("k-")
    assert "fix-login" in name


def test_mint_session_name_falls_back_to_project_path():
    # When no card title, project path should be used as before.
    name = dispatch._mint_session_name("/home/me/my-project")
    assert len(name) <= 20
    assert "my-project" in name


@pytest.mark.asyncio
async def test_spawn_failure_returns_analysis_card_to_analysis():
    transport = RecordingTransport(fail=True)
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="Investigate", column="Backlog")
        await s.commit()
        with pytest.raises(RuntimeError):
            await dispatch.dispatch_project(
                s, project_key=PK, project_path="/p", transport=transport,
            )
        await s.commit()
        card = await get_card(s, cid)
    assert card.column == "Backlog"      # compensated back to its source column
    assert card.claimed_by is None


# ---- redispatch: human override for stuck cards ----------------------------

@pytest.mark.asyncio
async def test_redispatch_releases_claim_and_respawns():
    """Re-dispatch a claimed card: release old claim, spawn new session."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-old-0001"},
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        result = await dispatch.redispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()
        card = await get_card(s, cid)
    assert result is not None
    assert card.claimed_by.startswith("agent:")
    assert card.claimed_by != "agent:k-old-0001"  # new session
    assert card.column == "engineer"
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_redispatch_unclaimed_card_dispatches_normally():
    """Re-dispatch an unclaimed card (e.g., after stale reaping) works like normal dispatch."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="orphan", column="engineer")
        # No claim - card was reaped
        await s.commit()
    async with KanbanSessionLocal() as s:
        result = await dispatch.redispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()
        card = await get_card(s, cid)
    assert result is not None
    assert card.claimed_by.startswith("agent:")
    assert card.column == "engineer"
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_redispatch_with_agent_override():
    """Re-dispatch with a different agent moves card to new agent's column."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck", column="developer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-old-0001"},
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        result = await dispatch.redispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
            agent_override="testing",
        )
        await s.commit()
        card = await get_card(s, cid)
    assert result is not None
    assert card.column == "testing"
    assert card.claimed_by.startswith("agent:")


@pytest.mark.asyncio
async def test_redispatch_returns_none_for_missing_card():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        result = await dispatch.redispatch_card(
            s, card_id="nonexistent", project_path="/p", transport=transport,
        )
    assert result is None
    assert transport.calls == []


@pytest.mark.asyncio
async def test_redispatch_all_orphans():
    """Batch redispatch: all unclaimed cards on agent columns get dispatched."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Two orphaned cards on agent columns (unclaimed)
        await _make_card(s, title="orphan1", column="developer")
        await _make_card(s, title="orphan2", column="testing")
        # One card that's fine (claimed)
        claimed = await _make_card(s, title="busy", column="developer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=claimed, payload={"claimed_by": "agent:k-alive-0001"},
        )
        # One card on Backlog (not orphaned)
        await _make_card(s, title="backlog", column="Backlog")
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.redispatch_all_orphans(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results) == 2
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_redispatch_all_no_orphans():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Only claimed cards
        claimed = await _make_card(s, title="busy", column="developer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=claimed, payload={"claimed_by": "agent:k-alive-0001"},
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.redispatch_all_orphans(
            s, project_key=PK, project_path="/p", transport=transport,
        )
    assert len(results) == 0
    assert transport.calls == []


# ---- dispatch_all_pending: batch dispatch from Backlog ---------------------

@pytest.mark.asyncio
async def test_dispatch_all_pending():
    """Batch dispatch: all unclaimed Backlog cards get dispatched."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="card1", column="Backlog")
        await _make_card(s, title="card2", column="Backlog")
        # claimed card should be skipped
        claimed = await _make_card(s, title="busy", column="Backlog")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=claimed, payload={"claimed_by": "me@ui"},
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results) == 2
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_dispatch_all_pending_empty():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Only Done cards
        await _make_card(s, title="done", column="Done")
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
    assert len(results) == 0
    assert transport.calls == []


# ---- count consistency: frontend vs backend vs actual dispatch ------------

from app.kanban.schemas import COLUMNS as _COLUMNS

_FIXED = set(_COLUMNS)


def _frontend_pending_count(cards) -> int:
    return sum(1 for c in cards if c.column == "Backlog" and not c.claimed_by)


def _frontend_orphan_count(cards) -> int:
    return sum(1 for c in cards if c.column not in _FIXED and not c.claimed_by)


@pytest.mark.asyncio
async def test_pending_count_matches_dispatch_results():
    """Frontend-style pending count = backend list_pending_cards = dispatch_all_pending results."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="a", column="Backlog")
        await _make_card(s, title="b", column="Backlog")
        await _make_card(s, title="c", column="Backlog")
        busy = await _make_card(s, title="busy", column="Backlog")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy, payload={"claimed_by": "me@ui"},
        )
        await _make_card(s, title="done", column="Done")
        await s.commit()
    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        fe = _frontend_pending_count(cards)
        backend = len(await service.list_pending_cards(s, PK))
        assert fe == 3, f"frontend pending={fe}"
        assert backend == 3, f"backend pending={backend}"
    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results) == 3, f"dispatched={len(results)}"


@pytest.mark.asyncio
async def test_orphan_count_matches_redispatch_results():
    """Frontend-style orphan count = backend list_orphaned_cards = redispatch_all_orphans results."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="o1", column="engineer")
        await _make_card(s, title="o2", column="testing")
        await _make_card(s, title="o3", column="code-review")
        busy = await _make_card(s, title="busy", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy, payload={"claimed_by": "agent:k-alive-0001"},
        )
        await _make_card(s, title="backlog", column="Backlog")
        await _make_card(s, title="blocked", column="Impediment")
        await _make_card(s, title="done2", column="Done")
        await s.commit()
    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        fe = _frontend_orphan_count(cards)
        backend = len(await service.list_orphaned_cards(s, PK))
        assert fe == 3, f"frontend orphans={fe}"
        assert backend == 3, f"backend orphans={backend}"
    async with KanbanSessionLocal() as s:
        results = await dispatch.redispatch_all_orphans(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results) == 3, f"redispatched={len(results)}"


@pytest.mark.asyncio
async def test_frontend_backend_claimed_by_unanimity():
    """Frontend `!c.claimed_by` and backend `claimed_by.is_(None)` must agree on all valid states."""
    async with KanbanSessionLocal() as s:
        c_unclaimed = await _make_card(s, title="none", column="engineer")
        c_human = await _make_card(s, title="human", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=c_human, payload={"claimed_by": "me@ui"},
        )
        c_agent = await _make_card(s, title="agented", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=c_agent, payload={"claimed_by": "agent:k-test-0001"},
        )
        await s.commit()

        card_none = await get_card(s, c_unclaimed)
        card_human = await get_card(s, c_human)
        card_agent = await get_card(s, c_agent)

        assert card_none.claimed_by is None
        assert not card_none.claimed_by

        assert card_human.claimed_by == "me@ui"
        assert card_human.claimed_by

        assert card_agent.claimed_by == "agent:k-test-0001"
        assert card_agent.claimed_by

        cards = await list_cards(s, PK)
        fe_orphans = _frontend_orphan_count(cards)
        be_orphans = len(await service.list_orphaned_cards(s, PK))
        assert fe_orphans == 1
        assert be_orphans == 1


@pytest.mark.asyncio
async def test_empty_string_claimed_by_causes_mismatch():
    """claimed_by='' (empty string): frontend treats as unclaimed, backend treats as claimed."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="bogus", column="engineer")
        await s.commit()

        from sqlalchemy import update
        from app.kanban.models import KanbanCard as KCModel
        await s.execute(
            update(KCModel).where(KCModel.id == cid).values(claimed_by="")
        )
        await s.commit()

        card = await get_card(s, cid)
        assert card.claimed_by == ""
        assert not card.claimed_by

        cards = await list_cards(s, PK)
        fe_orphans = _frontend_orphan_count(cards)
        be_orphans = len(await service.list_orphaned_cards(s, PK))
        assert fe_orphans == 1, f"frontend sees {fe_orphans} orphans"
        assert be_orphans == 0, f"backend sees {be_orphans} orphans"

        fe_pending = _frontend_pending_count(cards)
        be_pending = len(await service.list_pending_cards(s, PK))
        assert fe_pending == 0
        assert be_pending == 0


@pytest.mark.asyncio
async def test_dispatch_all_remaining_count_is_correct():
    """After dispatch_all_pending, remaining cards still show correct counts."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="a", column="Backlog")
        await _make_card(s, title="b", column="Backlog")
        await _make_card(s, title="orphan", column="engineer")
        busy = await _make_card(s, title="claimed", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy, payload={"claimed_by": "agent:k-alive-0001"},
        )
        await _make_card(s, title="blocked", column="Impediment")
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        assert len(results) == 2

        cards = await list_cards(s, PK)
        assert _frontend_pending_count(cards) == 0
        assert _frontend_orphan_count(cards) == 1
        assert len(await service.list_pending_cards(s, PK)) == 0
        assert len(await service.list_orphaned_cards(s, PK)) == 1


@pytest.mark.asyncio
async def test_redispatch_all_remaining_count_is_correct():
    """After redispatch_all_orphans, remaining cards still show correct counts."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="o1", column="engineer")
        await _make_card(s, title="o2", column="testing")
        await _make_card(s, title="pending", column="Backlog")
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.redispatch_all_orphans(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        assert len(results) == 2

        cards = await list_cards(s, PK)
        assert _frontend_orphan_count(cards) == 0
        assert _frontend_pending_count(cards) == 1
        assert len(await service.list_orphaned_cards(s, PK)) == 0
        assert len(await service.list_pending_cards(s, PK)) == 1


# ---- card transport field persistence ------------------------------------

@pytest.mark.asyncio
async def test_transport_field_persisted_on_create():
    """card.transport set at create time must survive the round-trip."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card", project_key=PK,
            entity_id=None,
            payload={"title": "sandcastle card", "transport": "sandcastle"},
        )
        await s.commit()
        card = await get_card(s, cid)
    assert card.transport == "sandcastle"


@pytest.mark.asyncio
async def test_transport_field_updated_via_update_op():
    """card.transport can be changed after creation via an update op."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"transport": "sandcastle"},
        )
        await s.commit()
        card = await get_card(s, cid)
    assert card.transport == "sandcastle"


@pytest.mark.asyncio
async def test_card_transport_sandcastle_uses_sandcastle_transport():
    """A card with transport=sandcastle must use sandcastle_transport when dispatched."""
    worktree = RecordingTransport()
    sc_calls = []

    def fake_sandcastle(*, directory, prompt, session_name, provider_id="claude-code"):
        sc_calls.append(session_name)
        return {"session_name": session_name, "transport": "sandcastle", "status": "started"}

    import unittest.mock as mock
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card", project_key=PK,
            entity_id=None,
            payload={"title": "sc card", "column": "Backlog", "transport": "sandcastle"},
        )
        await s.commit()

    with mock.patch.object(dispatch, "sandcastle_transport", side_effect=fake_sandcastle):
        async with KanbanSessionLocal() as s:
            result = await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=worktree,
            )
            await s.commit()

    # sandcastle_transport was called, not the worktree fallback
    assert len(sc_calls) == 1
    assert worktree.calls == []


@pytest.mark.asyncio
async def test_card_transport_worktree_overrides_sandcastle_project_default():
    """A card with transport=worktree uses worktree even when the project default is sandcastle."""
    sc_calls = []
    wt_calls = []

    def fake_sandcastle(*, directory, prompt, session_name, provider_id="claude-code"):
        sc_calls.append(session_name)
        return {"session_name": session_name, "transport": "sandcastle", "status": "started"}

    def fake_worktree(*, directory, prompt, session_name, provider_id="claude-code"):
        wt_calls.append(session_name)
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}

    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card", project_key=PK,
            entity_id=None,
            payload={"title": "worktree card", "column": "Backlog", "transport": "worktree"},
        )
        await s.commit()

    import unittest.mock as mock
    with mock.patch.object(dispatch, "sandcastle_transport", side_effect=fake_sandcastle), \
         mock.patch.object(dispatch, "worktree_transport", side_effect=fake_worktree):
        async with KanbanSessionLocal() as s:
            # project default is sandcastle, but card overrides to worktree
            result = await dispatch.dispatch_card(
                s, card_id=cid, project_path="/p", transport=fake_sandcastle,
            )
            await s.commit()

    assert sc_calls == []        # sandcastle was NOT called
    assert len(wt_calls) == 1   # worktree WAS called


# ---- resume transport -------------------------------------------------------

def test_make_resume_transport_records_call():
    """make_resume_transport produces a callable that passes session_id through."""
    calls = []

    def fake_spawn(provider_id, options, *, session_name):
        calls.append({"options": options, "session_name": session_name})
        return {"session_name": session_name}

    import unittest.mock as mock
    with mock.patch("app.services.agent_bridge.spawn.spawn_session", fake_spawn), \
         mock.patch("app.services.scheduling.session_registry.session_registry.can_add_session",
                    return_value=True):
        transport = dispatch.make_resume_transport(
            session_id="abc-123", project_folder="-home-user-repo",
        )
        result = transport(directory="/p", prompt="continue", session_name="k-test-0001")

    assert len(calls) == 1
    opts = calls[0]["options"]
    assert opts.mode == "resume"
    assert opts.session_id == "abc-123"
    assert opts.project_folder == "-home-user-repo"
    assert opts.prompt == "continue"
    assert result == {"session_name": "k-test-0001"}


@pytest.mark.asyncio
async def test_get_transport_for_card_uses_resume_when_set():
    """A card with resume_session_id gets make_resume_transport, not worktree_transport."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="resumable", column="engineer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key="",
            entity_id=cid,
            payload={"resume_session_id": "sess-xyz", "resume_project_folder": "-p"},
        )
        await s.flush()
        card = await get_card(s, cid)

    transport = dispatch.get_transport_for_card(card, dispatch.worktree_transport)
    # The returned transport should NOT be the worktree_transport or sandcastle_transport
    assert transport is not dispatch.worktree_transport
    assert transport is not dispatch.sandcastle_transport


@pytest.mark.asyncio
async def test_redispatch_with_resume_session_id_uses_resume_transport():
    """When card has resume_session_id, redispatch calls resume transport, not worktree."""
    calls = []

    def resume_transport(*, directory, prompt, session_name, provider_id="claude-code"):
        calls.append({"mode": "resume", "session_name": session_name})
        return {"session_name": session_name}

    import unittest.mock as mock

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="context-limit card", column="engineer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key="",
            entity_id=cid,
            payload={"resume_session_id": "old-sess-id"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-old-dead"},
        )
        await s.commit()

    with mock.patch.object(dispatch, "make_resume_transport", return_value=resume_transport):
        async with KanbanSessionLocal() as s:
            result = await dispatch.redispatch_card(
                s, card_id=cid, project_path="/p",
            )
            await s.commit()

    assert result is not None
    assert len(calls) == 1
    assert calls[0]["mode"] == "resume"


# ---- To Resume column ---------------------------------------------------------


@pytest.mark.asyncio
async def test_move_to_resume_moves_card_to_to_resume():
    """_move_to_resume finds a resumable session, sets resume fields, moves to To Resume,
    kills the tmux session, and releases the claim."""
    import unittest.mock as mock

    cid = None
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="context-limit", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-dead-0001"},
        )
        await s.commit()

    card = None
    from app.kanban import session_recovery

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=("sess-abc", "proj-folder"),
    ):
        with mock.patch.object(
            dispatch, "_kill_agent_session", return_value=None,
        ) as kill_mock:
            async with KanbanSessionLocal() as s:
                card = await get_card(s, cid)
                result = await dispatch._move_to_resume(
                    s, card=card, project_key=PK, project_path="/p",
                )
                await s.commit()
                card = await get_card(s, cid)

    assert result is True
    assert card is not None
    assert card.column == "To Resume"
    assert card.resume_session_id == "sess-abc"
    assert card.resume_project_folder == "proj-folder"
    assert card.claimed_by is None
    kill_mock.assert_called_once_with("k-dead-0001")


@pytest.mark.asyncio
async def test_move_to_resume_returns_false_when_no_resume_target():
    """_move_to_resume returns False when no resumable transcript is found."""
    import unittest.mock as mock
    from app.kanban import session_recovery

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="no-resume", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-dead-0002"},
        )
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=None,
    ):
        async with KanbanSessionLocal() as s:
            card = await get_card(s, cid)
            result = await dispatch._move_to_resume(
                s, card=card, project_key=PK, project_path="/p",
            )
            await s.commit()

    assert result is False


@pytest.mark.asyncio
async def test_move_to_resume_returns_false_for_fixed_column_card():
    """_move_to_resume returns False immediately for cards already on fixed columns."""
    import unittest.mock as mock
    from app.kanban import session_recovery

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="already-done", column="Done")
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=("sess-abc", "proj-folder"),
    ):
        async with KanbanSessionLocal() as s:
            card = await get_card(s, cid)
            result = await dispatch._move_to_resume(
                s, card=card, project_key=PK, project_path="/p",
            )

    assert result is False


@pytest.mark.asyncio
async def test_reaper_moves_resumable_dead_session_to_to_resume():
    """reap_stale_claims with project_path moves resumable dead sessions to To Resume."""
    import unittest.mock as mock
    from app.kanban import session_recovery

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="resumable-dead", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-dead-0003"},
        )
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=("sess-xyz", "proj-folder"),
    ):
        with mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
            async with KanbanSessionLocal() as s:
                reaped = await dispatch.reap_stale_claims(
                    s, project_key=PK, cards=await list_cards(s, PK),
                    live_sessions=set(), project_path="/p",
                )
                await s.commit()
                card = await get_card(s, cid)

    assert reaped == 1
    assert card.column == "To Resume"
    assert card.resume_session_id == "sess-xyz"
    assert card.claimed_by is None


@pytest.mark.asyncio
async def test_reaper_without_project_path_plain_release():
    """reap_stale_claims without project_path falls back to plain release for dead sessions."""
    import unittest.mock as mock
    from app.kanban import session_recovery

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="plain-dead", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-dead-0004"},
        )
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=("sess-xyz", "proj-folder"),
    ):
        async with KanbanSessionLocal() as s:
            reaped = await dispatch.reap_stale_claims(
                s, project_key=PK, cards=await list_cards(s, PK),
                live_sessions=set(),
                # project_path not set — should NOT call _move_to_resume
            )
            await s.commit()
            card = await get_card(s, cid)

    assert reaped == 1
    assert card.column == "engineer"  # NOT moved to To Resume
    assert card.resume_session_id is None  # resume fields NOT set
    assert card.claimed_by is None


# ---- move_limited_session_to_resume (live session hit its usage limit) -----

@pytest.mark.asyncio
async def test_move_limited_session_to_resume_moves_matching_card(monkeypatch):
    """A Notification hook event for a live, limit-hit session moves its card to
    To Resume and kills the (still alive) tmux session, same as the dead-session
    reaper does for a crashed one."""
    import unittest.mock as mock
    import app.kanban.db as kdb
    from app.kanban import session_recovery

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="limit-hit", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-live-0001"},
        )
        await s.commit()

    with mock.patch.object(dispatch, "_safe_resolve_key", return_value=PK), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-live", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None) as kill_mock:
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-live-0001",
        )

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)

    assert result is True
    assert card.column == "To Resume"
    assert card.resume_session_id == "sess-live"
    assert card.claimed_by is None
    kill_mock.assert_called_once_with("k-live-0001")


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_ignores_non_worktree_cwd():
    """A cwd that isn't a `<project>/.claude/worktrees/<name>` shape (e.g. a manual
    `claude` session, or the project root itself) is left untouched."""
    result = await dispatch.move_limited_session_to_resume("/home/me/some-project")
    assert result is False


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_returns_false_when_no_matching_card(monkeypatch):
    """No card claimed by that session -> no-op, even if the cwd shape matches."""
    import unittest.mock as mock
    import app.kanban.db as kdb

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        await _make_card(s, title="unrelated", column="engineer")
        await s.commit()

    with mock.patch.object(dispatch, "_safe_resolve_key", return_value=PK):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-no-such-session",
        )

    assert result is False


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_returns_false_when_project_key_unresolved():
    """When the derived project path can't be resolved to a project key, bail out
    before touching the kanban DB at all."""
    import unittest.mock as mock

    with mock.patch.object(dispatch, "_safe_resolve_key", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-live-0002",
        )

    assert result is False


@pytest.mark.asyncio
async def test_active_session_count_excludes_to_resume():
    """Cards in To Resume are excluded from _active_session_count (fixed column)."""
    async with KanbanSessionLocal() as s:
        c1 = await _make_card(s, title="active", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=c1, payload={"claimed_by": "agent:k-alive-0005"},
        )
        c2 = await _make_card(s, title="resumable", column="To Resume")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=c2, payload={"claimed_by": "agent:k-alive-0006"},
        )
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        count = dispatch._active_session_count(cards)

    assert count == 1  # only c1 (engineer), not c2 (To Resume)


@pytest.mark.asyncio
async def test_dispatch_picks_up_to_resume_card():
    """_next_card picks unclaimed cards from To Resume when Backlog is empty."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="resume-me", column="To Resume")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is not None
    assert next_card.column == "To Resume"


@pytest.mark.asyncio
async def test_dispatch_prefers_backlog_over_to_resume():
    """_next_card prefers Backlog cards over To Resume cards."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="new-task", column="Backlog")
        await _make_card(s, title="resume-me", column="To Resume")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is not None
    assert next_card.title == "new-task"
    assert next_card.column == "Backlog"


@pytest.mark.asyncio
async def test_dispatch_prefers_higher_priority_within_column():
    """_next_card picks a 'high' priority card over an older 'none'-priority card
    in the same column, even though rank order would otherwise pick the older one."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="filed-first", column="Backlog", priority=None)
        await _make_card(s, title="urgent", column="Backlog", priority="high")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is not None
    assert next_card.title == "urgent"


@pytest.mark.asyncio
async def test_dispatch_orders_by_priority_high_medium_low_none():
    """_next_card ranks priority high > medium > low > none, regardless of rank order."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="none-card", column="Backlog", priority="none")
        await _make_card(s, title="low-card", column="Backlog", priority="low")
        await _make_card(s, title="medium-card", column="Backlog", priority="medium")
        await _make_card(s, title="high-card", column="Backlog", priority="high")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is not None
    assert next_card.title == "high-card"


@pytest.mark.asyncio
async def test_dispatch_column_preference_beats_priority():
    """Backlog still wins over To Resume even when the To Resume card is 'high'
    priority — the column preference is about resume-recovery, not urgency."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="new-task", column="Backlog", priority=None)
        await _make_card(s, title="resume-me", column="To Resume", priority="high")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is not None
    assert next_card.title == "new-task"


# ---- git-ship / session-end workflow --------------------------------------


class TestBuildShipInstructions:
    """_build_ship_instructions produces correct instructions per ship mode."""

    def test_direct_mode_includes_merge_commands(self):
        instructions = dispatch._build_ship_instructions("direct")
        assert "git merge --no-ff" in instructions
        assert "git push origin HEAD:master" in instructions
        assert "git fetch origin" in instructions
        assert "pytest backend/tests/" not in instructions  # gate runs tests, no double-run
        assert "attach_deliverable" in instructions
        assert 'kind="branch"' in instructions
        assert 'move_card' in instructions
        assert '"Done"' in instructions
        assert "gh pr create" not in instructions

    def test_pull_request_mode_includes_gh_commands(self):
        instructions = dispatch._build_ship_instructions("pull-request")
        assert "gh pr create --draft" in instructions
        assert "git push -u origin HEAD" in instructions
        assert "git fetch origin" in instructions
        assert "pytest backend/tests/" not in instructions  # gate runs tests, no double-run
        assert "attach_deliverable" in instructions
        assert 'kind="pr"' in instructions
        assert 'move_card' in instructions
        assert '"Done"' in instructions
        assert "git merge --no-ff" not in instructions

    def test_both_modes_rely_on_pre_push_gate(self):
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            assert "pre-push gate" in instructions
            assert "--no-verify" in instructions   # told never to bypass a red gate
            assert "commit your work" in instructions.lower() or "Commit your work" in instructions

    def test_both_modes_include_sync_step(self):
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            assert "git fetch origin" in instructions


class TestBuildCardPromptSessionEnd:
    """build_card_prompt includes the Session-end workflow section."""

    def test_direct_mode_includes_session_end_section(self):
        class _C:
            title = "My Card"
            description = "Do the thing"
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
        assert "Session-end workflow" in prompt
        assert "git merge --no-ff" in prompt
        assert "git push origin HEAD:master" in prompt
        assert "move_card" in prompt
        assert '"Done"' in prompt

    def test_pull_request_mode_includes_session_end_section(self):
        class _C:
            title = "My Card"
            description = "Do the thing"
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="pull-request")
        assert "Session-end workflow" in prompt
        assert "gh pr create --draft" in prompt
        assert "git push -u origin HEAD" in prompt
        assert "move_card" in prompt
        assert '"Done"' in prompt

    def test_impediment_card_still_has_session_end_section(self):
        class _C:
            title = "Bug"
            description = "Fix the crash"
        prompt = dispatch.build_card_prompt(
            _C(), persona="You are a debugger.", ship_mode="direct",
            impediment_question="Where is the crash?",
        )
        assert "IMPEDIMENT" in prompt
        assert "Session-end workflow" in prompt
        assert "merge --no-ff" in prompt

    def test_session_end_section_comes_after_main_instructions(self):
        class _C:
            title = "T"
            description = ""
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
        # The session-end section should appear after the main "Work autonomously" paragraph
        main_idx = prompt.index("Work autonomously")
        ship_idx = prompt.index("Session-end workflow")
        assert ship_idx > main_idx, "Session-end workflow should appear after main instructions"


# ---- run_dispatch_tick honours the global usage-limit pause ----------------

@pytest.mark.asyncio
async def test_run_dispatch_tick_skips_everything_when_paused(monkeypatch):
    """When a global dispatch pause is active (Claude usage limit hit), the tick
    must not touch queued-card retries or per-project dispatch at all --
    respawning while the account-wide limit is still active would just bounce
    the card straight back to "To Resume" and re-trigger the same limit."""
    import unittest.mock as mock
    from datetime import datetime, timedelta, timezone

    import app.kanban.db as kdb
    from app.kanban.dispatch_pause import set_paused_until

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        await set_paused_until(s, datetime.now(timezone.utc) + timedelta(minutes=5))
        await s.commit()

    with mock.patch.object(dispatch, "_retry_queued_cards") as retry_mock, \
         mock.patch.object(dispatch, "list_autodispatch_projects") as list_mock:
        await dispatch.run_dispatch_tick()

    retry_mock.assert_not_called()
    list_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_dispatch_tick_runs_normally_when_not_paused(monkeypatch):
    """Sanity check: the new pause guard must not block a tick when there is no
    active pause -- otherwise every project's auto-dispatch would silently die."""
    import unittest.mock as mock

    import app.kanban.db as kdb

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    with mock.patch.object(dispatch, "_retry_queued_cards") as retry_mock, \
         mock.patch.object(dispatch, "list_autodispatch_projects", return_value=[]) as list_mock:
        await dispatch.run_dispatch_tick()

    retry_mock.assert_called_once()
    list_mock.assert_called_once()
