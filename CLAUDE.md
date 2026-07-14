# ⚠️ Fork: Claude Cockpit — lees dit eerst

Dit is een **fork** van claude-deck, hernoemd naar **Claude Cockpit**. De oorspronkelijke
**scheduled-messages** feature (timer/cron → injectie in CC-sessies via tmux) is
**vrijwel af** — Tasks 1–11 van `docs/cockpit/fase-2-plan.md` zijn geïmplementeerd
(backend-tests groen, frontend build clean), alleen **Task 12 (runtime e2e)** en de
**fase-1 runtime-checklist** (`fase-1-validation.md`) zijn mensenwerk.

Het zwaartepunt van de **actieve** ontwikkeling ligt nu bij de kanban-/multi-agent-laag:

- **Kanban auto-dispatch** — de dispatcher claimt + spawnt Todo-kaarten
  (`docs/cockpit/kanban-dispatch-spec.md`, follow-ups in `kanban-followups.md`).
- **Multi-agent kanban** — analyst-fase splitst parent-kaarten op in kind-kaarten met
  afhankelijkheids-DAG en plan-attachment; executors wachten op hun deps
  (`docs/cockpit/multi-agent-kanban.md`).
- **Kanban string-conventies** — vast kolommen (`COLUMNS` vs `_DISPATCH_COLUMNS`),
  comment-prefix-contract (`**Summary:**`/`**Impediment:**`/`**Resolution:**`/…),
  deliverable-kinds (`pr`/`branch`/`commit`/`link`/`note`/`plan`/`plan_ref`/`spec`).
  Lees vóór je een nieuwe vaste kolom introduceert of een Done/Impediment-comment
  post: `docs/cockpit/kanban-conventions.md`. Validatiescript:
  `scripts/check-kanban-conventions.sh`.
- **Agent Mail** — cross-session berichten tussen willekeurige sessies met durable
  repo-identiteit en inspectable mailbox (`docs/cockpit/agent-mail-spec.md`).

- **Volledige oriëntatie + huidige taak:** `docs/cockpit/00-orientation.md`
- **Scheduled-messages plan (vrijwel af):** `docs/cockpit/fase-2-plan.md`
- **Huidige open pool:** `docs/cockpit/kanban-followups.md`
- **Omgeving:** WSL Ubuntu, Docker (`docker compose up -d` → :8000), tmux, claude CLI.

Hieronder volgt de oorspronkelijke claude-deck-documentatie (codebase-structuur etc.).

---

## Doelstelling & zelfverbetering

Bouw een **agentic developers platform**: een agentisch software engineering platform dat
AI-agents inzet voor de ontwikkeling, het beheer en de evolutie van softwareapplicaties.
Dit heeft **twee even belangrijke, eersteklas doelen**:

1. **Andere applicaties** — het platform bouwt en beheert externe/aparte doel-applicaties
   (repos buiten Cockpit zelf): analyse, implementatie, tests, onderhoud en evolutie ervan
   via agents. Dit is geen bijproduct; het is de primaire bestaansreden.
2. **Zichzelf** — het platform bouwt, beheert en verbetert continu zijn eigen codebase
   (zie **Zelfverbetering** hieronder).

De orchestratie-kern (dispatch, worktrees, agent mail, dependency-DAG, session-lifecycle)
moet daarom generiek zijn: agent-onafhankelijk én repo-onafhankelijk, zodat een willekeurige
doel-applicatie er via dezelfde executie-primitieven op aangesloten kan worden.

### Kernprincipes

