#!/usr/bin/env bash
# Test harness for scripts/check-kanban-meta-security-conflicts.sh.
#
# Exercises the conflict sweeper against synthetic SQLite fixtures in a
# tempdir — one kanban.db (kanban_meta) and one claude_registry.db
# (projects + project_security_profiles). Each fixture registers a fake
# project whose git remote the python helper can resolve (it shells out
# to `git remote get-url origin` per project), so we copy a tiny git
# repo into the tempdir for each fixture project.
#
# Tasks covered:
#   1.  arg parsing — `--help` works and mentions the four real flags.
#   2.  clean case — every override agrees with its profile → exit 0
#       and "OK".
#   3.  skip_permissions=1 conflicts with product-staging profile
#       (effective skip=False) → hit, named explicitly.
#   4.  transport=worktree conflicts with product-staging profile
#       (effective transport=sandcastle) → hit, named explicitly.
#   5.  skip_permissions=0 conflicts with meta profile (effective
#       skip=True) → hit (override flips the other way).
#   6.  transport=sandcastle conflicts with meta profile (effective
#       transport=worktree) → hit.
#   7.  override for an unregistered project (no Projects row) → NOT
#       flagged as a conflict (different problem, out of scope).
#   8.  override for a registered project without a profile row → NOT
#       flagged (nothing to contradict).
#   9.  skip_permissions on product-staging profile that *agrees*
#       (skip_permissions=0 matching effective skip=False) → NOT a hit.
#  10.  transport on product-staging profile that *agrees* (transport=
#       sandcastle) → NOT a hit.
#  11.  --strict mode → exit 1 on hits; exit 0 when clean.
#  12.  error path — missing kanban DB → exit 2.
#  13.  error path — missing registry DB → exit 2.
#  14.  error path — bad flag → exit 2.
#  15.  real ~/.claude-registry/kanban.db + main registry DB are
#       reachable; the check runs against the live board and reports
#       the load-bearing overrides documented in kanban card d5642a57.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/check-kanban-meta-security-conflicts.sh"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ---
# Fixture: create a fake project at $1/proj, a tiny git repo whose
# ``git remote get-url origin`` returns ``https://github.com/test/proj``
# (matching the project's project_key after normalization: ``git:github
# .com/test/proj``).
fake_project() {
  local dir="$1"
  mkdir -p "$dir"
  ( cd "$dir" && \
    git init --quiet -b main && \
    git config user.email "fixture@test" && \
    git config user.name "fixture" && \
    touch .gitkeep && \
    git add .gitkeep && \
    git commit --quiet -m "init" && \
    git remote add origin "https://github.com/test/$(basename "$dir")" \
  ) >/dev/null 2>&1
}

# Seed both DBs (kanban + registry) for the given project. Args: db_dir
# (the per-fixture scratch dir), project_path, risk_class,
# default_skip_permissions (0/1), default_transport.
# Inserts: kanban_meta rows (NONE — caller decides), one projects row,
# one project_security_profiles row.
seed_registry() {
  local db_dir="$1"
  local proj_path="$2"
  local risk_class="$3"
  local def_skip="$4"
  local def_transport="$5"
  local reg_db="$db_dir/registry.db"
  rm -f "$reg_db"
  python3 - "$reg_db" "$proj_path" "$risk_class" "$def_skip" "$def_transport" <<'PY'
import sqlite3, sys
db, path, risk_class, def_skip, def_transport = sys.argv[1:]
con = sqlite3.connect(db)
con.executescript("""
    CREATE TABLE projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name VARCHAR NOT NULL,
        path VARCHAR NOT NULL,
        is_active BOOLEAN NOT NULL,
        last_accessed DATETIME NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        kind TEXT NOT NULL DEFAULT 'product',
        priority INTEGER
    );
    CREATE TABLE project_security_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_path VARCHAR NOT NULL,
        risk_class VARCHAR NOT NULL DEFAULT 'product-staging',
        default_transport VARCHAR NOT NULL DEFAULT 'sandcastle',
        default_skip_permissions BOOLEAN NOT NULL DEFAULT 0,
        secrets_scope_id VARCHAR,
        resource_quota JSON,
        network_policy VARCHAR NOT NULL DEFAULT 'allowlist',
        egress_allowlist JSON,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    );
""")
con.execute(
    """INSERT INTO projects
        (name, path, is_active, last_accessed, created_at, updated_at)
        VALUES (?, ?, 1, '2026-07-22 00:00:00', '2026-07-22 00:00:00', '2026-07-22 00:00:00')""",
    ("test-proj", path),
)
con.execute(
    """INSERT INTO project_security_profiles
        (project_path, risk_class, default_transport,
         default_skip_permissions, resource_quota, network_policy,
         egress_allowlist, created_at, updated_at)
        VALUES (?, ?, ?, ?, '{}', 'allowlist', '[]',
                '2026-07-22 00:00:00', '2026-07-22 00:00:00')""",
    (path, risk_class, def_transport, def_skip),
)
con.commit(); con.close()
PY
}

