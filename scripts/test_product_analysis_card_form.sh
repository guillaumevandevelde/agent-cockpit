#!/usr/bin/env bash
# Test harness for the product-analysis card form that the intake-authoring
# skill must expose prospectively, plus the matching vocabulary alignment in
# product-analysis/SKILL.md step 1, plus the implementation marker in
# docs/cockpit/product-analyse-methode-decision.md §7 item 1.
#
# Kanban-kaart: bc6b266c… (follow-up on 8394f725… → product-analyse-methode-decision).
#
# Assertions (status quo before this fix: all FAIL, by construction):
#   1. intake-authoring/SKILL.md mentions a forward-looking Backlog form for
#      product analyses that is NOT the meta-project intake/promotion flow.
#   2. That form fixes the title to `Product analyse - <naam of URL>` exactly.
#   3. That form carries exactly four fixed field labels in the description:
#      `URL/product`, `Premisse/aanleiding`, `Focusvragen`, `Diepgang` (and the
#      Focusvragen label is paired with the literal escape `geen — gebruik de
#      standaard`).
#   4. The form sets `work_type="analysis"` and names the `product-analysis`
#      skill as the executor — without inventing an `agent` value or backend
#      field.
#   5. product-analysis/SKILL.md step 1 reads those four labels 1-to-1.
#   6. product-analysis/SKILL.md step 1 still preserves the legacy default
#      behaviour for bare-title cards (no impediment on missing premise alone).
#   7. Existing card 87b99d2d… is not retroactively rewritten; the new form is
#      documented as prospective.
#   8. docs/cockpit/product-analyse-methode-decision.md §7 item 1 carries an
#      `✅ Geïmplementeerd (kaart bc6b266c…)` marker, analogous to item 2's
#      existing `✅ Geïmplementeerd (kaart d5072884…)` marker.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel)"
INTAKE="$REPO_ROOT/.claude/skills/intake-authoring/SKILL.md"
PRODUCT="$REPO_ROOT/.claude/skills/product-analysis/SKILL.md"
DECISION="$REPO_ROOT/docs/cockpit/product-analyse-methode-decision.md"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
# check "<label>" "<grep-args>" — run grep against the file, count hits.
check() {
  local label="$1"
  local pattern="$2"
  local file="$3"
  local extra="${4:-}"
  if grep -qE -- "$pattern" "$file" $extra; then
    ok "$label"
  else
    bad "$label"
  fi
}

# ----------------------------------------------------------------------------
echo "Task 1: intake-authoring exposes a forward-looking product-analysis card form"
[ -f "$INTAKE" ] || { echo "  FAIL: $INTAKE missing"; exit 1; }
check "intake-authoring mentions a Product analyse form" "Product analyse" "$INTAKE"
check "intake-authoring form lives in an existing project" "bestaand project" "$INTAKE"
check "intake-authoring form is NOT the meta-project intake/promotion flow" "geen .*intake|geen .*promotie" "$INTAKE"
check "intake-authoring form targets Backlog" "Backlog" "$INTAKE"

# ----------------------------------------------------------------------------
echo "Task 2: exact title form 'Product analyse - <naam of URL>'"
check "intake-authoring pins the title form" "Product analyse - <naam of URL>" "$INTAKE"

# ----------------------------------------------------------------------------
echo "Task 3: exactly four fixed field labels in the description"
check "field label 'URL/product'" "URL/product" "$INTAKE"
check "field label 'Premisse/aanleiding'" "Premisse/aanleiding" "$INTAKE"
check "field label 'Focusvragen'" "Focusvragen" "$INTAKE"
check "field label 'Diepgang'" "Diepgang" "$INTAKE"
check "Focusvragen paired with 'geen — gebruik de standaard'" "geen.*gebruik de standaard" "$INTAKE"

# ----------------------------------------------------------------------------
echo "Task 4: work_type=\"analysis\" + product-analysis skill, no invented agent"
check "intake-authoring sets work_type=\"analysis\"" 'work_type="analysis"' "$INTAKE"
check "intake-authoring names the product-analysis skill" "product-analysis" "$INTAKE"
# Negative: must not invent a `card.agent` value (skills are not personas).
if grep -qE 'card\.agent="product-analyst"|agent="product-analyst"' "$INTAKE"; then
  bad "intake-authoring does NOT set card.agent=\"product-analyst\""
else
  ok "intake-authoring does NOT set card.agent=\"product-analyst\""
fi

# ----------------------------------------------------------------------------
echo "Task 5: product-analysis/SKILL.md step 1 reads the four labels 1-to-1"
[ -f "$PRODUCT" ] || { echo "  FAIL: $PRODUCT missing"; exit 1; }
check "product-analysis mentions 'URL/product'" "URL/product" "$PRODUCT"
check "product-analysis mentions 'Premisse/aanleiding'" "Premisse/aanleiding" "$PRODUCT"
check "product-analysis mentions 'Focusvragen'" "Focusvragen" "$PRODUCT"
check "product-analysis mentions 'Diepgang'" "Diepgang" "$PRODUCT"

# ----------------------------------------------------------------------------
echo "Task 6: product-analysis preserves legacy default for bare-title cards"
check "product-analysis still defaults on bare-title cards" "bare title" "$PRODUCT"
check "product-analysis does NOT report_impediment for missing premise alone" \
  "do \*\*not\*\*.*report_impediment|report_impediment for that alone|default to the generic" "$PRODUCT"

# ----------------------------------------------------------------------------
echo "Task 7: existing card 87b99d2d is not retroactively rewritten"
check "intake-authoring mentions 87b99d2d as not-rewritten / prospective" "87b99d2d" "$INTAKE"
check "intake-authoring says the form applies prospectively" "prospectively|forward-looking|vooruitkijkend" "$INTAKE"

# ----------------------------------------------------------------------------
echo "Task 8: implementation marker in product-analyse-methode-decision.md §7 item 1"
[ -f "$DECISION" ] || { echo "  FAIL: $DECISION missing"; exit 1; }
check "decision doc mentions 'bc6b266c'" "bc6b266c" "$DECISION"
check "decision doc has '✅ Geïmplementeerd (kaart bc6b266c…)' marker" \
  "✅ Geïmplementeerd .*bc6b266c" "$DECISION"

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
