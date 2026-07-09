# Plans

Browse, search, and read execution-plan documents produced by Claude Code sessions.

## Overview

The Plans page lists every execution plan written under `~/.claude/plans/` (and per-project plan directories) — markdown documents that Claude Code generates when running a long, multi-step task. Each plan is a self-contained spec: title, structured headings, code blocks, tables, and a list of linked sessions that were active while the plan was authored.

The page has two views:

- **List** — all plans grouped by date (Today / This Week / This Month / Older), with title, excerpt, size, and modified-at
- **Detail** — full plan content rendered as markdown, plus a list of sessions that were live while the plan was being written

## How to Use

### Browsing Plans

The list view shows three stat cards at the top:

- **Total Plans** — count of plan files
- **Date Range** — first to most recent plan
- **Total Size** — combined disk usage

Use the **search box** to filter plans by title, excerpt, or slug. Results are grouped by date with the same Today/Week/Month/Older buckets.

### Reading a Plan

Click any plan card to open the detail view:

- **Title** and last-modified date at the top
- **Markdown body** — full plan content with rendered headings, tables, and code blocks
- **Linked sessions** — sessions that were active while the plan was authored, with project folder and git branch

The detail page is read-only — plans are Claude-authored documents and Cockpit doesn't edit them.

### Searching Plans

Client-side search matches against title, excerpt, and slug. For full-text content search across all plans, use the backend's `plans/search` endpoint directly.

## Plan Sources

| Location | Scope |
|----------|-------|
| `~/.claude/plans/*.md` | User-scoped plans |
| `.claude/plans/*.md` | Project-scoped plans |
| Per-project plan dirs | As configured |

The list endpoint aggregates across all known plan directories for the active project.

## Tips

- **Plans are markdown** — copy any plan into your editor to adapt it for reuse.
- **Linked sessions** help reconstruct the context: if a plan is stale, click into the linked sessions to see the conversation that produced it.
- **Large plans** are still rendered fully — there's no pagination in the detail view; the markdown component handles the scroll.

## See also

- [Sessions](./sessions.md) — browse the conversations that produced these plans
- [Kanban](./kanban.md) — analysts attach plans to cards as deliverables