# Seed a kanban DB with the override rows the fixture wants. Args:
# kanban_db, then pairs of (key, value).
seed_kanban() {
  local kdb="$1"; shift
  rm -f "$kdb"
  python3 - "$kdb" "$@" <<'PY'
import sqlite3, sys
db = sys.argv[1]
pairs = sys.argv[2:]
con = sqlite3.connect(db)
con.executescript("""
    CREATE TABLE kanban_meta (
        key VARCHAR(64) PRIMARY KEY,
        value TEXT
    );
""")
for i in range(0, len(pairs), 2):
    con.execute("INSERT INTO kanban_meta VALUES (?, ?)", (pairs[i], pairs[i+1]))
con.commit(); con.close()
PY
}

# Run the SUT with both DB env vars pointed at the fixture. Echoes
# stdout+stderr, captures exit code.
run() {
  local kdb="$1" rdb="$2"; shift 2
  KANBAN_DB="$kdb" REGISTRY_DB="$rdb" bash "$SUT" "$@" 2>&1
}

# ----------------------------------------------------------------------------
echo "Task 1: arg parsing — --help works and lists the four real flags"
out=$(bash "$SUT" --help 2>&1 || true)
check "--help runs without error" 'echo "$out" | grep -qE "check-kanban-meta-security-conflicts.sh"'
check "--help mentions --strict"    'echo "$out" | grep -qE "\-\-strict"'
check "--help mentions --kanban-db" 'echo "$out" | grep -qE "\-\-kanban-db"'
check "--help mentions --registry-db" 'echo "$out" | grep -qE "\-\-registry-db"'

# ----------------------------------------------------------------------------
echo "Task 2: clean board — every override agrees with its profile"
clean_dir="$TMP/clean"; mkdir -p "$clean_dir/proj"
fake_project "$clean_dir/proj"
seed_registry "$clean_dir" "$clean_dir/proj" "product-staging" 0 "sandcastle"
# Both overrides match the product-staging defaults: skip=False, transport=sandcastle.
seed_kanban "$clean_dir/kanban.db" \
  "skip_permissions:git:github.com/test/proj" "0" \
  "transport:git:github.com/test/proj"        "sandcastle"
out=$(run "$clean_dir/kanban.db" "$clean_dir/registry.db"); rc=$?
check "clean → exit 0"               '[ "$rc" -eq 0 ]'
check "clean → prints OK"            'echo "$out" | grep -qE "^OK: every kanban_meta"'
check "clean → does NOT print WARNING" '! echo "$out" | grep -qE "WARNING:"'
out=$(run "$clean_dir/kanban.db" "$clean_dir/registry.db" --strict); rc=$?
check "clean + --strict → exit 0"    '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 3: skip_permissions=1 conflicts with product-staging profile"
sp_dir="$TMP/sp"; mkdir -p "$sp_dir/proj"
fake_project "$sp_dir/proj"
seed_registry "$sp_dir" "$sp_dir/proj" "product-staging" 0 "sandcastle"
seed_kanban "$sp_dir/kanban.db" \
  "skip_permissions:git:github.com/test/proj" "1"
