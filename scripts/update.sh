#!/bin/bash
# update.sh — One-click self-update for Claude Cockpit.
#
# Called by the backend API. Streams structured JSON events to stdout:
#   {"event":"<type>","message":"...","data":{...}}
# Exit code 0 = success, non-zero = failure (auto-rollback already applied).
#
# Events: preflight, pulling, building, installing, healthcheck, done, error
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

log_event() {
    local type="$1" msg="${2:-}" data="${3:-{}}"
    printf '{"event":"%s","message":"%s","data":%s}\n' "$type" "$msg" "$data"
}

PREVIOUS_HEAD=""

# ── Preflight ──────────────────────────────────────────────────────────────

log_event "preflight" "Preflight checks uitvoeren..."

# Check for dirty working tree
if ! GIT_DIR="$PROJECT_ROOT/.git" git status --porcelain 2>/dev/null | grep -q .; then
    : # clean
else
    log_event "error" "Werkmap is niet schoon. Commit of stash wijzigingen eerst."
    exit 1
fi

# Check git is usable
if ! GIT_DIR="$PROJECT_ROOT/.git" git rev-parse HEAD >/dev/null 2>&1; then
    log_event "error" "Geen geldige git repository."
    exit 1
fi

PREVIOUS_HEAD="$(GIT_DIR="$PROJECT_ROOT/.git" git rev-parse HEAD)"

# ── Pull latest ────────────────────────────────────────────────────────────

log_event "pulling" "Nieuwste code ophalen via git pull..."

if ! git -C "$PROJECT_ROOT" fetch origin 2>&1 | while IFS= read -r line; do
    log_event "pulling" "$line"
done; then
    log_event "error" "git fetch origin mislukt."
    exit 1
fi

# Determine current branch and merge
CURRENT_BRANCH="$(GIT_DIR="$PROJECT_ROOT/.git" git rev-parse --abbrev-ref HEAD)"
if [ "$CURRENT_BRANCH" = "master" ] || [ "$CURRENT_BRANCH" = "main" ]; then
    # Fast-forward only: no accidental merge commits
    if ! git -C "$PROJECT_ROOT" merge --ff-only "origin/$CURRENT_BRANCH" 2>&1 | while IFS= read -r line; do
        log_event "pulling" "$line"
    done; then
        log_event "error" "git merge --ff-only origin/$CURRENT_BRANCH is niet mogelijk (diverged?)."
        exit 1
    fi
else
    # For branches: pull with rebase
    if ! git -C "$PROJECT_ROOT" pull --rebase 2>&1 | while IFS= read -r line; do
        log_event "pulling" "$line"
    done; then
        log_event "error" "git pull --rebase mislukt."
        exit 1
    fi
fi

NEW_HEAD="$(GIT_DIR="$PROJECT_ROOT/.git" git rev-parse HEAD)"

if [ "$PREVIOUS_HEAD" = "$NEW_HEAD" ]; then
    log_event "done" "Al up-to-date — geen wijzigingen om te bouwen." '{"rolled_back":false,"already_latest":true}'
    # Nothing to build or restart — just report success.
    exit 0
fi

log_event "pulling" "Bijgewerkt naar $(git -C "$PROJECT_ROOT" log --oneline -1)"

# ── Frontend build ─────────────────────────────────────────────────────────

log_event "building" "Frontend dependencies installeren..."

if ! (cd "$PROJECT_ROOT/frontend" && npm install 2>&1); then
    log_event "error" "npm install mislukt — rollback starten..."
    git -C "$PROJECT_ROOT" reset --hard "$PREVIOUS_HEAD"
    exit 1
fi

log_event "building" "Frontend builden (npm run build)..."

if ! (cd "$PROJECT_ROOT/frontend" && npm run build 2>&1); then
    log_event "error" "Frontend build mislukt — rollback starten..."
    git -C "$PROJECT_ROOT" reset --hard "$PREVIOUS_HEAD"
    exit 1
fi

log_event "building" "Frontend build geslaagd."

# ── Backend dependencies ───────────────────────────────────────────────────

log_event "installing" "Backend dependencies installeren..."

if [ -f "$PROJECT_ROOT/backend/venv/bin/activate" ]; then
    if ! (cd "$PROJECT_ROOT/backend" && source venv/bin/activate && pip install -q -r requirements-dev.txt 2>&1); then
        log_event "error" "Backend pip install mislukt — rollback starten..."
        git -C "$PROJECT_ROOT" reset --hard "$PREVIOUS_HEAD"
        exit 1
    fi
fi

if [ -f "$PROJECT_ROOT/backend/package-lock.json" ]; then
    if ! (cd "$PROJECT_ROOT/backend" && npm install 2>&1); then
        log_event "error" "Backend npm install mislukt — rollback starten..."
        git -C "$PROJECT_ROOT" reset --hard "$PREVIOUS_HEAD"
        exit 1
    fi
fi

log_event "installing" "Backend dependencies up-to-date."

# ── Healthcheck ────────────────────────────────────────────────────────────

log_event "healthcheck" "Healthcheck na herstart..."

# Give the server a moment — the supervisor will have restarted it
sleep 2

HEALTH_URL="http://127.0.0.1:8000/api/v1/health"
MAX_RETRIES=12
RETRY_DELAY=5

for i in $(seq 1 "$MAX_RETRIES"); do
    if curl -fsS -o /dev/null --max-time 5 "$HEALTH_URL" 2>/dev/null; then
        log_event "done" "Update geslaagd! Cockpit draait op de nieuwe versie." \
            '{"rolled_back":false,"commit":"'"$NEW_HEAD"'"}'
        exit 0
    fi
    log_event "healthcheck" "Wachten op herstart (poging $i/$MAX_RETRIES)..."
    sleep "$RETRY_DELAY"
done

# Healthcheck failed — rollback
log_event "error" "Cockpit reageert niet na update — rollback naar vorige commit..."
git -C "$PROJECT_ROOT" reset --hard "$PREVIOUS_HEAD"

# Rebuild frontend with old code
(cd "$PROJECT_ROOT/frontend" && npm run build 2>&1)
# Reinstall backend deps at old version
if [ -f "$PROJECT_ROOT/backend/venv/bin/activate" ]; then
    (cd "$PROJECT_ROOT/backend" && source venv/bin/activate && pip install -q -r requirements-dev.txt 2>&1)
fi

log_event "error" "Rollback voltooid. Cockpit draait op vorige versie." \
    '{"rolled_back":true,"commit":"'"$PREVIOUS_HEAD"'"}'
exit 1
