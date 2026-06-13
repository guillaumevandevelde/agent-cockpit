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

# True when the pid file exists and names a live process.
is_running() {
    local pidf="$1" pid
    [ -f "$pidf" ] || return 1
    pid="$(cat "$pidf" 2>/dev/null)"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# Kill a process and its entire descendant tree (ported from dev.sh).
kill_tree() {
    local pid="$1"
    [ -z "$pid" ] && return 0
    local pids="$pid" frontier="$pid" next
    while [ -n "$frontier" ]; do
        next="$(pgrep -P $frontier 2>/dev/null | tr '\n' ' ')"
        [ -z "$next" ] && break
        pids="$pids $next"
        frontier="$next"
    done
    kill -TERM $pids 2>/dev/null || true
    sleep 1
    kill -KILL $pids 2>/dev/null || true
}

# Append a line to the supervisor event log.
sup_log() {
    mkdir -p "$LOG_DIR"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_DIR/supervisor.log"
}

# Supervise one service: run cmd, restart on exit with backoff, give up on
# crash-loop (>= MAX_FAILS exits within WINDOW seconds).
watch_service() {
    local name="$1" cmd="$2"
    local svc_dir="$LOG_DIR/$name"
    local pidf="$RUN_DIR/$name.pid"
    local base="${COCKPIT_BACKOFF_BASE:-1}"
    local max_fails=5 window=30
    local fails=0 window_start restart=0
    mkdir -p "$svc_dir" "$RUN_DIR"
    window_start=$(date +%s)

    while true; do
        local ts logf started ended ran
        ts="$(date '+%Y%m%d-%H%M%S')-$$-$restart"
        logf="$svc_dir/run-$ts.log"
        ln -sfn "$logf" "$svc_dir/latest.log"
        started=$(date +%s)
        # Run in its own session so kill_tree can reap the whole subtree.
        setsid bash -c "$cmd" >>"$logf" 2>&1 &
        local child=$!
        echo "$child" > "$pidf"
        wait "$child"; local code=$?
        ended=$(date +%s); ran=$((ended - started))
        restart=$((restart + 1))
        sup_log "$name exited code=$code (ran ${ran}s, restart #$restart)"

        # Reset the crash-loop window if the run was healthy.
        if [ "$ran" -gt "$window" ]; then
            fails=0; window_start=$ended
        fi
        fails=$((fails + 1))
        if [ $((ended - window_start)) -le "$window" ] && [ "$fails" -ge "$max_fails" ]; then
            sup_log "$name crash-loop ($fails exits in <= ${window}s) — gestopt met herstarten, kijk in $svc_dir/latest.log"
            rm -f "$pidf"
            return 0
        fi
        # Backoff: base, 2*base, capped at 5*base.
        local wait_s=$(( base * (restart < 2 ? 1 : (restart < 3 ? 2 : 5)) ))
        sleep "$wait_s"
    done
}

cmd_status() { echo "status: not yet implemented"; }

main() { echo "main: not yet implemented"; }

# Source-guard: when sourced (e.g. by tests) only define functions.
if [[ "${COCKPIT_NO_MAIN:-0}" != "1" && "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
