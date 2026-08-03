---
name: new-app
description: Use when a human says "ik heb een idee voor een nieuwe app/tool/project" (or the English "I have an idea for a new app / tool / project") and wants it to become a real Cockpit project. Runs an interactive interview (superpowers:brainstorming → user-approved design, superpowers:writing-plans → TDD-plan), writes it incrementally to a durable scratch dir, then births the project directly — no intake card, no Promote click. Resume a half-finished interview with `/new-app --resume <slug>`. Runs interactively outside the autonomous dispatcher.
---

# new-app

Turn a free conversation about a new app idea into a **real project on the
board**: a git repo with a seeded `.claude/`, the design + plan committed as
repo files, a registered `Project` row, and a first Backlog card — in one
interactive session.

Nothing lands on the meta-board in between. **The inceptie-flow is
cardless**: there is no intake card and no Promote click. The interview
writes to a durable scratch dir, and the birth happens through the
`create_project_from_interview` MCP tool.

This skill is the rewrite of the old `intake-authoring` skill, whose output
was a card in the `intake` column waiting for a human to Promote. That
carrier is gone (kanban card `1fa1b693…`;
`docs/cockpit/kaartloze-app-inceptie-decision.md` §3 revises
`intake-authoring-flow-decision.md` §3). What survives from that older
decision is §4.2 — the human-in-the-loop shape — and it still holds: see
*Approval gates* below.

## When to use

- A human says **"ik heb een idee voor een nieuwe app / tool / project"**
  (or the English equivalent) and wants it built as a Cockpit project. The
  conversation is the input; a live project is the output.
- A previous run of this skill stopped halfway — crash, closed terminal,
  failed birth. Resume it with `/new-app --resume <slug>`.

## When NOT to use

- The human wants a small change inside an **existing** project — that is
  `flag-problem` (for problems) or a normal Backlog card via the dispatcher.
- The human points at one external product / repo / URL and asks *"wat kunnen
  we hiervan leren"* — that is `product-analysis-card`.
- The idea already has a repo. Use `superpowers:brainstorming` +
  `superpowers:writing-plans` directly, land the plan in that repo's
  `docs/plans/`, and file a normal Backlog card.
- You are an **autonomously dispatched** session. This skill is interactief
  by construction (see *Approval gates*); a dispatched session has no human
  to approve design sections.

## Approval gates — native interactive, never `report_impediment`

`brainstorming`'s approval is a many-turn conversation: section-by-section
approval, revise-and-re-present, spec review. `report_impediment` is the
opposite — one question, a fixed option list, and it **ends the session**.
You cannot squeeze a free design dialogue through it without killing and
restarting the session dozens of times.

> The interview runs **native interactive**, with the human present. The
> approval gates stay the sub-skills' own gates. `report_impediment` is
> **never** used for the dialogue — not for a design section, not for a plan
> step. (`intake-authoring-flow-decision.md` §4.2, still valid.)

## The scratch dir — durable and incremental

Every run owns one directory:

```
~/.claude-registry/interviews/<slug>/
    design.md     # the approved design so far
    plan.md       # the approved plan so far
    state.json    # where the run is
```

It lives in `~/.claude-registry/` on purpose: outside every git repo, so it
survives a crashed session, a closed terminal, a `git clean`, and a worktree
that gets reaped. `<slug>` is the project's working name, lower-cased, with
every non-alphanumeric run collapsed to a single dash.

**Write after each approved section — not at the end.** Every time the human
approves a design section, rewrite `design.md` with everything approved so
far and update `state.json`. Same for the plan. A run that writes only at the
end loses the whole interview when the session dies, which is exactly what
this directory exists to prevent.

Override the sub-skills' default output locations to these two files.
`brainstorming` defaults to `docs/superpowers/specs/…` and wants to commit;
`writing-plans` defaults to `docs/superpowers/plans/…`. Neither applies here:
the scratch dir is not a git repo, and nothing from the interview belongs in
the Cockpit repo. The durable copy is made by the birth, which commits both
files into the **new** project's repo.

### `state.json`

