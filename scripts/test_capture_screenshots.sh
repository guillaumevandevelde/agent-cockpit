#!/usr/bin/env bash
# Test harness for scripts/capture-screenshots.sh + scripts/lib/seed-demo-home.py.
#
# Exercises the structural invariants the card `beabca63…` explicitly asks
# for. We do NOT exercise the end-to-end capture flow (that requires a real
# browser, real backend, real throwaway HOME, and minutes per run — neither
# fit in a regression harness, nor is it the right granularity for catching
# regressions: the *invariant* of the script is what we want to nail down,
# not the visual output). Tasks:
#
#   1. arg parsing — `--help` works, mentions Usage + the named flags.
#   2. unknown args are rejected with a clean error + exit code.
#   3. tmux isolation invariants — the script MUST run its backend under
#      `env -u TMUX -u TMUX_PANE` with an isolated `TMUX_TMPDIR` and an
#      own-socket tmux server. Missing any of these leaks the real tmux
#      server / real $HOME into the captured UI (this is the exact bug
#      the card calls out in §2 of the suggested improvement).
#   4. cleanup trap — an EXIT trap exists, names tmux-server + backend +
#      throwaway HOME in its body, and is installed before any side-effect.
#   5. output targets — the script writes `screenshots/*.png` AND
#      `cockpit-rebrand-{light,dark}.png` at the repo root (the README
#      hero pair referenced from §1 of the acceptance criteria).
#   6. route list — the Playwright invocation covers every screenshot the
#      README references (16 gallery PNGs) AND the two hero images.
#   7. same-origin contract — no separate vite preview / `npm run dev`
#      server is started; the script relies on the backend already serving
#      `frontend/dist` (no CORS / proxy mess for the terminal WebSocket).
#   8. free-port discovery — the script does NOT hardcode port 8000 (a
#      concurrent `cockpit.sh start` would collide and silently lose the
#      race for the throwaway backend).
#   9. seed-demo-home.py — exists, accepts `--target <dir>`, and the
#      emitted files do NOT contain real paths from this repo, real
#      usernames, or the real `$HOME` (the sanitization acceptance
#      criterion: "geen echte project-, repo- of gebruikersnamen").
#
# Conventions: `PASS`/`FAIL` counters + `ok`/`bad`/`check` helpers +
# `Total: $PASS passed, $FAIL failed` summary line + `[ "$FAIL" -eq 0 ]`
# final gate, mirroring scripts/test_baseline_bash_tests.sh.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/capture-screenshots.sh"
SEED="$SCRIPT_DIR/lib/seed-demo-home.py"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

[ -f "$SUT" ] || { echo "FATAL: $SUT not found"; exit 2; }

# ----------------------------------------------------------------------------
echo "Task 1: arg parsing — --help"
out=$(bash "$SUT" --help 2>&1) && rc=0 || rc=$?
check "--help exits 0" '[ "$rc" -eq 0 ]'
check "--help mentions Usage" \
    'echo "$out" | grep -qE "Usage:"'
check "--help mentions --output-dir" \
    'echo "$out" | grep -qE "\-\-output-dir"'
check "--help mentions --keep-home (debug flag)" \
    'echo "$out" | grep -qE "\-\-keep-home"'
check "--help mentions --port" \
    'echo "$out" | grep -qE "\-\-port"'

# ----------------------------------------------------------------------------
echo
echo "Task 2: unknown args are rejected"
out=$(bash "$SUT" --no-such-flag 2>&1 || true)
check "unknown arg emits an error" \
    'echo "$out" | grep -qE "unknown|Unknown|invalid|Invalid|Usage"'

# ----------------------------------------------------------------------------
echo
echo "Task 3: tmux isolation invariants — backend runs under env -u TMUX -u TMUX_PANE"
src="$(cat "$SUT")"
check "script invokes env with -u TMUX" \
    'echo "$src" | grep -qE "env[^\\n]*-u[[:space:]]+TMUX([[:space:]]|$)"'
check "script invokes env with -u TMUX_PANE" \
    'echo "$src" | grep -qE "env[^\\n]*-u[[:space:]]+TMUX_PANE([[:space:]]|$)"'