- Gebruik Claude Code als primaire AI-agent, maar ontwerp het platform agent-onafhankelijk zodat andere AI-agents eenvoudig kunnen worden geïntegreerd.
- Automatiseer zoveel mogelijk werkzaamheden zonder de gebruiker uit de beslissingsketen te verwijderen.
- Geef de gebruiker volledige transparantie over alle geplande, lopende en uitgevoerde acties, inclusief de motivatie, voortgang en resultaten.
- Bewaak continu de doelstellingen van het platform en stuur werkzaamheden hier proactief op bij.
- Verbeter het platform continu:
  - **Functioneel** door nieuwe functionaliteit, workflows en automatiseringen voor te stellen en te implementeren.
  - **Technisch** door technische schuld, bugs, beveiligingsrisico's, performantieproblemen, stabiliteitsproblemen en onderhoudsproblemen automatisch te detecteren, te analyseren en waar mogelijk zelfstandig te verhelpen.
- Optimaliseer continu de eigen werking door inefficiënties te identificeren en processen, architectuur en configuratie te verbeteren.
- Ontwerp alle functionaliteit modulair, uitbreidbaar en onderhoudbaar.
- Zorg ervoor dat alle wijzigingen reproduceerbaar, controleerbaar en auditbaar zijn.
- Respecteer de ingestelde autonomiegrenzen en vraag goedkeuring voor acties die buiten deze grenzen vallen.

### Zelfverbetering

Het platform streeft naar continue zelfoptimalisatie en moet onder andere in staat zijn om:

- nieuwe functionaliteit voor te stellen en te ontwikkelen;
- repetitieve taken verder te automatiseren;
- codekwaliteit te verbeteren;
- technische schuld te verminderen;
- bugs proactief te detecteren en herstellen;
- performantieknelpunten te identificeren en optimaliseren;
- beveiligingsproblemen te detecteren en mitigeren;
- foutieve configuraties te corrigeren;
- zichzelf te monitoren en waar mogelijk zelfhelend op te treden;
- architectuur en afhankelijkheden actueel en gezond te houden.

### Succescriteria

Het platform ontwikkelt zich continu verder, voert werkzaamheden steeds autonomer uit, blijft volledig transparant en wordt na verloop van tijd functioneel rijker, technisch robuuster en efficiënter.

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
# cockpit.sh start auto-installs missing/stale deps (npm install, pip install) before starting
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
bash backend/test_commands_api.sh                         # Curl-based API tests
bash scripts/test_pytest_baseline.sh                      # Bash tests for pytest-baseline / pytest-compare scripts

# Lint
cd frontend && npm run lint      # ESLint

# Pytest baseline (attribute pre-existing failures on origin/master — kanban card 4c7c5346)
./scripts/pytest-baseline.sh                # Capture pre-existing failures (idempotent, cached 24h)
./scripts/pytest-compare.sh                 # Run pytest + classify: pre-existing / NEW / FIXED

# Version
./scripts/bump-version.sh <major|minor|patch>  # Sync version across VERSION, package.json, pyproject.toml
```

## Architecture

```
backend/                  # FastAPI + async SQLAlchemy + aiosqlite
├── app/
│   ├── main.py          # FastAPI app, CORS, lifespan
│   ├── config.py        # pydantic-settings (defaults in code, no .env required)
│   ├── database.py      # Async SQLAlchemy engine + session
│   ├── api/v1/          # 30 route modules (router.py aggregates all), incl. subdir routers:
│   │                    #   cc_bridge/, kanban/, runs/, sandcastle/, scheduled_messages/
│   ├── models/          # database.py (ORM), schemas.py (Pydantic)
│   ├── services/        # 58 service files (business logic), incl. subdirs:
│   │                    #   agentic_cli/, agent_mail/, blueprint/, runs/, scheduling/, templates/
│   └── utils/           # path_utils, file_utils