```json
{
  "slug": "recipe-box",
  "project_name": "Recipe Box",
  "target_path": "/home/vdvgu/projects/recipe-box",
  "phase": "interview",
  "last_approved_section": "3. Datamodel",
  "title": "Recipe Box — eerste werkende versie",
  "description": "2-4 zinnen kern van het idee.",
  "updated_at": "2026-08-03T21:14:00Z"
}
```

| Key | Meaning |
|---|---|
| `slug` | Directory name; derived from `project_name` at step 1. |
| `project_name` | Working name of the project. Known from step 1. |
| `target_path` | Absolute path for the new repo. `null` until step 4. |
| `phase` | `interview` → `ready_for_birth` → `born`. |
| `last_approved_section` | Heading of the last section the human approved (`null` before the first one). This is where `--resume` picks the dialogue back up. |
| `title` / `description` | For the first Backlog card of the new project. `null` until step 4. |
| `updated_at` | UTC ISO 8601, rewritten on every write. |

`phase` is the only field `--resume` branches on, so it must be accurate at
every moment — write it *before* the step it describes, never after.

## Step 0 — read the invocation

- `/new-app` — a fresh run. Go to step 1.
- `/new-app --resume <slug>` — read
  `~/.claude-registry/interviews/<slug>/state.json` and branch on `phase`
  (see *Resume semantics*). If the directory or `state.json` is missing, say
  so and list the slugs that do exist; do not silently start a fresh run.

A fresh run whose slug already exists is **not** a fresh run — the human is
almost certainly resuming. Show the existing `state.json` and offer
`--resume` instead of overwriting it.

## Step 1 — announce, name, and open the scratch dir

Say out loud: *"I'm using the new-app skill: interview → new project. No
intake card — the project is born directly at the end."*

Ask for a working name for the project (one question, per the sub-skill's
dialogue discipline). Derive `<slug>` from it, create
`~/.claude-registry/interviews/<slug>/`, and write `state.json` with
`phase: "interview"`, `project_name`, and `last_approved_section: null`.

## Step 2 — design

**REQUIRED SUB-SKILL: `superpowers:brainstorming`.** Run it through
understanding → exploration → design presentation → design documentation.
Two overrides:

1. The design doc is `~/.claude-registry/interviews/<slug>/design.md`. Do not
   write into `docs/superpowers/specs/` and do not commit anything to this
   repo.
2. After **each approved** section: rewrite `design.md` and set
   `last_approved_section` + `updated_at` in `state.json`.

## Step 3 — plan

**REQUIRED SUB-SKILL: `superpowers:writing-plans`.** Same two overrides: the
plan is `~/.claude-registry/interviews/<slug>/plan.md`, rewritten after each
approved section together with `state.json`.

## Step 4 — collect the birth inputs, then flip to `ready_for_birth`

Ask for, and write into `state.json`:

- `target_path` — absolute path for the new repo. It must **not** exist yet;
  the birth refuses to clobber.
- `title` + `description` for the first Backlog card of the new project — the
  kern of the idea in 2-4 sentences. These are what the first executor reads.

Then set `phase: "ready_for_birth"`. Do this **before** calling the birth, so
a session that dies mid-call resumes into "retry the birth" instead of "redo
the interview".

## Step 5 — birth

Call the `cockpit-kanban` MCP tool:

```
create_project_from_interview(
    project_name=<state.project_name>,
    target_path=<state.target_path>,
    title=<state.title>,
    description=<state.description>,
    spec_md=<body of design.md>,
    plan_md=<body of plan.md>,
)
```

Do **not** call the old `create_project_from_intake` — that is the
card-carried route, and it needs an intake card this flow deliberately never
creates. Do not create a card in the `intake` column either. There is no
Promote step.

The tool returns `{"project_id", "new_project_key", "first_card_id"}` on
success, or `{"error": …, "message": …}` on failure. It is atomic: on failure
it has already rolled back the directory, the `Project` row, the autodispatch
meta and the partial card, so nothing is half-registered.

| `error` | What happened | What to do |
|---|---|---|
| `validation_failed` | Empty `spec_md`/`plan_md`, or a `Project` row already registered at `target_path`. | Check the scratch files are non-empty; pick another path. |
| `target_path_exists` | The directory already exists. | Ask for a fresh path, update `state.json`, retry. |
| `scaffold_failed` | `git init`, blueprint apply or the first commit failed. | Report the message verbatim; the target dir was already removed by the rollback. |

