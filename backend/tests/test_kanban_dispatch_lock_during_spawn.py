"""Pin the kanban-DB write-lock invariant around the synchronous spawn call.

Kanban card ``a2d15978d897436ca992e22f9ba23ba6`` observed that an
auto-dispatch tick holding the kanban SQLite write lock for the full
~30-40s spawn window produced ``sqlite3.OperationalError: database is
locked`` on concurrent UI/MCP writes (busy_timeout=5000 → 500 to the
caller). The fix lives in commit ``fd381651``: ``_run_card`` now calls
``await session.commit()`` between the claim/move/``pending_spawn_session``
writes and the synchronous ``card_transport(...)`` call, so the lock is
released before the spawn blocks the event loop.

These tests pin two related invariants:

1. **No open transaction at spawn-time.** ``session.in_transaction()`` is
   ``False`` at the moment ``card_transport(...)`` is invoked. If a
   regression moves the commit *after* the spawn, this turns True and
   concurrent writers start timing out at ``sqlite_busy_timeout_ms``.

2. **The commit ordering is monotonic.** ``_run_card`` must commit before
   the transport is called — never after. Catches a regression where a
   well-meaning cleanup moves the commit to the post-spawn bookkeeping
   block, which would still pass test (1) by accident (the post-spawn
   commit re-closes the transaction but the spawn itself would have
   blocked the lock for the full ~30s window).
"""
import asyncio
import sqlite3
import time
import unittest.mock as mock

import pytest
import pytest_asyncio

from app.kanban import dispatch
from app.kanban.operations import apply_operation
from app.kanban.service import get_card
from tests.kanban_test_db import TestSessionLocal, _db_path, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


PK = "git:example.com/me/repo"


async def _make_card(s, title="lock-task", column="Backlog"):
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None, payload={"title": title, "column": column},
    )
    await s.flush()
    return cid


class _EventOrderingTransport:
    """Spy transport that records the session transaction state at call time.

    Used to prove ``session.commit()`` ran BEFORE the sync spawn call:
    ``session.in_transaction()`` is the SQLAlchemy-blessed test for "is this
    session currently holding a SQLite transaction". Returning ``False`` at
    call-time is the load-bearing invariant; returning ``True`` is the
    regression that the kanban card documents.

    ``commit_counter`` is an optional dict like ``{"n": 0}`` whose ``"n"`` key
    is incremented by the test's patched ``AsyncSession.commit``. The spy
    snapshots the count at the moment ``card_transport`` is invoked, so a
    test can assert ``commit_call_count_at_call >= 1`` to prove at least one
    commit landed before the sync spawn — the second invariant the card
    pins (the in-transaction check alone wouldn't catch a deferred commit,
    because the post-spawn bookkeeping closes the transaction before the
    test inspects it).
    """

    def __init__(self, commit_counter: dict | None = None) -> None:
        self.in_transaction_at_call: bool | None = None
        self.called_at: float | None = None
        self.commit_call_count_at_call: int | None = None
        self.commit_counter = commit_counter
        self.call_count = 0

    def __call__(self, *, directory, prompt, session_name, cli_id="claude-code",
                 provider="anthropic", model=None,
                 endpoint_name=None, endpoint_base_url=None,
                 endpoint_auth_token=None,
                 card_id=None, column_name=None):
        self.call_count += 1
        self.called_at = time.monotonic()
        sess = self._session_ref[0]
        if sess is not None:
            # The session-side assertion: a fresh commit must have closed
            # the transaction, otherwise SQLite's write lock is still held
            # for the duration of this (sync) call.
            self.in_transaction_at_call = sess.in_transaction()
            # Snapshot the commit counter so a regression that defers the
            # commit to post-spawn bookkeeping surfaces as
            # ``commit_call_count_at_call == 0`` here.
            if self.commit_counter is not None:
                self.commit_call_count_at_call = self.commit_counter["n"]
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}

    # Bound by the test before invoking _run_card so the spy can inspect
    # the session that's running the dispatch.
    _session_ref: list = [None]


