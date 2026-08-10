#!/usr/bin/env bash
# Test harness for scripts/check-doc-links.sh.
#
# Exercises relative Markdown-link validation against synthetic fixture dirs:
#
#   1. arg parsing — `--help` works.
#   2. clean case — same-dir, parent, bare, anchored, and image targets exist.
#   3. non-local links — document anchors and external URLs are ignored.
#   4. drift case — a missing target is reported without failing (advisory).
#   5. anchors — the fragment is stripped before checking the target.
#   6. --strict — the same drift exits 1.
#   7. error path — a missing docs directory exits 2.
#   8. real tree — docs/cockpit, .claude/skills, and .claude/agents are link-clean.
#   9. deep skill scope — links resolve relative to the source file's directory,
#      not the docs root, so a skill at .claude/skills/foo/SKILL.md can reach
#      docs/cockpit via `../../../docs/cockpit/...`.
#  10. deep drift — a broken deeper link is reported with the file path
#      relative to the repo root.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$SCRIPT_DIR/check-doc-links.sh"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Run the SUT against a single scope by redirecting the other two to
# non-existent paths so they're silently skipped. Existing fixtures test
# only the docs/cockpit path; the deeper-scope fixtures (tasks 9/10) point
# CLAUDE_SKILLS_DIR at their own tree.
run_scope() {
  local docs="$1"
  shift
  DOCS_DIR="$docs" \
    CLAUDE_SKILLS_DIR="$TMP/no-such-skills" \
    CLAUDE_AGENTS_DIR="$TMP/no-such-agents" \
    bash "$SUT" "$@"
}

# ----------------------------------------------------------------------------
echo "Task 1: arg parsing — --help"
out=$(bash "$SUT" --help 2>&1 || true)
check "--help mentions Usage" 'echo "$out" | grep -qE "Usage:"'
check "--help mentions --strict" 'echo "$out" | grep -qE "\-\-strict"'
check "--help documents CLAUDE_SKILLS_DIR" 'echo "$out" | grep -qF "CLAUDE_SKILLS_DIR"'
check "--help documents CLAUDE_AGENTS_DIR" 'echo "$out" | grep -qF "CLAUDE_AGENTS_DIR"'

