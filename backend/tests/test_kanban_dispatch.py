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

    def __call__(self, *, directory, prompt, session_name, cli_id="claude-code",
                 provider="anthropic", model=None):
        self.calls.append({"directory": directory, "prompt": prompt,
                           "session_name": session_name, "cli_id": cli_id,
                           "provider": provider, "model": model})
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


def test_card_prompt_executor_phase_has_retro_and_ship_steps():
    class _C:
        title = "T"
        description = ""
    prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct",
                                        phase="executor")
    assert "session-retro" in prompt
    assert "merge your branch into master" in prompt
    assert "npm run lint && npm run build" in prompt


def test_direct_ship_recipe_has_uncommitted_changes_preflight():
    """Direct-mode ship recipe must guard against the silent no-op where the
    detached worktree only sees COMMITTED state: uncommitted/untracked changes
    in the source worktree merge as "Everything up-to-date" instead of shipping.
    A pre-flight check must abort with an explicit error before the merge."""
    instructions = dispatch._build_ship_instructions("direct")
    assert "git diff --quiet HEAD" in instructions
    assert "ls-files --others --exclude-standard" in instructions
    assert "uncommitted" in instructions
    # The guard must sit before the detached-worktree merge it protects.
    assert instructions.index("git diff --quiet HEAD") < instructions.index(
        "git worktree add --detach"
    )


def test_card_prompt_analyst_phase_has_retro_but_no_ship_steps():
    """Analyst cards are planning-only: they get the retro step and the
    move-to-Done exit, but none of the engineer merge/frontend-ship steps
    (see docs/cockpit/headless-session-retro-decision.md)."""
    class _C:
        title = "T"
        description = ""
    prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct",
                                        phase="analyst")
    assert "session-retro" in prompt
    assert "Move the parent card to Done" in prompt
    assert "merge your branch into master" not in prompt
    assert "npm run lint && npm run build" not in prompt


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
    assert transport.calls[0]["cli_id"] == "claude-code"


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
    assert transport.calls[0]["cli_id"] == "mimo-code"
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
    assert transport.calls[0]["provider"] == "anthropic"


@pytest.mark.asyncio
async def test_dispatch_uses_column_default_provider():
    """A column configured with default_provider="minimax" (e.g. an "engineer"
    column meant for bulk coding work) routes its cards' spawn to that platform,
    while columns without one keep the default Anthropic subscription."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax",
        )
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
        card = await get_card(s, cid)
    assert card.column == "engineer"
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "minimax"


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
    # per-column override > card.model > column.default_model > persona frontmatter
    assert dispatch._effective_model("m5", "opus", "sonnet", "haiku") == "m5"
    assert dispatch._effective_model(None, "opus", "sonnet", "haiku") == "opus"
    assert dispatch._effective_model(None, None, "sonnet", "haiku") == "sonnet"
    assert dispatch._effective_model(None, None, None, "haiku") == "haiku"
    assert dispatch._effective_model(None, None, None, None) is None
    assert dispatch._effective_model("", "", "", "") is None


def test_effective_model_persona_fallback_suppressed_for_non_anthropic():
    # A persona `model:` alias (e.g. "opus") is Anthropic-only. When the column
    # routes to a non-Anthropic provider it must NOT leak in as --model, so the
    # provider env's native model (e.g. MiniMax-M3) stays in effect.
    assert dispatch._effective_model(None, None, None, "opus", provider="minimax") is None
    assert dispatch._effective_model(None, None, None, "opus", provider="bedrock") is None
    # Anthropic (or unknown/None provider) keeps the persona fallback.
    assert dispatch._effective_model(None, None, None, "opus", provider="anthropic") == "opus"
    assert dispatch._effective_model(None, None, None, "opus", provider=None) == "opus"
    # Explicit column-default / card / override models still win for any provider —
    # they may deliberately name a provider-native model.
    assert dispatch._effective_model(None, None, "MiniMax-M3", "opus", provider="minimax") == "MiniMax-M3"
    assert dispatch._effective_model(None, "MiniMax-M3", None, "opus", provider="minimax") == "MiniMax-M3"
    assert dispatch._effective_model("MiniMax-M3", None, None, "opus", provider="minimax") == "MiniMax-M3"


# ---- per-card column_overrides: model+provider per target column ----------

@pytest.mark.asyncio
async def test_dispatch_column_override_beats_column_defaults():
    """Parent-card scenario: an engineer column defaulting to minimax/M3, but a
    card carrying a per-column override for "engineer" spawns with the override's
    provider AND model instead — even though Sonnet 5 lives only on Anthropic."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax", default_model="MiniMax-M3[1m]",
        )
        cid = await _make_card(s)
        await apply_operation(s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"column_overrides": {
                "engineer": {"model": "sonnet-5", "provider": "anthropic"}}})
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "anthropic"
    assert transport.calls[0]["model"] == "sonnet-5"


@pytest.mark.asyncio
async def test_dispatch_column_override_beats_card_model():
    """A per-column override outranks card.model (the card-global override)."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await apply_operation(s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"model": "opus", "column_overrides": {
                "engineer": {"model": "sonnet-5", "provider": "anthropic"}}})
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] == "sonnet-5"


@pytest.mark.asyncio
async def test_dispatch_column_override_provider_only_leaves_model_fallthrough():
    """An override may set provider without model: the provider is overridden but
    the model still falls through to column.default_model / persona / None."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_model="sonnet",
        )
        cid = await _make_card(s)
        await apply_operation(s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"column_overrides": {
                "engineer": {"provider": "bedrock"}}})
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "bedrock"
    assert transport.calls[0]["model"] == "sonnet"


@pytest.mark.asyncio
async def test_dispatch_column_override_for_other_column_is_ignored():
    """An override keyed on a column other than the dispatch target has no effect
    — behaves as if no override existed for the resolved column."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await apply_operation(s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"column_overrides": {
                "analyst": {"model": "sonnet-5", "provider": "bedrock"}}})
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    # card dispatches into "engineer"; only an "analyst" override exists -> no effect
    assert transport.calls[0]["provider"] == "anthropic"
    assert transport.calls[0]["model"] is None


@pytest.mark.asyncio
async def test_dispatch_without_column_overrides_is_backwards_compatible():
    """A card with column_overrides=None dispatches exactly as it does today."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        card = await get_card(s, cid)
        assert card.column_overrides is None
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "anthropic"
    assert transport.calls[0]["model"] is None


# The two tests below share one card-shaped override dict spanning both the
# "analyst" and "engineer" columns and prove each phase resolves its OWN entry,
# because the lookup is keyed on the phase's resolved target column.
_BOTH_PHASE_OVERRIDES = {
    "analyst": {"model": "opus", "provider": "anthropic"},
    "engineer": {"model": "MiniMax-M3[1m]", "provider": "minimax"},
}


@pytest.mark.asyncio
async def test_dispatch_analyst_target_uses_analyst_override(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "analyst.md").write_text("You are the Analyst.")
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="Investigate", column="Backlog")
        await apply_operation(s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"agent": "analyst",
                                    "column_overrides": _BOTH_PHASE_OVERRIDES})
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path=str(tmp_path), transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "anthropic"
    assert transport.calls[0]["model"] == "opus"


@pytest.mark.asyncio
async def test_dispatch_engineer_target_uses_engineer_override():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await apply_operation(s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"column_overrides": _BOTH_PHASE_OVERRIDES})
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "minimax"
    assert transport.calls[0]["model"] == "MiniMax-M3[1m]"


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
    assert transport.calls[0]["cli_id"] == "claude-code"


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
    assert card.column == "engineer"        # first card got picked
    assert len(transport.calls) == 2        # both dispatchable cards get claimed


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
async def test_dispatch_fills_every_pending_card_in_one_tick():
    """Without a project-level cap, dispatch_project dispatches every
    dispatchable card in a single tick; per-column caps (when set) are the only
    structural limit at the dispatcher level."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        for i in range(4):
            await _make_card(s, title=f"c{i}", column="Backlog")
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert result is not None
    assert len(transport.calls) == 4


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
    """A column with max_sessions=NULL (unset) does not gate dispatch -- all
    dispatchable cards in that column get claimed in one tick."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
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
    assert len(transport.calls) == 4


@pytest.fixture
def project_with_analyst(tmp_path):
    """Project with engineer + analyst persona files, mirroring the real repo
    layout, so a work_type='analysis' card resolves to the analyst column."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    for name in ("engineer", "analyst"):
        (agents_dir / f"{name}.md").write_text(f"# {name}")
    return str(tmp_path)


@pytest.mark.asyncio
async def test_resolve_target_column_applies_work_type_fallback(project_with_analyst):
    """The cap gate (`_resolve_target_column`) resolves a card whose `agent` is
    a CLI id via the work_type fallback — the same way the spawn path
    (`_phase_target_agent`) does. A work_type='analysis' card with
    agent='claude-code' must resolve to 'analyst', not the hardcoded
    'engineer' fallback."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="analyse-me", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"agent": "claude-code", "work_type": "analysis"},
        )
        await s.flush()
        card = await get_card(s, cid)
        col = await dispatch._resolve_target_column(
            s, card, project_path=project_with_analyst, project_key=PK,
        )
    assert col == "analyst"


