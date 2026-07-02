"""Shared test fixtures for kanban tests.

Patches KanbanSessionLocal in all modules so tests never touch the production DB.
"""
import os

import pytest
import pytest_asyncio

import app.kanban.db as _kanban_db
from tests.kanban_test_db import test_engine, TestSessionLocal

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

    import app.kanban.mcp_server as _mcp
    originals[(_mcp, "KanbanSessionLocal")] = _mcp.KanbanSessionLocal
    _mcp.KanbanSessionLocal = _test_sf

    yield

    for (mod, attr), val in originals.items():
        setattr(mod, attr, val)
