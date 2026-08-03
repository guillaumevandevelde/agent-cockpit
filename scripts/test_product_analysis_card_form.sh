#!/usr/bin/env bash
# Test harness for the product-analysis card form that the
# product-analysis-card skill must expose, plus the matching vocabulary
# alignment in product-analysis/SKILL.md step 1, plus the implementation
# marker in docs/cockpit/product-analyse-methode-decision.md §7 item 1.
#
# The card form was originally housed in intake-authoring/SKILL.md
# alongside the inceptie-flow. It was carved out into its own skill when
# intake-authoring is rewritten to `new-app` (see
# docs/cockpit/kaartloze-app-inceptie-decision.md), so the form's
# canonical home is now product-analysis-card/SKILL.md.
#
# Kanban-kaart: bc6b266c… (follow-up on 8394f725… → product-analyse-methode-decision).
#
# Assertions:
#   1. product-analysis-card/SKILL.md mentions a forward-looking Backlog form
#      for product analyses that is NOT the meta-project intake/promotion flow.
#   2. That form fixes the title to `Product analyse - <naam of URL>` exactly.
#   3. The create_card template carries exactly four fixed `Label:` lines
#      (URL/product, Premisse/aanleiding, Focusvragen, Diepgang) — no fifth
#      Label: line. Focusvragen pairs with the literal `geen — gebruik de
#      standaard` despite wrapping.
#   4. The form sets `work_type="analysis"` and names the `product-analysis`
#      skill as the executor — without inventing an `agent` value or backend
#      field. The negative assertion catches BOTH single- and double-quoted
#      assignment forms. The antipattern-prose "veelgemaakte fouten" row may
#      MENTION `card.agent='product-analyst'` to warn against it.
#   5. product-analysis/SKILL.md step 1 reads those four labels 1-to-1 and
#      also carries the `geen — gebruik de standaard` literal despite any
#      line-wrapping.
#   6. product-analysis/SKILL.md step 1 still preserves the legacy default
#      behaviour for bare-title cards (no impediment on missing premise alone).
#   7. Existing card 87b99d2d… is not retroactively rewritten; the new form is
#      documented as prospective.
#   8. docs/cockpit/product-analyse-methode-decision.md §7 item 1 carries an
#      `✅ Geïmplementeerd (kaart bc6b266c…)` marker, scoped to the §7 item 1
#      paragraph (not any free-floating `bc6b266c` mention elsewhere in the
#      doc), analogous to item 2's existing `✅ Geïmplementeerd (kaart
#      d5072884…)` marker.
#   9. The product-analysis-card frontmatter `description:` only triggers on
#      the product-analysis authoring trigger (NOT the inceptie / new-app
#      trigger — that one belongs to intake-authoring). The description
#      stays when-to-use only (no workflow summary).
#  10. A top-level `## When to use` bullet points to the product-analysis
#      form / external-product comparison trigger. There is no
#      inceptie / new-app bullet (that one lives in intake-authoring).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel)"
INTAKE="$REPO_ROOT/.claude/skills/product-analysis-card/SKILL.md"
PRODUCT="$REPO_ROOT/.claude/skills/product-analysis/SKILL.md"
DECISION="$REPO_ROOT/docs/cockpit/product-analyse-methode-decision.md"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check() {
  local label="$1"
  local pattern="$2"
  local file="$3"
  if grep -qE -- "$pattern" "$file"; then
    ok "$label"
  else
    bad "$label"
  fi
}

# Extract the product-analyse create_card(...) block (the second
# `card = create_card(` block in the file). Use awk that emits a delimiter
# between blocks, then pick the one whose body mentions Product analyse.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PA_BLOCK="$TMP/pa_block.txt"
awk '
  /^card = create_card\(/ { block=""; capturing=1 }
  capturing { block = block $0 "\n" }
  capturing && /^\)$/ {
    if (block ~ /Product analyse/) print block
    capturing=0
  }
' "$INTAKE" > "$PA_BLOCK"

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
echo "Task 3a: exactly four fixed description-field lines in the create_card template"
check "create_card template (product-analyse form) extracted" \
  'card = create_card' "$PA_BLOCK"
check "template carries 'URL/product:'" 'URL/product:' "$PA_BLOCK"
check "template carries 'Premisse/aanleiding:'" 'Premisse/aanleiding:' "$PA_BLOCK"
check "template carries 'Focusvragen:'" 'Focusvragen:' "$PA_BLOCK"
check "template carries 'Diepgang:'" 'Diepgang:' "$PA_BLOCK"

