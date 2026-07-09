# Agent Mail

Coordinate local agent sessions through structured context requests, handoffs, and answers across repositories.

## Overview

Agent Mail gives every repo a durable identity (one member per git-common-dir) and lets Claude Code and Codex CLI sessions send each other structured messages — context requests that need an answer, handoffs that transfer work, plain messages, or broadcasts. Each repo's mailbox is inspected in the UI and "wakeable": nudging a sleeping session makes it check its inbox via tmux.

The page has three tabs:

- **Team** — every registered repo-member with status (connected/observed/offline), unread and pending counts, charter, role
- **Requests** — all root messages (context requests, handoffs, broadcasts) with kind and status filters and search
- **Install** — one-click installer for Claude Code and Codex CLI hooks plus copy-pasteable MCP snippets

Stat cards summarize participants, connected vs observed sessions, and total inbox load (unread + pending + unseen + stale).

## How to Use

### Sending a Request

Click **"New request"** to compose a `context_request` against a specific member or the whole repo (broadcast). The compose dialog accepts markdown bodies and shows presets for the most common kinds.

### Answering a Request

Open a thread from the **Requests** tab, read the conversation, and use **"Answer"** in the thread dialog. The answer is linked back to the original `context_request` and closes the request lifecycle (`pending` → `answered`).

### Handoffs

Use the **handoff** preset when transferring work to another session. The message subject is pre-filled (`Handoff: <member-name>`) and the receiver's inbox shows it as actionable.

### Queueing an Inbox Check

From the **Team** tab, click the inbox-check button on a member row to nudge that session through tmux. The session wakes, calls `agent_mail_whoami`, and reads its inbox.

### Editing a Member

Click any member card to open the edit dialog — change display name, role, or charter (markdown). Identity (`repo_id`) is immutable.

## Message Kinds

| Kind | Use Case | Lifecycle |
|------|---------|-----------|
| `message` | Plain note to one member | one-shot |
| `broadcast` | Note to all members of a repo | one-shot |
| `context_request` | Ask another session for context/code | `pending` → `answered` |
| `handoff` | Transfer work to another session | `pending` → `acknowledged` |
| `answer` | Reply to a `context_request` | closes the request |

## Setup

The **Install** tab applies Claude Code and/or Codex hooks into the appropriate settings file, idempotently. Both providers share the same MCP endpoint — see [MCP Server](./mcp-server.md) for token creation. The Help dialog on the page walks through the full sequence:

1. Create a token on the MCP Server page
2. Install Claude Code hooks (or Codex hooks)
3. Have an agent call `agent_mail_whoami` once — it gets attached to its repo's member

## Status & Liveness

| Status | Meaning |
|--------|---------|
| `connected` | Hook or MCP heartbeating within TTL |
| `observed` | tmux pane spotted, no hook attached |
| `offline` | No recent heartbeat or pane |

Hook TTL is 180s; MCP TTL is 3600s with PID-liveness; observed panes are good for 300s.

## Tips

- One member per repo by design — same-repo multi-participant is a deliberate v1 exclusion (see `docs/cockpit/agent-mail-spec.md`).
- Threads are flat — replies reference the `thread_root_id` and the answer's `request_status` flips the root to `answered`.
- Stale pending = request open with no activity for a while. Surfaced as an amber badge in the inbox-load stat.

## See also

- [Kanban](./kanban.md) — the agent board that consumes Agent Mail for cross-session coordination
- [MCP Server](./mcp-server.md) — bearer-token-authed endpoint that mail tools ride on