out=$(run "$sp_dir/kanban.db" "$sp_dir/registry.db"); rc=$?
check "sp → exit 0 (advisory)"       '[ "$rc" -eq 0 ]'
check "sp → WARNING header"          'echo "$out" | grep -qE "WARNING:.*contradict"'
check "sp → names the project_key"   'echo "$out" | grep -qF "git:github.com/test/proj"'
check "sp → labels the kind"         'echo "$out" | grep -qE "\\[skip_permissions\\]"'
check "sp → shows override value"    'echo "$out" | grep -qF "skip_permissions=1"'
check "sp → shows profile risk_class" 'echo "$out" | grep -qF "risk_class='"'"'product-staging'"'"'"'
check "sp → shows effective skip"    'echo "$out" | grep -qF "effective skip=false"'
out=$(run "$sp_dir/kanban.db" "$sp_dir/registry.db" --strict); rc=$?
check "sp + --strict → exit 1"       '[ "$rc" -eq 1 ]'

# ----------------------------------------------------------------------------
echo "Task 4: transport=worktree conflicts with product-staging profile"
tr_dir="$TMP/tr"; mkdir -p "$tr_dir/proj"
fake_project "$tr_dir/proj"
seed_registry "$tr_dir" "$tr_dir/proj" "product-staging" 0 "sandcastle"
seed_kanban "$tr_dir/kanban.db" \
  "transport:git:github.com/test/proj" "worktree"
out=$(run "$tr_dir/kanban.db" "$tr_dir/registry.db"); rc=$?
check "tr → exit 0 (advisory)"       '[ "$rc" -eq 0 ]'
check "tr → WARNING header"          'echo "$out" | grep -qE "WARNING:"'
check "tr → names the project_key"   'echo "$out" | grep -qF "git:github.com/test/proj"'
check "tr → labels the kind"         'echo "$out" | grep -qE "\\[transport\\]"'
check "tr → shows override value"    'echo "$out" | grep -qF "transport=worktree"'
check "tr → shows effective transport" 'echo "$out" | grep -qF "effective transport=sandcastle"'
out=$(run "$tr_dir/kanban.db" "$tr_dir/registry.db" --strict); rc=$?
check "tr + --strict → exit 1"       '[ "$rc" -eq 1 ]'

# ----------------------------------------------------------------------------
echo "Task 5: skip_permissions=0 conflicts with meta profile (effective=True)"
meta_sp_dir="$TMP/meta_sp"; mkdir -p "$meta_sp_dir/proj"
fake_project "$meta_sp_dir/proj"
# meta profile: effective skip=True, effective transport=worktree.
seed_registry "$meta_sp_dir" "$meta_sp_dir/proj" "meta" 1 "worktree"
seed_kanban "$meta_sp_dir/kanban.db" \
  "skip_permissions:git:github.com/test/proj" "0"
out=$(run "$meta_sp_dir/kanban.db" "$meta_sp_dir/registry.db"); rc=$?
check "meta_sp → exit 0 (advisory)"  '[ "$rc" -eq 0 ]'
check "meta_sp → reports conflict"   'echo "$out" | grep -qE "WARNING:.*contradict"'
check "meta_sp → effective skip=true" 'echo "$out" | grep -qF "effective skip=true"'

# ----------------------------------------------------------------------------
echo "Task 6: transport=sandcastle conflicts with meta profile (effective=worktree)"
meta_tr_dir="$TMP/meta_tr"; mkdir -p "$meta_tr_dir/proj"
fake_project "$meta_tr_dir/proj"
seed_registry "$meta_tr_dir" "$meta_tr_dir/proj" "meta" 1 "worktree"
seed_kanban "$meta_tr_dir/kanban.db" \
  "transport:git:github.com/test/proj" "sandcastle"
out=$(run "$meta_tr_dir/kanban.db" "$meta_tr_dir/registry.db"); rc=$?
check "meta_tr → exit 0 (advisory)"  '[ "$rc" -eq 0 ]'
check "meta_tr → reports conflict"   'echo "$out" | grep -qE "WARNING:"'
check "meta_tr → effective transport=worktree" 'echo "$out" | grep -qF "effective transport=worktree"'

