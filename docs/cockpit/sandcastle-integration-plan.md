# Sandcastle Integration Plan — Claude Cockpit

## 1. Critical Analysis: Sandcastle vs Existing CC Bridge / Agent Bridge

### What sandcastle does better

| Capability | CC Bridge / Agent Bridge | Sandcastle |
|---|---|---|
| **Container isolation** | None — runs directly on host via tmux | Docker/Podman/Vercel containers with configurable images |
| **Git branch safety** | Worktree mode exists but manual | Automatic branch strategies: `head`, `merge-to-head`, named branches — commits on temp branches merge back cleanly |
| **Multi-agent orchestration** | Single session per tmux pane | Multiple agents on parallel branches via `createSandbox()` / `createWorktree()` |
| **Structured output** | Raw tmux capture-pane text | `Output.object()` / `Output.string()` — typed extraction from agent stdout |
| **Agent provider abstraction** | CC-only (CC Bridge) or manual provider enum (Agent Bridge) | 6 built-in providers: Claude Code, Codex, Cursor, Pi, OpenCode, Copilot |
| **Session capture/resume** | Depends on provider CLI features | Built-in `resumeSession`, `captureSessions`, `sessionStorage` with host↔sandbox transfer |
| **Completion detection** | None — poll tmux for idle | `completionSignal` pattern + `completionTimeoutSeconds` for automatic termination |
| **Sandbox lifecycle** | Manual tmux kill | `createSandbox()` returns a reusable handle with `run()`, `exec()`, `close()` |
| **Worktree reuse** | Manual git worktree management | Automatic fast-forward from origin when reusing named branches |

### What CC Bridge / Agent Bridge does better

| Capability | CC Bridge / Agent Bridge | Sandcastle |
|---|---|---|
| **Interactive terminal access** | WebSocket PTY relay — live typing in browser | Not designed for interactive TUI — `interactive()` is host-terminal-only |
| **Multi-provider tmux discovery** | `discover_agent_sessions()` finds all running sessions across providers | No session discovery — you must track what you spawned |
| **CC Cockpit integration** | Native: session cards, preview, kill, rename, spawn — all wired to the UI | External library — needs a full adapter layer |
| **Per-session rename/kill** | Built-in API | Manual `close()` on sandbox handles |
| **Resumable sessions** | `list_resumable_sessions()` with project-scoped search | `resumeSession` exists but requires knowing the session ID |
| **No container requirement** | Works on any host with tmux + provider CLI | Docker/Podman required for sandboxed runs (or no-sandbox fallback) |

### Verdict: Maximum integration strategy

Sandcastle should **complement** CC Bridge/Agent Bridge, not replace them:

1. **CC Bridge remains** for interactive terminal sessions (human-in-the-loop work).
2. **Sandcastle feature** handles **headless/automated agent runs** in containers — fire-and-forget tasks, batch operations, multi-agent parallel execution.
3. **Agent Bridge** gains awareness of sandcastle for session discovery (sandcastle-spawned sessions appear in Agent Bridge listing).

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                    Claude Cockpit UI                      │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────────┐  │
│  │CC Bridge │  │Agent Brg │  │   Sandcastle (NEW)    │  │
│  │(interactive)│(multi-prov)│  │ Config │ Runs │ Logs │  │
│  └────┬─────┘  └────┬─────┘  └──────────┬────────────┘  │
│       │              │                   │                │
│       ▼              ▼                   ▼                │
│  ┌──────────────────────────────────────────────────┐    │
│  │              FastAPI Backend                       │    │
│  │  ┌──────────┐ ┌────────────┐ ┌─────────────────┐ │    │
│  │  │cc_bridge │ │  runs/     │ │ sandcastle (NEW) │ │    │
│  │  │/router   │ │ /router    │ │ /router.py       │ │    │
│  │  └────┬─────┘ └─────┬──────┘ └────────┬────────┘ │    │
│  │       │              │                 │           │    │
│  │       ▼              ▼                 ▼           │    │
│  │  ┌──────────┐ ┌────────────┐ ┌─────────────────┐ │    │
│  │  │cc_bridge │ │  runs/     │ │ sandcastle_svc   │ │    │
│  │  │/services │ │/services   │ │ (NEW)            │ │    │
│  │  └──────────┘ └────────────┘ └────────┬────────┘ │    │
│  │                                        │          │    │
│  │                                   ┌────▼────────┐ │    │
│  │                                   │ subprocess   │ │    │
│  │                                   │ node + TS    │ │    │
│  │                                   │ (sandcastle) │ │    │
│  │                                   └─────────────┘ │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │              SQLite (async)                        │    │
│  │  projects │ scheduled_messages │ sandcastle_runs   │    │
│  │           │ sandcastle_config  │ sandcastle_logs   │    │
│  └──────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
```

### Key design decision: Python subprocess wrapper

Sandcastle is a TypeScript/Node.js library. The CC backend is Python/FastAPI. Two integration approaches:

- **Option A**: Install `@ai-hero/sandcastle` as npm package, write a thin Node.js orchestrator script, call it via `subprocess` from Python.
- **Option B**: Port the core sandcastle logic to Python (asyncio subprocess managing Docker/Podman commands).

**Recommendation: Option A** — Use sandcastle as-is via a Node.js subprocess wrapper. Reasons:
1. Sandcastle is actively maintained; porting creates maintenance burden.
2. The Docker/Podman interactions are just CLI commands — sandcastle adds branch management and agent lifecycle on top.
3. The Node.js process communicates results via JSON on stdout, easy to parse from Python.

---

## 3. Database Model

### New table: `sandcastle_config` (per-project settings)

```python
# backend/app/models/sandcastle.py

