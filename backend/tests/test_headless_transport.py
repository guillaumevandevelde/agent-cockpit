"""Tests for the headless stream-json transport + third liveness source.

The transport adds a third ``SpawnTransport`` sibling to ``dispatch.py`` for
``claude -p --output-format stream-json --verbose`` and a third liveness source
(``_live_headless_sessions``) so the reaper doesn't release-and-redispatch headless
claims every tick — the same dispatch-loop bug sandcastle had before
``_live_sandcastle_sessions`` was added. See
``docs/cockpit/headless-stream-json-transport-spike.md`` §5 for the precedent.

Two layers:

- **Parser/mapping** — the stream-json → ACP-isomorphic event mapping from
  spike §4 (rate_limit / session_init / message_chunk / tool_call /
  usage_result / error).
- **Liveness + rate-limit pause** — the new liveness source plus the typed
  ``rate_limit_event.resets_at`` driving ``set_paused_until`` instead of the
  tmux-path's ``FALLBACK_PAUSE_HOURS`` guess.
"""
import asyncio
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

import app.kanban.headless_runner as hr
from app.kanban.dispatch import TRANSPORTS
from app.services.agentic_cli.structured_events import (
    ErrorEvent,
    MessageChunkEvent,
    MessageRole,
    RateLimitEvent,
    RateLimitStatus,
    RateLimitType,
    SessionInitEvent,
    StructuredEventType,
    ToolCallEvent,
    ToolCallStatus,
    UsageResultEvent,
    parse_structured_event,
)

# ---- TRANSPORTS tuple ------------------------------------------------------


def test_transports_tuple_includes_headless():
    # The new transport value must be in the validator tuple so it can be set
    # as a per-project default via KanbanMeta. Regression guard: dropping the
    # entry here would silently invalidate the new transport at runtime.
    assert "headless" in TRANSPORTS
    assert TRANSPORTS.index("headless") >= 0  # present, not just truthy


# ---- spawn-gate message (bevinding 5) --------------------------------------
#
# Headless transport's MemoryLimit path must use the same cause-aware message
# as the other transports (worktree / sandcastle / resume). See
# docs/cockpit/spawn-test-bridge-sessions-analyse.md bevinding 5.


def test_headless_transport_raises_with_counter_ceiling_message(monkeypatch):
    """When the in-process counter is the binding constraint, the headless
    transport must not lead the error message with memory figures.

    Uses live-matching tmux panes (no leak) — the honest "genuinely full"
    shape at the transport-integration level. Seeding a phantom-pane leak
    here would just get cleaned up by the self-healing reconciliation on the
    first ``can_add_session()`` call (unthrottled on a fresh registry); that
    diagnostic property is exhaustively covered at the SessionRegistry unit
    level instead (test_limit_message_surfaces_zombie_pane_count)."""
    from types import SimpleNamespace

    import app.services.scheduling.session_registry as sreg
    from app.kanban.dispatch import MemoryLimitExceeded

    reg = sreg.SessionRegistry(max_sessions=5)
    live_panes = {f"%{200 + i}" for i in range(5)}
    monkeypatch.setattr(sreg.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="\n".join(sorted(live_panes)) + "\n", stderr="",
    ))
    for i in range(5):
        reg.record(
            "SessionStart", session_id=f"sess-{i}",
            cwd="/proj", tmux_pane=f"%{200 + i}",
        )
    monkeypatch.setattr(sreg, "session_registry", reg)
    monkeypatch.setattr(sreg, "get_memory_status_cached", lambda: SimpleNamespace(
        usage_percent=0.15, available_bytes=13562 * 1024 * 1024,
        is_critical=False, estimated_max_sessions=107,
    ))

    with pytest.raises(MemoryLimitExceeded) as ei:
        hr.headless_transport(
            directory="/tmp/proj", prompt="hi", session_name="k-hl-0001",
        )

    msg = str(ei.value)
    assert "counter ceiling" in msg
    assert "5/5" in msg
    assert "5 live" in msg
    # Memory is comfortable — must NOT be presented as cause.
    assert "Memory: 15% used, 13562MB available" not in msg


# ---- liveness source -------------------------------------------------------


