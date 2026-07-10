"""Live git status for a running agent session's working directory.

Resolves the tmux pane's current path on demand and runs git out-of-process
with asyncio so a slow/large repo never blocks the event loop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 5.0
_TMUX_TIMEOUT = 5.0

_EMPTY_STATUS: dict[str, Any] = {
    "branch": None,
    "detached": False,
    "upstream": None,
    "ahead": 0,
    "behind": 0,
    "dirty": False,
}


def parse_porcelain_v2(output: str) -> dict[str, Any]:
    """Parse `git status --porcelain=v2 --branch` into a status summary.

    Branch header lines start with `# branch.*`; any other non-header line is a
    tracked/untracked change entry, which marks the worktree dirty.
    """
    result: dict[str, Any] = dict(_EMPTY_STATUS)
    for line in output.splitlines():
        if line.startswith("# branch.head "):
            head = line[len("# branch.head "):].strip()
            if head == "(detached)":
                result["detached"] = True
                result["branch"] = None
            else:
                result["branch"] = head
        elif line.startswith("# branch.upstream "):
            result["upstream"] = line[len("# branch.upstream "):].strip()
        elif line.startswith("# branch.ab "):
            for token in line[len("# branch.ab "):].split():
                if token.startswith("+"):
                    result["ahead"] = int(token[1:])
                elif token.startswith("-"):
                    result["behind"] = int(token[1:])
        elif line and not line.startswith("#"):
            result["dirty"] = True
    return result


async def resolve_pane_cwd(target: str) -> str | None:
    """Return the live current path of a tmux pane, or None if unavailable."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "display-message", "-p", "-t", target, "#{pane_current_path}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_TMUX_TIMEOUT)
    except FileNotFoundError:
        return None
    except TimeoutError:
        logger.warning("tmux display-message timed out for %s", target)
        return None
    if proc.returncode != 0:
        return None
    path = stdout.decode().strip()
    return path or None


async def get_git_status(directory: str) -> dict[str, Any]:
    """Run git status in ``directory`` and return a status summary.

    A non-git directory (or missing git binary) yields ``is_git_repo: False``.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", directory, "status", "--porcelain=v2", "--branch",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_GIT_TIMEOUT)
    except FileNotFoundError:
        return {"is_git_repo": False, **_EMPTY_STATUS}
    except TimeoutError:
        logger.warning("git status timed out in %s", directory)
        return {"is_git_repo": False, **_EMPTY_STATUS}
    if proc.returncode != 0:
        return {"is_git_repo": False, **_EMPTY_STATUS}
    return {"is_git_repo": True, **parse_porcelain_v2(stdout.decode())}


async def get_session_git_status(target: str) -> dict[str, Any] | None:
    """Live git status for the pane ``target``; None if the pane is gone."""
    cwd = await resolve_pane_cwd(target)
    if cwd is None:
        return None
    return await get_git_status(cwd)
