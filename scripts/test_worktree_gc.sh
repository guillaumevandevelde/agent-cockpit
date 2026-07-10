#!/usr/bin/env bash
# Test harness for scripts/worktree-gc.sh + scripts/kanban_active_worktrees.py.
#
# Verifies the behaviour that motivated the "skip active agent claims" fix:
# worktree-gc.sh must NOT remove a worktree whose branch is the only checkout
# of an active (Backlog/agent-column claimed) kanban card. The fix for the
# "worktree-gc verwijdert branch/worktree van actieve analyst-sessie" problem
# depends on this.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# Run worktree-gc.sh with all paths redirected to a temp dir.
# mode="--apply" (or anything starting with --apply) → passes the flag through
# anything else → dry-run (no flag)
run_gc() {
    local mode="$1" fake_root="$2" fake_kanban_db="$3"
    local args=()
    [ "$mode" = "--apply" ] && args=(--apply)
    WORKTREE_GC_ROOT="$fake_root" KANBAN_DB="$fake_kanban_db" \
        bash "$SCRIPT_DIR/worktree-gc.sh" "${args[@]}" 2>&1
}

# Seed a kanban DB with the schema worktree-gc.sh expects.
seed_kanban_db() {
    local db_path="$1"
    rm -f "$db_path"
    python3 - "$db_path" <<'PY'
import sqlite3, sys
db = sys.argv[1]
con = sqlite3.connect(db)
cur = con.cursor()
cur.execute("""
    CREATE TABLE kanban_cards (
        id TEXT PRIMARY KEY,
        project_key TEXT,
        title TEXT,
        column TEXT,
        claimed_by TEXT,
        claimed_at TEXT
    )
""")
con.commit()
con.close()
PY
}

# Insert a row representing a card. Args: db id column claimed_by
insert_card() {
    local db="$1" id="$2" col="$3" claim="$4"
    python3 - "$db" "$id" "$col" "$claim" <<'PY'
import sqlite3, sys
db, cid, col, claim = sys.argv[1:5]
con = sqlite3.connect(db)
con.execute(
    "INSERT INTO kanban_cards (id, project_key, title, column, claimed_by, claimed_at) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    (cid, "git-example-test", cid, col, claim, "2026-07-10T00:00:00"),
)
con.commit(); con.close()
PY
}

# Make a bare-ish git repo with one branch+worktree we control.
# Args: root_dir worktree_name branch_name
make_worktree() {
    local root="$1" wtname="$2" brname="$3"
    rm -rf "$root"
    mkdir -p "$root/.claude/worktrees"
    ( cd "$root"
      git init -q -b master
      git config user.email "t@t" && git config user.name "t"
      touch "a.txt" && git add a.txt && git commit -qm a
      # Create a feature branch identical to master and a worktree on it.
      git branch "$brname"
      git worktree add -q ".claude/worktrees/$wtname" "$brname"
    )
}

echo "Task 1: dry-run skips worktree matching an active (Backlog) claim"
T="$(mktemp -d)"
make_worktree "$T" "k-active-1" "k-active-1"
seed_kanban_db "$T/kanban.db"
insert_card "$T/kanban.db" "k-active-1" "Backlog" "agent:k-active-1"
out="$(run_gc "" "$T" "$T/kanban.db")"
check "KEEP message for active worktree" \
    'echo "$out" | grep -qE "KEEP[[:space:]]+k-active-1.*active claim"'
check "worktree directory still present" '[ -d "$T/.claude/worktrees/k-active-1" ]'
check "branch still present" 'git -C "$T" branch --list k-active-1 | grep -q k-active-1'
rm -rf "$T"

echo ""
echo "Task 2: dry-run skips worktree matching an active analyst-column claim"
T="$(mktemp -d)"
make_worktree "$T" "k-active-2" "k-active-2"
seed_kanban_db "$T/kanban.db"
insert_card "$T/kanban.db" "k-active-2" "analyst" "agent:k-active-2"
out="$(run_gc "" "$T" "$T/kanban.db")"
check "KEEP message for analyst-claimed worktree" \
    'echo "$out" | grep -qE "KEEP[[:space:]]+k-active-2.*active claim"'
check "worktree directory still present" '[ -d "$T/.claude/worktrees/k-active-2" ]'
rm -rf "$T"

