#!/usr/bin/env bash
# check-dispatch-resolver-usage.sh — flag ad-hoc provider/model lookups in
# dispatch.py that bypass the canonical resolver.
#
# Background (kanban card 931855b0…): the manual-pause gate (kaart
# f056b2888a…, fix commit 77f5c8c) shipped with five FCR gaps because the
# gate re-implemented the 5-layer provider precedence chain (global override
# → pool → per-card column override → column default) ad-hoc instead of
# reusing `resolve_effective_provider_and_model`. Each of the four dispatch
# entry points had its own narrow slice of the chain and they disagreed.
# The fix introduced `_card_is_manually_paused` that re-walks the chain via
# the canonical resolver, but the underlying lesson — that any new feature
# touching "what provider does this card spawn on" must route through the
# canonical resolver — was not enforced anywhere. A future gate (quota
# accounting, cost attribution, cross-pool routing) is likely to repeat the
# same mistake.
#
# This script is the enforcement lever. It greps:
#   backend/app/kanban/dispatch.py for direct reads of
#     column_override.get("provider"/"model")
#     get_column_default_provider( / get_column_default_model(
#     column.default_provider / column.default_model
#     getattr(card, "model"
#   that are NOT inside the resolver function body and NOT annotated
#   with a `# resolver-bypass: <reason>` justification — the sentinel
#   alone (no reason text after the colon) does NOT exempt a line.
#
# Strategy: a Python helper walks the file line-by-line while tracking
#   (a) whether we're inside a triple-quoted docstring (skip those lines),
#   (b) whether we're inside the body of `resolve_effective_provider_and_model`
#       (the canonical resolver — its own reads of these helpers are the
#       implementation, not bypasses),
# and emits a TSV of (file, line, line_text) for every code-line hit that
# does not carry a `# resolver-bypass:` annotation.
#
# Scope: backend/app/kanban/dispatch.py ONLY. Tests, frontend, and other
# backend modules are out of scope — the resolver is the dispatch's
# single source of truth, and the 5-layer chain is dispatch-specific.
#
# Advisory by default (mirrors check-decision-register.sh,
# check-kanban-meta-security-conflicts.sh, check-schema-rename-coverage.sh,
# check-doc-frontmatter.sh, check-doc-links.sh, check-test-harness-coverage.sh,
# check-analysis-outcomes.sh — every check-*.sh in this repo): prints hits
# but exits 0. Pass --strict to flip to blocking. This is a deliberate,
# repo-wide convention (see each script's own header + CLAUDE.md's
# "(advisory; --strict = exit 1)" annotation on every one of them), not an
# oversight — a brand-new advisory sweeper landing in CI as a hard failure
# on day one would block unrelated PRs on a pattern nobody has triaged yet.
#
# Usage:
#   scripts/check-dispatch-resolver-usage.sh [--strict] [--file PATH]
#
# Env:
#   DISPATCH_FILE   path to the dispatch.py to scan
#                   (default: backend/app/kanban/dispatch.py relative to
#                   the repo root; overridden by --file= for testing)
#
# Exit codes:
#   0  clean (no hits, or hits printed in advisory mode)
#   1  hits found + --strict
#   2  usage error / target file missing

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

DISPATCH_FILE="${DISPATCH_FILE:-$REPO_ROOT/backend/app/kanban/dispatch.py}"
STRICT=0

for arg in "$@"; do
  case "$arg" in
    --strict)  STRICT=1 ;;
    --file=*)  DISPATCH_FILE="${arg#--file=}" ;;
    --help|-h)
      sed -n '3,57p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    "")
      ;;
    *)
      echo "ERROR: unknown argument '$arg' (see --help)" >&2
      exit 2
      ;;
  esac
done

if [ ! -r "$DISPATCH_FILE" ]; then
  echo "ERROR: dispatch file not found or not readable at: $DISPATCH_FILE" >&2
  echo "Set DISPATCH_FILE=/path/to/dispatch.py or pass --file=PATH." >&2
  exit 2
fi

