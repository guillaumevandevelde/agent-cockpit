---
title: "Pane-Targeted Attention Implementation Plan"
type: plan
status: active
---

# Pane-Targeted Attention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Agent Bridge show exactly which session needs input, and let an attention notification attach the exact tmux pane, by joining Presence and Bridge on the tmux pane id.

**Architecture:** A CC hook forwards `$TMUX_PANE`; the presence backend stores it as `tmux_pane` and exposes it on every `session_update`. The Agent Bridge subscribes to the presence WebSocket, joins `presence.tmux_pane == bridge.pane_id`, renders per-row/per-pane badges, and deep-links notifications to `/cc-bridge?attach=<pane_id>` which auto-attaches that pane.

**Tech Stack:** FastAPI + async SQLAlchemy + aiosqlite (backend), pytest/pytest-asyncio (tests), React 19 + TypeScript + Vite + react-router (frontend).

**Spec:** `docs/cockpit/pane-attention-spec.md`

---

## File Structure

**Backend**
- Modify `backend/app/models/schemas.py` — `tmux_pane` on `PresenceEventIn` and `PresenceSessionResponse`.
- Modify `backend/app/models/database.py` — `tmux_pane` column on `PresenceSession`.
- Modify `backend/app/services/presence_service.py` — store `tmux_pane` in `process_event`, expose in `_to_response`.
- Modify `backend/app/api/v1/presence.py` — config-snippet command-hook variant.
- Create `backend/tests/test_presence_tmux_pane.py` — service + snippet tests.

**Frontend**
- Modify `frontend/src/types/presence.ts` — `tmux_pane?` on `PresenceSession`.
- Create `frontend/src/features/cc-bridge/attention.ts` — `AttentionKind` + `paneAttentionKind()`.
- Create `frontend/src/features/cc-bridge/useAttentionByPane.ts` — presence-WS → `Map<pane_id, AttentionKind>`.
- Modify `frontend/src/features/cc-bridge/CCBridgePage.tsx` — join + badges wiring + `?attach=` auto-attach.
- Modify `frontend/src/features/cc-bridge/SessionList.tsx` — pass attention through.
- Modify `frontend/src/features/cc-bridge/SessionCard.tsx` — render badge.
- Modify `frontend/src/features/cc-bridge/TerminalView.tsx` — pane-header indicator.
- Modify `frontend/src/hooks/useAttentionNotifications.ts` — navigate to bridge via `tmux_pane`.

---

## Task 1: Backend — store & expose `tmux_pane`

**Files:**
- Modify: `backend/app/models/schemas.py` (`PresenceEventIn`, `PresenceSessionResponse`)
- Modify: `backend/app/models/database.py` (`PresenceSession`)
- Modify: `backend/app/services/presence_service.py` (`process_event`, `_to_response`)
- Test: `backend/tests/test_presence_tmux_pane.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_presence_tmux_pane.py`:

```python
import pytest

from app.database import Base, engine, AsyncSessionLocal
from app.services.presence_service import PresenceService


@pytest.mark.asyncio
async def test_process_event_stores_and_exposes_tmux_pane():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    service = PresenceService()
    async with AsyncSessionLocal() as db:
        resp = await service.process_event(
            {
                "session_id": "sess-pane-1",
                "hook_event_name": "UserPromptSubmit",
                "cwd": "/home/dev/project-x",
                "tmux_pane": "%7",
            },
            db,
        )
        await db.commit()
    assert resp.tmux_pane == "%7"


@pytest.mark.asyncio
async def test_absent_tmux_pane_does_not_overwrite_existing():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    service = PresenceService()
    async with AsyncSessionLocal() as db:
        await service.process_event(
            {
                "session_id": "sess-pane-2",
                "hook_event_name": "UserPromptSubmit",
                "cwd": "/home/dev/project-y",
                "tmux_pane": "%3",
            },
            db,
        )
        # A later event without tmux_pane must not clear the stored value.
        resp = await service.process_event(
            {
                "session_id": "sess-pane-2",
                "hook_event_name": "Stop",
                "cwd": "/home/dev/project-y",
            },
            db,
        )
        await db.commit()
    assert resp.tmux_pane == "%3"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_presence_tmux_pane.py -v`
