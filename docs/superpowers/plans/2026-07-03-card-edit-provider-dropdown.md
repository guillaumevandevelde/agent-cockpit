# Kanban Card Edit Provider Dropdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the "Agent" field in the kanban card create/edit dialog (`CardEditDialog.tsx`) a provider dropdown (claude-code / open-code / codex-cli / mimo-code) instead of a persona dropdown, matching the provider picker that already exists in `CardDrawer.tsx`'s quick-action bar.

**Architecture:** `CardEditDialog.tsx` currently fetches `.claude/agents/*.md` persona names via `kanbanApi.agents()` and shows them in a `Select`, writing the chosen persona name into the card's `agent: string | null` field. `CardDrawer.tsx` has a separate, already-correct `Select` on the same field that lists installed providers from `useProviderContext()` with an `"__auto__"` sentinel mapping to `null`. This plan ports that same pattern into `CardEditDialog.tsx` (dropping the persona fetch/fallback entirely) and relabels both selects from "Agent" to "Provider" for consistency. No backend, schema, or API changes.

**Tech Stack:** React 19 + TypeScript, shadcn/ui `Select` (Radix), `ProviderContext` (`frontend/src/contexts/ProviderContext.tsx`).

## Global Constraints

- No backend changes — `card.agent` keeps storing a provider id or `null`; `dispatch.py`'s provider/persona disambiguation is untouched.
- Do not remove `kanbanApi.agents()` / `GET /kanban/agents` — still used by `ColumnSettingsDialog.tsx` for the per-column persona default.
- Do not touch the column→persona mapping (`ColumnSettingsDialog.tsx`, `dispatch.py`'s `_persona_for_card`).
- No automated frontend test harness covers full component rendering with Radix `Select` in this repo (existing `*.test.tsx` files only cover hooks/lib functions) — verification for this plan is `npm run build` (typecheck) + `npm run lint` + manual browser check, per the approved spec's Testing section.
- Frontend commands run from `frontend/` (e.g. `cd frontend && npm run build`).

---

### Task 1: `CardEditDialog.tsx` — Agent field becomes a Provider dropdown

**Files:**
- Modify: `frontend/src/features/kanban/components/CardEditDialog.tsx`

**Interfaces:**
- Consumes: `useProviderContext()` from `frontend/src/contexts/ProviderContext.tsx`, returning `{ providers: AgentProviderStatus[] }` where `AgentProviderStatus` (`frontend/src/types/providers.ts`) has `id: AgentProviderId`, `display_name: string`, `installed: boolean`.
- Produces: `CardEditDialog`'s `onSubmit` callback still receives `agent: string | null` (unchanged shape) — now always a provider id or `null`, never a persona name. `KanbanPage.tsx` and `CardDrawer.tsx` (the two callers) need no changes since they already pass provider-id-shaped `defaultAgent` values (`selectedProviderId` and `card.agent` respectively).

- [ ] **Step 1: Remove the persona-fetching state and effect**

In `frontend/src/features/kanban/components/CardEditDialog.tsx`, delete the `availableAgents` state declaration and the effect that fetches it:

```tsx
  const [availableAgents, setAvailableAgents] = useState<string[]>([]);
```

(this line currently sits among the other `useState` declarations, just before `resumeSessions`)

and:

```tsx
  // Fetch available agents for the dropdown
  useEffect(() => {
    if (!projectPath) return;
    let cancelled = false;
    kanbanApi.agents(projectPath)
      .then((r) => { if (!cancelled) setAvailableAgents(r.agents); })
      .catch(() => { if (!cancelled) setAvailableAgents([]); });
    return () => { cancelled = true; };
  }, [projectPath]);
```

- [ ] **Step 2: Swap the `kanbanApi` import for `useProviderContext`, and add the `AUTO` sentinel**

Replace this import line:

```tsx
import { kanbanApi } from "../api";
```

with:

```tsx
import { useProviderContext } from "@/contexts/ProviderContext";
```

Then, right after the `parseLabels` helper function (before `export function CardEditDialog`), add the sentinel constant used by the new provider select:

```tsx
const AUTO = "__auto__"; // sentinel: null agent (dispatch resolves the provider at run time)
```

- [ ] **Step 3: Initialize `agent` state from the sentinel, and read installed providers**

After Step 1's removal of the `availableAgents` line, the top of the component body has this block (note `agent`'s init still reads `?? ""`):

```tsx
  const [agent, setAgent] = useState<string>(defaultAgent ?? "");
  const [transport, setTransport] = useState<string>(initial?.transport ?? "auto");

  const [resumeSessions, setResumeSessions] = useState<ResumableSession[]>([]);
```

Change it to:

```tsx
  const [agent, setAgent] = useState<string>(defaultAgent ?? AUTO);
  const [transport, setTransport] = useState<string>(initial?.transport ?? "auto");
  const { providers } = useProviderContext();
  const installedProviders = providers.filter((p) => p.installed);

  const [resumeSessions, setResumeSessions] = useState<ResumableSession[]>([]);
```