# ---
# Inline Python helper. The job is small but the bookkeeping (docstring
# state, resolver-function range) is easier to express in straight-line
# Python than in grep -P tricks. Output is TSV:
#
#   <line_number>\t<line_text>
#
# Stderr is captured so we can surface Python errors with the same
# exit-code-and-stderr pattern as scripts/check-kanban-meta-security-conflicts.sh.
PY_STDERR_FILE="$(mktemp)"
HIT_TSV="$(python3 - "$DISPATCH_FILE" 2>"$PY_STDERR_FILE" <<'PY'
import sys, re

path = sys.argv[1]

# Patterns that pick a provider/model outside the resolver. Each is a
# standalone regex (no alternation) so the printed hit text shows which
# pattern matched. The card's suggested-improvement text names two
# literal example shapes — `column.default_provider` and
# `getattr(card, "model", ...)` — which we match directly (even though
# the current codebase has no raw `column.default_provider` attribute
# access; the helper-function form `get_column_default_provider(...)`
# is what's actually used today for the same effect). We scan for BOTH
# the literal card-text shapes and the actual codebase shapes so a
# future caller using either form gets caught.
PATTERNS = [
    re.compile(r'column_override\.get\(\s*"(?:provider|model)"'),
    re.compile(r'\bget_column_default_provider\s*\('),
    re.compile(r'\bget_column_default_model\s*\('),
    re.compile(r'\bcolumn\.default_provider\b'),
    re.compile(r'\bcolumn\.default_model\b'),
    re.compile(r'getattr\(\s*card\s*,\s*"model"'),
]

# The canonical resolver itself — its body legitimately reads these
# helpers/fields because it IS the implementation of the chain. We
# skip the range from `def resolve_effective_provider_and_model(` to
# the next top-level `def ` / `async def ` at the same indent, so any
# future re-implementation of the chain is also exempted by name.
RESOLVER_NAME = "resolve_effective_provider_and_model"

# Triple-quoted string delimiters. Track both styles so plain
# `"""docstring"""` and `'''docstring'''` both flip the in-docstring
# flag. Nested triple-quoted strings are not legal in Python, so a
# single boolean is enough.
TRIPLE = ('"""', "'''")


def find_resolver_range(lines):
    """Return (start_line, end_line) inclusive for the resolver function.

    start_line is the line that opens `def resolve_effective_provider_and_model(`.
    end_line is the last line before the next same-indent `def ` / `async def `
    (or the last line of the file, whichever comes first). Returns None
    when the resolver isn't present — the script then flags every hit
    literally, which is the safe default for a renamed resolver.
    """
    start = None
    for i, line in enumerate(lines, start=1):
        if re.match(rf'^(?:async\s+)?def\s+{RESOLVER_NAME}\s*\(', line):
            start = i
            break
    if start is None:
        return None
    # Find the indent of the def line — the next top-level function at
    # the same indent closes the resolver.
    m = re.match(r'^(\s*)', lines[start - 1])
    base_indent = m.group(1) if m else ''
    for j in range(start, len(lines)):
        line = lines[j]
        ms = re.match(r'^(\s*)(?:async\s+)?def\s+\w', line)
        if ms and ms.group(1) == base_indent and not line.lstrip().startswith('#'):
            return (start, j - 1)
    return (start, len(lines))


with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

resolver_range = find_resolver_range(lines)
hits = []
in_docstring = False
docstring_delim = None