@pytest.mark.asyncio
async def test_analysis_card_gated_against_analyst_not_engineer(project_with_analyst):
    """Regression: a work_type='analysis' card whose `agent` is a CLI id
    ('claude-code', not a persona file) must be gated against its *real* target
    column (analyst) — the column the spawn resolves via the work_type
    fallback — not the hardcoded 'engineer' fallback. A saturated engineer
    column must not starve it while the analyst column still has room.

    Before the fix, `_resolve_target_column` dropped the work_type fallback and
    mis-resolved the card to 'engineer'; with engineer at its cap the card was
    skipped every tick and the analyst never picked it up."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(s, project_key=PK, name="engineer",
                                     default_agent="engineer", max_sessions=1)
        await service.create_column(s, project_key=PK, name="analyst",
                                     default_agent="analyst", max_sessions=2)
        # Saturate the engineer column with a live agent claim.
        busy_id = await _make_card(s, title="eng-busy", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy_id, payload={"claimed_by": "agent:k-eng-0001"},
        )
        # An analysis card carrying a CLI id in `agent` (as real cards do when
        # created with an explicit agent='claude-code').
        cid = await _make_card(s, title="analyse-me", column="Backlog")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"agent": "claude-code", "work_type": "analysis"},
        )
        await s.commit()

        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path=project_with_analyst,
            transport=transport,
        )
        await s.commit()

        moved = await get_card(s, cid)

    # The full engineer column did not block it; it was dispatched to analyst.
    assert result is not None
    assert len(transport.calls) == 1
    assert moved.column == "analyst"
    assert (moved.claimed_by or "").startswith("agent:")


@pytest.mark.asyncio
async def test_column_max_sessions_column_roundtrip():
    """max_sessions on a column can be set via create_column and read back."""
    async with KanbanSessionLocal() as s:
        await service.create_column(s, project_key=PK, name="testcol",
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
async def test_retry_queued_cards_drains_every_dispatchable_card(monkeypatch):
    """Without a per-project session cap, _retry_queued_cards dispatches every
    dispatchable queued card in one tick; per-column caps (when set) are the only
    structural limit at the retry path."""
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
        for i in range(4):
            ids.append(await _make_card(s, title=f"q{i}", column="Backlog"))
        await s.commit()

    for cid in ids:
        fresh_queue.enqueue(card_id=cid, project_key=PK, project_path="/p")

    await dispatch._retry_queued_cards(transport)

    assert len(transport.calls) == 4
    assert fresh_queue.size == 0


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
    monkeypatch.setattr(
        "app.kanban.dispatch.safe_resolve_project_key", lambda p: PK
    )
    async with KanbanSessionLocal() as s:
        await dispatch.set_default_transport(s, PK, "sandcastle")
        await s.commit()
    t = await dispatch.get_transport_for_project("/any/path")
    assert t is dispatch.sandcastle_transport


@pytest.mark.asyncio
async def test_get_transport_for_project_defaults_worktree(monkeypatch):
    monkeypatch.setattr(
        "app.kanban.dispatch.safe_resolve_project_key", lambda p: PK
    )
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
    # Backlog card is prioritised (it's new work); the orphan is dispatched
    # afterwards, in the same tick when no per-column cap blocks it.
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        orphan = await _make_card(s, title="orphaned", column="developer")
        waiting = await _make_card(s, title="waiting", column="Backlog")
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        orphan_card = await get_card(s, orphan)
        waiting_card = await get_card(s, waiting)
    assert waiting_card.claimed_by is not None   # Backlog card wins the priority
    assert orphan_card.claimed_by is not None    # orphan also dispatched this tick
    # Backlog card must be picked before the orphan in this tick.
    assert len(transport.calls) == 2
    assert "waiting" in transport.calls[0]["session_name"]
    assert "orphaned" in transport.calls[1]["session_name"]


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
async def test_reaper_spares_live_headless_claim_without_tmux():
    # Regression guard for the dispatch-loop documented in
    # docs/cockpit/headless-stream-json-transport-spike.md §5: a headless run has
    # no tmux session AND no SandcastleRun row, so neither of the two original
    # liveness sources can vouch for it. Without the third source (the
    # _live_headless_sessions set plumbed into the reaper), the reaper would
    # release + re-dispatch the card every tick — exactly the sandcastle bug
    # the new sibling was introduced to prevent.
    async with KanbanSessionLocal() as s:
        hl = await _make_card(s, title="headless WIP", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=hl, payload={"claimed_by": "agent:k-hl-0001"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions=set(), sandcastle_live=set(),
            headless_live={"k-hl-0001"},
        )
        await s.commit()
        card = await get_card(s, hl)
    assert reaped == 0
    assert card.claimed_by == "agent:k-hl-0001"


@pytest.mark.asyncio
async def test_reaper_reaps_dead_headless_claim():
    # Same shape as test_reaper_reaps_dead_sandcastle_claim: when the headless
    # subprocess is gone (not in headless_live) AND no tmux session AND no
    # sandcastle row, the stale claim is reaped.
    async with KanbanSessionLocal() as s:
        hl = await _make_card(s, title="headless dead", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=hl, payload={"claimed_by": "agent:k-hl-dead"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions=set(), sandcastle_live=set(),
            headless_live=set(),
        )
        await s.commit()
        card = await get_card(s, hl)
    assert reaped == 1
    assert card.claimed_by is None


# ---- transport selection ---------------------------------------------------


def test_default_transport_accepts_headless():
    # The TRANSPORTS tuple must include "headless" so it can be set as a
    # per-project default via KanbanMeta. Unknown values fall back to the
    # default ("worktree") — the legacy contract.
    import asyncio

    from app.kanban.models import KanbanMeta

    async def _check():
        async with KanbanSessionLocal() as s:
            s.add(KanbanMeta(key=dispatch.TRANSPORT_PREFIX + PK, value="headless"))
            await s.commit()
            return await dispatch.get_default_transport(s, PK)

    assert asyncio.run(_check()) == "headless"


def test_default_transport_falls_back_on_unknown_value():
    # Regression guard for the TRANSPORTS-tuple expansion: an unknown value
    # in the meta row silently falls back to the project default rather
    # than raising — the legacy contract that lets an operator recover from a
    # bad value without a DB migration.
    import asyncio

    from app.kanban.models import KanbanMeta

    async def _check():
        async with KanbanSessionLocal() as s:
            s.add(KanbanMeta(key=dispatch.TRANSPORT_PREFIX + PK, value="garbage"))
            await s.commit()
            return await dispatch.get_default_transport(s, PK)

    assert asyncio.run(_check()) == "worktree"  # DEFAULT_TRANSPORT


def test_get_transport_for_card_headless():
    # A card with transport="headless" resolves to headless_transport;
    # a card without it falls through to the project default. Resume
    # priority is preserved (the resume check happens first).
    from app.kanban.headless_runner import headless_transport

    card_hl = KanbanCard(transport="headless", project_key=PK)
    assert dispatch.get_transport_for_card(card_hl, default_transport=RecordingTransport()) is headless_transport

    card_default = KanbanCard(transport=None, project_key=PK)
    fallback = RecordingTransport()
    assert dispatch.get_transport_for_card(card_default, default_transport=fallback) is fallback

    # Resume still wins over an explicit transport= (legacy contract).
    card_resume = KanbanCard(
        transport="headless", project_key=PK,
        resume_session_id="resume-1", resume_project_folder="-home-x-y",
    )
    chosen = dispatch.get_transport_for_card(card_resume, default_transport=fallback)
    # Resume transports are unique per (session_id, folder); assert it's NOT
    # the headless one (any non-headless transport is fine — that's the contract).
    assert chosen is not headless_transport


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

    def fake_spawn(cli_id, options, session_name=None):
        captured["cli"] = cli_id
        captured["options"] = options
        captured["session_name"] = session_name
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}

    import app.kanban.dispatch as d
    monkeypatch.setattr(d.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "app.services.runs.spawn.spawn_session", fake_spawn)

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

    def fake_spawn(cli_id, options, session_name=None):
        raise RuntimeError("tmux exploded")

    import app.kanban.dispatch as d
    monkeypatch.setattr(d.subprocess, "run", fake_run)
    monkeypatch.setattr("app.services.runs.spawn.spawn_session", fake_spawn)

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
    # spawn_session's own collision fallback (runs.spawn._session_name_for)
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

    def resume_transport(*, directory, prompt, session_name, cli_id="claude-code", provider="anthropic", model=None):
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

    def resume_transport(*, directory, prompt, session_name, cli_id="claude-code", provider="anthropic", model=None):
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


# ---- priority sort on bulk dispatch paths ---------------------------------

def _dispatch_order(transport) -> list[str]:
    """Extract the order in which the recording transport was invoked, by
    matching each call's prompt against card titles (build_card_prompt embeds
    the title verbatim). Returns the title sequence in dispatch order."""
    out = []
    for call in transport.calls:
        prompt = call["prompt"]
        for title in ("urgent", "medium-card", "low-card", "card-a", "card-b",
                      "card-c", "orphan-high", "orphan-mid", "orphan-low"):
            if title in prompt:
                out.append(title)
                break
    return out


@pytest.mark.asyncio
async def test_dispatch_all_pending_dispatches_high_priority_first():
    """dispatch_all_pending sorts by priority desc (high → medium → low) before
    the per-card loop, so the manual "Dispatch All" button no longer falls back
    to rank FIFO when an operator tags urgent work."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Insert in rank order low → high → medium so a FIFO implementation
        # would dispatch in that order. The fix must reorder them.
        await _make_card(s, title="low-card", column="Backlog", priority="low")
        await _make_card(s, title="urgent", column="Backlog", priority="high")
        await _make_card(s, title="medium-card", column="Backlog", priority="medium")
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results) == 3
    assert _dispatch_order(transport) == ["urgent", "medium-card", "low-card"]


