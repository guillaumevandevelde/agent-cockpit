# Agent Performance

Per-agent outcome, duration, and token statistics across a project's kanban board.

## Overview

The Agent Performance page reads from the kanban op-log for the active project and aggregates every finished card into per-agent stats: how many tasks each agent completed, how often they failed, average and median duration, and total token usage (when available). Charts compare agents side by side.

## How to Use

### Selecting a Project

The page is project-scoped — the active project in the sidebar (or project switcher) determines which cards are aggregated. Switch projects to compare boards.

### Reading the Stats Cards

Top-level cards summarize totals:

- **Total tasks** — sum of completed, failed, in-progress across agents
- **Completed / Failed** — outcome split with a success rate
- **Average duration** — mean time from claim to terminal column

### Per-Agent Charts

Two bar charts break down per-agent behavior:

- **Outcome** — completed vs failed count per agent
- **Tokens** — total tokens consumed per agent (only shown if the op-log has token data)

### Per-Agent Table

Each agent row shows:

| Column | Description |
|--------|-------------|
| `agent` | Agent persona (e.g. `engineer`, `analyst`) |
| `tasks` | Total cards claimed |
| `completed` | Cards moved to `Done` |
| `failed` | Cards in `Impediment` or with failure op |
| `success rate` | `completed / (completed + failed)` |
| `avg / median duration` | Time from claim to terminal column |
| `tokens` | Input + output + cache (if tracked) |

### Common Failures

A short list shows the most frequent failure reasons across all agents — useful for spotting systemic blockers (`test failures`, `timeout`, `permission denied`).

## Source of Truth

The page reads from the same op-log as the [Kanban](./kanban.md) board. Cards moved to `Done` contribute to the completed count; cards moved to `Impediment` or that ended with a failure op contribute to failed. In-progress cards are claimed but not yet terminal.

## Tips

- Token totals are best-effort — only available if the dispatch pipeline records token usage on each op. Empty token fields mean the pipeline isn't writing them.
- Median is more informative than average for boards with one stuck card; pair the two.
- Compare the same agent across two projects by switching the project switcher.

## See also

- [Kanban](./kanban.md) — the source of all aggregated stats