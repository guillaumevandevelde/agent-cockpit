"""Per-tmux-session structured signals derived from CC hook events.

For a long time, dispatch observability leaned on terminal-scraping helpers
(``_is_rate_limited_session`` in the reaper, ``wait_for_pane_ready`` in
delivery). They worked, but they were approximations of typed events we already
get over the hook channel and they were reactive only (the reaper poll, the
ready-poll). This module records those typed events *as they arrive* and exposes
fast, non-poll lookups so the reaper and the delivery engine can prefer the
typed signal when one is available and fall back to the pane scrape only when
the hook pipeline hasn't caught up yet — or never will (the classic
rate-limited-on-first-spawn case where the ``claude`` process dies before
initialising its hook scripts).

Sister module to ``SessionRegistry`` (which keys by Claude ``session_id`` and
tracks idle flag + pane mapping). ``SessionSignalRegistry`` keys by the
**tmux session name** the dispatcher assigned, because that's what the reaper
hands us when it scans ``live_sessions`` and what the delivery engine derives
from the spawn target.

Both record the same thing (a Notification classified as "limit", a SessionStart
fired) but in different forms and with different consumers. ``SessionRegistry``
is the source of truth for "is this tmux pane currently idle"; this one is the
source of truth for "did this tmux session ever emit a typed limit notification".
The split keeps each one focused and avoids growing either registry into a
god-module.
"""
import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def session_name_for_dispatched_cwd(cwd: str | None) -> str | None:
    """Derive the tmux session name of a dispatched session from ``cwd``.

    Kanban-dispatched agents run in
    ``<project_path>/.claude/worktrees/<session_name>``, so the parent dir must
    be ``worktrees`` and *its* parent must be ``.claude``. Other cwd shapes
    (the project root, a manually started `claude` session, sandcastle
    container paths) have no session name we recognise and return ``None``.

    Mirrors ``SessionRegistry._session_name_for_dispatched_cwd``; duplicated
    here so this module can be imported without dragging in the (larger)
    session-registry dep graph.
    """
    if not cwd:
        return None
    worktree = Path(cwd)
    if worktree.parent.name != "worktrees" or worktree.parent.parent.name != ".claude":
        return None
    return worktree.name


