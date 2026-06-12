# Self-healing dev supervisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Een gedetacheerde bash-supervisor (`scripts/cockpit.sh`) die backend + frontend bewaakt, crashende processen automatisch herstart met backoff en crash-loop-guard, output naar logbestanden schrijft met 7-daagse retentie, en blijft draaien nadat de terminal sluit.

**Architecture:** Eén bash-script `scripts/cockpit.sh` met twee gezichten: (1) een CLI-dispatcher (`start`/`stop`/`restart`/`status`/`logs`) die de gebruiker aanroept, en (2) een verborgen `__supervisor`-modus die `start` gedetacheerd via `setsid` opstart. De supervisor draait per service een `watch_service`-loop die het proces herstart bij exit. De te starten commando's zitten achter env-vars (`COCKPIT_BACKEND_CMD` / `COCKPIT_FRONTEND_CMD`) met de echte uvicorn/npm-commando's als default — die seam maakt de supervisor testbaar met onschuldige fake-commando's. Het script is source-safe (functies worden gedefinieerd zonder iets te starten) zodat een bash-testharnas de pure functies direct kan aanroepen.

**Tech Stack:** Bash 5.x, `setsid`, `find`, POSIX-signalen. Geen extra dependencies. Tests zijn een bash-harnas (`scripts/test_cockpit.sh`) in de stijl van het bestaande `backend/test_commands_api.sh`.

---

## File Structure

- **Create `scripts/cockpit.sh`** — control-script + supervisor. Eén verantwoordelijkheid: dev-processen zelfhelend draaien. Bevat: pad-resolutie, helperfuncties (`prune_logs`, `is_running`, `kill_tree`, `now_ms`), CLI-commando's (`cmd_start`/`cmd_stop`/`cmd_restart`/`cmd_status`/`cmd_logs`), de supervisor (`supervisor_main`, `watch_service`), en een source-guard + `main`-dispatcher onderaan.
- **Create `scripts/test_cockpit.sh`** — bash-testharnas die `cockpit.sh` met `COCKPIT_NO_MAIN=1` sourcet en functies test, plus integratietests van de start/stop-lifecycle met geïnjecteerde fake-commando's.
- **`.gitignore`** — `logs/` staat er al in (regel 37); geen wijziging nodig, enkel verifiëren.

Paden binnen het script:
- `PROJECT_ROOT` = parent van `scripts/`.
- `LOG_DIR` = `$PROJECT_ROOT/logs`.
- `RUN_DIR` = `$LOG_DIR/.run` — PID-files (`supervisor.pid`, `backend.pid`, `frontend.pid`).
- Per service: `$LOG_DIR/<service>/run-<timestamp>.log` + `$LOG_DIR/<service>/latest.log` (symlink).
- `$LOG_DIR/supervisor.log` — exit/restart-events (append, niet geprunet).

---

## Task 1: Skelet + source-guard

**Files:**
- Create: `scripts/cockpit.sh`
- Test: `scripts/test_cockpit.sh`

- [ ] **Step 1: Write the failing test**

Create `scripts/test_cockpit.sh`:

```bash
#!/bin/bash
# Test harness for scripts/cockpit.sh
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# Source the script without running main()
export COCKPIT_NO_MAIN=1
# shellcheck disable=SC1090
source "$SCRIPT_DIR/cockpit.sh"

echo "Task 1: source-guard + paths"
check "PROJECT_ROOT set"        '[ -n "$PROJECT_ROOT" ]'
check "LOG_DIR under root"      '[ "$LOG_DIR" = "$PROJECT_ROOT/logs" ]'
check "cmd_status is a function" 'declare -F cmd_status >/dev/null'

echo ""
echo "Total: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash scripts/test_cockpit.sh`
Expected: FAIL — `cockpit.sh` bestaat nog niet (`source: No such file`).

- [ ] **Step 3: Write minimal implementation**

Create `scripts/cockpit.sh`:

```bash
#!/bin/bash
# Claude Cockpit dev supervisor — self-healing backend + frontend.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_ROOT/logs"
RUN_DIR="$LOG_DIR/.run"

cmd_status() { echo "status: not yet implemented"; }

main() { echo "main: not yet implemented"; }

# Source-guard: when sourced (e.g. by tests) only define functions.
if [[ "${COCKPIT_NO_MAIN:-0}" != "1" && "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash scripts/test_cockpit.sh`
Expected: PASS — `3 passed, 0 failed`.

