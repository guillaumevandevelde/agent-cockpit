#!/usr/bin/env python3
"""Scan kanban_cards for dangling `depends_on` ids.

Each non-Done card may carry a `depends_on` JSON array of card ids. The
dispatch dep-resolver (`backend/app/kanban/dep_resolver.py`,
`meets_dep_prerequisites`) fails *closed* on a dep that doesn't resolve to a
live card: a missing parent is treated the same as "not Done yet", so the
dependent card is never dispatchable. When the referenced card was later
deleted (e.g. via "Clear Done" or a manual edit) even though the depended-on
work is really finished, the dependent card is **permanently and invisibly
blocked** — the board only shows a cryptic `Blocked by: (missing)`.

This is the vangnet from `docs/cockpit/dangling-depends-on-analyse.md` §4: an
advisory sweeper that flags every non-Done card whose `depends_on` names a
card id that no longer exists. It catches the cases the delete-guard (sister
card) misses: cross-project deps and manual edits.

Output: a single JSON document on stdout (always — no human-readable form) so
the caller can pipe into `jq`, compare against a saved baseline, or attach it
to a follow-up [chore] card. Schema:

    {
      "schema_version": 1,
      "scanned_at": "<ISO-8601 UTC>",
      "db_path": "<absolute path>",
      "totals": {
        "cards_with_depends_on": <int>,
        "cards_with_dangling": <int>,
        "dangling_dep_ids": <int>
      },
      "rows": [
        {
          "card_id": "...",
          "card_title": "<nullable>",
          "column": "<nullable>",
          "project_key": "<nullable>",
          "dangling_dep_ids": ["...", "..."],
          "depends_on": ["...all declared deps..."]
        }
      ]
    }

Healthy cards (every `depends_on` id resolves to some card) are silently
omitted. Existence is checked board-wide, not per-project — a dep whose id
resolves to a card in *any* project is not dangling; only an id that resolves
nowhere is. Exit codes:

    0  clean OR (advisory mode and ≥1 hit)
    1  --strict and ≥1 hit
    2  usage error, DB missing/unreadable, or sqlite query failed

Advisory by default — mirrors `scripts/sweep_dangling_plan_refs.py`'s posture
("signal, not gate"). --strict is for CI/pre-commit: a backlog-cleanup
pipeline should block on a non-zero count rather than admit fresh danglings
back onto the board.

Usage:
    scripts/sweep_dangling_depends_on.py [--db PATH] [--strict] [--help]
    scripts/sweep_dangling_depends_on.py --json    # default; explicit for clarity
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

DONE_COLUMN = "Done"

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
    yet; the "no tables" path must surface as a clean report rather than a
    sqlite OperationalError crash.
    """
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _parse_deps(raw: str | None) -> list[str]:
    """Return the list of dep-ids declared in a card's `depends_on` column.

    The column is a SQLAlchemy JSON column, so it holds a JSON-encoded string
    (an array) or NULL. A malformed / non-array value yields an empty list —
    a dangling *dep-id* is the concern here, not a corrupt `depends_on` blob
    (that belongs to a schema-validation tool, not this sweeper). Non-string
    entries are coerced to str so a numeric id still gets an existence check.
    """
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(val, list):
        return []
    return [str(x) for x in val if x is not None and str(x) != ""]


def sweep(db_path: Path) -> dict:
    """Run the sweep against ``db_path`` and return the report dict.

    Never raises on missing tables or empty databases — those map to a clean
    report with 0 danglings. Raises on file missing/unreadable (caller turns
    that into an exit-2 error).
    """
    if not db_path.exists() or not db_path.is_file():
        raise FileNotFoundError(f"kanban DB not found at: {db_path}")
    try:
        # Read-only: the sweeper should never mutate the board — the only
        # writes belong to a follow-up chore card / the delete-guard that
        # consumes the report and decides what to repair.
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
                "cards_with_depends_on": 0,
                "cards_with_dangling": 0,
                "dangling_dep_ids": 0,
            },
            "rows": [],
        }
        if not _table_exists(con, "kanban_cards"):
            return report

        # Board-wide set of every live card id — the existence oracle. A dep
        # is dangling iff its id is not in this set.
        live_ids = {
            row[0] for row in con.execute("SELECT id FROM kanban_cards").fetchall()
        }

        rows = con.execute(
            "SELECT id, title, \"column\", project_key, depends_on "
            "FROM kanban_cards "
            "WHERE \"column\" != ? AND depends_on IS NOT NULL AND depends_on != '[]'",
            (DONE_COLUMN,),
        ).fetchall()

        for card_id, title, column, project_key, raw_deps in rows:
            deps = _parse_deps(raw_deps)
            if not deps:
                continue
            report["totals"]["cards_with_depends_on"] += 1
            dangling = [d for d in deps if d not in live_ids]
            if not dangling:
                continue
            report["totals"]["cards_with_dangling"] += 1
            report["totals"]["dangling_dep_ids"] += len(dangling)
            report["rows"].append({
                "card_id": card_id,
                "card_title": title,
                "column": column,
                "project_key": project_key,
                "dangling_dep_ids": dangling,
                "depends_on": deps,
            })
        return report
    finally:
        con.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sweep_dangling_depends_on.py",
        description=(
            "Find non-Done kanban_cards whose depends_on names a card id that "
            "no longer exists. Emits a JSON report on stdout and exits 0 "
            "(advisory) / 1 (with --strict + hits) / 2 (DB or query error). "
            "Run via the bash test harness "
            "scripts/test_sweep_dangling_depends_on.sh for the contract."
        ),
    )
    parser.add_argument(
        "--db",
        default=None,
        help=(
            "Path to kanban.db. Defaults to $KANBAN_DB or "
            f"{DEFAULT_DB}. The bash test harness always passes a "
            "--db override via the env var so the real board is untouched."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit 1 when any dangling dep is found. Default is advisory: "
            "exit 0 even with hits, mirroring scripts/sweep_dangling_plan_refs.py."
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

    if args.strict and report["totals"]["dangling_dep_ids"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
