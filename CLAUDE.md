# ⚠️ Fork: Claude Cockpit — lees dit eerst

Dit is een **fork** van claude-deck, hernoemd naar **Claude Cockpit**, met daarbovenop een
**scheduled-messages** feature (timer/cron → injectie in CC-sessies via tmux) en een
per-project **kanban**-board met agent self-service via MCP. Beide zijn gebouwd en gemerged.

- **Volledige oriëntatie:** `docs/cockpit/00-orientation.md`
- **Ontwerp-/planningsdocs:** `docs/cockpit/` (fase-1/2, kanban, pane-attention).
- **Kanban follow-ups (post-v1 backlog):** `docs/cockpit/kanban-followups.md`
- **Omgeving:** WSL Ubuntu, user `guillaume`. De dev-stack draait **direct in WSL**
  (uvicorn `:8000` + Vite `:5173`), niet in Docker. Verder: tmux, claude CLI.

Hieronder volgt de oorspronkelijke claude-deck-documentatie (codebase-structuur etc.).

---

# Claude Cockpit

Web app for managing Claude Code configurations, MCP servers, commands, plugins, hooks, and permissions.

## Commands

```bash
# Install
./scripts/install.sh             # Setup venv, install deps, create dirs (requires Python 3.11+, Node 18+)

# Development
./scripts/dev.sh                 # Start both backend + frontend servers (attached, Ctrl+C to stop)
cd backend && source venv/bin/activate && uvicorn app.main:app --reload --port 8000  # Backend only
cd frontend && npm run dev       # Frontend only (port 5173)

# Self-healing dev stack (detached supervisor: auto-restart on crash, logs to logs/, survives terminal close)
./scripts/cockpit.sh start       # Start backend+frontend supervised in the background
./scripts/cockpit.sh status      # Show supervisor/backend/frontend status
./scripts/cockpit.sh logs backend  # Follow backend logs (or: logs frontend)
./scripts/cockpit.sh restart     # Stop, then start
./scripts/cockpit.sh stop        # Stop supervisor + all processes
bash scripts/test_cockpit.sh     # Test the supervisor (bash harness)

# Build
./scripts/build.sh               # Production frontend build → frontend/dist
cd frontend && npm run build     # Same as above

# Test
cd backend && source venv/bin/activate && pytest tests/  # Python tests
bash backend/test_commands_api.sh                         # Curl-based API tests

# Lint
cd frontend && npm run lint      # ESLint

# Version
./scripts/bump-version.sh <major|minor|patch>  # Sync version across VERSION, package.json, pyproject.toml
```

## Architecture

```
backend/                  # FastAPI + async SQLAlchemy + aiosqlite
├── app/
│   ├── main.py          # FastAPI app, CORS, lifespan
│   ├── config.py        # pydantic-settings (defaults in code, no .env required)
│   ├── database.py      # Async SQLAlchemy engine + session (device-local store)
│   ├── kanban/          # Kanban domain: separate SQLite store, ops log, MCP server
│   ├── api/v1/          # ~25 route groups (router.py aggregates all)
│   ├── models/          # database.py (ORM), schemas.py (Pydantic)
│   ├── services/        # ~45 service files across services/ and its subpackages
│   └── utils/           # path_utils, file_utils

frontend/                 # React 19 + Vite + TypeScript + shadcn/ui
├── src/
│   ├── App.tsx          # Routes (~26 pages)
│   ├── features/        # Feature modules (22 dirs, each with page + components + API + types)
│   ├── components/      # layout/, shared/, ui/ (20 shadcn components)
│   ├── hooks/           # useApi, useProjects, useSessionsApi, useUsageApi
│   ├── contexts/        # ProjectContext, ThemeContext
│   ├── types/           # Shared TypeScript types (15 files)
│   └── lib/             # api.ts, constants.ts, utils.ts
```

### Features