for idx, raw in enumerate(lines, start=1):
    line = raw.rstrip('\n')

    # Resolve the in-docstring flag BEFORE we look at content, so a line
    # that both opens and closes a single-line docstring counts as a
    # docstring line (and gets skipped). The pattern is: if the same
    # line contains an even number of the active delimiter (in
    # non-raw contexts the simpler "starts with `"""` and ends with
    # `"""`" handles the common case), flip the flag. For multi-line
    # openers that don't close on the same line, leave the flag set.
    if not in_docstring:
        for delim in TRIPLE:
            if delim in line:
                # Cheap heuristic: if the delimiter appears twice it's a
                # single-line docstring; if once, multi-line open.
                count = line.count(delim)
                if count >= 2:
                    # Single-line docstring — skip the whole line.
                    line = None
                    break
                in_docstring = True
                docstring_delim = delim
                line = None
                break
    else:
        if docstring_delim in line:
            # Closing delimiter on this line (may also be the opener of
            # a new docstring, but Python doesn't allow nested triple-
            # quotes so this is the end of the current one).
            in_docstring = False
            docstring_delim = None
        line = None

    if line is None:
        continue

    # Skip the resolver function body — its reads of the helpers/fields
    # are the implementation, not callers.
    if resolver_range is not None:
        rs, re_end = resolver_range
        if rs <= idx <= re_end:
            continue

    # Skip full-line `#` comments — a prose reference to
    # `column.default_provider` etc. in an explanatory comment is not
    # code that bypasses the resolver. Trailing comments on a code line
    # (e.g. `x = foo()  # note`) still scan the code portion normally;
    # only a line whose FIRST non-whitespace character is `#` is skipped.
    if line.lstrip().startswith('#'):
        continue

    # Hit detection. Stop at the first matching pattern per line so the
    # printed hit text is unambiguous.
    matched = None
    for pat in PATTERNS:
        if pat.search(line):
            matched = pat.pattern
            break
    if matched is None:
        continue

    # Exemption: `# resolver-bypass:` JUSTIFICATION on the same line —
    # the sentinel alone is not enough, there must be non-whitespace
    # reason text after the colon. A bare `# resolver-bypass:` (no
    # reason) is exactly the kind of unexplained bypass this script
    # exists to catch, so it does NOT exempt the line. The trailing
    # colon is part of the sentinel so a comment like `# resolver
    # bypass` (no colon) doesn't accidentally exempt a line either.
    bypass_match = re.search(r'#\s*resolver-bypass:(.*)$', line)
    if bypass_match and bypass_match.group(1).strip():
        continue

    # Flatten tabs/newlines so the bash awk below stays column-anchored.
    safe = line.replace('\t', ' ').replace('\r', '')
    hits.append((idx, safe, matched))

if not hits:
    print('', end='')
    sys.exit(0)

for idx, safe, pat in hits:
    print(f"{idx}\t{safe}\t{pat}")
sys.exit(0)
PY
)"
PY_RC=$?
if [ "$PY_RC" -ne 0 ]; then
  echo "ERROR: scan failed (exit $PY_RC); see stderr above." >&2
  [ -s "$PY_STDERR_FILE" ] && cat "$PY_STDERR_FILE" >&2 || true
  rm -f "$PY_STDERR_FILE"
  exit 2
fi
rm -f "$PY_STDERR_FILE"

# Empty stdout from Python means clean.
if [ -z "$HIT_TSV" ]; then
  echo "OK: every provider/model lookup in ${DISPATCH_FILE#$REPO_ROOT/} routes through resolve_effective_provider_and_model."
  exit 0
fi

total=$(printf '%s\n' "$HIT_TSV" | wc -l | tr -d ' ')
echo "WARNING: ${total} ad-hoc provider/model lookup(s) in ${DISPATCH_FILE#$REPO_ROOT/} bypass the canonical resolver:" >&2
echo "" >&2
REL_FILE="${DISPATCH_FILE#$REPO_ROOT/}"
printf '%s\n' "$HIT_TSV" | awk -F'\t' -v fname="$REL_FILE" '
  {
    ln   = $1
    text = $2
    pat  = $3
    printf "  %s:%s  [pattern: %s]\n", fname, ln, pat
    printf "         %s\n\n", text
  }
' >&2

echo "Any new dispatch-side gate / pool helper / per-card spawn decision /" >&2
echo "quota-accounting / cost-attribution lookup should call" >&2
echo "  resolve_effective_provider_and_model(...)" >&2
echo "instead of re-walking the 5-layer chain ad-hoc. The card's evidence" >&2
echo "(kaart f056b2888a…, fix commit 77f5c8c) is that the manual-pause gate" >&2
echo "shipped with five FCR gaps because each entry point had its own narrow" >&2
echo "slice of the chain." >&2
echo "" >&2
echo "If the hit is legitimate (e.g. an existing helper that intentionally" >&2
echo "narrows the chain, or the resolver itself before the docstring update" >&2
echo "landed), annotate the line with a trailing  # resolver-bypass: <reason>" >&2
echo "comment so the script exits clean." >&2

if [ "$STRICT" -eq 1 ]; then
  exit 1
fi
echo "(advisory — not failing the build; run with --strict to enforce)" >&2
exit 0