@pytest.mark.asyncio
async def test_dispatch_all_pending_preserves_rank_within_same_priority():
    """Stable sort on rank: within the same priority, older (lower-rank) cards
    still dispatch first — the fix must not scramble the existing tie-break."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Two high-priority cards (a created first, b created second) and one low
        await _make_card(s, title="card-a", column="Backlog", priority="high")
        await _make_card(s, title="card-b", column="Backlog", priority="high")
        await _make_card(s, title="card-c", column="Backlog", priority="low")
        await s.commit()
    async with KanbanSessionLocal() as s:
        await dispatch.dispatch_all_pending(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    order = _dispatch_order(transport)
    # High first (in rank order: a, b), then low
    assert order == ["card-a", "card-b", "card-c"]


@pytest.mark.asyncio
async def test_redispatch_all_orphans_dispatches_high_priority_first():
    """redispatch_all_orphans sorts orphans by priority desc, matching the
    auto-tick's _next_card behaviour."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        # Insert in rank order low → high → medium so a FIFO implementation
        # would dispatch in that order.
        await _make_card(s, title="orphan-low", column="developer", priority="low")
        await _make_card(s, title="orphan-high", column="testing", priority="high")
        await _make_card(s, title="orphan-mid", column="review", priority="medium")
        await s.commit()
    async with KanbanSessionLocal() as s:
        results = await dispatch.redispatch_all_orphans(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert len(results) == 3
    assert _dispatch_order(transport) == ["orphan-high", "orphan-mid", "orphan-low"]


def test_priority_key_helper_matches_priority_rank():
    """The extracted `_priority_key` helper must produce the same sort key the
    inline `_PRIORITY_RANK.get(c.priority, 0)` did — same numeric rank per
    priority, defaulting to 0 for unknown / None. Guards the helper extraction
    in dispatch.py."""
    class _C:
        def __init__(self, p):
            self.priority = p
    assert dispatch._priority_key(_C("high")) == 3
    assert dispatch._priority_key(_C("medium")) == 2
    assert dispatch._priority_key(_C("low")) == 1
    assert dispatch._priority_key(_C("none")) == 0
    assert dispatch._priority_key(_C(None)) == 0
    assert dispatch._priority_key(_C("garbage")) == 0


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

    def fake_sandcastle(*, directory, prompt, session_name, cli_id="claude-code", provider="anthropic", model=None):
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

    def fake_sandcastle(*, directory, prompt, session_name, cli_id="claude-code", provider="anthropic", model=None):
        sc_calls.append(session_name)
        return {"session_name": session_name, "transport": "sandcastle", "status": "started"}

    def fake_worktree(*, directory, prompt, session_name, cli_id="claude-code", provider="anthropic", model=None):
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

    def fake_spawn(cli_id, options, *, session_name):
        calls.append({"options": options, "session_name": session_name})
        return {"session_name": session_name}

    import unittest.mock as mock
    with mock.patch("app.services.runs.spawn.spawn_session", fake_spawn), \
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

    def resume_transport(*, directory, prompt, session_name, cli_id="claude-code", provider="anthropic", model=None):
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
async def test_move_to_resume_sets_scheduled_at_when_provided():
    """_move_to_resume writes an explicit scheduled_at onto the card, so the
    dispatch tick's _is_due check can hold it out of auto-dispatch until then."""
    import unittest.mock as mock

    from app.kanban import session_recovery

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="context-limit-scheduled", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-dead-0005"},
        )
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=("sess-abc", "proj-folder"),
    ):
        with mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
            async with KanbanSessionLocal() as s:
                card = await get_card(s, cid)
                result = await dispatch._move_to_resume(
                    s, card=card, project_key=PK, project_path="/p",
                    scheduled_at="2026-07-11T23:10:00+02:00",
                )
                await s.commit()
                card = await get_card(s, cid)

    assert result is True
    assert card.scheduled_at == "2026-07-11T23:10:00+02:00"


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
async def test_reaper_move_to_resume_sets_fallback_scheduled_at():
    """The reaper never has a parsed reset time (only tmux pane content, no
    Notification message) -- it must fall back to now + FALLBACK_PAUSE_HOURS so
    the card doesn't get immediately re-picked up by the next dispatch tick
    while the rate limit is still in effect."""
    import unittest.mock as mock
    from datetime import UTC, datetime, timedelta

    from app.kanban import session_recovery
    from app.services.scheduling.auto_resume import FALLBACK_PAUSE_HOURS

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="resumable-dead-fallback", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-dead-0006"},
        )
        await s.commit()

    before = datetime.now(UTC)
    with mock.patch.object(
        session_recovery, "_resolve_resume_target", return_value=("sess-fb", "proj-folder"),
    ):
        with mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
            async with KanbanSessionLocal() as s:
                reaped = await dispatch.reap_stale_claims(
                    s, project_key=PK, cards=await list_cards(s, PK),
                    live_sessions=set(), project_path="/p",
                )
                await s.commit()
                card = await get_card(s, cid)
    after = datetime.now(UTC)

    assert reaped == 1
    assert card.column == "To Resume"
    assert card.scheduled_at is not None
    fire_at = datetime.fromisoformat(card.scheduled_at)
    assert before + timedelta(hours=FALLBACK_PAUSE_HOURS) <= fire_at
    assert fire_at <= after + timedelta(hours=FALLBACK_PAUSE_HOURS)


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

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
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
async def test_move_limited_session_sets_scheduled_at_from_parsed_reset(monkeypatch):
    """When the Notification hook path has already parsed the reset time, it's
    passed through to move_limited_session_to_resume and lands on the card's
    scheduled_at so _is_due keeps the card out of dispatch until then --
    independent of when the global dispatch_pause expires."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="limit-hit-scheduled", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-live-0007"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-live-2", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-live-0007",
            scheduled_at="2026-07-11T23:10:00+02:00",
        )

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)

    assert result is True
    assert card.column == "To Resume"
    assert card.scheduled_at == "2026-07-11T23:10:00+02:00"


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_posts_comment_with_reset_time(monkeypatch):
    """After a successful move to To Resume, an activity comment surfaces WHY the
    card is there ("Rate-limit hit") and WHEN it will auto-resume (parsed reset
    time). Without this, the activity feed is silent and an operator has to dive
    into dispatch.py logs to understand the move."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="limit-hit-3", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-live-0008"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-live-3", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-live-0008",
            scheduled_at="2026-07-11T23:10:00+02:00",
        )
    assert result is True

    async with KanbanSessionLocal() as s:
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]
    assert any("Rate-limit hit" in t for t in comment_texts)
    assert any("2026-07-11T23:10:00+02:00" in t for t in comment_texts)


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_posts_fallback_comment_when_no_reset(monkeypatch):
    """When the Notification hook path couldn't parse a reset time, the
    activity comment falls back to the same ~5h window the reaper uses -- the
    activity feed mirrors what the global dispatch pause / scheduled_at tell the
    dispatcher."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="limit-hit-4", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-live-0009"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-live-4", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-live-0009",
        )

    async with KanbanSessionLocal() as s:
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]
    assert any("fallback" in t for t in comment_texts)


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_no_comment_when_move_fails(monkeypatch):
    """If the resume path can't find a resumable worktree (returns False), no
    comment is posted -- the card wasn't moved and there's nothing to explain."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="no-resume", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-no-resume"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target", return_value=None,
         ):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-no-resume",
            scheduled_at="2026-07-11T23:10:00+02:00",
        )
    assert result is False

    async with KanbanSessionLocal() as s:
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]
    assert not any("Rate-limit hit" in t for t in comment_texts)


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

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-no-such-session",
        )

    assert result is False


@pytest.mark.asyncio
async def test_move_limited_session_to_resume_returns_false_when_project_key_unresolved():
    """When the derived project path can't be resolved to a project key, bail out
    before touching the kanban DB at all."""
    import unittest.mock as mock

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=None
    ):
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

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
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

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
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


# ---- fase 2: spillover-bij-limiet (analyse §4 Optie B / §5) -----------------

@pytest.mark.asyncio
async def test_move_limited_session_spills_over_when_pool_has_capacity(monkeypatch):
    """A limit-hit card whose project has a pool with another available
    subscription is moved to To Resume WITHOUT a reset-time scheduled_at, so
    the next tick immediately re-dispatches it onto the spillover subscription
    (the just-limited provider is skipped via its per-provider pause). The
    activity comment says it's spilling over, not waiting."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery, subscription_pool
    from app.kanban.subscription_pool import PoolEntry

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)
    # No real usage signal in tests -> pick decision rides on paused providers.
    async def _no_snapshots(entries):
        return {}
    monkeypatch.setattr(dispatch, "_gather_pool_usage_snapshots", _no_snapshots)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="spill-429", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-spill-0001"},
        )
        # Pool: anthropic (the card's default provider) then minimax.
        await subscription_pool.set_subscription_pool(s, PK, [
            PoolEntry(provider="anthropic", model=None, drempel=0.9),
            PoolEntry(provider="minimax", model=None, drempel=0.9),
        ])
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-spill", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-spill-0001",
            scheduled_at="2026-07-11T23:10:00+02:00",
        )

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]

    assert result is True
    assert card.column == "To Resume"
    # Spillover: scheduled_at dropped so the card is immediately dispatch-eligible.
    assert card.scheduled_at is None
    assert any("spilling over" in t for t in comment_texts)


@pytest.mark.asyncio
async def test_move_limited_session_pauses_when_all_subscriptions_exhausted(monkeypatch):
    """When the pool has no other available subscription (single entry, whose
    provider just hit its limit), the card falls back to the existing
    per-provider pause: To Resume + reset-time scheduled_at, waiting for reset."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery, subscription_pool
    from app.kanban.subscription_pool import PoolEntry

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)
    async def _no_snapshots(entries):
        return {}
    monkeypatch.setattr(dispatch, "_gather_pool_usage_snapshots", _no_snapshots)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="exhausted-429", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-exhaust-0001"},
        )
        # Single-entry pool: anthropic (the card's provider). Once it's limited
        # there is nothing to spill to.
        await subscription_pool.set_subscription_pool(s, PK, [
            PoolEntry(provider="anthropic", model=None, drempel=0.9),
        ])
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-exhaust", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-exhaust-0001",
            scheduled_at="2026-07-11T23:10:00+02:00",
        )

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]

    assert result is True
    assert card.column == "To Resume"
    # No spillover: the reset-time pause is preserved so the card waits.
    assert card.scheduled_at == "2026-07-11T23:10:00+02:00"
    assert any("Auto-resume scheduled at" in t for t in comment_texts)


@pytest.mark.asyncio
async def test_move_limited_session_no_pool_keeps_reset_pause(monkeypatch):
    """Backward-compat: with no subscription pool configured, the reactive
    limit path is unchanged — reset-time scheduled_at is preserved."""
    import unittest.mock as mock

    import app.kanban.db as kdb
    from app.kanban import session_recovery

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="nopool-429", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-nopool-0001"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ), \
         mock.patch.object(
             session_recovery, "_resolve_resume_target",
             return_value=("sess-nopool", "proj-folder"),
         ), \
         mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        result = await dispatch.move_limited_session_to_resume(
            "/p/.claude/worktrees/k-nopool-0001",
            scheduled_at="2026-07-11T23:10:00+02:00",
        )

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)

    assert result is True
    assert card.column == "To Resume"
    assert card.scheduled_at == "2026-07-11T23:10:00+02:00"


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
async def test_auto_dispatch_tick_posts_comment_for_due_scheduled_card():
    """When the auto-dispatch tick picks up a card whose `scheduled_at` was in
    the past (i.e. auto-resuming, not force-dispatching), post an activity
    comment with the original scheduled_at so the operator can see the tick
    didn't force-dispatch early."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="auto-resume-me", column="To Resume",
                                scheduled_at="2000-01-01T00:00:00+00:00")
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]
    assert any("Auto-resuming" in t for t in comment_texts)
    assert any("2000-01-01T00:00:00+00:00" in t for t in comment_texts)


@pytest.mark.asyncio
async def test_auto_dispatch_tick_no_comment_for_unscheduled_card():
    """A card without `scheduled_at` isn't 'auto-resuming' — it's just a normal
    dispatch. No auto-resume comment should be posted."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="ordinary", column="Backlog")
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]
    assert not any("Auto-resuming" in t for t in comment_texts)


