"""Aggregate resumable Claude Code sessions across a project and its git worktrees."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import ResumableSession
from app.services.agent_bridge.spawn import _validate_directory
from app.services.session_service import SessionService

logger = logging.getLogger(__name__)

def _encode_project_folder(path: str) -> str:
    """Encode an absolute path to Claude's project folder name.

    Mirrors the frontend's claudeProjectFolderFromPath in frontend/src/lib/utils.ts:
    '/' and '.' both map to '-'. Order matters ('/' first), so '/a/.claude' -> '-a--claude'.
    """
    return path.rstrip("/").replace("/", "-").replace(".", "-")


def _list_worktrees(directory: str) -> list[tuple[str, bool]]:
    """Return (worktree_path, is_main) tuples for the repo containing `directory`.

    The first entry from `git worktree list` is the main worktree. Falls back to
    a single (directory, True) entry when git is unavailable or the directory is
    not a git repository.
    """
    try:
        result = subprocess.run(
            ["git", "-C", directory, "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return [(directory, True)]
    if result.returncode != 0:
        return [(directory, True)]

    paths: list[str] = []
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            paths.append(line[len("worktree ") :].strip())
    if not paths:
        return [(directory, True)]
    return [(path, index == 0) for index, path in enumerate(paths)]


async def list_resumable_sessions(
    directory: str,
    limit: int,
    db: AsyncSession | None,
) -> list[ResumableSession]:
    """List resumable sessions across `directory`'s project and its worktrees."""
    main_dir = _validate_directory(directory)
    worktrees = _list_worktrees(main_dir)

    service = SessionService(db)
    aggregated: list[ResumableSession] = []
    for path, is_main in worktrees:
        folder = _encode_project_folder(path)
        label = "main" if is_main else (Path(path).name or "main")
        response = await service.list_sessions(
            project_folder=folder,
            limit=limit,
            sort_by="date",
            sort_order="desc",
        )
        for summary in response.sessions:
            aggregated.append(
                ResumableSession(**summary.model_dump(), worktree_label=label)
            )

    # ISO-8601 timestamps sort lexicographically in chronological order
    # (assuming the offset-free local timestamps SessionService emits are monotonic).
    aggregated.sort(key=lambda s: s.modified_at, reverse=True)
    return aggregated[:limit]
