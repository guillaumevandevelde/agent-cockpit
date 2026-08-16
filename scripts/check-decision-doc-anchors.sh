#!/usr/bin/env bash
# check-decision-doc-anchors.sh — advisory check that every docs/cockpit/*-decision.md
# that makes a claim about Cockpit code backs it with a file:line anchor.
#
# Verifies the §4 "claims over onze eigen code krijgen een file:line-anker"
# rule from docs/cockpit/taalgebruik-conventies.md against the existing
# decisions in docs/cockpit/*-decision.md. A doc whose prose carries NO
# anchor (or whose anchors are all under docs/, fenced code, or prose
# like "regel 42" without a path) is reported as drift.
#
# Anchors counted: `` backend/app/.../foo.py:42 `` or
# `` frontend/src/.../bar.tsx:13-22 `` (line-range allowed), also
# `` backend/tests/.../test_x.py:99 `` — these are the canonical cite
# targets in this repo. Surrounding backticks are optional. A bare
# ``:NN`` with no path, or a path under ``docs/`` (which is a doc-doc
# link, not a code anchor), is intentionally NOT counted.
#
# Fenced code blocks (```` ``` ````) are skipped so a `worker.py:42`
# string inside ```python ... ``` doesn't count as an anchor. Inline
# links with ``.md:LINENO`` style are also skipped — same reason.
#
# Advisory by design — exits 0 even when docs drift (mirrors the other
# doc-checks). Pass --strict to exit 1. Use --json for machine-readable
# output (per-doc drift list).
#
# Usage:
#   scripts/check-decision-doc-anchors.sh [--strict] [--json]
#
# Env:
#   DECISIONS_DIR   directory holding decisions.md + *-decision.md
#                   (default: <repo>/docs/cockpit; overridden by the
#                   test harness through fixtures)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DECISIONS_DIR="${DECISIONS_DIR:-$REPO_ROOT/docs/cockpit}"

STRICT=0
JSON=0
for arg in "$@"; do
  case "$arg" in
    --strict) STRICT=1 ;;
    --json)   JSON=1 ;;
    --help|-h)
      sed -n '3,38p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    "")
      ;;
    *)
      echo "ERROR: unknown argument '$arg' (see --help)" >&2
      exit 2
      ;;
  esac
done

# Find every *-decision.md under DECISIONS_DIR (maxdepth 1 — siblings only).
# An empty sample (no decision docs at all) is a valid clean state.
mapfile -t -d '' DOCS < <(
  find "$DECISIONS_DIR" -maxdepth 1 -type f -name '*-decision.md' -print0 2>/dev/null | sort -z
)