@pytest.mark.asyncio
async def test_session_has_no_open_transaction_when_spawn_starts():
    """``_run_card`` must release the SQLite write lock BEFORE the sync
    spawn call begins. Without this, concurrent UI/MCP writers time out at
    ``sqlite_busy_timeout_ms=5000`` and the API returns 500.

    Concretely: at the moment ``card_transport(...)`` is invoked,
    ``session.in_transaction()`` is ``False`` — the pre-spawn
    ``await session.commit()`` has already closed the transaction.

    Regression card: ``a2d15978d897436ca992e22f9ba23ba6``.
    Fix commit: ``fd381651`` ("pre-register spawn name so an interrupted
    tmux session can't orphan") which made the pre-spawn commit
    unconditional.
    """
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        card = await get_card(s, cid)

    transport = _EventOrderingTransport()
    transport._session_ref[0] = None  # bind later

    # The autouse fixture owns the engine; we run the dispatch on a fresh
    # session and hand the spy a reference to it.
    async with KanbanSessionLocal() as s:
        transport._session_ref[0] = s
        await dispatch._run_card(
            s, card=card, project_key=PK, project_path="/p",
            transport=transport, live_sessions=set(),
        )

    assert transport.call_count == 1, "spy transport was not invoked"
    assert transport.in_transaction_at_call is False, (
        "session had an open transaction at spawn time — SQLite write "
        "lock is held during the sync spawn call, reproducing the "
        "concurrent-writer 500 bug from card a2d15978d897436ca992e22f9ba23ba6"
    )


@pytest.mark.asyncio
async def test_commit_runs_before_sync_spawn_call():
    """``session.commit()`` must run BEFORE the ``card_transport(...)`` call,
    not after. A regression that defers the commit to the post-spawn
    bookkeeping block would still pass the in-transaction check (the
    post-spawn commit closes the transaction before the test inspects it),
    but would hold the lock for the full spawn duration — the exact bug
    the card documents.

    Pin the order: count ``AsyncSession.commit`` invocations during
    ``_run_card`` and snapshot the count from inside the spy transport at
    the moment it is called. ``commit_count >= 1`` at spawn-time proves
    at least one commit ran before the sync spawn. (No upper bound: the
    post-spawn bookkeeping writes commit again, but those happen after
    the spawn returns, not during.)
    """
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        card = await get_card(s, cid)

    from sqlalchemy.ext.asyncio import AsyncSession
    counter = {"n": 0}
    original_commit = AsyncSession.commit

    async def counting_commit(self, *args, **kwargs):
        counter["n"] += 1
        return await original_commit(self, *args, **kwargs)

    # Patch at the class level. The autouse fixture rebuilds the engine
    # between tests, so the patched class is GC'd cleanly — no
    # cross-test leakage.
    AsyncSession.commit = counting_commit
    try:
        # Hand the counter dict to the spy so ``__call__`` can snapshot
        # the commit count at the moment ``card_transport`` runs. Without
        # this wire, the spy never reads the counter and the assertion
        # below is tautological (passes whether or not a commit ran
        # before spawn) — the very pattern the card rejects.
        transport = _EventOrderingTransport(commit_counter=counter)

        async with KanbanSessionLocal() as s:
            transport._session_ref[0] = s
            # Patch the per-instance commit accessor too: SQLAlchemy may
            # bind commit on the instance via __init__ for some session
            # implementations; the class-level patch is what counts for
            # the test.
            s.__class__.commit = counting_commit

            await dispatch._run_card(
                s, card=card, project_key=PK, project_path="/p",
                transport=transport, live_sessions=set(),
            )

            # Snapshot the counter AFTER _run_card returns — we need the
            # total commit count, then we verify the spy observed ≥1 at
            # spawn-time.
            post_run_count = counter["n"]
    finally:
        AsyncSession.commit = original_commit

    assert transport.call_count == 1, "spy transport was not invoked"
    # The spawn-time invariant: at least one commit ran before the
    # synchronous spawn call. ``commit_call_count_at_call`` is the snapshot
    # the spy took inside ``__call__``; ``is None`` means the test forgot
    # to wire the counter, which is a different failure mode than "no
    # commit ran" and surfaces as a missing-fixture bug.
    assert transport.commit_call_count_at_call is not None, (
        "spy transport was not wired to the commit counter — the test "
        "fixture is incomplete, not a regression of the spawn-time "
        "commit invariant"
    )
    assert transport.commit_call_count_at_call >= 1, (
        f"no commit ran before the sync spawn call "
        f"(observed={transport.commit_call_count_at_call}); "
        f"the SQLite write lock would have been held for the full spawn "
        f"duration. Regression of card a2d15978d897436ca992e22f9ba23ba6."
    )
    # Sanity: post-spawn bookkeeping wrote at least one more commit OR the
    # function exited cleanly. We don't pin a strict number because the
    # compensation-on-failure path adds commits; we only need to confirm
    # the spawn-time invariant above.

    # Stop silencing the variable (only referenced for clarity in the assert message).
    _ = post_run_count


