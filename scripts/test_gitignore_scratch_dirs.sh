#!/usr/bin/env bash
# Test harness for the .gitignore entries that gate ship pre-flight.
#
# Coverage:
#   1. .tmp-measure-token-saver/ is listed in .gitignore (the literal pattern
#      used by scripts/measure-token-saver.sh at line 102 for its default
#      RESULT_DIR). Without this, every scripts/test_*.sh gate invocation
#      leaves an untracked scratch dir in the worktree root that trips
#      `git ls-files --others --exclude-standard` during ship.
#   2. The pattern is non-empty (i.e. it has actual content, not just a
#      CRLF line that git's check-ignore falsely reports as a match at
#      exit 0 — kanban card c28e576d…). A bare CRLF at "line 88" was the
#      root cause that made the symptom invisible to grep / check-ignore
#      while still leaving the dir untracked under git status.
#   3. A sample scratch dir matching the pattern is genuinely ignored:
#      `git status --porcelain` does not show it as `??`, and a file
#      created inside the dir (the level measure-token-saver.sh actually
#      writes — `<dir>/<ts>/...`) is ignored too. This is the load-
#      bearing check; if (1) passes but (3) fails, the pattern is
#      malformed (e.g. trailing whitespace from CRLF) and the ship
#      pre-flight will still trip.
#   4. .gitignore has LF line endings, not CRLF. The CRLF in this file
#      was what made git's parser accept an empty pattern that looked
#      like a real match in `check-ignore -v` output. Cross-check by
#      checking `file .gitignore` and counting carriage returns.
#
# This harness exists because of kanban card 8b48152e… (`measure-token-
# saver.sh` leaves untracked scratch dirs in the worktree root).
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GITIGNORE="$REPO_ROOT/.gitignore"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# ---------------------------------------------------------------------------
echo "Task 1: .tmp-measure-token-saver/ is listed in .gitignore"

# Strip carriage returns before grepping — CRLF in the source file would
# otherwise hide the pattern from a literal-text grep (the pattern itself
# has no \r, so a CRLF-terminated line never matches a clean grep).
PATTERN_LINE="$(tr -d '\r' < "$GITIGNORE" | grep -nE '^\.tmp-measure-token-saver/?$' || true)"
check ".gitignore has a clean .tmp-measure-token-saver/ entry" \
    '[ -n "$PATTERN_LINE" ]'

# ---------------------------------------------------------------------------
echo "Task 2: a sample scratch dir matching the pattern is gitignored"

# Build a throwaway git repo seeded with this project's .gitignore, then
# drop a scratch dir matching the pattern and verify git actually ignores
# it (NOT just that check-ignore -v reports a false-positive match on a
# blank CRLF line — see the comment in kanban card 8b48152e… and the
# `check-ignore` lie test below).
TMP="$(mktemp -d)"
trap 'mv "$TMP" "$TMP.parked-$$" 2>/dev/null || true' EXIT
REPO="$TMP/repo"
mkdir -p "$REPO"
( cd "$REPO" && \
  git init -q -b master && \
  git config user.email t@t && \
  git config user.name t && \
  cp "$GITIGNORE" .gitignore && \
  git add .gitignore && \
  git commit -qm seed )

SCRATCH="$REPO/.tmp-measure-token-saver/20260101T000000Z-test"
mkdir -p "$SCRATCH"
echo payload > "$SCRATCH/marker"

# Load-bearing check: git status must not show the dir as untracked.
# This is the check the original symptom failed; if check-ignore were the
# only assertion, the CRLF-blank-line false-positive would slip past.
STATUS_OUT="$(cd "$REPO" && git status --porcelain)"
check "git status --porcelain does not show scratch dir as ??" \
    '! grep -q "^?? \.tmp-measure-token-saver/" <<<"$STATUS_OUT"'
check "git status --porcelain does not show inner marker file as ??" \
    '! grep -q "^?? \.tmp-measure-token-saver/.*marker" <<<"$STATUS_OUT"'

# Cross-check check-ignore itself returns exit 0 (match) for the inner
# file. The exit code is the load-bearing signal — `-v` output alone is
# not, because git reports a false-positive match on empty patterns.
( cd "$REPO" && git check-ignore ".tmp-measure-token-saver/20260101T000000Z-test/marker" >/dev/null )
check "git check-ignore returns exit 0 (match) for inner marker file" \
    '[ "$?" = "0" ]'

# Red-herring guard: a bare-CRLF blank line in .gitignore used to make
# `check-ignore -v` show a "match at line N" output that looked correct
# but did nothing — the dir was still untracked. Verify the pattern
# reported by `check-ignore -v` is non-empty for the inner file (the
# actual real-world path that measure-token-saver.sh writes).
CHECK_IGNORE_OUT="$(cd "$REPO" && git check-ignore -v ".tmp-measure-token-saver/20260101T000000Z-test/marker" 2>/dev/null || true)"
# Format: <source>:<linenum>:<pattern>\t<pathname>. An empty pattern field
# (the CRLF-blank-line lie) collapses to `<source>:<linenum>:\t<path>` with
# the pattern slot being just a tab character — `<source>:<linenum>:` followed
# immediately by whitespace. A real pattern has at least one non-whitespace
# character right after the second colon.
check "check-ignore -v reports a NON-empty pattern (no CRLF-blank-line lie)" \
    'echo "$CHECK_IGNORE_OUT" | grep -qE "^\.gitignore:[0-9]+:[[:graph:]]"'

# ---------------------------------------------------------------------------
echo "Task 3: .gitignore uses LF line endings (CRLF was the trap)"

CRLF_COUNT="$(tr -cd '\r' < "$GITIGNORE" | wc -c | tr -d " ")"
check ".gitignore contains zero CR bytes (LF line endings)" \
    '[ "${CRLF_COUNT:-0}" = "0" ]'

FILE_KIND="$(file -b "$GITIGNORE" 2>/dev/null || true)"
check "file(1) does NOT report CRLF line terminators on .gitignore" \
    '! grep -q "CRLF" <<<"$FILE_KIND"'

# ---------------------------------------------------------------------------
echo ""
echo "Summary: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