TOTAL=${#DOCS[@]}

# Anchor: path under backend/app, frontend/src, or backend/tests, followed
# by `:NN` (optionally `NN-MM` range). Surrounding backticks optional.
#
# Two alternatives:
#   1. Backticked: `backend/app/.../foo.py:42` — preceded by `` ` `` so the
#      path starts cleanly inside the backticks.
#   2. Unbackticked prose: ``backend/app/foo.py:42`` — preceded by whitespace
#      or punctuation (NOT another path char or digit), so a mid-token
#      fragment like ``xybackend/app/foo.py:42`` doesn't count.
#
# `awk` does not honour `\b` — gawk-style word boundaries silently become a
# literal backspace byte when passed via `awk -v`. We therefore build the
# boundary ourselves: a leading character class that excludes path chars and
# digits, plus the `(^|...)` start-of-line alternative.
ANCHOR_RE='`(backend/app|backend/tests|frontend/src)/[^`:[:space:]]+:[0-9]+(-[0-9]+)?`|(^|[[:space:][:punct:]])(backend/app|backend/tests|frontend/src)/[^`[:space:]]+:[0-9]+(-[0-9]+)?'

# Count anchors per doc, skipping fenced code blocks.
#
# Two modes matter:
#   - Inside fences: every line is content, but we skip the whole block.
#     Detected by tracking whether we're currently between ``` markers.
#   - Outside fences: any line that contains the anchor regex counts.
#
# Frontmatter between `---` markers is also skipped — those four keys carry
# canonical anchors elsewhere (the `**Kaart:**` field, e.g.) and grepping
# them for code anchors is just noise. A real claim lives in the body,
# not the frontmatter.
count_anchors() {
  # $1 = file path. Echoes the count of anchors in non-fence body lines.
  awk -v re="$ANCHOR_RE" '
    BEGIN { in_fence = 0; in_fm = 0; fm_done = 0; }
    {
      line = $0
      # Frontmatter handling: leading ``---`` opens it, next ``---`` closes.
      # Only relevant for the first contiguous run at the top of the file.
      if (!fm_done) {
        if (in_fm) {
          if (line ~ /^---[[:space:]]*$/) { in_fm = 0; fm_done = 1; }
          next
        }
        if (line ~ /^---[[:space:]]*$/) { in_fm = 1; next }
        fm_done = 1
      }
      # Fenced code blocks: count consecutive backtick-fence lines; we are
      # IN a fence when we just saw an opener and have not seen the matching
      # closer. ```` ``` ```` (with optional language tag) opens/closes.
      if (line ~ /^```/) {
        in_fence = (in_fence == 0) ? 1 : 0
        next
      }
      if (in_fence) next
      if (match(line, re)) {
        count++
        # Count each line once even if multiple anchors appear — the
        # check is "at least one anchor" so a multi-anchor line is 1.
      }
    }
    END { print count+0 }
  ' "$1"
}

drifted=()   # "<rel>|<anchor_count>" per drifting doc
clean_count=0

for f in "${DOCS[@]}"; do
  rel="${f#"$REPO_ROOT"/}"
  n=$(count_anchors "$f")
  if [ "$n" -eq 0 ]; then
    drifted+=("$rel|0")
  else
    clean_count=$((clean_count + 1))
  fi
done

drift_count=${#drifted[@]}

# --- emit ------------------------------------------------------------------
emit_json() {
  printf '{\n'
  printf '  "sample_size": %d,\n' "$TOTAL"
  printf '  "clean_count": %d,\n' "$clean_count"
  printf '  "drift_count": %d,\n' "$drift_count"
  printf '  "drifted": [\n'
  first=1
  for entry in "${drifted[@]}"; do
    rel="${entry%%|*}"
    if [ "$first" -eq 1 ]; then first=0; else printf ',\n'; fi
    printf '    {"path": "%s"}' "$rel"
  done
  printf '\n  ]\n'
  printf '}\n'
}

if [ "$JSON" -eq 1 ]; then
  emit_json
  if [ "$STRICT" -eq 1 ] && [ "$drift_count" -gt 0 ]; then
    exit 1
  fi
  exit 0
fi

# Human-readable summary.
if [ "$drift_count" -eq 0 ]; then
  echo "OK: every docs/cockpit/*-decision.md in the sample carries a backend/app or frontend/src file:line anchor."
  echo "  clean: $clean_count, sample: $TOTAL"
  exit 0
fi

echo "WARNING: $drift_count decision doc(s) make claims about Cockpit code without a file:line anchor:" >&2
for entry in "${drifted[@]}"; do
  rel="${entry%%|*}"
  echo "  - $rel" >&2
done
echo "" >&2
echo "Each claim over onze eigen code heeft een pad:regel-anker nodig (zie docs/cockpit/taalgebruik-conventies.md §4)." >&2
echo "Een anker is bv. \`backend/app/services/foo.py:42\` of \`frontend/src/features/x/Y.tsx:13-22\`." >&2
echo "Fenced code, kale ':NN'-verwijzingen zonder pad, en paden onder docs/ tellen niet als anker." >&2

if [ "$STRICT" -eq 1 ]; then
  exit 1
fi
echo "(advisory — not failing the build; run with --strict to enforce)" >&2
exit 0
