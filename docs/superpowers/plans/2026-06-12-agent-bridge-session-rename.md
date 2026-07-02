# Agent Bridge Session Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users name an Agent Bridge session after its feature (worktree) name at spawn time and rename it later via an inline pencil edit on the session card.

**Architecture:** Names are the real tmux session names (`tmux rename-session`) — no separate store. The spawn helper accepts a *preferred* name (the worktree name, or an explicit field) and falls back to the existing `<basename>-<uuid>` default. A new rename service + endpoint changes the live tmux session; the frontend refreshes the list and re-targets attached panes by their (stable) `pane_id`.

**Tech Stack:** FastAPI (backend service + router), pytest with `monkeypatch`/`SimpleNamespace` fakes for `subprocess.run`, React 19 + TypeScript + shadcn/ui (frontend).

---

## File Structure

**Backend**
- `backend/app/services/agent_bridge/spawn.py` — add `_sanitize_session_name`, `_running_session_names`, `preferred` arg on `_session_name_for`, `session_name` arg on `spawn_session`, new `rename_session`.
- `backend/app/api/v1/agent_bridge/router.py` — `session_name` on `SpawnRequest`, new `RenameRequest` + `POST /sessions/{target}/rename`.
- `backend/tests/test_agent_bridge_spawn.py` — extend with preferred-name + collision tests.
- `backend/tests/test_agent_bridge_rename.py` — new, covers `rename_session`.

**Frontend**
- `frontend/src/features/cc-bridge/types.ts` — `session_name?` on `SpawnSessionRequest`, new `RenameSessionResponse`.
- `frontend/src/features/cc-bridge/api.ts` — `renameSession()`.
- `frontend/src/features/cc-bridge/NewSessionDialog.tsx` — optional "Session name" field.
- `frontend/src/features/cc-bridge/SessionCard.tsx` — inline pencil edit.
- `frontend/src/features/cc-bridge/SessionList.tsx` — thread `onRename`.
- `frontend/src/features/cc-bridge/CCBridgePage.tsx` — `handleRename` + re-target attached panes.

Backend commands assume `cd backend && source venv/bin/activate` first. Frontend commands assume `cd frontend`.

---

### Task 1: Backend — preferred name + sanitize helpers

**Files:**
- Modify: `backend/app/services/agent_bridge/spawn.py:32-35` (and `spawn_session` signature ~`:38`)
- Test: `backend/tests/test_agent_bridge_spawn.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_agent_bridge_spawn.py`:

```python
def test_sanitize_session_name_strips_invalid_chars():
    from app.services.agent_bridge import spawn

    assert spawn._sanitize_session_name("My Feature!") == "My-Feature"
    assert spawn._sanitize_session_name("---") == ""
    assert spawn._sanitize_session_name("a" * 40) == "a" * 20


def test_session_name_for_uses_preferred_when_free(monkeypatch):
    from app.services.agent_bridge import spawn

    monkeypatch.setattr(spawn, "_running_session_names", lambda: set())

    assert spawn._session_name_for("/tmp/whatever", preferred="my-feature") == "my-feature"


def test_session_name_for_adds_suffix_on_collision(monkeypatch):
    from app.services.agent_bridge import spawn

    monkeypatch.setattr(spawn, "_running_session_names", lambda: {"my-feature"})
    monkeypatch.setattr(spawn.uuid, "uuid4", lambda: SimpleNamespace(hex="deadbeef"))

    assert spawn._session_name_for("/tmp/whatever", preferred="my-feature") == "my-feature-dead"


def test_spawn_session_uses_worktree_name_as_session_name(monkeypatch, tmp_path):
    from app.services.agent_bridge import spawn
    from app.services.providers.base import SpawnCommandOptions

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_running_session_names", lambda: set())
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()

    result = spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="worktree", worktree_name="my-feature"),
    )

    assert result["session_name"] == "my-feature"
    assert calls[0][:5] == ["tmux", "new-session", "-d", "-s", "my-feature"]


def test_spawn_session_explicit_session_name_overrides(monkeypatch, tmp_path):
    from app.services.agent_bridge import spawn
    from app.services.providers.base import SpawnCommandOptions

    def fake_run(args, capture_output=True, text=True, timeout=10):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_running_session_names", lambda: set())
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()

    result = spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="plain"),
        session_name="custom name",
    )

    assert result["session_name"] == "custom-name"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_bridge_spawn.py -v`
