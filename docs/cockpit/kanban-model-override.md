# Kanban model-override — card/column/persona model-precedentie

> **Canoniek** voor "welk model draait een kanban-gedispatchte sessie". Bouwt voort op
> [`kanban-dispatch-spec.md`](./kanban-dispatch-spec.md) en
> [`work-type-routing-analysis.md`](./work-type-routing-analysis.md). Voor bestandsdetails/TDD:
> [superpowers-tegenhanger](../superpowers/specs/2026-07-10-kanban-model-override-design.md).

## Probleem dat het oplost

De dispatcher koos vroeger alleen een **persona** (`work_type` → analyst/engineer), nooit een
**model**. Trigger: idle Anthropic-abonnement-capaciteit die anders op analyst-werk zou wachten
naar engineer-werk redirecten. Twee bugs die tegelijk gefixt zijn:

- `SpawnCommandOptions.model` was wél gekoppeld aan `--model` voor codex/copilot/open-code,
  maar **niet** voor `claude_code` (de default-provider) — `ClaudeCodeProvider` las
  `options.model` nooit.
- Persona-files (`.claude/agents/engineer.md`) declareerden `model: 'claude-opus-4-8'` in
  frontmatter, maar `_strip_frontmatter()` gooide dat veld weg vóór het de prompt bereikte —
  dood veld voor gedispatchte sessies.

## Precedentieketen (blijvende beslissing)

```
card.model  >  column.default_model  >  persona-frontmatter model:  >  geen --model (platform-default)
```

Pure functie `_effective_model(card, column_default_model, persona_model)` in
`backend/app/kanban/dispatch.py`; `persona_model` komt uit een nieuwe `_read_persona_model()`
die de frontmatter **parseert vóór** `_strip_frontmatter` hem weggooit (malformed YAML of
ontbrekende `model:` → `None`, nooit een raise). Één resolutiepunt dekt analyst- én
executor-fase omdat beide door dezelfde spawn-transport funnelen.

## Datamodel & provider-fix

| Tabel | Veld | Notitie |
|---|---|---|
| `KanbanColumn` | `default_model: String(64) \| None` | Column-brede default — de "redirect deze kolom naar een ander model"-hendel. |
| `KanbanCard` | `model: String(64) \| None` | Per-card override. |

Beide **free-text, geen enum, geen backend-validatie** — zelfde contract als `card.agent`/
`labels`. Een gesloten enum zou actief fout zijn: de `claude`-CLI accepteert ook een volledige
model-ID buiten elke alias-lijst. `ClaudeCodeProvider.build_spawn_command` voegt nu
onvoorwaardelijk `["--model", options.model]` toe wanneer gezet (alle modes: plain/worktree/
resume).

## Model-opties refresh (kanban-scoped)

De selecteerbare lijst is niet hardcoded: `POST /api/v1/kanban/model-options/refresh` draait
`claude -p "/model"`, parseert de `Available: …`-regel en cachet in een `KanbanMeta`-rij
(`model_options:claude-code`). `GET /api/v1/kanban/model-options` geeft de cache terug met een
kleine seed-fallback (`["sonnet","opus","haiku"]`). Het UI-veld is free-text-met-suggesties
(datalist-stijl), geen gesloten `<Select>`. Refresh is een **expliciete, handmatige** actie —
geen live re-query bij elke dropdown-render, en model-resolutie roept de CLI nooit live aan.

## Backward-compat

Beide kolommen defaulten naar `NULL`; een lege precedentieketen betekent geen `--model` →
exact het gedrag van vroeger. Personas zonder `model:` resolven op die laag naar `None`.
Onbekende model-string → doorgegeven aan `--model`; de CLI-fout wordt afgehandeld via de
bestaande dead-session-reaper / `MAX_DISPATCH_FAILURES`, geen nieuwe validatielaag.
