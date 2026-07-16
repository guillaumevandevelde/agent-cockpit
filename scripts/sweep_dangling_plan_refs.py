#!/usr/bin/env python3
"""Scan kanban_deliverables for dangling `plan_ref` rows.

A `plan_ref` deliverable links a child executor card to the `plan` deliverable
on its analyst-parent. The dispatcher resolves it on every spawn
(`backend/app/kanban/dispatch.py:_resolve_plan_for_child`) and surfaces a
PLAN CONTEXT preamble to the executor. When the parent or the referenced plan
is gone, the dispatcher's status becomes one of

    PLAN_DANGLING_PARENT     parent_card_id doesn't resolve to a kanban_cards row
    PLAN_MISSING_ON_PARENT   parent alive but the plan_deliverable_id isn't on it as kind='plan'
    PLAN_MALFORMED           ref JSON doesn't parse or lacks required keys

…and every subsequent dispatch of that child wastes a section explaining the
"Plan niet beschikbaar" diagnosis. The row never gets cleaned up; this sweeper
is the housekeeping tool to find them.

Output: a single JSON document on stdout (always — no human-readable form) so
the caller can pipe into `jq`, compare against a saved baseline, or attach it
to a follow-up [chore] card. Schema:

    {
      "schema_version": 1,
      "scanned_at": "<ISO-8601 UTC>",
      "db_path": "<absolute path>",
      "totals": {
        "plan_ref_rows": <int>,
        "dangling": <int>,
        "by_status": {
          "dangling_parent": <int>,
          "plan_missing_on_parent": <int>,
          "malformed_ref": <int>
        }
      },
      "rows": [
        {
          "deliverable_id": "...",
          "child_card_id": "...",
          "child_title": "<nullable — surfaced when the child card still exists>",
          "parent_card_id": "...",
          "plan_deliverable_id": "...",
          "status": "dangling_parent|plan_missing_on_parent|malformed_ref",
          "reason": "<human-readable>",
          "created_at": "<ISO-8601>"
        }
      ]
    }

Healthy plan_ref rows are silently omitted. Exit codes:

    0  clean OR (advisory mode and ≥1 hit)
    1  --strict and ≥1 hit
    2  usage error, DB missing/unreadable, or sqlite query failed

Advisory by default — mirrors `scripts/check-analysis-outcomes.sh`'s posture
("signal, not gate"). --strict is for CI: a backlog-cleanup pipeline should
block on a non-zero count rather than admit fresh danglings back onto the
board.

Usage:
    scripts/sweep_dangling_plan_refs.py [--db PATH] [--strict] [--help]
    scripts/sweep_dangling_plan_refs.py --json    # default; explicit for clarity
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

STATUS_DANGLING_PARENT = "dangling_parent"
STATUS_PLAN_MISSING_ON_PARENT = "plan_missing_on_parent"
STATUS_MALFORMED_REF = "malformed_ref"

ALL_STATUSES = (STATUS_DANGLING_PARENT, STATUS_PLAN_MISSING_ON_PARENT, STATUS_MALFORMED_REF)

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

    A fresh-DB fixture (zero-byte sqlite file) won't have the kanban tables
    yet; the dispatcher's "no tables" path must surface as a clean report
    rather than a sqlite OperationalError crash.
    """
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _categorise(
    con: sqlite3.Connection,
    raw_ref: str | None,
    parent_lookup_cache: dict[str, bool],
    plan_lookup_cache: dict[str, bool],
) -> tuple[str, str, str | None, str | None]:
    """Return (status, reason, parent_card_id, plan_deliverable_id) for one row.

    The lookup_*_cache dicts memoize per-row existence checks across the
    whole sweep — without them, a sweep over a DB with N dangling rows would
    issue up to 2N redundant SELECTs against an already small per-card budget.
    A live board has dozens of plan_refs; with a few analyst families that's
    hundreds of redundant cardinality lookups.

    Returns parent_card_id / plan_deliverable_id as None when the ref JSON
    doesn't parse or doesn't carry them — the caller's report can still
    name the deliverable + child card even when the ref is empty.
    """
    if not raw_ref:
        return (STATUS_MALFORMED_REF, "ref is empty", None, None)
    try:
        ref = json.loads(raw_ref)
    except (TypeError, ValueError) as e:
        return (STATUS_MALFORMED_REF, f"ref is not parseable JSON: {e}", None, None)
    if not isinstance(ref, dict):
        return (STATUS_MALFORMED_REF, "ref JSON is not an object", None, None)
    parent_id = ref.get("parent_card_id")
    plan_id = ref.get("plan_deliverable_id")
    if not parent_id or not plan_id:
        missing = [k for k in ("parent_card_id", "plan_deliverable_id") if not ref.get(k)]
        return (STATUS_MALFORMED_REF,
                f"ref is missing required key(s): {', '.join(missing)}",
                parent_id, plan_id)

    parent_live = parent_lookup_cache.get(parent_id)
    if parent_live is None:
        parent_live = con.execute(
            "SELECT 1 FROM kanban_cards WHERE id=?", (parent_id,)
        ).fetchone() is not None
        parent_lookup_cache[parent_id] = parent_live
    if not parent_live:
        return (STATUS_DANGLING_PARENT,
                f"parent card {parent_id!r} does not exist",
                parent_id, plan_id)

    plan_key = (parent_id, plan_id)
    plan_live = plan_lookup_cache.get(plan_key)
    if plan_live is None:
        plan_live = con.execute(
            "SELECT 1 FROM kanban_deliverables "
            "WHERE id=? AND card_id=? AND kind='plan'",
            (plan_id, parent_id),
        ).fetchone() is not None
        plan_lookup_cache[plan_key] = plan_live
    if not plan_live:
        return (STATUS_PLAN_MISSING_ON_PARENT,
                f"parent {parent_id!r} exists but plan {plan_id!r} "
                f"(kind='plan') is not on it",
                parent_id, plan_id)
    # Healthy — the caller filters this out. Returning a sentinel status
    # would force every consumer to learn about it; recasting as a tuple
    # shape with a None status is even worse. The well-typed return is
    # "we found an issue"; the helper owns the "issue" vocabulary.
    return ("ok", "", parent_id, plan_id)


