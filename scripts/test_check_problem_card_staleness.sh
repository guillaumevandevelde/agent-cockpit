#!/usr/bin/env bash
# Test harness for scripts/check-problem-card-staleness.sh.
#
# Exercises the staleness sweeper against a synthetic fixture set: a SQLite
# kanban DB, a tempdir decisions.md, and a tmpdir git repo. The real-board
# check is a final optional task — the production kanban.db / decisions.md /
# git history may legitimately surface hits on a working repo, and we don't
# want flaky tests to depend on operator cleanup or transient board state.
#
# The sweeper cross-references three sources:
#
#   1. kanban_cards with column IN ('Backlog', 'Doing', 'Impediment'),
#      title LIKE '%[problem]%', and title NOT LIKE '%[self-improve]%'.
#   2. decisions.md table rows (Datum + Vraag + Uitkomst + Doc).
#   3. git log subjects from the repo, since the earliest card.created_at.
#
# A card is flagged as "possibly already resolved" when its title/description
# keyword set (lowercased, stopword-filtered, 3+ chars) shares ≥
# MIN_OVERLAP distinct keywords with a Vraag+Uitkomst+Doc row whose Datum
# is STRICTLY AFTER the card.created_at date, OR with a commit subject
# dated strictly after the card.created_at date. Same-day sources are
# conservatively excluded (we don't know who came first).
#
# Tasks covered:
#   1.  arg parsing — `--help` works and mentions the real flags.
#   2.  error — missing DB → exit 2.
#   3.  error — missing decisions.md → exit 2.
#   4.  error — missing repo → exit 2.
#   5.  error — unknown argument → exit 2.
#   6.  clean board — no overlap between any open [problem] card and any
#       decision/commit source → exit 0 + "OK".
#   7.  decision-overlap hit — newer decision shares keywords with card.
#   8.  commit-overlap hit — newer commit shares keywords with card.
#   9.  older decision (Datum <= card.created_at) → NOT flagged.
#  10.  older commit (date <= card.created_at) → NOT flagged.
#  11.  same-day decision (Datum == card.created_at) → NOT flagged
#       (conservative: ambiguous ordering).
#  12.  same-day commit (date == card.created_at) → NOT flagged.
#  13.  `[self-improve]` card on Backlog → excluded entirely.
#  14.  `[problem]` card on Done column → excluded entirely.
#  15.  --strict + hits → exit 1; --strict + clean → exit 0.
#  16.  empty board (zero open [problem] cards) → exit 0 + "OK".
#  17.  card with no usable keywords (just "[problem]") → never flagged
#       (no overlap possible).
#  18.  multiple decision hits on same card → each reported as own line.
#  19.  real ~/.claude-registry/kanban.db is reachable AND the real board
#       emits the clean-state OK line (not the loose "OK or WARNING"
#       tautology that an earlier shape of this task masked — see
#       self-improve card e5136a3f959d4886a7757b85e9d31f55).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$SCRIPT_DIR/check-problem-card-staleness.sh"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
# Fixture: minimal kanban DB matching the production column order so the
# sweeper's SELECT (id, title, description, created_at, column) finds what
# it expects. Only the columns the script reads are non-null.
seed_db() {
  local db="$1"
  rm -f "$db"
  python3 - "$db" <<'PY'
import sqlite3, sys
db = sys.argv[1]
con = sqlite3.connect(db)
con.executescript("""
    CREATE TABLE kanban_cards (
        id TEXT PRIMARY KEY,
        project_key TEXT,
        title TEXT,
        description TEXT,
        column TEXT,
        rank TEXT,
        priority TEXT,
        labels JSON,
        agent TEXT,
        transport TEXT,
        claimed_by TEXT,
        claimed_at DATETIME,
        created_at DATETIME,
        updated_at DATETIME,
        title_hlc TEXT,
        description_hlc TEXT,
        column_hlc TEXT,
        rank_hlc TEXT,
        claim_hlc TEXT,
        resume_session_id TEXT,
        resume_project_folder TEXT,
        scheduled_at TEXT,
        dispatch_failures INTEGER DEFAULT 0,
        analyst_agent_id TEXT,
        executor_agent_id TEXT,
        parent_card_id TEXT,
        analyst_run_id TEXT,
        depends_on TEXT,
        work_type TEXT,
        metadata TEXT,
        model TEXT,
        column_overrides TEXT
    );
""")
con.commit(); con.close()
PY
}

