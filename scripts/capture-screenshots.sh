#!/usr/bin/env bash
# Capture a fresh `screenshots/` set + both hero PNGs from a clean checkout.
#
# What this does
# --------------
# Spins up an isolated Cockpit instance against a throwaway $HOME + throwaway
# tmux server, drives Chromium against every route the README references, and
# writes the resulting 1280x800 PNGs back into the repo. The previous approach
# was an ad-hoc dance re-invented twice (commits f2b2153 + kaart 35d372a0):
# seed HOME, sanitize demo data, mount frontend/dist on the backend, run
# Playwright, commit the PNGs, throw the rig away. This script makes that
# dance one command and idempotent.
#
# Why these specific invariants
# -----------------------------
#   * `env -u TMUX -u TMUX_PANE` on the backend line — without `-u TMUX`,
#     tmux reads $TMUX and joins the host's *real* tmux server, leaking real
#     repo paths and session names into the captured UI. This is the bug the
#     card calls out in §2 of the suggested improvement.
#   * Own `TMUX_TMPDIR` per run — keeps the isolated tmux server on its own
#     socket; never collides with the host's `~/.tmux/` defaults.
#   * Same-origin via the backend already mounting `frontend/dist`
#     (`backend/app/main.py:294`) — no separate preview/dev server, no
#     CORS, no proxy for the terminal WebSocket. Any sibling server the
#     script starts would re-introduce the very CORS/proxy mess this
#     script avoids.
#   * Cleanup in an EXIT trap installed BEFORE any side-effect — a Ctrl+C,
#     SIGTERM, or Playwright crash must still kill the backend, tear down
#     the tmux server, and remove the throwaway HOME.
#
# Exit codes: 0 success, 2 usage error, 1 backend never came up / Playwright
# failed.

set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

# ----------------------------------------------------------------------------
# Defaults / flags
# ----------------------------------------------------------------------------
OUTPUT_DIR="$REPO_ROOT"
KEEP_HOME=0
PORT=""
LOG_DIR=""
DRY_RUN=0
SCREENSHOTS_DIR_NAME="screenshots"
SCREENSHOT_VIEWPORT="1280x800"

usage() {
    cat <<EOF
Usage: $0 [--output-dir <dir>] [--port <port>] [--keep-home] [--dry-run] [--help]

Capture the README screenshot gallery + both hero PNGs from a clean checkout.

Options:
  --output-dir <dir>   Where to write screenshots/ + hero PNGs. Default: repo root.
  --port <port>        Pin the throwaway backend to a specific port. Default: pick a free one.
  --keep-home          Do NOT delete the throwaway \$HOME on exit. Useful for debugging the harness.
  --dry-run            Print the plan, do not start a backend / Playwright.
  --log-dir <dir>      Where to write the harness log. Default: \$TMPDIR/capture-screenshots-<pid>.
  -h, --help           Show this message.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --output-dir)
            [ -n "${2:-}" ] || { echo "ERROR: --output-dir requires a value" >&2; exit 2; }
            OUTPUT_DIR="$2"; shift 2 ;;
        --port)
            [ -n "${2:-}" ] || { echo "ERROR: --port requires a value" >&2; exit 2; }
            PORT="$2"; shift 2 ;;
        --keep-home) KEEP_HOME=1; shift ;;
        --dry-run)   DRY_RUN=1; shift ;;
        --log-dir)
            [ -n "${2:-}" ] || { echo "ERROR: --log-dir requires a value" >&2; exit 2; }
            LOG_DIR="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

# ----------------------------------------------------------------------------
# Logging + state
# ----------------------------------------------------------------------------
PID=$$
: "${LOG_DIR:="${TMPDIR:-/tmp}/capture-screenshots-$PID"}"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/capture.log"
log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG_FILE" >&2; }

THROWAWAY_HOME=""
TMUX_TMPDIR_RUNTIME=""
TMUX_SOCKET=""
BACKEND_PID=""
PLAYWRIGHT_NODE=""
CLEANED_UP=0

