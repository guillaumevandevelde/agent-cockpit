"""Edge cases for CC Bridge tmux discovery: tmux missing, timing out, returning
errors, or emitting malformed output. The bridge must degrade to an empty result
rather than crash the API."""
import subprocess
from unittest.mock import MagicMock, patch

from app.services.runs import discovery


def test_discover_returns_empty_when_tmux_not_installed():
    with patch.object(discovery.subprocess, "run", side_effect=FileNotFoundError()):
        assert discovery.discover_agent_sessions() == []


def test_discover_returns_empty_when_tmux_times_out():
    with patch.object(discovery.subprocess, "run",
                      side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=10)):
        assert discovery.discover_agent_sessions() == []


def test_discover_returns_empty_when_tmux_exits_nonzero():
    # e.g. "no server running on /tmp/tmux-1000/default"
    result = MagicMock(returncode=1, stderr="no server running", stdout="")
    with patch.object(discovery.subprocess, "run", return_value=result):
        assert discovery.discover_agent_sessions() == []


def test_discover_skips_malformed_pane_lines():
    # A line that does not split into the expected 7 fields must be ignored,
    # not crash the parser.
    result = MagicMock(returncode=0, stderr="", stdout="too|few|fields\n")
    with patch.object(discovery.subprocess, "run", return_value=result):
        assert discovery.discover_agent_sessions() == []


def test_capture_pane_preview_returns_empty_when_pane_not_found():
    result = MagicMock(returncode=1, stdout="", stderr="can't find pane")
    with patch.object(discovery.subprocess, "run", return_value=result):
        assert discovery.capture_pane_preview("missing:0.0") == ""


def test_capture_pane_preview_returns_empty_on_subprocess_error():
    with patch.object(discovery.subprocess, "run",
                      side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=5)):
        assert discovery.capture_pane_preview("slow:0.0") == ""
