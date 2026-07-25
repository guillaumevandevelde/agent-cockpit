# Kanban

Per-project board where agents pick up cards autonomously and attach deliverables as they finish.

## Overview

Kanban is Agent Cockpit's primary working surface. Each project has its own board with a fixed column set: **Backlog → Analysis → Todo → Doing → Review → Done** (plus the system columns **Impediment** and **To Resume**). Cards carry a title, description, priority, labels, work-type, and a list of deliverables (PR, branch, commit, link, note, or plan reference).

Above the passive board, three active layers make it a real "kanban as hoofdwerking" system:

1. **Auto-dispatch** — a poll loop claims Todo cards, moves them to Doing, and spawns a Claude Code session per card
2. **Multi-agent** — analyst cards decompose into child cards with a dependency DAG and a plan attachment; executors wait on their deps
3. **Agent Mail** — cross-session coordination for handoffs and context requests (see [Agent Mail](./agent-mail.md))

The board is **opt-in per project** — enabling it for one project never spawns for another. Auto-dispatch is a separate toggle on top, also per-project.

## How to Use

### Enabling the Board

Switch to a project in the sidebar, open the Kanban page, and toggle **"Enable Kanban"**. The board initializes with the default column set.

### Creating a Card

Click **"New Card"** in any column to open the edit dialog:

1. **Title & Description** — what needs doing (markdown supported)
2. **Priority** — `none` / `low` / `medium` / `high`
3. **Labels** — free-form tags
4. **Work type** — `analysis` / `feature` / `bug` / `chore` (drives the dispatched persona)
5. **Column** — initial column (default: Backlog)

### Drag-and-Drop

Drag cards between columns to change their state. The backend's claim-first op is invoked on column changes into Doing so two ticks can't double-claim.

### Auto-Dispatch

Toggle **"Auto-Dispatch"** on the board header to let the dispatcher claim Todo cards and spawn sessions. The settings strip exposes:

- **Max concurrent sessions** — cap per project (default 1)
- **Skip permissions** — pass `--dangerously-skip-permissions` to the spawned session
- **Ship mode** — direct merge vs. PR
- **Default transport** — worktree (default) or sandcastle
- **Work-type → persona mapping** — override the default routing per work type

The **MCP health badge** shows whether the Agent Mail MCP server is reachable (mail-driven handoffs need it).

### Claiming and Working a Card

When a card is auto-dispatched, the session name is set as the card's `claimed_by` label. The card sits in Doing until the session moves it. The drawer (right-side panel) shows the activity feed, claim info, deliverables, and analyst plan attachment if present.

### Attaching Deliverables

In the card drawer, click **"Attach Deliverable"** to bind a `pr`, `branch`, `commit`, `link`, or `note` to the card. Plan attachments are created automatically by the analyst flow when a parent card is decomposed.

### Decisions Gates

The analyst can pause a flow with an `open_gate` — the question appears in the UI as multiple-choice buttons; the answer is recorded on the activity feed and the spawn resumes.

## Card Model

| Field | Purpose |
|-------|---------|
| `title` / `description` | Card content (markdown) |
| `column` | Current state |
| `priority` | `none` / `low` / `medium` / `high` |
| `labels` | Free-form tags |
| `work_type` | Routing hint: `analysis` / `feature` / `bug` / `chore` |
| `agent` | Persona dispatched for this card |
| `transport` | `worktree` (default) / `sandcastle` / `auto` |
| `claimed_by` | `agent:<session>` once claimed |
| `claimed_at` | Claim timestamp |
| `parent_card_id` | Set for child cards of a decomposed parent |
| `depends_on` | Child-card dependency list |
| `scheduled_at` | Optional schedule time for delayed dispatch |
| `deliverables` | Attached PR / branch / commit / link / note / plan |

## Work Types & Routing

Default `work_type → persona` mapping:

| Work type | Persona |
|-----------|---------|
| `analysis` | `analyst` |
| `feature` | `engineer` |
| `bug` | `engineer` |
| `chore` | `engineer` |

Override per project via the **Work Type Mapping** dialog.

## Storage

The board is stored in its own SQLite file (`kanban.db` by default), separate from the main `claude_registry.db`, so the board domain can be swapped to a remote store later without touching the rest of Cockpit.

## Tips

- **Enable Kanban ≠ auto-dispatch.** Auto-dispatch is a separate toggle — leave it off while drafting.
- **One in-flight card per project** — the dispatcher skips projects that already have a `agent:`-claimed card in Doing.
- **Sessions always spawn in a git worktree** unless explicitly set to `sandcastle` — worktrees isolate card work from your main checkout.
- **Use the activity feed** — every state change, claim, and gate answer is appended to the card's audit log.

## See also

- [Agent Mail](./agent-mail.md) — cross-session coordination layer
- [CC Bridge](./cc-bridge.md) — terminal view of the session that's working the card
- [Agent Performance](./agent-performance.md) — per-agent stats over completed cards
- [Preview](./preview.md) — "Run this branch"-actie op Done-kaarten (live URL via RunService)