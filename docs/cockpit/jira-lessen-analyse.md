---
title: "Wat kunnen we leren van JIRA? — kritische analyse"
type: analysis
status: active
---

# Wat kunnen we leren van JIRA? — kritische analyse

> Status: **analyse, geen besluit.** Leaf-spike voor de kanban-kaart
> "Analyse - leer van JIRA" (2026-07-14). Vraag van de gebruiker: kijk kritisch
> of er iets van JIRA te leren valt — met name **hoe zij items koppelen, subtaken
> aanmaken en de zichtbaarheid verbeteren**, en of we bij te veel velden een kaart
> in een **apart venster** moeten openen. Bouwt voort op `kanban-spec.md`,
> `multi-agent-kanban.md`, `kanban-conventions.md` en `work-type-routing-analysis.md`.

## 0. Kernboodschap vooraf

JIRA is ontworpen voor **mensen-teams met sprints, story points, assignees en
approval-workflows**. Ons bord is grotendeels **agent-gedreven**: de dispatcher
claimt en spawnt kaarten autonoom, personas doen analyse/executie, en de mens zit
op de beslissings- en review-punten. Het klakkeloos overnemen van JIRA-concepten
(sprints, epics-als-verplichte-hiërarchie, story points, custom-field-schema's,
workflow-transitie-schermen) zou **precies de complexiteit importeren die de
gebruiker "te complex" noemt**, zonder waarde voor een autonoom bord.

De vraag is dus niet "wat doet JIRA" maar "welke **primitieven** van JIRA lossen een
concreet probleem op dat wij vandaag écht hebben". Drie daarvan doen dat:

1. **Getypeerde, bi-directionele koppelingen** (met name `duplicates` en `relates-to`) —
   lossen een pijn op die onze dedupe-workflows (session-retro, flag-problem,
   market-research) elke sessie handmatig bevechten.
2. **Zichtbaarheid van bestaande relaties** — onze `depends_on`/parent-data bestaat al,
   maar is in de UI vrijwel onvindbaar en niet-navigeerbaar.
3. **Full-page / apart-venster kaartweergave** — direct gevraagd; onze `CardDrawer`
   is één 1261-regel `LG`-modal die de "te veel velden"-pijn nu al veroorzaakt.

De rest is bewust **non-doel** (§5).

## 1. Huidige stand (geverifieerd in de code)

### 1a. Welke koppelingen bestaan er al?

| Koppeling | Veld / bron | Semantiek | Richting |
|---|---|---|---|
| **Afhankelijkheid** | `KanbanCard.depends_on` (JSON list of card-ids) — `schemas.py`, resolver in `dep_resolver.py` | Kind B start pas als alle deps in kolom `Done` staan. Missing parent = niet-geblokkeerd. | Eenrichting (B → A), alleen "blocks/blocked-by" |
| **Decompositie** | `KanbanCard.parent_card_id` + `plan_ref`-deliverable | Analyst-parent → executor-kind. Plan-attachment koppelt de set. | Eenrichting (kind → parent) |
| **Review** | `metadata.reviewed_card_id` | Review-kaart wijst terug naar de beoordeelde Done-kaart. | Eenrichting, "geen aggregator werkt het origineel bij" |
| **Spec-doc** | `metadata.spec_doc` (`SPEC_DOC_META_KEY`) | Kaart → canoniek `docs/cockpit/`-doc dat 'ie implementeert. | Eenrichting, kaart → doc |
| **Labels** | `KanbanCard.labels` (JSON) | **Puur decoratief** — nergens in `dispatch.py` gelezen (`work-type-routing-analysis.md` §1a). | n.v.t. |

**Observatie:** we hebben vier ad-hoc, eenrichtings-koppelingsmechanismen (twee eerste-klas
kolommen, twee `metadata`-conventies). Er is **geen uniform, getypeerd, bi-directioneel
link-model**. Er is geen `duplicates`, geen `relates-to`, geen `causes/caused-by`.

### 1b. Wat kan een mens/agent vandaag NIET uitdrukken?

- "Deze kaart is een **duplicaat** van die andere" — terwijl `session-retro`,
  `flag-problem` en `market-research` alle drie een expliciete dedupe-stap hebben die
  eindigt in *"comment op de bestaande kaart óf maak een nieuwe"*. De losende kaart
  wordt gesloten zonder machinaal spoor naar het origineel.
