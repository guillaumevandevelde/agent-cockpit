"""Device-independent project identity for the board.

Primary key = normalized git remote ("git:<host>/<path>"); fallback =
"slug:<basename>" so repos without a remote still get a stable-ish key.
"""
import logging
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def _git_remote(project_path: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", project_path, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        url = out.stdout.strip()
        return url or None
    except Exception:
        return None


def normalize_remote(url: str) -> str:
    url = url.strip()
    url = re.sub(r"\.git$", "", url)
    url = re.sub(r"^[a-z]+://", "", url)        # strip scheme (https://, ssh://)
    url = re.sub(r"^[^@/]+@", "", url)          # strip user@
    url = url.replace(":", "/")                 # scp-style host:path -> host/path
    return re.sub(r"/+", "/", url)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "project"


def resolve_project_key(
    project_path: str,
    _remote_getter: Callable[[str], str | None] = _git_remote,
) -> str:
    remote = _remote_getter(project_path)
    if remote:
        return f"git:{normalize_remote(remote)}"
    return f"slug:{_slug(Path(project_path).name)}"


def safe_resolve_project_key(
    project_path: str,
    _remote_getter: Callable[[str], str | None] = _git_remote,
) -> str | None:
    """Resolve a project key without surfacing failures.

    Wraps `resolve_project_key` in a catch-all so call sites that must not
    fail-open (e.g. env-injection / audit rows in spawn_session) can pass a
    `None` project_key instead of letting the bare exception escape. Returns
    `None` on any failure; never raises.
    """
    try:
        return resolve_project_key(project_path, _remote_getter=_remote_getter)
    except Exception:
        return None


async def resolve_project_path(project_key: str) -> str | None:
    """Reverse of `resolve_project_key`: the local filesystem path of the
    first registered project whose computed key matches `project_key`.

    Scans all registered projects, computing each one's key, and returns the
    first matching path. Returns None when no registered project matches or on
    a DB error; a candidate whose own key lookup raises (e.g. not a git repo)
    is skipped rather than aborting the scan.

    This is the single public helper for the `project_key -> path` direction —
    see the earlier three-way duplication (`session_cleanup._get_project_path`,
    the run-ledger service, and `dispatch.match_project_paths`) that motivated
    it. Note: O(n) `resolve_project_key` calls, each shelling out to git, with
    no caching; callers needing to map *many* keys in one shot should use the
    single-pass bulk mapper `dispatch.match_project_paths` instead of calling
    this once per key.
    """
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.database import Project

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