- [ ] **Step 5: Commit**

```bash
chmod +x scripts/cockpit.sh scripts/test_cockpit.sh
git add scripts/cockpit.sh scripts/test_cockpit.sh
git commit -m "feat(cockpit): script skeleton with source-guard"
```

---

## Task 2: Log-retentie (`prune_logs`)

**Files:**
- Modify: `scripts/cockpit.sh`
- Test: `scripts/test_cockpit.sh`

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_cockpit.sh` before the `Total:` echo:

```bash
echo ""
echo "Task 2: prune_logs"
TMP_LOGS="$(mktemp -d)"
mkdir -p "$TMP_LOGS/backend"
touch "$TMP_LOGS/backend/run-old.log"  && touch -d "8 days ago" "$TMP_LOGS/backend/run-old.log"
touch "$TMP_LOGS/backend/run-new.log"
LOG_DIR="$TMP_LOGS" prune_logs
check "old run-log pruned"   '[ ! -f "$TMP_LOGS/backend/run-old.log" ]'
check "fresh run-log kept"   '[ -f "$TMP_LOGS/backend/run-new.log" ]'
rm -rf "$TMP_LOGS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash scripts/test_cockpit.sh`
Expected: FAIL — `prune_logs: command not found` / both checks fail.

- [ ] **Step 3: Write minimal implementation**

In `scripts/cockpit.sh`, add after the path vars:

```bash
# Delete per-run logs older than 7 days. Only touches run-*.log so PID files
# and supervisor.log are never removed.
prune_logs() {
    [ -d "$LOG_DIR" ] || return 0
    find "$LOG_DIR" -type f -name 'run-*.log' -mtime +7 -delete 2>/dev/null || true
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash scripts/test_cockpit.sh`
Expected: PASS — Task 1 + Task 2 checks all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/cockpit.sh scripts/test_cockpit.sh
git commit -m "feat(cockpit): prune run-logs older than 7 days"
```

---

## Task 3: PID-helpers (`is_running`, `kill_tree`)

**Files:**
- Modify: `scripts/cockpit.sh`
- Test: `scripts/test_cockpit.sh`

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_cockpit.sh` before the `Total:` echo:

```bash
echo ""
echo "Task 3: pid helpers"
TMP_RUN="$(mktemp -d)"
PIDF="$TMP_RUN/x.pid"
check "is_running false when no pidfile" '! is_running "$PIDF"'
sleep 30 & SLEEP_PID=$!
echo "$SLEEP_PID" > "$PIDF"
check "is_running true for live pid"     'is_running "$PIDF"'
kill_tree "$SLEEP_PID"
check "is_running false after kill_tree"  '! kill -0 "$SLEEP_PID" 2>/dev/null'
rm -rf "$TMP_RUN"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash scripts/test_cockpit.sh`
Expected: FAIL — `is_running: command not found`.

- [ ] **Step 3: Write minimal implementation**

In `scripts/cockpit.sh`, add:

```bash
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash scripts/test_cockpit.sh`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/cockpit.sh scripts/test_cockpit.sh
git commit -m "feat(cockpit): is_running + kill_tree helpers"
```

---

## Task 4: `watch_service` — restart-loop met backoff + crash-loop-guard

**Files:**
- Modify: `scripts/cockpit.sh`
- Test: `scripts/test_cockpit.sh`

This is the heart of the self-healing. `watch_service <name> <cmd>` runs `<cmd>` via `bash -c`, logs each run to a fresh `run-<ts>.log`, and on exit logs the code to `supervisor.log` and restarts. Backoff grows 1→2→5s (capped). A crash-loop guard stops restarting after 5 exits within 30s; the counter resets once a run stays up >30s.

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_cockpit.sh` before the `Total:` echo:

