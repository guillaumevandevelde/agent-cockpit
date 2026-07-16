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


def test_record_rejection_log_uses_cause_aware_message(monkeypatch, caplog):
    """The legacy ``record()`` rejection log used the misleading
    "Memory: X% used, YMB available" pattern regardless of cause. The new
    format (via ``build_limit_message``) makes the counter-ceiling vs
    memory-ceiling distinction visible from the log line alone."""
    import logging

    import app.services.scheduling.session_registry as mod

    monkeypatch.setattr(mod, "_list_live_tmux_pane_ids", lambda: set())
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


def test_slot_breakdown_reports_tmux_and_external_split():
    """slot_breakdown separates tmux-backed sessions from external reservations
    so a leak in either bucket is visible from the message alone."""
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
    pane IDs are still alive — a leak (zombie panes) shows up here."""
    import app.services.scheduling.session_registry as mod

    monkeypatch.setattr(
        mod, "_list_live_tmux_pane_ids",
        lambda: {"%1", "%3"},  # %1 alive, %2 dead
    )
    reg = SessionRegistry(max_sessions=10)
    reg.record("SessionStart", session_id="s1", cwd="/p", tmux_pane="%1")
    reg.record("SessionStart", session_id="s2", cwd="/p", tmux_pane="%2")
    reg.record("SessionStart", session_id="s3", cwd="/p", tmux_pane="%3")
    breakdown = reg.slot_breakdown()
    assert breakdown["tmux_total"] == 3
    assert breakdown["tmux_live"] == 2
    assert breakdown["tmux_dead"] == 1


def test_slot_breakdown_marks_live_count_unknown_when_tmux_unreachable(monkeypatch):
    """When tmux is unreachable, the breakdown reports None for live/dead
    counts (not -1, not a crash) so the message can degrade to 'unknown'."""
    import app.services.scheduling.session_registry as mod

    monkeypatch.setattr(mod, "_list_live_tmux_pane_ids", lambda: None)
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

    # Comfortable memory (the actual scenario from bevinding 5: 13.5GB free)
    monkeypatch.setattr(mod, "_list_live_tmux_pane_ids", lambda: {"%1", "%2", "%3"})
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

    monkeypatch.setattr(mod, "_list_live_tmux_pane_ids", lambda: set())
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
    from the message alone — that's the whole point of bevinding 5."""
    import app.services.scheduling.session_registry as mod

    # Three stored panes, only one still alive — typical leak shape.
    monkeypatch.setattr(mod, "_list_live_tmux_pane_ids", lambda: {"%3"})
    monkeypatch.setattr(mod, "get_memory_status_cached", _fake_memory_status(
        usage_percent=0.10, available_mb=16000, is_critical=False,
        estimated_max_sessions=120,
    ))
    reg = SessionRegistry(max_sessions=5)
    reg.record("SessionStart", session_id="s1", cwd="/p", tmux_pane="%1")  # dead
    reg.record("SessionStart", session_id="s2", cwd="/p", tmux_pane="%2")  # dead
    reg.record("SessionStart", session_id="s3", cwd="/p", tmux_pane="%3")  # live
    reg.record("SessionStart", session_id="s4", cwd="/p", tmux_pane="%4")  # dead
    reg.record("SessionStart", session_id="s5", cwd="/p", tmux_pane="%5")  # dead

    msg = reg.build_limit_message()

    # Both halves of the breakdown surface — operators can see "1 live, 4
    # phantom" and immediately suspect a leak.
    assert "1 live" in msg
    assert "4 phantom" in msg or "4 dead" in msg


def test_list_live_tmux_pane_ids_parses_pane_ids():
    """The helper parses tmux's `list-panes -a -F '#{pane_id}'` output and
    returns a set of pane IDs. Validated directly with a fake CompletedProcess
    so we don't need a live tmux server."""
    import subprocess
    from unittest.mock import MagicMock

    from app.services.scheduling.session_registry import _list_live_tmux_pane_ids

    fake = MagicMock(spec=subprocess.CompletedProcess)
    fake.returncode = 0
    fake.stdout = "%1\n%2\n%3\n"

    import app.services.scheduling.session_registry as mod

    mod.subprocess.run = MagicMock(return_value=fake)
    try:
        result = _list_live_tmux_pane_ids()
        assert result == {"%1", "%2", "%3"}
    finally:
        # Reset to whatever was there before — the import below this test
        # doesn't matter since the test is the last one to touch this helper.
        pass


def test_list_live_tmux_pane_ids_returns_none_when_tmux_missing():
    """If `tmux` isn't installed or returns an error, the helper returns None
    rather than raising — the message builder needs to degrade gracefully."""
    import subprocess
    from unittest.mock import MagicMock

    from app.services.scheduling.session_registry import _list_live_tmux_pane_ids

    fake = MagicMock(spec=subprocess.CompletedProcess)
    fake.returncode = 1
    fake.stdout = "tmux: command not found\n"

    import app.services.scheduling.session_registry as mod
    original = mod.subprocess.run
    mod.subprocess.run = MagicMock(return_value=fake)
    try:
        assert _list_live_tmux_pane_ids() is None
    finally:
        mod.subprocess.run = original


def test_list_live_tmux_pane_ids_returns_none_on_timeout():
    """If `tmux list-panes` hangs (server overloaded / wedged), the helper
    times out and returns None rather than blocking the spawn gate."""
    import subprocess
    from unittest.mock import MagicMock

    import app.services.scheduling.session_registry as mod
    from app.services.scheduling.session_registry import _list_live_tmux_pane_ids
    original = mod.subprocess.run
    mod.subprocess.run = MagicMock(
        side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=2),
    )
    try:
        assert _list_live_tmux_pane_ids() is None
    finally:
        mod.subprocess.run = original