- [ ] **Step 4: Replace the Agent select JSX with a Provider select**

Replace this whole block:

```tsx
          <div className="space-y-2">
            <Label>Agent</Label>
            {availableAgents.length > 0 ? (
              <Select value={agent} onValueChange={setAgent}>
                <SelectTrigger>
                  <SelectValue placeholder="Select agent (optional)" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="">None</SelectItem>
                  {availableAgents.map((a) => (
                    <SelectItem key={a} value={a}>
                      {a}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <Input
                id="card-agent"
                placeholder="Agent name (optional)"
                value={agent}
                onChange={(e) => setAgent(e.target.value)}
              />
            )}
          </div>
```

with:

```tsx
          <div className="space-y-2">
            <Label>Provider</Label>
            <Select value={agent} onValueChange={setAgent}>
              <SelectTrigger>
                <SelectValue placeholder="Provider" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={AUTO}>Auto (selected provider)</SelectItem>
                {installedProviders.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
```

- [ ] **Step 5: Update the submit mapping**

Change:

```tsx
                agent: agent.trim() || null,
```

to:

```tsx
                agent: agent === AUTO ? null : agent,
```

- [ ] **Step 6: Typecheck and lint**

Run: `cd frontend && npm run build`
Expected: builds cleanly, no TypeScript errors (in particular: no unused-import error for the removed `kanbanApi` import, no unused-variable error for the removed `availableAgents` state).

Run: `cd frontend && npm run lint`
Expected: no new ESLint errors in `CardEditDialog.tsx`.

- [ ] **Step 7: Manual verification in the browser**

Start the dev stack if it isn't already running: `./scripts/cockpit.sh start` (or `./scripts/dev.sh`), then open the Kanban feature for a project that has at least one installed provider (`claude-code` is always installed in this dev environment).

1. Click "New card" → the "Provider" dropdown shows "Auto (selected provider)" plus every installed provider (e.g. `claude-code`, and `open-code`/`codex-cli`/`mimo-code` if installed); it defaults to the globally selected provider (top-right provider switcher).
2. Create the card with an explicit non-default provider selected (if more than one is installed) → open it → click "Edit" → the dropdown pre-selects that same provider.
3. Change it to "Auto (selected provider)", save → re-open "Edit" → dropdown shows "Auto (selected provider)" again (confirms `agent` was written as `null` and round-trips through `defaultAgent`).
4. Simulate a pre-migration card (stale persona value): find any card id from step 1-3 (visible in the drawer URL or via `GET /api/v1/kanban/cards?project_path=<path>`), then set its `agent` field to an old-style persona name directly via the API:
   `curl -X PATCH "http://localhost:8000/api/v1/kanban/cards/<card_id>" -H "Content-Type: application/json" -d '{"agent": "engineer"}'`
   Open that card's "Edit" dialog → the Provider dropdown shows the placeholder (no item matches `"engineer"`), confirming the documented edge-case degradation. Save the form (selecting any provider, or leaving Auto) → re-open "Edit" → the dropdown now shows a real, matching value — confirming the stale value self-heals on save.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/kanban/components/CardEditDialog.tsx
git commit -m "fix(kanban): card edit dialog Agent field now selects a provider, not a persona"
```

---

### Task 2: `CardDrawer.tsx` — relabel "Agent" to "Provider"

**Files:**
- Modify: `frontend/src/features/kanban/components/CardDrawer.tsx:146`

**Interfaces:**
- Consumes: nothing new — this select already lists `installedProviders` from `useProviderContext()` (unchanged).
- Produces: nothing new — purely a display-string change, no behavior change.

- [ ] **Step 1: Change the placeholder text**

In `frontend/src/features/kanban/components/CardDrawer.tsx`, change:

```tsx
              <SelectValue placeholder="Agent" />
```

to:

```tsx
              <SelectValue placeholder="Provider" />
```

(this is the only occurrence of `placeholder="Agent"` in the file — the select's current value is always `AUTO` or a provider id, so the placeholder is rarely visible, but it should say "Provider" for consistency with Task 1's relabeled dialog and for screen readers / any moment the trigger renders without a matched value)

- [ ] **Step 2: Typecheck and lint**

Run: `cd frontend && npm run build`
Expected: builds cleanly, no TypeScript errors.

Run: `cd frontend && npm run lint`
Expected: no new ESLint errors.

- [ ] **Step 3: Manual verification in the browser**

Open any kanban card's drawer (click a card, not "Edit") → the quick-select in the action bar behaves exactly as before (Auto / provider list, dispatch/re-dispatch still work) — only its (usually invisible) placeholder text changed.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/kanban/components/CardDrawer.tsx
git commit -m "chore(kanban): relabel card drawer provider select placeholder to 'Provider'"
```