def test_live_headless_sessions_returns_only_running_processes(tmp_path):
    # The new liveness source reads durable pidfiles + OS-level checks
    # (kaart a450df1a… AC 2). Two live subprocesses + one pidfile pointing
    # at a dead pid — only the live ones count toward liveness.
    import json
    import os
    import subprocess
    import sys
    import time

    project_root = tmp_path / "proj"
    wt_1 = project_root / ".claude" / "worktrees" / "k-hl-1"
    wt_2 = project_root / ".claude" / "worktrees" / "k-hl-2"
    wt_3 = project_root / ".claude" / "worktrees" / "k-hl-3"
    for wt in (wt_1, wt_2, wt_3):
        wt.mkdir(parents=True)

    procs = []
    try:
        for wt in (wt_1, wt_2):
            proc = subprocess.Popen(
                [sys.executable, "-c",
                 "import time, sys; sys.stdout.write('READY\\n'); sys.stdout.flush(); time.sleep(120)"],
                cwd=str(wt),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            # Wait for READY.
            deadline = time.time() + 5.0
            while time.time() < deadline:
                line = proc.stdout.readline()
                if line.strip() == b"READY":
                    break
            procs.append(proc)
            (wt / hr._HEADLESS_PIDFILE_NAME).write_text(json.dumps({
                "session_name": wt.name,
                "pid": proc.pid,
                "worktree_path": str(wt),
                "log_path": str(wt / "events.jsonl"),
                "started_at": time.time(),
            }))
        # Dead pidfile — guaranteed not to be a real process.
        (wt_3 / hr._HEADLESS_PIDFILE_NAME).write_text(json.dumps({
            "session_name": "k-hl-3",
            "pid": 2**30,
            "worktree_path": str(wt_3),
            "log_path": str(wt_3 / "events.jsonl"),
            "started_at": time.time(),
        }))

        hr._remember_project_root(str(project_root))
        hr._headless_processes.clear()
        assert hr.live_headless_sessions() == {"k-hl-1", "k-hl-2"}
    finally:
        for proc in procs:
            try:
                os.killpg(proc.pid, 15)  # SIGTERM
                proc.wait(timeout=2.0)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(proc.pid, 9)
                except ProcessLookupError:
                    pass


def test_live_headless_sessions_empty_on_failure(monkeypatch):
    # The defensive contract: a transient registry glitch yields an empty
    # set (so the reaper is *more* eager, never less). Same fail-open shape
    # as _live_sandcastle_sessions.
    def _boom(*args, **kwargs):
        raise RuntimeError("registry exploded")
    monkeypatch.setattr(hr, "_known_worktree_dirs", _boom)
    assert hr.live_headless_sessions() == set()


def test_live_headless_sessions_empty_when_registry_empty():
    # No project roots registered → no worktrees to scan → empty set.
    hr._headless_processes.clear()
    # Don't clear _known_project_roots globally — that's test-fixture state
    # shared with sibling tests. Just verify the no-input case.
    saved = hr._known_project_roots.copy()
    hr._known_project_roots.clear()
    try:
        assert hr.live_headless_sessions() == set()
    finally:
        hr._known_project_roots.update(saved)


# ---- kill_headless_session (human-takeover promotion) ----------------------
#
# `docs/cockpit/human-takeover-headless-decision.md` §7 point 2: promotion
# first ends the headless process if still alive. `run_headless`'s own
# `finally` block drains `_headless_processes` once the process actually
# exits, so this function only has to signal it — it must not mutate the
# registry itself.


def test_kill_headless_session_terminates_live_process(tmp_path):
    # The new kill path uses os.killpg(rec.pid, SIGTERM). It only acts on a
    # real live subprocess; the registry holds HeadlessRunRecord (pid +
    # worktree path) and the OS check enforces "is the pid actually ours?"
    import os
    import subprocess
    import sys
    import time

    from app.services.scheduling.session_registry import session_registry

    # Reserve the slot so the kill path's session_registry bookkeeping is honest.
    session_registry.reserve_external("k-hl-1")
    try:
        wt = tmp_path / "wt"
        wt.mkdir()
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "import time, sys; sys.stdout.write('READY\\n'); sys.stdout.flush(); time.sleep(60)"],
            cwd=str(wt),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            deadline = time.time() + 5.0
            while time.time() < deadline:
                line = proc.stdout.readline()
                if line.strip() == b"READY":
                    break
            rec = hr.HeadlessRunRecord(
                session_name="k-hl-1",
                pid=proc.pid,
                worktree_path=str(wt),
                log_path=wt / "events.jsonl",
                started_at=time.time(),
            )
            hr._headless_processes["k-hl-1"] = rec

            assert hr.kill_headless_session("k-hl-1") is True
            # Wait for the signal to land (subprocess exits via SIGTERM).
            proc.wait(timeout=5.0)
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, 9)
                except ProcessLookupError:
                    pass
    finally:
        session_registry.release_external("k-hl-1")