class SandcastleConfig(Base):
    """Per-project sandcastle configuration."""
    __tablename__ = "sandcastle_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_path: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    sandbox_provider: Mapped[str] = mapped_column(String(32), default="no-sandbox")  # docker | podman | vercel | no-sandbox
    agent_provider: Mapped[str] = mapped_column(String(32), default="claude-code")  # claude-code | codex | cursor | pi | opencode | copilot
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    branch_strategy: Mapped[str] = mapped_column(String(32), default="merge-to-head")  # head | merge-to-head | branch
    docker_image: Mapped[str | None] = mapped_column(String(256), nullable=True)  # custom image name
    max_iterations: Mapped[int] = mapped_column(Integer, default=1)
    idle_timeout_seconds: Mapped[int] = mapped_column(Integer, default=600)
    permission_mode: Mapped[str] = mapped_column(String(32), default="acceptEdits")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
```

### New table: `sandcastle_runs` (run history)

```python
class SandcastleRun(Base):
    """Record of a sandcastle agent run."""
    __tablename__ = "sandcastle_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_path: Mapped[str] = mapped_column(String(1024), index=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("sandcastle_configs.id"))
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
        # pending | running | completed | failed | cancelled
    branch: Mapped[str | None] = mapped_column(String(256), nullable=True)
    commits: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{sha: "..."}]
    stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log_file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # structured output
```

---

## 4. API Design

All endpoints under `/api/v1/sandcastle`:

### Configuration (per-project)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/sandcastle/config?project_path=...` | Get sandcastle config for a project |
| `PUT` | `/sandcastle/config` | Create/update config for a project |
| `PATCH` | `/sandcastle/config/{id}/toggle` | Quick enable/disable toggle |
| `GET` | `/sandcastle/configs` | List all project configs |

### Runs

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/sandcastle/runs` | Start a new agent run (async — returns run ID) |
| `GET` | `/sandcastle/runs?project_path=...` | List runs for a project |
| `GET` | `/sandcastle/runs/{run_id}` | Get run status + output |
| `DELETE` | `/sandcastle/runs/{run_id}` | Cancel a running agent (kills subprocess) |
| `GET` | `/sandcastle/runs/{run_id}/logs` | Stream logs (SSE or polling) |

### System

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/sandcastle/health` | Check Docker/Podman availability + sandcastle binary |
| `GET` | `/sandcastle/providers` | List available sandbox providers |

### Backend router structure

```
backend/app/
├── api/v1/sandcastle/
│   ├── __init__.py
│   └── router.py          # FastAPI router with all endpoints above
├── models/
│   └── sandcastle.py       # SandcastleConfig + SandcastleRun ORM models
├── services/
│   └── sandcastle_service.py  # Business logic: config CRUD, run orchestration
└── services/sandcastle/
    ├── __init__.py
    ├── orchestrator.py     # Node.js subprocess management
    ├── config.py           # Config validation, defaults
    └── runner.py           # Run lifecycle: start, poll, cancel, logs
```

### Node.js wrapper script

```
backend/scripts/
└── sandcastle_runner.mjs   # Entry point called via subprocess
```

This script:
1. Receives config JSON via stdin or CLI args
2. Calls `sandcastle.run()` with the appropriate providers
3. Streams stdout/stderr to a log file
4. Outputs JSON result on completion

---

## 5. Frontend Design

### New feature module: `frontend/src/features/sandcastle/`

```
frontend/src/features/sandcastle/
├── SandcastlePage.tsx         # Main page: config + run history
├── SandcastleConfigCard.tsx   # Per-project configuration card
├── SandcastleRunList.tsx      # List of runs with status badges
├── SandcastleRunCard.tsx      # Individual run card (expandable)
├── SandcastleRunDialog.tsx    # "New Run" dialog (prompt, options)
├── SandboxHealthBadge.tsx     # Docker/Podman availability indicator
├── api.ts                     # API client functions
└── types.ts                   # TypeScript types
```

