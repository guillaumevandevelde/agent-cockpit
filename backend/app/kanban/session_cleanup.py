"""Session cleanup when kanban cards complete.

When a card moves to "Done", the agent session that worked on it should be
closed. This module provides the cleanup logic and integrates with the
kanban operations pipeline.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DONE_COLUMN = "Done"


def _extract_session_name(claimed_by: str | None) -> str | None:
    """Extract session name from claimant string like 'agent:k-test-1234'."""
    if not claimed_by:
        return None
    prefix = "agent:"
    if claimed_by.startswith(prefix):
        return claimed_by[len(prefix):]
    return None


def _kill_tmux_session(session_name: str) -> bool:
    """Kill a tmux session by name."""
    try:
        result = subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            logger.info("Killed tmux session: %s", session_name)
            return True
        logger.warning("Failed to kill session %s: %s", session_name, result.stderr)
        return False
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("Error killing session %s: %s", session_name, e)
        return False


async def _get_project_path(project_key: str) -> Optional[str]:
    """Look up the local project path for a kanban project key.

    Scans all registered projects and returns the first whose computed key
    matches. Returns None when no match is found or on error.
    """
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.database import Project
    from app.kanban.project_key import resolve_project_key

    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(Project.path))).scalars().all()
        for path in rows:
            try:
                if resolve_project_key(path) == project_key:
                    return path
            except Exception:
                continue
    except Exception as e:
        logger.warning("Could not look up project path for %s: %s", project_key, e)
    return None


def _remove_worktree_at(session_name: str, project_path: str) -> bool:
    """Remove the worktree for a session at a known project path.

    Worktrees live at <project_path>/.claude/worktrees/<session_name>.
    """
    worktree_path = Path(project_path) / ".claude" / "worktrees" / session_name
    if not worktree_path.exists():
        logger.info("Worktree %s not found (already removed)", worktree_path)
        return True
    try:
        result = subprocess.run(
            ["git", "-C", project_path, "worktree", "remove",
             str(worktree_path), "--force"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("Removed worktree: %s", worktree_path)
            return True
        logger.warning("Failed to remove worktree %s: %s", worktree_path, result.stderr)
        return False
    except Exception as e:
        logger.warning("Error removing worktree %s: %s", worktree_path, e)
        return False


async def cleanup_session_for_card(card_id: str, project_key: str) -> dict:
    """Clean up the agent session that worked on a completed card.

    Called when a card moves to "Done". Steps:
    1. Resolve the tmux session name from the card's claimant field.
    2. Kill the tmux session.
    3. Remove the git worktree (looked up via the project registry).

    Returns a dict with cleanup results.
    """
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.service import get_card

    result: dict = {"cleaned": False, "session_name": None, "error": None}

    try:
        async with KanbanSessionLocal() as session:
            card = await get_card(session, card_id)
            if card is None:
                result["error"] = "card_not_found"
                return result

            session_name = _extract_session_name(card.claimed_by)
            if not session_name:
                result["error"] = "no_agent_session"
                return result

            result["session_name"] = session_name

        if not _kill_tmux_session(session_name):
            result["error"] = "failed_to_kill_session"
            return result

        project_path = await _get_project_path(project_key)
        if project_path:
            _remove_worktree_at(session_name, project_path)
        else:
            logger.warning(
                "No registered path for project %s; worktree not removed", project_key
            )

        result["cleaned"] = True
        logger.info("Cleaned up session %s for completed card %s", session_name, card_id)

    except Exception as e:
        result["error"] = str(e)
        logger.exception("Error cleaning up session for card %s", card_id)

    return result


def on_card_moved_to_done(card_id: str, project_key: str) -> None:
    """Schedule session cleanup when a card moves to Done.

    Always called from within a running async context (the kanban operations
    pipeline), so we schedule the cleanup as a background task via the
    already-running event loop.
    """
    import asyncio

    async def _cleanup() -> dict:
        return await cleanup_session_for_card(card_id, project_key)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_cleanup())
    except RuntimeError:
        asyncio.run(_cleanup())