def test_kill_headless_session_false_when_unknown_session():
    assert hr.kill_headless_session("k-hl-does-not-exist") is False


def test_kill_headless_session_false_when_already_exited(tmp_path):
    # Pidfile points at a dead pid → the OS liveness check returns False →
    # kill_headless_session returns False without signaling anything.
    import json

    project_root = tmp_path / "proj"
    wt = project_root / ".claude" / "worktrees" / "k-hl-2"
    wt.mkdir(parents=True)
    (wt / hr._HEADLESS_PIDFILE_NAME).write_text(json.dumps({
        "session_name": "k-hl-2",
        "pid": 2**30,  # guaranteed dead
        "worktree_path": str(wt),
        "log_path": str(wt / "events.jsonl"),
        "started_at": 0.0,
    }))
    hr._remember_project_root(str(project_root))
    hr._headless_processes.clear()
    assert hr.kill_headless_session("k-hl-2") is False


# ---- stream-json → ACP-isomorphic mapping ----------------------------------
#
# Spike §4 is the source of truth. Each test below exercises one row of the
# mapping table by feeding a representative raw payload through
# parse_structured_event and asserting the resulting structured-event type +
# the critical field mapping (tool_call_id, is_error → status, resetsAt → unix
# timestamp).

def test_mapping_session_init_from_system_init():
    raw = {
        "type": "system",
        "subtype": "init",
        "session_id": "sess-abc",
        "cwd": "/tmp/proj",
        "model": "claude-opus-4-8",
        "permissionMode": "acceptEdits",
    }
    event = parse_structured_event(hr.map_stream_event(raw))
    assert isinstance(event, SessionInitEvent)
    assert event.type == StructuredEventType.SESSION_INIT
    assert event.session_id == "sess-abc"
    assert event.cwd == "/tmp/proj"
    assert event.model == "claude-opus-4-8"
    assert event.permission_mode == "acceptEdits"


def test_mapping_assistant_text_to_message_chunk_assistant():
    raw = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "hi there"}]},
    }
    event = parse_structured_event(hr.map_stream_event(raw))
    assert isinstance(event, MessageChunkEvent)
    assert event.role is MessageRole.ASSISTANT
    assert event.text == "hi there"


def test_mapping_assistant_thinking_to_message_chunk_thought():
    raw = {
        "type": "assistant",
        "message": {"content": [{"type": "thinking", "thinking": "considering..."}]},
    }
    event = parse_structured_event(hr.map_stream_event(raw))
    assert isinstance(event, MessageChunkEvent)
    assert event.role is MessageRole.THOUGHT
    assert event.text == "considering..."


def test_mapping_assistant_tool_use_to_tool_call_in_progress():
    raw = {
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use",
            "id": "toolu_01",
            "name": "Read",
            "input": {"file_path": "/etc/hosts"},
        }]},
    }
    event = parse_structured_event(hr.map_stream_event(raw))
    assert isinstance(event, ToolCallEvent)
    assert event.tool_call_id == "toolu_01"
    assert event.title == "Read"
    assert event.kind == "read"
    assert event.status is ToolCallStatus.IN_PROGRESS
    assert event.raw_input == {"file_path": "/etc/hosts"}


def test_mapping_user_tool_result_success_to_tool_call_completed():
    raw = {
        "type": "user",
        "message": {"content": [{
            "type": "tool_result",
            "tool_use_id": "toolu_01",
            "is_error": False,
            "content": "file contents",
        }]},
    }
    event = parse_structured_event(hr.map_stream_event(raw))
    assert isinstance(event, ToolCallEvent)
    assert event.tool_call_id == "toolu_01"
    assert event.status is ToolCallStatus.COMPLETED
    assert event.raw_output == {"content": "file contents"}


def test_mapping_user_tool_result_error_to_tool_call_failed():
    raw = {
        "type": "user",
        "message": {"content": [{
            "type": "tool_use_result",
            "tool_use_id": "toolu_02",
            "is_error": True,
            "content": "permission denied",
        }]},
    }
    event = parse_structured_event(hr.map_stream_event(raw))
    assert isinstance(event, ToolCallEvent)
    assert event.tool_call_id == "toolu_02"
    assert event.status is ToolCallStatus.FAILED


