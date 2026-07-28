"""Tests for CC Bridge pty relay."""
import json
import os
import signal


def test_parse_control_message_resize():
    from app.services.runs.pty_relay import parse_control_message
    msg = json.dumps({"type": "resize", "cols": 120, "rows": 40})
    result = parse_control_message(msg)
    assert result is not None
    assert result["type"] == "resize"
    assert result["cols"] == 120
    assert result["rows"] == 40


def test_parse_control_message_returns_none_for_plain_text():
    from app.services.runs.pty_relay import parse_control_message
    assert parse_control_message("ls -la") is None
    assert parse_control_message("hello world") is None


def test_parse_control_message_returns_none_for_invalid_json():
    from app.services.runs.pty_relay import parse_control_message
    assert parse_control_message("{invalid") is None


def test_parse_control_message_returns_none_for_json_without_type():
    from app.services.runs.pty_relay import parse_control_message
    assert parse_control_message('{"cols": 80}') is None


def test_resize_pty_does_not_raise_on_invalid_fd():
    from app.services.runs.pty_relay import resize_pty
    resize_pty(-1, 24, 80)


# --- regression: scoped cleanup of orphaned relay processes --------------
#
# Kanban card 6069ea8b...: a previous cleanup_orphaned_relays() ran
#   pgrep -f "tmux attach-session"
# machine-wide and SIGTERM'd every match — including relay processes owned
# by OTHER backends and human tmux attach viewers on the same box. The fix
# scopes the cleanup to relay PIDs this backend itself wrote to a pidfile
# under ~/.claude-registry/. See ``backend/app/services/runs/pty_relay.py``
# for the current implementation.


def test_cleanup_orphaned_relays_does_not_machine_wide_pgrep(tmp_path, monkeypatch):
    """cleanup_orphaned_relays must NOT run pgrep -f across the box.

    Regression test for kanban card 6069ea8b...: a machine-wide
    ``pgrep -f "tmux attach-session"`` SIGTERM'd every match on the box,
    including relay processes owned by concurrent backends and human
    ``tmux attach`` viewers. The fix walks the pidfile dir instead.
    """
    from app.services.runs import pty_relay

    # Redirect the pidfile dir so the cleanup walks an empty tmp_path
    monkeypatch.setattr(pty_relay, "_PIDFILE_DIR", tmp_path)

    # Capture every subprocess.run invocation
    run_calls = []
    real_run = pty_relay.subprocess.run

    def fake_run(*args, **kwargs):
        run_calls.append(args[0] if args else kwargs.get("args"))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(pty_relay.subprocess, "run", fake_run)

    pty_relay.cleanup_orphaned_relays()

    # No pgrep at all — not machine-wide, not user-scoped.
    pgrep_calls = [c for c in run_calls if c and "pgrep" in c]
    assert pgrep_calls == [], (
        f"cleanup_orphaned_relays called pgrep machine-wide: {pgrep_calls}"
    )


