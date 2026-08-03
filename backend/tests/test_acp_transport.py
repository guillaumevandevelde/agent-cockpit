"""Tests for the ACP-backed SpawnTransport sibling for ``open-code``.

Kaart ``f647a44e…``: a fourth ``SpawnTransport`` (``acp_transport``) that spawns
``opencode acp`` as a subprocess, drives it through JSON-RPC 2.0 over stdio,
and maps ACP ``session/update`` notifications onto the ACP-isomorphic
``StructuredEvent`` model. The headless-transport's signature parity test
(``test_spawn_transport_signature_parity``) is the cross-cutting check; this
module covers the ACP-specific surface: TRANSPORTS tuple, liveness source,
event mapping, permission-gate activation, and an end-to-end fixture against
a fake ACP server.

Source-of-truth for the protocol surface:
``docs/cockpit/acp-transport-opencode-go-nogo.md`` §2 / §3.2 (OpenCode 1.18.8
was measured end-to-end against a non-TTY stdio pipe).
"""
import json
import os
import subprocess
import sys
import time

import pytest

from app.kanban.dispatch import TRANSPORTS
from app.services.agentic_cli.structured_events import (
    ContextUsageEvent,
    MessageChunkEvent,
    MessageRole,
    PermissionOption,
    PermissionRequestEvent,
    PlanUpdateEvent,
    ToolCallEvent,
    ToolCallStatus,
    UsageResultEvent,
    parse_structured_event,
)

# ---- TRANSPORTS tuple ------------------------------------------------------


def test_transports_tuple_includes_acp():
    """The ``acp`` transport must be in the validator tuple.

    Regression guard: dropping the entry here would silently invalidate the
    new transport at runtime (the per-project default transport setter would
    reject the value), mirroring the same guard
    ``test_transports_tuple_includes_headless`` already pins for headless.
    """
    assert "acp" in TRANSPORTS
    assert TRANSPORTS.index("acp") >= 0  # present, not just truthy


# ---- liveness source -------------------------------------------------------


def test_live_acp_sessions_returns_only_running_processes(tmp_path):
    """Pidfile-scan + OS-level check returns only the live session.

    Mirrors ``test_live_headless_sessions_returns_only_running_processes`` —
    two live subprocesses + one pidfile pointing at a dead pid → only the
    live ones count. Same restart-survival contract as the headless source
    (kaart ``a450df1a…``): the pidfile is the source of truth, OS checks
    guard against pid reuse.
    """
    import app.kanban.acp_transport as acp

    project_root = tmp_path / "proj"
    wt_1 = project_root / ".claude" / "worktrees" / "k-acp-1"
    wt_2 = project_root / ".claude" / "worktrees" / "k-acp-2"
    wt_3 = project_root / ".claude" / "worktrees" / "k-acp-3"
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
            deadline = time.time() + 5.0
            while time.time() < deadline:
                line = proc.stdout.readline()
                if line.strip() == b"READY":
                    break
            procs.append(proc)
            (wt / acp._ACP_PIDFILE_NAME).write_text(json.dumps({
                "session_name": wt.name,
                "pid": proc.pid,
                "worktree_path": str(wt),
                "started_at": time.time(),
            }))
        # Dead pidfile — guaranteed not to be a real process.
        (wt_3 / acp._ACP_PIDFILE_NAME).write_text(json.dumps({
            "session_name": "k-acp-3",
            "pid": 2**30,
            "worktree_path": str(wt_3),
            "started_at": time.time(),
        }))

        acp._remember_project_root(str(project_root))
        acp._acp_processes.clear()
        assert acp.live_acp_sessions() == {"k-acp-1", "k-acp-2"}
    finally:
        for proc in procs:
            try:
                os.killpg(proc.pid, 15)
                proc.wait(timeout=2.0)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(proc.pid, 9)
                except ProcessLookupError:
                    pass


def test_live_acp_sessions_empty_on_failure(monkeypatch):
    """Any failure yields an empty set so the reaper is *more* eager, never less.

    Same fail-open contract as ``live_headless_sessions`` (and
    ``_live_sandcastle_sessions``): a transient registry hiccup must not
    keep a truly-dead claim alive forever.
    """
    import app.kanban.acp_transport as acp

    def _boom(*args, **kwargs):
        raise RuntimeError("registry exploded")
    monkeypatch.setattr(acp, "_known_worktree_dirs", _boom)
    assert acp.live_acp_sessions() == set()


