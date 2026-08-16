#!/usr/bin/env bash
# Test harness for scripts/check-decision-doc-anchors.sh.
#
# Exercises the "decision doc claims about our own code carry a file:line
# anchor" check from docs/cockpit/taalgebruik-conventies.md §4 against
# synthetic fixture dirs (never the real docs/cockpit tree).
#
#   1. arg parsing — `--help` works, unknown flag exits 2.
#   2. clean case — every decision doc carries ≥1 anchor → exit 0, "OK".
#   3. drift case — one doc has zero anchors → advisory, names the doc.
#   4. --strict turns drift into exit 1.
#   5. anchors only count under backend/app/ or frontend/src/.
#   6. a :line without a path is NOT an anchor (no false clears).
#   7. a path to a docs/cockpit/*.md file is NOT an anchor (own-doc links).
#   8. a fenced-code `:NN` reference is NOT an anchor (code blocks skipped).
#   9. error path — missing DECISIONS_DIR (no *-decision.md at all) → exit 0 OK.
#  10. real docs/cockpit tree is drift-free (anchored).
#
# Anchors are recognised as `backend/app/.../foo.py:NN` or
# `frontend/src/.../bar.tsx:NN` — optionally with a `:NN-MM` line range
# suffix. A bare `:NN` (no path) or a path under docs/ is not an anchor.
# A path under backend/tests/ is also an anchor (acceptance criterion
# named backend/app/ as the main example; tests/ lives under the same
# repo-root and is the canonical cite target for many real claims).
#
# Note on assertions: per CLAUDE.md "No local pytest" + the "Never
# truncate a verification grep" rule, the clean-state check asserts the
# EXACT clean-state line emitted by the SUT, not a permissive
# `grep -qE "^OK:|WARNING:"` that would pass in both broken and fixed
# states.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/check-decision-doc-anchors.sh"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
echo "Task 1: arg parsing — --help"
out=$(bash "$SUT" --help 2>&1 || true)
check "--help mentions Usage"           'echo "$out" | grep -qE "Usage:"'
check "--help mentions --strict"        'echo "$out" | grep -qE -- "--strict"'
bash "$SUT" --bogus-flag >/dev/null 2>&1; rc=$?
check "unknown flag exits 2"            '[ "$rc" = "2" ]'

# ----------------------------------------------------------------------------
echo "Task 2: clean case — every decision doc carries ≥1 anchor"
clean="$TMP/clean"; mkdir -p "$clean"
cat > "$clean/decisions.md" <<'EOF'
# reg
| d | v | u | [`a-decision.md`](./a-decision.md) | x |
EOF
cat > "$clean/a-decision.md" <<'EOF'
---
title: "A"
type: decision
status: decided
---
**Datum:** 2026-08-13
**Status:** besloten
**Kaart:** `abc12345`
**Uitkomst:** GO.

# A

Het werkpaard staat in `backend/app/services/worker.py:42`.
EOF
cat > "$clean/b-decision.md" <<'EOF'
---
title: "B"
type: decision
status: decided
---
**Datum:** 2026-08-13
**Status:** besloten
**Kaart:** `abc12346`
**Uitkomst:** GO.

# B

De UI zit in `frontend/src/features/x/Y.tsx:13-22`.
EOF
out=$(DECISIONS_DIR="$clean" bash "$SUT" 2>&1); rc=$?
check "clean → exit 0"                   '[ "$rc" = "0" ]'
# Exact clean-state assertion — CLAUDE.md: "Never truncate a verification grep".
check "clean → exact OK line"            'echo "$out" | grep -qF "OK: every docs/cockpit/*-decision.md in the sample carries a backend/app or frontend/src file:line anchor."'
check "clean → no WARNING line"          '! echo "$out" | grep -qE "WARNING:"'
out_strict=$(DECISIONS_DIR="$clean" bash "$SUT" --strict 2>&1); rc=$?
check "clean is green under --strict"    '[ "$rc" = "0" ]'

# ----------------------------------------------------------------------------
echo "Task 3: drift case — one doc has no anchors"
drift="$TMP/drift"; mkdir -p "$drift"
cat > "$drift/decisions.md" <<'EOF'
# reg
| d | v | u | [`a-decision.md`](./a-decision.md) | x |
EOF
cat > "$drift/a-decision.md" <<'EOF'
---
title: "A"
type: decision
status: decided
---
**Datum:** 2026-08-13
**Status:** besloten
**Kaart:** `abc12345`
**Uitkomst:** GO.

