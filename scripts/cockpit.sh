#!/bin/bash
# Claude Cockpit dev supervisor — self-healing backend + frontend.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
: "${LOG_DIR:="$PROJECT_ROOT/logs"}"
: "${RUN_DIR:="$LOG_DIR/.run"}"

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
# crash-loop (MAX_FAILS consecutive fast crashes with no healthy run between).
watch_service() {
    local name="$1" cmd="$2"
    local svc_dir="$LOG_DIR/$name"
    local pidf="$RUN_DIR/$name.pid"
    local base="${COCKPIT_BACKOFF_BASE:-1}"
    local window="${COCKPIT_WINDOW:-30}"
    local max_fails=5
    local fails=0 restart=0 child=""
    mkdir -p "$svc_dir" "$RUN_DIR"
    # On shutdown: reap the current child and stop — do not respawn.
    trap 'kill_tree "$child" 2>/dev/null; rm -f "$pidf"; trap - TERM INT; exit 0' TERM INT

    while true; do
        local ts logf started ended ran
        ts="$(date '+%Y%m%d-%H%M%S')-$$-$restart"
        logf="$svc_dir/run-$ts.log"
        ln -sfn "$logf" "$svc_dir/latest.log"
        started=$(date +%s)
        # Run in its own session so kill_tree can reap the whole subtree.
        # NOTE: cmd is executed as a shell string (not an argv array).
        setsid bash -c "$cmd" >>"$logf" 2>&1 &
        child=$!
        echo "$child" > "$pidf"
        wait "$child"; local code=$?
        ended=$(date +%s); ran=$((ended - started))
        restart=$((restart + 1))
        sup_log "$name exited code=$code (ran ${ran}s, restart #$restart)"

        # A run that stayed up past the health threshold counts as recovery.
        if [ "$ran" -gt "$window" ]; then
            fails=0
        else
            fails=$((fails + 1))
        fi
        if [ "$fails" -ge "$max_fails" ]; then
            sup_log "$name crash-loop ($fails opeenvolgende snelle crashes) — gestopt met herstarten, kijk in $svc_dir/latest.log"
            rm -f "$pidf"
            return 0
        fi
        # Backoff: base, 2*base, capped at 5*base.
        local wait_s=$(( base * (restart < 2 ? 1 : (restart < 3 ? 2 : 5)) ))
        sleep "$wait_s"
    done
}

# --- default service commands (overridable via env for tests) ---
default_backend_cmd() {
    echo "cd '$PROJECT_ROOT/backend' && source venv/bin/activate && exec uvicorn app.main:app --reload --port 8000 ${HOST:+--host $HOST}"
}
default_frontend_cmd() {
    echo "cd '$PROJECT_ROOT/frontend' && exec npm run dev ${HOST:+-- --host $HOST}"
}

# --- supervisor entrypoint (run detached by cmd_start) ---
supervisor_main() {
    mkdir -p "$RUN_DIR"
    echo "$$" > "$RUN_DIR/supervisor.pid"
    local backend_cmd frontend_cmd
    backend_cmd="${COCKPIT_BACKEND_CMD:-}"
    [ -z "$backend_cmd" ] && backend_cmd="$(default_backend_cmd)"
    frontend_cmd="${COCKPIT_FRONTEND_CMD:-}"
    [ -z "$frontend_cmd" ] && frontend_cmd="$(default_frontend_cmd)"
    local children=()
    trap 'for c in "${children[@]}"; do kill_tree "$c"; done; rm -f "$RUN_DIR"/*.pid; exit 0' TERM INT
    sup_log "supervisor started (pid $$)"
    watch_service backend  "$backend_cmd"  & children+=("$!")
    watch_service frontend "$frontend_cmd" & children+=("$!")
    wait
}

