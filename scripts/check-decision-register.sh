#!/usr/bin/env bash
#
# check-decision-register.sh — advisory drift check for the decision register.
#
# Verifies that every decision document (docs/cockpit/*-decision.md) is linked
# from the canonical register (docs/cockpit/decisions.md). It does NOT verify
# that the register row is content-correct — only that each decision has a
# visible, browsable entry, which is the discoverability gap the register was
# created to close (see docs/cockpit/decisions.md, "Waarom dit bestaat").
#
# Scope: the *-decision.md naming convention only. The register also indexes
# spikes and analysis docs that carry a verdict (spike-*.md, *-analyse.md), but
# those names don't reliably distinguish "has a decision" from "is background
# reading", so enforcing them would produce false positives. Adding those rows
# stays a human call; this check guards the unambiguous class.
#
# Advisory by design: exits 0 even when docs are unlinked (prints a warning),
# mirroring check-superpowers-promotions.sh ("signal, not gate"). Pass --strict
# to exit 1 on any unlinked doc (e.g. to harden into a blocking CI step later).
#
# Usage:
#   scripts/check-decision-register.sh [--strict]
#
# Env:
#   DECISIONS_DIR   directory holding decisions.md + *-decision.md
#                   (default: <repo>/docs/cockpit; overridden by the tests)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DECISIONS_DIR="${DECISIONS_DIR:-$REPO_ROOT/docs/cockpit}"
REGISTER="$DECISIONS_DIR/decisions.md"

STRICT=0
case "${1:-}" in
  --strict) STRICT=1 ;;
  --help|-h)
    sed -n '3,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  "") ;;
  *)
    echo "ERROR: unknown argument '$1' (see --help)" >&2
    exit 2
    ;;
esac

if [ ! -f "$REGISTER" ]; then
  echo "ERROR: decision register not found at $REGISTER" >&2
  echo "Create it, or point DECISIONS_DIR at the directory that holds decisions.md." >&2
  exit 2
fi

missing=()
while IFS= read -r -d '' f; do
  base="$(basename "$f")"
  if ! grep -qF "$base" "$REGISTER"; then
    rel="${f#"$REPO_ROOT"/}"
    missing+=("$rel")
  fi
done < <(find "$DECISIONS_DIR" -maxdepth 1 -type f -name '*-decision.md' -print0 2>/dev/null | sort -z)

if [ "${#missing[@]}" -eq 0 ]; then
  echo "OK: every docs/cockpit/*-decision.md is linked from the decision register."
  exit 0
fi

echo "WARNING: ${#missing[@]} decision doc(s) not linked from docs/cockpit/decisions.md:" >&2
for m in "${missing[@]}"; do
  echo "  - $m" >&2
done
echo "" >&2
echo "Add a row to the register in docs/cockpit/decisions.md (newest first):" >&2
echo "  | <datum> | <vraag> | <uitkomst in één zin> | [\`<doc>.md\`](./<doc>.md) | <kaart-id> |" >&2

if [ "$STRICT" -eq 1 ]; then
  exit 1
fi
echo "(advisory — not failing the build; run with --strict to enforce)" >&2
exit 0
