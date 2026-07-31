---
name: git-ship
description: 'Standardised session-end workflow: run tests, ship (merge-to-master or draft PR), attach deliverable, move card to Done. Provider-agnostic — works with Claude Code, OpenCode, Codex CLI, or any agent spawned in a git worktree.'
---

# git-ship — Standardised Session-End Workflow

Ship the work in the current worktree **safely and unattended**. Never ship red tests.
Your opening prompt states the **ship mode**: `direct` or `pull-request`. Follow the matching
path below.

This skill is the companion to `_build_ship_instructions` in
`backend/app/kanban/dispatch.py`.  The dispatch prompt inlines the same steps so
the workflow works even when the agent cannot read `.claude/skills/`.

**Session-end retro:** step 6 of both modes invokes the `session-retro` skill
(`.claude/skills/session-retro/SKILL.md`) between `attach_deliverable` and the
`move_card → Done`. This is wired only for executor/engineer sessions; analyst
sessions exit via `move_parent → Done` in `analyst_prompt.py` and are out of
scope here.

## Worktree scope — subshell-cwd rule (kaart 1181b6fa…)

When you run a Bash tool-call, the cwd **persists into the next call**. A
compound `cd backend && pytest …` therefore leaks the new cwd: the next
call lands in `backend/`, and a follow-up `cd docs/cockpit` fails with
`no such file or directory` (or runs against the wrong paths). The
same rule applies — wrap each `cd` in a subshell (the frontend-check
block above already does), or use `git -C <abs-path>` for the rest of
the recipe, so the cwd change stays scoped to that one command. See the
engineer persona's *Werkomgeving in worktree* section in
`.claude/agents/engineer.md` for the broader cwd-safety rules (writes to
the canonical checkout, `git -C` for absolute repo-root operations, the
`$HOME/.cache/cockpit-ship/ship-merge-$$` scratch-worktree location, …).

## 1. Sync

```bash
git fetch origin
```

## 2. Run frontend checks yourself before shipping (only when the branch touches `frontend/`)

There is no local pre-push gate — nothing blocks a red push. Run the frontend
checks yourself before merging/pushing — but **only when this branch actually
changed frontend code**. A docs-/backend-only branch would otherwise pay a
multi-minute `npm ci` + build for zero frontend coverage, so gate the check on
the branch diff:

```bash
git fetch origin -q
FRONTEND_TOUCHED=$( { BASE=$(git merge-base HEAD origin/master); git diff --name-only "$BASE" -- frontend/; git ls-files --others --exclude-standard -- frontend/; } | head -1 )
if [ -n "$FRONTEND_TOUCHED" ]; then
  # Fresh worktrees have no node_modules (gitignored). Fast path: when
  # frontend/package-lock.json is unchanged vs origin/master, symlink the main
  # checkout's already-installed frontend/node_modules instead of paying a
  # multi-minute `npm ci`. Fall back to `npm ci` when the lockfile diverged
  # (frontend deps changed) or main's node_modules is absent / partial.
  # Note on `<project-root>`: this skill is project-agnostic — substitute
  # the absolute path of the dispatched project's *main* checkout (the
  # tree where `master` is checked out, NOT your worktree). For the meta
  # project that's `/home/vdvgu/claude-cockpit`; for a product project it
  # is wherever that project was provisioned. The dispatcher inlines the
  # resolved path directly into your prompt (see
  # `_build_ship_instructions` in backend/app/kanban/dispatch.py, kaart
  # a962b209…), so use that string verbatim instead of guessing. If you
  # only have this skill and no prompt, run
  # `git worktree list --porcelain | head -1` to discover the main
  # checkout, or walk three levels up from your worktree.
  # Card 15cc257d… also handled the partial-install trap: an interrupted
  # `npm ci` leaves some scoped dirs but no `.bin/`, which makes `npm run
  # lint` die with `eslint: not found` and blocks a plain symlink. Move the
  # partial aside (`mv`, not `rm` — `rm` is deny-listed) before bootstrapping.
  # Note: `<project-root>` is always shell-quoted in the bash below —
  # the dispatcher uses `shlex.quote`, which wraps the path in single
  # quotes and escapes any embedded single quote. Single quotes are
  # stricter than double quotes here: a path like `/tmp/prod$1/...` or
  # `/tmp/has "quote"/...` stays literal because `sh` does no
  # variable expansion or quote interpretation inside `'…'`. Project
  # names can contain spaces (``/home/me/My Project``), shell
  # metacharacters (``$``/``&``/```/``"``), or backslashes; unquoted
  # `[ -d … ]` / `ln -s …` silently breaks on all of them
  # (kaart a962b209… blocker C).
  ( cd frontend && \
    if [ -d node_modules ] && [ ! -d node_modules/.bin ]; then \
      mv node_modules "../node_modules.partial-$(date +%s)" && \
      echo "moved partial node_modules aside (missing .bin/)"; \
    fi && \
    if [ ! -d node_modules ]; then \
      BASE=$(git merge-base HEAD origin/master) && \
      if git diff --quiet "$BASE" origin/master -- frontend/package-lock.json \
         && [ -d "<project-root>/frontend/node_modules/.bin" ]; then \
        ln -s "<project-root>/frontend/node_modules" node_modules && \
        echo "bootstrapped frontend/node_modules via symlink (lockfile matches master)"; \
      else \
        npm ci; \
      fi; \
    fi && \
    npm run lint && npm run build \
  )   # only proceed once green