frontend/                 # React 19 + Vite + TypeScript + shadcn/ui
├── src/
│   ├── App.tsx          # Routes (29 pages)
│   ├── features/        # Feature modules (26 dirs, each with page + components + API + types)
│   ├── components/      # layout/, shared/, ui/ (19 shadcn components)
│   ├── hooks/           # useApi, useProjects, useSessionsApi, useUsageApi
│   ├── contexts/        # ProjectContext, ThemeContext
│   ├── types/           # Shared TypeScript types (15 files)
│   └── lib/             # api.ts, constants.ts, utils.ts
```

### Features

Config, MCP Servers, MCP Server (registry), Commands, Plugins, Hooks, Permissions, Agents, Agent Performance, Skills, Memory, Context, Projects, Backup, Output Styles, Status Line, Sessions, CC Bridge, Kanban, Scheduled Messages, Plans, Presence, Sandcastle, APM, Usage, Updates, Dashboard

### API Routes

All under `/api/v1/`: config, projects, cli, mcp, mcp-server, commands, plugins, hooks, permissions, agents, agent-activity, backup, output-styles, statusline, sessions, usage, memory, context, plans, presence, providers, codex-config, status, apm, files, plus subdir routers: cc-bridge, agent-bridge, kanban, scheduled-messages, sandcastle

## Key Decisions

- **Backend**: FastAPI + async SQLAlchemy + aiosqlite + SQLite
- **Frontend**: React 19 + Vite 7 + TypeScript + TailwindCSS + shadcn/ui
- **Database**: SQLite at `backend/claude_registry.db` (auto-created via `create_all`, no migrations)
- **API**: RESTful `/api/v1/`, Vite proxies `/api` → `http://localhost:8000`
- **CORS**: `localhost:5173`

## Code Style

- **Frontend**: ESLint + TypeScript strict mode (`noUnusedLocals`, `noUnusedParameters`). Path alias `@/*` → `./src/*`
- **No impure calls in render**: the react-compiler ESLint rule rejects `Date.now()` / `Math.random()` (etc.) called directly in a component's render body — including as an inline argument expression, e.g. `formatLabel(Date.now())` inside JSX/render. Move the impure call inside the helper function itself instead (see `isFutureSchedule` in `frontend/src/features/kanban/components/CardItem.tsx`), otherwise `npm run lint` fails with `Cannot call impure function during render`.
- **Backend**: Type hints throughout, async/await patterns, pydantic models for validation

## UI Conventions

- **Clickable cards**: All clickable Card components must use the `CLICKABLE_CARD` constant from `@/lib/constants`. This gives a consistent `border-2 hover:border-primary/50` orange border hover effect, plus `cursor-pointer`, `transition-colors`, and `focus-visible:ring-2` for keyboard a11y. Action buttons inside clickable cards must use `e.stopPropagation()` and keyboard handlers must support Enter/Space.
- **Modal sizes**: Use `MODAL_SIZES.SM`, `MODAL_SIZES.MD`, or `MODAL_SIZES.LG` from `@/lib/constants` for dialog sizing.
- **Markdown rendering**: Use `<MarkdownRenderer>` from `@/components/shared/MarkdownRenderer` for read-only markdown display. Use `<MarkdownPreviewToggle>` from `@/components/shared/MarkdownPreviewToggle` for editable markdown with Edit/Preview tabs.
- **Browser-verifying a UI change when the shared dev stack is busy**: if `./scripts/cockpit.sh start` refuses to start because another concurrent session already holds ports 8000/5173, don't wait for it and don't use the live kanban board as a screenshot fixture. Follow `docs/cockpit/isolated-component-preview.md` — a scratch Vite entry on an unused port mounts just the changed component (with a minimal fixture), then Playwright screenshots it in light + dark.

## Git Workflow

