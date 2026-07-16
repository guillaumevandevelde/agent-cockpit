"""In-memory per-session pane map + idle state, fed by CC hook events.

Keyed by Claude session_id (not cwd), so concurrent sessions in one working
copy are tracked independently. Idle == a Stop with no later busy event.

Enforces hardware-aware session limits via memory_monitor.

Also tracks spawn-time gaps (`mark_spawned` / `get_stuck_sessions`) so the
dispatch reaper can spot tmux sessions whose `claude` process died immediately
after spawn (e.g. a 429 Token Plan limit) before it ever sent a hook event.
That gap is undetectable from `record()` alone, because no hook means no
`record()` call. Stale tracking is cleared on the kill path.
"""
import asyncio
import logging
import subprocess
import time
from pathlib import Path

from app.services.memory_monitor import get_memory_status_cached

logger = logging.getLogger(__name__)

_IDLE_EVENTS = {"Stop"}
_BUSY_EVENTS = {"UserPromptSubmit", "SessionStart", "Notification"}

# Minimum seconds between successive ``tmux list-panes`` reconciliations from
# ``_maybe_reconcile``. The lazy reconcile runs on every ``can_add_session``
# call, which itself fires per hook event — without a throttle a busy backend
# would shell out to tmux dozens of times per second. The value is exposed at
# module scope so tests can monkeypatch it down.
_RECONCILE_INTERVAL_S = 5.0


