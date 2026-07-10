# backend/tests/test_kanban_dispatch.py
import pytest
import pytest_asyncio

from app.kanban import dispatch, service
from app.kanban.models import KanbanCard
from app.kanban.operations import apply_operation
from app.kanban.service import get_card, list_cards
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


PK = "git:example.com/me/repo"


async def _make_card(s, title="Task", column="Backlog", priority=None, scheduled_at=None):
    payload = {"title": title, "column": column}
    if priority is not None:
        payload["priority"] = priority
    if scheduled_at is not None:
        payload["scheduled_at"] = scheduled_at
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

    def __call__(self, *, directory, prompt, session_name, provider_id="claude-code",
                 platform="anthropic", model=None):
        self.calls.append({"directory": directory, "prompt": prompt,
                           "session_name": session_name, "provider_id": provider_id,
                           "platform": platform, "model": model})
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


def test_card_prompt_includes_problem_flag_reminder():
    """Every dispatched session should be reminded to file (not just mention)
    problems it notices outside its assigned card's scope — see kanban card
    'Kritische zelf structurering en reflectie' and the flag-problem skill."""
    class _C:
        title = "T"
        description = ""
    prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
    assert "flag-problem" in prompt
    assert "project-key" in prompt
    assert "create_card" in prompt


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
async def test_dispatch_defaults_to_anthropic_platform():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["platform"] == "anthropic"


@pytest.mark.asyncio
async def test_dispatch_uses_column_default_platform():
    """A column configured with default_platform="minimax" (e.g. an "engineer"
    column meant for bulk coding work) routes its cards' spawn to that platform,
    while columns without one keep the default Anthropic subscription."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_platform="minimax",
        )
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
        card = await get_card(s, cid)
    assert card.column == "engineer"
    assert len(transport.calls) == 1
    assert transport.calls[0]["platform"] == "minimax"


# ---- model precedence: card.model > column.default_model > persona frontmatter ----

@pytest.mark.asyncio
async def test_dispatch_no_model_by_default():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] is None


@pytest.mark.asyncio
async def test_dispatch_uses_card_model_over_everything():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_model="sonnet",
        )
        cid = await _make_card(s)
        await apply_operation(s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"model": "opus"})
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] == "opus"


@pytest.mark.asyncio
async def test_dispatch_uses_column_default_model_when_card_model_unset():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_model="sonnet",
        )
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] == "sonnet"


@pytest.mark.asyncio
async def test_dispatch_falls_back_to_persona_frontmatter_model(tmp_path):
    transport = RecordingTransport()
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "engineer.md").write_text(
        "---\nname: 'engineer'\nmodel: 'claude-opus-4-8'\n---\nBe an engineer.\n"
    )
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path=str(tmp_path), transport=transport,
        )
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] == "claude-opus-4-8"


def test_effective_model_precedence():
    assert dispatch._effective_model("opus", "sonnet", "haiku") == "opus"
    assert dispatch._effective_model(None, "sonnet", "haiku") == "sonnet"
    assert dispatch._effective_model(None, None, "haiku") == "haiku"
    assert dispatch._effective_model(None, None, None) is None
    assert dispatch._effective_model("", "", "") is None


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


# ---- per-column session cap -------------------------------------------------


def test_active_session_count_by_column():
    cards = [
        _bare_card("engineer", "agent:a"),
        _bare_card("review", "agent:b"),
        _bare_card("Backlog", "agent:c"),
        _bare_card("Done", "agent:d"),
        _bare_card("engineer", "me@ui"),
        _bare_card("engineer", None),
        _bare_card("analyst", "agent:e"),
    ]
    counts = dispatch._active_session_count_by_column(cards)
    assert counts == {"engineer": 1, "review": 1, "analyst": 1}


@pytest.fixture
def project_with_agents(tmp_path):
    """Create a temporary project with agent persona files so column resolution
    resolves agent names (engineer, review) to their agent columns instead of
    falling through to the hardcoded 'engineer' default."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    for name in ("engineer", "review"):
        (agents_dir / f"{name}.md").write_text(f"# {name}")
    return str(tmp_path)


