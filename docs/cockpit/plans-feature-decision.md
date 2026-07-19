---
title: "Plans-feature — analyse & richting (leaf spike)"
type: decision
status: proposed
---

# Plans-feature — analyse & richting (leaf spike)

**Datum:** 2026-07-14
**Status:** voorgesteld
**Kaart:** `45ac606e…` (review: `a70a9272…`)
**Uitkomst:** ✅ **BESLIST 2026-07-17 — Optie B (herbestemmen).** De gebruiker gaf go op Optie B: herbestem Plans tot read-only mensvenster op de spec-/plan-laag (B = kaart-plan-attachments + C = `docs/cockpit/`-docs), faseer `kanban_plans` uit. **Randvoorwaarde van de gebruiker:** de B↔C-join via `spec_doc` is GEEN gratis stap (0× gepopuleerd, zie §8.2) — **lever B en C eerst náást elkaar**; de join is uitgesteld werk. Gedecomponeerd in 4 vervolgkaarten (zie §10). Alternatieven A (volledig uitfaseren) en C (writer aanhaken) zijn hiermee afgewezen.

_Historische noot: tot 2026-07-15 stond deze regel ten onrechte als genomen beslissing (backfill-fout, commit `4101d56`); de review 2026-07-15 corrigeerde 'm naar "nog niet beslist". De go hierboven is de eerste échte menselijke beslissing._

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
   > ⚠️ **Bijgesteld door de review (zie §8).** Deze stap was te optimistisch: de
   > `spec_doc`-link is vandaag **0× gepopuleerd** en heeft als enige writer een
   > handmatig UI-veld. De B-kant (34 `plan`/`plan_ref`-deliverables) en de C-kant
   > (66 `docs/cockpit/`-docs) zijn elk apart wél afleidbaar zonder anker; het is
   > specifiek de **join** tussen B en C die op lucht rust. Een aggregator die op
   > `spec_doc` leunt, herhaalt de "gedefinieerd, geen producent"-fout van
   > `kanban_plans` zelf.
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
  *(Review-correctie §8: de B↔C-**join** via `spec_doc` is géén gratis stap — dat anker
  is 0× gepopuleerd. Lever B en C eerst naast elkaar; de join is een aparte kaart die
  eerst een producent voor `spec_doc` nodig heeft.)*
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

**Status 2026-07-17:** ✅ **go gegeven — Optie B.** De gebruiker koos Optie B op
kaart `a70a9272…`, met de expliciete randvoorwaarde dat B en C éérst náást elkaar
geleverd worden (de `spec_doc`-join is uitgesteld — zie §8.2 + §10). Gedecomponeerd
in 4 vervolgkaarten; zie §10.

_(2026-07-15: deze go/no-go was toen nog nooit gegeven en stond ten onrechte als
"beslist" in het register — zie §8.3.)_

> **Dit doc doet géén uitspraak (meer) over de vraagstatus.** Of de vraag daadwerkelijk
> bij de gebruiker ligt, lees je op kaart `a70a9272…` op het bord (kolom + open gate),
> niet hier. Twee opeenvolgende sessies hebben in deze paragraaf een voorlegging
> geclaimd die niet had plaatsgevonden (§8.4); de claim is daarom vervangen door deze
> verwijzing. Het bord is de bron van waarheid voor bord-acties — een doc is dat per
> definitie niet.

## 8. Review-verificatie (2026-07-15, kaart `a70a9272…`)

Een review-kaart ("Is er een gevolg voor deze analyse?") toetste dit doc tegen de
werkelijke code + live DB. Uitkomst: **de diagnose klopt en was eerder te mild**, maar
de aanbeveling had één zwakke poot en de status was verkeerd geboekt.

**1. Diagnose bevestigd én gekwantificeerd.** Gemeten op `~/.claude-registry/kanban.db`:

| Store | Meting | Zichtbaar in Plans |
|---|---|---|
| A `kanban_plans` | **0 rijen** | ja (leeg) |
| B `plan`/`plan_ref`-deliverables | **34** | nee |
| C `docs/cockpit/*.md` | **66** | nee |

De pagina is dus een leeg venster naast ~100 reële artefacten. Geverifieerd: de enige
writers naar A zijn `kanban_plan_service.py:201` (POST /plans) en
`migrate_plans_to_kanban.py:138`; `createPlan`/`updatePlan` bestaan in
`usePlansApi.ts` maar **geen enkele component roept ze aan**. `DEFAULT_PROJECT_KEY =
"slug:global-plans"` (regel 48) bevestigt de bucket-mismatch.