# Insert a card. Args: db, id, title, column, work_type, created_at, description.
card() {
  python3 - "$@" <<'PY'
import sqlite3, sys
db, cid, title, col, work_type, created, desc = sys.argv[1:8]
con = sqlite3.connect(db)
con.execute(
    """INSERT INTO kanban_cards
        (id, project_key, title, description, column, rank, priority,
         labels, agent, transport, claimed_by, claimed_at,
         created_at, updated_at,
         dispatch_failures, work_type)
       VALUES (?, 'proj', ?, ?, ?, '', NULL, 'null', NULL, NULL,
               NULL, NULL, ?, ?, 0, ?)""",
    (cid, title, desc, col, created, created, work_type),
)
con.commit(); con.close()
PY
}

# Write a synthetic decisions.md with the standard header + extra rows on
# stdin. Args: path.
write_decisions() {
  local path="$1"
  cat > "$path" <<'MD'
# Beslis-register

| Datum | Vraag | Uitkomst | Document | Kaart |
|---|---|---|---|---|
MD
  while IFS= read -r line; do
    echo "$line" >> "$path"
  done
}

# Seed a git repo at the given path. Args: repo path.
seed_repo() {
  local repo="$1"
  mkdir -p "$repo"
  # Use a stable initial branch name across git versions
  git -C "$repo" init -q -b main
  git -C "$repo" config user.email "test@example.com"
  git -C "$repo" config user.name "test"
  git -C "$repo" commit --allow-empty -q -m "chore: initial commit"
}

# Append a commit on the repo. Args: repo, date (YYYY-MM-DD), subject.
git_commit() {
  local repo="$1" date="$2" subject="$3"
  GIT_COMMITTER_DATE="$date 12:00:00 +0000" \
  GIT_AUTHOR_DATE="$date 12:00:00 +0000" \
    git -C "$repo" commit --allow-empty -q -m "$subject"
}

# Run the SUT with KANBAN_DB / DECISIONS_MD / REPO_ROOT all pointed at the
# fixture. Extra args go on the command line. Echoes stdout+stderr, captures
# exit code.
run() {
  local db="$1" dec="$2" repo="$3"; shift 3
  KANBAN_DB="$db" DECISIONS_MD="$dec" REPO_ROOT="$repo" \
    bash "$SUT" "$@" 2>&1
}

# ----------------------------------------------------------------------------
echo "Task 1: arg parsing — --help works and lists the real flags"
out=$(bash "$SUT" --help 2>&1 || true)
check "--help runs without error"           'echo "$out" | grep -qE "check-problem-card-staleness.sh"'
check "--help mentions --strict"            'echo "$out" | grep -qE "\-\-strict"'
check "--help mentions --db"                'echo "$out" | grep -qE "\-\-db"'
check "--help mentions --decisions"         'echo "$out" | grep -qE "\-\-decisions"'
check "--help mentions --repo"              'echo "$out" | grep -qE "\-\-repo"'

# ----------------------------------------------------------------------------
echo "Task 2: error path — missing DB → exit 2"
seed_repo "$TMP/repo_task2"
write_decisions "$TMP/dec.md" <<'EOF'
EOF
out=$(KANBAN_DB="$TMP/does-not-exist.db" DECISIONS_MD="$TMP/dec.md" REPO_ROOT="$TMP/repo_task2" \
      bash "$SUT" 2>&1); rc=$?
check "missing DB → exit 2"                 '[ "$rc" -eq 2 ]'
check "missing DB → ERROR mentions path"    'echo "$out" | grep -qE "ERROR.*kanban DB"'

# ----------------------------------------------------------------------------
echo "Task 3: error path — missing decisions.md → exit 2"
db3="$TMP/db3.db"; seed_db "$db3"
seed_repo "$TMP/repo_task3"
out=$(KANBAN_DB="$db3" DECISIONS_MD="$TMP/does-not-exist.md" REPO_ROOT="$TMP/repo_task3" \
      bash "$SUT" 2>&1); rc=$?
