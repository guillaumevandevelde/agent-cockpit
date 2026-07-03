# Kanban card edit dialog: Agent field → Provider dropdown — design

**Date:** 2026-07-03
**Status:** Approved (design); ready for writing-plans
**Builds on:** `frontend/src/features/kanban/components/CardEditDialog.tsx`,
`frontend/src/features/kanban/components/CardDrawer.tsx`, `backend/app/kanban/dispatch.py`,
`docs/superpowers/specs/2026-06-15-kanban-agents-design.md` (column→persona mapping, unchanged)

## Problem

A kanban card has a single `agent: string | null` field that the backend (`dispatch.py`)
deliberately overloads to mean **either** of two unrelated things, disambiguated by string
matching against the known-provider-ids set:

- a **provider id** (`claude-code`, `open-code`, `codex-cli`, `mimo-code`) → which CLI spawns
  the session, or
- a **persona name** (`.claude/agents/<name>.md`, e.g. `engineer`) → an explicit per-card
  override of the column-derived persona.

Two different UI widgets write to this same field with two different, non-overlapping sets
of options:

- `CardDrawer.tsx`'s quick-select already lists **providers** correctly (from
  `useProviderContext()`), with an "Auto" sentinel that maps to `null`.
- `CardEditDialog.tsx`'s "Agent" field (used for both "New card" and "Edit card") lists
  **personas** fetched from `GET /kanban/agents` (a directory listing of
  `.claude/agents/*.md`), with a freetext fallback `Input` if that directory is empty.

Because both widgets act on the same field, touching one silently discards a value set via
the other — e.g. picking a provider in the drawer, then opening "Edit", touching nothing else
relevant and saving, can blank out the provider choice back to persona-shaped state (or
vice versa). Users see the edit-form field labeled "Agent" and reasonably expect it to control
which coding-agent CLI runs the card, not a persona override.

Separately: persona selection is being reframed as a Claude-Code-internal subagent concept,
already surfaced elsewhere in the app (the standalone "Agents" page under Claude tools, backed
by `AgentService.list_agents` / `.claude/agents/*.md`). The kanban feature should not offer a
second, competing way to pick a persona per-card.

## Goals

- `CardEditDialog.tsx`'s "Agent" field becomes a **provider** dropdown: same options and same
  `null`-means-auto semantics as `CardDrawer.tsx`'s existing quick-select.
- Both widgets present a consistent label: **"Provider"** instead of the ambiguous "Agent".
- No backend or schema changes — `card.agent` keeps storing a provider id or `null`; the
  existing provider/persona disambiguation in `dispatch.py` is untouched (it still tolerates a
  persona value written by direct API/MCP calls, which is harmless, unused-by-UI flexibility).

## Non-goals

- Removing or changing the column→persona mapping (`ColumnSettingsDialog.tsx`'s "default
  agent", `dispatch.py`'s `_persona_for_card`). That stays exactly as designed in
  `2026-06-15-kanban-agents-design.md`.
- Removing `GET /kanban/agents` or `kanbanApi.agents()` — still used by
  `ColumnSettingsDialog.tsx` for the per-column persona default, a legitimate, separate use.
- A data migration for existing cards whose `agent` field holds a stale persona name (e.g.
  `"engineer"`). See "Edge case" below — no action needed, it self-heals on next save.
- Changing how `dispatch.py` resolves provider vs. persona from `card.agent`.

## Design

### `CardEditDialog.tsx`

- Remove the `availableAgents` state and its `kanbanApi.agents(projectPath)` fetch effect, and
  the freetext `Input` fallback branch.
- Add `useProviderContext()` (same hook `CardDrawer.tsx` already uses); derive
  `installedProviders = providers.filter(p => p.installed)`.
- Replace the "Agent" `<Select>` with a provider `<Select>`:
  - A sentinel option `AUTO` ("Auto (selected provider)") that maps to `agent: null` on submit
    — same sentinel value and label `CardDrawer.tsx` already uses, for consistency.
  - One `<SelectItem>` per installed provider, `value={p.id}`, label `{p.display_name}`.
- State init changes from `useState<string>(defaultAgent ?? "")` to
  `useState<string>(defaultAgent ?? AUTO)`; the submit handler maps
  `agent === AUTO ? null : agent` (previously `agent.trim() || null`).
- Label text: `"Agent"` → `"Provider"`.

### `CardDrawer.tsx`

- No functional change (it already lists providers correctly). Only the visible label next to
  its quick-select changes from `"Agent"` to `"Provider"` for consistency with the edit dialog.

### Data flow

`card.agent` remains a plain nullable string on the card record; both callers of
`CardEditDialog` (`KanbanPage.tsx`'s "New card" flow, passing `defaultAgent={selectedProviderId}`,
and `CardDrawer.tsx`'s "Edit" flow, passing `defaultAgent={card.agent}`) already pass
provider-id-shaped values, so no caller changes are needed beyond the dialog itself.
`dispatch.py`'s existing `_known_provider_ids()` membership check continues to resolve the
field correctly — untouched.

### Edge case: stale persona values

A card saved before this change may have `agent` set to a persona name (e.g. `"engineer"`)
that doesn't match any provider id. Opening "Edit" on such a card will show the dropdown at
its placeholder (no matching `SelectItem`) rather than a selected value. This is acceptable
degradation, not a bug to fix: saving the form (even without touching the field) will write
`null` (Auto) or an explicitly chosen provider, replacing the stale value. No migration script
needed — this is a local dev SQLite database with a small number of cards.

## Testing

Manual verification in the browser (no automated frontend test harness exists yet, per
project gotchas):

1. Create a new card → Provider dropdown shows "Auto (selected provider)" plus all installed
   providers; default matches the globally selected provider.
2. Edit an existing card that has a provider set → dropdown pre-selects that provider.
3. Edit an existing card with a stale persona value → dropdown falls back to placeholder;
   saving clears it to a valid value.
4. Save with "Auto" selected → `card.agent` is `null`.
5. Save with an explicit provider selected → `card.agent` is that provider's id.
6. Dispatch a card with an explicit non-default provider → the correct CLI is spawned (cross-
   check against `CardDrawer.tsx`'s pre-existing quick-select behavior, which is unchanged).
