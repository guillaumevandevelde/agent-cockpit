# Kanban MCP Robustness & Error Visibility

**Date:** 2026-06-27  
**Status:** Approved

## Problem

The kanban MCP has two compounding issues:

1. **`mcp_status` checks config, not reality.** The frontend reads `.mcp.json` to decide if MCP is "enabled", but doesn't verify the SSE server is actually reachable or healthy. If the backend crashes, the token rotates, or the SSE mount breaks, the board shows "MCP: enabled" while agents silently fail.

2. **MCP tools have no error boundary.** Unhandled exceptions in `mcp_server.py` propagate as opaque MCP errors to the agent. SQLite contention (busy/locked), missing cards, or logic errors produce no structured feedback and can leave the agent confused. Cards can end up stuck (claimed but dispatch failed).

The failure pattern is **wisselend** (varying) — sometimes tools aren't visible, sometimes calls fail — which points to connection issues being interleaved with tool-level failures.

## Goal

- **Primary:** MCP is more robust — tools don't crash silently, transient DB errors are retried, and errors are always structured
- **Secondary:** When something goes wrong, it is immediately visible on the board as a persistent indicator (not a fleeting toast)

## Out of Scope

- Agent-side error reporting (agents posting error cards)
- Last-activity (`mcp_last_call`) tracking — deferred to a future iteration
- Changing the MCP transport from SSE to anything else

---

## Design

### 1. Backend: `GET /api/v1/kanban/mcp-health`

New endpoint added to `router.py`. Makes an internal HTTP probe to the SSE server and returns a structured health response.

**Implementation:**

```python
@router.get("/mcp-health")
async def mcp_health():
    """Probe whether the kanban MCP SSE server is actually reachable."""
    import httpx, time
    headers = {}
    if settings.api_token:
        headers["Authorization"] = f"Bearer {settings.api_token}"
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "http://localhost:8000/kanban-mcp/sse",
                headers=headers,
                timeout=2.0,
            )
        latency_ms = int((time.monotonic() - t0) * 1000)
        healthy = r.status_code == 200
        error = None if healthy else f"HTTP {r.status_code}"
    except Exception as e:
        latency_ms = None
        healthy = False
        error = str(e)
    return {"healthy": healthy, "latency_ms": latency_ms, "error": error}
```

**Response schema:**
```json
{ "healthy": true, "latency_ms": 45, "error": null }
{ "healthy": false, "latency_ms": null, "error": "Connect timeout" }
```

Notes:
- Uses `httpx` (already in deps via FastAPI ecosystem) with a 2s timeout
- SSE connections stay open indefinitely; `httpx` will receive the `200 OK` header and the connection is closed by the client immediately after — latency measurement is connection establishment only
- Includes the API token header so auth issues are also detected

---

### 2. Backend: MCP Tool Error Boundary

All tools in `mcp_server.py` are wrapped with a decorator that:
- Catches all exceptions
- Logs with full traceback (to backend logs, not agent output)
- Returns `{"error": "<human message>", "type": "<ExceptionClassName>"}` instead of raising

This means the MCP session never crashes on tool errors — the agent receives a structured error it can act on (e.g., retry, report impediment, or surface to user).

**Decorator:**

```python
import asyncio, functools, logging, traceback

logger = logging.getLogger(__name__)

def _safe(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            logger.error("MCP tool %s failed:\n%s", fn.__name__, traceback.format_exc())
            return {"error": str(exc), "type": type(exc).__name__}
    return wrapper
```

Applied via `@mcp.tool()` + `@_safe` on every tool function.

**DB retry for transient SQLite errors:**

SQLite can raise `OperationalError: database is locked` under concurrent access. A single retry after 100ms covers most cases without adding meaningful latency.

```python
from sqlalchemy.exc import OperationalError

async def _with_retry(fn):
    try:
        return await fn()
    except OperationalError:
        await asyncio.sleep(0.1)
        return await fn()
```

The retry is applied inside the `_safe` wrapper per tool call, not at the decorator level, to keep the decorator generic.

---

### 3. Frontend: `mcpHealth` API call

Add to `frontend/src/features/kanban/api.ts`:

```typescript
mcpHealth: (): Promise<{ healthy: boolean; latency_ms: number | null; error: string | null }> =>
  apiClient<{ healthy: boolean; latency_ms: number | null; error: string | null }>(
    `${BASE}/mcp-health`
  ),
```

---

### 4. Frontend: Polling + Banner in `KanbanPage`

When MCP is enabled for the project, `KanbanPage` polls `mcp-health` every 30 seconds. State:

```typescript
const [mcpHealth, setMcpHealth] = useState<{ healthy: boolean; error: string | null } | null>(null);
```

On poll:
- Result → set `mcpHealth`
- If `enabled && mcpHealth && !mcpHealth.healthy` → render `McpHealthBanner`
- If `enabled && mcpHealth?.healthy` → banner hidden

Poll starts when `enabled === true`, stops when `enabled === false` (no unnecessary requests when MCP is off).

---

### 5. Frontend: `McpHealthBanner` Component

New file: `frontend/src/features/kanban/components/McpHealthBanner.tsx`

Renders a persistent red `Alert` above the `Board`:

```
┌─────────────────────────────────────────────────────────────┐
│ ⚠  MCP unreachable — Connect timeout                  [Retry]│
│    Agents cannot use kanban tools until this is resolved.    │
└─────────────────────────────────────────────────────────────┘
```

Props:
- `error: string | null` — shown as detail text
- `onRetry: () => void` — re-triggers the health check immediately

Uses `Alert` + `AlertDescription` from shadcn/ui. Styled with `variant="destructive"`.

---

### 6. Frontend: Status Dot in `EnableKanbanToggle`

The existing "MCP: enabled" / "Enable MCP" button gets a small dot indicator:

- **Grey dot** — health not yet checked (initial state)
- **Green dot** — `healthy: true`
- **Red dot** — `healthy: false`

The dot is a `w-2 h-2 rounded-full` inline element placed left of the label text. `EnableKanbanToggle` receives `mcpHealth` as a prop from `KanbanPage` (which already owns the poll state).

---

## Files Changed

| File | Change |
|---|---|
| `backend/app/api/v1/kanban/router.py` | Add `GET /mcp-health` endpoint |
| `backend/app/kanban/mcp_server.py` | Add `_safe` decorator + DB retry to all tools |
| `frontend/src/features/kanban/api.ts` | Add `mcpHealth()` |
| `frontend/src/features/kanban/KanbanPage.tsx` | Add poll + banner state + pass health to toggle |
| `frontend/src/features/kanban/components/McpHealthBanner.tsx` | New sticky alert component |
| `frontend/src/features/kanban/components/EnableKanbanToggle.tsx` | Add status dot prop |

## Error Handling

| Scenario | Result |
|---|---|
| Backend running, SSE healthy | `{healthy: true}`, banner hidden, green dot |
| Backend running, SSE returns non-200 | `{healthy: false, error: "HTTP 401"}`, red banner |
| Backend crashed / not reachable | health poll fails silently (no crash), banner stays visible |
| MCP tool throws exception | `{error: "...", type: "..."}` returned to agent, logged server-side |
| SQLite locked | One retry after 100ms; if still locked, returns structured error |
| MCP disabled for project | No health poll, no banner (polling only when enabled) |

## Testing

- Manual: start backend, load kanban board → green dot visible
- Manual: stop backend process, wait 30s → red banner appears with error
- Manual: restart backend → banner auto-clears on next poll
- Manual: cause a tool error (e.g., call `get_card` with invalid ID) → check backend logs for structured error, agent receives `{"error": "...", "type": "..."}` not a crash
