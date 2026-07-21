#!/usr/bin/env bash
#
# check-test-harness-coverage.sh — advisory drift check between CLAUDE.md's
# `# Test` block and the on-disk `scripts/test_*.sh` harnesses.
#
# Background: kanban card 5e988e4e documented that 6 of 14 test harnesses
# under scripts/test_*.sh were never referenced from CLAUDE.md. CLAUDE.md's
# `# Test` block is the de-facto index of bash harnesses for dispatched
# agents (there is no runner that walks scripts/test_*.sh as a whole), so
# a harness that isn't listed there is effectively inert: the guard
# exists, but no agent ever invokes it. This script flags that drift so
# the gap can't recur silently — same shape as check-doc-links.sh and
# check-decision-register.sh.
#
# Two directions of drift are checked:
#
#   A. **Missing from CLAUDE.md** — a `scripts/test_<name>.sh` exists on
#      disk but the basename does not appear as `scripts/test_<name>.sh`
#      in CLAUDE.md. (The card's headline symptom.)
#
#   B. **Phantom in CLAUDE.md** — CLAUDE.md mentions `scripts/test_<name>.sh`
#      but no such file exists. Catches stale references after a harness
#      is renamed or deleted, so a typo or rename doesn't leave a broken
#      command in the test block.
#
# Both directions are flagged in the same run; exit code reflects the
# overall worst case (any drift → 1 under `--strict`, 0 otherwise).
#
# Notes on what is matched:
#   - The pattern `scripts/test_<name>.sh` is anchored on the path prefix
#     `scripts/test_` so CLAUDE.md prose like "test the foo bar" doesn't
#     false-match. The on-disk glob is `scripts/test_*.sh` for the same
#     reason.
#   - The bash harness `bash backend/test_commands_api.sh` (in the `# Test`
#     block, not under `scripts/`) is **not** covered by this check —
#     its scope is intentionally limited to the `scripts/test_*.sh`
#     family. Extending it is out of scope; if that harness drifts,
#     handle it via a separate check.
#   - The `# Test` block is identified by scanning for the line starting
#     `# Test` followed by content lines until the next `# ` heading or
#     end-of-file. Anything outside that block (e.g. an incidental mention
#     in a description elsewhere in CLAUDE.md) does NOT count.
#
# Advisory by design: exits 0 when drift is found and prints a warning.
# Pass --strict to exit 1 on drift.
#
# Usage:
#   bash scripts/check-test-harness-coverage.sh [--strict]
#   # Defaults to <repo>/CLAUDE.md and <repo>/scripts. Override with
#   # CLAUDE_MD / SCRIPTS_DIR env vars (used by the test harness).
#
# Env:
#   CLAUDE_MD  path to CLAUDE.md to scan (default: <repo>/CLAUDE.md)
#   SCRIPTS_DIR  directory whose test_*.sh files are checked
#                (default: <repo>/scripts)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

CLAUDE_MD="${CLAUDE_MD:-$REPO_ROOT/CLAUDE.md}"
SCRIPTS_DIR="${SCRIPTS_DIR:-$REPO_ROOT/scripts}"

STRICT=0
for arg in "$@"; do
  case "$arg" in
    --strict) STRICT=1 ;;
    --help|-h)
      sed -n '2,50p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    "") ;;
    *)
      echo "ERROR: unknown argument '$arg' (see --help)" >&2
      exit 2
      ;;
  esac
done

if [ ! -f "$CLAUDE_MD" ]; then
  echo "ERROR: CLAUDE.md not found at $CLAUDE_MD" >&2
  exit 2
fi
if [ ! -d "$SCRIPTS_DIR" ]; then
  echo "ERROR: scripts directory not found at $SCRIPTS_DIR" >&2
  exit 2
fi

# --- extract the `# Test` block -------------------------------------------
# Starts at a line beginning with `# Test` (with optional trailing space or
# colon) and continues until the next `# <word>` heading or end-of-file.
# We deliberately do NOT match lines starting with `## Test` — those are
# sub-headings and shouldn't claim the block.
TEST_BLOCK=$(awk '
  BEGIN { in_block = 0 }
  /^# Test([[:space:]].*)?$/ { in_block = 1; next }
  in_block && /^# /          { in_block = 0 }
  in_block                   { print }
' "$CLAUDE_MD")

# --- collect harnases referenced in the block ----------------------------
# Match `scripts/test_<name>.sh` as a substring so e.g. `bash scripts/test_X.sh`
# and `scripts/test_X.sh ...` both register as a reference. Capture just the
# basename for the comparison.
LISTED=$(printf '%s\n' "$TEST_BLOCK" \
  | grep -oE 'scripts/test_[A-Za-z0-9_-]+\.sh' \
  | sed 's|^scripts/||' \
  | sort -u)

# --- collect harnases on disk -------------------------------------------
ON_DISK=$(find "$SCRIPTS_DIR" -maxdepth 1 -type f -name 'test_*.sh' \
  -printf '%f\n' \
  | sort -u)

# --- diff in both directions ---------------------------------------------
MISSING_FROM_CLAUDE=()
while IFS= read -r harness; do
  [ -z "$harness" ] && continue
  if ! printf '%s\n' "$LISTED" | grep -qxF "$harness"; then
    MISSING_FROM_CLAUDE+=("$harness")
  fi
done <<< "$ON_DISK"

PHANTOM_IN_CLAUDE=()
while IFS= read -r harness; do
  [ -z "$harness" ] && continue
  if ! printf '%s\n' "$ON_DISK" | grep -qxF "$harness"; then
    PHANTOM_IN_CLAUDE+=("$harness")
  fi
done <<< "$LISTED"

total_drift=$((${#MISSING_FROM_CLAUDE[@]} + ${#PHANTOM_IN_CLAUDE[@]}))

# --- report ---------------------------------------------------------------
if [ "$total_drift" -eq 0 ]; then
  printf 'OK: %d test harness(es) in CLAUDE.md # Test block match scripts/test_*.sh on disk\n' \
    "$(printf '%s\n' "$ON_DISK" | wc -l)"
  exit 0
fi

if [ "${#MISSING_FROM_CLAUDE[@]}" -gt 0 ]; then
  echo "WARNING: ${#MISSING_FROM_CLAUDE[@]} test harness(es) exist on disk but are not listed in CLAUDE.md # Test:" >&2
  for h in "${MISSING_FROM_CLAUDE[@]}"; do
    echo "  - scripts/$h" >&2
  done
fi

if [ "${#PHANTOM_IN_CLAUDE[@]}" -gt 0 ]; then
  echo "WARNING: ${#PHANTOM_IN_CLAUDE[@]} test harness(es) listed in CLAUDE.md # Test do not exist on disk:" >&2
  for h in "${PHANTOM_IN_CLAUDE[@]}"; do
    echo "  - scripts/$h" >&2
  done
fi

if [ "$STRICT" -eq 1 ]; then
  echo "ERROR: $total_drift drift item(s) — see above (run without --strict to ignore)" >&2
  exit 1
fi

echo "WARNING: $total_drift CLAUDE.md / scripts/test_*.sh drift item(s) — re-run with --strict to fail" >&2
exit 0