@pytest.mark.asyncio
async def test_dispatch_respects_per_column_cap(project_with_agents):
    """When a column has per-column max_sessions, the dispatcher stops
    dispatching cards to that column once the cap is reached."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await dispatch.set_max_sessions(s, PK, 10)
        await service.create_column(s, project_key=PK, name="engineer",
                                     default_agent="engineer", max_sessions=1)
        await service.create_column(s, project_key=PK, name="review",
                                     default_agent="review", max_sessions=2)
        for i in range(4):
            cid = await _make_card(s, title=f"eng-{i}", column="Backlog")
            await apply_operation(
                s, op_type="update", entity_type="card", project_key=PK,
                entity_id=cid, payload={"agent": "engineer"},
            )
        await s.commit()

        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()

    assert result is not None
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_per_column_cap_does_not_block_other_columns(project_with_agents):
    """Per-column caps apply independently: a full engineer column doesn't
    block cards targeting the review column."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await dispatch.set_max_sessions(s, PK, 10)
        await service.create_column(s, project_key=PK, name="engineer",
                                     default_agent="engineer", max_sessions=1)
        await service.create_column(s, project_key=PK, name="review",
                                     default_agent="review", max_sessions=2)
        # Fill the engineer slot first
        busy_id = await _make_card(s, title="eng-busy", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy_id, payload={"claimed_by": "agent:k-eng-0001"},
        )
        cid = await _make_card(s, title="eng-2", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"agent": "engineer"},
        )
        for title in ("rev-1", "rev-2"):
            cid = await _make_card(s, title=title, column="Backlog")
            await apply_operation(
                s, op_type="update", entity_type="card", project_key=PK,
                entity_id=cid, payload={"agent": "review"},
            )
        await s.commit()

        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()

    assert result is not None
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_column_cap_defaults_null_means_no_per_column_limit(project_with_agents):
    """A column with max_sessions=NULL (unset) falls back to the project cap."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await dispatch.set_max_sessions(s, PK, 2)
        await service.create_column(s, project_key=PK, name="engineer",
                                     default_agent="engineer", max_sessions=None)
        await service.create_column(s, project_key=PK, name="analyst",
                                     default_agent="analyst", max_sessions=None)
        for i in range(4):
            cid = await _make_card(s, title=f"task-{i}", column="Backlog")
            await apply_operation(
                s, op_type="update", entity_type="card", project_key=PK,
                entity_id=cid, payload={"agent": "engineer"},
            )
        await s.commit()

        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_agents,
            transport=transport,
        )
        await s.commit()

    assert result is not None
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_column_max_sessions_column_roundtrip():
    """max_sessions on a column can be set via create_column and read back."""
    async with KanbanSessionLocal() as s:
        col = await service.create_column(s, project_key=PK, name="testcol",
                                           default_agent="test", max_sessions=3)
        await s.commit()
        cols = await service.list_columns(s, PK)
    matching = [c for c in cols if c.name == "testcol"]
    assert len(matching) == 1
    assert matching[0].max_sessions == 3


@pytest.mark.asyncio
async def test_column_max_sessions_can_be_updated():
    """max_sessions on a column can be updated."""
    async with KanbanSessionLocal() as s:
        col = await service.create_column(s, project_key=PK, name="testcol",
                                           default_agent="test", max_sessions=1)
        await s.commit()
        cid = col.id
    async with KanbanSessionLocal() as s:
        updated = await service.update_column(s, cid, max_sessions=5)
        await s.commit()
    assert updated.max_sessions == 5


# ---- retry queue ------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_queued_cards_respects_per_project_cap(monkeypatch):
    """Retrying memory-queued cards must honour the per-project session cap.

    Regression: the retry path dispatched every retryable card, checking only the
    hardware/memory limit, so queued cards could push a project past its cap (e.g.
    3 running from the normal loop + 3 retried = 6 sessions)."""
    from types import SimpleNamespace

    import app.kanban.db as kdb
    import app.services.scheduling.pending_queue as pq_mod
    from app.services.scheduling.pending_queue import PendingQueue

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
async def test_retry_queued_cards_dispatches_queued_orphan(monkeypatch):
    # An orphan (unclaimed card left behind in an agent column, see the orphan
    # fallback in _next_card) that got memory-queued must be retried like any
    # other queued card, not silently dropped for not being in "Backlog".
    from types import SimpleNamespace

    import app.kanban.db as kdb
    import app.services.scheduling.pending_queue as pq_mod
    from app.services.scheduling.pending_queue import PendingQueue

    fresh_queue = PendingQueue()
    monkeypatch.setattr(pq_mod, "pending_queue", fresh_queue)
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)
    monkeypatch.setattr(
        dispatch, "get_memory_status_cached",
        lambda: SimpleNamespace(is_critical=False, usage_percent=0.1),
    )

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        orphan = await _make_card(s, title="orphaned", column="developer")
        await s.commit()

    fresh_queue.enqueue(card_id=orphan, project_key=PK, project_path="/p")

    await dispatch._retry_queued_cards(transport)

    assert len(transport.calls) == 1
    assert fresh_queue.size == 0
    async with KanbanSessionLocal() as s:
        card = await get_card(s, orphan)
    assert card.claimed_by is not None


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
    # Cap pinned to 1 so this test isolates the reap-then-dispatch-next-Backlog-card
    # behavior from the orphan-redispatch fallback covered separately below.
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        dead = await _make_card(s, title="orphaned", column="developer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=dead, payload={"claimed_by": "agent:k-dead-0001"},
        )
        waiting = await _make_card(s, title="waiting", column="Backlog")
        await dispatch.set_max_sessions(s, PK, 1)
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
            live_sessions=set(),  # no live sessions -> the agent claim is stale
        )
        await s.commit()
        dead_card = await get_card(s, dead)
        waiting_card = await get_card(s, waiting)
    assert dead_card.claimed_by is None       # stale claim reaped
    assert dead_card.column == "developer"    # cap full after "waiting" -> not yet redispatched
    assert result is not None                 # cap freed -> next card dispatched
    assert waiting_card.column == "engineer"
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_orphaned_agent_column_card_redispatched_when_cap_has_room():
    # A card left unclaimed in an agent column (e.g. by a prior reap whose dead
    # session had no resumable transcript, see reap_stale_claims) must not be
    # stranded forever: with a free cap slot and nothing waiting in Backlog/To
    # Resume, the tick must pick it back up itself -- otherwise auto-dispatch
    # silently stalls until a human notices and hits "redispatch" by hand.
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        orphan = await _make_card(s, title="orphaned", column="developer")
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        card = await get_card(s, orphan)
    assert result is not None
    assert len(transport.calls) == 1
    assert card.claimed_by is not None and card.claimed_by.startswith("agent:")


@pytest.mark.asyncio
async def test_orphaned_agent_column_card_waits_for_backlog_cards_first():
    # When both a fresh Backlog card and a leftover orphan are available, the
    # Backlog card is prioritised (it's new work); the orphan only fills cap
    # room left over after Backlog/To Resume are exhausted.
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        orphan = await _make_card(s, title="orphaned", column="developer")
        waiting = await _make_card(s, title="waiting", column="Backlog")
        await dispatch.set_max_sessions(s, PK, 1)
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        orphan_card = await get_card(s, orphan)
        waiting_card = await get_card(s, waiting)
    assert waiting_card.claimed_by is not None   # Backlog card wins the single slot
    assert orphan_card.claimed_by is None        # orphan still waiting for room
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


def test_live_sessions_empty_set_on_tmux_3_6_no_server_wording(monkeypatch):
    # tmux 3.6 dropped the "no server running" wording in favour of a generic
    # "error connecting to <socket> (No such file or directory)" message for the
    # exact same "no server ever started" case. This must still map to an empty
    # set, not None, or the reaper/session-recovery permanently refuses to touch
    # any claim whenever no tmux server has been started yet on this host.
    import app.kanban.dispatch as d

    class R:
        returncode = 1
        stdout = ""
        stderr = "error connecting to /tmp/tmux-1000/default (No such file or directory)"
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: R())
    assert d._live_sessions() == set()


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


def test_mint_session_name_avoids_collision_with_live_tmux_session(monkeypatch):
    # If the minted name happens to already be a running tmux session,
    # spawn_session's own collision fallback (agent_bridge.spawn._session_name_for)
    # silently renames the *actual* tmux session -- but the kanban claim, git
    # worktree and git branch were already committed under the original name.
    # cleanup_session_for_card then looks up a tmux session that never existed
    # under that name, assumes the agent "already exited", and releases the
    # claim -- orphaning the real, still-running tmux session forever. Minting
    # must therefore never hand out a name that's already live when the caller
    # has a fresh liveness snapshot.
    import itertools
    import uuid as uuid_mod

    colliding_hex = "aaaa"
    free_hex = "bbbb"
    fake_hexes = itertools.chain([colliding_hex, free_hex], itertools.repeat(free_hex))

    class FakeUUID:
        def __init__(self, hex_val):
            self.hex = hex_val

    monkeypatch.setattr(
        uuid_mod, "uuid4", lambda: FakeUUID(next(fake_hexes))
    )

    name = dispatch._mint_session_name(
        "/home/me/proj", live_sessions={f"k-proj-{colliding_hex}"},
    )

    assert name != f"k-proj-{colliding_hex}"
    assert name == f"k-proj-{free_hex}"


def test_mint_session_name_skips_collision_check_when_live_sessions_unknown(monkeypatch):
    # live_sessions=None (the default) means "no snapshot" -- e.g. a caller/test
    # that doesn't have a fresh tmux query. Minting must not shell out to tmux
    # itself in that case (that would turn every unit test that mints a session
    # name into an integration test hitting the real tmux binary).
    def boom():
        raise AssertionError("must not query tmux when live_sessions is None")

    monkeypatch.setattr(dispatch, "_live_sessions", boom)

    name = dispatch._mint_session_name("/home/me/proj")
    assert name.startswith("k-proj-")


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
async def test_redispatch_resumes_instead_of_fresh_session_when_resumable():
    """A card stuck on a live-but-limit-hit session (never reaped, no resume_session_id
    set yet) should resume the existing Claude conversation when redispatched, not
    discard it and spawn a brand new worktree session."""
    import unittest.mock as mock

    from app.kanban import session_recovery

    resume_calls = []

    def resume_transport(*, directory, prompt, session_name, provider_id="claude-code", platform="anthropic", model=None):
        resume_calls.append(session_name)
        return {"session_name": session_name}

    fresh_transport = RecordingTransport()

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="limit-hit", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-limited-0001"},
        )
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target",
        return_value=("sess-resumed", "proj-folder"),
    ), mock.patch.object(
        dispatch, "make_resume_transport", return_value=resume_transport,
    ), mock.patch.object(
        dispatch, "_kill_agent_session", return_value=None,
    ) as kill_mock:
        async with KanbanSessionLocal() as s:
            result = await dispatch.redispatch_card(
                s, card_id=cid, project_path="/p", transport=fresh_transport,
            )
            await s.commit()
            card = await get_card(s, cid)

    assert result is not None
    assert len(resume_calls) == 1
    assert fresh_transport.calls == []  # never fell back to a fresh session
    assert card.resume_session_id == "sess-resumed"
    assert card.resume_project_folder == "proj-folder"
    kill_mock.assert_called_once_with("k-limited-0001")


@pytest.mark.asyncio
async def test_redispatch_no_resumable_transcript_falls_back_to_fresh_session():
    """When the old session's worktree has no resumable transcript, redispatch still
    falls back to a fresh session (existing behaviour)."""
    import unittest.mock as mock

    from app.kanban import session_recovery

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-old-0002"},
        )
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=None,
    ):
        async with KanbanSessionLocal() as s:
            result = await dispatch.redispatch_card(
                s, card_id=cid, project_path="/p", transport=transport,
            )
            await s.commit()
            card = await get_card(s, cid)

    assert result is not None
    assert len(transport.calls) == 1
    assert card.resume_session_id is None


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

from datetime import UTC

from app.kanban.schemas import COLUMNS as _COLUMNS

_FIXED = set(_COLUMNS)


def _frontend_pending_count(cards) -> int:
    return sum(1 for c in cards if c.column in ("Backlog", "To Resume") and not c.claimed_by)


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
async def test_list_pending_cards_includes_unclaimed_to_resume():
    """To Resume cards (unclaimed, tagged for resume) are dispatch candidates too —
    not just Backlog. Dispatch all must not silently skip them."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="backlog-card", column="Backlog")
        await _make_card(s, title="resumable-card", column="To Resume")
        claimed = await _make_card(s, title="claimed-resume", column="To Resume")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=claimed, payload={"claimed_by": "me@ui"},
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        pending = await service.list_pending_cards(s, PK)
    assert {c.title for c in pending} == {"backlog-card", "resumable-card"}


