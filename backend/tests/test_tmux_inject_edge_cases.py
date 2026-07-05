"""Edge cases for tmux message injection (scheduled-message delivery, model A):
tmux missing, send-keys timing out, the Enter step failing, and the readiness
poll giving up. Delivery must report failure rather than raise into the scheduler."""
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.services.scheduling import tmux_inject
from app.services.scheduling.tmux_inject import _capture_pane, send_text, wait_for_pane_ready


def test_send_text_returns_false_when_tmux_missing():
    with patch.object(tmux_inject.subprocess, "run", side_effect=FileNotFoundError()):
        assert send_text("sess:0.0", "hi") is False


def test_send_text_returns_false_on_timeout():
    with patch.object(tmux_inject.subprocess, "run",
                      side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=10)):
        assert send_text("sess:0.0", "hi") is False


def test_send_text_returns_false_when_enter_step_fails():
    # First send-keys (literal text) succeeds, the follow-up Enter fails: the
    # message would be typed but never submitted, so this must report failure.
    literal_ok = MagicMock(returncode=0, stderr="")
    enter_fail = MagicMock(returncode=1, stderr="no such pane")
    with patch.object(tmux_inject.subprocess, "run",
                      side_effect=[literal_ok, enter_fail]) as run:
        assert send_text("sess:0.0", "hi") is False
    assert run.call_count == 2


def test_capture_pane_returns_none_on_failure():
    result = MagicMock(returncode=1, stdout="", stderr="no pane")
    with patch.object(tmux_inject.subprocess, "run", return_value=result):
        assert _capture_pane("missing:0.0") is None


@pytest.mark.asyncio
async def test_wait_for_pane_ready_times_out_when_never_ready():
    # A pane that never renders claude's input frame must make the poll give up
    # and return False instead of hanging forever.
    with patch.object(tmux_inject, "_capture_pane", return_value="booting..."):
        ready = await wait_for_pane_ready("sess:0.0", timeout_s=0.2, poll_s=0.05)
    assert ready is False


@pytest.mark.asyncio
async def test_wait_for_pane_ready_true_when_frame_rendered():
    with patch.object(tmux_inject, "_capture_pane", return_value="╭─ prompt ─╮"):
        ready = await wait_for_pane_ready("sess:0.0", timeout_s=1.0,
                                          poll_s=0.05, settle_s=0.0)
    assert ready is True