@pytest.mark.asyncio
async def test_run_card_for_resume_transport_also_commits_before_spawn():
    """The resume / headless / sandcastle branch (``else:`` of the
    ``is_fresh_worktree`` check) also calls ``await session.commit()``
    before the spawn. The dispatch hot path can land on either branch
    depending on the card's transport config; the lock-release invariant
    must hold on both.
    """
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        # Force the non-worktree branch by setting resume_session_id;
        # _run_card's resume path skips is_fresh_worktree.
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={
                "resume_session_id": "sess-x",
                "resume_project_folder": "proj-x",
            },
        )
        await s.commit()
        card = await get_card(s, cid)

    # Patch the resume transport factory to return our spy.
    def resume_transport(*, directory, prompt, session_name, cli_id="claude-code",
                          provider="anthropic", model=None, **kwargs):
        sess = transport._session_ref[0]
        if sess is not None:
            transport.in_transaction_at_call = sess.in_transaction()
            transport.call_count += 1
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}

    transport = _EventOrderingTransport()

    with mock.patch.object(dispatch, "make_resume_transport", return_value=resume_transport):
        async with KanbanSessionLocal() as s:
            transport._session_ref[0] = s
            await dispatch._run_card(
                s, card=card, project_key=PK, project_path="/p",
                transport=resume_transport, live_sessions=set(),
            )

    assert transport.call_count == 1, "resume transport was not invoked"
    assert transport.in_transaction_at_call is False, (
        "resume branch held an open transaction at spawn time — "
        "lock-release invariant regressed on the resume path"
    )


@pytest.mark.asyncio
async def test_fresh_worktree_branch_also_commits_before_spawn():
    """The fresh-worktree branch (``if is_fresh_worktree:`` in ``_run_card``)
    must also release the SQLite write lock BEFORE the synchronous spawn
    call. This is the production hot path — the normal dispatch route
    resolves its transport through ``get_transport_for_project`` →
    ``make_worktree_transport(skip_permissions=skip)`` and lands here.

    Earlier tests in this module pinned the resume / sandcastle / headless
    branches but never this one: the bare ``_EventOrderingTransport``
    carries no ``transport_kind`` label, so ``_transport_is_worktree``
    returns ``False`` and the dispatch falls through to the ``else:`` arm.
    A regression that removes the pre-spawn commit from the
    ``if is_fresh_worktree:`` block would still pass every test in this
    file — exactly the gap kanban card ``a2d15978d897436ca992e22f9ba23ba6``
    flagged in its IMPEDIMENT follow-up. This test closes it.

    Detection method: tag the spy with ``transport_kind = "worktree"``
    (the same label ``make_worktree_transport`` stamps on its closures)
    so ``_transport_is_worktree`` returns ``True`` and the dispatch
    enters the production branch. Then assert the same
    ``in_transaction() is False`` + ``commit_call_count_at_call >= 1``
    invariants the other tests pin on the ``else:`` branch.
    """
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        card = await get_card(s, cid)

    from sqlalchemy.ext.asyncio import AsyncSession
    counter = {"n": 0}
    original_commit = AsyncSession.commit

    async def counting_commit(self, *args, **kwargs):
        counter["n"] += 1
        return await original_commit(self, *args, **kwargs)

    AsyncSession.commit = counting_commit
    try:
        transport = _EventOrderingTransport(commit_counter=counter)
        # The label that flips ``_transport_is_worktree`` to True. Every
        # worktree-transport factory in dispatch.py stamps the same label
        # on its closures (see ``make_worktree_transport``'s tag-comment
        # and kaart a962b209…). Setting it on the spy routes the dispatch
        # through the production ``if is_fresh_worktree:`` branch.
        transport.transport_kind = "worktree"

        async with KanbanSessionLocal() as s:
            transport._session_ref[0] = s
            s.__class__.commit = counting_commit

            await dispatch._run_card(
                s, card=card, project_key=PK, project_path="/p",
                transport=transport, live_sessions=set(),
            )
    finally:
        AsyncSession.commit = original_commit

    assert transport.call_count == 1, "spy transport was not invoked"
    # Same invariants as the resume-branch test, applied to the branch
    # the production dispatch actually takes.
    assert transport.in_transaction_at_call is False, (
        "fresh-worktree branch held an open transaction at spawn time — "
        "the production dispatch path regressed on the lock-release "
        "invariant (card a2d15978d897436ca992e22f9ba23ba6)"
    )
    assert transport.commit_call_count_at_call is not None, (
        "spy transport was not wired to the commit counter — the test "
        "fixture is incomplete, not a regression of the spawn-time "
        "commit invariant"
    )
    assert transport.commit_call_count_at_call >= 1, (
        f"no commit ran before the sync spawn call on the fresh-worktree "
        f"branch (observed={transport.commit_call_count_at_call}); "
        f"SQLite write lock held for the full ~30-40s spawn window — "
        f"concurrent UI/MCP writes would 500 with 'database is locked'. "
        f"Regression of card a2d15978d897436ca992e22f9ba23ba6."
    )