- "Deze bug **is veroorzaakt door** die feature-kaart" (causaliteit voor postmortems).
- "Deze twee kaarten **hangen samen**, lees ze samen" — zonder een harde dep te forceren
  (want `depends_on` blokkeert ook de dispatch, wat je vaak niet wilt).

### 1c. Zichtbaarheid van bestaande relaties = slecht

Geverifieerd in `frontend/src/features/kanban/components/CardDrawer.tsx`:

- `depends_on` en `parent_card_id` worden **alleen** gerenderd binnen de plan-sectie
  (regels 698–732), en **alleen** als afgekapte 8-teken-id-badges (`depId.slice(0, 8)`).
- De parent-knop doet `toast.info("Open parent … in the board")` (regel 712) — hij
  **navigeert niet**; de gebruiker moet de parent zelf op het bord terugzoeken.
- De `depends_on`-badges zijn **niet klikbaar** (regels 726–729) — enkel weergave.
- Er is **geen omgekeerde weergave**: op kaart A zie je nergens "wordt geblokkeerd door
  B" of "blokkeert C, D". De DAG bestaat in data maar is in de UI onzichtbaar behalve
  als je toevallig op de juiste kaart de plan-sectie opent.

### 1d. Card-detail = één grote modal

`CardDrawer` is **1261 regels** en rendert in een `MODAL_SIZES.LG`-dialog
(`DialogContent className={MODAL_SIZES.LG}`, regel 1027). Het bevat titel, beschrijving,
kolom, work_type, agent/model, transport, labels, priority, deliverables, plan-editor,
spec-link, run-tab, comments/activity. Dat is **precies de "te veel relevante velden"-
situatie** die de gebruiker noemt: een modal is een slechte container voor een dicht,
scrollbaar, multi-tab detailscherm, en je verliest deep-linkbaarheid (geen URL per kaart).

### 1e. Wat we WEL al goed doen (JIRA heeft dit ook, wij ook)

- **Sub-taken / decompositie**: de analyst-fase splitst een parent in kind-kaarten met
  een dependency-DAG en plan-attachment (`multi-agent-kanban.md`). Dit is functioneel
  gelijk aan JIRA's Story → Sub-task, maar dan **agent-gedreven** i.p.v. handmatig — een
  duidelijke plus die we niet moeten weggooien.
- **Work-type → persona-routing** dekt de rol die JIRA's "issue type" (Bug/Task/Story)
  speelt, maar dan met een concreet dispatch-effect i.p.v. alleen een badge.
- **Deliverable-refs** (pr/branch/commit/plan/spec) zijn rijker dan JIRA's losse
  "development panel"-integratie voor onze use-case.

## 2. Wat JIRA doet rond koppelen / subtaken / zichtbaarheid

Ter referentie, de relevante JIRA-primitieven:

- **Issue links (getypeerd, bi-directioneel):** `blocks`/`is blocked by`,
  `relates to`, `duplicates`/`is duplicated by`, `causes`/`is caused by`,
  `clones`/`is cloned by`. Elke link toont op **beide** issues automatisch de
  omgekeerde kant.
- **Hiërarchie:** Epic → Story → Sub-task, met **rollup-progress** (een epic toont
  "7 van 12 kinderen done" als balk).
- **Sub-tasks:** lichtgewicht kind-issues, inline aan te maken op een issue, met eigen
  status maar getoond als checklist/lijst op de parent.
- **Issue-detailweergave:** volledige pagina met eigen URL (`/browse/PROJ-123`),
  deep-linkbaar; daarnaast een "detail view" side-panel. Velden zijn georganiseerd in
  secties met **progressive disclosure** ("show more fields", ingeklapte panelen).
- **Backlog ↔ board-scheiding + swimlanes** (per epic/assignee/label).

## 3. Kritische filter — wat past bij óns bord?