Expected: FAIL — `PresenceSessionResponse` has no `tmux_pane` (AttributeError / validation), or the ORM column does not exist.

- [ ] **Step 3: Add the ORM column**

In `backend/app/models/database.py`, in `class PresenceSession`, add after the `last_user_prompt` column (around line 172):

```python
    tmux_pane: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 4: Add the schema fields**

In `backend/app/models/schemas.py`, in `class PresenceEventIn`, add after `permission_mode`:

```python
    tmux_pane: Optional[str] = None
```

In `class PresenceSessionResponse`, add a `tmux_pane` field (near `project_path`):

```python
    tmux_pane: Optional[str] = None
```

- [ ] **Step 5: Store and expose in the service**

In `backend/app/services/presence_service.py`, inside `process_event`, near the cwd-derivation block (after the `if cwd and not session.label:` block, ~line 105), add:

```python
        # Tmux pane id (from the hook's $TMUX_PANE) — the exact join key to the
        # Agent Bridge. Only set when present so a later event without it (e.g. a
        # non-tmux event) doesn't clear it.
        pane = payload.get("tmux_pane")
        if pane:
            session.tmux_pane = pane
```

In `_to_response`, add to the `PresenceSessionResponse(...)` constructor:

```python
            tmux_pane=session.tmux_pane,
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_presence_tmux_pane.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/schemas.py backend/app/models/database.py backend/app/services/presence_service.py backend/tests/test_presence_tmux_pane.py
git commit -m "feat(presence): store and expose tmux_pane on sessions"
```

---

## Task 2: Backend — config-snippet command-hook variant

**Files:**
- Modify: `backend/app/api/v1/presence.py` (`get_config_snippet`)
- Test: `backend/tests/test_presence_tmux_pane.py` (append)

The current snippet emits HTTP hooks, which cannot forward env vars. A **command** hook can: it receives the hook JSON on stdin, merges in `$TMUX_PANE` with `jq`, and posts to `/events`. Using `. + {tmux_pane:$p}` is a passthrough — it forwards exactly what Claude Code sends plus the pane, so no field remapping is introduced.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_presence_tmux_pane.py`:

```python
@pytest.mark.asyncio
async def test_config_snippet_is_command_hook_with_tmux_pane():
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get("/api/v1/presence/config-snippet")
    assert r.status_code == 200
    snippet = r.json()["snippet"]
    stop_hook = snippet["hooks"]["Stop"][0]["hooks"][0]
    assert stop_hook["type"] == "command"
    assert "$TMUX_PANE" in stop_hook["command"]
    assert "tmux_pane" in stop_hook["command"]
    assert "/api/v1/presence/events" in stop_hook["command"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_presence_tmux_pane.py::test_config_snippet_is_command_hook_with_tmux_pane -v`
Expected: FAIL — current hook `type` is `"http"`, no `command` key.

- [ ] **Step 3: Implement the command-hook snippet**

In `backend/app/api/v1/presence.py`, replace the body of `get_config_snippet` (the `url`/`events`/`snippet`/`instructions` block, ~lines 99-115) with:

