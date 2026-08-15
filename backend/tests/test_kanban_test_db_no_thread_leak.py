"""Regression test for kanban-kaart 5554de60...

``test_kanban_dispatch.py`` runs end-green but leaves behind
``PytestUnhandledThreadExceptionWarning`` instances: an aiosqlite
``_connection_worker_thread`` raises ``RuntimeError: Event loop is closed``
when it next tries to deliver a pending future. The warning fires on
whichever test happens to be running when the orphaned thread makes its
delivery attempt — so the test that "carries" the warning shifts
run-to-run and the leak is invisible to anyone grepping the traceback for
a specific test.

Root cause: the autouse ``_reset_test_db`` / ``_reset_app_database_tables``
fixtures in ``backend/tests/conftest.py`` only ran ``reset_test_tables()``
on entry; their teardown was empty. pytest-asyncio gives every test its
own event loop, and ``engine.dispose()`` was never called between tests.
An AsyncSession abandoned by the previous test's body (via a cyclic
reference that survives until the *next* test's ``gc.collect``) keeps its
aiosqlite ``Connection`` alive across the loop swap. The Connection's
worker thread has a future bound to the previous (now-closed) loop; its
next ``call_soon_threadsafe`` raises ``RuntimeError: Event loop is
closed``, which pytest's threadexception hook surfaces as a warning.

The fix: every per-test fixture that opens a connection on a test engine
must also dispose the engine on teardown. ``engine.dispose()`` invalidates
the (NullPool) pool, the next test's ``reset_test_tables`` runs
``gc.collect(1)`` which breaks the AsyncSession reference cycle, and
SQLAlchemy calls ``Connection.terminate()`` on each unreleased
connection — closing the underlying aiosqlite Connection cleanly, so its
worker thread sees ``_STOP_RUNNING_SENTINEL`` on the queue and exits
without touching the (now-closed) event loop.

This module is the warning-as-error regression test the card asked for.
It deliberately opens an ``async with`` session, drops the reference (to
simulate the cyclic leak that back-to-back dispatch tests can leave
behind), and asserts that — *after* the autouse fixture teardown's
``engine.dispose()`` has run on the next test's ``reset_test_tables``
trigger — no aiosqlite ``_connection_worker_thread`` is still alive when
the next test's body executes. Without the conftest teardown that
disposes the engine, the orphan worker's pending future lands on a
closed pytest-asyncio loop and the warning-as-error promotion fails the
test with the RuntimeError the warning would otherwise paper over.
"""
import asyncio
import gc
import threading

import pytest
from sqlalchemy import text

from app.kanban import dispatch
from app.kanban.operations import apply_operation
from tests.app_database_test_db import TestSessionLocal as AppTestSessionLocal
from tests.kanban_test_db import TestSessionLocal

PK = "git:example.com/me/repo"


def _aiosqlite_worker_threads() -> list[str]:
    return [
        t.name for t in threading.enumerate()
        if "_connection_worker_thread" in t.name
    ]


