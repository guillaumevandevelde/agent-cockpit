#!/usr/bin/env python3
"""Scan kanban_columns for (default_provider, default_model) pairs the API refuses.

`update_column` co-validates the pair server-side: for a provider with a
model-options cache, a model outside that cache is rejected with 422 (see
`_allowed_models_for_provider` in backend/app/api/v1/kanban/router.py). That
guard only covers NEW writes — rows persisted before it landed were never
migrated, and a stale row is worse than a rejected one:

  * the dispatcher falls through to an unrelated model at spawn time (the
    original "minimax column stuck on opus" report), and
  * the column-settings dialog loads the invalid model into the form, so
    every Save comes back 422 — the column cannot be repaired through the UI.

This sweeper finds those rows. `--fix` clears `default_model` to NULL, which
means "let the dispatch chain choose"; for minimax that resolves to
MINIMAX_DEFAULT_MODEL (backend/app/services/agentic_cli/provider_env.py).
Clearing beats guessing a replacement: we know what the user CANNOT have had,
not what they wanted.

Providers without a model-options cache (e.g. bedrock, whose ARN-shaped ids
are never bare aliases) accept any string and are skipped — matching the
backend, which returns None for them.

Output: a single JSON document on stdout, mirroring the sibling sweepers
(sweep_dangling_plan_refs.py, sweep_dangling_depends_on.py). Schema:

    {
      "schema_version": 1,
      "scanned_at": "<ISO-8601 UTC>",
      "db_path": "<absolute path>",
      "fixed": <bool — whether --fix was applied>,
      "totals": {
        "columns_scanned": <int>,
        "invalid": <int>,
        "by_provider": {"<provider>": <int>, ...}
      },
      "rows": [
        {
          "column_id": "...",
          "project_key": "...",
          "name": "engineer",
          "default_provider": "minimax",
          "default_model": "opus",
          "known_options": ["MiniMax-M3"],
          "reason": "<human-readable>",
          "fixed": <bool>
        }
      ]
    }

Valid columns are silently omitted. Exit codes:

    0  clean OR (advisory mode and >=1 hit)
    1  --strict and >=1 hit that was not fixed
    2  usage error, DB missing/unreadable, or sqlite query failed

Advisory by default -- "signal, not gate", same posture as
scripts/check-analysis-outcomes.sh.

Usage:
    scripts/sweep_invalid_column_models.py [--db PATH] [--strict] [--fix]
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

DEFAULT_DB = "~/.claude-registry/kanban.db"

# Provider -> (kanban_meta cache key, seed used when the cache is absent).
# Mirrors `_allowed_models_for_provider` in the kanban router and the
# *_MODEL_OPTIONS_SEED constants in backend/app/kanban/dispatch.py. A
# provider absent here has no closed set: any model string is accepted, so
# there is nothing to sweep.
PROVIDER_MODEL_CACHES: dict[str, tuple[str, tuple[str, ...]]] = {
    "minimax": ("model_options:minimax", ("MiniMax-M3",)),
    "anthropic": ("model_options:claude-code", ("sonnet", "opus", "haiku")),
}


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

    A fresh-DB fixture (zero-byte sqlite file) has no kanban tables yet;
    that must surface as a clean report, not an OperationalError crash.
    """
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _known_options(con: sqlite3.Connection, provider: str) -> list[str] | None:
    """Allowed models for ``provider``, or None when any string is allowed.

    Falls back to the seed when the cache row is missing, empty, or holds
    unparseable JSON — the same "never return an empty list" guard the
    backend's get_cached_*_model_options helpers apply, so a never-refreshed
    board doesn't report every column as invalid.
    """
    entry = PROVIDER_MODEL_CACHES.get(provider)
    if entry is None:
        return None
    key, seed = entry
    if not _table_exists(con, "kanban_meta"):
        return list(seed)
    row = con.execute(
        "SELECT value FROM kanban_meta WHERE key=?", (key,),
    ).fetchone()
    if row is None or not row[0]:
        return list(seed)
    try:
        options = json.loads(row[0])
    except (TypeError, ValueError):
        return list(seed)
    if not isinstance(options, list) or not options:
        return list(seed)
    return [str(o) for o in options]