# A

Geen anker hier — alleen een claim in het luchtledige.
EOF
out=$(DECISIONS_DIR="$drift" bash "$SUT" 2>&1); rc=$?
check "drift → exit 0 (advisory)"        '[ "$rc" = "0" ]'
check "drift → names the offending doc"  'echo "$out" | grep -qF "a-decision.md"'
check "drift → reports exactly 1 warning" 'echo "$out" | grep -qE "WARNING: 1 decision doc"'
check "drift → mentions the rule"        'echo "$out" | grep -qE "(fout|drift|anchor|file:line)"'

# ----------------------------------------------------------------------------
echo "Task 4: --strict turns drift into exit 1"
out=$(DECISIONS_DIR="$drift" bash "$SUT" --strict 2>&1); rc=$?
check "drift + --strict → exit 1"        '[ "$rc" = "1" ]'
check "drift + --strict → names doc"     'echo "$out" | grep -qF "a-decision.md"'

# ----------------------------------------------------------------------------
echo "Task 5: anchor path must be under backend/app or frontend/src (or backend/tests)"
paths="$TMP/paths"; mkdir -p "$paths"
cat > "$paths/decisions.md" <<'EOF'
# reg
EOF
# bare path under docs/ → NOT an anchor
cat > "$paths/docs-doc-decision.md" <<'EOF'
---
title: "X"
type: decision
status: decided
---
**Datum:** 2026-08-13
**Status:** besloten
**Kaart:** `abc12345`
**Uitkomst:** GO.

# X

Zie `docs/cockpit/foo.md:10` voor de redenering.
EOF
out=$(DECISIONS_DIR="$paths" bash "$SUT" 2>&1); rc=$?
check "docs/ path is not an anchor"      '[ "$rc" = "0" ]'
check "docs/ path → doc is drift"        'echo "$out" | grep -qF "docs-doc-decision.md"'

# valid anchor under backend/app — clear that one again with a doc that has one
cat > "$paths/docs-doc-decision.md" <<'EOF'
---
title: "X"
type: decision
status: decided
---
**Datum:** 2026-08-13
**Status:** besloten
**Kaart:** `abc12345`
**Uitkomst:** GO.

# X

Zie `backend/app/services/worker.py:42`.
EOF
out=$(DECISIONS_DIR="$paths" bash "$SUT" 2>&1); rc=$?
check "backend/app path IS an anchor"    '[ "$rc" = "0" ] && echo "$out" | grep -qE "^OK: every docs/cockpit/\*-decision\.md in the sample carries a backend/app or frontend/src file:line anchor\."'

# valid anchor under backend/tests — same
cat > "$paths/docs-doc-decision.md" <<'EOF'
---
title: "X"
type: decision
status: decided
---
**Datum:** 2026-08-13
**Status:** besloten
**Kaart:** `abc12345`
**Uitkomst:** GO.

# X

Zie `backend/tests/test_x.py:99`.
EOF
out=$(DECISIONS_DIR="$paths" bash "$SUT" 2>&1); rc=$?
check "backend/tests path IS an anchor"  '[ "$rc" = "0" ] && echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
# Task 5b — unbackticked anchors count as anchors too. The script header
# promises "Surrounding backticks are optional"; an awk `\b` would silently
# become a backspace byte when passed via `awk -v`, so the harness pins the
# behaviour with a doc that only has prose anchors (no backticks anywhere).
# Without this task, `prompt-injectors-decision.md:20` would never have been
# surfaced as a false-positive drift — the regression that the previous
# reviewer caught.
echo "Task 5b: unbackticked prose anchor IS counted as anchor (no false-positive on prompt-injectors style)"
unbtick="$TMP/unbtick"; mkdir -p "$unbtick"
cat > "$unbtick/decisions.md" <<'EOF'
# reg
EOF
cat > "$unbtick/unbtick-decision.md" <<'EOF'
---
title: "X"
type: decision
status: decided
---
**Datum:** 2026-08-13
**Status:** besloten
**Kaart:** `abc12345`
**Uitkomst:** GO.

# X

