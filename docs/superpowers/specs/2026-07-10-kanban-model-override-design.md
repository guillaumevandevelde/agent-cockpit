# Kanban card/column model override — design

**Date:** 2026-07-10
**Status:** Approved (design); ready for writing-plans
**Builds on:** kanban dispatch (`backend/app/kanban/dispatch.py`,
`docs/cockpit/kanban-dispatch-spec.md`), work-type persona routing
(`docs/cockpit/work-type-routing-analysis.md` — already implemented), the provider
abstraction (`backend/app/services/providers/`).

## Problem

The kanban dispatcher never lets a project/card pick which underlying model a spawned
session runs. Two related facts, both verified in code during design:

- `SpawnCommandOptions.model` (`base.py:33`) is already wired to a `--model` CLI flag
  for `codex_cli`, `copilot_cli` and `open_code` — but **not** for `claude_code`, the
  dispatcher's default and most-used provider. `ClaudeCodeProvider.build_spawn_command`
  (`claude_code.py:57-77`) never reads `options.model`.
- Persona files (`.claude/agents/engineer.md`, `analyst.md`) already declare
  `model: 'claude-opus-4-8'` in YAML frontmatter, but `_read_persona_file()`
  (`dispatch.py:366-371`) calls `_strip_frontmatter()` (`:351-356`) before the persona
  body reaches the prompt — that field is **dead** for kanban-dispatched sessions today.

Trigger: the user has Anthropic subscription capacity that would normally sit idle
waiting for analyst-type work, and wants to redirect it to engineer-type work instead —
today's routing only picks a *persona* (`work_type` → analyst/engineer), never a model.

## Goals

- A card can set an explicit model (`card.model`), overriding everything below it.
- A column can set a default model (`column.default_model`) for every card dispatched
  into it — the "redirect this column's capacity to a different model" lever.
- A persona file's frontmatter `model:` field becomes a real, respected default (fixing
  the dead field) — the natural place to declare "engineer normally runs on Opus."
- When none of the above is set, behavior is unchanged: no `--model` flag is passed, the
  CLI/platform default applies (Sonnet 5 for Anthropic, MiniMax-M3 for minimax via the
  existing `ANTHROPIC_MODEL` env-var route in `platform_env.py`).
- The selectable model list is not hardcoded — a manual refresh action queries the
  installed `claude` CLI (`claude -p "/model"`) and caches the current alias list, so a
  new Anthropic model alias needs no code change.
- Applies uniformly to analyst and executor phases (same spawn-transport code path) and
  to plain/worktree/resume spawn modes.

## Non-goals

- No live/automatic re-query of the model list on every dropdown render — refresh is a
  manual, explicit action.
- No validation or enum enforcement of `model` values anywhere (card, column, or API) —
  free text, matching the existing `card.agent`/`labels` precedent. The CLI itself
  accepts "a full model ID" beyond any enumerable alias list, so a closed enum would be
  actively wrong.
- No change to the MiniMax model-selection mechanism (`ANTHROPIC_MODEL` env var) —
  already provider-appropriate, out of scope.
- No generic cross-provider "model options" API (à la Codex's `models_cache.json` +
  `GET /providers/{id}/launch-options`) — scoped to kanban via `KanbanMeta` for now.
  Promote to the provider layer later if Agent Bridge or another flow needs it too.
- No `CardItem` badge for the chosen model (parallel to the existing `work_type` badge)
  — not requested, skip until there's a concrete need.

## Design

### 1. Data model

New nullable columns, same pattern as `default_agent`/`default_platform`
(`KanbanColumn`, `models.py:112,117`) and `agent`/`work_type` (`KanbanCard`,
`models.py:47`):

| Table | Field | Type | Notes |
|---|---|---|---|
| `KanbanColumn` | `default_model` | `String(64) \| None` | Column-wide default, same UX slot as `default_platform`. |
| `KanbanCard` | `model` | `String(64) \| None` | Per-card override, same UX slot as `agent`. |

Both are free text — no FK, no enum, no backend validation. Added via
`ALTER TABLE ... ADD COLUMN` in `db.py`'s boot-time migration path, mirroring how
`work_type` was added (`db.py:130-131`).

### 2. Precedence resolution

New pure function, sibling to `_phase_target_agent`:

```python
def _effective_model(card, column_default_model, persona_model) -> str | None:
    return card.model or column_default_model or persona_model or None
```

- `persona_model` comes from a new `_read_persona_model(project_path, filename)` that
  parses the frontmatter block **before** `_strip_frontmatter` discards it, using
  `pyyaml` (already a backend dependency, `requirements.txt:11`) on the
  `---\n...\n---\n` block, returning `frontmatter.get("model")`. Malformed YAML or a
  missing `model:` key both resolve to `None` — never raises.