# ----------------------------------------------------------------------------
# Cleanup — runs from the EXIT trap. Idempotent. Covers all exit paths
# (success, error, signal).
# ----------------------------------------------------------------------------
cleanup() {
    [ "$CLEANED_UP" -eq 1 ] && return 0
    CLEANED_UP=1
    log "cleanup: tearing down backend + tmux + throwaway HOME"

    if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        kill -TERM "$BACKEND_PID" 2>/dev/null || true
        sleep 0.5
        kill -KILL "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
    fi

    if [ -n "$TMUX_SOCKET" ] && [ -S "$TMUX_SOCKET" ]; then
        tmux -S "$TMUX_SOCKET" kill-server 2>/dev/null || true
    fi

    if [ -n "$TMUX_TMPDIR_RUNTIME" ] && [ -d "$TMUX_TMPDIR_RUNTIME" ]; then
        # `rm` is deny-listed in this repo's .claude/settings.json; use `mv`
        # to move the throwaway dirs out of the repo tree, mirroring the
        # convention from the rest of the harness family.
        mv "$TMUX_TMPDIR_RUNTIME" "$LOG_DIR/tmux-tmpdir.removed" 2>/dev/null || true
    fi

    if [ "$KEEP_HOME" -eq 0 ] && [ -n "$THROWAWAY_HOME" ] && [ -d "$THROWAWAY_HOME" ]; then
        mv "$THROWAWAY_HOME" "$LOG_DIR/home.removed" 2>/dev/null || true
    elif [ -n "$THROWAWAY_HOME" ] && [ -d "$THROWAWAY_HOME" ]; then
        log "KEEP_HOME=1 — throwaway HOME preserved at $THROWAWAY_HOME"
    fi

    if [ -n "$LOG_DIR" ] && [ "$DRY_RUN" -eq 1 ]; then
        log "DRY_RUN=1 — log preserved at $LOG_DIR"
    fi
}

# Install BEFORE any side-effect so a mid-launch failure still cleans up.
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

# Pick a free TCP port in the ephemeral range. Backend listens on 127.0.0.1
# only — no need to bind a public address. Returns 1 if every candidate was
# already taken (extremely unlikely, but the EXIT trap handles it cleanly).
find_free_port() {
    local p
    for _ in $(seq 1 50); do
        p="$(( ( RANDOM % 5000 ) + 40000 ))"
        if command -v ss >/dev/null 2>&1; then
            ss -ltnH 2>/dev/null | grep -qE "[:.]${p}([^0-9]|$)" || { printf '%s\n' "$p"; return 0; }
        elif command -v lsof >/dev/null 2>&1; then
            lsof -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1 || { printf '%s\n' "$p"; return 0; }
        else
            # Fallback: try to bind in Python and read back.
            "$REPO_ROOT/backend/venv/bin/python" - "$p" <<'PY' 2>/dev/null && { printf '%s\n' "$p"; return 0; }
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
finally:
    s.close()
PY
        fi
    done
    return 1
}