Expected: the 5 new tests FAIL (`AttributeError: ... has no attribute '_sanitize_session_name'` / `_running_session_names`, and `spawn_session() got an unexpected keyword argument 'session_name'`). The 4 pre-existing tests still PASS.

- [ ] **Step 3: Implement the helpers and wire them in**

In `backend/app/services/agent_bridge/spawn.py`, replace `_session_name_for` (lines 32-35) with:

```python
def _sanitize_session_name(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "-", raw).strip("-")[:20]


def _running_session_names() -> set[str]:
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return set()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _session_name_for(directory: str, preferred: str | None = None) -> str:
    if preferred:
        base = _sanitize_session_name(preferred)
        if base and base not in _running_session_names():
            return base
        return f"{base or 'session'}-{uuid.uuid4().hex[:4]}"
    basename = Path(directory).name or "project"
    safe_basename = _sanitize_session_name(basename) or "project"
    return f"{safe_basename}-{uuid.uuid4().hex[:4]}"
```

Then change the `spawn_session` signature (line 38) and the name resolution (line 47). Signature:

```python
def spawn_session(provider_id: str, options: SpawnCommandOptions, session_name: str | None = None) -> dict:
```

Replace line 47 (`name = _session_name_for(directory)`) with:

```python
    preferred = session_name or (options.worktree_name if options.mode == "worktree" else None)
    name = _session_name_for(directory, preferred) if preferred else _session_name_for(directory)
```