class SessionRegistry:
    def __init__(self, max_sessions: int | None = None) -> None:
        self._panes: dict[str, str] = {}
        self._idle: dict[str, bool] = {}
        self._waiters: dict[str, list[asyncio.Event]] = {}
        self._external: set[str] = set()  # non-tmux sessions (e.g. sandcastle runs)
        self._max_sessions_override = max_sessions
        # spawn-name -> monotonic spawn time. Set by the dispatch transports
        # the moment a tmux session is created; cleared on kill.
        self._spawn_times: dict[str, float] = {}
        # Names whose cwd matched one of our dispatched sessions on a recent
        # hook event. Once non-empty for a name, the session is no longer
        # considered "stuck" — its `claude` process is alive enough to call home.
        self._spawn_received_hooks: set[str] = set()
        # Monotonic timestamp of the last ``_panes`` reconciliation. ``None``
        # means we have never queried tmux yet — the next non-empty reconcile
        # always shells out so stale entries left over from a previous
        # backend run get cleared on the first ``can_add_session`` after
        # restart.
        self._last_reconcile_at: float | None = None

    @property
    def effective_max_sessions(self) -> int:
        """Get the effective max sessions, considering override and hardware limits."""
        if self._max_sessions_override is not None:
            return self._max_sessions_override
        status = get_memory_status_cached()
        return status.estimated_max_sessions

    @property
    def session_count(self) -> int:
        """Number of tracked sessions, tmux panes plus external reservations.

        Pure read — does NOT call out to tmux. Stale ``_panes`` entries only
        disappear once a reconciliation has run (lazily via ``can_add_session``
        or explicitly via ``cleanup_stale_sessions``); this property is the
        honest count of whatever is currently in the dicts.
        """
        return len(self._panes) + len(self._external)

    def can_add_session(self) -> bool:
        """Check if we can track another session without exceeding limits.

        Self-healing: reconciles ``_panes`` against tmux before the check, so
        a crashed/killed/rebooted pane no longer blocks a new spawn. The
        reconciliation is throttled (see ``_RECONCILE_INTERVAL_S``) so this
        method — which is on the hook-event hot path — does not shell out to
        tmux on every event.
        """
        self._maybe_reconcile()
        return self.session_count < self.effective_max_sessions

    def reserve_external(self, key: str) -> None:
        """Reserve a slot for a non-tmux session (e.g. a sandcastle container run).

        Sandcastle agents run in containers, not tmux, so the hook-fed pane map never
        sees them — yet they consume memory just the same. Reserving here makes them
        count against the shared budget so `can_add_session()` is honest. Idempotent."""
        self._external.add(key)

    def release_external(self, key: str) -> None:
        """Release a previously reserved external slot. No-op for unknown keys."""
        self._external.discard(key)

    def record(self, event: str, session_id: str, cwd: str,
               tmux_pane: str | None = None) -> bool:
        """Record a session event.

        Returns True if the event was recorded, False if rejected due to limits.
        """
        # Check limits for new sessions
        if session_id not in self._panes and not self.can_add_session():
            status = get_memory_status_cached()
            logger.warning(
                f"Session limit reached ({self.session_count}/{self.effective_max_sessions}). "
                f"Memory: {status.usage_percent:.0%} used, "
                f"{status.available_bytes / (1024*1024):.0f}MB available. "
                f"Rejecting session {session_id[:8]}..."
            )
            return False

        if tmux_pane:
            self._panes[session_id] = tmux_pane
        if event in _IDLE_EVENTS:
            self._idle[session_id] = True
            for ev in self._waiters.get(session_id, []):
                ev.set()
        elif event in _BUSY_EVENTS:
            self._idle[session_id] = False
        # Side-effect: when this hook event came from one of our dispatched
        # tmux sessions, clear its stuck-spawn flag. We resolve cwd → session
        # name from the worktree layout (<project>/.claude/worktrees/<name>);
        # any other cwd shape (project root, sandcastle, an arbitrary `claude`
        # session) is not ours and is ignored.
        spawned_name = self._session_name_for_dispatched_cwd(cwd)
        if spawned_name is not None and spawned_name in self._spawn_times:
            self._spawn_received_hooks.add(spawned_name)
        return True

    def mark_spawned(self, session_name: str) -> None:
        """Record that the dispatch transport just spawned `session_name`.

        Used by the reaper (next-tick) to detect stuck sessions: a tmux pane
        that has been alive longer than `get_stuck_sessions`' timeout but has
        not yet sent any hook event. The classic trigger is a 429 Token Plan
        limit on first spawn — the `claude` CLI prints the error but never
        initialises hooks, so `record()` is never called.

        Re-calling with the same name resets the spawn clock (used for
        --resume re-attaches, where the same tmux session is reused).
        """
        self._spawn_times[session_name] = time.monotonic()

    def clear_spawn(self, session_name: str) -> None:
        """Forget a previously-marked spawn. Called from the dispatch kill
        path so the registry doesn't carry zombie tracking once a session
        has been torn down. Idempotent."""
        self._spawn_times.pop(session_name, None)
        self._spawn_received_hooks.discard(session_name)

    def get_stuck_sessions(
        self, live_session_names: set[str], *, timeout_s: int = 120,
    ) -> set[str]:
        """Return names of tmux sessions that are alive but have spawned
        longer ago than `timeout_s` and have not yet sent any hook event.

        Only sessions whose `cwd` shape matches a dispatched worktree are
        considered (the registry only knows about spawned sessions in the
        first place — anything outside its tracking is silently ignored).
        Sessions not in `live_session_names` are also excluded: the reaper
        has a separate dead-session path for those, and we must never tell it
        to send signals to a session that has already disappeared.
        """
        now = time.monotonic()
        return {
            name
            for name, spawned_at in self._spawn_times.items()
            if name in live_session_names
            and now - spawned_at >= timeout_s
            and name not in self._spawn_received_hooks
        }

    @staticmethod
    def _session_name_for_dispatched_cwd(cwd: str) -> str | None:
        """Return the tmux session name of a dispatched session for `cwd`,
        or None if `cwd` is not a dispatched worktree.

        A dispatched agent runs in ``<project_path>/.claude/worktrees/<session_name>``
        so the parent dir must be ``worktrees`` and *its* parent must be ``.claude``.
        Other cwd shapes (the project root, an unrelated `claude` session, a
        sandcastle container path) have no session name we recognise.
        """
        if not cwd:
            return None
        worktree = Path(cwd)
        if worktree.parent.name != "worktrees" or worktree.parent.parent.name != ".claude":
            return None
        return worktree.name

    def cleanup_stale_sessions(self, max_idle_seconds: int = 3600) -> int:
        """Remove ``_panes`` (and ``_idle``) entries whose tmux pane is gone.

        Implements what the old stub promised: a real reconciliation against
        ``tmux list-panes`` so the session count follows reality even when no
        caller explicitly tracked the teardown — a crash, ``kill -9``, a host
        reboot, or a bridge-test spawn that never went through the
        ``clear_spawn`` path. Forces a fresh tmux query (bypasses the
        ``_maybe_reconcile`` throttle) because an explicit caller always wants
        the latest state, not a cached "nothing to do" from a few seconds ago.

        ``max_idle_seconds`` is retained for API backwards compatibility but
        no longer drives the behaviour — the registry no longer trusts
        hook-event timestamps for liveness, only the tmux pane actually
        existing. Returns the number of session_ids removed (0 when nothing
        was stale, or when tmux was unavailable and we conservatively kept
        every entry).
        """
        return self._maybe_reconcile(force=True)

    @staticmethod
    def _live_pane_ids() -> set[str] | None:
        """Set of currently-live tmux pane ids, or ``None`` when tmux can't be
        queried.

        Returning ``None`` (not an empty set) on a tmux hiccup lets the
        caller distinguish "no panes alive" from "we don't know". An empty
        set is the truthful answer for "tmux is fine and no panes match",
        and clears every stale entry on the next reconciliation.

        Bounded by a 5s subprocess timeout so a hung tmux never wedges the
        hook path; covered by ``_RECONCILE_INTERVAL_S`` for callers that
        shell out on every event.
        """
        try:
            result = subprocess.run(
                ["tmux", "list-panes", "-a", "-F", "#{pane_id}"],
                capture_output=True, text=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def _maybe_reconcile(self, *, force: bool = False) -> int:
        """Drop ``_panes`` (and ``_idle``) entries whose tmux pane no longer
        exists.

        Self-healing reconciliation against ``tmux list-panes`` — covers
        crashes, ``kill -9``, reboots, and bridge-test spawns that never went
        through the ``clear_spawn`` path. The mapping remains intact for live
        panes (so ``pane_for()`` keeps feeding the scheduled-messages inject
        pipeline); only stale entries go.

        Throttled: skips the tmux round-trip if less than
        ``_RECONCILE_INTERVAL_S`` has passed since the last call. Pass
        ``force=True`` to bypass the throttle (used by the explicit
        ``cleanup_stale_sessions`` public method).

        Returns the number of session_ids removed. ``0`` covers three
        different cases — empty registry, throttled, tmux unavailable —
        which is fine; the caller does not need to distinguish them.
        """
        # Nothing to reconcile, nothing to throttle against — bail without
        # burning a tmux call on every hook event when no session has ever
        # registered a pane.
        if not self._panes:
            return 0
        now = time.monotonic()
        if (
            not force
            and self._last_reconcile_at is not None
            and now - self._last_reconcile_at < _RECONCILE_INTERVAL_S
        ):
            return 0
        self._last_reconcile_at = now
        live = self._live_pane_ids()
        if live is None:
            # tmux unavailable or errored; be conservative and keep every
            # entry. The next reconciliation (after the throttle or the
            # next spawn) will retry; if tmux is permanently gone, the
            # ``effective_max_sessions`` ceiling will catch the limit too.
            return 0
        stale = [sid for sid, pane in self._panes.items() if pane not in live]
        for sid in stale:
            self._panes.pop(sid, None)
            self._idle.pop(sid, None)
        if stale:
            logger.info(
                "SessionRegistry reconciled against tmux: removed %d stale session(s)",
                len(stale),
            )
        return len(stale)

    def pane_for(self, session_id: str) -> str | None:
        return self._panes.get(session_id)

    def is_idle(self, session_id: str) -> bool:
        return self._idle.get(session_id, False)

    async def wait_until_idle(self, session_id: str, timeout_s: float) -> bool:
        if self._idle.get(session_id, False):
            return True
        ev = asyncio.Event()
        self._waiters.setdefault(session_id, []).append(ev)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout_s)
            return True
        except TimeoutError:
            return False
        finally:
            self._waiters.get(session_id, []).remove(ev)


# Module-level singleton (shared by hook endpoint + delivery engine)
session_registry = SessionRegistry()