```bash
echo ""
echo "Task 4: watch_service crash-loop guard"
TMP_W="$(mktemp -d)"
mkdir -p "$TMP_W"
# A command that always fails instantly -> must trip the crash-loop guard fast.
COCKPIT_BACKOFF_BASE=0 LOG_DIR="$TMP_W" \
    watch_service flap "exit 1" >/dev/null 2>&1
GUARD_HITS="$(grep -c 'crash-loop' "$TMP_W/supervisor.log" 2>/dev/null || echo 0)"
EXIT_LINES="$(grep -c 'flap exited' "$TMP_W/supervisor.log" 2>/dev/null || echo 0)"
check "crash-loop guard tripped"          '[ "$GUARD_HITS" -ge 1 ]'
check "logged ~5 exits before giving up"  '[ "$EXIT_LINES" -ge 5 ] && [ "$EXIT_LINES" -le 7 ]'
check "a run-log was written"             'ls "$TMP_W"/flap/run-*.log >/dev/null 2>&1'
rm -rf "$TMP_W"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash scripts/test_cockpit.sh`
Expected: FAIL — `watch_service: command not found`.

- [ ] **Step 3: Write minimal implementation**

In `scripts/cockpit.sh`, add:

```bash
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash scripts/test_cockpit.sh`
Expected: PASS — guard trips, ~5 exit lines logged, a run-log exists. (Runs fast because `COCKPIT_BACKOFF_BASE=0`.)

- [ ] **Step 5: Commit**

```bash
git add scripts/cockpit.sh scripts/test_cockpit.sh
git commit -m "feat(cockpit): watch_service restart loop with crash-loop guard"
```

---

## Task 5: Default commando's + supervisor + CLI (`start`/`stop`/`status`/`restart`/`logs`)

**Files:**
- Modify: `scripts/cockpit.sh`
- Test: `scripts/test_cockpit.sh`

- [ ] **Step 1: Write the failing test (integration lifecycle)**

Append to `scripts/test_cockpit.sh` before the `Total:` echo:

```bash
echo ""
echo "Task 5: start/status/stop lifecycle (injected commands)"
TMP_L="$(mktemp -d)"
export COCKPIT_BACKEND_CMD="sleep 1000"
export COCKPIT_FRONTEND_CMD="sleep 1000"
# Run the real CLI in a subshell with isolated dirs.
run_cli() { LOG_DIR="$TMP_L" RUN_DIR="$TMP_L/.run" bash "$SCRIPT_DIR/cockpit.sh" "$@"; }

run_cli start >/dev/null 2>&1
sleep 2
SUP_PID="$(cat "$TMP_L/.run/supervisor.pid" 2>/dev/null)"
check "supervisor pid recorded"   '[ -n "$SUP_PID" ] && kill -0 "$SUP_PID" 2>/dev/null'
check "status reports running"    'run_cli status 2>&1 | grep -qi "supervisor.*running\|running"'
# Kill backend child -> supervisor must respawn it.
BK_PID="$(cat "$TMP_L/.run/backend.pid" 2>/dev/null)"
kill "$BK_PID" 2>/dev/null
sleep 3
BK_PID2="$(cat "$TMP_L/.run/backend.pid" 2>/dev/null)"
check "backend respawned (new pid)" '[ -n "$BK_PID2" ] && [ "$BK_PID2" != "$BK_PID" ] && kill -0 "$BK_PID2" 2>/dev/null'
run_cli stop >/dev/null 2>&1
sleep 1
check "supervisor gone after stop" '! kill -0 "$SUP_PID" 2>/dev/null'
check "stop is idempotent"          'run_cli stop >/dev/null 2>&1'
unset COCKPIT_BACKEND_CMD COCKPIT_FRONTEND_CMD
rm -rf "$TMP_L"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash scripts/test_cockpit.sh`
Expected: FAIL — `start`/`status`/`stop` not implemented; supervisor.pid never written.

- [ ] **Step 3: Write minimal implementation**

In `scripts/cockpit.sh`, replace the placeholder `cmd_status` and `main` with the full CLI + supervisor. Add HOST parsing too.

```bash
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
    backend_cmd="${COCKPIT_BACKEND_CMD:-$(default_backend_cmd)}"
    frontend_cmd="${COCKPIT_FRONTEND_CMD:-$(default_frontend_cmd)}"
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
```

