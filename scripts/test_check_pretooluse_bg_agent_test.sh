#!/usr/bin/env bash
#
# test_check_pretooluse_bg_agent_test.sh — harness for
# scripts/check-pretooluse-bg-agent-test.sh (kanban card
# a712f5c65f1545678f57b1f4ab450514).
#
# Each case builds a throwaway `git init` repo, stages a specific shape, and
# asserts the SUT's verdict. Real-state assertions are SPECIFIC, per the
# CLAUDE.md note: the no-op case greps the exact
# `^OK: no PreToolUse hooks in …` line, never a permissive `^OK:|WARNING:`
# alternation that would pass in both the broken and the fixed state
# (self-improve card e5136a3f).
#
# Coverage:
#   1. Empty settings               -> OK no-op line, exit 0
#   2. Missing settings.json        -> OK "no settings.json" line, exit 0
#   3. Populated + no signal        -> WARN, exit 0 advisory
#   4. Populated + no signal + strict -> WARN, exit 1
#   5. Populated + marker file      -> OK, exit 0
#   6. Populated + test file        -> OK, exit 0
#   7. Populated + both signals     -> OK, exit 0 (either is enough)
#   8. Test file matching only      -> WARN, exit 0 (BOTH tokens required)
#       `pretooluse` (no
#       `background`)
#   9. Case-insensitive test match  -> OK, exit 0
#  10. Carve-out: empty settings    -> re-pin the no-op line, regression pin
#       in this repo (the .claude/settings.json on master ships
#       `"PreToolUse": []`)
#  11. Non-git dir                  -> exit 2
#  12. Unknown argument             -> exit 2

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$SCRIPT_DIR/check-pretooluse-bg-agent-test.sh"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

