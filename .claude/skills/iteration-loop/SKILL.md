---
name: iteration-loop
description: Use when running a structured repeat-until-clean loop with a named preset — `verify` (test + lint + build), `simplify` (code-review effort=low), `investigate` (read-only sweep), `flag-cycle` (drain flag-problem findings), `pytest-attr` (attribute pytest failures to engineer vs pre-existing on master), or `bash-test-attr` (attribute bash-test failures the same way for `scripts/test_*.sh`). Emits `<loop-complete>` when clean and `<loop-blocked>` when stuck; tracks each iteration in `.claude/state/iteration-<card-id>.txt` and posts a summary to the host kanban card.
---

# iteration-loop

A small, structured loop that runs a **named preset** (e.g. `verify`,
`simplify`, `investigate`) **repeatedly until clean**, emits a clear signal
tag at the end (`<loop-complete>` or `<loop-blocked>`), and leaves a
per-iteration breadcrumb in `.claude/state/iteration-<card-id>.txt` so an
operator — or a follow-up `session-problem-scan` — can see what the loop
did and why it stopped.

The shape is borrowed from `claude-task-master`'s `task-master loop`
command (loop runs Claude Code in a Docker sandbox, one task per loop,
tracks via `progress.txt`, signals via tags). This skill brings the same
discipline to Cockpit's engineer/analyst agents but stays inside the
existing `.claude/skills/` framework — no Docker, no cron, no new CLI.

The **caller** (the engineer session, an analyst, or a scheduled run)
invokes this skill; the loop itself just lays out the contract, the
presets, and the bookkeeping. The caller is still the one driving
tool calls and edits.

## When to use

- You're about to declare a card "done" and want a **tracked, repeatable
  quality gate** before shipping (use preset `verify`).
- You want to drain a class of low-severity findings into a single,
  auditable cleanup pass (use `simplify` for code-review follow-ups, or
  `flag-cycle` to work through `flag-problem` Backlog cards you
  generated).
- You want a read-only sweep for one specific pattern and want the
  results machine-readable (use `investigate`).

## When NOT to use

- You're running a single command once and don't need a loop (just run it
  inline — the loop adds tracking overhead; don't call a one-shot a
  "loop").
- The host task isn't producing findings or change (the loop shines when
  there's something to iterate on; otherwise it's ceremony).
- You're mid-card and just want to check one thing — `iteration-loop` is
  for the **end-of-card** gate, not a substitute for in-flight
  verification of the work in progress.

## The contract — every preset obeys these three rules

1. **Per-iteration record.** Append one line to
   `.claude/state/iteration-<card-id>.txt` for every iteration:
   timestamp, preset, iteration number, action, outcome. This file is
   the loop's audit trail; the parent card's comment is the human-visible
   summary, this file is the machine-readable one. The file is
   **append-only** within a loop — never rewrite a previous line.
2. **End signal.** When the preset is satisfied (e.g. all tests pass, all
   findings closed), emit `<loop-complete>` on its own line in the chat
   output. When the loop hits a hard blocker (a failing check the loop
   can't fix, an external dependency the operator must resolve), emit
   `<loop-blocked>` followed by a one-line reason. **These tags are
   picked up by the calling agent and by post-card automation — never
   bury them inside code fences or other text.** A tag mid-prose is a
   missed signal.
3. **One host card per loop.** A loop runs against exactly one kanban
   card. At loop end, `comment` on that card with: preset used,
   iterations run, final outcome, path to the progress file. This is
   what an operator sees; the progress file is what a follow-up
   `session-problem-scan` reads.

## Presets

Each preset names **what runs**, **what "clean" means**, and **what to do
when blocked**. The output of one preset can feed the next — in
particular `investigate` → `flag-cycle` is a common two-pass.

### `verify` — test + lint + build (default end-of-card gate)

- **Runs:** the in-worktree checks from `git-ship` step 2 — the
  `FRONTEND_TOUCHED` probe + symlink-or-`npm ci` bootstrap (when
  `frontend/package-lock.json` matches origin/master, symlink the main
  checkout's `frontend/node_modules` to skip the multi-minute install;
  only fall back to `npm ci` when the lockfile diverged or main's
  `node_modules` is missing/partial — partial is detected by a
  `node_modules` dir without `.bin/` and moved aside via `mv` since
  `rm` is deny-listed). Backend `pytest` is **not** run locally on this
  box (see CLAUDE.md / `git-ship` rationale — the shared box's
  concurrent sessions caused multi-minute stalls under full pytest).
  The loop trusts GitHub Actions `quality.yml` for the backend gate and
  only verifies what is verifiable in-worktree.
  If `ruff` is wired into the worktree (`backend/.venv/bin/ruff`), run
  that too; otherwise skip and note it in the progress file.
- **Clean when:** lint passes AND build succeeds, with zero warnings
  that aren't already in the ESLint baseline.
- **Blocked when:** build fails on a code issue the loop can't fix in one
  pass (cross-file type error, missing dependency, broken import).
  Emit `<loop-blocked>` and surface the failing file:line in the parent
  card comment.
- **Default iteration cap:** 3 — after three failed builds, the loop is
  no longer making progress; re-plan.

### `simplify` — code-review effort=low, applied repeatedly

- **Runs:** invoke the `code-review` skill with effort `low` on the
  current working-tree diff. Address each finding inline. Re-run until
  either no findings or three consecutive no-finding iterations.
- **Clean when:** three consecutive iterations return zero findings.
- **Blocked when:** a finding the loop can't safely address (touches
  shared infra, requires a human call) — file it via `flag-problem`
  and emit `<loop-blocked>` with the finding ID.
