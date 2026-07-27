# Shared ruff-venv resolution for scripts/ruff-baseline.sh and
# scripts/ruff-compare.sh. Mirrors the fallback chain in
# scripts/lib/resolve-pytest-cmd.sh — same venv, same shared-main-checkout
# fallback, same PATH tail — so a fresh worktree with no local venv still
# works (CLAUDE.md "Werkomgeving in worktree" — worktree venvs are .gitignored
# and `scripts/install.sh` is never run there).
#
# Usage: source this file, then:
#   resolve_ruff_cmd "$BACKEND_DIR" [shared_venv_override]
# On success, sets $RUFF_CMD and returns 0. On failure, prints a "tried"
# hint to stderr and returns 1 — caller decides whether that's fatal.
#
# Fallback order (first hit wins):
#   1. $RUFF_CMD already set by the caller (explicit override) — left
#      untouched, including if it points nowhere; the caller's own
#      executable check catches that.
#   2. <backend_dir>/venv/bin/ruff — worktree-local venv.
#   3. shared_venv_override, defaulting to
#      /home/vdvgu/claude-cockpit/backend/venv/bin/ruff — the shared
#      main-checkout venv. The override param exists only so tests can
#      point this at a fixture instead of the real shared box path.
#   4. bare `ruff` on PATH.
resolve_ruff_cmd() {
    local backend_dir="$1"
    local shared_venv="${2:-/home/vdvgu/claude-cockpit/backend/venv/bin/ruff}"

    if [ -n "${RUFF_CMD:-}" ]; then
        return 0
    elif [ -x "$backend_dir/venv/bin/ruff" ]; then
        RUFF_CMD="$backend_dir/venv/bin/ruff"
    elif [ -x "$shared_venv" ]; then
        RUFF_CMD="$shared_venv"
    elif command -v ruff >/dev/null 2>&1; then
        RUFF_CMD="$(command -v ruff)"
    else
        echo "error: ruff not found. Tried:" >&2
        echo "  - \$RUFF_CMD (unset)" >&2
        echo "  - $backend_dir/venv/bin/ruff (worktree-local venv; .gitignored — run scripts/install.sh to create one)" >&2
        echo "  - $shared_venv (shared main checkout venv)" >&2
        echo "  - bare 'ruff' on PATH (activate a venv first)" >&2
        return 1
    fi
}
