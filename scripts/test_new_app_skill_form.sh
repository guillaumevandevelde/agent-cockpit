#!/usr/bin/env bash
# Test harness for the `new-app` skill — the cardless inceptie voordeur
# (kanban card 1fa1b693…, fase 1 + 2 van
# docs/cockpit/kaartloze-app-inceptie-decision.md §3).
#
# The skill is a rewrite of `intake-authoring`, which landed its output as a
# card in the `intake` column for a human to Promote. That carrier is gone:
# the interview writes incrementally to a durable scratch dir and then calls
# the cardless birth (`create_project_from_interview`) directly.
#
# The assertions below pin the parts of the skill a future edit could
# silently drop — the scratch-dir contract, the resume semantics, the
# copy-then-delete ordering, and (most importantly) the negative: the skill
# must never fall back to the old intake route.
#
# Assertions:
#   1. `.claude/skills/new-app/SKILL.md` exists with `name: new-app`, and the
#      old `.claude/skills/intake-authoring/` directory is gone.
#   2. The frontmatter description carries the human trigger ("idee voor een
#      nieuwe app/tool/project") so the skill is discoverable, and names the
#      resume entrypoint.
#   3. Both sub-skills are named, and the approval gates are pinned as native
#      interactive — with an explicit "not report_impediment" for the dialogue.
#   4. The scratch dir is `~/.claude-registry/interviews/<slug>/` and carries
#      exactly design.md + plan.md + state.json, written INCREMENTALLY (after
#      each approved section, not at the end).
#   5. state.json documents the four required keys (last approved section,
#      project_name, target_path, phase) and all three phase values.
#   6. `--resume <slug>` documents both branches: `ready_for_birth` retries
#      ONLY the birth; `interview` continues from the last approved section.
#   7. The birth goes through the MCP tool `create_project_from_interview`,
#      and the scratch dir is deleted only AFTER a fully successful birth
#      (copy-then-delete). On failure the dir survives and the session
#      reports the path + the `--resume` command.
#   8. The success report names the new project path, the project_key, the
#      first Backlog card id, and the autodispatch state.
#   9. NEGATIVE — the skill never calls `create_project_from_intake` and never
#      creates a card in the `intake` column. Every mention of either must sit
#      in a prohibition sentence (that is the point of the mention).
#  10. No dead pointer left behind: no doc or sibling skill still references
#      the `.claude/skills/intake-authoring/` path.

set -u

REPO_ROOT="$(git rev-parse --show-toplevel)"
SKILL="$REPO_ROOT/.claude/skills/new-app/SKILL.md"
OLD_SKILL_DIR="$REPO_ROOT/.claude/skills/intake-authoring"