def test_mapping_result_success_to_usage_result():
    raw = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 1234,
        "total_cost_usd": 0.045,
        "num_turns": 3,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 0,
        },
    }
    event = parse_structured_event(hr.map_stream_event(raw))
    assert isinstance(event, UsageResultEvent)
    assert event.stop_reason == "success"
    assert event.cost_usd == 0.045
    assert event.total_tokens == 150
    assert event.input_tokens == 100
    assert event.output_tokens == 50


def test_mapping_result_error_to_error_event():
    raw = {
        "type": "result",
        "subtype": "error_max_turns",
        "is_error": True,
        "result": "exceeded max turns",
    }
    event = parse_structured_event(hr.map_stream_event(raw))
    assert isinstance(event, ErrorEvent)
    assert event.message == "exceeded max turns"


def test_mapping_rate_limit_event_carries_resets_at_unix():
    # This is the load-bearing mapping for the rate-limit pause path:
    # `resetsAt` (camelCase from Claude) → `resets_at` (snake_case in our model).
    raw = {
        "type": "rate_limit_event",
        "session_id": "sess-xyz",
        "rate_limit_info": {
            "status": "allowed_warning",
            "resetsAt": 1784070000,
            "rateLimitType": "five_hour",
            "utilization": 0.97,
            "isUsingOverage": False,
            "surpassedThreshold": 0.9,
        },
    }
    event = parse_structured_event(hr.map_stream_event(raw))
    assert isinstance(event, RateLimitEvent)
    assert event.status is RateLimitStatus.ALLOWED_WARNING
    assert event.resets_at == 1784070000
    assert event.rate_limit_type is RateLimitType.FIVE_HOUR
    assert event.utilization == 0.97
    assert event.is_using_overage is False
    assert event.surpassed_threshold == 0.9


# ---- 429 pause: rate_limit_event.resets_at drives the pause ---------------


@pytest.mark.asyncio
async def test_rate_limit_event_drives_set_paused_until(monkeypatch):
    # The card-eis: instead of FALLBACK_PAUSE_HOURS, use the typed resets_at
    # as the pause deadline. set_paused_until must be called with the parsed
    # datetime + the session's provider (NOT the FALLBACK_HOURS timedelta).
    reset_ts = 1784070000
    expected_dt = datetime.fromtimestamp(reset_ts, UTC)
    set_paused_mock = AsyncMock()
    monkeypatch.setattr(
        "app.kanban.dispatch_pause.set_paused_until", set_paused_mock,
    )

    event = RateLimitEvent(
        status=RateLimitStatus.ALLOWED_WARNING,
        resets_at=reset_ts,
        rate_limit_type=RateLimitType.FIVE_HOUR,
        utilization=0.97,
    )
    await hr._on_rate_limit_event(event, provider="anthropic")

    assert set_paused_mock.await_count == 1
    args, kwargs = set_paused_mock.call_args
    # set_paused_until(session, when, *, provider=...) — the runner opens
    # its own DB session because it's fire-and-forget; the deadline lands at
    # args[1] and provider at the kwarg.
    pause_until = args[1] if len(args) > 1 else kwargs.get("when") or kwargs.get("until")
    assert pause_until == expected_dt
    assert kwargs.get("provider") == "anthropic"


@pytest.mark.asyncio
async def test_rate_limit_event_without_resets_at_falls_back(monkeypatch):
    # Defensive: carrier has never been documented as required (it's best-effort
    # per the structured_events.RateLimitEvent docstring). When resets_at is
    # absent, fall back to FALLBACK_PAUSE_HOURS — same shape as the tmux path,
    # just clearly logged so an operator can spot the regression.
    set_paused_mock = AsyncMock()
    monkeypatch.setattr(
        "app.kanban.dispatch_pause.set_paused_until", set_paused_mock,
    )
    event = RateLimitEvent(
        status=RateLimitStatus.ALLOWED_WARNING,
        resets_at=None,
        rate_limit_type=None,
        utilization=None,
    )
    await hr._on_rate_limit_event(event, provider="anthropic")
    assert set_paused_mock.await_count == 1
    # The fallback is provider-agnostic (legacy global pause shape). The exact
    # duration isn't asserted — the regression guard is "we didn't silently drop
    # the pause".


# ---- end-to-end fixture cycle ----------------------------------------------


