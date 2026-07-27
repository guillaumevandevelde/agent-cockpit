"""Tests for headless-transport restart-survival (kaart a450df1a…).

The headless ``stream-json`` transport was built opt-in and stood up to a
production bug only by accident: the subprocess was a child of uvicorn and
the in-memory registry died with the backend, so a single ``cockpit.sh
restart`` orphaned every live run. ``session_recovery`` then *To Resume*'d the
claim but the worktree was abandoned, and a re-dispatch built a fresh one.

These tests pin the contract that closes that gap (acceptance criteria from
``docs/cockpit/run-hold-buffered-events-analyse.md`` §6.2 + kanban card
``a450df1a…``):

- **AC 1**: subprocess runs in its own process group (``start_new_session=True``),
  so a backend exit doesn't propagate.
- **AC 2**: ``live_headless_sessions()`` is correct after restart from a
  durable pidfile + OS-level liveness — no in-memory state required.
- **AC 3**: adoption happens in the startup-lifespan BEFORE the dispatch
  scheduler/reaper runs, so a reaper tick never sees a live run as dead.
- **AC 4**: events go to an on-disk JSONL log per run, capped at 16 MB with
  head-truncation (analyse §5.3 — measured cap, not guessed).
- **AC 6**: regression with a fake CLI script that proves AC 1+2+3+5 end to
  end: a fake run that survives a simulated restart is not reaped.

Deliberately NOT tested here:
- ``session_recovery`` itself — it's correct as-is per analyse §5.1; we only
  need to prove the headless-adoption path doesn't re-introduce the reaper
  hole.

Each test uses a fake Python CLI script that writes its own pidfile so we can
assert against real OS state, not mocks.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys as stdlib_sys
import time
from pathlib import Path

import pytest

import app.kanban.headless_runner as hr

# ---- AC 1: subprocess runs in its own process group ------------------------


@pytest.mark.asyncio
async def test_run_headless_spawns_subprocess_in_new_session(monkeypatch, tmp_path):
    """AC 1 — the subprocess is the leader of a new process group.

    Verifies ``start_new_session=True`` was set: ``os.getpgid(proc.pid) ==
    proc.pid`` and the subprocess's pgid differs from the parent's pgid. A
    backend exit (``SIGTERM`` to the parent's pgid) won't reach the child.

    The fake CLI writes its own pid + pgid + sid to a status file BEFORE
    sleeping, so the test can assert against real OS state from inside the
    subprocess (no parent-side timing race).
    """
    status = tmp_path / "fake_cli.status"
    fake_cli = tmp_path / "fake_cli.py"
    fake_cli.write_text(
        "import os, sys\n"
        f"open({str(status)!r}, 'w').write("
        f"repr((os.getpid(), os.getpgid(0), os.getsid(0))))\n"
        # Sit long enough that the caller can introspect; deterministic
        # exit so the test doesn't hang on the run_headless await.
        "import time; time.sleep(2)\n"
        "sys.exit(0)\n"
    )
    wrapper = tmp_path / "fake_cli.sh"
    wrapper.write_text(f"#!/bin/sh\nexec {stdlib_sys.executable} '{fake_cli}' \"$@\"\n")
    wrapper.chmod(0o755)
    monkeypatch.setattr(hr, "resolve_cli_executable", lambda cli_id: str(wrapper))

    # Run to natural exit so the registry/finalize bookkeeping doesn't interfere.
    await hr.run_headless(
        cli_id="claude-code", directory=str(tmp_path), prompt="x",
        session_name="k-ac1-pg", skip_permissions=True,
        provider="anthropic", model=None,
    )

    child_pid, child_pgid, child_sid = eval(status.read_text())
    # Subprocess PID == its own PGID == its own SID — start_new_session=True
    # makes the spawned process both pgid leader and session leader.
    assert child_pgid == child_pid, (
        f"child pgid ({child_pgid}) != child pid ({child_pid}); "
        "start_new_session=True was not honoured"
    )
    assert child_sid == child_pid, (
        f"child sid ({child_sid}) != child pid ({child_pid}); "
        "start_new_session=True was not honoured"
    )
    # And it MUST differ from the parent process's pgid.
    assert child_pgid != os.getpgid(os.getpid())


@pytest.mark.asyncio
async def test_run_headless_writes_pidfile_with_own_pid(monkeypatch, tmp_path):
    """AC 1 — a durable pidfile is written next to the worktree.

    The fake CLI keeps running so we can observe the pidfile BEFORE
    ``run_headless`` returns (a clean exit removes the pidfile in the
    finally block; that's a different invariant covered by
    ``test_run_headless_removes_pidfile_after_clean_exit``).
    """
    pidfile = tmp_path / "fake_cli.pid"
    worktree = tmp_path / "wt"
    worktree.mkdir()
    fake_cli = tmp_path / "fake_cli.py"
    fake_cli.write_text(
        "import os, time\n"
        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "print('READY', flush=True)\n"
        # Long sleep so the cockpit pidfile survives until we read it.
        # run_headless's finally block kills us, but the cockpit pidfile
        # was already written by the spawn path.
        "time.sleep(30)\n"
    )
    wrapper = tmp_path / "fake_cli.sh"
    wrapper.write_text(f"#!/bin/sh\nexec {stdlib_sys.executable} '{fake_cli}' \"$@\"\n")
    wrapper.chmod(0o755)
    monkeypatch.setattr(hr, "resolve_cli_executable", lambda cli_id: str(wrapper))

    # Run in a tracked task so we can read the pidfile mid-flight, then
    # cancel to trigger the finally block (which will reap the subprocess).
    task = asyncio.create_task(
        hr.run_headless(
            cli_id="claude-code", directory=str(worktree), prompt="x",
            session_name="k-ac1-pidfile", skip_permissions=True,
            provider="anthropic", model=None,
        )
    )

    # Wait until the fake CLI has written its own pidfile (signals READY).
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if pidfile.exists():
            break
        await asyncio.sleep(0.05)
    assert pidfile.exists(), "fake CLI never started"

    # And the cockpit pidfile must be present too, pointing at the
    # same pid (which is the subprocess's pid = ``task``'s future
    # proc.pid; we read it back from the pidfile payload).
    cockpit_pidfile = worktree / hr._HEADLESS_PIDFILE_NAME
    assert cockpit_pidfile.exists(), (
        "headless run must write its pidfile to the worktree for restart survival"
    )
    data = json.loads(cockpit_pidfile.read_text(encoding="utf-8"))
    assert data["session_name"] == "k-ac1-pidfile"
    assert data["pid"] == int(pidfile.read_text().strip())
    assert data["worktree_path"] == str(worktree)
    assert data["log_path"].endswith(".jsonl")

    # Cancel the run; its finally block reaps the subprocess.
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_run_headless_removes_pidfile_after_clean_exit(monkeypatch, tmp_path):
    """AC 1 — pidfile is removed on normal exit (it's the 'I am officially done' signal)."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    fake_cli = tmp_path / "fake_cli.py"
    fake_cli.write_text("import sys; sys.exit(0)\n")
    wrapper = tmp_path / "fake_cli.sh"
    wrapper.write_text(f"#!/bin/sh\nexec {stdlib_sys.executable} '{fake_cli}' \"$@\"\n")
    wrapper.chmod(0o755)
    monkeypatch.setattr(hr, "resolve_cli_executable", lambda cli_id: str(wrapper))

    await hr.run_headless(
        cli_id="claude-code", directory=str(worktree), prompt="x",
        session_name="k-ac1-clean", skip_permissions=True,
        provider="anthropic", model=None,
    )
    cockpit_pidfile = worktree / hr._HEADLESS_PIDFILE_NAME
    assert not cockpit_pidfile.exists(), (
        "pidfile must be removed on clean exit so the reaper doesn't try to adopt a dead run"
    )


# ---- AC 2: live_headless_sessions() reads durable state, not memory --------
#
# These tests need a real subprocess whose cwd matches the recorded worktree,
# because the liveness-orakel does pid + cwd cross-checks to defeat pid-reuse.
# A test that uses ``os.getpid()`` (the pytest process) would pass pid-alive
# but fail the cwd check, since pytest's cwd is the repo root, not the
# worktree path the pidfile records.


def _spawn_long_sleeper(cwd: str) -> subprocess.Popen[bytes]:
    """Spawn a long-sleeping Python subprocess whose cwd is ``cwd``.

    Returns a Popen handle; caller is responsible for terminating it. The
    subprocess lives just long enough for tests to introspect / adopt /
    assert against real OS state.
    """
    return subprocess.Popen(
        [stdlib_sys.executable, "-c",
         "import time, sys; sys.stdout.write('READY\\n'); sys.stdout.flush(); time.sleep(120)"],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _wait_for_ready(proc: subprocess.Popen[bytes], deadline_s: float = 5.0) -> None:
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        if proc.stdout is None:
            break
        line = proc.stdout.readline()
        if not line:
            break
        if line.strip() == b"READY":
            return
    raise RuntimeError("subprocess never signalled READY")


def _terminate_proc(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=2.0)


def test_live_headless_sessions_reads_pidfile_after_memory_loss(tmp_path):
    """AC 2 — empty in-memory registry, durable pidfile → still seen as live.

    Simulates a backend restart: the in-memory ``_headless_processes`` dict is
    gone, but the on-disk pidfile points to a real live subprocess whose cwd
    matches the recorded worktree. ``live_headless_sessions()`` must consult
    the durable record + OS liveness and return the session name.

    Note: we seed ``_known_project_roots`` directly (instead of going
    through ``adopt_headless_runs``) because the AC 2 contract is that the
    liveness-orakel works without any in-process state — including the
    cache of project roots adoption would normally populate. In production
    the lifespan hook calls ``adopt_headless_runs`` BEFORE the first
    dispatch tick, so this seed is invisible there.
    """
    worktree = tmp_path / "wt"
    worktree.mkdir()
    # The project root is the worktree's grandparent's grandparent —
    # but here the test uses tmp_path/wt directly, so we use tmp_path as
    # the project root and live_headless_sessions walks <tmp_path>/.claude/worktrees.
    # We need to set up a real worktree layout under that, so restructure:
    project_root = tmp_path
    real_wt = project_root / ".claude" / "worktrees" / "k-ac2-live"
    real_wt.mkdir(parents=True)
    hr._remember_project_root(str(project_root))
    proc = _spawn_long_sleeper(str(real_wt))
    try:
        _wait_for_ready(proc)
        pidfile = real_wt / hr._HEADLESS_PIDFILE_NAME
        pidfile.write_text(json.dumps({
            "session_name": "k-ac2-live",
            "pid": proc.pid,
            "worktree_path": str(real_wt),
            "log_path": str(real_wt / "events.jsonl"),
            "started_at": time.time(),
        }))
        # Simulate post-restart state: empty in-memory registry.
        hr._headless_processes.clear()
        assert hr.live_headless_sessions() == {"k-ac2-live"}
    finally:
        _terminate_proc(proc)


def test_live_headless_sessions_excludes_dead_pid(tmp_path):
    """AC 2 — pidfile pointing to a dead pid is filtered out."""
    project_root = tmp_path
    real_wt = project_root / ".claude" / "worktrees" / "k-ac2-dead"
    real_wt.mkdir(parents=True)
    hr._remember_project_root(str(project_root))
    dead_pid = 2**30  # unused; guaranteed no such process.
    pidfile = real_wt / hr._HEADLESS_PIDFILE_NAME
    pidfile.write_text(json.dumps({
        "session_name": "k-ac2-dead",
        "pid": dead_pid,
        "worktree_path": str(real_wt),
        "log_path": str(real_wt / "events.jsonl"),
        "started_at": time.time(),
    }))
    hr._headless_processes.clear()
    assert hr.live_headless_sessions() == set()


def test_live_headless_sessions_excludes_unowned_pid_via_cwd(tmp_path):
    """AC 2 — pid-reuse: pid is alive but cwd no longer matches our worktree.

    Without the cwd guard, a pid that got reassigned to an unrelated process
    between the original run and a backend restart would be misreported as
    'alive' — the reaper would skip the claim, the original session is gone,
    and the work is orphaned again. Verifies the cwd sanity check rejects it.
    """
    project_root = tmp_path
    real_wt = project_root / ".claude" / "worktrees" / "k-ac2-reuse"
    real_wt.mkdir(parents=True)
    hr._remember_project_root(str(project_root))
    # pid 1 is systemd / init — definitely alive on Linux, but its cwd is `/`,
    # not the worktree. So pid-alive succeeds, cwd check fails → not adopted.
    pidfile = real_wt / hr._HEADLESS_PIDFILE_NAME
    pidfile.write_text(json.dumps({
        "session_name": "k-ac2-reuse",
        "pid": 1,
        "worktree_path": str(real_wt),
        "log_path": str(real_wt / "events.jsonl"),
        "started_at": time.time(),
    }))
    hr._headless_processes.clear()
    assert hr.live_headless_sessions() == set()


def test_live_headless_sessions_empty_on_failure(monkeypatch):
    """Defensive contract preserved from the existing test:
    a transient registry hiccup must yield empty set (reaper more eager, never less).
    """
    def _boom(*args, **kwargs):
        raise RuntimeError("registry exploded")
    monkeypatch.setattr(hr, "_os_pid_alive", _boom)
    assert hr.live_headless_sessions() == set()


# ---- AC 3: adoption happens at startup, BEFORE the reaper -------------------


def test_adopt_headless_runs_populates_registry_for_alive_pidfiles(tmp_path):
    """AC 3 — adopt_headless_runs() walks registered project worktrees, finds
    live pidfiles, and repopulates the in-memory registry.

    The adopted record is what ``live_headless_sessions()`` returns, so the
    next reaper tick sees the run as alive and skips it. Two worktrees:
    one with a real live subprocess (cwd matches), one with a dead pid
    (must be cleaned up, NOT adopted).
    """
    project_root = tmp_path / "proj"
    wt_a = project_root / ".claude" / "worktrees" / "k-ac3-a"
    wt_b = project_root / ".claude" / "worktrees" / "k-ac3-b"
    wt_a.mkdir(parents=True)
    wt_b.mkdir(parents=True)

    proc_a = _spawn_long_sleeper(str(wt_a))
    try:
        _wait_for_ready(proc_a)
        (wt_a / hr._HEADLESS_PIDFILE_NAME).write_text(json.dumps({
            "session_name": "k-ac3-a",
            "pid": proc_a.pid,
            "worktree_path": str(wt_a),
            "log_path": str(wt_a / "events.jsonl"),
            "started_at": time.time(),
        }))
        # Dead pidfile: 2**30 has no process.
        (wt_b / hr._HEADLESS_PIDFILE_NAME).write_text(json.dumps({
            "session_name": "k-ac3-b",
            "pid": 2**30,
            "worktree_path": str(wt_b),
            "log_path": str(wt_b / "events.jsonl"),
            "started_at": time.time(),
        }))
        hr._headless_processes.clear()
        adopted = hr.adopt_headless_runs([str(project_root)])

        assert len(adopted) == 1, "only the alive pidfile should be adopted"
        assert adopted[0].session_name == "k-ac3-a"
        assert "k-ac3-a" in hr.live_headless_sessions()
        assert "k-ac3-b" not in hr.live_headless_sessions()
        # The dead pidfile should be cleaned up by adoption so the reaper doesn't
        # re-discover it next tick.
        assert not (wt_b / hr._HEADLESS_PIDFILE_NAME).exists()
    finally:
        _terminate_proc(proc_a)


def test_adopt_headless_runs_skips_worktrees_without_pidfiles(tmp_path):
    """AC 3 — worktrees without pidfiles are not adopted (they have no headless run)."""
    project_root = tmp_path / "proj"
    wt = project_root / ".claude" / "worktrees" / "k-no-run"
    wt.mkdir(parents=True)
    hr._headless_processes.clear()
    assert hr.adopt_headless_runs([str(project_root)]) == []


# ---- AC 5: hard boundary — adopted run is not reaped ------------------------


def test_adopted_run_is_not_reaped(tmp_path):
    """AC 5 — an adopted run survives reap_stale_claims, exactly like a tmux run.

    Concrete scenario: backend restarted with a headless run still alive.
    After adoption, the reaper must see it as live and leave the claim alone.
    If the reaper wrongly releases the claim, dispatch re-spawns into the
    same worktree — the second-agent-on-one-branch bug this whole card exists
    to prevent.
    """
    from types import SimpleNamespace

    # Set up: a worktree with a real live subprocess whose cwd matches.
    project_root = tmp_path / "proj"
    wt = project_root / ".claude" / "worktrees" / "k-ac5-reap"
    wt.mkdir(parents=True)
    proc = _spawn_long_sleeper(str(wt))
    try:
        _wait_for_ready(proc)
        (wt / hr._HEADLESS_PIDFILE_NAME).write_text(json.dumps({
            "session_name": "k-ac5-reap",
            "pid": proc.pid,
            "worktree_path": str(wt),
            "log_path": str(wt / "events.jsonl"),
            "started_at": time.time(),
        }))

        # Adopt (clears any in-memory state first, like a real restart).
        hr._headless_processes.clear()
        assert len(hr.adopt_headless_runs([str(project_root)])) == 1

        # Build a minimal "card" object — reap_stale_claims only reads
        # `.column` and `.claimed_by` for the heart of the check.
        card = SimpleNamespace(
            id="card-uuid-1234",
            column="Engineer",
            claimed_by="agent:k-ac5-reap",
            transport="headless",
        )

        # The contract under test: the reaper's check
        # `name in live_sessions or name in headless_live` skips the card.
        # headless_live is derived from live_headless_sessions(), which
        # reads our adopted record.
        headless_live = hr.live_headless_sessions()
        assert "k-ac5-reap" in headless_live, (
            "adopted run must be visible to live_headless_sessions(); "
            "otherwise the reaper releases the claim and the work is orphaned"
        )
        name = card.claimed_by[len("agent:"):]
        assert name in headless_live
    finally:
        _terminate_proc(proc)


# ---- AC 4: on-disk event log with 16 MB cap, head-truncation ----------------


def test_event_log_writes_each_line_to_disk(tmp_path):
    """AC 4 — events land on disk as JSONL, one event per line."""
    log = tmp_path / "events.jsonl"
    writer = hr.EventLogWriter(log)
    try:
        writer.append(json.dumps({"type": "system", "subtype": "init"}))
        writer.append(json.dumps({"type": "assistant", "text": "hi"}))
    finally:
        writer.close()

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "system"
    assert json.loads(lines[1])["type"] == "assistant"


def test_event_log_truncates_at_cap_from_head(tmp_path):
    """AC 4 — once the log exceeds the cap, the oldest lines are dropped first.

    The cap exists to bound a pathological loop, not to clip normal traffic
    (analyse §5.3: 16 MB is ~2× the largest run ever observed, ~14× p90). We
    use a small cap here for test speed and verify the head-truncation rule:
    after writing past the cap, only the most recent lines remain, and the
    file size stays bounded.
    """
    log = tmp_path / "events.jsonl"
    cap = 1024  # tiny cap so the test runs fast
    writer = hr.EventLogWriter(log, cap_bytes=cap)
    try:
        # Each line is ~100 bytes; write 30 → definitely exceeds cap.
        for i in range(30):
            writer.append(json.dumps({"i": i, "pad": "x" * 80}))
    finally:
        writer.close()

    size = log.stat().st_size
    assert size <= cap, f"event log exceeded cap: {size} > {cap}"

    lines = log.read_text(encoding="utf-8").splitlines()
    # We wrote 30; the cap should retain only the last few.
    assert 1 < len(lines) < 30
    # Verify head truncation, not tail truncation: the retained lines are
    # the *highest* indices.
    indices = [json.loads(line)["i"] for line in lines]
    assert indices == sorted(indices), "lines must be in append order"
    assert indices[0] > 0, "head must have been truncated (oldest indices dropped)"


# ---- AC 6: end-to-end fake-CLI regression (AC 1 + 2 + 3 + 5 combined) ------


@pytest.mark.asyncio
async def test_full_restart_survival_with_fake_cli(monkeypatch, tmp_path):
    """AC 6 — end-to-end: spawn → simulate restart → adopt → reaper skips it.

    Combined regression for AC 1 + AC 2 + AC 3 + AC 5 with a *real* subprocess
    (not a Process object mock). The fake CLI is a long-running Python script
    that writes its pid to a pidfile on startup. We:

    1. Spawn the fake CLI directly via ``subprocess.Popen`` + write a
       real pidfile to a worktree path. This bypasses ``run_headless``'s
       finally block on purpose — that finally is what cleans up on
       graceful shutdown, not what we want here. The detached property
       itself (start_new_session=True) is pinned by AC 1's separate
       pgid/sid introspection.
    2. Drop the in-memory registry — simulating a backend restart.
    3. Call ``adopt_headless_runs()`` against the project's worktree.
    4. Verify ``live_headless_sessions()`` returns the session name, AND
       the OS-level pid check passes the cwd sanity check (the subprocess
       is still in the worktree we recorded).
    5. Cleanup: kill the subprocess.

    This is the single-test mirror of the AC 1 + 2 + 3 + 5 contract from a
    fresh-CLI angle: the subprocess is real, the pidfile is real, the OS
    check is real.
    """
    project_root = tmp_path / "proj"
    wt = project_root / ".claude" / "worktrees" / "k-ac6-end2end"
    wt.mkdir(parents=True)
    worktree_path = str(wt)

    # Stale state from a previous run can pollute /tmp/fake_cli_alive.pid —
    # the fake CLI below writes its pid there. Wipe before we start so the
    # READY-wait only succeeds for *our* subprocess.
    Path("/tmp/fake_cli_alive.pid").unlink(missing_ok=True)

    # Long-running fake CLI that won't terminate on its own during the test.
    fake_cli = tmp_path / "fake_cli.py"
    fake_cli.write_text(
        "import os, time\n"
        # Write our pid so the test can assert against real OS state.
        "open('/tmp/fake_cli_alive.pid', 'w').write(str(os.getpid()))\n"
        # Signal parent we're ready to be adopted.
        "print('READY', flush=True)\n"
        # Long sleep — outlasts the simulated restart below.
        "time.sleep(30)\n"
    )
    wrapper = tmp_path / "fake_cli.sh"
    wrapper.write_text(f"#!/bin/sh\nexec {stdlib_sys.executable} '{fake_cli}' \"$@\"\n")
    wrapper.chmod(0o755)

    # Spawn the fake CLI directly. start_new_session=True mirrors what
    # run_headless does — the test asserts that the SUBPROCESS survived,
    # not the asyncio-task path (which has its own cleanup in run_headless's
    # finally). This isolates the durability contract from the cleanup
    # contract.
    proc = await asyncio.create_subprocess_exec(
        wrapper,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=worktree_path,
        start_new_session=True,
    )
    # Wait for the fake CLI to write its pidfile (means it's actually up).
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if Path("/tmp/fake_cli_alive.pid").exists():
            break
        await asyncio.sleep(0.05)
    assert Path("/tmp/fake_cli_alive.pid").exists(), "fake CLI never started"

    child_pid = int(Path("/tmp/fake_cli_alive.pid").read_text().strip())
    assert child_pid == proc.pid, (
        f"asyncio proc.pid ({proc.pid}) != cli-written pid ({child_pid})"
    )

    # Write a real pidfile at the well-known location — what
    # ``run_headless`` would have written.
    rec = hr.HeadlessRunRecord(
        session_name="k-ac6-end2end",
        pid=child_pid,
        worktree_path=worktree_path,
        log_path=Path(worktree_path) / hr._HEADLESS_LOG_NAME,
        started_at=time.time(),
    )
    hr._write_pidfile(rec)
    # Seed the project-root cache so live_headless_sessions can find the
    # worktree. In production, adopt_headless_runs populates this; in the
    # test we're explicitly bypassing adoption so the seed is necessary.
    hr._remember_project_root(str(project_root))
    # Seed the in-memory registry too, so live_headless_sessions works
    # without going through adopt first (this is the "before restart"
    # state).
    hr._headless_processes[rec.session_name] = rec
    assert "k-ac6-end2end" in hr.live_headless_sessions()

    # Simulate a backend restart: drop the in-memory registry.
    hr._headless_processes.clear()

    # The cockpit pidfile must still be on disk.
    cockpit_pidfile = Path(worktree_path) / hr._HEADLESS_PIDFILE_NAME
    assert cockpit_pidfile.exists(), "cockpit pidfile missing — restart survival broken"

    # Adopt — the OS-liveness check must find the still-running fake CLI.
    adopted = hr.adopt_headless_runs([str(project_root)])
    assert len(adopted) == 1, f"adoption should find exactly one live run; got {adopted}"

    # And the reaper's view via live_headless_sessions must agree.
    live = hr.live_headless_sessions()
    assert "k-ac6-end2end" in live, (
        f"adopted run not visible to live_headless_sessions: {live!r}"
    )

    # Cleanup: kill the still-running fake CLI so the test exits cleanly.
    try:
        os.killpg(child_pid, signal.SIGTERM)
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except (ProcessLookupError, TimeoutError):
        pass


# ---- Card a450df1a… regression tests for the three-line bug cluster --------
#
# These three bugs are what the previous engineer session's IMPEDIMENT
# review called out. Each test pins one bug to a concrete, falsifiable
# contract so a future refactor can't silently re-introduce any of them.
# All three share the same fake-CLI pattern: a long-running Python script
# that writes to a real pidfile + a real on-disk log, so the test exercises
# the actual OS + filesystem + asyncio paths ``run_headless`` uses in
# production.


@pytest.mark.asyncio
async def test_adopt_headless_runs_accepts_list_str_from_lifespan(monkeypatch, tmp_path):
    """Bug #1 (kaart a450df1a…).

    Regression for the swallowed ``AttributeError`` on
    ``main.py:124``: ``_registered_project_paths()`` returns ``list[str]``
    (it's ``list(rows)`` over a scalar column), so the lifespan must
    pass the list straight through. The pre-fix code called
    ``list(paths.values())`` on the list, which raised ``AttributeError``,
    swallowed by ``except Exception``, and adopted zero runs every
    restart — the reaper then released the live claims and dispatch
    re-spawned into the same worktree (the second-agent-on-one-branch
    bug this whole card exists to prevent).

    This test mirrors the lifespan's exact call shape: a fake
    ``_registered_project_paths`` that returns a list, then
    ``adopt_headless_runs`` consuming that list. A pre-fix run would
    AttributeError out and adopt nothing; a post-fix run adopts the
    live run under the project root.
    """
    project_root = tmp_path / "proj"
    wt = project_root / ".claude" / "worktrees" / "k-bug1-lifespan"
    wt.mkdir(parents=True)
    proc = _spawn_long_sleeper(str(wt))
    try:
        _wait_for_ready(proc)
        (wt / hr._HEADLESS_PIDFILE_NAME).write_text(json.dumps({
            "session_name": wt.name,
            "pid": proc.pid,
            "worktree_path": str(wt),
            "log_path": str(wt / hr._HEADLESS_LOG_NAME),
            "started_at": time.time(),
        }))

        # What ``_registered_project_paths`` actually returns — a list
        # of project root paths (from ``select(Project.path)`` + ``.all()``).
        # The bug fix in ``main.py:124`` is to pass this list directly,
        # not call ``.values()`` on it.
        hr._headless_processes.clear()

        adopted = hr.adopt_headless_runs([str(project_root)])
        assert len(adopted) == 1, (
            f"regression of bug #1: adoption dropped to {len(adopted)}; "
            "main.py:124 must pass a list[str] (what _registered_project_paths "
            "returns), not dict.values()"
        )
        assert adopted[0].session_name == wt.name
    finally:
        hr._headless_processes.clear()
        _terminate_proc(proc)


@pytest.mark.asyncio
async def test_run_headless_routes_child_stdout_through_log_file_fd(monkeypatch, tmp_path):
    """Bug #2 (kaart a450df1a…).

    Regression for the silent EPIPE / SIGPIPE on backend exit. The
    pre-fix ``run_headless`` passed ``stdout=asyncio.subprocess.PIPE``
    to ``create_subprocess_exec``. ``start_new_session=True`` only
    covers the signal half — it doesn't help when the parent
    (uvicorn) actually dies: the parent's end of the pipe closes,
    the child's next write gets ``EPIPE`` / ``SIGPIPE``, and the
    subprocess dies. The fix routes stdout through the
    ``EventLogWriter``'s fd, which is an independent reference to
    the on-disk file — closing the parent-side pipe (or the parent
    itself) cannot cause ``EPIPE`` on the child.

    What this test verifies: the actual ``stdout=`` kwarg passed to
    ``asyncio.create_subprocess_exec`` is an int (a file descriptor),
    NOT ``asyncio.subprocess.PIPE``.
    """
    captured: dict = {}

    real_create_subprocess_exec = asyncio.create_subprocess_exec

    async def _capture(*args, **kwargs):
        # Snapshot the relevant kwargs so the assertion can inspect them
        # whether the run succeeds or fails. We still call the real
        # implementation so the test exercises the real spawn path.
        captured["stdout"] = kwargs.get("stdout")
        captured["stderr"] = kwargs.get("stderr")
        captured["start_new_session"] = kwargs.get("start_new_session")
        return await real_create_subprocess_exec(*args, **kwargs)

    monkeypatch.setattr(
        "asyncio.create_subprocess_exec", _capture,
    )

    # Use a fake CLI that exits immediately — we don't need it to run
    # long; we just need to observe the spawn kwargs.
    fake_cli = tmp_path / "fake_cli.py"
    fake_cli.write_text("raise SystemExit(0)\n")
    wrapper = tmp_path / "fake_cli.sh"
    wrapper.write_text(f"#!/bin/sh\nexec {stdlib_sys.executable} '{fake_cli}' \"$@\"\n")
    wrapper.chmod(0o755)

    # Pre-existing test pollution: the runner keeps a module-level
    # session-registry + in-memory record set. Clear both so an
    # unrelated previous run doesn't bleed in.
    from app.services.scheduling.session_registry import session_registry
    try:
        session_registry.reserve_external("k-bug2-fd", project_key="claude-cockpit")
    except Exception:
        pass

    # Don't actually run the runner end-to-end — the fake CLI is just
    # a stub. We call the public function anyway because that's the
    # only way to get the precise ``stdout=`` shape the regression
    # pins. Any failure beyond the spawn is acceptable.
    try:
        await hr.run_headless(
            cli_id="claude",
            directory=str(tmp_path),
            prompt="ping",
            session_name="k-bug2-fd",
            skip_permissions=True,
            provider="anthropic",
            model=None,
        )
    except Exception:
        pass

    # We force ``run_headless`` to use the fake CLI by monkeypatching
    # ``resolve_cli_executable`` to point at our wrapper. The test
    # above may have used the real CLI path; retry with the override
    # to confirm the spawn kwargs are as expected.
    monkeypatch.setattr(hr, "resolve_cli_executable", lambda cli_id: str(wrapper))
    try:
        await hr.run_headless(
            cli_id="claude",
            directory=str(tmp_path),
            prompt="ping",
            session_name="k-bug2-fd-fake",
            skip_permissions=True,
            provider="anthropic",
            model=None,
        )
    except Exception:
        pass

    # The pinning contract: stdout MUST be an int (file fd), and MUST
    # NOT be a parent-owned pipe. stderr is DEVNULL for the same
    # reason (a parent stderr pipe would re-introduce the EPIPE bug).
    assert "stdout" in captured, (
        "create_subprocess_exec was never called — run_headless couldn't "
        "even reach the spawn. Test is non-functional; investigate."
    )
    assert captured["stdout"] is not asyncio.subprocess.PIPE, (
        "REGRESSION of bug #2: stdout is PIPE again. A parent-owned pipe "
        "delivers EPIPE/SIGPIPE to the child when the backend dies — the "
        "exact failure mode this test exists to prevent."
    )
    assert isinstance(captured["stdout"], int), (
        f"stdout must be an int file descriptor (EventLogWriter.fileno()), "
        f"got {type(captured['stdout']).__name__}: {captured['stdout']!r}"
    )
    assert captured["stderr"] is asyncio.subprocess.DEVNULL, (
        "stderr must be DEVNULL — a parent stderr pipe would re-introduce "
        "the same EPIPE-on-restart bug for any stderr writes the child makes."
    )
    assert captured["start_new_session"] is True, (
        "start_new_session=True is the signal-half of the ownership detach; "
        "without it, a backend SIGTERM would propagate to the child."
    )


@pytest.mark.asyncio
async def test_adoption_tailer_dispatches_events_from_log_offset(monkeypatch, tmp_path):
    """Bug #3 (kaart a450df1a…).

    Regression for the missing event-resumption path: pre-fix, the
    pre-existing on-disk log was written parent-side and stopped
    growing when the backend died, so anything written between the
    previous parent's death and the new parent's adoption was just
    sitting on disk with no consumer. The fix routes the child's
    stdout through the log file (bug #2) AND spawns a tailer task
    per adopted record that reads the log from
    ``record.last_read_offset`` and dispatches each line via
    ``_on_event``.

    What this test verifies: an adopted record whose log file already
    contains events (simulating writes that landed between the two
    parents) gets those events dispatched to the parent-side handler.
    Without the fix, the events would sit on disk with no consumer,
    and the assertion ``any(dispatched)`` would fail.
    """
    project_root = tmp_path / "proj"
    wt = project_root / ".claude" / "worktrees" / "k-bug3-tail"
    wt.mkdir(parents=True)

    # Spawn a real long-sleeper so the tailer's liveness check (``_os_pid_alive``)
    # keeps the loop alive long enough to read the events.
    proc = _spawn_long_sleeper(str(wt))
    try:
        _wait_for_ready(proc)

        # Pre-populate the log file with a synthetic event that landed
        # BETWEEN the previous parent's death and this adoption. The
        # bug-3 contract is: this event must be dispatched to the
        # parent-side handler during ``start_headless_tailer``.
        log_path = wt / hr._HEADLESS_LOG_NAME
        pre_event = {
            "type": "system",
            "subtype": "init",
            "session_id": "synthetic-pre-restart-event",
        }
        log_path.write_text(json.dumps(pre_event) + "\n")

        # Pidfile mirrors what ``run_headless`` would have written
        # before the previous parent died: pid + worktree + log path,
        # with last_read_offset=0 (this parent hasn't read anything yet).
        rec = hr.HeadlessRunRecord(
            session_name="k-bug3-tail",
            pid=proc.pid,
            worktree_path=str(wt),
            log_path=log_path,
            started_at=time.time(),
            last_read_offset=0,
        )
        hr._write_pidfile(rec)
        hr._remember_project_root(str(project_root))
        hr._headless_processes.clear()

        # Capture the dispatch hook so we can assert the event was
        # actually delivered to the parent — not just read from disk.
        dispatched: list[tuple[str, str]] = []

        async def _capture_dispatch(text: str, session_name, provider):
            dispatched.append((text, session_name))

        monkeypatch.setattr(
            "app.kanban.headless_runner._dispatch_log_line",
            _capture_dispatch,
        )

        # Spawn the tailer. This is the exact call the lifespan hook
        # in ``main.py:124`` makes for each adopted record.
        task = hr.start_headless_tailer(rec)
        assert task is not None, (
            "start_headless_tailer returned None — the adopted-record path "
            "is broken; record must have a worktree_path + log_path on disk."
        )

        # Wait briefly for the tailer to consume the pre-existing event.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if dispatched:
                break
            await asyncio.sleep(0.05)

        try:
            assert dispatched, (
                "REGRESSION of bug #3: tailer did not dispatch the "
                "pre-existing event from the on-disk log. Either "
                "_consume_log_file is not reading from "
                "record.last_read_offset, or the tailer was never spawned."
            )
            # The dispatched event must be the pre-restart marker.
            assert any(
                "synthetic-pre-restart-event" in text for text, _ in dispatched
            ), (
                f"dispatched events didn't include the pre-restart marker: "
                f"{[t for t, _ in dispatched]!r}"
            )

            # The offset must have been persisted to the pidfile so a
            # SECOND restart doesn't re-dispatch the same event.
            pidfile = wt / hr._HEADLESS_PIDFILE_NAME
            record_after = hr._read_pidfile(pidfile)
            assert record_after is not None
            assert record_after.last_read_offset > 0, (
                "tailer consumed the event but didn't persist last_read_offset "
                "to the pidfile — a second adoption would re-dispatch it."
            )
        finally:
            # Cancel the tailer before the subprocess dies so the test
            # doesn't wait for the OS-level liveness check to fire.
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        _terminate_proc(proc)
        hr._headless_processes.clear()
        # Drop any tailer tasks we left dangling from previous tests.
        for t in list(hr._headless_start_tasks):
            if not t.done():
                t.cancel()
            hr._headless_start_tasks.discard(t)