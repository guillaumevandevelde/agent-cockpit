"""Tests for the kanban REST ``run_write_with_retry`` wrap (kind-4).

Pins the contract from `docs/cockpit/kanban-write-retry-vangnet-decision.md`
§4 kind-4:

* Every POST/PATCH/DELETE/PUT handler in
  ``backend/app/api/v1/kanban/router.py`` that opens a kanban session
  routes the work through ``run_write_with_retry`` — so an
  ``OperationalError("database is locked")`` from a concurrent dispatch
  tick is silently retried up to ``max_retries`` times before the route
  surfaces the failure.
* ``create_project_from_interview`` opens two sessions (kanban + app DB):
  only the kanban session is wrapped; the app DB session is opened
  **once** outside the wrapper, so a retry never re-enters the
  ``Project``-row insert.
* The post-commit ``_reload`` runs on a fresh session after every retry
  — no stale-collection poisoning from pre-mutation state held across
  the retry boundary.

The test set is intentionally small and exercise-focused: a structural
sweep over the source file (uses ``ast`` so it cannot be fooled by
helpful variable names) plus three behavioural tests that prove the
wrap actually swaps the session on retry.
"""

from __future__ import annotations

import ast
import sqlite3

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

from app.main import app
from tests.kanban_test_db import reset_test_tables

PK = "git:example.com/me/repo"


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


# --- structural sweep ---------------------------------------------------------


def _router_write_handler_names() -> list[str]:
    """Return every async handler name in router.py that is decorated with
    a write HTTP method (POST / PATCH / DELETE / PUT).
    """
    import pathlib

    path = pathlib.Path(__file__).resolve().parent.parent / "app" / "api" / "v1" / "kanban" / "router.py"
    src = path.read_text()
    tree = ast.parse(src)

    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.decorator_list:
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr in {"post", "patch", "delete", "put"} and isinstance(func.value, ast.Name) and func.value.id == "router":
                names.append(node.name)
                break
    return names


def _router_source() -> str:
    import pathlib

    path = pathlib.Path(__file__).resolve().parent.parent / "app" / "api" / "v1" / "kanban" / "router.py"
    return path.read_text()


def test_every_write_handler_calls_run_write_with_retry():
    """Every POST/PATCH/DELETE/PUT handler in router.py that touches the
    kanban DB must route its work through ``run_write_with_retry``.

    Acceptance criterion from kind-4: '45 routes gewikkeld'. We pin it
    via an AST sweep over the handler body — the helper must appear
    in every write handler that opens a kanban session, and the handler
    must not decide to 'only sometimes retry' (the wrap is uniform).

    A handler that does NOT open a KanbanSessionLocal (purely
    filesystem-backed, e.g. ``disable``) is skipped — the wrap is only
    load-bearing for paths that interact with the SQLite write lock.
    """
    src = _router_source()
    handler_names = _router_write_handler_names()
    assert handler_names, "router.py contains no write handlers — fixture is wrong"

    # Parse the per-handler bodies once.
    tree = ast.parse(src)
    bodies: dict[str, list[ast.AST]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bodies[node.name] = list(ast.walk(node))

    missing = []
    for name in handler_names:
        if name not in bodies:
            continue
        # Skip handlers that do not touch the kanban DB — the wrap is
        # only load-bearing for paths that race on the SQLite write
        # lock. A filesystem-only handler (e.g. `disable` rewriting the
        # project's .mcp.json) has no DB-session to retry.
        if not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "KanbanSessionLocal"
            for node in bodies[name]
        ):
            continue
        calls = [
            node for node in bodies[name]
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "run_write_with_retry"
        ]
        if not calls:
            missing.append(name)
    assert not missing, (
        f"write handlers without run_write_with_retry wrap: {missing}. "
        "See docs/cockpit/kanban-write-retry-vangnet-decision.md §4 kind-4."
    )