check "missing decisions.md → exit 2"       '[ "$rc" -eq 2 ]'
check "missing decisions.md → ERROR mentions path" 'echo "$out" | grep -qE "ERROR.*decisions"'

# ----------------------------------------------------------------------------
echo "Task 4: error path — missing repo → exit 2"
out=$(KANBAN_DB="$db3" DECISIONS_MD="$TMP/dec.md" REPO_ROOT="$TMP/does-not-exist-repo" \
      bash "$SUT" 2>&1); rc=$?
check "missing repo → exit 2"               '[ "$rc" -eq 2 ]'

# ----------------------------------------------------------------------------
echo "Task 5: error path — unknown argument → exit 2"
out=$(KANBAN_DB="$db3" DECISIONS_MD="$TMP/dec.md" REPO_ROOT="$TMP/repo_task3" \
      bash "$SUT" --bogus 2>&1); rc=$?
check "unknown arg → exit 2"                '[ "$rc" -eq 2 ]'
check "unknown arg → ERROR names the flag"  'echo "$out" | grep -qE "unknown argument"'

# ----------------------------------------------------------------------------
echo "Task 6: clean board — no overlap between any open [problem] card and any source"
clean_db="$TMP/clean.db"; seed_db "$clean_db"
card "$clean_db" "CLEAN001" "[problem] Provider CLI terminology broken in tests" "Backlog" \
     "bug" "2026-07-10 10:00:00" "failing test about provider cli"
write_decisions "$TMP/dec_clean.md" <<'EOF'
| 2026-07-15 | Database scaling naar Postgres? | NO-GO, blijft op SQLite. | [doc](./database-scaling-decision.md) | `aaa00000…` |
EOF
seed_repo "$TMP/repo_clean"
git_commit "$TMP/repo_clean" "2026-07-12" "feat: unrelated login flow"
out=$(run "$clean_db" "$TMP/dec_clean.md" "$TMP/repo_clean"); rc=$?
check "clean → exit 0"                      '[ "$rc" -eq 0 ]'
check "clean → prints OK"                   'echo "$out" | grep -qE "^OK:"'
check "clean → does NOT print WARNING"      '! echo "$out" | grep -qE "WARNING:"'
check "clean → does NOT name the card"      '! echo "$out" | grep -qF "CLEAN001"'
out=$(run "$clean_db" "$TMP/dec_clean.md" "$TMP/repo_clean" --strict); rc=$?
check "clean + --strict → exit 0"          '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 7: decision-overlap hit — newer decision shares keywords"
hit_db="$TMP/hit.db"; seed_db "$hit_db"
card "$hit_db" "HIT00001" "[problem] Provider CLI terminology broken in tests" "Backlog" \
     "bug" "2026-07-10 10:00:00" "rename provider to cli across smoke tests"
write_decisions "$TMP/dec_hit.md" <<'EOF'
| 2026-07-12 | Provider CLI rename shipped? | Yes, rename done everywhere. | [doc](./provider-cli-decision.md) | `bbb00000…` |
EOF
seed_repo "$TMP/repo_hit"
out=$(run "$hit_db" "$TMP/dec_hit.md" "$TMP/repo_hit"); rc=$?
check "hit-decision → exit 0 (advisory)"    '[ "$rc" -eq 0 ]'
check "hit-decision → WARNING header"       'echo "$out" | grep -qE "WARNING:"'
check "hit-decision → names the card"       'echo "$out" | grep -qF "HIT00001"'
check "hit-decision → tags source kind"     'echo "$out" | grep -qF "[decision]"'
check "hit-decision → names the doc"        'echo "$out" | grep -qF "provider-cli-decision.md"'
out=$(run "$hit_db" "$TMP/dec_hit.md" "$TMP/repo_hit" --strict); rc=$?
check "hit-decision + --strict → exit 1"    '[ "$rc" -eq 1 ]'