PASS=0; FAIL=0
ok()  { echo "  ok: $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check() {
  local label="$1" pattern="$2" file="$3"
  if grep -qE -- "$pattern" "$file"; then ok "$label"; else bad "$label"; fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
echo "Task 1: the skill exists as new-app and intake-authoring is gone"
if [ -f "$SKILL" ]; then
  ok "$SKILL exists"
else
  echo "  FAIL: $SKILL missing"
  echo ""
  echo "passed: $PASS, failed: $((FAIL+1))"
  exit 1
fi
FRONTMATTER="$TMP/frontmatter.txt"
awk 'BEGIN{n=0} /^---$/{n++; if(n==1) next; if(n==2) exit} n==1{print}' "$SKILL" > "$FRONTMATTER"
check "frontmatter pins 'name: new-app'" '^name: new-app$' "$FRONTMATTER"
if [ -e "$OLD_SKILL_DIR" ]; then
  bad "the old .claude/skills/intake-authoring/ directory still exists (rewrite, not a copy)"
else
  ok "the old .claude/skills/intake-authoring/ directory is gone"
fi

# ----------------------------------------------------------------------------
echo "Task 2: frontmatter carries the human trigger + the resume entrypoint"
# The description is what routes a human's sentence to this skill; the trigger
# phrase from the card is "ik heb een idee voor een nieuwe app/tool/project".
FM_FLAT="$TMP/fm_flat.txt"
tr '\n' ' ' < "$FRONTMATTER" > "$FM_FLAT"
check "description carries the 'idee voor een nieuwe app' trigger" \
  'idee voor een nieuwe app' "$FM_FLAT"
check "description names the tool/project variants of the trigger" \
  'idee voor een nieuwe app[^.]{0,40}(tool|project)' "$FM_FLAT"
check "description names the --resume entrypoint" '--resume' "$FM_FLAT"

# ----------------------------------------------------------------------------
echo "Task 3: both sub-skills + native interactive approval gates"
check "names superpowers:brainstorming" 'superpowers:brainstorming' "$SKILL"
check "names superpowers:writing-plans" 'superpowers:writing-plans' "$SKILL"
SKILL_FLAT="$TMP/skill_flat.txt"
tr '\n' ' ' < "$SKILL" > "$SKILL_FLAT"
check "approval gates stay native interactive" \
  '(native interactive|natively interactive|interactief)' "$SKILL_FLAT"
# The dialogue must NOT be routed through report_impediment (decision §4.2).
check "explicitly rules report_impediment out for the dialogue" \
  'report_impediment' "$SKILL_FLAT"
if grep -qE '(never|Never|NOT|not|nooit|niet)[^.]{0,120}report_impediment|report_impediment[^.]{0,120}(never|is not|NOT|nooit|niet)' "$SKILL_FLAT"; then
  ok "report_impediment is mentioned in a prohibition, not as an instruction"
else
  bad "report_impediment is mentioned without a prohibition — the gate must stay interactive"
fi

# ----------------------------------------------------------------------------
echo "Task 4: durable scratch dir, written incrementally"
check "scratch dir is ~/.claude-registry/interviews/<slug>/" \
  '~/\.claude-registry/interviews/<slug>' "$SKILL"
check "scratch carries design.md" 'design\.md' "$SKILL"
check "scratch carries plan.md" 'plan\.md' "$SKILL"
check "scratch carries state.json" 'state\.json' "$SKILL"
# Incremental, not end-of-run: the whole point is crash-survivability.
if grep -qE '(na elke goedgekeurde|after each approved|after every approved)' "$SKILL"; then
  ok "writes are pinned to 'after each approved section' (incremental)"
else
  bad "no 'after each approved section' rule — writes could collapse to end-of-run"
fi
if grep -qE '(niet pas aan het eind|not at the end|not only at the end)' "$SKILL"; then
  ok "explicitly rejects the write-once-at-the-end shape"
else
  bad "does not reject the write-once-at-the-end shape"
fi

# ----------------------------------------------------------------------------
echo "Task 5: state.json schema — four required keys + three phases"
# Scope the key assertions to the state.json schema block so a stray mention
# elsewhere in the prose cannot satisfy them.
STATE_BLOCK="$TMP/state_block.txt"
awk '
  /^### `state\.json`/ { capturing=1; next }
  capturing && /^## / { capturing=0 }
  capturing { print }
' "$SKILL" > "$STATE_BLOCK"
if [ -s "$STATE_BLOCK" ]; then
  ok "state.json schema block found (### \`state.json\`)"
else
  bad "no '### \`state.json\`' schema block — the schema must be documented in one place"
fi
check "state.json documents project_name" '"?project_name"?' "$STATE_BLOCK"
check "state.json documents target_path" '"?target_path"?' "$STATE_BLOCK"
check "state.json documents phase" '"?phase"?' "$STATE_BLOCK"
check "state.json documents the last approved section" \
  '"?last_approved_section"?' "$STATE_BLOCK"
for PHASE in interview ready_for_birth born; do
  check "state.json documents phase value '$PHASE'" "$PHASE" "$STATE_BLOCK"
done

# ----------------------------------------------------------------------------
echo "Task 6: --resume semantics, both branches"
check "documents the /new-app --resume <slug> invocation" \
  '/new-app --resume <slug>' "$SKILL"
# ready_for_birth → ONLY the birth is retried (do not re-run the interview).
if grep -qE 'ready_for_birth[^|]*\|[^|]*(alleen de geboorte|only the birth|birth only)' "$SKILL"; then
  ok "resume on ready_for_birth retries ONLY the birth"
else
  bad "resume on ready_for_birth does not pin 'only the birth' (interview must not repeat)"
fi
# interview → continue from the last approved section.
if grep -qE '\| *`?interview`? *\|[^|]*(verder|continue|resume)' "$SKILL"; then
  ok "resume on interview continues the dialogue"
else
  bad "resume on interview does not pin 'continue the dialogue'"
fi
check "resume on interview picks up at the last approved section" \
  'last_approved_section' "$SKILL"

# ----------------------------------------------------------------------------
echo "Task 7: birth via create_project_from_interview + copy-then-delete"
check "calls the cardless birth tool" 'create_project_from_interview' "$SKILL"
check "names the copy-then-delete rule" \
  '(copy-then-delete|kopieer-dan-verwijder)' "$SKILL"
if grep -qE '(pas (na|nadat)|only after)[^.]{0,120}(geslaagde geboorte|successful birth|birth succeed)' "$SKILL_FLAT"; then
  ok "deletion is gated on a fully successful birth"
else
  bad "deletion is not gated on a successful birth — the scratch dir could vanish on failure"
fi
if grep -qE '(blijft (de map|de scratch)|dir survives|blijft staan)' "$SKILL_FLAT"; then
  ok "on failure the scratch dir survives"
else
  bad "no 'scratch dir survives on failure' rule"
fi
# `rm` is deny-listed repo-wide (.claude/settings.json) — removal must be mv.
if grep -qE 'mv ' "$SKILL"; then
  ok "removal uses mv (rm is deny-listed in this repo)"
else
  bad "removal does not use mv — 'rm' is deny-listed by .claude/settings.json"
fi

# ----------------------------------------------------------------------------
echo "Task 8: the success report names all four facts"
REPORT_BLOCK="$TMP/report_block.txt"
awk '
  /^## Step 7 — report/ { capturing=1; next }
  capturing && /^## / { capturing=0 }
  capturing { print }
' "$SKILL" > "$REPORT_BLOCK"
if [ -s "$REPORT_BLOCK" ]; then
  ok "report step block found (## Step 7 — report)"
else
  bad "no '## Step 7 — report' block — the success report must be one place"
fi
check "report names the new project path" '(project-pad|project path|target_path)' "$REPORT_BLOCK"
check "report names the project_key" '(project_key|new_project_key)' "$REPORT_BLOCK"
check "report names the first Backlog card id" 'first_card_id' "$REPORT_BLOCK"
check "report names the autodispatch state" 'autodispatch' "$REPORT_BLOCK"
# The birth flips autodispatch from BootstrapPolicy.autodispatch_default,
# which is False (security-default-deny, bootstrap_policy.py:77). The report
# must state the real state, not a hardcoded "it's on".
check "report pins autodispatch OFF at birth (security-default-deny)" \
  '(uit|off|False)' "$REPORT_BLOCK"

# ----------------------------------------------------------------------------
echo "Task 9: NEGATIVE — no fallback to the old intake route"
# Both strings may appear, but ONLY inside a prohibition sentence: that is
# their whole purpose in this file. A bare instruction line would mean the
# skill still has the old carrier wired up.
NEG_FAIL=0
while IFS= read -r line; do
  case "$line" in
    *NIET*|*niet*|*Not*|*not*|*NOT*|*never*|*Never*|*nooit*|*geen*|*Geen*|*No\ *|*vervangt*|*replaced*|*replaces*|*oude*|*old*) ;;
    *) echo "    offending line: $line"; NEG_FAIL=1 ;;
  esac
