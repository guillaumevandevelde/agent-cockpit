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
  # Card 15cc257d… also handled the partial-install trap: an interrupted
  # `npm ci` leaves some scoped dirs but no `.bin/`, which makes `npm run
  # lint` die with `eslint: not found` and blocks a plain symlink. Move the
  # partial aside (`mv`, not `rm` — `rm` is deny-listed) before bootstrapping.
  ( cd frontend && \
    if [ -d node_modules ] && [ ! -d node_modules/.bin ]; then \
      mv node_modules "../node_modules.partial-$(date +%s)" && \
      echo "moved partial node_modules aside (missing .bin/)"; \
    fi && \
    if [ ! -d node_modules ]; then \
      BASE=$(git merge-base HEAD origin/master) && \
      if git diff --quiet "$BASE" origin/master -- frontend/package-lock.json \
         && [ -d /home/vdvgu/claude-cockpit/frontend/node_modules/.bin ]; then \
        ln -s /home/vdvgu/claude-cockpit/frontend/node_modules node_modules && \
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

## 3. Commit your work

Make sure every change is committed to the current branch:

```bash
git add -A && git commit -m "<descriptive summary>"
```

## 4a. Ship mode `direct` — merge to master

Only when every test passed. You are in a linked worktree while `master` is
checked out in the main working copy, so checking out `master` here fails with
`'master' is already used by worktree at ...`. Merge through a throwaway
detached worktree instead — it never touches your current checkout:

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
# Pre-flight: the detached worktree only sees COMMITTED state. Uncommitted/untracked
# changes here merge as a silent no-op ("Everything up-to-date"), pushing nothing —
# abort so you commit them (step 3) first.
if ! git diff --quiet HEAD || [ -n "$(git ls-files --others --exclude-standard)" ]; then
  echo 'ERROR: uncommitted/untracked changes — git add + git commit first, then re-run.' >&2; exit 1
fi
TMP=$(mktemp -d)
# Slot name MUST be unique per session: git derives the `.git/worktrees/<name>`
# entry from the path's basename, so a fixed name (e.g. `m`) collides under
# concurrent dispatched sessions — both target the same gitdir slot, and a
# stale HEAD (or a half-pruned gitdir from a crashed predecessor) leaks into
# the fresh session's merge push, producing a spurious non-fast-forward
# rejection against origin/master. `$$` (this process's PID) guarantees a
# fresh slot per invocation — do NOT simplify back to a fixed name.
# (kanban card c23dfe46…)
git worktree add --detach "$TMP/merge-$$" origin/master
git -C "$TMP/merge-$$" merge --no-ff "$BRANCH" -m "Merge $BRANCH"
git -C "$TMP/merge-$$" push origin HEAD:master
git worktree remove --force "$TMP/merge-$$"
```

Then `attach_deliverable` (kind `branch`, ref=`<your-branch-name>`), **run the session-end
retro** (invoke the `session-retro` skill — read
`.claude/skills/session-retro/SKILL.md` for the full procedure: reflect → dedupe → file
0–N `[self-improve]` cards → `comment` on this host card), and finally `move_card` to
`Done` with a `summary` of the work you did (required — the move is rejected without it).

If the push is rejected (master moved / protected): fall back to the `pull-request` path.

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
the move is rejected without it).

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
- A new worktree always branches from `origin/master`.
- `attach_deliverable` before `move_card` so the deliverable is on the card.
- Run the **session-end retro** (`session-retro` skill) between `attach_deliverable`
  and `move_card → Done` so self-improvement lessons land on the Backlog, not in
  the void of a closed transcript.
- `move_card` into `Done` or `Impediment` requires `summary` — the server rejects the
  move without it (`report_impediment` already supplies one via its `question` arg).