@pytest.mark.asyncio
async def test_dispatch_all_pending_resumes_to_resume_cards():
    """dispatch_all_pending must dispatch unclaimed To Resume cards through the
    resume transport (their recorded resume_session_id), not the default/fresh one."""
    import unittest.mock as mock

    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="a", column="Backlog")
        resumable = await _make_card(s, title="resumable", column="To Resume")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=resumable,
            payload={"resume_session_id": "sess-abc", "resume_project_folder": "proj-folder"},
        )
        await s.commit()

    resume_calls = []

    def resume_transport(*, directory, prompt, session_name, provider_id="claude-code", platform="anthropic", model=None):
        resume_calls.append(session_name)
        return {"session_name": session_name}

    with mock.patch.object(dispatch, "make_resume_transport", return_value=resume_transport):
        async with KanbanSessionLocal() as s:
            results = await dispatch.dispatch_all_pending(
                s, project_key=PK, project_path="/p", transport=transport,
            )
            await s.commit()

    assert len(results) == 2
    assert len(resume_calls) == 1        # the To Resume card resumed its session
    assert len(transport.calls) == 1     # the plain Backlog card used the default transport


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


# ---- depends_on gate on bulk dispatch paths -------------------------------

@pytest.mark.asyncio
async def test_dispatch_all_pending_skips_blocked_card():
    """A Backlog card whose depends_on points to a non-Done parent must NOT be
    spawned by dispatch_all_pending — same predicate the auto-dispatch tick uses.
    Without this gate, the bulk action silently contradicts the Blocked badge."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        parent = await _make_card(s, title="parent", column="Backlog")
        # Child whose only dep is the still-Open parent.
        child = await _make_card(s, title="child", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=child, payload={"depends_on": [parent]},
        )
        # A second, unblocked card that should still go through.
        await _make_card(s, title="free", column="Backlog")
        await s.commit()

    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()

    assert len(results) == 2, f"expected only unblocked cards dispatched, got {results}"
    # The blocked child must not appear in the spawned set; the free card and
    # the (Open but unblocked) parent do.
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_redispatch_all_orphans_skips_blocked_card():
    """An orphaned card whose depends_on points to a non-Done parent must NOT be
    spawned by redispatch_all_orphans. Mirrors the dispatch_all_pending contract."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Parent on Backlog (still Open). Child on an agent column, unclaimed
        # → an "orphan" eligible for redispatch_all_orphans — but blocked on the
        # parent via depends_on.
        parent = await _make_card(s, title="parent", column="Backlog")
        blocked_orphan = await _make_card(s, title="blocked-orphan", column="developer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=blocked_orphan, payload={"depends_on": [parent]},
        )
        # An orphan with no deps must still go through.
        await _make_card(s, title="free-orphan", column="testing")
        await s.commit()

    async with KanbanSessionLocal() as s:
        results = await dispatch.redispatch_all_orphans(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()

    assert len(results) == 1, f"expected only unblocked orphan dispatched, got {results}"
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_dispatch_all_pending_picks_up_card_after_dep_clears():
    """After the parent moves to Done, the previously-blocked child becomes
    dispatchable on the next bulk call — confirms the transition is live and
    doesn't require a restart."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        parent = await _make_card(s, title="parent", column="Backlog")
        child = await _make_card(s, title="child", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=child, payload={"depends_on": [parent]},
        )
        await s.commit()

    # First bulk call: parent is unblocked (no deps), child is blocked. Only
    # the parent should dispatch — the Blocked child stays in Backlog.
    async with KanbanSessionLocal() as s:
        results_before = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results_before) == 1

    # Move parent to Done → child's deps are now satisfied.
    async with KanbanSessionLocal() as s:
        await apply_operation(
            s, op_type="move", entity_type="card", project_key=PK,
            entity_id=parent, payload={"column": "Done"},
        )
        await s.commit()

    # Second bulk call: child is now dispatchable.
    async with KanbanSessionLocal() as s:
        results_after = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results_after) == 1


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

    def fake_sandcastle(*, directory, prompt, session_name, provider_id="claude-code", platform="anthropic", model=None):
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
            await dispatch.dispatch_card(
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

    def fake_sandcastle(*, directory, prompt, session_name, provider_id="claude-code", platform="anthropic", model=None):
        sc_calls.append(session_name)
        return {"session_name": session_name, "transport": "sandcastle", "status": "started"}

    def fake_worktree(*, directory, prompt, session_name, provider_id="claude-code", platform="anthropic", model=None):
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
            await dispatch.dispatch_card(
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

    def resume_transport(*, directory, prompt, session_name, provider_id="claude-code", platform="anthropic", model=None):
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
async def test_move_limited_session_to_resume_handles_backlog_card(monkeypatch):
    """A 429-hit session whose card already landed in Backlog (e.g. moved
    there by a prior reap that bumped dispatch_failures back to source_column)
    must still get moved to To Resume when its hook event fires — otherwise
    the card sits in Backlog with a 429-killed session, never picked up
    again. The fix: move_limited_session_to_resume accepts cards on Backlog
    and Impediment, not only agent columns."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="backlog-429", column="Backlog")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-backlog-0001"},
        )
        await s.commit()

    with mock.patch.object(dispatch, "_safe_resolve_key", return_value=PK), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-backlog", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-backlog-0001",
        )

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)

    assert result is True
    assert card.column == "To Resume"
    assert card.resume_session_id == "sess-backlog"
    assert card.claimed_by is None


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_handles_impediment_card(monkeypatch):
    """Same as the Backlog case, but for a card that ended up in Impediment
    before its hook event arrived — Impediment is human territory so this
    is more theoretical, but the function should be uniformly permissive."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="impediment-429", column="Impediment")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-imp-0001"},
        )
        await s.commit()

    with mock.patch.object(dispatch, "_safe_resolve_key", return_value=PK), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-imp", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-imp-0001",
        )

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)

    assert result is True
    assert card.column == "To Resume"
    assert card.claimed_by is None


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


class _FakeCard:
    def __init__(self, scheduled_at=None):
        self.scheduled_at = scheduled_at


def test_is_due_none_and_empty_are_due():
    assert dispatch._is_due(_FakeCard(None)) is True
    assert dispatch._is_due(_FakeCard("")) is True


def test_is_due_malformed_value_fails_open():
    assert dispatch._is_due(_FakeCard("not-a-date")) is True


def test_is_due_naive_datetime_is_treated_as_utc():
    assert dispatch._is_due(_FakeCard("2000-01-01T00:00:00")) is True
    assert dispatch._is_due(_FakeCard("2099-01-01T00:00:00")) is False


def test_is_due_future_and_past():
    assert dispatch._is_due(_FakeCard("2099-01-01T00:00:00+00:00")) is False
    assert dispatch._is_due(_FakeCard("2000-01-01T00:00:00+00:00")) is True


@pytest.mark.asyncio
async def test_next_card_skips_future_scheduled_card():
    """A card with a future scheduled_at is invisible to auto-dispatch until due."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="later", column="Backlog",
                          scheduled_at="2099-01-01T00:00:00+00:00")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is None


