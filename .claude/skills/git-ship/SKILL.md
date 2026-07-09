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

## 2. Run frontend checks yourself before shipping

There is no local pre-push gate — nothing blocks a red push. Run the frontend
checks yourself in this worktree before merging/pushing:

```bash
cd frontend && npm run lint && npm run build
```

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

Only when every test passed:

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
git checkout master
git merge --no-ff "$BRANCH"
git push origin HEAD:master
git checkout "$BRANCH"   # back so the worktree stays valid
```

Then `attach_deliverable` (kind `branch`, ref=`<your-branch-name>`) and `move_card` to
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

If it merged: `attach_deliverable` (kind `pr`, ref=`<PR-URL>`), then `move_card` to `Done`
with a `summary` of the work you did (required — the move is rejected without it).

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
- `move_card` into `Done` or `Impediment` requires `summary` — the server rejects the
  move without it (`report_impediment` already supplies one via its `question` arg).
