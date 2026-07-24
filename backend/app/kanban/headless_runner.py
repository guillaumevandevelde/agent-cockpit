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

Restart-survival (kanban card ``a450df1a…``):
A naive headless runner would die with the backend process — ``kill_tree``
reaps descendants, the stdout pipe closes on the next write, and the
in-memory registry is empty after restart. To match the worktree/tmux
transport's robustness, this module writes a durable **pidfile** per run
(``<worktree>/.cockpit-headless.json``) holding the pid + worktree + log
path. The subprocess is spawned with ``start_new_session=True`` so it
becomes its own session leader (PID == PGID == SID) — a backend exit or
``cockpit.sh restart`` doesn't propagate. ``live_headless_sessions()``
combines the in-memory cache with OS-level pid + cwd checks, so a run is
correctly reported as alive after a backend restart as long as its pidfile
points to a live process whose cwd is still the original worktree. On
backend startup, :func:`adopt_headless_runs` walks every registered
project's worktrees, re-attaches still-alive pidfiles to the in-memory
registry, and cleans up dead ones — all BEFORE the dispatch scheduler /
reaper runs, so a reaper tick never sees an adopted run as dead.

Events are written to an on-disk JSONL log (``<worktree>/.cockpit-headless-
events.jsonl``) capped at 16 MB with head-truncation, so a pathological
event loop can never blow up disk and a restart can always inspect what
happened. The cap is measured (analyses §5.3): 16 MB is ~2× the largest
run ever observed in 998 dispatched transcripts, ~14× p90. Adoption
re-reads the log from the start; re-processing is idempotent because each
structured event either just logs or sets a pause timestamp.

Public surface (everything else is module-private):

- :func:`headless_transport` — the ``SpawnTransport`` callable used by the
  dispatcher.
- :func:`run_headless` — coroutine that owns one end-to-end run: worktree +
  subprocess spawn + event-stream consumption + cleanup.
- :func:`live_headless_sessions` — third liveness source consumed by
  ``dispatch.reap_stale_claims``. Defensive: any failure yields ``set()`` so
  a registry hiccup makes the reaper *eager*, never blind.
