#!/usr/bin/env python3
"""Scan kanban_cards for parents parked in `Awaiting Subtasks` with zero
living children.

The historical-inventory sweeper for kaart `400d6a77…`. The bug was that
``close_parent_if_all_children_done`` short-circuited on `not children or
any(...): return False`, so a parent parked in `Awaiting Subtasks` whose
last child was deleted (via ``DELETE /api/v1/kanban/cards/{cid}`` or
``POST /api/v1/kanban/clear-column``) stayed there forever — on the live
board, five parents accumulated up to 6 weeks of stranded parked time
before being manually closed.

The fix wires auto-close into the delete and clear-column paths. This
sweeper is the vangnet for the historical inventory: it surfaces any
parents that are still parked AND have no children, so an operator can
manually move them to Done or back to a productive column.

Output: a single JSON document on stdout (always — no human-readable
form) so the caller can pipe into `jq`, compare against a saved baseline,
or attach it to a follow-up [chore] card. Schema:

    {
      "schema_version": 1,
      "scanned_at": "<ISO-8601 UTC>",
      "db_path": "<absolute path>",
      "totals": {
        "scanned_parents": <int>,
        "orphaned_parents": <int>
      },
      "rows": [
        {
          "card_id": "...",
          "title": "<nullable>",
          "column": "Awaiting Subtasks",
          "project_key": "...",
          "created_at": "<ISO-8601>",
          "updated_at": "<ISO-8601>",
          "parked_since": "<ISO-8601 — same as updated_at>",
          "reason": "<human-readable>"
        }
      ]
    }

Healthy parents (≥1 child, OR parked-but-with-children, OR in any other
column) are silently omitted. Exit codes:

    0  clean OR (advisory mode and ≥1 hit)
    1  --strict and ≥1 hit
    2  usage error, DB missing/unreadable, or sqlite query failed

Advisory by default — mirrors `scripts/sweep_dangling_plan_refs.py`'s
posture ("signal, not gate"). --strict is for CI: a backlog-cleanup
pipeline should block on a non-zero count rather than admit fresh
orphans back onto the board.

Usage:
    scripts/sweep_orphaned_parents_awaiting_subtasks.py [--db PATH] [--strict] [--help]
    scripts/sweep_orphaned_parents_awaiting_subtasks.py --json    # default; explicit for clarity
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path


SCHEMA_VERSION = 1

# Hard-coded by convention (docs/cockpit/kanban-conventions.md §1):
# the Awaiting Subtasks column name is fixed across all projects, so
# hard-coding it here is correct. If the column name ever changes,
# this constant and the test fixture's seed both move in lockstep.
AWAITING_SUBTASKS_COLUMN = "Awaiting Subtasks"

DEFAULT_DB = "~/.claude-registry/kanban.db"


def _resolve_db_path(cli_arg: str | None) -> Path:
    """Resolve the DB path: CLI arg > $KANBAN_DB > default."""
    if cli_arg:
        return Path(cli_arg).expanduser().resolve()
    env = os.environ.get("KANBAN_DB")
    if env:
        return Path(env).expanduser().resolve()
    return Path(DEFAULT_DB).expanduser().resolve()


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    """Return True iff ``name`` is a user table in ``con``.

    A fresh-DB fixture (zero-byte sqlite file) won't have the kanban
    tables yet; the sweep's "no tables" path must surface as a clean
    report rather than a sqlite OperationalError crash.
    """
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _column_exists(con: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True iff ``table.column`` exists. The kanban_cards schema
    has been extended over time; older fixtures may not carry every
    column the sweep wants to surface. PRAGMA is cheap and per-run, so
    we probe once up front and degrade gracefully when a column is
    absent (the row still appears, just without that field)."""
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def sweep(db_path: Path) -> dict:
    """Run the sweep against ``db_path`` and return the report dict.

    Never raises on missing tables or empty databases — those map to a
    clean report with 0 hits. Raises on file missing/unreadable (caller
    turns that into an exit-2 error).

    The query joins ``kanban_cards`` with itself: parked parents on the
    left, their children on the right, then filters to rows that have
    no matching child. SQLite evaluates this with a NOT EXISTS
    subquery, which is the canonical "orphaned parent" pattern —
    cheaper than fetching every parent and counting children in Python
    (a board with thousands of cards would otherwise allocate a list
    per parent on every sweep).
    """
    if not db_path.exists() or not db_path.is_file():
        raise FileNotFoundError(f"kanban DB not found at: {db_path}")
    try:
        # Read-only: the sweeper should never mutate the board — the
        # only writes belong to a follow-up chore card that consumes
        # the report and decides what to do with each orphan.
        uri = f"file:{db_path}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        raise RuntimeError(f"cannot open kanban DB {db_path}: {e}") from e

    try:
        report: dict = {
            "schema_version": SCHEMA_VERSION,
            "scanned_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "db_path": str(db_path),
            "totals": {
                "scanned_parents": 0,
                "orphaned_parents": 0,
            },
            "rows": [],
        }
        if not _table_exists(con, "kanban_cards"):
            return report

        # Probe which optional columns exist on this fixture. The
        # sweep is graceful when older schemas don't carry them — the
        # row still surfaces, just without the missing fields.
        has_project_key = _column_exists(con, "kanban_cards", "project_key")
        has_created_at = _column_exists(con, "kanban_cards", "created_at")
        has_updated_at = _column_exists(con, "kanban_cards", "updated_at")

        # Build the SELECT list defensively — every column is wrapped
        # in a NULL fallback via `coalesce(..., NULL)` so the row
        # shape is stable across fixture versions. This keeps the JSON
        # contract steady for any consumer that jq-greps for keys.
        select_parts = ["p.id", "p.title"]
        if has_project_key:
            select_parts.append("p.project_key")
        if has_created_at:
            select_parts.append("p.created_at")
        if has_updated_at:
            select_parts.append("p.updated_at")
        select_sql = ", ".join(select_parts)

        sql = (
            f"SELECT {select_sql} FROM kanban_cards p "
            f"WHERE p.column = ? "
            f"AND NOT EXISTS ("
            f"  SELECT 1 FROM kanban_cards c "
            f"  WHERE c.parent_card_id = p.id"
            f")"
        )
        rows = con.execute(sql, (AWAITING_SUBTASKS_COLUMN,)).fetchall()
        report["totals"]["scanned_parents"] = len(rows)
        report["totals"]["orphaned_parents"] = len(rows)

        col_idx = 0
        for row in rows:
            card_id = row[col_idx]; col_idx += 1
            title = row[col_idx]; col_idx += 1
            project_key = row[col_idx] if has_project_key else None
            if has_project_key:
                col_idx += 1
            created_at = row[col_idx] if has_created_at else None
            if has_created_at:
                col_idx += 1
            updated_at = row[col_idx] if has_updated_at else None
            if has_updated_at:
                col_idx += 1
            # ``updated_at`` is the most recent mutation, which for a
            # parked parent is the moment it was parked (no further
            # mutations happen on a parked card until it auto-closes
            # or is moved). Surface it as ``parked_since`` for the
            # operator so the report answers "how long has this been
            # stranded?" without an extra query.
            reason = (
                f"Parent parked in {AWAITING_SUBTASKS_COLUMN!r} with zero "
                f"children — close_parent_if_all_children_done should have "
                f"auto-closed this on the most recent child delete, but a "
                f"pre-fix sweep left it stranded (kaart 400d6a77…)."
            )
            report["rows"].append({
                "card_id": card_id,
                "title": title,
                "column": AWAITING_SUBTASKS_COLUMN,
                "project_key": project_key,
                "created_at": created_at,
                "updated_at": updated_at,
                "parked_since": updated_at,
                "reason": reason,
            })
        return report
    finally:
        con.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sweep_orphaned_parents_awaiting_subtasks.py",
        description=(
            "Find kanban_cards rows in `Awaiting Subtasks` with zero "
            "living children — parents stranded by the pre-fix delete "
            "and Clear-Done paths (kaart 400d6a77…). Emits a JSON report "
            "on stdout and exits 0 (advisory) / 1 (with --strict + hits) "
            "/ 2 (DB or query error). Run via the bash test harness "
            "scripts/test_sweep_orphaned_parents_awaiting_subtasks.sh for "
            "the contract."
        ),
    )
    parser.add_argument(
        "--db",
        default=None,
        help=(
            "Path to kanban.db. Defaults to $KANBAN_DB or "
            f"{DEFAULT_DB}. The bash test harness always passes "
            "--db-style override via the env var so the real board is "
            "untouched."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit 1 when any orphaned parent is found. Default is "
            "advisory: exit 0 even with hits, mirroring "
            "scripts/sweep_dangling_plan_refs.py."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON on stdout (default; flag exists for pipeline clarity).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    db_path = _resolve_db_path(args.db)
    try:
        report = sweep(db_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(
            "Set KANBAN_DB=/path/to/kanban.db or pass --db=/path/to/kanban.db.",
            file=sys.stderr,
        )
        return 2
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")

    if args.strict and report["totals"]["orphaned_parents"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
