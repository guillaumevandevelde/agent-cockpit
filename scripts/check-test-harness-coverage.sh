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
#   - A literal `scripts/test_*.sh` (with `*`) in the `# Test` block is
#     treated as a family-level reference — it implicitly covers every
#     on-disk `scripts/test_*.sh`. This matches the "family-level" idiom
#     already in use on line 78 of CLAUDE.md and lets the block stay
#     concise as new harnesses are added. Self-improve card 8c7cfc14
#     tracked the gap where the regex below didn't recognize that form
#     and falsely reported 19 drift items. The phantom direction is
#     unaffected: a specific name listed in `# Test` but missing on disk
#     still fires.
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
# Carve-out list: some harnesses intentionally live OUTSIDE the `# Test`
# block (supervisor section, feature-specific docs) and we don't want
# the guard to flag them as missing-from-CLAUDE.md. The list below
# declares those exceptions in one place — visible intent, easy to
# audit, and master can stay CLEAN with `--strict` enabled. Each entry
# must carry a one-line rationale. Adding an entry that doesn't justify
# itself is the failure mode this list prevents (cf. the card's complaint
# about "guarded but inert").
#
#   test_cockpit.sh             — supervisor harness, lives in the
#                                 Self-healing dev stack section
#   test_measure_token_saver.sh — feature harness for the token-saver
#                                 meet-recept, documented in
#                                 docs/cockpit/token-saver-meet-harnas.md §4
#
# The carve-out only suppresses direction A (missing-from-CLAUDE.md).
# Direction B (phantom-in-CLAUDE.md, i.e. a name listed in `# Test` that
# doesn't exist on disk) still fires for carved names — if you accidentally
# start listing `test_cockpit.sh` in `# Test` while it's been deleted, you
# want to know.
#
# Override with the CARVE_OUTS env var (space-separated basenames, no
# `scripts/` prefix). Used by the test harness to exercise the path
# without editing this file.
#
# Advisory by design: exits 0 when drift is found and prints a warning.
# Pass --strict to exit 1 on drift.
#
# Usage:
#   bash scripts/check-test-harness-coverage.sh [--strict]
#   # Defaults to <repo>/CLAUDE.md and <repo>/scripts. Override with
#   # CLAUDE_MD / SCRIPTS_DIR / CARVE_OUTS env vars (used by the test harness).
#
# Env:
#   CLAUDE_MD  path to CLAUDE.md to scan (default: <repo>/CLAUDE.md)
#   SCRIPTS_DIR  directory whose test_*.sh files are checked
#                (default: <repo>/scripts)
#   CARVE_OUTS  space-separated basenames to exclude from missing-from-
#               CLAUDE.md drift (default: the hardcoded list above)

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
      sed -n '2,82p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
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
# Match `scripts/test_<name>.sh` (specific name) OR `scripts/test_*.sh`
# (family-level wildcard) as a substring so e.g. `bash scripts/test_X.sh`,
# `scripts/test_X.sh ...`, and the family-level `scripts/test_*.sh`
# all register as a reference. Capture just the basename for the
# comparison; the `test_*.sh` form is split out below as a family marker.
# `|| true` swallows grep's exit-1 when there are zero matches — under
# `set -o pipefail` this would otherwise abort the script on empty
# fixtures (carve-out scenarios: the `# Test` block is intentionally
# empty when the only disk harnesses are carved out).
LISTED=$(printf '%s\n' "$TEST_BLOCK" \
  | { grep -oE 'scripts/test_(\*|[A-Za-z0-9_-]+)\.sh' || true; } \
  | sed 's|^scripts/||' \
  | sort -u)

# A literal `test_*.sh` entry in the # Test block is a family-level
# reference: it implicitly covers every `scripts/test_*.sh` on disk.
# Drop it from LISTED before the per-basename phantom check (no file is
# literally named `test_*.sh` on disk, so leaving it in would misfire as
# a phantom), and remember it so MISSING_FROM_CLAUDE short-circuits for
# every on-disk harness of this family.
FAMILY_GLOB_REFERENCED=0
if printf '%s\n' "$LISTED" | grep -qxF 'test_*.sh'; then
  FAMILY_GLOB_REFERENCED=1
  LISTED=$(printf '%s\n' "$LISTED" | grep -vxF 'test_*.sh' || true)
fi