cmd_start() {
    if is_running "$RUN_DIR/supervisor.pid"; then
        echo "Cockpit draait al (supervisor pid $(cat "$RUN_DIR/supervisor.pid")). Gebruik 'restart' of 'status'."
        return 1
    fi
    mkdir -p "$RUN_DIR"
    prune_logs
    # Detach: survive terminal close. Pass HOST + injected cmds through.
    COCKPIT_BACKEND_CMD="${COCKPIT_BACKEND_CMD:-}" \
    COCKPIT_FRONTEND_CMD="${COCKPIT_FRONTEND_CMD:-}" \
    HOST="${HOST:-}" \
    setsid bash "$SCRIPT_DIR/cockpit.sh" __supervisor </dev/null >>"$LOG_DIR/supervisor.log" 2>&1 &
    disown 2>/dev/null || true
    sleep 1
    if is_running "$RUN_DIR/supervisor.pid"; then
        echo "Cockpit gestart (supervisor pid $(cat "$RUN_DIR/supervisor.pid"))."
        echo "Logs: ./scripts/cockpit.sh logs backend"
    else
        echo "Supervisor startte niet — zie $LOG_DIR/supervisor.log"
        return 1
    fi
}

cmd_stop() {
    if is_running "$RUN_DIR/supervisor.pid"; then
        local sup; sup="$(cat "$RUN_DIR/supervisor.pid")"
        kill -TERM "$sup" 2>/dev/null || true
        sleep 1
        kill_tree "$sup"
        echo "Cockpit gestopt."
    else
        echo "Cockpit draaide niet."
    fi
    rm -f "$RUN_DIR"/*.pid 2>/dev/null || true
}

cmd_restart() { cmd_stop; sleep 1; cmd_start; }

cmd_status() {
    local s b f
    is_running "$RUN_DIR/supervisor.pid" && s="running (pid $(cat "$RUN_DIR/supervisor.pid"))" || s="stopped"
    is_running "$RUN_DIR/backend.pid"    && b="running (pid $(cat "$RUN_DIR/backend.pid"))"    || b="stopped"
    is_running "$RUN_DIR/frontend.pid"   && f="running (pid $(cat "$RUN_DIR/frontend.pid"))"   || f="stopped"
    echo "supervisor: $s"
    echo "backend:    $b"
    echo "frontend:   $f"
}

cmd_logs() {
    local svc="${1:-backend}"
    local latest="$LOG_DIR/$svc/latest.log"
    if [ -L "$latest" ] || [ -f "$latest" ]; then
        tail -f "$latest"
    else
        echo "Geen log voor '$svc' (nog niet gestart?). Verwacht: $latest"
        return 1
    fi
}

usage() {
    cat <<EOF
Usage: $0 <command> [--host <host>]

Commands:
  start          Start de zelfhelende supervisor (gedetacheerd)
  stop           Stop supervisor + processen
  restart        Stop, dan start
  status         Toon status van supervisor/backend/frontend
  logs [svc]     Volg logs (svc = backend|frontend, default backend)

Options:
  --host <host>  Bind backend+frontend aan host (bv. 0.0.0.0)
EOF
}

main() {
    HOST=""
    local cmd="${1:-}"; shift || true
    # parse --host from remaining args
    local rest=() svc_arg=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --host) HOST="${2:-}"; shift 2 ;;
            --host=*) HOST="${1#*=}"; shift ;;
            *) rest+=("$1"); shift ;;
        esac
    done
    [ "${#rest[@]}" -gt 0 ] && svc_arg="${rest[0]}"
    case "$cmd" in
        start)      cmd_start ;;
        stop)       cmd_stop ;;
        restart)    cmd_restart ;;
        status)     cmd_status ;;
        logs)       cmd_logs "$svc_arg" ;;
        __supervisor) supervisor_main ;;
        -h|--help|"") usage ;;
        *) echo "Onbekend commando: $cmd"; usage; return 1 ;;
    esac
}

# Source-guard: when sourced (e.g. by tests) only define functions.
# When executed directly (BASH_SOURCE[0] == $0), always run main regardless of
# COCKPIT_NO_MAIN — that var only prevents execution when sourced.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
elif [[ "${COCKPIT_NO_MAIN:-0}" != "1" ]]; then
    main "$@"
fi