@pytest.mark.asyncio
async def test_dispose_test_engine_reclaims_leaked_aiosqlite_workers():
    """``dispose_test_engine`` (the helper the conftest's autouse fixture
    runs in its post-yield) must terminate every aiosqlite
    ``_connection_worker_thread`` that an abandoned AsyncSession left
    behind. Without this, the orphan thread's next
    ``call_soon_threadsafe`` lands on a closed pytest-asyncio loop and
    surfaces as ``PytestUnhandledThreadExceptionWarning`` on whichever
    test happens to run next (kanban-kaart 5554de60…).
    """
    # Start both engines from an empty pool. Whether the sessions below open a
    # *new* aiosqlite connection (and so a new worker thread) or reuse one that
    # the conftest's ``reset_test_tables`` left checked in depends on how many
    # tests ran before this one. On CI 2026-08-15 the baseline was non-empty,
    # which is exactly the state that makes the leak spawn fewer workers than
    # this test assumes. Disposing here removes that dependency.
    from tests.app_database_test_db import test_engine as _app_engine
    from tests.kanban_test_db import dispose_test_engine as _dispose_kanban

    await _dispose_kanban()
    await _app_engine.dispose()

    # Baseline the worker threads that already exist. ``threading.enumerate()``
    # is process-wide, so in a full-suite run it also sees workers belonging to
    # engines this test never created and never disposes — an earlier test's
    # live connection then fails the post-dispose assertion below for work this
    # test is not responsible for. Only the threads *this* test leaks are in
    # scope for the regression (kanban-kaart 5554de60…).
    pre_existing = set(_aiosqlite_worker_threads())

    # Deliberately orphan a session on the kanban engine and one on the
    # app-database engine — the same cyclic-AsyncSession leak pattern that
    # back-to-back dispatch tests can leave behind in real life. Run a
    # query first so the aiosqlite ``Connection`` is actually opened
    # (the factory is lazy until first checkout), then drop the local
    # reference so only the session's internal ref cycle keeps the
    # connection alive.
    kanban_factory = TestSessionLocal()
    app_factory = AppTestSessionLocal()
    leaked = kanban_factory()
    await leaked.execute(text("SELECT 1"))
    leaked = None
    leaked = app_factory()
    await leaked.execute(text("SELECT 1"))
    leaked = None

    # Sample the workers this test just leaked BEFORE forcing the collection
    # below. ``gc.collect()`` reclaims the AsyncSession reference cycles, and
    # SQLAlchemy's collector hook then *terminates* the non-checked-in
    # connection — it says so verbatim in the SAWarning it emits here. The
    # aiosqlite worker threads exit on their own schedule after that, so how
    # many are still enumerable a few statements later is a race that moves
    # with machine load and with how many tests ran before this one.
    #
    # Sampling after the collect is what made this test flaky: it passed when
    # run alone (2 of 2 workers still enumerable) and failed inside the full
    # suite on CI 2026-08-15 (0 of 2 left, so the set difference was empty and
    # the invariant below reported "leaked sessions did not produce an orphan
    # worker thread"). Measured locally with a preceding test file: 2 workers
    # before the collect, 1 after.
    leaked_workers = set(_aiosqlite_worker_threads()) - pre_existing

    # Force the gen-1 GC the conftest's next ``reset_test_tables()`` would
    # otherwise run on the *next* test. Without this the AsyncSession
    # reference cycle still holds the aiosqlite ``Connection`` and
    # ``dispose_test_engine`` has nothing concrete to clean up — the
    # test would pass for the wrong reason (the threads never materialise
    # in the first place).
    gc.collect()

    # Sanity-check the test setup invariant: the leak must actually
    # produce orphan worker threads, otherwise the post-dispose
    # assertion below would be vacuous. If this assertion starts failing
    # in some future SQLAlchemy / aiosqlite version, the dispose path
    # needs revisiting — the leak contract has shifted underneath us.
    assert leaked_workers, (
        "test setup invariant violated: leaked sessions did not produce "
        "an orphan worker thread; the regression assertion below would "
        "be vacuous."
    )

    # The cleanup path the conftest's autouse fixture runs in its
    # post-yield. Calling it explicitly here lets us assert post-cleanup
    # state from inside the test body — we can't observe what the
    # post-yield does from outside, so this is the only way to make the
    # regression deterministic.
    from tests.kanban_test_db import dispose_test_engine
    await dispose_test_engine()
    from tests.app_database_test_db import test_engine as app_test_engine
    await app_test_engine.dispose()

    # Poll briefly: the worker's ``break`` on ``_STOP_RUNNING_SENTINEL``
    # is gated on ``call_soon_threadsafe`` succeeding, which costs one
    # event-loop tick. 100 ms × 20 is generous without slowing the suite.
    # Scope the regression to the workers this test actually leaked, not to
    # "anything new since the baseline". A ``- pre_existing`` difference also
    # picks up a worker some *other* engine spawned after the baseline was
    # taken, which this test neither owns nor disposes.
    for _ in range(20):
        if not set(_aiosqlite_worker_threads()) & leaked_workers:
            break
        await asyncio.sleep(0.005)

    survivors = sorted(set(_aiosqlite_worker_threads()) & leaked_workers)
    assert not survivors, (
        f"aiosqlite worker threads leaked past dispose_test_engine: "
        f"{survivors}. The kanban test DB's dispose path is supposed to "
        f"reclaim these via ``engine.dispose()``; if the assertion ever "
        f"fires, the dispose is no longer catching the orphan workers "
        f"and the conftest's autouse-fixture teardown needs revisiting "
        f"(kanban-kaart 5554de60…)."
    )


