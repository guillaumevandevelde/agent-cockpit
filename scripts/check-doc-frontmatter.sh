#!/usr/bin/env bash
#
# check-doc-frontmatter.sh — advisory drift check for docs/cockpit frontmatter.
#
# Verifies that every docs/cockpit/*.md carries a minimal, OKF-compatible YAML
# frontmatter block (title + type + status) with a recognised type/status. This
# is the machine-readable backbone from
# docs/cockpit/knowledge-structure-navigation-analysis.md §4.1 — `type` is the
# virtual folder (replacing the filename convention) and `status` lifts
# proposed/decided/superseded out of prose banners into a queryable field.
#
# What it flags:
#   - docs with no frontmatter block at all (line 1 is not `---`),
#   - docs missing a non-empty `title`,
#   - docs whose `type` is not one of: decision spec analysis plan reference index,
#   - docs whose `status` is not one of: proposed active decided superseded.
#
# Advisory by design: exits 0 even when docs drift (prints a warning), mirroring
# check-decision-register.sh / check-superpowers-promotions.sh ("signal, not
# gate"). Pass --strict to exit 1 on any offending doc (e.g. to harden into a
# blocking CI step later).
#
# Usage:
#   scripts/check-doc-frontmatter.sh [--strict]
#
# Env:
#   DOCS_DIR   directory holding the *.md docs to check
#              (default: <repo>/docs/cockpit; overridden by the tests)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DOCS_DIR="${DOCS_DIR:-$REPO_ROOT/docs/cockpit}"

VALID_TYPES="decision spec analysis plan reference index"
VALID_STATUS="proposed active decided superseded"

STRICT=0
for arg in "$@"; do
  case "$arg" in
    --strict) STRICT=1 ;;
    --help|-h)
      sed -n '3,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    "") ;;
    *)
      echo "ERROR: unknown argument '$arg' (see --help)" >&2
      exit 2
      ;;
  esac
done

if [ ! -d "$DOCS_DIR" ]; then
  echo "ERROR: docs directory not found at $DOCS_DIR" >&2
  echo "Point DOCS_DIR at the directory that holds the *.md docs." >&2
  exit 2
fi

in_list() {
  # $1 = needle, $2 = space-separated haystack
  case " $2 " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

# Extract a top-level scalar field from the frontmatter block on stdin.
# Handles `key: value`, `key: "quoted"`, `key: 'quoted'`. Echoes the value.
fm_field() {
  # $1 = block text, $2 = key
  printf '%s\n' "$1" | awk -v key="$2" '
    function trim(s){ sub(/^[ \t]+/,"",s); sub(/[ \t]+$/,"",s); return s }
    {
      if ($0 ~ "^" key ":") {
        v = $0
        sub("^" key ":", "", v)
        v = trim(v)
        # strip a single layer of matching quotes
        if (v ~ /^".*"$/) { v = substr(v, 2, length(v) - 2) }
        else if (v ~ /^'\''.*'\''$/) { v = substr(v, 2, length(v) - 2) }
        print v
        exit
      }
    }
  '
}

problems=()

while IFS= read -r -d '' f; do
  rel="${f#"$REPO_ROOT"/}"

  # Frontmatter must open on the very first line.
  if [ "$(head -1 "$f")" != "---" ]; then
    problems+=("$rel|no-frontmatter")
    continue
  fi

  # Slice the block between the opening `---` and the next `---`.
  block="$(awk 'NR==1{next} /^---[ \t]*$/{exit} {print}' "$f")"

  title="$(fm_field "$block" title)"
  type="$(fm_field "$block" type)"
  status="$(fm_field "$block" status)"

  issues=()
  [ -z "$title" ] && issues+=("missing-title")
  if [ -z "$type" ]; then
    issues+=("missing-type")
  elif ! in_list "$type" "$VALID_TYPES"; then
    issues+=("unknown-type:$type")
  fi
  if [ -z "$status" ]; then
    issues+=("missing-status")
  elif ! in_list "$status" "$VALID_STATUS"; then
    issues+=("unknown-status:$status")
  fi

  if [ "${#issues[@]}" -gt 0 ]; then
    problems+=("$rel|$(IFS=, ; echo "${issues[*]}")")
  fi
done < <(find "$DOCS_DIR" -maxdepth 1 -type f -name '*.md' -print0 2>/dev/null | sort -z)

if [ "${#problems[@]}" -eq 0 ]; then
  echo "OK: every docs/cockpit/*.md has valid frontmatter (title + type + status)."
  exit 0
fi

echo "WARNING: ${#problems[@]} doc(s) have missing or invalid frontmatter:" >&2
for p in "${problems[@]}"; do
  echo "  - ${p%%|*}  (${p#*|})" >&2
done
echo "" >&2
echo "Add an OKF-compatible frontmatter block at the very top of each doc:" >&2
echo "  ---" >&2
echo "  title: \"<human-readable title>\"" >&2
echo "  type: $VALID_TYPES" >&2
echo "  status: $VALID_STATUS" >&2
echo "  ---" >&2
echo "(type = decision|spec|analysis|plan|reference|index; status = proposed|active|decided|superseded)" >&2

if [ "$STRICT" -eq 1 ]; then
  exit 1
fi
echo "(advisory — not failing the build; run with --strict to enforce)" >&2
exit 0
