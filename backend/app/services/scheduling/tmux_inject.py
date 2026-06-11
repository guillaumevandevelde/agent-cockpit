"""Inject text into a tmux pane via send-keys (model A delivery)."""
import logging
import subprocess

logger = logging.getLogger(__name__)


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