@pytest.mark.asyncio
async def test_next_card_picks_up_due_scheduled_card():
    """Once scheduled_at is in the past, the card becomes a normal dispatch candidate."""
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="ready", column="Backlog",
                          scheduled_at="2000-01-01T00:00:00+00:00")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is not None
    assert next_card.title == "ready"


@pytest.mark.asyncio
async def test_next_card_prefers_unscheduled_over_future_scheduled():
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="later", column="Backlog",
                          scheduled_at="2099-01-01T00:00:00+00:00")
        await _make_card(s, title="now", column="Backlog")
        await s.commit()

    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        next_card = dispatch._next_card(cards)
    assert next_card is not None
    assert next_card.title == "now"


@pytest.mark.asyncio
async def test_dispatch_all_pending_skips_future_scheduled_card():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="later", column="Backlog",
                          scheduled_at="2099-01-01T00:00:00+00:00")
        await _make_card(s, title="now", column="Backlog")
        await s.commit()

    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results) == 1
    assert len(transport.calls) == 1


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
        assert "venv/bin/activate" not in instructions  # local pytest dropped, see feedback_no_local_pytest memory
        assert "pytest -q" not in instructions
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
        assert "venv/bin/activate" not in instructions  # local pytest dropped, see feedback_no_local_pytest memory
        assert "pytest -q" not in instructions
        assert "attach_deliverable" in instructions
        assert 'kind="pr"' in instructions
        assert 'move_card' in instructions
        assert '"Done"' in instructions
        assert "git merge --no-ff" not in instructions

    def test_both_modes_instruct_running_tests_before_shipping(self):
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            assert "no pre-push gate" in instructions
            assert "venv/bin/activate" not in instructions  # backend gate is quality.yml CI only, not local
            assert "pytest -q" not in instructions
            assert "npm run lint" in instructions
            assert "npm run build" in instructions
            assert "quality.yml" in instructions  # backend gate, mentioned as such
            assert "Never ship" in instructions
            assert "commit your work" in instructions.lower() or "Commit your work" in instructions

    def test_both_modes_include_sync_step(self):
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            assert "git fetch origin" in instructions

    def test_pull_request_mode_polls_for_merge_before_done(self):
        instructions = dispatch._build_ship_instructions("pull-request")
        assert "gh pr ready" in instructions
        assert "gh pr merge --auto --squash" in instructions
        assert "mergeStateStatus" in instructions
        assert "report_impediment" in instructions
        # Regression guard: BLOCKED (and other mergeStateStatus values) can mean
        # "checks still pending", not "checks failed" — a naive case-match that
        # treats *BLOCKED* as failure would false-fail on every PR the instant
        # CI starts running. Failure detection must instead be based on actual
        # check conclusions.
        assert "*BLOCKED*|CLOSED*) echo" not in instructions
        assert "FAILED" in instructions
        assert "statusCheckRollup" in instructions
        # A wedged PR must not poll forever.
        assert "ITER" in instructions and "40" in instructions

    def test_both_modes_require_a_summary_when_moving_to_done(self):
        """move_card requires `summary` on Done/Impediment (see mcp_server.py);
        the instructions must tell the agent to actually pass it, otherwise every
        move_card("Done") call in the wild fails on summary_required."""
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            assert "summary=" in instructions
            assert "summary_required" not in instructions  # not the agent's problem to debug


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
    from datetime import datetime, timedelta

    import app.kanban.db as kdb
    from app.kanban.dispatch_pause import set_paused_until

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        await set_paused_until(s, datetime.now(UTC) + timedelta(minutes=5))
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