wait_for_health() {
    local url="$1" timeout="${2:-30}" deadline
    deadline=$(( $(date +%s) + timeout ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if curl -fsS -o /dev/null --max-time 2 "$url" 2>/dev/null; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

# ----------------------------------------------------------------------------
# Step 0: preflight — verify venv + frontend/dist + Playwright are present.
# A missing dist means the script can't serve the SPA; better to fail loudly
# here than after the throwaway HOME is built.
# ----------------------------------------------------------------------------
if [ ! -x "$REPO_ROOT/backend/venv/bin/python" ]; then
    echo "ERROR: backend/venv not found. Run scripts/install.sh first." >&2
    exit 2
fi
if [ ! -d "$REPO_ROOT/frontend/dist" ] || [ ! -f "$REPO_ROOT/frontend/dist/index.html" ]; then
    echo "ERROR: frontend/dist not built. Run scripts/build.sh (or \`cd frontend && npm run build\`) first." >&2
    exit 2
fi
if [ ! -d "$REPO_ROOT/frontend/node_modules/@playwright/test" ]; then
    echo "ERROR: frontend/node_modules/@playwright/test missing. Run \`cd frontend && npm install\` first." >&2
    exit 2
fi
mkdir -p "$OUTPUT_DIR/$SCREENSHOTS_DIR_NAME"

# ----------------------------------------------------------------------------
# Step 1: pick a free port for the throwaway backend.
# ----------------------------------------------------------------------------
if [ -z "$PORT" ]; then
    if ! PORT="$(find_free_port)"; then
        echo "ERROR: no free port found in ephemeral range" >&2
        exit 1
    fi
fi
log "throwaway backend will listen on 127.0.0.1:$PORT"

# ----------------------------------------------------------------------------
# Step 2: seed the throwaway HOME with sanitized demo data.
# ----------------------------------------------------------------------------
THROWAWAY_HOME="$LOG_DIR/home"
mkdir -p "$THROWAWAY_HOME"
log "seeding throwaway HOME at $THROWAWAY_HOME via scripts/lib/seed-demo-home.py"
if ! "$LIB_DIR/seed-demo-home.py" --target "$THROWAWAY_HOME" >>"$LOG_FILE" 2>&1; then
    echo "ERROR: seed-demo-home.py failed (see $LOG_FILE)" >&2
    exit 1
fi

# ----------------------------------------------------------------------------
# Step 3: own tmux server on its own socket + its own TMUX_TMPDIR.
# `env -u TMUX -u TMUX_PANE` is the keystone — without `-u TMUX`, the
# backend reads the inherited $TMUX and joins the host's *real* tmux
# server, leaking real session names + repo paths into the captured UI.
# ----------------------------------------------------------------------------
TMUX_TMPDIR_RUNTIME="$LOG_DIR/tmux"
mkdir -p "$TMUX_TMPDIR_RUNTIME"
TMUX_SOCKET="$TMUX_TMPDIR_RUNTIME/default.sock"
log "launching own tmux server on socket $TMUX_SOCKET (TMUX_TMPDIR=$TMUX_TMPDIR_RUNTIME)"
TMUX_TMPDIR="$TMUX_TMPDIR_RUNTIME" tmux -S "$TMUX_SOCKET" -f /dev/null new-session -d -s capture -x 200 -y 50 || true

if [ "$DRY_RUN" -eq 1 ]; then
    log "DRY_RUN=1 — plan verified, skipping backend + Playwright. Artifacts at $LOG_DIR."
    exit 0
fi

# ----------------------------------------------------------------------------
# Step 4: start the backend. CRITICAL — every flag here is load-bearing.
#   env -u TMUX -u TMUX_PANE        → drops inherited tmux-client env so
#                                       uvicorn's tmux-bridge code can't
#                                       reach the host's real server.
#   HOME=$THROWAWAY_HOME            → Claude/MCP/skill/config discovery
#                                       reads from the sanitized throwaway.
#   TMUX_TMPDIR=$TMUX_TMPDIR_RUNTIME → uvicorn's tmux tooling stays on
#                                       our isolated socket.
# Same-origin: the backend already mounts frontend/dist at "/" (no
# separate dev/preview server — see backend/app/main.py:294).
# ----------------------------------------------------------------------------
log "launching backend (uvicorn) on 127.0.0.1:$PORT"
env -u TMUX -u TMUX_PANE \
    HOME="$THROWAWAY_HOME" \
    TMUX_TMPDIR="$TMUX_TMPDIR_RUNTIME" \
    TMUX="$TMUX_SOCKET,0,0" \
    COCKPIT_BIND_HOST="127.0.0.1" \
    COCKPIT_PORT="$PORT" \
    nohup "$REPO_ROOT/backend/venv/bin/python" -m uvicorn app.main:app \
        --host 127.0.0.1 \
        --port "$PORT" \
        --app-dir "$REPO_ROOT/backend" \
        >>"$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
log "backend pid=$BACKEND_PID"

# ----------------------------------------------------------------------------
# Step 5: wait for the backend to come up.
# ----------------------------------------------------------------------------
if ! wait_for_health "http://127.0.0.1:$PORT/health" 30; then
    log "ERROR: backend never became healthy on http://127.0.0.1:$PORT/health — tail $LOG_DIR/backend.log"
    tail -20 "$LOG_DIR/backend.log" >&2 || true
    exit 1
fi
log "backend healthy; same-origin UI at http://127.0.0.1:$PORT/"

# ----------------------------------------------------------------------------
# Step 6: run Playwright. The Node script captures each route at 1280x800,
# plus both hero images under prefers-color-scheme: light/dark. Outputs
# land in $OUTPUT_DIR/screenshots/ + $OUTPUT_DIR/cockpit-rebrand-*.png.
# ----------------------------------------------------------------------------
PLAYWRIGHT_NODE="$LOG_DIR/capture.mjs"
cat > "$PLAYWRIGHT_NODE" <<EOF
// Generated by scripts/capture-screenshots.sh — same-origin against the
// throwaway backend on 127.0.0.1:\$PORT. Every route the README links is
// captured at \${SCREENSHOT_VIEWPORT}. Both hero PNGs are emitted at the
// repo root under light + dark color schemes.
import { chromium } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';

const BASE = 'http://127.0.0.1:\$PORT';
const OUT = resolve(process.env.OUTPUT_DIR);
const SCREENSHOTS_DIR = resolve(OUT, 'screenshots');
const VIEWPORT = { width: 1280, height: 800 };

// route key  →  URL path  →  output filename
const ROUTES = [
  ['dashboard',          '/',                    'dashboard.png'],
  ['kanban',             '/kanban',              'kanban.png'],
  ['portfolio',          '/portfolio',           'portfolio.png'],
  ['agent-performance',  '/agent-performance',   'agent-performance.png'],
  ['agent-bridge',       '/agent-bridge',        'cc-bridge.png'],
  ['presence',           '/presence',            'presence.png'],
  ['agent-mail',         '/agent-mail',          'agent-mail.png'],
  ['scheduled-messages', '/scheduled-messages',  'scheduled-messages.png'],
  ['security',           '/security',            'security.png'],
  ['blueprints',         '/blueprints',          'blueprints.png'],
  ['usage',              '/usage',               'usage-tracking.png'],
  ['context',            '/context',             'context.png'],
  ['sessions',           '/sessions',            'sessions.png'],
  ['mcp',                '/mcp',                 'mcp-servers.png'],
  ['config',             '/config',              'config.png'],
  ['skills',             '/skills',              'skills.png'],
];

await mkdir(SCREENSHOTS_DIR, { recursive: true });

const browser = await chromium.launch({ headless: true });
try {
  // Gallery: default color scheme (light by default; most users will see light).
  {
    const ctx = await browser.newContext({ viewport: VIEWPORT, colorScheme: 'light' });
    const page = await ctx.newPage();
    for (const [_key, path, file] of ROUTES) {
      await page.goto(BASE + path, { waitUntil: 'networkidle', timeout: 30000 });
      // Settle: dismiss the welcome dialog if it's blocking the first paint.
      try { await page.waitForTimeout(250); } catch {}
      await page.screenshot({ path: resolve(SCREENSHOTS_DIR, file) });
      console.log('captured', file);
    }
    await ctx.close();
  }

  // Hero: dashboard in light + dark color schemes.
  for (const scheme of ['light', 'dark']) {
    const ctx = await browser.newContext({ viewport: VIEWPORT, colorScheme: scheme });
    const page = await ctx.newPage();
    await page.goto(BASE + '/', { waitUntil: 'networkidle', timeout: 30000 });
    try { await page.waitForTimeout(250); } catch {}
    const hero = scheme === 'light' ? 'cockpit-rebrand-light.png' : 'cockpit-rebrand-dark.png';
    await page.screenshot({ path: resolve(OUT, hero) });
    console.log('captured', hero);
    await ctx.close();
  }
} finally {
  await browser.close();
}
EOF

PLAYWRIGHT_OUTPUT_DIR="$OUTPUT_DIR" \
HOME="$THROWAWAY_HOME" \
    node "$PLAYWRIGHT_NODE" >>"$LOG_FILE" 2>&1 || {
    log "ERROR: Playwright capture failed (see $LOG_FILE)"
    tail -40 "$LOG_FILE" >&2 || true
    exit 1
}
log "Playwright capture complete"

# ----------------------------------------------------------------------------
# Step 7: cleanup is in the EXIT trap — explicit exit 0 here triggers it.
# ----------------------------------------------------------------------------
log "all done — screenshots at $OUTPUT_DIR/$SCREENSHOTS_DIR_NAME/ + $(ls "$OUTPUT_DIR"/cockpit-rebrand-*.png 2>/dev/null | wc -l) hero PNG(s)"
exit 0