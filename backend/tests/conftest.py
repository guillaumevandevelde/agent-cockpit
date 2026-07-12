"""Shared test fixtures for kanban tests.

Patches KanbanSessionLocal in all modules so tests never touch the production DB.
"""
import os

import pytest
import pytest_asyncio

import app.kanban.db as _kanban_db
# Eagerly import the kanban models so every table registered on
# ``KanbanBase.metadata`` is materialized by the test-DB reset fixture
# below. Without this, tests that only import e.g. ``app.services.x`` and
# never touch ``app.kanban.models`` directly would see a test DB missing
# any model added after the conftest itself was last imported (e.g. the
# ``kanban_plans`` table from kanban card 727470a8).
import app.kanban.models  # noqa: F401
from tests.kanban_test_db import TestSessionLocal, test_engine

_test_sf = TestSessionLocal()


@pytest.fixture(autouse=True)
def _isolate_git_env():
    """Never let a test's git subprocess escape its tmp_path onto the real repo.

    Tests like test_agent_bridge_git_status do `git init/add/commit` in a
    `tmp_path` with cwd set. But when the suite runs under a git hook (pre-push)
    or any git context, git exports GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE into
    the environment, and `git` honours those over cwd. An "isolated" fixture then
    commits onto the REAL repo's HEAD — that is exactly how a fixture once
    committed `init` / `a.txt` onto master and wiped the whole tree. Strip every
    GIT_* redirection var for the duration of each test so cwd is authoritative.
    """
    saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("GIT_")}
    try:
        yield
    finally:
        os.environ.update(saved)


@pytest_asyncio.fixture(autouse=True)
async def _reset_test_db():
    """Drop and recreate all tables before each test."""
    from tests.kanban_test_db import reset_test_tables
    await reset_test_tables()
    yield


@pytest_asyncio.fixture(autouse=True, scope="session")
async def _cleanup_test_projects():
    """Safety net: purge leftover test rows from the real app DB.

    Some tests (e.g. test_mcp_server.py::test_mcp_tool_list_projects) must
    exercise the actual `projects` table in claude_registry.db rather than
    an isolated test DB, because they go through the MCP tool layer. By
    convention those tests name their row "mcp-test-*" / path it under
    "/tmp/test-*". Individual tests clean up after themselves, but a crash
    or an interrupted run can still leak rows — this fixture sweeps any
    stragglers once the whole session finishes so claude_registry.db can't
    accumulate junk projects across repeated test runs.
    """
    yield

    from sqlalchemy import delete, or_

    from app.database import AsyncSessionLocal, Base, engine
    from app.models.database import Project

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(Project).where(
                or_(Project.name.like("mcp-test-%"), Project.path.like("/tmp/test-%"))
            )
        )
        await db.commit()


@pytest_asyncio.fixture(autouse=True, scope="session")
def _patch_kanban_db():
    """Replace KanbanSessionLocal in all modules that import it."""
    originals = {}

    originals[(_kanban_db, "kanban_engine")] = _kanban_db.kanban_engine
    originals[(_kanban_db, "KanbanSessionLocal")] = _kanban_db.KanbanSessionLocal
    _kanban_db.kanban_engine = test_engine
    _kanban_db.KanbanSessionLocal = _test_sf

    import app.api.v1.kanban.router as _router
    originals[(_router, "KanbanSessionLocal")] = _router.KanbanSessionLocal
    _router.KanbanSessionLocal = _test_sf

    # /api/v1/plans migrated to the kanban DB in kanban card 727470a8 — it
    # imports KanbanSessionLocal the same way the kanban router does, so it
    # needs the same test-DB swap or its endpoints hit the production engine.
    import app.api.v1.plans as _plans_router
    originals[(_plans_router, "KanbanSessionLocal")] = _plans_router.KanbanSessionLocal
    _plans_router.KanbanSessionLocal = _test_sf

    import app.kanban.mcp_server as _mcp
    originals[(_mcp, "KanbanSessionLocal")] = _mcp.KanbanSessionLocal
    _mcp.KanbanSessionLocal = _test_sf

    yield

    for (mod, attr), val in originals.items():
        setattr(mod, attr, val)
