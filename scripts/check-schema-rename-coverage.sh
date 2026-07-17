#!/usr/bin/env bash
# check-schema-rename-coverage.sh — post-rename grep sweep for stale field refs.
#
# Background: kanban card `ad15e08271c242238db239a90dc559d4` documented that
# commit `558ca55 refactor(backend): rename provider/platform terminology`
# shipped with **2 silent-red tests** that only surfaced a week later, when
# a follow-up problem card happened to grep the same area. The root cause:
# the engineer's commit recipe had no "I renamed a column / Pydantic field,
# re-grep the codebase for stale references" step. This script IS that step.
#
# Strategy: a pure-mechanical grep over `backend/app/` + `backend/tests/` for
# stale references to a renamed identifier. No pytest, no DB, no network —
# runs in milliseconds, no concurrency surface (matches the project's
# `feedback_no_local_pytest` memory by avoiding pytest entirely).
#
# Modes:
#   default            auto-detect: parse `git diff origin/master -- backend/app/`
#                      for `ALTER TABLE <t> RENAME COLUMN <old> TO <new>`.
#                      No matches → exit 0 with "no renames detected" hint.
#   --rename <args>    explicit override. Two forms:
#                        --rename <table> <old> <new>   (column rename)
#                        --rename <old> <new>           (bare/class rename)
#                      Repeatable; takes precedence over auto-detect.
#
# Per rename, three grep patterns (with word-boundary guards):
#   <table>\.<old>          table-qualified reference (e.g. `mail_agent_sessions.provider`)
#   \.<old>\b               bare attribute access (e.g. `req.provider`)
#   \b<old>\s*[:=]          field declaration / kwarg
#                           (e.g. `provider: str = "unknown"`, `Mail(provider="x")`)
#
# Scope: `backend/app/**/*.py` + `backend/tests/**/*.py`. Excludes
# `backend/app/database.py` (canonical migration spot — the legacy column
# tuple references there are intentional; the patterns above don't false-
# positive on the literal `"old"` strings used in `_migrate_terminology_columns`,
# but the exclusion is belt-and-suspenders).
#
# Not scanned: `docs/`, `frontend/`, `scripts/`, `*.md`, migration files.
#
# Exit codes:
#   0    clean (no renames detected, OR auto-detect found renames and no hits
#        survive, OR hits found in advisory default mode)
#   1    hits found + --strict
#   2    bad args / missing repo
#
# Advisory by default (matches `scripts/check-decision-register.sh`,
# `scripts/check-kanban-conventions.sh`): prints `[leak]` markers + a
# summary line, exits 0. Pass `--strict` to flip to blocking.
#
# Usage:
#   bash scripts/check-schema-rename-coverage.sh                   # auto-detect
#   bash scripts/check-schema-rename-coverage.sh --strict          # blocking on hits
#   bash scripts/check-schema-rename-coverage.sh --rename mail_agent_sessions provider cli
#   bash scripts/check-schema-rename-coverage.sh --rename AgentProvider AgenticCli
#   bash scripts/check-schema-rename-coverage.sh --list-all        # show every hit
#   bash scripts/check-schema-rename-coverage.sh --root <path>     # scan a different tree
#   bash scripts/check-schema-rename-coverage.sh --help
#
# --root <path>  override REPO_ROOT (default: parent of the script's own
#                directory). Used by the test harness to point at a fixture
#                git repo. Engineers can also use it to scope the check to a
#                subdirectory of the workspace.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---- arg parsing ----------------------------------------------------------

STRICT=0
LIST_ALL=0
ROOT_OVERRIDE=""
# Renames are accumulated as: "table|old|new" (table may be empty for bare).
RENAMES=()

usage() {
    awk 'NR==1{next} /^$/{exit} {sub(/^# ?/,""); print}' "$0"
}