# Counting: exactly four Label: lines inside the product-analyse block.
LABEL_LINES=$(grep -cE '^\s+"(URL/product|Premisse/aanleiding|Focusvragen|Diepgang):' "$PA_BLOCK" || true)
if [ "$LABEL_LINES" -eq 4 ]; then
  ok "exactly four Label: lines inside create_card template (found: $LABEL_LINES)"
else
  bad "expected exactly 4 Label: lines, found: $LABEL_LINES"
fi

# Anti-regression: no fifth / non-standard Label: line inside the block.
NONSTD=$(grep -E '^\s+"[A-Z][a-zA-Z/ ]+:' "$PA_BLOCK" \
  | grep -vE 'URL/product|Premisse/aanleiding|Focusvragen|Diepgang' || true)
if [ -z "$NONSTD" ]; then
  ok "no fifth / non-standard Label: line inside create_card template"
else
  bad "non-standard Label: line(s) inside create_card template:"$'\n'"$NONSTD"
fi

# ----------------------------------------------------------------------------
echo "Task 3b: 'geen — gebruik de standaard' literal on the producer side"
# The phrase may wrap across lines. Use a `tr`-joined copy of the file to
# make line-wrapped phrases searchable as a single line. The phrase is
# `geen — gebruik de standaard` — the em-dash introduces 3 chars between
# `geen` and `gebruik` (space, U+2014 em-dash, space) — so we allow any
# punctuation between the two halves of the phrase.
PRODUCER_FLAT="$TMP/intake_flat.txt"
tr '\n' ' ' < "$INTAKE" > "$PRODUCER_FLAT"
if grep -qE 'Focusvragen.{0,40}geen[^a-z]*gebruik de[^a-z]*standaard' "$PRODUCER_FLAT"; then
  ok "intake-authoring pairs Focusvragen with 'geen — gebruik de standaard' (despite wrapping)"
else
  bad "intake-authoring does NOT pair Focusvragen with 'geen — gebruik de standaard'"
fi

# ----------------------------------------------------------------------------
echo "Task 4a: work_type=\"analysis\" + product-analysis skill"
check "intake-authoring sets work_type=\"analysis\"" 'work_type="analysis"' "$INTAKE"
check "intake-authoring names the product-analysis skill" "product-analysis" "$INTAKE"

# ----------------------------------------------------------------------------
echo "Task 4b: negative — no card.agent=\"product-analyst\" assignment (single- or double-quoted)"
# The negative scope is the create_card template (assignments live there).
# The antipattern-prose "veelgemaakte fouten" table row may MENTION
# `card.agent='product-analyst'` (single-quoted) to warn against it; that
# row is text in a markdown cell, NOT an assignment in the create_card
# template.
NEG_IN_TEMPLATE=$(grep -E "card\\.agent=['\"]product-analyst['\"]|agent=['\"]product-analyst['\"]" "$PA_BLOCK" || true)
if [ -z "$NEG_IN_TEMPLATE" ]; then
  ok "create_card template does NOT assign card.agent=\"product-analyst\" (single or double quoted)"
else
  bad "create_card template assigns card.agent=\"product-analyst\":"$'\n'"$NEG_IN_TEMPLATE"
fi

# Belt-and-braces: the antipattern prose warns against it. The mention
# uses single quotes (inside a markdown table cell). Verify the warning
# exists so the test is meaningful.
check "intake-authoring has antipattern prose warning against card.agent='product-analyst'" \
  "card\\.agent=['\"]product-analyst['\"]" "$INTAKE"

# ----------------------------------------------------------------------------
echo "Task 5: product-analysis/SKILL.md step 1 reads the four labels 1-to-1"
[ -f "$PRODUCT" ] || { echo "  FAIL: $PRODUCT missing"; exit 1; }
check "product-analysis mentions 'URL/product'" "URL/product" "$PRODUCT"
check "product-analysis mentions 'Premisse/aanleiding'" "Premisse/aanleiding" "$PRODUCT"
check "product-analysis mentions 'Focusvragen'" "Focusvragen" "$PRODUCT"
check "product-analysis mentions 'Diepgang'" "Diepgang" "$PRODUCT"

# ----------------------------------------------------------------------------
echo "Task 5b: 'geen — gebruik de standaard' literal on the consumer side"
# Same wrapping-tolerant check as the producer side: join lines, allow
# punctuation between the phrase halves (em-dash, hyphens, spaces, line
# wraps after `tr`). The window after `Focusvragen` is wide (300 chars)
# because the consumer-side wraps `Focusvragen`, the explanatory prose,
# AND the literal across multiple lines.
CONSUMER_FLAT="$TMP/product_flat.txt"
tr '\n' ' ' < "$PRODUCT" > "$CONSUMER_FLAT"
if grep -qE 'Focusvragen.{0,300}geen[^a-z]*gebruik de[^a-z]*standaard' "$CONSUMER_FLAT"; then
  ok "product-analysis carries 'geen — gebruik de standaard' literal (despite wrapping)"
