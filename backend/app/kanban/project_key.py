"""Device-independent project identity for the board.

Primary key = normalized git remote ("git:<host>/<path>"); fallback =
"slug:<basename>" so repos without a remote still get a stable-ish key.
"""
import re
import subprocess
from collections.abc import Callable
from pathlib import Path


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