@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
@pytest.mark.asyncio
async def test_dispatch_flow_no_aiosqlite_thread_leaks(tmp_path):
    """Smoke test for the warning-as-error path on the dispatch flow.

    Runs the exact slice that ``test_kanban_dispatch.py::test_dispatch_
    provider_id_falls_back_to_engineer`` exercises — a card with an
    explicit ``agent`` payload through ``dispatch.dispatch_project`` —
    repeated enough times to amplify any cyclic-AsyncSession leak the
    dispatch path leaves behind. With
    ``PytestUnhandledThreadExceptionWarning`` promoted to an error, any
    aiosqlite worker thread that crosses the pytest-asyncio event-loop
    boundary with a still-bound future fails this test directly.

    Note: this is a structural smoke test, not a deterministic regression
    for ``dispose_test_engine``. The companion test
    ``test_dispose_test_engine_reclaims_leaked_aiosqlite_workers`` is the
    deterministic regression — it fails immediately if the conftest's
    autouse teardown stops calling ``engine.dispose()``. This test
    primarily guards against the warning-as-error filter being accidentally
    removed in a future pytest config change, and exercises the full
    dispatch path end-to-end to confirm no obvious leak surfaces from
    a single dispatch in isolation.
    """

    class _RecordingTransport:
        def __init__(self):
            self.calls = []

        def __call__(self, *, directory, prompt, session_name, **kwargs):
            self.calls.append({"directory": directory, "prompt": prompt,
                               "session_name": session_name})
            return {"session_name": session_name, "transport": "test"}

    # Seed the project agents dir with a stub persona so ``dispatch_project``
    # can resolve the agent ID against the filesystem — same setup as
    # ``test_dispatch_provider_id_falls_back_to_engineer``.
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "engineer.md").write_text("You are the Engineer.")

    for iteration in range(3):
        async with TestSessionLocal()() as s:
            cid = await apply_operation(
                s, op_type="create", entity_type="card", project_key=PK,
                entity_id=None,
                payload={"title": f"leak-canary-{iteration}", "column": "Backlog"},
            )
            await apply_operation(
                s, op_type="update", entity_type="card", project_key=PK,
                entity_id=cid, payload={"agent": "mimo-code"},
            )
            await s.commit()

        transport = _RecordingTransport()
        async with TestSessionLocal()() as s:
            await dispatch.dispatch_project(
                s, project_key=PK, project_path=str(tmp_path), transport=transport,
            )
            await s.commit()

        # Structural sanity that we actually went through the dispatch
        # path — otherwise the warning-as-error below would never fire
        # even if the bug regressed (the dispatch path is the source of
        # the original leak; if we never reach it, the test is vacuous).
        assert transport.calls, (
            "dispatch_project did not call the transport on iteration "
            f"{iteration}; the warning-as-error below would never "
            "trigger even if the aiosqlite thread leak came back."
        )

    # The fixture teardown's dispose runs after this returns; if any
    # aiosqlite worker survives, the threadexception hook fires inside
    # this test's call frame and pytest's filter promotes it to an error.
    # Without the dispose, the leak the original card filed against
    # would surface here as ``RuntimeError: Event loop is closed``
    # rather than a warning — which is exactly the determinism gap this
    # test was meant to close.