def test_cleanup_orphaned_relays_signals_pids_from_pidfile_only(tmp_path, monkeypatch):
    """SIGTERM only goes to PIDs listed in the pidfile dir AND whose parent is dead.

    Even when many tmux attach-session processes exist on the host (e.g. a
    human watching a pane, a second backend instance), the cleanup must
    not touch them. The pidfile is the only source of truth for "PIDs this
    backend owns", AND the relay's parent must be dead (otherwise a
    concurrent backend still owns it).
    """
    from app.services.runs import pty_relay

    monkeypatch.setattr(pty_relay, "_PIDFILE_DIR", tmp_path)

    # Drop two pidfiles in the dir: one for a "real" PID we'll target,
    # one for an obviously-dead PID we want cleaned up too.
    owned_pid = 43210
    dead_pid = 11
    dead_ppid_for_owned = 90001
    dead_ppid_for_dead = 90002
    (tmp_path / f"{owned_pid}.pid").write_text("owned-target")
    (tmp_path / f"{dead_pid}.pid").write_text("dead-target")

    # All pidfile PIDs are alive (the liveness probe sees a live process).
    monkeypatch.setattr(pty_relay, "_is_pid_alive", lambda pid: True)

    # The PPID is a previously-running backend whose /proc/<ppid>/status
    # still says "PID = dead_ppid" — it's gone (its PPID is not our PID,
    # and ``os.kill(ppid, 0)`` raises ProcessLookupError below).
    monkeypatch.setattr(pty_relay, "_read_ppid", lambda pid: {
        owned_pid: dead_ppid_for_owned,
        dead_pid: dead_ppid_for_dead,
    }[pid])

    killed_pids = []

    def fake_kill(pid, sig):
        killed_pids.append((pid, sig))
        # PPID liveness probe returns ProcessLookupError (parent is dead).
        if sig == 0:
            raise ProcessLookupError(pid)
        # The SIGTERM target dead_pid (PID 11) is gone for our purposes.
        if pid == dead_pid:
            raise ProcessLookupError(pid)

    monkeypatch.setattr("os.kill", fake_kill)

    pty_relay.cleanup_orphaned_relays()

    # Exactly the two pidfile PIDs were targeted, with SIGTERM.
    assert (owned_pid, signal.SIGTERM) in killed_pids
    assert (dead_pid, signal.SIGTERM) in killed_pids
    # Plus the two PPID liveness probes.
    assert (dead_ppid_for_owned, 0) in killed_pids
    assert (dead_ppid_for_dead, 0) in killed_pids

    # Both pidfiles were swept, dead or alive — they are stale after a
    # backend restart by definition (the in-memory registry is gone).
    assert list(tmp_path.glob("*.pid")) == []


def test_cleanup_orphaned_relays_ignores_other_session_pids(tmp_path, monkeypatch):
    """Without pidfiles, no PIDs are touched at all.

    Models the production case on a host that also runs a separate backend
    or a human tmux attach — their relay PIDs are not in our pidfile dir,
    so cleanup must be a no-op even if pgrep would have found them.
    """
    from app.services.runs import pty_relay

    monkeypatch.setattr(pty_relay, "_PIDFILE_DIR", tmp_path)

    killed_pids = []

    def fake_kill(pid, sig):
        killed_pids.append((pid, sig))

    monkeypatch.setattr("os.kill", fake_kill)

    pty_relay.cleanup_orphaned_relays()

    assert killed_pids == [], (
        f"cleanup_orphaned_relays killed PIDs without pidfile evidence: {killed_pids}"
    )


def test_pty_relay_close_removes_its_pidfile(tmp_path, monkeypatch):
    """PtyRelay.close() must remove the pidfile it wrote at spawn.

    Without this, a clean shutdown leaves the pidfile behind and the next
    backend startup would SIGTERM our own (already-dead) relay as a false
    orphan. We verify the cleanup path is wired into the close() path.
    """
    from app.services.runs import pty_relay

    monkeypatch.setattr(pty_relay, "_PIDFILE_DIR", tmp_path)

    relay = pty_relay.PtyRelay(target="card-test", read_only=True)
    fake_pid = 88801
    relay.process = type(
        "FakeProc", (), {"pid": fake_pid, "poll": lambda self: None}
    )()

    # Simulate the spawn-time pidfile write directly — the production
    # Popen is mocked at the spawn path's actual entry, but for this
    # regression we just exercise the close path: write pidfile, then
    # close, expect it gone.
    pty_relay._write_relay_pidfile(target="card-test", pid=fake_pid)
    pidfile = tmp_path / f"{fake_pid}.pid"
    assert pidfile.exists()

    # Stub out process.terminate / kill / wait — we don't want real signal
    # machinery in a unit test.
    relay.process.terminate = lambda: None
    relay.process.wait = lambda timeout=None: None

    relay.close()

    assert not pidfile.exists()


