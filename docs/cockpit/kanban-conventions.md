---
title: "Kanban-DB conventions"
type: reference
status: active
---

# Kanban-DB conventions

> **Bron van waarheid:** dit document is leidend voor de **string-conventies** in de
> kanban-DB (vast kolommen, comment-label-prefixes, deliverable-kinds).
> Gerelateerd canoniek: [`kanban-spec.md`](./kanban-spec.md) (datamodel),
> [`kanban-dispatch-spec.md`](./kanban-dispatch-spec.md) (auto-dispatch-gedrag),
> [`multi-agent-kanban.md`](./multi-agent-kanban.md) (analyst-fase),
> [`agent-mail-spec.md`](./agent-mail-spec.md) (cross-session berichten).
>
> Zie `00-orientation.md` → *Documenten* voor de drie-bomen-regel.

Het kanban-bord heeft **drie "onzichtbare" string-conventies** die nergens in een
prompt of error-message worden uitgelegd — ze leven als constante string in de bron,
en engineers leren ze door een test te runnen die het "verkeerde" label aftreurt.
Dit document legt ze vast zodat de volgende engineer-sessie ze niet opnieuw hoeft te
ontdekken.

## 1. De drie vaste-kolom sets

Er zijn drie verschillende sets namen in omloop rond "vaste kolommen". Ze zijn niet
synoniem en het is belangrijk ze uit elkaar te houden.

| Set | Bron van waarheid | Wat zit erin | Wat wordt er wel/niet mee gedaan |
|---|---|---|---|
| **`COLUMNS`** | `backend/app/kanban/schemas.py:13` | `["intake", "Backlog", "Impediment", "Awaiting Subtasks", "Done", "To Resume"]` | **Bron van waarheid voor wat een "vaste" kolom is op de server.** De frontend `KanbanPage.tsx FIXED_COLUMNS` is een snapshot (kans op drift). |
| **`_DISPATCH_COLUMNS`** | `backend/app/kanban/dispatch.py:1655` | `("Backlog", "To Resume")` | **Auto-dispatch scan alleen deze twee** — nieuwe kaarten worden van `Backlog` opgepakt, heropende kaarten van `To Resume`. Alles wat hier niet in zit, wordt nooit automatisch naar een sessie gestuurd. |
| **`FIXED_COLUMNS`** | `frontend/src/features/kanban/KanbanPage.tsx:23` | `new Set([...])` met dezelfde namen als `COLUMNS` | Alleen voor bord-rendering (welke kolommen verdwijnen in de agent-sectie). |

### Wat betekent dit in de praktijk?

- **Een nieuwe vaste kolom toevoegen** → voeg de naam toe aan `COLUMNS` (één plek).
  De frontend krijgt 'm vanzelf zodra het project `enable` opnieuw draait
  (`POST /api/v1/kanban/enable` itereert `COLUMNS` en maakt ontbrekende rijen aan
  — zie `router.py:728–731`). De dispatcher hoeft **niet** te veranderen: vaste
  kolommen zijn per definitie niet auto-dispatched, dus een nieuwe vaste kolom
  wordt automatisch overgeslagen.
- **`_DISPATCH_COLUMNS` uitbreiden** → doe dit alleen als de nieuwe kolom **wel**
  auto-dispatched moet worden. Vrijwel nooit: `Backlog` + `To Resume` zijn de
  complete set voor de "nieuwe taak" + "hervatte taak" flows, respectievelijk.
- **`FIXED_COLUMNS` in de frontend** → update deze **alleen** als je ook `COLUMNS`
  in `schemas.py` verandert. De lijst is een snapshot voor client-side logica;
  de server-side `COLUMNS` is leidend.

### `ensure_*_column` helpers per vaste kolom

Vaste-kolommen krijgen hun `kanban_columns`-rij meestal via `POST /enable`
(itereert `COLUMNS`). Maar er zijn drie uitzonderingen die **buiten** die
bulk-sync vallen:

| Helper | Wanneer aangeroepen | Wat het doet |
|---|---|---|
| `ensure_intake_column` (`service.py:558`) | Iedere keer een intake-kaart wordt aangemaakt op een project dat nog geen `intake`-rij had | Voegt `intake` aan `kanban_columns` toe met `rank="0000"` (linksboven) en verschuift bestaande rijen +1 — idempotent, dus dubbel-aanroep is veilig. |
| `ensure_analyst_column` (`service.py:531`) | Iedere keer een kaart een `analyst_agent_id` krijgt op een project dat nog geen `analyst`-rij had | Idempotent, rank net vóór `Done` zodat de analyst-kolom op de natuurlijke plek tussen agent-kolommen en Done landt. |
| `ensure_awaiting_subtasks_column` (`service.py:635`) | Vanuit de `move_card`-parkeerlogica, de eerste keer een kaart écht in `Awaiting Subtasks` parkeert op een project dat nog geen rij had | Idempotent, rank net vóór `Done` — zelfde beleid als `ensure_analyst_column`. |

