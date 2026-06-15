# Kanban dispatch agents — design

**Date:** 2026-06-15
**Status:** Approved (design); ready for writing-plans
**Builds on:** `docs/cockpit/kanban-dispatch-spec.md` (auto-dispatch), `backend/app/kanban/dispatch.py`

## Problem

The auto-dispatcher already claims an unclaimed **Todo** card, moves it to **Doing**, and
spawns a Claude session in a worktree. But the spawned session has no persona and only a
thin generic prompt, so:

1. It is not strongly steered toward a concrete, repeatable workflow.
2. It is not autonomous — it stalls on permission prompts and has no defined terminal state.
3. It has no safe, project-appropriate way to *ship* its result. The user works with
   **branch protection** at work (no direct pushes to master) but wants direct
   merge+push for personal projects.

## Goals

- A card picked up from the board is **completed end-to-end without human intervention**.
- Two distinct, well-defined personas mapped to the board's columns.
- A **per-project ship mode** so the terminal "ship" step matches the project's policy:
  direct-to-master for personal repos, **draft pull request** for protected repos.
- Personas live as **editable files under version control**, not hardcoded in Python.

## Non-goals

- Replacing the tmux transport (podman wrapper is a separate, later effort).
- Syncing the per-project dispatch/ship config across devices (stays device-local in
  `KanbanMeta`, like the existing autodispatch flag). A follow-up if needed.
- A general agent-authoring UI. Personas are markdown files edited by hand for now.

## Design

### 1. Two agents, mapped to columns

The dispatcher selects the persona from the **source column** of the claimed card.

| Source column | Persona file | Deliverable | Terminal action |
|---|---|---|---|
| **Analysis** | `.claude/agents/kanban-analyst.md` | Decompose the problem: create new **Todo** cards via `cockpit-kanban` `create_card`, each with scope / approach / acceptance criteria documented. | Source card → **Done** + `comment` linking the created card ids. |
| **Todo** | `.claude/agents/kanban-developer.md` | Implement in the worktree, run the test suite, and — only on green — ship per the project's ship mode. | Card → **Review** + `attach_deliverable` (commit / branch / PR url). On failure: `comment` with the failing output, card stays in **Doing** (no merge / no PR). |

The dispatcher must therefore poll **both** Analysis and Todo columns for unclaimed cards
(today it only polls Todo). Analysis cards get the analyst; Todo cards get the developer.

### 2. Autonomy

- **`skip_permissions=True`** when spawning (today the transport hardcodes `False`).
  Without it the session blocks on the first tool prompt and cannot be autonomous. This is
  the deliberate trade-off the "as autonomous as possible" requirement demands.
- **Self-driving opening prompt**: persona body (read from the agent file) + card context
  + explicit **terminal conditions**. The agent knows the card is already claimed by it and
  already in Doing, does the work, and ends in one well-defined state.
- **No human-in-the-loop**: failure paths are also terminal (comment + leave card in Doing),
  so the agent never hangs waiting for input.

### 3. Personas as files

```
.claude/agents/kanban-analyst.md      # analyst persona + instructions
.claude/agents/kanban-developer.md    # developer persona + instructions
.claude/skills/git-ship/SKILL.md      # safe test -> ship procedure (both modes)
```

The dispatcher **reads the body** of the matching agent file and injects it as a preamble
ahead of the card context in the spawn prompt. Personas are tuned without touching Python
and are versioned with the repo. If a file is missing, the dispatcher falls back to the
current generic prompt and logs a warning (degraded, not broken).

### 4. Two ship modes — per project

Stored as `shipmode:<project_key>` in `KanbanMeta` (device-local, alongside the existing
`autodispatch:<project_key>` flag). **Default = `pull-request`** — the safe side, so a
project never accidentally auto-pushes to master; the user flips their own projects to
`direct` as a conscious opt-in.

| Mode | For | Developer-agent terminal ship step |
|---|---|---|
| **`direct`** | personal projects | tests green → merge worktree branch into master → `git push origin HEAD:master` → card → **Review** |
| **`pull-request`** | work / branch protection | tests green → push branch to origin → **`gh pr create --draft`** → PR url via `attach_deliverable` → card → **Review** |