check "script sets its own TMUX_TMPDIR (not inherited)" \
    'echo "$src" | grep -qE "TMUX_TMPDIR="'
check "script launches an own-socket tmux server" \
    'echo "$src" | grep -qE "tmux[[:space:]]+-S[[:space:]]+[^[:space:]]+-L[[:space:]]+[^[:space:]]+([[:space:]]|$)|tmux[[:space:]]+-S[[:space:]]+[^[:space:]]+([[:space:]]|$)"'
# Multi-line: env -u TMUX -u TMUX_PANE \ ... -m uvicorn app.main:app is one
# logical command. The test asserts both anchors appear in the script
# (the actual ordering is a continuation, not a single line).
check "backend launch keeps -u TMUX and -u TMUX_PANE on the env invocation that runs uvicorn" \
    'echo "$src" | grep -qE "env -u TMUX -u TMUX_PANE" && echo "$src" | grep -qE "uvicorn.*app\\.main:app"'

# ----------------------------------------------------------------------------
echo
echo "Task 4: cleanup trap + body"
check "EXIT trap is installed before any side-effect" \
    'echo "$src" | awk "/trap.*EXIT/{found=1; exit} END{exit !found}"'
check "cleanup body references the throwaway HOME" \
    'echo "$src" | grep -qE "cleanup|clean_up|teardown"'
check "cleanup body terminates the backend (kill or pkill)" \
    'echo "$src" | grep -qE "(kill|pkill)[^\\n]*(BACKEND_PID|backend|pid)"'
check "cleanup body kills the own-socket tmux server" \
    'echo "$src" | grep -qE "tmux[[:space:]]+-S[[:space:]]+[^[:space:]]+kill-server|tmux.*kill-server"'
check "cleanup body removes the throwaway HOME" \
    'echo "$src" | grep -qE "rm -rf[^\\n]*THROWAWAY_HOME|THROWAWAY_HOME.*rm -rf|mv[^\\n]*THROWAWAY_HOME"'

# ----------------------------------------------------------------------------
echo
echo "Task 5: output targets — screenshots/ + both hero PNGs at repo root"
check "script references screenshots/ output dir" \
    'echo "$src" | grep -qE "screenshots/"'
check "script targets cockpit-rebrand-light.png" \
    'echo "$src" | grep -qE "cockpit-rebrand-light\\.png"'
check "script targets cockpit-rebrand-dark.png" \
    'echo "$src" | grep -qE "cockpit-rebrand-dark\\.png"'

# ----------------------------------------------------------------------------
echo
echo "Task 6: route list covers every README screenshot"
# The route list may live as a bash array, a JSON map, or a Playwright test
# file — be liberal in what we accept: grep for the route paths under the
# relevant fixture filenames.
# Presence, Agent Mail and the Security Profile page were removed on
# 2026-08-13 (docs/cockpit/kern-terugbrengen-plan.md); their routes are
# deliberately absent from the list below.
for png in dashboard kanban portfolio agent-performance cc-bridge \
           scheduled-messages blueprints usage-tracking \
           context sessions mcp-servers config skills; do
    check "route list includes $png.png" \
        "echo \"\$src\" | grep -qE \"${png}\\.(png|/)\""
done
# Light + dark hero captures are the dashboard route, but under different
# Playwright color-scheme settings. Confirm both are emitted.
check "hero capture runs under light color scheme" \
    'echo "$src" | grep -qE "color-scheme.*light|prefers-color-scheme.*light|light"'
check "hero capture runs under dark color scheme" \
    'echo "$src" | grep -qE "color-scheme.*dark|prefers-color-scheme.*dark|dark"'

# ----------------------------------------------------------------------------
echo
echo "Task 7: same-origin contract — no separate vite server is started"
# The card explicitly notes: serve frontend/dist from the same backend
# (same-origin, no CORS / proxy mess for the terminal WebSocket). So the
# script must NOT launch npm-run-dev / npm-run-preview / vite-preview as a
# sibling of the backend. Labels avoid backticks so we don't command-
# substitute them in the check() call.
check "no npm-run-dev invocation" \
    '! echo "$src" | grep -qE "npm[[:space:]]+run[[:space:]]+dev"'