# ---- dead-on-arrival circuit breaker (dispatch_failures -> Impediment) ----
#
# A session that dies within seconds of being claimed (stale --resume worktree,
# missing sandcastle config, ...) used to loop forever: claimed, reaped as dead,
# re-claimed by the very next tick, dead again. reap_stale_claims now counts
# consecutive dead-on-arrival reaps per card and moves it to Impediment after
# MAX_DISPATCH_FAILURES instead of retrying forever.

async def _backdate_claim(s, card_id: str, seconds_ago: float) -> None:
    """Rewrite a card's claimed_at directly (bypassing the op-log) to simulate a
    session that ran for a while before dying, rather than dying on arrival."""
    from datetime import datetime, timedelta

    card = await s.get(KanbanCard, card_id)
    card.claimed_at = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    await s.flush()


@pytest.mark.asyncio
async def test_reap_increments_dispatch_failures_on_dead_on_arrival():
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="doa", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-doa-0001"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK), live_sessions=set())
        await s.commit()
        card = await get_card(s, cid)
    assert reaped == 1
    assert card.claimed_by is None
    assert card.dispatch_failures == 1
    assert card.column == "engineer"  # still under MAX_DISPATCH_FAILURES, not moved


@pytest.mark.asyncio
async def test_reap_moves_to_impediment_after_max_dispatch_failures():
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="doa", column="engineer")
        await s.commit()

    for _ in range(dispatch.MAX_DISPATCH_FAILURES):
        async with KanbanSessionLocal() as s:
            await apply_operation(
                s, op_type="claim", entity_type="card", project_key=PK,
                entity_id=cid, payload={"claimed_by": "agent:k-doa-0002"},
            )
            await s.commit()
            await dispatch.reap_stale_claims(
                s, project_key=PK, cards=await list_cards(s, PK), live_sessions=set())
            await s.commit()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.column == "Impediment"
    assert card.claimed_by is None
    assert card.dispatch_failures == 0  # reset so a future redispatch starts fresh


