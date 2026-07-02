"""Inject text into a tmux pane via send-keys (model A delivery)."""
import asyncio
import logging
import subprocess

logger = logging.getLogger(__name__)

# Box-drawing chars claude's TUI draws once its input frame is rendered. A
# blank/booting pane (or a bare shell) has none of these, so their presence is
# a reliable "claude is up and ready for input" signal across CC versions.
_READY_MARKERS = ("─", "╭", "╰")  # ─ ╭ ╰


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
    """Poll until a freshly-spawned claude TUI is ready to accept input.

    Returns True once the pane renders claude's input frame (plus a short
    settle so the first keystroke isn't dropped mid-render), or False on
    timeout. Async so the scheduler event loop is never blocked while waiting.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
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