# ----------------------------------------------------------------------------
echo "Task 7: override for an unregistered project → NOT a profile conflict"
unreg_dir="$TMP/unreg"; mkdir -p "$unreg_dir"
# No fake project, no projects row. Just a kanban_meta override for a
# project_key that nothing knows about.
seed_kanban "$unreg_dir/kanban.db" \
  "skip_permissions:git:github.com/test/ghost" "1" \
  "transport:git:github.com/test/ghost"        "worktree"
# Seed an empty registry DB so the script doesn't exit 2 on missing file.
seed_registry "$unreg_dir" "/no/such/path" "product-staging" 0 "sandcastle"
out=$(run "$unreg_dir/kanban.db" "$unreg_dir/registry.db"); rc=$?
check "unreg → exit 0 (clean — no profile to contradict)" '[ "$rc" -eq 0 ]'
check "unreg → prints OK"             'echo "$out" | grep -qE "^OK:"'
check "unreg → does NOT name ghost"   '! echo "$out" | grep -qF "ghost"'

# ----------------------------------------------------------------------------
echo "Task 8: registered project WITHOUT a profile row → NOT a conflict"
noprofile_dir="$TMP/noprofile"; mkdir -p "$noprofile_dir/proj"
fake_project "$noprofile_dir/proj"
# Seed a registry DB with the project but NO profile row.
python3 - "$noprofile_dir/registry.db" "$noprofile_dir/proj" <<'PY'
import sqlite3, sys
db, path = sys.argv[1], sys.argv[2]
con = sqlite3.connect(db)
con.executescript("""
    CREATE TABLE projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name VARCHAR NOT NULL,
        path VARCHAR NOT NULL,
        is_active BOOLEAN NOT NULL,
        last_accessed DATETIME NOT NULL,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        kind TEXT NOT NULL DEFAULT 'product',
        priority INTEGER
    );
    CREATE TABLE project_security_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_path VARCHAR NOT NULL,
        risk_class VARCHAR NOT NULL DEFAULT 'product-staging',
        default_transport VARCHAR NOT NULL DEFAULT 'sandcastle',
        default_skip_permissions BOOLEAN NOT NULL DEFAULT 0,
        secrets_scope_id VARCHAR,
        resource_quota JSON,
        network_policy VARCHAR NOT NULL DEFAULT 'allowlist',
        egress_allowlist JSON,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    );
""")
con.execute(
    """INSERT INTO projects
        (name, path, is_active, last_accessed, created_at, updated_at)
        VALUES ('test-proj', ?, 1, '2026-07-22 00:00:00',
                '2026-07-22 00:00:00', '2026-07-22 00:00:00')""",
    (path,),
)
con.commit(); con.close()
PY
seed_kanban "$noprofile_dir/kanban.db" \
  "skip_permissions:git:github.com/test/proj" "1" \
  "transport:git:github.com/test/proj"        "worktree"
out=$(run "$noprofile_dir/kanban.db" "$noprofile_dir/registry.db"); rc=$?
check "noprofile → exit 0 (no profile to contradict)" '[ "$rc" -eq 0 ]'
check "noprofile → prints OK"          'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 9: agreeing skip_permissions (0) on product-staging → NOT a hit"
ok_skip_dir="$TMP/ok_skip"; mkdir -p "$ok_skip_dir/proj"
fake_project "$ok_skip_dir/proj"
seed_registry "$ok_skip_dir" "$ok_skip_dir/proj" "product-staging" 0 "sandcastle"
seed_kanban "$ok_skip_dir/kanban.db" \
  "skip_permissions:git:github.com/test/proj" "0"
out=$(run "$ok_skip_dir/kanban.db" "$ok_skip_dir/registry.db"); rc=$?
check "ok_skip → exit 0"               '[ "$rc" -eq 0 ]'
check "ok_skip → prints OK"            'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 10: agreeing transport (sandcastle) on product-staging → NOT a hit"
ok_tr_dir="$TMP/ok_tr"; mkdir -p "$ok_tr_dir/proj"
fake_project "$ok_tr_dir/proj"
seed_registry "$ok_tr_dir" "$ok_tr_dir/proj" "product-staging" 0 "sandcastle"
seed_kanban "$ok_tr_dir/kanban.db" \
  "transport:git:github.com/test/proj" "sandcastle"
