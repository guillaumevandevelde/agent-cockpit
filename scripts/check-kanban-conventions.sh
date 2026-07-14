#!/usr/bin/env bash
# Validate kanban-DB string conventions (docs/cockpit/kanban-conventions.md §1).
#
# For every project that has any `kanban_columns` row (i.e. kanban enabled),
# assert that EVERY name in the canonical `COLUMNS` list from
# backend/app/kanban/schemas.py has a matching row. Catches the
# "project-enabled-BEFORE-`intake`-was-added"-class of stale-column bugs
# before they ship — without this check, the column silently disappears
# from the board until the project is re-enabled or the matching
# `ensure_<name>_column` helper runs.
#
# Exit code: 0 when clean, 1 when any project has missing fixed columns.
# The script is read-only against the DB (no schema changes, no writes).
#
# Usage:
#   bash scripts/check-kanban-conventions.sh                 # default DB path
#   bash scripts/check-kanban-conventions.sh /path/to/db.sqlite
#
# Set KANBAN_CONVENTIONS_QUIET=1 to suppress per-project output and only
# print the summary line (handy in CI).

set -euo pipefail

DB_PATH="${1:-backend/claude_registry.db}"
QUIET="${KANBAN_CONVENTIONS_QUIET:-0}"

if [ ! -f "$DB_PATH" ]; then
  echo "check-kanban-conventions: DB '$DB_PATH' not found — skipping (run uvicorn once to create it, or pass a path)." >&2
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-kanban-conventions: python3 not on PATH — skipping." >&2
  exit 0
fi

# Inline python keeps the script portable: no dependency on the `sqlite3`
# CLI (which is missing on minimal Windows / WSL distros) and lets us reuse
# the canonical `COLUMNS` list shape via a heredoc rather than parsing the
# Python source with regex.
result=$(KANBAN_CONVENTIONS_QUIET="$QUIET" python3 - "$DB_PATH" <<'PY'
import os
import sqlite3
import sys

db_path = sys.argv[1]
quiet = os.environ.get("KANBAN_CONVENTIONS_QUIET", "0") == "1"

# Mirror of backend/app/kanban/schemas.py COLUMNS. Keep in sync via the
# docs/cockpit/kanban-conventions.md §1 contract — if you change one,
# change both.
FIXED_COLUMNS = ["intake", "Backlog", "Impediment", "Done", "To Resume"]

conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
try:
    # Projects that have at least one kanban_columns row = "kanban enabled
    # for this project". Projects without any row are skipped — they were
    # never enabled, and `POST /enable` is what creates the rows.
    cur = conn.execute(
        "SELECT project_key, name FROM kanban_columns ORDER BY project_key, name"
    )
    by_project: dict[str, set[str]] = {}
    for project_key, name in cur.fetchall():
        by_project.setdefault(project_key, set()).add(name)
finally:
    conn.close()

missing_total = 0
projects = sorted(by_project)
for project_key in projects:
    present = by_project[project_key]
    missing = [c for c in FIXED_COLUMNS if c not in present]
    if missing:
        missing_total += len(missing)
        if not quiet:
            print(
                f"[stale] {project_key}: missing fixed columns: {', '.join(missing)}",
                file=sys.stderr,
            )
    else:
        if not quiet:
            print(f"[ok]    {project_key}: all {len(FIXED_COLUMNS)} fixed columns present")

if missing_total == 0:
    print(f"check-kanban-conventions: clean ({len(projects)} project(s) checked)")
    sys.exit(0)
else:
    print(
        f"check-kanban-conventions: {missing_total} missing fixed-column row(s) "
        f"across {len(projects)} project(s) — see docs/cockpit/kanban-conventions.md §1",
        file=sys.stderr,
    )
    sys.exit(1)
PY
)
status=$?

# python already printed its own status lines; just propagate exit code.
echo "$result"
exit $status