**On any failure blijft de scratch-map staan** — the dir survives exactly
where it is. Report the directory path and the literal resume command
(`/new-app --resume <slug>`) so the human — or the next session — picks up
from `ready_for_birth` without redoing the interview.

## Step 6 — copy-then-delete

Only after a **fully successful** birth, in this order:

1. Write `phase: "born"` into `state.json`, plus `new_project_key` and
   `first_card_id` from the response.
2. Verify the copy landed: `git -C <target_path> ls-files docs/specs docs/plans`
   must list the committed design + plan. That commit is the durable copy;
   until you have seen it, the scratch dir is still the only copy.
3. Only then remove the scratch dir — **copy-then-delete**, never the other
   way round. The removal happens pas na een volledig geslaagde geboorte
   (only after a successful birth has been verified).

Removal is a `mv`, not an `rm`: `rm` is deny-listed repo-wide
(`.claude/settings.json`), and CLAUDE.md prescribes `mv` for exactly this.

```bash
mv ~/.claude-registry/interviews/<slug> \
   ~/.claude-registry/interviews/.trash/<slug>-$(date -u +%Y%m%dT%H%M%SZ)
```

`.trash/` is dot-prefixed, so `--resume` and any sweeper that lists slugs
skip it. Retention of `.trash/` is a separate card's business, not this
skill's.

## Step 7 — report

Tell the human, in one short block:

- the **new project path** (`target_path`) and that the repo is
  git-initialised with `.claude/` seeded and the design + plan committed;
- the **`new_project_key`** (`project_key`) — the bucket the new board lives
  in;
- the **`first_card_id`** — the first Backlog card of the new project;
- the **autodispatch** state. Autodispatch is **uit** (off) at birth: the
  birth flips it from `BootstrapPolicy.autodispatch_default`, which is
  `False` (security-default-deny,
  `backend/app/services/bootstrap_policy.py:77`). Say so plainly, and tell
  the human to enable autodispatch for the new project when they want that
  first card to start running. Report what the birth actually set — never
  claim it is on.

Then stop. The new project's own board takes over from here.

## Resume semantics

| `phase` | `/new-app --resume <slug>` does |
|---|---|
| `interview` | Continue (verder) the dialogue. Read `design.md` / `plan.md` back into context and pick up at `last_approved_section` — do not restart at the first question, and do not re-ask what is already approved. |
| `ready_for_birth` | Retry alleen de geboorte (only the birth): steps 5-7. The interview is finished; never re-run it. |
| `born` | The birth already succeeded, so the delete in step 6 must have failed. Verify the project exists at `target_path`, then finish step 6's `mv`. Do **not** attempt a second birth — `target_path` exists and the tool would refuse to clobber. |

## Common mistakes

| Excuse | Why it's wrong |
|---|---|
| "I'll write `design.md` once at the end, it's cleaner" | The scratch dir exists to survive a dead session. A single end-of-run write loses the entire interview. |
| "I'll delete the scratch dir right after calling the birth" | Copy-then-delete. Until the commit in the new repo is verified, the scratch dir is the only copy. |
| "The birth failed, I'll clean up the scratch dir" | No. A failed birth is exactly when the dir must survive — that is what `--resume` reads. |
| "I'll use the old `create_project_from_intake`, it's the flow I know" | It needs an intake card. This flow deliberately creates none; the cardless route is `create_project_from_interview`. |
| "I'll drop the idea on an `intake`-column card as a backup" | That reintroduces the carrier this rewrite removed, and nothing promotes it. The scratch dir *is* the backup. |
| "I'll ask the design-approval question via `report_impediment`" | That ends the session mid-interview. Approval stays native interactive (§4.2). |
| "I'll report that autodispatch is on" | It is off at birth by policy. Reporting it as on sends the human away expecting work that never dispatches. |
| "`rm -rf` the scratch dir is simpler" | `rm` is deny-listed in this repo. Use `mv` into `.trash/`. |