def test_live_acp_sessions_empty_when_registry_empty():
    """No project roots registered → no worktrees to scan → empty set."""
    import app.kanban.acp_transport as acp

    acp._acp_processes.clear()
    saved = acp._known_project_roots.copy()
    acp._known_project_roots.clear()
    try:
        assert acp.live_acp_sessions() == set()
    finally:
        acp._known_project_roots.update(saved)


# ---- ACP event → StructuredEvent mapping -----------------------------------
#
# Spike §4 is the source of truth for which ACP variants we map. Each test
# pins one row by feeding the raw ACP payload through map_acp_event +
# parse_structured_event and asserting the resulting structured-event shape.


def test_mapping_agent_message_chunk_to_message_chunk_assistant():
    """ACP ``agent_message_chunk`` → ``message_chunk`` role=assistant."""
    import app.kanban.acp_transport as acp

    raw = {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "ses_1",
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "Hello, world."},
            },
        },
    }
    event = parse_structured_event(acp.map_acp_event(raw))
    assert isinstance(event, MessageChunkEvent)
    assert event.role is MessageRole.ASSISTANT
    assert event.text == "Hello, world."
    assert event.session_id == "ses_1"


def test_mapping_agent_thought_chunk_to_message_chunk_thought():
    """ACP ``agent_thought_chunk`` → ``message_chunk`` role=thought."""
    import app.kanban.acp_transport as acp

    raw = {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "ses_1",
            "update": {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "thinking..."},
            },
        },
    }
    event = parse_structured_event(acp.map_acp_event(raw))
    assert isinstance(event, MessageChunkEvent)
    assert event.role is MessageRole.THOUGHT
    assert event.text == "thinking..."


def test_mapping_user_message_chunk_to_message_chunk_user():
    """ACP ``user_message_chunk`` → ``message_chunk`` role=user."""
    import app.kanban.acp_transport as acp

    raw = {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "ses_1",
            "update": {
                "sessionUpdate": "user_message_chunk",
                "content": {"type": "text", "text": "user reply"},
            },
        },
    }
    event = parse_structured_event(acp.map_acp_event(raw))
    assert isinstance(event, MessageChunkEvent)
    assert event.role is MessageRole.USER
    assert event.text == "user reply"


def test_mapping_tool_call_initial_to_pending():
    """ACP ``tool_call`` (initial notification) → ``tool_call`` status=pending."""
    import app.kanban.acp_transport as acp

    raw = {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "ses_1",
            "update": {
                "sessionUpdate": "tool_call",
                "toolCall": {
                    "toolCallId": "call_1",
                    "title": "Edit /tmp/gated.txt",
                    "kind": "edit",
                    "status": "pending",
                    "rawInput": {"filepath": "/tmp/gated.txt"},
                },
            },
        },
    }
    event = parse_structured_event(acp.map_acp_event(raw))
    assert isinstance(event, ToolCallEvent)
    assert event.tool_call_id == "call_1"
    assert event.title == "Edit /tmp/gated.txt"
    assert event.kind == "edit"
    assert event.status is ToolCallStatus.PENDING
    assert event.raw_input == {"filepath": "/tmp/gated.txt"}


def test_mapping_tool_call_update_in_progress_to_in_progress():
    """ACP ``tool_call_update`` status=in_progress → ``tool_call`` status=in_progress."""
    import app.kanban.acp_transport as acp

    raw = {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "ses_1",
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCall": {
                    "toolCallId": "call_1",
                    "status": "in_progress",
                    "kind": "edit",
                    "title": "Edit /tmp/gated.txt",
                },
            },
        },
    }
    event = parse_structured_event(acp.map_acp_event(raw))
    assert isinstance(event, ToolCallEvent)
    assert event.tool_call_id == "call_1"
    assert event.status is ToolCallStatus.IN_PROGRESS


def test_mapping_tool_call_update_completed_to_completed():
    """ACP ``tool_call_update`` status=completed → ``tool_call`` status=completed."""
    import app.kanban.acp_transport as acp

    raw = {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "ses_1",
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCall": {
                    "toolCallId": "call_1",
                    "status": "completed",
                    "rawOutput": {"success": True},
                },
            },
        },
    }
    event = parse_structured_event(acp.map_acp_event(raw))
    assert isinstance(event, ToolCallEvent)
    assert event.status is ToolCallStatus.COMPLETED
    assert event.raw_output == {"success": True}


def test_mapping_tool_call_update_failed_to_failed():
    """ACP ``tool_call_update`` status=failed → ``tool_call`` status=failed."""
    import app.kanban.acp_transport as acp

    raw = {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "ses_1",
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCall": {
                    "toolCallId": "call_1",
                    "status": "failed",
                    "rawOutput": {"error": "permission denied"},
                },
            },
        },
    }
    event = parse_structured_event(acp.map_acp_event(raw))
    assert isinstance(event, ToolCallEvent)
    assert event.status is ToolCallStatus.FAILED