Config, MCP Servers, Commands, Plugins, Hooks, Permissions, Agents, Skills, Memory, Projects, Backup, Output Styles, Status Line, Sessions, CC Bridge, Usage, Dashboard, Scheduled Messages, Kanban, Presence, Plans, Context

### API Routes

All under `/api/v1/`: health, config, projects, cli, mcp, commands, plugins, hooks, permissions, agents, backup, output-styles, statusline, sessions, cc-bridge, agent-bridge, usage, memory, context, plans, presence, providers, codex-config, status, scheduled-messages, kanban.

The kanban MCP (SSE) server is mounted separately at `/kanban-mcp` (outside `/api/v1`); agents point their `.mcp.json` at `http://localhost:8000/kanban-mcp/sse`.

## Key Decisions

- **Backend**: FastAPI + async SQLAlchemy + aiosqlite + SQLite
- **Frontend**: React 19 + Vite 7 + TypeScript + TailwindCSS + shadcn/ui
- **Database**: two SQLite stores, both auto-created via `create_all` (no migration tool — see Gotchas): `backend/claude_registry.db` (device-local data) and `backend/kanban.db` (portable, sync-able kanban domain)
- **API**: RESTful `/api/v1/`, Vite proxies `/api` → `http://localhost:8000`
- **CORS**: `localhost:5173`
- **Auth**: optional bearer token (`api_token` in config, unset by default = open/local-only). When set, `require_api_token` (`main.py`) protects `/api/v1/*` **and** the `/kanban-mcp` mount; WebSocket endpoints carry their own token checks.

## Code Style

- **Frontend**: ESLint + TypeScript strict mode (`noUnusedLocals`, `noUnusedParameters`). Path alias `@/*` → `./src/*`
- **Backend**: Type hints throughout, async/await patterns, pydantic models for validation

## UI Conventions

- **Clickable cards**: All clickable Card components must use the `CLICKABLE_CARD` constant from `@/lib/constants`. This gives a consistent `border-2 hover:border-primary/50` orange border hover effect, plus `cursor-pointer`, `transition-colors`, and `focus-visible:ring-2` for keyboard a11y. Action buttons inside clickable cards must use `e.stopPropagation()` and keyboard handlers must support Enter/Space.
- **Modal sizes**: Use `MODAL_SIZES.SM`, `MODAL_SIZES.MD`, or `MODAL_SIZES.LG` from `@/lib/constants` for dialog sizing.
- **Markdown rendering**: Use `<MarkdownRenderer>` from `@/components/shared/MarkdownRenderer` for read-only markdown display. Use `<MarkdownPreviewToggle>` from `@/components/shared/MarkdownPreviewToggle` for editable markdown with Edit/Preview tabs.

## CI/CD

GitHub Actions workflows in `.github/workflows/`:
- `claude.yml` — Claude Code integration (triggers on @claude mentions)
- `codeql.yml` — CodeQL security analysis
- `release.yml` — Manual release (builds frontend, creates GitHub release)

## Gotchas

- No `.env` file needed — all config has defaults in `backend/app/config.py`
- Backend deps live in two places that must stay in sync: `backend/requirements.txt` (runtime; what `install.sh` + CI install via `requirements-dev.txt`) and `backend/pyproject.toml` `[project.dependencies]`. Add a runtime dep to both; dev-only tools go in `requirements-dev.txt` + `[project.optional-dependencies].dev`.
- Databases live at `backend/claude_registry.db` and `backend/kanban.db`, created automatically on first run
- No database migration system — tables are created with `create_all`; schema changes require deleting the db (the kanban materialized tables can also be rebuilt via `rematerialize()` from its ops log). Migrations are intentionally deferred to post-v1; do not reintroduce Alembic without wiring it into startup.
- Frontend tests are minimal (Vitest is configured; only `src/lib/api.test.ts` exists so far)
- Backups stored in `~/.claude-registry/backups/`
