#!/usr/bin/env python3
"""Scan kanban_cards for cards that shipped a deliverable but never moved to a
terminal column.

The exact pathology this sweeper catches (kanban card
``4a60048365004d808e2dbfdd9551afe4``): a dispatched session ran a spike,
attached a ``branch`` deliverable to the card, and then — by crash, by
context-end, or simply by forgetting — never moved the card to Done.
``kanban_cards.claimed_by`` is now NULL, the column is still an agent
column (``analyst``/``engineer``/``claude``/etc.), and
``enrich_done_info`` finds no ``**Summary:** …`` comment op. To the
dispatcher's ``_next_card`` orphan-fallback the card looks untouched, so
on the next tick it gets re-claimed and a fresh session is spawned —
which has to re-derive the entire context (which port, fired or not,
what the prior session found) before it can see the existing
deliverable and conclude the work is already done. Card ``a4a091fa…``
burned 13 days this way.

This is the vangnet for that loop: an advisory sweeper that flags every
card with the broken shape so a follow-up chore card can move it to Done
(and stop the silent re-dispatch). The check combines three independent
signals, each necessary:

  - ``kanban_deliverables`` has ≥1 row for the card. The work shipped.
  - the card's column is **not** in ``{Done, Impediment, Awaiting
    Subtasks}`` (matches ``operations._TERMINAL_CLEANUP_COLUMNS``).
    Otherwise it's not a dispatch-candidate and the loop doesn't fire.
  - ``claimed_by`` is NULL/empty. Otherwise a session is actively
    working on it; flagging would be noise.

The fourth axis — ``done_summary`` populated — is the same op-log
enrichment the dispatch path uses (see ``enrich_done_info`` in
``backend/app/kanban/service.py``): a card that *did* receive a Summary
comment is, by the same definition, on its way to Done, even before the
move materialises. Flagging it would re-create the same problem in
reverse. We read it directly from ``kanban_ops.payload`` here so the
sweeper stays independent of the running app.

Output: a single JSON document on stdout (always — no human-readable
form) so the caller can pipe into ``jq``, compare against a saved
baseline, or attach it to a follow-up ``[chore]`` card. Schema:

    {
      "schema_version": 1,
      "scanned_at": "<ISO-8601 UTC>",
      "db_path": "<absolute path>",
      "totals": {
        "cards_scanned": <int>,           # rows touched by the LEFT JOIN
        "cards_with_deliverables": <int>, # ≥1 deliverable, regardless of column
        "orphaned_cards": <int>           # ≥1 deliverable AND non-terminal AND no claim AND no summary
      },
      "rows": [
        {
          "card_id": "...",
          "card_title": "<nullable>",
          "column": "<nullable>",
          "project_key": "<nullable>",
          "deliverables": [
            {"kind": "branch|...", "ref": "..."},
            ...
          ],
          "deliverable_count": <int>
        }
      ]
    }

Healthy cards (no deliverables, or any of the four "not orphaned" cases)
are silently omitted. Exit codes:

    0  clean OR (advisory mode and ≥1 hit)
    1  --strict and ≥1 hit
    2  usage error, DB missing/unreadable, or sqlite query failed

Advisory by default — mirrors the sibling sweepers
(``scripts/sweep_dangling_depends_on.py``,
``scripts/sweep_dangling_plan_refs.py``). ``--strict`` is for CI/pre-commit:
a backlog-cleanup pipeline should block on a non-zero count rather than
admit fresh orphans back onto the board.

Usage:
    scripts/sweep_orphaned_deliverables.py [--db PATH] [--strict] [--help]
    scripts/sweep_orphaned_deliverables.py --json    # default; explicit
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

# Matches ``backend/app/kanban/operations.py::_TERMINAL_CLEANUP_COLUMNS`` —
# columns whose moves count as "the session ends here". Kept as a local
# literal so the sweeper stays import-free and runnable against a bare
# kanban.db fixture without bringing the ORM along.
TERMINAL_COLUMNS = frozenset({"Done", "Impediment", "Awaiting Subtasks"})

# Prefix the dispatch / Move-to-Done gate writes on every Done-move
# comment. ``service.enrich_done_info`` looks for the same prefix on
# kanban_ops.payload["text"]; reusing it here means the sweeper's
# "done_summary present" notion is identical to the dispatch path's.
DONE_SUMMARY_PREFIX = "**Summary:** "

DEFAULT_DB = "~/.claude-registry/kanban.db"

# Unit separator (ASCII 0x1F): not a valid character in any of the
# deliverable kinds (``branch``/``commit``/``note``/etc.) or refs (a
# portable identifier with no ASCII control chars by construction).
# Used as the GROUP_CONCAT separator so a single deliverable blob
# round-trips losslessly into a Python list of ``(kind, ref)`` pairs.
# Inlined into the SQL because ``GROUP_CONCAT``'s separator argument
# must be a string literal — bind parameters don't reach it.
_DELIVERABLE_SEP = "\x1f"


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
    tables yet; the "no tables" path must surface as a clean report
    rather than a sqlite OperationalError crash.
    """
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _load_cards_with_deliverables(con: sqlite3.Connection) -> list[dict]:
    """Return one row per card that has ≥1 deliverable.

    ``LEFT JOIN`` so cards without deliverables show up with
    ``deliverable_count=0`` and ``deliverables_json=NULL``; the caller
    filters those out so we never emit a report row that has no
    deliverables field worth showing. The four columns we surface from
    kanban_cards are intentionally the minimum — the sweep doesn't read
    any of the other 30+ columns, so a fixture that only declares these
    four is enough.
    """
    rows = con.execute(
        "SELECT c.id, c.title, c.\"column\", c.project_key, c.claimed_by, "
        "  COUNT(d.id) AS deliverable_count, "
        "  GROUP_CONCAT(d.kind || ':' || d.ref, '" + _DELIVERABLE_SEP + "') AS deliverables_blob "
        "FROM kanban_cards c "
        "LEFT JOIN kanban_deliverables d ON d.card_id = c.id "
        "GROUP BY c.id, c.title, c.\"column\", c.project_key, c.claimed_by"
    ).fetchall()
    out: list[dict] = []
    for card_id, title, column, project_key, claimed_by, count, blob in rows:
        deliverables: list[dict] = []
        if blob:
            for entry in blob.split("\x1f"):
                kind, _, ref = entry.partition(":")
                deliverables.append({"kind": kind, "ref": ref})
        out.append({
            "card_id": card_id,
            "card_title": title,
            "column": column,
            "project_key": project_key,
            "claimed_by": claimed_by,
            "deliverable_count": count,
            "deliverables": deliverables,
        })
    return out