@pytest.mark.asyncio
async def test_manual_dispatch_card_does_not_post_auto_resume_comment():
    """Manual `dispatch_card` is an explicit human override (UI button). It
    shouldn't post the auto-resume comment that the auto-tick path posts — the
    operator already knows they triggered this."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="manual-resume", column="To Resume",
                                scheduled_at="2000-01-01T00:00:00+00:00")
        await s.commit()
        result = await dispatch.dispatch_card(
            s, card_id=cid, project_path="/p", transport=transport,
        )
        await s.commit()
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]
    assert result is not None
    assert not any("Auto-resuming" in t for t in comment_texts)


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
        # Merge happens through a throwaway detached worktree, not `git checkout
        # master` (which deterministically fails in a linked worktree — see the
        # [self-improve] card that motivated this recipe).
        assert "git worktree add --detach \"$TMP/m\" origin/master" in instructions
        assert "git checkout master" not in instructions
        assert "merge --no-ff" in instructions
        assert "push origin HEAD:master" in instructions
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
        assert "merge --no-ff" not in instructions
        assert "git worktree add --detach" not in instructions

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

    def test_frontend_gate_is_conditional_on_frontend_diff(self):
        """The frontend lint+build gate must only run when the branch actually
        touches ``frontend/`` — a docs-/backend-only branch would otherwise pay
        a multi-minute ``npm ci`` + build for zero coverage. The instructions
        must (a) probe the branch diff scoped to ``frontend/``, (b) keep the
        lint+build command for the touched case, and (c) emit a visible skip
        log for the untouched case."""
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            # (a) diff probe scoped to frontend/ against the branch base
            # (merge-base variant — origin/master alone false-positives when
            # master advanced on frontend/ since the branch was cut; card cd7ff20b)
            assert "git merge-base HEAD origin/master" in instructions
            assert "git diff --name-only \"$BASE\"" in instructions
            assert "git diff --name-only origin/master -- frontend/" not in instructions
            # untracked frontend files count too (fresh files not yet committed)
            assert "git ls-files --others --exclude-standard -- frontend/" in instructions
            # (b) the actual gate command survives, guarded by the probe
            assert "npm run lint && npm run build" in instructions
            # (c) explicit skip log when there is no frontend diff
            assert "geen frontend-diff — gate overgeslagen" in instructions

    def test_frontend_gate_installs_deps_when_node_modules_missing(self):
        """A dispatched worktree is a fresh ``git worktree add`` off
        origin/master with no ``node_modules`` (gitignored), so the frontend
        gate must install deps before running lint/build — otherwise the first
        run dies with ``eslint: not found`` / ``vite: not found``. The install
        must be guarded on a missing ``node_modules`` so repeat runs within the
        same session don't re-pay the ~40s install."""
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            # reproducible install matching CI (quality.yml uses `npm ci`)
            assert "npm ci" in instructions
            # only install when node_modules is absent
            assert "-d node_modules" in instructions or "-d frontend/node_modules" in instructions

    def test_frontend_gate_symlinks_main_node_modules_when_lockfile_matches(self):
        """Symptom (card 15cc257d…): a fresh worktree's `npm ci` adds ~40-90s
        to every frontend-touching card. When ``frontend/package-lock.json``
        is identical to origin/master, the main checkout's already-installed
        ``frontend/node_modules`` is safe to symlink — the lockfile diff
        against master is the correctness gate. The frontend gate must use
        this fast path; only fall back to ``npm ci`` when the lockfile
        diverged (a frontend-deps change)."""
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            # the shortcut itself: a symlink of the main checkout's node_modules
            assert "ln -s" in instructions
            # the lockfile-diff gate that decides whether the shortcut is safe
            assert "package-lock.json" in instructions
            # uses the same merge-base used by the FRONTEND_TOUCHED probe, so
            # the comparison never lies when master advanced since the branch
            # was cut (regression: card cd7ff20b)
            assert "git merge-base HEAD origin/master" in instructions
            # the fallback path remains documented — npm ci is the recovery
            # when the lockfile diverges OR main's node_modules is absent
            assert "npm ci" in instructions

    def test_frontend_gate_moves_partial_node_modules_aside_before_symlinking(self):
        """Secondary papercut (card 15cc257d…): an interrupted ``npm ci``
        leaves a partial ``node_modules`` (some scoped dirs present but
        missing ``.bin/``), which then fails confusingly with
        ``eslint: not found`` and blocks a plain symlink until moved aside.
        ``rm`` is deny-listed in ``.claude/settings.json``, so cleanup must
        use ``mv`` and the gate must detect the partial state by the missing
        ``.bin/`` directory."""
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            # detect partial install: node_modules exists but .bin/ does not
            assert "node_modules/.bin" in instructions
            # move it aside — `rm` is deny-listed, `mv` is the only safe move
            assert " mv " in instructions or instructions.startswith("mv ")
            # must NOT suggest `rm -rf node_modules` (rm is deny-listed)
            assert "rm -rf" not in instructions
            assert "rm -fr" not in instructions
            assert "rm node_modules" not in instructions

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


class TestBuildShipInstructionsSessionRetro:
    """The session-end retro step is injected between attach_deliverable and
    move_card→Done for executor/engineer cards in both ship modes (the analyst
    path is wired separately). The retro is the engine behind
    self-improvement: a `[self-improve]` card filed here survives past the
    transcript and lands on the dispatcher queue, while a comment in the
    transcript does not.
    """

    def test_direct_mode_includes_session_retro_step(self):
        instructions = dispatch._build_ship_instructions("direct")
        assert "session-retro" in instructions
        assert "self-improve" in instructions
        assert ".claude/skills/session-retro/SKILL.md" in instructions

    def test_pull_request_mode_includes_session_retro_step(self):
        instructions = dispatch._build_ship_instructions("pull-request")
        assert "session-retro" in instructions
        assert "self-improve" in instructions
        assert ".claude/skills/session-retro/SKILL.md" in instructions

    def test_session_retro_step_runs_after_attach_deliverable_and_before_move_card(self):
        """Acceptance: the retro must be the *last* step before move_card→Done
        (after ship + attach_deliverable, never before them). A retro wired
        earlier would burn time on lessons that ship-discipline should catch."""
        for mode in ("direct", "pull-request"):
            instructions = dispatch._build_ship_instructions(mode)
            attach_idx = instructions.index("attach_deliverable")
            retro_idx = instructions.index("session-retro")
            move_idx = instructions.index('move_card')
            assert attach_idx < retro_idx < move_idx, (
                f"order broken in {mode}: "
                f"attach@{attach_idx} retro@{retro_idx} move@{move_idx}"
            )

    def test_session_retro_step_uses_consistent_step_numbering(self):
        """The retro step is renumbered in each mode to fit between attach (5/6)
        and move (7/8). Regression guard: if the step number drifts, the agent
        loses its place."""
        # direct: attach=5, retro=6, move=7
        direct = dispatch._build_ship_instructions("direct")
        assert "5. **Attach the deliverable**" in direct
        assert "6. **Run the session-end retro**" in direct
        assert "7. **Move the card to Done**" in direct
        # pull-request: attach=6, retro=7, move=8
        pr = dispatch._build_ship_instructions("pull-request")
        assert "6. **Attach the deliverable**" in pr
        assert "7. **Run the session-end retro**" in pr
        assert "8. **Move the card**" in pr

    def test_build_card_prompt_includes_session_retro_step(self):
        """The retro step reaches the dispatch prompt (not just the helper)."""
        class _C:
            title = "My Card"
            description = "Do the thing"
        for mode in ("direct", "pull-request"):
            prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode=mode)
            assert "session-retro" in prompt, f"missing in {mode} prompt"
            assert "self-improve" in prompt, f"missing in {mode} prompt"


class TestBuildCardPromptSessionEnd:
    """build_card_prompt includes the Session-end workflow section."""

    def test_direct_mode_includes_session_end_section(self):
        class _C:
            title = "My Card"
            description = "Do the thing"
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
        assert "Session-end workflow" in prompt
        assert "merge --no-ff" in prompt
        assert "push origin HEAD:master" in prompt
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

    def test_impediment_prompt_renders_answer_as_authoritative(self):
        """When resolve_impediment forwards a chosen gate answer as the separate
        `impediment_answer` field, build_card_prompt must surface it under the
        `## IMPEDIMENT` section as an authoritative decision — so the resumed
        agent acts on it instead of re-asking the question."""
        class _C:
            title = "Bug"
            description = "Fix the crash"
        prompt = dispatch.build_card_prompt(
            _C(), persona="You are a debugger.", ship_mode="direct",
            impediment_question="Postgres or SQLite?",
            impediment_answer="Postgres",
        )
        assert "## IMPEDIMENT" in prompt
        assert "Postgres or SQLite?" in prompt
        # The chosen answer is rendered as authoritative (decision language),
        # not as an open question — so the resumed session acts on it.
        assert "Postgres" in prompt
        assert "authoritative" in prompt

    def test_impediment_prompt_without_answer_keeps_legacy_question_framing(self):
        """Backwards compat: when no answer was given (legacy free-text
        impediment), the IMPEDIMENT section keeps the open-question framing
        instead of the authoritative-decision framing."""
        class _C:
            title = "Bug"
            description = "Fix the crash"
        prompt = dispatch.build_card_prompt(
            _C(), persona="You are a debugger.", ship_mode="direct",
            impediment_question="Where is the crash?",
        )
        assert "## IMPEDIMENT" in prompt
        assert "Where is the crash?" in prompt
        assert "clarify what's needed" in prompt


class TestBuildCardPromptHostCardId:
    """The dispatched agent must see its host card's full id in the prompt
    header, so it can call `comment`/`attach_deliverable`/`move_card` on the
    right card by id instead of guessing from the prose (which may quote other
    card ids, leading to short-prefix collisions — see kanban card "Executor
    prompt omits host card_id; ids in card text mislead MCP calls")."""

    def test_executor_prompt_includes_host_card_id_label(self):
        class _C:
            title = "T"
            description = ""
            id = "abcdef1234567890"
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
        assert "Host card id: abcdef1234567890" in prompt

    def test_analyst_prompt_includes_host_card_id_label(self):
        """Analyst phase renders a lighter ship-instructions block, but the
        host-card-id line lives above the phase split and must surface in
        both phases."""
        class _C:
            title = "T"
            description = ""
            id = "abcdef1234567890"
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct",
                                            phase="analyst")
        assert "Host card id: abcdef1234567890" in prompt

    def test_host_card_id_appears_unambiguously_when_description_quotes_other_ids(self):
        """Regression for the actual bug: the card description cites another
        card's short id (`3ffdc75e`), and the agent mistook it for the host
        id. With an explicit `Host card id:` label, the agent copies the
        labeled value verbatim instead of scraping ids from prose."""
        class _C:
            title = "Self-improve card"
            description = (
                "Earlier evidence mentioned card 3ffdc75e but that's a "
                "different Done card. This card's id is the real one."
            )
            id = "5b63cafe00000001"
        prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="direct")
        assert "Host card id: 5b63cafe00000001" in prompt
        # The misleading short id still appears in the description (that's
        # fine — it's evidence text), but the host id is unambiguous.
        assert "Host card id: 3ffdc75e" not in prompt


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


