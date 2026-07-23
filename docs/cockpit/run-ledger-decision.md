---
title: "Per-kaart run-ledger — scope & ontwerp — beslissing"
type: decision
status: decided
---

# Per-kaart run-ledger — scope & ontwerp — beslissing

**Datum:** 2026-07-17
**Status:** besloten
**Kaart:** `4ce329cd…`
**Uitkomst:** ✅ **Bouwen, als aggregatie in een `CardDrawer`-tab.** Nieuw top-level-scherm afgewezen (dupliceert kaart-navigatie) → `Ledger`-tab naast Deliverables/Activity/Plan/Tokens/Run. `structured_events` als primaire bron **NO-GO nu** — dubbel geblokkeerd: headless is niet default (`DEFAULT_TRANSPORT="worktree"`) én `headless_runner._on_event` gooit events weg (geen store).

> Kanban-kaart: **`[observability] Per-kaart run-ledger: stitch prompt → files →
> tests → outcome → model`** (`work_type=analysis`, leaf design-deliverable).
> Volgt op [`orchestration-flow-analysis.md`](./orchestration-flow-analysis.md)
> §3.2 (gat 2 — "geen enkel scherm dat de run per stap toont").
>
> DoD = dit beslisdocument + concrete vervolgkaarten. Geen feature-code in deze
> kaart.

## 0. TL;DR

- **Nieuw scherm of `CardDrawer`-uitbreiding?** → **Uitbreiding van `CardDrawer`**
  (nieuwe `Ledger`-tab naast Deliverables/Activity/Plan/Tokens/Run). De drawer is
  al de per-kaart-hub met de kaart-context in de hand; een los top-level-scherm
  zou de kaart-navigatie dupliceren en de ledger loskoppelen van de kaart. (§3)
- **Databronnen — `structured_events` consumeren of bestaande bronnen
  aggregeren?** → **Aggregeer bestaande durabele bronnen** (git-diff,
  activity-feed, CI/verify, per-model tokens). De `structured_events`-route is
  vandaag **dubbel geblokkeerd** en levert daarom niets bruikbaars op korte
  termijn (§2). De getypeerde-timeline-verrijking is een **fase-2** die pas zin
  heeft nadat headless een gebruikt pad is én events gepersisteerd worden (§5).
- **Toets tegen bestaande observability** → de écht ontbrekende laag is smal: een
  per-kaart **run-samenvatting** die de spine `prompt → files → tests → outcome →
  model` aan elkaar rijgt en **doorlinkt** naar de al-gebouwde transcript- (Run)
  en token-views i.p.v. ze te herbouwen (§4).
- **Vervolgkaarten** → 2 kaarten: backend-aggregator-endpoint + frontend
  Ledger-tab (frontend `depends_on` backend). Fase-2 events-persistentie blijft
  §-prose tot headless-adoptie het concreet maakt (§6). ⇒ `outcome=decomposed`.

## 1. Wat de kaart precies vraagt

De orchestratie-analyse noemde zes dingen die een goede orchestrator per run moet
tonen. Nulmeting per kolom — **waar leeft het vandaag, en is het gestitcht?**

| Wat de kaart wil zien | Waar het vandaag durabel leeft | In-app zichtbaar? |
|---|---|---|
| welke taak elk model kreeg | `card.title` / `card.description` | ✅ (drawer-header) |
| welke context het model ontving | `dispatch.build_card_prompt` (spawn-prompt) | ❌ nergens gesurfaced |
| welke files wijzigden | `branch`/`pr`-deliverable → git-diff | ❌ extern (git) |
| welke tests draaiden / wat faalde | CI (GitHub Actions) + `.claude/state/iteration-<card>.txt` | ❌ extern |
| wat geaccepteerd werd | `Done` + `**Outcome:**`/`**Summary:**`-comment | ✅ (activity-tab) |
| welk model elke stap deed | `card.model` + per-model tokens (`CardTokensTab`) | ✅ (tokens-tab) |

Drie van de zes zijn al in-app zichtbaar, verspreid over drie tabs; drie leven
durabel maar buiten de UI. **Niets stitcht ze tot één chronologisch per-run-beeld.**
Dat is precies het gat — niet het ontbreken van de data, maar het ontbreken van de
join-laag.

## 2. De `structured_events`-route is vandaag dubbel geblokkeerd

De kaart vraagt expliciet af te wegen of de ledger `structured_events` consumeert.
Geverifieerd in code (niet uit geheugen):

