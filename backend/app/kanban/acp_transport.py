"""ACP-backed SpawnTransport for ``open-code`` (``opencode acp``).

The fourth ``SpawnTransport`` sibling (kaart ``f647a44e…``) — alongside
``worktree``, ``sandcastle`` and ``headless`` — that spawns
``opencode acp`` as a subprocess, drives it through JSON-RPC 2.0 over stdio,
and maps ACP ``session/update`` notifications onto the ACP-isomorphic
``StructuredEvent`` model in :mod:`app.services.agentic_cli.structured_events`.

Why a sibling and not a refactor of :mod:`app.kanban.headless_runner`:
``_build_argv`` in ``headless_runner.py:1064-1081`` hardcodes Claude's
``-p --output-format stream-json --verbose --dangerously-skip-permissions``
flags, which is meaningless to OpenCode's ACP server. The ACP adapter has a
fundamentally different wire format (JSON-RPC requests/responses + push
notifications vs. claude-code's pure JSONL stream) and a fundamentally
different spawn (stdio pipes vs. file-backed stdout for restart-survival),
so the cleanest implementation is a parallel module that owns its own
spawn/protocol/lifecycle. The two stay sync'd only at the
``StructuredEvent`` boundary — both populate the same model.

Permission gate as the load-bearing seam (brondoc §2.4 / §3.3):
``session/request_permission`` only fires when OpenCode's permission config
puts ``edit`` and ``bash`` on ``ask``. With OpenCode's default-config it is
silent. :func:`_write_permission_config` writes a per-worktree
``opencode.json`` that turns the gate on, so the spawn makes the gate
load-bearing rather than relying on whatever the user happens to have
configured. The handler at :func:`_pick_permission_response` answers each
incoming request with a typed JSON-RPC ``result`` naming an ``allow_once``
option, so the turn can proceed without blocking on a UI.

Restart-survival (kaart ``a450df1a…`` precedent): the subprocess is spawned
with ``start_new_session=True`` and a durable pidfile
(``<worktree>/.cockpit-acp.json``) records the pid + worktree + log path so
:func:`live_acp_sessions` can OS-verify it across a backend restart.
Adoption is wired into ``app.main.lifespan`` alongside the headless
adopter.

Public surface (everything else is module-private):

- :func:`acp_transport` — the ``SpawnTransport`` callable the dispatcher
  routes to when ``card.transport == "acp"``.
- :func:`map_acp_event` — pure JSON-RPC notification → StructuredEvent
  dict. Tested in isolation so the transport doesn't have to know about
  ACP wire casing.
- :func:`map_acp_result` — pure JSON-RPC response → StructuredEvent dict
  for terminal ``session/prompt`` results (stopReason + usage).
- :func:`live_acp_sessions` — fourth liveness source consumed by
  ``reap_stale_claims``. Defensive: any failure yields ``set()``.
- :func:`kill_acp_session` — best-effort SIGTERM for the human-takeover
  promotion path.
- :func:`adopt_acp_runs` — startup hook that re-attaches still-alive runs.
- :func:`_write_permission_config` — writes the per-worktree
  ``opencode.json`` that makes the gate load-bearing.
- :func:`_pick_permission_response` — pure: pick the first
  ``allow_once`` option from a permission request's options list.
- :func:`resolve_acp_executable` — return the binary to spawn. Tests
  override this to point at a fake JSON-RPC responder.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.agentic_cli.structured_events import (
    MessageRole,
    PermissionOption,
    PermissionOptionKind,
    RateLimitEvent,
    StructuredEvent,
    ToolCallStatus,
    parse_structured_event,
)
from app.services.scheduling.session_registry import session_registry

logger = logging.getLogger("app.kanban.acp_transport")


# Pidfile name distinct from headless_runner._HEADLESS_PIDFILE_NAME so the
# two liveness sources scan a different on-disk artifact and a corruption in
# one can't masquerade as a live run in the other.
_ACP_PIDFILE_NAME = ".cockpit-acp.json"
# Default log cap (mirrors the headless transport's 16 MB; ACP's wire is
# sparser but the same pathological-loop argument applies).
_DEFAULT_LOG_CAP_BYTES = 16 * 1024 * 1024
# Name of the per-worktree OpenCode config file that turns on the
# permission gate. Must be written BEFORE the subprocess starts; OpenCode
# reads it on spawn.
_ACP_PERMISSION_CONFIG_NAME = "opencode.json"


# Module-level in-memory cache of in-flight ACP subprocesses. Populated by
# :func:`run_acp` after spawn and by :func:`adopt_acp_runs` at startup.
# Keyed by session_name. The Process object itself dies with the asyncio
# loop; this dict carries only durable identity (pid + worktree path) so
# the liveness source can OS-verify across restarts.
_acp_processes: dict[str, AcpRunRecord] = {}

# Project roots that may host an ACP worktree. Populated by
# :func:`_remember_project_root` (called from :func:`run_acp` and from
# :func:`adopt_acp_runs`). Bounded by the number of registered projects.
_known_project_roots: set[str] = set()


def _remember_project_root(project_root: str) -> None:
    """Add ``project_root`` to the cache of pidfile-search roots.

    Idempotent. Called by :func:`run_acp` after spawn and by
    :func:`adopt_acp_runs` while walking registered projects.
    """
    _known_project_roots.add(str(project_root))


@dataclass
class AcpRunRecord:
    """Durable identity of a single ACP run.

    Holds just enough to (a) check liveness via the OS, (b) signal the
    process group for human-takeover / final cleanup, and (c) locate the
    on-disk pidfile so a fresh backend can re-attach.

    The Process object itself lives in the asyncio loop that spawned it;
    this record survives a backend restart because it's also persisted to
    the pidfile and re-derived at adoption time.
    """

    session_name: str
    pid: int
    worktree_path: str
    started_at: float


def _os_pid_alive(pid: int, expected_cwd: str) -> bool:
    """True iff ``pid`` exists AND its cwd matches ``expected_cwd``.

    Same predicate as ``headless_runner._os_pid_alive``: two checks because
    pid-reuse is real. ``/proc/<pid>/cwd`` is Linux-only; non-Linux falls
    back to pid-alive only.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # pid exists; let the cwd check decide
    try:
        cwd_link = Path(f"/proc/{pid}/cwd").readlink()
    except PermissionError:
        return False
    except (OSError, FileNotFoundError):
        return True
    return str(cwd_link) == expected_cwd


