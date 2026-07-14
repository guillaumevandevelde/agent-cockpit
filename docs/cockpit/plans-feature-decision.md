# Plans-feature — analyse & richting (leaf spike)

> Kanban-kaart: **"Analyse - Plan functionaliteit"**.
> Vraag (gebruiker): *"Vandaag is er onder operations een plan functionaliteit. Geen
> idee waar deze vandaag voor dient, er verschijnt daar niets in? Is het de bedoeling
> dat we daar een overzicht hebben van plannen en documentatie? Hoe kunnen we deze
> optimaal gebruiken? Enten op spec-driven development?"*
>
> Dit is een leaf-spike: DoD is dit beslisdocument met een verklaring van de huidige
> stand + een concrete aanbeveling. Geen feature-code in deze kaart.
> Verwant: [`spec-driven-development-analysis.md`](./spec-driven-development-analysis.md),
> [`spec-driven-development-fase-0-decision.md`](./spec-driven-development-fase-0-decision.md).

## TL;DR

De Plans-pagina is **verweesde infrastructuur**: een volledig gebouwde browser + CRUD-
service + migratiescript bovenop een tabel (`kanban_plans`) waar in de **live workflow
niets naar schrijft**. Daarom verschijnt er niets. Het is geen bug in de pagina — er is
simpelweg geen producent.

Tegelijk produceert het platform *wél* volop plannen en specs, maar die leven in **drie
andere stores** die de Plans-pagina niet toont. De pagina is dus een leeg venster naast
een volle kamer.

**Aanbeveling:** niet de lege tabel oplappen, maar de Plans-feature **herbestemmen tot
het read-only mensvenster op de canonieke spec-/plan-laag** die de spec-driven-
development-sporen (Fase 0/1/2) al hebben opgebouwd — en de verweesde `kanban_plans`-
tabel + handmatige CRUD **uitfaseren**. Dit is precies het "enten op spec-driven
development" dat de kaart intuïtief aanvoelt, en het dicht het gat dat de SSOT-analyse
zelf benoemde: er is een durend, doorbladerbaar spec-oppervlak nodig, maar geen mens-UI
die het toont.

Dit raakt productstrategie (een hele feature herbestemmen/uitfaseren) → **menselijke
go/no-go op de richting** voordat uitvoering start (zie §7).

## 1. Wat de feature vandaag ís (geverifieerd in de code)

- **Frontend** `frontend/src/features/plans/PlansPage.tsx` + `PlanDetailPage.tsx`:
  lijst met stat-cards (totaal, datumbereik, grootte), client-side zoek, groepering
  per datum, detail-view met markdown-render en "linked sessions". Read-only in de UI —
  er is **geen "nieuw plan"-knop of editor**, ook al bestaan `createPlan`/`updatePlan`
  in `usePlansApi.ts`.
- **Backend** `backend/app/api/v1/plans.py` → `KanbanPlanService`
  (`backend/app/services/kanban_plan_service.py`): CRUD + zoek + stats over de
  `kanban_plans`-tabel, **gescoped op `project_key`**. De SPA stuurt het actieve
  project-pad mee (`useProjectContext`), dat resolvet naar een git-remote-key
  (bijv. `git:github.com/…/claude-cockpit`).
- **Herkomst:** oorspronkelijk (claude-deck-erfenis) een bestandsbrowser over Claude
  Code's native ExitPlanMode-plannen in `~/.claude/plans/*.md`. Later (kanban-kaart
  `727470a8`, "drie-bomen-regel") her-backed op de kanban-DB-tabel `kanban_plans`, met
  een eenmalig migratiescript `migrate_plans_to_kanban.py`. `docs/features/plans.md`
  beschrijft nog de **oude** bestand-gebaseerde variant en is dus verouderd.

## 2. Waarom er niets verschijnt (de diagnose)

Er is **geen writer naar `kanban_plans` in de normale workflow**. De enige twee writers:

1. **`POST /plans` (handmatige REST)** — `createPlan`/`updatePlan` bestaan in de hook,
   maar **geen UI-pad roept ze aan** (geen knop, geen editor). In de praktijk schrijft
   niemand hier.
2. **Het eenmalige migratiescript** — importeert legacy `~/.claude/plans/*.md`. Op deze
   box schrijven Cockpit-sessies dáár niet (ExitPlanMode-output landt niet automatisch
   in die map op een manier die hierheen synct), dus de bron is leeg. En zelfs als het
   draait, landt alles standaard in de bucket **`slug:global-plans`** — een *andere*
   bucket dan de SPA voor het actieve project bevraagt (`git:github.com/…`). Dubbele
   mismatch: leeg én verkeerd geadresseerd.

Gevolg: `total_plans = 0` voor elk echt project. De pagina werkt correct; er is alleen
niets te tonen.

## 3. Het echte probleem: vier concurrerende noties van "plan/spec"

Ironisch genoeg loste kaart `727470a8` de "drie-bomen-regel" op door een **vierde** store
te introduceren. Vandaag bestaan naast elkaar:

| # | Store | Wie schrijft | Zichtbaar in Plans-pagina? |
|---|---|---|---|
| A | `kanban_plans`-tabel | niemand (live) | **ja** — maar leeg |
| B | Analyst-plan-attachments: `KanbanDeliverable(kind='plan'/'plan_ref')` **op een kaart** | de analyst-fase (`add_plan_attachment`) | nee |
| C | `docs/cockpit/*.md` canonieke beslis-/spec-docs (de SSOT-prozaboom) | mensen + engineer-kaarten | nee |
| D | Claude Code native ExitPlanMode-plannen (`~/.claude/plans/`) | CC-sessies (niet gesynct) | nee (leeg) |

De Plans-pagina toont **alleen A** (leeg). De werkelijke planning-artefacten van het
platform (B + C) zijn onzichtbaar in de enige UI die "Plans" heet. Het model-commentaar
(`KanbanPlan`, `models.py:205-208`) erkent de scheiding tussen A en B expliciet, maar
niets brengt ze samen voor een mens.

Dit sluit direct aan op de SSOT-analyse (§2, punt 2): *"De Plans-feature is een
bestandsbrowser, geen spec-motor … Als SSOT-infrastructuur is dit vandaag leeg."* En op
het sterkste argument daaruit (§3): *"Sessies zijn efemeer; specs zijn dat niet"* — er is
behoefte aan een durend, doorbladerbaar spec-oppervlak. Dat oppervlak bestaat (B + C),
maar heeft geen mensvenster.

## 4. Ontwerp-opties

### Optie A — Uitfaseren
Verwijder de Plans-pagina + `kanban_plans`-tabel + `KanbanPlanService` + migratiescript.
Eerlijk over dode infra; minder oppervlak.
*Tegen:* gooit een reële behoefte weg (durend, doorbladerbaar spec-venster) die de SSOT-
analyse zelf identificeert. Herbouwen kost later meer dan herbestemmen nu.

### Optie B — Herbestemmen tot mensvenster op de spec-/plan-laag *(aanbevolen)*
Maak van Plans een **read-only "Plans & Specs"-overzicht** dat aggregeert wat er al is:
- **C** — canonieke `docs/cockpit/`-beslis-/spec-docs (git-backed prozaboom), en
- **B** — kaart-plan-attachments (de analyst-plannen), gejoined via de Fase-1-
  `card.metadata["spec_doc"]`-link (`SPEC_DOC_META_KEY`).

De verweesde `kanban_plans`-tabel (A) wordt gedemoteerd/uitgefaseerd i.p.v. gevuld — het
is precies de "vierde boom" waar `00-orientation.md` §3 voor waarschuwt. Dit is het
mensvenster dat de spec-driven-development-sporen misten, en "ent" de feature letterlijk
op spec-driven development.