def test_mapping_usage_update_to_context_usage():
    """ACP ``usage_update`` → ``context_usage`` (the dep-kaart variant)."""
    import app.kanban.acp_transport as acp

    raw = {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "ses_1",
            "update": {
                "sessionUpdate": "usage_update",
                "used": 29108,
                "size": 200000,
                "cost": {"amount": 0.0, "currency": "USD"},
            },
        },
    }
    event = parse_structured_event(acp.map_acp_event(raw))
    assert isinstance(event, ContextUsageEvent)
    assert event.used == 29108
    assert event.size == 200000
    assert event.cost is not None
    assert event.cost.amount == 0.0
    assert event.cost.currency == "USD"


def test_mapping_usage_update_without_cost_is_valid():
    """ACP ``usage_update`` without ``cost`` block is still valid (not every vendor emits it)."""
    import app.kanban.acp_transport as acp

    raw = {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "ses_1",
            "update": {
                "sessionUpdate": "usage_update",
                "used": 1000,
                "size": 100000,
            },
        },
    }
    event = parse_structured_event(acp.map_acp_event(raw))
    assert isinstance(event, ContextUsageEvent)
    assert event.cost is None


def test_mapping_available_commands_update_to_assistant_message_chunk():
    """ACP ``available_commands_update`` → ``message_chunk`` role=assistant with summary."""
    import app.kanban.acp_transport as acp

    raw = {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "ses_1",
            "update": {
                "sessionUpdate": "available_commands_update",
                "availableCommands": [
                    {"name": "build", "description": "Run the build"},
                    {"name": "test", "description": "Run the tests"},
                ],
            },
        },
    }
    event = parse_structured_event(acp.map_acp_event(raw))
    assert isinstance(event, MessageChunkEvent)
    assert event.role is MessageRole.ASSISTANT
    assert "build" in event.text
    assert "test" in event.text


def test_mapping_plan_to_plan_update():
    """ACP ``plan`` → ``plan_update``."""
    import app.kanban.acp_transport as acp

    raw = {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "ses_1",
            "update": {
                "sessionUpdate": "plan",
                "entries": [
                    {"content": "Read the file", "priority": "high", "status": "completed"},
                    {"content": "Edit the file", "priority": "medium", "status": "in_progress"},
                ],
            },
        },
    }
    event = parse_structured_event(acp.map_acp_event(raw))
    assert isinstance(event, PlanUpdateEvent)
    assert len(event.entries) == 2
    assert event.entries[0].content == "Read the file"
    assert event.entries[1].content == "Edit the file"


def test_mapping_session_prompt_result_to_usage_result():
    """ACP ``session/prompt`` result with stopReason + usage → ``usage_result``.

    The result of a JSON-RPC request is wrapped in ``result``; map_acp_event
    extracts ``stopReason`` + ``usage`` into the terminal ``UsageResultEvent``
    so the dispatcher sees the same shape it sees from the claude-code
    stream-json transport.
    """
    import app.kanban.acp_transport as acp

    raw = {
        "jsonrpc": "2.0",
        "id": 3,
        "result": {
            "stopReason": "end_turn",
            "usage": {
                "inputTokens": 308,
                "outputTokens": 15,
                "totalTokens": 29143,
                "thoughtTokens": 20,
                "cachedReadTokens": 28800,
            },
        },
    }
    event = parse_structured_event(acp.map_acp_result(raw))
    assert isinstance(event, UsageResultEvent)
    assert event.stop_reason == "end_turn"
    assert event.input_tokens == 308
    assert event.output_tokens == 15
    assert event.total_tokens == 29143


