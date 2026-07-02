"""Tests for session cleanup when cards complete."""
import asyncio
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from app.kanban.session_cleanup import (
    _extract_session_name,
    _get_project_path,
    _kill_tmux_session,
    _remove_worktree_at,
    cleanup_session_for_card,
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

    def test_handles_missing_tmux(self):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            assert _kill_tmux_session("k-test-1234") is False


class TestGetProjectPath:
    @pytest.mark.asyncio
    async def test_returns_matching_path(self):
        with patch("app.database.AsyncSessionLocal") as mock_sl, \
             patch("app.kanban.project_key.resolve_project_key",
                   return_value="git:example.com/me/repo"):
            mock_session = AsyncMock()
            mock_execute = MagicMock()
            mock_execute.scalars.return_value.all.return_value = ["/home/me/repo"]
            mock_session.execute = AsyncMock(return_value=mock_execute)
            mock_sl.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sl.return_value.__aexit__ = AsyncMock(return_value=None)

            path = await _get_project_path("git:example.com/me/repo")
            assert path == "/home/me/repo"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_match(self):
        with patch("app.database.AsyncSessionLocal") as mock_sl, \
             patch("app.kanban.project_key.resolve_project_key",
                   return_value="git:other.com/repo"):
            mock_session = AsyncMock()
            mock_execute = MagicMock()
            mock_execute.scalars.return_value.all.return_value = ["/home/me/repo"]
            mock_session.execute = AsyncMock(return_value=mock_execute)
            mock_sl.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sl.return_value.__aexit__ = AsyncMock(return_value=None)

            path = await _get_project_path("git:example.com/me/repo")
            assert path is None

    @pytest.mark.asyncio
    async def test_returns_none_on_db_error(self):
        with patch("app.database.AsyncSessionLocal") as mock_sl:
            mock_sl.return_value.__aenter__ = AsyncMock(side_effect=Exception("db error"))
            mock_sl.return_value.__aexit__ = AsyncMock(return_value=None)

            path = await _get_project_path("git:example.com/me/repo")
            assert path is None

    @pytest.mark.asyncio
    async def test_skips_paths_where_key_lookup_fails(self):
        with patch("app.database.AsyncSessionLocal") as mock_sl, \
             patch("app.kanban.project_key.resolve_project_key",
                   side_effect=[Exception("not a git repo"), "git:example.com/me/repo"]):
            mock_session = AsyncMock()
            mock_execute = MagicMock()
            mock_execute.scalars.return_value.all.return_value = ["/bad/path", "/home/me/repo"]
            mock_session.execute = AsyncMock(return_value=mock_execute)
            mock_sl.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sl.return_value.__aexit__ = AsyncMock(return_value=None)

            path = await _get_project_path("git:example.com/me/repo")
            assert path == "/home/me/repo"


class TestRemoveWorktreeAt:
    def test_removes_existing_worktree(self, tmp_path):
        worktree = tmp_path / ".claude" / "worktrees" / "k-test-1234"
        worktree.mkdir(parents=True)

        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = _remove_worktree_at("k-test-1234", str(tmp_path))

        assert result is True
        args = mock_run.call_args[0][0]
        assert args[0] == "git"
        assert "worktree" in args
        assert "remove" in args
        assert "--force" in args
        assert str(worktree) in args

    def test_returns_true_when_worktree_not_found(self, tmp_path):
        assert _remove_worktree_at("k-nonexistent", str(tmp_path)) is True

    def test_returns_false_on_git_failure(self, tmp_path):
        worktree = tmp_path / ".claude" / "worktrees" / "k-test-1234"
        worktree.mkdir(parents=True)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "not a worktree"
        with patch("subprocess.run", return_value=mock_result):
            assert _remove_worktree_at("k-test-1234", str(tmp_path)) is False

    def test_returns_false_on_exception(self, tmp_path):
        worktree = tmp_path / ".claude" / "worktrees" / "k-test-1234"
        worktree.mkdir(parents=True)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 30)):
            assert _remove_worktree_at("k-test-1234", str(tmp_path)) is False