@pytest.mark.asyncio
async def test_full_dispatch_cycle_no_reap_loop(monkeypatch, tmp_path, capsys):
    """End-to-end: spawn → live → exit → reaper cleans up, no reap-loop.

    Replaces a real ``claude -p`` subprocess with a tiny inline script that
    prints the same stream-json sequence the CLI emits (system/init → assistant
    text → result success). The point of the test is to validate the *plumbing*
    — subprocess lifecycle, liveness registry, reaper integration — not the
    Claude-side event vocabulary (covered by the mapping tests above).
    """
    import sys as stdlib_sys

    # 1. Write the fake-CLI fixture: a Python script that emits one JSON event
    #    per line and exits 0.
    fake_cli = tmp_path / "fake_claude.py"
    fake_cli.write_text(
        "import json, sys, time\n"
        "def emit(p): sys.stdout.write(json.dumps(p) + '\\n'); sys.stdout.flush()\n"
        "emit({'type':'system','subtype':'init','session_id':'sess-fixture',"
        "     'cwd':'.','model':'claude-opus-4-8','permissionMode':'acceptEdits'})\n"
        "emit({'type':'assistant','message':{'content':[{'type':'text','text':'ok'}]}})\n"
        "emit({'type':'result','subtype':'success','is_error':False,"
        "     'duration_ms':5,'total_cost_usd':0.0,'num_turns':1,"
        "     'usage':{'input_tokens':1,'output_tokens':1}})\n"
        "sys.exit(0)\n"
    )

    # 2. Point headless_runner at the fixture instead of `claude`.
    monkeypatch.setattr(hr, "resolve_cli_executable",
                        lambda cli_id: stdlib_sys.executable)

    # 3. Run a single headless subprocess via the runner's internals.
    # Use a real worktree layout (project_root/.claude/worktrees/<name>) so
    # live_headless_sessions()'s pidfile scan finds the run by name.
    project_root = tmp_path / "proj"
    worktree = project_root / ".claude" / "worktrees" / "k-fixture-1"
    worktree.mkdir(parents=True)
    proc = await asyncio.create_subprocess_exec(
        stdlib_sys.executable, str(fake_cli),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(worktree),
        start_new_session=True,
    )
    # Register it as if headless_runner had spawned it, then consume the stream
    # via the parser. We do this manually here to exercise the public surface
    # rather than the private _run_async path. The new registry holds a
    # HeadlessRunRecord (pid + worktree + log path); live_headless_sessions
    # OS-checks each record, so we must point it at the real subprocess.
    rec = hr.HeadlessRunRecord(
        session_name="k-fixture-1",
        pid=proc.pid,
        worktree_path=str(worktree),
        log_path=worktree / "events.jsonl",
        started_at=time.time(),
    )
    hr._headless_processes["k-fixture-1"] = rec
    # Also write a durable pidfile and register the project root so
    # live_headless_sessions() (which now reads pidfiles, not memory)
    # can find this run.
    hr._write_pidfile(rec)
    hr._remember_project_root(str(project_root))

    # 4. While the proc is still running, the liveness source must report it.
    live = hr.live_headless_sessions()
    assert "k-fixture-1" in live, (
        "liveness source did not report the running fixture — would dispatch-loop"
    )

    # 5. Drain the stream through the parser.
    parsed = []
    async for line in proc.stdout:
        raw = line.decode().strip()
        if not raw:
            continue
        try:
            import json
            payload = json.loads(raw)
        except Exception:
            continue
        parsed.append(payload["type"])
    rc = await proc.wait()

    # 6. After exit, the liveness source must NOT report it.
    hr._headless_processes.pop("k-fixture-1", None)
    assert rc == 0
    assert "k-fixture-1" not in hr.live_headless_sessions()
    assert parsed == ["system", "assistant", "result"]


# ---- AC 2 + AC 4: single unmapped event must not orphan the subprocess ----
#
# The card-eis: `map_stream_event` lets an unknown payload pass through
# (`{"type": ptype, **payload}`) so the schema's ValidationError carries the
# original event verbatim — a debug-friendly choice. But _consume_stream
# didn't catch the exception, so run_headless's finally block cleaned the
# registry while the subprocess kept running. live_headless_sessions then
# reported the session as dead → reaper released the claim → dispatcher
# re-spawned a second agent in the same worktree/branch.
#
# Fix contract: a single unmapped event is logged and skipped (same shape as
# the existing non-JSON-line tolerance), so the run continues to drain the
# rest of the stream and ends naturally. After the run, no live subprocess
# may remain and the registry must be clean.


