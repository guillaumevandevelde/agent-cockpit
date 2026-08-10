#!/usr/bin/env python3
"""Scan kanban_cards for children stuck in ``awaiting_plan_ref`` past the deadline.

The ``awaiting_plan_ref`` hold was originally written to close a seconds-wide
race between an analyst's step 3 (create_card) and step 4 (add_plan_attachment)
— but it had no upper bound, so a crashed analyst run parked its children for
days without a single signal. Kanban kaart 2341a40e… found five cards stuck
that way, three of them for 16 days.

The dispatch tick now escalates overdue holds inline (a comment + an
idempotent ``metadata["plan_ref_overdue_at"]`` marker), so going forward the
escalation is self-healing. This sweeper is the **vangnet for the existing
stock**: any card whose ``held_reason='awaiting_plan_ref'`` stamp is older
than the deadline is surfaced, so an operator can manually unblock the
historical inventory that predates the inline fix.

Output: a single JSON document on stdout (always — no human-readable form)
so the caller can pipe into ``jq``, compare against a saved baseline, or
attach it to a follow-up [chore] card. Schema::

    {
      "schema_version": 1,
      "scanned_at": "<ISO-8601 UTC>",
      "db_path": "<absolute path>",
      "deadline_seconds": 600,
      "totals": {
        "scanned_cards": <int>,
        "overdue": <int>
      },
      "rows": [
        {
          "card_id": "...",
          "title": "<nullable>",
          "column": "<current column>",
          "project_key": "...",
          "parent_card_id": "<nullable>",
          "held_since": "<ISO-8601 — when the awaiting_plan_ref hold was first stamped>",
          "overdue_seconds": <int>,
          "has_marker": <bool>,
          "reason": "<human-readable>"
        }
      ]
    }

Exit codes:

    0  clean OR (advisory mode and ≥1 hit)
    1  --strict and ≥1 hit
    2  usage error, DB missing/unreadable, or sqlite query failed

Advisory by default — mirrors ``scripts/sweep_dangling_plan_refs.py``'s
posture ("signal, not gate"). ``--strict`` is for CI: a backlog-cleanup
pipeline should block on a non-zero count rather than admit stale
``awaiting_plan_ref`` rows back onto the board.

Usage::

    scripts/sweep_awaiting_plan_ref_overdue.py [--db PATH] [--strict] [--help]
    scripts/sweep_awaiting_plan_ref_overdue.py --json    # default; explicit for clarity
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

# The dispatcher exports the deadline from dep_resolver so the threshold has
# one source of truth — read it here so the sweeper moves when the threshold
# moves. A direct import from the backend package would couple this script
# to a fast-evolving codebase; the constant lives in plain Python and the
# dispatch tick + the sweeper both honor it.
_DEADLINE_SECONDS = 600  # mirror of dep_resolver.PLAN_REF_DEADLINE_SECONDS

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
    """Return True iff ``name`` is a user table in ``con``."""
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


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def sweep(db_path: Path, *, deadline_seconds: int = _DEADLINE_SECONDS,
          now: datetime | None = None) -> dict:
    """Run the sweep against ``db_path`` and return the report dict.

    Never raises on missing tables or empty databases — those map to a
    clean report with 0 hits. Raises on file missing/unreadable (caller
    turns that into an exit-2 error).
    """
    if not db_path.exists() or not db_path.is_file():
        raise FileNotFoundError(f"kanban DB not found at: {db_path}")
    try:
        uri = f"file:{db_path}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        raise RuntimeError(f"cannot open kanban DB {db_path}: {e}") from e

    now = now or datetime.now(UTC)
    try:
        report: dict = {
            "schema_version": SCHEMA_VERSION,
            "scanned_at": now.isoformat(timespec="seconds"),
            "db_path": str(db_path),
            "deadline_seconds": deadline_seconds,
            "totals": {
                "scanned_cards": 0,
                "overdue": 0,
            },
            "rows": [],
        }
        if not _table_exists(con, "kanban_cards"):
            return report

        has_project_key = _column_exists(con, "kanban_cards", "project_key")
        has_parent_card_id = _column_exists(con, "kanban_cards", "parent_card_id")
        has_metadata = _column_exists(con, "kanban_cards", "metadata")

        select_parts = ["id", "title", "column", "held_reason", "held_since"]
        if has_project_key:
            select_parts.append("project_key")
        if has_parent_card_id:
            select_parts.append("parent_card_id")
        if has_metadata:
            select_parts.append("metadata")
        select_sql = ", ".join(select_parts)

        # Pull every card stamped awaiting_plan_ref; we filter on the
        # Python side so the "overdue" classification uses the same
        # tz-aware parsing as the dispatch tick (and so the report can
        # name the overdue_seconds for the operator).
        rows = con.execute(
            f"SELECT {select_sql} FROM kanban_cards "
            f"WHERE held_reason = ?",
            ("awaiting_plan_ref",),
        ).fetchall()
        report["totals"]["scanned_cards"] = len(rows)

        col_idx = 0
        for row in rows:
            card_id = row[col_idx]; col_idx += 1
            title = row[col_idx]; col_idx += 1
            column = row[col_idx]; col_idx += 1
            held_reason = row[col_idx]; col_idx += 1
            held_since_raw = row[col_idx]; col_idx += 1
            project_key = row[col_idx] if has_project_key else None
            if has_project_key:
                col_idx += 1
            parent_card_id = row[col_idx] if has_parent_card_id else None
            if has_parent_card_id:
                col_idx += 1
            metadata_raw = row[col_idx] if has_metadata else None
            if has_metadata:
                col_idx += 1

            held_since = _parse_iso(held_since_raw)
            if held_since is None:
                # Held but no clock — caller decides whether that
                # deserves a row. Conservative: include it with
                # overdue_seconds=0 so the operator sees the gap.
                overdue_seconds = 0
            else:
                overdue_seconds = int((now - held_since).total_seconds())
            if overdue_seconds < deadline_seconds:
                continue

            has_marker = False
            if metadata_raw:
                try:
                    meta = json.loads(metadata_raw)
                except (TypeError, ValueError):
                    meta = None
                if isinstance(meta, dict):
                    has_marker = bool(meta.get("plan_ref_overdue_at"))

            reason = (
                f"held_reason='awaiting_plan_ref' has been on this card "
                f"for {overdue_seconds}s (>{deadline_seconds}s deadline). "
                f"The analyst parent has not delivered an "
                f"``add_plan_attachment``; the child is invisible to the "
                f"dispatcher (kaart 2341a40e…). Manual unblock: resume "
                f"the analyst run or open its plan-attachment."
            )
            report["rows"].append({
                "card_id": card_id,
                "title": title,
                "column": column,
                "project_key": project_key,
                "parent_card_id": parent_card_id,
                "held_since": held_since_raw,
                "overdue_seconds": overdue_seconds,
                "has_marker": has_marker,
                "reason": reason,
            })
            report["totals"]["overdue"] += 1
        return report
    finally:
        con.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sweep_awaiting_plan_ref_overdue.py",
        description=(
            "Find kanban_cards rows whose ``held_reason='awaiting_plan_ref'`` "
            "stamp is older than the deadline — the vangnet for the historical "
            "stock that predates the inline escalation (kaart 2341a40e…). "
            "Emits a JSON report on stdout and exits 0 (advisory) / 1 (with "
            "--strict + hits) / 2 (DB or query error). Run via the bash test "
            "harness scripts/test_sweep_awaiting_plan_ref_overdue.sh for the "
            "contract."
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
            "Exit 1 when any overdue row is found. Default is advisory: "
            "exit 0 even with hits, mirroring "
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

    if args.strict and report["totals"]["overdue"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
