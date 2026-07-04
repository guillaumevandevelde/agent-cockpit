# MiniMax credential UI — move from New Agent Session dialog to a Providers page

**Date:** 2026-07-04
**Status:** Design — pending implementation
**Scope:** Relocate the MiniMax API key Save/Change/Clear form out of the "New Agent Session" dialog into a new, dedicated `/providers` page. The New Agent Session dialog keeps only what's genuinely per-session (the endpoint choice) plus a read-only status check and a link to the new page.

## Problem

A prior card (`k-minimax-ui-ve-415a` + follow-up `worktree-k-minimax-ui-credentials`, merged to master) added a MiniMax platform option to the New Agent Session dialog, including an inline password field to set/change/clear the API key via `POST`/`DELETE /api/v1/agent-bridge/platforms/minimax/credentials`.

Feedback: configuring a credential is a one-time, global action — it shouldn't live inside a dialog that's conceptually "spawn a session," which implies per-session reconfiguration. It needs a persistent home the user visits once, not a form that resurfaces every time they open "New Agent Session."

## Decisions (locked)

- **Scope: MiniMax only, for now.** No general "Providers" framework section for Anthropic/Bedrock — those have nothing to configure via UI today, so building placeholder sections for them is not justified yet (YAGNI). The page and route are named generically (`Providers` / `/providers`) so a second provider's credentials can be added later without a rename.
- **Nav placement:** new "Providers" item in the existing **Claude Code** provider-specific nav group (`frontend/src/lib/navigation.ts`), next to Config/Sessions/MCP Servers — not in the provider-agnostic `commonNavigation`. Rationale: the platform switch (Anthropic/Bedrock/MiniMax) only affects `claude` CLI invocations (`ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`/`CLAUDE_CODE_USE_BEDROCK`); it has no meaning for Codex/MiMoCode/OpenCode sessions, matching how Config/MCP Servers are already scoped to Claude Code only.
- **Dialog fallback:** when MiniMax isn't configured, the dialog shows a short notice with a link to `/providers` instead of an inline form. The Endpoint selector (International/China) stays in the dialog since it's a genuinely per-session choice, not a credential.
- **No backend changes.** The existing endpoints already do exactly what's needed and are unaffected by where the UI lives:
  - `GET /api/v1/agent-bridge/platforms/minimax/status` → `{"configured": bool}`
  - `POST /api/v1/agent-bridge/platforms/minimax/credentials` → set (write `.env`, update in-memory `Settings`, never echo the key)
  - `DELETE /api/v1/agent-bridge/platforms/minimax/credentials` → clear

## Approach

Move the existing Save/Change/Clear UI (currently inline in `NewSessionDialog.tsx`) into a new small feature module, and slim the dialog down to a read-only consumer of the same status endpoint.

### Alternatives considered (rejected)

- **Leave it in the dialog** — rejected per explicit feedback: reads as per-session config, not a one-time setup step.
- **General Providers framework with sections for every platform** — rejected for now: Anthropic/Bedrock have no editable credential (Bedrock resolves AWS creds from the host's credential chain; Anthropic needs nothing), so a "read-only status" section for them today would be empty ceremony. The route/name stays generic so this is a pure future addition, not a rename, if that changes.
- **Config page** (`/config`) — rejected: that page edits Claude Code's own `settings.json` (a different domain — CLI tool config, not Cockpit's own backend `Settings`/`.env`). Mixing the two would blur what "Config" means.

## Components & data flow

### Frontend

- **`frontend/src/features/providers/` (new feature folder)**
  - `ProvidersPage.tsx` — page shell (header + description), renders `MinimaxCredentialsCard`.
  - `MinimaxCredentialsCard.tsx` — the exact Save/Change/Clear form logic currently inline in `NewSessionDialog.tsx` (fetch status on mount, password input, Save/Change/Clear buttons, inline error), moved here essentially unchanged. Uses the existing `fetchMinimaxPlatformStatus`, `setMinimaxApiKey`, `clearMinimaxApiKey` from `frontend/src/features/cc-bridge/api.ts` (no API changes — these stay where they are since they're `agent-bridge`-prefixed calls; a `providers` feature importing from `cc-bridge/api.ts` is acceptable given both call the same backend router).

- **`frontend/src/App.tsx`** — add `<Route path="providers" element={<ProvidersPage />} />` inside the existing provider-scoped route tree (same nesting level as `config`, `sessions`, `mcp`).

- **`frontend/src/lib/navigation.ts`** — add `{ name: 'Providers', href: '/providers', icon: KeyRound }` (no `capability` gate — always visible for the Claude Code provider, same as Config) to the `'claude-code'` entry's nav group, alongside Config/Sessions/MCP Servers.

- **`frontend/src/features/cc-bridge/NewSessionDialog.tsx`** — simplified:
  - Remove: `minimaxKeyInput`, `minimaxKeyEditing`, `savingMinimaxKey`, `minimaxKeyError` state; `handleSaveMinimaxKey`/`handleClearMinimaxKey`; the password-input JSX block; the `setMinimaxApiKey`/`clearMinimaxApiKey` imports.
  - Keep: the `minimaxConfigured` status fetch (read-only), the Endpoint `Select` (International/China), and — when `minimaxConfigured === false` — a short notice with a `Link` (react-router-dom) to `/providers` instead of the removed form.

### Backend

No changes.

## Testing

- Backend: none needed (no backend changes).
- Frontend: this repo has no frontend test infrastructure (documented gotcha). Verify via `tsc --noEmit`, `npm run lint` (expect only the same pre-existing `set-state-in-effect` warning pattern, no new errors), and `npm run build`. Re-run the same live verification as the prior card: start an isolated backend instance, `curl` the status/credentials endpoints, confirm the built JS bundle contains the new page's strings (no browser available in this sandbox for a full click-through).

## Out of scope

- Adding Anthropic/Bedrock sections to the Providers page.
- Any backend endpoint changes.
- Removing the Endpoint (International/China) selector from the dialog.
