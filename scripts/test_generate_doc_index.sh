#!/usr/bin/env bash
# Test harness for scripts/generate-doc-index.py.
#
# Exercises the frontmatter-derived index + llms.txt generator against synthetic
# fixture dirs (never the real docs/cockpit tree, so the test stays green
# regardless of which docs exist on the branch):
#
#   1. arg parsing — --help works.
#   2. generate mode — writes README block + llms.txt from frontmatter.
#   3. coverage — every doc appears (100%, not a hand-curated subset).
#   4. grouping/badges — docs grouped by type with a status badge.
#   5. idempotency — a second run reports "already up to date".
#   6. --check clean — freshly generated tree is in sync → exit 0, "OK".
#   7. --check drift — a hand-edited README block is flagged, exit 0 (advisory).
#   8. --check --strict — same drift → exit 1.
#   9. llms.txt shape — H1 + blockquote + linked list per llmstxt.org.
#  10. real tree — the repo's own generated artifacts are in sync.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/generate-doc-index.py"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

BEGIN="<!-- BEGIN GENERATED DOC INDEX (scripts/generate-doc-index.py) — DO NOT EDIT BY HAND -->"
END="<!-- END GENERATED DOC INDEX -->"

make_doc() {
  # $1 = path, $2 = title, $3 = type, $4 = status
  printf -- '---\ntitle: "%s"\ntype: %s\nstatus: %s\n---\n\n# %s\n' "$2" "$3" "$4" "$2" > "$1"
}

run_gen() { python3 "$SUT" --docs-dir "$1" --readme "$1/README.md" --llms "$1/llms.txt" "${@:2}"; }

# ----------------------------------------------------------------------------
echo "Task 1: arg parsing — --help"
out=$(python3 "$SUT" --help 2>&1 || true)
check "--help mentions usage" 'echo "$out" | grep -qiE "usage:"'
check "--help mentions --check" 'echo "$out" | grep -qE "\-\-check"'

# ----------------------------------------------------------------------------
echo "Task 2/3/4: generate mode — coverage + grouping + badges"
gen="$TMP/gen"; mkdir -p "$gen"
make_doc "$gen/README.md" "spec-boom (index)" index active
make_doc "$gen/alpha-analyse.md" "Alpha analyse" analysis proposed
make_doc "$gen/beta-decision.md" "Beta beslissing" decision decided
make_doc "$gen/gamma-spec.md" "Gamma spec" spec active
# Seed the README with markers so the block splices in place.
printf '# Index\n\nHand-curated preamble.\n\n%s\n%s\n\n## Regels\n\n1. keep me.\n' "$BEGIN" "$END" > "$gen/README.md.tmp"
# README.md was overwritten by make_doc above; restore the marker fixture.
mv "$gen/README.md.tmp" "$gen/README.md"
out=$(run_gen "$gen" 2>&1); rc=$?
check "generate → exit 0" '[ "$rc" -eq 0 ]'
readme="$(cat "$gen/README.md")"
check "preamble preserved" 'echo "$readme" | grep -qF "Hand-curated preamble."'
check "## Regels preserved" 'echo "$readme" | grep -qF "## Regels"'
check "covers alpha" 'echo "$readme" | grep -qF "(./alpha-analyse.md)"'
check "covers beta" 'echo "$readme" | grep -qF "(./beta-decision.md)"'
check "covers gamma" 'echo "$readme" | grep -qF "(./gamma-spec.md)"'
check "grouped by type (Analysis heading)" 'echo "$readme" | grep -qE "^### Analysis \([0-9]+\)"'
check "grouped by type (Decision heading)" 'echo "$readme" | grep -qE "^### Decision \([0-9]+\)"'
check "status badge present (proposed)" 'echo "$readme" | grep -qF "🟡 proposed"'
check "status badge present (decided)" 'echo "$readme" | grep -qF "🔵 decided"'

# ----------------------------------------------------------------------------
echo "Task 5: idempotency — second run is a no-op"
out=$(run_gen "$gen" 2>&1)
check "second run → already up to date" 'echo "$out" | grep -qiF "already up to date"'

# ----------------------------------------------------------------------------
echo "Task 6: --check clean"
out=$(run_gen "$gen" --check 2>&1); rc=$?
check "clean check → exit 0" '[ "$rc" -eq 0 ]'
check "clean check → OK" 'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 7/8: --check drift + --strict"
# Corrupt the generated block by adding a stray doc without regenerating.
make_doc "$gen/delta-plan.md" "Delta plan" plan active
out=$(run_gen "$gen" --check 2>&1); rc=$?
check "drift check → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
check "drift check → WARNING" 'echo "$out" | grep -qiE "out of sync"'
out=$(run_gen "$gen" --check --strict 2>&1); rc=$?
check "drift --strict → exit 1" '[ "$rc" -eq 1 ]'

# ----------------------------------------------------------------------------
echo "Task 9: llms.txt shape"
run_gen "$gen" >/dev/null 2>&1  # regenerate to pick up delta
llms="$(cat "$gen/llms.txt")"
check "llms H1 project name" 'echo "$llms" | head -1 | grep -qE "^# "'
check "llms blockquote summary" 'echo "$llms" | grep -qE "^> "'
check "llms links a doc with type/status" 'echo "$llms" | grep -qE "\(\./delta-plan\.md\): type=plan status=active"'

# ----------------------------------------------------------------------------
echo "Task 10: real docs/cockpit tree is in sync"
out=$(python3 "$SUT" --check --strict 2>&1); rc=$?
check "real tree --check --strict → exit 0" '[ "$rc" -eq 0 ]'
check "real tree → OK" 'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo ""
echo "===================="
echo "PASS: $PASS   FAIL: $FAIL"
[ "$FAIL" -eq 0 ]