# ---- clear_dispatch_pause (manual operator override) ----------------------

@pytest.mark.asyncio
async def test_clear_dispatch_pause_clears_an_active_pause():
    from datetime import datetime, timedelta

    from app.kanban.dispatch_pause import is_dispatch_paused, set_paused_until

    async with KanbanSessionLocal() as s:
        await set_paused_until(s, datetime.now(UTC) + timedelta(minutes=10))
        await s.commit()

    async with KanbanSessionLocal() as s:
        cleared, was_paused = await dispatch.clear_dispatch_pause(s)
        await s.commit()
        assert (cleared, was_paused) == (True, True)

    async with KanbanSessionLocal() as s:
        assert await is_dispatch_paused(s) is False


@pytest.mark.asyncio
async def test_clear_dispatch_pause_is_noop_when_not_paused():
    async with KanbanSessionLocal() as s:
        cleared, was_paused = await dispatch.clear_dispatch_pause(s)
        assert (cleared, was_paused) == (False, False)


@pytest.mark.asyncio
async def test_clear_dispatch_pause_comments_on_to_resume_cards():
    from datetime import datetime, timedelta

    from app.kanban.dispatch_pause import set_paused_until

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="Rate limited", column="To Resume")
        other_cid = await _make_card(s, title="Untouched", column="Backlog")
        await set_paused_until(s, datetime.now(UTC) + timedelta(minutes=10))
        await s.commit()

    async with KanbanSessionLocal() as s:
        cleared, was_paused = await dispatch.clear_dispatch_pause(s)
        await s.commit()
        assert (cleared, was_paused) == (True, True)

    async with KanbanSessionLocal() as s:
        to_resume_activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in to_resume_activity if op.op_type == "comment"
        ]
        assert any("cleared manually" in text for text in comment_texts)

        other_activity = await service.card_activity(s, other_cid)
        assert not any(op.op_type == "comment" for op in other_activity)


@pytest.mark.asyncio
async def test_clear_dispatch_pause_lets_next_tick_run(monkeypatch):
    """After a manual clear, the next dispatch tick must not be skipped -- this
    is the actual point of the override: unstick a tick the auto-detection
    paused incorrectly, without waiting for the wall-clock deadline."""
    import unittest.mock as mock
    from datetime import datetime, timedelta

    import app.kanban.db as kdb
    from app.kanban.dispatch_pause import set_paused_until

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        await set_paused_until(s, datetime.now(UTC) + timedelta(minutes=10))
        await s.commit()

    async with KanbanSessionLocal() as s:
        await dispatch.clear_dispatch_pause(s)
        await s.commit()

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
    # tagged so the board renders it red — a technical dispatch failure, not a
    # human-parked impediment (see dispatch.ERROR_LABEL / CardItem.tsx)
    assert dispatch.ERROR_LABEL in (card.labels or [])


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
async def test_synchronous_spawn_failure_comment_includes_last_error():
    """When a synchronous spawn exception (str(exc)) trips
    MAX_DISPATCH_FAILURES, the auto-move comment must include the actual
    error message — not just the generic "Check the backend logs" hint —
    so triage doesn't need a logs-dive. Verifies kanban card
    5ec5a68013da4422b0a49fb2731cb8a7 ("Impediment-comment toont echte
    spawn-error niet")."""
    transport = RecordingTransport(fail=True)
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="explode-with-error", column="To Resume")
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
        activity = await service.card_activity(s, card.id)
    assert card.column == "Impediment"
    failure_comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment"
        and op.payload["text"].startswith("[dispatch-failure]")
    ]
    assert failure_comments, "no dispatch-failure auto-move comment posted"
    # RecordingTransport raises `RuntimeError("tmux exploded")` so str(exc)
    # is "tmux exploded" — the comment must carry it (not just the legacy
    # "check the logs" hint) for the operator to triage in one read.
    assert "tmux exploded" in failure_comments[-1]
    # The structured prefix must remain intact — `impediment_status_for_card`
    # uses it to classify the card as dispatch_failed (not needs_answer).
    assert failure_comments[-1].startswith("[dispatch-failure]")


@pytest.mark.asyncio
async def test_synchronous_spawn_failure_comment_truncates_long_error(monkeypatch):
    """A pathological exception (10 KB of noise) still produces a
    single-line, length-capped comment — the activity feed stays
    readable, and a runaway traceback can't dominate the thread."""
    class LoudTransport(RecordingTransport):
        def __call__(self, **kwargs):
            self.calls.append(kwargs)
            # 1000-char message — the truncation caps the comment at 300.
            raise ValueError("boom: " + ("x" * 1000))

    transport = LoudTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="loud-explode", column="To Resume")
        await s.commit()

    for _ in range(dispatch.MAX_DISPATCH_FAILURES):
        async with KanbanSessionLocal() as s:
            with pytest.raises(ValueError):
                await dispatch.dispatch_project(
                    s, project_key=PK, project_path="/p", transport=transport,
                )
            await s.commit()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        activity = await service.card_activity(s, card.id)
    assert card.column == "Impediment"
    failure_comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment"
        and op.payload["text"].startswith("[dispatch-failure]")
    ]
    assert failure_comments, "no dispatch-failure auto-move comment posted"
    # The 300-char cap keeps the comment from absorbing a runaway exception
    # verbatim; the "..." marker tells the reader it's truncated.
    assert "..." in failure_comments[-1]