class TestCleanupSessionForCard:
    @pytest.mark.asyncio
    async def test_cleans_up_agent_session(self):
        mock_card = MagicMock()
        mock_card.claimed_by = "agent:k-test-1234"

        with patch("app.kanban.db.KanbanSessionLocal") as mock_ksl, \
             patch("app.kanban.service.get_card",
                   new=AsyncMock(return_value=mock_card)), \
             patch("app.kanban.session_cleanup._cancel_sandcastle_run",
                   new=AsyncMock(return_value=False)), \
             patch("app.kanban.session_cleanup._kill_tmux_session",
                   return_value=True) as mock_kill, \
             patch("app.kanban.session_cleanup._get_project_path",
                   new=AsyncMock(return_value="/home/me/repo")), \
             patch("app.kanban.session_cleanup._remove_worktree_at",
                   return_value=True) as mock_rm:
            mock_session = AsyncMock()
            mock_ksl.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ksl.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await cleanup_session_for_card("card-123", "git:example.com/me/repo")

        assert result["cleaned"] is True
        assert result["session_name"] == "k-test-1234"
        assert result["error"] is None
        mock_kill.assert_called_once_with("k-test-1234")
        mock_rm.assert_called_once_with("k-test-1234", "/home/me/repo")

    @pytest.mark.asyncio
    async def test_returns_error_when_card_not_found(self):
        with patch("app.kanban.db.KanbanSessionLocal") as mock_ksl, \
             patch("app.kanban.service.get_card",
                   new=AsyncMock(return_value=None)):
            mock_session = AsyncMock()
            mock_ksl.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ksl.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await cleanup_session_for_card("card-missing", "git:example.com/me/repo")

        assert result["cleaned"] is False
        assert result["error"] == "card_not_found"

    @pytest.mark.asyncio
    async def test_returns_error_when_no_agent_session(self):
        mock_card = MagicMock()
        mock_card.claimed_by = "me@ui"

        with patch("app.kanban.db.KanbanSessionLocal") as mock_ksl, \
             patch("app.kanban.service.get_card",
                   new=AsyncMock(return_value=mock_card)):
            mock_session = AsyncMock()
            mock_ksl.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ksl.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await cleanup_session_for_card("card-123", "git:example.com/me/repo")

        assert result["cleaned"] is False
        assert result["error"] == "no_agent_session"

    @pytest.mark.asyncio
    async def test_continues_when_kill_fails(self):
        """When tmux is already dead, cleanup should still succeed and
        remove the worktree (the agent may have exited naturally)."""
        mock_card = MagicMock()
        mock_card.claimed_by = "agent:k-test-1234"

        with patch("app.kanban.db.KanbanSessionLocal") as mock_ksl, \
             patch("app.kanban.service.get_card",
                   new=AsyncMock(return_value=mock_card)), \
             patch("app.kanban.session_cleanup._cancel_sandcastle_run",
                   new=AsyncMock(return_value=False)), \
             patch("app.kanban.session_cleanup._kill_tmux_session", return_value=False), \
             patch("app.kanban.session_cleanup._get_project_path",
                   new=AsyncMock(return_value="/tmp/repo")), \
             patch("app.kanban.session_cleanup._remove_worktree_at"):
            mock_session = AsyncMock()
            mock_ksl.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ksl.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await cleanup_session_for_card("card-123", "git:example.com/me/repo")

        assert result["cleaned"] is True
        assert result["tmux_killed"] is False

    @pytest.mark.asyncio
    async def test_skips_worktree_removal_when_no_project_path(self):
        mock_card = MagicMock()
        mock_card.claimed_by = "agent:k-test-1234"

        with patch("app.kanban.db.KanbanSessionLocal") as mock_ksl, \
             patch("app.kanban.service.get_card",
                   new=AsyncMock(return_value=mock_card)), \
             patch("app.kanban.session_cleanup._cancel_sandcastle_run",
                   new=AsyncMock(return_value=False)), \
             patch("app.kanban.session_cleanup._kill_tmux_session", return_value=True), \
             patch("app.kanban.session_cleanup._get_project_path",
                   new=AsyncMock(return_value=None)), \
             patch("app.kanban.session_cleanup._remove_worktree_at") as mock_rm:
            mock_session = AsyncMock()
            mock_ksl.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ksl.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await cleanup_session_for_card("card-123", "git:unknown/repo")

        assert result["cleaned"] is True
        mock_rm.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancels_sandcastle_run_and_skips_tmux(self):
        """Sandcastle sessions have no tmux/worktree: cancelling the run is the
        whole cleanup, so the tmux/worktree path must be skipped."""
        mock_card = MagicMock()
        mock_card.claimed_by = "agent:k-test-1234"

        with patch("app.kanban.db.KanbanSessionLocal") as mock_ksl, \
             patch("app.kanban.service.get_card",
                   new=AsyncMock(return_value=mock_card)), \
             patch("app.kanban.session_cleanup._cancel_sandcastle_run",
                   new=AsyncMock(return_value=True)), \
             patch("app.kanban.session_cleanup._release_claim",
                   new=AsyncMock(return_value=None)), \
             patch("app.kanban.session_cleanup._kill_tmux_session") as mock_kill, \
             patch("app.kanban.session_cleanup._remove_worktree_at") as mock_rm:
            mock_session = AsyncMock()
            mock_ksl.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ksl.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await cleanup_session_for_card("card-123", "git:example.com/me/repo")

        assert result["cleaned"] is True
        mock_kill.assert_not_called()
        mock_rm.assert_not_called()


class TestOnCardMovedToDone:
    def test_schedules_task_in_running_loop(self):
        mock_loop = MagicMock()
        with patch("asyncio.get_running_loop", return_value=mock_loop):
            on_card_moved_to_done("card-123", "project-abc")

        mock_loop.create_task.assert_called_once()

    def test_runs_directly_when_no_loop(self):
        with patch("asyncio.get_running_loop",
                   side_effect=RuntimeError("no running event loop")), \
             patch("asyncio.run") as mock_run:
            on_card_moved_to_done("card-123", "project-abc")

        mock_run.assert_called_once()