> **De "ensure_intake_column"-bugklasse** — een project dat `enable` draaide
> **vóór** `intake` aan `COLUMNS` werd toegevoegd, heeft geen `intake`-rij in
> `kanban_columns` totdat `ensure_intake_column` (of een re-`enable`) draait.
> Zonder die rij wordt `intake` niet op het bord getoond en kunnen intake-kaarten
> onzichtbaar verdwijnen. De validatiescript
> [`scripts/check-kanban-conventions.sh`](../../scripts/check-kanban-conventions.sh)
> detecteert deze klasse voor elk project dat wel een `kanban_columns`-rij heeft
> maar niet alle namen uit `COLUMNS`.

> **`Awaiting Subtasks` is een parkeerkolom, geen agent-kolom.** Een `move_card`
> naar `Done` op een kaart met ≥1 kind-kaart (`parent_card_id == card.id`) landt
> hier in plaats van `Done`, en sluit automatisch zodra álle kinderen `Done`
> bereiken (`service.close_parent_if_all_children_done`, aangeroepen vanuit
> `mcp_server.move_card`). De regel is parent-generiek, niet
> `work_type == "analysis"`-specifiek. Zie
> [`analyse-levenscyclus-decision.md`](./analyse-levenscyclus-decision.md) §3
> voor het volledige ontwerp.

## 2. Comment-label contract

Comments op een kaart zijn **niet zomaar tekst**: een aantal prefixes zijn
*canonieke signalen* die de server leest om de kaart-status te bepalen. Schrijf
de prefix exact zoals hieronder of de consumer ziet je comment als ruis.

| Prefix | Consumer | Wanneer gepost | Trigger op |
|---|---|---|---|
| `**Summary:** ` | `enrich_done_info` (`service.py:103`) | `mcp_server.move_card` met `column="Done"` (vereist `summary`) | `op.payload.text LIKE '**Summary:** %'` — поверх de comment-tekst **minus** de prefix wordt `CardResponse.done_summary`. |
| `**Impediment:** ` | `impediment_status_for_card` (`service.py:139`) | `mcp_server.report_impediment` (zonder `options`) | Eerste comment met deze prefix zonder latere `**Resolution:**` → `impediment_status = "needs_answer"`. |
| `**Resolution:** ` | `impediment_status_for_card` | Mens-antwoord op een Impediment-vraag (UI `/resolve-impediment`) | Nieuwste comment met deze prefix → `impediment_status = "resolved"`. |
| `**Gate:** ` | Frontend (board chrome) | `mcp_server.open_gate` | Visuele indicator dat er een open `KanbanGate` is; niet server-side gelezen. |
| `**Promoted to project:** ` | (geen) | `services/inception_service.py:247` post ook een `**Summary:**` met dezelfde info; deze prefix is puur documentatie voor de activity-feed. | Wordt door `enrich_done_info` **niet** gematcht — `done_summary` blijft `None`. |
| `**Review requested:** ` | (geen) | `mcp_server.request_review` | Prefix is *uniek tegenover* `**Summary:**` zodat `enrich_done_info` het nooit als Done-summary leest. |
| `**Revisit:** ` | `dispatch.extract_revisit_question` | `mcp_server.reopen_card` | Het laatste comment met deze prefix wordt door de dispatch-prompt als `## REVISIT`-sectie ingevoegd. |
| `[dispatch-failure]` | `impediment_status_for_card` | `dispatch._move_to_impediment_after_repeated_failures` | Nieuwste comment met deze prefix → `impediment_status = "dispatch_failed"`. De UI beveelt dan **redispatch**, geen menselijk antwoord. |
| `**Outcome:** ` | `mcp_server.move_card` (analyst-Done gate) | `mcp_server.move_card` met `outcome=<value>` op een analyse-kaart (`work_type='analysis'` of `agent='analyst'`) die naar Done gaat | Verplichte tag op de Done-move van een analyse-kaart. Body is `<value> — <summary>`, met `<value>` uit de gesloten enum `decomposed` \| `not_feasible` \| `no_action_needed`. `not_feasible` en `no_action_needed` zetten óók het canonieke label (`not-feasible` / `no-action-needed`) op de kaart — `decomposed` zet geen label, de kind-kaarten zijn het bewijs. Zie `analysis-outcome-contract-decision.md` §5 voor het "waarom". |

