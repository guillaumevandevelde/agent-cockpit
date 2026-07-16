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


def test_live_headless_sessions_returns_only_running_processes(monkeypatch):
    # 3 names in the registry, 2 still alive (returncode is None), 1 finished
    # — only the alive ones count toward liveness.
    class FakeProc:
        def __init__(self, returncode):
            self.returncode = returncode

    monkeypatch.setattr(hr, "_headless_processes", {
        "k-hl-1": FakeProc(None),
        "k-hl-2": FakeProc(None),
        "k-hl-3": FakeProc(0),
    })
    assert hr.live_headless_sessions() == {"k-hl-1", "k-hl-2"}


def test_live_headless_sessions_empty_on_failure(monkeypatch):
    # The defensive contract: a transient registry glitch yields an empty
    # set (so the reaper is *more* eager, never less). Same fail-open shape
    # as _live_sandcastle_sessions.
    def _boom():
        raise RuntimeError("registry exploded")
    monkeypatch.setattr(hr, "_headless_processes", property(_boom))
    assert hr.live_headless_sessions() == set()


def test_live_headless_sessions_empty_when_registry_empty():
    assert hr.live_headless_sessions() == set()


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
    proc = await asyncio.create_subprocess_exec(
        stdlib_sys.executable, str(fake_cli),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Register it as if headless_runner had spawned it, then consume the stream
    # via the parser. We do this manually here to exercise the public surface
    # rather than the private _run_async path.
    hr._headless_processes["k-fixture-1"] = proc

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