**Blokkade A — events stromen alleen onder de headless-transport, en die is niet
default.** `backend/app/kanban/dispatch.py:219` → `DEFAULT_TRANSPORT = "worktree"`
(tmux). De headless-transport (`headless_runner.py`) is een opt-in derde sibling
per project. De overgrote meerderheid van de kaarten draait vandaag onder
tmux/worktree en produceert **nul** `structured_events`.

**Blokkade B — zelfs onder headless worden de events weggegooid, niet
gepersisteerd.** `headless_runner._on_event` (regel 328-358) bedraadt alleen de
load-bearing signalen: `rate_limit` → `set_paused_until`, `session_init`/
`usage_result`/`error` → *loggen*, en al het overige (`tool_call`, `message_chunk`,
`plan_update`) → `logger.debug`. Er is **geen event-store, geen tabel, geen
consumeerbare timeline** — de events zijn transient. Een run-ledger die op
`structured_events` leunt zou dus eerst een compleet nieuw persistentie-substraat
moeten bouwen (én headless tot een gebruikt pad moeten promoveren) vóór het één
rij kan tonen.

**Conclusie:** de `structured_events`-datavloer is de juiste *lange-termijn*-bron
(getypeerde per-tool-call-timeline mét per-stap-model), maar hij is geblokkeerd op
infra die niet de taak van de run-ledger is. De ledger-waarde nú halen we uit de
bestaande durabele bronnen, die ~5 van de 6 kolommen al dragen zonder enige nieuwe
datavloer.

## 3. Nieuw scherm vs. `CardDrawer`-uitbreiding

**Besluit: uitbreiding — een nieuwe `Ledger`-tab in `CardDrawer`.**

Motivatie:

- De `CardDrawer` (`frontend/src/features/kanban/components/CardDrawer.tsx`) is al
  de per-kaart-hub met tabs Deliverables / Activity / Plan / Tokens / Run. De
  ledger gaat over exact één kaart en heeft precies dezelfde context (id,
  project-path, claim/session) al in de hand.
- Een los top-level "Orchestrator"-scherm zou de kaart-selectie-navigatie
  dupliceren, een tweede bron-van-waarheid voor "welke kaart bekijk ik" invoeren,
  en de ledger loskoppelen van de plek waar een mens al naar toe navigeert als
  hij "wat is er met kaart X gebeurd?" vraagt.
- De ledger is de **structured summary** die complementair is aan de bestaande
  `Run`-tab (live pane + ruwe transcript-replay). Ze horen naast elkaar in
  dezelfde drawer, met de ledger die *doorlinkt* naar de transcript voor diepte.

## 4. Anti-dubbeling: wat de ledger NIET herbouwt

Getoetst tegen elke bestaande observability-oppervlak, zodat de ledger alleen de
ontbrekende stitch-laag bouwt:

| Bestaand oppervlak | Wat het doet | Overlap met ledger? |
|---|---|---|
| **CC Bridge / Sessions / `Run`-tab** (`CardRunTab.tsx`) | live tmux-pane + ruwe JSONL-transcript-replay | **Nee** — ruwe stream, geen gestructureerde spine. Ledger *linkt* er naartoe, herrendert 'm niet. |
| **`CardTokensTab`** (`GET /kanban/cards/{cid}/usage`) | per-model tokens + kosten + session-id | **Deels** — dekt kolom "welk model elke stap". Ledger **hergebruikt/linkt** deze tab, herleidt tokens niet opnieuw. |
| **Activity-feed** (prefix-contract `**Summary:**`/`**Outcome:**`/…) | chronologische comment-stream | **Bron, geen dubbel** — de ledger leest hieruit outcome/impediment en herschikt tot de spine. |
| **APM** (`ApmPage`, `/apm`) | Agent *Package* Manager — dependency-installer | **Nee** — puur naamcollisie, nul functionele overlap. |
| **Dashboard `/agent-activity`** | live-run pane-preview, fleet-breed | **Nee** — fleet-live, niet per-kaart-historie. |

**De écht ontbrekende laag** is daarom smal: een per-kaart join die
`prompt → files → tests → outcome → model` in één chronologische ledger toont, met
uitgaande links naar de al-gebouwde transcript- en token-views voor diepte. Geen
enkel bestaand oppervlak dekt die join.

## 5. Ontwerp van de stitch-laag (fase 1 — aggregatie)

