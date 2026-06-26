"""Coerce arbitrary strings into valid git branch names.

A worktree's branch name must satisfy `git check-ref-format`. User-supplied
names (e.g. an Agent Bridge worktree session) can contain spaces, path
traversal, or characters git forbids, which would make `git worktree add` /
`claude --worktree` fail. `sanitize_git_branch_name` rewrites such a name into
the closest valid branch name so the caller can use it (and tell the user it
was adjusted).
"""
from __future__ import annotations

import re

# Characters git always forbids in a ref, plus ASCII control chars and space.
_FORBIDDEN = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


def sanitize_git_branch_name(raw: str) -> str:
    """Return a valid git branch name derived from ``raw``.

    Follows the rules enforced by ``git check-ref-format``:
    forbidden characters and the ``..`` / ``@{`` sequences become ``-``; each
    slash-delimited component is trimmed of leading/trailing dots and dashes and
    of a trailing ``.lock``; empty components and leading/trailing slashes are
    dropped; runs of dashes collapse.

    Raises:
        ValueError: if nothing usable remains (e.g. ``"..."`` or whitespace).
    """
    s = _FORBIDDEN.sub("-", raw)
    s = s.replace("..", "-").replace("@{", "-")

    components: list[str] = []
    for part in s.split("/"):
        part = part.strip(".-")
        while part.endswith(".lock"):
            part = part[: -len(".lock")].strip(".-")
        if part:
            components.append(part)

    name = "/".join(components)
    name = re.sub(r"-{2,}", "-", name)

    if not name or name == "@":
        raise ValueError(f"Cannot derive a valid git branch name from {raw!r}")
    return name