def sweep(db_path: Path, fix: bool = False) -> dict:
    """Scan every column; optionally clear invalid default_model values."""
    if not db_path.exists():
        raise FileNotFoundError(f"kanban DB not found at {db_path}")

    try:
        con = sqlite3.connect(str(db_path))
    except sqlite3.Error as e:  # pragma: no cover - unreadable file
        raise RuntimeError(f"cannot open {db_path}: {e}") from e

    report: dict = {
        "schema_version": SCHEMA_VERSION,
        "scanned_at": datetime.now(UTC).isoformat(),
        "db_path": str(db_path),
        "fixed": bool(fix),
        "totals": {"columns_scanned": 0, "invalid": 0, "by_provider": {}},
        "rows": [],
    }

    try:
        if not _table_exists(con, "kanban_columns"):
            return report

        try:
            rows = con.execute(
                "SELECT id, project_key, name, default_provider, default_model "
                "FROM kanban_columns",
            ).fetchall()
        except sqlite3.Error as e:
            raise RuntimeError(f"query failed on {db_path}: {e}") from e

        report["totals"]["columns_scanned"] = len(rows)
        options_cache: dict[str, list[str] | None] = {}

        for column_id, project_key, name, provider, model in rows:
            # Either side null = nothing to co-validate. A null model already
            # means "defer to the dispatch chain"; a null provider means the
            # chain picks the vendor and re-validates at spawn time.
            if not provider or not model:
                continue
            if provider not in options_cache:
                options_cache[provider] = _known_options(con, provider)
            allowed = options_cache[provider]
            if allowed is None or model in allowed:
                continue

            if fix:
                try:
                    con.execute(
                        "UPDATE kanban_columns SET default_model=NULL WHERE id=?",
                        (column_id,),
                    )
                except sqlite3.Error as e:
                    raise RuntimeError(f"fix failed on {db_path}: {e}") from e

            report["rows"].append({
                "column_id": column_id,
                "project_key": project_key,
                "name": name,
                "default_provider": provider,
                "default_model": model,
                "known_options": allowed,
                "reason": (
                    f"model {model!r} is not valid for provider {provider!r}; "
                    f"known options: {allowed}"
                ),
                "fixed": bool(fix),
            })
            by_provider = report["totals"]["by_provider"]
            by_provider[provider] = by_provider.get(provider, 0) + 1

        report["totals"]["invalid"] = len(report["rows"])
        if fix and report["rows"]:
            con.commit()
        return report
    finally:
        con.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sweep_invalid_column_models.py",
        description=(
            "Find kanban_columns rows whose (default_provider, default_model) "
            "pair the API would reject with 422. Emits a JSON report on "
            "stdout and exits 0 (advisory) / 1 (with --strict + unfixed hits) "
            "/ 2 (DB or query error). Run via the bash test harness "
            "scripts/test_sweep_invalid_column_models.sh for the contract."
        ),
    )
    parser.add_argument(
        "--db",
        default=None,
        help=(
            "Path to kanban.db. Defaults to $KANBAN_DB or "
            f"{DEFAULT_DB}. The bash test harness always overrides this so "
            "the real board is untouched."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit 1 when any invalid pair remains. Default is advisory: "
            "exit 0 even with hits."
        ),
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help=(
            "Clear default_model to NULL on every invalid row, so the "
            "dispatch chain picks the model. Writes to the DB; without it "
            "the sweeper is read-only."
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
        report = sweep(db_path, fix=args.fix)
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

    # --fix resolves the hits, so a fixed run is not a failure even in strict
    # mode; only rows still standing should block a pipeline.
    if args.strict and report["totals"]["invalid"] and not args.fix:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
