"""Tests for the per-project ``ceremony_profile`` field.

Covers the Pydantic enum boundary (``code`` | ``knowledge``), the ORM
default on the ``projects`` table, the ``PATCH /api/v1/projects/{id}``
round-trip, the dispatch prompt branch
(``build_card_prompt`` swaps the session-end recipe), and the
``_load_ceremony_profile`` failure-mode contract (no project path, project
not in registry, DB error). Decision reference:
``cockpit-richting-decision.md`` §4.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1 import projects as projects_api
from app.database import Base
from app.kanban.dispatch import (
    _build_knowledge_ship_instructions,
    _build_ship_instructions,
    build_card_prompt,
)
from app.main import app
from app.models.schemas import (
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
)
from app.services.project_service import ProjectService

# ---------------------------------------------------------------- schema layer


def test_project_create_defaults_ceremony_profile_to_code():
    p = ProjectCreate(name="demo", path="/tmp/demo")
    assert p.ceremony_profile == "code"


def test_project_create_accepts_valid_ceremony_profiles():
    for profile in ("code", "knowledge"):
        assert (
            ProjectCreate(name="d", path=f"/tmp/{profile}", ceremony_profile=profile)
            .ceremony_profile
            == profile
        )


def test_project_create_rejects_unknown_ceremony_profile():
    with pytest.raises(ValidationError):
        ProjectCreate(name="d", path="/tmp/d", ceremony_profile="bogus")


def test_project_update_accepts_ceremony_profile():
    u = ProjectUpdate(ceremony_profile="knowledge")
    assert u.ceremony_profile == "knowledge"


def test_project_update_rejects_unknown_ceremony_profile():
    with pytest.raises(ValidationError):
        ProjectUpdate(ceremony_profile="bogus")


def test_project_update_ceremony_profile_is_optional():
    # ``ProjectUpdate`` follows the same skip-when-None discipline as the
    # rest of its fields: leaving ``ceremony_profile`` absent means "don't
    # touch", so a PATCH with just ``{"kind": "meta"}`` keeps the existing
    # profile. Mirrors the existing test for ``kind``.
    u = ProjectUpdate(kind="meta")
    assert u.ceremony_profile is None


def test_project_response_carries_ceremony_profile():
    r = ProjectResponse(
        id=1,
        name="demo",
        path="/tmp/demo",
        kind="meta",
        priority=5,
        ceremony_profile="knowledge",
        is_active=False,
        last_accessed="2026-01-01T00:00:00",
        created_at="2026-01-01T00:00:00",
    )
    assert r.ceremony_profile == "knowledge"


# ---------------------------------------------------------------- db fixtures


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Isolated in-memory SQLite session so we never touch the real DB.

    Mirrors the fixture in ``test_project_kind.py`` so the two test files
    stay interchangeable for whoever extends the project layer next.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------- ORM default


@pytest.mark.asyncio
async def test_orm_default_ceremony_profile_is_code(db_session):
    """A row created via the service without an explicit profile keeps the
    schema default (``code``). Locks the existing-fleet behaviour: a
    migration that flipped the default would silently change the
    session-end recipe for every project that never opted in.
    """
    service = ProjectService(db_session)
    created = await service.add_project(ProjectCreate(name="demo", path="/tmp/demo"))
    assert created.ceremony_profile == "code"


@pytest.mark.asyncio
async def test_add_project_persists_knowledge_profile(db_session):
    service = ProjectService(db_session)
    created = await service.add_project(
        ProjectCreate(name="kb", path="/tmp/kb", ceremony_profile="knowledge")
    )
    assert created.ceremony_profile == "knowledge"


@pytest.mark.asyncio
async def test_update_project_switches_profile_without_touching_others(db_session):
    """A PATCH with only ``ceremony_profile`` leaves the other fields
    intact. Mirrors the existing ``test_update_project_changes_kind_and_priority``
    test for ``kind``/``priority`` — the partial-PATCH contract must hold
    for the new field too, otherwise every profile change is also a name
    change in the eyes of the audit log.
    """
    service = ProjectService(db_session)
    created = await service.add_project(ProjectCreate(name="demo", path="/tmp/demo"))

    updated = await service.update_project(
        created.id, ProjectUpdate(ceremony_profile="knowledge")
    )
    assert updated.ceremony_profile == "knowledge"
    assert updated.name == "demo"
    assert updated.kind == "product"


# ---------------------------------------------------------------- API route


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


class _FakeProjectService:
    pass


@pytest.mark.asyncio
async def test_patch_project_updates_ceremony_profile(monkeypatch):
    fake = _FakeProjectService()
    fake.update_project = AsyncMock(
        return_value=ProjectResponse(
            id=1,
            name="kb",
            path="/tmp/kb",
            kind="product",
            priority=None,
            ceremony_profile="knowledge",
            is_active=False,
            last_accessed="2026-01-01T00:00:00",
            created_at="2026-01-01T00:00:00",
        )
    )
    monkeypatch.setattr(projects_api, "ProjectService", lambda db: fake)

    async with _client() as ac:
        r = await ac.patch(
            "/api/v1/projects/1", json={"ceremony_profile": "knowledge"}
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ceremony_profile"] == "knowledge"


@pytest.mark.asyncio
async def test_patch_project_rejects_unknown_ceremony_profile(monkeypatch):
    fake = _FakeProjectService()
    fake.update_project = AsyncMock(return_value=None)
    monkeypatch.setattr(projects_api, "ProjectService", lambda db: fake)

    async with _client() as ac:
        r = await ac.patch(
            "/api/v1/projects/1", json={"ceremony_profile": "bogus"}
        )
    assert r.status_code == 422


# ---------------------------------------------------------------- dispatch branch


@pytest.fixture
def fake_card():
    """Minimal stand-in for a ``KanbanCard`` row — only the attributes
    ``build_card_prompt`` reads are populated. The dispatcher is type-
    ducktyped on the card, not isinstance-checked, so a MagicMock is
    enough for prompt-construction tests.
    """
    card = MagicMock()
    card.title = "Documenteer X"
    card.description = "Schrijf een korte notitie over X."
    card.id = "test-card-id"
    card.column = "engineer"
    card.parent_card_id = None
    card.analyst_agent_id = None
    card.executor_agent_id = None
    card.agent = None
    return card


def test_build_ship_instructions_code_branch_keeps_full_recipe():
    """Default ``code`` profile still produces the full engineer recipe —
    regression guard for the new branching code path.
    """
    full = _build_ship_instructions("direct")
    knowledge = _build_knowledge_ship_instructions("direct")

    # The full recipe names the FCR subagent and the frontend lint step.
    assert "Feature-Compliance-Review" in full
    assert "npm run lint" in full
    # The knowledge recipe skips both.
    assert "Feature-Compliance-Review" not in knowledge
    assert "npm run lint" not in knowledge


def test_build_card_prompt_routes_to_knowledge_recipe(fake_card):
    """``build_card_prompt`` must swap the session-end block based on
    ``ceremony_profile``. This is the single load-bearing call: the
    dispatcher's spawn prompt is built here, so a regression that drops
    the branch and always uses the code recipe would silently push
    knowledge work through the factory again — the exact failure the
    card was filed to fix.
    """
    code_prompt = build_card_prompt(
        fake_card,
        persona=None,
        ship_mode="direct",
        ceremony_profile="code",
    )
    knowledge_prompt = build_card_prompt(
        fake_card,
        persona=None,
        ship_mode="direct",
        ceremony_profile="knowledge",
    )
    # The recipe text is rendered inside `## Session-end workflow`.
    code_recipe = code_prompt.split("## Session-end workflow", 1)[1]
    knowledge_recipe = knowledge_prompt.split("## Session-end workflow", 1)[1]

    assert "Feature-Compliance-Review" in code_recipe
    assert "Feature-Compliance-Review" not in knowledge_recipe
    assert "npm run lint" in code_recipe
    assert "npm run lint" not in knowledge_recipe
    # The knowledge recipe still attaches a note-kind deliverable — the
    # owner of the knowledge repo is supposed to find their doc by reading
    # the card row, not by grepping git log.
    assert 'kind="note"' in knowledge_recipe


def test_build_card_prompt_knowledge_forces_direct_mode(fake_card):
    """When the dispatch header says ``pull-request`` but the project is
    a knowledge one, the rendered recipe must downgrade to direct merge
    and surface a one-line note explaining why. Knowledge projects have
    no PR lane by spec.
    """
    pr_prompt = build_card_prompt(
        fake_card,
        persona=None,
        ship_mode="pull-request",
        ceremony_profile="knowledge",
    )
    pr_recipe = pr_prompt.split("## Session-end workflow", 1)[1]
    assert "mode = direct" in pr_recipe
    # And the original PR mode is named in the downgrade note so the
    # researcher doesn't try to "fix" it.
    assert "pull-request" in pr_recipe


def test_build_card_prompt_keeps_analyst_and_reviewer_branches(fake_card):
    """The new knowledge branch must not eat the analyst/reviewer cases —
    those still take their dedicated builders regardless of profile. A
    regression here would silently route an analyst card through the
    knowledge ship recipe when its project happens to carry the
    knowledge flag (analyst projects are mostly research-shaped).
    """
    fake_card.agent = "reviewer"
    reviewer_prompt = build_card_prompt(
        fake_card,
        persona=None,
        ship_mode="direct",
        ceremony_profile="knowledge",
    )
    assert "independent reviewer" in reviewer_prompt


# ---------------------------------------------------------------- load helper


from app.kanban import dispatch as dispatch_module  # noqa: E402


@pytest.mark.asyncio
async def test_load_ceremony_profile_defaults_when_path_missing():
    """Empty ``project_path`` → ``code`` immediately, no DB call. The
    helper must short-circuit before opening a session so a misrouted
    spawn (no project path attached) doesn't get a registry round-trip.
    """
    assert await dispatch_module._load_ceremony_profile(None) == "code"
    assert await dispatch_module._load_ceremony_profile("") == "code"


@pytest.mark.asyncio
async def test_load_ceremony_profile_defaults_on_db_error():
    """DB failure → ``code``, not an exception. The helper sits on the
    spawn hot path; raising would propagate to the dispatcher and
    silently strand a card in Doing.
    """
    with patch(
        "app.database.AsyncSessionLocal",
        side_effect=RuntimeError("simulated registry outage"),
    ):
        assert (
            await dispatch_module._load_ceremony_profile("/some/path")
            == "code"
        )


@pytest.mark.asyncio
async def test_load_ceremony_profile_defaults_on_unknown_value():
    """A row carrying a value the schema doesn't recognise (manual PATCH,
    future profile the running code doesn't know yet) defaults to
    ``code`` — the conservative choice. The Pydantic Literal is the
    guard for new writes; this is the safety net for legacy rows.
    """
    fake_session = AsyncMock()
    fake_session.__aenter__.return_value = fake_session
    fake_session.__aexit__.return_value = False
    # ``scalar_one_or_none`` is sync in real SQLAlchemy — a MagicMock
    # with ``return_value`` covers it; ``AsyncMock`` would emit a
    # "coroutine was never awaited" warning and return the coroutine
    # object instead of the value, which the helper would then
    # ``not row`` short-circuit on.
    fake_session.execute.return_value.scalar_one_or_none = MagicMock(
        return_value="future-profile"
    )
    with patch(
        "app.database.AsyncSessionLocal", return_value=fake_session
    ):
        assert (
            await dispatch_module._load_ceremony_profile("/some/path")
            == "code"
        )


@pytest.mark.asyncio
async def test_load_ceremony_profile_returns_known_values():
    """Sanity check the happy path: a row carrying each recognised
    value is returned verbatim. Locks the contract that
    ``knowledge`` flows through unchanged so the dispatch branch
    reaches ``_build_knowledge_ship_instructions``.
    """
    for value in ("code", "knowledge"):
        fake_session = AsyncMock()
        fake_session.__aenter__.return_value = fake_session
        fake_session.__aexit__.return_value = False
        fake_session.execute.return_value.scalar_one_or_none = MagicMock(
            return_value=value
        )
        with patch(
            "app.database.AsyncSessionLocal", return_value=fake_session
        ):
            assert (
                await dispatch_module._load_ceremony_profile("/some/path")
                == value
            )


@pytest.mark.asyncio
async def test_load_ceremony_profile_defaults_on_empty_row():
    """A row carrying ``None`` (pre-migration row on a registry that
    hasn't run the revision yet) defaults to ``code``. Empty must
    behave the same as missing — the lookup cannot return ``None`` to
    the caller, because the dispatch branch only matches on the
    literal string ``"knowledge"``.
    """
    fake_session = AsyncMock()
    fake_session.__aenter__.return_value = fake_session
    fake_session.__aexit__.return_value = False
    fake_session.execute.return_value.scalar_one_or_none = MagicMock(
        return_value=None
    )
    with patch(
        "app.database.AsyncSessionLocal", return_value=fake_session
    ):
        assert (
            await dispatch_module._load_ceremony_profile("/some/path")
            == "code"
        )

# ---- persona-swap: ceremony_profile=knowledge routes engineer fallback
# ---- to researcher.md (cockpit-richting-decision.md §4).

class _FakeCard:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.fixture
def knowledge_project(tmp_path):
    """Project layout with researcher.md + engineer.md + analyst.md + reviewer.md
    present in .claude/agents. Used to verify the knowledge ceremony routes the
    engineer-default fallback to researcher while leaving analyst/reviewer
    routes alone (kaart 5fcfca7f… persona-swap blocker)."""
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    for name in ("engineer", "researcher", "analyst", "reviewer"):
        (agents / f"{name}.md").write_text(f"# {name} persona\n")
    return tmp_path


def test_phase_target_agent_knowledge_swaps_engineer_to_researcher(knowledge_project):
    """ceremony_profile='knowledge' overrides the engineer fallback to
    researcher when both personas exist in the project — the mismatch the
    feature was created to fix. Other personas (analyst/reviewer) keep
    their existing precedence: knowledge is *not* a global override."""
    card = _FakeCard()
    assert (
        dispatch_module._phase_target_agent(
            card, project_path=str(knowledge_project), phase="executor",
            source_column="Backlog", ceremony_profile="knowledge",
        )
        == "researcher"
    )


def test_phase_target_agent_knowledge_keeps_analyst_phase(knowledge_project):
    """Knowledge projects still get an analyst when the analyst phase fires —
    the swap only applies to the executor fallback lane."""
    card = _FakeCard()
    assert (
        dispatch_module._phase_target_agent(
            card, project_path=str(knowledge_project), phase="analyst",
            source_column="Backlog", ceremony_profile="knowledge",
        )
        == "analyst"
    )


def test_phase_target_agent_knowledge_preserves_explicit_reviewer(knowledge_project):
    """An explicit agent_override='reviewer' on a knowledge project still
    resolves to reviewer — the ceremony override only re-routes the engineer
    default, not arbitrary persona picks."""
    card = _FakeCard()
    assert (
        dispatch_module._phase_target_agent(
            card, project_path=str(knowledge_project), phase="executor",
            source_column="Backlog", agent_override="reviewer",
            ceremony_profile="knowledge",
        )
        == "reviewer"
    )


def test_phase_target_agent_knowledge_without_researcher_falls_back(knowledge_project):
    """If the project doesn't carry researcher.md, knowledge projects
    degrade to the legacy engineer routing rather than failing the spawn.
    The blueprint ships researcher.md for new projects, but hand-created
    projects may not have it."""
    (knowledge_project / ".claude" / "agents" / "researcher.md").unlink()
    card = _FakeCard()
    assert (
        dispatch_module._phase_target_agent(
            card, project_path=str(knowledge_project), phase="executor",
            source_column="Backlog", ceremony_profile="knowledge",
        )
        == "engineer"
    )


def test_phase_target_agent_code_profile_unchanged(knowledge_project):
    """ceremony_profile='code' (default) keeps the existing engineer
    fallback — the persona swap is opt-in via the project setting."""
    card = _FakeCard()
    assert (
        dispatch_module._phase_target_agent(
            card, project_path=str(knowledge_project), phase="executor",
            source_column="Backlog", ceremony_profile="code",
        )
        == "engineer"
    )
