"""Shared test fixtures for kanban + app.database tests.

Patches ``KanbanSessionLocal`` / ``kanban_engine`` and ``AsyncSessionLocal`` /
``engine`` in every module so tests never touch the production DB.
"""
import os
import sys

import pytest
import pytest_asyncio

import app.database as _app_database
import app.kanban.db as _kanban_db

# Eagerly import the kanban models so every table registered on
# ``KanbanBase.metadata`` is materialized by the test-DB reset fixture
# below. Without this, tests that only import e.g. ``app.services.x`` and
# never touch ``app.kanban.models`` directly would see a test DB missing
# any table added to ``app/kanban/models.py`` after the conftest itself
# was last imported.
import app.kanban.models  # noqa: F401

# Same rationale for ``app.models``: the device-local ``claude_registry.db``
# tables (project / mcp_token / sandcastle / scheduled / agent_mail /
# security_audit / ...) only land on ``Base.metadata`` once each module in
# ``app/models/*.py`` has been imported. Without this, a test that only
# pulls in ``app.services.x`` would see a test DB missing any table
# registered by a model file the test didn't import transitively. The
# conftest's eager import guarantees every model is materialised before
# the reset fixture runs.
import app.models  # noqa: F401

# ``app.models.database`` is intentionally *not* in ``app.models/__init__.py``
# (it predates the eager-import convention and is treated as the core table
# set: Project, Backup, AutoBackupSettings, Marketplace, ...). Import it
# explicitly here so the per-test ``drop_all``/``create_all`` pass sees
# every core table. Without this, a test that only pulls in e.g.
# ``app.services.agent_mail_service`` would see ``projects``/``backups``
# missing from the test DB and fail with ``no such table: projects``.
import app.models.database  # noqa: F401
from tests.app_database_test_db import TestSessionLocal as _AppDbSessionLocal
from tests.app_database_test_db import test_engine as _app_db_test_engine
from tests.kanban_test_db import TestSessionLocal, test_engine

_test_sf = TestSessionLocal()
_app_db_test_sf = _AppDbSessionLocal()


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
    """Drop and recreate all kanban tables before each test."""
    from tests.kanban_test_db import reset_test_tables
    await reset_test_tables()
    yield


@pytest_asyncio.fixture(autouse=True)
async def _reset_app_database_tables():
    """Drop and recreate all ``app.database.Base`` tables before each test.

    Mirrors ``_reset_test_db`` for the wider ``claude_registry.db`` schema:
    every test starts with a fresh set of project / mcp_token / sandcastle /
    scheduled / agent_mail / security_audit / ... rows so prior tests can't
    leak into the current one. The drop_all + create_all pass is fast enough
    that even tests that don't touch the DB pay only milliseconds.
    """
    from tests.app_database_test_db import reset_test_tables
    await reset_test_tables()
    yield


@pytest.fixture(autouse=True)
def _reset_singleton_state():
    """Clear per-id in-memory state on every module-level singleton.

    Sibling to ``_reset_app_database_tables``: the DB reset makes
    auto-increment ids restart at 1 every test, which collides with
    singletons that key per-id state on the instance (e.g.
    ``external_agent_mail_service._send_windows`` keyed by ``actor.id``,
    ``agent_mail_service._last_auto_nudge_at`` keyed by ``member_id``).

    Self-improve kanban card 42f44a05: keeps the list in one place
    (``app.services._testing.reset_all_singleton_test_state``) so the next
    service that gains per-id state can be wired in alongside instead of
    needing a per-file autouse fixture.
    """
    from app.services._testing import reset_all_singleton_test_state
    reset_all_singleton_test_state()
    yield
    reset_all_singleton_test_state()