def _write_lock_is_free() -> bool:
    """True when no other connection holds the kanban write lock.

    Asks the only question that matters, from a genuinely separate
    connection: *can another writer get in?* The short ``timeout`` keeps a
    regression fast — 0.2s instead of sitting out the full 5s
    ``busy_timeout`` — so a held lock reads as a failure, not a hang.
    """
    con = sqlite3.connect(_db_path, timeout=0.2, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("ROLLBACK")
        return True
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return False
        raise
    finally:
        con.close()


@pytest.mark.asyncio
async def test_write_lock_is_free_during_the_pre_spawn_resolution_phase():
    """The lock must already be free BEFORE the pre-spawn resolution phase.

    Every other test in this module probes at spawn-time, which is too late
    to see the real exposure. ``_run_card`` opens its transaction at the
    very first write it does — claim, move, ``pending_spawn_session`` — and
    then spends tens of seconds resolving before it reaches the pre-spawn
    commit: provider/model resolution, ceremony profile, prior-branch git,
    prompt injectors, plan lookup, and a 2s endpoint HTTP probe. SQLite
    hands out one write lock, so every other writer on the board burns its
    5s ``busy_timeout`` and fails for that whole window.

    Measured on the live backend (``logs/backend/run-20260817-094413-3592-2.log``):
    the tick claimed a card at 13:04:34.2 and only committed at ~13:05:00.8
    — 26s. ``POST /kanban/cards`` hit a locked DB twice inside it, and three
    ``DELETE /kanban/cards/{id}`` calls in the next tick's window returned
    500 ``database is locked``.

    The probe patches ``_resolve_prior_branch_warning`` — the consumer's
    binding, per ``docs/cockpit/test-doubles-convention.md`` — because it
    runs after all three writes and before the pre-spawn commit.
    """
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        card = await get_card(s, cid)

    observed: list[bool] = []
    original = dispatch._resolve_prior_branch_warning

    async def probing_prior_branch_warning(session, **kwargs):
        # Probe from a worker thread, the way a real competing writer
        # arrives (another request, the MCP server). A blocking sqlite3
        # call on the event-loop thread would stall aiosqlite's own result
        # delivery and deadlock the connection we are asking about.
        observed.append(await asyncio.to_thread(_write_lock_is_free))
        return await original(session, **kwargs)

    dispatch._resolve_prior_branch_warning = probing_prior_branch_warning
    try:
        transport = _EventOrderingTransport()
        transport.transport_kind = "worktree"
        async with KanbanSessionLocal() as s:
            transport._session_ref[0] = s
            await dispatch._run_card(
                s, card=card, project_key=PK, project_path="/p",
                transport=transport, live_sessions=set(),
            )
    finally:
        dispatch._resolve_prior_branch_warning = original

    assert observed, (
        "the probe never fired — _run_card no longer calls "
        "_resolve_prior_branch_warning, so this test proves nothing"
    )
    assert all(observed), (
        "the kanban write lock was still held when _run_card entered its "
        "pre-spawn resolution phase. Claim/move/pending_spawn_session open "
        "the transaction and the pre-spawn commit is tens of seconds away, "
        "so every concurrent board write (POST /kanban/cards, DELETE "
        "/kanban/cards/{id}) fails on busy_timeout for that whole window."
    )