echo ""
echo "Task 3: dry-run removes worktree for a card in Done (claim cleared)"
T="$(mktemp -d)"
make_worktree "$T" "k-done-1" "k-done-1"
seed_kanban_db "$T/kanban.db"
# Card is Done and has no active claim (Done cards are released).
insert_card "$T/kanban.db" "k-done-1" "Done" ""
out="$(run_gc "" "$T" "$T/kanban.db")"
check "WOULD-REMOVE for Done card" \
    'echo "$out" | grep -qE "WOULD-REMOVE.*k-done-1"'
check "worktree still present (dry-run)" '[ -d "$T/.claude/worktrees/k-done-1" ]'
rm -rf "$T"

echo ""
echo "Task 4: dry-run removes worktree for a card in Impediment (claim cleared)"
T="$(mktemp -d)"
make_worktree "$T" "k-imp-1" "k-imp-1"
seed_kanban_db "$T/kanban.db"
insert_card "$T/kanban.db" "k-imp-1" "Impediment" ""
out="$(run_gc "" "$T" "$T/kanban.db")"
check "WOULD-REMOVE for Impediment card" \
    'echo "$out" | grep -qE "WOULD-REMOVE.*k-imp-1"'
rm -rf "$T"

echo ""
echo "Task 5: --apply actually removes the worktree for a Done card"
T="$(mktemp -d)"
make_worktree "$T" "k-done-2" "k-done-2"
seed_kanban_db "$T/kanban.db"
insert_card "$T/kanban.db" "k-done-2" "Done" ""
out="$(run_gc --apply "$T" "$T/kanban.db")"
check "REMOVED message printed" \
    'echo "$out" | grep -qE "REMOVED.*k-done-2"'
check "worktree directory removed" '[ ! -d "$T/.claude/worktrees/k-done-2" ]'
check "branch removed" '! git -C "$T" branch --list k-done-2 | grep -q k-done-2'
rm -rf "$T"

echo ""
echo "Task 6: --apply leaves an active-claim worktree alone"
T="$(mktemp -d)"
make_worktree "$T" "k-active-3" "k-active-3"
seed_kanban_db "$T/kanban.db"
insert_card "$T/kanban.db" "k-active-3" "Backlog" "agent:k-active-3"
out="$(run_gc --apply "$T" "$T/kanban.db")"
check "KEEP for active claim even on --apply" \
    'echo "$out" | grep -qE "KEEP[[:space:]]+k-active-3.*active claim"'
check "worktree still present after --apply" '[ -d "$T/.claude/worktrees/k-active-3" ]'
check "branch still present after --apply" 'git -C "$T" branch --list k-active-3 | grep -q k-active-3'
rm -rf "$T"

echo ""
echo "Task 7: missing kanban DB does not crash; falls back to merge+clean logic"
T="$(mktemp -d)"
make_worktree "$T" "k-no-db" "k-no-db"
# Don't seed a kanban DB at all.
out="$(run_gc "" "$T" "/nonexistent/path/kanban.db")"
check "dry-run continues with missing DB" \
    'echo "$out" | grep -qE "WOULD-REMOVE.*k-no-db"'
rm -rf "$T"

echo ""
echo "Task 8: a non-agent claim (e.g. human label) does NOT block removal"
T="$(mktemp -d)"
make_worktree "$T" "k-human" "k-human"
seed_kanban_db "$T/kanban.db"
# claimed_by is a human label (no "agent:" prefix) — not a live session, so
# the script must still consider this worktree removable.
insert_card "$T/kanban.db" "k-human" "Backlog" "human:me"
out="$(run_gc "" "$T" "$T/kanban.db")"
check "WOULD-REMOVE for human-only claim" \
    'echo "$out" | grep -qE "WOULD-REMOVE.*k-human"'
rm -rf "$T"

echo ""
echo "Task 9: active claim on a DIFFERENT branch does not block this one"
T="$(mktemp -d)"
make_worktree "$T" "k-stale" "k-stale"
seed_kanban_db "$T/kanban.db"
# A different card with an active claim — its branch is k-other, not k-stale.
insert_card "$T/kanban.db" "k-other-card" "Backlog" "agent:k-other"
out="$(run_gc "" "$T" "$T/kanban.db")"
check "WOULD-REMOVE when claim points elsewhere" \
    'echo "$out" | grep -qE "WOULD-REMOVE.*k-stale"'
rm -rf "$T"

echo ""
echo "Total: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]