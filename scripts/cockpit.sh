#!/bin/bash
# Claude Cockpit dev supervisor — self-healing backend + frontend.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_ROOT/logs"
RUN_DIR="$LOG_DIR/.run"

# Delete per-run logs older than 7 days. Only touches run-*.log so PID files
# and supervisor.log are never removed.
prune_logs() {
    [ -d "$LOG_DIR" ] || return 0
    find "$LOG_DIR" -type f -name 'run-*.log' -mtime +7 -delete 2>/dev/null || true
}

cmd_status() { echo "status: not yet implemented"; }

main() { echo "main: not yet implemented"; }

# Source-guard: when sourced (e.g. by tests) only define functions.
if [[ "${COCKPIT_NO_MAIN:-0}" != "1" && "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
