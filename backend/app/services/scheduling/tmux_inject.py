"""Inject text into a tmux pane via send-keys (model A delivery)."""
import asyncio
import logging
import subprocess

logger = logging.getLogger(__name__)

# Box-drawing chars claude's TUI draws once its input frame is rendered. A
# blank/booting pane (or a bare shell) has none of these, so their presence is
# a reliable "claude is up and ready for input" signal across CC versions.
_READY_MARKERS = ("─", "╭", "╰")  # ─ ╭ ╰


def _session_name_from_target(tmux_target: str) -> str:
    """Extract the tmux session name from a target like 'sess:0.0' -> 'sess'.

    Used to look up the structured SessionStart signal from
    ``session_signals`` — which is keyed by tmux session name, not by
    pane-id-bearing target — without forcing callers to reformat their
    ``spawn_for`` / ``resolve_target`` output. Empty string when the target
    doesn't carry a session name (defensive: callers shouldn't pass those,
    but we don't want a stray ``:0.0`` to crash the lookup).
    """
    if not tmux_target:
        return ""
    return tmux_target.split(":", 1)[0]


def _capture_pane(tmux_target: str) -> str | None:
    """Return the visible text of a pane, or None if capture failed."""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", tmux_target],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("capture-pane error for %s: %s", tmux_target, e)
        return None
    if r.returncode != 0:
        return None
    return r.stdout


async def wait_for_pane_ready(tmux_target: str, timeout_s: float = 30.0,
                              poll_s: float = 0.5, settle_s: float = 1.0) -> bool:
    """Wait until a freshly-spawned claude TUI is ready to accept input.

    Prefers the typed ``SessionStart`` hook signal when one is available
    (recorded by the hook endpoint before any pane render happens, so the
    poll loop short-circuits the moment CC has emitted its start hook
    rather than racing the next ``tmux capture-pane`` tick). Falls back to
    the box-drawing-char pane scan for sessions whose ``claude`` process
    hasn't yet emitted any hook — the classic first-spawn case where the
    hook pipeline needs CC itself to come up before it can record anything.

    Returns True once the pane renders claude's input frame (plus a short
    settle so the first keystroke isn't dropped mid-render), or False on
    timeout. Async so the scheduler event loop is never blocked while
    waiting. The settle is applied to both code paths so callers can rely
    on a uniform "input frame is up AND stable" guarantee regardless of
    whether the signal came from a hook or a pane poll.
    """
    from app.services.scheduling.session_signals import session_signals

    session_name = _session_name_from_target(tmux_target)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s

    # Fast path: SessionStart already recorded. Cheap check, no tmux roundtrip.
    if session_name and session_signals.is_started(session_name):
        await asyncio.sleep(settle_s)
        return True

    while loop.time() < deadline:
        # Poll the structured signal too — covers the case where the SessionStart
        # arrives *during* the poll loop (typical: spawn took 50ms, SessionStart
        # fires at 80ms, poll cadence is 500ms). One cheap dict lookup per tick.
        if session_name and session_signals.is_started(session_name):
            await asyncio.sleep(settle_s)
            return True
        out = await asyncio.to_thread(_capture_pane, tmux_target)
        if out and any(m in out for m in _READY_MARKERS):
            await asyncio.sleep(settle_s)
            return True
        await asyncio.sleep(poll_s)
    logger.warning("pane %s never became ready within %.0fs", tmux_target, timeout_s)
    return False


def send_text(tmux_target: str, text: str) -> bool:
    """Type `text` into the tmux pane and press Enter. Returns True on success.

    Uses `-l` (literal) so message content is never interpreted as key names.
    """
    try:
        r1 = subprocess.run(
            ["tmux", "send-keys", "-t", tmux_target, "-l", text],
            capture_output=True, text=True, timeout=10,
        )
        if r1.returncode != 0:
            logger.warning("send-keys literal failed for %s: %s", tmux_target, r1.stderr)
            return False
        r2 = subprocess.run(
            ["tmux", "send-keys", "-t", tmux_target, "Enter"],
            capture_output=True, text=True, timeout=10,
        )
        if r2.returncode != 0:
            logger.warning("send-keys Enter failed for %s: %s", tmux_target, r2.stderr)
            return False
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("send-keys error for %s: %s", tmux_target, e)
        return False