@pytest.mark.asyncio
async def test_reap_clears_stale_resume_fields_on_dead_on_arrival():
    # A resume_session_id/resume_project_folder pointing at a worktree that was
    # since merged and GC'd would otherwise be retried forever by
    # get_transport_for_card, dying in seconds every time.
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stale-resume", column="engineer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"resume_session_id": "old-session",
                                     "resume_project_folder": "-old-worktree"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-stale-0001"},
        )
        await s.commit()
        await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK), live_sessions=set())
        await s.commit()
        card = await get_card(s, cid)
    assert card.resume_session_id is None
    assert card.resume_project_folder is None
    assert card.dispatch_failures == 1


@pytest.mark.asyncio
async def test_reap_does_not_count_failure_for_long_running_claim():
    # A session that ran for a while before dying (real crash, OOM, manual kill)
    # proved the dispatch target itself works -- must not count toward the
    # dead-on-arrival circuit breaker.
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="ran-a-while", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-ran-0001"},
        )
        await s.commit()
        await _backdate_claim(s, cid, dispatch.DEAD_ON_ARRIVAL_SECONDS + 10)
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK), live_sessions=set())
        await s.commit()
        card = await get_card(s, cid)
    assert reaped == 1
    assert card.claimed_by is None
    assert card.dispatch_failures == 0
    assert card.column == "engineer"


@pytest.mark.asyncio
async def test_repeated_synchronous_spawn_failures_move_to_impediment():
    # A synchronous spawn exception (e.g. resolve_directory raising because a
    # --resume worktree was merged and GC'd -- the "voorbereiding public repo"
    # case) is a different code path from the tmux dead-session reaper, but must
    # trip the same MAX_DISPATCH_FAILURES circuit breaker instead of looping
    # forever between source_column and a fresh claim.
    transport = RecordingTransport(fail=True)
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="always-fails", column="To Resume")
        await s.commit()

    for _ in range(dispatch.MAX_DISPATCH_FAILURES):
        async with KanbanSessionLocal() as s:
            with pytest.raises(RuntimeError):
                await dispatch.dispatch_project(
                    s, project_key=PK, project_path="/p", transport=transport,
                )
            await s.commit()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.column == "Impediment"
    assert card.claimed_by is None
    assert card.dispatch_failures == 0
    assert len(transport.calls) == dispatch.MAX_DISPATCH_FAILURES


@pytest.mark.asyncio
async def test_synchronous_spawn_failure_clears_stale_resume_fields(monkeypatch):
    # The card has resume_session_id set, so get_transport_for_card always picks
    # the resume transport over the `transport` passed to dispatch_project (see
    # get_transport_for_card) -- patch make_resume_transport itself so the failure
    # is deterministic instead of depending on real ~/.claude/projects contents.
    transport = RecordingTransport(fail=True)
    monkeypatch.setattr(dispatch, "make_resume_transport", lambda *a, **k: transport)
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stale-resume-spawn", column="To Resume")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"resume_session_id": "old-session",
                                     "resume_project_folder": "-old-worktree"},
        )
        await s.commit()
        with pytest.raises(RuntimeError):
            await dispatch.dispatch_project(
                s, project_key=PK, project_path="/p", transport=transport,
            )
        await s.commit()
        card = await get_card(s, cid)
    assert card.resume_session_id is None
    assert card.resume_project_folder is None
    assert card.dispatch_failures == 1


@pytest.mark.asyncio
async def test_reap_resets_failure_streak_after_long_running_claim():
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="recovering", column="engineer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"dispatch_failures": 2},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-recover-0001"},
        )
        await s.commit()
        await _backdate_claim(s, cid, dispatch.DEAD_ON_ARRIVAL_SECONDS + 10)
        await s.commit()
        await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK), live_sessions=set())
        await s.commit()
        card = await get_card(s, cid)
    assert card.dispatch_failures == 0