### UI Layout

```
SandcastlePage
├── SandcastleConfigCard (top)
│   ├── Enable/Disable toggle (Switch component)
│   ├── Sandbox Provider selector (Docker/Podman/No-Sandbox)
│   ├── Agent Provider selector (Claude Code/Codex/etc)
│   ├── Model selector (optional)
│   ├── Branch Strategy selector
│   ├── Docker Image input (if Docker/Podman selected)
│   ├── Advanced: max iterations, idle timeout, permission mode
│   └── SandboxHealthBadge (green/red indicator)
├── "New Run" button → SandcastleRunDialog
│   ├── Prompt textarea (or prompt file upload)
│   ├── Optional: branch name override
│   ├── Optional: max iterations override
│   └── Run button
└── SandcastleRunList (bottom)
    ├── Filter: All / Running / Completed / Failed
    └── SandcastleRunCard[] (each expandable)
        ├── Status badge (pending/running/completed/failed)
        ├── Branch name + commit count
        ├── Duration
        ├── Expandable stdout/stderr
        └── Cancel button (if running)
```

### Sidebar integration

Add "Sandcastle" nav item under "Operations" group in `Sidebar.tsx`:

```tsx
// In commonNavigation[1].items (Operations group):
{ name: 'Sandcastle', href: '/sandcastle', icon: Castle },
```

### Per-project toggle in sidebar

The per-project toggle is handled at the page level (like scheduled messages), not in the sidebar itself. The sidebar shows the Sandcastle link; the SandcastlePage reads the current project from `ProjectContext` and shows the enable/disable toggle for that project.

### Route in App.tsx

```tsx
const SandcastlePage = lazy(() => import('./features/sandcastle/SandcastlePage').then(m => ({ default: m.SandcastlePage })))
// ...
<Route path="sandcastle" element={<SandcastlePage />} />
```

---

## 6. Integration Points

### 6.1 Sandcastle ↔ Agent Bridge

When sandcastle spawns a run, the running Node.js subprocess creates a tmux session (or Docker container). Agent Bridge's `discover_agent_sessions()` should optionally pick these up:

- **Approach**: Sandcastle sessions are tagged with a naming convention: `sc-{project}-{hash}`. Agent Bridge discovery filters can include/exclude these.
- **Minimal change**: No modification to Agent Bridge needed initially — sandcastle runs live in their own Docker containers, not tmux. Future enhancement: surface container logs in Agent Bridge.

### 6.2 Sandcastle ↔ Scheduled Messages

Scheduled messages can trigger sandcastle runs:

- Add a new `target_kind` value: `"sandcastle"` on `ScheduledMessage`.
- When a scheduled message fires with `target_kind="sandcastle"`, instead of `tmux send-keys`, it creates a sandcastle run with the message as the prompt.
- This requires adding a `sandcastle_config_id` field to `ScheduledMessage`.

### 6.3 Sandcastle ↔ Kanban

Kanban auto-dispatch could trigger sandcastle runs for parallel task execution:

- A kanban card in "In Progress" state triggers a sandcastle run with the card description as prompt.
- The run's branch becomes the card's deliverable (`kind: "branch"`).

### 6.4 Sandcastle ↔ CC Bridge

No direct integration — they serve different use cases:
- CC Bridge: interactive human-in-the-loop sessions
- Sandcastle: automated headless agent runs

---

## 7. Implementation Phases

### Phase 1: Foundation (Core feature module)

**Goal**: Basic sandcastle feature with config and single runs.

| Step | Files | Description |
|------|-------|-------------|
| 1.1 | `backend/app/models/sandcastle.py` | ORM models (SandcastleConfig, SandcastleRun) |
| 1.2 | `backend/app/services/sandcastle_service.py` | Config CRUD + run start/stop/status |
| 1.3 | `backend/scripts/sandcastle_runner.mjs` | Node.js subprocess entry point |
| 1.4 | `backend/app/api/v1/sandcastle/router.py` | REST API endpoints |
| 1.5 | `backend/app/api/v1/router.py` | Register sandcastle router |
| 1.6 | `frontend/src/features/sandcastle/types.ts` | TypeScript types |
| 1.7 | `frontend/src/features/sandcastle/api.ts` | API client |
| 1.8 | `frontend/src/features/sandcastle/SandcastlePage.tsx` | Main page |
| 1.9 | `frontend/src/features/sandcastle/SandcastleConfigCard.tsx` | Config card |
| 1.10 | `frontend/src/features/sandcastle/SandcastleRunList.tsx` | Run list |
| 1.11 | `frontend/src/features/sandcastle/SandcastleRunDialog.tsx` | New run dialog |
| 1.12 | `frontend/src/components/layout/Sidebar.tsx` | Add Sandcastle nav item |
| 1.13 | `frontend/src/App.tsx` | Add route |
| 1.14 | npm install `@ai-hero/sandcastle` in backend (or project root) |

