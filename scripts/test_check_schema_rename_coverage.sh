#!/usr/bin/env bash
# Test harness for scripts/check-schema-rename-coverage.sh.
#
# Builds a fake git repo under TMPDIR (one initial commit on master, one
# rename on a feature branch) and exercises the script's arg parser,
# auto-detect, explicit --rename, --strict, --list-all, scope (no
# docs/frontend), and exit-code paths without ever touching the real
# repo's git state.
#
# Coverage:
#   1. --help mentions Usage + --rename + --strict + --root
#   2. unknown arg → exit 2
#   3. auto-detect on the rename branch → leak(s) listed, advisory exit 0
#   4. auto-detect --strict on the rename branch → leak(s) listed, exit 1
#   5. explicit --rename <table> <old> <new> --strict → exit 1
#   6. explicit bare --rename <old> <new> --strict → exit 1
#   7. --rename with only 1 arg → exit 2
#   8. --list-all --strict shows every hit (no truncation)
#   9. scope: stale refs in docs/ + frontend/ are NOT flagged
#  10. multiple --rename + --strict aggregates hits and exits 1
#  11. clean fixture (no renames in diff) → exit 0 with "no renames detected"
#  12. fixture with NO leak after a rename → exit 0 (the canonical 558ca55
#      scenario, post-fix)
#  13. --rename foo --strict (3-arg form then bare) → 3-arg consumes all
#      three, no "stray" leak detection from --strict
#  14. --root points script at a different tree (the test fixture itself)
#
# This harness uses --root to point the script at the fixture so it
# operates purely on the test's git repo — never on the engineer's
# checked-out code. The script does NOT mutate any persistent state.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$SCRIPT_DIR/check-schema-rename-coverage.sh"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
# Don't auto-clean TMP — when a test fails we want the fixture left around
# so the operator can `ls /tmp/...` to debug. Move to a stable suffix name
# on EXIT so it doesn't pile up indefinitely.
trap 'mv "$TMP" "${TMP}.leftover" >/dev/null 2>&1 || true' EXIT

# ----------------------------------------------------------------------------
# Build the fixture repo. This mirrors the shape of 558ca55 (a column rename
# on a feature branch, one of the two downstream consumers correctly updated,
# the other still using the old name).
make_fixture() {
    local root="$1"
    rm -rf "$root"
    mkdir -p "$root/backend/app" "$root/backend/tests" "$root/docs" "$root/frontend/src"
    # master: clean state, all-new names
    cat > "$root/backend/app/foo.py" <<'EOF'
class Foo:
    cli: str = "x"
    def get(self):
        return self.cli
EOF
    cat > "$root/backend/tests/test_foo.py" <<'EOF'
def test_foo():
    obj = Foo()
    assert obj.cli == "x"
EOF
    # Decoy refs in non-scoped dirs (must NOT be flagged).
    echo "stale .bar ref to verify scope" > "$root/docs/foo.md"
    echo "obj.bar in frontend src" > "$root/frontend/src/x.tsx"
    git -C "$root" init -q -b master
    git -C "$root" config user.email "t@t" && git -C "$root" config user.name "t"
    git -C "$root" add -A
    git -C "$root" commit -qm initial
    # feature branch: rename + intentional leak in test_baz.py
    git -C "$root" checkout -q -b my-rename
    cat >> "$root/backend/app/foo.py" <<'EOF'

# Future migration:
# ALTER TABLE baztab RENAME COLUMN bar TO baz
EOF
    cat > "$root/backend/tests/test_baz.py" <<'EOF'
def test_baz():
    obj = Baz()
    # STALE: forgot to rename in this test
    assert obj.bar == "x"
    BazClass(bar="y")
EOF
    git -C "$root" add -A
    git -C "$root" commit -qm rename
    # Set up origin/master as a ref pointing to master (so auto-detect can
    # `git diff origin/master`).
    git -C "$root" update-ref refs/remotes/origin/master \
        "$(git -C "$root" rev-parse master)"
    git -C "$root" checkout -q my-rename
}

# Helper: run the SUT with --root pointing at the fixture, capture stdout
# and exit code. Extra args are forwarded to the script after --root.
# Usage: run_sut [SUT_ARG ...]
run_sut() {
    local out_file
    out_file="$(mktemp)"
    bash "$SUT" --root "$FIXTURE" "$@" >"$out_file" 2>&1
    local rc=$?
    SUT_OUT="$(cat "$out_file")"
    SUT_RC=$rc
    rm -f "$out_file"
}

