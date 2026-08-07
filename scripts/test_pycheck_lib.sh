#!/usr/bin/env bash
# Test harness for scripts/lib/pycheck.sh.
#
# The helper is the asserted pycheck extracted from scripts/test_po_digest_source.sh
# (kaart 06863c73d1544b20bcc0208feb92bb50). The trap it prevents: bare expression
# `exec(eval)` returns 0 even when the expression is `False`, so a `pycheck` helper
# that runs `exec(textwrap.dedent(expr))` without an explicit `assert` would print
# `ok` on a failing condition. This harness asserts the helper's contract:
#
#   1. False bare expression → nonzero exit (the regression we are closing).
#   2. True bare expression   → zero exit.
#   3. Multi-line expression whose last line is bare → last line gets Assert-wrapped.
#   4. Multi-line expression whose last line is already `assert …, ctx` → passes
#      the AssertionError with the supplied message.
#   5. Stdin must be JSON; non-JSON stdin → nonzero exit (so callers don't get a
#      silent "ok" on a typo in the upstream collector).
#   6. The helper can be sourced from a sibling scripts/test_*.sh without
#      re-defining the function (we source, call, expect success).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$SCRIPT_DIR/lib/pycheck.sh"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# Source the helper. Must define `pycheck` in this shell.
if [ ! -r "$LIB" ]; then
  bad "lib/pycheck.sh exists and is readable"
  echo "passed: $PASS, failed: $FAIL"
  exit 1
fi
ok "lib/pycheck.sh exists and is readable"

# shellcheck source=/dev/null
. "$LIB"

if ! type pycheck >/dev/null 2>&1; then
  bad "pycheck function is defined after sourcing"
  echo "passed: $PASS, failed: $FAIL"
  exit 1
fi
ok "pycheck function is defined after sourcing"

# ----------------------------------------------------------------------------
echo "Contract 1: false bare expression returns nonzero"
if echo '{"ok":false}' | pycheck 'd["ok"]' >/dev/null 2>&1; then
  bad "false bare expression returns nonzero"
else
  ok "false bare expression returns nonzero"
fi

# ----------------------------------------------------------------------------
echo "Contract 2: true bare expression returns zero"
if echo '{"ok":true}' | pycheck 'd["ok"]' >/dev/null 2>&1; then
  ok "true bare expression returns zero"
else
  bad "true bare expression returns zero"
fi

# ----------------------------------------------------------------------------
echo "Contract 3: multi-line expression — last bare line is Assert-wrapped"
# Bare `matches` is Assert-wrapped: empty list fails, non-empty passes.
# Without the wrap, both would return 0.

if echo '{"xs":[]}' | pycheck '
matches = [x for x in d["xs"] if x.get("hit")]
matches
' >/dev/null 2>&1; then
  bad "empty list → last bare expression returns nonzero"
else
  ok "empty list → last bare expression returns nonzero"
fi

if echo '{"xs":[{"hit":1}]}' | pycheck '
matches = [x for x in d["xs"] if x.get("hit")]
matches
' >/dev/null 2>&1; then
  ok "non-empty list → last bare expression returns zero"
else
  bad "non-empty list → last bare expression returns zero"
fi

# ----------------------------------------------------------------------------
echo "Contract 4: explicit assert …, ctx surfaces msg on failure"

# Single-line expression keeps the checker under 40 words.
capture=$(echo '{"xs":[]}' | pycheck 'assert d["xs"], "expected at least one hit"' 2>&1)
rc=$?
if [ "$rc" -ne 0 ]; then
  ok "explicit assert returns nonzero on failure"
else
  bad "explicit assert returns nonzero on failure (rc=$rc)"
fi
if printf '%s' "$capture" | grep -qF "expected at least one hit"; then
  ok "explicit assert surfaces the supplied msg"
else
  bad "explicit assert surfaces the supplied msg (got: $capture)"
fi

# ----------------------------------------------------------------------------
echo "Contract 5: non-JSON out returns nonzero (no silent 'ok')"
if echo 'not json' | pycheck 'd["ok"]' >/dev/null 2>&1; then
  bad "non-JSON out returns nonzero"
else
  ok "non-JSON out returns nonzero"
fi

# ----------------------------------------------------------------------------
echo "Contract 6: re-sourcing does not re-define the function (idempotent)"
# Sourcing twice should still leave pycheck callable; bash's function-definition
# semantics overwrite cleanly, so this is mostly a smoke test that the lib
# has no `set -e`-style trap that aborts on re-source.
if . "$LIB" && echo '{"ok":true}' | pycheck 'd["ok"]' >/dev/null 2>&1; then
  ok "re-sourcing the lib leaves pycheck usable"
else
  bad "re-sourcing the lib leaves pycheck usable"
fi

# ----------------------------------------------------------------------------
echo "Contract 7: stdin wins over a stale \$out in caller scope"
# Regression for the original kaart 06863c73d1544b20bcc0208feb92bb50 trap
# (kaart impediment follow-up): an earlier revision read `$out` from caller
# scope via `<<<"$out"`, so a helper that *also* accepted a pipe would silently
# assert the caller's leftover variable while the pipe contents went unread.
# The contract: the JSON piped in is the JSON asserted, regardless of any
# `out=...` assignment in the calling shell.
out='{"ok":true}'   # leftover from a previous task in the same script run
if echo '{"ok":false}' | pycheck 'd["ok"]' >/dev/null 2>&1; then
  bad "stdin wins over stale \$out (false piped in, true leftover must fail)"
else
  ok "stdin wins over stale \$out (false piped in, true leftover must fail)"
fi

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