### Veelgemaakte fouten

- **`**Promoted to project:** …` als move_card-summary** → ziet er logisch uit
  (je hebt toch een project gepromoot?), maar `enrich_done_info` matcht alleen
  op `**Summary:** `. De `done_summary` blijft `null` en de Done-kaart toont
  geen samenvatting op het bord. **Gebruik altijd `**Summary:** ` als label.**
- **`**Summary**` zonder de dubbele punt of spatie** → geen match. De prefix is
  letterlijk `"**Summary:** "` (20 tekens, inclusief de afsluitende spatie).
- **`[dispatch-failure]` voor een handmatige move naar Impediment** → de UI zal
  de kaart als `dispatch_failed` classificeren en een "Redispatch"-knop tonen
  in plaats van de vraag. Menselijke Impediment-moves moeten via
  `report_impediment` (met of zonder `options`) of een gewone `move_card` met
  `column="Impediment"` + `summary="<vraag>"` — beide produceren automatisch
  de juiste prefix intern.

### 2a. Outcome-label vocabulary (analyse-fase afronding)

Voor analyse-kaarten (`work_type='analysis'` of `agent='analyst'`) is de
`Done`-move via `mcp_server.move_card` ge-poort op een gesloten
`outcome`-enum. De canonieke label-waarden die bij `not_feasible` en
`no_action_needed` automatisch op de kaart worden gezet, zijn:

| `outcome`           | Label                | Betekenis |
|---------------------|----------------------|-----------|
| `decomposed`        | (geen — kind-kaarten zijn het bewijs) | De analyse leverde ≥1 vervolgkaart op (geverifieerd tegen `parent_card_id`). |
| `not_feasible`      | `not-feasible`       | De analyse concludeert: niet bouwen. Rationale in `summary`. |
| `no_action_needed`  | `no-action-needed`   | Sturings-/ontwerpdoc; geen vervolgkaarten. Rechtvaardiging in `summary`. |

Andere waarden worden geweigerd met `{"error": "invalid_outcome",
"allowed": [...]}`. Een analyse-Done zonder `outcome` wordt geweigerd met
`{"error": "outcome_required"}`. Een `decomposed`-claim zonder kinderen
wordt geweigerd met `{"error": "no_children"}` — dit is de anti-lie-check
die liegen over `decomposed` onmogelijk maakt. De labels worden
**append-only** op bestaande labels gezet (`not_feasible` naast bv. een
ander vrij label), niet overschreven. Zie
[`analysis-outcome-contract-decision.md`](./analysis-outcome-contract-decision.md)
§5 voor het "waarom".

> **Niet-ondersteunde vierde uitkomst: "input nodig".** Die hoort bij
> `report_impediment` (Impediment-kolom), niet bij een Done-move. De
> poort probeert 'm niet te modelleren.

## 3. Deliverable-kinds

Een deliverable (`KanbanDeliverable`) koppelt een draagbare referentie aan een
kaart: een PR, een branch, een commit-sha, een URL, of de canonieke plan-/spec-body.
De lijst van canonieke producers hieronder — voeg hier een nieuwe `kind` toe
als je er een introduceert.

| Kind | Canonieke producer | Betekenis |
|---|---|---|
| `pr` | `attach_deliverable(card_id, kind="pr", ref="<url>")` (MCP) of `POST /cards/{cid}/deliverables` (REST) | Verwijzing naar een GitHub PR. Mag URL of `<org>/<repo>#<n>` zijn — geen validatie van de wire-format, alleen dat de string niet leeg is (`schemas.py:181–187 AttachRequest.ref: min_length=1`). |
| `branch` | idem | `<branch-name>` — typisch `k-<kaart-slug>` of een ander werkstation-conventie. |
| `commit` | idem | Volledige `<sha>` (40 hex). |
| `link` | idem | Een willekeurige URL (docs, dashboards, externe systemen). |
| `note` | idem | Vrije tekst — geen URL/SHA-vereisten. |
| `plan` | `add_plan_attachment` (MCP/REST); `PATCH /cards/{cid}/plan-attachment` (update) | Het markdown-plan van de analyst-fase. `ref` is **de body zelf**, geen URL. Precies één per parent-kaart; `_materialize` koppelt hem aan kind-kaart `plan_ref`s. **Childless escape hatch:** voor een intake-kaart (of andere kaart zonder kinderen) is `attach_deliverable(kind="plan", ref=<md body>)` het intake-correcte pad — `add_plan_attachment` weigert kind-loze parents (`mcp_server.add_plan_attachment:690-696`). |
| `plan_ref` | `add_plan_attachment` (idem) | Pointer op een kind-kaart terug naar het `plan`-deliverable van de parent. `ref` is de `plan_deliverable_id`. |
| `spec` | `attach_deliverable(card_id, kind="spec", ref=<md body>)` | Companion van `plan` — output van de `brainstorming`-skill. `ref` is wederom de body (lege body wordt geweigerd: `mcp_server.attach_deliverable:349`). |

