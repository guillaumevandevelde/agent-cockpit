#!/usr/bin/env bash
# Test harness for scripts/po-digest-source.py.
#
# The collector is the mechanical half of the PO-digest (docs/cockpit/po-digest-design.md
# §6): it gathers the raw weekly building blocks for the four digest sections and prints
# them as JSON on stdout. The skill redacts them; the collector interprets nothing.
#
# Tests run against synthetic SQLite fixtures seeded into a tmpdir; the real-board case is
# the last task (fails open if the live DB is missing). Mirrors the "fixture-first, real
# board last" pattern from scripts/test_sweep_dangling_depends_on.sh and friends.
#
# Tasks covered (mapped to the card's acceptance criteria):
#
#   0.  The pycheck helper rejects a false bare expression; otherwise later
#       assertions could print `ok` without validating their condition.
#   1.  --help runs without error and lists the four real flags (--since, --until,
#       --project-key, --kanban-db) plus the synopsis.
#   2.  A minimal kanban DB with `**Summary:**` ops in the window and exactly
#       on the since-boundary → the JSON has `shipped` containing both card-ids,
#       titles from the create-ops, and the new-comment text.
#   3.  A card that was DELETED but still has a Summary-op in the window → the entry
#       surfaces in `shipped` with the create-op title (no kanban_cards row needed).
#   4.  Window fallback when --since is omitted → no docs/cockpit/po-digest/ then
#       since = now − 7d; the Summary-op falls inside the window iff it is < 7d old.
#   5.  Window fallback when --since is omitted AND a previous week file exists in
#       docs/cockpit/po-digest/ → since = that file's `until:` frontmatter field.
#   6.  decisions dedupe: the same `+|` line appearing in two commits in the window
#       → output contains exactly one entry for that row text.
#   7.  course_changes: a `↩︎ herzien door`-row in decisions.md in the window → surfaces
#       under `course_changes` (NOT `decisions`); a `**Outcome:** not_feasible` comment
#       op in the window → also surfaces under `course_changes`.
#   8.  waiting backend-down: the collector does NOT crash when the backend is
#       unreachable; the `waiting` key is empty and an `errors.waiting` entry says so.
#   9.  waiting backend-up: a stub that returns a minimal valid `WachtrijResponse`
#       surfaces as items[0].card_id under `waiting`.
#  10.  JSON shape invariant: top-level keys are exactly {shipped, decisions, waiting,
#       course_changes, window} regardless of empty/non-empty content.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/po-digest-source.py"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# Python assertion helper. The argument is a python expression that should
# evaluate to True; the JSON the SUT produced is fed via stdin via the
# `out` variable. Avoids the eval/escape gymnastics of the inline
# `python3 -c "..."` pattern (the inside-quote `\"` escape interacts poorly
# with multi-line python code passed through `eval`).
#
# Usage:  echo "$out" | pycheck "any(x['card_id']=='card-A' for x in d['shipped'])"
#         echo "$out" | pycheck "d['window']['since'].startswith('2026-07-20')"
#
# Implementation lives in scripts/lib/pycheck.sh (kaart 06863c73d1544b20bcc0208feb92bb50)
# so other harnesses pick up the same Assert-wrap on bare expressions — the trap
# that produced a silent `ok` on a False condition is exactly the bare-`exec`
# tautology this helper replaces. Task 0 below validates that the sourced
# helper still rejects false bare expressions.
# shellcheck source=/dev/null
. "$SCRIPT_DIR/lib/pycheck.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
# Fixture: a minimal kanban DB with the two tables the collector reads.
# `kanban_cards` row is OPTIONAL — the Summary-op fixture alone (Task 3) must
# still surface the card.
seed_db() {
  local db="$1"
  rm -f "$db"
  python3 - "$db" <<'PY'
import sqlite3, sys
db = sys.argv[1]
con = sqlite3.connect(db)
con.executescript("""
    CREATE TABLE kanban_ops (
        op_id TEXT PRIMARY KEY,
        device_id TEXT,
        seq INTEGER,
        hlc TEXT,
        project_key TEXT,
        entity_type TEXT,
        entity_id TEXT,
        op_type TEXT,
        payload TEXT,
        created_at TEXT
    );
    CREATE TABLE kanban_cards (
        id TEXT PRIMARY KEY,
        project_key TEXT,
        title TEXT,
        description TEXT,
        "column" TEXT
    );
""")
con.commit(); con.close()
PY
}

