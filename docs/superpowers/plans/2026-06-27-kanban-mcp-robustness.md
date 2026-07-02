# Kanban MCP Robustness & Error Visibility — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the kanban MCP more robust (structured errors, DB retry, real health probe) and show a persistent red banner on the board when the MCP SSE server is unreachable.

**Architecture:** A new `_probe_sse` helper is extracted to make the health endpoint testable. All MCP tools get a `_safe` decorator that catches exceptions and returns structured error dicts. The frontend polls `/kanban/mcp-health` every 30 s and shows a sticky `McpHealthBanner` when the probe fails.

**Tech Stack:** FastAPI, httpx (backend probe), SQLAlchemy/aiosqlite, React 19, shadcn/ui `Alert`, TypeScript, pytest-asyncio

## Global Constraints

- Python 3.11+, FastAPI patterns used throughout the codebase
- Frontend: strict TypeScript, ESLint (`noUnusedLocals`, `noUnusedParameters`), path alias `@/*`
- No new shadcn components needed — `alert.tsx` already exists
- Tests live in `backend/tests/`, use `ASGITransport` + `AsyncClient` from httpx, and `pytest.mark.asyncio`
- No frontend test infrastructure — frontend tasks use manual testing steps
- `httpx` is used in tests but not in `pyproject.toml` main deps; add it in Task 1

---

### Task 1: Backend — `_probe_sse` helper + `GET /mcp-health` endpoint

**Files:**
- Modify: `backend/pyproject.toml` — add `httpx` to main deps
- Modify: `backend/app/api/v1/kanban/router.py` — add `_probe_sse` + `GET /mcp-health`
- Modify: `backend/tests/test_kanban_api.py` — add two tests for the health endpoint

**Interfaces:**
- Produces: `GET /api/v1/kanban/mcp-health` → `{"healthy": bool, "latency_ms": int | null, "error": str | null}`
- Produces: `async def _probe_sse(url: str, headers: dict, timeout: float) -> tuple[bool, int | None, str | None]`

- [ ] **Step 1: Add httpx to pyproject.toml**

Open `backend/pyproject.toml`. In the `dependencies` list, add `"httpx>=0.27.0"` after the `mcp` line:

```toml
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "sqlalchemy>=2.0.25",
    "pydantic>=2.6.0",
    "pydantic-settings>=2.1.0",
    "python-dotenv>=1.0.0",
    "aiosqlite>=0.19.0",
    "tomlkit>=0.13.2",
    "mcp>=1.2.0",
    "httpx>=0.27.0",
    "bcrypt>=4.0.0",
]
```

- [ ] **Step 2: Write the failing tests**

At the bottom of `backend/tests/test_kanban_api.py`, add:

```python
from unittest.mock import patch, AsyncMock


@pytest.mark.asyncio
async def test_mcp_health_returns_healthy_when_sse_responds():
    """mcp-health returns healthy:true when the probe succeeds."""
    async def fake_probe(url, headers, timeout):
        return True, 42, None

    with patch("app.api.v1.kanban.router._probe_sse", fake_probe):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.get("/api/v1/kanban/mcp-health")

    assert r.status_code == 200
    data = r.json()
    assert data["healthy"] is True
    assert data["latency_ms"] == 42
    assert data["error"] is None


@pytest.mark.asyncio
async def test_mcp_health_returns_unhealthy_when_sse_unreachable():
    """mcp-health returns healthy:false when the probe raises."""
    async def fake_probe(url, headers, timeout):
        return False, None, "Connect timeout"

    with patch("app.api.v1.kanban.router._probe_sse", fake_probe):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.get("/api/v1/kanban/mcp-health")

    assert r.status_code == 200
    data = r.json()
    assert data["healthy"] is False
    assert data["latency_ms"] is None
    assert data["error"] == "Connect timeout"
```

- [ ] **Step 3: Run tests — expect failure**

```bash
cd backend && source venv/bin/activate && pytest tests/test_kanban_api.py::test_mcp_health_returns_healthy_when_sse_responds tests/test_kanban_api.py::test_mcp_health_returns_unhealthy_when_sse_unreachable -v
```

