#!/usr/bin/env bash
#
# cleanup-test-projects.sh — purge test-created rows from the `projects`
# table in backend/claude_registry.db.
#
# test_mcp_server.py::test_mcp_tool_list_projects exercises the MCP tool
# layer against the real app DB (not an isolated test DB), so it — and any
# future test following the same convention — creates rows named
# "mcp-test-<uuid>" pathed under "/tmp/test-<uuid>". conftest.py now sweeps
# these automatically after every `pytest` run; this script is for
# retroactive cleanup of rows left over from before that fix, or for ad-hoc
# use outside of pytest.
#
# Usage:
#   scripts/cleanup-test-projects.sh            # dry-run: show what WOULD be removed (default)
#   scripts/cleanup-test-projects.sh --apply     # actually delete matching rows
#   scripts/cleanup-test-projects.sh -h|--help
#
set -euo pipefail

APPLY=0
for arg in "$@"; do
  case "$arg" in
    --apply|--yes|-y) APPLY=1 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | sed '/^!/d'
      exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -z "$ROOT" ] && ROOT="$(cd "$(dirname "$(git rev-parse --git-common-dir)")" && pwd)"
DB="$ROOT/backend/claude_registry.db"

if [ ! -f "$DB" ]; then
  echo "cleanup-test-projects: no DB at $DB — nothing to do."
  exit 0
fi

MODE=count APPLY="$APPLY" DB="$DB" python3 - <<'PY'
import os
import sqlite3

db_path = os.environ["DB"]
apply = os.environ["APPLY"] == "1"

con = sqlite3.connect(db_path, timeout=10)
try:
    cur = con.execute(
        "SELECT id, name, path FROM projects "
        "WHERE name LIKE 'mcp-test-%' OR path LIKE '/tmp/test-%' "
        "ORDER BY id"
    )
    rows = cur.fetchall()

    if not rows:
        print("cleanup-test-projects: no leftover test projects found.")
        raise SystemExit(0)

    for row_id, name, path in rows:
        prefix = "REMOVED" if apply else "WOULD-REMOVE"
        print(f"{prefix:13} id={row_id:<5} {name}  ({path})")

    if apply:
        con.execute(
            "DELETE FROM projects "
            "WHERE name LIKE 'mcp-test-%' OR path LIKE '/tmp/test-%'"
        )
        con.commit()
        print(f"\ncleanup-test-projects: removed {len(rows)} row(s).")
    else:
        print(f"\ncleanup-test-projects: {len(rows)} row(s) to remove. Re-run with --apply to delete them.")
finally:
    con.close()
PY
