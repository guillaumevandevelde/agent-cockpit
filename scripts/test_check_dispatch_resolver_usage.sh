#!/usr/bin/env bash
# Test harness for scripts/check-dispatch-resolver-usage.sh.
#
# Builds a synthetic dispatch.py under TMPDIR and exercises the script's
# scope (resolver-body exclusion, docstring exclusion, bypass annotation),
# pattern detection, exit codes, and --file= behaviour. The script
# itself is read-only against the actual repo, and the harness never
# touches backend/app/kanban/dispatch.py — every fixture lives under
# TMPDIR and is moved aside on EXIT for forensic inspection.
#
# Coverage:
#   1. --help mentions Usage + --strict + --file
#   2. unknown arg → exit 2
#   3. --file= pointing at a missing path → exit 2
#   4. clean fixture (no patterns) → exit 0
#   5. hit detection (column_override.get) → reports + exit 0 in advisory
#   6. hit detection + --strict → exit 1
#   7. bypass annotation `# resolver-bypass:` suppresses the hit
#   8. resolver function body is excluded (the canonical
#      `resolve_effective_provider_and_model` reads don't trip the script)
#   9. docstring lines are excluded (the script's self-documenting
#      patterns in the leading comment / docstring don't trip)
#  10. multiple patterns in one fixture aggregate into one warning
#
# Self-cleanup: TMP is moved to <TMP>.leftover on EXIT so a failed
# fixture persists for the operator to inspect — `rm -rf` is
# deny-listed in .claude/settings.json so we use `mv`-on-exit instead.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$SCRIPT_DIR/check-dispatch-resolver-usage.sh"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'mv "$TMP" "${TMP}.leftover" >/dev/null 2>&1 || true' EXIT

# ---
# Helper: invoke the SUT with --file= pointing at a fixture. Stashes
# stdout / stderr / exit code in $SOUT / $SERR / $SRC and supports a
# leading set of args (e.g. `--strict`) before --file=.
run_sut() {
  local extra="$1" file="$2"
  local sout serr
  sout="$(mktemp)"; serr="$(mktemp)"
  # shellcheck disable=SC2086
  bash "$SUT" $extra --file="$file" >"$sout" 2>"$serr"
  SRC=$?
  SOUT="$(cat "$sout")"; SERR="$(cat "$serr")"
  mv "$sout" "${sout}.leftover" 2>/dev/null || true
  mv "$serr" "${serr}.leftover" 2>/dev/null || true
}

# ---
# Fixture: minimal dispatch.py with the canonical resolver stub + a
# couple of hits for the patterns under test. The resolver body carries
# the actual patterns too (it's the implementation), so this fixture
# also validates the resolver-body exclusion.
make_fixture_resolver_plus_hits() {
  local root="$1"
  mkdir -p "$root"
  cat > "$root/dispatch.py" <<'EOF'
# synthetic dispatch.py for tests

# This is a docstring-style block that mentions the patterns; the
# script must NOT flag these because docstrings are excluded.
#
# Example patterns referenced for documentation:
#   column_override.get("provider")
#   get_column_default_provider(
#   get_column_default_model(
#   getattr(card, "model"

async def resolve_effective_provider_and_model(session, *, project_key, target_agent):
    """The canonical resolver — its body legitimately reads the
    helpers/fields below because it IS the implementation of the chain.
    The script must skip this range."""
    column_override = {"provider": "anthropic"}
    override_provider = column_override.get("provider") or None
    column_default_provider = await get_column_default_provider(session, project_key, target_agent)
    return override_provider or column_default_provider

async def some_helper():
    column_override = {"provider": "anthropic"}
    override_provider = column_override.get("provider") or None
    return override_provider
EOF
}

# 1. --help mentions Usage + --strict + --file
check "help: mentions --strict" \
  'bash "$SUT" --help 2>&1 | grep -q -- "--strict"'
check "help: mentions --file" \
  'bash "$SUT" --help 2>&1 | grep -q -- "\-\-file"'
check "help: mentions Usage" \
  'bash "$SUT" --help 2>&1 | grep -q "^Usage"'

# 2. unknown arg → exit 2
check "usage: unknown arg exits 2" \
  'SRC=0; bash "$SUT" --bogus-arg >/dev/null 2>&1 || SRC=$?; [ "$SRC" -eq 2 ]'

# 3. --file= pointing at a missing path → exit 2
check "usage: missing --file= exits 2" \
  'SRC=0; bash "$SUT" --file=/nonexistent/path/dispatch.py >/dev/null 2>&1 || SRC=$?; [ "$SRC" -eq 2 ]'