def sweep(db_path: Path) -> dict:
    """Run the sweep against ``db_path`` and return the report dict.

    Never raises on missing tables or empty databases — those map to a clean
    report with 0 danglings. Raises on file missing/unreadable or on schema
    mismatch inside the per-row categorisation (caller turns those into
    exit-2 errors).
    """
    if not db_path.exists() or not db_path.is_file():
        raise FileNotFoundError(f"kanban DB not found at: {db_path}")
    try:
        # Read-only: the sweeper should never mutate the board — the only
        # writes belong to a follow-up chore card that consumes the report
        # and decides what to drop or migrate.
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
                "plan_ref_rows": 0,
                "dangling": 0,
                "by_status": {s: 0 for s in ALL_STATUSES},
            },
            "rows": [],
        }
        if not _table_exists(con, "kanban_deliverables") \
                or not _table_exists(con, "kanban_cards"):
            return report

        rows = con.execute(
            "SELECT id, card_id, ref, created_at FROM kanban_deliverables "
            "WHERE kind='plan_ref'"
        ).fetchall()
        report["totals"]["plan_ref_rows"] = len(rows)

        parent_cache: dict[str, bool] = {}
        plan_cache: dict[tuple[str, str], bool] = {}
        for d_id, child_id, raw_ref, created_at in rows:
            status, reason, parent_id, plan_id = _categorise(
                con, raw_ref, parent_cache, plan_cache,
            )
            if status == "ok":
                continue
            report["totals"]["by_status"][status] += 1
            report["totals"]["dangling"] += 1

            # The child_title lookup is a UX nicety for the operator; the
            # sweep's core contract only needs the deliverable + child +
            # parent + plan ids. When kanban_cards lacks a title column
            # (truncated synthetic fixtures used by the bash test harness),
            # degrade to None rather than abort the whole row.
            child_title = None
            try:
                row = con.execute(
                    "SELECT title FROM kanban_cards WHERE id=?", (child_id,)
                ).fetchone()
                if row is not None:
                    child_title = row[0]
            except sqlite3.OperationalError:
                pass
            report["rows"].append({
                "deliverable_id": d_id,
                "child_card_id": child_id,
                "child_title": child_title,
                "parent_card_id": parent_id,
                "plan_deliverable_id": plan_id,
                "status": status,
                "reason": reason,
                "created_at": created_at,
            })
        return report
    finally:
        con.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sweep_dangling_plan_refs.py",
        description=(
            "Find kanban_deliverables rows of kind='plan_ref' whose parent or "
            "referenced plan no longer resolve on the board. Emits a JSON "
            "report on stdout and exits 0 (advisory) / 1 (with --strict + "
            "hits) / 2 (DB or query error). Run via the bash test harness "
            "scripts/test_sweep_dangling_plan_refs.sh for the contract."
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
            "Exit 1 when any dangling row is found. Default is advisory: "
            "exit 0 even with hits, mirroring scripts/check-analysis-outcomes.sh."
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

    if args.strict and report["totals"]["dangling"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