**2. Nieuw: de `spec_doc`-join rust op lucht.** §5 stap 3 rekende op de Fase-1-link als
bestaand fundament. Realiteit: `SPEC_DOC_META_KEY` is gedefinieerd
(`schemas.py:31`, `types.ts:188`), maar **0 kaarten** dragen 'm, en de enige writer is
een handmatig veld in `CardDrawer.tsx:773` — geen enkele agent/automatisering vult 'm.
Dit is dezelfde pathologie als `kanban_plans`: infra zonder producent. Optie B blijft
haalbaar (B en C zijn los prima afleidbaar), maar wie de **B↔C-join** wil, moet eerst
een producent voor `spec_doc` regelen — dat is echt werk, geen "geen nieuw datamodel".

**3. De beslissing stond ten onrechte als genomen geboekt.** `decisions.md` voerde deze
vraag sinds de register-backfill (`4101d56`, 2026-07-15) op met uitkomst
"**Herbestemmen.**", terwijl §7 expliciet op een go/no-go wacht die nooit kwam. Omdat
het register stelt *"staat er een uitkomst, dan is de vraag beslist"*, zat deze analyse
in een **false-settled** toestand: heropening onderdrukt, uitvoering afwezig.
Gecontroleerd of dit systemisch was — dat is het **niet**: de drie andere docs die op
een go/no-go parkeren (`spec-driven-development-fase-0`, `acp-transport`,
`orchestration-substrate`) kregen hun go aantoonbaar wél en zijn uitgevoerd
(`docs/plans-legacy/`, `structured_events.py`, headless transport in `dispatch.py`).
Plans is de enige uitzondering. De registerregel is bij deze review gecorrigeerd naar
"nog niet beslist".

**Antwoord op de reviewvraag ("is er een gevolg?"):** vandaag **nee** — geen
vervolgkaarten, `kanban_plan_service.py` + tabel onaangeroerd, pagina nog steeds leeg.
De go/no-go in §7 is daarmee nog steeds de enige blocker.

**4. Postmortem: de review-kaart reproduceerde de kwaal die ze onderzocht.**
De reviewvraag was *"is er een gevolg?"*. Het antwoord bleef **nee** omdat vier
opeenvolgende sessies (`…-2520`, `…-b030`, `…-b4b3`, plus drie sessies die niets
opleverden) dezelfde vorm hadden: diagnose stellen, doc bijwerken, `attach`, `release` —
**zonder terminale move**. De go/no-go uit §7 bereikte de gebruiker daardoor tien uur lang
niet. De analyse zonder gevolg had een review zonder gevolg gebaard.

Twee sessies "losten" dit bovendien op door in het doc te schríjven dat ze de impediment
gefiled hadden; de op-log weerlegde dat beide keren. Daaruit volgen twee lessen, in
oplopende scherpte:

- **Een doc dat een kanban-actie claimt, is geen bewijs dat de actie gebeurde.** Toets
  tegen de op-log (`GET /cards/{id}/activity`), niet tegen proza. Dit doc doet daarom
  géén uitspraak meer over de vraagstatus — §7 verwijst naar het bord.
- **De ship-workflow kent dit eindpunt niet.** Stap 7 schrijft *"move to Done"* voor,
  terwijl de juiste terminale actie bij een openstaande product-fork
  `report_impediment` is. Een sessie die de workflow letterlijk afloopt, komt er dus
  nooit vanzelf uit — de retro (stap 6) is telkens het laatste dat lukt. Dit is geen
  reeks toevallige slordigheden maar een gat in de workflow; als scope-uitbreiding
  gecommentarieerd op `fc86d037…`.

De vierde sessie (`k-review-analys-d9b9`) heeft daarom bewust **niets aan §7 toegevoegd**
en haar budget besteed aan de terminale actie i.p.v. aan een paragraaf erover. Of dat
gelukt is, bewijst dit doc niet — dat is het hele punt. Lees het bord:

```bash
curl -s "http://localhost:8000/api/v1/kanban/cards/a70a9272c7fe4134b6a8236b4c532f81/gates"
```

## 9. Werkelijke oorzaak van de churn (2026-07-16) — een bug, geen discipline

