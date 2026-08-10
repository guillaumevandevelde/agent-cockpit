"""Pin the asyncio event-loop-unblocked invariant around the sync spawn call.

Kanban card ``12227dcab0db46e588755f6e12b2853a`` observed that
``_run_card`` invokes the worktree ``SpawnTransport`` synchronously inside an
``async def`` running on the FastAPI event-loop thread. The transport body
runs ``subprocess.run([\"git\", ...])`` + ``subprocess.run([\"git\", \"worktree\",
\"add\", ...])`` + ``subprocess.run([\"tmux\", \"new-session\", ...])``
sequentially, blocking the asyncio loop for the full ~30-40s spawn window.
Other HTTP handlers cannot be scheduled; an operator click in the UI feels
like a frozen page until the spawn returns.

The fix lives in the same commit as this test: ``_run_card`` now invokes the
transport through ``asyncio.to_thread(...)`` so the subprocess runs land on a
thread-pool worker and the event loop remains free. The error path (sync
exception from the transport) flows through the existing ``try/except`` around
the ``to_thread`` call — a transport raising on the worker re-raises in the
coroutine exactly as it did before.

Regression card: ``12227dcab0db46e588755f6e12b2853a``.
"""
import threading
import unittest.mock as mock

import pytest
import pytest_asyncio

from app.kanban import dispatch
from app.kanban.operations import apply_operation
from app.kanban.service import get_card
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


PK = "git:example.com/me/repo"


async def _make_card(s, title="loop-task", column="Backlog"):
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None, payload={"title": title, "column": column},
    )
    await s.flush()
    return cid


class _ThreadRecordingTransport:
    """Spy transport that records the OS thread it was invoked on.

    The dispatch hot path runs on the asyncio loop's thread. Python's
    ``threading.main_thread()`` returns that same thread (CPython's
    ``BaseEventLoop._thread_id`` IS the main thread's ident by default —
    see ``asyncio.events.py``). Before the fix, the sync transport call
    ran on the main thread and blocked it for the full spawn window. After
    the fix (``asyncio.to_thread`` wrap), the call runs on a
    ``ThreadPoolExecutor`` worker — a *different* ``Thread`` instance.
    """

    def __init__(self) -> None:
        self.called_from_main_thread: bool | None = None
        self.called_thread_ident: int | None = None
        self.call_count = 0

    def __call__(self, *, directory, prompt, session_name, cli_id="claude-code",
                 provider="anthropic", model=None,
                 endpoint_name=None, endpoint_base_url=None,
                 endpoint_auth_token=None,
                 card_id=None, column_name=None):
        current = threading.current_thread()
        self.called_from_main_thread = current is threading.main_thread()
        self.called_thread_ident = current.ident
        self.call_count += 1
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}


@pytest.mark.asyncio
async def test_worktree_spawn_runs_off_event_loop_thread():
    """``_run_card`` must invoke the sync worktree transport off the
    asyncio event loop's main thread. With the bug, the transport runs
    synchronously on the main thread (the asyncio loop thread) and
    blocks every other coroutine — UI clicks, MCP tool calls, even
    unrelated ``asyncio.sleep``-based heartbeats — for the full spawn
    duration. After the fix (``asyncio.to_thread`` wrap), the call lands
    on a ``ThreadPoolExecutor`` worker and the main thread stays free.

    Asserting ``called_from_main_thread is False`` is the load-bearing
    invariant: ``True`` reproduces the frozen-UI symptom that the kanban
    card documents.

    Regression card: ``12227dcab0db46e588755f6e12b2853a``.
    """
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        card = await get_card(s, cid)

    transport = _ThreadRecordingTransport()

    async with KanbanSessionLocal() as s:
        # The worktree transport is selected by ``_transport_is_worktree``
        # via the ``transport_kind`` label. Default = worktree path; the
        # spy needs the same label so ``_run_card`` commits before spawn
        # (write-lock invariant from card a2d15978d897436ca992e22f9ba23ba6
        # — pre-spawn commit is unconditional in the current code path,
        # but labelling the spy ensures we still test the worktree branch
        # if the resume/headless branch ever diverges again).
        transport.transport_kind = "worktree"  # type: ignore[attr-defined]

        await dispatch._run_card(
            s, card=card, project_key=PK, project_path="/p",
            transport=transport, live_sessions=set(),
        )

    assert transport.call_count == 1, "spy transport was not invoked"
    assert transport.called_from_main_thread is False, (
        "worktree transport ran on the asyncio main thread — the spawn "
        "blocks the FastAPI event loop for its full duration; UI and MCP "
        "calls land in the queue behind it. Regression of card "
        "12227dcab0db46e588755f6e12b2853a."
    )
    # Sanity: a different OS thread really did run the transport (not just
    # a re-entrant call on the same thread). This also rules out a false
    # negative where ``threading.current_thread() is threading.main_thread()``
    # happens to be False for an unrelated reason (e.g. threading internals
    # changed).
    assert transport.called_thread_ident != threading.main_thread().ident, (
        "thread ident equals the main thread's ident even though the "
        "Thread object differs — transport did not actually run on a "
        "worker thread."
    )


@pytest.mark.asyncio
async def test_worktree_spawn_exception_propagates_through_to_thread():
    """A sync exception from the worktree transport must still reach the
    caller's ``try/except`` block. ``asyncio.to_thread`` re-raises worker
    exceptions inside the awaiting coroutine, so the existing
    compensation path (release claim, clear pending_spawn_session, bump
    dispatch failures, etc.) keeps firing — same semantics as the
    pre-fix sync call.

    Regression card: ``12227dcab0db46e588755f6e12b2853a``.
    """
    from app.kanban.dispatch import CardSpawnFailed

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        card = await get_card(s, cid)

    def exploding_transport(*, directory, prompt, session_name,
                            cli_id="claude-code", provider="anthropic",
                            model=None, **kwargs):
        raise RuntimeError("simulated subprocess failure")

    exploding_transport.transport_kind = "worktree"  # type: ignore[attr-defined]

    with pytest.raises(CardSpawnFailed) as excinfo:
        async with KanbanSessionLocal() as s:
            await dispatch._run_card(
                s, card=card, project_key=PK, project_path="/p",
                transport=exploding_transport, live_sessions=set(),
            )

    assert "simulated subprocess failure" in str(excinfo.value)


@pytest.mark.asyncio
async def test_resume_transport_also_runs_off_event_loop_thread():
    """The resume / headless / sandcastle branch (``else:`` of the
    ``is_fresh_worktree`` check) takes a *different* transport but
    lands in the same ``card_transport(...)`` call site, so the
    ``asyncio.to_thread`` wrap covers it identically. Pin the invariant
    for both paths to catch a future refactor that special-cases only
    one.
    """
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        # Force the non-worktree branch by setting resume_session_id;
        # ``_run_card``'s resume path skips ``is_fresh_worktree``.
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={
                "resume_session_id": "sess-x",
                "resume_project_folder": "proj-x",
            },
        )
        await s.commit()
        card = await get_card(s, cid)

    resume_spy = _ThreadRecordingTransport()

    with mock.patch.object(dispatch, "make_resume_transport", return_value=resume_spy):
        async with KanbanSessionLocal() as s:
            await dispatch._run_card(
                s, card=card, project_key=PK, project_path="/p",
                transport=resume_spy, live_sessions=set(),
            )

    assert resume_spy.call_count == 1, "resume transport was not invoked"
    assert resume_spy.called_from_main_thread is False, (
        "resume transport ran on the asyncio main thread — same "
        "event-loop blockage as the worktree path. Regression of card "
        "12227dcab0db46e588755f6e12b2853a."
    )