# ----------------------------------------------------------------------------
echo "Task 1: --help mentions Usage + the documented flags"
out=$(bash "$SUT" --help 2>&1)
check "--help mentions Usage" \
    'echo "$out" | grep -qE "Usage:"'
check "--help mentions --rename" \
    'echo "$out" | grep -qE "\-\-rename"'
check "--help mentions --strict" \
    'echo "$out" | grep -qE "\-\-strict"'
check "--help mentions --root" \
    'echo "$out" | grep -qE "\-\-root"'

# ----------------------------------------------------------------------------
echo
echo "Task 2: unknown arg → exit 2"
# Task 2 needs *some* fixture in place so --root can resolve, even though
# the unknown arg short-circuits before any scope check. The fixture is
# built in Task 3 (one line below); set a placeholder so `set -u` is happy.
FIXTURE="$TMP/placeholder_for_unknown_arg"
mkdir -p "$FIXTURE"
run_sut --bogus
check "unknown arg exits 2" '[ "$SUT_RC" = "2" ]'
check "unknown arg prints 'unknown argument'" \
    'echo "$SUT_OUT" | grep -qE "unknown argument"'

# ----------------------------------------------------------------------------
echo
echo "Task 3: auto-detect on rename branch → leaks listed, advisory exit 0"
make_fixture "$TMP/fx"
FIXTURE="$TMP/fx"
run_sut
check "auto-detect surfaces the [leak] line" \
    'echo "$SUT_OUT" | grep -qE "\[leak\] rename bar -> baz \(table=baztab\)"'
check "auto-detect advisory exit 0" '[ "$SUT_RC" = "0" ]'
check "auto-detect summary line mentions 2 stale refs" \
    'echo "$SUT_OUT" | grep -qE "2 stale reference"'

# ----------------------------------------------------------------------------
echo
echo "Task 4: auto-detect --strict on rename branch → exit 1"
run_sut --strict
check "auto-detect --strict exits 1" '[ "$SUT_RC" = "1" ]'
check "--strict prints the blocker hint" \
    'echo "$SUT_OUT" | grep -qE "strict set.*exiting 1"'

# ----------------------------------------------------------------------------
echo
echo "Task 5: explicit --rename <table> <old> <new> --strict → exit 1"
run_sut --rename baztab bar baz --strict
check "explicit 3-arg --rename --strict exits 1" '[ "$SUT_RC" = "1" ]'
check "explicit 3-arg surfaces the [leak] line" \
    'echo "$SUT_OUT" | grep -qE "\[leak\] rename bar -> baz \(table=baztab\)"'

# ----------------------------------------------------------------------------
echo
echo "Task 6: explicit bare --rename <old> <new> --strict → exit 1"
run_sut --rename Foo Bar --strict
check "explicit bare --rename --strict exits 1" '[ "$SUT_RC" = "1" ]'
check "explicit bare surfaces 'table=' (empty table)" \
    'echo "$SUT_OUT" | grep -qE "\[leak\] rename Foo -> Bar \(table=\)"'

# ----------------------------------------------------------------------------
echo
echo "Task 7: --rename with only 1 arg → exit 2"
run_sut --rename foo
check "--rename with 1 arg exits 2" '[ "$SUT_RC" = "2" ]'
check "--rename 1-arg error message" \
    'echo "$SUT_OUT" | grep -qE "needs at least"'

# ----------------------------------------------------------------------------
echo
echo "Task 8: --list-all shows every hit (no truncation)"
# Add a 12th hit so we exceed the default HIT_CAP=10.
cat >> "$FIXTURE/backend/tests/test_baz.py" <<'EOF'

def extra_hits():
    a = obj.bar
    b = obj.bar
    c = obj.bar
    d = obj.bar
    e = obj.bar
    f = obj.bar
    g = obj.bar
    h = obj.bar
    i = obj.bar
    j = obj.bar
    k = obj.bar
    return a,b,c,d,e,f,g,h,i,j,k
EOF
git -C "$FIXTURE" add -A && git -C "$FIXTURE" commit -qm "more leaks"
git -C "$FIXTURE" update-ref refs/remotes/origin/master \
    "$(git -C "$FIXTURE" rev-parse my-rename~1)"
run_sut --rename baztab bar baz --list-all
check "--list-all does NOT print truncation message" \
    '! echo "$SUT_OUT" | grep -qE "more.*--list-all"'