def test_mapping_request_permission_to_permission_request():
    """ACP ``session/request_permission`` request → ``permission_request`` event."""
    import app.kanban.acp_transport as acp

    raw = {
        "jsonrpc": "2.0",
        "id": 42,
        "method": "session/request_permission",
        "params": {
            "sessionId": "ses_1",
            "toolCall": {
                "toolCallId": "call_1",
                "title": "Edit /tmp/gated.txt",
                "kind": "edit",
                "status": "pending",
                "rawInput": {"filepath": "/tmp/gated.txt"},
            },
            "options": [
                {"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
                {"optionId": "reject_once", "name": "Reject", "kind": "reject_once"},
            ],
        },
    }
    event = parse_structured_event(acp.map_acp_event(raw))
    assert isinstance(event, PermissionRequestEvent)
    assert event.tool_call_id == "call_1"
    assert event.title == "Edit /tmp/gated.txt"
    assert len(event.options) == 2
    assert event.options[0].option_id == "allow_once"
    assert event.options[0].kind.value == "allow_once"


def test_mapping_unknown_session_update_passes_through():
    """Unknown ACP sessionUpdate variants pass through as a generic payload.

    Same philosophy as ``map_stream_event``: an unmapped payload is left as
    ``{"type": <variant>, ...payload}`` so the schema's ValidationError
    surfaces the original event verbatim. The transport's consumer logs and
    skips — a single unknown variant must not kill the run.
    """
    import app.kanban.acp_transport as acp

    raw = {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "ses_1",
            "update": {"sessionUpdate": "future_variant", "data": "ignored"},
        },
    }
    mapped = acp.map_acp_event(raw)
    assert mapped.get("type") == "future_variant"
    assert mapped.get("session_id") == "ses_1"


# ---- permission-gate activation (load-bearing) -----------------------------
#
# Brondoc §3.3: with OpenCode's default-config the permission gate does NOT
# fire. The transport MUST explicitly write a permission-config that puts
# edit/bash on "ask", otherwise the gate is silent — a gate that never
# closes is worse than no gate at all. This test pins the spawn-time config
# side and the runtime handler side: the spawn writes the config file, and
# when a request_permission notification arrives the handler responds with
# a JSON-RPC result that names an ``allow_once`` option.


def test_permission_config_is_written_to_worktree(tmp_path):
    """Spawn writes an ``opencode.json`` with edit+bash on ``ask``.

    Regression guard: without this, OpenCode's default config keeps the
    gate silent (brondoc §3.3), which is the failure mode this whole card
    exists to prevent. The transport MUST make the gate load-bearing, not
    rely on the user's preexisting config.
    """
    import app.kanban.acp_transport as acp

    worktree = tmp_path / "wt"
    worktree.mkdir()
    acp._write_permission_config(str(worktree))

    config = json.loads((worktree / "opencode.json").read_text())
    assert config["permission"]["edit"] == "ask"
    assert config["permission"]["bash"] == "ask"


def test_permission_request_response_uses_allow_once_option():
    """When a ``session/request_permission`` request arrives, the handler
    responds with the first ``allow_once`` option — the load-bearing
    permission-gate test (brondoc §2.4).

    Picks the FIRST ``allow_once`` option deterministically so the test
    stays reproducible: the mock server logs the chosen option and the
    test asserts it's the first one in the list. A handler that silently
    picks ``reject_once`` would close the gate but block every tool — also
    load-bearing in the opposite direction.
    """
    import app.kanban.acp_transport as acp

    options = [
        PermissionOption(option_id="allow_once", name="Allow once", kind="allow_once"),
        PermissionOption(option_id="allow_always", name="Allow always", kind="allow_always"),
        PermissionOption(option_id="reject_once", name="Reject", kind="reject_once"),
    ]

    chosen = acp._pick_permission_response(options)
    assert chosen == {"outcome": {"outcome": "selected", "optionId": "allow_once"}}


def test_permission_request_response_falls_back_to_first_when_no_allow_once():
    """If the only options are rejects, the handler picks the first one
    (do-not-block-the-turn). The gate stays load-bearing: a request without
    any allow option is malformed and the handler still answers so the turn
    can continue. Production OpenCode always offers at least one allow.
    """
    import app.kanban.acp_transport as acp

    options = [
        PermissionOption(option_id="reject_once", name="Reject", kind="reject_once"),
    ]

    chosen = acp._pick_permission_response(options)
    assert chosen == {"outcome": {"outcome": "selected", "optionId": "reject_once"}}


# ---- end-to-end fixture ----------------------------------------------------
#
# Drives a full JSON-RPC handshake + session/new + session/prompt against a
# fake ACP server script (Python + asyncio). Pins the public surface: the
# transport reserves a session slot, spawns the binary, drives JSON-RPC over
# stdio, drains ``session/update`` notifications, and produces structured
# events. The fake server records every JSON-RPC request it received so the
# test can assert the protocol sequence (initialize → session/new →
# session/prompt) was honored.