out=$(run "$ok_tr_dir/kanban.db" "$ok_tr_dir/registry.db"); rc=$?
check "ok_tr → exit 0"                 '[ "$rc" -eq 0 ]'
check "ok_tr → prints OK"              'echo "$out" | grep -qE "^OK:"'

# ----------------------------------------------------------------------------
echo "Task 11: --strict mode round-trip"
out=$(run "$sp_dir/kanban.db" "$sp_dir/registry.db" --strict); rc=$?
check "strict + hits → exit 1"         '[ "$rc" -eq 1 ]'
check "strict + hits → still names the card" 'echo "$out" | grep -qF "git:github.com/test/proj"'
out=$(run "$clean_dir/kanban.db" "$clean_dir/registry.db" --strict); rc=$?
check "strict + clean → exit 0"        '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 12: error path — missing kanban DB → exit 2"
out=$(KANBAN_DB="$TMP/no-such-kanban.db" REGISTRY_DB="$clean_dir/registry.db" \
      bash "$SUT" 2>&1); rc=$?
check "missing kanban DB → exit 2"     '[ "$rc" -eq 2 ]'
check "missing kanban DB → ERROR"      'echo "$out" | grep -qE "ERROR.*kanban DB"'

# ----------------------------------------------------------------------------
echo "Task 13: error path — missing registry DB → exit 2"
out=$(KANBAN_DB="$clean_dir/kanban.db" REGISTRY_DB="$TMP/no-such-registry.db" \
      bash "$SUT" 2>&1); rc=$?
check "missing registry DB → exit 2"  '[ "$rc" -eq 2 ]'
check "missing registry DB → ERROR"   'echo "$out" | grep -qE "ERROR.*registry DB"'

# ----------------------------------------------------------------------------
echo "Task 14: error path — unknown argument → exit 2"
out=$(KANBAN_DB="$clean_dir/kanban.db" REGISTRY_DB="$clean_dir/registry.db" \
      bash "$SUT" --bogus 2>&1); rc=$?
check "unknown arg → exit 2"           '[ "$rc" -eq 2 ]'
check "unknown arg → ERROR names flag" 'echo "$out" | grep -qF "unknown argument"'

# ----------------------------------------------------------------------------
echo "Task 15: real ~/.claude-registry/kanban.db + main registry DB"
# The live board has the load-bearing overrides documented in kanban card
# d5642a57; the check should reach them via the git-common-dir fallback.
if [ -r "$HOME/.claude-registry/kanban.db" ] \
   && [ -r "/home/vdvgu/claude-cockpit/backend/claude_registry.db" ]; then
  out=$(KANBAN_DB="$HOME/.claude-registry/kanban.db" \
        REGISTRY_DB="/home/vdvgu/claude-cockpit/backend/claude_registry.db" \
        bash "$SUT" 2>&1); rc=$?
  check "real board → exit 0 (advisory)"           '[ "$rc" -eq 0 ]'
  check "real board → no python traceback"         '! echo "$out" | grep -qE "Traceback"'
  check "real board → emits WARNING with both load-bearing conflicts" '
    echo "$out" | grep -qE "WARNING: [0-9]+ kanban_meta override.*contradict" &&
    echo "$out" | grep -qE "\\[skip_permissions\\] git:github\\.com/guillaumevandevelde/claude-cockpit" &&
    echo "$out" | grep -qE "\\[transport\\] git:github\\.com/guillaumevandevelde/claude-cockpit" &&
    echo "$out" | grep -qF "skip_permissions=1" &&
    echo "$out" | grep -qF "transport=worktree" &&
    echo "$out" | grep -qF "risk_class='"'"'product-staging'"'"'"
  '
else
  echo "  (skip — live kanban.db or claude_registry.db not present)"
fi

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]