# ----------------------------------------------------------------------------
echo "Task 8: commit-overlap hit — newer commit shares keywords"
hit2_db="$TMP/hit2.db"; seed_db "$hit2_db"
card "$hit2_db" "HIT00002" "[problem] Provider CLI terminology broken in tests" "Backlog" \
     "bug" "2026-07-10 10:00:00" "rename provider to cli across smoke tests"
write_decisions "$TMP/dec_hit2.md" <<'EOF'
EOF
seed_repo "$TMP/repo_hit2"
git_commit "$TMP/repo_hit2" "2026-07-13" "fix: rename provider to cli in smoke tests"
out=$(run "$hit2_db" "$TMP/dec_hit2.md" "$TMP/repo_hit2"); rc=$?
check "hit-commit → exit 0 (advisory)"      '[ "$rc" -eq 0 ]'
check "hit-commit → WARNING header"         'echo "$out" | grep -qE "WARNING:"'
check "hit-commit → names the card"         'echo "$out" | grep -qF "HIT00002"'
check "hit-commit → tags source kind"       'echo "$out" | grep -qF "[commit]"'

# ----------------------------------------------------------------------------
echo "Task 9: older decision (Datum <= card.created_at) → NOT flagged"
old_db="$TMP/old.db"; seed_db "$old_db"
card "$old_db" "OLD00001" "[problem] Provider CLI terminology broken" "Backlog" \
     "bug" "2026-07-15 10:00:00" "rename provider to cli"
write_decisions "$TMP/dec_old.md" <<'EOF'
| 2026-07-10 | Provider CLI rename shipped? | Yes, rename done. | [doc](./old-decision.md) | `ccc00000…` |
EOF
seed_repo "$TMP/repo_old"
out=$(run "$old_db" "$TMP/dec_old.md" "$TMP/repo_old"); rc=$?
check "older-decision → exit 0 clean"       '[ "$rc" -eq 0 ]'
check "older-decision → prints OK"          'echo "$out" | grep -qE "^OK:"'
check "older-decision → does NOT name card" '! echo "$out" | grep -qF "OLD00001"'

# ----------------------------------------------------------------------------
echo "Task 10: older commit (date <= card.created_at) → NOT flagged"
old2_db="$TMP/old2.db"; seed_db "$old2_db"
card "$old2_db" "OLD00002" "[problem] Provider CLI rename" "Backlog" \
     "bug" "2026-07-15 10:00:00" "rename provider to cli"
write_decisions "$TMP/dec_old2.md" <<'EOF'
EOF
seed_repo "$TMP/repo_old2"
git_commit "$TMP/repo_old2" "2026-07-10" "fix: provider cli rename"  # before card
out=$(run "$old2_db" "$TMP/dec_old2.md" "$TMP/repo_old2"); rc=$?
check "older-commit → exit 0 clean"         '[ "$rc" -eq 0 ]'
check "older-commit → prints OK"            'echo "$out" | grep -qE "^OK:"'
check "older-commit → does NOT name card"   '! echo "$out" | grep -qF "OLD00002"'

# ----------------------------------------------------------------------------
echo "Task 11: same-day decision (Datum == card.created_at) → NOT flagged"
sd_db="$TMP/sd.db"; seed_db "$sd_db"
card "$sd_db" "SD000001" "[problem] Provider CLI rename" "Backlog" \
     "bug" "2026-07-15 10:00:00" "rename provider to cli"
write_decisions "$TMP/dec_sd.md" <<'EOF'
| 2026-07-15 | Provider CLI rename shipped? | Yes, rename done. | [doc](./sd-decision.md) | `ddd00000…` |
EOF
seed_repo "$TMP/repo_sd"
out=$(run "$sd_db" "$TMP/dec_sd.md" "$TMP/repo_sd"); rc=$?
check "same-day-decision → exit 0 clean"    '[ "$rc" -eq 0 ]'
check "same-day-decision → prints OK"       'echo "$out" | grep -qE "^OK:"'
check "same-day-decision → does NOT name card" '! echo "$out" | grep -qF "SD000001"'

