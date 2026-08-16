#!/usr/bin/env python3
"""Sweep afgeronde zelfverbeterkaarten zonder effectclaim.

Derde ingreep uit [`docs/cockpit/cockpit-richting-decision.md`](../docs/cockpit/cockpit-richting-decision.md)
§8: een `[self-improve]`/`[problem]`-kaart in `Done` hoort te zeggen welk
waargenomen gedrag nu anders is. Zonder die claim blijft onmeetbaar of de loop
iets oplevert — precies het gat dat de meting van 2026-08-15 blootlegde.

De vorm is dezelfde als voor documenten: het script hergebruikt de
`EFFECT_PATTERNS` uit `sweep_unchecked_implemented_markers.py`, zodat "wat telt
als effectclaim" op één plek staat. Gezocht wordt in de comments van de kaart
(de op-log, `entity_type='comment'`) en in de beschrijving.

Het script detecteert uitsluitend de **structurele afwezigheid** van een
effectclaim. De inhoud beoordelen blijft mensenwerk.

Output: één JSON-document op stdout. Schema:

    {
      "schema_version": 1,
      "scanned_at": "<ISO-8601 UTC>",
      "db_path": "<abs path>",
      "totals": {
        "done_loop_cards": <int>,
        "with_effect": <int>,
        "without_effect": <int>
      },
      "rows": [
        {"card_id": "...", "card_title": "...", "project_key": "..."}
      ]
    }

Exit codes:

    0  schoon OF (advisory-modus en >=1 hit)
    1  --strict en >=1 hit
    2  DB ontbreekt of is onleesbaar

Advisory by default, net als de andere sweepers.

Usage:
    scripts/sweep_self_improve_effect_claims.py [--db PATH] [--since YYYY-MM-DD] [--strict]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sweep_unchecked_implemented_markers import EFFECT_PATTERNS  # noqa: E402

SCHEMA_VERSION = 1
DEFAULT_DB = "~/.claude-registry/kanban.db"
DONE_COLUMN = "Done"

# Zelfde herkenning als `app.kanban.self_improve.is_self_improve_card`, maar in
# SQL-vorm: dit script draait zonder de backend-venv.
TITLE_MARKERS = ("[self-improve]", "[problem]")
LABEL_MARKERS = ("self-improve", "problem")


def _resolve_db_path(cli_arg: str | None) -> Path:
    if cli_arg:
        return Path(cli_arg).expanduser().resolve()
    env = os.environ.get("KANBAN_DB")
    if env:
        return Path(env).expanduser().resolve()
    return Path(DEFAULT_DB).expanduser().resolve()


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone() is not None


def _is_loop_card(title: str | None, raw_labels: str | None) -> bool:
    low = (title or "").lower()
    if any(m in low for m in TITLE_MARKERS):
        return True
    try:
        labels = json.loads(raw_labels or "[]")
    except (TypeError, ValueError):
        return False
    if not isinstance(labels, list):
        return False
    return any(str(x).lower() in LABEL_MARKERS for x in labels)


def _has_effect_claim(text: str) -> bool:
    return any(p.search(text) for p in EFFECT_PATTERNS)


def sweep(db_path: Path, since: str | None = None) -> dict:
    """Scan ``db_path`` en geef het rapport terug.

    Een ontbrekende tabel (verse DB) levert een schoon rapport op, geen crash.
    """
    if not db_path.exists() or not db_path.is_file():
        raise FileNotFoundError(f"kanban DB not found at: {db_path}")
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise RuntimeError(f"cannot open kanban DB {db_path}: {e}") from e

    report: dict = {
        "schema_version": SCHEMA_VERSION,
        "scanned_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "db_path": str(db_path),
        "since": since,
        "totals": {"done_loop_cards": 0, "with_effect": 0, "without_effect": 0},
        "rows": [],
    }
    try:
        if not _table_exists(con, "kanban_cards"):
            return report

        sql = (
            'SELECT id, title, labels, project_key, description '
            'FROM kanban_cards WHERE "column" = ?'
        )
        params: list = [DONE_COLUMN]
        if since:
            sql += " AND updated_at >= ?"
            params.append(since)

        has_ops = _table_exists(con, "kanban_ops")
        for cid, title, labels, project_key, description in con.execute(sql, params):
            if not _is_loop_card(title, labels):
                continue
            report["totals"]["done_loop_cards"] += 1
            text = description or ""
            if has_ops:
                rows = con.execute(
                    "SELECT payload FROM kanban_ops "
                    "WHERE entity_id = ? AND op_type = 'comment'",
                    (cid,),
                ).fetchall()
                text += "\n" + "\n".join(str(r[0] or "") for r in rows)
            if _has_effect_claim(text):
                report["totals"]["with_effect"] += 1
                continue
            report["totals"]["without_effect"] += 1
            report["rows"].append({
                "card_id": cid,
                "card_title": title,
                "project_key": project_key,
            })
        return report
    finally:
        con.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sweep_self_improve_effect_claims.py",
        description=(
            "Find Done [self-improve]/[problem] cards without an effect claim. "
            "JSON on stdout; exit 0 (advisory) / 1 (--strict + hits) / 2 (DB error)."
        ),
    )
    parser.add_argument("--db", default=None, help="pad naar kanban.db")
    parser.add_argument(
        "--since", default=None,
        help="alleen kaarten met updated_at >= deze ISO-datum (historic-grens)",
    )
    parser.add_argument("--strict", action="store_true", help="exit 1 bij >=1 hit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    db_path = _resolve_db_path(args.db)
    try:
        report = sweep(db_path, since=args.since)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    if report["totals"]["without_effect"] and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
