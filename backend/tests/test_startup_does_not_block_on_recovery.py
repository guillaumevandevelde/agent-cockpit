"""Backend readiness must never be gated on session recovery.

Regression test for the permanent-502 restart loop: ``lifespan`` used to
``await recover_interrupted_sessions()`` inline. Recovery re-dispatches one
real agent session per interrupted card (~37s per resume spawn), so two or
more stale claims meant the lifespan never reached ``yield``, uvicorn never
began accepting on :8000, and ``cockpit.sh``'s health watchdog (30s grace +
3x10s = 50s budget) SIGKILLed the process at 51s. The next boot found the
same claims and died identically -- 12 consecutive restarts were observed.

The invariant these tests pin:

  1. Startup completes even while recovery is still running (the 502 bug).
  2. Recovery still precedes the autodispatch boot-reset and the dispatch
     tick -- the ordering the inline version guaranteed.
  3. A recovery failure still arms the dispatch tick.

Patch note: ``_recover_and_start_dispatch`` imports each collaborator
*inside* the function body, so patching the source module (rather than an
``app.main`` binding) is what the consumer actually looks up here -- the
opposite of the usual import-time-binding trap in
docs/cockpit/test-doubles-convention.md.
"""
import asyncio

import pytest

import app.main as main_module


@pytest.fixture
def neutralised_startup(monkeypatch):
    """Stub the side-effecting parts of lifespan that aren't under test.

    Keeps the real DB init (conftest already points those at the test DBs)
    but blocks anything that would touch the developer's machine: the
    ~/.claude/settings.json hook installer, the orphaned-relay sweep, and
    every APScheduler job registration.
    """
    import app.services.runs.pty_relay as pty_relay
    from app.services.scheduling import scheduler as scheduler_mod

    async def _noop_hooks():
        return None

    monkeypatch.setattr(main_module, "ensure_scheduling_hooks_installed", _noop_hooks)
    monkeypatch.setattr(pty_relay, "cleanup_orphaned_relays", lambda: None)
    monkeypatch.setattr(pty_relay, "close_all_relays", _noop_hooks)

    calls: list[str] = []

    class _FakeScheduler:
        def start(self):
            calls.append("start")

        def schedule_kanban_dispatch(self, interval_seconds=10):
            calls.append("schedule_kanban_dispatch")

        def schedule_stale_detection(self, interval_minutes=30):
            calls.append("schedule_stale_detection")

        def schedule_once(self, *a, **kw):
            calls.append("schedule_once")

        def schedule_cron(self, *a, **kw):
            calls.append("schedule_cron")

        def schedule_auto_backup(self, *a, **kw):
            calls.append("schedule_auto_backup")

        def shutdown(self):
            calls.append("shutdown")

    monkeypatch.setattr(scheduler_mod, "scheduler_service", _FakeScheduler())
    return calls


@pytest.mark.asyncio
async def test_startup_completes_while_recovery_is_still_running(
    neutralised_startup, monkeypatch
):
    """The 502 bug itself: a recovery that never returns must not stop the app
    from becoming ready."""
    import app.kanban.session_recovery as session_recovery

    recovery_entered = asyncio.Event()
    release_recovery = asyncio.Event()

    async def _hanging_recovery():
        recovery_entered.set()
        await release_recovery.wait()
        return 0

    monkeypatch.setattr(
        session_recovery, "recover_interrupted_sessions", _hanging_recovery
    )

    from fastapi import FastAPI
    app = FastAPI()

    cm = main_module.lifespan(app)
    started = False
    try:
        # Before the fix this await never returned -- the lifespan sat inside
        # recover_interrupted_sessions. 10s is far below the ~37s-per-card
        # recovery cost yet far above honest startup work.
        await asyncio.wait_for(cm.__aenter__(), timeout=10)
        started = True

        # Startup is done, and recovery is genuinely still in flight (not
        # merely skipped) -- that is what makes this assertion non-vacuous.
        await asyncio.wait_for(recovery_entered.wait(), timeout=5)
        assert not app.state.startup_task.done()
    finally:
        release_recovery.set()
        # Only unwind a lifespan that actually started. On a regression the
        # wait_for above raises TimeoutError with the generator suspended
        # mid-startup, and calling __aexit__ on it would hang the suite
        # instead of reporting a clean failure.
        if started:
            await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_recovery_precedes_boot_reset_and_dispatch_tick(
    neutralised_startup, monkeypatch
):
    """Moving recovery off the startup path must not reorder it past the
    autodispatch boot-reset: the reset force-disables the very flag recovery
    reads, so running it first would turn recovery into a silent no-op."""
    import app.kanban.dispatch as kanban_dispatch
    import app.kanban.session_recovery as session_recovery

    order: list[str] = []

    async def _recovery():
        order.append("recovery")
        return 0

    async def _reset(_session):
        order.append("boot_reset")
        return False

    monkeypatch.setattr(session_recovery, "recover_interrupted_sessions", _recovery)
    monkeypatch.setattr(kanban_dispatch, "reset_autodispatch_for_boot", _reset)

    calls = neutralised_startup
    await main_module._recover_and_start_dispatch()
    order.append("dispatch_armed" if "schedule_kanban_dispatch" in calls else "unarmed")

    assert order == ["recovery", "boot_reset", "dispatch_armed"]


@pytest.mark.asyncio
async def test_recovery_failure_still_arms_the_dispatch_tick(
    neutralised_startup, monkeypatch
):
    """A transient recovery error must not leave auto-dispatch permanently
    dead -- each step is individually best-effort."""
    import app.kanban.session_recovery as session_recovery

    async def _boom():
        raise RuntimeError("tmux unavailable")

    monkeypatch.setattr(session_recovery, "recover_interrupted_sessions", _boom)

    calls = neutralised_startup
    await main_module._recover_and_start_dispatch()

    assert "schedule_kanban_dispatch" in calls