# Parse argv manually (the project's other check-*.sh scripts use the same
# `for arg in "$@"` + case pattern). --rename consumes positional args until
# the next --flag, so we don't use getopts.
ARGS=("$@")
i=0
while [ "$i" -lt "${#ARGS[@]}" ]; do
    arg="${ARGS[$i]}"
    case "$arg" in
        -h|--help)       usage; exit 0 ;;
        --strict)        STRICT=1 ;;
        --list-all)      LIST_ALL=1 ;;
        --root)
            i=$((i + 1))
            ROOT_OVERRIDE="${ARGS[$i]:-}"
            if [ -z "$ROOT_OVERRIDE" ]; then
                echo "error: --root needs a <path>" >&2
                exit 2
            fi
            ;;
        --rename)
            i=$((i + 1))
            a1="${ARGS[$i]:-}"
            if [ -z "$a1" ]; then
                echo "error: --rename needs at least '<old> <new>'" >&2
                exit 2
            fi
            i=$((i + 1))
            a2="${ARGS[$i]:-}"
            if [ -z "$a2" ]; then
                echo "error: --rename needs at least '<old> <new>'" >&2
                exit 2
            fi
            # Peek at the third positional arg WITHOUT advancing past it. If
            # it starts with `--` (or is empty — another flag is next), treat
            # as a bare rename `<old> <new>` and rewind i so the outer
            # loop's `i=$((i+1))` lands on a3 (the next flag). Otherwise
            # consume it as the table-qualified form `<table> <old> <new>`.
            # Without the rewind, `--rename Foo Bar --strict` would skip
            # `--strict` because a3 was peeked but not advanced past.
            i=$((i + 1))
            a3="${ARGS[$i]:-}"
            case "$a3" in
                --*|"")
                    RENAMES+=("|$a1|$a2")
                    i=$((i - 1))
                    ;;
                *)
                    RENAMES+=("$a1|$a2|$a3")
                    ;;
            esac
            ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
    i=$((i + 1))
done

# Apply --root override (resolved to absolute path so file-listing below is
# consistent regardless of where the caller cd'd to). Placed AFTER the parse
# loop because ROOT_OVERRIDE is set inside it.
if [ -n "$ROOT_OVERRIDE" ]; then
    REPO_ROOT="$(cd "$ROOT_OVERRIDE" && pwd)"
fi

if [ "${#RENAMES[@]}" -eq 0 ]; then
    # Auto-detect: parse `git diff origin/master -- backend/app/` for renames.
    if ! git -C "$REPO_ROOT" rev-parse --verify --quiet origin/master >/dev/null; then
        echo "error: origin/master not found locally — 'git fetch origin' first" >&2
        echo "  (or pass --rename explicitly to bypass auto-detect)" >&2
        exit 2
    fi
    # Capture only the ADDED lines (`^+`); renames live in `_migrate_*` as
    # f-string args like `ALTER TABLE foo RENAME COLUMN bar TO baz` — the
    # `baz` part will be on the next line as a continuation in some styles,
    # so allow for a space-prefixed continuation by joining consecutive +lines.
    diff_text=$(git -C "$REPO_ROOT" diff origin/master -- backend/app/ || true)
    while IFS= read -r match; do
        [ -z "$match" ] && continue
        RENAMES+=("$match")
    done < <(echo "$diff_text" \
        | grep -E '^\+.*ALTER TABLE[[:space:]]+[A-Za-z0-9_]+[[:space:]]+RENAME COLUMN[[:space:]]+[A-Za-z0-9_]+[[:space:]]+TO[[:space:]]+[A-Za-z0-9_]+' \
        | sed -E 's/.*ALTER TABLE[[:space:]]+([A-Za-z0-9_]+)[[:space:]]+RENAME COLUMN[[:space:]]+([A-Za-z0-9_]+)[[:space:]]+TO[[:space:]]+([A-Za-z0-9_]+).*/\1|\2|\3/' \
        | sort -u)
fi

if [ "${#RENAMES[@]}" -eq 0 ]; then
    echo "check-schema-rename-coverage: no renames detected (auto-detect found 0 ALTER TABLE RENAME COLUMN patterns in diff vs origin/master; pass --rename to override)"
    exit 0
fi

# ---- scope ----------------------------------------------------------------

# Search root: backend/app/ + backend/tests/, .py only, excluding the
# canonical migration file. We pass this via `find -print0` + `xargs -0 grep`
# so paths with spaces or unicode don't trip the loop.
APP_SCOPE="$REPO_ROOT/backend/app"
TEST_SCOPE="$REPO_ROOT/backend/tests"
EXCLUDE_FILE="$APP_SCOPE/database.py"