Expected: FAILED — `404 Not Found` (endpoint doesn't exist yet).

- [ ] **Step 4: Implement `_probe_sse` and `GET /mcp-health`**

In `backend/app/api/v1/kanban/router.py`, add at the top-level (after the imports, before `router = APIRouter(...)`):

```python
import time
import httpx


async def _probe_sse(url: str, headers: dict, timeout: float) -> tuple[bool, int | None, str | None]:
    """Probe the SSE endpoint. Returns (healthy, latency_ms, error_str)."""
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", url, headers=headers, timeout=timeout) as r:
                healthy = r.status_code == 200
                latency_ms = int((time.monotonic() - t0) * 1000)
                error = None if healthy else f"HTTP {r.status_code}"
                return healthy, latency_ms, error
    except Exception as exc:
        return False, None, str(exc)
```

Then add the endpoint (anywhere in router.py, e.g. after `mcp_status`):

```python
@router.get("/mcp-health")
async def mcp_health():
    """Probe whether the kanban MCP SSE server is actually reachable."""
    headers: dict = {}
    if settings.api_token:
        headers["Authorization"] = f"Bearer {settings.api_token}"
    healthy, latency_ms, error = await _probe_sse(
        "http://localhost:8000/kanban-mcp/sse", headers, timeout=2.0
    )
    return {"healthy": healthy, "latency_ms": latency_ms, "error": error}
```

Note: `time` and `httpx` are added at the top of the file with the existing imports.

- [ ] **Step 5: Run tests — expect pass**

```bash
cd backend && source venv/bin/activate && pytest tests/test_kanban_api.py::test_mcp_health_returns_healthy_when_sse_responds tests/test_kanban_api.py::test_mcp_health_returns_unhealthy_when_sse_unreachable -v
```

Expected: both PASSED.

- [ ] **Step 6: Run full test suite**

```bash
cd backend && source venv/bin/activate && pytest tests/ -x -q
```

Expected: all pass (no regressions).

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/app/api/v1/kanban/router.py backend/tests/test_kanban_api.py
git commit -m "feat(kanban): add GET /mcp-health endpoint with SSE probe"
```

---

### Task 2: Backend — MCP tool `_safe` decorator with DB retry

**Files:**
- Modify: `backend/app/kanban/mcp_server.py` — add `_safe` decorator, apply to all tools
- Modify: `backend/tests/test_kanban_mcp.py` — add tests for the decorator

**Interfaces:**
- Produces: `_safe` decorator — wraps any `async` MCP tool, returns `{"error": str, "type": str}` on failure, retries once on `OperationalError`

- [ ] **Step 1: Write the failing tests**

At the bottom of `backend/tests/test_kanban_mcp.py`, add:

```python
from sqlalchemy.exc import OperationalError


@pytest.mark.asyncio
async def test_safe_decorator_catches_unexpected_exceptions():
    """_safe wraps any async fn and returns a structured error dict on failure."""
    from app.kanban.mcp_server import _safe

    @_safe
    async def broken():
        raise ValueError("kaboom")

    result = await broken()
    assert result == {"error": "kaboom", "type": "ValueError"}


@pytest.mark.asyncio
async def test_safe_decorator_retries_once_on_sqlite_locked():
    """_safe retries once on OperationalError (SQLite database locked)."""
    from app.kanban.mcp_server import _safe

    call_count = 0

    @_safe
    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OperationalError("database is locked", None, None)
        return {"ok": True}

    result = await flaky()
    assert result == {"ok": True}
    assert call_count == 2


@pytest.mark.asyncio
async def test_safe_decorator_returns_error_if_retry_also_fails():
    """_safe returns error dict when the retry also raises."""
    from app.kanban.mcp_server import _safe

    @_safe
    async def always_locked():
        raise OperationalError("database is locked", None, None)

    result = await always_locked()
    assert "error" in result
    assert result["type"] == "OperationalError"
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd backend && source venv/bin/activate && pytest tests/test_kanban_mcp.py::test_safe_decorator_catches_unexpected_exceptions tests/test_kanban_mcp.py::test_safe_decorator_retries_once_on_sqlite_locked tests/test_kanban_mcp.py::test_safe_decorator_returns_error_if_retry_also_fails -v
```

Expected: FAILED — `ImportError: cannot import name '_safe'`.

- [ ] **Step 3: Implement the `_safe` decorator in `mcp_server.py`**

At the top of `backend/app/kanban/mcp_server.py`, after the existing imports, add:

```python
import asyncio
import functools
import logging
import traceback

from sqlalchemy.exc import OperationalError

logger = logging.getLogger(__name__)


def _safe(fn):
    """Catch all exceptions from an MCP tool. Retries once on SQLite OperationalError."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except OperationalError:
            logger.warning("MCP tool %s: SQLite contention, retrying once", fn.__name__)
            await asyncio.sleep(0.1)
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                logger.error("MCP tool %s failed after retry:\n%s",
                             fn.__name__, traceback.format_exc())
                return {"error": str(exc), "type": type(exc).__name__}
        except Exception as exc:
            logger.error("MCP tool %s failed:\n%s", fn.__name__, traceback.format_exc())
            return {"error": str(exc), "type": type(exc).__name__}
    return wrapper
```

- [ ] **Step 4: Apply `@_safe` to every tool in `mcp_server.py`**

Every `@mcp.tool()` function gets `@_safe` added directly below it (so `_safe` runs first, then `mcp.tool()` sees the safe-wrapped function). Apply to all 12 tools: `list_cards`, `get_card`, `create_card`, `claim_card`, `move_card`, `update_card`, `comment`, `attach_deliverable`, `release_card`, `report_impediment`, `set_resume`, `redispatch_card`.

Example — change this pattern:

```python
@mcp.tool()
async def list_cards(project: str, column: str | None = None) -> list[dict]:
```

to:

```python
@mcp.tool()
@_safe
async def list_cards(project: str, column: str | None = None) -> list[dict]:
```

Repeat for every tool function. Do not change any function body.

- [ ] **Step 5: Run the decorator tests — expect pass**

```bash
cd backend && source venv/bin/activate && pytest tests/test_kanban_mcp.py::test_safe_decorator_catches_unexpected_exceptions tests/test_kanban_mcp.py::test_safe_decorator_retries_once_on_sqlite_locked tests/test_kanban_mcp.py::test_safe_decorator_returns_error_if_retry_also_fails -v
```

Expected: all PASSED.

- [ ] **Step 6: Run full kanban MCP test suite**

```bash
cd backend && source venv/bin/activate && pytest tests/test_kanban_mcp.py tests/test_kanban_mcp_auth.py tests/test_kanban_mcp_mount.py -v
```

Expected: all PASSED. The existing tests should still pass because `_safe` is transparent when there's no exception.

- [ ] **Step 7: Run full test suite**

```bash
cd backend && source venv/bin/activate && pytest tests/ -x -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/app/kanban/mcp_server.py backend/tests/test_kanban_mcp.py
git commit -m "feat(kanban): add _safe decorator with DB retry to all MCP tools"
```

---

### Task 3: Frontend — `mcpHealth` API call + `McpHealthBanner` component

**Files:**
- Modify: `frontend/src/features/kanban/api.ts` — add `mcpHealth()`
- Create: `frontend/src/features/kanban/components/McpHealthBanner.tsx`

**Interfaces:**
- Produces: `kanbanApi.mcpHealth()` → `Promise<{ healthy: boolean; latency_ms: number | null; error: string | null }>`
- Produces: `<McpHealthBanner error={string | null} onRetry={() => void} />` — renders nothing when healthy, sticky red alert when not

- [ ] **Step 1: Add `mcpHealth` to `api.ts`**

Open `frontend/src/features/kanban/api.ts`. Add the following entry to the `kanbanApi` object, after `mcpStatus`:

```typescript
  mcpHealth: (): Promise<{ healthy: boolean; latency_ms: number | null; error: string | null }> =>
    apiClient<{ healthy: boolean; latency_ms: number | null; error: string | null }>(
      `${BASE}/mcp-health`
    ),
```

- [ ] **Step 2: Create `McpHealthBanner.tsx`**

Create `frontend/src/features/kanban/components/McpHealthBanner.tsx` with this content:

```tsx
import { AlertTriangle } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

interface McpHealthBannerProps {
  error: string | null;
  onRetry: () => void;
}

export function McpHealthBanner({ error, onRetry }: McpHealthBannerProps) {
  return (
    <Alert
      variant="destructive"
      className="flex items-center justify-between flex-shrink-0 py-2"
    >
      <div className="flex items-center gap-2">
        <AlertTriangle className="h-4 w-4 flex-shrink-0" />
        <AlertDescription>
          <span className="font-medium">MCP unreachable</span>
          {error && <span className="ml-1 opacity-80">— {error}</span>}
          <span className="ml-2 text-sm opacity-60">
            Agents cannot use kanban tools until this is resolved.
          </span>
        </AlertDescription>
      </div>
      <Button
        size="sm"
        variant="outline"
        onClick={onRetry}
        className="ml-4 flex-shrink-0 border-destructive/50 hover:bg-destructive/10"
      >
        Retry
      </Button>
    </Alert>
  );
}
```

- [ ] **Step 3: Check TypeScript compiles**

```bash
cd frontend && npm run build 2>&1 | tail -20
```

Expected: Build succeeds, no TypeScript errors mentioning `McpHealthBanner` or `mcpHealth`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/kanban/api.ts frontend/src/features/kanban/components/McpHealthBanner.tsx
git commit -m "feat(kanban): add mcpHealth API call and McpHealthBanner component"
```

---

### Task 4: Frontend — Wire up polling in `KanbanPage` + status dot in `EnableKanbanToggle`

**Files:**
- Modify: `frontend/src/features/kanban/KanbanPage.tsx` — add `mcpEnabled` state, polling, banner
- Modify: `frontend/src/features/kanban/components/EnableKanbanToggle.tsx` — add status dot + `onEnabledChange` prop

**Interfaces:**
- Consumes: `kanbanApi.mcpHealth()` from Task 3
- Consumes: `<McpHealthBanner>` from Task 3
- Consumes: `EnableKanbanToggle` props extended with `mcpHealth` and `onEnabledChange`

- [ ] **Step 1: Extend `EnableKanbanToggle` props**

Open `frontend/src/features/kanban/components/EnableKanbanToggle.tsx`.

Replace the props interface and component signature:

```tsx
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { kanbanApi } from "../api";

interface McpHealthState {
  healthy: boolean;
}

export function EnableKanbanToggle({
  projectPath,
  onChanged,
  mcpHealth,
  onEnabledChange,
}: {
  projectPath: string;
  onChanged: () => void;
  mcpHealth?: McpHealthState | null;
  onEnabledChange?: (enabled: boolean) => void;
}) {
  const [enabled, setEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    if (!projectPath) return;
    kanbanApi
      .mcpStatus(projectPath)
      .then((r) => {
        setEnabled(r.enabled);
        onEnabledChange?.(r.enabled);
      })
      .catch(() => {
        setEnabled(false);
        onEnabledChange?.(false);
      });
  }, [projectPath, onEnabledChange]);

  if (!projectPath || enabled === null) return null;

  const dot = enabled ? (
    <span
      className={cn(
        "inline-block w-2 h-2 rounded-full mr-1.5 flex-shrink-0",
        mcpHealth == null
          ? "bg-muted-foreground/40"
          : mcpHealth.healthy
          ? "bg-green-400"
          : "bg-red-400"
      )}
    />
  ) : null;

  return (
    <div className="flex gap-2">
      <Button
        size="sm"
        variant={enabled ? "default" : "outline"}
        onClick={async () => {
          try {
            await kanbanApi.enable(projectPath);
            setEnabled(true);
            onEnabledChange?.(true);
            toast.success("Kanban enabled (MCP registered)");
            onChanged();
          } catch {
            toast.error("Failed to enable kanban");
          }
        }}
      >
        {dot}
        {enabled ? "MCP: enabled" : "Enable MCP"}
      </Button>
      {enabled && (
        <Button
          size="sm"
          variant="ghost"
          onClick={async () => {
            try {
              await kanbanApi.disable(projectPath);
              setEnabled(false);
              onEnabledChange?.(false);
              toast.success("Kanban disabled");
              onChanged();
            } catch {
              toast.error("Failed to disable kanban");
            }
          }}
        >
          Disable
        </Button>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Wire polling + banner in `KanbanPage`**

Open `frontend/src/features/kanban/KanbanPage.tsx`.

Add these imports at the top (alongside existing imports):

```tsx
import { McpHealthBanner } from "./components/McpHealthBanner";
```

Add two new state variables and the `checkHealth` callback, directly after the existing state declarations:

```tsx
const [mcpEnabled, setMcpEnabled] = useState(false);
const [mcpHealth, setMcpHealth] = useState<{
  healthy: boolean;
  latency_ms: number | null;
  error: string | null;
} | null>(null);

const checkHealth = useCallback(async () => {
  try {
    const h = await kanbanApi.mcpHealth();
    setMcpHealth(h);
  } catch {
    setMcpHealth({ healthy: false, latency_ms: null, error: "Health check failed" });
  }
}, []);
```

Add a new `useEffect` for the health poll, after the existing `useEffect` blocks:

```tsx
useEffect(() => {
  if (!mcpEnabled) {
    setMcpHealth(null);
    return;
  }
  void checkHealth();
  const id = setInterval(() => void checkHealth(), 30_000);
  return () => clearInterval(id);
}, [mcpEnabled, checkHealth]);
```

In the JSX, add the banner between the header div and `<Board>`. Find the line:

```tsx
      <Board
```

And add before it:

```tsx
      {mcpEnabled && mcpHealth && !mcpHealth.healthy && (
        <McpHealthBanner error={mcpHealth.error} onRetry={checkHealth} />
      )}
```

Update the `EnableKanbanToggle` usage to pass the new props. Find:

```tsx
          <EnableKanbanToggle projectPath={projectPath} onChanged={reload} />
```

Replace with:

```tsx
          <EnableKanbanToggle
            projectPath={projectPath}
            onChanged={reload}
            mcpHealth={mcpEnabled ? mcpHealth : null}
            onEnabledChange={setMcpEnabled}
          />
```

- [ ] **Step 3: Check TypeScript compiles**

```bash
cd frontend && npm run build 2>&1 | tail -20
```

Expected: Build succeeds, no TypeScript errors.

- [ ] **Step 4: Run ESLint**

```bash
cd frontend && npm run lint 2>&1 | tail -20
```

Expected: no new warnings or errors.

- [ ] **Step 5: Manual test — happy path**

Start the dev stack: `./scripts/cockpit.sh start`  
Open `http://localhost:5173`, navigate to Kanban.

1. MCP enabled for a project → button shows a **green dot** within 1–2 seconds of page load
2. MCP disabled → button shows no dot
3. MCP enabled, backend running → **no banner** above the board

- [ ] **Step 6: Manual test — error path**

1. With MCP enabled, stop the backend: `./scripts/cockpit.sh stop`
2. (Frontend still served by Vite dev server)
3. Wait up to 30 s or click Retry if already visible
4. A **red banner** appears: "MCP unreachable — …" with a Retry button
5. The toggle button shows a **red dot**
6. Restart backend: `./scripts/cockpit.sh start` (wait a few seconds)
7. Click **Retry** → banner disappears, dot turns green

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/kanban/KanbanPage.tsx frontend/src/features/kanban/components/EnableKanbanToggle.tsx
git commit -m "feat(kanban): poll mcp-health every 30s, show sticky banner and status dot on failure"
```

---

## Self-Review

**Spec coverage:**
- ✅ `GET /kanban/mcp-health` probing SSE with httpx — Task 1
- ✅ `_safe` decorator on all MCP tools — Task 2
- ✅ SQLite retry once on OperationalError — Task 2
- ✅ `mcpHealth()` frontend API call — Task 3
- ✅ `McpHealthBanner` sticky red alert — Task 3, 4
- ✅ 30 s polling from KanbanPage when enabled — Task 4
- ✅ Status dot (grey/green/red) in EnableKanbanToggle — Task 4
- ✅ Poll stops when MCP disabled — Task 4 (`useEffect` cleanup)
- ✅ Retry button calls `checkHealth` immediately — Task 4

**Placeholder scan:** None found.

**Type consistency:**
- `mcpHealth` state shape `{ healthy, latency_ms, error }` matches `mcpHealth()` return type throughout Tasks 3–4
- `McpHealthState` in `EnableKanbanToggle` uses only `{ healthy }` (correct — the dot only cares about healthy/not)
- `_probe_sse` returns `tuple[bool, int | None, str | None]` — consumed correctly in `mcp_health` endpoint
