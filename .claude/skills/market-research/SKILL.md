---
name: market-research
description: Use when researching competitor open-source agent platforms, ecosystem trends, or new techniques — and converting findings into concrete, scoped Backlog kanban cards. Provides a fixed external sources list, a filter+dedupe gate against existing cards, and a card template that forces acceptatiecriteria-niveau scope.
---

# market-research

You are doing a periodic outward-looking pass: scanning a fixed list of
competitor repos, GitHub topics, and changelogs for changes that might inform
Cockpit's roadmap. The output of this skill is NOT a long report — it's 1–N
concrete, scoped Backlog cards that a future engineer (or analyst) can act on
without re-doing your research. "I read 15 sources and here's a wall of text"
is failure; "I read 4 sources and filed 2 cards with acceptance criteria" is
success.

This is the **outward-facing, periodic** companion to:

- `flag-problem` — in-session, self-noticed problem → Backlog card
- `session-problem-scan` — on-demand sweep over past transcripts → Backlog cards

Use this when **you** are the trigger (you / a human / a scheduled job decided
it's time to scan the ecosystem). The cadence itself is a separate concern —
see sibling Backlog card *"Terugkerende cadans voor het
zelfverbeteringsonderzoek voorstellen"*.

## When to use

- A periodic sweep is due (weekly cadence TBD; see sibling card).
- A new release of a competitor / adjacent tool appeared that might affect
  Cockpit's roadmap.
- A human asks "what's new in <space>?" and the answer should become
  trackable work, not just a verbal reply.

## When NOT to use

- The finding is *about* a specific bug in *this* repo — that's `flag-problem`.
- You found a stuck session / repeated transcript error — that's
  `session-problem-scan`.
- You want to read 1–2 web pages to inform a single in-progress card — use
  `WebSearch`/`WebFetch` directly, no skill needed. Skills exist to
  standardise recurring loops, not to wrap every web fetch.
- You want to validate a single concrete idea against the world ("should we
  build X?") — that's a single-card plan, not a research sweep; just file the
  card with whatever evidence you have.

## Step 1 — get the *real* project key first

Same gotcha as `flag-problem` Step 1: kanban cards are bucketed by a free-form
`project` string with no validation. A typo or a differently-derived key
creates an invisible parallel board; a card filed under any other string is
orphaned and dedupe against it silently misses.

Resolve it for real, don't guess. If the `cockpit-kanban` MCP server is
available, call its `resolve_project_key` tool with this repo's working
directory — this works even for MCP-only agents with no shell/HTTP access.
Otherwise, with shell access:

```bash
curl -s "http://localhost:8000/api/v1/kanban/project-key?project_path=$(git rev-parse --show-toplevel)"
```

Use that exact returned string as `project` in every `list_cards` /
`create_card` call below.

## Step 2 — pull the external sources list

Pull each source below. Stop and assess **before** moving to Step 3 — if
nothing changed since the last run, end here with a "no findings" record
(see Step 6).

| Source | What to extract |
|---|---|
| `claude-task-master` — releases, README, `.claude/`, `src/` | New `research`-tool enhancements, complexity-analysis changes, supported AI providers, MCP integrations |
| `Aperant` / "Auto Claude" — releases, ideation-phase outputs | New "Ideation"-phase categories, worktree-isolation tweaks, auto-changelog patterns |
| `claude-deck` — this fork's upstream (releases + `master` commits since last sweep) | Anything that didn't make it across via the fork — divergence check |
| GitHub topic `ai-agent` (>1k★, created last 30d) | New repos worth a glance |
| GitHub topic `autonomous-coding` (same filter) | New repos worth a glance |
| GitHub topic `ai-coding-agents` (same filter) | New repos worth a glance |
| Anthropic / OpenAI / Google SDK changelogs | New agent-loop primitives, prompt-caching, structured output that change how a CC session can be scripted |
| LangChain / LangGraph release notes | New agent-orchestration primitives worth comparing against Cockpit's `cc-bridge` / `agent-bridge` |

Use `WebFetch`/`WebSearch` to query each. The list is deliberately short —
extend it deliberately with a one-line rationale, not exhaustively. Adding a
source without a reason turns the skill into a web crawler.

## Step 3 — filter for Cockpit relevance

Each source dump is 90% noise. Filter aggressively:

1. **Actionable** — implies a concrete code change, library swap, or new
   feature for *this* repo, not "X is interesting in general".
2. **Scope-checked** — touchable in a single engineer-session (~1–5 files, or
   one small feature card). Bigger ideas still get filed, but as a *parent*
   feature card with an explicit "needs decomposition" callout, not as a
   single muddy card.
3. **Not already covered** — handed off to Step 4 for the actual check.

If nothing is actionable, end here. A no-finding run is a legitimate outcome
(it means "ecosystem is stable, no roadmap movement this week"). Do NOT
manufacture fake cards to make the run feel productive.

## Step 4 — dedupe against existing cards

```
list_cards(project=<resolved key>, column="Backlog")
list_cards(project=<resolved key>, column="Impediment")
```

Read titles and descriptions for the **same underlying idea**, not the same
keyword. A card titled "Add research-tool wrapper" and one titled "Market
research automation" might describe the same idea.

- **Duplicate found** → `comment(card_id, text)` with what's new: source URL,
  the specific finding, and a one-line note about how it refines /
  strengthens / contradicts what's already there. Don't file a second card.
- **No duplicate** → continue to Step 5.

## Step 5 — file 1–3 Backlog cards

For each actionable, non-duplicate finding, create exactly one card.
Target: **1–3 cards per run**. More than that means the filter is too loose;
zero means the run was a no-op (also legitimate — see Step 3).

`create_card(project=<resolved key>, column="Backlog", title=..., description=...)`

- **title**: `[research] <one-line summary>` — short, specific, searchable.
- **description**, structured for fast triage:

```markdown
## Finding
<1–2 sentences — what the external source changed, and why it matters for Cockpit>

## Source
- URL: <github release / blog post / changelog>
- Captured at: <YYYY-MM-DD>
- Reference project(s): <claude-task-master, Aperant, claude-deck, ...>

## Why it matters for Cockpit
<Concrete gap or opportunity in this repo — file path, feature, or dependency
that the change would touch>

## Suggested next step
<Concrete first action — what an engineer would do to act on this. "Investigate
further" alone is not enough; say what to read / build / compare.>

## Acceptance criteria
<1–3 bullets, scoped to a single engineer-session. Same level of specificity
as the cards already in column="Backlog".>
```

## Step 6 — record the run

After each run, even a no-finding one, leave a short breadcrumb so the next
run knows where to start from. Pick whichever fits the deployment:

1. **Comment on this skill's host card** (the kanban card that invoked the
   skill): date, sources pulled, # actionable findings, # filed / # deduped /
   # no-op.
2. **Update `.claude/state/research-last-run.json`** in the worktree:
   ```json
   { "last_run": "YYYY-MM-DD", "sources_seen": { "<repo>": "<sha>" } }
   ```
   Lighter weight, no DB roundtrip, but tied to a worktree.

Pick option 2 if a scheduled/cron-driven agent will run this soon (no human
to read comments). Pick option 1 if a human triages the runs. The two can
coexist; the breadcrumb is the goal, the mechanism is interchangeable.

## Step 7 — schedule the next run (chain-of-one-shots)

**Only do this step if the host card that opened you has a "Step 7 — schedule
the next run" instruction in its description.** The recurring-cadence proposal
(`docs/cockpit/recurring-cadence-proposal.md`) attaches this prompt to its
weekly trigger cards; if your card doesn't have it, this step does not apply
to you (a human or a one-off scheduled-message opened you).

When it does apply, the **last** action before you move your host card to
Done is to create the **successor** card that will open next week's run —
otherwise the chain dies and the next week has to be re-seeded by hand.

1. Resolve the project key via Step 1 — never guess (see Common mistakes).
2. Compute `next_scheduled_at` = next Monday 09:00 in the project's default
   timezone (`Europe/Brussels`), expressed as an ISO-8601 string with offset.
   Compute from "now + 7 days, then snap forward to the next Monday 09:00"
   so the chain self-corrects after a missed run rather than drifting
   forward by exactly 7 days.
3. Call `create_card` (or REST `POST /api/v1/kanban/cards`) with the same
   `parent_card_id` as your host card, the same `project`, `work_type`,
   `agent`, and `labels`, and `scheduled_at = next_scheduled_at`. **Always
   include `parent_card_id`** — without it, the successor is an orphan
   with no link back to the cadence proposal and the audit trail in the
   kanban activity feed is broken.
4. The successor's `description` is a verbatim copy of the host card's
   description (which already includes the "Step 7 — schedule the next run"
   instruction). One typo and the chain dies — copy carefully, or build the
   description from a known-good template.
5. Do this even if your run filed zero Backlog cards (zero-finding is
   legitimate per Step 3) — a dead chain is worse than a no-op run.

The chain ends when a human deletes the host card (or pushes its
`scheduled_at` past the successor's `scheduled_at` and then deletes both).
A per-card `enabled` field does not exist on `KanbanCard` — pause by
removing or far-futuring the card; do not search for `enabled=false`.

## Common mistakes

| Excuse | Why it's wrong |
|---|---|
| "I read 15 sources and didn't find anything — let me file 1 card anyway" | Zero-finding is a legitimate outcome. Don't manufacture work. |
| "I'll just dump all findings in a comment on my own card" | The skill's value is structured Backlog cards that survive triage; comments on the running card die with it. |
| "I made a card titled 'Investigate claude-task-master' with no acceptance criteria" | "Investigate X" is the antithesis of a scoped card. Use the template's *Why-it-matters* + *Acceptance-criteria* sections. |
| "I'll fetch every GitHub repo in the ai-agent topic" | The sources list is deliberately short. Extend it deliberately with a one-line rationale — never as part of a single run. |
| "Let me create 5 backlog cards, the backlog is the queue anyway" | Backlog is finite attention. 1–3 high-quality > 5 mediocre. If you genuinely have 5+, file the 3 best and comment-list the rest on the parent card for a future run. |
| "I'll skip dedupe — I read the Backlog already in a previous run" | Backlog mutates between runs (other cards get filed / closed). Always re-pull. |
| "I guessed `git:github.com/<owner>/<repo>` for the project key" | Resolve via Step 1, every time. A guessed key silently orphans the cards. |

## Quick reference

```text
Pull sources  →  filter for actionable  →  dedupe vs Backlog+Impediment
              →  file 1–3 Backlog cards (per template)
              →  record the run (comment or .claude/state/research-last-run.json)
              →  (Step 7 only if the host card asks) create the successor card
```

Acceptance gate for the run: at least one source pulled, filter pass done,
dedupe checked, decision recorded (cards filed OR explicit no-op), and —
if the host card invoked Step 7 — successor card created with the right
`parent_card_id`. Anything less is a half-run — restart from Step 2.