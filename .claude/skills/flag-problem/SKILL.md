---
name: flag-problem
description: Use when you notice a problem during your own session — a bug, confusing docs, a tooling gap, a broken assumption, a workflow obstacle — that is NOT the task you were assigned. Dedupes against existing kanban cards and either comments on a matching one or files a new Backlog card, so the observation survives past this session instead of vanishing when the transcript closes.
---

# flag-problem

You are mid-task and you notice something broken that isn't what you were asked
to fix — a stale doc, a tool that lied about its own defaults, an inconsistent
API, a footgun that cost you 10 minutes to work around. Don't just mention it
in chat and move on. File it, or add to an existing filing, so the next
session doesn't rediscover it the hard way.

This is the **in-session, self-noticed** counterpart to
`session-problem-scan`, which is an **external, on-demand sweep** over past
transcripts for crashed/stuck sessions. Use this skill for "I just hit
something weird while doing my actual work"; use `session-problem-scan` for
"is anything broken across the board/transcripts right now".

## When to use

- You hit a bug, a misleading doc, a confusing tool result, or a workflow gap
  that isn't the card you're working — and it cost you real time or would
  cost the next session real time.
- You're about to finish a session and realize something you worked around
  deserves a permanent fix later.

## When NOT to use

- The problem *is* your assigned task — just fix it, don't file a card about
  your own card.
- A one-off transient failure that resolved itself on retry (network blip,
  flaky test you didn't touch) — not signal, just noise.
- You're already filing via `report_impediment` (you're blocked and handing
  off the card itself) — that's a different mechanism for a different
  purpose; don't also file a duplicate Backlog card for the same blocker.

## Step 1 — get the *real* project key first

**This is the step most likely to silently fail.** Kanban cards are bucketed
by a free-form `project` string with no validation — a typo or a
differently-derived key creates an invisible parallel board. This was
verified the hard way while building this skill: `list_cards(project=
"claude-cockpit")` returned an empty board even though real cards existed,
because the live board for this repo is keyed `git:github.com/
guillaumevandevelde/claude-cockpit` — derived from the git remote, not typed
by hand. A card filed under any other string (a guessed slug, a display name,
a different-cased string) is orphaned: invisible from the real board, and it
makes future dedupe checks against it silently miss.

Resolve it for real, don't guess. If the `cockpit-kanban` MCP server is
available, call its `resolve_project_key` tool with this repo's working
directory (e.g. `git rev-parse --show-toplevel`) — this works even for
MCP-only agents with no shell/HTTP access. Otherwise, with shell access:

```bash
curl -s "http://localhost:8000/api/v1/kanban/project-key?project_path=$(git rev-parse --show-toplevel)"
```

Either way you get back `{"project_key": "git:host/owner/repo"}` (or
`slug:<name>` if the repo has no remote). **Use that exact string** as
`project` in every `list_cards`/`create_card` call below. If you're already inside a
kanban-dispatched session, this is the same key your own card lives under —
you can also confirm by finding your own card:
`list_cards(project=<resolved key>)` and matching `claimed_by` against your
session name (branch name / `agent:<branch>`).

## Step 2 — dedupe

```
list_cards(project=<resolved key>, column="Backlog")
list_cards(project=<resolved key>, column="Impediment")
```

Read titles and descriptions for the same root cause — not just the same
symptom. Match on: same file/tool/endpoint, same error signature, same
misleading doc. A card titled differently but describing the same underlying
issue still counts as a duplicate.

- **Duplicate found** → `comment(card_id, text)` with what's new: your own
  evidence, a different trigger path, confirmation it's still happening, or
  a narrower root cause than what's already written. Don't create a second
  card for the same problem — that fragments the trail `session-problem-scan`
  and human triage both rely on.
- **No duplicate** → go to Step 3.

## Step 3 — file it

`create_card(project=<resolved key>, column="Backlog", title=..., description=...)`

- **title**: `[problem] <one-line summary>` — specific enough that a future
  dedupe pass (yours or someone else's) can match it by keyword.
- **description**, structured for fast triage:

```markdown
## Problem
<1-3 sentences — what's broken, and why it's not just you>

## Evidence
- Where: <file:line, endpoint, command, or doc section>
- What you saw: <actual output/behavior>
- What you expected: <per the doc/code/convention it contradicts>

## Suggested fix
<concrete next step, if you have one — otherwise say what you'd check first>
```

Keep it short — a triage session should be able to act in under a minute.

## Don't let this derail your actual task

Filing takes two tool calls once you have the resolved project key. Do it,
then get back to the card you were actually dispatched for. If the problem
is big enough to need real investigation, file a *stub* card now (evidence +
"needs investigation") rather than going down the rabbit hole mid-session —
a future session (or a human) can pick it up with full context restored from
your evidence section.

## Common mistakes

| Excuse | Why it's wrong |
|--------|-----------------|
| "I'll just mention it in my session-end comment" | Comments on *your* card are invisible to dedupe and don't surface on the Backlog — they die with this card. |
| "I'm sure the project key is `<repo-name>`" | Guessing here creates an orphaned bucket. Resolve it via the endpoint in Step 1, every time. |
| "Close enough title, I'll just make a new card" | Skim the existing Backlog/Impediment cards for root cause first — fragmenting one problem across three cards makes triage worse, not better. |
| "This is basically my task, I'll just note it in the same card" | If it's genuinely a separate, addressable problem, it deserves its own card so it doesn't get silently dropped when this card closes. |
