#!/usr/bin/env python3
"""Print kanban worktree/branch pairs that must NOT be removed by worktree-gc.sh.

The Kanban dispatch model owns each in-flight agent session via a kanban card:
the card's `claimed_by` is the live `agent:<worktree_name>` claimant, and its
`column` is one of the agent columns (Backlog, analyst, engineer, ...). When
that combination is set, the worktree under `<project>/.claude/worktrees/
<worktree_name>` is in active use — removing it out from under a running
session is exactly the failure mode worktree-gc.sh used to hit (see the
"[problem] worktree-gc verwijdert branch/worktree van actieve analyst-sessie"
postmortem). Done/Impediment cards have their claim cleared (or it never
existed), so they're not protected.

Output: one `<worktree_name>\t<branch>` per line on stdout. Names match the
basename of the worktree directory under `<project>/.claude/worktrees/`.
Empty stdout = nothing is protected.

Exit code: 0 on success (including when the DB is absent or unreadable — the
caller can fall back to the legacy merge+clean logic).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


# Columns whose cards have already been released by the cleanup pipeline, so
# their worktrees are safe to remove. Matches backend/app/kanban/operations.py
# ("Done" releases the claim on the move) and Impediment (where the original
# agent claim has been released before the card was handed off).
_RELEASED_COLUMNS = {"Done", "Impediment"}


def active_worktrees(db_path: str | None) -> list[tuple[str, str]]:
    """Return [(worktree_name, branch), ...] for cards that hold a live claim.

    Args:
        db_path: Absolute path to kanban.db, or None when missing/unset.

    Returns:
        List of (worktree_name, branch) tuples. Each row matches an active
        kanban card: `claimed_by LIKE 'agent:%'` AND column NOT IN released.
    """
    if not db_path or not Path(db_path).exists():
        return []

    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            "SELECT claimed_by FROM kanban_cards "
            "WHERE claimed_by LIKE 'agent:%' "
            "AND (column IS NULL OR column NOT IN (?, ?))",
            (*_RELEASED_COLUMNS,),
        )
        return [(row[0].removeprefix("agent:"), row[0].removeprefix("agent:"))
                for row in cur.fetchall()]
    except sqlite3.Error:
        # Schema mismatch, corrupt DB, etc. — treat as no active claims rather
        # than crashing gc. The legacy merge+clean logic will still run.
        return []
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=os.environ.get("KANBAN_DB"),
        help="Path to kanban.db (defaults to $KANBAN_DB env var)",
    )
    args = parser.parse_args()
    for name, branch in active_worktrees(args.db):
        print(f"{name}\t{branch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())