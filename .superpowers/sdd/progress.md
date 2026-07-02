# Kanban MCP Robustness — SDD Progress

Plan: docs/superpowers/plans/2026-06-27-kanban-mcp-robustness.md
Branch: worktree-kanban-mcp-robustness
Start commit: e3a7e44936e645ec6cd8db2167c4980b053977c8

## Tasks

- [x] Task 1: Backend — `_probe_sse` helper + `GET /mcp-health` endpoint
- [x] Task 2: Backend — MCP tool `_safe` decorator with DB retry
- [x] Task 3: Frontend — `mcpHealth` API call + `McpHealthBanner` component
- [ ] Task 4: Frontend — Wire up polling in `KanbanPage` + status dot in `EnableKanbanToggle`
Task 1: complete (commits e3a7e44..3639ba6, review clean)
Task 2: complete (commits 3639ba6..f16a6a8, review clean — Minor: OperationalError import not at top of test file)
Task 3: complete (commits f16a6a8..8ced69f, fix for null guard applied, review clean)
