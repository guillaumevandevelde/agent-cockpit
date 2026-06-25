"""Shared test fixtures for kanban tests.

Patches KanbanSessionLocal in all modules so tests never touch the production DB.
"""
import pytest_asyncio

import app.kanban.db as _kanban_db
from tests.kanban_test_db import test_engine, TestSessionLocal

_test_sf = TestSessionLocal()


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