### Phase 2: Docker Integration

**Goal**: Sandcastle runs in Docker containers with branch management.

| Step | Files | Description |
|------|-------|-------------|
| 2.1 | `.sandcastle/Dockerfile` | Sandcastle Docker image |
| 2.2 | `backend/app/services/sandcastle/runner.py` | Docker-specific run logic |
| 2.3 | `SandboxHealthBadge.tsx` | Docker/Podman availability check |
| 2.4 | `backend/app/api/v1/sandcastle/router.py` | Health endpoint |

### Phase 3: Multi-Agent & Structured Output

**Goal**: Parallel runs, structured output extraction, log streaming.

| Step | Files | Description |
|------|-------|-------------|
| 3.1 | `SandcastleRunCard.tsx` | Expandable run details with stdout/stderr |
| 3.2 | `backend/app/services/sandcastle/runner.py` | Log streaming, structured output parsing |
| 3.3 | `backend/app/api/v1/sandcastle/router.py` | SSE log streaming endpoint |
| 3.4 | Support `Promise.all` parallel runs from UI |

### Phase 4: Cross-Feature Integration

**Goal**: Wire sandcastle into scheduled messages, kanban, agent bridge.

| Step | Files | Description |
|------|-------|-------------|
| 4.1 | `backend/app/models/scheduled_message.py` | Add `target_kind="sandcastle"` support |
| 4.2 | `backend/app/services/scheduling/scheduler.py` | Dispatch to sandcastle when target_kind=sandcastle |
| 4.3 | Kanban dispatch integration (optional) | Trigger sandcastle from kanban cards |

---

## 8. File Inventory

### Files to create

| Path | Purpose |
|------|---------|
| `backend/app/models/sandcastle.py` | ORM models |
| `backend/app/api/v1/sandcastle/__init__.py` | Package init |
| `backend/app/api/v1/sandcastle/router.py` | API endpoints |
| `backend/app/services/sandcastle/__init__.py` | Package init |
| `backend/app/services/sandcastle/runner.py` | Subprocess orchestration |
| `backend/app/services/sandcastle/config.py` | Config validation |
| `backend/scripts/sandcastle_runner.mjs` | Node.js entry point |
| `frontend/src/features/sandcastle/SandcastlePage.tsx` | Main page |
| `frontend/src/features/sandcastle/SandcastleConfigCard.tsx` | Config card |
| `frontend/src/features/sandcastle/SandcastleRunList.tsx` | Run list |
| `frontend/src/features/sandcastle/SandcastleRunCard.tsx` | Run card |
| `frontend/src/features/sandcastle/SandcastleRunDialog.tsx` | New run dialog |
| `frontend/src/features/sandcastle/SandboxHealthBadge.tsx` | Health indicator |
| `frontend/src/features/sandcastle/api.ts` | API client |
| `frontend/src/features/sandcastle/types.ts` | TypeScript types |
| `docs/cockpit/sandcastle-integration-plan.md` | This plan |

### Files to modify

| Path | Change |
|------|--------|
| `backend/app/api/v1/router.py` | Import + include sandcastle router |
| `backend/app/models/__init__.py` | Import SandcastleConfig, SandcastleRun |
| `frontend/src/App.tsx` | Add lazy import + route for SandcastlePage |
| `frontend/src/components/layout/Sidebar.tsx` | Add Sandcastle to Operations nav group |
| `backend/app/models/scheduled_message.py` | Add sandcastle_config_id FK (Phase 4) |

---

## 9. Risk Assessment

- [ ] **Docker dependency**: Sandcastle requires Docker/Podman for sandboxed runs. No-sandbox fallback exists but loses isolation.
- [ ] **Node.js subprocess**: The Python→Node.js subprocess boundary adds complexity. Need robust error handling and process lifecycle management.
- [ ] **No database migrations**: CC uses `create_all()` — adding new tables requires deleting `claude_registry.db` (or manually running `CREATE TABLE`).
- [ ] **npm package maturity**: `@ai-hero/sandcastle` is at v0.11.0 — API may still evolve.
- [ ] **Container image management**: Users need to build/pull Docker images with the right CLI tools installed.
- [ ] **Resource consumption**: Docker containers for agent runs consume memory/CPU; need timeouts and cleanup.