@pytest.mark.asyncio
async def test_reaper_dead_on_arrival_impediment_keeps_legacy_fallback():
    """The reap path (`_release_dead_claim`'s dead-on-arrival branch)
    doesn't see the original spawn exception — the session was spawned
    successfully, then died. Without a captured pane the comment must
    keep the legacy "Check the backend logs" hint so operators know where
    to look. Bounds the no-last-error branch of `_move_to_impediment_after_..`."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="reap-fallback", column="engineer")
        # Pre-arm dispatch_failures so the *next* do-a reap pushes the card
        # past MAX_DISPATCH_FAILURES instead of just bumping the counter.
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"dispatch_failures":
                                     dispatch.MAX_DISPATCH_FAILURES - 1},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-reap-fallback"},
        )
        await s.commit()
        await _backdate_claim(s, cid, dispatch.DEAD_ON_ARRIVAL_SECONDS - 5)
        await s.commit()
        await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK), live_sessions=set())
        await s.commit()
        card = await get_card(s, cid)
        activity = await service.card_activity(s, cid)
    assert card.column == "Impediment"
    failure_comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment"
        and op.payload["text"].startswith("[dispatch-failure]")
    ]
    assert failure_comments, "no dispatch-failure auto-move comment posted"
    # Reap path has no last_error → falls back to the legacy hint.
    assert "Check the backend logs" in failure_comments[-1]


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


# ---- portfolio-cap: gate the sum of agent-claims across all projects -------


async def _make_claimed_agent_card(s, project_key, session_name):
    """Create a card, move it into an agent column with an `agent:` claim so it
    counts toward _active_session_count for `project_key`."""
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=project_key,
        entity_id=None, payload={"title": "busy", "column": "Backlog"},
    )
    await s.flush()
    card = await get_card(s, cid)
    card.column = "engineer"
    card.claimed_by = f"agent:{session_name}"
    await s.flush()
    return cid


@pytest.mark.asyncio
async def test_run_dispatch_tick_skips_when_portfolio_cap_reached(monkeypatch, caplog):
    """5 autodispatch projects each holding 1 agent-claim (total 5) with cap=4:
    the whole tick is skipped before any per-project dispatch runs."""
    import logging
    import unittest.mock as mock

    import app.kanban.db as kdb

    keys = [f"git:example.com/me/repo{i}" for i in range(5)]
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)
    monkeypatch.setattr(dispatch, "_retry_queued_cards", mock.AsyncMock())
    monkeypatch.setattr(dispatch, "list_autodispatch_projects",
                        mock.AsyncMock(return_value=keys))
    monkeypatch.setattr(dispatch, "_registered_project_paths",
                        mock.AsyncMock(return_value=["/p"]))
    match_mock = mock.Mock(return_value={keys[0]: "/p"})
    monkeypatch.setattr(dispatch, "match_project_paths", match_mock)
    monkeypatch.setattr(dispatch.settings, "portfolio_cap_enabled", True)
    monkeypatch.setattr(dispatch.settings, "portfolio_cap_value", 4)

    async with KanbanSessionLocal() as s:
        for i, key in enumerate(keys):
            await _make_claimed_agent_card(s, key, session_name=f"s{i}")
        # A dispatchable card that would be spawned if the tick weren't skipped.
        await _make_card(s, title="pending", column="Backlog")
        await s.commit()

    transport = RecordingTransport()
    with caplog.at_level(logging.INFO, logger="app.kanban.dispatch"):
        await dispatch.run_dispatch_tick(transport=transport)

    assert len(transport.calls) == 0          # returned before the dispatch loop
    match_mock.assert_not_called()            # never reached path resolution
    assert "portfolio-cap reached (5/4 active sessions across 5 projects)" in caplog.text


@pytest.mark.asyncio
async def test_run_dispatch_tick_ignores_portfolio_cap_when_disabled(monkeypatch):
    """With the feature flag off, the same 5-claims-over-cap-4 state does not
    short-circuit the tick — a pending card in an enabled project is dispatched."""
    import unittest.mock as mock

    import app.kanban.db as kdb

    keys = [f"git:example.com/me/repo{i}" for i in range(5)]
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)
    monkeypatch.setattr(dispatch, "_retry_queued_cards", mock.AsyncMock())
    monkeypatch.setattr(dispatch, "list_autodispatch_projects",
                        mock.AsyncMock(return_value=keys))
    monkeypatch.setattr(dispatch, "_registered_project_paths",
                        mock.AsyncMock(return_value=["/p"]))
    monkeypatch.setattr(dispatch, "match_project_paths",
                        lambda *a, **kw: {keys[0]: "/p"})
    monkeypatch.setattr(dispatch, "_live_sessions", lambda: set())
    monkeypatch.setattr(dispatch, "_live_sandcastle_sessions",
                        mock.AsyncMock(return_value=set()))
    monkeypatch.setattr(dispatch.settings, "portfolio_cap_enabled", False)
    monkeypatch.setattr(dispatch.settings, "portfolio_cap_value", 4)

    async with KanbanSessionLocal() as s:
        for i, key in enumerate(keys):
            await _make_claimed_agent_card(s, key, session_name=f"s{i}")
        # Pending card under the one project that maps to a local path, so the
        # tick has something to dispatch once it does not short-circuit.
        await apply_operation(
            s, op_type="create", entity_type="card", project_key=keys[0],
            entity_id=None, payload={"title": "pending", "column": "Backlog"},
        )
        await s.commit()

    transport = RecordingTransport()
    await dispatch.run_dispatch_tick(transport=transport)

    assert len(transport.calls) >= 1          # dispatch proceeded despite 5 claims


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
    # The 429 path now pauses per-provider (anthropic by default -- no
    # column default in this test, no override) rather than the legacy
    # global slot. The legacy global slot is intentionally untouched so
    # other providers' traffic is not collateral-frozen.
    assert paused_until is None
    async with KanbanSessionLocal() as s2:
        paused_provider = await dispatch_pause.get_paused_until(
            s2, provider="anthropic",
        )
    assert paused_provider is not None
    # FALLBACK_PAUSE_HOURS = 5 — accept any wall-clock drift up to 60s.
    expected = datetime.now(UTC) + timedelta(hours=5)
    assert abs((paused_provider - expected).total_seconds()) < 60


@pytest.mark.asyncio
async def test_cleanup_stuck_rate_limited_session_posts_comment(monkeypatch):
    """When the stuck-session reaper reaps a 429 session, an activity comment
    on the card surfaces what happened and why the card was released -- the
    'tmux killed, claim released, dispatch paused for ~5h' lifecycle is otherwise
    invisible from the activity feed."""
    import app.kanban.dispatch as d
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-429-comment")
    clock.advance(200)
    monkeypatch.setattr(d, "session_registry", reg)

    pane = "API Error: 429 — Token Plan limit reached for this account"
    monkeypatch.setattr(d, "_capture_pane_content", lambda name, *, lines=20: pane)
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: None)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck-429-comment", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-429-comment"},
        )
        await s.commit()
        await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-429-comment"}, project_path="/p",
        )
        await s.commit()
        activity = await service.card_activity(s, cid)
        comment_texts = [
            op.payload["text"] for op in activity if op.op_type == "comment"
        ]

    assert any("Stuck session" in t for t in comment_texts)
    assert any("429" in t for t in comment_texts)
    assert any("~5h" in t for t in comment_texts)


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
    # tagged so the board renders it red — a technical dispatch failure, not a
    # human-parked impediment (see dispatch.ERROR_LABEL / CardItem.tsx)
    assert dispatch.ERROR_LABEL in (card.labels or [])


@pytest.mark.asyncio
async def test_reaper_stuck_session_impediment_comment_includes_pane(monkeypatch):
    """When a 429 rate-limit session trips MAX_DISPATCH_FAILURES, the
    dispatch-failure auto-move comment must surface the captured pane
    content (`API Error: 429 …`) so the operator sees the actual rate-
    limit reason — not just "Check the backend logs". See kanban card
    5ec5a68013da4422b0a49fb2731cb8a7."""
    import app.kanban.dispatch as d
    from app.services.scheduling import session_registry as sreg

    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    monkeypatch.setattr(d, "session_registry", reg)
    monkeypatch.setattr(
        d, "_capture_pane_content",
        lambda name, *, lines=20: "API Error: 429 rate limit reached",
    )
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: None)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="repeat-429-with-pane", column="engineer")
        await s.commit()

    for _ in range(dispatch.MAX_DISPATCH_FAILURES):
        reg.clear_spawn("k-imp-0002")
        reg.mark_spawned("k-imp-0002")
        clock.advance(200)
        async with KanbanSessionLocal() as s:
            await apply_operation(
                s, op_type="claim", entity_type="card", project_key=PK,
                entity_id=cid, payload={"claimed_by": "agent:k-imp-0002"},
            )
            await s.commit()
            await dispatch.reap_stale_claims(
                s, project_key=PK, cards=await list_cards(s, PK),
                live_sessions={"k-imp-0002"}, project_path="/p",
            )
            await s.commit()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        activity = await service.card_activity(s, cid)
    assert card.column == "Impediment"
    failure_comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment"
        and op.payload["text"].startswith("[dispatch-failure]")
    ]
    assert failure_comments, "no dispatch-failure auto-move comment posted"
    assert "API Error: 429" in failure_comments[-1]


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


# ---- structured-signal fast path (acp-transport-decision.md §6 kaart 2) -----
#
# The reaper previously inspected tmux pane content for 429 substrings. The
# card above (§6 kaart 1 / orchestration-substrate §6 kaart 2) replaces that
# with typed Notification-classification signals recorded by the hook
# endpoint. The tests below verify the fast path works and the pane-scan
# fallback still kicks in for sessions that never fired a hook.


@pytest.mark.asyncio
async def test_reaper_stuck_session_uses_structured_signal_when_recorded(monkeypatch):
    """The structured-signal fast path: when a Notification(kind=limit) has
    already been recorded for the stuck session, the reaper must trigger
    the full cleanup (kill tmux + dispatch pause + dispatch_failures bump)
    without needing to scrape the pane. The pane may have been cleared by
    the time the reaper runs, so a real-world test would have the capture
    return None or unrelated text — we assert the structured signal alone
    is enough to act."""
    import app.kanban.dispatch as d
    from app.services.scheduling import session_registry as sreg
    from app.services.scheduling import session_signals as ssignals

    ssignals.session_signals.clear("k-struct-0001")
    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-struct-0001")
    clock.advance(200)
    monkeypatch.setattr(d, "session_registry", reg)

    # Record the structured signal before the reaper runs — this is the
    # normal lifecycle: Notification hook fires, classify says "limit",
    # registry records, then the reaper sweeps on its next tick.
    ssignals.session_signals.record_limit(
        "/p/.claude/worktrees/k-struct-0001",
        "API Error: 429 rate limit reached",
    )
    # Pane scrape would return unrelated text or fail — the structured
    # signal must still drive the cleanup.
    monkeypatch.setattr(
        d, "_capture_pane_content", lambda name, *, lines=20: None,
    )
    killed = []
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: killed.append(name))

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck-struct", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-struct-0001"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-struct-0001"}, project_path="/p",
        )
        await s.commit()
        card = await get_card(s, cid)

    assert reaped == 1
    assert killed == ["k-struct-0001"]
    assert card.claimed_by is None  # claim released by the cleanup path
    assert card.dispatch_failures == 1
    ssignals.session_signals.clear("k-struct-0001")


@pytest.mark.asyncio
async def test_reaper_stuck_session_still_falls_back_to_pane_without_signal(monkeypatch):
    """The fail-open path: when no structured signal has been recorded (the
    classic 429-on-first-spawn case where the `claude` process died before
    initialising hooks), the reaper must still catch the rate-limit via the
    pane substring-match — that's the entire reason the pane scrape
    survived this refactor."""
    import app.kanban.dispatch as d
    from app.services.scheduling import session_registry as sreg
    from app.services.scheduling import session_signals as ssignals

    ssignals.session_signals.clear("k-pane-0001")
    clock = _FrozenClock()
    monkeypatch.setattr(sreg.time, "monotonic", clock)
    reg = sreg.SessionRegistry()
    reg.mark_spawned("k-pane-0001")
    clock.advance(200)
    monkeypatch.setattr(d, "session_registry", reg)
    # No structured signal recorded — session never fired a hook.
    monkeypatch.setattr(
        d, "_capture_pane_content", lambda name, *, lines=20: "API Error: 429",
    )
    killed = []
    monkeypatch.setattr(d, "_kill_agent_session", lambda name: killed.append(name))

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck-pane", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-pane-0001"},
        )
        await s.commit()
        reaped = await dispatch.reap_stale_claims(
            s, project_key=PK, cards=await list_cards(s, PK),
            live_sessions={"k-pane-0001"}, project_path="/p",
        )
        await s.commit()
        card = await get_card(s, cid)

    assert reaped == 1
    assert killed == ["k-pane-0001"]
    assert card.dispatch_failures == 1
    ssignals.session_signals.clear("k-pane-0001")


# ---- post_agent_status_comment (CC 2.1.198+ background-agent notifications) -


@pytest.mark.asyncio
async def test_post_agent_status_comment_writes_to_claimed_card(monkeypatch):
    """A `agent_needs_input` / `agent_completed` notification for a
    kanban-dispatched session lands as a comment op on the card claimed
    by that session. The card itself is NOT moved (no `move` op emitted,
    column unchanged)."""
    import unittest.mock as mock

    from sqlalchemy import select

    from app.kanban import db as kdb
    from app.kanban.models import KanbanOp

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="background agent card", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-bg-0001"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ):
        result = await dispatch.post_agent_status_comment(
            "/p/.claude/worktrees/k-bg-0001", "Session reported completion",
        )

    assert result is True

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        comment_rows = (await s.execute(
            select(KanbanOp)
            .where(KanbanOp.entity_id == cid)
            .where(KanbanOp.op_type == "comment")
        )).scalars().all()
        move_rows = (await s.execute(
            select(KanbanOp)
            .where(KanbanOp.entity_id == cid)
            .where(KanbanOp.op_type == "move")
        )).scalars().all()

    # Card column is untouched; the only side effect is the activity comment.
    assert card.column == "engineer"
    assert [r.payload.get("text") for r in comment_rows] == [
        "Session reported completion",
    ]
    assert move_rows == []


@pytest.mark.asyncio
async def test_post_agent_status_comment_ignores_non_worktree_cwd():
    """A cwd that isn't a `<project>/.claude/worktrees/<name>` shape (a
    manual `claude` session, sandcastle, project root) must not be
    touched. Same contract as ``move_limited_session_to_resume`` — the
    hook path is a no-op for non-kanban sessions."""
    result = await dispatch.post_agent_status_comment(
        "/home/me/some-project", "Session is waiting for input",
    )
    assert result is False


@pytest.mark.asyncio
async def test_post_agent_status_comment_returns_false_when_no_matching_card(monkeypatch):
    """No card claimed by that session -> no-op, even if the cwd shape matches."""
    import unittest.mock as mock

    import app.kanban.db as kdb

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        await _make_card(s, title="unrelated", column="engineer")
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ):
        result = await dispatch.post_agent_status_comment(
            "/p/.claude/worktrees/k-no-such-session", "Session reported completion",
        )

    assert result is False


@pytest.mark.asyncio
async def test_post_agent_status_comment_returns_false_when_project_key_unresolved():
    """Unresolvable project path -> bail out before touching the DB."""
    import unittest.mock as mock

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=None
    ):
        result = await dispatch.post_agent_status_comment(
            "/p/.claude/worktrees/k-unknown-0001", "Session is waiting for input",
        )

    assert result is False


@pytest.mark.asyncio
async def test_post_agent_status_comment_skips_cards_in_terminal_columns(monkeypatch):
    """Cards already on Done / To Resume must not receive a fresh
    'agent finished' comment — the operator has already declared an
    outcome, and re-commenting would be noise."""
    import unittest.mock as mock

    import app.kanban.db as kdb

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    for terminal_col in ("Done", "To Resume"):
        async with KanbanSessionLocal() as s:
            cid = await _make_card(
                s, title=f"finished card in {terminal_col}",
                column=terminal_col,
            )
            await apply_operation(
                s, op_type="claim", entity_type="card", project_key=PK,
                entity_id=cid, payload={"claimed_by": f"agent:k-term-{terminal_col}"},
            )
            await s.commit()

        with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ):
            result = await dispatch.post_agent_status_comment(
                f"/p/.claude/worktrees/k-term-{terminal_col}",
                "Session reported completion",
            )

        assert result is False, (
            f"post_agent_status_comment must skip cards on {terminal_col}"
        )



# ---- child-card plan_ref dispatch gate (create_card→add_plan_attachment race) --

async def _make_child(s, *, parent_card_id, title="child", column="Backlog"):
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None,
        payload={"title": title, "column": column,
                 "parent_card_id": parent_card_id},
    )
    await s.flush()
    return cid


async def _link_plan_ref(s, *, child_id, parent_id, plan_deliverable_id="plan-1"):
    import json
    await apply_operation(
        s, op_type="link_plan_ref", entity_type="deliverable",
        project_key=PK, entity_id=child_id,
        payload={"ref_json": json.dumps({
            "parent_card_id": parent_id,
            "plan_deliverable_id": plan_deliverable_id,
        }), "depends_on": []},
    )
    await s.flush()


@pytest.mark.asyncio
async def test_child_without_plan_ref_is_not_dispatched():
    """Race case: the analyst created a child (create_card) but hasn't attached
    the plan yet (add_plan_attachment). The child must NOT be dispatched — it
    would otherwise get the 'Plan niet beschikbaar' placeholder prompt."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        parent = await _make_card(s, title="parent", column="Done")
        child = await _make_child(s, parent_card_id=parent, title="child")
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        child_card = await get_card(s, child)
    assert transport.calls == []            # nothing spawned
    assert child_card.column == "Backlog"   # child stayed put, unclaimed
    assert not child_card.claimed_by