# ----------------------------------------------------------------------------
echo "Task 2/3: clean relative links and ignored non-local links"
clean="$TMP/clean/docs"; mkdir -p "$clean/assets"
printf '# Same directory\n' > "$clean/target.md"
printf '# Parent directory\n' > "$TMP/clean/shared.md"
printf 'image' > "$clean/assets/diagram.png"
cat > "$clean/index.md" <<'EOF'
[explicit same-dir](./target.md)
[bare same-dir](target.md)
[parent with anchor](../shared.md#details)
![relative image](./assets/diagram.png)
[document anchor](#local-heading)
[external URL](https://example.com/missing)
[mail address](mailto:test@example.com)
EOF
out=$(run_scope "$clean" 2>&1); rc=$?
check "clean → exit 0" '[ "$rc" -eq 0 ]'
check "clean → prints OK" 'echo "$out" | grep -qE "^OK:"'
check "clean → no warnings" '! echo "$out" | grep -qE "WARNING:"'

# ----------------------------------------------------------------------------
echo "Task 4/5: missing anchored target is advisory and reported"
drift="$TMP/drift"; mkdir -p "$drift"
cat > "$drift/source.md" <<'EOF'
[missing source of truth](./missing.md#canonical-section)
EOF
out=$(run_scope "$drift" 2>&1); rc=$?
check "drift → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
check "drift → prints WARNING" 'echo "$out" | grep -qE "WARNING:"'
check "drift → names the source doc" 'echo "$out" | grep -qF "source.md"'
check "drift → reports the original anchored link" 'echo "$out" | grep -qF "./missing.md#canonical-section"'
check "drift → reports exactly 1 broken link" 'echo "$out" | grep -qE "WARNING: 1 broken relative Markdown link"'

# Prove the anchor is removed for the filesystem check: creating the path without
# a literal #canonical-section makes the same fixture clean.
printf '# Now present\n' > "$drift/missing.md"
out=$(run_scope "$drift" 2>&1); rc=$?
check "existing target with anchor → exit 0" '[ "$rc" -eq 0 ]'
check "existing target with anchor → prints OK" 'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 6: --strict turns drift into a failure"
strict="$TMP/strict"; mkdir -p "$strict"
printf '[missing](../absent.md)\n' > "$strict/source.md"
out=$(run_scope "$strict" --strict 2>&1); rc=$?
check "drift + --strict → exit 1" '[ "$rc" -eq 1 ]'
check "drift + --strict → still names the target" 'echo "$out" | grep -qF "../absent.md"'

# ----------------------------------------------------------------------------
echo "Task 7: error path — no scope dir at all"
out=$(DOCS_DIR="$TMP/does-not-exist" \
      CLAUDE_SKILLS_DIR="$TMP/does-not-exist" \
      CLAUDE_AGENTS_DIR="$TMP/does-not-exist" \
      bash "$SUT" 2>&1); rc=$?
check "no scope → exit 2" '[ "$rc" -eq 2 ]'
check "no scope → ERROR" 'echo "$out" | grep -qE "ERROR:.*docs"'

# ----------------------------------------------------------------------------
echo "Task 8: the repo's full scope (docs + skills + agents) is link-clean"
out=$(bash "$SUT" --strict 2>&1); rc=$?
check "real repo → exit 0 under --strict" '[ "$rc" -eq 0 ]'
check "real repo → prints OK" 'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 9: deep skill scope — links resolve relative to the source file"
deepclean="$TMP/deepclean"; mkdir -p "$deepclean/.claude/skills/foo"
mkdir -p "$deepclean/docs/cockpit"
printf '# Target\n' > "$deepclean/docs/cockpit/target.md"
cat > "$deepclean/.claude/skills/foo/SKILL.md" <<'EOF'
# Skill
[link to cockpit](../../../docs/cockpit/target.md)
EOF
out=$(DOCS_DIR="$deepclean/no-such" \
      CLAUDE_SKILLS_DIR="$deepclean/.claude/skills" \
      CLAUDE_AGENTS_DIR="$TMP/no-such-agents" \
      bash "$SUT" 2>&1); rc=$?
check "deep clean → exit 0" '[ "$rc" -eq 0 ]'
check "deep clean → prints OK" 'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 10: deep drift — broken link in a skill file is reported"
deepdrift="$TMP/deepdrift"; mkdir -p "$deepdrift/.claude/skills/foo"
cat > "$deepdrift/.claude/skills/foo/SKILL.md" <<'EOF'
# Skill
[link to missing](../../../docs/cockpit/missing.md)
EOF
out=$(DOCS_DIR="$deepdrift/no-such" \
      CLAUDE_SKILLS_DIR="$deepdrift/.claude/skills" \
      CLAUDE_AGENTS_DIR="$TMP/no-such-agents" \
      bash "$SUT" 2>&1); rc=$?
check "deep drift → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
check "deep drift → prints WARNING" 'echo "$out" | grep -qE "WARNING:"'
check "deep drift → reports the path relative to repo root" \
  'echo "$out" | grep -qF ".claude/skills/foo/SKILL.md"'
check "deep drift → reports the original link" \
  'echo "$out" | grep -qF "../../../docs/cockpit/missing.md"'

# And --strict on the same fixture flips the exit code.
out=$(DOCS_DIR="$deepdrift/no-such" \
      CLAUDE_SKILLS_DIR="$deepdrift/.claude/skills" \
      CLAUDE_AGENTS_DIR="$TMP/no-such-agents" \
      bash "$SUT" --strict 2>&1); rc=$?
check "deep drift + --strict → exit 1" '[ "$rc" -eq 1 ]'

# ----------------------------------------------------------------------------
echo "Task 11: agents scope — top-level .claude/agents/*.md is scanned"
agents="$TMP/agents"; mkdir -p "$agents/.claude/agents"
mkdir -p "$agents/docs/cockpit"
printf '# Target\n' > "$agents/docs/cockpit/target.md"
cat > "$agents/.claude/agents/reviewer.md" <<'EOF'
# Reviewer
[link to cockpit](../../docs/cockpit/target.md)
EOF
out=$(DOCS_DIR="$agents/no-such" \
      CLAUDE_SKILLS_DIR="$TMP/no-such-skills" \
      CLAUDE_AGENTS_DIR="$agents/.claude/agents" \
      bash "$SUT" 2>&1); rc=$?
check "agents clean → exit 0" '[ "$rc" -eq 0 ]'
check "agents clean → prints OK" 'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 12: fence-on-last-line — next file's line numbers are correct"
# Repro for kanban card 216c8ada…: a file ending on a fence line previously
# skipped the per-file reset and either inflated line numbers or (worse)
# silently skipped the next file because $fenced stayed non-zero. The
# filenames sort a-z, so the fence-end file is always processed before
# the link-bearing one.
fence_end="$TMP/fence-end"; mkdir -p "$fence_end"
{
  printf '# Doc A — ends on a fenced block\n'
  printf 'paragraph one\n'
  for i in $(seq 3 116); do printf 'line %d\n' "$i"; done
  printf '```bash\n'    # line 117 — file ends here, on a fence
} > "$fence_end/aaa-fence-end.md"
cat > "$fence_end/bbb-dead-link.md" <<'EOF'
# Doc B — broken link on a known line
intro
[missing reference](./does-not-exist.md)
EOF
out=$(run_scope "$fence_end" 2>&1); rc=$?
check "fence-end → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
check "fence-end → prints WARNING" 'echo "$out" | grep -qE "WARNING:"'
# The actual line of the link is 3 in bbb-dead-link.md. The bug would
# either (a) report an inflated line (117 + 3 = 120) or (b) silently
# skip the file (no warning at all).
check "fence-end → reports the EXPECTED line number 3" \
  'echo "$out" | grep -qE "bbb-dead-link.md:3\b"'
check "fence-end → does NOT report an inflated line 120" \
  '! echo "$out" | grep -qE "bbb-dead-link.md:120\b"'
check "fence-end → names the dead link target" \
  'echo "$out" | grep -qF "./does-not-exist.md"'

# ----------------------------------------------------------------------------
echo "Task 13: even line count — clean run no false reports"
fence_clean="$TMP/fence-clean"; mkdir -p "$fence_clean"
{
  printf '# A\n'
  printf '```bash\n'
  printf 'echo inside fence\n'
  printf '```\n'
  printf 'plain link to existing\n'
} > "$fence_clean/aaa-fenced-clean.md"
cat > "$fence_clean/bbb-clean.md" <<'EOF'
# B
[a link to nothing special](./does-not-exist.md)
EOF
# No expected false negatives here. The dead link in bbb-clean.md should
# still be reported with its real line number (line 2), not the sum of
# aaa-fenced-clean.md's line count + 2.
out=$(run_scope "$fence_clean" 2>&1); rc=$?
check "fence-clean → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
check "fence-clean → reports the EXPECTED line number 2" \
  'echo "$out" | grep -qE "bbb-clean.md:2\b"'

# ----------------------------------------------------------------------------
echo "Task 14: odd fence count — second file is NOT silently skipped"
# A file with an UNMATCHED opening fence leaves $fenced = 1 across the
# file boundary under the old code. The next file's body would `next if
# $fenced;` for every line and report zero broken links — a silent false
# negative even when the next file genuinely has dead links.
unbalanced="$TMP/unbalanced"; mkdir -p "$unbalanced"
{
  printf '# First — opens a fence but never closes\n'
  printf '```bash\n'
  printf 'echo orphan code block\n'
} > "$unbalanced/aaa-unbalanced-fence.md"
cat > "$unbalanced/bbb-real-dead-link.md" <<'EOF'
# Second — has a real dead link
intro line
[missing](./nope.md)
trailer
EOF
out=$(run_scope "$unbalanced" 2>&1); rc=$?
check "unbalanced-fence → exit 0 (advisory)" '[ "$rc" -eq 0 ]'
check "unbalanced-fence → prints WARNING (link NOT silently skipped)" \
  'echo "$out" | grep -qE "WARNING:"'
check "unbalanced-fence → names bbb-real-dead-link.md" \
  'echo "$out" | grep -qF "bbb-real-dead-link.md"'
check "unbalanced-fence → reports the link target" \
  'echo "$out" | grep -qF "./nope.md"'
check "unbalanced-fence → reports the EXPECTED line 3 (not 5+)" \
  'echo "$out" | grep -qE "bbb-real-dead-link.md:3\b"'

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