```python
    url = "http://localhost:8000/api/v1/presence/events"
    events = [
        "Notification", "PreToolUse", "PostToolUse", "Stop",
        "SessionStart", "SessionEnd", "UserPromptSubmit",
        "SubagentStart", "SubagentStop",
    ]
    # Command hook: forward the hook JSON from stdin plus $TMUX_PANE (the exact
    # join key to the Agent Bridge), then POST to the events endpoint.
    command = (
        "jq -c --arg p \"$TMUX_PANE\" '. + {tmux_pane:$p}' | "
        f"curl -sf -X POST {url} -H 'Content-Type: application/json' -d @-"
    )
    snippet = {
        "hooks": {
            event: [{"hooks": [{"type": "command", "command": command}]}]
            for event in events
        }
    }
    instructions = (
        "Add this to your ~/.claude/settings.json (or merge into existing hooks). "
        "Requires `jq` and `curl` on PATH. Then restart any running Claude Code "
        "sessions for the hooks to take effect."
    )
    return PresenceConfigSnippet(snippet=snippet, instructions=instructions)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_presence_tmux_pane.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/presence.py backend/tests/test_presence_tmux_pane.py
git commit -m "feat(presence): emit command-hook snippet that forwards \$TMUX_PANE"
```

---

## Task 3: Frontend — presence type + attention helper

**Files:**
- Modify: `frontend/src/types/presence.ts`
- Create: `frontend/src/features/cc-bridge/attention.ts`

Badges reflect *current* persistent state (waiting/error), not transient narratives — so a stale 🔐 badge never lingers. The transient 🔐 notification stays the job of `useAttentionNotifications`.

- [ ] **Step 1: Add `tmux_pane` to the presence type**

In `frontend/src/types/presence.ts`, in `interface PresenceSession`, add after `project_path?: string`:

```typescript
  tmux_pane?: string
```

- [ ] **Step 2: Create the attention helper**

Create `frontend/src/features/cc-bridge/attention.ts`:

```typescript
import type { PresenceSession } from '@/types/presence'

/** Persistent attention state shown as a badge in the Agent Bridge. */
export type AttentionKind = 'input' | 'error'

/** Current attention state of a presence session, or null if none. */
export function paneAttentionKind(session: PresenceSession): AttentionKind | null {
  if (session.status === 'error') return 'error'
  if (session.status === 'stopped') return 'input'
  return null
}
```

- [ ] **Step 3: Verify it compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds (tsc + vite), 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/presence.ts frontend/src/features/cc-bridge/attention.ts
git commit -m "feat(cc-bridge): add tmux_pane type and attention helper"
```

---

## Task 4: Frontend — `useAttentionByPane` hook

**Files:**
- Create: `frontend/src/features/cc-bridge/useAttentionByPane.ts`

Subscribes to the presence WebSocket and exposes a live `Map<pane_id, AttentionKind>` for joining against bridge sessions by `pane_id`.

- [ ] **Step 1: Create the hook**

Create `frontend/src/features/cc-bridge/useAttentionByPane.ts`:

```typescript
import { useCallback, useMemo, useState } from 'react'
import { usePresenceWebSocket } from '@/hooks/usePresenceWebSocket'
import type { PresenceSession } from '@/types/presence'
import { paneAttentionKind, type AttentionKind } from './attention'

/**
 * Live map of tmux pane id -> attention state, derived from the presence WS.
 * Join against Agent Bridge sessions via `session.pane_id`.
 */