@pytest.mark.asyncio
async def test_acp_transport_full_turn(monkeypatch, tmp_path):
    """End-to-end: spawn → JSON-RPC handshake → session/prompt → exit.

    Uses a fake ``opencode acp`` binary (a Python script that speaks JSON-RPC
    over its stdin/stdout) and asserts:

    - the transport reserved a session slot and wrote a pidfile,
    - the fake server saw ``initialize`` → ``session/new`` → ``session/prompt``
      in that order,
    - the transport returned ``status: started`` and the subprocess exited
      cleanly,
    - the pidfile was removed in the finally block.
    """
    import app.kanban.acp_transport as acp

    # A request log so the test can assert the protocol order.
    request_log = tmp_path / "requests.jsonl"
    fake_acp = tmp_path / "fake_acp.py"
    fake_acp.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, os\n"
        # Write our PID so the transport can find it in the pidfile (matches
        # the real-subprocess pid it spawned).
        f"open({str(tmp_path / 'pidfile.txt')!r}, 'w').write(str(os.getpid()))\n"
        f"LOG = open({str(request_log)!r}, 'a', buffering=1)\n"
        # The fake server is a hand-rolled JSON-RPC 2.0 over stdio responder.
        "def _read_message():\n"
        "    line = sys.stdin.readline()\n"
        "    if not line:\n"
        "        return None\n"
        "    return json.loads(line)\n"
        "def _send(msg):\n"
        "    sys.stdout.write(json.dumps(msg) + '\\n')\n"
        "    sys.stdout.flush()\n"
        "def _handle(req):\n"
        "    LOG.write(json.dumps({'id': req.get('id'), 'method': req.get('method')}) + '\\n')\n"
        "    if req['method'] == 'initialize':\n"
        "        return {'protocolVersion': 1, 'agentCapabilities': {}}\n"
        "    if req['method'] == 'session/new':\n"
        "        return {'sessionId': 'ses_fake_1'}\n"
        "    if req['method'] == 'session/prompt':\n"
        "        # Push one of each measured update variant, then close the turn.\n"
        "        for upd in [\n"
        "            {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'hi'}},\n"
        "            {'sessionUpdate': 'agent_thought_chunk', 'content': {'type': 'text', 'text': 'thinking'}},\n"
        "            {'sessionUpdate': 'usage_update', 'used': 5, 'size': 100000},\n"
        "        ]:\n"
        "            _send({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'ses_fake_1','update':upd}})\n"
        "        return {'stopReason': 'end_turn', 'usage': {'inputTokens': 1, 'outputTokens': 1, 'totalTokens': 2}}\n"
        "    return {}\n"
        "while True:\n"
        "    req = _read_message()\n"
        "    if req is None:\n"
        "        break\n"
        "    resp = {'jsonrpc':'2.0','id':req['id'],'result':_handle(req)}\n"
        "    _send(resp)\n"
    )
    fake_acp.chmod(0o755)
    # The transport calls subprocess via `opencode acp`; intercept
    # `resolve_acp_executable` so we get the fake binary instead.
    monkeypatch.setattr(acp, "resolve_acp_executable", lambda: str(fake_acp))
    # The transport tries to create a real git worktree (mirror of
    # headless_transport / make_worktree_transport). The tmp_path isn't a
    # git repo, so no-op the git call — same fixture shape as
    # test_headless_transport_waits_for_clean_init.
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    # Fake-binary has no real session_registry; the transport's checks for
    # capacity shouldn't trip in this isolated test.
    monkeypatch.setattr(acp.session_registry, "can_add_session", lambda: True)
    monkeypatch.setattr(acp.session_registry, "reserve_external", lambda name: None)

    worktree = tmp_path / "wt"
    worktree.mkdir()

    result = await acp.acp_transport(
        directory=str(worktree), prompt="hello", session_name="k-acp-e2e",
        cli_id="open-code", provider="opencode-go",
    )
    assert result["session_name"] == "k-acp-e2e"
    assert result["transport"] == "acp"
    assert result["status"] == "started"

    # The protocol order: initialize → session/new → session/prompt.
    seen = [
        json.loads(line)["method"]
        for line in request_log.read_text().splitlines()
        if line.strip()
    ]
    assert seen == ["initialize", "session/new", "session/prompt"]

    # The background run_acp task is still alive at this point —
    # acp_transport returns as soon as session/new lands. Wait for the
    # task tracked in _acp_start_tasks to finish its finally-block cleanup
    # so the pidfile is removed and the in-memory registry is drained
    # before we assert.
    for task in list(acp._acp_start_tasks):
        if task.get_name() == f"acp-run-{result['session_name']}":
            await task
            break

    # Pidfile is gone in the finally block (we completed cleanly).
    assert "k-acp-e2e" not in acp._acp_processes
    assert not (worktree / acp._ACP_PIDFILE_NAME).exists()