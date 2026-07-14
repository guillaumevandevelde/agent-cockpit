"""Shared test fixtures for kanban tests.

Patches KanbanSessionLocal in all modules so tests never touch the production DB.
"""
import os
import sys

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
    """Swap every ``KanbanSessionLocal`` / ``kanban_engine`` reference to the
    test factory/engine, regardless of which module imported it.

    Iterates ``sys.modules`` and rebinds every module whose attribute is the
    prod reference (by identity). New modules that do
    ``from app.kanban.db import KanbanSessionLocal`` at import time are picked
    up automatically — no allow-list to maintain when a 5th router/service
    starts talking to the kanban DB.

    Self-improve kanban card 07d95f2c: the previous version of this fixture
    hard-coded a list of known consumers (``app.api.v1.kanban.router``,
    ``app.api.v1.plans``, ``app.kanban.mcp_server``). Each new consumer was a
    silent prod-DB test until a failing test surfaced it. The fix is structural:
    a module-level ``from app.kanban.db import KanbanSessionLocal`` binds the
    prod factory into the importing module's ``__dict__`` at import time, so
    any module that has the prod reference still sitting in it is a swap
    candidate. Identity (``is``) comparison avoids touching unrelated
    attributes — only modules whose attribute points at the *same* object as
    ``app.kanban.db.KanbanSessionLocal`` (the prod factory) get rebound.
    """
    # Capture the prod references BEFORE patching the canonical module, so we
    # can detect them on other modules by identity.
    original_sf = _kanban_db.KanbanSessionLocal
    original_engine = _kanban_db.kanban_engine

    _kanban_db.kanban_engine = test_engine
    _kanban_db.KanbanSessionLocal = _test_sf

    rebound = []
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        try:
            current_sf = getattr(mod, "KanbanSessionLocal", None)
        except Exception:
            current_sf = None
        if current_sf is original_sf:
            mod.KanbanSessionLocal = _test_sf
            rebound.append((mod, "KanbanSessionLocal", current_sf))
        try:
            current_engine = getattr(mod, "kanban_engine", None)
        except Exception:
            current_engine = None
        if current_engine is original_engine:
            mod.kanban_engine = test_engine
            rebound.append((mod, "kanban_engine", current_engine))

    yield

    for mod, attr, val in rebound:
        setattr(mod, attr, val)