- Called at both `SpawnCommandOptions` construction sites (`dispatch.py:713`, `:1935`),
  passed through as `model=effective_model`.
- Because analyst- and executor-phase spawns both funnel through the same transport
  functions (confirmed by tracing `_phase_provider_id`/`_phase_target_agent`,
  `dispatch.py:37-107`, into the shared spawn call), this single resolution point
  covers both phases with no phase-specific branching required.

### 3. Provider fix — `ClaudeCodeProvider.build_spawn_command`

```python
if options.model:
    command += ["--model", options.model]
```

Added unconditionally, before the mode-specific prompt append, applying to all modes
(plain/worktree/resume) — same placement pattern as `codex_cli.py:89-95`.
`open_code.py:131-132` already does this; no change needed there. `copilot_cli.py` is
also already wired for `--model` (and separately for `--agent`, unrelated to this
feature).

### 4. Model-options refresh (kanban-scoped)

- `refresh_claude_model_options()`: runs `claude -p "/model"` as a subprocess (short
  timeout, no worktree/session needed — verified output:
  `Current model: Sonnet 5 (default)` followed by
  `Usage: /model <name>. Available: sonnet, opus, haiku, fable, best, sonnet[1m],
  opus[1m], fable[1m], opusplan, default, or a full model ID.`), parses the
  `Available: ...` line into a list, drops the trailing "or a full model ID" clause,
  and upserts a `KanbanMeta` row (`model_options:claude-code` → JSON array) — the same
  device-local key/value store already used for `autodispatch:<project_key>`.
- `GET /api/v1/kanban/model-options` returns the cached list, falling back to a small
  hardcoded seed (`["sonnet", "opus", "haiku"]`) if never refreshed.
- `POST /api/v1/kanban/model-options/refresh` triggers the refresh function.
- UI: a small "Refresh" affordance next to the model fields in `ColumnSettingsDialog`/
  `CardEditDialog`. The field itself is free-text-with-suggestions (a `<datalist>`-style
  control — pick a suggestion or type anything), not a closed `<Select>`.

### 5. UI changes

- `ColumnSettingsDialog.tsx`: new "Default model" field next to the existing Platform
  select, following the `default_platform` edit/display pattern (`:78-231`).
- `CardEditDialog.tsx`: new "Model" field next to `work_type`/`agent`, following the
  same pattern as the `WORK_TYPES`/`workType` state (`:96-174`).
- No `CardItem` badge (see Non-goals).

### 6. Backward compatibility

- Both new columns default to `NULL`; existing rows/cards behave identically — an empty
  precedence chain means no `--model` flag, i.e. today's behavior exactly.
- Persona frontmatter parsing is additive: personas without a `model:` field (or without
  frontmatter at all) resolve to `None` at that layer, same as today.
- `_strip_frontmatter`'s existing behavior (frontmatter dropped from the *prompt* body)
  is unchanged — only a new, separate read of the same file extracts `model:` before
  that stripping happens.

### 7. Error handling

| Case | Behavior |
|---|---|
| `claude -p "/model"` fails/times out during refresh | Refresh endpoint returns an error; the cached list (or seed fallback) is left as-is. No dispatch-time impact — model resolution never calls the CLI live. |
| Unknown/invalid model string in `card.model` / `column.default_model` / persona frontmatter | Passed through as-is to `--model`; the `claude` CLI's own error handling applies. Same failure class as an unrecognized `--agent`/`--platform` value today — surfaced via the existing dead-session reaper / `MAX_DISPATCH_FAILURES` compensation (`kanban-dispatch-spec.md` "Spawn fails" step). No new validation layer added. |
| Frontmatter present but malformed YAML | `_read_persona_model` catches the parse error, returns `None` (falls through to the platform default) — never blocks dispatch. |

### 8. Testing strategy

**Unit tests** (`backend/tests/kanban/`):

| File | Coverage |
|---|---|
| `test_dispatch_model.py` | `_effective_model` precedence (card > column > persona > `None`) across all combinations; persona frontmatter with/without `model:`; malformed frontmatter → `None`. |
| `test_claude_code_provider.py` | `build_spawn_command` emits `--model` when `options.model` is set, omits it when `None`, across plain/worktree/resume modes. |
| `test_model_options.py` | `refresh_claude_model_options` parses a captured `claude -p "/model"` output fixture; `GET`/`POST` endpoints round-trip via `KanbanMeta`; seed fallback applies when never refreshed. |

**Manual smoke**: extend `docs/cockpit/kanban-dispatch-spec.md` (or
`multi-agent-kanban.md`) with a short section — set `column.default_model="opus"`,
dispatch a card, confirm the spawned tmux pane's `claude` invocation includes
`--model opus` (visible via the pane's command history).