§8.4 hierboven is **weerlegd bewaard**: de diagnose ("de agents vergeten de terminale
actie", "een gat in de ship-workflow") was fout, en de aanbevolen remedie —
*"roep `report_impediment` aan i.p.v. `move_card(Done)`"* — zou net zo goed gefaald
hebben. Beide eindigen namelijk in dezelfde kapotte code. Bewaard omdat de fout zelf
leerzaam is: vijf sessies schreven een gedrags-verklaring voor wat een race condition was.

**Bewijs (op-log + backend-log, niet proza):**

| Waarneming | Bron |
|---|---|
| `move card a70a9272 (payload_keys=['column'])` gelogd 07:24:55.516 | backend-log |
| Diezelfde move-op staat **niet** in `kanban_ops` | `sqlite3 kanban.db` |
| `Killed tmux session: k-review-analys-590c` 07:24:55.**523** (7 ms later, zelfde `correlation_id`) | backend-log |
| `Exception terminating connection <aiosqlite…>` 07:24:55.526 | backend-log |
| `failed to release claim on card a70a9272` | backend-log |
| ~26 claim→spawn→dood-na-90s cycli, 99 activity-entries | `GET /cards/{id}/activity` |

**Mechanisme.** `operations._materialize` vuurde `on_card_moved_to_done` **vóór** de
commit van de aanroeper. Die cleanup killt de tmux-sessie waarin de MCP-client draait die
de move *op dat moment aan het uitvoeren is*. De client sterft → zijn in-flight request-task
wordt gecancelled → `await s.commit()` wordt nooit bereikt → **de move rollt terug, de kill
niet**. Resultaat: sessie weg, kaart nog in `analyst` én nog geclaimd → reaper → dispatcher
→ sessie #n+1. `apply_operation` logt vóór de commit, dus het log toont een move die de DB
nooit zag — precies de val waar §8.4 in trapte.

Dit trof elke kaart die naar `Done`/`Impediment` bewoog (beide zijn
`_TERMINAL_CLEANUP_COLUMNS`); dat de meeste kaarten tóch afsloten, maakte het een
flaky race i.p.v. een zichtbare storing — en dus jarenlang onopgemerkt.

**Fix:** cleanup verhuisd naar een `after_commit`-hook (`operations._cleanup_after_commit`),
zodat alleen een move die écht geland is de sessie beëindigt. Regressietest:
`backend/tests/test_kanban_terminal_cleanup_ordering.py` (faalt aantoonbaar op de oude code).

**Gevolg voor de reviewvraag.** "Is er een gevolg?" had twee blockers, niet één. De
tweede is nu weg. De eerste — de go/no-go uit §7 — is nog steeds open en is een echte
productvraag; die hoort bij een mens, niet bij nog een analyse.

## 10. Resolutie (2026-07-17, kaart `a70a9272…`) — go op Optie B + decompositie

De laatste blocker (§7-go/no-go) is beantwoord. **De gebruiker koos Optie B** met
één expliciete randvoorwaarde:

> Optie B (aanbevolen) — herbestem Plans tot read-only mensvenster op de spec-/plan-laag
> (kaart-plan-attachments + `docs/cockpit/`), faseer `kanban_plans` uit. **NB:** de
> B↔C-join via `spec_doc` is GEEN gratis stap (0× gepopuleerd) — lever B en C eerst
> náást elkaar.

Die randvoorwaarde is exact de zwakke poot die de review in §8.2 blootlegde: de
`spec_doc`-join heeft geen producent. Ze stuurt de decompositie: de kern (B en C
náást elkaar) mag niet wachten op het oplossen van de join.

**Vervolgkaarten** (kinderen van `a70a9272…`, aangemaakt in deze sessie; DAG via
plan-attachment):

| # | Kaart | Type | Dep | Kern |
|---|---|---|---|---|
| 1 | `885d0b61…` [plans-window] Aggregator-backend | feature | — | endpoint dat B (`plan`/`plan_ref`-deliverables) en C (`docs/cockpit/`-index) als twee **gescheiden** secties retourneert; geen join, geen nieuw datamodel |
| 2 | `9e33a359…` [plans-window] Frontend herbestemming | feature | 1 | Plans-pagina toont het aggregaat i.p.v. `kanban_plans`; detail linkt naar kaart (B) of rendert doc (C) |
| 3 | `528c5ca2…` [plans-window] `kanban_plans` uitfaseren | chore | 2 | tabel/CRUD/migratie demoteren ná bevestiging geen externe `POST /plans`-caller; `docs/features/plans.md` bijwerken |
| 4 | `bb1f61aa…` [plans-window] B↔C-join (uitgesteld) | analysis | 1 | éérst een producent voor `spec_doc` ontwerpen, dán de correlatie — of gemotiveerd `not_feasible` |

Kaart 4 is bewust `work_type=analysis` en losgekoppeld: de join wacht op een
`spec_doc`-producent en blokkeert de kern-levering (1-3) niet. Daarmee is de
"gedefinieerd, geen producent"-val uit §8.2 vermeden i.p.v. herhaald.