def test_create_project_from_interview_keeps_app_db_session_outside_wrapper():
    """The two-session inception route must keep the app DB session
    open OUTSIDE the run_write_with_retry factory — a retry would
    re-insert the Project row otherwise.

    Pin: the kanban session is opened inside the coro_factory, the
    app DB session is opened in the route's own `async with` block.
    """
    src = _router_source()
    tree = ast.parse(src)
    handler = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "create_project_from_interview"
    )
    handler_src = ast.unparse(handler)

    # The wrap must call run_write_with_retry — same shape as the other
    # 44 write handlers.
    assert "run_write_with_retry" in handler_src, (
        "create_project_from_interview must route the kanban half through run_write_with_retry"
    )

    # The app DB AsyncSessionLocal must appear OUTSIDE the coro_factory
    # lambda. We inspect the AST: the handler has exactly one
    # `async with AsyncSessionLocal() as app_db` block and that block
    # sits at the route's own level (not inside the kanban factory).
    async_withs = [
        node for node in ast.walk(handler)
        if isinstance(node, ast.AsyncWith)
    ]
    app_db_blocks = [
        node for node in async_withs
        if any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "AsyncSessionLocal"
            for item in node.items
        )
    ]
    assert len(app_db_blocks) == 1, (
        "create_project_from_interview must open AsyncSessionLocal exactly once"
    )

    kanban_blocks = [
        node for node in async_withs
        if any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "KanbanSessionLocal"
            for item in node.items
        )
    ]
    assert kanban_blocks, "create_project_from_interview must still open the kanban session"

    # The kanban `async with` block must be nested inside a function whose
    # body is a run-able coroutine — find the IMMEDIATE enclosing function
    # (the deepest one) and verify it's not the route itself. The walk
    # visits outer functions first, so iterate by line range and pick the
    # tightest fit.
    def _enclosing_func(start: ast.AsyncWith) -> ast.AST | None:
        # The innermost function is the one whose line range is the
        # smallest that still contains `start`.
        candidates = []
        for node in ast.walk(handler):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.lineno <= start.lineno and (
                node.end_lineno is None or node.end_lineno >= getattr(start, "end_lineno", start.lineno)
            ):
                candidates.append(node)
        if not candidates:
            return None
        # Tightest fit = smallest enclosing range.
        candidates.sort(key=lambda n: (
            (n.end_lineno or n.lineno) - n.lineno,
            n.lineno,
        ))
        return candidates[0]

    for kb in kanban_blocks:
        enclosing = _enclosing_func(kb)
        assert enclosing is not None, "kanban block must be inside some function"
        assert enclosing.name != "create_project_from_interview", (
            "kanban session must be opened inside a nested factory, not the route itself"
        )


# --- behavioural: lock-contention retry on a write route ----------------------


def _locked_error() -> OperationalError:
    return OperationalError(
        "INSERT INTO foo ...", {},
        sqlite3.OperationalError("database is locked"),
    )


@pytest.mark.asyncio
async def test_create_card_retries_on_lock_then_succeeds(monkeypatch):
    """A transient lock-OperationalError on the first attempt must be
    retried transparently; the route returns the freshly-created card.

    The wrapper is the production ``run_write_with_retry``; we patch
    ``KanbanSessionLocal`` to inject a single lock-raise into the
    first attempt's session, then verify the second attempt succeeds
    and the card is persisted.
    """
    from app.api.v1.kanban import router as router_module
    from app.kanban.db import KanbanSessionLocal

    real_sessionlocal = KanbanSessionLocal
    injected_once = {"done": False}

    def _flaky_sessionmaker(*args, **kwargs):
        # Wrap the real sessionmaker so ONE attempt sees a session whose
        # first execute() raises a lock-OperationalError.
        real_factory = real_sessionlocal

        class _Flaky:
            def __init__(self):
                self._real = real_factory()

            async def __aenter__(self):
                await self._real.__aenter__()
                return self

            async def __aexit__(self, *args):
                return await self._real.__aexit__(*args)

            async def execute(self, *a, **kw):
                if not injected_once["done"]:
                    injected_once["done"] = True
                    raise _locked_error()
                return await self._real.execute(*a, **kw)

            def __getattr__(self, name):
                return getattr(self._real, name)

        return _Flaky()

    # Patch the symbol the router module imported. The convention from
    # ``test_doubles-convention.md``: patch where the consumer looks.
    monkeypatch.setattr(router_module, "KanbanSessionLocal", _flaky_sessionmaker)

    # Also patch the same symbol in the db module (the helper imports it
    # so the wrapper sees the same KasbanSessionLocal source as the route).
    # This is the same symbol the helper itself uses to open the wrapping
    # session on retries — but in this test the helper is fully exercised,
    # so we only need the route's session to be flaky.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post(
            "/api/v1/kanban/cards",
            json={"project_key": PK, "title": "first", "confirm_new_project": True},
        )
    assert r.status_code == 201, r.text
    assert injected_once["done"], "the flaky session was never invoked — patch missed"
    assert r.json()["title"] == "first"