@pytest.mark.asyncio
async def test_child_with_plan_ref_is_dispatched():
    """Once add_plan_attachment has linked the plan_ref, the same child becomes
    dispatch-eligible and is spawned normally."""
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        parent = await _make_card(s, title="parent", column="Done")
        child = await _make_child(s, parent_card_id=parent, title="child")
        await _link_plan_ref(s, child_id=child, parent_id=parent)
        await s.commit()
        await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
        child_card = await get_card(s, child)
    assert len(transport.calls) == 1        # child got spawned
    assert child_card.column != "Backlog"   # moved into an agent column
    assert child_card.claimed_by


@pytest.mark.asyncio
async def test_next_card_gate_distinguishes_race_from_genuine_miss():
    """The plan_ref gate keeps the race case (plan attached moments later) out of
    dispatch, while the genuine-miss placeholder path is only reached by a child
    that DOES hold a plan_ref pointing at a now-missing parent/plan."""
    async with KanbanSessionLocal() as s:
        parent = await _make_card(s, title="parent", column="Done")
        # Race case: child without plan_ref -> gated, _next_card skips it.
        raced = await _make_child(s, parent_card_id=parent, title="raced")
        await s.commit()
        cards = await list_cards(s, PK)
        raced_card = next(c for c in cards if c.id == raced)
        assert dispatch._awaiting_plan_ref(raced_card) is True
        assert dispatch._next_card([raced_card]) is None

        # Genuine-miss case: child holds a plan_ref, but the parent is gone.
        await _link_plan_ref(
            s, child_id=raced, parent_id="deleted-parent",
            plan_deliverable_id="gone",
        )
        await s.commit()

    # Re-query with a fresh session, mirroring how a real dispatch tick always
    # opens a new `KanbanSessionLocal()` (see `run_dispatch_tick`). Reusing `s`
    # above would serve `missed_card.deliverables` from the identity map's
    # already-loaded (pre-link) collection instead of the just-committed row,
    # since `expire_on_commit=False` never invalidates already-loaded
    # relationships without an explicit `expire`/`refresh`.
    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        missed_card = next(c for c in cards if c.id == raced)
        # No longer gated — it IS eligible now (plan_ref present).
        assert dispatch._awaiting_plan_ref(missed_card) is False
        # ...and resolving its plan yields a DANGLING_PARENT status
        # (the parent_id in the ref was "deleted-parent" which never existed).
        plan_status, plan_md, plan_id, parent_id = await dispatch._resolve_plan_for_child(
            s, missed_card,
        )
    assert plan_status == dispatch.PLAN_DANGLING_PARENT
    assert plan_md is None
    assert plan_id == "gone"
    assert parent_id == "deleted-parent"
    section = dispatch._plan_context_section(
        status=plan_status,
        plan_markdown=plan_md,
        plan_deliverable_id=plan_id,
        parent_card_id=parent_id,
        # The child in this test was created without a description; the
        # softened-guidance path requires a non-empty description.
        card_description="",
    )
    assert "Plan niet beschikbaar" in section
    assert "deleted-parent" in section


# ---- card 4a03565d: status-aware plan resolution + softened guidance -------