> **`DELIVERABLE_KINDS`** in `schemas.py:14` is `["pr", "branch", "commit", "link", "note"]`
> — de korte lijst die **gevalideerd** wordt door clients die het als enum zien
> (Pydantic `Literal` etc.). `plan`, `plan_ref` en `spec` worden **apart**
> afgehandeld door de plan/spec-tools en staan niet in deze enum — als client
> voel je je vrij om ze te posten via dezelfde `attach_deliverable` MCP-tool,
> maar `DELIVERABLE_KINDS` zal ze niet "kennen".

## 3a. Card-gate (`metadata.gated_on`) — business-triggers buiten de kaart-DAG

> **Bron van waarheid:** [`backend/app/kanban/dispatch.py:_is_gated`](../backend/app/kanban/dispatch.py)
> (de predicate die de dispatcher elke tick uitleest) +
> [`backend/app/kanban/mcp_server.py:set_card_gate`](../backend/app/kanban/mcp_server.py)
> (canonieke set/clear tool) +
> [`backend/app/api/v1/kanban/router.py:set_gate`](../backend/app/api/v1/kanban/router.py)
> (REST mirror op `POST /api/v1/kanban/cards/{cid}/set-gate`).

Een kaart kan **bewust gepoort** zijn op een *business-trigger* die geen
kanban-kaart is — bv. *"activeert pas bij tweede-executor-provider-onboarding"*
(kaart `a4a091fa…`) of *"wacht op JIRA-ticket PROJ-1423"*. De dispatcher kent
alleen `depends_on` (kaart-naar-kaart-DAG) en `scheduled_at` (kloktrigger);
beide zijn verkeerde tools voor een trigger die **niet door een ander kanban-
kaart of een specifiek tijdstip** wordt gemodelleerd.

De oplossing is een **machine-leesbare metadata-vlag** —
`card.metadata["gated_on"]` — een vrije tekst die de reden van de poort
vastlegt. De dispatcher leest 'm elke tick via `_is_gated(card)` en houdt de
kaart uit auto-dispatch zolang de sleutel een niet-lege string draagt. De
kaart blijft zichtbaar op Backlog met de trigger als reden, net als bij
`depends_on` of `scheduled_at`.

### Drie orthogonale hold-mechanismen

| Mechanisme | Veld | Predicate | Wanneer "los"? |
|---|---|---|---|
| Kaart-DAG | `depends_on` (lijst van card-ids) | `dep_resolver.meets_dep_prerequisites` | Alle genoemde parents staan op `Done` |
| Kloktrigger | `scheduled_at` (ISO8601) | `dispatch._is_due` | De tijd is bereikt |
| Business-trigger | `metadata.gated_on` (string) | `dispatch._is_gated` | Een mens past de metadata aan (of een tool-call) |

De drie zijn onafhankelijk: een kaart is dispatchable iff `meets_dep_prerequisites` ∧
`_is_due` ∧ `not _awaiting_plan_ref` ∧ `not _is_gated`. Geen van beide heeft
de ander nodig om te werken.

### Waarom `metadata.gated_on` (en niet de alternatieven)?

De kaart noemde drie kandidaten; dit zijn de afwegingen:

| Kandidaat | Voordeel | Nadeel | Gekozen? |
|---|---|---|---|
| **`metadata.gated_on`** | Geen schema-migratie (`metadata` bestaat al), triviaal op te heffen (verwijder sleutel), semantisch onafhankelijk van DAG/klok, UI kan de sleutel prominent renderen | Minder zichtbaar dan een dedicated kolom | **Ja** — minste complexiteit, max orthogonaal met bestaande mechanismen |
| Expliciete `Gated`-kolom | Meest zichtbaar op het bord | Nieuwe vaste-kolom-set, schema-migratie op `KanbanColumn`, front-end snapshot drift, een 4e item in `_DISPATCH_COLUMNS` skip-list | Nee — disproportionele infra voor één bit informatie |
| Hergebruik `scheduled_at` | Reeds bestaand veld, geen migratie | Semantisch verkeerd: `scheduled_at` zegt *"op tijdstip X"* en is een kloktrigger; een business-trigger heeft geen klok. Conflatie maakt de UX verwarrend ("dit zou toch mogen lopen? oh ja, er staat een datum in de toekomst") | Nee — semantische mismatch is de hele bug-klasse |