class SessionSignalRegistry:
    """Per-tmux-session typed signals recorded by the hook endpoint.

    All hooks are pipelined through here on arrival (besides their existing
    presence/registry updates). The reaper and the delivery engine ask
    structured questions first and only fall back to pane scraping when no
    structured signal is recorded yet (or, for sessions that never initialised
    hooks, never will be).

    Thread-/asyncio-safety: ``record_*`` is called from the FastAPI request
    handler; the reaper/delivery callers await ``wait_until_*`` in the same
    event loop. ``asyncio.Event`` synchronisation is fine here — there's no
    threading involved.
    """

    def __init__(self) -> None:
        # session_name -> (Notification message, raw message capture). The
        # message is kept so a future audit log (replacement for the current
        # pane-snippet dump in `_cleanup_stuck_session`) can surface the
        # canonical CC output without re-deriving it.
        self._limits: dict[str, str] = {}
        self._started: set[str] = set()
        self._started_waiters: dict[str, list[asyncio.Event]] = {}

    def record_started(self, cwd: str | None) -> str | None:
        """Mark the session running in ``cwd`` as having fired its SessionStart hook.

        Returns the session name when the cwd matched a dispatched worktree
        (so callers can log it), ``None`` otherwise. Always idempotent for the
        same name — repeated SessionStart hooks aren't an error (CC fires them
        on every prompt turn), we just want the timestamp-free "we've seen
        *any* SessionStart" fact.
        """
        name = session_name_for_dispatched_cwd(cwd)
        if name is None:
            return None
        if name in self._started:
            return name
        self._started.add(name)
        # Wake every waiter currently waiting on this session — fast-path the
        # "is the pane already ready?" check that wait_for_pane_repeatedly does
        # otherwise have to wait for the next poll tick.
        for ev in self._started_waiters.pop(name, []):
            ev.set()
        logger.debug("session_signals: marked %s as started", name)
        return name

    def record_limit(self, cwd: str | None, message: str = "") -> str | None:
        """Mark the session running in ``cwd`` as having hit a rate limit.

        Called from the hook endpoint after ``classify_notification`` returns
        ``"limit"`` — when the Notification event is a free-text 429/limit
        message. Different rate-limit-notification variants (Claude's
        "session limit … resets 11pm (Europe/Brussels)", Minimax's "Token Plan"
        message, the bare "API Error: 429" string) all flow through the same
        call so the reaper's detection stays single-sourced.
        """
        name = session_name_for_dispatched_cwd(cwd)
        if name is None:
            return None
        # First-write-wins: the canonical Notification may have arrived twice
        # (Notification hook + the new background-agent re-emit), and the
        # second time we want the same message we already captured — picking
        # the later one would just churn logging for no signal change.
        if name not in self._limits:
            self._limits[name] = message
            logger.debug(
                "session_signals: marked %s as rate-limited (msg=%r)",
                name, message[:200],
            )
        return name

    def is_started(self, session_name: str) -> bool:
        """True iff we've recorded a SessionStart for ``session_name``."""
        return session_name in self._started

    def is_rate_limited(self, session_name: str) -> bool:
        """True iff we've recorded a Notification(kind=limit) for ``session_name``."""
        return session_name in self._limits

    def limit_message(self, session_name: str) -> str | None:
        """Return the canonical rate-limit Notification message, when recorded.

        Useful for the reaper's "what's the operator looking at?" comment —
        replaces the pane-snippet fallback with the canonical CC output that
        classified as a limit. ``None`` when no signal was recorded.
        """
        return self._limits.get(session_name)

    def is_limit_message_processed(self, session_name: str, message: str) -> bool:
        """True iff we've already recorded a Notification(limit) with the
        *same* ``message`` text for ``session_name``.

        Used by ``handle_rate_limit_signal`` as the idempotency gate for the
        transcript-tail sweep: a session that's hit its limit writes nothing
        new to its transcript, so the same message stays at the tail
        indefinitely. Without this check every dispatch tick (≈10 s) would
        re-arm the per-provider pause — for unparseable messages the
        ``now + FALLBACK_PAUSE_HOURS`` fallback slides the deadline forward
        by 10 s per tick (kaart e279a52b, MiniMax Token Plan: 8u36m
        onafgebroken her-armeren); for parseable ones ``parse_reset_time``'s
        "past → +1 day" rollover pushes the deadline +24 u at the exact
        moment the limit was supposed to lift (gemeten op sessie
        k-update-readme-e85e om 2026-07-28T03:20:04Z — vier seconden ná de
        reset van 05:20 +02:00).
        """
        stored = self._limits.get(session_name)
        if stored is None:
            return False
        return stored == message

    def clear_limit(self, session_name: str) -> None:
        """Forget only the rate-limit signal for ``session_name``.

        Distinct from ``clear()``, which drops *every* signal (started +
        limit) for that session — that's the right move when a tmux pane is
        killed and the name may be reused, but too aggressive for the
        transcript-tail sweep's "the session recovered on its own" path,
        which must keep ``started`` set but reset ``limits`` so the next
        *genuine* limit (different message text) can be recorded fresh
        under the same session name. Without this carve-out
        ``record_limit``'s first-write-wins semantics would silently swallow
        the new message — the dedupe gate would then keep matching the old
        one forever.
        """
        self._limits.pop(session_name, None)

    async def wait_until_started(
        self, session_name: str, timeout_s: float,
    ) -> bool:
        """Wait for a SessionStart hook to be recorded for ``session_name``.

        Returns True if the signal arrives (or was already recorded) within
        ``timeout_s``; False on timeout. Designed to be cancelled cleanly so a
        delivery timeout in the calling task propagates.
        """
        if session_name and session_name in self._started:
            return True
        if not session_name or timeout_s <= 0:
            return False
        ev = asyncio.Event()
        self._started_waiters.setdefault(session_name, []).append(ev)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout_s)
            return True
        except TimeoutError:
            return False
        finally:
            try:
                self._started_waiters[session_name].remove(ev)
            except (KeyError, ValueError):
                pass

    def clear(self, session_name: str) -> None:
        """Forget every signal recorded for ``session_name``.

        Called from the dispatch kill path (and from ``mark_spawned`` resets)
        so a re-spawn under the same name doesn't inherit a "limit" flag from
        the previous occupant. Also wakes any pending ``wait_until_started``
        waiters with a False-ish state — but since ``clear`` is paired with
        killing the tmux pane, those waiters will see the tmux pane disappear
        from `live_sessions` on the next tick anyway.
        """
        self._started.discard(session_name)
        self._limits.pop(session_name, None)
        # Drain waiters; the spawn-side contract "if I clear, the spawn just
        # died so don't bother waiting" is implicit — drain so we don't leak
        # asyncio.Event handles.
        for ev in self._started_waiters.pop(session_name, []):
            ev.set()


# Module-level singleton (shared by hook endpoint + reaper + delivery engine).
# Tests that need isolation construct their own SessionSignalRegistry() instead
# of monkey-patching this one — that keeps cross-test leakage impossible.
session_signals = SessionSignalRegistry()
