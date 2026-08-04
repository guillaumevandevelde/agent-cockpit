#!/usr/bin/env bash
# Validate kanban-DB string conventions (docs/cockpit/kanban-conventions.md §1).
#
# For every project that has any `kanban_columns` row (i.e. kanban enabled),
# assert that EVERY name in the canonical `COLUMNS` list from
# backend/app/kanban/schemas.py has a matching row. Catches the
# "project-enabled-BEFORE-a-column-was-added"-class of stale-column bugs
# before they ship — without this check, the column silently disappears
# from the board until the project is re-enabled or the matching
# `ensure_<name>_column` helper runs.
#
# Exit code: 0 when clean, 1 when any project has missing fixed columns.
# The script is read-only against the DB (no schema changes, no writes).
#
# Usage:
#   bash scripts/check-kanban-conventions.sh                 # auto-discover DB
#   bash scripts/check-kanban-conventions.sh /path/to/db.sqlite
#
# DB-path resolution chain (first that resolves to a real file wins):
#   1. The argument path, when given and present on disk.
#   2. KANBAN_DB env var, when set and present on disk.
#   3. MAIN_DB_PATH env var, when set and present on disk (escape hatch
#      for setups where the main checkout lives at a non-default path).
#   4. The default kanban DB at ~/.claude-registry/kanban.db (anchored by
#      backend/app/config.py:_default_kanban_database_url). One board per
#      machine; worktrees share it.
#   5. git-common-dir discovery: walk `git rev-parse --git-common-dir` to
#      the repo root and look for `backend/claude_registry.db` there as a
#      legacy fallback (kept for backwards-compat with older setups that
#      colocated the board in the repo). Without this + (4), the script
#      would silently skip in every dispatched worktree session — exactly
#      the gap kanban card 71e88ac2 documented.
#   6. Nothing found → skip (exit 0) instead of failing, so a fresh clone
#      or an unrelated CI checkout doesn't break the build.
#
# Set KANBAN_CONVENTIONS_QUIET=1 to suppress per-project output and only
# print the summary line (handy in CI).

set -euo pipefail

# --- arg parsing -----------------------------------------------------------
for arg in "$@"; do
  case "$arg" in
    -h|--help)
      sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --)
      # trailing sentinel; anything after is treated literally below
      ;;
  esac
done

QUIET="${KANBAN_CONVENTIONS_QUIET:-0}"

# --- resolve the DB path ---------------------------------------------------
DB_PATH=""
candidate="${1:-}"
if [ -n "$candidate" ] && [ -f "$candidate" ]; then
  DB_PATH="$candidate"
elif [ -n "${KANBAN_DB:-}" ] && [ -f "$KANBAN_DB" ]; then
  DB_PATH="$KANBAN_DB"
elif [ -n "${MAIN_DB_PATH:-}" ] && [ -f "$MAIN_DB_PATH" ]; then
  DB_PATH="$MAIN_DB_PATH"
else
  # Default: the board DB at ~/.claude-registry/kanban.db. Mirrors
  # backend/app/config.py:_default_kanban_database_url so the script
  # validates the same store the app reads from. One board per machine
  # is the design — worktrees share it.
  if [ -n "${HOME:-}" ] && [ -f "$HOME/.claude-registry/kanban.db" ]; then
    DB_PATH="$HOME/.claude-registry/kanban.db"
  fi
fi

# Last-resort fallback: walk to the main checkout via git-common-dir and
# look for backend/claude_registry.db there. Works from a bare checkout,
# from any subdir of the main checkout, and from inside a linked worktree
# (where git-common-dir still points at the main checkout's .git).
# Legacy path kept for backwards-compat with older colocated setups; the
# default above already handles modern per-machine-anchored boards.
if [ -z "$DB_PATH" ] && command -v git >/dev/null 2>&1; then
  if common_dir="$(git rev-parse --git-common-dir 2>/dev/null)" \
     && [ -n "$common_dir" ]; then
    abs_common="$(cd "$common_dir" 2>/dev/null && pwd -P)" || abs_common=""
    if [ -n "$abs_common" ]; then
      main_root="$(dirname "$abs_common")"
      candidate="$main_root/backend/claude_registry.db"
      [ -f "$candidate" ] && DB_PATH="$candidate"
    fi
  fi
fi

if [ -z "$DB_PATH" ]; then
  echo "check-kanban-conventions: no kanban DB found — skipping (run uvicorn once to create it, set KANBAN_DB or MAIN_DB_PATH, or pass a path)." >&2
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
FIXED_COLUMNS = ["Backlog", "Impediment", "Done", "To Resume"]

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