# ----------------------------------------------------------------------------
echo "Task 12: same-day commit (date == card.created_at) → NOT flagged"
sd2_db="$TMP/sd2.db"; seed_db "$sd2_db"
card "$sd2_db" "SD000002" "[problem] Provider CLI rename" "Backlog" \
     "bug" "2026-07-15 10:00:00" "rename provider to cli"
write_decisions "$TMP/dec_sd2.md" <<'EOF'
EOF
seed_repo "$TMP/repo_sd2"
# Commit earlier in the day than the card (10:00:00); card at 10:00:00 too.
# Sweeper compares dates only → same-day → skip.
git_commit "$TMP/repo_sd2" "2026-07-15" "fix: provider cli rename earlier today"
out=$(run "$sd2_db" "$TMP/dec_sd2.md" "$TMP/repo_sd2"); rc=$?
check "same-day-commit → exit 0 clean"      '[ "$rc" -eq 0 ]'
check "same-day-commit → prints OK"         'echo "$out" | grep -qE "^OK:"'
check "same-day-commit → does NOT name card" '! echo "$out" | grep -qF "SD000002"'

# ----------------------------------------------------------------------------
echo "Task 13: [self-improve] card on Backlog → excluded entirely"
si_db="$TMP/si.db"; seed_db "$si_db"
card "$si_db" "SELF0001" "[self-improve] Provider CLI rename is a hidden flag" "Backlog" \
     "" "2026-07-10 10:00:00" "rename provider to cli"
write_decisions "$TMP/dec_si.md" <<'EOF'
| 2026-07-15 | Provider CLI rename shipped? | Yes, rename done. | [doc](./si-decision.md) | `eee00000…` |
EOF
seed_repo "$TMP/repo_si"
out=$(run "$si_db" "$TMP/dec_si.md" "$TMP/repo_si"); rc=$?
check "self-improve → exit 0 clean"         '[ "$rc" -eq 0 ]'
check "self-improve → prints OK"            'echo "$out" | grep -qE "^OK:"'
check "self-improve → does NOT name card"   '! echo "$out" | grep -qF "SELF0001"'

# ----------------------------------------------------------------------------
echo "Task 14: [problem] card on Done column → excluded entirely"
done_db="$TMP/done.db"; seed_db "$done_db"
card "$done_db" "DONE0001" "[problem] Provider CLI rename" "Done" \
     "bug" "2026-07-10 10:00:00" "rename provider to cli"
write_decisions "$TMP/dec_done.md" <<'EOF'
| 2026-07-15 | Provider CLI rename shipped? | Yes, rename done. | [doc](./done-decision.md) | `fff00000…` |
EOF
seed_repo "$TMP/repo_done"
out=$(run "$done_db" "$TMP/dec_done.md" "$TMP/repo_done"); rc=$?
check "done-column → exit 0 clean"          '[ "$rc" -eq 0 ]'
check "done-column → prints OK"             'echo "$out" | grep -qE "^OK:"'
check "done-column → does NOT name card"    '! echo "$out" | grep -qF "DONE0001"'

# ----------------------------------------------------------------------------
echo "Task 15: --strict round-trip"
out=$(run "$hit_db" "$TMP/dec_hit.md" "$TMP/repo_hit" --strict); rc=$?
check "strict + decision-hit → exit 1"      '[ "$rc" -eq 1 ]'
out=$(run "$hit2_db" "$TMP/dec_hit2.md" "$TMP/repo_hit2" --strict); rc=$?
check "strict + commit-hit → exit 1"        '[ "$rc" -eq 1 ]'
out=$(run "$clean_db" "$TMP/dec_clean.md" "$TMP/repo_clean" --strict); rc=$?
check "strict + clean → exit 0"             '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 16: empty board — zero open [problem] cards → OK"
empty_db="$TMP/empty.db"; seed_db "$empty_db"
write_decisions "$TMP/dec_empty.md" <<'EOF'
| 2026-07-15 | Something | Yes. | [doc](./empty-decision.md) | `ggg00000…` |
EOF
seed_repo "$TMP/repo_empty"
out=$(run "$empty_db" "$TMP/dec_empty.md" "$TMP/repo_empty"); rc=$?
check "empty → exit 0"                      '[ "$rc" -eq 0 ]'
check "empty → prints OK"                   'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 17: card with no usable keywords → never flagged"
nokw_db="$TMP/nokw.db"; seed_db "$nokw_db"
card "$nokw_db" "NOKW0001" "[problem]" "Backlog" "bug" "2026-07-10 10:00:00" ""
write_decisions "$TMP/dec_nokw.md" <<'EOF'
| 2026-07-15 | Provider CLI rename shipped? | Yes, rename done. | [doc](./nokw-decision.md) | `hhh00000…` |
EOF
seed_repo "$TMP/repo_nokw"
git_commit "$TMP/repo_nokw" "2026-07-12" "fix: provider cli rename"
out=$(run "$nokw_db" "$TMP/dec_nokw.md" "$TMP/repo_nokw"); rc=$?
check "no-keywords → exit 0 clean"          '[ "$rc" -eq 0 ]'
check "no-keywords → prints OK"             'echo "$out" | grep -qE "^OK:"'
check "no-keywords → does NOT name card"    '! echo "$out" | grep -qF "NOKW0001"'