def _card_has_done_summary(con: sqlite3.Connection, card_id: str) -> bool:
    """Return True iff any comment op on ``card_id`` carries the
    ``**Summary:** `` prefix.

    Mirrors ``service.enrich_done_info``'s scan over ``kanban_ops``. A
    card with such a comment is by the app's own definition "on its way
    to Done" — the move may not have materialised yet, but the session
    did the work and recorded the outcome. Flagging it here would
    re-create the very loop this sweeper exists to catch.
    """
    row = con.execute(
        "SELECT 1 FROM kanban_ops "
        "WHERE entity_type = 'comment' "
        "  AND entity_id = ? "
        "  AND op_type = 'comment' "
        "  AND json_extract(payload, '$.text') LIKE ? "
        "LIMIT 1",
        (card_id, DONE_SUMMARY_PREFIX + "%"),
    ).fetchone()
    return row is not None


def sweep(db_path: Path) -> dict:
    """Run the sweep against ``db_path`` and return the report dict.

    Never raises on missing tables or empty databases — those map to a
    clean report with 0 orphans. Raises on file missing/unreadable
    (caller turns that into an exit-2 error).
    """
    if not db_path.exists() or not db_path.is_file():
        raise FileNotFoundError(f"kanban DB not found at: {db_path}")
    try:
        # Read-only: the sweeper should never mutate the board — the
        # only writes belong to a follow-up chore card / the
        # delete-guard that consumes the report and decides what to
        # repair.
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
                "cards_scanned": 0,
                "cards_with_deliverables": 0,
                "orphaned_cards": 0,
            },
            "rows": [],
        }
        if not _table_exists(con, "kanban_cards"):
            return report

        cards = _load_cards_with_deliverables(con)
        for card in cards:
            report["totals"]["cards_scanned"] += 1
            if card["deliverable_count"] == 0:
                # No deliverable attached — by definition not in the
                # "shipped but forgot to move" class. Skip early so the
                # summary-scan query only fires on candidates.
                continue
            report["totals"]["cards_with_deliverables"] += 1
            column = card["column"]
            claimed_by = card["claimed_by"]
            # Four predicates AND'd; the order is short-circuit-friendly
            # so a card in a terminal column (the common case for
            # properly-finished work) skips both the claim check and
            # the summary scan.
            if column in TERMINAL_COLUMNS:
                continue
            if claimed_by:
                continue
            if _card_has_done_summary(con, card["card_id"]):
                continue
            report["totals"]["orphaned_cards"] += 1
            report["rows"].append({
                "card_id": card["card_id"],
                "card_title": card["card_title"],
                "column": column,
                "project_key": card["project_key"],
                "deliverables": card["deliverables"],
                "deliverable_count": card["deliverable_count"],
            })
        return report
    finally:
        con.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sweep_orphaned_deliverables.py",
        description=(
            "Find kanban_cards that have ≥1 deliverable but never moved "
            "to a terminal column (Done/Impediment/Awaiting Subtasks) "
            "and have no live claim and no Done Summary comment. "
            "Emits a JSON report on stdout and exits 0 (advisory) / "
            "1 (with --strict + hits) / 2 (DB or query error). "
            "Run via the bash test harness "
            "scripts/test_sweep_orphaned_deliverables.sh for the contract."
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
            "Exit 1 when any orphan is found. Default is advisory: "
            "exit 0 even with hits, mirroring "
            "scripts/sweep_dangling_depends_on.py / "
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

    if args.strict and report["totals"]["orphaned_cards"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())