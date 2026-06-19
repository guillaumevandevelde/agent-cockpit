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

# Read a process's argv as a space-joined string ("" if the process is gone).
proc_cmdline() {
    local pid="$1"
    [ -r "/proc/$pid/cmdline" ] || return 0
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null
}

# True when the pid file names a live process. With a second arg, ALSO require
# that process's argv to contain that substring. PIDs are recycled (especially
# from low numbers right after a reboot), so a leftover pid file can name a
# completely unrelated process — e.g. a tmux or claude session. Without this
# check, stop/restart would TERM/KILL that foreign tree. The pattern makes us
# act only on a process we can prove is ours.
is_running() {
    local pidf="$1" pattern="${2:-}" pid
    [ -f "$pidf" ] || return 1
    pid="$(cat "$pidf" 2>/dev/null)"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    if [ -n "$pattern" ]; then
        case "$(proc_cmdline "$pid")" in
            *"$pattern"*) : ;;
            *) return 1 ;;
        esac
    fi
    return 0
}

# Marker that uniquely identifies our supervisor process in /proc/<pid>/cmdline.
SUPERVISOR_MARKER="cockpit.sh __supervisor"

# $RUN_DIR lives on disk and survives reboots, but PIDs do not. Stamp the dir
# with the kernel boot id; when it changes, every pid file is from a previous
# boot and its number may now belong to something else — so wipe them before we
# read (let alone kill) anything. This is the primary guard against the
# "restart killed my claude session, had to reboot" failure.
current_boot_id() { cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown; }

ensure_fresh_boot() {
    # On WSL2/NTFS, mkdir -p fails to create nested dirs when the parent doesn't
    # exist yet — split into two explicit steps to work around the WSL bug.
    mkdir -p "$LOG_DIR"
    mkdir -p "$RUN_DIR"
    local idf="$RUN_DIR/boot_id" now stored
    now="$(current_boot_id)"
    stored="$(cat "$idf" 2>/dev/null || true)"
    if [ "$stored" != "$now" ]; then
        [ -n "$stored" ] && sup_log "boot id gewijzigd — stale pid-bestanden uit vorige boot verwijderd"
        rm -f "$RUN_DIR"/*.pid 2>/dev/null || true
        printf '%s\n' "$now" > "$idf"
    fi
}

# True when something is already listening on the given local TCP port.
port_in_use() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -ltnH 2>/dev/null | grep -qE "[:.]${port}([^0-9]|$)"
    elif command -v lsof >/dev/null 2>&1; then
        lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    else
        return 1
    fi
}

# Kill a process and its entire descendant tree (ported from dev.sh).
kill_tree() {
    local pid="$1"
    [ -z "$pid" ] && return 0
    local pids="$pid" frontier="$pid" next
    while [ -n "$frontier" ]; do
        # pgrep -P wants a COMMA-separated ppid list. Passing space-separated
        # pids makes it read the 2nd+ as a name regex, so it silently misses
        # grandchildren (orphaning vite/esbuild/uvicorn children that keep the
        # ports bound). Convert the frontier to a comma list.
        next="$(pgrep -P "${frontier// /,}" 2>/dev/null | tr '\n' ' ')"
        next="${next% }"
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
    mkdir -p "$LOG_DIR"
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
    if is_running "$RUN_DIR/supervisor.pid" "$SUPERVISOR_MARKER"; then
        echo "Cockpit draait al (supervisor pid $(cat "$RUN_DIR/supervisor.pid")). Gebruik 'restart' of 'status'."
        return 1
    fi
    # Preflight: don't crash-loop fighting another stack for the ports. Skipped
    # when commands are injected (tests use fake services on no ports).
    if [ -z "${COCKPIT_BACKEND_CMD:-}" ] && [ -z "${COCKPIT_FRONTEND_CMD:-}" ]; then
        local busy=""
        port_in_use 8000 && busy="8000"
        port_in_use 5173 && busy="${busy:+$busy + }5173"
        if [ -n "$busy" ]; then
            echo "Poort(en) al in gebruik: $busy — waarschijnlijk een losse 'dev.sh' stack of een oude orphan."
            echo "Stop die eerst (./scripts/cockpit.sh stop, of beëindig de dev.sh-stack). Cockpit niet gestart."
            return 1
        fi
    fi
    mkdir -p "$LOG_DIR"
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
    # Only kill a pid we can prove is our supervisor. A stale/foreign pid file
    # (after a reboot, PID reuse) must never reach kill_tree.
    if is_running "$RUN_DIR/supervisor.pid" "$SUPERVISOR_MARKER"; then
        local sup; sup="$(cat "$RUN_DIR/supervisor.pid")"
        kill -TERM "$sup" 2>/dev/null || true
        sleep 1
        kill_tree "$sup"
        echo "Cockpit gestopt."
    else
        echo "Cockpit draaide niet (of het pid-bestand was verouderd)."
    fi
    rm -f "$RUN_DIR"/*.pid 2>/dev/null || true
}

cmd_restart() { cmd_stop; sleep 1; cmd_start; }

cmd_status() {
    local s b f
    is_running "$RUN_DIR/supervisor.pid" "$SUPERVISOR_MARKER" && s="running (pid $(cat "$RUN_DIR/supervisor.pid"))" || s="stopped"
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
    # Wipe pid files left over from a previous boot before any command reads or
    # acts on them. (Not for the detached supervisor — it writes its own pid.)
    case "$cmd" in
        start|stop|restart|status) ensure_fresh_boot ;;
    esac
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

# Source-guard: run main only when executed directly. When the script is
# sourced (e.g. by tests) BASH_SOURCE[0] != $0, so only the functions get
# defined — sourcing is always side-effect free.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