PASS=0; FAIL=0
ok()  { echo "  ok: $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Build a throwaway repo at $1; remaining args are paths to create + commit.
# `$repo/.claude/settings.json` is created with the SECOND positional after
# the repo, so callers can stage a specific JSON payload.
mkrepo() {
  local repo="$1"; shift
  mkdir -p "$repo"
  git -C "$repo" init -q --initial-branch=main
  git -C "$repo" config user.email t@t
  git -C "$repo" config user.name t
  echo seed > "$repo/README.md"
  git -C "$repo" add README.md
  local p
  for p in "$@"; do
    mkdir -p "$(dirname "$repo/$p")"
    echo x > "$repo/$p"
    git -C "$repo" add -f "$p"
  done
  git -C "$repo" commit -qm seed
}

# Write the JSON to .claude/settings.json for a throwaway repo.
write_settings() {
  local repo="$1"; shift
  local json="$1"; shift
  mkdir -p "$repo/.claude"
  printf '%s' "$json" > "$repo/.claude/settings.json"
}

NOOP_SETTINGS='{"hooks":{"PreToolUse":[]}}'
POPULATED_SETTINGS='{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"echo h"}]}]}}'

echo "== 1. empty settings is a no-op =="
mkrepo "$TMP/empty"
write_settings "$TMP/empty" "$NOOP_SETTINGS"
OUT="$("$SUT" --repo="$TMP/empty" 2>&1)"; RC=$?
if [ "$RC" -eq 0 ]; then ok "exit 0"; else bad "expected exit 0, got $RC"; fi
# SPECIFIC clean-state assertion — not `^OK:|WARNING:`.
if grep -qE "^OK: no PreToolUse hooks in $TMP/empty/.claude/settings.json — background-agent-test gate is a no-op$" <<<"$OUT"; then
  ok "emits the exact no-op OK line"
else
  bad "no-op OK line missing; got: $OUT"
fi

echo "== 2. missing settings.json is clean =="
mkrepo "$TMP/nofile"
# No .claude/settings.json written.
OUT="$("$SUT" --repo="$TMP/nofile" 2>&1)"; RC=$?
if [ "$RC" -eq 0 ]; then ok "exit 0"; else bad "expected exit 0, got $RC"; fi
if grep -qE "^OK: no .* found — no PreToolUse contract to enforce$" <<<"$OUT"; then
  ok "emits the missing-settings OK line"
else
  bad "missing-settings OK line missing; got: $OUT"
fi

echo "== 3. populated + no signal -> WARN (advisory) =="
mkrepo "$TMP/pop" README.md
write_settings "$TMP/pop" "$POPULATED_SETTINGS"
OUT="$("$SUT" --repo="$TMP/pop" 2>&1)"; RC=$?
if [ "$RC" -eq 0 ]; then ok "advisory exits 0"; else bad "expected exit 0, got $RC"; fi
if grep -qE "^WARNING: POPULATED:1 PreToolUse hook\(s\)" <<<"$OUT"; then
  ok "warns with a hook count"
else
  bad "no WARNING line; got: $OUT"
fi
if grep -qE "^  Neither .claude/hooks/pretooluse-bg-agent-test-pass nor a test file" <<<"$OUT"; then
  ok "names both remediation paths"
else
  bad "remediation path not named; got: $OUT"
fi
if grep -q "513e37a1a86e41db8b6af8423292f6b6" <<<"$OUT"; then
  ok "cross-references the underlying incident card"
else
  bad "incident card not cross-referenced in the warning"
fi

echo "== 4. populated + no signal + --strict -> exit 1 =="
OUT="$("$SUT" --repo="$TMP/pop" --strict 2>&1)"; RC=$?
if [ "$RC" -eq 1 ]; then ok "exit 1"; else bad "expected exit 1, got $RC"; fi

echo "== 5. populated + marker file -> OK =="
mkrepo "$TMP/marker"
write_settings "$TMP/marker" "$POPULATED_SETTINGS"
mkdir -p "$TMP/marker/.claude/hooks"
touch "$TMP/marker/.claude/hooks/pretooluse-bg-agent-test-pass"
OUT="$("$SUT" --repo="$TMP/marker" 2>&1)"; RC=$?
if [ "$RC" -eq 0 ]; then ok "exit 0"; else bad "expected exit 0, got $RC"; fi
if grep -qE "^OK: PreToolUse hook\(s\) present and background-agent-test contract met \( marker=\.claude/hooks/pretooluse-bg-agent-test-pass\)$" <<<"$OUT"; then
  ok "emits the marker-pass OK line"
else
  bad "marker-pass OK line missing; got: $OUT"
fi

echo "== 6. populated + test file -> OK =="
mkrepo "$TMP/testfile"
write_settings "$TMP/testfile" "$POPULATED_SETTINGS"
mkdir -p "$TMP/testfile/backend/tests"
touch "$TMP/testfile/backend/tests/test_pretooluse_background_agent_fire.py"
OUT="$("$SUT" --repo="$TMP/testfile" 2>&1)"; RC=$?
if [ "$RC" -eq 0 ]; then ok "exit 0"; else bad "expected exit 0, got $RC"; fi
if grep -qE "^OK: PreToolUse hook\(s\) present.*test_match=test_pretooluse_background_agent_fire\.py" <<<"$OUT"; then
  ok "names the matched test file"
else
  bad "test_match not named; got: $OUT"
fi

echo "== 7. populated + both signals -> OK =="
mkrepo "$TMP/both"
write_settings "$TMP/both" "$POPULATED_SETTINGS"
mkdir -p "$TMP/both/.claude/hooks" "$TMP/both/backend/tests"
touch "$TMP/both/.claude/hooks/pretooluse-bg-agent-test-pass"
touch "$TMP/both/backend/tests/test_pretooluse_background_agent_fire.py"
OUT="$("$SUT" --repo="$TMP/both" 2>&1)"; RC=$?
if [ "$RC" -eq 0 ]; then ok "exit 0"; else bad "expected exit 0, got $RC"; fi
if grep -qE "^OK: .*\( marker=.* test_match=test_pretooluse_background_agent_fire\.py\)$" <<<"$OUT"; then
  ok "names both signals"
else
  bad "both-signal OK line missing; got: $OUT"
fi

echo "== 8. test file matching only 'pretooluse' (no 'background') -> WARN =="
mkrepo "$TMP/half"
write_settings "$TMP/half" "$POPULATED_SETTINGS"
mkdir -p "$TMP/half/backend/tests"
touch "$TMP/half/backend/tests/test_pretooluse_x.py"
OUT="$("$SUT" --repo="$TMP/half" 2>&1)"; RC=$?
if [ "$RC" -eq 0 ]; then ok "advisory exits 0"; else bad "expected exit 0, got $RC"; fi
if grep -qE "^WARNING: POPULATED:1 PreToolUse hook\(s\)" <<<"$OUT"; then
  ok "still warns (BOTH tokens required)"
else
  bad "should warn — only one token matched; got: $OUT"
fi

echo "== 9. case-insensitive test match -> OK =="
mkrepo "$TMP/case"
write_settings "$TMP/case" "$POPULATED_SETTINGS"
mkdir -p "$TMP/case/backend/tests"
touch "$TMP/case/backend/tests/test_PreToolUse_Background_Agent.py"
OUT="$("$SUT" --repo="$TMP/case" 2>&1)"; RC=$?
if [ "$RC" -eq 0 ] && grep -qE "^OK: .*test_match=test_PreToolUse_Background_Agent\.py" <<<"$OUT"; then
  ok "case-insensitive match works"
else
  bad "expected case-insensitive match; got: $OUT"
fi

echo "== 10. live repo regression pin (no-op today) =="
if [ -f "$REPO_ROOT/.claude/settings.json" ]; then
  OUT="$("$SUT" --repo="$REPO_ROOT" --strict 2>&1)"; RC=$?
  if [ "$RC" -eq 0 ]; then
    ok "this repo stays clean under --strict (PreToolUse: [])"
  else
    bad "live repo regression pin tripped: $OUT"
  fi
else
  ok "SKIP: no .claude/settings.json at $REPO_ROOT"
fi

echo "== 11. non-git directory =="
mkdir -p "$TMP/notgit"
"$SUT" --repo="$TMP/notgit" >/dev/null 2>&1; RC=$?
if [ "$RC" -eq 2 ]; then ok "exit 2"; else bad "expected exit 2, got $RC"; fi

echo "== 12. unknown argument =="
"$SUT" --nope >/dev/null 2>&1; RC=$?
if [ "$RC" -eq 2 ]; then ok "exit 2"; else bad "expected exit 2, got $RC"; fi

echo
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
