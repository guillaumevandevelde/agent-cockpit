# Multi-agent kanban — smoke-test cookbook

> Status: handleiding voor de twee-fase workflow (analyst → executors).
> Bouwt voort op `kanban-spec.md` en het design in
> `docs/superpowers/specs/2026-07-08-multi-agent-kanban-design.md`.

## 1. Wanneer gebruik je multi-agent?

Gebruik de multi-agent-workflow voor kaarten die eerst **analyse** verdelen in **N
onderling afhankelijke taken** en waarvan de uitvoering over **N executors** parallel
mag lopen. Een klassiek voorbeeld: "refactor de auth-flow in 4 stappen, waarbij stap 2
wacht op stap 1". Voor simpele, enkelvoudige taken kun je bij de gewone
single-agent-dispatch blijven.

## 2. `analyst.md` voorbeeld

Plaats een persona-bestand in je project-folder
(`<project>/.claude/agents/analyst.md`) dat uitlegt hoe de analyst-sessie kaarten
splitst en een plan schrijft. Een minimale body:

```markdown
# Analyst

Je bent de analyst-fase van een multi-agent kanban-workflow. Jouw taak:

1. Lees de parent-kaart (titel + beschrijving).
2. Analyseer de codebase en splits het werk in **N kind-kaarten** (max 50).
3. Schrijf een **plan** als `KanbanDeliverable(kind="plan")` op de parent-kaart:
   - Markdown-sectie met de aanpak per kind-kaart.
   - JSON front-matter met `child_card_ids` en `depends_on_graph`.
4. Roep voor elke kind-kaart **`create_card(project, title, description)`** aan
   met alleen de basisvelden (geen `parent_card_id` of `depends_on` — die worden
   hieronder apart gezet).
5. Roep één keer **`add_plan_attachment(card_id=parent, plan_markdown, child_card_ids, depends_on_graph)`**
   aan op de parent-kaart. Deze tool is de single source of truth voor:
   - het koppelen van kind-kaarten aan de parent (`child_card_ids`),
   - de dependency-DAG tussen kinderen (`depends_on_graph`),
   - de 50-child cap (`MAX_CHILDREN_PER_PLAN`).
6. Verplaats de parent-kaart naar `Done` met `move_card(parent, "Done", summary="Plan opgesplitst in N taken")`.
7. Geen parallelle analyst-sessies — jij bent de enige.
8. Cyclische deps worden geweigerd door `add_plan_attachment` — ontwerp acyclisch.
```

De analyst gebruikt dezelfde MCP-tools als elke andere agent (`create_card`,
`add_plan_attachment`, `attach_deliverable`, `move_card`) — de dispatcher herkent
het plan-attachment en spawnt automatisch kind-kaarten zodra hun deps in `Done`
staan.

## 3. Stappen in de UI

1. Maak een kaart aan in **Backlog** via de "+"-knop rechtsboven het bord.
2. Open de **CardEditDialog** (klik op de kaart → "Edit").
3. Vul titel + beschrijving in.
4. Kies bij **Analyst-agent** de provider `claude-code` (degene die de analyse doet).
5. Kies bij **Executor-agent** een andere provider, bv. `mimo-code` (degene die de
   kind-kaarten uitvoert).
6. Klik **Dispatch**.

De kaart wordt geclaimd, de analyst-sessie spawnt in een tmux-pane, en je ziet de
kaart in **Doing** verschijnen.

## 4. Verwacht gedrag

- **Bord:** de analyst verplaatst de parent-kaart **expliciet** naar **Done**
  via `move_card(parent, "Done", summary="Plan opgesplitst in N taken")`
  nadat `add_plan_attachment` is geslaagd. Dit is geen automatische overgang —
  de analyst-sessie is zelf verantwoordelijk voor deze afsluiting. De
  kind-kaarten verschijnen in dezelfde kolom waar de parent stond (meestal
  **Backlog** of **Todo**), gelinkt aan de parent.
- **Drawer → Plan-tab:** open de parent-kaart, klik op het **Plan-tab** in de
  drawer. Je ziet het plan-attachment (`KanbanDeliverable(kind="plan")`) met de
  markdown-aanpak en de `depends_on_graph`.
- **Kind-kaarten:** elke kind-kaart heeft een `plan_ref`-deliverable die naar het
  parent-plan wijst. De executor-sessie krijgt dit in zijn prompt-preamble.
- **Dependency-respect:** kind-kaarten worden **niet** gedispatched zolang een
  kaart waarvan ze afhangen nog niet in `Done` staat. De dispatcher checkt dit
  elke tick.

## 5. Limieten

- **Cap 50 kind-kaarten per parent.** Een plan met meer dan 50 kinderen wordt
  geweigerd door `add_plan_attachment` (error: `too_many_children`). Splits het
  werk in meerdere parent-kaarten.
- **Cyclische deps worden geweigerd.** Het systeem valideert `depends_on_graph`
  op cycli voordat kind-kaarten worden aangemaakt. Een kind kan niet afhangen
  van zichzelf of van een kaart die (transitief) van hem afhangt.
- **Geen aggregator aan parent-kant.** De parent-kaart gaat direct naar `Done`
  na het wegschrijven van het plan. Er is geen sessie die de deliverables van
  kind-kaarten later samenvoegt op de parent. Als je een samenvatting wilt,
  maak daar zelf een aparte kaart voor.
- **Eén analyst per parent.** Parallelle analyst-sessies voor dezelfde parent
  zijn niet toegestaan — de tweede dispatch wordt overgeslagen.
- **Persona vast op `analyst` in deze iteratie.** `analyst_agent_id` en
  `executor_agent_id` accepteren alleen provider-ids
  (`claude-code`, `mimo-code`, `codex-cli`, `open-code`, `copilot-cli`), geen
  vrije persona-namen.