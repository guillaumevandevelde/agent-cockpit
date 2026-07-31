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


class McpServerConfigError(RuntimeError):
    """Claude Code skipped one or more MCP entries during startup."""


def _mcp_server_config_error(
    payload: Mapping[str, Any],
) -> McpServerConfigError | None:
    errors = payload.get("mcp_server_errors")
    if not errors:
        return None
    details = "; ".join(
        f"{item.get('name', '<unknown>')} ({item.get('type', 'unknown')}): "
        f"{item.get('message', 'no message')}"
        for item in errors
        if isinstance(item, Mapping)
    )
    return McpServerConfigError(
        f"Claude Code skipped MCP server configuration: {details or errors!r}"
    )


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


@dataclass
class HeadlessRunRecord:
    """Durable identity of a single headless run.

    Holds just enough to (a) check liveness via the OS, (b) signal the
    process group for human-takeover / final cleanup, (c) locate the
    on-disk event log, and (d) track how far the tailer has read so the
    next backend can resume from that offset.

    The Process object itself lives in the asyncio loop that spawned it;
    this record survives a backend restart because it's also persisted to
    the pidfile and re-derived at adoption time.

    ``last_read_offset`` is the byte offset into ``log_path`` the tailer
    has already dispatched. After ``adopt_headless_runs`` opens the
    log file it seeks to this offset and reads forward — if the file
    was truncated from the head (cap exceeded), the offset is reset to 0
    on the read side. Persisted to the pidfile on every dispatch so a
    restart can't lose more than one event's worth of progress.
    """
    session_name: str
    pid: int
    worktree_path: str
    log_path: Path
    started_at: float  # unix epoch
    last_read_offset: int = 0


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

    ``last_read_offset`` is optional in the payload — pidfiles written by
    older versions don't carry it, and we fall back to 0 (the tailer
    will re-read from the start; events are idempotent).
    """
    try:
        data = json.loads(pidfile.read_text(encoding="utf-8"))
        return HeadlessRunRecord(
            session_name=data["session_name"],
            pid=int(data["pid"]),
            worktree_path=data["worktree_path"],
            log_path=Path(data["log_path"]),
            started_at=float(data["started_at"]),
            last_read_offset=int(data.get("last_read_offset", 0)),
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

    Two double duties after the restart-survival fix (kaart ``a450df1a…``):

    1. Owns the file descriptor for the child subprocess's stdout.
       ``asyncio.create_subprocess_exec(..., stdout=log_writer.fileno())``
       hands the fd to the child; the child writes raw JSONL to the log
       file directly. That ownership transfer is what makes a backend
       exit (uvicorn crash, ``cockpit.sh restart``) survivable: the
       parent's pipe going away is no longer the child's problem, AND the
       log file keeps accumulating events even when the parent is gone.

    2. Bounded by ``cap_bytes`` with head-truncation. Appends land as a
       single line (``str + "\\n"``). When the file would exceed
       ``cap_bytes``, the writer truncates the oldest bytes (preserving
       line boundaries — the first byte kept is the byte AFTER the first
       newline in the truncated window). The cap exists to bound
       pathological loops; analyse §5.3 shows 16 MB is ~2× the largest
       run ever observed in 998 dispatched transcripts and ~14× p90, so
       the truncation path should almost never fire in normal traffic.

    After the restart-survival fix, ``append`` is advisory-only: the
    child writes through the inherited fd, and the tailer reads from
    the same file. ``append`` is preserved for the parent-side path
    (e.g. tests that hand-write events to the log) and for the
    truncation-trigger path. Truncation can also be invoked directly via
    :meth:`truncate_head` (the tailer does this when the file size
    crosses ``cap_bytes`` from the read side).
    """

    def __init__(self, path: Path, cap_bytes: int = _DEFAULT_LOG_CAP_BYTES):
        self.path = path
        self.cap_bytes = cap_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._size = self.path.stat().st_size if self.path.exists() else 0
        self._f = self.path.open("a", encoding="utf-8", buffering=1)

    def fileno(self) -> int:
        """Underlying file descriptor for the child to inherit as stdout.

        The fd is opened in append mode, so the child always writes at
        the current end-of-file (and the parent's reads via a separate
        fd see every byte). The parent's fd is closed by :meth:`close`
        but the child's copy survives exec — so when the parent exits
        the child keeps writing to the same file on disk.
        """
        return self._f.fileno()

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

    def truncate_head(self) -> None:
        """Drop the oldest bytes so the file fits under ``cap_bytes``.

        Public entry point for the tailer (which tracks the file size on
        the read side and triggers truncation when growth goes past the
        cap). Closes+reopens the append-mode fd so subsequent writes
        from the child land at the new end of file.
        """
        self._truncate_head()

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

    ``last_read_offset`` is updated on every dispatched event so a
    crash-orphaned reader doesn't re-process events the new tailer can
    see still in the log (events are idempotent, but skipping them
    saves the work).
    """
    pidfile = _pidfile_path(record.worktree_path)
    payload = {
        "session_name": record.session_name,
        "pid": record.pid,
        "worktree_path": record.worktree_path,
        "log_path": str(record.log_path),
        "started_at": record.started_at,
        "last_read_offset": record.last_read_offset,
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


def adopt_headless_runs(project_paths: list[str]) -> list[HeadlessRunRecord]:
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

    Returns the list of adopted records so the caller (the lifespan
    startup hook) can spawn a tailer task for each one — the tailer
    reads the on-disk event log from the persisted
    ``last_read_offset``, dispatches events, and persists new offsets
    back to the pidfile. Without the tailer, the on-disk log would
    keep growing but no consumer would be reading it.
    """
    adopted: list[HeadlessRunRecord] = []
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
            adopted.append(rec)
            logger.info(
                "headless adopt: adopted session %s (pid %d, worktree %s, "
                "resuming at offset %d)",
                rec.session_name, rec.pid, rec.worktree_path,
                rec.last_read_offset,
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


async def headless_transport(*, directory: str, prompt: str, session_name: str,
                       cli_id: str = "claude-code", provider: str = "anthropic",
                       model: str | None = None,
                       endpoint_name: str | None = None,
                       endpoint_base_url: str | None = None,
                       endpoint_auth_token: str | None = None,
                       # Accepted for SpawnTransport parity, deliberately
                       # unused — the RTK token-saver (kaart c31333bf…) is
                       # installed only by the worktree transport. See
                       # tests/test_spawn_transport_signature_parity.py.
                       card_id: str | None = None,
                       column_name: str | None = None) -> dict:
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

    startup_future = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(
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
            startup_future=startup_future,
        ),
        name=f"headless-run-{session_name}",
    )
    _headless_start_tasks.add(task)
    task.add_done_callback(_headless_task_done_callback)

    done, _ = await asyncio.wait(
        {startup_future, task}, return_when=asyncio.FIRST_COMPLETED,
    )
    if startup_future in done:
        startup_future.result()
    else:
        await task
        raise RuntimeError(f"headless {session_name} exited before session init")

    return {
        "session_name": session_name,
        "transport": "headless",
        "status": "started",
    }


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


