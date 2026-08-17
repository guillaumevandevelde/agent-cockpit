#!/usr/bin/env python3
"""Print ``worktree_lease:<name>`` + ``worktree_owner:<name>`` rows from KanbanMeta.

Companion helper to ``scripts/worktree-gc.sh`` (kanban card a2268cd2…,
``docs/cockpit/fork-strategy-claude-deck-316.md`` §4.3). The gc script
needs to know which worktrees are currently inside their lease TTL so
it can leave them alone — a kill -9 of the agent process skips the
cleanup path entirely and leaves the worktree behind, possibly with
merged+clean state that would otherwise be reaped.

Output: one ``<worktree_name>\\t<owner>\\t<iso_expiry>`` per row on
stdout. Ordered by worktree name for deterministic output. Empty
stdout = no leases recorded. The script is decoupled from the
Python backend so the bash gc script can keep running on a system
where the backend's venv is broken or absent.

A row whose owner key is missing is silently skipped — a half-written
lease (expiry without owner) is treated as if it did not exist, the
same way ``app.kanban.lease.get_worktree_lease`` treats it. A
malformed expiry is also skipped so the gc script never crashes on a
board-side corruption.

Exit code: 0 on success (including when the DB is absent or unreadable).
With ``--clear <name>``: deletes both rows for that worktree in a single
transaction and exits 0. Failures are best-effort: a broken kanban DB
must never block the gc script from running.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


# Mirrors ``app.kanban.lease.WORKTREE_LEASE_PREFIX`` and
# ``app.kanban.lease.WORKTREE_OWNER_PREFIX``. The kanban-meta key for the
# expiry is ``worktree_lease:<worktree_name>`` so the gc script can fetch
# every lease with a single LIKE query.
LEASE_PREFIX = "worktree_lease:"
OWNER_PREFIX = "worktree_owner:"


def _open(db_path: str | None) -> sqlite3.Connection | None:
    """Best-effort open: a missing DB yields None so the caller can no-op."""
    if not db_path or not Path(db_path).exists():
        return None
    try:
        return sqlite3.connect(db_path)
    except sqlite3.Error:
        return None


def _parse_iso(value: str) -> str | None:
    """Echo the expiry string back if it parses; None otherwise.

    The gc script does its own ``now`` comparison in the shell, so we
    only validate that the row is parseable. We deliberately do NOT
    compare against ``now`` here — that decision must be made with the
    gc script's own clock to avoid timezone skew across machines.
    """
    from datetime import datetime
    try:
        datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return value


def list_leases(db_path: str | None) -> list[tuple[str, str, str]]:
    """Return ``[(worktree_name, owner, iso_expiry), ...]`` for parseable rows."""
    con = _open(db_path)
    if con is None:
        return []
    try:
        cur = con.execute(
            "SELECT key, value FROM kanban_meta WHERE key LIKE ?",
            (f"{LEASE_PREFIX}%",),
        )
        rows = cur.fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()

    result: list[tuple[str, str, str]] = []
    for key, expiry_value in rows:
        wt_name = key[len(LEASE_PREFIX):]
        expiry = _parse_iso(expiry_value)
        if expiry is None:
            continue
        # Owner is a separate row; a half-written lease is skipped.
        try:
            owner = _read_owner(db_path, wt_name)
        except sqlite3.Error:
            continue
        if owner is None:
            continue
        result.append((wt_name, owner, expiry))
    result.sort(key=lambda r: r[0])
    return result


def _read_owner(db_path: str, wt_name: str) -> str | None:
    con = _open(db_path)
    if con is None:
        return None
    try:
        cur = con.execute(
            "SELECT value FROM kanban_meta WHERE key = ?",
            (f"{OWNER_PREFIX}{wt_name}",),
        )
        row = cur.fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def clear_lease(db_path: str, wt_name: str) -> bool:
    """Delete both lease rows. Best-effort: returns True on success or no-op."""
    con = _open(db_path)
    if con is None:
        return True
    try:
        cur = con.execute(
            "DELETE FROM kanban_meta WHERE key IN (?, ?)",
            (f"{LEASE_PREFIX}{wt_name}", f"{OWNER_PREFIX}{wt_name}"),
        )
        con.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=os.environ.get("KANBAN_DB"),
        help="Path to kanban.db (defaults to $KANBAN_DB env var)",
    )
    parser.add_argument(
        "--clear",
        metavar="WORKTREE_NAME",
        help="Delete both lease rows for the given worktree name and exit.",
    )
    args = parser.parse_args()

    if args.clear:
        return 0 if clear_lease(args.db, args.clear) else 1

    for wt_name, owner, expiry in list_leases(args.db):
        print(f"{wt_name}\t{owner}\t{expiry}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