@pytest.mark.asyncio
async def test_run_headless_does_not_leave_subprocess_on_unmapped_event(
    monkeypatch, tmp_path,
):
    """AC 2 + AC 4 — regression for the orphan-subprocess bug.

    Before the fix: parse_structured_event raised ValidationError on the
    unknown ``type``; _consume_stream didn't catch it; run_headless's finally
    block popped the registry + released the slot but never terminated the
    subprocess → orphan → reap → second agent in the same branch.

    After the fix: the unmapped event is logged and skipped, the fake CLI's
    follow-up ``result`` event drains through, and the subprocess exits 0
    on its own. run_headless returns normally, the PID is dead, and the
    registry is clean.
    """
    import os
    import sys as stdlib_sys

    pidfile = tmp_path / "fake_cli.pid"
    fake_cli = tmp_path / "fake_claude.py"
    fake_cli.write_text(
        "import json, sys, os\n"
        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "def emit(p): sys.stdout.write(json.dumps(p) + '\\n'); sys.stdout.flush()\n"
        "emit({'type':'system','subtype':'init','session_id':'sess-bad-event',"
        "     'cwd':'.','model':'claude-opus-4-8','permissionMode':'acceptEdits'})\n"
        "emit({'type':'future_event_v99','data':'we do not map this'})\n"
        "emit({'type':'result','subtype':'success','is_error':False,"
        "     'duration_ms':1,'total_cost_usd':0.0,'num_turns':1,"
        "     'usage':{'input_tokens':1,'output_tokens':1}})\n"
        "sys.exit(0)\n"
    )
    # run_headless calls _build_argv which prepends ``-p --output-format
    # stream-json --verbose -- <prompt>`` to the executable. A bare python
    # executable would reject the unknown ``-p`` flag — use a shell wrapper
    # that ignores its argv and runs the Python fixture instead.
    wrapper = tmp_path / "fake_claude.sh"
    wrapper.write_text(
        f"#!/bin/sh\nexec {stdlib_sys.executable} '{fake_cli}' \"$@\"\n"
    )
    wrapper.chmod(0o755)
    monkeypatch.setattr(hr, "resolve_cli_executable", lambda cli_id: str(wrapper))

    # Before the fix: this raises pydantic.ValidationError.
    # After the fix: returns normally with exit_code=0.
    result = await hr.run_headless(
        cli_id="claude-code", directory=str(tmp_path), prompt="hi",
        session_name="k-fixture-bad-event", skip_permissions=True,
        provider="anthropic", model=None,
    )
    assert result["exit_code"] == 0

    # 1. Subprocess is gone.
    pid = int(pidfile.read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)

    # 2. Registry + liveness source are clean.
    assert "k-fixture-bad-event" not in hr._headless_processes
    assert "k-fixture-bad-event" not in hr.live_headless_sessions()


# ---- AC 1: an unexpected exception must terminate the subprocess ------------
#
# The per-event ValidationError fix above is necessary but not sufficient:
# any other exception that escapes the read loop (a bug in _on_event, an OS
# error, an asyncio.CancelledError) could still orphan the subprocess.
# run_headless's finally must guarantee the subprocess is terminated and
# waited for, on every exit path.


