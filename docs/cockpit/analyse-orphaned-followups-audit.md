# Audit: voltooide analyses zonder aangemaakte vervolgkaarten

**Kaart:** "Analyse - voltooide analyses" (`e00fc1f5`) · **Datum:** 2026-07-14 · **Type:** analyse-leaf-spike

## Aanleiding

De gebruiker merkte op dat meerdere analyses naar **Done** gingen terwijl in hun
`done_summary` stond dat de bijhorende implementatietaken *niet* waren aangemaakt —
"analyses die tot niets geleid hebben". Opdracht:

1. Werk zo autonoom mogelijk — maak zelf de meest geschikte keuze.
2. Kan de keuze niet zelf gemaakt worden → faciliteer ze optimaal en maak duidelijk
   dát ze gemaakt moet worden; zet het ticket dan niet zomaar op Done.
3. Kijk de bestaande "lege" analyses na en maak waar nodig alsnog de
   implementatiekaarten aan.

Dit doc is de audit + het logboek van de acties die deze sessie autonoom heeft
ondernomen.

## Methode

Alle 28 Done-kaarten opgehaald, gefilterd op `work_type=="analysis" || agent=="analyst"`
(13 kaarten). Per analyse: `done_summary` gelezen, de voorgestelde vervolgkaarten
geëxtraheerd uit het bijhorende `docs/cockpit/*.md`-doc, en gecontroleerd of die
kaarten daadwerkelijk op het bord staan (Backlog/Todo/Doing/Done/Impediment/To Resume).

## Systemische bevinding

