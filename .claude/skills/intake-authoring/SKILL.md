---
name: intake-authoring
description: Use when a human wants to author a new app-idea or inceptie-kaart (free conversation → promotable intake-column kaart met spec+plan). Runs interactively outside the autonomous dispatcher. For a product-analyse Backlog-kaart (one external product / repo / URL comparison), use `product-analysis-card` instead.
---

# intake-authoring

Turn a free conversation about a new app-idea into a **promotable** kanban
intake-card: one card in the `intake` column of the meta-project with a
`spec`-deliverable (the design-doc) and a `plan`-deliverable (the
implementation plan) attached. Once the card is on the board, a human clicks
the existing **Promote** button (`create_project_from_intake`) to birth the
real project.

This skill is the **voordeur** of the inceptie-pipeline (gat A in
`docs/cockpit/product-inceptie-pipeline.md` §2.3). Decision:
[`docs/cockpit/intake-authoring-flow-decision.md`](../../../docs/cockpit/intake-authoring-flow-decision.md).

## When to use

- A human says "I have an idea for a new app / project / tool" and wants to
  formalise it as a Cockpit project — the conversation is the input.
- After a long chat, the human wants a portable design-doc + TDD plan that
  survives past the session.
- The output target is **the meta-project's intake column** (not a new
  project — that doesn't exist yet, by design).

## When NOT to use

- The human wants to capture a small change inside an existing project —
  that's `flag-problem` (for problems) or a normal Backlog card via the
  dispatcher. Don't route ordinary bug-fix ideation through here.
- The human points at one external product / repo / URL and asks *"vergelijk
  deze toepassing met de onze"*, *"wat kunnen we leren van X"*, or
  *"Product analyse - <url>"* — that is the `product-analysis-card` skill
  (Backlog-kaart in an existing project, no promote-flow). This skill is
  the inceptie-flow only.
- The idea already has a repo. Use `superpowers:brainstorming` + `writing-plans`
  directly, land the plan in the existing repo's `docs/plans/`, then file a
  normal Backlog card.
- The human wants the skill to **promote** the intake card into a project.
  That is the Promote button's job (`create_project_from_intake`). This skill
  stops at "promotable"; promotion is a deliberate human click.

## The contract — three artefacts, exactly once

1. **One card in the `intake` column of the meta-project** (`create_card` via
   the `cockpit-kanban` MCP server, `column="intake"`). The card's `title` is
   a short slug of the idea; `description` is a 2-4 sentence summary.
   `create_project_from_intake` reads `title` + `description` from this card
   and copies them onto the first Backlog card of the new project — so make
   them the *kern* of the idea.
2. **One `spec`-deliverable** carrying the brainstorming design-doc
   (`attach_deliverable(card_id, kind="spec", ref=<design-md-body>)`).
3. **One `plan`-deliverable** carrying the writing-plans plan
   (`attach_deliverable(card_id, kind="plan", ref=<plan-md-body>)`).

**Route for `kind="plan"` — verified working on a childless card.** The
analyst-style `add_plan_attachment` rejects childless cards
(`mcp_server.py:573`, requires `child_card_ids`). `attach_deliverable(kind="plan")`
is the intake-correct path: it only checks `ref` non-empty
(`mcp_server.py:349-372`, `schemas.py:205-211`) and accepts `kind="plan"`
on any card. Conventions §3 documents this; an in-session smoke test
(card `7b807d6a237c488ab603cfc4a7741670`, deleted) confirmed both `plan` and
`spec` land.

**Do NOT call `create_project_from_intake`.** Promotion is the human's click,
not the skill's.

## Flow

**Step 1 — confirm trigger and announce.** State out loud: "I'm using the
intake-authoring skill to turn this conversation into an intake card."
Briefly recap the contract (3 artefacts, no promotion) and ask the human to
confirm. If they wanted something else (e.g. just brainstorming for an
existing repo), stop and hand back to plain brainstorming.

**Step 2 — design-doc.** **REQUIRED SUB-SKILL:** Use
`superpowers:brainstorming`. Follow it through phase 1-4 (understanding →
exploration → design presentation → design documentation). The skill writes
its output to `docs/plans/YYYY-MM-DD-<topic>-design.md`. Read that file
back into context — that's the design-doc body you'll attach as `spec`.

**Step 3 — implementation plan.** **REQUIRED SUB-SKILL:** Use
`superpowers:writing-plans`. It writes the plan to
`docs/plans/YYYY-MM-DD-<feature-name>.md`. Read it back — that's the plan
body you'll attach as `plan`.

**Step 4 — resolve the meta-project key.** **Do not guess.** Use the
`cockpit-kanban` MCP `resolve_project_key` tool with this repo's working
directory. A hand-typed or guessed key creates an orphaned bucket invisible
from the real board (this is the lesson in `flag-problem` step 1, learned
the hard way). If MCP is unavailable, fall back to
`curl -s "http://localhost:8000/api/v1/kanban/project-key?project_path=$(git rev-parse --show-toplevel)"`.

**Step 5 — land the card + two deliverables.**

```
card = create_card(
    project=<resolved meta-project key>,
    column="intake",
    title="<short slug of the idea>",
    description="<2-4 sentence kern of the idea>",
)
attach_deliverable(card_id=card.id, kind="spec", ref=<design-md body>)
attach_deliverable(card_id=card.id, kind="plan", ref=<plan-md body>)
```

Stop here. The card is now promotable via the Promote button. Tell the human
the card id and that they can Promote whenever they're ready.

## Common mistakes

| Excuse | Why it's wrong |
|---|---|
| "I'll just call `create_project_from_intake` to finish the flow" | Promotion is the human's click. The skill hands them a promotable card; nothing more. |
| "I'll use `add_plan_attachment` to land the plan" | That tool rejects childless cards (`mcp_server.py:573`). Use `attach_deliverable(kind="plan", ...)`. |
| "The meta-project key is `claude-cockpit`, I'll type it" | Guessing creates an orphaned bucket. Resolve via `resolve_project_key` or the REST endpoint — every time. |
| "I'll skip the brainstorming approval gate, the user already agreed" | The approval gate is the entire reason `brainstorming` is reused. Section-by-section approval is what catches the "we built the wrong thing" failure mode. |
| "I'll create the card in Backlog, it's basically the same" | `intake` is the only column `create_project_from_intake` accepts (`inception_service.py:89`). Anywhere else, Promote fails. |

## Why interactive, not autonomous

Intake is human-werk by definition: the dispatch-loop never picks up `intake`
column cards (`_DISPATCH_COLUMNS = ("Backlog", "To Resume")`,
`docs/cockpit/kanban-conventions.md` §1). The brainstorming-approval gates are
many-turn conversational flows that don't compress into single-shot
`report_impediment` decisions (decision §4). Run this skill in a dedicated
interactive session with the human present.
