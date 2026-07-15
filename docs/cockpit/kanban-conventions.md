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
| **`COLUMNS`** | `backend/app/kanban/schemas.py:13` | `["intake", "Backlog", "Impediment", "Done", "To Resume"]` | **Bron van waarheid voor wat een "vaste" kolom is op de server.** De frontend `KanbanPage.tsx FIXED_COLUMNS` is een snapshot (kans op drift). |
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
(itereert `COLUMNS`). Maar er zijn twee uitzonderingen die **buiten** die
bulk-sync vallen:

| Helper | Wanneer aangeroepen | Wat het doet |
|---|---|---|
| `ensure_intake_column` (`service.py:558`) | Iedere keer een intake-kaart wordt aangemaakt op een project dat nog geen `intake`-rij had | Voegt `intake` aan `kanban_columns` toe met `rank="0000"` (linksboven) en verschuift bestaande rijen +1 — idempotent, dus dubbel-aanroep is veilig. |
| `ensure_analyst_column` (`service.py:531`) | Iedere keer een kaart een `analyst_agent_id` krijgt op een project dat nog geen `analyst`-rij had | Idempotent, rank net vóór `Done` zodat de analyst-kolom op de natuurlijke plek tussen agent-kolommen en Done landt. |

> **De "ensure_intake_column"-bugklasse** — een project dat `enable` draaide
> **vóór** `intake` aan `COLUMNS` werd toegevoegd, heeft geen `intake`-rij in
> `kanban_columns` totdat `ensure_intake_column` (of een re-`enable`) draait.
> Zonder die rij wordt `intake` niet op het bord getoond en kunnen intake-kaarten
> onzichtbaar verdwijnen. De validatiescript
> [`scripts/check-kanban-conventions.sh`](../../scripts/check-kanban-conventions.sh)
> detecteert deze klasse voor elk project dat wel een `kanban_columns`-rij heeft
> maar niet alle namen uit `COLUMNS`.

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
| `plan` | `add_plan_attachment` (MCP/REST); `PATCH /cards/{cid}/plan-attachment` (update) | Het markdown-plan van de analyst-fase. `ref` is **de body zelf**, geen URL. Precies één per parent-kaart; `_materialize` koppelt hem aan kind-kaart `plan_ref`s. |
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

## 4. Bron van waarheid — waar lees je wat?

| Vraag | Eerst hier kijken |
|---|---|
| Welke namen zijn canoniek als "vaste kolom"? | `backend/app/kanban/schemas.py:13` — `COLUMNS`. **Server-side.** |
| Welke kolommen worden automatisch gedispatched? | `backend/app/kanban/dispatch.py:1655` — `_DISPATCH_COLUMNS`. **Server-side.** |
| Welke comment-prefix wordt waar gelezen? | Tabel §2 hierboven + de prefix-constanten in `backend/app/kanban/service.py:100,134–136,216,226`. |
| Welke `kind` mag ik op `attach_deliverable` zetten? | De MCP `attach_deliverable` docstring + `backend/app/kanban/mcp_server.py:339–361`. |
| Hoe zet ik sibling-deps op een kaart via MCP? | `mcp.create_card(..., depends_on=[...])` / `mcp.update_card(card_id, depends_on=[...])` (`mcp_server.py:125–199, 268–305`). De dispatcher gebruikt deze lijst om de kaart pas op te pakken als de genoemde siblings op `Done` of `Impediment` staan. De REST `CardCreate` / `CardUpdate` schemas (`schemas.py:147, :169`) accepteren hetzelfde veld; de MCP wrappers waren historisch beperkter en exposeerden dit alleen via `add_plan_attachment(depends_on_graph=...)`. |
| Hoe zet/lift ik een business-trigger gate? | `mcp.set_card_gate(card_id, gated_on=<trigger>)` (MCP) of `POST /api/v1/kanban/cards/{cid}/set-gate {"gated_on": "<trigger>"}` (REST). `gated_on=None` of `""` licht de gate. Leest in `dispatch._is_gated` — zie §3a voor rationale en de keuze tegen `depends_on` / `scheduled_at` / dedicated kolom. |
| Is deze productbeslissing al genomen, en wat kwam eruit? | [`decisions.md`](./decisions.md) — het chronologische beslis-register (datum, vraag, uitkomst, doc-link, kaart-id). **Kijk hier vóór je een beslissing heropent.** |
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
