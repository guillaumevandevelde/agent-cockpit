#!/usr/bin/env bash
#
# check-superpowers-promotions.sh — advisory drift check for the spec-boom SSOT.
#
# Verifies that every work-output file under docs/superpowers/{plans,specs}/ is
# registered in the promotion ledger (docs/superpowers/README.md). It does NOT
# verify that a promotion is *content-correct* — only that each piece of work
# has a deliberate, visible status (avoids the "link theater" trap called out in
# docs/cockpit/spec-driven-development-analysis.md §7).
#
# Advisory by design: exits 0 even when files are unregistered (prints a warning),
# mirroring the "signal, not gate" recommendation (§4 option C). Pass --strict to
# exit 1 on any unregistered file (e.g. to harden into a blocking CI step later).
#
# Usage:
#   scripts/check-superpowers-promotions.sh [--strict]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SP_DIR="$REPO_ROOT/docs/superpowers"
LEDGER="$SP_DIR/README.md"

STRICT=0
[ "${1:-}" = "--strict" ] && STRICT=1

if [ ! -f "$LEDGER" ]; then
  echo "ERROR: ledger not found at $LEDGER" >&2
  exit 2
fi

missing=()
while IFS= read -r -d '' f; do
  base="$(basename "$f")"
  [ "$base" = "README.md" ] && continue
  if ! grep -qF "$base" "$LEDGER"; then
    missing+=("${f#"$REPO_ROOT"/}")
  fi
done < <(find "$SP_DIR/plans" "$SP_DIR/specs" -type f -name '*.md' -print0 2>/dev/null)

if [ "${#missing[@]}" -eq 0 ]; then
  echo "OK: every docs/superpowers/{plans,specs} file is registered in the promotion ledger."
  exit 0
fi

echo "WARNING: ${#missing[@]} superpowers file(s) not registered in docs/superpowers/README.md:" >&2
for m in "${missing[@]}"; do
  echo "  - $m" >&2
done
echo "" >&2
echo "Add a row to the promotion ledger in docs/superpowers/README.md (status ⏳ or ✅)." >&2

if [ "$STRICT" -eq 1 ]; then
  exit 1
fi
echo "(advisory — not failing the build; run with --strict to enforce)" >&2
exit 0