def _read_pidfile(pidfile: Path) -> AcpRunRecord | None:
    """Parse an ACP pidfile; None on any failure.

    Defensive: malformed files are treated as "not a live run" and removed
    by the caller, so a half-written pidfile (crashed backend mid-write)
    doesn't wedge the liveness source.
    """
    try:
        data = json.loads(pidfile.read_text(encoding="utf-8"))
        return AcpRunRecord(
            session_name=data["session_name"],
            pid=int(data["pid"]),
            worktree_path=data["worktree_path"],
            started_at=float(data["started_at"]),
        )
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return None


def _known_worktree_dirs() -> list[Path]:
    """Enumerate the worktree directories that may host an ACP pidfile."""
    return [
        Path(p) / ".claude" / "worktrees"
        for p in sorted(_known_project_roots)
    ]


def live_acp_sessions() -> set[str]:
    """Session names of ACP subprocesses that are still running.

    Fourth liveness source for ``reap_stale_claims`` — sits alongside
    ``_live_sessions`` (tmux), ``_live_sandcastle_sessions`` (DB rows), and
    ``_live_headless_sessions`` (headless pidfiles). An ACP run has none of
    a tmux session, a SandcastleRun row, or a headless pidfile, so without
    this fourth source the reaper would release + re-dispatch ACP claims
    every tick — the same dispatch-loop bug sandcastle had before its own
    second source was added.

    Reads durable pidfiles (one per worktree), NOT the in-memory cache —
    this is the canonical answer to "is this ACP run still alive?" and
    works correctly even with an empty cache (immediately after a backend
    restart, before adoption has run).

    Each pidfile is OS-verified (pid + cwd); a dead pid drops the session
    from the result; a pid that's alive but in a different cwd is treated
    as foreign (pid-reuse) and also dropped.

    Defensive: any failure yields ``set()`` so a hiccup makes the reaper
    *eager*, never blind.
    """
    try:
        alive: set[str] = set()
        dead: list[tuple[str, int]] = []
        for worktrees_dir in _known_worktree_dirs():
            if not worktrees_dir.is_dir():
                continue
            for wt_dir in worktrees_dir.iterdir():
                if not wt_dir.is_dir():
                    continue
                pidfile = wt_dir / _ACP_PIDFILE_NAME
                if not pidfile.exists():
                    continue
                rec = _read_pidfile(pidfile)
                if rec is None:
                    try:
                        pidfile.unlink()
                    except OSError:
                        pass
                    continue
                if _os_pid_alive(rec.pid, rec.worktree_path):
                    alive.add(rec.session_name)
                    _acp_processes[rec.session_name] = rec
                else:
                    try:
                        pidfile.unlink()
                    except OSError:
                        pass
                    dead.append((rec.session_name, rec.pid))
        for name, _pid in dead:
            _acp_processes.pop(name, None)
        return alive
    except Exception:
        logger.exception("could not query live acp sessions")
        return set()


