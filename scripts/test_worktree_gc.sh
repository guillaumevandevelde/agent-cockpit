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
# kanban_meta: the new worktree-lease layer (kanban card a2268cd2…). gc reads
# ``worktree_lease:<name>`` + ``worktree_owner:<name>`` rows here.
cur.execute("""
    CREATE TABLE kanban_meta (
        key TEXT PRIMARY KEY,
        value TEXT
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

# Insert a worktree lease. Args: db name owner expiry_iso
insert_lease() {
    local db="$1" name="$2" owner="$3" expiry="$4"
    python3 - "$db" "$name" "$owner" "$expiry" <<'PY'
import sqlite3, sys
db, name, owner, expiry = sys.argv[1:5]
con = sqlite3.connect(db)
con.executemany(
    "INSERT INTO kanban_meta (key, value) VALUES (?, ?)",
    [
        (f"worktree_lease:{name}", expiry),
        (f"worktree_owner:{name}", owner),
    ],
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

# Make a repo with a branch that has NO worktree — the shape the backend
# leaves behind after it removes the worktree on a Done move but keeps the
# branch. Args: root_dir branch_name [--unmerged]
make_orphan_branch_repo() {
    local root="$1" brname="$2" unmerged="${3:-}"
    rm -rf "$root"
    mkdir -p "$root/.claude/worktrees"
    ( cd "$root"
      git init -q -b master
      git config user.email "t@t" && git config user.name "t"
      touch "a.txt" && git add a.txt && git commit -qm a
      git branch "$brname"
      if [ "$unmerged" = "--unmerged" ]; then
        # One commit that master does not have — must survive gc.
        git worktree add -q ".claude/worktrees/tmp-$brname" "$brname"
        ( cd ".claude/worktrees/tmp-$brname"
          echo work > b.txt && git add b.txt && git commit -qm "unmerged work" )
        git worktree remove --force ".claude/worktrees/tmp-$brname"
      fi
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
echo "Task 10: a LIVE worktree lease blocks removal (kill -9 safety net)"
T="$(mktemp -d)"
make_worktree "$T" "k-leased" "k-leased"
seed_kanban_db "$T/kanban.db"
# No active claim — the agent process was killed before the Done move.
# The lease is the only signal that this worktree is in use.
FUTURE="$(date -u -d '+1 hour' '+%Y-%m-%dT%H:%M:%S+00:00' 2>/dev/null || \
          date -u -v+1H '+%Y-%m-%dT%H:%M:%S+00:00')"
insert_lease "$T/kanban.db" "k-leased" "dispatch:k-leased" "$FUTURE"
out="$(run_gc "" "$T" "$T/kanban.db")"
check "KEEP for live lease reason" \
    'echo "$out" | grep -qE "KEEP[[:space:]]+k-leased.*live lease"'
check "worktree still present" '[ -d "$T/.claude/worktrees/k-leased" ]'
out_apply="$(run_gc --apply "$T" "$T/kanban.db")"
check "KEEP on --apply too" \
    'echo "$out_apply" | grep -qE "KEEP[[:space:]]+k-leased.*live lease"'
check "worktree still present after --apply" \
    '[ -d "$T/.claude/worktrees/k-leased" ]'
rm -rf "$T"

echo ""
echo "Task 11: an EXPIRED lease is treated as no lease — gc reclaims the worktree"
T="$(mktemp -d)"
make_worktree "$T" "k-stale-lease" "k-stale-lease"
seed_kanban_db "$T/kanban.db"
# Subtract 1 second from the deadline so the value is unambiguously past.
PAST="$(date -u -d '-1 second' '+%Y-%m-%dT%H:%M:%S+00:00' 2>/dev/null || \
        date -u -v-1S '+%Y-%m-%dT%H:%M:%S+00:00')"
insert_lease "$T/kanban.db" "k-stale-lease" "dispatch:zombie" "$PAST"
out="$(run_gc --apply "$T" "$T/kanban.db")"
check "REMOVED on --apply for expired lease" \
    'echo "$out" | grep -qE "REMOVED.*k-stale-lease"'
check "worktree directory removed" \
    '[ ! -d "$T/.claude/worktrees/k-stale-lease" ]'
# The expired lease row should be cleared so the next gc run sees no
# stale lease pointing at a directory that no longer exists.
check "expired lease row cleared from kanban_meta" \
    '[ "$(python3 - "$T/kanban.db" "k-stale-lease" <<PY
import sqlite3, sys
db, name = sys.argv[1], sys.argv[2]
con = sqlite3.connect(db)
cur = con.execute("SELECT key FROM kanban_meta WHERE key IN (?, ?)",
                  (f"worktree_lease:{name}", f"worktree_owner:{name}"))
print(len(cur.fetchall()))
PY
)" = "0" ]'
rm -rf "$T"

echo ""
echo "Task 12: a half-written lease (expiry without owner) is skipped"
T="$(mktemp -d)"
make_worktree "$T" "k-half" "k-half"
seed_kanban_db "$T/kanban.db"
FUTURE="$(date -u -d '+1 hour' '+%Y-%m-%dT%H:%M:%S+00:00' 2>/dev/null || \
          date -u -v+1H '+%Y-%m-%dT%H:%M:%S+00:00')"
# Only the expiry row; the owner row is missing. The helper script must
# skip it so gc falls through to the standard merge+clean path.
python3 - "$T/kanban.db" "k-half" "$FUTURE" <<'PY'
import sqlite3, sys
db, name, expiry = sys.argv[1], sys.argv[2], sys.argv[3]
con = sqlite3.connect(db)
con.execute("INSERT INTO kanban_meta (key, value) VALUES (?, ?)",
            (f"worktree_lease:{name}", expiry))
con.commit(); con.close()
PY
out="$(run_gc "" "$T" "$T/kanban.db")"
check "WOULD-REMOVE for half-written lease (no owner row)" \
    'echo "$out" | grep -qE "WOULD-REMOVE.*k-half"'
rm -rf "$T"

echo ""
echo "Task 13: a malformed expiry is skipped, not crashed"
T="$(mktemp -d)"
make_worktree "$T" "k-bad-exp" "k-bad-exp"
seed_kanban_db "$T/kanban.db"
# Garbage value — the helper must skip without crashing the gc script.
python3 - "$T/kanban.db" "k-bad-exp" <<'PY'
import sqlite3, sys
db, name = sys.argv[1], sys.argv[2]
con = sqlite3.connect(db)
con.executemany(
    "INSERT INTO kanban_meta (key, value) VALUES (?, ?)",
    [
        (f"worktree_lease:{name}", "not-a-date"),
        (f"worktree_owner:{name}", "dispatch:k-bad-exp"),
    ],
)
con.commit(); con.close()
PY
out="$(run_gc "" "$T" "$T/kanban.db")"
check "WOULD-REMOVE for malformed lease (skipped, not crashed)" \
    'echo "$out" | grep -qE "WOULD-REMOVE.*k-bad-exp"'
rm -rf "$T"

echo ""
echo "Task 14: a merged branch with NO worktree is reclaimed (the orphan-branch leak)"
# The backend removes the worktree on the Done move but never the branch
# (session_cleanup._remove_worktree_at). The worktree pass below can never
# see such a branch again, so it accumulated forever — 82 dead branches on
# the live repo before this pass existed.
T="$(mktemp -d)"
make_orphan_branch_repo "$T" "k-orphan-merged"
seed_kanban_db "$T/kanban.db"
out="$(run_gc "" "$T" "$T/kanban.db")"
check "WOULD-REMOVE-BRANCH for merged worktree-less branch" \
    'echo "$out" | grep -qE "WOULD-REMOVE-BRANCH.*k-orphan-merged"'
check "branch still present (dry-run)" \
    'git -C "$T" branch --list k-orphan-merged | grep -q k-orphan-merged'
rm -rf "$T"

echo ""
echo "Task 15: --apply deletes the merged worktree-less branch"
T="$(mktemp -d)"
make_orphan_branch_repo "$T" "k-orphan-apply"
seed_kanban_db "$T/kanban.db"
out="$(run_gc --apply "$T" "$T/kanban.db")"
check "REMOVED-BRANCH message printed" \
    'echo "$out" | grep -qE "REMOVED-BRANCH.*k-orphan-apply"'
check "branch actually deleted" \
    '! git -C "$T" branch --list k-orphan-apply | grep -q k-orphan-apply'
rm -rf "$T"

echo ""
echo "Task 16: an UNMERGED worktree-less branch is kept"
T="$(mktemp -d)"
make_orphan_branch_repo "$T" "k-orphan-unmerged" --unmerged
seed_kanban_db "$T/kanban.db"
out="$(run_gc --apply "$T" "$T/kanban.db")"
check "KEEP for unmerged orphan branch" \
    'echo "$out" | grep -qE "KEEP[[:space:]]+k-orphan-unmerged.*unmerged"'
check "branch survives --apply" \
    'git -C "$T" branch --list k-orphan-unmerged | grep -q k-orphan-unmerged'
rm -rf "$T"

echo ""
echo "Task 17: an active kanban claim protects a worktree-less branch"
# A dispatched session whose worktree was removed out from under it (or a
# sandcastle/headless transport that never made one) still owns the branch.
T="$(mktemp -d)"
make_orphan_branch_repo "$T" "k-orphan-claimed"
seed_kanban_db "$T/kanban.db"
insert_card "$T/kanban.db" "k-orphan-claimed" "Backlog" "agent:k-orphan-claimed"
out="$(run_gc --apply "$T" "$T/kanban.db")"
check "KEEP for actively claimed orphan branch" \
    'echo "$out" | grep -qE "KEEP[[:space:]]+k-orphan-claimed.*active claim"'
check "branch survives --apply" \
    'git -C "$T" branch --list k-orphan-claimed | grep -q k-orphan-claimed'
rm -rf "$T"

echo ""
echo "Task 18: a live lease protects a worktree-less branch"
T="$(mktemp -d)"
make_orphan_branch_repo "$T" "k-orphan-leased"
seed_kanban_db "$T/kanban.db"
FUTURE="$(date -u -d '+1 hour' '+%Y-%m-%dT%H:%M:%S+00:00' 2>/dev/null || \
          date -u -v+1H '+%Y-%m-%dT%H:%M:%S+00:00')"
insert_lease "$T/kanban.db" "k-orphan-leased" "dispatch:k-orphan-leased" "$FUTURE"
out="$(run_gc --apply "$T" "$T/kanban.db")"
check "KEEP for leased orphan branch" \
    'echo "$out" | grep -qE "KEEP[[:space:]]+k-orphan-leased.*live lease"'
check "branch survives --apply" \
    'git -C "$T" branch --list k-orphan-leased | grep -q k-orphan-leased'
rm -rf "$T"

echo ""
echo "Task 19: master is never deleted by the orphan-branch pass"
T="$(mktemp -d)"
make_orphan_branch_repo "$T" "k-orphan-master-guard"
seed_kanban_db "$T/kanban.db"
out="$(run_gc --apply "$T" "$T/kanban.db")"
check "master not reported" '! echo "$out" | grep -qE "BRANCH[[:space:]]+master"'
check "master still present" 'git -C "$T" branch --list master | grep -q master'
rm -rf "$T"

echo ""
echo "Task 20: a branch checked out in a worktree is not double-handled"
# Pass 1 owns it; the orphan pass must skip anything with a checkout.
T="$(mktemp -d)"
make_worktree "$T" "k-has-wt" "k-has-wt"
seed_kanban_db "$T/kanban.db"
out="$(run_gc "" "$T" "$T/kanban.db")"
check "reported once by the worktree pass" \
    '[ "$(echo "$out" | grep -c "k-has-wt")" = "1" ]'
check "not reported as an orphan branch" \
    '! echo "$out" | grep -qE "BRANCH.*k-has-wt"'
rm -rf "$T"

echo ""
echo "Task 21: a DETACHED worktree still shields the branch it was made for"
# `git worktree list --porcelain` prints `detached` instead of `branch
# refs/heads/<x>` for a detached checkout, so matching on the branch line
# alone misses it and pass 2 reports the branch as an orphan — observed on
# the live repo, where a dispatched session had gone detached.
T="$(mktemp -d)"
make_worktree "$T" "k-detached" "k-detached"
( cd "$T/.claude/worktrees/k-detached" && git checkout -q --detach )
seed_kanban_db "$T/kanban.db"
out="$(run_gc "" "$T" "$T/kanban.db")"
check "branch not reported as an orphan" \
    '! echo "$out" | grep -qE "WOULD-REMOVE-BRANCH.*k-detached"'
check "reported at most once" \
    '[ "$(echo "$out" | grep -c "k-detached")" -le 1 ]'
out_apply="$(run_gc --apply "$T" "$T/kanban.db")"
check "branch survives --apply while the worktree dir exists" \
    '[ ! -d "$T/.claude/worktrees/k-detached" ] || git -C "$T" branch --list k-detached | grep -q k-detached'
rm -rf "$T"

echo ""
echo "Total: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]