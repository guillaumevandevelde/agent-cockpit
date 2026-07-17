# Shared pytest-venv resolution for scripts/pytest-baseline.sh and
# scripts/pytest-compare.sh. Mirrors the fallback chain scripts/run-single-test.sh
# already uses, so both scripts work unmodified in a worktree session with no
# local venv (.gitignore excludes venv/, scripts/install.sh is never run
# there — see CLAUDE.md "Werkomgeving in worktree") instead of dying with
# "not executable — run scripts/install.sh first".
#
# Usage: source this file, then:
#   resolve_pytest_cmd "$BACKEND_DIR" [shared_venv_override]
# On success, sets $PYTEST_CMD and returns 0. On failure, prints a "tried"
# hint to stderr and returns 1 — caller decides whether that's fatal.
#
# Fallback order (first hit wins):
#   1. $PYTEST_CMD already set by the caller (explicit override) — left
#      untouched, including if it points nowhere; the caller's own
#      executable check catches that.
#   2. <backend_dir>/venv/bin/pytest — worktree-local venv.
#   3. shared_venv_override, defaulting to
#      /home/vdvgu/claude-cockpit/backend/venv/bin/pytest — the shared
#      main-checkout venv. The override param exists only so tests can point
#      this at a fixture instead of the real shared box path.
#   4. bare `pytest` on PATH.
resolve_pytest_cmd() {
    local backend_dir="$1"
    local shared_venv="${2:-/home/vdvgu/claude-cockpit/backend/venv/bin/pytest}"

    if [ -n "${PYTEST_CMD:-}" ]; then
        return 0
    elif [ -x "$backend_dir/venv/bin/pytest" ]; then
        PYTEST_CMD="$backend_dir/venv/bin/pytest"
    elif [ -x "$shared_venv" ]; then
        PYTEST_CMD="$shared_venv"
    elif command -v pytest >/dev/null 2>&1; then
        PYTEST_CMD="$(command -v pytest)"
    else
        echo "error: pytest not found. Tried:" >&2
        echo "  - \$PYTEST_CMD (unset)" >&2
        echo "  - $backend_dir/venv/bin/pytest (worktree-local venv; .gitignored — run scripts/install.sh to create one)" >&2
        echo "  - $shared_venv (shared main checkout venv)" >&2
        echo "  - bare 'pytest' on PATH (activate a venv first)" >&2
        return 1
    fi
}
