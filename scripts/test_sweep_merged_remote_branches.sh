#!/usr/bin/env bash
# Test harness for scripts/sweep_merged_remote_branches.py.
#
# Exercises the merged-remote-branch sweeper against synthetic git repos in
# tempdirs, so the tests stay green regardless of the live `origin`'s state.
# The real-origin check is a final optional task — `git ls-remote origin` and
# `git cherry origin/master origin/<b>` go over the network, and we don't want
# flaky tests to depend on operator cleanup.
#
# A branch is "fully merged" when `git cherry <base> <remote>/<branch>` emits
# ZERO `+ <sha>` lines — every commit's patch is already in <base>, so the
# branch is dead on the remote (squash-merged or fast-forwarded). The sweeper
# flags those branches; advisory by default, --strict exits 1 with hits.
#
# Tasks covered:
#   1.  --help runs and lists all real flags + the synopsis.
#   2.  error — missing repo path → exit 2 + ERROR on stderr.
#   3.  repo without <remote> (no `origin`) under --no-fetch → exit 0 with an
#       empty report (purely-local mode doesn't need a configured remote URL;
#       an empty refs/remotes/<remote>/* namespace is just "nothing to scan").
#       The fetch-enabled counterpart is tested in Task 4's note.
#   4.  clean repo (remote exists but no refs/remotes/<remote>/*) → exit 0 with
#       empty rows; master not required to exist.
#   5.  one fully-merged branch + master → exactly one row, surfaces branch.
#   6.  one unmerged branch → not reported (0 unmerged == only merged counts).
#   7.  mixed board (1 merged + 1 unmerged) → reports only merged.
#   8.  master itself is excluded from results even when listed as a remote
#       ref (it's the comparison branch, not a target).
#   9.  --base-branch override works (a non-master branch is the comparison).
#  10.  --exclude <branch> works (additional branches are skipped).
#  11.  --no-fetch skips fetch — the harness uses update-ref to avoid network,
#       but the flag must work without an actual remote configured.
#  12.  --strict with hits → exit 1; --strict clean → exit 0.
#  13.  multi-branch board (3 merged, 2 unmerged) → reports 3, totals correct.
#  14.  remote --base-branch override (e.g. `--remote upstream`) works.
#  15.  real `origin` on this repo is reachable and reports JSON when --no-fetch
#       is set so the network fetch is skipped.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUT="$SCRIPT_DIR/sweep_merged_remote_branches.py"

