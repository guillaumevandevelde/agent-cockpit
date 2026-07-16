from types import SimpleNamespace

import pytest

from app.services.scheduling.session_registry import SessionRegistry


def _fake_memory_status(*, usage_percent: float, available_mb: float,
                        is_critical: bool, estimated_max_sessions: int):
    """Return a stub for ``get_memory_status_cached`` matching the real
    MemoryStatus interface the message builder reads (usage_percent,
    available_bytes, is_critical). Patches the consumer's namespace, NOT
    the source module — see CLAUDE.md test-doubles convention."""
    return lambda: SimpleNamespace(
        usage_percent=usage_percent,
        available_bytes=int(available_mb * 1024 * 1024),
        is_critical=is_critical,
        estimated_max_sessions=estimated_max_sessions,
    )


def test_pane_mapping_and_idle_transitions():
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert reg.pane_for("s1") == "%3"
    assert reg.is_idle("s1") is False          # SessionStart => busy
    reg.record("Stop", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert reg.is_idle("s1") is True
    reg.record("UserPromptSubmit", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert reg.is_idle("s1") is False


def test_pane_kept_when_event_has_no_pane():
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%3")
    reg.record("Stop", session_id="s1", cwd="/proj", tmux_pane=None)
    assert reg.pane_for("s1") == "%3"


def test_unknown_session_is_not_idle():
    reg = SessionRegistry()
    assert reg.is_idle("nope") is False
    assert reg.pane_for("nope") is None


def test_session_end_frees_slot():
    """The acceptance scenario from the card: SessionStart occupies a slot,
    SessionEnd releases it immediately -- no waiting for the next
    reconciliation sweep."""
    reg = SessionRegistry(max_sessions=1)
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert reg.session_count == 1
    assert reg.pane_for("s1") == "%3"
    assert reg.can_add_session() is False

    reg.record("SessionEnd", session_id="s1", cwd="/proj", tmux_pane="%3")

    assert reg.session_count == 0
    assert reg.pane_for("s1") is None
    assert reg.is_idle("s1") is False
    assert reg.can_add_session() is True


def test_session_end_bypasses_session_limit():
    """Releasing a slot must never itself be rejected by the limit check --
    a full registry (session_count == effective_max_sessions) would otherwise
    reject the very SessionEnd that's supposed to free it."""
    reg = SessionRegistry(max_sessions=1)
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert reg.can_add_session() is False

    result = reg.record("SessionEnd", session_id="s1", cwd="/proj", tmux_pane="%3")

    assert result is True
    assert reg.session_count == 0


def test_session_end_for_unknown_session_is_a_noop():
    reg = SessionRegistry()
    result = reg.record("SessionEnd", session_id="never-started", cwd="/proj")
    assert result is True
    assert reg.pane_for("never-started") is None


def test_external_reservations_count_toward_session_total(monkeypatch):
    # Sandcastle runs have no tmux pane but still consume memory, so they must
    # count against the shared session budget.
    from app.services.scheduling import session_registry as mod

    # ``can_add_session`` now reconciles ``_panes`` against tmux before the
    # check (self-healing for crashes/kill-9/bridge-test-spawn leaks). Mock
    # tmux so the %1 pane stays alive — this test is about the
    # reservation arithmetic, not reconciliation.
    fake = _FakeTmux(live_panes={"%1"})
    monkeypatch.setattr(mod.subprocess, "run", fake)
    reg = SessionRegistry(max_sessions=3)
    assert reg.session_count == 0
    reg.reserve_external("k-a-0001")
    reg.reserve_external("k-b-0002")
    assert reg.session_count == 2
    # Mixed with a tmux session:
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%1")
    assert reg.session_count == 3
    assert reg.can_add_session() is False


def test_external_reservations_are_released_and_idempotent():
    reg = SessionRegistry(max_sessions=2)
    reg.reserve_external("k-a-0001")
    reg.reserve_external("k-a-0001")  # same key twice -> counts once
    assert reg.session_count == 1
    reg.release_external("k-a-0001")
    assert reg.session_count == 0
    reg.release_external("k-a-0001")  # releasing an unknown key is a no-op
    assert reg.session_count == 0


@pytest.mark.asyncio
async def test_wait_until_idle_returns_immediately_when_idle():
    reg = SessionRegistry()
    reg.record("Stop", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert await reg.wait_until_idle("s1", timeout_s=0.1) is True


@pytest.mark.asyncio
async def test_wait_until_idle_times_out_when_busy():
    reg = SessionRegistry()
    reg.record("UserPromptSubmit", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert await reg.wait_until_idle("s1", timeout_s=0.1) is False


@pytest.mark.asyncio
async def test_wait_until_idle_wakes_on_stop():
    import asyncio
    reg = SessionRegistry()
    reg.record("UserPromptSubmit", session_id="s1", cwd="/proj", tmux_pane="%3")

    async def fire_stop():
        await asyncio.sleep(0.02)
        reg.record("Stop", session_id="s1", cwd="/proj", tmux_pane="%3")

    asyncio.create_task(fire_stop())
    assert await reg.wait_until_idle("s1", timeout_s=1.0) is True


# --- hook-event gap tracking (used by the dispatch reaper to detect stuck
# 429-blocked sessions that never initialised hooks) -------------------


class _Clock:
    """Test clock with a controllable monotonic source for spawn-tracking tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_mark_spawned_tracks_session(monkeypatch):
    """mark_spawned registers a session; get_stuck_sessions surfaces it once
    the spawn age exceeds the timeout and the session is still alive in tmux."""
    from app.services.scheduling import session_registry as mod
    clock = _Clock()
    monkeypatch.setattr(mod.time, "monotonic", clock)
    reg = SessionRegistry()
    reg.mark_spawned("k-test-0001")

    # Recently spawned and the reaper shouldn't call it stuck yet
    assert reg.get_stuck_sessions({"k-test-0001"}, timeout_s=120) == set()
    # Advance past the timeout — now stuck
    clock.advance(130)
    assert reg.get_stuck_sessions({"k-test-0001"}, timeout_s=120) == {"k-test-0001"}


def test_record_clears_stuck_flag(monkeypatch):
    """A hook event arriving from the spawned session's cwd clears the
    stuck flag — the spawn is no longer waiting for hooks to initialise."""
    from app.services.scheduling import session_registry as mod
    clock = _Clock()
    monkeypatch.setattr(mod.time, "monotonic", clock)
    reg = SessionRegistry()
    reg.mark_spawned("k-test-0002")
    clock.advance(200)
    # Stuck before any hook arrived
    assert reg.get_stuck_sessions({"k-test-0002"}, timeout_s=120) == {"k-test-0002"}

    # Cwd shape of a dispatched session: <project>/.claude/worktrees/<name>
    reg.record(
        "SessionStart", session_id="s-abc",
        cwd="/home/me/proj/.claude/worktrees/k-test-0002",
        tmux_pane="%5",
    )

    assert reg.get_stuck_sessions({"k-test-0002"}, timeout_s=120) == set()


def test_get_stuck_sessions_ignores_recent(monkeypatch):
    """A freshly-spawned session is not stuck, even before its first hook.
    A real `claude` startup takes ~10-30s before hooks fire; the reaper must
    not race that."""
    from app.services.scheduling import session_registry as mod
    clock = _Clock()
    monkeypatch.setattr(mod.time, "monotonic", clock)
    reg = SessionRegistry()
    reg.mark_spawned("k-test-0003")
    # 30 seconds in: still well under the 120s default timeout
    clock.advance(30)
    assert reg.get_stuck_sessions({"k-test-0003"}, timeout_s=120) == set()


def test_get_stuck_sessions_ignores_unknown(monkeypatch):
    """A name present in live_sessions but never recorded as spawned is not
    stuck — the registry simply doesn't know about it. This avoids false
    positives against hand-launched `claude` sessions and pre-existing tmux
    sessions that the dispatcher never tracked."""
    from app.services.scheduling import session_registry as mod
    clock = _Clock()
    monkeypatch.setattr(mod.time, "monotonic", clock)
    reg = SessionRegistry()
    clock.advance(10_000)
    assert reg.get_stuck_sessions({"someone-elses-session"}, timeout_s=120) == set()


def test_get_stuck_sessions_ignores_dead_tmux(monkeypatch):
    """If the tmux session is gone, the reaper already has a separate path
    (Dead-session reaping) — this method only flags sessions that are still
    alive in tmux. A dead session must NOT show up here, otherwise the reaper
    would try to send signals to it."""
    from app.services.scheduling import session_registry as mod
    clock = _Clock()
    monkeypatch.setattr(mod.time, "monotonic", clock)
    reg = SessionRegistry()
    reg.mark_spawned("k-test-0004")
    clock.advance(200)
    # Empty live set = session already gone from tmux
    assert reg.get_stuck_sessions(set(), timeout_s=120) == set()


def test_clear_spawn_removes_session(monkeypatch):
    """clear_spawn forgets the session so the reaper stops reporting it stuck
    (used after the reaper itself kills a stuck session, or after a graceful
    shutdown, so the registry doesn't carry zombie tracking forever)."""
    from app.services.scheduling import session_registry as mod
    clock = _Clock()
    monkeypatch.setattr(mod.time, "monotonic", clock)
    reg = SessionRegistry()
    reg.mark_spawned("k-test-0005")
    clock.advance(200)
    assert reg.get_stuck_sessions({"k-test-0005"}, timeout_s=120) == {"k-test-0005"}

    reg.clear_spawn("k-test-0005")
    assert reg.get_stuck_sessions({"k-test-0005"}, timeout_s=120) == set()
    # Idempotent: clearing twice is fine
    reg.clear_spawn("k-test-0005")
    assert reg.get_stuck_sessions({"k-test-0005"}, timeout_s=120) == set()


def test_record_ignores_non_kanban_cwd(monkeypatch):
    """Hook events from sessions that did not match the dispatched
    cwd shape (e.g. an arbitrary `claude` session running in the project root)
    must not trip the spawn-tracking machinery — they would silently
    mark a different session as "received hooks" via the session-name
    path, which we don't want."""
    from app.services.scheduling import session_registry as mod
    clock = _Clock()
    monkeypatch.setattr(mod.time, "monotonic", clock)
    reg = SessionRegistry()
    reg.mark_spawned("k-test-0006")
    clock.advance(200)
    # Plain project root: not our dispatched cwd shape
    reg.record(
        "SessionStart", session_id="s-other",
        cwd="/home/me/proj", tmux_pane="%9",
    )
    # Session k-test-0006 still appears stuck — the unrelated record above
    # must not have cleared anything.
    assert reg.get_stuck_sessions({"k-test-0006"}, timeout_s=120) == {"k-test-0006"}


def test_record_ignores_unknown_session_name(monkeypatch):
    """A hook event for a cwd whose tail is not in the spawn registry must
    be a no-op for stuck tracking — typical when a user manually runs
    `claude` in a project root, which still hits the hook-event endpoint
    but is not one of our dispatched sessions."""
    from app.services.scheduling import session_registry as mod
    clock = _Clock()
    monkeypatch.setattr(mod.time, "monotonic", clock)
    reg = SessionRegistry()
    # No mark_spawned — registry knows nothing about k-manual-0099
    reg.record(
        "SessionStart", session_id="s-abc",
        cwd="/home/me/proj/.claude/worktrees/k-manual-0099",
        tmux_pane="%7",
    )
    # Nothing to be stuck on (registry empty), and we did NOT crash.
    assert reg.get_stuck_sessions({"k-manual-0099"}, timeout_s=120) == set()


# --- self-healing reconciliation against `tmux list-panes` -----------------
#
# The registry used to be append-only on ``_panes``: any claude session that
# ever sent a hook event permanently occupied a slot until backend restart.
# A crash, kill -9, reboot, or bridge-test spawn (no kill path) would leak.
# These tests pin the self-healing reconciliation that fixes that.


class _FakeTmux:
    """Records ``subprocess.run`` calls and returns a controllable result set.

    Tests set ``live_panes`` to the set of pane_ids tmux should report alive
    on the next call; the fake shells those out as tmux's stdout. Tests can
    also raise on demand to exercise tmux-unavailable branches.
    """

    def __init__(self, live_panes: set[str] | None = None) -> None:
        self.live_panes = live_panes or set()
        self.calls: list[tuple] = []
        self.raise_on_call: type[Exception] | None = None
        self.returncode: int = 0

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raise_on_call is not None:
            raise self.raise_on_call
        if self.returncode != 0:
            stdout, stderr = "", "tmux: server not running\n"
        else:
            stdout = "\n".join(sorted(self.live_panes)) + "\n"
            stderr = ""
        return _FakeResult(stdout=stdout, stderr=stderr, returncode=self.returncode)


class _FakeResult:
    def __init__(self, *, stdout: str, stderr: str, returncode: int) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_reconcile_removes_dead_panes(monkeypatch):
    """A pane in ``_panes`` that no longer exists in tmux is removed —
    covers crashes, kill -9, reboots, and bridge-test spawns."""
    from app.services.scheduling import session_registry as mod

    fake = _FakeTmux(live_panes={"%2"})  # only %2 is alive in tmux
    monkeypatch.setattr(mod.subprocess, "run", fake)
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s-alive", cwd="/proj", tmux_pane="%2")
    reg.record("SessionStart", session_id="s-dead-1", cwd="/proj", tmux_pane="%1")
    reg.record("SessionStart", session_id="s-dead-2", cwd="/proj", tmux_pane="%3")
    reg.record("Stop", session_id="s-dead-1", cwd="/proj", tmux_pane="%1")

    removed = reg.cleanup_stale_sessions()
    assert removed == 2
    assert reg.pane_for("s-dead-1") is None
    assert reg.pane_for("s-dead-2") is None
    assert reg.pane_for("s-alive") == "%2"
    # Idle flag cleared along with the pane mapping.
    assert reg.is_idle("s-dead-1") is False
    assert reg.is_idle("s-dead-2") is False
    assert reg.is_idle("s-alive") is False


def test_reconcile_preserves_live_panes(monkeypatch):
    """A pane in ``_panes`` that tmux still reports alive must NOT be removed,
    because ``pane_for`` feeds the scheduled-messages-inject pipeline and
    must keep its mapping intact while the session is live."""
    from app.services.scheduling import session_registry as mod

    live = {"%5", "%7", "%9"}
    fake = _FakeTmux(live_panes=live)
    monkeypatch.setattr(mod.subprocess, "run", fake)
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s-a", cwd="/proj", tmux_pane="%5")
    reg.record("SessionStart", session_id="s-b", cwd="/proj", tmux_pane="%7")
    reg.record("SessionStart", session_id="s-c", cwd="/proj", tmux_pane="%9")

    removed = reg.cleanup_stale_sessions()
    assert removed == 0
    assert reg.pane_for("s-a") == "%5"
    assert reg.pane_for("s-b") == "%7"
    assert reg.pane_for("s-c") == "%9"


def test_reconcile_preserves_external_reservations(monkeypatch):
    """External reservations (sandcastle runs, no tmux pane) must NOT be
    touched by reconciliation — they don't correspond to a tmux pane at all."""
    from app.services.scheduling import session_registry as mod

    fake = _FakeTmux(live_panes=set())  # no panes alive at all
    monkeypatch.setattr(mod.subprocess, "run", fake)
    reg = SessionRegistry()
    reg.reserve_external("k-sandcastle-001")
    reg.reserve_external("k-sandcastle-002")

    removed = reg.cleanup_stale_sessions()
    assert removed == 0
    assert reg.session_count == 2  # external slots untouched
    reg.release_external("k-sandcastle-001")
    assert reg.session_count == 1


def test_reconcile_empty_registry_skips_tmux(monkeypatch):
    """When ``_panes`` is empty there is nothing to reconcile, so we must
    not even shell out to tmux — that's wasted work on every hook event
    for sessions that have not yet populated the registry."""
    from app.services.scheduling import session_registry as mod

    fake = _FakeTmux(live_panes={"%5"})
    monkeypatch.setattr(mod.subprocess, "run", fake)
    reg = SessionRegistry()

    removed = reg.cleanup_stale_sessions()
    assert removed == 0
    assert fake.calls == []  # never invoked subprocess


def test_reconcile_throttles_repeated_calls(monkeypatch):
    """``_maybe_reconcile`` skips the tmux round-trip if less than
    ``_RECONCILE_INTERVAL_S`` has elapsed since the last successful call.
    The first call shells out; subsequent calls inside the window are a
    cheap no-op. Past the window, the next call shells out again."""
    import app.services.scheduling.session_registry as reg_mod
    from app.services.scheduling import session_registry as mod

    clock = _Clock()
    monkeypatch.setattr(mod.time, "monotonic", clock)
    fake = _FakeTmux(live_panes={"%1"})
    monkeypatch.setattr(mod.subprocess, "run", fake)
    monkeypatch.setattr(reg_mod, "_RECONCILE_INTERVAL_S", 5.0)

    reg = SessionRegistry(max_sessions=10)
    # Seed _panes directly — record() would itself trigger reconcile, which
    # would taint the throttle accounting we want to assert on.
    reg._panes["s1"] = "%1"
    reg._panes["s2"] = "%2"  # stale

    # First can_add_session triggers tmux; clears s2.
    assert reg.can_add_session() is True
    assert len(fake.calls) == 1
    assert reg.pane_for("s2") is None

    # Within the throttle window: no further tmux calls.
    clock.advance(1)
    assert reg.can_add_session() is True
    assert len(fake.calls) == 1

    clock.advance(3)
    assert reg.can_add_session() is True
    assert len(fake.calls) == 1

    # Past the throttle: tmux called again.
    clock.advance(2)
    assert reg.can_add_session() is True
    assert len(fake.calls) == 2


def test_reconcile_force_bypasses_throttle(monkeypatch):
    """The public ``cleanup_stale_sessions`` API must always run a fresh
    reconciliation — explicit callers want the latest state, not a cached
    'nothing to do' from 3 seconds ago."""
    from app.services.scheduling import session_registry as mod

    clock = _Clock()
    monkeypatch.setattr(mod.time, "monotonic", clock)
    fake = _FakeTmux(live_panes={"%1"})
    monkeypatch.setattr(mod.subprocess, "run", fake)

    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%2")

    # First call: tmux roundtrip, removes s1.
    assert reg.cleanup_stale_sessions() == 1
    assert len(fake.calls) == 1

    # Re-add another stale session, then call cleanup_stale_sessions again
    # *within* the throttle window — it must still shell out (force=True).
    reg.record("SessionStart", session_id="s2", cwd="/proj", tmux_pane="%3")
    clock.advance(0.5)
    assert reg.cleanup_stale_sessions() == 1
    assert len(fake.calls) == 2


def test_reconcile_survives_tmux_not_installed(monkeypatch):
    """If tmux is not installed (FileNotFoundError) the reconciliation must
    be a safe no-op — never raise into a hook handler or a spawn path."""
    from app.services.scheduling import session_registry as mod

    def raise_fnf(*args, **kwargs):
        raise FileNotFoundError("tmux: command not found")

    monkeypatch.setattr(mod.subprocess, "run", raise_fnf)
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%5")

    assert reg.cleanup_stale_sessions() == 0  # conservative: don't remove
    assert reg.pane_for("s1") == "%5"  # entry preserved
    # can_add_session also doesn't blow up.
    assert reg.can_add_session() is True


def test_reconcile_survives_tmux_timeout(monkeypatch):
    """A hung tmux (subprocess.TimeoutExpired) must not block the hook path."""
    import subprocess

    from app.services.scheduling import session_registry as mod

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["tmux"], timeout=5)

    monkeypatch.setattr(mod.subprocess, "run", raise_timeout)
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%5")

    assert reg.cleanup_stale_sessions() == 0
    assert reg.pane_for("s1") == "%5"


def test_reconcile_survives_tmux_error(monkeypatch):
    """A non-zero returncode from tmux (e.g. server stopped between calls)
    must also be a safe no-op."""
    from app.services.scheduling import session_registry as mod

    fake = _FakeTmux(live_panes=set())
    fake.returncode = 1
    fake.stderr = "tmux: no server running on /tmp/tmux-1000/default\n"
    monkeypatch.setattr(mod.subprocess, "run", fake)

    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%5")

    assert reg.cleanup_stale_sessions() == 0
    assert reg.pane_for("s1") == "%5"


def test_can_add_session_returns_true_after_stale_sessions_cleaned(monkeypatch):
    """The acceptance scenario from the card: N sessions that started, stopped,
    and got killed (or whose tmux panes simply disappeared) all free their
    slots, and ``can_add_session`` becomes True again — without a backend
    restart. This was the original bug: ``_panes`` was append-only."""
    from app.services.scheduling import session_registry as mod

    # During setup every recorded pane is alive in tmux, so record() (which
    # calls can_add_session -> reconcile) doesn't prune anything prematurely.
    # The "kill" scenario flips live_panes to only %5 just before the check.
    fake = _FakeTmux(live_panes={f"%{i + 1}" for i in range(5)})
    monkeypatch.setattr(mod.subprocess, "run", fake)

    reg = SessionRegistry(max_sessions=5)
    # Five sessions record their hooks; everything is alive in tmux.
    for i in range(5):
        sid = f"s-{i:03d}"
        pane = f"%{i + 1}"
        reg.record("SessionStart", session_id=sid, cwd="/proj", tmux_pane=pane)

    assert reg.session_count == 5
    assert reg.can_add_session() is False  # full

    # Now the four other tmux panes disappear (kill -9 / crash /
    # bridge-test spawn). Only %5 stays alive.
    fake.live_panes = {"%5"}
    # Reconciliation must free 4 slots and let a new session be added.
    reg.cleanup_stale_sessions()
    assert reg.session_count == 1
    assert reg.can_add_session() is True

    # The surviving pane still maps to its session_id (pane_for() contract).
    assert reg.pane_for("s-004") == "%5"
    # And a fresh record() for a new session fits — the limit is now honest.
    reg.record("SessionStart", session_id="s-new", cwd="/proj", tmux_pane="%5")
    assert reg.session_count == 2
    assert reg.can_add_session() is True


def test_session_count_drops_only_after_reconciliation(monkeypatch):
    """``session_count`` is a pure read; it does not shell out to tmux. The
    stale entries only disappear once reconciliation has run (lazily via
    ``can_add_session`` or explicitly via ``cleanup_stale_sessions``)."""
    from app.services.scheduling import session_registry as mod

    fake = _FakeTmux(live_panes=set())
    monkeypatch.setattr(mod.subprocess, "run", fake)
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%5")
    assert reg.session_count == 1
    # Bare property access must not trigger tmux.
    assert fake.calls == []
    # Once we hit can_add_session, the lazy reconcile runs.
    reg.can_add_session()
    assert reg.session_count == 0
    assert len(fake.calls) == 1


def test_record_overwrites_stale_pane_for_reused_session_id(monkeypatch):
    """If a session_id comes back with a new tmux_pane (e.g. after a
    crash + manual restart), ``record`` must update the mapping to the new
    pane — and the stale pane_id must not be retained."""
    from app.services.scheduling import session_registry as mod

    fake = _FakeTmux(live_panes={"%9"})
    monkeypatch.setattr(mod.subprocess, "run", fake)
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s-reused", cwd="/proj", tmux_pane="%3")
    assert reg.pane_for("s-reused") == "%3"
    reg.record("SessionStart", session_id="s-reused", cwd="/proj", tmux_pane="%9")
    assert reg.pane_for("s-reused") == "%9"
    # Cleanup removes it because %3 is gone from tmux; %9 is kept.
    assert reg.cleanup_stale_sessions() == 0  # %9 still alive
    assert reg.pane_for("s-reused") == "%9"


def test_reconcile_uses_subprocess_timeout(monkeypatch):
    """``_live_pane_ids`` must pass a ``timeout`` kwarg to ``subprocess.run``
    so a hung tmux never wedges the hook path."""
    from app.services.scheduling import session_registry as mod

    fake = _FakeTmux(live_panes=set())
    monkeypatch.setattr(mod.subprocess, "run", fake)
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%5")
    reg.cleanup_stale_sessions()
    assert len(fake.calls) == 1
    _args, kwargs = fake.calls[0]
    assert kwargs.get("timeout") is not None
    assert kwargs["timeout"] <= 10  # sane bound, not None / 0


def test_record_rejection_log_uses_cause_aware_message(monkeypatch, caplog):
    """The legacy ``record()`` rejection log used the misleading
    "Memory: X% used, YMB available" pattern regardless of cause. The new
    format (via ``build_limit_message``) makes the counter-ceiling vs
    memory-ceiling distinction visible from the log line alone."""
    import logging

    import app.services.scheduling.session_registry as mod

    # All recorded panes stay "alive" throughout, so the self-healing
    # reconciliation triggered by can_add_session() never removes anything —
    # this test cares about the rejection message, not the leak-detection
    # path (see test_limit_message_surfaces_zombie_pane_count for that).
    fake = _FakeTmux(live_panes={"%1", "%2"})
    monkeypatch.setattr(mod.subprocess, "run", fake)
    monkeypatch.setattr(mod, "get_memory_status_cached", lambda: SimpleNamespace(
        usage_percent=0.15, available_bytes=13562 * 1024 * 1024,
        is_critical=False, estimated_max_sessions=107,
    ))

    reg = SessionRegistry(max_sessions=2)
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%1")
    reg.record("SessionStart", session_id="s2", cwd="/proj", tmux_pane="%2")

    with caplog.at_level(logging.WARNING, logger=mod.logger.name):
        result = reg.record(
            "SessionStart", session_id="s3",
            cwd="/proj", tmux_pane="%3",
        )

    assert result is False
    assert any(
        "counter ceiling" in rec.message for rec in caplog.records
    ), f"expected cause-aware message in log; got: {[r.message for r in caplog.records]}"
    # The legacy misleading pattern MUST NOT appear as cause.
    assert not any(
        "Memory: 15% used, 13562MB available" in rec.message
        for rec in caplog.records
    )


# --- limit diagnostics: distinguishing counter ceiling from memory pressure ---

# These tests pin down the spawn-gate error message (see bevinding 5 in
# docs/cockpit/spawn-test-bridge-sessions-analyse.md): the legacy message
# blamed memory whenever a counter ceiling was actually the binding constraint,
# steering every diagnosis toward "more RAM / less parallelism" instead of the
# real cause. The new helpers and message must make the cause unambiguous and
# surface a counter leak (slots held without a live tmux pane) from the log
# line alone.


def test_limit_cause_is_counter_ceiling_when_override_set():
    """An explicit _max_sessions_override is a counter ceiling — NOT memory."""
    reg = SessionRegistry(max_sessions=5)
    assert reg.limit_cause() == "counter_ceiling"


def test_limit_cause_is_memory_ceiling_without_override():
    """Without an override, the limit is derived from memory_monitor."""
    reg = SessionRegistry()
    assert reg.limit_cause() == "memory_ceiling"


def test_slot_breakdown_reports_tmux_and_external_split(monkeypatch):
    """slot_breakdown separates tmux-backed sessions from external reservations
    so a leak in either bucket is visible from the message alone."""
    import app.services.scheduling.session_registry as mod

    # Hermetic: avoid a real subprocess call to the host's tmux server.
    fake = _FakeTmux(live_panes={"%1", "%2"})
    monkeypatch.setattr(mod.subprocess, "run", fake)
    reg = SessionRegistry(max_sessions=10)
    reg.reserve_external("k-ex-001")
    reg.record("SessionStart", session_id="s1", cwd="/p", tmux_pane="%1")
    reg.record("SessionStart", session_id="s2", cwd="/p", tmux_pane="%2")
    breakdown = reg.slot_breakdown()
    assert breakdown["tmux_total"] == 2
    assert breakdown["external_total"] == 1
    assert breakdown["session_count"] == 3
    assert breakdown["effective_max"] == 10


def test_slot_breakdown_includes_live_tmux_count_when_tmux_reachable(monkeypatch):
    """When tmux is reachable, the breakdown reports how many of the stored
    pane IDs are still alive — a leak (zombie panes) shows up here.

    Seeds ``_panes`` directly (bypassing ``record()``) so the result is
    deterministic regardless of the self-healing reconcile's real-time
    throttle window — see test_limit_message_surfaces_zombie_pane_count for
    why that matters."""
    import app.services.scheduling.session_registry as mod

    fake = _FakeTmux(live_panes={"%1", "%3"})  # %1 alive, %2 dead, %3 alive
    monkeypatch.setattr(mod.subprocess, "run", fake)
    reg = SessionRegistry(max_sessions=10)
    reg._panes["s1"] = "%1"
    reg._panes["s2"] = "%2"
    reg._panes["s3"] = "%3"
    breakdown = reg.slot_breakdown()
    assert breakdown["tmux_total"] == 3
    assert breakdown["tmux_live"] == 2
    assert breakdown["tmux_dead"] == 1


def test_slot_breakdown_marks_live_count_unknown_when_tmux_unreachable(monkeypatch):
    """When tmux is unreachable, the breakdown reports None for live/dead
    counts (not -1, not a crash) so the message can degrade to 'unknown'."""
    import app.services.scheduling.session_registry as mod

    def raise_fnf(*args, **kwargs):
        raise FileNotFoundError("tmux: command not found")

    monkeypatch.setattr(mod.subprocess, "run", raise_fnf)
    reg = SessionRegistry(max_sessions=10)
    reg.record("SessionStart", session_id="s1", cwd="/p", tmux_pane="%1")
    breakdown = reg.slot_breakdown()
    assert breakdown["tmux_total"] == 1
    assert breakdown["tmux_live"] is None
    assert breakdown["tmux_dead"] is None


def test_limit_message_counter_ceiling_does_not_blame_memory(monkeypatch):
    """The spawn-gate message for a counter ceiling must NOT lead with memory
    figures (the original symptom — operators chase RAM when the real cause is
    the explicit counter). Memory figures are only present when memory is
    actually the binding constraint."""
    import app.services.scheduling.session_registry as mod

    # Comfortable memory (the actual scenario from bevinding 5: 13.5GB free).
    # All three panes stay "alive" so the self-healing reconcile triggered
    # by can_add_session() never removes any of them.
    fake = _FakeTmux(live_panes={"%1", "%2", "%3"})
    monkeypatch.setattr(mod.subprocess, "run", fake)
    # Patch where the consumer looks — `session_registry` did
    # `from app.services.memory_monitor import get_memory_status_cached`, so
    # the binding lives in `mod` namespace, not the source module.
    # (See CLAUDE.md test-doubles convention.)
    monkeypatch.setattr(mod, "get_memory_status_cached", _fake_memory_status(
        usage_percent=0.15, available_mb=13562, is_critical=False,
        estimated_max_sessions=107,
    ))
    reg = SessionRegistry(max_sessions=5)  # counter ceiling, not memory
    reg.record("SessionStart", session_id="s1", cwd="/p", tmux_pane="%1")
    reg.record("SessionStart", session_id="s2", cwd="/p", tmux_pane="%2")
    reg.record("SessionStart", session_id="s3", cwd="/p", tmux_pane="%3")
    reg.reserve_external("k-ex-001")
    reg.reserve_external("k-ex-002")

    msg = reg.build_limit_message()

    # Cause is explicit and unambiguous.
    assert "counter ceiling" in msg
    assert "5/5" in msg  # count vs effective max
    # The legacy misleading "Memory: X% used, YMB available" pattern MUST NOT
    # appear as if memory were the cause. A note about memory being NOT the
    # binding constraint is fine; the legacy cause-presentation is not.
    assert "Memory: 15% used, 13562MB available" not in msg
    assert "Memory:" not in msg or "not the binding constraint" in msg
    # Slot breakdown shows the leak signal: live vs dead panes.
    assert "3 live" in msg
    assert "tmux-backed" in msg
    # External reservations reported separately so they can't be confused
    # with a leaking counter.
    assert "2 external" in msg


def test_limit_message_memory_ceiling_does_show_memory_pressure(monkeypatch):
    """When memory IS the binding constraint, memory figures stay in the
    message because they're the actual cause — the goal isn't to hide memory,
    it's to not blame memory when it's not the cause."""
    import app.services.scheduling.session_registry as mod

    # No tmux fake needed: this scenario only uses reserve_external(), so
    # _panes stays empty and slot_breakdown()'s tmux_total==0 branch never
    # calls out to tmux at all.
    monkeypatch.setattr(mod, "get_memory_status_cached", _fake_memory_status(
        usage_percent=0.92, available_mb=1024, is_critical=True,
        estimated_max_sessions=8,
    ))
    reg = SessionRegistry()  # no override → memory ceiling
    reg.reserve_external("k-ex-001")
    reg.reserve_external("k-ex-002")
    reg.reserve_external("k-ex-003")
    reg.reserve_external("k-ex-004")
    reg.reserve_external("k-ex-005")
    reg.reserve_external("k-ex-006")
    reg.reserve_external("k-ex-007")
    reg.reserve_external("k-ex-008")

    msg = reg.build_limit_message()

    assert "memory ceiling" in msg
    assert "92%" in msg  # cause-relevant memory figure stays
    assert "8/8" in msg
    assert "8 external" in msg


def test_limit_message_surfaces_zombie_pane_count(monkeypatch):
    """A counter leak (slots held without a live tmux pane) MUST be visible
    from the message alone — that's the whole point of bevinding 5.

    Seeds ``_panes`` directly instead of going through ``record()``: the
    self-healing reconcile added alongside this card (triggered by
    ``can_add_session()``, throttled to once per ``_RECONCILE_INTERVAL_S``)
    would otherwise non-deterministically clean up the very zombies this
    test wants to observe, depending on real-time throttle timing. Direct
    seeding keeps the "5 stored, 1 live" shape stable so the only tmux call
    is ``build_limit_message()``'s own direct (unthrottled) probe."""
    import app.services.scheduling.session_registry as mod

    # Five stored panes, only one still alive — typical leak shape.
    fake = _FakeTmux(live_panes={"%3"})
    monkeypatch.setattr(mod.subprocess, "run", fake)
    monkeypatch.setattr(mod, "get_memory_status_cached", _fake_memory_status(
        usage_percent=0.10, available_mb=16000, is_critical=False,
        estimated_max_sessions=120,
    ))
    reg = SessionRegistry(max_sessions=5)
    reg._panes["s1"] = "%1"  # dead
    reg._panes["s2"] = "%2"  # dead
    reg._panes["s3"] = "%3"  # live
    reg._panes["s4"] = "%4"  # dead
    reg._panes["s5"] = "%5"  # dead

    msg = reg.build_limit_message()

    # Both halves of the breakdown surface — operators can see "1 live, 4
    # phantom" and immediately suspect a leak.
    assert "1 live" in msg
    assert "4 phantom" in msg or "4 dead" in msg


# Note: direct unit tests for the tmux-probe helper (parses pane-id output,
# returns None on missing/error/timeout tmux) live with the reconciliation
# tests above as SessionRegistry._live_pane_ids — see
# test_reconcile_survives_tmux_not_installed / _timeout / _error and
# test_reconcile_uses_subprocess_timeout. slot_breakdown() and
# build_limit_message() reuse that same static method (no separate
# module-level helper) so there is no additional surface to test here.
