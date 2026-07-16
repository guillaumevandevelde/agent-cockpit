"""Headless stream-json subprocess runner.

The third ``SpawnTransport`` sibling (``headless_transport`` in
``app.kanban.dispatch``) routes card dispatches through this module instead
of tmux. The runner spawns ``claude -p --output-format stream-json --verbose``
as a subprocess, parses its JSONL output into ACP-isomorphic
``StructuredEvent`` objects (see ``app.services.agentic_cli.structured_events``
and the mapping table in
``docs/cockpit/headless-stream-json-transport-spike.md`` §4), and feeds those
events to the dispatch layer.

Why this module owns the subprocess registry: the reaper's third liveness
source (``live_headless_sessions``) reads from the same dict this module
mutates. Keeping them in one file is what makes "is this headless run still
alive?" a single-line answer and prevents the two from drifting the way
sandcastle's two-source wiring didn't (see spike §5 for the precedent).

Public surface (everything else is module-private):

- :func:`headless_transport` — the ``SpawnTransport`` callable used by the
  dispatcher.
- :func:`run_headless` — coroutine that owns one end-to-end run: worktree +
  subprocess spawn + event-stream consumption + cleanup.
- :func:`live_headless_sessions` — third liveness source consumed by
  ``dispatch.reap_stale_claims``. Defensive: any failure yields ``set()`` so
  a registry hiccup makes the reaper *eager*, never blind.
- :func:`map_stream_event` — pure mapping from a raw stream-json payload to
  the dict shape :func:`parse_structured_event` accepts. Tested in isolation
  so the parser doesn't have to know about Claude's wire format.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.services.agentic_cli.structured_events import (
    MessageRole,
    RateLimitEvent,
    StructuredEvent,
    StructuredEventType,
    ToolCallStatus,
    parse_structured_event,
)
from app.services.scheduling.auto_resume import FALLBACK_PAUSE_HOURS
from app.services.scheduling.session_registry import session_registry

logger = logging.getLogger("app.kanban.headless_runner")


# Module-level registry of in-flight headless subprocesses, keyed by session
# name. Populated by :func:`run_headless` (or a test stub) before the process
# has a chance to exit, drained in a ``finally`` block so the liveness source
# never reports a dead process. Read by :func:`live_headless_sessions` from
# inside ``app.kanban.dispatch.reap_stale_claims``.
_headless_processes: dict[str, asyncio.subprocess.Process] = {}


def live_headless_sessions() -> set[str]:
    """Session names of headless subprocesses that are still running.

    Third liveness source for ``reap_stale_claims`` (alongside tmux and
    sandcastle). Defensive: any failure yields ``set()`` so a registry hiccup
    makes the reaper *eager* (more likely to release a dead claim), never
    blind (less likely — that's the bug the empty-set policy avoids).
    """
    try:
        return {
            name for name, proc in _headless_processes.items()
            if proc.returncode is None
        }
    except Exception:
        logger.exception("could not query live headless sessions")
        return set()


def resolve_cli_executable(cli_id: str) -> str:
    """Resolve the CLI id to the executable to spawn.

    Claude is on PATH as ``claude``. Tests override this to point at a fake
    script so the full subprocess + event-stream path can run without a real
    subscription.
    """
    if cli_id == "claude-code":
        return "claude"
    return cli_id


def headless_transport(*, directory: str, prompt: str, session_name: str,
                       cli_id: str = "claude-code", provider: str = "anthropic",
                       model: str | None = None) -> dict:
    """SpawnTransport sibling for headless ``stream-json`` runs.

    Mirrors :func:`app.kanban.dispatch.make_worktree_transport`'s signature so
    the dispatcher can swap transparently. The worktree branch and dir stay
    the canonical three identity facets (claim, branch, worktree-dir — see
    spike §5.1); only the liveness-orakel changes, which is what
    :func:`live_headless_sessions` is for.

    Runs the agent as a subprocess via :func:`run_headless`; the caller
    (the dispatch loop) is async, so we schedule as a tracked task and return
    immediately with the same shape the other transports return (session_name
    + transport identifier).
    """
    from app.services.scheduling.session_registry import session_registry

    if not session_registry.can_add_session():
        from app.kanban.dispatch import MemoryLimitExceeded
        # Cause-aware message — same builder as the worktree / sandcastle /
        # resume transports, so a counter leak doesn't get mis-diagnosed as
        # a memory problem (bevinding 5 in
        # docs/cockpit/spawn-test-bridge-sessions-analyse.md).
        raise MemoryLimitExceeded(session_registry.build_limit_message())

    # Reserve the slot synchronously so the count is correct for the rest of
    # this dispatch tick. ``run_headless`` releases it in its finally block.
    # Mirror of sandcastle_transport's reserve_external pattern.
    session_registry.reserve_external(session_name)

    repo = directory
    worktree_path = str(Path(repo) / ".claude" / "worktrees" / session_name)

    def _spawn_git_worktree() -> None:
        """Create the worktree synchronously (we're already off the event loop
        path for the worktree commands — same shape as make_worktree_transport).
        """
        subprocess.run(
            ["git", "-C", repo, "fetch", "origin"],
            capture_output=True, text=True, timeout=60, check=True,
        )
        subprocess.run(
            ["git", "-C", repo, "worktree", "add", "-b", session_name,
             worktree_path, "origin/master"],
            capture_output=True, text=True, timeout=60, check=True,
        )

    try:
        _spawn_git_worktree()
    except Exception:
        session_registry.release_external(session_name)
        raise

    project_key = _safe_resolve_project_key(repo)
    skip_permissions = True  # read from project meta in a follow-up

    # Async-context dispatch path: schedule without blocking. A sync caller
    # (none today — dispatcher always runs in a loop) would run inline.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        task = loop.create_task(
            run_headless(
                cli_id=cli_id,
                directory=worktree_path,
                prompt=prompt,
                session_name=session_name,
                skip_permissions=skip_permissions,
                provider=provider,
                model=model,
                project_key=project_key,
            )
        )
        # Strong reference so the task can't be GC'd before it runs (same
        # pattern as _sandcastle_start_tasks in dispatch.py).
        _headless_start_tasks.add(task)
        task.add_done_callback(_headless_start_tasks.discard)

        return {
            "session_name": session_name,
            "transport": "headless",
            "status": "started",
        }

    # Sync fallback: run inline (the result-dict shape mirrors what async mode
    # would have returned if it had blocked).
    return asyncio.run(
        run_headless(
            cli_id=cli_id, directory=worktree_path, prompt=prompt,
            session_name=session_name, skip_permissions=skip_permissions,
            provider=provider, model=model, project_key=project_key,
        )
    )


# Strong references to in-flight headless start tasks. asyncio only keeps weak
# references to tasks, so without this set a fire-and-forget task can be garbage
# collected mid-flight and the run silently never starts.
_headless_start_tasks: set = set()


async def run_headless(
    cli_id: str, *, directory: str, prompt: str, session_name: str,
    skip_permissions: bool, provider: str, model: str | None,
    project_key: str | None = None,
) -> dict:
    """Spawn the headless subprocess and consume its event stream.

    Mirrors the lifetime contract ``sandcastle_transport`` provides:

    - Reserve the slot via ``session_registry.reserve_external`` (caller's
      responsibility, done in :func:`headless_transport`).
    - Track the subprocess in :data:`_headless_processes` for the liveness
      source.
    - Drain the stream line by line, parse each into a
      :class:`StructuredEvent`, and dispatch via the local ``_on_event``
      callback (rate-limit handling is its own function so it's testable in
      isolation).
    - Release the slot on exit, regardless of return code.
    """
    argv = _build_argv(
        resolve_cli_executable(cli_id), prompt, skip_permissions=skip_permissions,
    )

    env = _build_env(provider=provider, model=model, project_key=project_key)

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=directory,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    _headless_processes[session_name] = proc
    try:
        returncode = await _consume_stream(proc, session_name, provider=provider)
        return {
            "session_name": session_name,
            "transport": "headless",
            "exit_code": returncode,
        }
    finally:
        # Drop from the registry BEFORE releasing the slot so the liveness
        # source can't briefly report a dead process as alive.
        _headless_processes.pop(session_name, None)
        session_registry.release_external(session_name)


def _build_argv(executable: str, prompt: str, *, skip_permissions: bool) -> list[str]:
    """Build the argv for a headless stream-json invocation.

    No shell interpretation: passed to ``asyncio.create_subprocess_exec``,
    which does NOT have the tmux ``~16KB`` imsg cap that
    ``runs.spawn._prompt_file_shell_command`` exists to work around. The
    prompt therefore lands as a plain argv element.
    """
    argv = [
        executable, "-p",
        "--output-format", "stream-json",
        "--verbose",
    ]
    if skip_permissions:
        argv.append("--dangerously-skip-permissions")
    argv.append("--")
    argv.append(prompt)
    return argv


def _build_env(*, provider: str, model: str | None,
               project_key: str | None) -> dict[str, str] | None:
    """Build the explicit env for the subprocess.

    Mirrors the env-injection pattern in ``runs.spawn.spawn_session``: never
    merge ``os.environ``, only inject what the agent needs (provider creds +
    the COCKPIT_* bookkeeping vars). Returns None to let the child inherit
    the parent env when there's no provider/project context to inject.
    """
    from app.services.agentic_cli.provider_env import build_provider_env, build_spawn_env

    provider_env = build_provider_env(provider, model=model, cli_id="claude-code")
    spawn_env = build_spawn_env(
        provider_env=provider_env, extra_env=None,
        project_key=project_key, runtime="headless",
    )
    return dict(spawn_env.env)


async def _consume_stream(proc: asyncio.subprocess.Process, session_name: str,
                          *, provider: str) -> int:
    """Drain the subprocess's stdout, parse each JSON line, dispatch via _on_event.

    Reads until EOF; collects stderr in parallel so a hang in the parser
    doesn't leak a child. The first ``readline`` after EOF returns ``b""`` so
    the loop terminates naturally.
    """
    assert proc.stdout is not None
    async def _read_stderr() -> bytes:
        assert proc.stderr is not None
        return await proc.stderr.read()

    stderr_task = asyncio.create_task(_read_stderr())
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                logger.warning(
                    "headless %s: dropping non-JSON line: %r",
                    session_name, text[:200],
                )
                continue
            structured = parse_structured_event(map_stream_event(payload))
            await _on_event(structured, session_name=session_name, provider=provider)
        returncode = await proc.wait()
    finally:
        stderr = await stderr_task
        if stderr:
            logger.warning(
                "headless %s: stderr:\n%s", session_name, stderr.decode(errors="replace"),
            )

    return returncode


async def _on_event(event: StructuredEvent, *, session_name: str, provider: str) -> None:
    """Dispatch a single structured event.

    v1 wires only the load-bearing signals into the dispatch state machine:
    ``rate_limit`` → ``set_paused_until`` (typed, replaces
    ``FALLBACK_PAUSE_HOURS``); ``session_init`` → log + readiness marker
    (replaces the tmux pane box-drawing scrape for headless); everything else
    is debug-logged so trace mode sees them without spamming the comment feed.
    """
    if event.type == StructuredEventType.RATE_LIMIT:
        assert isinstance(event, RateLimitEvent)
        await _on_rate_limit_event(event, provider=provider)
        return
    if event.type == StructuredEventType.SESSION_INIT:
        logger.info(
            "headless %s: session_init received (claude_session_id=%s, model=%s)",
            session_name, event.session_id, event.model,
        )
        return
    if event.type == StructuredEventType.USAGE_RESULT:
        logger.info(
            "headless %s: usage_result stop_reason=%s cost_usd=%s",
            session_name, event.stop_reason, event.cost_usd,
        )
        return
    if event.type == StructuredEventType.ERROR:
        logger.warning(
            "headless %s: error: %s", session_name, event.message,
        )
        return
    logger.debug("headless %s: %s event", session_name, event.type.value)


async def _on_rate_limit_event(event: RateLimitEvent, *, provider: str) -> None:
    """Translate a typed rate-limit event into a dispatch pause.

    The whole point of the headless transport (§6.1 of the spike): the
    tmux-path's pane-substring scrape + ``FALLBACK_PAUSE_HOURS`` guess is
    replaced by the precise ``resets_at`` timestamp Claude emits
    (``resetsAt`` on the wire, unix epoch seconds).

    When ``resets_at`` is absent (the carrier has never been documented as
    required — see :class:`RateLimitEvent`'s docstring) we fall back to
    ``FALLBACK_PAUSE_HOURS`` rather than skipping the pause, so this path
    degrades to the legacy behaviour instead of silently dropping a 429.

    Opens its own DB session because the runner is fire-and-forget — there is
    no caller session to reuse. The pause write is one row in ``KanbanMeta``
    and a commit; cheap.
    """
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dispatch_pause import set_paused_until

    if event.resets_at is not None:
        until = datetime.fromtimestamp(event.resets_at, UTC)
        logger.info(
            "headless rate_limit: pausing dispatch for provider=%s until %s (typed resets_at)",
            provider, until.isoformat(),
        )
    else:
        until = datetime.now(UTC) + timedelta(hours=FALLBACK_PAUSE_HOURS)
        logger.warning(
            "headless rate_limit: resets_at missing — falling back to "
            "FALLBACK_PAUSE_HOURS=%sh for provider=%s",
            FALLBACK_PAUSE_HOURS, provider,
        )

    async with KanbanSessionLocal() as session:
        await set_paused_until(session, until, provider=provider)
        await session.commit()


# ---- mapping ---------------------------------------------------------------

# The mapping below is intentionally a pure function (no I/O, no logger, no
# exceptions) so the test suite can pin each row of the spike §4 table in
# isolation. Adding a new stream-json event type means adding one ``elif`` arm
# + one new mapping test — no behavior change to anything else.

def map_stream_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Map a raw stream-json payload to the dict shape ``parse_structured_event`` accepts.

    Implements the table in
    ``docs/cockpit/headless-stream-json-transport-spike.md`` §4. Returns a dict
    the schema's discriminator picks up on; unknown payloads fall through to
    ``{"type": payload.get("type"), **payload}`` so the schema's
    ValidationError carries the original event verbatim for debugging.

    The mapping covers:

    - ``system`` + ``subtype=init`` → ``session_init``
    - ``assistant`` content ``text``/``thinking`` → ``message_chunk``
    - ``assistant`` content ``tool_use`` → ``tool_call`` in_progress
    - ``user`` content ``tool_result`` → ``tool_call`` completed/failed
    - ``rate_limit_event`` → ``rate_limit`` (camelCase → snake_case)
    - ``result`` is_error → ``usage_result`` or ``error``
    """
    ptype = payload.get("type")

    if ptype == "system" and payload.get("subtype") == "init":
        return {
            "type": StructuredEventType.SESSION_INIT.value,
            "session_id": payload["session_id"],
            "cwd": payload.get("cwd"),
            "model": payload.get("model"),
            "permission_mode": payload.get("permissionMode"),
        }

    if ptype == "assistant":
        message = payload.get("message") or {}
        content = message.get("content") or []
        # Find the first meaningful content block — assistant messages can
        # carry multiple types in one event; we emit one structured event per
        # block but the test suite pins a single-block shape, so mapping
        # takes the first block.
        for block in content:
            btype = block.get("type")
            if btype == "text":
                return {
                    "type": StructuredEventType.MESSAGE_CHUNK.value,
                    "role": MessageRole.ASSISTANT.value,
                    "text": block.get("text", ""),
                }
            if btype == "thinking":
                return {
                    "type": StructuredEventType.MESSAGE_CHUNK.value,
                    "role": MessageRole.THOUGHT.value,
                    "text": block.get("thinking", ""),
                }
            if btype == "tool_use":
                name = block.get("name")
                return {
                    "type": StructuredEventType.TOOL_CALL.value,
                    "tool_call_id": block["id"],
                    "title": name,
                    "kind": name.lower() if isinstance(name, str) else None,
                    "status": ToolCallStatus.IN_PROGRESS.value,
                    "raw_input": block.get("input"),
                }
        return {"type": ptype, **payload}

    if ptype == "user":
        message = payload.get("message") or {}
        content = message.get("content") or []
        for block in content:
            btype = block.get("type")
            if btype in ("tool_result", "tool_use_result"):
                status = (
                    ToolCallStatus.FAILED.value
                    if block.get("is_error")
                    else ToolCallStatus.COMPLETED.value
                )
                return {
                    "type": StructuredEventType.TOOL_CALL.value,
                    "tool_call_id": block.get("tool_use_id"),
                    "status": status,
                    "raw_output": _normalize_tool_result_content(
                        block.get("content"),
                    ),
                }
        return {"type": ptype, **payload}

    if ptype == "rate_limit_event":
        info = payload.get("rate_limit_info") or {}
        return {
            "type": StructuredEventType.RATE_LIMIT.value,
            "session_id": payload.get("session_id"),
            "status": info.get("status", "allowed"),
            "resets_at": info.get("resetsAt"),
            "rate_limit_type": info.get("rateLimitType"),
            "utilization": info.get("utilization"),
            "is_using_overage": info.get("isUsingOverage"),
            "surpassed_threshold": info.get("surpassedThreshold"),
        }

    if ptype == "result":
        is_error = bool(payload.get("is_error"))
        if is_error:
            return {
                "type": StructuredEventType.ERROR.value,
                "message": str(payload.get("result") or payload.get("subtype") or "error"),
            }
        usage = payload.get("usage") or {}
        total = (
            (usage.get("input_tokens") or 0)
            + (usage.get("output_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
        ) or None
        return {
            "type": StructuredEventType.USAGE_RESULT.value,
            "stop_reason": payload.get("subtype"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": total,
            "cost_usd": payload.get("total_cost_usd"),
        }

    # Unknown / unsupported event type: pass through so the schema's
    # ValidationError surfaces the original payload for debugging.
    return {"type": ptype, **payload}


def _normalize_tool_result_content(content: Any) -> dict[str, Any] | None:
    """Wrap a tool_result's ``content`` field into the ``raw_output`` shape.

    Claude's stream-json emits ``content`` as either a string (simple cases)
    or a list of content blocks (rich tool output). We normalize both into a
    dict so ``ToolCallEvent.raw_output`` always carries a uniform shape.
    """
    if content is None:
        return None
    if isinstance(content, str):
        return {"content": content}
    if isinstance(content, list):
        return {"blocks": content}
    return {"content": content}


def _safe_resolve_project_key(repo: str) -> str | None:
    """Thin wrapper around the safe project-key resolver.

    Import indirection so tests that import ``headless_runner`` without a full
    app setup don't fail at module-import time; the resolver itself is a
    no-op on missing git remotes, so the fallback is benign.
    """
    try:
        from app.kanban.dispatch import safe_resolve_project_key
        return safe_resolve_project_key(repo)
    except Exception:
        return None