# Insert one op row. Args: db, op_id, entity_id, op_type, payload_json, created_at.
op() {
  python3 - "$@" <<'PY'
import json, sqlite3, sys
db, op_id, entity_id, op_type, payload, created_at = sys.argv[1:]
con = sqlite3.connect(db)
con.execute(
    "INSERT INTO kanban_ops (op_id, device_id, seq, hlc, project_key, "
    "entity_type, entity_id, op_type, payload, created_at) "
    "VALUES (?, 'test', 1, '2026-07-25T00:00:00Z', 'pk', 'card', ?, ?, ?, ?)",
    (op_id, entity_id, op_type, json.dumps(json.loads(payload)) if payload else "{}", created_at),
)
con.commit(); con.close()
PY
}

# Insert one kanban_cards row (optional — used to verify the deleted-card path).
card_row() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, cid, pkey, title, col = sys.argv[1:]
con = sqlite3.connect(db)
con.execute(
    'INSERT INTO kanban_cards (id, project_key, title, description, "column") '
    "VALUES (?, ?, ?, '', ?)",
    (cid, pkey, title, col),
)
con.commit(); con.close()
PY
}

# Run the SUT with --kanban-db + --project-key + window flags. Captures exit.
# Echoes stdout JSON; pass extra args through.
run_collector() {
  local db="$1"; shift
  python3 "$SUT" --kanban-db "$db" --project-key "git:github.com/guillaumevandevelde/claude-cockpit" "$@"
}

# ----------------------------------------------------------------------------
echo "Task 0: pycheck rejects false bare expressions"
out='{"ok":false}'
if echo '{"ok":false}' | pycheck 'd["ok"]' >/dev/null 2>&1; then
  bad "pycheck → false expression fails"
else
  ok "pycheck → false expression fails"
fi

# ----------------------------------------------------------------------------
echo "Task 1: --help runs and lists the real flags"
out=$(python3 "$SUT" --help 2>&1); rc=$?
check "--help exits 0"          '[ "$rc" -eq 0 ]'
check "--help shows synopsis"   'echo "$out" | grep -qE "^usage:"'
check "--help mentions --since" 'echo "$out" | grep -qE "\-\-since"'
check "--help mentions --until" 'echo "$out" | grep -qE "\-\-until"'
check "--help mentions --project-key" 'echo "$out" | grep -qE "\-\-project-key"'
check "--help mentions --kanban-db"   'echo "$out" | grep -qE "\-\-kanban-db"'

# ----------------------------------------------------------------------------
echo "Task 2: Summary-ops in the window and on the since-boundary surface under shipped"
db1="$TMP/t2.db"; seed_db "$db1"
# Match SQLAlchemy's SQLite DateTime storage: space separator, no UTC offset.
op "$db1" "op1" "card-A" "create" '{"title":"Card A title","project_key":"pk"}' "2026-07-25 10:00:00.000000"
op "$db1" "op2" "card-A" "comment" '{"text":"**Summary:** shipped A."}' "2026-07-25 11:00:00.000000"
op "$db1" "op-boundary-create" "card-boundary" "create" '{"title":"Boundary card"}' "2026-07-20 00:00:00.000000"
op "$db1" "op-boundary-summary" "card-boundary" "comment" '{"text":"**Summary:** boundary shipped."}' "2026-07-20 00:00:00.000000"
out=$(run_collector "$db1" --since 2026-07-20T00:00:00Z --until 2026-07-26T00:00:00Z 2>&1)
check "shipped → exit 0"           '[ "$?" -eq 0 ]'
check "shipped → valid JSON"       'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
check "shipped → has card-A"       'echo "$out" | pycheck "any(x[\"card_id\"]==\"card-A\" for x in d[\"shipped\"])"'
check "shipped → includes exact since-boundary" 'echo "$out" | pycheck "assert any(x[\"card_id\"]==\"card-boundary\" for x in d[\"shipped\"]), d[\"shipped\"]"'
check "shipped → title from create-op" 'echo "$out" | pycheck "next(x for x in d[\"shipped\"] if x[\"card_id\"]==\"card-A\")[\"title\"]==\"Card A title\""'
check "shipped → summary text"     'echo "$out" | pycheck "next(x for x in d[\"shipped\"] if x[\"card_id\"]==\"card-A\")[\"summary\"]==\"shipped A.\""'

