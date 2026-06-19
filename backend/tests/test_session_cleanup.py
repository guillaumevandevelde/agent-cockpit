"""Tests for session cleanup when cards complete."""
from unittest.mock import patch, MagicMock, AsyncMock
import subprocess

from app.kanban.session_cleanup import (
    _extract_session_name,
    _kill_tmux_session,
    on_card_moved_to_done,
)


class TestExtractSessionName:
    def test_extracts_from_agent_prefix(self):
        assert _extract_session_name("agent:k-test-1234") == "k-test-1234"

    def test_returns_none_for_no_prefix(self):
        assert _extract_session_name("me@ui") is None

    def test_returns_none_for_none(self):
        assert _extract_session_name(None) is None

    def test_returns_none_for_empty(self):
        assert _extract_session_name("") is None


class TestKillTmuxSession:
    def test_kills_session_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result):
            assert _kill_tmux_session("k-test-1234") is True

    def test_kills_session_failure(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "session not found"
        with patch("subprocess.run", return_value=mock_result):
            assert _kill_tmux_session("k-test-1234") is False

    def test_handles_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 10)):
            assert _kill_tmux_session("k-test-1234") is False


class TestOnCardMovedToDone:
    @patch("app.kanban.session_cleanup.cleanup_session_for_card", new_callable=AsyncMock)
    @patch("asyncio.get_event_loop")
    def test_schedules_cleanup(self, mock_get_loop, mock_cleanup):
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False
        mock_get_loop.return_value = mock_loop
        
        on_card_moved_to_done("card-123", "project-abc")
        
        # Should have called run_until_complete
        mock_loop.run_until_complete.assert_called_once()