@pytest.mark.asyncio
async def test_run_headless_terminates_subprocess_on_unexpected_exception(
    monkeypatch, tmp_path,
):
    """AC 1 — Unexpected exception → subprocess terminated and waited for.

    The fake CLI emits one well-formed event then sleeps long enough that,
    without the fix, the test would either hang on the orphan or the
    ProcessLookupError probe would fail because the process is still alive.
    The monkeypatched ``_on_event`` raises a non-ValidationError so the
    catch-around-the-parser does NOT swallow it — the exception is real and
    the finally-block termination is the only thing that prevents the
    orphan.
    """
    import os
    import sys as stdlib_sys

    pidfile = tmp_path / "fake_cli.pid"
    fake_cli = tmp_path / "fake_claude.py"
    fake_cli.write_text(
        "import json, sys, os, time\n"
        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "def emit(p): sys.stdout.write(json.dumps(p) + '\\n'); sys.stdout.flush()\n"
        "emit({'type':'system','subtype':'init','session_id':'sess-explode',"
        "     'cwd':'.','model':'claude-opus-4-8','permissionMode':'acceptEdits'})\n"
        "time.sleep(60)\n"
    )
    # See AC2/AC4 test for why the wrapper is needed.
    wrapper = tmp_path / "fake_claude.sh"
    wrapper.write_text(
        f"#!/bin/sh\nexec {stdlib_sys.executable} '{fake_cli}' \"$@\"\n"
    )
    wrapper.chmod(0o755)
    monkeypatch.setattr(hr, "resolve_cli_executable", lambda cli_id: str(wrapper))

    async def _explode(*args, **kwargs):
        raise RuntimeError("simulated unexpected failure")
    monkeypatch.setattr(hr, "_on_event", _explode)

    # The unexpected exception must propagate to the caller (so the operator
    # sees the real cause); the cleanup must have already terminated the
    # subprocess by the time it does.
    with pytest.raises(RuntimeError, match="simulated unexpected failure"):
        await hr.run_headless(
            cli_id="claude-code", directory=str(tmp_path), prompt="hi",
            session_name="k-fixture-explode", skip_permissions=True,
            provider="anthropic", model=None,
        )

    # Subprocess must be dead. Poll briefly to absorb the SIGTERM-reap race
    # but bail fast — the test fixture sleeps 60s, so anything still alive
    # at this point is the orphan we're trying to prove is impossible.
    pid = int(pidfile.read_text().strip())
    deadline = asyncio.get_event_loop().time() + 5.0
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        if asyncio.get_event_loop().time() > deadline:
            pytest.fail(f"subprocess {pid} still alive after run_headless raised")
        await asyncio.sleep(0.05)

    assert "k-fixture-explode" not in hr._headless_processes
    assert "k-fixture-explode" not in hr.live_headless_sessions()


# ---- AC 3: an exception escaping the run task must be visibly logged --------
#
# The previous done_callback was ``_headless_start_tasks.discard``, which
# silently dropped the task's exception. The exception then only surfaced at
# GC as a "Task exception was never retrieved" warning — invisible from the
# dispatch log. The replacement callback logs the exception via the
# runner's logger, so an operator scanning the log sees both the breadcrumb
# and the full traceback.


def test_headless_task_done_callback_logs_exceptions(caplog):
    """AC 3 — done_callback logs the task's exception, not just discards it.

    The callback's job is twofold: keep the strong-ref set from leaking
    (the original ``_headless_start_tasks.discard`` purpose) AND surface
    any exception to the operator-visible logger. This test pins the second
    half — a regression here would silently drop run failures again, which
    is exactly the gap the card calls out.
    """
    import logging

    async def _boom():
        raise RuntimeError("simulated boom")

    async def _runner():
        task = asyncio.create_task(_boom(), name="k-test-boom")
        task.add_done_callback(hr._headless_task_done_callback)
        # add_done_callback fires synchronously when the task is done; the
        # exception itself is re-raised by ``await task``, which we catch
        # here only because the test isn't about exception propagation.
        try:
            await task
        except RuntimeError:
            pass

    with caplog.at_level(logging.ERROR, logger="app.kanban.headless_runner"):
        asyncio.run(_runner())

    assert any(
        "simulated boom" in rec.getMessage() for rec in caplog.records
    ), f"expected a log record mentioning 'simulated boom'; got: {[r.getMessage() for r in caplog.records]}"


# ---- AC 1 (hardened): a SIGTERM-ignoring child must still be reaped ------
#
# The first AC 1 test uses a fake CLI that responds to SIGTERM (Python's
# ``time.sleep`` is interruptible). A pathological child that traps SIGTERM
# and ignores it (e.g. via ``signal.signal(SIGTERM, SIG_IGN)``) is the real
# reason for the SIGKILL fallback in the finally block — without it, the
# stderr drain in ``_consume_stream`` would hang on the still-open pipe.
# This test pins that escape hatch.