- **Default iteration cap:** 5 (3 no-finding + 2 fix passes).

### `investigate` — read-only sweep, surface only

- **Runs:** read-only search across the worktree for a single pattern
  (stale doc reference, deprecated import, leftover `TODO` from a closed
  card, etc.). Does NOT modify files. Collects hits into the progress
  file with `file:line:match`.
- **Clean when:** zero hits, or every hit has been filed as a
  `flag-problem` Backlog card.
- **Blocked when:** the search itself errors (ripgrep not available,
  path doesn't exist, pattern is malformed). Emit `<loop-blocked>` with
  the error verbatim.
- **Default iteration cap:** 1 — this preset is single-pass by design;
  re-running it on the same pattern almost never finds new hits.

### `flag-cycle` — drain `flag-problem` findings

- **Runs:** pull `Backlog` and `Doing` for the resolved project, pick
  cards filed via `flag-problem` (title prefix `[problem]`) that match
  the current card's scope. For each: apply the suggested fix if it's
  small and safe, otherwise `comment` the card with "deferred, needs
  <reason>" so a future session picks it up.
- **Clean when:** zero matching cards remain unaddressed.
- **Blocked when:** a fix requires a code change outside this loop's
  scope (a real engineer card should pick it up). Emit `<loop-blocked>`
  and leave the original `flag-problem` card untouched.
- **Default iteration cap:** 3 — the loop is meant to drain a small
  batch, not to chew through a month of backlog.

### `pytest-attr` — attribute pytest failures to engineer vs pre-existing

- **Use when:** an engineer card touches `backend/` and the engineer
  wants to know "is this `FAILED` mine or one of the ~15-20 pre-existing
  failures on `origin/master`?" without running `git stash + pytest +
  git stash pop` four extra times per session (the pain that motivated
  kanban card 4c7c5346). Pairs with `scripts/pytest-baseline.sh` (one-
  shot baseline capture) and `scripts/pytest-compare.sh` (current-vs-
  baseline attribution).
- **Runs:**
  1. If `.claude/state/pytest-baseline.txt` is missing or older than
     `--max-age-hours` (env `PYTEST_BASELINE_MAX_AGE_HOURS`, default 24),
     run `scripts/pytest-baseline.sh` to refresh it on a clean detached
     worktree of `origin/master`. No-op if a fresh cache already exists.
  2. Run `scripts/pytest-compare.sh` to capture the current failure set
     and classify each test as `pre-existing`, `NEW (your fault)`, or
     `FIXED by your changes`.
- **Worktree sessions (no local venv):** both scripts resolve pytest via
  the same worktree-local → shared main-checkout venv → PATH fallback
  chain as `scripts/run-single-test.sh` (`scripts/lib/resolve-pytest-cmd.sh`),
  so no `PYTEST_CMD=` override is needed even in a fresh worktree.
- **Clean when:** `pytest-compare.sh` exits 0 — every current failure
  is also in the baseline (no new failures). Pre-existing + FIXED-only
  diffs are fine; the loop's job is "did this card add any new reds?",
  not "is master 100% green?".
- **"Pre-existing" ≠ "passing".** `pytest-compare.sh` classifying a
  failure as "pre-existing (not your fault)" means the same test is
  already red on `origin/master` — it does **not** mean the test
  passes. An exit-0/clean loop summary can still be sitting on top of
  deterministically-failing tests; read the actual failure/traceback
  before concluding a targeted-file failure is order-dependent or
  environmental (see kanban card `1419c9ef`).
- **Blocked when:** `pytest-compare.sh` exits 1 — at least one `NEW`
  failure. The attribution output is what the engineer triages; the
  loop emits `<loop-blocked>` and the engineer fixes the named tests.
- **Caveat — when NOT to use:** this preset overrides the shared-box
  rationale that backend `pytest` is normally NOT run locally (see
  `git-ship` step 2 / CLAUDE.md "No local pre-push gate"). It exists
  for engineers who *choose* to debug their backend changes with a
  local pytest run; CI's `quality.yml` is still the canonical backend
  gate. Default to `verify` for the standard end-of-card gate; use
  `pytest-attr` only when an engineer session is explicitly debugging
  a failing test.
- **Default iteration cap:** 3 — usually one pass is enough; more is
  needed when the engineer is iterating on a fix (each pass reruns
  pytest + comparison, which is fast since the baseline is cached).

### `bash-test-attr` — attribute bash-test failures to engineer vs pre-existing

- **Use when:** an engineer card touches `scripts/` or `docs/cockpit/` and
  the engineer wants to know "is this `FAIL: …` line in `scripts/test_*.sh`
  mine or one of the ~1-3 pre-existing failures on `origin/master`?"
  without running `git stash -u && bash && git stash pop` four extra times
  per session. Same motivation as `pytest-attr` (kanban card 4c7c5346),
  applied to the 16 `scripts/test_*.sh` harnesses; kaart
  `ecea763e802a4cd59011652dd2537839` is the precedent-tracking ticket.
- **Runs:**
  1. If `.claude/state/bash-test-baseline.txt` is missing or older than
     `--max-age-hours` (env `BASH_TEST_BASELINE_MAX_AGE_HOURS`, default 24),
     run `scripts/baseline-bash-tests.sh` to refresh it on a clean detached
     worktree of `origin/master`. No-op if a fresh cache exists.
  2. Run `scripts/compare-bash-tests.sh` to capture the current failure set
     and classify each line as `pre-existing`, `NEW (your fault)`, or
     `FIXED by your changes`. NEW/FIXED sections are grouped by harness-name
     so the engineer can read across 16 harnesses at a glance; the
     pre-existing section lists the unique harness names that match.
- **Harness-shape dependency:** every `scripts/test_*.sh` is expected to
  follow the project convention (`bad() { echo "  FAIL: $1"; … }` +
  `Total: $PASS passed, $FAIL failed` summary + exit `$FAIL == 0`).
  Harnesses that violate this convention (e.g. crash without emitting
  `FAIL:` lines because of a bash parse error) are still attributed —
  the comparator synthesizes a single `(harness crashed without FAIL
  lines)` sentinel so the line survives the comm-based diff.
- **Worktree sessions:** bash tests need no venv and no interpreter
  resolution — `bash` is on every PATH, and the scripts under test only
  touch `scripts/*.sh` and (sometimes) `docs/cockpit/`. The comparator's
  `BASH_TEST_FAKE_WORKTREE=1` + `BASH_TEST_CWD=` overrides exist only for
  the test harness, not for normal engineer use.
- **Clean when:** `compare-bash-tests.sh` exits 0 — every current failure
  is also in the baseline (no new failures).
- **"Pre-existing" ≠ "passing".** `compare-bash-tests.sh` classifying a
  failure as "pre-existing (not your fault)" means the same `FAIL: …`
  line is already produced by `origin/master` — it does **not** mean
  the test passes. Read the actual FAIL: line before concluding a
  targeted-file failure is environmental.
- **Blocked when:** `compare-bash-tests.sh` exits 1 — at least one NEW
  failure. The attribution output is what the engineer triages; the
  loop emits `<loop-blocked>` and the engineer fixes the named
  harnesses.
- **Caveat — when NOT to use:** this preset does not run pytest. For a scripts-only card, it **is** the standard end-of-card attribution gate: run it after targeted bash tests to distinguish pre-existing failures from regressions. For a card that also touches backend code, use `pytest-attr` for pytest attribution; for a card that touches frontend code, use `verify` for frontend lint/build. These checks are complementary when a card spans multiple surfaces.
- **Default iteration cap:** 3 — usually one pass is enough; the
  baseline is cached, so re-running is cheap.

## Per-iteration protocol

```
LOOP (preset=<name>, host_card=<id>):
  iter = 0
  cap  = <preset default cap>
  while iter < cap:
    iter += 1
    append one line to .claude/state/iteration-<host_card>.txt
      format: "<iso8601> | preset=<name> | iter=<iter> | <action> | <outcome>"
    run the preset's "Runs" step
    if clean → emit `<loop-complete>` and EXIT
    if blocked → emit `<loop-blocked>` and EXIT
  emit `<loop-blocked>` (cap reached)
```

The cap exists to prevent runaway loops when a finding is structurally
not converging. If you hit the cap, the loop treats the remaining delta
as blocked — **re-plan, don't keep iterating.**

## End-of-loop housekeeping

After `<loop-complete>` or `<loop-blocked>`:

1. `comment` on the host kanban card with: preset used, iterations run,
   final outcome, path to the progress file.
2. If `<loop-blocked>`, also `comment` on any specific Backlog cards
   the loop opened (via `flag-problem` etc.) so the trail is complete
   for whoever picks them up next.
3. **Lifecycle is git-driven, not worktree-gc-driven.** The progress
   path `.claude/state/iteration-<card-id>.txt` is in `.gitignore`
   (added by commit `31f3a51` "chore: gitignore iteration-loop
   progress files"), so a file written there is ignored by git — the
   ship pre-flight only tests tracked-file changes (`git status
   --porcelain | grep -v '^??'`), so untracked files like this one are
   out of the blocking path, and `scripts/worktree-gc.sh` treats the
   worktree as clean and removes the whole `.claude/` subtree along
   with the worktree when the card moves to `Done`. No manual `rm`
   needed.

   Some past sessions have also committed these files into their
   card's commit (search `git ls-files .claude/state/` — they're
   still on `master`), but that was a workaround for the
   pre-`31f3a51` state where the path wasn't gitignored. It is **not**
   required today; if `git status` reports nothing under
   `.claude/state/`, leave it alone.

   **Subdir gotcha — the path is cwd-relative.** If a mid-session
   `cd` left the loop running with cwd in a subdirectory (e.g.
   `cd backend && …` for a pytest run, then a subsequent iteration
   appending to `.claude/state/…` from that cwd), the file lands at
   e.g. `backend/.claude/state/iteration-<card-id>.txt` — and the
   gitignore pattern does **not** match nested paths, so the file
   shows up under `git status` as untracked. With the new pre-flight
   (tracked-files-only), an untracked `iteration-*.txt` no longer
   *blocks* the ship, but it still leaks across worktree boundaries
   if the loop writes to a subdir. Best practice: the loop writes to
   the gitignored root (`.claude/state/`) so the file is invisible to
   both `git status` and the pre-flight — if you ever see the file
   under a non-ignored subdir, move it before the next ship so it
   doesn't ride along on a different worktree's `git worktree remove`.

   If the pre-flight ever mentions an untracked
   `iteration-*.txt` in its advisory, `git status --ignored` will
   tell you whether the loop wrote it under the gitignored root or
   a non-ignored subdir — the advisory lists the first 20.

## Choosing a preset — quick decision table

| You want to… | Preset |
|---|---|
| Run the standard end-of-card gate for a scripts-only card (`scripts/` or `scripts/test_*.sh`) | `bash-test-attr` |
| Run the standard end-of-card gate for a card that touches frontend code | `verify` |
| Drain a batch of `code-review` findings on a small diff | `simplify` |
| Sweep for one specific pattern across the worktree without changing anything | `investigate` |
| Work through `[problem]` cards that match this card's scope | `flag-cycle` |
| Attribute pytest failures to "yours" vs "pre-existing on master" | `pytest-attr` |
| Attribute bash-test failures (`scripts/test_*.sh`) to "yours" vs "pre-existing on master" | `bash-test-attr` |

If you're not sure, choose by the files touched: use `bash-test-attr` when the card is scripts-only, `verify` when it touches frontend code, and add `pytest-attr` only when explicitly debugging backend pytest failures.

## Common mistakes

| Excuse | Why it's wrong |
|---|---|
| "I'll just run the command once and skip the loop" | The loop's value is the trail in `progress.txt` and the explicit signal tag. Single runs are fine, but call them what they are — not a "loop". |
| "I'll keep iterating until the build is green" | The cap exists because after N iterations the loop is no longer making progress. Re-plan instead of grinding. |
| "I'll bury the signal tag inside a code block" | Post-card automation greps for `<loop-complete>` / `<loop-blocked>` on their own line; embedded tags are missed. |
| "I'll write the progress file from scratch each iteration" | The file is an append-only log; rewriting it loses the audit trail and breaks the `session-problem-scan` reader. |
| "One preset fits all — I'll just always run `verify`" | The presets have different clean-criteria; `verify` won't catch stale docs, `investigate` won't catch failing builds. Match the preset to the gate. |
| "I'll re-plan silently without emitting `<loop-blocked>`" | The whole point of the signal tag is to surface "I gave up" so the parent card and the operator see it. A silent re-plan is a hidden failure. |

## Quick reference

```text
Run loop with preset=<name>, host_card=<id>:
  iter = 0; cap = preset default
  while iter < cap:
    append "<iso> | preset=<name> | iter=<N> | <action> | <outcome>" to
      .claude/state/iteration-<id>.txt
    run preset's "Runs" step
    if clean → emit `<loop-complete>` and EXIT
    if blocked → emit `<loop-blocked>` and EXIT
  cap reached → emit `<loop-blocked>` and EXIT
End: comment on host card with preset / iters / outcome / progress path.
```