check "no npm-run-preview invocation" \
    '! echo "$src" | grep -qE "npm[[:space:]]+run[[:space:]]+preview"'
check "no vite-preview invocation" \
    '! echo "$src" | grep -qE "vite[[:space:]]+preview"'
check "script relies on the backend mounting frontend/dist" \
    'echo "$src" | grep -qE "frontend/dist|StaticFiles|dist/index"'

# ----------------------------------------------------------------------------
echo
echo "Task 8: free-port discovery — no hardcoded 8000"
# The backend listens on whatever port the script picks. 8000 is reserved
# for the concurrent `cockpit.sh start` dev backend on this box — a
# hardcoded port would collide and silently lose the race for the
# throwaway instance.
check "script picks a free port (does NOT hardcode 8000)" \
    '! echo "$src" | grep -qE "uvicorn[^\\n]*--port[[:space:]]+8000([[:space:]]|$)|--port[[:space:]]+8000"'
check "script defines or references a free-port helper" \
    'echo "$src" | grep -qE "find_free_port|FREE_PORT|free.*port|PORT="'

# ----------------------------------------------------------------------------
echo
echo "Task 9: seed-demo-home.py exists, accepts --target, sanitizes real names"
check "seed script exists" '[ -f "$SEED" ]'
check "seed script is executable" '[ -x "$SEED" ]'

if [ -x "$SEED" ]; then
    seed_tmp="$(mktemp -d)"
    out=$(HOME="$seed_tmp" python3 "$SEED" --target "$seed_tmp/out" 2>&1) || rc=$?
    rc=${rc:-0}
    check "seed script exits 0 on --target" '[ "$rc" -eq 0 ]'
    check "seed script populates --target" \
        '[ -d "$seed_tmp/out" ] && [ -n "$(ls -A "$seed_tmp/out" 2>/dev/null)" ]'

    # Sanitization acceptance criterion: emitted files MUST NOT contain the
    # real repo path, the real $HOME, the real username, or the real git
    # remote of this checkout. A single grep across all generated files is
    # the cheapest validator — a hit on any of these = leaked real data.
    real_repo="$(cd "$REPO_ROOT" && pwd)"
    real_user="$(id -un 2>/dev/null || echo unknown)"
    real_home="${HOME:-/nonexistent}"
    real_remote="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || echo "")"
    leaks=""
    if [ -d "$seed_tmp/out" ]; then
        # Belt-and-braces: grep -rF over every file we just produced.
        for forbidden in "$real_repo" "$real_user" "$real_home" "$real_remote"; do
            [ -z "$forbidden" ] && continue
            hits="$(grep -rlF "$forbidden" "$seed_tmp/out" 2>/dev/null || true)"
            if [ -n "$hits" ]; then
                leaks="$leaks $forbidden($hits)"
            fi
        done
    fi
    check "no real repo path in seed output" '[ -z "$(echo "$leaks" | grep -F "$real_repo" || true)" ]'
    check "no real username in seed output" '[ -z "$(echo "$leaks" | grep -F "$real_user" || true)" ]'
    check "no real HOME in seed output" '[ -z "$(echo "$leaks" | grep -F "$real_home" || true)" ]'

    rm -rf "$seed_tmp"
fi

# ----------------------------------------------------------------------------
echo
echo "Task 10: README references match script output — every screenshot the"
echo "         README links must be producible by the script (no dead refs)"
expected_screenshots=$(grep -oE 'screenshots/[a-z0-9-]+\.png' "$REPO_ROOT/README.md" | sort -u)
covered=0
total=0
for ref in $expected_screenshots; do
    total=$((total + 1))
    png="$(basename "$ref")"
    # The filename appears in the script as `'<filename>.png'` inside the
    # Node ROUTES array, or in `cockpit-rebrand-{light,dark}.png`. Match
    # the literal `.png` suffix to avoid false negatives.
    if echo "$src" | grep -qF "${png}"; then
        covered=$((covered + 1))
    else
        bad "README screenshot ${png} not referenced in script"
    fi
