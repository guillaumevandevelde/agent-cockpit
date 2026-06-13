"""Device-independent project identity for the board.

Primary key = normalized git remote ("git:<host>/<path>"); fallback =
"slug:<basename>" so repos without a remote still get a stable-ish key.
"""
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional


def _git_remote(project_path: str) -> Optional[str]:
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
    url = url.replace(":", "/", 1) if "/" not in url.split(":", 1)[0] else url
    url = url.replace(":", "/")                 # scp-style host:path -> host/path
    return re.sub(r"/+", "/", url)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "project"


def resolve_project_key(
    project_path: str,
    _remote_getter: Callable[[str], Optional[str]] = _git_remote,
) -> str:
    remote = _remote_getter(project_path)
    if remote:
        return f"git:{normalize_remote(remote)}"
    return f"slug:{_slug(Path(project_path).name)}"