Het pad is backend/app/services/worker.py:42 volgens het harness hierboven.
Ook frontend/src/features/x/Y.tsx:13-22 naast een komma.
EOF
out=$(DECISIONS_DIR="$unbtick" bash "$SUT" 2>&1); rc=$?
check "unbackticked prose anchor → exit 0 (clean)"  '[ "$rc" = "0" ]'
check "unbackticked prose anchor → exact OK line"    'echo "$out" | grep -qF "OK: every docs/cockpit/*-decision.md in the sample carries a backend/app or frontend/src file:line anchor."'

# Negative: same fixtures, but the prose anchor is glued to a path char so
# `\b` would NOT have caught it — the boundary check must reject this.
cat > "$unbtick/unbtick-decision.md" <<'EOF'
---
title: "X"
type: decision
status: decided
---
**Datum:** 2026-08-13
**Status:** besloten
**Kaart:** `abc12345`
**Uitkomst:** GO.

# X

Een fragment xybackend/app/services/worker.py:42 mag niet tellen — het
`xy`-voorvoegsel is geen anker-boundary.
EOF
out=$(DECISIONS_DIR="$unbtick" bash "$SUT" 2>&1); rc=$?
check "mid-token anchor → still drift (boundary guard)"  'echo "$out" | grep -qF "unbtick-decision.md"'
check "mid-token anchor → no false OK line"              '! echo "$out" | grep -qF "OK: every docs/cockpit/*-decision.md in the sample carries a backend/app or frontend/src file:line anchor."'

# ----------------------------------------------------------------------------
echo "Task 6: bare :line (no path) is NOT an anchor"
bare="$TMP/bare"; mkdir -p "$bare"
cat > "$bare/decisions.md" <<'EOF'
# reg
EOF
cat > "$bare/bare-decision.md" <<'EOF'
---
title: "X"
type: decision
status: decided
---
**Datum:** 2026-08-13
**Status:** besloten
**Kaart:** `abc12345`
**Uitkomst:** GO.

# X

De fout zit op regel 42 van worker.py (geen anker — geen pad).
EOF
out=$(DECISIONS_DIR="$bare" bash "$SUT" 2>&1); rc=$?
check "bare :line → doc is drift"        '[ "$rc" = "0" ]'
check "bare :line → names doc"           'echo "$out" | grep -qF "bare-decision.md"'

# ----------------------------------------------------------------------------
echo "Task 7: fenced-code :NN reference is NOT an anchor (code blocks skipped)"
fence="$TMP/fence"; mkdir -p "$fence"
cat > "$fence/decisions.md" <<'EOF'
# reg
EOF
cat > "$fence/fence-decision.md" <<'EOF'
---
title: "X"
type: decision
status: decided
---
**Datum:** 2026-08-13
**Status:** besloten
**Kaart:** `abc12345`
**Uitkomst:** GO.

# X

Geen anker, wel een code-voorbeeld:

```
# fictief — worker.py:42 is hier puur een string, geen claim
def f(): return 1
```
EOF
out=$(DECISIONS_DIR="$fence" bash "$SUT" 2>&1); rc=$?
check "fenced :NN → doc is drift"        '[ "$rc" = "0" ]'
check "fenced :NN → names doc"           'echo "$out" | grep -qF "fence-decision.md"'

# ----------------------------------------------------------------------------
echo "Task 8: missing DECISIONS_DIR is not an error when sample is empty (no false positives)"
empty="$TMP/empty"; mkdir -p "$empty"
out=$(DECISIONS_DIR="$empty" bash "$SUT" 2>&1); rc=$?
check "empty sample → exit 0"            '[ "$rc" = "0" ]'
check "empty sample → exact OK line"     'echo "$out" | grep -qF "OK: every docs/cockpit/*-decision.md in the sample carries a backend/app or frontend/src file:line anchor."'

# ----------------------------------------------------------------------------
echo "Task 10: real docs/cockpit tree is allowed to drift under advisory (the 21 grandfathered docs from before this convention wait to be backfilled)"
out=$(bash "$SUT" 2>&1); rc=$?
check "real tree → exit 0 advisory"      '[ "$rc" = "0" ]'
# Either every doc carries an anchor (clean) or at least one WARNING line
# appears naming drift. The card explicitly accepts this: grandfathered
# docs surface as warnings until they get backfilled.
check "real tree → OK or numbered WARNING" 'echo "$out" | grep -qE "^OK: every docs/cockpit/\*-decision\.md in the sample carries a backend/app or frontend/src file:line anchor\.|WARNING: [1-9][0-9]* decision doc"'

# ----------------------------------------------------------------------------
echo
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
