# Scheduled Messages

Schedule one-off or recurring prompts that get injected into Claude Code or Codex CLI sessions on a timer or cron.

## Overview

Scheduled Messages let you compose a prompt once and have it delivered into a running session at a future moment — either at an exact timestamp or on a cron schedule. Delivery is via tmux `send-keys` against a matched session, or by spawning a fresh session in a sandcastle if no matching session exists.

The page is a list of every scheduled message with status, trigger, target, and an expandable delivery log showing every fire attempt and outcome.

## How to Use

### Creating a Scheduled Message

Click **"New Message"** to open the form:

1. **Target project** — path to the repo the message applies to
2. **Target kind** — `project` (any active session in this project), `session` (a specific session), or `sandcastle` (spawn a fresh sandbox)
3. **Trigger** —
   - **Once** — pick a date/time
   - **Cron** — enter a cron expression and timezone
4. **Permission mode** — `default` / `acceptEdits` / `bypass`
5. **When the session is busy** — `wait_until_idle` or `send_now`
6. **When the session is missing** — `spawn` (start a fresh one) or `skip`
7. **Message** — the prompt to inject (markdown)

Click **Save** to schedule.

### Reading the List

Each row shows:

| Field | Meaning |
|-------|---------|
| Status badge | `scheduled` / `pending_delivery` / `delivered` / `failed` / `cancelled` |
| Target kind | `project` / `session` / `sandcastle` |
| Target project | Short project path |
| Trigger | `Once — 2026-07-10 14:30` or `Cron: 0 9 * * 1-5 (Europe/Amsterdam)` |
| Message | First line, truncated |
| Enabled | Disabled messages don't fire |

Click a row to expand its **Delivery Log** — every fire attempt with timestamp, resolved session, action, wait duration, outcome, and error message (if any).

### Toggling and Deleting

Use the per-row toggle to enable/disable without deleting, and the trash icon to delete outright. Multi-select with checkboxes supports bulk delete.

### Clearing History

The **Clear History** action wipes all past delivery records (the scheduled messages themselves stay). Use it after a long debug session to start with a clean log.

## Status States

| Status | Meaning |
|--------|---------|
| `scheduled` | Waiting for its trigger time |
| `pending_delivery` | Triggered, attempting delivery |
| `delivered` | Successfully injected |
| `failed` | Delivery failed (see log for reason) |
| `cancelled` | Manually cancelled or deleted |

## Trigger Types

| Type | Use Case |
|------|---------|
| `once` | One-off at a specific timestamp |
| `cron` | Recurring on a cron expression (5-field standard) |

All times are stored in UTC and evaluated in the message's configured timezone for display.

## Delivery Modes

| Setting | Behavior |
|---------|---------|
| `when_busy = wait_until_idle` | Queue until the session shows idle, then send |
| `when_busy = send_now` | Inject immediately even mid-generation |
| `on_missing_session = spawn` | Start a fresh session in the project if none matches |
| `on_missing_session = skip` | Skip the fire and log a failure |

## Tips

- **Use project target** when you don't care which session handles it — any active session in the repo will do.
- **Use session target** when you're continuing a specific conversation — pair with **resume** badges.
- **Cron messages survive restarts** — the scheduler re-reads the schedule on each backend start.
- **Permission mode** controls how the injected prompt is treated by the receiving session — `bypass` lets it run even when the session would normally ask for confirmation.

## See also

- [Sessions](./sessions.md) — the conversations these messages get injected into
- [Kanban](./kanban.md) — the dispatcher also uses the scheduler for `scheduled_at` cards