@pytest.fixture(autouse=True)
def _isolate_usage_service_projects_dir(tmp_path, monkeypatch):
    """Pin ``UsageService.projects_dir`` to a per-test empty dir.

    Without this, ``UsageService(db=...)`` reads ``get_claude_projects_dir``
    from ``app.utils.path_utils`` at ``__init__`` time, which on this host
    is the real ``~/.claude/projects/**`` tree — 956 JSONL files / 523 MB
    as of kaart 103718db. A test that forgets to mock
    ``get_all_usage_entries`` / ``get_block_usage`` (the common pattern in
    ``test_subscription_usage_provider.py::TestAnthropicUsageProviderModelAttribution``
    and ``test_subscriptions_endpoint.py``, both of which mock at the
    method level) blocks the asyncio event loop on ``Path.iterdir()``
    inside the ``async`` coroutine for several minutes — long enough to
    hang ``scripts/run-single-test.sh`` past its 10s safety net even with
    ``--timeout-method=thread``, and to drop the dispatch's interactive
    prompt into an SSH idle-disconnect on the shared box.

    We patch on the consumer side (``app.services.usage_service``) per
    ``docs/cockpit/test-doubles-convention.md`` rule 1 — patching the
    source module (``app.utils.path_utils``) is a silent no-op because
    ``from app.utils.path_utils import get_claude_projects_dir`` binds
    the original into ``usage_service``'s namespace at import time.

    Tests that need a *real* ``projects_dir`` (with synthetic JSONL files
    under ``tmp_path``) override this with their own ``monkeypatch.setattr``
    on the same consumer attribute — ``monkeypatch`` is
    function-scoped, so the test's override is applied after this
    autouse fixture's setup and wins.
    """
    empty_projects_dir = tmp_path / "usage_service_projects"
    empty_projects_dir.mkdir()
    monkeypatch.setattr(
        "app.services.usage_service.get_claude_projects_dir",
        lambda: empty_projects_dir,
    )
    yield


@pytest_asyncio.fixture(autouse=True, scope="session")
async def _patch_app_database():
    """Swap every ``AsyncSessionLocal`` / ``engine`` reference to the test
    factory/engine for the whole session, regardless of which module
    imported them.

    Self-improve kanban card 02e80e79: generalises
    ``_patch_kanban_db``'s identity-swap technique to ``app.database``
    itself. Mirrors the kanban fixture structurally:

      * Capture the prod references from the canonical module BEFORE
        patching it, so we can detect them on other modules by identity.
      * Rebind the canonical module.
      * Walk ``sys.modules`` and rebind every module whose ``engine`` /
        ``AsyncSessionLocal`` attribute still points at the *same* object
        as the prod references.

    A new MCP tool / service / router that does
    ``from app.database import AsyncSessionLocal`` is picked up
    automatically — no allow-list to maintain. The ``mcp_server.tools.*``
    modules and any ``app.services.*`` importer are now isolated with no
    per-file ``monkeypatch.setattr`` needed.

    Test that exercises this guarantee end-to-end:
    ``tests/test_app_database_isolation.py::test_app_database_swap_reaches_indirect_consumer``.

    Once every ``app.database`` consumer goes through the test DB, the
    legacy ``_cleanup_test_projects`` safety net (which sweated
    mcp-test-* rows that escaped the prod DB on a crash) is no longer
    needed: any row a test writes lives in the temp-file engine, which
    is unlinked at process exit by ``atexit``. Kanban card 02e80e79
    removed that net in the same commit.
    """
    original_sf = _app_database.AsyncSessionLocal
    original_engine = _app_database.engine

    _app_database.engine = _app_db_test_engine
    _app_database.AsyncSessionLocal = _app_db_test_sf

    # Some modules import the helpers (``get_db``) or the ``Base`` class
    # alongside the engine/session factory. ``get_db`` is a closure over
    # the module-level ``AsyncSessionLocal`` and ``Base`` is a class (same
    # object either way), so neither needs swapping — only the two named
    # attributes do.
    rebound = []
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        try:
            current_sf = getattr(mod, "AsyncSessionLocal", None)
        except Exception:
            current_sf = None
        if current_sf is original_sf:
            mod.AsyncSessionLocal = _app_db_test_sf
            rebound.append((mod, "AsyncSessionLocal", current_sf))
        try:
            current_engine = getattr(mod, "engine", None)
        except Exception:
            current_engine = None
        if current_engine is original_engine:
            mod.engine = _app_db_test_engine
            rebound.append((mod, "engine", current_engine))

    yield

    for mod, attr, val in rebound:
        setattr(mod, attr, val)


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