# 4. clean fixture (no patterns) → exit 0
mkdir -p "$TMP/clean"
cat > "$TMP/clean/dispatch.py" <<'EOF'
async def some_helper():
    return 42
EOF
run_sut "" "$TMP/clean/dispatch.py"
check "clean: exit 0" '[ "$SRC" -eq 0 ]'
check "clean: OK message on stdout" 'echo "$SOUT" | grep -q "^OK:"'

# 5. hit detection (column_override.get) → reports + exit 0 in advisory
mkdir -p "$TMP/hit"
cat > "$TMP/hit/dispatch.py" <<'EOF'
async def some_helper():
    column_override = {"provider": "anthropic"}
    override_provider = column_override.get("provider") or None
    return override_provider
EOF
run_sut "" "$TMP/hit/dispatch.py"
check "hit: warning on stderr" 'echo "$SERR" | grep -q "^WARNING:"'
check "hit: file:line in output" 'echo "$SERR" | grep -q "dispatch.py:3"'
check "hit: advisory exit 0" '[ "$SRC" -eq 0 ]'

# 6. hit + --strict → exit 1
run_sut "--strict" "$TMP/hit/dispatch.py"
check "hit + --strict: exit 1" '[ "$SRC" -eq 1 ]'

# 7. bypass annotation suppresses the hit
mkdir -p "$TMP/bypass"
cat > "$TMP/bypass/dispatch.py" <<'EOF'
async def some_helper():
    column_override = {"provider": "anthropic"}
    override_provider = column_override.get("provider") or None  # resolver-bypass: legitimate narrow helper
    return override_provider
EOF
run_sut "" "$TMP/bypass/dispatch.py"
check "bypass: clean exit 0" '[ "$SRC" -eq 0 ]'
check "bypass: OK message on stdout" 'echo "$SOUT" | grep -q "^OK:"'

# 8. resolver function body is excluded
mkdir -p "$TMP/resolver"
make_fixture_resolver_plus_hits "$TMP/resolver"
run_sut "" "$TMP/resolver/dispatch.py"
# The fixture has a hit at line 23 (the helper function) but the
# resolver body reads at lines 17-18 should NOT be flagged.
check "resolver: only the helper line is flagged, not the body" \
  'echo "$SERR" | grep -q "dispatch.py:23" && ! echo "$SERR" | grep -q "dispatch.py:17" && ! echo "$SERR" | grep -q "dispatch.py:18"'

# 9. docstring lines are excluded
# The fixture from case 8 also has the pattern references in the
# header comment block (lines 4-9). The same matcher should exclude
# them — they're not inside a triple-quoted string, but the leading
# hashed comment block is also valid Python and should not be flagged.
# The script's `find_resolver_range` + `PATTERNS.search` only fires
# on strings containing the patterns, so the header comment text
# mentioning them is excluded by virtue of the patterns not being
# syntactically identical (the comment uses them in prose, not actual
# method-call form). The resolver-body-exclusion test above already
# covers the body; this case adds a docstring with the exact patterns.
mkdir -p "$TMP/docstring"
cat > "$TMP/docstring/dispatch.py" <<'EOF'
async def some_helper():
    """Helper that documents the patterns it intentionally avoids:
    column_override.get("provider")
    get_column_default_provider(
    get_column_default_model(
    getattr(card, "model"
    """
    return 42
EOF
run_sut "" "$TMP/docstring/dispatch.py"
check "docstring: no false-positive on patterns mentioned in docstring" \
  '[ "$SRC" -eq 0 ]'
check "docstring: OK message on stdout" \
  'echo "$SOUT" | grep -q "^OK:"'

# 10. multiple patterns in one fixture aggregate into one warning
mkdir -p "$TMP/multi"
cat > "$TMP/multi/dispatch.py" <<'EOF'
async def helper_a():
    column_override = {"provider": "anthropic"}
    return column_override.get("provider") or None

async def helper_b():
    return await get_column_default_provider(None, "p", "a")

async def helper_c():
    return getattr(card, "model", None)
EOF
run_sut "" "$TMP/multi/dispatch.py"
check "multi: 3 hits reported" \
  'echo "$SERR" | grep -q "dispatch.py:3" && echo "$SERR" | grep -q "dispatch.py:6" && echo "$SERR" | grep -q "dispatch.py:9"'
check "multi: '3 ad-hoc' summary" \
  'echo "$SERR" | grep -q "3 ad-hoc"'

# ---
echo
if [ "$FAIL" -eq 0 ]; then
  echo "check-dispatch-resolver-usage: all $PASS checks passed."
  exit 0
fi
echo "check-dispatch-resolver-usage: $FAIL of $((PASS+FAIL)) checks failed."
exit 1