@pytest.mark.asyncio
async def test_run_headless_kills_subprocess_that_ignores_sigterm(
    monkeypatch, tmp_path,
):
    """AC 1 (hardened) — SIGTERM-ignoring child → SIGKILL fallback fires.

    The fake CLI installs ``SIG_IGN`` on SIGTERM, then sleeps long enough
    that a hung child would blow the test's hard pytest-timeout cap. With
    the fix: ``_consume_stream``'s finally terminates (ignored), runs out
    the 2s grace, then kills the child. The test finishes in ~2s and the
    subprocess is reaped.
    """
    import os
    import sys as stdlib_sys

    pidfile = tmp_path / "fake_cli.pid"
    fake_cli = tmp_path / "fake_claude.py"
    fake_cli.write_text(
        "import json, sys, os, time, signal\n"
        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        # Trap SIGTERM — the runner's first-line cleanup is harmless
        # against this child; only the SIGKILL fallback actually stops it.
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "def emit(p): sys.stdout.write(json.dumps(p) + '\\n'); sys.stdout.flush()\n"
        "emit({'type':'system','subtype':'init','session_id':'sess-sigign',"
        "     'cwd':'.','model':'claude-opus-4-8','permissionMode':'acceptEdits'})\n"
        "time.sleep(120)\n"
    )
    wrapper = tmp_path / "fake_claude.sh"
    wrapper.write_text(
        f"#!/bin/sh\nexec {stdlib_sys.executable} '{fake_cli}' \"$@\"\n"
    )
    wrapper.chmod(0o755)
    monkeypatch.setattr(hr, "resolve_cli_executable", lambda cli_id: str(wrapper))

    async def _explode(*args, **kwargs):
        raise RuntimeError("forced failure against SIGTERM-ignoring child")
    monkeypatch.setattr(hr, "_on_event", _explode)

    with pytest.raises(RuntimeError, match="forced failure against SIGTERM-ignoring child"):
        await hr.run_headless(
            cli_id="claude-code", directory=str(tmp_path), prompt="hi",
            session_name="k-fixture-sigign", skip_permissions=True,
            provider="anthropic", model=None,
        )

    # Subprocess must be reaped (SIGKILL path, not SIGTERM).
    pid = int(pidfile.read_text().strip())
    deadline = asyncio.get_event_loop().time() + 5.0
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        if asyncio.get_event_loop().time() > deadline:
            pytest.fail(f"SIGTERM-ignoring subprocess {pid} still alive after kill fallback")
        await asyncio.sleep(0.05)

    assert "k-fixture-sigign" not in hr._headless_processes
    assert "k-fixture-sigign" not in hr.live_headless_sessions()


# ---- AC 2 (hardened): a malformed payload (KeyError in map) must skip -----
#
# The first AC 2 test exercises the pydantic.ValidationError branch. But
# ``map_stream_event`` itself can raise KeyError/TypeError/AttributeError
# when a payload has the right discriminator shape but a missing required
# field (``payload["session_id"]``, ``block["id"]``). Those exceptions
# happen BEFORE pydantic sees the dict and would, without the broadened
# catch, kill the run the same way the original bug did.


@pytest.mark.asyncio
async def test_run_headless_does_not_leave_subprocess_on_malformed_payload(
    monkeypatch, tmp_path,
):
    """AC 2 (hardened) — KeyError/TypeError from map_stream_event is also tolerated.

    The fake CLI emits a ``system/init`` event without ``session_id``,
    which trips ``payload["session_id"]`` inside ``map_stream_event`` (a
    KeyError). Before the fix that propagated up and orphaned the
    subprocess; after, it's logged and skipped so the run continues
    past the malformed event and exits naturally.
    """
    import os
    import sys as stdlib_sys

    pidfile = tmp_path / "fake_cli.pid"
    fake_cli = tmp_path / "fake_claude.py"
    fake_cli.write_text(
        "import json, sys, os\n"
        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "def emit(p): sys.stdout.write(json.dumps(p) + '\\n'); sys.stdout.flush()\n"
        # Missing session_id → map_stream_event's payload['session_id'] KeyErrors.
        "emit({'type':'system','subtype':'init',"
        "     'cwd':'.','model':'claude-opus-4-8','permissionMode':'acceptEdits'})\n"
        "emit({'type':'result','subtype':'success','is_error':False,"
        "     'duration_ms':1,'total_cost_usd':0.0,'num_turns':1,"
        "     'usage':{'input_tokens':1,'output_tokens':1}})\n"
        "sys.exit(0)\n"
    )
    wrapper = tmp_path / "fake_claude.sh"
    wrapper.write_text(
        f"#!/bin/sh\nexec {stdlib_sys.executable} '{fake_cli}' \"$@\"\n"
    )
    wrapper.chmod(0o755)
    monkeypatch.setattr(hr, "resolve_cli_executable", lambda cli_id: str(wrapper))

    result = await hr.run_headless(
        cli_id="claude-code", directory=str(tmp_path), prompt="hi",
        session_name="k-fixture-malformed", skip_permissions=True,
        provider="anthropic", model=None,
    )
    assert result["exit_code"] == 0

    pid = int(pidfile.read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)

    assert "k-fixture-malformed" not in hr._headless_processes
    assert "k-fixture-malformed" not in hr.live_headless_sessions()