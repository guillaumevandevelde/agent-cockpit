# MiniMax Providers Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the MiniMax API key Save/Change/Clear form out of the "New Agent Session" dialog into a new, dedicated `/providers` page, leaving only the per-session Endpoint choice and a status-aware notice in the dialog.

**Architecture:** New `frontend/src/features/providers/` feature folder with a `MinimaxCredentialsCard` component (the form, relocated) rendered by a `ProvidersPage`, wired into `App.tsx` (route) and `navigation.ts` (nav item under the Claude Code provider group). `NewSessionDialog.tsx` loses its credential-editing state/handlers/JSX and gains a `react-router-dom` `Link` to `/providers`.

**Tech Stack:** React 19 + TypeScript + Vite, shadcn/ui components (`Card`, `Button`, `Input`, `Label`), react-router-dom. No backend changes — reuses the existing `fetchMinimaxPlatformStatus` / `setMinimaxApiKey` / `clearMinimaxApiKey` functions in `frontend/src/features/cc-bridge/api.ts`.

## Global Constraints

- No backend changes in this plan — `GET/POST/DELETE /api/v1/agent-bridge/platforms/minimax/{status,credentials}` are unchanged; do not touch `backend/`.
- This repo has **no frontend test runner** (documented in `CLAUDE.md` under Gotchas: "Frontend tests not yet set up"). Each frontend task substitutes `npx tsc --noEmit -p tsconfig.json` (from `frontend/`) and `npm run build` for the red/green test cycle — run both after every file change and expect zero errors. Do not add a test framework as part of this plan.
- Follow existing patterns exactly: page shell matches `frontend/src/features/hosts/HostsPage.tsx` (`<div className="space-y-6">` + `h1`/`p` header); settings form matches the `Card`/`CardHeader`/`CardTitle`/`CardDescription`/`CardContent` structure used in `frontend/src/features/statusline/StatusLinePage.tsx`.
- The MiniMax credential UI logic being moved already exists verbatim in `frontend/src/features/cc-bridge/NewSessionDialog.tsx` (lines 118–208 for state/handlers, lines 717–800 for JSX, as of this plan's writing) — copy its behavior, don't redesign it.

---

### Task 1: Create the Providers page (component + route + nav entry)

**Files:**
- Create: `frontend/src/features/providers/MinimaxCredentialsCard.tsx`
- Create: `frontend/src/features/providers/ProvidersPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/lib/navigation.ts`

**Interfaces:**
- Consumes: `fetchMinimaxPlatformStatus(): Promise<PlatformStatusResponse>`, `setMinimaxApiKey(key: string): Promise<PlatformStatusResponse>`, `clearMinimaxApiKey(): Promise<PlatformStatusResponse>` — all already exported from `frontend/src/features/cc-bridge/api.ts`, where `PlatformStatusResponse = { configured: boolean }`.
- Produces: `MinimaxCredentialsCard` (named export, no props) from `frontend/src/features/providers/MinimaxCredentialsCard.tsx`. `ProvidersPage` (named export, no props) from `frontend/src/features/providers/ProvidersPage.tsx`. Route `/providers`. Nav item labeled "Providers" under the `'claude-code'` provider nav group.

- [ ] **Step 1: Create `MinimaxCredentialsCard.tsx`**

```tsx
import { useState, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { fetchMinimaxPlatformStatus, setMinimaxApiKey, clearMinimaxApiKey } from '@/features/cc-bridge/api'

export function MinimaxCredentialsCard() {
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [keyInput, setKeyInput] = useState('')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchMinimaxPlatformStatus()
      .then((data) => { if (!cancelled) setConfigured(data.configured) })
      .catch(() => { if (!cancelled) setConfigured(null) })
    return () => { cancelled = true }
  }, [])

  async function handleSave() {
    const key = keyInput.trim()
    if (!key) return
    setSaving(true)
    setError(null)
    try {
      const result = await setMinimaxApiKey(key)
      setConfigured(result.configured)
      setKeyInput('')
      setEditing(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save MiniMax API key')
    } finally {
      setSaving(false)
    }
  }

  async function handleClear() {
    setSaving(true)
    setError(null)
    try {
      const result = await clearMinimaxApiKey()
      setConfigured(result.configured)
      setKeyInput('')
      setEditing(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to clear MiniMax API key')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>MiniMax</CardTitle>
        <CardDescription>
          API key for launching Claude Code sessions against MiniMax instead of Anthropic. Sent once to
          the backend and written to its local .env file — never stored in the database, never shown again.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {configured === null && (
          <p className="text-xs text-muted-foreground">Checking configuration...</p>
        )}

        {configured === true && !editing && (
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm text-muted-foreground">MiniMax API key configured.</p>
            <div className="flex gap-2 shrink-0">
              <button
                type="button"
                className="text-xs text-muted-foreground hover:text-foreground underline"
                onClick={() => setEditing(true)}
              >
                Change
              </button>
              <button
                type="button"
                className="text-xs text-destructive hover:text-destructive/80 underline"
                onClick={handleClear}
                disabled={saving}
              >
                Clear
              </button>
            </div>
          </div>
        )}

        {(configured === false || editing) && (
          <div className="space-y-1.5">
            <Label htmlFor="minimax-api-key">MiniMax API key</Label>
            <div className="flex gap-2">
              <Input
                id="minimax-api-key"
                type="password"
                autoComplete="off"
                value={keyInput}
                onChange={(e) => setKeyInput(e.target.value)}
                placeholder="sk-..."
              />
              <Button
                type="button"
                size="sm"
                onClick={handleSave}
                disabled={!keyInput.trim() || saving}
              >
                {saving ? 'Saving...' : 'Save'}
              </Button>
              {configured === true && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => { setEditing(false); setKeyInput(''); setError(null) }}
                  disabled={saving}
                >
                  Cancel
                </Button>
              )}
            </div>
            {error && (
              <p className="text-xs text-destructive">{error}</p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
```

- [ ] **Step 2: Create `ProvidersPage.tsx`**

```tsx
import { MinimaxCredentialsCard } from './MinimaxCredentialsCard'

export function ProvidersPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Providers</h1>
        <p className="text-sm text-muted-foreground mt-1">
          One-time credentials for platforms Claude Code sessions can launch against.
        </p>
      </div>
      <MinimaxCredentialsCard />
    </div>
  )
}
```

- [ ] **Step 3: Wire the route in `App.tsx`**

Add the lazy import next to the other feature page imports (after the `HostsPage` line):

```tsx
const HostsPage = lazy(() => import('./features/hosts/HostsPage').then((m) => ({ default: m.HostsPage })))
const ProvidersPage = lazy(() => import('./features/providers/ProvidersPage').then((m) => ({ default: m.ProvidersPage })))
```

Add the route next to `hosts` (after the `<Route path="hosts" element={<HostsPage />} />` line):

```tsx
                <Route path="hosts" element={<HostsPage />} />
                <Route path="providers" element={<ProvidersPage />} />
```

- [ ] **Step 4: Add the nav item in `navigation.ts`**

Add `KeyRound` to the `lucide-react` import list (alongside `Server`, `Shield`, etc.):

```ts
  Shield,
  KeyRound,
  Bot,
```

Add the nav item to the `'claude-code'` group, right after MCP Servers:

```ts
        { name: 'MCP Servers', href: '/mcp', icon: Server, capability: 'mcp' },
        { name: 'Providers', href: '/providers', icon: KeyRound },
        { name: 'Plugins', href: '/plugins', icon: Package, capability: 'plugins' },
```

No `capability` key — `supportsProvider()` treats a missing `capability` as always-visible (see `frontend/src/lib/navigation.ts:160-165`), matching this item being a Cockpit-level setting rather than a per-provider CLI capability.

- [ ] **Step 5: Verify**

```bash
cd frontend
npx tsc --noEmit -p tsconfig.json
npm run build
```
Expected: both commands exit 0, no TypeScript errors, build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/providers/MinimaxCredentialsCard.tsx frontend/src/features/providers/ProvidersPage.tsx frontend/src/App.tsx frontend/src/lib/navigation.ts
git commit -m "feat: add Providers page with MiniMax credential configuration"
```

---

### Task 2: Simplify the New Agent Session dialog

**Files:**
- Modify: `frontend/src/features/cc-bridge/NewSessionDialog.tsx`

**Interfaces:**
- Consumes: route `/providers` (from Task 1) via a `react-router-dom` `Link`.
- Produces: no new exports — `NewSessionDialog`'s public props (`NewSessionDialogProps`) are unchanged.

- [ ] **Step 1: Remove the now-unused imports and add `Link`**

Replace:
```tsx
import { spawnSession, fetchResumableSessions, bulkResumeSessions, fetchMinimaxPlatformStatus, setMinimaxApiKey, clearMinimaxApiKey } from './api'
```
with:
```tsx
import { spawnSession, fetchResumableSessions, bulkResumeSessions, fetchMinimaxPlatformStatus } from './api'
```

Add, next to the other `@/contexts`/`@/types` imports:
```tsx
import { Link } from 'react-router-dom'
```

- [ ] **Step 2: Remove the credential-editing state**

Remove these four lines (they currently sit right after `minimaxConfigured`'s `useState`):
```tsx
  const [minimaxKeyInput, setMinimaxKeyInput] = useState('')
  const [minimaxKeyEditing, setMinimaxKeyEditing] = useState(false)
  const [savingMinimaxKey, setSavingMinimaxKey] = useState(false)
  const [minimaxKeyError, setMinimaxKeyError] = useState<string | null>(null)
```

- [ ] **Step 3: Simplify the status-check effect**

Replace:
```tsx
  useEffect(() => {
    if (!open || platform !== 'minimax') return
    let cancelled = false
    setMinimaxConfigured(null)
    setMinimaxKeyInput('')
    setMinimaxKeyEditing(false)
    setMinimaxKeyError(null)
    fetchMinimaxPlatformStatus()
      .then((data) => { if (!cancelled) setMinimaxConfigured(data.configured) })
      .catch(() => { if (!cancelled) setMinimaxConfigured(null) })
    return () => { cancelled = true }
  }, [open, platform])

  async function handleSaveMinimaxKey() {
    const key = minimaxKeyInput.trim()
    if (!key) return
    setSavingMinimaxKey(true)
    setMinimaxKeyError(null)
    try {
      const result = await setMinimaxApiKey(key)
      setMinimaxConfigured(result.configured)
      setMinimaxKeyInput('')
      setMinimaxKeyEditing(false)
    } catch (err) {
      setMinimaxKeyError(err instanceof Error ? err.message : 'Failed to save MiniMax API key')
    } finally {
      setSavingMinimaxKey(false)
    }
  }

  async function handleClearMinimaxKey() {
    setSavingMinimaxKey(true)
    setMinimaxKeyError(null)
    try {
      const result = await clearMinimaxApiKey()
      setMinimaxConfigured(result.configured)
      setMinimaxKeyInput('')
      setMinimaxKeyEditing(false)
    } catch (err) {
      setMinimaxKeyError(err instanceof Error ? err.message : 'Failed to clear MiniMax API key')
    } finally {
      setSavingMinimaxKey(false)
    }
  }
```
with:
```tsx
  useEffect(() => {
    if (!open || platform !== 'minimax') return
    let cancelled = false
    setMinimaxConfigured(null)
    fetchMinimaxPlatformStatus()
      .then((data) => { if (!cancelled) setMinimaxConfigured(data.configured) })
      .catch(() => { if (!cancelled) setMinimaxConfigured(null) })
    return () => { cancelled = true }
  }, [open, platform])
```

- [ ] **Step 4: Simplify the dialog-close reset effect**

Replace:
```tsx
      setMinimaxConfigured(null)
      setMinimaxKeyInput('')
      setMinimaxKeyEditing(false)
      setMinimaxKeyError(null)
    }
  }, [open, defaultProvider])