export function useAttentionByPane(): Map<string, AttentionKind> {
  const [sessions, setSessions] = useState<Map<string, PresenceSession>>(new Map())

  const onSessionUpdate = useCallback((session: PresenceSession) => {
    setSessions((prev) => {
      const next = new Map(prev)
      next.set(session.session_id, session)
      return next
    })
  }, [])

  const onSessionRemove = useCallback((sessionId: string) => {
    setSessions((prev) => {
      const next = new Map(prev)
      next.delete(sessionId)
      return next
    })
  }, [])

  const onSessionsCleared = useCallback(() => setSessions(new Map()), [])

  usePresenceWebSocket({
    onSessionUpdate,
    onSessionRemove,
    onSessionsCleared,
    enabled: true,
  })

  return useMemo(() => {
    const map = new Map<string, AttentionKind>()
    for (const s of sessions.values()) {
      if (!s.tmux_pane) continue
      const kind = paneAttentionKind(s)
      if (kind) map.set(s.tmux_pane, kind)
    }
    return map
  }, [sessions])
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds, 0 errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/cc-bridge/useAttentionByPane.ts
git commit -m "feat(cc-bridge): live pane->attention map from presence WS"
```

---

## Task 5: Frontend — badge in the session list

**Files:**
- Modify: `frontend/src/features/cc-bridge/SessionCard.tsx`
- Modify: `frontend/src/features/cc-bridge/SessionList.tsx`
- Modify: `frontend/src/features/cc-bridge/CCBridgePage.tsx`

- [ ] **Step 1: Add an `attention` prop + badge to SessionCard**

In `frontend/src/features/cc-bridge/SessionCard.tsx`:

Add the import at the top:

```typescript
import type { AttentionKind } from './attention'
```

Extend the props interface:

```typescript
interface SessionCardProps {
  session: CCSession
  gridPosition: number | null
  onClick: () => void
  onKill: (session: CCSession) => void
  attention?: AttentionKind | null
}
```

Update the signature:

```typescript
export function SessionCard({ session, gridPosition, onClick, onKill, attention }: SessionCardProps) {
```

Inside the `<div className="flex items-center justify-between">` header row, immediately before the `<span className="text-sm font-medium truncate">{session.session_name}</span>`, add a dot:

```tsx
          {attention && (
            <span
              className={cn(
                'h-2 w-2 rounded-full shrink-0',
                attention === 'error' ? 'bg-red-500' : 'bg-yellow-500'
              )}
              title={attention === 'error' ? 'Command failed' : 'Waiting for input'}
            />
          )}
```

- [ ] **Step 2: Thread `attentionByPane` through SessionList**

In `frontend/src/features/cc-bridge/SessionList.tsx`:

Add the import:

```typescript
import type { AttentionKind } from './attention'
```

Add to `interface SessionListProps`:

```typescript
  attentionByPane: Map<string, AttentionKind>
```

Add `attentionByPane,` to the destructured params of `export function SessionList({ ... })`.

In the `sessions.map((session) => {` block, pass the prop to `<SessionCard>`:

```tsx
              attention={session.pane_id ? attentionByPane.get(session.pane_id) ?? null : null}
```

- [ ] **Step 3: Wire it from CCBridgePage**

In `frontend/src/features/cc-bridge/CCBridgePage.tsx`:

Add the import:

```typescript
import { useAttentionByPane } from './useAttentionByPane'
```

Inside `CCBridgePage`, after the existing `const { sessions, loading, error, refresh } = useCCSessions()`:

```typescript
  const attentionByPane = useAttentionByPane()
```

Pass it to `<SessionList ... />`:

```tsx
            attentionByPane={attentionByPane}
```

- [ ] **Step 4: Verify build + lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: build 0 errors; lint reports no new problems in `SessionCard.tsx`, `SessionList.tsx`, `CCBridgePage.tsx`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/cc-bridge/SessionCard.tsx frontend/src/features/cc-bridge/SessionList.tsx frontend/src/features/cc-bridge/CCBridgePage.tsx
git commit -m "feat(cc-bridge): attention badge on session list rows"
```

---

## Task 6: Frontend — pane-header indicator in TerminalView

**Files:**
- Modify: `frontend/src/features/cc-bridge/TerminalView.tsx`
- Modify: `frontend/src/features/cc-bridge/CCBridgePage.tsx`

- [ ] **Step 1: Add an `attention` prop + indicator to TerminalView**

In `frontend/src/features/cc-bridge/TerminalView.tsx`:

Add the import:

```typescript
import type { AttentionKind } from './attention'
```

Extend props and signature:

```typescript
interface TerminalViewProps {
  target: string | null
  fullscreen: boolean
  onToggleFullscreen: () => void
  onClose: () => void
  attention?: AttentionKind | null
}
```

```typescript
export function TerminalView({ target, fullscreen, onToggleFullscreen, onClose, attention }: TerminalViewProps) {
```

In the footer header `<div className="flex items-center justify-between px-3 py-2 border-t bg-background">`, inside the first `<div className="flex items-center gap-3">`, add as the first child:

```tsx
            {attention && (
              <span
                className={cn(
                  'h-2 w-2 rounded-full shrink-0',
                  attention === 'error' ? 'bg-red-500' : 'bg-yellow-500'
                )}
                title={attention === 'error' ? 'Command failed' : 'Waiting for input'}
              />
            )}
```

(`cn` is already imported in this file.)

- [ ] **Step 2: Pass per-pane attention from CCBridgePage**

In `frontend/src/features/cc-bridge/CCBridgePage.tsx`, build a target→pane_id lookup and pass attention to each `<TerminalView>`.

After `const attentionByPane = useAttentionByPane()`, add:

```typescript
  const paneByTarget = useMemo(() => {
    const map = new Map<string, string>()
    for (const s of sessions) {
      if (s.pane_id) map.set(s.tmux_target, s.pane_id)
    }
    return map
  }, [sessions])
```

Add `useMemo` to the React import at the top of the file:

```typescript
import { useState, useEffect, useCallback, useMemo } from 'react'
```

In the `activeTargets.map((target) => { ... })` block, where `<TerminalView ... />` is rendered, add:

```tsx
                      attention={(() => {
                        const pane = paneByTarget.get(target)
                        return pane ? attentionByPane.get(pane) ?? null : null
                      })()}
```

- [ ] **Step 3: Verify build + lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: build 0 errors; no new lint problems in the two files.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/cc-bridge/TerminalView.tsx frontend/src/features/cc-bridge/CCBridgePage.tsx
git commit -m "feat(cc-bridge): attention indicator on attached panes"
```

---

## Task 7: Frontend — notification deep-links to the exact pane

**Files:**
- Modify: `frontend/src/hooks/useAttentionNotifications.ts`
- Modify: `frontend/src/features/cc-bridge/CCBridgePage.tsx`

- [ ] **Step 1: Navigate to the bridge using tmux_pane**

In `frontend/src/hooks/useAttentionNotifications.ts`, the `fire` callback currently navigates to `/presence?session=...`. Change it to prefer the bridge when the session has a pane.

The `fire` signature currently receives `(sessionId, event)`. Change it to also take the session so it can read `tmux_pane`. Update `fire`:

```typescript
  const fire = useCallback(
    (session: PresenceSession, event: AttentionEvent) => {
      const notification = new Notification(event.title, {
        body: event.body,
        tag: event.tag,
      })
      notification.onclick = () => {
        window.focus()
        if (session.tmux_pane) {
          navigate(`/cc-bridge?attach=${encodeURIComponent(session.tmux_pane)}`)
        } else {
          navigate(`/presence?session=${encodeURIComponent(session.session_id)}`)
        }
        notification.close()
      }
    },
    [navigate],
  )
```

In `onSessionUpdate`, update the call site from `fire(session.session_id, event)` to:

```typescript
          fire(session, event)
```

- [ ] **Step 2: Auto-attach the pane in CCBridgePage**

In `frontend/src/features/cc-bridge/CCBridgePage.tsx`:

Add the import:

```typescript
import { useSearchParams } from 'react-router-dom'
```

Inside the component, add:

```typescript
  const [searchParams, setSearchParams] = useSearchParams()
```

Add an `attachTarget` helper that attaches even when the grid is full (drops the oldest), used only for the deep-link:

```typescript
  const attachTarget = useCallback((target: string) => {
    setActiveTargets((prev) => {
      if (prev.includes(target)) return prev
      if (prev.length >= MAX_GRID_PANES) return [...prev.slice(1), target]
      return [...prev, target]
    })
    setFocusedTarget(target)
  }, [])
```

Add the deep-link effect. It resolves `?attach=<pane_id>` to a discovered session's `tmux_target`, attaches it, and clears the param. If the pane isn't discovered yet, it refreshes once and retries on the next render:

```typescript
  useEffect(() => {
    const pane = searchParams.get('attach')
    if (!pane) return
    const match = sessions.find((s) => s.pane_id === pane)
    if (match) {
      attachTarget(match.tmux_target)
      const next = new URLSearchParams(searchParams)
      next.delete('attach')
      setSearchParams(next, { replace: true })
    } else {
      refresh()
    }
  }, [searchParams, sessions, attachTarget, refresh, setSearchParams])
```

- [ ] **Step 3: Verify build + lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: build 0 errors; no new lint problems in `useAttentionNotifications.ts`, `CCBridgePage.tsx`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/hooks/useAttentionNotifications.ts frontend/src/features/cc-bridge/CCBridgePage.tsx
git commit -m "feat(cc-bridge): notifications deep-link and auto-attach the exact pane"
```

---

## Task 8: Runtime validation (manual)

**Prereq:** This is end-to-end validation against real Claude Code + tmux. It needs `docker compose up` (or `./scripts/dev.sh`) and a logged-in `claude`. No automated test.

- [ ] **Step 1: Reset the presence DB (new column)**

The schema has no migrations (`create_all`). Stop the backend, then:

```bash
rm -f backend/claude_registry.db backend/claude_registry.db-wal backend/claude_registry.db-shm
```

Restart the backend so the table is recreated with `tmux_pane`.

- [ ] **Step 2: Install the command hook**

In the app: open the Agent Bridge / Presence "Connect" dialog and copy the generated snippet (now a **command** hook) into `~/.claude/settings.json`, or fetch it directly:

```bash
curl -s http://localhost:8000/api/v1/presence/config-snippet | jq .
```

Confirm the `Stop` hook is `type: command` and contains `$TMUX_PANE`. Restart any running `claude` sessions.

- [ ] **Step 3: Verify the join carries the pane**

Start a `claude` session inside a tmux pane, submit a prompt, then check:

```bash
curl -s http://localhost:8000/api/v1/presence/sessions | jq '.sessions[] | {session_id, status, tmux_pane}'
```

Expected: the session shows a non-empty `tmux_pane` (e.g. `"%0"`) matching that pane.

- [ ] **Step 4: Verify badges**

Open the Agent Bridge. Let the `claude` session finish (status → stopped). Expected: a yellow dot on that session's row in the list, and on its pane header if attached. Run a failing command in another session; expected: a red dot.

- [ ] **Step 5: Verify notification auto-attach**

Enable the bell (attention toggle), grant permission. Trigger a "waiting for input" transition. Click the desktop notification. Expected: the Agent Bridge opens and that exact pane is attached + focused.

- [ ] **Step 6: Commit any fixes**

If the command-hook payload field names from Claude Code differ from what `/events` expects (the spec's flagged risk), adjust the `jq` filter in `get_config_snippet` to remap fields, re-run Task 2's test, and commit.

---

## Self-Review Notes

- **Spec coverage:** §1 hook enrichment → Task 2 + Task 8.2; §2 backend store/expose → Task 1; §3 frontend join → Task 4 + Task 5.3; §4 indicators → Task 5 (list) + Task 6 (pane); §5 notification auto-attach → Task 7; DB-reset gotcha → Task 8.1; command-hook field-mapping risk → Task 8.6.
- **Badges scoped to `input`/`error`** (current persistent state), per the spec's note that 🔐 notifications are transient and remain the desktop-notification's job. This is a deliberate narrowing of §3's three states for the badge surface.
- **Types are consistent:** `AttentionKind = 'input' | 'error'`, `paneAttentionKind()`, `useAttentionByPane(): Map<string, AttentionKind>`, `attachTarget(target)`, `attentionByPane` prop — used identically across Tasks 3–7.
