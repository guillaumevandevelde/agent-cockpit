# Multi-agent kanban — smoke-test cookbook

> Status: handleiding voor de twee-fase workflow (analyst → executors).
> Bouwt voort op `kanban-spec.md` en het design in
> `docs/superpowers/specs/2026-07-08-multi-agent-kanban-design.md`.

> **Bron van waarheid:** dit document is leidend voor de multi-agent flow
> (analyst → kind-kaarten + dependency-DAG + plan-attachment).
> Gerelateerde superpowers-werkdocumenten:
>
> - `docs/superpowers/specs/2026-07-08-multi-agent-kanban-design.md` — ontwerp-rationale + datavelden.
> - `docs/superpowers/plans/2026-07-08-multi-agent-kanban.md` — TDD-implementatieplan dat bovenstaande heeft uitgevoerd.
>
> Zie `00-orientation.md` → *Documenten* voor de drie-bomen-regel.

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
   hieronder apart gezet). **Uitzondering:** vereist een kind-kaart zélf nog
   onderzoek, scope-bepaling of verdere decompositie vóór een executor 'm zonder
   extra context kan implementeren, geef 'm dan `work_type="analysis"` mee. Zo'n
   kind routeert bij dispatch naar de `analyst`-persona (i.p.v. de executor) en
   krijgt het 📊-badge, zodat het eerst een eigen plan-fase doorloopt. Kind-kaarten
   die al direct uitvoerbaar zijn krijgen een passend `work_type`
   (`feature`/`bug`/`chore`) of laten het veld leeg.
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

Loopt de analyst tijdens de analyse vast op iets dat alleen een mens kan beslissen
(technologie-keuze, scope-vraag, onduidelijke requirements), gebruik dan
`report_impediment(card_id, question, options=[...])` — de kaart gaat naar
`Impediment`, de claim wordt vrijgegeven en de sessie eindigt direct. Bij
`options=` verschijnen er keuze-knoppen in de UI; het gekozen antwoord wordt bij
`resolve_impediment` automatisch in de `impediment_question` van de hervatte
sessie gezet. Zie `kanban-dispatch-spec.md` → *Reporting a human-decision
impediment* voor details.

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

## 6. Bij analyst-crash: herstel-pad

Een analyst-sessie kan halverwege crashen — net als elke andere sessie. De
dispatcher heeft een automatisch vangnet, maar soms is menselijk ingrijpen
nodig. Dit is het stappenplan.

### 6.1 Hoe herken je een vastgelopen analyst?

Een analyst-kaart is "vastgelopen" als **alle drie** kloppen:

1. De kaart staat al lange tijd in de `analyst`-kolom (langer dan een
   normale sessie duurt — een paar uur, niet een paar minuten).
2. `analyst_run_id` is **niet** gezet op de kaart (controleer via de
   CardEditDialog of de REST API).
3. Er is geen levende tmux-sessie meer die aan de kaart-claim hangt
   (`claimed_by` start met `agent:` maar de sessie bestaat niet meer).

Het typische gevolg: de dispatcher blijft de analyst proberen te spawnen
bij elke tick tot `MAX_DISPATCH_FAILURES` wordt bereikt; dan verplaatst de
kaart zich naar `Impediment`.

### 6.2 Herstarten met `redispatch_card`

Voor kaarten met `analyst_agent_id` gezet en geen `analyst_run_id` is de
juiste reactie **`mcp__cockpit-kanban__redispatch_card`** (of de UI-actie
"Redispatch" op de kaart). De dispatcher bepaalt automatisch de juiste
fase:

- Kaart heeft `analyst_agent_id` en geen `analyst_run_id` → er wordt een
  **analyst**-sessie gespawnd (geen executor — die stap is nog niet
  geweest).
- Kaart heeft al een `analyst_run_id` (analyst is klaar) → er wordt een
  **executor**-sessie gespawnd.
- Kaart heeft geen `analyst_agent_id` (legacy single-agent) → er wordt een
  executor-sessie gespawnd via de oude logica.

`redispatch_card` doodt eerst de bestaande tmux-sessie (als die er nog
is), geeft de claim vrij, en spawnt dan opnieuw. De bestaande worktree
wordt hergebruikt als er een resumable Claude-transcript in staat
(dezelfde logica die de dead-session reaper gebruikt).

### 6.3 Forceren: plan helemaal opnieuw

Soms is de analyst wel klaar maar is het plan corrupt, of zijn de
kind-kaarten half aangemaakt. In dat geval wil je niet "opnieuw
dispatchen" maar "opnieuw plannen vanaf nul". Twee opties:

- **Handmatig via de REST API** — wis `analyst_run_id` met
  `PATCH /api/v1/kanban/cards/{card_id}` en payload
  `{"analyst_run_id": null}`. De volgende tick ziet de lege
  `analyst_run_id` en spawnt opnieuw een analyst. Verwijder de
  bestaande `plan`/`plan_ref` deliverables apart als je ook het plan
  zelf wilt wissen (de Plan-tab in de drawer laat die zien).
- **Wachten op een dedicated endpoint** — een toekomstige
  `mcp__cockpit-kanban__reset_plan` tool kan dit in één aanroep doen.
  Tot die tijd is de REST PATCH de canonieke escape-hatch.

### 6.4 Wat NIET te doen

- **Handmatig kind-kaarten aanmaken zonder plan.** Zonder
  `add_plan_attachment` (en dus zonder `plan_ref` deliverables) zien de
  kind-kaarten de "Plan niet beschikbaar"-placeholder in hun prompt. De
  executor weet dan niet wat hij moet doen.
- **Parent-kaart direct naar `Done` verplaatsen zonder plan.** De
  kind-kaarten worden dan als zelfstandige Backlog-kaarten behandeld
  zonder `parent_card_id`-binding; ze verliezen hun koppeling aan het
  plan.
- **`analyst_run_id` op een niet-lege waarde zetten om de dispatcher
  over te slaan.** De analyst is dan niet écht gedraaid; de kind-kaarten
  ontvangen een corrupt of leeg plan. Gebruik in plaats daarvan de
  REST PATCH om `analyst_run_id` juist op `null` te zetten.

## 7. REST-fallback voor `add_plan_attachment`

`add_plan_attachment` is primair een MCP-tool. Het heeft sinds de
"[problem] worktree-gc verwijdert branch/worktree van actieve analyst-sessie"
fix een REST-tegenhanger:

- `POST /api/v1/kanban/cards/{cid}/plan-attachment`
  met body `{plan_markdown, child_card_ids, depends_on_graph?}`
  → retourneert `{parent_card_id, plan_deliverable_id, child_card_ids}`.

De validatie is identiek aan de MCP-versie (`parent_mismatch`,
`child_not_found`, `cycle_detected`, `too_many_children`) — dezelfde
op-log, dezelfde schemas. Gebruik dit entry-point wanneer de kanban
MCP-server onbereikbaar is maar de REST API nog wel werkt (bv. de MCP-
proces-cwd is weggehaald door een eerder gc-incident; de REST-mount op
`:8000` heeft daar geen last van). De frontend-wrapper zit in
`frontend/src/features/kanban/api.ts` als `kanbanApi.addPlanAttachment`.

De PATCH `/cards/{cid}/plan-attachment` blijft bestaan voor het
overschrijven van een bestaand plan-attachment (de Plan-tab "Opslaan"-
knop in de CardDrawer gebruikt die).
