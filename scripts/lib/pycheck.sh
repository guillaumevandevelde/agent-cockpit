#!/usr/bin/env bash
# Asserted pycheck helper for scripts/test_*.sh harnesses.
#
# Why this file exists
# --------------------
# The naive `pycheck` pattern — `echo "$out" | python3 -c "...exec(textwrap.dedent('''
# $expr '''))..."` — silently passes a bare expression whose value is False: an
# `Expr` AST node evaluated under `exec(...)` does not raise, so the helper's
# exit code is 0 even when the assertion should fail. This is the exact
# tautology that the `grep -qE "^OK:|WARNING:"` warning in CLAUDE.md calls
# out for shell harnesses: the helper never says "I am asserting".
#
# The fix is to parse the expression, wrap the LAST statement in an
# `ast.Assert` when it is a bare `ast.Expr`, and then `exec(compile(...))` the
# result. A bare expression like `d["ok"]` is rewritten to `assert d["ok"]`,
# so a False value raises `AssertionError` and the helper exits nonzero.
#
# What this file provides
# -----------------------
#   pycheck <python-expression>
#       Reads JSON from stdin (bound to `d`), evaluates the expression as the
#       last statement of a multi-statement module. If the last statement is a
#       bare expression, it is Assert-wrapped; explicit `assert …, ctx` lines
#       pass through unchanged (the wrap is conditional on ast.Expr only).
#       Returns the python interpreter's exit code: 0 on success, nonzero on
#       assertion failure, exception, or non-JSON stdin.
#
# Pattern in a harness:
#
#     . "$SCRIPT_DIR/lib/pycheck.sh"
#     out='{"ok":true}'
#     check "ok is true" 'echo "$out" | pycheck "d[\\"ok\\"]"'
#
# Plus a Task-0-style self-check at the top of every consuming harness so the
# helper's truthfulness is itself asserted (see scripts/test_po_digest_source.sh
# Task 0, and the unit harness scripts/test_pycheck_lib.sh for the contract).

pycheck() {
    local expr="$1"
    # Feed `$out` from the caller's scope via here-string so the harness-side
    # `out=$(...); pycheck "..."` pattern keeps working without an explicit
    # pipe. The contract: caller assigns the SUT's JSON to `$out` in scope
    # before calling `pycheck` (the conventional variable name across
    # scripts/test_*.sh harnesses). Tests in scripts/test_pycheck_lib.sh
    # follow the same contract — see the explicit `out=...` assignments at
    # the top of each contract.
    python3 -c "
import ast, json, sys, textwrap
d = json.loads(sys.stdin.read())
source = textwrap.dedent('''$expr''')
tree = ast.parse(source)
if tree.body and isinstance(tree.body[-1], ast.Expr):
    tree.body[-1] = ast.Assert(test=tree.body[-1].value, msg=None)
exec(compile(ast.fix_missing_locations(tree), '<pycheck>', 'exec'))
" <<<"$out"
}