# ----------------------------------------------------------------------------
echo "Task 3: a DELETED card (no kanban_cards row) still surfaces via the create-op"
db2="$TMP/t3.db"; seed_db "$db2"
# No kanban_cards row for card-B — but the create-op is in the op-log.
op "$db2" "op3" "card-B" "create" '{"title":"Card B title (deleted later)"}' "2026-07-25 10:00:00.000000"
op "$db2" "op4" "card-B" "comment" '{"text":"**Summary:** shipped B."}' "2026-07-25 11:00:00.000000"
out=$(run_collector "$db2" --since 2026-07-20T00:00:00Z --until 2026-07-26T00:00:00Z 2>&1)
check "deleted card → exit 0"        '[ "$?" -eq 0 ]'
check "deleted card → survives in shipped" 'echo "$out" | pycheck "any(x[\"card_id\"]==\"card-B\" for x in d[\"shipped\"])"'
check "deleted card → title from create-op" 'echo "$out" | pycheck "next(x for x in d[\"shipped\"] if x[\"card_id\"]==\"card-B\")[\"title\"]==\"Card B title (deleted later)\""'

# ----------------------------------------------------------------------------
echo "Task 4: window fallback when --since is omitted — no prior week file, since = now - 7d"
# An op 2 days ago must fall INSIDE the now-7d window.
db3="$TMP/t3.db"
RECENT_TS=$(python3 -c 'import datetime; print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S.%f"))')
op "$db3" "op5" "card-C" "create" '{"title":"Card C"}' "$RECENT_TS"
op "$db3" "op6" "card-C" "comment" '{"text":"**Summary:** recent."}' "$RECENT_TS"
# Empty PO_DIGEST_DIR so the fallback path is "no prior file".
out=$(PO_DIGEST_DIR="$TMP/empty" python3 "$SUT" --kanban-db "$db3" --project-key "pk" --until "$(date -u +%Y-%m-%dT%H:%M:%SZ)" 2>&1)
check "fresh window → exit 0"    '[ "$?" -eq 0 ]'
check "fresh window → card in shipped" 'echo "$out" | pycheck "any(x[\"card_id\"]==\"card-C\" for x in d[\"shipped\"])"'
check "fresh window → window.since is ~7d ago" 'echo "$out" | python3 -c "
import json,sys,datetime
d=json.loads(sys.stdin.read())
s=datetime.datetime.fromisoformat(d[\"window\"][\"since\"].replace(\"Z\",\"+00:00\"))
now=datetime.datetime.now(datetime.timezone.utc)
delta=(now-s).total_seconds()
# SUT rounds to whole seconds; allow ±60s slack.
assert abs(delta-7*86400) < 60, (d[\"window\"], delta)
"'

# ----------------------------------------------------------------------------
echo "Task 5: window fallback when --since is omitted AND a previous week file exists"
mkdir -p "$TMP/po-digest"
# Anchor the 'until' to a fixed, parseable date so the test is deterministic.
PRIOR_UNTIL="2026-07-20T00:00:00Z"
cat > "$TMP/po-digest/2026-W29.md" <<EOF
---
title: "Week 29"
until: "${PRIOR_UNTIL}"
since: "2026-07-13T00:00:00Z"
---

# Week 29 digest
EOF
# An op dated 2026-07-22 (between the prior's until and a fresh 'now') must
# fall inside the recovered window.
db4="$TMP/t5.db"; seed_db "$db4"
op "$db4" "op7" "card-D" "create" '{"title":"Card D"}' "2026-07-22 10:00:00.000000"
op "$db4" "op8" "card-D" "comment" '{"text":"**Summary:** mid-week."}' "2026-07-22 11:00:00.000000"
# And an op dated 2026-07-10 (before the prior's since) must NOT fall inside.
op "$db4" "op9" "card-E" "create" '{"title":"Card E"}' "2026-07-10 10:00:00.000000"
op "$db4" "op10" "card-E" "comment" '{"text":"**Summary:** too old."}' "2026-07-10 11:00:00.000000"
out=$(PO_DIGEST_DIR="$TMP/po-digest" python3 "$SUT" --kanban-db "$db4" --project-key "pk" --until "2026-07-26T00:00:00Z" 2>&1)
check "prior-file → exit 0"       '[ "$?" -eq 0 ]'
check "prior-file → since = prior.until" 'echo "$out" | pycheck "d[\"window\"][\"since\"].startswith(\"2026-07-20\")"'
check "prior-file → card-D in shipped" 'echo "$out" | pycheck "any(x[\"card_id\"]==\"card-D\" for x in d[\"shipped\"])"'
check "prior-file → card-E NOT in shipped" 'echo "$out" | pycheck "not any(x[\"card_id\"]==\"card-E\" for x in d[\"shipped\"])"'

# ----------------------------------------------------------------------------
echo "Task 6: decisions dedupe — same +| line in two commits → exactly one entry"
# Seed a fake git repo with a decisions.md that has a single row added, then
# cherry-picked / rebased so the same `+|` line appears in two commits. The
# collector must dedupe on row text.
REPO6="$TMP/repo6"; mkdir -p "$REPO6/docs/cockpit"
cd "$REPO6"
git init -q -b main .
git config user.email "test@example.com"
git config user.name "test"
export GIT_AUTHOR_DATE="2026-07-21T00:00:00Z"
export GIT_COMMITTER_DATE="2026-07-21T00:00:00Z"
printf '# reg\n' > docs/cockpit/decisions.md
git add docs/cockpit/decisions.md
git commit -q -m "init"
ROW='| 2026-07-25 | Same question? | **GO.** | [`d.md`](./d.md) | abc |'
export GIT_AUTHOR_DATE="2026-07-22T00:00:00Z"
export GIT_COMMITTER_DATE="2026-07-22T00:00:00Z"
printf '# reg\n\n%s\n' "$ROW" > docs/cockpit/decisions.md
git add docs/cockpit/decisions.md
git commit -q -m "decision A"
export GIT_AUTHOR_DATE="2026-07-23T00:00:00Z"
export GIT_COMMITTER_DATE="2026-07-23T00:00:00Z"
printf '# reg\n\n%s\n' "$ROW" > docs/cockpit/decisions.md
git add docs/cockpit/decisions.md
git commit -q -m "decision A again (cherry-pick)"
unset GIT_AUTHOR_DATE GIT_COMMITTER_DATE
cd "$REPO_ROOT"
WINDOW_SINCE="2026-07-20T00:00:00Z"
WINDOW_UNTIL="2026-07-26T00:00:00Z"
out=$(python3 "$SUT" --repo-root "$REPO6" --project-key "pk" --since "$WINDOW_SINCE" --until "$WINDOW_UNTIL" 2>&1)
check "decisions dedupe → exit 0" '[ "$?" -eq 0 ]'
check "decisions dedupe → exactly 1 row for our line" 'echo "$out" | pycheck "
matches=[x for x in d[\"decisions\"] if \"Same question\" in x.get(\"row\",\"\")]
assert len(matches)==1, (len(matches), d[\"decisions\"][:5])
assert \"GO\" in matches[0][\"row\"], matches[0]
"'

# ----------------------------------------------------------------------------
echo "Task 7: course_changes — reversal-marker rows + Outcome comments + reopens"
REPO7="$TMP/repo7"; mkdir -p "$REPO7/docs/cockpit"
cd "$REPO7"
git init -q -b main .
git config user.email "t@e.com"
git config user.name "t"
export GIT_AUTHOR_DATE="2026-07-21T00:00:00Z"
export GIT_COMMITTER_DATE="2026-07-21T00:00:00Z"
printf '# reg\n' > docs/cockpit/decisions.md
git add docs/cockpit/decisions.md
git commit -q -m "init"
# A row that contains the reversal marker (↩︎ herzien door) is the spec §3
# sectie-4 trigger. The literal char is encoded in the file so the regex
# inside the SUT matches the byte-for-byte text.
export GIT_AUTHOR_DATE="2026-07-22T00:00:00Z"
export GIT_COMMITTER_DATE="2026-07-22T00:00:00Z"
printf '# reg\n\n| 2026-07-25 | Old question? | **GO.** | [`old.md`](./old.md) | id1 |\n| 2026-07-22 | ↩︎ herzien door [`r.md`](./r.md) | Revisie | — | id2 |\n' > docs/cockpit/decisions.md
git add docs/cockpit/decisions.md
git commit -q -m "old decision + revisie"
unset GIT_AUTHOR_DATE GIT_COMMITTER_DATE
cd "$REPO_ROOT"
out=$(python3 "$SUT" --repo-root "$REPO7" --project-key "pk" --since 2026-07-20T00:00:00Z --until 2026-07-26T00:00:00Z 2>&1)
check "course_changes → exit 0" 'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
check "course_changes → reversal row surfaced" 'echo "$out" | pycheck "
matches=[x for x in d[\"course_changes\"] if \"herzien\" in x.get(\"row\",\"\")]
assert any(\"id2\" in m[\"row\"] for m in matches), (matches, d[\"course_changes\"])
"'
check "course_changes → reversal row NOT under decisions" 'echo "$out" | pycheck "
assert not [x for x in d[\"decisions\"] if \"herzien\" in x.get(\"row\",\"\")], d[\"decisions\"]
"'
# Outcome and reopen paths use the exact since-boundary in backend-native storage form.
db7="$TMP/t7.db"; seed_db "$db7"
op "$db7" "opoc1" "card-X" "create" '{"title":"Card X"}' "2026-07-20 00:00:00.000000"
op "$db7" "opoc2" "card-X" "comment" '{"text":"**Outcome:** not_feasible — explained."}' "2026-07-20 00:00:00.000000"
op "$db7" "opreopen" "card-R" "reopen" '{}' "2026-07-20 00:00:00.000000"
out=$(python3 "$SUT" --kanban-db "$db7" --repo-root "$REPO7" --project-key "pk" --since 2026-07-20T00:00:00Z --until 2026-07-26T00:00:00Z 2>&1)
check "course_changes → exact-boundary not_feasible Outcome surfaced" 'echo "$out" | pycheck "
matches=[x for x in d[\"course_changes\"] if x.get(\"kind\")==\"outcome_not_feasible\" and x[\"card_id\"]==\"card-X\"]
assert matches, d[\"course_changes\"]
"'
check "course_changes → exact-boundary reopen surfaced" 'echo "$out" | pycheck "
matches=[x for x in d[\"course_changes\"] if x.get(\"kind\")==\"reopen\" and x[\"card_id\"]==\"card-R\"]
assert matches, d[\"course_changes\"]
"'

# ----------------------------------------------------------------------------
echo "Task 8: waiting backend-down — does NOT crash, waiting is empty, errors.waiting reports it"
# We point the collector at a port nothing is listening on. Uses 127.0.0.1:1
# (RFC: reserved) to keep the test deterministic.
out=$(BACKEND_BASE_URL="http://127.0.0.1:1" python3 "$SUT" --project-key "pk" --since 2026-07-20T00:00:00Z --until 2026-07-26T00:00:00Z 2>&1); rc=$?
check "backend-down → exit 0"  '[ "$rc" -eq 0 ]'
check "backend-down → valid JSON" 'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
check "backend-down → waiting is empty" 'echo "$out" | pycheck "d[\"waiting\"]==[]"'
check "backend-down → errors.waiting surfaces" 'echo "$out" | pycheck "isinstance(d.get(\"errors\",{}).get(\"waiting\"),str) and d[\"errors\"][\"waiting\"]"'

# ----------------------------------------------------------------------------
echo "Task 9: waiting backend-up — stub HTTP server returns a WachtrijResponse"
# Spin up a tiny stub server on a free port that returns a known JSON.
STUB_PORT_FILE="$TMP/.port"
python3 - "$STUB_PORT_FILE" <<'PY' &
import json, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
port_file = sys.argv[1]
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = json.dumps({
            "project_key": "pk",
            "total": 1,
            "items": [{"card_id": "wacht-1", "card_title": "Wacht op 1", "card_column": "Impediment",
                       "kind": "impediment_needs_answer", "reason": "needs answer",
                       "created_at": "2026-07-25T00:00:00Z", "wait_seconds": 3600}],
        }).encode()
        self.wfile.write(body)
    def log_message(self, *args, **kwargs):
        pass  # silence the default access log on stderr
srv = HTTPServer(("127.0.0.1", 0), Handler)
with open(port_file, "w") as f:
    f.write(str(srv.server_address[1]))
srv.serve_forever()
PY
STUB_PID=$!
# Wait for the port file to materialize (≤ 5s).
PORT=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if [ -s "$STUB_PORT_FILE" ]; then PORT="$(cat "$STUB_PORT_FILE")"; break; fi
  sleep 0.5
done
if [ -n "$PORT" ] && [ "$PORT" != "0" ]; then
  out=$(BACKEND_BASE_URL="http://127.0.0.1:$PORT" python3 "$SUT" --project-key "pk" --since 2026-07-20T00:00:00Z --until 2026-07-26T00:00:00Z 2>&1); rc=$?
  check "backend-up → exit 0"  '[ "$rc" -eq 0 ]'
  check "backend-up → waiting.surfaces wacht-1" 'echo "$out" | pycheck "any(x[\"card_id\"]==\"wacht-1\" for x in d[\"waiting\"])"'
  kill "$STUB_PID" 2>/dev/null || true
  wait "$STUB_PID" 2>/dev/null || true
else
  echo "  (skip — stub server failed to start on a free port)"
fi

# ----------------------------------------------------------------------------
echo "Task 10: JSON shape invariant — top-level keys are exactly the contract"
db10="$TMP/t10.db"; seed_db "$db10"
out=$(run_collector "$db10" --since 2026-07-20T00:00:00Z --until 2026-07-26T00:00:00Z 2>&1)
check "shape → exit 0"  '[ "$?" -eq 0 ]'
check "shape → valid JSON" 'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
check "shape → top-level keys are exactly the contract" 'echo "$out" | pycheck "
assert set(d.keys())=={\"shipped\",\"decisions\",\"waiting\",\"course_changes\",\"window\"}, set(d.keys())
"'
check "shape → window has since + until" 'echo "$out" | pycheck "
assert {\"since\",\"until\"}.issubset(set(d[\"window\"].keys())), d[\"window\"]
"'
check "shape → empty sections are lists, not null" 'echo "$out" | pycheck "
for k in (\"shipped\",\"decisions\",\"waiting\",\"course_changes\"):
    assert isinstance(d[k],list), (k, d[k])
"'

# ----------------------------------------------------------------------------
echo "Task 11: fresh-worktree — equal mtimes + README.md without frontmatter picks the latest W-week"
# Reproduction of the W33 incident (kaart df54a63d…): a fresh git checkout
# stamps all files with the same mtime, so mtime-based ordering falls back
# to iterdir() order and may land on README.md (no `until:` frontmatter).
# The collector must (a) parse the YYYY-Www token out of the filename rather
# than rely on mtime, and (b) skip files whose frontmatter has no `until:`
# instead of stopping at the first candidate.
mkdir -p "$TMP/po-digest-fresh"
PRIOR_UNTIL_W32="2026-08-08T08:05:30Z"
PRIOR_UNTIL_W33="2026-08-10T06:00:49Z"
cat > "$TMP/po-digest-fresh/2026-W31.md" <<EOF
---
until: "2026-07-31T00:00:00Z"
---
# W31
EOF
cat > "$TMP/po-digest-fresh/2026-W32.md" <<EOF
---
until: "${PRIOR_UNTIL_W32}"
---
# W32
EOF
cat > "$TMP/po-digest-fresh/2026-W33.md" <<EOF
---
until: "${PRIOR_UNTIL_W33}"
---
# W33
EOF
cat > "$TMP/po-digest-fresh/README.md" <<'EOF'
# This is a README without `until:` frontmatter.
EOF
# Force identical mtime so mtime-based ordering is meaningless — exactly
# what a fresh checkout produces.
touch -d "2026-08-10T07:00:00Z" "$TMP/po-digest-fresh/2026-W31.md" \
                                "$TMP/po-digest-fresh/2026-W32.md" \
                                "$TMP/po-digest-fresh/2026-W33.md" \
                                "$TMP/po-digest-fresh/README.md"
# Confirm the precondition: all four files share mtime.
SAME_MTIME=$(stat -c '%Y' "$TMP/po-digest-fresh"/*.md | sort -u | wc -l)
check "fresh-worktree → all files share mtime" '[ "$SAME_MTIME" -eq 1 ]'
db11="$TMP/t11.db"; seed_db "$db11"
out=$(PO_DIGEST_DIR="$TMP/po-digest-fresh" python3 "$SUT" --kanban-db "$db11" --project-key "pk" --until "2026-08-11T00:00:00Z" 2>&1); rc=$?
check "fresh-worktree → exit 0" '[ "$rc" -eq 0 ]'
check "fresh-worktree → since = W33.until (NOT now-7d)" 'echo "$out" | pycheck "
assert d[\"window\"][\"since\"].startswith(\"2026-08-10T06:00:49\"), d[\"window\"][\"since\"]
"'
check "fresh-worktree → since is NOT the silent now-7d fallback" 'echo "$out" | pycheck "
import datetime
s=datetime.datetime.fromisoformat(d[\"window\"][\"since\"].replace(\"Z\",\"+00:00\"))
u=datetime.datetime.fromisoformat(d[\"window\"][\"until\"].replace(\"Z\",\"+00:00\"))
delta=(u-s).total_seconds()
assert delta < 2*86400, (delta, d[\"window\"])
"'
check "fresh-worktree → no silent fallback error message" 'echo "$out" | pycheck "
assert \"window_fallback\" not in d.get(\"errors\",{}), d.get(\"errors\")
"'

# ----------------------------------------------------------------------------
echo "Task 12: when ALL candidates lack until-frontmatter, the collector falls back to now-7d AND reports it in errors"
mkdir -p "$TMP/po-digest-empty-fm"
cat > "$TMP/po-digest-empty-fm/README.md" <<'EOF'
# readme only
EOF
cat > "$TMP/po-digest-empty-fm/2026-W32.md" <<'EOF'
---
title: no until
---
# W32 (no until key)
EOF
touch -d "2026-08-10T07:00:00Z" "$TMP/po-digest-empty-fm/2026-W32.md" \
                                "$TMP/po-digest-empty-fm/README.md"
db12="$TMP/t12.db"; seed_db "$db12"
# Use --until = test-time now so the now-7d fallback gives a ~7d delta.
NOW_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
out=$(PO_DIGEST_DIR="$TMP/po-digest-empty-fm" python3 "$SUT" --kanban-db "$db12" --project-key "pk" --until "$NOW_ISO" 2>&1); rc=$?
check "empty-fm → exit 0" '[ "$rc" -eq 0 ]'
check "empty-fm → since ≈ now - 7d" 'echo "$out" | pycheck "
import datetime
s=datetime.datetime.fromisoformat(d[\"window\"][\"since\"].replace(\"Z\",\"+00:00\"))
u=datetime.datetime.fromisoformat(d[\"window\"][\"until\"].replace(\"Z\",\"+00:00\"))
delta=(u-s).total_seconds()
assert abs(delta-7*86400) < 60, (delta, d[\"window\"])
"'
check "empty-fm → errors.window_fallback explains why" 'echo "$out" | pycheck "
err=d.get(\"errors\",{}).get(\"window_fallback\",\"\")
assert err, d.get(\"errors\")
assert \"until\" in err.lower() or \"frontmatter\" in err.lower(), err
"'

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