# --- collect harnases on disk -------------------------------------------
ON_DISK=$(find "$SCRIPTS_DIR" -maxdepth 1 -type f -name 'test_*.sh' \
  -printf '%f\n' \
  | sort -u)

# --- carve-out list -------------------------------------------------------
# Default: basenames of scripts/test_*.sh that intentionally live outside
# the `# Test` block (supervisor section, feature-specific docs). Mirrors
# the rationale documented in the script header. CARVE_OUTS env var
# overrides the default for the test harness.
DEFAULT_CARVE_OUTS=(test_cockpit.sh test_measure_token_saver.sh)

if [ "${CARVE_OUTS+x}" = x ]; then
  # CARVE_OUTS is set (including to the empty string). Split on whitespace.
  # An empty value disables the carve-out entirely — useful when a future
  # operator wants the guard to be strict-by-default.
  if [ -z "$CARVE_OUTS" ]; then
    CARVE_OUT_ARR=()
  else
    # shellcheck disable=SC2206  # intentional word-split on whitespace
    CARVE_OUT_ARR=($CARVE_OUTS)
  fi
else
  CARVE_OUT_ARR=("${DEFAULT_CARVE_OUTS[@]}")
fi

is_carved_out() {
  local target="$1"
  local entry
  for entry in "${CARVE_OUT_ARR[@]}"; do
    if [ "$entry" = "$target" ]; then
      return 0
    fi
  done
  return 1
}

# --- diff in both directions ---------------------------------------------
MISSING_FROM_CLAUDE=()
if [ "$FAMILY_GLOB_REFERENCED" -eq 1 ]; then
  # Family-level `scripts/test_*.sh` reference covers every on-disk
  # harness of this family. Nothing can be missing-from-CLAUDE.md for
  # the family; the phantom direction below still catches specific
  # names listed in # Test that don't exist on disk.
  :
else
  while IFS= read -r harness; do
    [ -z "$harness" ] && continue
    if ! printf '%s\n' "$LISTED" | grep -qxF "$harness"; then
      MISSING_FROM_CLAUDE+=("$harness")
    fi
  done <<< "$ON_DISK"
fi

# Apply the carve-out: a basename in CARVE_OUT_ARR is silently dropped from
# the missing-from-CLAUDE.md list. The phantom direction (next block) is
# untouched — a carved name that *is* in `# Test` but doesn't exist on
# disk still surfaces as drift, because CLAUDE.md is the source of truth
# for the # Test block.
if [ "${#CARVE_OUT_ARR[@]}" -gt 0 ]; then
  FILTERED_MISSING=()
  for harness in "${MISSING_FROM_CLAUDE[@]}"; do
    if is_carved_out "$harness"; then
      continue
    fi
    FILTERED_MISSING+=("$harness")
  done
  MISSING_FROM_CLAUDE=("${FILTERED_MISSING[@]}")
fi

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
  # Build a count of the harness set actually being reported on:
  #   listed in `# Test`     + missing-from-# Test but carved out
  # Carved-out harnesses are intentionally NOT in `# Test` but are also
  # not drift, so they belong in the "covered" total. The plain
  # ON_DISK count would over- or under-report against either of those
  # sets; the union is what the operator wants to see.
  # Count non-empty lines so empty LISTED doesn't count its trailing
  # newline as a "line".
  listed_count=0
  if [ -n "$LISTED" ]; then
    listed_count=$(printf '%s\n' "$LISTED" | wc -l)
  fi
  carved_in_disk=0
  on_disk_count=0
  for h in $ON_DISK; do
    on_disk_count=$((on_disk_count + 1))
    if is_carved_out "$h"; then
      carved_in_disk=$((carved_in_disk + 1))
    fi
  done
  if [ "$FAMILY_GLOB_REFERENCED" -eq 1 ]; then
    # Family-level `scripts/test_*.sh` covers every on-disk harness; the
    # covered set is the whole family. Carve-outs are already on disk, so
    # on_disk_count already includes them — no separate addition.
    printf 'OK: %d test harness(es) covered (CLAUDE.md # Test family-level glob + carve-outs)\n' \
      "$on_disk_count"
  else
    printf 'OK: %d test harness(es) covered (CLAUDE.md # Test + carve-outs)\n' \
      "$(( listed_count + carved_in_disk ))"
  fi
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