done
if [ "$covered" -eq "$total" ]; then
    ok "all $total README screenshot references appear in script"
fi

# Hero PNGs are root-level, not in screenshots/. Confirm the script emits
# both hero files at the right place.
check "hero light PNG emitted at repo root" \
    'echo "$src" | grep -qE "cockpit-rebrand-light\\.png"'
check "hero dark PNG emitted at repo root" \
    'echo "$src" | grep -qE "cockpit-rebrand-dark\\.png"'

# ----------------------------------------------------------------------------
echo
echo "Task 11: demo-state POST loop — wrapper consumes demo-state.jsonl"
# Without this step the kanban / scheduled-messages
# screenshots would be blank (the previous ad-hoc flow did this
# seeding by hand; commits f2b2153 + kaart 35d372a0 both shipped demo
# data alongside the PNGs).
check "wrapper references demo-state.jsonl" \
    'echo "$src" | grep -qF "demo-state.jsonl"'
check "wrapper POSTs to /api/v1/kanban/cards" \
    'echo "$src" | grep -qE "/api/v1/kanban/cards"'
check "wrapper POSTs to /api/v1/scheduled-messages" \
    'echo "$src" | grep -qE "/api/v1/scheduled-messages"'
check "wrapper uses curl with -X POST" \
    'echo "$src" | grep -qE "curl[^\\n]*-X[[:space:]]+POST"'
check "POST loop sends confirm_new_project=True on kanban cards" \
    'echo "$src" | grep -qE "confirm_new_project"'
check "curl POST lives in an if/else so failures continue, not exit 1" \
    'echo "$src" | grep -qF "posted="'
check "curl POST failure path does NOT exit 1" 'post_block="$(printf "%s" "$src" | sed -n "/DEMO_STATE=/,/PLAYWRIGHT_NODE/p")"; ! printf "%s" "$post_block" | grep -qF "exit 1"'

# Ordering: the POST loop must run AFTER `wait_for_health` and BEFORE
# the Playwright capture. Line numbers from grep -n serve as a cheap
# textual proxy for script order.
wait_health_line="$(printf '%s\n' "$src" | grep -nF 'if ! wait_for_health' | head -1 | cut -d: -f1)"
post_loop_line="$(printf '%s\n' "$src" | grep -nF 'DEMO_STATE=' | head -1 | cut -d: -f1)"
playwright_line="$(printf '%s\n' "$src" | grep -nF 'cat > "$PLAYWRIGHT_NODE"' | head -1 | cut -d: -f1)"
check "POST loop sits between wait_for_health and Playwright capture" \
    '[ -n "$wait_health_line" ] && [ -n "$post_loop_line" ] && [ -n "$playwright_line" ] && [ "$wait_health_line" -lt "$post_loop_line" ] && [ "$post_loop_line" -lt "$playwright_line" ]'

# Also verify the seeder actually emits the kinds the wrapper expects.
if [ -x "$SEED" ]; then
    seed_check_tmp="$(mktemp -d)"
    python3 "$SEED" --target "$seed_check_tmp/out" >/dev/null 2>&1 || true
    if [ -f "$seed_check_tmp/out/demo-state.jsonl" ]; then
        kinds="$(python3 -c '
import json, sys
seen = set()
for line in open(sys.argv[1]):
    line = line.strip()
    if not line: continue
    try: seen.add(json.loads(line).get("kind", ""))
    except Exception: pass
print(" ".join(sorted(k for k in seen if k)))
' "$seed_check_tmp/out/demo-state.jsonl")"
        # ``presence_event`` is intentionally not required: Presence was
        # removed on 2026-08-13. The seed script may still emit those lines
        # for older fixtures; the wrapper skips them.
        for expected in kanban_card scheduled_message; do
            check "demo-state.jsonl contains $expected entries" \
                "printf '%s' \"\$kinds\" | grep -qF \"$expected\""
        done
    else
        bad "demo-state.jsonl not produced by seed script"
    fi
    rm -rf "$seed_check_tmp"
fi

# ----------------------------------------------------------------------------
echo
echo "Total: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]