def test_write_relay_pidfile_roundtrip(tmp_path, monkeypatch):
    """_write_relay_pidfile writes <pid>.pid with the target name in body."""
    from app.services.runs import pty_relay

    monkeypatch.setattr(pty_relay, "_PIDFILE_DIR", tmp_path)

    pty_relay._write_relay_pidfile(target="my-tmux-target", pid=12345)

    pidfile = tmp_path / "12345.pid"
    assert pidfile.exists()
    # Body is the target name (debug-only — the filename carries the PID).
    assert "my-tmux-target" in pidfile.read_text(encoding="utf-8")


# --- PPID-based orphan disambiguation --------------------------------------
#
# The pidfile-only approach leaves a second concurrency hole: any backend
# process under the same user reads pidfiles from the shared directory and
# SIGTERMs every PID, including ones written by a *concurrent* (still alive)
# backend on the same box. A separate backend start would still kill that
# backend's live relays. The fix is to consult the relay's PPID before
# signaling: only kill when the parent process is gone (genuinely orphaned),
# skip when the parent is alive (a concurrent backend still owns it).


def test_cleanup_orphaned_relays_skips_relay_with_alive_ppid(tmp_path, monkeypatch):
    """A relay whose parent is alive (a concurrent backend still owns it) is left alone.

    Regression test for the FCR-found hole: a pidfile-only check would
    SIGTERM every PID in the shared directory, including relays that
    belong to a different backend process still running on the same box.
    """
    from app.services.runs import pty_relay

    monkeypatch.setattr(pty_relay, "_PIDFILE_DIR", tmp_path)

    # Simulate: a relay from backend A (pid 100), A is alive and owns this relay.
    relay_pid = 55555
    other_backend_pid = 100
    (tmp_path / f"{relay_pid}.pid").write_text("concurrent-backend-target")

    # Mock _read_ppid so we don't depend on a real /proc on the test runner.
    monkeypatch.setattr(pty_relay, "_read_ppid", lambda pid: other_backend_pid)

    # Mock os.kill for PPID liveness check: PID 100 is alive.
    def fake_kill(pid, sig):
        if pid == other_backend_pid and sig == 0:
            return  # liveness check — alive
        raise AssertionError(
            f"SIGTERM {pid} when relay is owned by a live concurrent backend — should have been skipped"
        )

    monkeypatch.setattr("os.kill", fake_kill)

    pty_relay.cleanup_orphaned_relays()

    # Pidfile should NOT be removed — the relay is still owned by another backend.
    assert (tmp_path / f"{relay_pid}.pid").exists(), (
        "pidfile removed despite the parent backend still being alive"
    )


def test_cleanup_orphaned_relays_kills_relay_with_dead_ppid(tmp_path, monkeypatch):
    """A relay whose parent is dead (the genuine orphan case) IS killed."""
    from app.services.runs import pty_relay

    monkeypatch.setattr(pty_relay, "_PIDFILE_DIR", tmp_path)

    relay_pid = 66666
    dead_ppid = 200
    (tmp_path / f"{relay_pid}.pid").write_text("orphaned-target")

    monkeypatch.setattr(pty_relay, "_read_ppid", lambda pid: dead_ppid)

    killed_pids = []

    def fake_kill(pid, sig):
        killed_pids.append((pid, sig))
        if pid == dead_ppid and sig == 0:
            raise ProcessLookupError(pid)  # PPID is dead

    monkeypatch.setattr("os.kill", fake_kill)

    pty_relay.cleanup_orphaned_relays()

    assert (relay_pid, signal.SIGTERM) in killed_pids
    assert not (tmp_path / f"{relay_pid}.pid").exists(), (
        "pidfile not removed after killing orphaned relay"
    )