(The existing worktree-name defaulting block on lines 48-49 stays unchanged: when no worktree name was given it still falls back to `name`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_bridge_spawn.py -v`
Expected: all tests PASS (4 original + 5 new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_bridge/spawn.py backend/tests/test_agent_bridge_spawn.py
git commit -m "feat(agent-bridge): default session name to feature/worktree name at spawn"
```

---

### Task 2: Backend — rename_session service

**Files:**
- Modify: `backend/app/services/agent_bridge/spawn.py` (add `rename_session`)
- Test: `backend/tests/test_agent_bridge_rename.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_agent_bridge_rename.py`:

```python
"""Tests for renaming an Agent Bridge tmux session."""
from types import SimpleNamespace


def test_rename_session_renames_tmux_and_moves_metadata(monkeypatch):
    from app.services.agent_bridge import spawn

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_running_session_names", lambda: {"old-name"})
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()
    spawn.get_spawned_sessions()["old-name"] = {"provider": "claude-code", "mode": "worktree"}

    result = spawn.rename_session("old-name", "New Name!")

    assert result == {"renamed": True, "session_name": "New-Name", "tmux_target": "New-Name:0.0"}
    assert calls[0] == ["tmux", "rename-session", "-t", "old-name", "New-Name"]
    assert "old-name" not in spawn.get_spawned_sessions()
    assert spawn.get_spawned_sessions()["New-Name"]["mode"] == "worktree"


def test_rename_session_rejects_empty_name(monkeypatch):
    from app.services.agent_bridge import spawn
    import pytest

    monkeypatch.setattr(spawn, "_running_session_names", lambda: set())

    with pytest.raises(ValueError):
        spawn.rename_session("old-name", "---")


def test_rename_session_rejects_collision(monkeypatch):
    from app.services.agent_bridge import spawn
    import pytest

    monkeypatch.setattr(spawn, "_running_session_names", lambda: {"old-name", "taken"})

    with pytest.raises(ValueError):
        spawn.rename_session("old-name", "taken")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_bridge_rename.py -v`
Expected: FAIL with `AttributeError: module 'app.services.agent_bridge.spawn' has no attribute 'rename_session'`.

- [ ] **Step 3: Implement `rename_session`**

Append to `backend/app/services/agent_bridge/spawn.py` (after `kill_session`, before `get_spawned_sessions`):

```python
def rename_session(old_name: str, new_name: str) -> dict:
    """Rename a tmux session, keeping spawn metadata under the new key."""
    sanitized = _sanitize_session_name(new_name)
    if not sanitized:
        raise ValueError("Session name must contain a letter, number, '_' or '-'")
    if sanitized == old_name:
        return {"renamed": True, "session_name": old_name, "tmux_target": f"{old_name}:0.0"}
    if sanitized in _running_session_names():
        raise ValueError(f"A session named '{sanitized}' already exists")

    try:
        result = subprocess.run(
            ["tmux", "rename-session", "-t", old_name, sanitized],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise ValueError(f"tmux rename-session failed: {result.stderr.strip()}")
    except FileNotFoundError:
        raise ValueError("tmux is not installed or not in PATH")
    except subprocess.TimeoutExpired:
        raise ValueError("tmux rename-session timed out")

    metadata = _spawned_sessions.pop(old_name, None)
    if metadata is not None:
        _spawned_sessions[sanitized] = metadata

    logger.info("Renamed session %s -> %s", old_name, sanitized)
    return {"renamed": True, "session_name": sanitized, "tmux_target": f"{sanitized}:0.0"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_bridge_rename.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_bridge/spawn.py backend/tests/test_agent_bridge_rename.py
git commit -m "feat(agent-bridge): rename_session service (tmux rename-session)"
```

---

### Task 3: Backend — router (spawn field + rename endpoint)

**Files:**
- Modify: `backend/app/api/v1/agent_bridge/router.py:15` (import), `:27-48` (`SpawnRequest`), `:119-147` (spawn endpoint), and add a new endpoint after the DELETE route (`:153`).

- [ ] **Step 1: Add `session_name` to `SpawnRequest` and pass it through**

In `backend/app/api/v1/agent_bridge/router.py`, add to `SpawnRequest` (e.g. right after the `provider` field on line 28):

```python
    session_name: str | None = None
```

Update the import on line 15 to include `rename_session`:

```python
from app.services.agent_bridge.spawn import kill_session, rename_session, spawn_session
```

Change the spawn call (line 145) from `return spawn_session(request.provider, options)` to:

```python
        return spawn_session(request.provider, options, session_name=request.session_name)
```

- [ ] **Step 2: Add the rename endpoint**

Add after the DELETE route (after line 152):

```python
class RenameRequest(BaseModel):
    name: str


@router.post("/sessions/{target}/rename")
def rename_session_endpoint(target: str, request: RenameRequest):
    try:
        return rename_session(old_name=target, new_name=request.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
```

- [ ] **Step 3: Smoke-test the import and routes load**

Run: `cd backend && source venv/bin/activate && python -c "from app.main import app; print([r.path for r in app.routes if 'rename' in r.path])"`
Expected: prints a list containing `/api/v1/agent-bridge/sessions/{target}/rename`.

- [ ] **Step 4: Run the backend test suite for the bridge**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_bridge_spawn.py tests/test_agent_bridge_rename.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/agent_bridge/router.py
git commit -m "feat(agent-bridge): session_name spawn field + rename endpoint"
```

---

### Task 4: Frontend — types + API client

**Files:**
- Modify: `frontend/src/features/cc-bridge/types.ts:33-55` (`SpawnSessionRequest`), add `RenameSessionResponse` near `KillSessionResponse` (`:62-65`).
- Modify: `frontend/src/features/cc-bridge/api.ts` (add `renameSession`).

- [ ] **Step 1: Add the request field and response type**

In `frontend/src/features/cc-bridge/types.ts`, add to `SpawnSessionRequest` (after `directory`):

```ts
  session_name?: string
```

Add after `KillSessionResponse`:

```ts
export interface RenameSessionResponse {
  renamed: boolean
  session_name: string
  tmux_target: string
}
```

- [ ] **Step 2: Add the API call**

In `frontend/src/features/cc-bridge/api.ts`, extend the type import on line 3 to include `RenameSessionResponse`, then add:

```ts
export async function renameSession(sessionName: string, name: string): Promise<RenameSessionResponse> {
  return apiClient<RenameSessionResponse>(`${BASE}/sessions/${encodeURIComponent(sessionName)}/rename`, {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}
```

- [ ] **Step 3: Verify it type-checks**

Run: `cd frontend && npm run build`
Expected: build succeeds (no TS errors). (`npm run lint` will report `renameSession`/`RenameSessionResponse` as unused until Tasks 5-7 wire them — that is expected; do not delete them.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/cc-bridge/types.ts frontend/src/features/cc-bridge/api.ts
git commit -m "feat(agent-bridge): frontend types + renameSession API client"
```

---

### Task 5: Frontend — "Session name" field in New Session dialog

**Files:**
- Modify: `frontend/src/features/cc-bridge/NewSessionDialog.tsx`

- [ ] **Step 1: Add state + reset**

After line 89 (`const [worktreeName, setWorktreeName] = useState('')`), add:

```tsx
  const [sessionName, setSessionName] = useState('')
```

In the reset-on-close effect, after `setWorktreeName('')` (line 169), add:

```tsx
      setSessionName('')
```

- [ ] **Step 2: Render the input**

Immediately after the Mode selector block (after its closing `</div>` on line 313, before the Directory block), add:

```tsx
          {/* Optional explicit session name */}
          <div className="space-y-1.5">
            <Label htmlFor="session-name">Session name</Label>
            <Input
              id="session-name"
              value={sessionName}
              onChange={(e) => setSessionName(e.target.value)}
              placeholder="auto"
              autoComplete="off"
            />
            <p className="text-xs text-muted-foreground">
              Optional. Defaults to the worktree name, or an auto-generated name.
            </p>
          </div>
```

- [ ] **Step 3: Include it in the spawn request**

In `handleLaunch`, inside the `request` object (after the `mode` line, line 219), add:

```tsx
        ...(sessionName.trim() && { session_name: sessionName.trim() }),
```

- [ ] **Step 4: Verify build + manual check**

Run: `cd frontend && npm run build`
Expected: build succeeds.
Manual: open New Session, type a name, launch → the new card uses that name (verified end-to-end in Task 7).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/cc-bridge/NewSessionDialog.tsx
git commit -m "feat(agent-bridge): optional session-name field in New Session dialog"
```

---

### Task 6: Frontend — inline pencil edit on SessionCard

**Files:**
- Modify: `frontend/src/features/cc-bridge/SessionCard.tsx`

- [ ] **Step 1: Replace the card with an edit-capable version**

Replace the entire contents of `frontend/src/features/cc-bridge/SessionCard.tsx` with:

```tsx
import { useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { CLICKABLE_CARD } from '@/lib/constants'
import { cn } from '@/lib/utils'
import type { CCSession } from './types'
import type { AttentionKind } from './attention'

interface SessionCardProps {
  session: CCSession
  gridPosition: number | null
  onClick: () => void
  onKill: (session: CCSession) => void
  onRename: (session: CCSession, newName: string) => Promise<void>
  attention?: AttentionKind | null
}

export function SessionCard({ session, gridPosition, onClick, onKill, onRename, attention }: SessionCardProps) {
  const projectName = session.cwd.split('/').pop() || session.cwd
  const isActive = gridPosition !== null

  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(session.session_name)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function startEdit() {
    setValue(session.session_name)
    setError(null)
    setEditing(true)
  }

  function cancelEdit() {
    setEditing(false)
    setError(null)
  }

  async function commitEdit() {
    const next = value.trim()
    if (!next || next === session.session_name) {
      cancelEdit()
      return
    }
    setSaving(true)
    setError(null)
    try {
      await onRename(session, next)
      setEditing(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Rename failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card
      className={cn(CLICKABLE_CARD, isActive && 'border-primary bg-primary/5')}
      onClick={editing ? undefined : onClick}
      onKeyDown={(e) => {
        if (editing) return
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
      tabIndex={editing ? -1 : 0}
      role="button"
    >
      <CardContent className="p-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 min-w-0">
            {attention && (
              <span
                className={cn(
                  'h-2 w-2 rounded-full shrink-0',
                  attention === 'error' ? 'bg-red-500' : 'bg-yellow-500'
                )}
                title={attention === 'error' ? 'Command failed' : 'Waiting for input'}
              />
            )}
            {editing ? (
              <Input
                autoFocus
                value={value}
                disabled={saving}
                className="h-6 text-sm py-0"
                onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => {
                  e.stopPropagation()
                  if (e.key === 'Enter') { e.preventDefault(); void commitEdit() }
                  if (e.key === 'Escape') { e.preventDefault(); cancelEdit() }
                }}
                onChange={(e) => setValue(e.target.value)}
                onBlur={() => { if (!saving) void commitEdit() }}
              />
            ) : (
              <span className="text-sm font-medium truncate">{session.session_name}</span>
            )}
          </div>
          {!editing && (
            <div className="flex items-center gap-1.5 shrink-0">
              <button
                className="h-5 w-5 flex items-center justify-center rounded text-muted-foreground/50 hover:text-foreground transition-colors"
                onClick={(e) => { e.stopPropagation(); startEdit() }}
                onKeyDown={(e) => e.stopPropagation()}
                title="Rename session"
              >
                <Pencil className="h-3 w-3" />
              </button>
              <button
                className="h-5 w-5 flex items-center justify-center rounded text-muted-foreground/50 hover:text-destructive transition-colors"
                onClick={(e) => { e.stopPropagation(); onKill(session) }}
                onKeyDown={(e) => e.stopPropagation()}
                title="Kill session"
              >
                <Trash2 className="h-3 w-3" />
              </button>
              {isActive ? (
                <span className="h-5 w-5 flex items-center justify-center rounded-full bg-primary text-primary-foreground text-xs font-bold">
                  {gridPosition + 1}
                </span>
              ) : (
                <span className="h-2 w-2 rounded-full bg-green-500" />
              )}
            </div>
          )}
        </div>
        {error && (
          <p className="text-xs text-destructive mt-1">{error}</p>
        )}
        <Badge variant="outline" className="mt-2 max-w-full truncate">
          {session.provider_display_name}
        </Badge>
        <p className="text-xs text-muted-foreground truncate mt-1" title={session.cwd}>
          {projectName}
        </p>
        <p className="text-xs text-muted-foreground mt-0.5">
          {session.tmux_target}
        </p>
      </CardContent>
    </Card>
  )
}
```

- [ ] **Step 2: Verify build (expects one error)**

Run: `cd frontend && npm run build`
Expected: FAIL — `SessionList.tsx` does not yet pass the required `onRename` prop. This is fixed in Task 7. (If you prefer a green build between tasks, do Step 1 of Task 7 before building.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/cc-bridge/SessionCard.tsx
git commit -m "feat(agent-bridge): inline pencil rename on session card"
```

---

### Task 7: Frontend — wire onRename through the list and page

**Files:**
- Modify: `frontend/src/features/cc-bridge/SessionList.tsx`
- Modify: `frontend/src/features/cc-bridge/CCBridgePage.tsx`

- [ ] **Step 1: Thread `onRename` through `SessionList`**

In `frontend/src/features/cc-bridge/SessionList.tsx`, add to `SessionListProps` (after `onKillSession`):

```tsx
  onRename: (session: CCSession, newName: string) => Promise<void>
```

Add `onRename` to the destructured props (after `onKillSession,`):

```tsx
  onRename,
```

Pass it to each card — in the `<SessionCard ... />` JSX (after `onKill={onKillSession}`):

```tsx
              onRename={onRename}
```

- [ ] **Step 2: Add `renameSession` import + handler in the page**

In `frontend/src/features/cc-bridge/CCBridgePage.tsx`, update the api import on line 8-ish. The current import is:

```tsx
import { NewSessionDialog } from './NewSessionDialog'
```

Add a new import line below the existing feature imports:

```tsx
import { renameSession } from './api'
```

Add the handler near `handleSpawned` (after line 140):

```tsx
  const handleRename = useCallback(async (session: CCSession, newName: string) => {
    const res = await renameSession(session.session_name, newName)
    const oldTarget = session.tmux_target
    const newTarget = res.tmux_target
    setActiveTargets((prev) => prev.map((t) => (t === oldTarget ? newTarget : t)))
    setFocusedTarget((cur) => (cur === oldTarget ? newTarget : cur))
    setFullscreenTarget((cur) => (cur === oldTarget ? newTarget : cur))
    refresh()
  }, [refresh])
```

- [ ] **Step 3: Pass the handler to `SessionList`**

In the `<SessionList ... />` JSX, after `onKillSession={setKillSession}`, add:

```tsx
              onRename={handleRename}
```

- [ ] **Step 4: Verify build + lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: both succeed with no errors (the unused-symbol warnings from Task 4 are now resolved).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/cc-bridge/SessionList.tsx frontend/src/features/cc-bridge/CCBridgePage.tsx
git commit -m "feat(agent-bridge): wire inline rename through list and page"
```

---

### Task 8: End-to-end manual verification

**Files:** none (manual).

- [ ] **Step 1: Start the dev stack**

Run backend: `cd backend && source venv/bin/activate && uvicorn app.main:app --reload --port 8000`
Run frontend (separate shell): `cd frontend && npm run dev`
(Runs directly under WSL — no Docker.)

- [ ] **Step 2: Spawn with a feature name**

In Agent Bridge → New Session: pick a project, Mode = Worktree, set Worktree Name `my-feature`, leave Session name blank → Launch.
Expected: the new card's title is `my-feature` (not `<dir>-<uuid>`), and its `tmux_target` reads `my-feature:0.0`.

- [ ] **Step 3: Rename via the pencil**

Click the pencil on the card, type `renamed-feature`, press Enter.
Expected: card title updates to `renamed-feature`, target becomes `renamed-feature:0.0`. If the session was attached in the grid, the terminal pane stays attached (re-targeted). The attention dot, if present, still tracks the same pane.

- [ ] **Step 4: Collision + empty-name guard**

Spawn a second session, rename it to `renamed-feature` (the existing name).
Expected: inline red error "A session named 'renamed-feature' already exists"; the card keeps its old name. Try renaming to `---` → inline error, no change.

- [ ] **Step 5: Kill + worktree cleanup**

Kill the renamed worktree session with cleanup.
Expected: session disappears and (for a worktree session) the git worktree is removed — confirming the metadata moved with the rename.

---

## Self-Review Notes

- **Spec coverage:** default-to-feature-name (Task 1 + Task 5), explicit name at spawn (Task 1 + Task 5), rename later inline (Tasks 2-3 backend, 6-7 frontend), tmux as source of truth (Tasks 1-2), attention unaffected / re-target by pane_id (Task 7 handler + Task 8 step 3), sanitize/uniqueness/empty-name errors (Tasks 1-2, verified in Task 8 step 4), worktree-cleanup metadata move (Task 2 + Task 8 step 5). All covered.
- **Type consistency:** `rename_session` returns `{renamed, session_name, tmux_target}` (Task 2) == `RenameSessionResponse` (Task 4) == consumed in `handleRename` (Task 7). `renameSession(sessionName, name)` signature matches the page call `renameSession(session.session_name, newName)`. `onRename: (session, newName) => Promise<void>` is identical in SessionCard (Task 6), SessionList (Task 7), and the page handler (Task 7).
- **Backward compat:** existing `_session_name_for` monkeypatches use a single-arg lambda; `spawn_session` only calls it with the `preferred` arg when `preferred` is truthy, so the 4 pre-existing spawn tests keep passing (verified in Task 1 Step 4).
```