Both modes:
- Start the worktree from **`origin/master`** (see §6).
- Push **only to origin** (`guillaumevandevelde/claude-cockpit`), never upstream, never `--force`.
- On **red tests**: no merge / no PR in either mode — `comment` with the failing output,
  card stays in **Doing**.

The dispatcher reads the mode and injects it into the prompt preamble
(`Ship mode: pull-request`). The `git-ship` skill documents both paths and branches on the
injected value.

### 5. git-ship skill

`.claude/skills/git-ship/SKILL.md` encodes the fixed, safe-to-run-unattended procedure the
developer agent invokes:

1. `git fetch origin`.
2. Run the test suite (backend pytest from the worktree's own backend dir — venv lives in
   the main checkout; frontend lint/build where relevant).
3. **Only if fully green**, branch on ship mode:
   - `direct`: merge the worktree branch into master and `git push origin HEAD:master`.
   - `pull-request`: check `gh auth status`; push the branch to origin; `gh pr create --draft`;
     attach the PR url to the card.
4. On red tests: do **not** merge / PR — comment on the card with the failure, leave it in Doing.
5. Prerequisite for `pull-request`: the **`gh` CLI must be authenticated**. If `gh auth status`
   fails, fail safe — no merge, comment "gh unavailable — manual PR needed", card stays in Doing.

The skill also encodes the rule that any further worktree branches from `origin/master`.

### 6. Worktree from `origin/master`

Today the transport uses `claude --worktree <name>`, which branches from the **local HEAD** —
wrong when the local checkout is behind or dirty. New design: the **dispatcher creates the
worktree itself** from `origin/master`:

1. `git -C <project_path> fetch origin`.
2. `git -C <project_path> worktree add <worktree_path> origin/master` (new branch off origin/master).
3. Spawn Claude in `mode="plain"` with `directory=<worktree_path>` (instead of letting the
   provider create the worktree from local HEAD).

Consequence: worktree cleanup (`kill_session(..., cleanup_worktree=True)`) currently keys off
the provider's `worktree_name` metadata for `mode="worktree"`. Since we move to dispatcher-owned
worktrees, the spawn metadata must still record the worktree path so cleanup can
`git worktree remove` it. Resolve this in the plan (either keep `mode="worktree"` with a base-ref
option, or extend the plain-mode metadata to carry the worktree path).

### 7. Dispatcher changes (`backend/app/kanban/dispatch.py`)

- Poll **Analysis** and **Todo** for unclaimed cards (not Todo only).
- Persona selection by source column → read the agent file body → prompt preamble.
- `build_card_prompt` takes persona body + ship mode + terminal conditions.
- Transport: `skip_permissions=True`; worktree created from `origin/master` (fetch + add).
- Read `shipmode:<project_key>` from `KanbanMeta`; inject into the prompt.

### 8. API + UI

- `KanbanMeta`-backed get/set for ship mode, mirroring the autodispatch endpoints
  (`GET/POST /kanban/shipmode`).
- Frontend: a small selector next to "Auto-pick" — *Ship: draft PR* / *Ship: direct to master* —
  with an orange/warning hint on `direct` so it reads as "sharp".

## Risks & mitigations

- **Unattended pushes** — bounded by the ship mode: `direct` only on the user's own projects,
  default is the safe draft-PR path. Green tests are a hard precondition for any merge/PR.
- **`gh` not authenticated** — fail safe, card stays in Doing with an explanatory comment.
- **Worktree from stale local HEAD** — eliminated by fetching and branching from `origin/master`.
- **Missing persona file** — degrade to the generic prompt + warning, do not crash the tick.
- **Cleanup of dispatcher-owned worktrees** — explicitly called out for the plan (§6).

## Open implementation questions (for writing-plans)

1. Worktree ownership vs. `kill_session` cleanup metadata (§6).
2. Exact test command(s) per project — fixed (backend pytest + frontend lint) vs. configurable.
3. Whether `gh pr create --draft` base is always `master` (assume yes for now).