def test_cleanup_orphaned_relays_skips_own_live_relay(tmp_path, monkeypatch):
    """A relay whose parent is the current backend is left alone (we still own it)."""
    from app.services.runs import pty_relay

    monkeypatch.setattr(pty_relay, "_PIDFILE_DIR", tmp_path)

    relay_pid = 77777
    # Mock PPID == our own PID
    own_pid = os.getpid()
    (tmp_path / f"{relay_pid}.pid").write_text("self-target")

    monkeypatch.setattr(pty_relay, "_read_ppid", lambda pid: own_pid)

    def fake_kill(pid, sig):
        raise AssertionError(
            f"SIGTERM {pid} when relay is our own live child — should have been skipped"
        )

    monkeypatch.setattr("os.kill", fake_kill)

    pty_relay.cleanup_orphaned_relays()

    # Pidfile still there — we'll remove it via PtyRelay.close().
    assert (tmp_path / f"{relay_pid}.pid").exists()


def test_read_ppid_parses_status_file(tmp_path, monkeypatch):
    """_read_ppid reads /proc/<pid>/status and extracts the PPid line."""
    from app.services.runs import pty_relay

    fake_proc = tmp_path / "1234" / "status"
    fake_proc.parent.mkdir(parents=True, exist_ok=True)
    fake_proc.write_text(
        "Name:\ttmux\n"
        "PPid:\t9876\n"
        "Threads:\t1\n",
        encoding="utf-8",
    )

    # Redirect /proc to tmp_path for this test by monkeypatching the path builder.
    monkeypatch.setattr(pty_relay, "_PROC_STATUS_PATH", lambda pid: tmp_path / str(pid) / "status")

    assert pty_relay._read_ppid(1234) == 9876


def test_read_ppid_returns_none_for_missing_process(tmp_path, monkeypatch):
    """_read_ppid returns None when the status file is missing (process gone)."""
    from app.services.runs import pty_relay

    monkeypatch.setattr(pty_relay, "_PROC_STATUS_PATH", lambda pid: tmp_path / f"missing-{pid}" / "status")

    assert pty_relay._read_ppid(99999) is None


def test_cleanup_orphaned_relays_prunes_stale_pidfile_when_pid_gone(tmp_path, monkeypatch):
    """A pidfile whose relay PID is gone must be pruned (no leak in the dir).

    Regression test for FCR-found hole: when /proc/<pid>/status is missing
    (process exited cleanly between write and cleanup), _read_ppid returns
    None and the cleanup code used to skip the entry, leaving the pidfile
    to accumulate forever. The docstring and commit claim promise stale
    pidfiles get pruned — this test makes that promise hold.
    """
    from app.services.runs import pty_relay

    monkeypatch.setattr(pty_relay, "_PIDFILE_DIR", tmp_path)

    gone_pid = 31337
    pidfile = tmp_path / f"{gone_pid}.pid"
    pidfile.write_text("ghost-target")

    # Simulate: process is gone (liveness probe fails), so /proc/<pid>/status
    # is missing too. The cleanup should take the "stale pidfile" path:
    # no SIGTERM, just prune the file.
    monkeypatch.setattr(pty_relay, "_is_pid_alive", lambda pid: False)
    monkeypatch.setattr(pty_relay, "_read_ppid", lambda pid: None)

    killed_pids = []

    def fake_kill(pid, sig):
        killed_pids.append((pid, sig))

    monkeypatch.setattr("os.kill", fake_kill)

    pty_relay.cleanup_orphaned_relays()

    # No SIGTERM sent — there's no process to kill.
    assert killed_pids == []
    # But the pidfile is pruned (this is the actual stale-pidfile contract).
    assert not pidfile.exists(), (
        "stale pidfile for gone relay process was not pruned"
    )


def test_is_orphaned_relay_false_when_pid_gone(tmp_path, monkeypatch):
    """_is_orphaned_relay returns False when /proc/<pid>/status is unreadable.

    The cleanup caller must not interpret 'cannot read PPID' as 'orphan' —
    the orphan determination requires the PPID to be readable AND proven
    dead. When the relay itself is gone, the cleanup's job is to prune the
    pidfile (not to kill anything), so the orphan check returns False.
    """
    from app.services.runs import pty_relay

    monkeypatch.setattr(pty_relay, "_PROC_STATUS_PATH", lambda pid: tmp_path / f"missing-{pid}" / "status")

    assert pty_relay._is_orphaned_relay(99999) is False