# Build the file list once. `find -path` excludes the migration file; `-name
# '*.py'` restricts to Python source. We then re-add it as a single trailing
# `-not -path` to keep the find expression flat and readable.
mapfile -d '' FILE_LIST < <(
    find "$APP_SCOPE" "$TEST_SCOPE" \
        -type f -name '*.py' \
        -not -path "$EXCLUDE_FILE" \
        -print0 2>/dev/null \
    | sort -z
)

if [ "${#FILE_LIST[@]}" -eq 0 ]; then
    # Empty scope (fresh worktree, or no backend code yet). Treat as clean.
    echo "check-schema-rename-coverage: no files in scope (backend/app/, backend/tests/) — nothing to check"
    exit 0
fi

# ---- scan -----------------------------------------------------------------

# Cap on hits printed per rename. LIST_ALL=1 disables the cap.
HIT_CAP=10
TOTAL_HITS=0
LEAKY_RENAMES=0

for rename in "${RENAMES[@]}"; do
    IFS='|' read -r table old new <<< "$rename"
    # Build the patterns. We escape the renamed token into a regex-safe form.
    # Identifiers are `[A-Za-z0-9_]+` only — no need for full re.escape, but
    # a literal dot in `table.old` is a regex `.` otherwise.
    esc_old=$(printf '%s' "$old" | sed 's/[][(){}.*+?^$|\\]/\\&/g')
    esc_table=$(printf '%s' "$table" | sed 's/[][(){}.*+?^$|\\]/\\&/g')

    patterns=()
    if [ -n "$table" ]; then
        patterns+=("$esc_table\\.$esc_old")
    fi
    patterns+=("\\.$esc_old\\b")
    patterns+=("\\b$esc_old[[:space:]]*[:=]")

    # Collect hits across all patterns. `grep -nH` prints file:line:match.
    # We do one pass per pattern (no -E alternation, to keep the patterns
    # readable in test output).
    hits_file=$(mktemp)
    for pat in "${patterns[@]}"; do
        # -E: extended regex; -n: line number; -H: filename; -I: skip binary.
        # || true because grep exits 1 when no match — not an error here.
        xargs -0 grep -nHI -E "$pat" "${FILE_LIST[@]}" \
            > "$hits_file" 2>/dev/null || true
    done
    # De-dup by file:line (different patterns can hit the same line).
    sort -u "$hits_file" -o "$hits_file"

    hit_count=$(wc -l < "$hits_file" | tr -d ' ')

    if [ "$hit_count" -eq 0 ]; then
        rm -f "$hits_file"
        continue
    fi

    LEAKY_RENAMES=$((LEAKY_RENAMES + 1))
    TOTAL_HITS=$((TOTAL_HITS + hit_count))

    label="$old"
    [ -n "$table" ] && label="$table.$old"
    echo "[leak] rename $old -> $new (table=$table): $hit_count stale reference(s) in backend/app/ + backend/tests/"
    if [ "$LIST_ALL" = 1 ]; then
        sed 's/^/    /' "$hits_file"
    else
        head -n "$HIT_CAP" "$hits_file" | sed 's/^/    /'
        if [ "$hit_count" -gt "$HIT_CAP" ]; then
            echo "    ...$((hit_count - HIT_CAP)) more (run with --list-all to see every hit)"
        fi
    fi
    rm -f "$hits_file"
done

# ---- summary --------------------------------------------------------------

if [ "$TOTAL_HITS" -eq 0 ]; then
    echo "check-schema-rename-coverage: clean (${#RENAMES[@]} rename(s) checked, 0 stale references)"
    exit 0
fi

echo
echo "check-schema-rename-coverage: $TOTAL_HITS stale reference(s) across $LEAKY_RENAMES rename(s)"
if [ "$STRICT" = 1 ]; then
    echo "  --strict set: exiting 1. Fix the references above (kanban card ad15e08271c242238db239a90dc559d4 documented the silent-red-test failure mode this guards against)."
    exit 1
fi
echo "  Advisory: exit 0. Re-run with --strict to fail the build, or fix the refs and re-run."
exit 0