# ----------------------------------------------------------------------------
echo "Task 18: multiple decision hits on same card → each reported"
multi_db="$TMP/multi.db"; seed_db "$multi_db"
card "$multi_db" "MULT0001" "[problem] Provider CLI rename across smoke tests" "Backlog" \
     "bug" "2026-07-10 10:00:00" "rename provider to cli in smoke test"
write_decisions "$TMP/dec_multi.md" <<'EOF'
| 2026-07-12 | Provider CLI rename shipped? | Yes, rename done. | [doc](./multi-decision-1.md) | `iii00000…` |
| 2026-07-13 | Smoke test suite rewrite? | Yes, all updated. | [doc](./multi-decision-2.md) | `jjj00000…` |
EOF
seed_repo "$TMP/repo_multi"
out=$(run "$multi_db" "$TMP/dec_multi.md" "$TMP/repo_multi"); rc=$?
check "multi-hit → exit 0 (advisory)"       '[ "$rc" -eq 0 ]'
check "multi-hit → names the card"          'echo "$out" | grep -qF "MULT0001"'
check "multi-hit → names first doc"         'echo "$out" | grep -qF "multi-decision-1.md"'
check "multi-hit → names second doc"        'echo "$out" | grep -qF "multi-decision-2.md"'

# ----------------------------------------------------------------------------
echo "Task 19: the real ~/.claude-registry/kanban.db is reachable"
if [ -r "$HOME/.claude-registry/kanban.db" ] && [ -r "$HOME/claude-cockpit/docs/cockpit/decisions.md" ]; then
  out=$(KANBAN_DB="$HOME/.claude-registry/kanban.db" \
        DECISIONS_MD="$HOME/claude-cockpit/docs/cockpit/decisions.md" \
        REPO_ROOT="$HOME/claude-cockpit" \
        bash "$SUT" 2>&1); rc=$?
  # Real board is expected to be clean (no open [problem] cards overlap with
  # newer decision/commit signals). The card that motivated this tightening
  # (5e988e4e follow-up, e5136a3f) flagged an earlier shape of this very
  # assertion as tautological: `^OK: || WARNING:` passes in BOTH the broken
  # and the fixed state, so it never catches a regression. Tighten to the
  # exact clean-state line emitted by scripts/check-problem-card-staleness.sh
  # (SUT scripts/check-problem-card-staleness.sh:332). If this assertion
  # starts failing, either the real board has a real staleness hit (triage
  # it) or the SUT's clean-state line drifted out of sync with this grep.
  check "real board → exit 0 (advisory)"           '[ "$rc" -eq 0 ]'
  check "real board → no python traceback"         '! echo "$out" | grep -qE "Traceback"'
  check "real board → clean-state OK line"         'echo "$out" | grep -qE "^OK: no Backlog \[problem\] cards overlap"'
  check "real board → no WARNING emitted"          '! echo "$out" | grep -qE "WARNING:"'
else
  echo "  (skip — real DB or decisions.md not present)"
fi

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
