"""Tests proving that ``app.database`` is isolated from the production DB
under the shared conftest mechanism.

Mirrors the kanban test-isolation guarantee (see ``tests/kanban_test_db.py``
+ ``tests/conftest.py::_patch_kanban_db``): when the conftest swaps the
canonical ``app.database.engine`` / ``AsyncSessionLocal`` to a temp-file
engine, every other module that did ``from app.database import engine`` (or
``AsyncSessionLocal``) at import time must see the *test* reference too —
otherwise an indirect consumer (e.g. ``app.services.sandcastle_service``
opening ``async with AsyncSessionLocal()`` on its own, or any future
``app.mcp_server.tools.*`` / ``app.services.*`` / ``app.api.v1.*`` module
that reaches for the device-local DB) still talks to the real
``claude_registry.db``.
"""
import pytest
import sqlalchemy

from app.database import Base
from tests.app_database_test_db import test_engine


@pytest.mark.asyncio
async def test_app_database_engine_is_test_engine_when_conftest_active():
    """The canonical ``app.database.engine`` is the test engine under the conftest."""
    from app import database as app_database_module

    assert app_database_module.engine is test_engine, (
        "app.database.engine should be swapped to the test engine by the conftest; "
        "if this fails, the identity-swap fixture is not running or not swapping the canonical module."
    )


@pytest.mark.asyncio
async def test_app_database_async_session_local_is_test_factory_when_conftest_active():
    """The canonical ``app.database.AsyncSessionLocal`` opens sessions on the test engine.

    ``TestSessionLocal`` is a callable class — the conftest binds a single
    instance into ``app.database.AsyncSessionLocal`` for the whole session,
    so identity comparison against the bound instance is the right check.
    Probing the ``bind`` object inside the returned session confirms the
    swap is real, not just a duck-typed coincidence.
    """
    from app import database as app_database_module
    from tests.app_database_test_db import TestSessionLocal as _AppDbSf

    # Conftest exposes the bound instance via reimport of the same class.
    # Identity-based check would only work against the single bound instance
    # the conftest created. Verify via the *engine the session binds to*
    # instead — that proves the swap took effect:
    async with app_database_module.AsyncSessionLocal() as s:
        bind_engine = s.bind
        assert bind_engine is test_engine, (
            "app.database.AsyncSessionLocal should open sessions bound to the test engine, "
            f"got a session whose bind is {bind_engine!r}"
        )
    # Also verify the class identity matches what the conftest bound:
    assert isinstance(app_database_module.AsyncSessionLocal, _AppDbSf)


@pytest.mark.asyncio
async def test_app_database_swap_reaches_indirect_consumer():
    """Modules that did ``from app.database import AsyncSessionLocal`` at import
    time see the swapped reference too — same identity-swap guarantee that
    ``_patch_kanban_db`` provides for kanban.

    Regression test for the structural fix that turned the kanban fixture from
    a hard-coded allow-list into a ``sys.modules`` walk: a new indirect
    consumer (e.g. ``app.services.sandcastle_service`` opening
    ``async with AsyncSessionLocal()`` on its own) is picked up
    automatically without anyone updating an allow-list.

    Uses ``sandcastle_service`` because that module imports cleanly even
    when other parts of the codebase have unrelated syntax issues, which
    keeps the regression decoupled from incidental import pathologies.
    """
    from app import database as app_database_module
    from app.services import sandcastle_service as sandcastle_module

    # Whatever the canonical module has right now is what every consumer should see:
    assert sandcastle_module.AsyncSessionLocal is app_database_module.AsyncSessionLocal, (
        "app.services.sandcastle_service.AsyncSessionLocal should be the same object "
        "as app.database.AsyncSessionLocal (identity-swap guarantee)."
    )
    # And that object opens sessions on the test engine:
    async with sandcastle_module.AsyncSessionLocal() as s:
        assert s.bind is test_engine, (
            "indirect consumer's AsyncSessionLocal should produce sessions on the test engine"
        )


@pytest.mark.asyncio
async def test_app_database_reset_clears_rows_between_tests():
    """The per-test reset fixture drops + recreates every ``Base.metadata``
    table, so rows written by a previous test are gone in the next test.

    Kanban uses ``KanbanBase.metadata``; app.database uses the wider
    ``Base.metadata`` (the device-local ``claude_registry.db`` Base), which
    includes agent_mail / projects / mcp_tokens / sandcastle / scheduled /
    security_audit / etc. The drop_all+create_all pass exercises every one.
    """
    from app.database import AsyncSessionLocal
    from app.models.database import Project

    # Write a row in this test
    async with AsyncSessionLocal() as s:
        s.add(Project(name="reset-test-probe", path="/tmp/reset-test-probe", is_active=False))
        await s.commit()

    async with AsyncSessionLocal() as s:
        result = await s.execute(sqlalchemy.select(Project).where(Project.name == "reset-test-probe"))
        row = result.scalar_one_or_none()
        assert row is not None, "row written in this test must be visible inside the test"

    # The next test will run reset_test_tables; if reset is broken the row
    # persists and pollutes prod-shaped state. We can't observe the next test
    # directly, but we can prove the fixture actually drops by triggering it
    # now via the same code path the fixture uses.
    from tests.app_database_test_db import reset_test_tables

    await reset_test_tables()

    async with AsyncSessionLocal() as s:
        result = await s.execute(sqlalchemy.select(Project).where(Project.name == "reset-test-probe"))
        row = result.scalar_one_or_none()
        assert row is None, "reset_test_tables must drop the row written above"


@pytest.mark.asyncio
async def test_app_database_tables_cover_all_models_after_eager_import():
    """After the conftest's eager ``import app.models``, ``Base.metadata`` has a
    table for every model in every ``app/models/*.py`` file.

    The agent_mail tests previously had to call ``create_all`` themselves
    because ``Base.metadata`` was empty until something imported
    ``app.models.agent_mail``. With the shared mechanism, the conftest does
    that import once and every test inherits a fully-populated metadata.
    """
    tables = set(Base.metadata.tables.keys())

    expected_core_tables = {
        "projects", "backups", "mcp_access_tokens", "scheduled_messages",
        "mail_team_members", "mail_messages", "mail_agent_sessions",
    }
    missing = expected_core_tables - tables
    assert not missing, f"Base.metadata is missing core tables after eager import: {missing}"
