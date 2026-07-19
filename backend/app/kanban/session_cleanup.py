"""Session cleanup when kanban cards complete.

When a card moves to "Done", the agent session that worked on it should be
closed. This module provides the cleanup logic and integrates with the
kanban operations pipeline.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from app.kanban.project_key import resolve_project_path
from app.services.sandcastle_service import sandcastle_service

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
    """Kill a tmux session by name.

    Retries once on a genuine failure (tmux/filesystem can be transiently
    unavailable, e.g. under WSL/DrvFs contention). Returns True if the
    session was killed or was already gone (tmux reports "can't find
    session"); False only if a live session could not be confirmed dead
    after the retry.
    """
    last_stderr = ""
    for attempt in range(2):
        try:
            result = subprocess.run(
                ["tmux", "kill-session", "-t", session_name],
                capture_output=True, text=True, timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("Error killing session %s: %s", session_name, e)
            return False

        if result.returncode == 0:
            logger.info("Killed tmux session: %s", session_name)
            return True
        if "can't find session" in result.stderr:
            logger.info("tmux session %s already gone", session_name)
            return True

        last_stderr = result.stderr
        if attempt == 0:
            logger.warning(
                "Failed to kill session %s, retrying: %s", session_name, last_stderr
            )

    logger.error(
        "Failed to kill session %s after retry — it may still be running: %s",
        session_name, last_stderr,
    )
    return False


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


async def _find_running_sandcastle_run(session_name: str):
    """Return the pending/running SandcastleRun whose branch == session_name, or None."""
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.sandcastle import SandcastleRun

    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(SandcastleRun).where(
                SandcastleRun.branch == session_name,
                SandcastleRun.status.in_(("pending", "running")),
            )
        )).scalar_one_or_none()


async def _cancel_sandcastle_run(session_name: str) -> bool:
    """Cancel the sandcastle run backing this session, if any. Returns True if a run
    was found and cancellation was attempted."""
    run = await _find_running_sandcastle_run(session_name)
    if run is None:
        return False
    try:
        await sandcastle_service.cancel_run(run.id)
    except Exception:
        logger.exception("failed to cancel sandcastle run %s", run.id)
    return True


async def _release_claim(card_id: str, project_key: str) -> None:
    """Clear the card's agent: claim so a Done card is never shown as claimed."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.operations import apply_operation

    try:
        async with KanbanSessionLocal() as session:
            await apply_operation(
                session, op_type="release", entity_type="card",
                project_key=project_key, entity_id=card_id, payload={},
            )
            await session.commit()
    except Exception:
        logger.exception("failed to release claim on card %s", card_id)


def _default_branch(worktree_path: str) -> str:
    """Best-effort remote default branch name (falls back to 'master')."""
    try:
        result = subprocess.run(
            ["git", "-C", worktree_path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().rsplit("/", 1)[-1]
    except Exception:
        pass
    return "master"


async def _worktree_path_for_card(card) -> Path | None:
    """Best-effort resolution of the git worktree directory backing this card.

    Tries the live claim (worktree lives at <project_path>/.claude/worktrees/
    <session_name>) first, then a "To Resume" target (card.resume_project_folder,
    which was recorded by the dead-session reaper). Returns None when neither
    resolves to an existing worktree.
    """
    session_name = _extract_session_name(getattr(card, "claimed_by", None))
    if session_name:
        project_path = await resolve_project_path(card.project_key)
        if project_path:
            candidate = Path(project_path) / ".claude" / "worktrees" / session_name
            if candidate.is_dir():
                return candidate

    resume_folder = getattr(card, "resume_project_folder", None)
    if resume_folder:
        from app.services.runs.cc_spawn import _resolve_project_directory
        try:
            resolved = _resolve_project_directory(
                resume_folder, getattr(card, "resume_session_id", None),
            )
        except ValueError:
            return None
        return Path(resolved)

    return None


async def find_worktree_unmerged_warning(card) -> dict | None:
    """Check whether the git worktree backing this card (if any) still holds
    commits or changes that never landed on the project's default branch.

    Advisory only — never raises. A card that survived a killed/orphaned agent
    session (see [[prepush-drvfs-contention...]] postmortem: a session's finished,
    committed fix sat unmerged for a day because the card was deleted without
    anyone checking its worktree) is exactly the case this guards against.
    Returns None when there's nothing to warn about, or when the worktree/git
    state can't be determined at all.
    """
    worktree_path = await _worktree_path_for_card(card)
    if worktree_path is None or not (worktree_path / ".git").exists():
        return None

    try:
        branch = subprocess.run(
            ["git", "-C", str(worktree_path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() or "HEAD"

        default_branch = _default_branch(str(worktree_path))
        merged = subprocess.run(
            ["git", "-C", str(worktree_path), "merge-base", "--is-ancestor", "HEAD", default_branch],
            capture_output=True, timeout=10,
        ).returncode == 0

        ahead = 0
        if not merged:
            count = subprocess.run(
                ["git", "-C", str(worktree_path), "rev-list", "--count", f"{default_branch}..HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            ahead = int(count.stdout.strip() or 0)

        dirty = bool(subprocess.run(
            ["git", "-C", str(worktree_path), "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip())
    except Exception:
        logger.warning("could not inspect worktree %s for unmerged work", worktree_path, exc_info=True)
        return None

    if merged and not dirty:
        return None

    return {
        "worktree_path": str(worktree_path),
        "branch": branch,
        "default_branch": default_branch,
        "ahead": ahead,
        "dirty": dirty,
    }


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

        # Sandcastle sessions have no tmux session: cancel the run instead.
        if await _cancel_sandcastle_run(session_name):
            await _release_claim(card_id, project_key)
            result["cleaned"] = True
            logger.info("Cancelled sandcastle run for completed card %s", card_id)
            return result

        # Try to kill the tmux session. _kill_tmux_session already treats
        # "already gone" as success and retries genuine failures once, so a
        # False here means a live session could not be confirmed dead. We
        # still continue with worktree removal and claim release, since a
        # stuck claim on an otherwise-Done card is worse than a possibly
        # lingering process.
        tmux_killed = _kill_tmux_session(session_name)
        if not tmux_killed:
            logger.warning(
                "Could not confirm tmux session %s is dead — continuing "
                "cleanup for card %s, but the agent process may still be "
                "running", session_name, card_id
            )

        project_path = await resolve_project_path(project_key)
        if project_path:
            _remove_worktree_at(session_name, project_path)
        else:
            logger.warning(
                "No registered path for project %s; worktree not removed", project_key
            )

        await _release_claim(card_id, project_key)
        result["cleaned"] = True
        result["tmux_killed"] = tmux_killed
        logger.info(
            "Cleaned up session %s for completed card %s (tmux killed: %s)",
            session_name, card_id, tmux_killed,
        )

    except Exception as e:
        result["error"] = str(e)
        logger.exception("Error cleaning up session for card %s", card_id)

    return result


# Strong references to in-flight cleanup tasks. asyncio only holds a weak
# reference to a scheduled task, so a task with no other reference can be
# garbage-collected before it runs (see asyncio.create_task docs). Mirrors
# the _sandcastle_start_tasks pattern in dispatch.py.
_cleanup_tasks: set = set()


def on_card_moved_to_done(card_id: str, project_key: str) -> None:
    """Schedule session cleanup when a card moves to a terminal column
    (Done or Impediment).

    Despite the historical name (kept for grep-trace stability — see
    kanban card 28b578ba), this now fires for both Done and Impediment
    transitions. Both are documented "session ends here" markers in
    `dispatch._build_ship_instructions` and `mcp_server.report_impediment`,
    so they share the same kill-the-tmux + remove-the-worktree pipeline.

    Always called from within a running async context (the kanban operations
    pipeline), so we schedule the cleanup as a background task via the
    already-running event loop, keeping a strong reference until it completes.
    """
    import asyncio

    async def _cleanup() -> dict:
        return await cleanup_session_for_card(card_id, project_key)

    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_cleanup())
        _cleanup_tasks.add(task)
        task.add_done_callback(_cleanup_tasks.discard)
    except RuntimeError:
        asyncio.run(_cleanup())
