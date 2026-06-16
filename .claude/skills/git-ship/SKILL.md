---
name: git-ship
description: Use after finishing work in a worktree — runs tests and, only if green, merges to master or opens a draft PR per the project's ship mode.
---

# git-ship

Ship the work in the current worktree **safely and unattended**. Never ship red tests.
Your opening prompt states the **ship mode**: `direct` or `pull-request`. Follow the matching
path below.

## 1. Sync

```bash
git fetch origin
```

## 2. Run the tests — they gate everything

- Backend: activate the project's Python venv (in this repo it lives in the **main checkout**
  at `backend/venv`), then from the worktree's own backend dir:
  `pytest tests/`
- Frontend (if frontend files changed): `cd frontend && npm run lint && npm run build`

If anything fails: **stop**. Do not merge, do not open a PR. `comment` on the card with the
failing output and leave the card in `Doing`. You are done.

## 3a. Ship mode `direct`

Only when every test passed:

```bash
git push origin HEAD:refs/heads/<your-branch>      # back up the branch first
git fetch origin
git checkout -B ship-master origin/master
git merge --no-ff <your-branch>
git push origin HEAD:master
```

Then `move_card` to `Review` and `attach_deliverable` (kind `branch` or `commit`).

If the push is rejected (master moved / protected): fall back to the `pull-request` path.

## 3b. Ship mode `pull-request`

Only when every test passed. Requires the `gh` CLI authenticated:

```bash
gh auth status            # if this fails, see "gh unavailable" below
git push -u origin HEAD
gh pr create --draft --base master --fill
```

Capture the PR URL from `gh pr create` output, `attach_deliverable` (kind `pr`, the URL),
then `move_card` to `Review`.

**gh unavailable:** if `gh auth status` fails, do not merge. Push the branch
(`git push -u origin HEAD`), `comment` "gh unavailable — manual PR needed from <branch>",
and leave the card in `Doing`.

## Rules

- Push **only** to `origin`. Never to any other remote. Never `--force`.
- Never merge or open a PR when tests are red.
- A new worktree always branches from `origin/master`.
