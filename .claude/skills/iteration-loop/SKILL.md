---
name: iteration-loop
description: Use when running a structured repeat-until-clean loop with a named preset — `verify` (test + lint + build), `simplify` (code-review effort=low), `investigate` (read-only sweep), or `flag-cycle` (drain flag-problem findings). Emits `<loop-complete>` when clean and `<loop-blocked>` when stuck; tracks each iteration in `.claude/state/iteration-<card-id>.txt` and posts a summary to the host kanban card.
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

- **Runs:** the in-worktree checks from `git-ship` step 2 — `cd frontend
  && { [ -d node_modules ] || npm ci; } && npm run lint && npm run
  build` (the guarded `npm ci` installs deps on a fresh worktree so lint
  doesn't die with `eslint: not found`). Backend `pytest` is **not** run
  locally on this box (see CLAUDE.md / `git-ship` rationale — the
  shared box's concurrent sessions caused multi-minute stalls under
  full pytest). The loop trusts GitHub Actions `quality.yml` for the
  backend gate and only verifies what is verifiable in-worktree.
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
3. The progress file stays put for the duration of the worktree;
   `scripts/worktree-gc.sh` cleans it up with the rest of the worktree
   when the card moves to `Done`. No manual `rm` needed.

## Choosing a preset — quick decision table

| You want to… | Preset |
|---|---|
| Run the standard end-of-card gate (lint + build, frontend) | `verify` |
| Drain a batch of `code-review` findings on a small diff | `simplify` |
| Sweep for one specific pattern across the worktree without changing anything | `investigate` |
| Work through `[problem]` cards that match this card's scope | `flag-cycle` |

If you're not sure, **start with `verify`** — it's the cheapest and the
most universally applicable.

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