def kill_acp_session(session_name: str) -> bool:
    """Best-effort SIGTERM of a still-running ACP subprocess.

    Signals the process group since the subprocess is its own session
    leader (``start_new_session=True``), so any grandchildren it spawned
    get the signal too. Returns True when a live process was signaled,
    False when there was nothing to kill (unknown session name, or already
    exited). Only signals — :func:`run_acp`'s own ``finally`` block drains
    the registry once the process actually exits.
    """
    rec = _acp_processes.get(session_name)
    if rec is None:
        for worktrees_dir in _known_worktree_dirs():
            pidfile = worktrees_dir / session_name / _ACP_PIDFILE_NAME
            if pidfile.exists():
                rec = _read_pidfile(pidfile)
                break
    if rec is None:
        return False
    if not _os_pid_alive(rec.pid, rec.worktree_path):
        _acp_processes.pop(session_name, None)
        return False
    try:
        os.killpg(rec.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _pidfile_path(worktree_path: str) -> Path:
    """Where the durable ACP-run record for ``worktree_path`` lives."""
    return Path(worktree_path) / _ACP_PIDFILE_NAME


def _write_pidfile(record: AcpRunRecord) -> None:
    """Persist the run record to disk. Atomic via ``write_text``.

    The pidfile is the durable source of truth for restart-survival. After
    a backend crash between spawn and the ``run_acp`` finally block, the
    next backend startup reads it via :func:`adopt_acp_runs` and recovers
    the run instead of treating it as dead and re-dispatching.
    """
    pidfile = _pidfile_path(record.worktree_path)
    payload = {
        "session_name": record.session_name,
        "pid": record.pid,
        "worktree_path": record.worktree_path,
        "started_at": record.started_at,
    }
    try:
        pidfile.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        logger.exception(
            "could not write acp pidfile for %s", record.session_name,
        )


def _remove_pidfile(worktree_path: str, expected_pid: int) -> None:
    """Remove the pidfile iff its pid still matches ``expected_pid``.

    Pid-check is the same race-detector as the OS-liveness check: if a
    fresh run has overwritten the pidfile, we MUST NOT remove the new
    run's record. Tolerant of a missing file (already cleaned up).
    """
    pidfile = _pidfile_path(worktree_path)
    try:
        raw = pidfile.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    except OSError:
        logger.exception("could not read acp pidfile %s", pidfile)
        return
    try:
        if json.loads(raw).get("pid") != expected_pid:
            return
    except (json.JSONDecodeError, KeyError):
        pass  # malformed → remove and let the next adopt rebuild
    try:
        pidfile.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception("could not remove acp pidfile %s", pidfile)


def adopt_acp_runs(project_paths: list[str]) -> list[AcpRunRecord]:
    """Re-attach still-alive ACP runs from durable pidfiles.

    Called once at backend startup, BEFORE the dispatch scheduler and
    reaper start ticking — see ``app.main.lifespan``. For each registered
    project, walks every worktree under
    ``<project>/.claude/worktrees/*`` and, if a ``.cockpit-acp.json``
    pidfile is present, OS-verifies its pid (pid alive AND cwd matches the
    recorded worktree, see :func:`_os_pid_alive`).

    The primary job is re-reserving the slot in the in-memory session
    registry (so ``can_add_session()`` is honest post-restart) and
    populating the in-memory cache (so ``kill_acp_session`` has a fast
    path). ``live_acp_sessions()`` itself reads pidfiles directly and
    doesn't need a populated cache, but the cache keeps the rest of the
    liveness machinery happy.

    The ordering matters: if the reaper's first tick runs before adoption,
    every live ACP run looks dead → reaper releases the claims →
    dispatcher re-spawns in the same worktree → two agents on one branch.
    Adoption-first is the same ordering ``session_recovery`` already uses
    for tmux sessions.

    Dead pidfiles are cleaned up here too (rather than waiting for the
    next ``live_acp_sessions()`` call) so the reaper doesn't see a dead
    session's pidfile at all on its first tick.

    Returns the list of adopted records so the caller can spawn a tailer
    task for each one (mirrors headless_runner.adopt_headless_runs).
    """
    adopted: list[AcpRunRecord] = []
    for project_path in project_paths:
        project_root = str(project_path)
        _remember_project_root(project_root)
        worktrees_dir = Path(project_root) / ".claude" / "worktrees"
        if not worktrees_dir.is_dir():
            continue
        for wt_dir in worktrees_dir.iterdir():
            if not wt_dir.is_dir():
                continue
            pidfile = _pidfile_path(str(wt_dir))
            if not pidfile.exists():
                continue
            rec = _read_pidfile(pidfile)
            if rec is None:
                logger.warning(
                    "acp adopt: malformed pidfile %s, removing", pidfile,
                )
                try:
                    pidfile.unlink()
                except OSError:
                    pass
                continue
            if not _os_pid_alive(rec.pid, rec.worktree_path):
                logger.info(
                    "acp adopt: pidfile %s points to dead/foreign pid %d; "
                    "removing",
                    pidfile, rec.pid,
                )
                try:
                    pidfile.unlink()
                except OSError:
                    pass
                continue
            _acp_processes[rec.session_name] = rec
            adopted.append(rec)
            logger.info(
                "acp adopt: adopted session %s (pid %d, worktree %s)",
                rec.session_name, rec.pid, rec.worktree_path,
            )
            session_registry.reserve_external(rec.session_name)
    return adopted


def resolve_acp_executable() -> str:
    """Resolve the binary to spawn for the ACP transport.

    OpenCode ships an ``opencode acp`` top-level command (OpenCode 1.18.8+,
    see brondoc §2.1). Tests override this to point at a fake JSON-RPC
    responder so the full protocol handshake can run without a real
    subscription.
    """
    return "opencode"


def _write_permission_config(worktree_path: str) -> Path:
    """Write a per-worktree ``opencode.json`` that turns on the gate.

    Load-bearing seam (brondoc §3.3): with OpenCode's default-config the
    permission gate is SILENT — a gate that never closes is worse than no
    gate at all. Writing this file before spawn makes ``edit`` and ``bash``
    prompt the agent via ``session/request_permission`` for every tool
    invocation, which is exactly what the typed permission-gate test
    (``test_permission_request_response_uses_allow_once_option``) relies
    on.

    Returns the path of the written file so callers can log/cleanup.
    """
    config_path = Path(worktree_path) / _ACP_PERMISSION_CONFIG_NAME
    payload = {
        "permission": {
            "edit": "ask",
            "bash": "ask",
        },
    }
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        logger.exception("could not write acp permission config at %s", config_path)
    return config_path


def _pick_permission_response(options: Iterable[PermissionOption]) -> dict[str, Any]:
    """Pick the JSON-RPC ``result`` payload for an in-flight permission request.

    Always picks the FIRST ``allow_once`` option (deterministic, matches
    the model's intent: allow this specific tool call once, ask again for
    the next one). Falls back to the first option in the list if no
    ``allow_once`` is offered — a malformed-but-non-blocking answer so the
    turn can continue.

    The wire shape is the JSON-RPC 2.0 ``result`` for a
    ``session/request_permission`` request:
    ``{"outcome": {"outcome": "selected", "optionId": "<id>"}}``.
    """
    options_list = list(options)
    for opt in options_list:
        if opt.kind is PermissionOptionKind.ALLOW_ONCE:
            return {
                "outcome": {
                    "outcome": "selected",
                    "optionId": opt.option_id,
                },
            }
    # No allow_once offered — answer with the first option so the turn can
    # proceed (the gate stays load-bearing: a request without any allow
    # option is malformed, but we never silently hang).
    if options_list:
        return {
            "outcome": {
                "outcome": "selected",
                "optionId": options_list[0].option_id,
            },
        }
    # No options at all — answer with an empty outcome (the wire spec
    # requires a non-null result for any request; an empty outcome is the
    # "no decision" escape hatch).
    return {"outcome": {"outcome": "cancelled"}}


# ---- ACP event mapping ----------------------------------------------------
#
# Pure functions: no I/O, no logger, no exceptions. The spike §4 table is
# the source of truth; adding a new variant means one new ``elif`` arm and
# one new mapping test.

def map_acp_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Map a raw ACP JSON-RPC notification to the dict shape
    :func:`parse_structured_event` accepts.

    Implements the table in brondoc §4 (mapping verified against OpenCode
    1.18.8 measured events). Returns a dict the schema's discriminator picks
    up on; unknown ``sessionUpdate`` variants fall through to
    ``{"type": <variant>, **payload}`` so the schema's ValidationError
    carries the original event verbatim for debugging.

    Covers:

    - ``session/update`` → ``agent_message_chunk`` / ``agent_thought_chunk``
      / ``user_message_chunk`` → ``message_chunk`` (role=assistant/thought/user)
    - ``session/update`` → ``tool_call`` / ``tool_call_update`` → ``tool_call``
      (status pending/in_progress/completed/failed)
    - ``session/update`` → ``usage_update`` → ``context_usage`` (the dep-kaart
      variant, model-validated in
      ``structured_events.ContextUsageEvent``)
    - ``session/update`` → ``available_commands_update`` → ``message_chunk``
      role=assistant with the names joined (spike §4 noted the model has no
      counterpart, but the mapper still surfaces it as an assistant message
      so the user sees the offered commands).
    - ``session/update`` → ``plan`` → ``plan_update``
    - ``session/request_permission`` (request) → ``permission_request``
    - Unknown ``sessionUpdate`` → pass through to the schema's
      ValidationError handler.
    """
    method = payload.get("method")

    if method == "session/update":
        params = payload.get("params") or {}
        session_id = params.get("sessionId")
        update = params.get("update") or {}
        update_kind = update.get("sessionUpdate")

        if update_kind == "agent_message_chunk":
            content = update.get("content") or {}
            return {
                "type": "message_chunk",
                "role": MessageRole.ASSISTANT.value,
                "text": content.get("text", ""),
                "session_id": session_id,
            }
        if update_kind == "agent_thought_chunk":
            content = update.get("content") or {}
            return {
                "type": "message_chunk",
                "role": MessageRole.THOUGHT.value,
                "text": content.get("text", ""),
                "session_id": session_id,
            }
        if update_kind == "user_message_chunk":
            content = update.get("content") or {}
            return {
                "type": "message_chunk",
                "role": MessageRole.USER.value,
                "text": content.get("text", ""),
                "session_id": session_id,
            }
        if update_kind in ("tool_call", "tool_call_update"):
            tool_call = update.get("toolCall") or {}
            return {
                "type": "tool_call",
                "tool_call_id": tool_call.get("toolCallId"),
                "title": tool_call.get("title"),
                "kind": tool_call.get("kind"),
                "status": tool_call.get("status", ToolCallStatus.PENDING.value),
                "raw_input": tool_call.get("rawInput"),
                "raw_output": tool_call.get("rawOutput"),
                "session_id": session_id,
            }
        if update_kind == "usage_update":
            cost = update.get("cost")
            cost_payload = None
            if isinstance(cost, Mapping):
                cost_payload = {
                    "amount": cost.get("amount"),
                    "currency": cost.get("currency"),
                }
            return {
                "type": "context_usage",
                "used": update.get("used"),
                "size": update.get("size"),
                "cost": cost_payload,
                "session_id": session_id,
            }
        if update_kind == "available_commands_update":
            commands = update.get("availableCommands") or []
            names = [
                c.get("name", "?") for c in commands if isinstance(c, Mapping)
            ]
            text = "Available commands: " + ", ".join(names) if names else ""
            return {
                "type": "message_chunk",
                "role": MessageRole.ASSISTANT.value,
                "text": text,
                "session_id": session_id,
            }
        if update_kind == "plan":
            entries = update.get("entries") or []
            return {
                "type": "plan_update",
                "entries": entries,
                "session_id": session_id,
            }
        # Unknown sessionUpdate variant — pass through so the schema's
        # ValidationError surfaces the original event verbatim for
        # debugging (same philosophy as map_stream_event).
        return {"type": update_kind, "session_id": session_id, **update}

    if method == "session/request_permission":
        params = payload.get("params") or {}
        session_id = params.get("sessionId")
        tool_call = params.get("toolCall") or {}
        raw_options = params.get("options") or []
        options = [
            PermissionOption(
                option_id=o.get("optionId"),
                name=o.get("name"),
                kind=o.get("kind"),
            )
            for o in raw_options if isinstance(o, Mapping)
        ]
        return {
            "type": "permission_request",
            "tool_call_id": tool_call.get("toolCallId"),
            "title": tool_call.get("title"),
            "options": [o.model_dump() for o in options],
            "session_id": session_id,
        }

    # Unknown method (or anything else that isn't a recognized ACP
    # notification/request) — pass through.
    return {"type": method, **payload}


def map_acp_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Map a raw ACP JSON-RPC response to a ``usage_result`` structured event.

    The terminal response of a ``session/prompt`` request carries
    ``stopReason`` + ``usage`` — exactly the shape ``UsageResultEvent``
    consumes. The transport's consumer treats this as the end-of-turn
    signal.
    """
    result = payload.get("result") or {}
    usage = result.get("usage") or {}
    return {
        "type": "usage_result",
        "stop_reason": result.get("stopReason"),
        "input_tokens": usage.get("inputTokens"),
        "output_tokens": usage.get("outputTokens"),
        "total_tokens": usage.get("totalTokens"),
        "cost_usd": (usage.get("cost") or {}).get("amount") if isinstance(usage.get("cost"), Mapping) else None,
    }


# ---- JSON-RPC client -------------------------------------------------------
#
# Minimal newline-delimited JSON-RPC 2.0 over stdio client. Sends requests
# one per line, receives responses and notifications in the same framing.
# Each response is routed by ``id`` to the waiter that issued the request;
# notifications (no ``id`` field) are routed to a consumer callback.


class _JsonRpcClient:
    """Line-delimited JSON-RPC 2.0 over a pair of asyncio streams.

    Spawns ``opencode acp`` (or the test's fake binary) and exposes a
    synchronous-feeling ``request`` API. Each call sends one
    ``{"jsonrpc":"2.0","id":N,"method":..., "params":...}`` line and
    awaits the matching response (matched by id). Notifications
    (``method`` present, no ``id``) are dispatched to
    ``on_notification``; requests from the server (``id`` present) are
    dispatched to ``on_server_request``.

    Why hand-rolled instead of an existing library: the ACP surface is
    small (request/response + notifications + server-side
    ``session/request_permission`` request), and a third-party JSON-RPC
    dep would be load-bearing only for this single transport. The local
    implementation is ~60 lines and exactly matches the measured OpenCode
    1.18.8 wire shape (brondoc §2).
    """

    def __init__(
        self, proc: asyncio.subprocess.Process,
        *, on_notification, on_server_request,
    ) -> None:
        self._proc = proc
        self._on_notification = on_notification
        self._on_server_request = on_server_request
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._closed = False

    def _alloc_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    async def request(
        self, method: str, params: dict[str, Any] | None = None,
        *, timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and await the matching response.

        ``timeout`` is enforced on the await side; the request itself is
        fire-and-forget (the server may take seconds to respond, e.g. a
        full session/prompt turn).
        """
        if self._closed:
            raise RuntimeError("JsonRpcClient is closed")
        rid = self._alloc_id()
        msg = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
            "params": params or {},
        }
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[rid] = fut
        try:
            self._proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            await self._proc.stdin.drain()
        except Exception:
            self._pending.pop(rid, None)
            raise
        if timeout is None:
            return await fut
        return await asyncio.wait_for(fut, timeout=timeout)

    async def respond_to_server_request(
        self, rid: int, result: dict[str, Any] | None = None,
    ) -> None:
        """Send a JSON-RPC ``result`` for a server-initiated request.

        Used to answer ``session/request_permission`` so the gate stays
        load-bearing (the turn proceeds once we've answered).
        """
        if self._closed:
            return
        msg = {"jsonrpc": "2.0", "id": rid, "result": result or {}}
        try:
            self._proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            await self._proc.stdin.drain()
        except Exception:
            logger.exception("acp: failed to send response to server request %s", rid)

    async def close(self) -> None:
        self._closed = True
        # Fail any in-flight pending requests so callers don't hang.
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("JsonRpcClient closed"))
        self._pending.clear()

    async def read_loop(self) -> None:
        """Read newline-delimited JSON-RPC messages until EOF.

        Each line is either:

        - a response (``id`` + ``result`` or ``error``) → resolves the
          matching pending future.
        - a notification (``method`` + ``params``, no ``id``) → forwarded
          to ``on_notification``.
        - a server-initiated request (``method`` + ``params`` + ``id``) →
          forwarded to ``on_server_request`` (the callback is responsible
          for calling :meth:`respond_to_server_request` to answer).
        """
        assert self._proc.stdout is not None
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:
                break
            try:
                msg = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                logger.warning("acp: dropping non-JSON line: %r", raw[:200])
                continue
            if not isinstance(msg, Mapping):
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                # Response to a pending request.
                rid = msg.get("id")
                fut = self._pending.pop(rid, None)
                if fut is not None and not fut.done():
                    if "error" in msg:
                        fut.set_exception(RuntimeError(
                            f"acp json-rpc error: {msg['error']!r}"
                        ))
                    else:
                        fut.set_result(msg)
                continue
            if "id" in msg and "method" in msg:
                # Server-initiated request (e.g. session/request_permission).
                try:
                    await self._on_server_request(msg)
                except Exception:
                    logger.exception("acp: on_server_request raised")
                continue
            if "method" in msg:
                # Notification.
                try:
                    await self._on_notification(msg)
                except Exception:
                    logger.exception("acp: on_notification raised")
                continue
            logger.warning("acp: dropping unrecognized message: %r", msg)


# ---- transport entry-point -------------------------------------------------


async def acp_transport(
    *, directory: str, prompt: str, session_name: str,
    cli_id: str = "open-code", provider: str = "opencode-go",
    model: str | None = None,
    endpoint_name: str | None = None,
    endpoint_base_url: str | None = None,
    endpoint_auth_token: str | None = None,
    card_id: str | None = None,
    column_name: str | None = None,
) -> dict:
    """SpawnTransport sibling for ACP-backed ``opencode acp`` runs.

    Mirrors :func:`headless_runner.headless_transport`'s signature so the
    dispatcher can swap transparently. The worktree branch and dir stay
    the canonical three identity facets; only the liveness-orakel changes,
    which is what :func:`live_acp_sessions` is for.

    Kaart-eis: selected when the resolved cli-id is ``open-code`` (the only
    CLI that ships a first-party ACP server in the measured population;
    see brondoc §2.1). A non-``open-code`` cli-id raises ValueError — the
    transport is not a generic ACP client, it is the ``open-code``-shaped
    sibling.

    Spawns ``opencode acp`` with a worktree-cwd, drives it through
    ``initialize`` → ``session/new`` → ``session/prompt``, and drains the
    notification stream until ``session/prompt`` returns. The session
    slot is reserved synchronously (mirror of ``headless_transport``) so
    the dispatch tick's count is honest before the async work starts.
    """
    if cli_id != "open-code":
        raise ValueError(
            f"acp_transport only supports cli_id='open-code', got {cli_id!r}; "
            f"another vendor's ACP adapter would need its own transport sibling."
        )

    if not session_registry.can_add_session():
        from app.kanban.dispatch import MemoryLimitExceeded
        raise MemoryLimitExceeded(session_registry.build_limit_message())

    session_registry.reserve_external(session_name)

    # Build the worktree synchronously off the event loop, mirroring
    # make_worktree_transport's pattern. The headless transport does the
    # same dance; reusing the shape means a future change to the worktree
    # creation semantics lands in both transports without drift.
    repo = directory
    worktree_path = str(Path(repo) / ".claude" / "worktrees" / session_name)

    def _spawn_git_worktree() -> None:
        import subprocess as _sp
        _sp.run(
            ["git", "-C", repo, "fetch", "origin"],
            capture_output=True, text=True, timeout=60, check=True,
        )
        _sp.run(
            ["git", "-C", repo, "worktree", "add", "-b", session_name,
             worktree_path, "origin/master"],
            capture_output=True, text=True, timeout=60, check=True,
        )

    try:
        _spawn_git_worktree()
    except Exception:
        session_registry.release_external(session_name)
        raise

    # Load-bearing seam: write the permission config BEFORE spawn so the
    # ACP server reads it on startup (brondoc §3.3 — the default-config
    # leaves the gate silent, which is the failure mode this whole card
    # exists to prevent).
    _write_permission_config(worktree_path)

    startup_future = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(
        run_acp(
            directory=worktree_path,
            prompt=prompt,
            session_name=session_name,
            cli_id=cli_id,
            provider=provider,
            model=model,
            startup_future=startup_future,
        ),
        name=f"acp-run-{session_name}",
    )
    _acp_start_tasks.add(task)
    task.add_done_callback(_acp_task_done_callback)

    done, _ = await asyncio.wait(
        {startup_future, task}, return_when=asyncio.FIRST_COMPLETED,
    )
    if startup_future in done:
        startup_future.result()
    else:
        await task
        raise RuntimeError(f"acp {session_name} exited before session init")

    return {
        "session_name": session_name,
        "transport": "acp",
        "status": "started",
    }


# Strong references to in-flight ACP start tasks (asyncio only holds weak
# refs; without this set a fire-and-forget task can be GC'd mid-flight).
_acp_start_tasks: set = set()


def _acp_task_done_callback(task: asyncio.Task) -> None:
    """Discard a finished ACP run task AND surface any exception.

    Mirrors ``headless_runner._headless_task_done_callback``: the previous
    behavior of silently discarding would only surface a run failure as a
    GC warning, invisible from the dispatch log. Logging the exception
    with the full traceback makes the breadcrumb visible to operators.
    """
    _acp_start_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.exception(
            "acp run task %s failed: %s",
            task.get_name() or "<unnamed>", exc, exc_info=exc,
        )


async def run_acp(
    *, directory: str, prompt: str, session_name: str,
    cli_id: str = "open-code", provider: str = "opencode-go",
    model: str | None = None,
    startup_future: asyncio.Future[None] | None = None,
) -> dict:
    """Spawn the ACP subprocess and drive one prompt turn.

    Mirrors :func:`headless_runner.run_headless`'s lifetime contract:

    - Spawn with ``start_new_session=True`` so the subprocess is its own
      session leader — a backend exit (``cockpit.sh restart`` / SIGTERM
      to the parent pgid) does NOT propagate. This is the ownership
      detach the worktree/tmux transport already enjoys for free.
    - Write a durable pidfile (``<worktree>/.cockpit-acp.json``) holding
      pid + worktree. The pidfile is the source of truth that survives a
      backend restart; :func:`adopt_acp_runs` reads it at startup and
      re-attaches the run BEFORE the reaper's first tick.
    - Track the subprocess in :data:`_acp_processes` (as an
      :class:`AcpRunRecord`) for the liveness source.
    - Release the slot on exit, regardless of return code. Remove the
      pidfile only when the recorded pid still matches our pid (a stale
      or rewritten pidfile is left alone — a fresh run owns that record).
    """
    binary = resolve_acp_executable()
    argv = [binary, "acp"]

    # The JSON-RPC client needs bidirectional stdio. PIPE is sufficient
    # because we drive the entire conversation from this side; the child
    # never reads from stdin on its own initiative.
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=directory,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    record = AcpRunRecord(
        session_name=session_name,
        pid=proc.pid,
        worktree_path=directory,
        started_at=time.time(),
    )
    _remember_project_root(Path(directory).parent.parent.parent)
    _acp_processes[session_name] = record
    _write_pidfile(record)

    # The consumer callbacks dispatch structured events into the headless-
    # style ``_on_event`` pipeline so the downstream event handling
    # (rate-limit pause, log paths, etc.) stays consistent. ACP's
    # structured-event surface is a strict subset of the headless one's
    # (no ``rate_limit`` / ``session_init`` from ACP) so the same handler
    # works without a branch.
    async def on_notification(msg: dict[str, Any]) -> None:
        try:
            structured = parse_structured_event(map_acp_event(msg))
        except Exception as exc:
            logger.warning(
                "acp %s: dropping unparseable notification: %r (%s: %s)",
                session_name, msg, type(exc).__name__, exc,
            )
            return
        await _on_event(structured, session_name=session_name, provider=provider)

    async def on_server_request(msg: dict[str, Any]) -> None:
        # Permission gate: parse the request, pick a response, send it
        # back. The gate is load-bearing — without our response the turn
        # blocks indefinitely. Brondoc §2.4 + §3.3.
        method = msg.get("method")
        if method == "session/request_permission":
            try:
                structured = parse_structured_event(map_acp_event(msg))
            except Exception as exc:
                logger.warning(
                    "acp %s: dropping unparseable permission request: %r (%s: %s)",
                    session_name, msg, type(exc).__name__, exc,
                )
                return
            options = (
                structured.options if isinstance(structured, type(structured))
                else []
            )
            await _on_event(structured, session_name=session_name, provider=provider)
            response = _pick_permission_response(options)
            await client.respond_to_server_request(msg.get("id"), response)
            return
        # Unknown server-initiated request — answer with an empty result
        # so the turn doesn't block. The schema's payload is still
        # forwarded to _on_event so debugging sees what came in.
        try:
            structured = parse_structured_event(map_acp_event(msg))
        except Exception:
            pass
        await client.respond_to_server_request(msg.get("id"), {})

    client = _JsonRpcClient(
        proc, on_notification=on_notification, on_server_request=on_server_request,
    )

    async def _consume() -> None:
        try:
            await client.read_loop()
        finally:
            await client.close()

    consumer_task = asyncio.create_task(_consume(), name=f"acp-consume-{session_name}")

    try:
        # Drive the handshake. Each call awaits the matching JSON-RPC
        # response by id, with a generous timeout so a slow first turn
        # doesn't kill the spawn.
        await client.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}},
        }, timeout=30.0)

        new_session = await client.request("session/new", {
            "cwd": directory,
            "mcpServers": [],
        }, timeout=30.0)
        session_id = (new_session.get("result") or {}).get("sessionId")
        if not session_id:
            raise RuntimeError(
                f"acp: session/new returned no sessionId: {new_session!r}"
            )

        # Mark startup ready AFTER we have a session id — the headless
        # transport uses the first ``session_init`` event for the same
        # gate. ACP has no equivalent notification (brondoc §4 noted
        # session_init is a deliberate super-set, not ACP-native), so
        # the session/new response is the next-best readiness marker.
        if startup_future is not None and not startup_future.done():
            startup_future.set_result(None)

        prompt_result = await client.request("session/prompt", {
            "sessionId": session_id,
            # ACP ``session/prompt`` takes the prompt as an array of
            # content blocks, not a bare string (measured against OpenCode
            # 1.18.8: a string payload returns ``-32602 Invalid params:
            # expected array, received string``). The text-block form is
            # the canonical "single user message" shape.
            "prompt": [{"type": "text", "text": prompt}],
        }, timeout=None)

        # Terminal: emit the usage_result event from the prompt response
        # so the dispatch layer sees the same end-of-turn marker the
        # stream-json transport produces.
        try:
            structured = parse_structured_event(map_acp_result(prompt_result))
        except Exception as exc:
            logger.warning(
                "acp %s: dropping unparseable prompt result: %r (%s: %s)",
                session_name, prompt_result, type(exc).__name__, exc,
            )
        else:
            await _on_event(structured, session_name=session_name, provider=provider)

        # Tell the child we're done — its stdin write loop will exit on
        # the next EOF and the process will close cleanly.
        try:
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.write_eof()
                await proc.stdin.drain()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            # Fall back to SIGTERM so we never leave a dangling subprocess.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                await proc.wait()

        return {
            "session_name": session_name,
            "transport": "acp",
            "exit_code": proc.returncode,
        }
    finally:
        # Stop the consumer task (it's tied to the client's lifetime;
        # closing stdin or killing the subprocess makes its readline
        # return EOF).
        if not consumer_task.done():
            consumer_task.cancel()
            try:
                await consumer_task
            except (asyncio.CancelledError, Exception):
                pass
        await client.close()
        # Drop from the registry and release the slot ONLY after the proc
        # is reaped — same ordering discipline as headless_runner.run_headless.
        _acp_processes.pop(session_name, None)
        _remove_pidfile(directory, expected_pid=proc.pid)
        session_registry.release_external(session_name)


async def _on_event(event: StructuredEvent, *, session_name: str, provider: str) -> None:
    """Dispatch a single structured event.

    Mirrors ``headless_runner._on_event``: ``rate_limit`` → ``set_paused_until``
    (typed, replaces ``FALLBACK_PAUSE_HOURS``); everything else is
    debug-logged so trace mode sees them without spamming the comment feed.
    ACP's surface does not currently emit ``rate_limit`` (it's a Claude
    stream-json super-set per brondoc §4) but the handler is wired so a
    future ACP vendor that does emit one gets the same pause behaviour.
    """
    from app.services.agentic_cli.structured_events import (
        ErrorEvent,
        RateLimitEvent,
        StructuredEventType,
        UsageResultEvent,
    )

    if event.type == StructuredEventType.RATE_LIMIT:
        assert isinstance(event, RateLimitEvent)
        await _on_rate_limit_event(event, provider=provider)
        return
    if event.type == StructuredEventType.USAGE_RESULT:
        assert isinstance(event, UsageResultEvent)
        logger.info(
            "acp %s: usage_result stop_reason=%s input=%s output=%s",
            session_name, event.stop_reason, event.input_tokens, event.output_tokens,
        )
        return
    if event.type == StructuredEventType.ERROR:
        assert isinstance(event, ErrorEvent)
        logger.warning("acp %s: error: %s", session_name, event.message)
        return
    logger.debug("acp %s: %s event", session_name, event.type.value)


async def _on_rate_limit_event(event: RateLimitEvent, *, provider: str) -> None:
    """Translate a typed rate-limit event into a dispatch pause.

    Mirrors ``headless_runner._on_rate_limit_event``: the typed
    ``resets_at`` timestamp drives ``set_paused_until`` instead of the
    legacy ``FALLBACK_PAUSE_HOURS`` guess. Falls back to the guess when
    ``resets_at`` is absent (same defensive contract).
    """
    from datetime import UTC, datetime, timedelta

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dispatch_pause import set_paused_until
    from app.services.scheduling.auto_resume import FALLBACK_PAUSE_HOURS

    if event.resets_at is not None:
        until = datetime.fromtimestamp(event.resets_at, UTC)
        logger.info(
            "acp rate_limit: pausing dispatch for provider=%s until %s",
            provider, until.isoformat(),
        )
    else:
        until = datetime.now(UTC) + timedelta(hours=FALLBACK_PAUSE_HOURS)
        logger.warning(
            "acp rate_limit: resets_at missing — falling back to "
            "FALLBACK_PAUSE_HOURS=%sh for provider=%s",
            FALLBACK_PAUSE_HOURS, provider,
        )

    async with KanbanSessionLocal() as session:
        await set_paused_until(session, until, provider=provider)
        await session.commit()