PASS=0; FAIL=0
ok()   { echo "  ok: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------------
# Fixture: a minimal local-only git repo on `master` with a fake `origin`
# remote. We don't configure an actual remote URL — the sweeper reads
# `refs/remotes/origin/*` (populated via update-ref, not fetch) so the test
# runs without any network. --no-fetch is the harness default.
#
# Args: root_dir
make_repo() {
  local root="$1"
  rm -rf "$root"
  mkdir -p "$root"
  (
    cd "$root"
    git init -q -b master
    git config user.email "t@t"
    git config user.name "t"
    git remote add origin "file:///dev/null"  # never fetched; ignored under --no-fetch
    # Seed master with three commits so each test branch has a non-trivial
    # base to compare against.
    : > master.txt && git add master.txt && git commit -qm "m1"
    : > m2.txt && git add m2.txt && git commit -qm "m2"
    : > m3.txt && git add m3.txt && git commit -qm "m3"
  )
}

# Create a branch from master with one extra commit. Args: root branch msg file
add_branch() {
  local root="$1" br="$2" msg="$3" file="$4"
  (
    cd "$root"
    git checkout -q master
    git checkout -q -b "$br"
    : > "$file" && echo "$msg" > "$file"
    git add "$file"
    git commit -qm "$msg"
    git rev-parse HEAD
  )
}

# Fast-forward master to include the branch's commits (the "merged" outcome).
# Args: root branch
merge_into_master() {
  local root="$1" br="$2"
  ( cd "$root" && git checkout -q master && git merge --ff-only "$br" >/dev/null 2>&1 )
}

# Mirror a local branch's tip into refs/remotes/origin/<branch> so the sweeper
# can see it without `git fetch`. Args: root branch
publish_remote() {
  local root="$1" br="$2"
  (
    cd "$root"
    local sha
    sha="$(git rev-parse "$br")"
    git update-ref "refs/remotes/origin/$br" "$sha"
  )
}

# Run the SUT pointed at the fixture repo with --no-fetch (no network).
# Echoes stdout+stderr merged; exit code captured by the caller via $?.
run() {
  local repo="$1"; shift
  python3 "$SUT" --repo "$repo" --no-fetch "$@" 2>&1
}

# ----------------------------------------------------------------------------
echo "Task 1: --help runs and lists all real flags + synopsis"
out=$(python3 "$SUT" --help 2>&1); rc=$?
check "--help runs without error"     '[ "$rc" -eq 0 ]'
check "--help shows synopsis"         'echo "$out" | grep -qE "^usage:"'
check "--help mentions --repo"        'echo "$out" | grep -qE "\-\-repo"'
check "--help mentions --remote"      'echo "$out" | grep -qE "\-\-remote"'
check "--help mentions --base-branch" 'echo "$out" | grep -qE "\-\-base-branch"'
check "--help mentions --exclude"     'echo "$out" | grep -qE "\-\-exclude"'
check "--help mentions --strict"      'echo "$out" | grep -qE "\-\-strict"'
check "--help mentions --no-fetch"    'echo "$out" | grep -qE "\-\-no-fetch"'

# ----------------------------------------------------------------------------
echo "Task 2: error — missing repo path → exit 2"
out=$(python3 "$SUT" --repo "$TMP/does-not-exist" --no-fetch 2>&1); rc=$?
check "missing repo → exit 2"          '[ "$rc" -eq 2 ]'
check "missing repo → ERROR on stderr" 'echo "$out" | grep -qE "ERROR"'

# ----------------------------------------------------------------------------
echo "Task 3: repo without <remote> under --no-fetch → empty report, exit 0"
noremote="$TMP/noremote"; rm -rf "$noremote"; mkdir -p "$noremote"
( cd "$noremote" && git init -q -b master && git config user.email "t@t" && git config user.name "t" \
  && : > a && git add a && git commit -qm a )
out=$(run "$noremote"); rc=$?
check "no remote → exit 0"             '[ "$rc" -eq 0 ]'
check "no remote → empty rows"         'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'
check "no remote → valid JSON"         'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'

# ----------------------------------------------------------------------------
echo "Task 4: clean repo (remote exists, no remote-tracking refs) → empty report"
clean="$TMP/clean"; make_repo "$clean"
out=$(run "$clean"); rc=$?
check "clean → exit 0"                 '[ "$rc" -eq 0 ]'
check "clean → valid JSON"             'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
check "clean → fully_merged == 0"      'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"fully_merged\"]==0, d[\"totals\"]"'
check "clean → rows == []"             'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'

# ----------------------------------------------------------------------------
echo "Task 5: one fully-merged branch + master → reports only the merged branch"
one="$TMP/one"; make_repo "$one"
add_branch "$one" "feat-merged" "merged commit" "merged.txt" >/dev/null
merge_into_master "$one" "feat-merged"
publish_remote "$one" "feat-merged"
publish_remote "$one" "master"
out=$(run "$one"); rc=$?
check "one merged → exit 0"            '[ "$rc" -eq 0 ]'
check "one merged → exactly 1 row"     'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, d[\"rows\"]"'
check "one merged → surfaces branch"   'echo "$out" | grep -qF "feat-merged"'
check "one merged → unmerged_commits==0" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0][\"unmerged_commits\"]==0, d[\"rows\"][0]"'
check "one merged → fully_merged == 1" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"fully_merged\"]==1, d[\"totals\"]"'

# ----------------------------------------------------------------------------
echo "Task 6: one unmerged branch → not reported"
unm="$TMP/unm"; make_repo "$unm"
add_branch "$unm" "feat-live" "live work" "live.txt" >/dev/null
publish_remote "$unm" "feat-live"
out=$(run "$unm"); rc=$?
check "unmerged → exit 0"              '[ "$rc" -eq 0 ]'
check "unmerged → 0 rows"              'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'
check "unmerged → fully_merged == 0"   'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"fully_merged\"]==0, d[\"totals\"]"'

# ----------------------------------------------------------------------------
echo "Task 7: mixed board — 1 merged + 1 unmerged → reports only the merged"
mix="$TMP/mix"; make_repo "$mix"
add_branch "$mix" "feat-merged" "merged" "m.txt" >/dev/null
merge_into_master "$mix" "feat-merged"
publish_remote "$mix" "feat-merged"
add_branch "$mix" "feat-live"   "live"    "l.txt" >/dev/null
publish_remote "$mix" "feat-live"
out=$(run "$mix"); rc=$?
check "mixed → exit 0"                 '[ "$rc" -eq 0 ]'
check "mixed → exactly 1 row"          'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, d[\"rows\"]"'
check "mixed → only merged id"         'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"][0][\"branch\"]==\"feat-merged\", d[\"rows\"][0]"'
check "mixed → live branch absent"     '! echo "$out" | grep -qF "feat-live"'

# ----------------------------------------------------------------------------
echo "Task 8: master itself is excluded even when present as a remote ref"
ms="$TMP/ms"; make_repo "$ms"
publish_remote "$ms" "master"
out=$(run "$ms"); rc=$?
check "master-ref-only → exit 0"       '[ "$rc" -eq 0 ]'
check "master-ref-only → 0 rows"       'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'

# ----------------------------------------------------------------------------
echo "Task 9: --base-branch override works"
bb="$TMP/bb"; make_repo "$bb"
# Create a separate "release" branch off master; merge feat-into-release into it.
add_branch "$bb" "feat-into-release" "into rel" "r.txt" >/dev/null
( cd "$bb" && git checkout -q -b release && git merge --ff-only feat-into-release >/dev/null 2>&1 )
publish_remote "$bb" "feat-into-release"
publish_remote "$bb" "release"
# When --base-branch=release, feat-into-release is fully merged. When
# --base-branch=master (default), it's also fully merged because of the FF
# merge into release off master. So we use a SECOND branch on a different
# line to disambiguate: feat-only-on-master is FF-merged into master only,
# not into release.
add_branch "$bb" "feat-only-on-master" "master-only" "mm.txt" >/dev/null
merge_into_master "$bb" "feat-only-on-master"
publish_remote "$bb" "feat-only-on-master"
out=$(run "$bb" --base-branch release); rc=$?
check "release base → exit 0"          '[ "$rc" -eq 0 ]'
check "release base → 1 row"           'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, d[\"rows\"]"'
check "release base → feat-into-release reported" 'echo "$out" | grep -qF "feat-into-release"'
check "release base → feat-only-on-master absent (not in release)" '! echo "$out" | grep -qF "feat-only-on-master"'

# ----------------------------------------------------------------------------
echo "Task 10: --exclude <branch> works"
ex="$TMP/ex"; make_repo "$ex"
add_branch "$ex" "feat-a" "a" "a.txt" >/dev/null
merge_into_master "$ex" "feat-a"
publish_remote "$ex" "feat-a"
add_branch "$ex" "feat-b" "b" "b.txt" >/dev/null
merge_into_master "$ex" "feat-b"
publish_remote "$ex" "feat-b"
out=$(run "$ex" --exclude feat-a); rc=$?
check "exclude → exit 0"               '[ "$rc" -eq 0 ]'
check "exclude → only feat-b reported" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1 and d[\"rows\"][0][\"branch\"]==\"feat-b\", d[\"rows\"]"'
check "exclude → feat-a in excluded list" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert \"feat-a\" in d[\"excluded\"], d[\"excluded\"]"'
check "exclude → feat-a not in rows"   'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert \"feat-a\" not in [r[\"branch\"] for r in d[\"rows\"]], d[\"rows\"]"'

# ----------------------------------------------------------------------------
echo "Task 11: --no-fetch skips fetch (no fetch errors without a real remote URL)"
nf="$TMP/nf"; make_repo "$nf"
add_branch "$nf" "feat-merged" "m" "m.txt" >/dev/null
merge_into_master "$nf" "feat-merged"
publish_remote "$nf" "feat-merged"
# We override --remote to a name that doesn't exist as an actual remote to
# prove --no-fetch bypasses the need for a configured remote URL.
out=$(python3 "$SUT" --repo "$nf" --no-fetch --remote nowhere 2>&1); rc=$?
check "no-fetch+fake-remote → exit 0"  '[ "$rc" -eq 0 ]'
check "no-fetch+fake-remote → 0 rows"  'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"rows\"]==[], d[\"rows\"]"'

# ----------------------------------------------------------------------------
echo "Task 12: --strict with hits → exit 1; clean → exit 0"
out=$(run "$one" --strict); rc=$?
check "strict + hits → exit 1"         '[ "$rc" -eq 1 ]'
out=$(run "$clean" --strict); rc=$?
check "strict + clean → exit 0"        '[ "$rc" -eq 0 ]'

# ----------------------------------------------------------------------------
echo "Task 13: multi-branch board — 3 merged, 2 unmerged → reports 3"
multi="$TMP/multi"; make_repo "$multi"
for n in m1 m2 m3; do
  add_branch "$multi" "feat-$n" "$n" "${n}.txt" >/dev/null
  merge_into_master "$multi" "feat-$n"
  publish_remote "$multi" "feat-$n"
done
for n in l1 l2; do
  add_branch "$multi" "feat-$n" "$n" "${n}.txt" >/dev/null
  publish_remote "$multi" "feat-$n"
done
out=$(run "$multi"); rc=$?
check "multi → exit 0"                 '[ "$rc" -eq 0 ]'
check "multi → exactly 3 rows"         'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==3, d[\"rows\"]"'
check "multi → fully_merged == 3"      'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"fully_merged\"]==3, d[\"totals\"]"'
check "multi → branches_scanned == 5"  'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert d[\"totals\"][\"branches_scanned\"]==5, d[\"totals\"]"'
check "multi → all merged ids present" 'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); bs={r[\"branch\"] for r in d[\"rows\"]}; assert bs=={\"feat-m1\",\"feat-m2\",\"feat-m3\"}, bs"'
check "multi → live ids absent"        '! echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); bs={r[\"branch\"] for r in d[\"rows\"]}; print(\"feat-l1\" in bs or \"feat-l2\" in bs)" | grep -q True'

# ----------------------------------------------------------------------------
echo "Task 14: --remote override works (refs/remotes/<other>/*)"
rem="$TMP/rem"; make_repo "$rem"
add_branch "$rem" "feat-up" "u" "u.txt" >/dev/null
merge_into_master "$rem" "feat-up"
# Mirror into refs/remotes/upstream/feat-up instead of origin/.
sha="$(cd "$rem" && git rev-parse feat-up)"
( cd "$rem" && git update-ref "refs/remotes/upstream/feat-up" "$sha" )
out=$(python3 "$SUT" --repo "$rem" --no-fetch --remote upstream 2>&1); rc=$?
check "remote=upstream → exit 0"       '[ "$rc" -eq 0 ]'
check "remote=upstream → 1 row"        'echo "$out" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); assert len(d[\"rows\"])==1, d[\"rows\"]"'
check "remote=upstream → surfaces branch" 'echo "$out" | grep -qF "feat-up"'

# ----------------------------------------------------------------------------
echo "Task 15: real origin on this repo — --no-fetch reports JSON without network"
# We use --no-fetch so the test doesn't pay a network round-trip; the live
# refs/remotes/origin/* cache from prior fetches is what's read.
out=$(cd "$REPO_ROOT" && python3 "$SUT" --repo "$REPO_ROOT" --no-fetch 2>&1); rc=$?
check "real origin → exit 0 (advisory or clean)" '[ "$rc" -eq 0 ] || [ "$rc" -eq 1 ]'
check "real origin → valid JSON"       'echo "$out" | python3 -c "import json,sys; json.loads(sys.stdin.read())"'
check "real origin → no python traceback" '! echo "$out" | grep -qE "Traceback"'

# ----------------------------------------------------------------------------
echo ""
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]