| JIRA-primitief | Lost een reëel probleem van ons op? | Oordeel |
|---|---|---|
| Getypeerde bi-directionele links (`duplicates`, `relates-to`, `causes`) | **Ja** — dedupe-workflows missen een machinaal duplicaat-spoor; postmortems missen causaliteit | **P1 — overnemen, minimaal** |
| Bi-directionele weergave van bestaande `depends_on` | **Ja** — data bestaat, UI verbergt 'm | **P0 — laaghangend, hoge waarde** |
| Klikbare/navigeerbare relatie-badges | **Ja** — parent-knop navigeert nu niet eens | **P0 — met bovenstaande** |
| Full-page kaartweergave met eigen URL | **Ja** — direct gevraagd; modal barst uit z'n voegen | **P1 — deep-linkbaar detail** |
| Progressive disclosure van velden (ingeklapte panelen) | Deels — verlicht modal-druk als full-page (nog) niet komt | **P2 — goedkope tussenstap** |
| Epic-rollup progress-balk op parent | Deels — parent toont nu geen "X/N kinderen done" | **P2 — nice-to-have visibiliteit** |
| Swimlanes / backlog-board-scheiding | Nauwelijks — ons bord is smal, agent-gedreven | **Non-doel (§5)** |
| Story points / sprints / velocity | Nee — geen mensen-sprint-cadans | **Non-doel** |
| Custom-field-schema's / workflow-transitieschermen | Nee — importeert net de "te complex"-bloat | **Non-doel** |
| Verplichte issue-type-hiërarchie | Nee — `work_type` + analyst-decompositie dekt dit lichter | **Non-doel** |

## 4. Aanbevelingen (geprioriteerd)

Elke aanbeveling is los dispatchbaar; scope bewust klein gehouden.

### P0 — Maak bestaande relaties zichtbaar en navigeerbaar (frontend-only)
**Probleem:** de DAG-data bestaat maar is onvindbaar (§1c).
**Scope:**
- Toon op elke kaart een **"Relaties"-sectie** in `CardDrawer` die zowel de
  vooruit- als achteruit-richting rendert: *Blocked by* (= `depends_on`), *Blocks*
  (= kaarten die déze kaart in hun `depends_on` hebben — server-afgeleid), *Parent* /
  *Children*.
- Maak alle relatie-badges **klikbaar** → open de gerefereerde kaart in de drawer
  (niet enkel een toast). Toon kaart-**titel + kolom-status**, niet enkel `id[:8]`.
- Backend: één read-endpoint dat voor een kaart de inkomende `depends_on`-verwijzers
  + kinderen teruggeeft (of verrijk `CardResponse` met `blocked_by`/`blocks`/`children`
  afgeleid uit de bestaande kolommen — geen datamodel-wijziging).
**Waarom eerst:** hoogste waarde/laagste risico; geen schema-migratie; verbetert direct
de visibiliteit die de gebruiker vraagt. **Risico:** verwaarloosbaar (read-only + UI).

### P1 — Getypeerd, bi-directioneel link-model (minimalistisch)
**Probleem:** geen manier om `duplicates`/`relates-to`/`causes` uit te drukken (§1b);
dedupe-workflows verliezen het spoor naar het origineel.
**Scope (bewust smal):**
- Introduceer **niet** een generiek link-schema met tientallen typen. Begin met een
  kleine vaste enum, bv. `LINK_TYPES = ["duplicates", "relates", "causes"]`, opgeslagen
  als `metadata.links: [{type, target_card_id}]` — **hergebruik de bestaande `metadata`-
  bag, geen nieuw datamodel** (zelfde patroon als `reviewed_card_id`/`spec_doc`).