- **Finishing a branch**: The default is to **merge back to `master` and push** — no need to ask. Skip the merge/PR/cleanup menu. If the main checkout's `master` is dirty (concurrent sessions share one working copy), do the merge in a temporary worktree. The exact recipe — note `--detach origin/master`, **not** `master`: checking out the branch name collides with the main worktree (`git worktree add` refuses two checkouts of one branch, failing with `'master' is already used by worktree at ...`). Run each git step as its own `Bash` call or chain with `&&` in one call — a bare `cd` in a separate `Bash` call does not persist cwd across tool calls, so `git merge` would silently run in the wrong worktree:
  ```bash
  # Pre-flight: the detached worktree only sees COMMITTED state — uncommitted/untracked
  # changes here merge as a silent no-op ("Everything up-to-date"). Commit first.
  if ! git diff --quiet HEAD || [ -n "$(git ls-files --others --exclude-standard)" ]; then
    echo 'ERROR: uncommitted/untracked changes — git add + git commit first, then re-run.' >&2; exit 1
  fi
  TMP=$(mktemp -d)
  git worktree add --detach "$TMP/m" origin/master
  git -C "$TMP/m" merge --no-ff <branch> -m "Merge <branch>: <summary>"
  git -C "$TMP/m" push origin HEAD:master
  git worktree remove "$TMP/m" --force
  ```
  Using `git -C "$TMP/m"` (instead of `cd`) sidesteps the lost-cwd trap entirely. The
  pre-flight guard catches the silent-no-op case where a docs-/quick-edit branch was
  never committed: the detached worktree merges committed history only, so an
  uncommitted file produces "Everything up-to-date" and pushes nothing.
- **Worktree hygiene**: After merging, the finished worktree + branch should be removed so `.claude/worktrees/` doesn't accumulate leftovers. Kanban dispatch auto-removes on card→Done; for everything else run `scripts/worktree-gc.sh` (dry-run) then `--apply`. It only removes worktrees that are **(a)** clean, **(b)** fully merged into `master`, **and (c)** not currently held by an active kanban agent claim (`claimed_by LIKE 'agent:%'` AND column NOT IN Done/Impediment) — see the postmortem of the "[problem] worktree-gc verwijdert branch/worktree van actieve analyst-sessie" card for why (c) is load-bearing: an analyst-only session never commits, so its branch is trivially merged+clean from creation and would otherwise be killed by the first gc run). `cockpit.sh start` nudges when leftovers exist.
- **Remote branch hygiene**: `delete_branch_on_merge` is enabled on the GitHub repo (set 2026-07-07), so any branch pushed for a PR (`git-ship`'s `pull-request` ship mode does `git push -u origin HEAD`) is deleted by GitHub the moment its PR merges — no manual sweep needed for the merged case anymore. Branches pushed for a PR that never merges (card hit `report_impediment`, `gh` was unavailable, checks never went green) still strand on `origin` — those need a human decision, not automation. Periodically check for them: `git branch -r | grep 'origin/k-'` then `git cherry master origin/<branch>` per candidate to see if it's actually merged (empty output) before deleting.
- **No local pre-push gate**: removed 2026-07-05 — a shared box running many concurrent dispatched agents made the old `.githooks/pre-push` test gate (full backend pytest + frontend lint/build on every push, serialized via flock) a recurring source of multi-minute stalls and SSH idle-disconnects under contention. Backend pytest + ruff and frontend lint/test/build now run in CI (`quality.yml`) instead — push freely, watch the Actions run. Note: the old hook also refused any push that collapsed a branch's file tree (a test git-fixture once wiped `master` down to `a.txt` and it got pushed); that preventive check is gone too. `scripts/cockpit-doctor.sh` (or `cockpit.sh doctor`) still gives a read-only, *after-the-fact* health check for the same clobbered-tree scenario plus `core.bare` mismatch, stale checkout, and leftover worktrees.

## CI/CD

GitHub Actions workflows in `.github/workflows/`:
- `claude.yml` — Claude Code integration (triggers on @claude mentions)
- `codeql.yml` — CodeQL security analysis
- `quality.yml` — backend (ruff + pytest) and frontend (lint + test + build), on push/PR to `master`
- `release.yml` — Manual release (builds frontend, creates GitHub release)

## Gotchas

- No `.env` file needed — all config has defaults in `backend/app/config.py`
- Database lives at `backend/claude_registry.db`, created automatically on first run
- No database migration system — schema changes require deleting the db
- Backups stored in `~/.claude-registry/backups/`
- `rm` is blocked via `.claude/settings.json` (`Bash(rm:*)` deny) — use `mv` to move unwanted files outside the repo, or `git clean -f -- <path>` for untracked files, instead