# --- behavioural: create_project_from_interview must keep the app DB stable --


@pytest.mark.asyncio
async def test_create_project_from_interview_retries_kanban_only(monkeypatch):
    """A lock-OperationalError on the kanban half must retry, but the
    app DB session must NOT be re-opened — a fresh AsyncSessionLocal on
    each retry would re-insert the Project row and double-write the
    target directory.

    Pin: across N retries, the route's app DB session is opened
    exactly once. We distinguish the route's session from the kanban
    audit log's short-lived sessions (``dispatch._record_audit``) by
    counting only the opens that happen *before* the wrapper runs.
    """
    from app.api.v1.kanban import router as router_module
    from app.database import AsyncSessionLocal as RealAppSessionLocal

    app_open_count = {"n": 0, "phase": "before"}

    class _CountingAppSession:
        def __init__(self):
            self._real = RealAppSessionLocal()

        async def __aenter__(self):
            # Only count opens that happen OUTSIDE the run_write_with_retry
            # wrapper — those are the route's own app DB session. The kanban
            # audit log writes a short-lived session per commit; those are
            # internal to the kanban layer and not part of the wrap contract.
            if app_open_count["phase"] == "before":
                app_open_count["n"] += 1
            return await self._real.__aenter__()

        async def __aexit__(self, *args):
            return await self._real.__aexit__(*args)

        def __getattr__(self, name):
            return getattr(self._real, name)

    real_kanban = router_module.KanbanSessionLocal
    injected_once = {"done": False}

    class _FlakyKanban:
        def __init__(self):
            self._real = real_kanban()

        async def __aenter__(self):
            await self._real.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self._real.__aexit__(*args)

        async def execute(self, *a, **kw):
            if not injected_once["done"]:
                injected_once["done"] = True
                raise _locked_error()
            return await self._real.execute(*a, **kw)

        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr(router_module, "KanbanSessionLocal", _FlakyKanban)
    # AsyncSessionLocal is imported inside the route with
    # `from app.database import AsyncSessionLocal`, so the route's
    # module attribute never sees it. Patch the source module instead
    # — same convention the mcp wrap tests use for app.database.
    import app.database as _database
    monkeypatch.setattr(_database, "AsyncSessionLocal", _CountingAppSession)

    # Build a fake target directory inside tmp so the inception service
    # has a place to write. The test treats the kanban retry as the
    # thing under test; the inception service's own side effects run
    # end-to-end on the first attempt that succeeds.
    import pathlib
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "repo"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            # The route's session opens BEFORE the wrapper — the count
            # fires exactly once. Switch the counter to "after" once
            # we're inside the wrapper so the kanban-audit sessions
            # don't pollute the route-level pin.
            from app.kanban.db import run_write_with_retry as _rwr
            orig = _rwr

            async def _counting_rwr(*args, **kwargs):
                app_open_count["phase"] = "after"
                try:
                    return await orig(*args, **kwargs)
                finally:
                    app_open_count["phase"] = "before"
            monkeypatch.setattr(router_module, "run_write_with_retry", _counting_rwr)

            r = await ac.post(
                "/api/v1/kanban/projects/from-interview",
                json={
                    "project_name": "RetryProj",
                    "target_path": str(target),
                    "title": "test",
                    "description": "test",
                    "spec_md": "# spec\n",
                    "plan_md": "# plan\n",
                },
            )
    assert r.status_code == 201, r.text
    assert injected_once["done"], "kanban flake was never triggered — wrap missed"
    assert app_open_count["n"] == 1, (
        f"AsyncSessionLocal opened {app_open_count['n']} times outside the wrap — "
        "retry must not re-open the app DB session"
    )