else
  bad "product-analysis does NOT carry 'geen — gebruik de standaard' literal"
fi

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
echo "Task 8: implementation marker in decision doc §7 item 1 (scoped)"
[ -f "$DECISION" ] || { echo "  FAIL: $DECISION missing"; exit 1; }
PARA_FILE="$TMP/decision-item1.txt"
awk '
  /^1\. `bc6b266c/ { in_para=1 }
  in_para { print }
  in_para && /^2\. `d5072884/ { in_para=0 }
' "$DECISION" > "$PARA_FILE"
if grep -qE "✅ Geïmplementeerd .*bc6b266c" "$PARA_FILE"; then
  ok "decision doc §7 item 1 carries '✅ Geïmplementeerd (kaart bc6b266c…)' marker"
else
  bad "decision doc §7 item 1 does NOT carry the implementation marker"
fi
TOTAL=$(grep -cE 'bc6b266c' "$DECISION" || true)
IN_PARA=$(grep -cE 'bc6b266c' "$PARA_FILE" || true)
if [ "$TOTAL" -le 3 ] && [ "$IN_PARA" -ge 1 ]; then
  ok "decision doc scopes 'bc6b266c' mentions to §7 item 1 (total: $TOTAL, in-para: $IN_PARA)"
else
  bad "decision doc has unexpected bc6b266c mentions (total: $TOTAL, in-para: $IN_PARA)"
fi

# ----------------------------------------------------------------------------
echo "Task 9: product-analysis-card frontmatter is discoverable for the trigger only"
# Extract the YAML frontmatter (the block between the first two `---` lines).
# This skill is the product-analysis authoring home — it must NOT carry the
# inceptie / new-app trigger (that one belongs to intake-authoring).
FRONTMATTER="$TMP/frontmatter.txt"
awk 'BEGIN{n=0} /^---$/{n++; if(n==1) next; if(n==2) exit} n==1{print}' "$INTAKE" > "$FRONTMATTER"
check "frontmatter extracted" "description:" "$FRONTMATTER"
# Product-analysis trigger must be discoverable.
check "frontmatter mentions product-analysis authoring trigger" \
  "product-analysis" "$FRONTMATTER"
check "frontmatter mentions Backlog / external product trigger" \
  "Backlog|external product|external application" "$FRONTMATTER"
# Description stays when-to-use only (no workflow summary).
check "frontmatter description is when-to-use style (no workflow step-list)" \
  "Use when" "$FRONTMATTER"
# Negative: frontmatter must NOT include the inceptie / new-app trigger —
# routing for that lives in intake-authoring, not here.
if grep -qE "inceptie|new app|app-idea" "$FRONTMATTER"; then
  bad "frontmatter mentions the inceptie / new-app trigger (routing should live in intake-authoring)"
else
  ok "frontmatter does NOT mention the inceptie / new-app trigger"
fi
# Negative: frontmatter must NOT be a workflow summary like "Steps: ...".
if grep -qE "^description: .*[Ss]tep [0-9]:" "$FRONTMATTER"; then
  bad "frontmatter description looks like a workflow summary"
else
  ok "frontmatter description is not a workflow summary"
fi

# ----------------------------------------------------------------------------
echo "Task 10: top-level 'When to use' bullet points to product-analysis form"
# Section-scoped extraction: from `^## When to use` up to (but not
# including) the next `^## ` heading.
WHEN_USE="$TMP/when_use.txt"
WHEN_NOT_USE="$TMP/when_not_use.txt"
awk '
  /^## When to use$/ { in_para=1; next }
  in_para && /^## / { in_para=0 }
  in_para { print }
' "$INTAKE" > "$WHEN_USE"
awk '
  /^## When NOT to use$/ { in_para=1; next }
  in_para && /^## / { in_para=0 }
  in_para { print }
' "$INTAKE" > "$WHEN_NOT_USE"
# Top-level bullet pointing to the product-analysis form / external-product
# comparison trigger. Use a forgiving regex covering the trigger phrases.
check "## When to use contains a product-analysis bullet" \
  "external product|vergelijk|Product analyse|product-analysis" "$WHEN_USE"
# Negative: this skill is product-analysis-only — the inceptie / new-app
# bullet lives in intake-authoring, not here.
if grep -qE "new app|app-idea" "$WHEN_USE"; then
  bad "## When to use mentions the inceptie / new-app bullet (that one lives in intake-authoring)"
else
  ok "## When to use does NOT mention the inceptie / new-app bullet"
fi

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
