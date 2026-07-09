# Context Window

Live context-window analysis for active Claude Code and Codex CLI sessions.

## Overview

The Context page surfaces per-session context usage in real time: a gauge showing the current fill level, a timeline of how the window grew over the session, a breakdown of what's eating the budget (files, tools, messages), cache efficiency, and projections for how long until the window fills.

The page polls every 10 seconds and pauses polling when the browser tab is hidden, so an inactive tab doesn't burn bandwidth.

## How to Use

### Active Sessions List

The left panel lists every session the backend considers active. Each row shows:

- Project name and session ID
- Last activity timestamp
- A small inline gauge of current context fill

Click a session to drill in.

### Per-Session Analysis

Once a session is selected, the right panel fills in:

#### Context Gauge

A large circular gauge with the current context percentage. Color follows the dashboard's scale:

| Color | Range | Meaning |
|-------|-------|---------|
| Green | 0–49% | Plenty of room |
| Yellow | 50–79% | Getting used |
| Orange | 80–94% | Running low |
| Red | 95–100% | Nearly full |

#### Timeline Chart

A line chart of context usage over the session's lifetime, sampled at each message boundary. Useful for spotting compaction events (sudden drops).

#### Composition Chart

A stacked breakdown of what currently occupies the window:

- **System** — system prompt and base instructions
- **Tools** — tool definitions
- **Messages** — user/assistant turns
- **Files** — file content reads

#### File Consumption Table

The largest files currently in context, sorted by token count. Shows the path and estimated token cost — first thing to trim if you need room.

#### Tool Usage Table

Tool-call counts per tool, with input and output token totals where the session log records them.

#### Cache Efficiency

A card showing cache hit rate (cache read tokens / total read+creation tokens). Higher is better.

#### Projections

Extrapolates the current burn rate into the future to estimate "context will fill in X minutes at this pace." Pair with the gauge when deciding whether to start a compaction or wrap up the session.

## Source of Truth

The page reads the active session's stored JSONL transcript and the live session metadata. The backend computes the analysis on demand and caches the result briefly to absorb poll intervals.

## Tips

- **Stop polling when away** — the visibility handler already does this; check that you haven't disabled background tabs at the browser level.
- **Projections are extrapolations** — they assume the recent burn rate continues; a sudden tool-heavy turn will make them pessimistic.
- The **File Consumption Table** is the fastest path to "where did my tokens go?" when a session surprises you with high usage.

## See also

- [Sessions](./sessions.md) — browse the raw transcripts the analysis reads from
- [Dashboard](./dashboard.md) — summary context widget across all sessions