- :func:`kill_headless_session` — best-effort SIGTERM for the human-takeover
  promotion (``app.kanban.takeover``); signals only, never mutates the
  registry (``run_headless``'s own ``finally`` block does that).
- :func:`adopt_headless_runs` — startup hook that walks registered
  projects' worktrees, OS-verifies each pidfile, and repopulates the
  in-memory registry. Runs BEFORE the dispatch scheduler/reaper (wired in
  ``app.main.lifespan``).
- :class:`EventLogWriter` — bounded JSONL append writer with head-truncation
  at 16 MB. Tested in isolation so the runner doesn't have to know about
  the cap mechanism.
- :func:`map_stream_event` — pure mapping from a raw stream-json payload to
  the dict shape :func:`parse_structured_event` accepts. Tested in isolation
  so the parser doesn't have to know about Claude's wire format.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pydantic

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
# name. Populated by :func:`run_headless` (after spawn) and by
# :func:`adopt_headless_runs` (at backend startup, when the in-memory cache
# is empty but durable pidfiles are on disk). Each value is a
# :class:`HeadlessRunRecord` carrying just the durable identity (pid, log
# path, worktree) — NOT the :class:`asyncio.subprocess.Process` object
# itself, which doesn't survive a backend restart. OS-level liveness is
# checked via :func:`_os_pid_alive` each tick.
#
# Why records instead of Process objects: the Process object's lifetime is
# tied to the asyncio loop that created it. After a backend restart the
# loop is new, every old Process object is gone, and any pid-file that
# points to a real running subprocess needs to be adopted by the new
# process. The record shape (pid + paths) is the minimum we need to kill
# and to detect liveness; the Process wrapper is incidental.
_HEADLESS_PIDFILE_NAME = ".cockpit-headless.json"
_HEADLESS_LOG_NAME = ".cockpit-headless-events.jsonl"
_DEFAULT_LOG_CAP_BYTES = 16 * 1024 * 1024  # 16 MB; see analyse §5.3 for derivation
_headless_processes: dict[str, HeadlessRunRecord] = {}
# Project roots that may host a headless worktree. Populated by
# :func:`adopt_headless_runs` at startup (canonical set, used per dispatch
# tick) and by :func:`run_headless` (so a fresh run is visible without
# waiting for the next adoption). Used by
# :func:`_known_worktree_dirs` to enumerate where pidfiles may live.
# Bounded by the number of registered projects on the device.
_known_project_roots: set[str] = set()


def _remember_project_root(project_root: str) -> None:
    """Add ``project_root`` to the cache of pidfile-search roots.

    Idempotent. Called by :func:`run_headless` after spawn and by
    :func:`adopt_headless_runs` while walking registered projects. Tests
    use this to seed the cache without going through adoption.
    """
    _known_project_roots.add(str(project_root))


@dataclass(frozen=True)
class HeadlessRunRecord:
    """Durable identity of a single headless run.

    Holds just enough to (a) check liveness via the OS, (b) signal the
    process group for human-takeover / final cleanup, and (c) locate the
    on-disk event log. The Process object itself lives in the asyncio
    loop that spawned it; this record survives a backend restart because
    it's also persisted to the pidfile and re-derived at adoption time.
    """
    session_name: str
    pid: int
    worktree_path: str
    log_path: Path
    started_at: float  # unix epoch


def _os_pid_alive(pid: int, expected_cwd: str) -> bool:
    """True iff ``pid`` exists AND its cwd matches ``expected_cwd``.

    Two checks because pid-reuse is real (a fresh process can pick up a pid
    the original run had): the ``os.kill(pid, 0)`` returns no error for any
    live pid — even one we don't own — so without the cwd sanity check a
    pid-reuse would falsely report 'alive', the reaper would skip the claim,
    the original session is long gone, and the work is orphaned. Linux-only
    ``/proc/<pid>/cwd`` readlink gives the absolute path the process was
    started in; comparing it to the recorded worktree catches reuse from
    any other cwd. ``PermissionError`` means "exists but not ours" — we
    treat that as alive (the operator can inspect it manually) but the cwd
    check still runs and catches the foreign-cwd case.
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
        # /proc/<pid>/cwd exists but we can't readlink it — usually pid 1
        # (systemd) or another process in a different user namespace.
        # Conservative: treat as foreign so we don't keep claims alive
        # for processes we don't actually own. The reaper will release
        # the claim and a fresh dispatch will spawn correctly.
        # NOTE: PermissionError IS a subclass of OSError, so this clause
        # must come before the broad OSError catch below.
        return False
    except (OSError, FileNotFoundError):
        # Not Linux, or procfs disabled. Fall back to pid-alive only —
        # the kernel already confirmed the pid exists (kill(pid, 0)
        # returned without ProcessLookupError).
        return True
    return str(cwd_link) == expected_cwd


def _read_pidfile(pidfile: Path) -> HeadlessRunRecord | None:
    """Parse a pidfile into a ``HeadlessRunRecord``; None on any failure.

    Defensive: malformed files are treated as "not a live run" and removed
    by the caller, so a half-written pidfile (e.g. crashed backend mid-
    write) doesn't wedge the liveness source.
    """
    try:
        data = json.loads(pidfile.read_text(encoding="utf-8"))
        return HeadlessRunRecord(
            session_name=data["session_name"],
            pid=int(data["pid"]),
            worktree_path=data["worktree_path"],
            log_path=Path(data["log_path"]),
            started_at=float(data["started_at"]),
        )
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return None


def _known_worktree_dirs() -> list[Path]:
    """Enumerate the worktree directories that may host a pidfile.

    The single source of truth is the on-disk pidfile in each worktree
    (kaart ``a450df1a…`` AC 2 — liveness without in-memory state). We
    walk ``<project>/.claude/worktrees/*`` for every project root we've
    remembered via :func:`_remember_project_root` — populated by
    :func:`adopt_headless_runs` at startup (canonical set) and by
    :func:`run_headless` for every fresh run.
    """
    return [
        Path(p) / ".claude" / "worktrees"
        for p in sorted(_known_project_roots)
    ]


def live_headless_sessions() -> set[str]:
    """Session names of headless subprocesses that are still running.

    Third liveness source for ``reap_stale_claims`` (alongside tmux and
    sandcastle). Reads from durable pidfiles (one per worktree), NOT the
    in-memory cache — this is the canonical answer to "is this headless
    run still alive?" and works correctly even with an empty cache (i.e.
    immediately after a backend restart, before adoption has run).

    Each pidfile is OS-verified (pid + cwd, see :func:`_os_pid_alive`):
    a dead pid drops the session from the result; a pid that's alive but
    in a different cwd is treated as foreign (pid-reuse) and also
    dropped. The cache is updated lazily: dead records are pruned so it
    doesn't accumulate, live ones are populated so :func:`kill_headless_session`
    has a fast path without re-reading the disk.

    Defensive: any failure yields ``set()`` so a hiccup makes the reaper
    *eager* (more likely to release a dead claim), never blind (less
    likely — that's the bug the empty-set policy avoids).
    """
    try:
        alive: set[str] = set()
        dead_pids: list[tuple[str, int]] = []
        for worktrees_dir in _known_worktree_dirs():
            if not worktrees_dir.is_dir():
                continue
            for wt_dir in worktrees_dir.iterdir():
                if not wt_dir.is_dir():
                    continue
                pidfile = wt_dir / _HEADLESS_PIDFILE_NAME
                if not pidfile.exists():
                    continue
                rec = _read_pidfile(pidfile)
                if rec is None:
                    # Malformed → drop it so we don't keep retrying.
                    try:
                        pidfile.unlink()
                    except OSError:
                        pass
                    continue
                if _os_pid_alive(rec.pid, rec.worktree_path):
                    alive.add(rec.session_name)
                    # Refresh the cache so kill_headless_session has a
                    # fast path (also: re-reserves the session_registry
                    # slot on adoption; here we just keep the record
                    # fresh).
                    _headless_processes[rec.session_name] = rec
                else:
                    # Dead pidfile — prune it so the reaper doesn't
                    # re-discover it next tick. ``session_recovery`` /
                    # To Resume handle the dead-card path via the
                    # normal agent-column cleanup, exactly as analyse
                    # §5.1 prescribes.
                    try:
                        pidfile.unlink()
                    except OSError:
                        pass
                    dead_pids.append((rec.session_name, rec.pid))
        for name, _pid in dead_pids:
            _headless_processes.pop(name, None)
        return alive
    except Exception:
        logger.exception("could not query live headless sessions")
        return set()


def kill_headless_session(session_name: str) -> bool:
    """Best-effort SIGTERM of a still-running headless subprocess.

    First step of the human-takeover promotion
    (`docs/cockpit/human-takeover-headless-decision.md` §7 point 2): end the
    headless process before spawning the tmux ``--resume`` replacement. Only
    signals — :func:`run_headless`'s own ``finally`` block drains
    ``_headless_processes`` once the process actually exits, so mutating the
    registry here would race it.

    Reads the pid from the in-memory cache first, falling back to the
    on-disk pidfile when the cache is empty (post-restart, before
    adoption has repopulated). Signals the process group (``os.killpg``)
    since the subprocess is its own session leader
    (``start_new_session=True``), so any grandchildren it spawned (e.g.
    a shell wrapper) get the signal too — same pattern as
    ``app.services.sandcastle_service._signal_process_group``.

    Returns True when a live process was signaled, False when there was
    nothing to kill (unknown session name, or already exited).
    """
    rec = _headless_processes.get(session_name)
    if rec is None:
        # Cache miss — try the pidfile (post-restart path).
        for worktrees_dir in _known_worktree_dirs():
            pidfile = worktrees_dir / session_name / _HEADLESS_PIDFILE_NAME
            if pidfile.exists():
                rec = _read_pidfile(pidfile)
                break
    if rec is None:
        return False
    if not _os_pid_alive(rec.pid, rec.worktree_path):
        _headless_processes.pop(session_name, None)
        return False
    try:
        os.killpg(rec.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    return True


# ---- event-log writer -------------------------------------------------------
#
# Bounded JSONL append writer for the headless event stream. Each event line
# is appended as-is (already a string when callers have it; otherwise
# json.dumps'd). When the file would exceed ``cap_bytes``, the writer
# truncates from the *head* — oldest events are dropped first, newest stay.
# Why head-truncation: the most recent events are also the most likely to
# still be in-flight at adoption time (the consumer falls behind the
# producer briefly, then catches up). Truncating the tail would silently
# lose the events that matter most during a restart, while head-truncation
# keeps the boundary "you might miss the very first events of a long run,
# but you will see everything from N seconds before the crash" — which is
# the visible-fault shape operators actually need.
#
# The cap (16 MB by default) is bounded above: even a hypothetical patho-
# logical loop generating 16 MB of events has to fit in 16 MB on disk. The
# implementation measures the size on disk after each append so we don't
# need to track every byte in memory, and re-opens the file in append mode
# after a truncation so subsequent appends are O(1).


class EventLogWriter:
    """Append-only JSONL event log with head-truncating size cap.

    Appends land as a single line (``str + "\\n"``). When the file would
    exceed ``cap_bytes``, the writer truncates the oldest bytes (preserving
    line boundaries — the first byte kept is the byte AFTER the first
    newline in the truncated window). The cap exists to bound pathological
    loops; analyse §5.3 shows 16 MB is ~2× the largest run ever observed
    in 998 dispatched transcripts and ~14× p90, so the truncation path
    should almost never fire in normal traffic.
    """

    def __init__(self, path: Path, cap_bytes: int = _DEFAULT_LOG_CAP_BYTES):
        self.path = path
        self.cap_bytes = cap_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._size = self.path.stat().st_size if self.path.exists() else 0
        self._f = self.path.open("a", encoding="utf-8", buffering=1)

    def append(self, line: str) -> None:
        if not line.endswith("\n"):
            line = line + "\n"
        self._f.write(line)
        self._f.flush()
        self._size += len(line.encode("utf-8"))
        if self._size > self.cap_bytes:
            self._truncate_head()

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass

    def _truncate_head(self) -> None:
        self._f.close()
        try:
            with self.path.open("rb") as f:
                # Keep the last ``cap_bytes`` bytes (a rough upper bound on
                # what's left); trim to a line boundary so we don't keep a
                # half-written first line.
                f.seek(-self.cap_bytes, 2)
                buf = f.read()
            nl = buf.find(b"\n")
            if 0 <= nl < len(buf) - 1:
                buf = buf[nl + 1:]
            self.path.write_bytes(buf)
            self._size = len(buf)
        except OSError:
            logger.exception("event log truncate failed for %s", self.path)
        self._f = self.path.open("a", encoding="utf-8", buffering=1)


def _pidfile_path(worktree_path: str) -> Path:
    """Where to write the durable headless-run record for ``worktree_path``."""
    return Path(worktree_path) / _HEADLESS_PIDFILE_NAME


def _log_path(worktree_path: str) -> Path:
    """Where the on-disk event log lives for a worktree."""
    return Path(worktree_path) / _HEADLESS_LOG_NAME


def _write_pidfile(record: HeadlessRunRecord) -> None:
    """Persist the run record to disk. Atomic via ``write_text``.

    The pidfile is the durable source of truth for restart-survival — if
    the backend crashes between spawn and the ``run_headless`` finally
    block, the next backend startup reads it via :func:`adopt_headless_runs`
    and recovers the run instead of treating it as dead and re-dispatching.
    """
    pidfile = _pidfile_path(record.worktree_path)
    payload = {
        "session_name": record.session_name,
        "pid": record.pid,
        "worktree_path": record.worktree_path,
        "log_path": str(record.log_path),
        "started_at": record.started_at,
    }
    try:
        pidfile.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        logger.exception(
            "could not write headless pidfile for %s", record.session_name,
        )


def _remove_pidfile(worktree_path: str, expected_pid: int) -> None:
    """Remove the pidfile iff its pid still matches ``expected_pid``.

    The pid check is the same race-detector as the OS-liveness check: if
    the pidfile has been overwritten by a fresh run after our spawn, we
    must NOT remove the new run's record. Tolerant of a missing file
    (already cleaned up by adoption).
    """
    pidfile = _pidfile_path(worktree_path)
    try:
        raw = pidfile.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    except OSError:
        logger.exception("could not read headless pidfile %s", pidfile)
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
        logger.exception("could not remove headless pidfile %s", pidfile)


def adopt_headless_runs(project_paths: list[str]) -> int:
    """Re-attach still-alive headless runs from durable pidfiles.

    Called once at backend startup, BEFORE the dispatch scheduler and reaper
    start ticking — see ``app.main.lifespan``. For each registered
    project, walks every worktree under
    ``<project>/.claude/worktrees/*`` and, if a ``.cockpit-headless.json``
    pidfile is present, OS-verifies its pid (pid alive AND cwd matches the
    recorded worktree, see :func:`_os_pid_alive`).

    The primary job is re-reserving the slot in the in-memory session
    registry (so ``can_add_session()`` is honest post-restart) and
    populating the in-memory cache (so ``kill_headless_session`` has a
    fast path). ``live_headless_sessions()`` itself reads pidfiles
    directly and doesn't need a populated cache, but the cache keeps
    the rest of the liveness machinery happy.

    The ordering matters: if the reaper's first tick runs before adoption,
    every live headless run looks dead → reaper releases the claims →
    dispatcher re-spawns in the same worktree → two agents on one branch.
    Adoption-first is the same ordering ``session_recovery`` already uses,
    so both mechanisms live in the same startup-lifespan block.

    Dead pidfiles are cleaned up here too (rather than waiting for the
    next ``live_headless_sessions()`` call) so the reaper doesn't see a
    dead session's pidfile at all on its first tick.

    Returns the number of runs adopted (for tests + an info log).
    """
    adopted = 0
    for project_path in project_paths:
        project_root = str(project_path)
        # Always remember the project root, even if no pidfile exists
        # there — that's how a freshly-registered project becomes visible
        # to ``live_headless_sessions`` on its first dispatch tick.
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
                    "headless adopt: malformed pidfile %s, removing", pidfile,
                )
                try:
                    pidfile.unlink()
                except OSError:
                    pass
                continue
            if not _os_pid_alive(rec.pid, rec.worktree_path):
                logger.info(
                    "headless adopt: pidfile %s points to dead/foreign pid %d; "
                    "removing",
                    pidfile, rec.pid,
                )
                try:
                    pidfile.unlink()
                except OSError:
                    pass
                continue
            _headless_processes[rec.session_name] = rec
            adopted += 1
            logger.info(
                "headless adopt: adopted session %s (pid %d, worktree %s)",
                rec.session_name, rec.pid, rec.worktree_path,
            )
            # Re-reserve the slot in the session registry so the post-restart
            # ``can_add_session`` count is honest. The pre-restart backend
            # had reserved it, but that reservation died with the process.
            session_registry.reserve_external(rec.session_name)
    return adopted


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
                       model: str | None = None,
                       endpoint_name: str | None = None,
                       endpoint_base_url: str | None = None,
                       endpoint_auth_token: str | None = None) -> dict:
    """SpawnTransport sibling for headless ``stream-json`` runs.

    Mirrors :func:`app.kanban.dispatch.make_worktree_transport`'s signature so
    the dispatcher can swap transparently. The worktree branch and dir stay
    the canonical three identity facets (claim, branch, worktree-dir — see
    spike §5.1); only the liveness-orakel changes, which is what
    :func:`live_headless_sessions` is for.

    kaart 27317b4871… (FCR gap 7): the dispatcher forwards
    ``endpoint_name`` / ``endpoint_base_url`` / ``endpoint_auth_token``
    through the SpawnTransport protocol whenever ``provider ==
    PROVIDER_COMPATIBLE`` (so the same code path serves worktree,
    sandcastle, headless, and resume). The previous signature dropped
    them on the floor — headless compatible dispatches ended in a
    ``TypeError`` because the protocol expanded but the implementation
    didn't. The kwargs are threaded into ``SpawnCommandOptions`` so
    ``run_headless``'s downstream ``build_provider_env`` sees the
    endpoint config the same way the worktree path does.

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
                endpoint_name=endpoint_name,
                endpoint_base_url=endpoint_base_url,
                endpoint_auth_token=endpoint_auth_token,
                project_key=project_key,
            )
        )
        # Strong reference so the task can't be GC'd before it runs (same
        # pattern as _sandcastle_start_tasks in dispatch.py). The done
        # callback ALSO logs the task's exception if it has one — the
        # previous ``_headless_start_tasks.discard`` callback silently
        # dropped exceptions, which is what the card calls out (AC 3).
        _headless_start_tasks.add(task)
        task.add_done_callback(_headless_task_done_callback)

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
            provider=provider, model=model,
            endpoint_name=endpoint_name,
            endpoint_base_url=endpoint_base_url,
            endpoint_auth_token=endpoint_auth_token,
            project_key=project_key,
        )
    )


# Strong references to in-flight headless start tasks. asyncio only keeps weak
# references to tasks, so without this set a fire-and-forget task can be garbage
# collected mid-flight and the run silently never starts.
_headless_start_tasks: set = set()


def _headless_task_done_callback(task: asyncio.Task) -> None:
    """Discard a finished headless run task from the strong-ref set, AND surface any exception.

    AC 3 (kanban card d373be64…): the previous callback was
    ``_headless_start_tasks.discard``, which silently dropped the task's
    exception. A run that failed for any reason then only surfaced at GC as
    a "Task exception was never retrieved" warning — invisible from the
    dispatch log. This callback keeps the strong-ref-set discipline AND
    logs the exception with the full traceback, so an operator scanning
    the log sees both the breadcrumb and the cause.
    """
    _headless_start_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.exception(
            "headless run task %s failed: %s",
            task.get_name() or "<unnamed>", exc, exc_info=exc,
        )


async def run_headless(
    cli_id: str, *, directory: str, prompt: str, session_name: str,
    skip_permissions: bool, provider: str, model: str | None,
    endpoint_name: str | None = None,
    endpoint_base_url: str | None = None,
    endpoint_auth_token: str | None = None,
    project_key: str | None = None,
) -> dict:
    """Spawn the headless subprocess and consume its event stream.

    Mirrors the lifetime contract ``sandcastle_transport`` provides:

    - Reserve the slot via ``session_registry.reserve_external`` (caller's
      responsibility, done in :func:`headless_transport`).
    - Spawn with ``start_new_session=True`` so the subprocess is its own
      session leader — a backend exit (``cockpit.sh restart`` / SIGTERM to
      the parent pgid) does NOT propagate. This is the ownership detach
      the worktree/tmux transport already enjoys for free (kaart
      ``a450df1a…`` AC 1).
    - Write a durable pidfile (``<worktree>/.cockpit-headless.json``)
      holding pid + worktree + log path. The pidfile is the source of
      truth that survives a backend restart; :func:`adopt_headless_runs`
      reads it at startup and re-attaches the run to the in-memory
      registry BEFORE the reaper's first tick.
    - Track the subprocess in :data:`_headless_processes` (as a
      :class:`HeadlessRunRecord`) for the liveness source.
    - Drain the stream line by line, parse each into a
      :class:`StructuredEvent`, and dispatch via the local ``_on_event``
      callback (rate-limit handling is its own function so it's testable
      in isolation). Each parsed event is also written to the on-disk
      JSONL log so a restart can inspect what happened and the cap bounds
      a pathological event loop.
    - Release the slot on exit, regardless of return code. Remove the
      pidfile only when the recorded pid still matches our pid (a stale
      or rewritten pidfile is left alone — a fresh run owns that record).
    """
    argv = _build_argv(
        resolve_cli_executable(cli_id), prompt, skip_permissions=skip_permissions,
    )

    env = _build_env(
        cli_id=cli_id,
        provider=provider, model=model, project_key=project_key,
        endpoint_base_url=endpoint_base_url,
        endpoint_auth_token=endpoint_auth_token,
    )

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=directory,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        start_new_session=True,  # kaart a450df1a… AC 1
    )
    record = HeadlessRunRecord(
        session_name=session_name,
        pid=proc.pid,
        worktree_path=directory,
        log_path=_log_path(directory),
        started_at=time.time(),
    )
    # Project root is two parents up from the worktree:
    #   <project>/.claude/worktrees/<session_name>
    # Remembering it makes the run visible to ``live_headless_sessions``
    # immediately (without waiting for the next adoption cycle).
    _remember_project_root(Path(directory).parent.parent.parent)
    _headless_processes[session_name] = record
    _write_pidfile(record)  # durable; survives backend restart
    log_writer = EventLogWriter(record.log_path)
    try:
        returncode = await _consume_stream(
            proc, session_name, provider=provider, log_writer=log_writer,
        )
        return {
            "session_name": session_name,
            "transport": "headless",
            "exit_code": returncode,
        }
    finally:
        # AC 1 (kaart d373be64…): every exit path must leave the
        # subprocess gone — ``_consume_stream``'s own finally already
        # terminates the child on the unexpected-exception path, but
        # there's a narrow window where a buggy code path inside
        # _consume_stream might return without finishing the cleanup
        # (e.g. a CancelledError injected from outside the try block).
        # This block is the safety net: signal anything still alive, then
        # wait for the actual reap. Cleanup errors are caught and logged
        # so they don't replace the original exception propagating up.
        try:
            if proc.returncode is None:
                try:
                    # Kill the entire process group (subprocess is its
                    # own session leader, so PGID == PID). Same pattern
                    # as ``app.services.sandcastle_service._signal_process_group``.
                    os.killpg(proc.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except TimeoutError:
                    logger.warning(
                        "headless %s: SIGTERM did not stop process, killing",
                        session_name,
                    )
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                    await proc.wait()
        except Exception:
            logger.exception(
                "headless %s: error during subprocess cleanup", session_name,
            )
        # Close the event-log writer BEFORE the registry/pidfile cleanup
        # so the log is flushed to disk in case adopt reads it.
        try:
            log_writer.close()
        except Exception:
            logger.exception(
                "headless %s: error closing event log writer", session_name,
            )
        # Drop from the registry and release the slot ONLY after the proc
        # is reaped. Releasing earlier would create a narrow window where
        # ``live_headless_sessions()`` reports the session as dead while
        # the subprocess is still alive — exactly the "dead-but-alive"
        # state the reaper uses to trigger a re-dispatch, and exactly
        # the failure mode this whole card exists to prevent.
        _headless_processes.pop(session_name, None)
        # Remove the pidfile only if our pid still owns it. After a
        # successful run this is true; if a fresh run somehow took over
        # this worktree, leave the new record alone.
        _remove_pidfile(directory, expected_pid=proc.pid)
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


def _build_env(*, cli_id: str, provider: str, model: str | None,
               project_key: str | None,
               endpoint_base_url: str | None = None,
               endpoint_auth_token: str | None = None) -> dict[str, str] | None:
    """Build the explicit env for the subprocess.

    Mirrors the env-injection pattern in ``runs.spawn.spawn_session``: never
    merge ``os.environ``, only inject what the agent needs (provider creds +
    the COCKPIT_* bookkeeping vars). Returns None to let the child inherit
    the parent env when there's no provider/project context to inject.

    kaart 27317b4871… (FCR gap 7): when ``provider == PROVIDER_COMPATIBLE``
    the dispatcher forwards the resolved endpoint's ``base_url`` +
    ``auth_token`` through the SpawnTransport contract. ``build_provider_env``
    reads them under the canonical ``base_url`` / ``auth_token`` kwargs
    (not the dispatcher-side ``endpoint_*`` names); pass them through here
    so the headless subprocess gets the same env the worktree transport
    produces for the same card.

    kaart 88f3c990… (provider-parity with worktree transport): ``cli_id``
    is the dispatched CLI's id, threaded through from ``run_headless`` —
    it MUST NOT be hard-coded to ``"claude-code"``, because every CLI has
    its own endpoint-routing mechanism and Claude-Code-specific env vars
    (``CLAUDE_CODE_USE_BEDROCK``, ``CLAUDE_CODE_AUTO_COMPACT_WINDOW``,
    the ``ANTHROPIC_*`` triple) are meaningless (or actively wrong) for
    Codex, OpenCode, Copilot, MiMo. The worktree transport passes
    ``cli.id`` to ``build_provider_env``; this function mirrors that.
    ``minimax_api_key`` and ``minimax_base_url`` come from the backend's
    Settings — the headless transport has no ``SpawnCommandOptions``
    carrier to hold them, so it reads settings directly (the worktree
    path reads ``settings.minimax_api_key`` + ``options.minimax_base_url
    or settings.minimax_base_url``; headless uses settings for both
    because the SpawnTransport protocol does not yet expose a
    per-card ``minimax_base_url`` override).
    """
    from app.config import settings
    from app.services.agentic_cli.provider_env import build_provider_env, build_spawn_env

    provider_env = build_provider_env(
        provider, model=model,
        cli_id=cli_id,
        minimax_api_key=settings.minimax_api_key,
        minimax_base_url=settings.minimax_base_url,
        base_url=endpoint_base_url,
        auth_token=endpoint_auth_token,
    )
    spawn_env = build_spawn_env(
        provider_env=provider_env, extra_env=None,
        project_key=project_key, runtime="headless",
    )
    return dict(spawn_env.env)


async def _consume_stream(proc: asyncio.subprocess.Process, session_name: str,
                          *, provider: str,
                          log_writer: EventLogWriter | None = None) -> int:
    """Drain the subprocess's stdout, parse each JSON line, dispatch via _on_event.

    Reads until EOF; collects stderr in parallel so a hang in the parser
    doesn't leak a child. The first ``readline`` after EOF returns ``b""`` so
    the loop terminates naturally.

    Each parsed event is also written verbatim to ``log_writer`` (when
    supplied) so a backend restart can inspect what happened via the
    durable on-disk log. Events that fail to map/parse are NOT written
    to the log — they were already logged with the payload for debugging,
    and adding a parallel "raw" log would double-write without adding
    information (analyse §6.2 AC 4 — the log is for visible events, not
    for the noise we already dropped).

    A single unmapped/parse-error event is logged and skipped (same shape as
    the non-JSON-line tolerance a few lines above) — a Claude-side
    vocabulary we don't yet know about must not kill the run and orphan the
    subprocess. ``map_stream_event`` deliberately passes unknown types
    through so the schema's ``ValidationError`` carries the original event
    verbatim; we catch that here and log the payload for debugging.
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
            # Tolerate a single unmapped/parse-error event — same shape as
            # the non-JSON-line tolerance above. The exception types here
            # are the realistic ones for a malformed Claude payload:
            # pydantic.ValidationError from parse_structured_event, plus
            # KeyError/TypeError/AttributeError/ValueError from
            # map_stream_event when ``payload["session_id"]`` is missing,
            # ``block`` is the wrong shape, etc. We deliberately do NOT
            # catch these around ``_on_event`` — a real bug there must
            # still kill the run, exactly as the card-eis asks (AC 2:
            # "een enkel onparseerbaar of ongemapt event doodt de run
            # niet" — i.e. parse-side, not handler-side).
            try:
                structured = parse_structured_event(map_stream_event(payload))
            except (
                pydantic.ValidationError, KeyError, TypeError,
                AttributeError, ValueError,
            ) as exc:
                logger.warning(
                    "headless %s: dropping event that failed to map/parse: %r (%s: %s)",
                    session_name, payload, type(exc).__name__, exc,
                )
                continue
            # Persist the raw event line to the on-disk log BEFORE
            # dispatching, so even if _on_event raises unexpectedly the
            # event is on disk for post-mortem inspection. ``line`` is the
            # already-stripped raw JSON the subprocess emitted; writing
            # the raw line keeps the log shape identical to what a
            # postmortem reader would replay.
            if log_writer is not None:
                try:
                    log_writer.append(line)
                except Exception:
                    logger.exception(
                        "headless %s: could not append event to log",
                        session_name,
                    )
            await _on_event(structured, session_name=session_name, provider=provider)
        returncode = await proc.wait()
    finally:
        # AC 1 (kanban card d373be64…): every exit path must leave the
        # subprocess reaped — both a normal EOF (proc has already exited)
        # and an unexpected exception (proc might still be alive). We
        # terminate + wait + kill-with-fallback BEFORE draining stderr so
        # a SIGTERM-ignoring child can't keep the stderr pipe open and
        # hang the drain below. Cleanup errors are caught and logged so
        # they don't replace the original exception propagating up.
        # Signals the process group (subprocess is its own session
        # leader via start_new_session=True) so any grandchildren also
        # get terminated — same pattern as
        # ``app.services.sandcastle_service._signal_process_group``.
        try:
            if proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except TimeoutError:
                    logger.warning(
                        "headless %s: SIGTERM did not stop process, killing",
                        session_name,
                    )
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                    await proc.wait()
        except Exception:
            logger.exception(
                "headless %s: error during subprocess cleanup", session_name,
            )
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