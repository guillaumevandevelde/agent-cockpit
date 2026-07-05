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

## 1. Sync

```bash
git fetch origin
```

## 2. Run tests yourself before shipping

There is no local pre-push gate — nothing blocks a red push. Run the checks
yourself in this worktree before merging/pushing:

```bash
cd backend && source venv/bin/activate && pytest -q
cd frontend && npm run lint && npm run build
```

Only proceed to shipping once both are green. GitHub Actions (`quality.yml`)
re-runs the same checks after you push as a backstop, but by then the work may
already be merged — it's not a substitute for checking yourself first.

If a test fails: fix the issue, re-run, and only ship once green. Never ship red
tests.

## 3. Commit your work

Make sure every change is committed to the current branch:

```bash
git add -A && git commit -m "<descriptive summary>"
```

## 4a. Ship mode `direct` — merge to master

Only when every test passed:

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
git checkout master
git merge --no-ff "$BRANCH"
git push origin HEAD:master
git checkout "$BRANCH"   # back so the worktree stays valid
```

Then `attach_deliverable` (kind `branch`, ref=`<your-branch-name>`) and `move_card` to `Done`.

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
while true; do
  STATE=$(gh pr view --json state,mergeStateStatus -q '.state + " " + .mergeStateStatus')
  echo "PR state: $STATE"
  case "$STATE" in
    MERGED*) break ;;
    *DIRTY*|*BLOCKED*|CLOSED*) echo 'PR did not merge'; exit 1 ;;
  esac
  sleep 30
done
```

If it merged: `attach_deliverable` (kind `pr`, ref=`<PR-URL>`), then `move_card` to `Done`.

If the loop exited because a check failed or the PR was closed: `attach_deliverable`
(kind `pr`, ref=`<PR-URL>`), then `report_impediment` instead of moving to Done —
a human needs to look at the failing PR.

**gh unavailable:** if `gh auth status` fails, do not merge. Push the branch
(`git push -u origin HEAD`), `comment` "gh unavailable — manual PR needed from <branch>",
`attach_deliverable` (kind `branch`), and stop — do not move the card to Done.
A human needs to open the PR manually.

## 5. Cleanup (automatic)

Once the card reaches `Done`, the backend automatically:
- Kills the tmux session backing this card.
- Removes the git worktree.
- Releases the `agent:` claim.

You do **not** need to clean up tmux or worktrees yourself. Just `move_card` to `Done`.

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