### Optie C — Writer aanhaken op de bestaande tabel
Voeg een "nieuw plan"-UI toe (`createPlan`/`updatePlan` bestaan al) en/of auto-capture
van ExitPlanMode-plannen in `kanban_plans`.
*Tegen:* houdt A als losstaande scratchpad-store in leven, **verergert** de vier-bomen-
fragmentatie en verbindt niets met spec-driven development. Behandelt het symptoom
("leeg"), niet de oorzaak (verweesd + geïsoleerd).

## 5. Aanbeveling: Optie B, gefaseerd, geënt op de bestaande SSOT-sporen

Bouw geen parallel systeem; hergebruik wat `spec-driven-development-*` al opleverde
(canonieke `docs/cockpit/`-boom, `spec_doc`-link op kaarten, drift-signaal). Concreet,
fundament → franje:

1. **Herdefinieer de feature als read-only aggregator-venster** ("Plans & Specs"): één
   plek waar een mens de durende plannen/specs van een project doorbladert. Bron =
   B (kaart-plan-attachments) + C (`docs/cockpit/`-docs), niet A.
2. **Uitfaseren van `kanban_plans` (A)** als aparte store: geen live writer, vierde boom,
   in strijd met de drie-bomen-regel. Demoteer de tabel/CRUD/migratie (of verwijder na
   bevestiging dat geen externe tooling `POST /plans` gebruikt).
3. **Join op de bestaande Fase-1-link.** De analyst-plan-attachment geldt per definitie
   als de spec (SSOT-analyse §6, Fase 1); `card.metadata["spec_doc"]` legt de kaart→doc-
   koppeling al vast. Het aggregatorvenster leest exact die ankers — geen nieuw
   datamodel.
4. **Werk `docs/features/plans.md` bij** zodra de richting vaststaat — het beschrijft nu
   de verouderde bestand-gebaseerde variant.

**Waarom niet A of C:** A weggooien vernietigt een reële, door de SSOT-analyse erkende
behoefte; C houdt de fragmentatie in stand en verbindt niets met spec-driven development.
B levert het ontbrekende mensvenster met minimale nieuwe infra en sluit de lus die de
SSOT-sporen begonnen.

## 6. Wat dit oplevert (buiten scope van deze kaart)

Dit is een leaf-spike (single deliverable = dit doc). De uitvoering van Optie B is
apart werk. Aanbevolen vervolgkaarten (indien go), grofweg in volgorde:

- **[plans-window] Aggregator-backend** — endpoint(s) die B (`kind='plan'/'plan_ref'`-
  deliverables + `spec_doc`-links) en C (`docs/cockpit/`-index) samenvoegen tot één
  read-only lijst per project. Bouwt op bestaande kanban-queries; geen nieuw datamodel.
- **[plans-window] Frontend herbestemming** — Plans-pagina toont het aggregaat i.p.v.
  `kanban_plans`; detail linkt door naar kaart of `docs/cockpit/`-doc.
- **[plans-window] `kanban_plans` uitfaseren** — demoteer/verwijder tabel + CRUD +
  migratie na bevestiging dat geen externe caller `POST /plans` gebruikt; update
  `docs/features/plans.md`.

Deze kaart maakt die kaarten **niet** aan (leaf-spike = één deliverable); ze wachten op
de go/no-go hieronder.

## 7. Menselijke go/no-go

Dit herbestemt/uitfaseert een hele feature en raakt de platform-brede spec-driven-
development-richting. Net als in `spec-driven-development-analysis.md` §7 is dit een
proces-/strategiebeslissing: de vervolgkaarten wachten op een expliciete go van de
gebruiker op **Optie B** (herbestemmen als spec-/plan-venster, `kanban_plans`
uitfaseren) vóórdat uitvoering start. Alternatieven blijven Optie A (volledig
uitfaseren) en Optie C (writer aanhaken) — expliciet afgeraden in §4-5.