done < <(grep -nE 'create_project_from_intake' "$SKILL" || true)
if [ "$NEG_FAIL" -eq 0 ]; then
  ok "every create_project_from_intake mention sits in a prohibition"
else
  bad "create_project_from_intake is mentioned outside a prohibition"
fi
# No intake-column card creation: `column="intake"` / `column='intake'` must
# not appear as an argument anywhere.
if grep -qE 'column *= *["'"'"']intake["'"'"']' "$SKILL"; then
  bad "the skill still creates a card with column=\"intake\""
else
  ok "the skill never passes column=\"intake\" to create_card"
fi

# ----------------------------------------------------------------------------
echo "Task 10: no dead .claude/skills/intake-authoring/ pointer left behind"
DEAD=$(grep -rln 'skills/intake-authoring' "$REPO_ROOT/docs" "$REPO_ROOT/.claude" 2>/dev/null || true)
if [ -z "$DEAD" ]; then
  ok "no doc or sibling skill points at the removed intake-authoring path"
else
  bad "dead .claude/skills/intake-authoring/ pointer(s) in:"$'\n'"$DEAD"
fi

# ----------------------------------------------------------------------------
echo "Task 11: sibling skills route the inceptie trigger to new-app"
# The two product-analysis skills carry a division-of-labour table whose
# inceptie row used to name `intake-authoring`. A row still naming the removed
# skill sends a human to a skill that no longer exists.
for SIBLING in product-analysis product-analysis-card; do
  SIB_FILE="$REPO_ROOT/.claude/skills/$SIBLING/SKILL.md"
  if [ ! -f "$SIB_FILE" ]; then
    bad "$SIBLING/SKILL.md missing"
    continue
  fi
  check "$SIBLING routes the inceptie trigger to new-app" '`new-app`' "$SIB_FILE"
done

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