@pytest.mark.asyncio
async def test_run_dispatch_tick_commits_compensating_ops_on_spawn_failure(monkeypatch):
    """Regression test for a real bug found live on this project's own board: a card
    stuck in "To Resume" with a resume_session_id pointing at a merged/GC'd worktree
    kept failing to spawn (ValueError from resolve_directory) every ~10s tick,
    forever, with the card ending each cycle in *exactly* the state it started --
    no failure count, no cleared resume pointer, not even the claim released.

    Root cause: run_dispatch_tick's per-project `except Exception:` branch logged
    the failure but never called `ks.commit()`. _run_card's except block *does*
    apply compensating ops (release the claim, clear the stale resume pointer, bump
    dispatch_failures, move back / to Impediment) before re-raising, but those were
    only flushed, not committed -- the `async with KanbanSessionLocal()` block's
    implicit close-without-commit silently discarded all of them. This test exercises
    the real run_dispatch_tick entrypoint (not dispatch_project directly, which is
    what the other spawn-failure tests use and why this bug went unnoticed) and
    asserts the compensating ops actually persist."""
    import unittest.mock as mock

    import app.kanban.db as kdb

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)
    monkeypatch.setattr(dispatch, "list_autodispatch_projects",
                        mock.AsyncMock(return_value=[PK]))
    monkeypatch.setattr(dispatch, "match_project_paths", lambda *a, **kw: {PK: "/p"})
    monkeypatch.setattr(dispatch, "_live_sessions", lambda: set())
    monkeypatch.setattr(dispatch, "_live_sandcastle_sessions",
                        mock.AsyncMock(return_value=set()))

    transport = RecordingTransport(fail=True)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="always-fails", column="Backlog")
        await s.commit()

    await dispatch.run_dispatch_tick(transport=transport)

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.column == "Backlog"        # compensating move-back was committed
    assert card.claimed_by is None         # compensating release was committed
    assert card.dispatch_failures == 1     # circuit-breaker counter was committed
    assert len(transport.calls) == 1


# ---- stuck-session reaper (alive in tmux, never sent hooks, 429/Token Plan
# in pane content -> set dispatch_pause, kill, release) ----------------------


class _FrozenClock:
    """Test clock for SessionRegistry's monotonic timer. Advancing moves
    spawn ages past the stuck timeout so the registry actually surfaces
    the name from get_stuck_sessions()."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.asyncio
async def test_reaper_reaps_stuck_session_with_429_and_pauses_dispatch(monkeypatch):
    """A session that's alive in tmux but never sent a hook (classic 429
    Token Plan signature: `claude` prints the error and never initialises
    hooks) must be killed by the reaper, its claim released, dispatch_failures
    bumped, and the global dispatch pause set to the fallback duration.
    Without this, the card sits claimed forever and auto-dispatch stalls."""
    from datetime import UTC, datetime, timedelta

    import app.kanban.dispatch as d
    from app.kanban import dispatch_pause
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-429-0001")
    clock.advance(200)  # past the default 120s stuck threshold
    monkeypatch.setattr(d, "session_registry", reg)

    # Mock capture-pane to simulate a 429 stuck tmux pane.
    pane = "API Error: 429 — Token Plan limit reached for this account"
    monkeypatch.setattr(
        d, "_capture_pane_content", lambda name, *, lines=20: pane,
    )
    killed = []
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: killed.append(name))

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck-429", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-429-0001"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-429-0001"}, project_path="/p",
        )
        await s.commit()
        card = await get_card(s, cid)
        paused_until = await dispatch_pause.get_paused_until(s)

    assert reaped == 1
    assert killed == ["k-429-0001"]
    assert card.claimed_by is None
    assert card.dispatch_failures == 1
    assert paused_until is not None
    # FALLBACK_PAUSE_HOURS = 5 — accept any wall-clock drift up to 60s.
    expected = datetime.now(UTC) + timedelta(hours=5)
    assert abs((paused_until - expected).total_seconds()) < 60


@pytest.mark.asyncio
async def test_reaper_skips_stuck_session_without_rate_limit(monkeypatch):
    """A session that's alive in tmux but just slow to send hooks (the pane
    shows ordinary work, no 429) must NOT be killed by the new reaper path —
    we'd otherwise silently lose a healthy in-flight session."""
    import app.kanban.dispatch as d
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-clean-0001")
    clock.advance(200)
    monkeypatch.setattr(d, "session_registry", reg)

    monkeypatch.setattr(
        d, "_capture_pane_content", lambda name, *, lines=20: "Working on it…",
    )
    killed = []
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: killed.append(name))

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck-clean", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-clean-0001"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-clean-0001"}, project_path="/p",
        )
        await s.commit()
        card = await get_card(s, cid)

    assert reaped == 0
    assert killed == []
    assert card.claimed_by == "agent:k-clean-0001"  # untouched


@pytest.mark.asyncio
async def test_reaper_stuck_session_fails_open_when_capture_pane_unavailable(monkeypatch):
    """If `capture-pane` itself fails (tmux not on PATH, timeout, …), the
    reaper must not act on the stuck session — fail-open is safer than
    killing a session whose pane we can't actually read."""
    import app.kanban.dispatch as d
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-failopen-1")
    clock.advance(200)
    monkeypatch.setattr(d, "session_registry", reg)

    # capture-pane returns None = failure to capture
    monkeypatch.setattr(d, "_capture_pane_content", lambda name, *, lines=20: None)
    killed = []
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: killed.append(name))

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck-no-capture", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-failopen-1"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-failopen-1"}, project_path="/p",
        )
        await s.commit()
        card = await get_card(s, cid)

    assert reaped == 0
    assert killed == []
    assert card.claimed_by == "agent:k-failopen-1"