### Hoe zet je een gate / hoe licht je 'm?

De canonieke paden zijn `mcp.set_card_gate(card_id, gated_on=<string>)` (MCP)
en `POST /api/v1/kanban/cards/{cid}/set-gate {"gated_on": "<string>"}` (REST).
Beide:

1. Schrijven `metadata.gated_on` via `apply_operation("update", ...)` (zelfde
   op-log-pad als elke andere metadata-edit — replay-veilig).
2. Posten een `**Gate:** set/cleared — <trigger>` activity-feed comment zodat
   de gate-historie zichtbaar is zonder metadata te hoeven inspecteren
   (comment-prefix volgt hetzelfde patroon als `**Summary:** ` / `**Impediment:** `
   in §2).
3. Normaliseren `""` en `None` naar "geen gate" (verwijderen sleutel, niet
   schrijven als JSON null — `_is_gated` doet dezelfde normalisatie aan de
   lees-kant zodat een type-fout de kaart niet eeuwig vasthoudt).

Handmatig via `update_card(metadata={...})` kan ook, maar is *geen*
canoniek pad: het post geen audit-comment en het is makkelijker om per ongeluk
de hele metadata-bag te overschrijven. De dedicated tools doen een
sleutel-specifieke merge en loggen de intentie.

### Regressietest

`backend/tests/test_dispatch_gate.py` bevat het end-to-end regressie-scenario
uit de kaart-beschrijving: een kind-kaart met `depends_on` op een `Done`
parent en `metadata.gated_on="second-executor-provider-onboarded"` mag niet
gespawned worden door `dispatch_project` tot een mens de sleutel verwijdert.

## 4a. MCP-affordances voor `depends_on` (sibling-deps zonder plan-flow)

Sibling-deps worden in de **analyst-fase** vanzelf gewired door
`add_plan_attachment(depends_on_graph=...)` — de planner post één keer een
DAG en alle kinderen krijgen hun `depends_on` via de `link_plan_ref`-op.

Buiten die flow is er ook directe MCP/REST-ondersteuning nodig — bijv.

- **retroactieve tracking**: je hebt kaarten al aangemaakt via `create_card`,
  en pas ná creatie ontdek je dat de ene kaart op de ander moet wachten;
- **sibling-only deps**: je wilt twee losse kaarten aan elkaar knopen zonder
  een parent + plan-attachment op te tuigen.

In beide gevallen exposeert de MCP-server `depends_on` rechtstreeks, zodat je
niet terug hoeft te vallen op `curl PATCH /api/v1/kanban/cards/{id}`:

```python
# Python-MCP voorbeeld
sibling = await m.create_card("PROJ", "Sibling")
gated   = await m.create_card(
    "PROJ", "Gated", depends_on=[sibling["id"]],
)
# Of: achteraf wire je op een bestaande kaart
await m.update_card(gated["id"], depends_on=[sibling["id"]])
```

**Semantiek — bewust 1:1 met de REST `CardUpdate.depends_on`:**

- `depends_on=None` → veld niet aangeraakt (skip-when-None, identiek aan
  `title`/`description`/`metadata`).
- `depends_on=[...]` → vervangt de huidige lijst (volledige write,
  geen append/merge).