Note: the empty `COCKPIT_BACKEND_CMD=""` passed to the detached supervisor must be treated as "use default". Adjust `supervisor_main` to fall back when empty:

```bash
    backend_cmd="${COCKPIT_BACKEND_CMD:-}"
    [ -z "$backend_cmd" ] && backend_cmd="$(default_backend_cmd)"
    frontend_cmd="${COCKPIT_FRONTEND_CMD:-}"
    [ -z "$frontend_cmd" ] && frontend_cmd="$(default_frontend_cmd)"
```

(Use this two-line form in `supervisor_main` instead of the single `:-` defaults so an explicitly-empty env var still falls back.)

- [ ] **Step 4: Run test to verify it passes**

Run: `bash scripts/test_cockpit.sh`
Expected: PASS — supervisor starts detached, status reports running, killed backend respawns with a new pid, stop kills everything, second stop is a no-op. All Task 1–5 checks pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/cockpit.sh scripts/test_cockpit.sh
git commit -m "feat(cockpit): detached supervisor + start/stop/status/restart/logs CLI"
```

---

## Task 6: Echte rooktest + documentatie

**Files:**
- Modify: `scripts/cockpit.sh` (alleen indien rooktest iets blootlegt)
- Modify: `CLAUDE.md` (Commands-sectie)

- [ ] **Step 1: Smoke test tegen de echte stack**

Run:
```bash
./scripts/cockpit.sh start
sleep 4
./scripts/cockpit.sh status
curl -s http://localhost:8000/api/v1/health
```
Expected: status toont supervisor/backend/frontend `running`; health geeft een JSON-respons. Controleer dat `logs/backend/latest.log` uvicorn-output bevat.

- [ ] **Step 2: Crash-herstel handmatig verifiëren**

Run:
```bash
kill "$(cat logs/.run/backend.pid)"
sleep 4
./scripts/cockpit.sh status
tail -n 5 logs/supervisor.log
```
Expected: backend draait weer met een nieuwe pid; `supervisor.log` toont de exit + restart. Frontend bleef ononderbroken draaien.

- [ ] **Step 3: Opruimen**

Run:
```bash
./scripts/cockpit.sh stop
./scripts/cockpit.sh status
```
Expected: alles `stopped`; geen wees-processen (`pgrep -f "uvicorn app.main" || echo clean`).

- [ ] **Step 4: Documenteer in CLAUDE.md**

In `CLAUDE.md`, onder de `## Commands` → Development-sectie, voeg toe na de `./scripts/dev.sh`-regel:

```bash
./scripts/cockpit.sh start    # Zelfhelende, gedetacheerde dev-stack (auto-restart + logs in logs/)
./scripts/cockpit.sh status   # Status van supervisor/backend/frontend
./scripts/cockpit.sh logs backend   # Volg backend-logs
./scripts/cockpit.sh stop     # Stop alles
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md scripts/cockpit.sh
git commit -m "docs(cockpit): document self-healing supervisor in CLAUDE.md"
```

---

## Self-Review notes

- **Spec coverage:** auto-restart → Task 4/5; crash-logs bewaren → Task 4 (run-logs) + Task 5 (latest.log/logs cmd); overleeft terminal sluiten → Task 5 (`setsid`+`disown` in `cmd_start`); 7-daagse retentie → Task 2 (`prune_logs`, aangeroepen in `cmd_start`); `logs/` gitignored → al aanwezig (geverifieerd). Crash-loop-guard → Task 4. Error handling (dubbele start, idempotente stop, stale pid) → Task 5.
- **Type/naam-consistentie:** `RUN_DIR`, `LOG_DIR`, pid-bestandsnamen (`supervisor.pid`/`backend.pid`/`frontend.pid`), functienamen (`prune_logs`, `is_running`, `kill_tree`, `watch_service`, `sup_log`, `supervisor_main`, `cmd_*`) consistent over alle taken.
- **Testbaarheids-seam:** `COCKPIT_NO_MAIN` (source-guard), `COCKPIT_BACKEND_CMD`/`COCKPIT_FRONTEND_CMD` (geïnjecteerde commando's), `COCKPIT_BACKOFF_BASE` (snelle crash-loop-test). Allemaal met echte defaults.
