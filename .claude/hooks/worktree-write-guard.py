#!/usr/bin/env python3
"""PreToolUse guard: refuse file-edit tools that target the shared checkout.

Why this exists
---------------
Claude Code's own worktree isolation is bound to the ``--worktree`` session
mode, not to a cwd that happens to sit inside a worktree. The Cockpit
dispatcher spawns ``mode="plain"`` (``backend/app/kanban/dispatch.py``), so a
dispatched session gets no isolation from the CLI at all -- see
``docs/cockpit/worktree-isolatie-meting.md``. Until this guard, the prose rule
in ``.claude/agents/engineer.md`` / ``analyst.md`` was the only thing standing
between an absolute-path slip and a write landing on top of a concurrent
session's uncommitted work (kanban card 513e37a1a86e41db8b6af8423292f6b6).

Scope: file-edit tools only
---------------------------
The guard covers ``Write`` / ``Edit`` / ``MultiEdit`` / ``NotebookEdit`` and
nothing else. That is deliberate, and it is what keeps the allowed-path list
empty: every legitimate write outside the worktree that this repo performs --
the ship recipe's ``git -C <main-checkout> pull --ff-only``, its merge worktree
under ``$HOME/.cache/cockpit-ship``, ``mv``-based cleanup -- goes through Bash,
never through an edit tool. Guarding Bash would mean parsing arbitrary shell
for write targets, which is a bug farm with a blast radius over every ship.
Claude Code's own ``--worktree`` mode makes the same cut: it blocks ``Write``
but lets ``echo X > <shared path>`` through.

Session recognition
-------------------
Both roots are derived lexically from the session ``cwd`` reported in the hook
payload:

    <main-checkout>/.claude/worktrees/<name>[/...]
    ^^^^^^^^^^^^^^^                     ^^^^^^
    main checkout                       worktree root

A cwd without ``/.claude/worktrees/`` is not a worktree session (an interactive
session in the main checkout, a shell in ``$HOME/.cache/cockpit-ship``), and the
guard allows everything. Deriving from the payload rather than from this file's
own location means the guard behaves identically whether Claude Code resolves
``$CLAUDE_PROJECT_DIR`` to the worktree or to the main checkout, and it
generalises to any project the dispatcher drives.

Path comparison is lexical (``normpath``, no ``realpath``). A symlink pointing
out of the worktree is therefore not caught -- accepted: this guards the
accident class (a relative path written as absolute), not a determined writer.

Contract
--------
Exit 0 = allow. Exit 2 = blocking error, stderr goes back to the model as the
reason. Every unexpected condition -- unparseable stdin, missing fields, any
exception -- exits 0. A broken guard must never stall a dispatch.
"""

import json
import os
import sys

MARKER = "/.claude/worktrees/"
GUARDED_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _verdict(payload: dict) -> str | None:
    """Return a deny reason, or None to allow."""
    if payload.get("tool_name") not in GUARDED_TOOLS:
        return None

    tool_input = payload.get("tool_input") or {}
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(target, str) or not target:
        return None

    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or MARKER not in cwd:
        return None

    main_checkout, _, rest = cwd.partition(MARKER)
    worktree_name = rest.split("/", 1)[0]
    if not main_checkout or not worktree_name:
        return None
    worktree_root = main_checkout + MARKER + worktree_name

    target = os.path.expanduser(target)
    if not os.path.isabs(target):
        target = os.path.join(cwd, target)
    target = os.path.normpath(target)

    if _under(target, worktree_root) or not _under(target, main_checkout):
        return None

    return (
        f"Refusing this write: {target} is in the shared checkout "
        f"{main_checkout}, not in your worktree {worktree_root}. Another "
        "dispatched session may have uncommitted work there. Write the "
        "worktree copy instead -- a relative path resolves to it, or prefix "
        f"the absolute path with {worktree_root}/."
    )


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            return 0
        reason = _verdict(payload)
    except Exception:  # noqa: BLE001 -- fail open, never stall a dispatch
        return 0
    if reason is None:
        return 0
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