else
  echo 'geen frontend-diff — gate overgeslagen'
fi
```

A branch that *does* touch `frontend/` (including a mixed frontend+docs diff)
runs the gate unconditionally; only a branch with no `frontend/` change skips
it. The worktree is a fresh `git worktree add` off origin/master, so its
`node_modules` is absent on the first run — the guarded `npm ci` installs deps
before lint/build (matching CI's `quality.yml`).

Do **not** run backend pytest locally in this repo — that step was removed
deliberately: this is a shared box, and concurrent dispatched sessions each
running the full pytest suite caused multi-minute stalls / SSH
idle-disconnects. GitHub Actions (`quality.yml`) runs ruff + pytest against
your push and is the backend gate; it also re-runs the frontend checks as a
backstop, but by then the work may already be merged — it's not a substitute
for checking the frontend yourself first.

If a frontend check fails: fix the issue, re-run, and only ship once green.
Never ship a known-red frontend check.

**Layout-chain guard (kaart 41a75826…):** raakt je diff een layout-afhankelijke
prop (`fillArea`, `flexibleHeight`, of iets anders dat erop rekent dat
`flex-1 min-h-0` van de container tot aan de widget doorloopt), mock dan **niet
de component wiens layout-keten in scope is** — een
`vi.mock("./Child", () => ({ Child: () => null }))` op precies die child maakt
elke keten-assertie vacuüm: de test blijft groen terwijl de productie-keten
halverwege breekt. Stub in plaats daarvan de *leaves* die jsdom niet aankan
(xterm's `TerminalView`, pollende hooks) en assert de className-keten hop voor
hop op de echte component; zet de bug eenmalig terug om te zien dat de test
écht faalt.

## 3. Commit your work

Make sure every change is committed to the current branch:

```bash
git add -A && git commit -m "<descriptive summary>"
```

**Schema/column-rename sweept:** als je diff een `ALTER TABLE ...
RENAME COLUMN` (of een andere model/Pydantic-schema-rename) introduceert,
draai dan `bash scripts/check-schema-rename-coverage.sh --strict` en
werk elke hit bij vóór de commit. Een gemiste referentie levert een
silent-red test op CI — net zoals kanban-kaart `ad15e08271c242238db239a90dc559d4`
documenteerde voor commit 558ca55 (de `provider` → `cli` rename shipte
met 2 latent-red tests). Het script grept `backend/app/` én
`backend/tests/` op resterende verwijzingen.

**Bron-analysedoc bijwerken (na een gefilede follow-up):** rondt je kaart een
follow-up af die in zijn beschrijving of `metadata.facet`/`metadata.parent_card`
naar een `docs/cockpit/*.md`-analyse-/designdoc verwijst, voeg dan **vóór de
commit** een korte `✅ Geïmplementeerd (kaart <id>)`-regel toe aan de paragraaf
van dat doc die de gap beschreef. Zo blijft het doc niet als "niets
geïmplementeerd, alleen analyse + gefilede gaten" staan terwijl zijn eigen
follow-ups al gemerged zijn (geobserveerd op de vier facet-docs van
synthese-kaart `c980a926…`: 33 van 35 follow-ups waren al gemerged terwijl 2
van de 4 docs zich nog als pure analyse presenteerden). **De bron is
`metadata["spec_doc"]`** — als de kaart-context boven aan deze prompt een
regel `**Brondoc (spec_doc):** …` toont, is dat het docpad dat je moet
bijwerken. Geen `spec_doc`-regel in de prompt én geen analysedoc-verwijzing
in beschrijving/facet/parent_card? Sla deze stap over. **Geen retroactieve
verplichting** — alleen het doc dat jouw kaart raakt; raakt je kaart geen
analysedoc, sla je deze stap over.

## 4a. Ship mode `direct` — merge to master

Only when every test passed. You are in a linked worktree while `master` is
checked out in the main working copy, so checking out `master` here fails with
`'master' is already used by worktree at ...`. Merge through a throwaway
detached worktree instead — it never touches your current checkout:

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
# Pre-flight: the detached worktree only sees COMMITTED state. Uncommitted changes
# to TRACKED files merge as a silent no-op ("Everything up-to-date"), pushing
# nothing — abort so you commit them (step 3) first.
#
# Tracked-only on purpose. `git ls-files --others --exclude-standard` used to be
# part of this condition and blocked ships on untracked files belonging to OTHER
# concurrent sessions sharing this worktree mount (544 files in a foreign
# `.tmp-measure-token-saver/` harness dir, kanban card c28e576d…). Those files
# can't cause the silent no-op this guard exists for — the merge never reads
# them — and since `rm` is deny-listed the only recovery was `mv`-ing another
# session's work aside. `git status --porcelain | grep -v '^??'` keeps every
# tracked state (` M`, `M `, `MM`, `A `, `D `) so a `git add` without a
# `git commit` is still refused, and drops only the `??` untracked lines.
#
# The trailing `--` is load-bearing: it separates revisions from paths. Without
# it, a file named `HEAD` anywhere in the repo root makes the argument
# ambiguous and git exits 128 with `fatal: ambiguous argument 'HEAD': both
# revision and filename` — which, under `if ! ...`, reads as "tree is dirty"
# and aborts EVERY ship with a bogus uncommitted-changes error (kanban card
# 7dd8a3dd…). `--` costs nothing and makes the guard immune to that class.
if ! git diff --quiet HEAD -- || [ -n "$(git status --porcelain | grep -v '^??')" ]; then
  echo 'ERROR: uncommitted changes to tracked files — git add + git commit first, then re-run.' >&2; exit 1
fi
# Untracked files are advisory, never fatal: a brand-new file you forgot to
# `git add` would ship as a silent omission, so list them — but do NOT exit,
# because most untracked noise here belongs to a concurrent session.
UNTRACKED=$(git ls-files --others --exclude-standard)
if [ -n "$UNTRACKED" ]; then
  echo 'NOTE: untracked files present (not blocking the ship). If any of these are YOURS and belong in this card, git add + git commit them now:' >&2
  printf '%s\n' "$UNTRACKED" | head -20 >&2
fi
# Throwaway worktree location. Two constraints, and they pull in opposite
# directions:
#
#   1. NOT under `mktemp -d` / `/tmp`. The Bash tool's harness can reap `/tmp`
#      between calls, so a /tmp-resident worktree may vanish mid-ship: the
#      merge commit lands in a now-missing checkout, the subsequent
#      `git push` fails with a spurious non-fast-forward, and the local merge
#      is lost. (kanban card 01aa1ef5…)
#   2. NOT under `.git/worktrees/` either. That was the previous fix for (1),
#      and it is actively harmful: `.git/worktrees/<name>/` is ALSO where git
#      keeps its own admin files (HEAD, index, MERGE_*, commondir, gitdir) for
#      that very worktree. Placing the CHECKOUT at the same path means the two
#      overlap — so `git -C "$WT" add -A` in the conflict carve-out staged
#      git's own admin files, and one ship through that branch committed ten
#      of them to the repo root. From then on every `git worktree add` checked
#      the tracked copies out over git's live admin files
#      ("fatal: .../index: index file smaller than expected") and no card
#      could ship at all. (kanban card 7dd8a3dd…)
#
# `$HOME/.cache/` satisfies both: persistent across Bash calls (not reaped),
# and outside every git working tree and gitdir. Note git still registers the
# admin slot under `.git/worktrees/ship-merge-$$` — that is correct and
# harmless; only the CHECKOUT must live elsewhere.
SHIP_TMP="${HOME}/.cache/cockpit-ship"
mkdir -p "$SHIP_TMP"
WT="$SHIP_TMP/ship-merge-$$"
# Main-checkout path discovery (kanban card 5e83b6e0…, third iteration).
# The ship-worktree is a detached checkout that cannot update `master`
# itself — only the canonical checkout where `master` is checked out can
# do that. `git rev-parse --git-common-dir` returns the SHARED gitdir
# (e.g. `/…/main-checkout/.git`), and `dirname` strips `.git` to give
# the main-checkout path. This is robust regardless of where the
# dispatched worktree sits on the filesystem and doesn't require the
# dispatcher to inline a project_root (the skill must be
# self-discovering when an agent reads it without the dispatch prompt).
MAIN_CHECKOUT="$(dirname "$(git rev-parse --git-common-dir)")"
# Slot name MUST be unique per session: git derives the `.git/worktrees/<name>`
# entry from the path's basename, so a fixed name (e.g. `ship-merge`) collides
# under concurrent dispatched sessions — both target the same gitdir slot, and
# a stale HEAD (or a half-pruned gitdir from a crashed predecessor) leaks
# into the fresh session's merge push, producing a spurious non-fast-forward
# rejection against origin/master. `$$` (this process's PID) guarantees a
# fresh slot per invocation — do NOT simplify back to a fixed name.
# (kanban card c23dfe46…)
# Local-master divergence guard (kanban card 5e83b6e0…). Step 1 already
# fetched origin, but to be defensive we fetch again here — this worktree
# may have been running between step 1 and step 4, and a concurrent
# session could have pushed to origin in that window. The throwaway
# worktree MUST base on LOCAL `master` (the integration point, not the
# last-pushed remote state) — otherwise a concurrent session's
# not-yet-pushed commits would be stranded when we push to origin. The
# negation of `--is-ancestor origin/master master` catches both "origin
# ahead" (origin has commits local doesn't) and the rarer "diverged"
# case (both sides have new commits); in either state, a push from local
# `master` would be rejected as non-fast-forward. Fail-fast with
# `report_impediment` and a clear remediation rather than producing a
# stale merge or a useless "Everything up-to-date" push.
git fetch origin -q
if ! git merge-base --is-ancestor origin/master master 2>/dev/null; then
  # Label semantics (kanban card 5e83b6e0…, second iteration): in
  # `git rev-list --count A..B`, A..B enumerates commits reachable from B
  # but NOT from A — i.e. commits B has that A doesn't. So
  # `master..origin/master` = commits `origin/master` has that local
  # `master` doesn't = how far local is BEHIND; and
  # `origin/master..master` = the symmetric AHEAD count. The previous
  # wiring had the two swapped, which printed `ahead=2 behind=0` while
  # local master was actually 2 BEHIND origin. Don't swap them back.
  BEHIND=$(git rev-list --count master..origin/master 2>/dev/null || echo "?")
  AHEAD=$(git rev-list --count origin/master..master 2>/dev/null || echo "?")
  echo "ERROR: local master is STALE — origin/master has commits local doesn't have." >&2
  echo "  ahead=$AHEAD behind=$BEHIND (master vs origin/master)" >&2
  echo "  Reconcile: git -C <main-checkout> pull --rebase origin master" >&2
  echo "  Then re-run the ship from this worktree. report_impediment." >&2
  exit 1
fi
git worktree add --detach "$WT" master
# 0-byte-index guard. A predecessor that aborted mid-ship in the shared
# gitdir can leave this slot's `index` truncated to 0 bytes, and
# `git worktree add` reports success anyway — the corruption only surfaces
# on the next command, as `fatal: …/index: index file smaller than
# expected`. Worse, `git worktree remove --force` then refuses with
# `is not a working tree`, so the slot is orphaned and the ship needs a
# manual rescue (kanban card 608e2a27…). The checkout already holds the
# right tree; only the index needs rebuilding, which `read-tree HEAD` does
# from the slot's own HEAD. Detect and repair here, BEFORE the merge, so
# the recovery is automatic instead of ~4 manual tool calls.
WT_GITDIR=$(git -C "$WT" rev-parse --absolute-git-dir)
if [ ! -s "$WT_GITDIR/index" ]; then
  echo "WARN: 0-byte index in $WT_GITDIR — rebuilding from HEAD (aborted predecessor in the shared gitdir)." >&2
  if ! git -C "$WT" read-tree HEAD; then
    echo "ERROR: read-tree HEAD failed — slot $WT is unusable; report_impediment." >&2
    exit 1
  fi
fi
if ! git -C "$WT" merge --no-ff "$BRANCH" -m "Merge $BRANCH"; then
  # CONFLICT path: try the generated-doc-index carve-out, otherwise
  # report_impediment. The condition MUST be machine-checkable — a
  # handwritten conflict always falls through (kanban card efb8187b…).
  # A *non-empty subset* of {docs/cockpit/README.md, docs/cockpit/llms.txt}
  # also passes (kanban card 72db7429…): both files are regenerated from
  # frontmatter anyway, so a conflict in only one of the two is the same
  # class as both — `comm -23` over the expected set surfaces any path
  # that ISN'T a generated file (the actual exclusion predicate).
  CONFLICTED=$(git -C "$WT" diff --name-only --diff-filter=U | LC_ALL=C sort -u)
  EXPECTED=$(printf 'docs/cockpit/README.md\ndocs/cockpit/llms.txt\n' | LC_ALL=C sort -u)
  NON_GENERATED=$(comm -23 <(printf '%s\n' "$CONFLICTED") <(printf '%s\n' "$EXPECTED"))
  if [ -n "$CONFLICTED" ] && [ -z "$NON_GENERATED" ]; then
    # Subset predicate passed. README.md is *partially* generated — only
    # the block between `<!-- BEGIN GENERATED DOC INDEX -->` and
    # `<!-- END GENERATED DOC INDEX -->` is owned by the regenerate
    # script; the surrounding hand-curated prose (feature→canonical-doc
    # mapping, "Regels", etc.) must NOT be silently clobbered. So if
    # README.md is in the conflict set, verify every conflict hunk sits
    # between the markers — anything outside falls through (kanban card
    # 72db7429…). The check runs BEFORE the `checkout --theirs` below
    # clears the merge markers; once cleared, the hunks are gone and the
    # check has nothing to look at. The structural invariant in
    # `backend/tests/test_ship_recipe_drift.py::test_readme_marker_check_sits_between_enumeration_and_open`
    # pins this order.
    if printf '%s\n' "$CONFLICTED" | grep -qx 'docs/cockpit/README.md'; then
      README_FILE="$WT/docs/cockpit/README.md"
      BEGIN_LINE=$(grep -nF '<!-- BEGIN GENERATED DOC INDEX' "$README_FILE" 2>/dev/null | head -1 | cut -d: -f1)
      END_LINE=$(grep -nF '<!-- END GENERATED DOC INDEX -->' "$README_FILE" 2>/dev/null | head -1 | cut -d: -f1)
      if [ -z "$BEGIN_LINE" ] || [ -z "$END_LINE" ]; then
        echo "ERROR: docs/cockpit/README.md missing BEGIN/END GENERATED DOC INDEX markers — falling back to report_impediment." >&2
        printf '  conflicted: %s\n' $CONFLICTED >&2
        echo "Conflicted worktree left at $WT for inspection (not removed)." >&2
        exit 1
      fi
      CONFLICT_LINES=$(grep -nE '^(<<<<<<< |=======$|>>>>>>> )' "$README_FILE" 2>/dev/null || true)
      if [ -n "$CONFLICT_LINES" ]; then
        OUTSIDE=$(awk -F: -v b="$BEGIN_LINE" -v e="$END_LINE" '$1 < b || $1 > e { print }' <<< "$CONFLICT_LINES")
        if [ -n "$OUTSIDE" ]; then
          echo "ERROR: docs/cockpit/README.md has conflict hunks outside the generated block — falling back to report_impediment." >&2
          printf '  offending lines:\n%s\n' "$OUTSIDE" >&2
          echo "Conflicted worktree left at $WT for inspection (not removed)." >&2
          exit 1
        fi
      fi
    fi
  else
    echo "ERROR: merge conflict in non-generated files (or empty conflict set) — falling back to report_impediment." >&2
    printf '  conflicted: %s\n' $CONFLICTED >&2
    echo "Conflicted worktree left at $WT for inspection (not removed)." >&2
    exit 1
  fi
  # Carve-out: at least one of the two generated doc-index files is
  # conflicted. `--theirs` clears the merge markers; the next regenerate
  # step overwrites both files anyway, so `--theirs` vs `--ours` is moot
  # in practice. The script MUST be invoked through the worktree path —
  # `scripts/generate-doc-index.py:78` derives its repo-root from
  # `Path(__file__).resolve().parent.parent`, so `./scripts/generate-doc-index.py`
  # would regenerate the calling shell's tree, not $WT.
  git -C "$WT" checkout --theirs -- docs/cockpit/README.md docs/cockpit/llms.txt
  "$WT"/scripts/generate-doc-index.py
  if ! "$WT"/scripts/generate-doc-index.py --check --strict; then
    echo "ERROR: generate-doc-index.py --check --strict failed after regenerate." >&2
    exit 1
  fi
  # Stage the two generated files BY NAME, never `add -A`. A blind `add -A`
  # stages everything under the worktree root, which is how ten of git's own
  # admin files (HEAD, index, MERGE_*, …) got committed to the repo root and
  # broke every subsequent ship (kanban card 7dd8a3dd…). Moving the worktree
  # out of `.git/` already removes that specific exposure; an explicit path
  # list closes the class — the carve-out is only ever entitled to commit the
  # files it just regenerated, so it should only ever be able to stage those.
  git -C "$WT" add -- docs/cockpit/README.md docs/cockpit/llms.txt
  git -C "$WT" commit --no-edit
fi
if git -C "$WT" push origin HEAD:master; then
  # Post-push local-master sync (kanban card 5e83b6e0…, third iteration).
  # The divergence guard above bases on local `master`, so a successful
  # push that doesn't also move local `master` leaves the guard tripped
  # on every subsequent ship on this multi-session box — even though
  # the divergence is fully explained by *our own* push. The cleanest
  # way to sync the main checkout is `git -C "$MAIN_CHECKOUT" pull
  # --ff-only origin master`, which in one step (a) fast-forwards the
  # local master ref and (b) updates the index AND working tree in the
  # main checkout — so the dev-stack (`cockpit.sh`) keeps running
  # against the latest tree. The throwaway `$WT` is detached HEAD and
  # cannot update master itself, which is why the sync runs against
  # `$MAIN_CHECKOUT` where master is actually checked out.
  #
  # `git pull --ff-only` REFUSES if the main checkout's working tree
  # has changes that would be overwritten by the merge (e.g. a
  # concurrent agent editing a file the merge also touches) — that
  # is the right default, we do not want to clobber in-flight edits.
  # In that case we fall back to `git update-ref refs/heads/master
  # origin/master`, which updates only the ref and at least keeps
  # the divergence guard from tripping on the next ship. The main
  # checkout's working tree stays on the user's conflicting edits
  # (they are preserved; the merged files are not on disk yet) until
  # someone resolves the conflict and runs `git pull --ff-only` by
  # hand — but the push still landed, that's what matters.
  # Fail-open in both cases: a successful push must NEVER be reverted
  # by a local-sync error.
  #
  # Why `update-ref` here and not `git fetch origin master:master`?
  # The fetch refspec `master:master` is exactly what we need, but
  # git REFUSES to update a ref that's currently checked out in
  # another worktree ("refusing to fetch into branch 'refs/heads/
  # master' checked out at …"). `update-ref` writes the ref
  # directly and bypasses that check — at the cost of leaving the
  # working tree stale. That's the trade-off we accept in the
  # fallback: the TYPICAL case is a clean main checkout, where
  # `pull --ff-only` keeps it fully current; the EDGE case (a
  # concurrent agent editing a file the merge also touches) gets
  # a ref-only update, the working tree stays on the user's edits,
  # and the next person to resolve the conflict runs
  # `git pull --ff-only` themselves. `update-ref` was rejected as
  # the SECOND-iteration PRIMARY path because it left a clean main
  # checkout stale (the 74-staged-deletions bug, observed in the
  # impediment on this card); here it's a deliberate fallback that
  # ONLY fires when the working tree is already in a state we can't
  # safely overwrite.
  if ! git -C "$MAIN_CHECKOUT" pull --ff-only origin master 2>/dev/null; then
    if ! git -C "$MAIN_CHECKOUT" update-ref refs/heads/master origin/master 2>/dev/null; then
      echo "WARN: kon lokale master in hoofd-checkout niet bijwerken naar origin/master — volgende ship kan op de divergentie-guard lopen, herstel handmatig met 'git -C \"$MAIN_CHECKOUT\" pull --ff-only origin master' (en los eventuele conflicten op die de working tree vuil houden)." >&2
    fi
  fi
  # Merge landed on master — delete the now-dead remote branch. GitHub's
  # `delete_branch_on_merge` (enabled 2026-07-07) only fires when a *PR*
  # merges; this route closes no PR, so without this line every shipped card
  # leaves a branch on `origin` forever (kanban card 3027671c…: 7 fully-merged
  # branches piled up over 6 weeks). Fail-open — an already-deleted branch
  # must not kill the ship. Only the REMOTE branch goes; the local branch
  # stays, so redispatch/resume off it still works.
  git push origin --delete "$BRANCH" || echo "WARN: kon origin/$BRANCH niet verwijderen (al weg?)"
else
  # Push rejected (master moved / protected). Keep `origin/$BRANCH` alive —
  # the pull-request fallback below needs it. Deleting here would strand the
  # work on exactly the path where the branch is still required.
  echo "WARN: push naar master afgewezen — origin/$BRANCH bewaard voor de pull-request-fallback." >&2
fi
git worktree remove --force "$WT"
```

Then `attach_deliverable` (kind `branch`, ref=`<your-branch-name>`), **run the session-end
retro** (invoke the `session-retro` skill — read
`.claude/skills/session-retro/SKILL.md` for the full procedure: reflect → dedupe → file
0–N `[self-improve]` cards → `comment` on this host card), and finally `move_card` to
`Done` with a `summary` of the work you did (required — the move is rejected without it).
**Product-taal** (conventie §5 van `docs/cockpit/kanban-conventions.md`, kaart
`4358fe0a…`): leid met één zin *productbetekenis* (wat kan de product owner nu doen /
zien / beslissen dat voorheen niet kon), zet de engineering-detail (bestanden, endpoints,
tests) erna. Een kale engineering-summary voldoet aan de gate maar niet aan de
product-taal-conventie. Voor een `report_impediment` met `options`: druk de opties uit
als **producttrade-offs**, niet als implementatie-forks.

If the push is rejected (master moved / protected): fall back to the `pull-request` path.

**Corrupt-slot recovery (0-byte index).** The `ship-merge-$$` slot lives in the
shared `.git/worktrees/`, which a concurrent or crashed session can leave in a
half-written state. The observed shape (kanban card `608e2a27…`): `git worktree
add --detach` prints `Preparing worktree (detached HEAD …)` and exits 0, but the
slot's `index` is 0 bytes — so the *merge* is what fails, with `fatal:
…/ship-merge-<pid>/index: index file smaller than expected`. The obvious cleanup
(`git worktree remove --force "$WT"`) then refuses with `is not a working tree`,
leaving the slot orphaned. Nothing about the checkout itself is wrong: it holds
the correct tree at the correct HEAD, only the index is missing. `git -C "$WT"
read-tree HEAD` rebuilds it from that HEAD, and the same slot merges normally on
the retry. The guard above does exactly that inline, so no session has to
rediscover the recipe by hand.

**Carve-out semantics.** If the merge block hits a conflict, the script enumerates
the conflict set with `git diff --name-only --diff-filter=U`. When that set is a
**non-empty subset** of `{docs/cockpit/README.md, docs/cockpit/llms.txt}`, the
script runs the carve-out automatically and the merge completes inline — no
human intervention. `docs/cockpit/llms.txt` is fully regenerated;
`docs/cockpit/README.md` is regenerated only between the
`<!-- BEGIN GENERATED DOC INDEX -->` and `<!-- END GENERATED DOC INDEX -->` markers,
and the carve-out verifies (via line numbers) that every conflict hunk sits
inside that block — a conflict outside the markers falls through to
`report_impediment` (kanban card 72db7429…). Both files are regenerated by
`scripts/generate-doc-index.py` from the frontmatter of `docs/cockpit/*.md`;
concurrent docs-sessions each regenerate from their own frontmatter snapshot,
the merged frontmatter is the union, and the regenerate inside `$WT` reconciles
from that union. **Why the conflict must remain visible**
(`.gitattributes`-alternative rejected): a `merge=ours` rule for both paths
would suppress the conflict entirely and silently keep master's
pre-regeneration index, losing any new frontmatter added on the branch until
someone manually re-runs the script. The "conflict → regenerate" loop is the
right pattern — the conflict acts as a freshness alarm.

If the carve-out rejects (a handwritten file is in the conflict set), the
worktree at `$WT` is left in its conflicted state for inspection and the script
exits 1. Follow the existing rule, `report_impediment` naming all conflicting
files so a human can resolve it; never force-push or discard either side of
the conflict.

## 4b. Ship mode `pull-request` — open a PR and wait for it to merge

Only when every test passed. Requires the `gh` CLI authenticated:

```bash
gh auth status            # if this fails, see "gh unavailable" below
git push -u origin HEAD
gh pr create --draft --base master --fill
gh pr ready
gh pr merge --auto --squash
```

Then **poll until the PR actually merges** — `master` requires the `quality.yml`
checks to pass, so this can take a few minutes:

```bash
ITER=0
while true; do
  DATA=$(gh pr view --json state,mergeStateStatus,statusCheckRollup)
  STATE=$(echo "$DATA" | jq -r '.state')
  MERGE_STATUS=$(echo "$DATA" | jq -r '.mergeStateStatus')
  echo "PR state: $STATE mergeStateStatus=$MERGE_STATUS"
  if [ "$STATE" = "MERGED" ]; then
    break
  fi
  if [ "$STATE" = "CLOSED" ]; then
    echo 'PR was closed without merging'; exit 1
  fi
  # mergeStateStatus=BLOCKED also just means "checks still running" — only a
  # genuinely failed/cancelled/timed-out check is a real failure.
  FAILED=$(echo "$DATA" | jq '[.statusCheckRollup[]? | select((.conclusion // .status // .state // "") | test("FAILURE|ERROR|CANCELLED|TIMED_OUT|ACTION_REQUIRED"; "i"))] | length')
  if [ "$FAILED" -gt 0 ]; then
    echo 'A required check failed'; exit 1
  fi
  if [ "$MERGE_STATUS" = "DIRTY" ]; then
    echo 'PR has merge conflicts with the base branch'; exit 1
  fi
  ITER=$((ITER + 1))
  if [ "$ITER" -ge 40 ]; then
    echo 'Timed out after ~20 minutes waiting for PR to merge'; exit 1
  fi
  sleep 30
done
```

If it merged: `attach_deliverable` (kind `pr`, ref=`<PR-URL>`), **run the session-end retro**
(invoke the `session-retro` skill — read `.claude/skills/session-retro/SKILL.md` for the
full procedure: reflect → dedupe → file 0–N `[self-improve]` cards → `comment` on this host
card), and finally `move_card` to `Done` with a `summary` of the work you did (required —
the move is rejected without it). **Product-taal** (conventie §5 van
`docs/cockpit/kanban-conventions.md`, kaart `4358fe0a…`): leid met één zin
*productbetekenis*, zet de engineering-detail erna. Een kale engineering-summary voldoet
aan de gate maar niet aan de product-taal-conventie. Voor een `report_impediment` met
`options`: druk de opties uit als **producttrade-offs**, niet als implementatie-forks.

If the loop exited because a check failed, the PR was closed, or the wait timed
out: `attach_deliverable` (kind `pr`, ref=`<PR-URL>`), then `report_impediment`
instead of moving to Done — a human needs to look at the failing/stuck PR.

**gh unavailable:** if `gh auth status` fails, do not merge. Push the branch
(`git push -u origin HEAD`), `comment` "gh unavailable — manual PR needed from <branch>",
`attach_deliverable` (kind `branch`), and stop — do not move the card to Done.
A human needs to open the PR manually.

## 5. Cleanup (automatic)

Once the card reaches `Done`, the backend automatically:
- Kills the tmux session backing this card.
- Removes the git worktree.
- Releases the `agent:` claim.

You do **not** need to clean up tmux or worktrees yourself. Just `move_card` to `Done`
(with `summary`).

**Safety net:** the auto-cleanup only fires for cards that actually reach `Done`.
Worktrees that are merged-but-never-Done, or created outside the kanban flow, leak.
`scripts/worktree-gc.sh` reclaims them — it removes a worktree only when its branch
is fully merged into `master` **and** its working tree is clean; anything dirty or
unmerged is kept. Run `scripts/worktree-gc.sh` (dry-run) to see leftovers, then
`scripts/worktree-gc.sh --apply` to remove them. `cockpit.sh start` prints a nudge
when leftovers exist.

## Rules

- Push **only** to `origin`. Never to any other remote. Never `--force`.
- Never merge or open a PR when tests are red.
- Never push after `git merge` reports a conflict — check its exit code before
  the push/worktree-remove; a misleading "Everything up-to-date" on push means
  nothing new was pushed, not that the merge succeeded.
- A new worktree always branches from `origin/master`.
- `attach_deliverable` before `move_card` so the deliverable is on the card.
- Run the **session-end retro** (`session-retro` skill) between `attach_deliverable`
  and `move_card → Done` so self-improvement lessons land on the Backlog, not in
  the void of a closed transcript.
- `move_card` into `Done` or `Impediment` requires `summary` — the server rejects the
  move without it (`report_impediment` already supplies one via its `question` arg). The
  `summary` itself is bound by the product-taal-conventie (§5 van
  `docs/cockpit/kanban-conventions.md`, kaart `4358fe0a…`): leid met één zin
  *productbetekenis*, zet de engineering-detail erna; voor impediment-options: druk
  *producttrade-offs* uit, niet als implementatie-forks.
