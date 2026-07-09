# Presence

Real-time file-change and event stream for active agent sessions, surfaced as live cards.

## Overview

Presence watches active Claude Code and Codex CLI sessions through hooks and reports each session as a live card showing the most recent file changes, status, and activity timeline. It complements [CC Bridge](./cc-bridge.md) (which is tmux-pane oriented) by tracking what's actually happening inside the session at the file-system level — what files were read, what was edited, when, and in what order.

The page updates in real time over a WebSocket and falls back to polling when the socket disconnects. Cards are sorted by status (active > error > idle > stopped).

## How to Use

### Connecting Presence Hooks

On first load, the page checks whether presence hooks are installed. If not, follow the on-screen setup notes — typically a one-time edit to `~/.claude/settings.json` (or the Codex equivalent) that registers Cockpit's presence endpoint.

### Reading Session Cards

Each card shows:

- **Status pill** — `active` / `idle` / `error` / `stopped`
- **Project + session ID**
- **Last event timestamp** (relative)
- **Recent files** — last few file changes with a sparkline of activity
- **Session duration** — how long the session has been live

Click a card to expand the full activity timeline (file reads, edits, commands).

### Removing a Session

Click the trash icon on a card to drop it from the view. The underlying session keeps running — only its presence record is cleared.

### Clearing All

The **Clear all** button removes every session record from the view at once. Use it when you've accumulated stale sessions from a long-running dev day and want a clean slate.

### Highlighting a Session

The page accepts a `?session=<id>` query parameter. When set, the matching card is ringed and scrolled into view — useful when a notification in another part of Cockpit points you at a specific session.

## Status States

| Status | Meaning |
|--------|---------|
| `active` | Recent event within the last minute |
| `idle` | No events recently but session still alive |
| `error` | Last event was an error |
| `stopped` | Session has ended (no recent heartbeats) |

## Source of Truth

Presence events flow:

```
Claude Code / Codex CLI
   ↓ (hook on file change)
Presence endpoint
   ↓
WebSocket fanout
   ↓
Browser cards (live update)
```

The backend retains the last N events per session so a reload shows recent activity immediately even before the next event arrives.

## Tips

- **Presence ≠ CC Bridge** — CC Bridge is the tmux pane (terminal view); Presence is the file-event view. They show complementary slices of the same session.
- **Hooks are per-CLI** — Claude Code and Codex CLI each have their own settings file; the setup notes tell you which to edit.
- **Highlighting** is a URL-level feature — you can link directly to a specific session from anywhere in Cockpit.

## See also

- [CC Bridge](./cc-bridge.md) — terminal view of the same sessions
- [Agent Mail](./agent-mail.md) — cross-session messaging that often piggybacks on active presence sessions