Een backend-aggregator stelt per kaart een getypeerde `RunLedger` samen uit
bestaande durabele bronnen, en een frontend-tab rendert die als verticale
timeline. De vijf spine-stappen en hun bron:

1. **Task** — `card.title` + `card.description` (al op de kaart).
2. **Context** — de dispatch-prompt. `build_card_prompt` is deterministisch maar
   wordt nergens gepersisteerd; de aggregator reconstrueert 'm (of we persisteren
   'm one-shot bij dispatch — implementatie-keuze voor de executor). Toon
   samengevat/inklapbaar, met sectie-koppen (`## IMPEDIMENT`/`## REVISIT`) als
   die aanwezig waren.
3. **Files** — diffstat van de `branch`/`pr`-deliverable (`git diff --stat
   origin/master...<branch>`). Bestandslijst + ±regels; klik → link naar de
   PR/branch. Herbouwt géén in-app diff-viewer (dat is een aparte, grotere kaart
   als het ooit nodig blijkt).
4. **Tests** — verify/CI-uitkomst. `.claude/state/iteration-<card-id>.txt` (de
   `iteration-loop`-tracker) is de lokale bron; CI-status is een link naar de
   GitHub Actions-run. Toon pass/fail + laatste iteratie-samenvatting.
5. **Outcome + model** — `Done`/`Impediment` + `**Outcome:**`/`**Summary:**` uit
   de activity-feed, plus `card.model` en de per-model-breakdown die
   `CardTokensTab` al levert (link, niet dupliceren).

De ledger is **read-only** en **best-effort per stap**: ontbreekt een bron (bv.
een Backlog-kaart zonder branch), dan toont die stap een lege/"nog niet"-toestand
i.p.v. te falen — exact het patroon dat `CardTokensTab` al hanteert voor de
"nog geen dispatch"-toestand.

**Fase 2 (voorwaardelijk, géén kaart nu).** Zodra (a) events gepersisteerd worden
en (b) de headless-transport een daadwerkelijk gebruikt pad is, verrijkt de ledger
stap 2-5 met de getypeerde per-tool-call-timeline uit `structured_events`
(elke `tool_call` met status/duur, per-stap-model uit `session_init.model`). Dit
is een enrichment van dezelfde tab, geen herontwerp. Het wordt een concrete kaart
op het moment dat headless-adoptie het waarde geeft — nu zou het speculatieve
backlog-voorraad zijn, geblokkeerd op §2.

## 6. Aanbeveling & vervolgkaarten

**Oordeel: positief — bouwen, in aggregatie-vorm.** De stitch-laag dekt de kaart-eis
met bestaande durabele data, zonder de headless-persistentie-infra af te wachten en
zonder CC Bridge/Sessions/APM/TokensTab te dupliceren. Twee onafhankelijk scopebare
kaarten, met één echt contract tussen frontend en backend:

1. **`[observability] Backend: per-kaart run-ledger-aggregator endpoint`**
   (`feature`) — `GET /kanban/cards/{cid}/run-ledger` retourneert een getypeerde
   `RunLedger` gestitcht uit prompt-context, branch-diffstat, verify/CI-status,
   activity-outcome en model. Best-effort per stap (lege toestand i.p.v. 500 bij
   ontbrekende bron). Herleidt tokens niet — verwijst naar de bestaande
   `/usage`-payload.
2. **`[observability] Frontend: Run-ledger-tab in CardDrawer`** (`feature`,
   `depends_on` #1) — nieuwe `Ledger`-tab die het endpoint consumeert en de spine
   `prompt → files → tests → outcome → model` als verticale timeline rendert, met
   doorlinks naar de bestaande Run- (transcript) en Tokens-tabs.

`depends_on`: #2 wacht op de endpoint-vorm (`RunLedger`-schema) van #1 — een echt
consumptie-contract, geen pure sequentie.

**Niet opnieuw openen** (al beslist / bewust uitgesteld):

- `structured_events` als primaire bron → geblokkeerd op §2 (headless niet default
  + events niet gepersisteerd); fase-2-verrijking, geen fase-1-fundament.
- Los top-level orchestrator-scherm → afgewezen t.g.v. `CardDrawer`-tab (§3).
- In-app diff-viewer die de git-diff herrendert → out of scope; de ledger linkt
  naar branch/PR. Wordt pas een kaart als een concrete behoefte het rechtvaardigt.