@pytest.mark.asyncio
async def test_reaper_stuck_session_clears_resume_fields(monkeypatch):
    """When a 429-stuck session is reaped, any stale resume_session_id /
    resume_project_folder pointing at a since-merged worktree must be
    cleared too — otherwise the next dispatch picks the resume transport
    and re-spawns against a dead worktree, hitting the same 429 again."""
    import app.kanban.dispatch as d
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-stale-429")
    clock.advance(200)
    monkeypatch.setattr(d, "session_registry", reg)

    monkeypatch.setattr(
        d, "_capture_pane_content",
        lambda name, *, lines=20: "API Error: 429 - Token Plan",
    )
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: None)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stale-resume-429", column="engineer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"resume_session_id": "old-sess",
                                     "resume_project_folder": "-old-worktree"},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-stale-429"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-stale-429"}, project_path="/p",
        )
        await s.commit()
        card = await get_card(s, cid)

    assert reaped == 1
    assert card.resume_session_id is None
    assert card.resume_project_folder is None
    assert card.dispatch_failures == 1
    assert card.claimed_by is None


@pytest.mark.asyncio
async def test_reaper_stuck_session_repeated_failures_move_to_impediment(monkeypatch):
    """A card that hits a 429 three ticks in a row must end up in Impediment
    (same circuit breaker as the dead-on-arrival path), so a human can
    look at it instead of the loop burning dispatch ticks forever."""
    import app.kanban.dispatch as d
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    monkeypatch.setattr(d, "session_registry", reg)
    monkeypatch.setattr(
        d, "_capture_pane_content", lambda name, *, lines=20: "API Error: 429",
    )
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: None)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="repeat-429", column="engineer")
        await s.commit()

    for _ in range(dispatch.MAX_DISPATCH_FAILURES):
        # Simulate a fresh spawn that has now been alive past the stuck
        # threshold: mark first (captures the current clock), then advance
        # so the reap sees the spawn age as >= timeout_s.
        reg.clear_spawn("k-imp-0001")
        reg.mark_spawned("k-imp-0001")
        clock.advance(200)
        async with KanbanSessionLocal() as s:
            await apply_operation(
                s, op_type="claim", entity_type="card", project_key=PK,
                entity_id=cid, payload={"claimed_by": "agent:k-imp-0001"},
            )
            await s.commit()
            await dispatch.reap_stale_claims(
                s, project_key=PK, cards=await list_cards(s, PK),
                live_sessions={"k-imp-0001"}, project_path="/p",
            )
            await s.commit()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.column == "Impediment"
    assert card.claimed_by is None
    assert card.dispatch_failures == 0  # reset so a future redispatch starts fresh


def test_capture_pane_content_returns_pane_text(monkeypatch):
    """_capture_pane_content shells out to `tmux capture-pane` and returns
    stdout. Verifies the cmd shape (session name + tail lines) since
    regressions there would silently shift what content the rate-limit
    detector sees."""
    import app.kanban.dispatch as d

    seen = []

    class R:
        returncode = 0
        stdout = "Working..."
        stderr = ""

    def fake_run(cmd, *a, **k):
        seen.append(cmd)
        return R()

    monkeypatch.setattr(d.subprocess, "run", fake_run)
    out = d._capture_pane_content("k-test", lines=20)
    assert out == "Working..."
    assert seen[0][0:3] == ["tmux", "capture-pane", "-t"]
    assert seen[0][3] == "k-test"
    assert "-S" in seen[0]
    assert "-20" in seen[0]


def test_capture_pane_content_returns_none_on_failure(monkeypatch):
    """If tmux capture-pane fails (session gone, non-zero exit, FileNotFound,
    timeout) the helper returns None so the reaper fails open. Returning
    a partial/empty string would silently downgrade the detector."""
    import app.kanban.dispatch as d

    class RFail:
        returncode = 1
        stdout = ""
        stderr = "can't find pane"

    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: RFail())
    assert d._capture_pane_content("k-missing") is None

    def raise_fnf(*a, **k):
        raise FileNotFoundError("tmux not on PATH")

    monkeypatch.setattr(d.subprocess, "run", raise_fnf)
    assert d._capture_pane_content("k-missing") is None


def test_is_rate_limited_session_matches_known_patterns():
    """_is_rate_limited_session must recognise the same set of substrings
    that the hook-event path uses (so a 429 detected via either source
    triggers the same dispatch-pause), but not match ordinary progress
    output that just happens to mention numbers or 'plan'."""
    import app.kanban.dispatch as d
    assert d._is_rate_limited_session("API Error: 429 Too Many Requests") is True
    assert d._is_rate_limited_session("Token Plan limit reached") is True
    assert d._is_rate_limited_session("Hit your usage limit for the day") is True
    assert d._is_rate_limited_session("Request rejected: rate limit") is True
    assert d._is_rate_limited_session("api error (429)") is True
    # Negative cases — these must NOT trip the detector.
    assert d._is_rate_limited_session("Working on tests...") is False
    assert d._is_rate_limited_session("Planning the next refactor") is False
    assert d._is_rate_limited_session("") is False
    assert d._is_rate_limited_session("Compaction 1/2 complete") is False