@pytest.mark.asyncio
async def test_resolve_plan_for_child_returns_dangling_parent_status():
    """A child with plan_ref whose parent_card_id points at a non-existent
    card must return PLAN_DANGLING_PARENT (not the generic (None,None,None))."""
    async with KanbanSessionLocal() as s:
        # Note: no parent card created — parent_id "ghost-parent" is dangling.
        child = await _make_child(s, parent_card_id="ghost-parent", title="child")
        await _link_plan_ref(
            s, child_id=child, parent_id="ghost-parent",
            plan_deliverable_id="plan-xyz",
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        child_card = next(c for c in cards if c.id == child)
        status, plan_md, plan_id, parent_id = await dispatch._resolve_plan_for_child(
            s, child_card,
        )
    assert status == dispatch.PLAN_DANGLING_PARENT
    assert plan_md is None
    assert plan_id == "plan-xyz"
    assert parent_id == "ghost-parent"


@pytest.mark.asyncio
async def test_resolve_plan_for_child_returns_plan_missing_on_parent_status():
    """A child with plan_ref whose parent exists but lacks the referenced
    plan deliverable must return PLAN_MISSING_ON_PARENT (not a generic
    failure that gets mistaken for 'parent deleted')."""
    async with KanbanSessionLocal() as s:
        # Create a real parent but DO NOT add a plan deliverable to it.
        parent = await _make_card(s, title="parent", column="Done")
        child = await _make_child(s, parent_card_id=parent, title="child")
        await _link_plan_ref(
            s, child_id=child, parent_id=parent,
            plan_deliverable_id="plan-id-not-on-parent",
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        child_card = next(c for c in cards if c.id == child)
        status, plan_md, plan_id, parent_id = await dispatch._resolve_plan_for_child(
            s, child_card,
        )
    assert status == dispatch.PLAN_MISSING_ON_PARENT
    assert plan_md is None
    assert plan_id == "plan-id-not-on-parent"
    assert parent_id == parent


@pytest.mark.asyncio
async def test_resolve_plan_for_child_returns_no_plan_ref_status():
    """A child card with no plan_ref deliverable at all must return
    PLAN_NO_REF. Mirrors the race-window case where the analyst hasn't
    attached the plan yet — but here we exercise the leaf helper directly
    because _awaiting_plan_ref already gates dispatch on plan_ref presence."""
    async with KanbanSessionLocal() as s:
        # Create parent + child but skip _link_plan_ref entirely.
        parent = await _make_card(s, title="parent", column="Done")
        child = await _make_child(s, parent_card_id=parent, title="child")
        await s.commit()
    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        child_card = next(c for c in cards if c.id == child)
        status, plan_md, plan_id, parent_id = await dispatch._resolve_plan_for_child(
            s, child_card,
        )
    assert status == dispatch.PLAN_NO_REF
    assert plan_md is None
    assert plan_id is None
    assert parent_id is None


@pytest.mark.asyncio
async def test_resolve_plan_for_child_returns_malformed_status_for_bad_json():
    """A child whose plan_ref ref is not parseable JSON must surface
    PLAN_MALFORMED instead of being silently swallowed as (None,None,None)."""
    async with KanbanSessionLocal() as s:
        parent = await _make_card(s, title="parent", column="Done")
        child = await _make_child(s, parent_card_id=parent, title="child")
        await apply_operation(
            s, op_type="link_plan_ref", entity_type="deliverable",
            project_key=PK, entity_id=child,
            payload={"ref_json": "not-json-{", "depends_on": []},
        )
        await s.commit()
    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
        child_card = next(c for c in cards if c.id == child)
        status, plan_md, plan_id, parent_id = await dispatch._resolve_plan_for_child(
            s, child_card,
        )
    assert status == dispatch.PLAN_MALFORMED
    assert plan_md is None
    assert plan_id is None
    assert parent_id is None


def test_plan_context_section_dangling_parent_distinguishes_from_no_ref():
    """The PLAN_DANGLING_PARENT message must mention the specific parent
    id, not collapse into 'mogelijk is de parent verwijderd of is het plan
    nooit opgeslagen' — that's the bug card 4a03565d reported."""
    section = dispatch._plan_context_section(
        status=dispatch.PLAN_DANGLING_PARENT,
        plan_markdown=None,
        plan_deliverable_id="plan-cb29",
        parent_card_id="parent-d4d7",
        card_description="",
    )
    assert "parent-d4d7" in section
    assert "plan-cb29" in section
    # Old message bundled two cases into one; the new message must be
    # specific about WHICH case it is.
    assert "bestaat niet meer" in section or "nooit aangemaakt" in section
    assert "nooit opgeslagen" not in section, (
        "old fallback phrasing must not leak into the new message — "
        "this is the exact symptom from card 4a03565d"
    )


def test_plan_context_section_missing_on_parent_message_is_specific():
    section = dispatch._plan_context_section(
        status=dispatch.PLAN_MISSING_ON_PARENT,
        plan_markdown=None,
        plan_deliverable_id="plan-missing",
        parent_card_id="parent-alive",
        card_description="",
    )
    assert "parent-alive" in section
    assert "plan-missing" in section
    assert "niet (meer) op te vinden" in section


def test_plan_context_section_malformed_message_is_specific():
    section = dispatch._plan_context_section(
        status=dispatch.PLAN_MALFORMED,
        plan_markdown=None,
        plan_deliverable_id=None,
        parent_card_id=None,
        card_description="",
    )
    assert "misvormd" in section
    assert "parseerbare JSON" in section


def test_plan_context_section_self_sufficient_card_does_not_force_impediment():
    """A card with a non-empty description is self-sufficient: the
    placeholder must guide the executor to proceed using the description
    and post a `**Self-improve:**` note, NOT unconditionally steer to
    report_impediment. This is the softening requirement from the
    acceptance criteria."""
    section = dispatch._plan_context_section(
        status=dispatch.PLAN_DANGLING_PARENT,
        plan_markdown=None,
        plan_deliverable_id="plan-1",
        parent_card_id="parent-1",
        card_description=(
            "Wire ACP-isomorf structured events into agentic_cli. "
            "Source: docs/cockpit/acp-transport-decision.md §6."
        ),
    )
    # Self-sufficient path: steer via Self-improve comment, not impediment.
    assert "Self-improve" in section
    assert "kaartbeschrijving" in section
    # The `report_impediment` reference must still appear as a *fallback*,
    # not as the primary guidance — the executor should not see it as
    # the first action to take. We check it appears only after "ALLEEN".
    alleen_idx = section.find("ALLEEN")
    imp_idx = section.find("report_impediment")
    if imp_idx != -1:
        assert alleen_idx != -1 and imp_idx > alleen_idx, (
            "report_impediment must only appear as a fallback after ALLEEN, "
            "not as the primary instruction"
        )


def test_plan_context_section_empty_description_steers_to_impediment():
    """A card with no usable description has no source of truth besides
    the plan — guidance must steer to report_impediment immediately."""
    section = dispatch._plan_context_section(
        status=dispatch.PLAN_DANGLING_PARENT,
        plan_markdown=None,
        plan_deliverable_id="plan-1",
        parent_card_id="parent-1",
        card_description="",
    )
    assert "report_impediment" in section
    assert "Self-improve" not in section


def test_plan_context_section_whitespace_only_description_steers_to_impediment():
    """A whitespace-only description is treated as empty (we strip() in
    the helper) — guidance must steer to report_impediment."""
    section = dispatch._plan_context_section(
        status=dispatch.PLAN_DANGLING_PARENT,
        plan_markdown=None,
        plan_deliverable_id="plan-1",
        parent_card_id="parent-1",
        card_description="   \n\t  ",
    )
    assert "report_impediment" in section
    assert "Self-improve" not in section


# ---- per-provider pause for limit hits (kanban-limit feature) --------------

@pytest.mark.asyncio
async def test_provider_for_card_uses_per_column_override_when_present():
    """When the card carries a column_overrides[<agent>].provider, that wins --
    the per-provider pause must target the SAME subscription a fresh respawn
    would (otherwise the pause would mismatch the subscription that hit its
    429)."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"column_overrides": {
                "engineer": {"provider": "bedrock"}}},
        )
        await s.commit()
        card = await get_card(s, cid)

        resolved = await dispatch._provider_for_card(s, PK, card, "engineer")

    assert resolved == "bedrock"


@pytest.mark.asyncio
async def test_provider_for_card_falls_through_to_column_default():
    """No per-column override -> column default_provider wins."""
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax",
        )
        cid = await _make_card(s)
        await s.commit()
        card = await get_card(s, cid)

        resolved = await dispatch._provider_for_card(s, PK, card, "engineer")

    assert resolved == "minimax"


@pytest.mark.asyncio
async def test_provider_for_card_falls_back_to_anthropic_when_nothing_configured():
    """No override, no column default -> the dispatcher's hard-coded
    PROVIDER_ANTHROPIC fallback (mirrors dispatch_card). A pause resolved here
    still targets anthropic specifically (the only subscription the fresh
    respawn would pick), not a global one."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        card = await get_card(s, cid)

        resolved = await dispatch._provider_for_card(s, PK, card, "engineer")

    assert resolved == "anthropic"


@pytest.mark.asyncio
async def test_provider_for_card_returns_none_when_inputs_insufficient():
    """If the caller hands in no card or no agent column, the helper refuses to
    guess a provider -- returning None so the caller can take the global-pause
    fallback rather than silently targeting anthropic."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        card = await get_card(s, cid)

    # No card -> None.
    async with KanbanSessionLocal() as s:
        assert await dispatch._provider_for_card(s, PK, None, "engineer") is None
    # No agent column -> None (a stale call, e.g. column already moved).
    async with KanbanSessionLocal() as s:
        assert await dispatch._provider_for_card(s, PK, card, "") is None


@pytest.mark.asyncio
async def test_provider_for_cwd_returns_column_default_for_matching_session(monkeypatch):
    """Hook-event path: with cwd matching a worktree, _provider_for_cwd
    resolves (project, session, card) and returns the card's column default
    provider. Mirrors move_limited_session_to_resume's lookup so both paths
    agree on what counts as a 'matching' card."""
    import unittest.mock as mock

    import app.kanban.db as kdb

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax",
        )
        cid = await _make_card(s, title="limax-card", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-prov-0001"},
        )
        await s.commit()

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ):
        resolved = await dispatch._provider_for_cwd(
            "/p/.claude/worktrees/k-prov-0001",
        )

    assert resolved == "minimax"


@pytest.mark.asyncio
async def test_provider_for_cwd_returns_none_for_non_worktree_cwd(monkeypatch):
    """A cwd that isn't <project>/.claude/worktrees/<name> isn't ours to touch --
    same precondition move_limited_session_to_resume enforces, so the
    fallback path stays consistent."""
    import unittest.mock as mock

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ):
        resolved = await dispatch._provider_for_cwd("/home/me/some-project")

    assert resolved is None


@pytest.mark.asyncio
async def test_provider_for_cwd_returns_none_when_no_card_claims_session(monkeypatch):
    """No card claimed by that session -> None, the same condition under
    which move_limited_session_to_resume no-ops. The hook keeps the legacy
    global-pause behaviour in that case."""
    import unittest.mock as mock

    import app.kanban.db as kdb

    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    with mock.patch(
        "app.kanban.dispatch.safe_resolve_project_key", return_value=PK
    ):
        resolved = await dispatch._provider_for_cwd(
            "/p/.claude/worktrees/k-prov-nonexistent",
        )

    assert resolved is None


@pytest.mark.asyncio
async def test_cleanup_stuck_session_pauses_only_affected_provider(monkeypatch):
    """The reaper path must mirror the hook: a stuck session running on a
    minimax column pauses only minimax. anthropic / bedrock stay clear so
    other traffic flows."""
    import unittest.mock as mock
    from datetime import UTC, datetime, timedelta

    from app.kanban import dispatch_pause
    from app.services.scheduling.auto_resume import FALLBACK_PAUSE_HOURS

    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax",
        )
        cid = await _make_card(s, title="stuck-rl", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-stuck-0001"},
        )
        await s.commit()
        card = await get_card(s, cid)

    # Keep tmux out of the picture: _kill_agent_session would otherwise hit the
    # host's tmux server (no such session here, returns None, harmless) but we
    # want a tight deterministic test.
    with mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        async with KanbanSessionLocal() as s:
            await dispatch._cleanup_stuck_session(
                s, card=card, project_key=PK,
                session_name="k-stuck-0001", pane_content="rate limited",
            )
            await s.commit()

    # Card's per-provider slot is set ...
    async with KanbanSessionLocal() as s:
        paused_minimax = await dispatch_pause.get_paused_until(
            s, provider="minimax"
        )
        paused_minimax_active = await dispatch_pause.is_dispatch_paused(
            s, provider="minimax"
        )
        # ... legacy global slot is NOT touched ...
        paused_global = await dispatch_pause.get_paused_until(s)
        paused_global_active = await dispatch_pause.is_dispatch_paused(s)
        # ... and sibling providers stay clear.
        paused_anthropic = await dispatch_pause.get_paused_until(
            s, provider="anthropic"
        )
        paused_bedrock = await dispatch_pause.get_paused_until(
            s, provider="bedrock"
        )

    expected_deadline = datetime.now(UTC) + timedelta(hours=FALLBACK_PAUSE_HOURS)
    assert paused_minimax is not None
    assert paused_minimax_active is True
    assert abs((paused_minimax - expected_deadline).total_seconds()) < 30
    assert paused_global is None
    assert paused_global_active is False
    assert paused_anthropic is None
    assert paused_bedrock is None


@pytest.mark.asyncio
async def test_cleanup_stuck_session_pauses_provider_from_column_override(monkeypatch):
    """When the card carries a per-column provider override, the pause targets
    THAT provider (bedrock here), not the column default -- a stale override
    on a stale card would otherwise pause the wrong subscription."""
    import unittest.mock as mock

    from app.kanban import dispatch_pause

    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_provider="minimax",
        )
        cid = await _make_card(s, title="stuck-override", column="engineer")
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"column_overrides": {
                "engineer": {"provider": "bedrock"}}},
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-stuck-0002"},
        )
        await s.commit()
        card = await get_card(s, cid)

    with mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        async with KanbanSessionLocal() as s:
            await dispatch._cleanup_stuck_session(
                s, card=card, project_key=PK,
                session_name="k-stuck-0002", pane_content="rate limited",
            )
            await s.commit()

    async with KanbanSessionLocal() as s:
        assert await dispatch_pause.is_dispatch_paused(s, provider="bedrock") is True
        assert await dispatch_pause.is_dispatch_paused(s, provider="minimax") is False
        assert await dispatch_pause.is_dispatch_paused(s) is False

