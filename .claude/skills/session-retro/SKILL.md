---
name: session-retro
description: Use when any dispatched session — executor/engineer or analyst — is about to close (right before move_card → Done) and you want to harvest self-improvement insights from THIS session — workflow friction, repeated tool failures, missed automations, surfaced tech debt, or instruction ambiguity that ate real time — and surface them as Backlog kanban cards so they survive past this transcript. Distinct from flag-problem (one-off in-session observation) and session-problem-scan (sweep over OTHER sessions).
---

# session-retro

End-of-session self-improvement reflection. You look back over the work you
just did in THIS session, identify 0–N concrete improvement points that
would have made this session — or the next one — measurably better at
advancing the app's self-verbetering doelstelling (CLAUDE.md §
"Zelfverbetering"), and file each as a Backlog kanban card. The session
keeps moving toward Done; the cards keep the lessons.

This is the **end-of-session, self-noticed** counterpart to:

- `flag-problem` — one-off problem noticed mid-session → Backlog card
- `session-problem-scan` — health scan over OTHER sessions' transcripts
- `market-research` — outward ecosystem scan → Backlog cards

Concretely: `flag-problem` reacts to a single "wait, that's broken" moment;
this skill audits the whole session for *systemic* friction that
single-shot flags would miss. `session-problem-scan` reads other
transcripts; this skill reads your own. Don't substitute one for the other.

## When to use

- The host card's prompt told you to invoke it (dispatcher injects this
  in the session-end workflow of **every** dispatched card — executor
  and analyst alike).
- A human says "do a retro on this session" / "what could have gone
  better".
- You're about to move_card → Done and want one last pass to harvest
  lessons before the transcript closes.

This applies to analyst sessions too: even though no code ships, an
analyst session still burns tool calls, reads context, and can surface
process friction (a confusing card description, a missing plan
attachment, a dedupe miss) worth capturing. The retro runs right
before the `move_parent → Done` exit, not after a ship step.

## When NOT to use

- The session barely ran (one trivial task, nothing to learn from). A
  forced retro on a 3-tool-call session is noise — say "no findings" and
  skip to Step 6.
- The improvement is the task itself — just do it, don't file a card
  about the card you're closing. Same gotcha as `flag-problem`.
- The finding belongs to a different mechanism: a hard blocker you can't
  resolve → `report_impediment`; a known recurring cadence →
  `market-research`; stuck OTHER sessions → `session-problem-scan`.

## Step 1 — get the *real* project key first

**Same gotcha as `flag-problem` Step 1.** A guessed project key silently
orphans the card onto an invisible board. Resolve it via the
`cockpit-kanban` MCP `resolve_project_key` tool with this repo's
working directory, or with shell:

```bash
curl -s "http://localhost:8000/api/v1/kanban/project-key?project_path=$(git rev-parse --show-toplevel)"
```

Use that exact returned string as `project` in every subsequent call.

## Step 2 — reflect (the only step with judgement)

Walk the session backwards and ask: **"Where did I burn time, context,
or attention that I shouldn't have had to?"** Score each candidate against
the four-pass filter below before moving to Step 3. The filter is the
hard part — everything else is plumbing.

### The four-pass filter

For each candidate improvement, in order:

1. **Self-blame vs systemic?** "I made a typo on line 47" → skip (your
   own slip, won't repeat at the same rate). "The dispatcher prompt
   doesn't tell me to run the frontend checks before push, so I shipped
   red once already" → keep (instructable, fixable).
2. **Materieel?** Did it cost real time/context, or would it cost the
   next session real time? "I had to look up which kanban endpoint
   resolves the project key" → keep. "Maybe someday it would be nice
   to have a slash command" → skip.
3. **Actionable here-or-nearby?** Can an engineer card close it without
   a research rabbit-hole? If the answer is "we'd need to redesign the
   dispatcher first", that's a parent card, not a single-session task
   — file the parent but keep it under `parent_card_id` linking to a
   feature family only if the host card you're closing has one.
4. **Novelty?** Would this be the second, third, ... Nth card about the
   same root cause? Re-pull Backlog/Impediment first (Step 3) and
   deduplicate — but if the existing card already captures it, prefer
   commenting over filing.

If a candidate fails any of 1–3, drop it. **Aim for 0–2 surviving
candidates per session.** A retro that files 5 cards per session is
ignored; a retro that files zero on a clean session is honest.

### Signal categories (what's worth filing)

| Category | Concrete shape | Example |
|---|---|---|
| Workflow frictie | Repeated tool calls / retries / workarounds | "Had to call `resolve_project_key` 3× because the prompt didn't tell me to memoize it" |
| Missed automation | Manual step you did > 1× this session | "Copied branch name into PR title by hand; could be templated" |
| Surfed tech debt | Bug / inconsistency you noticed but didn't touch | "`create_card` accepts `parent_card_id` but the analyst path forgets to set it 30% of the time" |
| Instruction ambiguity | Prompt/doc that was unclear and cost time | "`_build_ship_instructions` says 'run frontend checks' but doesn't say *which* command" |
| Repeated pattern | Same kind of tool failure across multiple turns | "Two `Bash` calls hit 'permission denied' because the worktree dir was the original master, not the new branch" |

### Not signal (skip these)

- The plan was slightly wrong but you adapted (note in done_summary,
  don't file).
- A flaky network call (one-off, retry succeeded).
- Cosmetic preferences ("I'd prefer snake_case in the description").
- A wishlist item with no current friction ("wouldn't it be nice if…").

## Step 3 — dedupe against existing cards

Even after the four-pass filter, check before filing:

```
list_cards(project=<resolved key>, column="Backlog")
list_cards(project=<resolved key>, column="Impediment")
```

Read titles and descriptions for the **same underlying improvement**,
not the same keyword. A `[problem]` card about a flaky test and a
`[self-improve]` card about the same flaky test are duplicates.

**Timing:** re-pull Backlog/Impediment *directly* before filing, not at
start-of-session. A scan from minutes ago is stale as soon as a parallel
session on the same board files an overlapping card (kaart `3a4ca295…`);
the dedupe pass is bound to the `create_card` call, not to retro-start.

- **Duplicate found** → `comment(card_id, text)` with what's new: this
  session's confirmation, a different trigger path, a narrower scope,
  or evidence the impact is worse than originally captured.
- **No duplicate** → continue to Step 4.

## Step 4 — file 0–N Backlog cards

`create_card(project=<resolved key>, column="Backlog", title=..., description=...)`

- **title**: `[self-improve] <one-line summary>` — must be specific and
  searchable. Use the same one-line pattern that future dedupe passes
  (yours or someone else's) would match on a keyword grep.
- **description**, structured for fast triage:

```markdown
## What I observed
<1–3 sentences — what happened this session, and why it costs time>

## Evidence
- Where: <file:line, command, prompt section, endpoint>
- Trigger: <the tool call / step that surfaced it>
- Frequency: <one-off, every-N-sessions, every session>

## Suggested improvement
<Concrete next step — what an engineer card would do. "Be more careful"
is not a fix; "prepend `resolve_project_key` to the prompt" is.>

## Acceptance criteria
<1–3 bullets, scoped to a single engineer-session.>
```

Keep total session overhead low — the retro runs *after* the actual work
is done; don't burn 30 turns writing thoughtful descriptions.

**Verify your `Where:` paths before you move on.** A pointer that does not
resolve sends every later reader down a dead trail, and this card gets copied
as a pattern by the next author. One command per filed card:

```bash
scripts/check-card-where-paths.sh --card=<new-card-id>
```

It strips `:line`, `:line-range`, `::symbol` and `#anchor` suffixes and then
`test -e`s what remains — so `foo.py:42` is checked as `foo.py`. Fix anything
it names. Note it is existence-only: a path can exist and still not contain
what you claim (`CLAUDE.md` does *not* hold the FCR prompt — kaart
`549ef4d6…`), so a green run is necessary, not sufficient.

## Step 5 — if zero findings, say so explicitly

Zero findings is a legitimate, **good** outcome. It means the session
went smoothly. Do NOT manufacture a card to make the run feel
productive. Skip Steps 3–4 and go straight to Step 6. A no-op retro is
the success case for sessions that ran cleanly.

## Step 6 — record the run on the host card

Post a short comment on the host kanban card so the operator (and a
follow-up `session-problem-scan`) can see the retro happened:

```
comment(card_id=<host_card_id>, text="Retro: filed N cards (links) | deduped M onto existing cards | no-op (clean session)")
```

Even a no-op gets a breadcrumb. "Retro ran, found nothing" is
information; "no retro was attempted" is a black hole.

## Quick reference

```text
resolve project key  →  reflect (four-pass filter, aim for 0–2 survivors)
                       →  dedupe vs Backlog+Impediment
                       →  file 0–N [self-improve] cards (per template)
                       →  comment on host card with outcome
```

## Common mistakes

| Excuse | Why it's wrong |
|---|---|
| "I'll just dump my findings in the host card's done_summary" | `done_summary` is read once at close time; Backlog cards are the durable queue the dispatcher consumes. |
| "I'll file 5 cards, the backlog is the queue anyway" | Backlog is finite attention. 0–2 high-quality per session beats 5 mediocre — every session. |
| "This is basically flag-problem, I'll use that tag" | The trigger and intent are different. `[problem]` = concrete bug/staleness; `[self-improve]` = systemic lesson from this session's *process*. |
| "I'll skip the ruisfilter, I'll just trust my judgement" | The filter is the whole point — without it, every retro spams the board and gets ignored. |
| "I guessed `git:github.com/<owner>/<repo>` for the project key" | Same as `flag-problem`: resolve via Step 1 every time, or the card vanishes. |
| "Nothing went wrong, no need to run the retro" | Then Step 5 says "no findings" and you comment the no-op in Step 6. The retro *ran* — that's the win. |
| "Let me reflect on the whole repo, not just this session" | That's `market-research` or `session-problem-scan`. This skill is bounded to what THIS session experienced. |

## Red flags — STOP and re-check

- You're filing more than 3 cards for a single session.
- A candidate improvement is about something you did *outside* this
  session (another card, another repo, an unrelated observation) —
  that's `flag-problem`, not retro.
- Your title says `[problem]` or `[research]` — wrong tag for this
  skill, even if the content is about self-improvement.
- You can't articulate what concrete fix the card would lead to —
  drop the candidate, it's not actionable enough.

## Worked example

You just shipped kind-1 of a 3-card decomposed feature in an executor
session. The session ran the full TDD loop, merged cleanly, and you
noticed three things worth capturing:

1. **Two `report_impediment` calls rejected because `summary` was
   missing on the *first* attempt** — both you and the previous session
   hit this. The MCP tool's error message is clear, but the dispatch
   prompt never tells you `summary` is required for Done/Impediment
   moves.
2. **`git remote get-url origin` returned an SSH URL, but the
   `resolve_project_key` MCP tool returned a `git:` key derived from
   the *HTTPS* variant** — caused one dedupe miss until you re-ran
   Step 1.
3. **The frontend lint+build ran clean first try** — no improvement
   needed.

After the four-pass filter: (1) survives (systemic, actionable,
novel), (2) drops (one-off; you only saw it once and fixed it
immediately), (3) drops (positive observation isn't an improvement
point).

Dedupe check finds no existing card about (1) — it's a known
papercut but never formally filed. File one `[self-improve]` card:

```markdown
title: [self-improve] report_impediment rejects without summary; prompt never warns
description:
  ## What I observed
  Two `move_card(column="Done")` calls in adjacent sessions failed with
  `{"error": "summary_required"}` because the dispatch prompt doesn't
  surface that Done/Impediment moves require a non-empty `summary`. The
  rejection is loud but discoverable only after the first attempt.

  ## Evidence
  - Where: backend/app/services/kanban/move_card path + dispatch prompt
    section "Kaart bijwerken"
  - Trigger: any session moving its host card to Done/Impediment
  - Frequency: every session that hasn't memorized the rule yet
    (i.e. always, until prompted)

  ## Suggested improvement
  Add a one-line callout to the dispatch prompt's "Kaart bijwerken"
  block: "`move_card` naar Done/Impediment vereist `summary` — anders
  krijg je `summary_required`." Optionally: also surface the
  requirement in the `move_card` tool's own description.

  ## Acceptance criteria
  - [ ] Dispatch prompt explicitly mentions `summary` is required for
        Done/Impediment
  - [ ] A fresh executor session hits the rule no more than once
        (ideally zero) before getting it right
```

Then post on the host card:

```
comment: Retro: filed 1 [self-improve] card (link) | 2 dropped by ruisfilter | 0 deduped
```

Total overhead: ~3 tool calls for the retro (resolve + list + create +
comment). Under a minute. That's the budget.