- Leeglijst `[]` → zet de kolom op `[]` (geen deps); om terug te gaan
  naar SQL `NULL` moet je via de REST PATCH gaan (Pydantic
  `exclude_unset` kan onderscheid maken tussen "afwezig" en "expliciet
  null", de MCP-wrapper kan dat niet).

De waarde landt via `apply_operation("update", ...)` in
`_materialize` (`backend/app/kanban/operations.py:206`), dezelfde
code-path als de REST PATCH — dus dispatcher-gating, op-log-replay en
`rematerialize()` gedragen zich identiek voor MCP- en REST-clients.

## 4b. Same-file Vervolgkaarten uit één analyse-doc — `depends_on` chainen

Wanneer één analyse-doc (typisch `docs/cockpit/*-analyse.md`) onderaan
een **"Vervolgkaarten"-tabel** N follow-ups uitspuugt die hetzelfde
"hot file" raken — bv. dezelfde module-level constanten of
samenhangende methoden — levert parallel dispatch van die N
**merge-conflicten** op zodra de eerste naar `master` pusht: de
overige N-1 zitten nog in een branch die dezelfde regio wijzigt, en
elke landing dwingt een verse `git worktree add --detach` +
`merge --no-ff` + handmatige conflict-resolution af. Bijkomend
probleem: wijzigingen van een concurrente sibling kunnen het
runtime-gedrag verschuiven waar je tests op leunen. Geobserveerd in
self-improve kaart `d8b137fc…` voor de 5 bevindingen van
[`spawn-test-bridge-sessions-analyse.md`](./spawn-test-bridge-sessions-analyse.md)
op `backend/app/services/scheduling/session_registry.py` (3 van de 5
landden in één week op master en forceerden elk een re-merge).

**Conventie:** als ≥2 Vervolgkaarten van één analyse-doc hetzelfde
bestand raken (of overlappende module-level state), keten ze
**lineair via `depends_on`** in plaats van parallel te dispatchen.
Dat kan via `add_plan_attachment(depends_on_graph=...)` (analyst-flow,
zie [`multi-agent-kanban.md`](./multi-agent-kanban.md) §2) of
retroactief via `update_card(depends_on=[...])` (zie §4a) als de
kaarten al aangemaakt zijn.

De keten-volgorde kan de natuurlijke prioriteit uit de analyse-doc
volgen, of gewoon de tabel-volgorde als er geen hiërarchie is — het
doel is sequentieel mergen, niet maximaliseren van
executor-parallelism. Een paar merge-cycli verliezen is goedkoper
dan drie keer dezelfde regio hoeven conflict-resolven.

**Wanneer NIET chainen:** als de Vervolgkaarten orthogonale
bestanden of subsystemen raken, is parallel dispatch prima — de
conflict-klasse geldt alleen bij overlap op een hot file. Dit is
een **proces-conventie**, geen mechanisme-fix: de dispatcher remt
parallelisme niet actief voor overlappende kaarten, de discipline
zit aan de analyst-/curator-kant bij het opstellen van de
`depends_on_graph`.

## 4c. Doc SUPERSEDED = banner + cross-ref audit (before you ship)

Een **SUPERSEDED-banner** bovenaan een `docs/cockpit/*.md` of
`docs/superpowers/{plans,specs}/*.md`-doc markeert dat de gearchiveerde tekst
**niet meer de canonieke waarheid** is — maar de banner zelf bereikt alleen
mensen die het bestand openen. Een toekomstige sessie die het bestand **niet**
opent, maar het wel via een cross-ref (inhoudsopgave, "Superpowers-tegenhanger",
promotie-ledger) als canoniek behandelt, krijgt de banner nooit te zien.

**De volledige supersession is dus banner ∪ cross-ref-audit.** Een engineer-kaart
die een doc SUPERSEDED markeert, doet vóór het shippen minimaal:

1. **Vind alle cross-refs** op de **basenaam** van het stale doc (niet op de
   hele pad-string — de basenaam is stabieler):

   ```bash
   basename=$(basename <stale-doc-path>)      # bv. 2026-07-08-subscription-usage-leftover-design.md
   grep -rn "$basename" docs/ backend/ frontend/ \
     --include='*.md' --include='*.sh' --include='*.py' --include='*.ts' --include='*.tsx'
   ```

2. **Voor elke hit** kies één van drie acties en leg 'm vast:

   | Cross-ref-categorie | Actie |
   |---|---|
   | Live feature-sectie die de stale doc als "Superpowers-tegenhanger" / ontwerp-referentie linkt | **Vervang** de framing — geen "tegenhanger" meer, maar "Voorganger (SUPERSEDED <datum>, kanban-card `<id>`)" met uitleg welke canonieke sectie de rol heeft overgenomen. |
   | Index-bestand (`docs/cockpit/README.md`, `docs/superpowers/README.md`) dat de stale doc als actieve tegenhanger of "✅ gepromoot" rij vermeldt | **Verwijder** uit de actieve lijst en zet 'm als footnote / `⚠️ superseded`-rij terug met link naar het canonieke doeldoc. `docs/superpowers/README.md` heeft hiervoor zelfs een aparte `⚠️ superseded`-status in de promotie-ledger-legend (toegevoegd 2026-07-17 in dezelfde PR). |
   | Andere doc die de stale doc historisch noemt | Laat staan, of pas aan als de context misleidend wordt — discretionair. |

3. **Verifieer** met dezelfde grep dat er geen resterende "counterpart"/"tegenhanger"-framing meer op de stale doc staat. Een cross-ref-overzicht zoals hieronder is het eindresultaat — *precies drie hits, alle drie expliciet "SUPERSEDED"*: `docs/cockpit/subscriptions.md` (sectie-opening als Voorganger), `docs/cockpit/README.md` (footnote), `docs/superpowers/README.md` (ledger-rij met `⚠️ superseded`).

**Voorbeeld uit de praktijk** (kanban-card `a495f2ce…`, 2026-07-17): de
supersession van `2026-07-08-subscription-usage-leftover-design.md` +
`-plan.md` door de simpeler `SubscriptionUsage` + `get_usage()`-vorm in
`backend/app/services/subscriptions/base.py` verving drie downstream-refs die
de stale doc nog als canonieke superpowers-tegenhanger / actieve ledger-rij
voorstelden — alle drie moesten op de hierboven beschreven manier worden
bijgewerkt. Zonder deze stap had een toekomstige sessie via
`docs/cockpit/README.md` of `docs/cockpit/subscriptions.md` nog steeds naar
het stale doc kunnen klikken, de banner zien, en zich afvragen waarom de
cockpit-kant het nog als "tegenhanger" behandelt.

**Wanneer NIET van toepassing:**

- De doc was **helemaal nooit** gelinkt (alleen in eigen prompts /
  git-history). Banner alleen is voldoende; geen cross-refs om te updaten.
- De cross-ref-update is **niet in deze PR** te rijgen — zet dan een
  vervolgkaart op Backlog (prefix `[self-improve]`) met de drie
  `file:line`-coördinaten, zodat de supersession niet half-shipt. Half-ships
  zijn erger dan een banner-only: ze misleiden actief over de canonieke
  waarheid.

**Geen geautomatiseerde check** (bewust, vooralsnog): de cross-ref-categorieën
hierboven zijn tekstueel en vragen om een lees-beslissing per hit — een
`check-doc-supersession-crossrefs.sh` zou of vals-positieve hits genereren
(commentaar, banner-citaten, git-history) of een te smal patroon hebben. De
handmatige grep + drie-categorieën-tabel is sneller dan het schrijven van de
checker en blijft auditeerbaar in de PR-diff.

## 4. Bron van waarheid — waar lees je wat?

| Vraag | Eerst hier kijken |
|---|---|
| Welke namen zijn canoniek als "vaste kolom"? | `backend/app/kanban/schemas.py:13` — `COLUMNS`. **Server-side.** |
| Welke kolommen worden automatisch gedispatched? | `backend/app/kanban/dispatch.py:1655` — `_DISPATCH_COLUMNS`. **Server-side.** |
| Welke comment-prefix wordt waar gelezen? | Tabel §2 hierboven + de prefix-constanten in `backend/app/kanban/service.py:100,134–136,216,226`. |
| Welke `outcome` mag ik op `move_card(..., outcome=…)` zetten voor een analyse-Done? | §2a hierboven + de gesloten enum in `backend/app/kanban/mcp_server.py:_OUTCOMES`. Onbekende waarden → `invalid_outcome`; zonder `outcome` → `outcome_required`; `decomposed` zonder kinderen → `no_children`. |
| Welke `kind` mag ik op `attach_deliverable` zetten? | De MCP `attach_deliverable` docstring + `backend/app/kanban/mcp_server.py:339–361`. Voor de childless-kaart escape hatch voor `kind="plan"`: §3 hierboven + de docstring op `attach_deliverable`. |
| Hoe zet ik sibling-deps op een kaart via MCP? | `mcp.create_card(..., depends_on=[...])` / `mcp.update_card(card_id, depends_on=[...])` (`mcp_server.py:125–199, 268–305`). De dispatcher gebruikt deze lijst om de kaart pas op te pakken als de genoemde siblings op `Done` of `Impediment` staan. De REST `CardCreate` / `CardUpdate` schemas (`schemas.py:147, :169`) accepteren hetzelfde veld; de MCP wrappers waren historisch beperkter en exposeerden dit alleen via `add_plan_attachment(depends_on_graph=...)`. |
| Hoe zet/lift ik een business-trigger gate? | `mcp.set_card_gate(card_id, gated_on=<trigger>)` (MCP) of `POST /api/v1/kanban/cards/{cid}/set-gate {"gated_on": "<trigger>"}` (REST). `gated_on=None` of `""` licht de gate. Leest in `dispatch._is_gated` — zie §3a voor rationale en de keuze tegen `depends_on` / `scheduled_at` / dedicated kolom. |
| Is deze productbeslissing al genomen, en wat kwam eruit? | [`decisions.md`](./decisions.md) — het chronologische beslis-register (datum, vraag, uitkomst, doc-link, kaart-id). **Kijk hier vóór je een beslissing heropent.** |
| Is deze Backlog `[problem]`-kaart eigenlijk al opgelost door recenter werk? | [`scripts/check-problem-card-staleness.sh`](../../scripts/check-problem-card-staleness.sh) — kruist `[problem]`-kaart-keywords tegen `decisions.md`-rijen + `git log`-subjects nieuwer dan `created_at`. |
| Welke agent-kolommen kunnen bestaan? | Per-project afgeleid van `.claude/agents/*.md`-filenames — `service.sync_agent_columns` + `router.enable:707`. |
| Welke agent-kolommen worden op dit moment gedispatched? | `dispatch._DISPATCH_COLUMNS` ∪ eventuele "orphan" agent-kolommen met ongeclaimde kaarten (`dispatch._next_card:1725–1737`). |

## 5. Validation

[`scripts/check-kanban-conventions.sh`](../../scripts/check-kanban-conventions.sh)
valideert dat elk project dat `kanban` enabled heeft (≥1 `kanban_columns`-rij) een
rij heeft voor elke naam uit `COLUMNS`. Dit vangt de
"project-enabled-vóór-`intake`-toegevoegd"-klasse van bugs voordat ze aan de
oppervlakte komen in de UI. Draai het lokaal of in CI na elke wijziging aan
`COLUMNS` of `ensure_*_column` helpers.

[`scripts/check-decision-register.sh`](../../scripts/check-decision-register.sh)
valideert dat elk `docs/cockpit/*-decision.md` gelinkt is vanuit het beslis-register
([`decisions.md`](./decisions.md)). Advies-only (exit 0 met een warning); `--strict`
geeft exit 1 voor CI-gebruik. Draai het als je een `[beslissing]`-kaart afrondt: een
nieuw beslisdocument zonder register-regel is precies de drift die het register moest
oplossen. Harness: `bash scripts/test_check_decision_register.sh`.

[`scripts/check-analysis-outcomes.sh`](../../scripts/check-analysis-outcomes.sh)
valideert dat elke `Done`-analyse (`work_type='analysis'` of `agent='analyst'`)
minstens één van de drie outcome-bewijzen draagt (zie
[`analysis-outcome-contract-decision.md`](./analysis-outcome-contract-decision.md) §5):
een `**Outcome:**` activity-comment, een `not-feasible`/`no-action-needed`-label,
of ≥1 kind-kaart. Vangnet voor het REST-bypass-gat (de gate zit alleen op de
MCP-tool) én voor de historische voorraad die de gate niet retroactief kan
dekken. Advies-only; `--strict` voor CI; `--since YYYY-MM-DD` verschuift de
historic-grens (default: 2026-07-16, commit `b2e7333` van de gate). Harness:
`bash scripts/test_check_analysis_outcomes.sh`.

[`scripts/check-problem-card-staleness.sh`](../../scripts/check-problem-card-staleness.sh)
flagt elke open `[problem]`-kaart op `Backlog` / `Doing` / `Impediment` (zonder
`[self-improve]`) waarvan de keywords overlappen met een **nieuwere** rij in
[`decisions.md`](./decisions.md) (Datum strikt na `created_at`) of een
**nieuwer** commit-subject (`git log --since`). Doel: de
"al-gefixed-door-onverwant-werk"-klasse vangen vóór de dispatcher een sessie +
worktree claimt. De persona-instructie "reproduce first, skip impl if it
doesn't reproduce" sluit deze klasse af *binnen* de sessie, maar die heeft dan
al een dispatch-cyclus betaald — deze sweeper maakt van die cyclus een `grep`.
Advies-only (`OK:` / `WARNING:`); `--strict` geeft exit 1 voor CI. Same-day
sources worden conservatief uitgesloten (`created_at` heeft tijd, `Datum`
alleen datum — onbekende volgorde). MIN_OVERLAP=2 keywords (lowercase,
stopword-filter, ≥3 chars). Harness:
`bash scripts/test_check_problem_card_staleness.sh`.

### `--check-headers` — de vier-velden-header per beslisdoc

`scripts/check-decision-register.sh --check-headers` voegt een tweede
drift-klasse toe: elk `*-decision.md` moet een uniform header-blok dragen
bovenaan (direct onder de `# Titel`-regel), en de `**Uitkomst:**`-regel in
het doc moet (whitespace-normalized, prefix-match) overeenkomen met de
overeenkomstige `Uitkomst`-cel van het register.

Het header-formaat staat in [`decisions.md` §Conventie](./decisions.md).
Kort:

```markdown
**Datum:** YYYY-MM-DD
**Status:** besloten | herzien | voorgesteld
**Kaart:** `<card-id>`
**Uitkomst:** <één zin — dezelfde zin als de register-regel>
```

Zonder een dergelijke header kan het register de uitkomst niet meer
machine-leesbaar verifiëren — dan moeten datum en uitkomst weer uit de
prosa worden gepeuterd (kaart `78cb8ce3…`, het "grep-archeologie"-
probleem dat de header-conventie sloot). Advies-only by design; voor
CI-gebruik: `bash scripts/check-decision-register.sh --check-headers --strict`.