- `depends_on` blijft de **enige** link die dispatch-gedrag stuurt (blocking). De nieuwe
  typen zijn puur informatief/visueel — ze mogen **nooit** de dispatcher beïnvloeden
  (anders herintroduceer je JIRA's workflow-complexiteit).
- Bi-directionele weergave hergebruikt de P0-"Relaties"-sectie.
- **Dedupe-integratie:** laat `session-retro`/`flag-problem`/`market-research` bij het
  vinden van een duplicaat een `duplicates`-link zetten i.p.v. (of naast) enkel een
  comment. Zo krijgt de dedupe-workflow eindelijk een machinaal spoor.
**Waarom:** lost een concrete, herhaalde workflow-pijn op met minimale datamodel-impact.
**Risico:** laag; `metadata`-bag is al vrij-vorm. Belangrijkste valkuil = scope-creep
naar een vol link-schema — bewaken via de vaste enum.
**Open vraag voor de mens:** willen we `duplicates` echt puur informatief, of moet een
als-duplicaat-gemarkeerde kaart ook automatisch uit de dispatch-scan vallen? (Standaard-
aanbeveling: informatief houden; auto-uit-scan is een aparte, latere kaart.)

### P1 — Full-page kaartweergave met eigen URL
**Probleem:** `CardDrawer` (1261 r.) barst uit een modal; geen deep-link per kaart (§1d).
**Scope:**
- Voeg een route toe, bv. `/kanban/card/:cardId`, die dezelfde detail-inhoud full-page
  rendert (hergebruik de bestaande drawer-componenten; niet dupliceren).
- "Open in apart venster/tab"-knop op de kaart en in de drawer (`window.open` naar die
  route, of gewoon een echte `<a target="_blank">`). Dit is letterlijk de door de
  gebruiker gevraagde "in een nieuw venster openen".
- De drawer blijft bestaan voor snelle inline-inspectie; de full-page is voor "veel
  velden / lang lezen / delen via URL".
**Waarom:** direct gevraagd; deep-linkbaarheid helpt ook agent-mail/cross-session verwijzen
naar een concrete kaart. **Risico:** middel — routing + herbruik van drawer-state kost
zorg; grootste risico is component-duplicatie i.p.v. hergebruik.

### P2 — Progressive disclosure in de drawer (goedkope tussenstap)
Als de full-page (P1) nog even duurt: groepeer de drawer-velden in **inklapbare
secties** (Details / Routing / Deliverables / Plan / Activity) met de meest gebruikte
open en de rest ingeklapt. Verlicht de "te veel velden"-druk zonder routing-werk.
**Risico:** laag, puur UI.

### P2 — Rollup-progress op parent-kaarten
Toon op een analyst-parent een **"X/N kinderen Done"**-indicator (en evt. mini-balk),
afgeleid uit de kind-kaarten via `parent_card_id`. Verbetert de visibiliteit van
lopende decomposities op het bord. **Risico:** laag; read-only afleiding.

## 5. Expliciete non-doelen (bewust NIET van JIRA overnemen)

Deze staan hier zodat een latere sessie ze niet "per ongeluk" alsnog voorstelt:

- **Sprints / story points / velocity / burndown** — geen mensen-sprint-cadans; het
  autonome bord heeft geen iteratie-boxing nodig.
- **Verplichte Epic→Story→Sub-task-hiërarchie** — `work_type` + analyst-decompositie
  dekt dit lichter; een verplichte hiërarchie is overhead.
- **Custom-field-schema's per project / field-configuration-schemes** — dit is precies
  de configureerbaarheids-bloat die JIRA "te complex" maakt. De `metadata`-bag dekt
  ad-hoc velden zonder schema-machinerie.
- **Workflow-transitie-schermen / approval-gates op elke move** — onze kolom-moves zijn
  bewust wrijvingsloos voor de dispatcher; approval zit op de mens-review-punten.
- **Swimlanes / aparte backlog-vs-board-modus** — ons bord is smal genoeg; kolommen +
  `work_type`-badges volstaan.
- **Link-typen die dispatch sturen** — alleen `depends_on` mag blocking zijn; nieuwe
  linktypen blijven informatief (zie P1-valkuil).

## 6. Voorgestelde vervolg (voor de mens om als kaarten in te plannen)

Deze leaf-spike levert alleen dit besluitdoc; hij maakt zelf **geen** kind-kaarten aan.
Aanbevolen volgorde als de gebruiker groen licht geeft:

1. **P0** (relatie-zichtbaarheid, frontend + 1 read-endpoint) — snelste zichtbare winst.
2. **P1 full-page kaart-route** — lost de "nieuw venster"-vraag direct op.
3. **P1 getypeerd link-model** — vereist eerst de mens-beslissing uit §4 (duplicates
   informatief vs. auto-uit-scan). Kandidaat voor `work_type="analysis"` als de
   dedupe-workflow-integratie verder uitgezocht moet worden.
4. **P2**-items (progressive disclosure, rollup) als losse laaghangende opruimkaarten.

## 7. Samenvatting

JIRA's waarde voor ons zit **niet** in zijn feature-breedte maar in drie primitieven:
**getypeerde bi-directionele links**, **zichtbaarheid van relaties**, en een
**deep-linkbare full-page kaartweergave**. Alle drie sluiten aan op een concrete,
bestaande pijn (dedupe zonder spoor, onvindbare DAG, modal-overload). De rest van JIRA
is voor een agent-gedreven bord bewust non-doel — het overnemen ervan zou net de
complexiteit importeren die de gebruiker wil vermijden. Aanbevolen startpunt: **P0**
(bestaande relaties zichtbaar/navigeerbaar maken) — hoogste waarde, laagste risico,
geen datamodel-wijziging.