# ----------------------------------------------------------------------------
echo
echo "Task 9: scope check — docs/ + frontend/ refs NOT flagged"
# docs/foo.md has 'stale .bar ref to verify scope' and frontend/src/x.tsx
# has 'obj.bar in frontend src'. The script must not pick these up.
run_sut --strict
check "scope: no docs/ hits" \
    '! echo "$SUT_OUT" | grep -qE "docs/foo\.md"'
check "scope: no frontend/ hits" \
    '! echo "$SUT_OUT" | grep -qE "frontend/src/x\.tsx"'

# ----------------------------------------------------------------------------
echo
echo "Task 10: multiple --rename + --strict aggregates across renames"
run_sut --rename baztab bar baz --rename Foo Bar --strict
check "multi-rename exits 1" '[ "$SUT_RC" = "1" ]'
check "multi-rename reports 2 leaky renames" \
    'echo "$SUT_OUT" | grep -qE "across 2 rename"'

# ----------------------------------------------------------------------------
echo
echo "Task 11: clean fixture (no renames in diff) → exit 0"
git -C "$FIXTURE" checkout -q master
run_sut
check "clean fixture exits 0" '[ "$SUT_RC" = "0" ]'
check "clean fixture prints 'no renames detected'" \
    'echo "$SUT_OUT" | grep -qE "no renames detected"'

# ----------------------------------------------------------------------------
echo
echo "Task 12: 558ca55-shaped scenario — rename committed, all consumers updated"
make_fixture "$TMP/clean"
# Update test_baz.py to use the new name (no leak) — sed replaces the
# dotted access AND the keyword-arg form so the script's patterns find
# nothing.
git -C "$TMP/clean" checkout -q my-rename
sed -i 's/\.bar/.baz/g; s/bar="/baz="/g' "$TMP/clean/backend/tests/test_baz.py"
git -C "$TMP/clean" add -A && git -C "$TMP/clean" commit -qm "fix downstream"
# origin/master must point at the ORIGINAL clean state (master), not at
# my-rename~1 — `~1` would resolve to the commit that ADDED the
# `ALTER TABLE` comment, which would make auto-detect find nothing.
git -C "$TMP/clean" update-ref refs/remotes/origin/master \
    "$(git -C "$TMP/clean" rev-parse master)"
FIXTURE="$TMP/clean"
run_sut --strict
check "no-leak fixture exits 0 with --strict" '[ "$SUT_RC" = "0" ]'
check "no-leak fixture prints 'clean'" \
    'echo "$SUT_OUT" | grep -qE "clean.*0 stale"'

# ----------------------------------------------------------------------------
echo
echo "Task 13: --rename in 3-arg form consumes exactly 3 positionals"
# If a future flag like --strict appears after the 3 positionals, it must
# still be parsed (this guards the arg-parser fix where --strict was being
# eaten as a3).
make_fixture "$TMP/argp"
FIXTURE="$TMP/argp"
run_sut --rename baztab bar baz --strict --list-all
check "3-arg form: --strict still parsed" '[ "$SUT_RC" = "1" ]'
check "3-arg form: --list-all applied (no truncation msg)" \
    '! echo "$SUT_OUT" | grep -qE "more.*--list-all"'

# ----------------------------------------------------------------------------
echo
echo "Task 14: --root pointing at a subdirectory with no backend/ → clean"
# Must be a real git repo with origin/master set; backend/ subtree is
# intentionally empty. We use explicit --rename so the script reaches the
# file-scope branch (auto-detect would short-circuit before scanning).
mkdir -p "$TMP/empty"
git -C "$TMP/empty" init -q -b master
git -C "$TMP/empty" config user.email t@t && git -C "$TMP/empty" config user.name t
git -C "$TMP/empty" commit -q --allow-empty -m "empty root"
git -C "$TMP/empty" update-ref refs/remotes/origin/master \
    "$(git -C "$TMP/empty" rev-parse master)"
FIXTURE="$TMP/empty"
run_sut --rename foo bar baz
check "empty --root exits 0 (no files in scope)" '[ "$SUT_RC" = "0" ]'
check "empty --root prints 'no files in scope'" \
    'echo "$SUT_OUT" | grep -qE "no files in scope"'

# ----------------------------------------------------------------------------
mv "$TMP" "${TMP}.leftover" >/dev/null 2>&1 || true
echo
echo "===================="
echo "Passed: $PASS  Failed: $FAIL"
echo "===================="
[ "$FAIL" -eq 0 ]