```
with:
```tsx
      setMinimaxConfigured(null)
    }
  }, [open, defaultProvider])
```

- [ ] **Step 5: Replace the MiniMax details JSX block**

Replace the entire block (from `{!isCodex && platform === 'minimax' && (` through its matching `)}`):
```tsx
          {!isCodex && platform === 'minimax' && (
            <div className="space-y-3 rounded-md border border-border p-3">
              {minimaxConfigured === null && (
                <p className="text-xs text-muted-foreground">Checking configuration...</p>
              )}

              {minimaxConfigured === true && !minimaxKeyEditing && (
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs text-muted-foreground">MiniMax API key configured.</p>
                  <div className="flex gap-2 shrink-0">
                    <button
                      type="button"
                      className="text-xs text-muted-foreground hover:text-foreground underline"
                      onClick={() => setMinimaxKeyEditing(true)}
                    >
                      Change
                    </button>
                    <button
                      type="button"
                      className="text-xs text-destructive hover:text-destructive/80 underline"
                      onClick={handleClearMinimaxKey}
                      disabled={savingMinimaxKey}
                    >
                      Clear
                    </button>
                  </div>
                </div>
              )}

              {(minimaxConfigured === false || minimaxKeyEditing) && (
                <div className="space-y-1.5">
                  <Label htmlFor="minimax-api-key">MiniMax API key</Label>
                  <div className="flex gap-2">
                    <Input
                      id="minimax-api-key"
                      type="password"
                      autoComplete="off"
                      value={minimaxKeyInput}
                      onChange={(e) => setMinimaxKeyInput(e.target.value)}
                      placeholder="sk-..."
                    />
                    <Button
                      type="button"
                      size="sm"
                      onClick={handleSaveMinimaxKey}
                      disabled={!minimaxKeyInput.trim() || savingMinimaxKey}
                    >
                      {savingMinimaxKey ? 'Saving...' : 'Save'}
                    </Button>
                    {minimaxConfigured === true && (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => { setMinimaxKeyEditing(false); setMinimaxKeyInput(''); setMinimaxKeyError(null) }}
                        disabled={savingMinimaxKey}
                      >
                        Cancel
                      </Button>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Sent once to the backend and written to its local .env file. Never stored in the database, never shown again.
                  </p>
                  {minimaxKeyError && (
                    <p className="text-xs text-destructive">{minimaxKeyError}</p>
                  )}
                </div>
              )}

              <div className="space-y-1.5">
                <Label>Endpoint</Label>
                <Select value={minimaxBaseUrl} onValueChange={setMinimaxBaseUrl}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={MINIMAX_BASE_URL_INTERNATIONAL}>International</SelectItem>
                    <SelectItem value={MINIMAX_BASE_URL_CHINA}>China</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}
```
with:
```tsx
          {!isCodex && platform === 'minimax' && (
            <div className="space-y-3 rounded-md border border-border p-3">
              {minimaxConfigured === null && (
                <p className="text-xs text-muted-foreground">Checking configuration...</p>
              )}

              {minimaxConfigured === false && (
                <p className="text-xs text-muted-foreground">
                  MiniMax API key not configured.{' '}
                  <Link to="/providers" className="underline hover:text-foreground">
                    Set it up on the Providers page
                  </Link>
                  .
                </p>
              )}

              <div className="space-y-1.5">
                <Label>Endpoint</Label>
                <Select value={minimaxBaseUrl} onValueChange={setMinimaxBaseUrl}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={MINIMAX_BASE_URL_INTERNATIONAL}>International</SelectItem>
                    <SelectItem value={MINIMAX_BASE_URL_CHINA}>China</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}
```

- [ ] **Step 6: Verify**

```bash
cd frontend
npx tsc --noEmit -p tsconfig.json
npm run build
npm run lint
```
Expected: `tsc` and `build` exit 0 with no errors. `lint` should report the same pre-existing `react-hooks/set-state-in-effect` warning pattern already present throughout this file (0 errors) — no *new* warnings tied to lines this task touched, and no lingering references to `minimaxKeyInput`, `minimaxKeyEditing`, `savingMinimaxKey`, `minimaxKeyError`, `setMinimaxApiKey`, or `clearMinimaxApiKey` (confirm with `grep -n "minimaxKeyInput\|minimaxKeyEditing\|savingMinimaxKey\|minimaxKeyError\|setMinimaxApiKey\|clearMinimaxApiKey" frontend/src/features/cc-bridge/NewSessionDialog.tsx` — expect no output).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/cc-bridge/NewSessionDialog.tsx
git commit -m "refactor: move MiniMax credential form out of New Agent Session dialog"
```

---

### Task 3: Full verification

**Files:** none (verification only).

- [ ] **Step 1: Full frontend check**

```bash
cd frontend
npx tsc --noEmit -p tsconfig.json
npm run lint
npm run build
```
Expected: all three exit 0. `lint` shows 0 errors (warnings-only, matching the pre-existing baseline).

- [ ] **Step 2: Confirm the backend is untouched and still green**

```bash
cd backend
python -m pytest tests/test_minimax_credentials.py tests/test_agent_bridge_platform_status.py -q
```
Expected: all tests pass (these were added/verified in the prior card and this plan makes no backend changes).

- [ ] **Step 3: Confirm the built bundle ships the new page**

```bash
grep -o "MiniMax API key configured\|Set it up on the Providers page\|One-time credentials for platforms" frontend/dist/assets/*.js | sort -u
```
Expected: all three strings found in the built output (proves `ProvidersPage`/`MinimaxCredentialsCard` compiled into the bundle and the dialog's new notice text is present).

- [ ] **Step 4: Live-verify the page against a running backend**

Start an isolated backend instance on a scratch port (do not touch the shared `:8000`/`:5173` dev servers other sessions may be using):
```bash
cd backend
nohup uvicorn app.main:app --port 8013 > /tmp/providers-page-verify.log 2>&1 &
sleep 2
curl -s localhost:8013/api/v1/agent-bridge/platforms/minimax/status
```
Expected: `{"configured":false}` (or `true` if a `.env` with `MINIMAX_API_KEY` already exists in that backend directory).

Then stop it:
```bash
pkill -f "uvicorn app.main:app --port 8013"
```

- [ ] **Step 5: No commit needed** (verification-only task; if any check fails, fix the issue in the relevant Task 1/2 file and re-run this task's steps before considering the plan done).