def start_headless_tailer(record: HeadlessRunRecord) -> asyncio.Task | None:
    """Spawn a tailer task that consumes an adopted run's on-disk event log.

    Called by the lifespan startup hook for each record returned by
    :func:`adopt_headless_runs`. The tailer reads the on-disk JSONL log
    from ``record.last_read_offset`` (the offset the previous parent
    had reached), dispatches each event via ``_on_event``, and persists
    new offsets back to the pidfile. The task self-terminates when the
    subprocess is dead (per :func:`_os_pid_alive`) and the log is fully
    drained.

    Returns the spawned task so the caller can hold a strong reference
    (``_headless_start_tasks`` keeps one for GC safety). Returns None
    if the event loop is not running (sync context); the caller is
    expected to be async.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    log_writer = EventLogWriter(record.log_path)
    stop_event = asyncio.Event()
    task = loop.create_task(
        _consume_log_file(
            proc=None,
            log_path=record.log_path,
            session_name=record.session_name,
            # Adopted runs use a generic provider here; the record
            # doesn't carry one and the rate-limit handler resolves
            # the provider from the dispatch side when a rate-limit
            # event lands. ``"anthropic"`` matches the most common
            # case; a non-anthropic provider's events still parse
            # (the rate_limit handler is provider-agnostic on the
            # set_paused_until side).
            provider="anthropic",
            record=record,
            log_writer=log_writer,
            stop_event=stop_event,
        ),
        name=f"headless-tail-adopted-{record.session_name}",
    )
    _headless_start_tasks.add(task)
    task.add_done_callback(_headless_task_done_callback)
    return task


async def run_headless(
    cli_id: str, *, directory: str, prompt: str, session_name: str,
    skip_permissions: bool, provider: str, model: str | None,
    endpoint_name: str | None = None,
    endpoint_base_url: str | None = None,
    endpoint_auth_token: str | None = None,
    project_key: str | None = None,
    startup_future: asyncio.Future[None] | None = None,
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

    # Ensure the worktree + on-disk log file exist before spawn so the
    # child has somewhere to write immediately. Opening the log writer
    # creates the file; the child's stdout-fd will append to it.
    log_path = _log_path(directory)
    log_writer = EventLogWriter(log_path)

    # AC 1 (kaart a450df1a…): the child's stdout is a *file* (durable
    # on disk), not a parent-owned pipe. ``start_new_session=True`` on
    # its own only covers the signal half — a uvicorn crash closes the
    # stdout pipe, the child's next write gets EPIPE/SIGPIPE, and the
    # subprocess dies. Routing stdout through the log file means the
    # child holds a separate fd pointing at the same on-disk file; the
    # parent's pipe dying is irrelevant. Stderr is sent to DEVNULL for
    # the same reason — we lose Claude's diagnostic stderr output
    # (the application's ``logger.warning`` calls inside the consumer
    # are preserved) but a parent-owned stderr pipe would re-introduce
    # the same EPIPE-on-restart problem.
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=directory,
        stdout=log_writer.fileno(),
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
        start_new_session=True,  # kaart a450df1a… AC 1 (signal half)
    )
    record = HeadlessRunRecord(
        session_name=session_name,
        pid=proc.pid,
        worktree_path=directory,
        log_path=log_path,
        started_at=time.time(),
    )
    # Project root is two parents up from the worktree:
    #   <project>/.claude/worktrees/<session_name>
    # Remembering it makes the run visible to ``live_headless_sessions``
    # immediately (without waiting for the next adoption cycle).
    _remember_project_root(Path(directory).parent.parent.parent)
    _headless_processes[session_name] = record
    _write_pidfile(record)  # durable; survives backend restart
    stop_event = asyncio.Event()
    tailer_task = asyncio.create_task(
        _consume_log_file(
            proc=proc,
            log_path=log_path,
            session_name=session_name,
            provider=provider,
            record=record,
            log_writer=log_writer,
            stop_event=stop_event,
            startup_future=startup_future,
        ),
        name=f"headless-tail-{session_name}",
    )
    try:
        # Race the tailer against proc.wait(). Whichever finishes first
        # determines the next step:
        #  - tailer fails (e.g. _on_event raises) → kill the subprocess,
        #    propagate the exception so the caller handles the failure.
        #  - proc exits (normal or abnormal code) → signal the tailer to
        #    drain, wait for it, return the exit code.
        # Pre-refactor this code did ``await proc.wait()`` first, which
        # blocked forever when the tailer raised on a still-running
        # subprocess (kaart a450df1a… regression test fixture uses a
        # SIGTERM-ignoring child; the runner must not wait 120s for the
        # SIGKILL fallback).
        proc_wait_task = asyncio.create_task(proc.wait())
        try:
            done, pending = await asyncio.wait(
                {proc_wait_task, tailer_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            # Defensive: on cancellation/await escape, both tasks are
            # best-effort cancelled. The ``finally`` below still reaps
            # the subprocess.
            proc_wait_task.cancel()
            tailer_task.cancel()
            raise
        if tailer_task in done:
            # Tailer raised or completed before the subprocess. Whatever
            # the cause, we need to terminate the subprocess — the tailer
            # only self-completes cleanly when the child wrote an EOF, so
            # a tailer-finished state here means the tailer raised.
            for t in pending:
                t.cancel()
            tailer_exc = tailer_task.exception()
            if tailer_exc is not None:
                # Surface the original exception via the finally's kill
                # path. Re-raise below so the caller sees the real cause.
                raise tailer_exc
            # No exception but tailer finished first — pathological
            # (e.g. process group lost), handle in the finally.
        # Subprocess exited (or tailer failed and we cancelled the wait).
        # The wait task is either done or cancelled; grab its returncode.
        if proc_wait_task.done() and not proc_wait_task.cancelled():
            returncode = proc_wait_task.result()
        else:
            returncode = await proc.wait()
        # Subprocess is dead; signal the tailer to stop (it'll do one
        # final pass to drain any in-flight writes the child buffered
        # before exit) and wait for it.
        stop_event.set()
        try:
            await asyncio.wait_for(tailer_task, timeout=2.0)
        except TimeoutError:
            tailer_task.cancel()
            try:
                await tailer_task
            except (asyncio.CancelledError, Exception):
                pass
            logger.warning(
                "headless %s: tailer did not stop within 2s of subprocess exit",
                session_name,
            )
        return {
            "session_name": session_name,
            "transport": "headless",
            "exit_code": returncode,
        }
    finally:
        # AC 1 (kaart d373be64…): every exit path must leave the
        # subprocess gone. ``proc.wait()`` above already returned, so
        # normally the subprocess is reaped — but a path that bypasses
        # the wait (e.g. an exception during spawn) still needs the
        # safety net. Cleanup errors are caught and logged so they
        # don't replace the original exception propagating up.
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
        # Stop the tailer if it's still running (the wait above timed
        # out, or the early-exit path skipped the wait). After this
        # point the child is reaped, so the tailer's pid-based liveness
        # check will return False on the next iteration and it'll exit.
        if not tailer_task.done():
            stop_event.set()
            try:
                await asyncio.wait_for(tailer_task, timeout=1.0)
            except (TimeoutError, asyncio.CancelledError, Exception):
                tailer_task.cancel()
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
        cli_id=cli_id,
    )
    return dict(spawn_env.env)


async def _consume_log_file(
    proc: asyncio.subprocess.Process | None,
    log_path: Path,
    session_name: str,
    *,
    provider: str,
    record: HeadlessRunRecord,
    log_writer: EventLogWriter,
    stop_event: asyncio.Event,
    startup_future: asyncio.Future[None] | None = None,
) -> int:
    """Tail the on-disk event log, parse each line, dispatch via ``_on_event``.

    AC 1 + AC 4 (kaart a450df1a…): the child's stdout is the on-disk log
    file (not a parent-owned pipe), so the child survives a backend
    exit. This function is the read side of that contract: it tails the
    log file from ``record.last_read_offset`` and dispatches each line
    as a structured event. The offset advances on every line and is
    persisted to the pidfile so a backend restart can pick up from
    where the previous parent left off instead of re-processing the
    full log (events are idempotent, but skipping the redundant work
    matters when the log has thousands of events).

    The function is used in two contexts:

    - **Fresh run** (``run_headless``): ``proc`` is the
      ``asyncio.subprocess.Process`` for the just-spawned child; the
      task is awaited after ``proc.wait()`` returns. The stop_event
      signals the tailer to drain remaining content and exit.

    - **Adopted run** (``adopt_headless_runs`` → lifespan): ``proc`` is
      ``None`` (we only have a pid + worktree path, not a live Process
      object). The tailer self-terminates when the recorded pid is
      dead (per :func:`_os_pid_alive`) AND the log is fully drained.
      The lifespan keeps a strong reference to the task so it isn't
      GC'd.

    Liveness is always checked via ``_os_pid_alive(record.pid,
    record.worktree_path)`` — the same predicate the reaper uses —
    so the tailer agrees with the reaper on what "alive" means, and
    a process that has been killed via the cleanup path is detected
    the same way a process that died of its own accord is.

    Cap handling: when the log exceeds ``log_writer.cap_bytes`` on the
    read side, the tailer triggers :meth:`EventLogWriter.truncate_head`
    and resets its offset to the new file size (the truncated bytes
    are the oldest content, which the tailer has already read and
    dispatched — events are idempotent so no re-processing is needed).
    """
    offset = record.last_read_offset
    with open(log_path, "rb") as f:
        while True:
            # If the parent signalled a stop, exit. The fresh-run path
            # signals after ``proc.wait()`` so the tailer still does a
            # final drain pass; we check liveness + drain below.
            if stop_event.is_set():
                # One last drain pass so any in-flight writes the child
                # buffered before exit land before we leave.
                f.seek(0, 2)
                size = f.tell()
                if size < offset:
                    offset = 0
                f.seek(offset)
                line = f.readline()
                while line:
                    offset = f.tell()
                    text = line.decode("utf-8", errors="replace").strip()
                    if text:
                        await _dispatch_log_line(
                            text, session_name, provider, startup_future=startup_future,
                        )
                    line = f.readline()
                record.last_read_offset = offset
                _write_pidfile(record)
                break

            # OS-level liveness check — same predicate the reaper uses.
            proc_alive = _os_pid_alive(record.pid, record.worktree_path)

            # Read new lines from the log file.
            f.seek(0, 2)
            size = f.tell()
            if size < offset:
                # File shrank (external truncation, e.g. someone ran
                # ``truncate`` on it). Reset to start; events are
                # idempotent so re-reading is fine.
                offset = 0
            f.seek(offset)
            line = f.readline()
            while line:
                offset = f.tell()
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    await _dispatch_log_line(
                        text, session_name, provider, startup_future=startup_future,
                    )
                line = f.readline()

            # Persist the new offset so a crash here doesn't lose
            # progress — the next adopt reads ``last_read_offset``
            # and seeks to it.
            if offset != record.last_read_offset:
                record.last_read_offset = offset
                _write_pidfile(record)

            # Cap enforcement on the read side: the child writes to
            # the log via its own fd, so we can't append-truncate from
            # here without racing the child. Instead, when the file
            # size crosses the cap, the tailer triggers
            # :meth:`EventLogWriter.truncate_head` and resets its
            # offset to the new EOF.
            if size > log_writer.cap_bytes:
                log_writer.truncate_head()
                f.seek(0, 2)
                new_size = f.tell()
                # Reset offset to the new EOF. We've already read
                # everything up to ``offset``; the bytes the
                # truncator dropped were the oldest ones we'd
                # already dispatched.
                offset = new_size
                record.last_read_offset = offset
                _write_pidfile(record)

            # Done condition: subprocess is dead AND we've consumed
            # everything in the log. ``proc_alive`` is the same
            # predicate ``live_headless_sessions`` uses, so the
            # tailer's view agrees with the reaper's.
            if not proc_alive and offset >= size:
                break

            await asyncio.sleep(0.05)
    return proc.returncode if proc is not None else 0


async def _dispatch_log_line(
    text: str,
    session_name: str,
    provider: str,
    *,
    startup_future: asyncio.Future[None] | None = None,
) -> None:
    """Parse one log-file line and dispatch the resulting event.

    Async so the tailer awaits ``_on_event`` per line — keeps the
    ordering the old ``_consume_stream`` had (read line, await
    dispatch, read next line). A rate-limit event that takes a moment
    to land in the DB pauses the *next* dispatch correctly, instead
    of racing with it.

    Tolerates non-JSON lines and unparseable / unmapped payloads by
    logging and skipping — same shape as the parse-error tolerance
    the old ``_consume_stream`` had, so a single malformed event
    never kills the run.
    """
    if not text:
        return
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(
            "headless %s: dropping non-JSON line: %r",
            session_name, text[:200],
        )
        return
    is_init = payload.get("type") == "system" and payload.get("subtype") == "init"
    if is_init:
        config_error = _mcp_server_config_error(payload)
        if config_error is not None:
            raise config_error
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
        return
    if is_init and startup_future is not None and not startup_future.done():
        startup_future.set_result(None)
    await _on_event(structured, session_name=session_name, provider=provider)


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