De **leaf-spike-conventie** ("een analyse-spike levert alleen het beslisdoc; ze zet
haar §-aanbevelingen niet zelf in kaarten om") is de directe oorzaak. Elk spike-doc
eindigt met "Voorgestelde vervolgkaarten (niet in deze kaart aangemaakt)", en of die
ooit ontstaan hing af van een **handmatige review-round-trip** ("Review: …"-kaart).
Waar die round-trip gebeurde (abonnementen, openhands §7) staan de kaarten er; waar
hij uitbleef (transport §6, per-provider pause, portfolio-migratie) verdampten de
aanbevelingen.

Dit is al eerder herkend en gedocumenteerd:

- **`docs/cockpit/autonomous-leaf-spike-followup.md`** (uit review² `f4093f05`) —
  ontwerpt de structurele fix: relaxeer het `create_card`-verbod voor leaf-spikes zodat
  ze hun eigen §-aanbevelingen in dezelfde sessie aanmaken, met spam-guard +
  scoped-impediment-escape.
- **Self-improve-kaart `75b54887`** ("Leaf-spike maakt zijn eigen vervolgkaarten aan")
  — de dispatch-klare implementatiekaart daarvan, staat op Backlog.

**Deze audit is de eenmalige opruiming van de reeds-ontstane achterstand**; `75b54887`
voorkomt dat de achterstand opnieuw ontstaat. De twee zijn complementair.

## Inventaris per analyse

| Analyse (Done) | Voorgestelde kaarten | Op bord? | Verdict |
|---|---|---|---|
| `d7c95f89` Maximaal gebruik abonnementen | 4 fase + spike | ✅ `b4b4a663`,`710c85a5`,`c7b05504`,`5aaf3a82`,`e376f06a` | **RESOLVED** via review `ce4d2fe0` |
| `43d10300` Analyseer openhands (§7) | 4 | ✅ `0039bbc2`,`66f25047`,`d4d7c087`,`170288ee` | **RESOLVED** via review `a1ca8999` |
| `f4093f05` Review² abonnementen (proces) | proces-fix | ✅ `75b54887` aangescherpt | **RESOLVED** |
| `21b384de` Portfolio security-grens | 1 (handoff-doc) | ✅ `cd777bb3` | **RESOLVED** |
| `3a8e5304` risk_class-taxonomie | (design-doc) | ✅ doc bestaat op master | **VALS ALARM** — lege `done_summary` is bulk-seed-artefact, deliverable bestaat |
| `069ad411` Prioriteiten om te bouwen | strategische stack | n.v.t. (P0 = `29da4563`/`fab0719c`) | **GEEN kaart-producent** — sturingsdoc, output = richting |
| `d4d7c087` ACP-transport (§6) + `orchestration-substrate` §6 | **5** (4 actief + 1 gepoort) | ❌ | **ORPHAN → nu aangemaakt** (zie Acties #2) |
| `f8021618` Review: per-provider dispatch-pause | **4** (met DAG) | ❌ | **ORPHAN → nu aangemaakt** (zie Acties #1) |
| `cd028576` Portfolio-migratie | 1 (classificatie-pass) | ❌ | **ORPHAN → nu aangemaakt** (zie Acties #3) |
| `071172d7` Intake-card-routing | 1 executor + open vraag | ❌ | **OPEN BESLISSING** (intake_kind nu vs. YAGNI) — zie Acties #4 |
| `e376f06a` Same-vendor multi-account spike | C1–C4 (gepoort) | ❌ (bewust) | **OPEN BESLISSING** (§7-fork gebruiker) — zie Acties #4 |

## Acties deze sessie

### #1 — Per-provider dispatch-pause: 4 kaarten aangemaakt

De gebruiker vlagde dit expliciet ("ik zie deze mogelijkheid nergens op het kanban
bord", review-kaart `f8021618`). Het volledige 4-kaarten-plan mét dependency-DAG stond
al verbatim in de review-kaart. Aangemaakt op **Backlog** en via `add_plan_attachment`
gekoppeld aan `f8021618`:

1. Foundation — `dispatch_pause.py` per-provider key-map + helpers + unit tests (geen deps)
2. Write-path — Notification-hook + reaper resolven provider, pauzeren per-provider (dep: #1)
3. Route — `GET /dispatch-pause` `paused_providers` + `DELETE` → `clear_all_pauses` (dep: #1)
4. Frontend — `DispatchPauseBanner` per-provider regels (dep: #3)

*Relatie tot abonnementen-werk:* per-provider pause is de **reactieve failover-primitive**
die de proactieve pool-router (`c7b05504` Fase 1b) consumeert — een 429 op provider X
pauzeert X en laat de router Y kiezen. Complementair, geen duplicaat.

### #2 — Transport-laag: 5 kaarten aangemaakt

`acp-transport-decision.md` §6 verenigt bewust de vervolgkaarten van
`orchestration-substrate-decision.md` §6 tot **één** kaartenset (geen tweede
transportspoor). Fully-specced met acceptance + DAG. Aangemaakt op **Backlog**, gekoppeld
aan `d4d7c087`:

1. `[refactor]` Vervang pane-scraping-observability door structured signalen (geen deps, hoogste leverage)
2. `[feature]` `structured_events`/`headless_run`-capability in `agentic_cli`, ACP-isomorf event-schema (geen deps)
3. `[spike]` Prototype headless stream-json-transport (Claude) achter `SpawnTransport` (dep: #2)
4. `[analysis]` Human-takeover-UX voor headless sessies (dep: #3)
5. `[spike, GEPOORT]` ACP-adaptertransport als sibling — **niet nu**, activeert pas bij tweede-executor-provider-onboarding (dep: #2)

Bewust op **Backlog** — een mens/prioritering promoot ze wanneer de transport-investering
(P1/P3 in `build-prioriteiten-analyse.md`) aan de beurt is. Kaart 5 draagt in haar titel
de "niet nu"-poort zodat ze niet per ongeluk wordt opgepakt.

> **Correctie (2026-07-15, review `4ec799e8`).** Dit stond er oorspronkelijk als *"de
> auto-dispatcher claimt alleen Todo-kaarten, dus dit is veilige staging"*. Dat klopt
> niet: `_DISPATCH_COLUMNS = ("Backlog", "To Resume")` (`backend/app/kanban/dispatch.py`)
> — Backlog is juist de *bron* van auto-dispatch, en een `Todo`-kolom bestaat niet eens
> (`COLUMNS` in `schemas.py`). Backlog is alleen de facto veilig zolang auto-dispatch
> per project uit staat (`is_autodispatch_enabled`). De titel-poort op kaart 5 is dus de
> enige echte rem — zie Backlog-kaart `f8ef71a0` ("Gepoorte kaarten worden
> auto-gedispatcht zodra hun depends_on klaar is").

### #3 — Portfolio-migratie: 1 kaart aangemaakt

`portfolio-migration-plan.md` beschrijft een read-only classificatie-pass die per
bestaand project een `[portfolio-migration]`-comment post (geen auto-flip; mens flipt via
`PATCH /projects/{id}`). Concreet en implementeerbaar; portfolio-werk is actief
(`729e5a16` PortfolioPage Done, `88886571` stale-state Backlog). Aangemaakt op **Backlog**,
gekoppeld aan `cd028576`.

### #4 — Twee open beslissingen: gefaciliteerd, niet stilzwijgend beslist

Deze twee zijn **echte productbeslissingen** die ik niet unilateraal hoor te nemen
(opdracht-punt 2). Ze zijn als expliciete `[beslissing]`-kaarten op **Impediment** gezet
met de vraag + kandidaat-opties, zodat ze op het bord zichtbaar zijn en niet in dit doc
verdampen:

- **Intake_kind nu vs. YAGNI** (uit `071172d7`) — Goal 1 (intake-kolom niet
  auto-dispatchen) is al door de code afgedekt. De enige open vraag is of we nú een
  optioneel `intake_kind`-veld (brainstorm/customer-discovery/legacy-import) toevoegen of
  wachten tot er een lezer voor is. **Geen executor-kaart aangemaakt** omdat het antwoord
  de scope van die kaart bepaalt.
- **Same-vendor multi-account §7-fork** (uit `e376f06a`) — de spike stond bewust op
  NO-GO/uitgesteld tot de gebruiker bevestigt of "meerdere accounts binnen één vendor"
  überhaupt speelt (vs. meerdere vendors, wat de code al modelleert). De C1–C4-decompositie
  ligt klaar in het spike-doc en wordt pas geopend ná bevestiging.

## Resultaat

- **10 concrete implementatiekaarten** die dreigden te verdampen staan nu met correcte
  dependency-DAG op Backlog, elk gekoppeld aan hun bron-analyse.
- **2 open productbeslissingen** staan expliciet op Impediment i.p.v. begraven in docs.
- De **systemische fix** (`75b54887`) voorkomt herhaling; deze audit ruimt de achterstand op.
