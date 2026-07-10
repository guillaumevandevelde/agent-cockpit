import pytest

from app.services.scheduling.session_registry import SessionRegistry


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


def test_external_reservations_count_toward_session_total():
    # Sandcastle runs have no tmux pane but still consume memory, so they must
    # count against the shared session budget.
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
