# Synchrone sub-agent-delegatie vs. async kanban-decompositie — beslisdocument

**Datum:** 2026-07-14
**Status:** Analyse / beslisdocument (leaf-spike; geen implementatie in deze kaart)
**Trigger:** kanban-kaart "[analysis] Synchrone sub-agent-delegatie vs. async
kanban-decompositie", voortkomend uit
[`openhands-analyse.md`](./openhands-analyse.md) §4.4 + §7.4.

**Verwant:**
[`openhands-analyse.md`](./openhands-analyse.md) (§4.4 — TaskToolSet-observatie),
[`multi-agent-kanban.md`](./multi-agent-kanban.md) (de async analyst→kind-kaarten-flow),
[`reviewer-agent-decision.md`](./reviewer-agent-decision.md) (een reeds-voorgestelde
synchrone subagent-call: de feature-compliance-review),
[`upstream-agent-teams-decision.md`](./upstream-agent-teams-decision.md) (het principe:
geen concurrerende orchestratielaag náást kanban-dispatch),
[`kanban-dispatch-spec.md`](./kanban-dispatch-spec.md) (de dispatch-primitieven).

---

## TL;DR

1. **De twee modellen zijn complementair, niet concurrerend — en dat is in de codebase
   al gedeeltelijk vastgelegd.** De `engineer.md`-persona zegt vandaag al: *"waar
   parallel werk nuttig is gebruik je je eigen subagents (de `Task`-tool) binnen deze
   sessie, zodat de context behouden blijft."* De synchrone in-sessie-delegatie
   (Cockpit's `Task`/`Agent`-subagents ≈ OpenHands' `TaskToolSet`) bestáát dus al als
   pattern; hij is alleen niet als bewuste keuze tegenover de async decompositie gezet.

2. **De grens ligt op één as: durability + zichtbaarheid vs. gedeelde in-memory context.**
   Async kanban-decompositie levert een *bordzichtbare, crash-overlevende, per-eenheid
   attachbare (tmux), potentieel cross-subscription* werk-eenheid. Synchrone subagents
   leveren *gedeelde context, blocking fan-out, geen board-artefact, sterft met de
   parent*. Elk model wint precies waar het andere zwak is.

3. **Conclusie: de status quo volstaat architectonisch — er is geen nieuw
   delegatie-mechanisme nodig.** Async blijft de ruggengraat voor de *decompositie zelf*
   (dat is exact de reden dat [`upstream-agent-teams-decision.md`](./upstream-agent-teams-decision.md)
   een concurrerende in-proces orchestratielaag afwees). Synchrone subagents zijn al
   beschikbaar en hebben al één concrete toepassing in de pijplijn (de FCR uit
   [`reviewer-agent-decision.md`](./reviewer-agent-decision.md)). Het enige echte gat is
   **bewuste sturing** — de personas noemen subagents in één regel zonder *wanneer*.
   Daarvoor is één kleine, gescopete doc-/persona-kaart voorgesteld (§7). Geen machinerie.

---

## 1. Wat elk model precies is

### 1.1 Synchrone in-sessie-delegatie (`Task`/`Agent`-subagents ≈ TaskToolSet)

Eén draaiende sessie roept binnen zijn eigen run een of meer subagents aan (de
`Task`-tool; in dit harnas de `Agent`-tool). De parent **blokkeert** tot de subagent
klaar is en krijgt diens eindresultaat terug als tool-result. Kenmerken:

- **Gedeelde run, behouden context.** De subagent start met een verse context-window,
  maar zijn *resultaat* landt terug in de context van de parent — de parent kan erop
  voortbouwen zonder herintroductie.
- **Blocking / synchroon.** De parent wacht; er is geen dispatch-latency, geen claim,
  geen aparte worktree.
- **Ephemeer.** De subagent produceert geen board-kaart, geen deliverable, geen
  attachbare pane. Sterft de parent, dan is de subagent-run weg.
- **Zelfde subscription/model-budget.** De subagent draait op hetzelfde abonnement en
  telt in het budget van de parent-sessie.

Bestaande hooks in de codebase: de `engineer.md`-persona-regel hierboven, de superpowers-
skill `dispatching-parallel-agents`, en de `Agent`-tool-agenttypes (`Explore`, `Plan`,
`general-purpose`, …).

### 1.2 Asynchrone kanban-decompositie (analyst → kind-kaarten → executors)

De analyst-fase splitst een parent-kaart in N kind-kaarten met een dependency-DAG en een
plan-attachment; de dispatcher spawnt elke kind-kaart als een **aparte executor-sessie**
zodra diens deps in `Done` staan (zie [`multi-agent-kanban.md`](./multi-agent-kanban.md)).
Kenmerken:

- **Aparte sessies + aparte worktrees + aparte claims.** Geen gedeelde in-memory context;
  alle overdracht loopt via de kaart-beschrijving, het plan-attachment en Agent Mail.
- **Asynchroon / non-blocking.** Kinderen draaien parallel (dep-DAG-gerespecteerd); de
  parent-sessie (analyst) is al `Done` voordat een executor start.
- **Durable + auditbaar.** Elke eenheid is een bordkaart met deliverables, comments en een
  claim; overleeft sessie-crashes (dead-session reaper / `redispatch_card`).
- **Attachbaar per eenheid.** Elke executor draait in een eigen tmux-pane die een mens live
  kan overnemen (`tmux attach`) — de transparantie-troef uit
  [`orchestration-substrate-decision.md`](./orchestration-substrate-decision.md) §4.5.
- **Cross-subscription/model mogelijk.** `analyst_agent_id` en `executor_agent_id` kunnen
  verschillende providers/abonnementen zijn.

---

## 2. Vergelijking op concrete criteria

| Criterium | Synchroon (subagent) | Async (kanban-kind-kaart) |
|---|---|---|
| **Levensduur van het werk** | Minuten, binnen één run | Minuten→uren, over losse sessies |
| **Gedeelde context** | ✅ resultaat terug in parent-context | ❌ alleen via kaart/plan/Agent Mail |
| **Board-zichtbaarheid / auditspoor** | ❌ ephemeer, geen kaart | ✅ kaart + deliverables + comments |
| **Crash-overleving** | ❌ sterft met parent | ✅ reaper + `redispatch_card` |
| **Human-takeover (attachbare pane)** | ❌ geen eigen pane | ✅ eigen tmux-pane per executor |
| **Cross-subscription / ander model** | ❌ zelfde budget als parent | ✅ per-fase provider/model |
| **Parallellisme** | ✅ fan-out binnen één run | ✅ N executors parallel (dep-DAG) |
| **Overhead per eenheid** | Laag (geen claim/worktree/dispatch) | Hoog (context-herintroductie, dispatch-latency) |
| **Verse ("cleared") context voor review** | ✅ subagent start schoon | ✅ aparte sessie start schoon |
| **Blokkeert de parent?** | ✅ ja (synchroon) | ❌ nee (parent is al klaar) |

De tabel maakt de breuklijn scherp: **kies async zodra durability, board-zichtbaarheid,
human-takeover of cross-subscription telt; kies synchroon zodra gedeelde context, lage
overhead en een blocking in-run stap tellen.** De twee sets overlappen nauwelijks — ze
zijn complementair, geen substituut.

## 3. Beslisheuristiek: wanneer welk model

Gebruik **synchrone subagents** wanneer *alle* van deze gelden:

1. Het werk moet **af zijn vóór de parent-sessie verdergaat** (blocking sub-stap).
2. De parent heeft het **resultaat in zijn eigen context** nodig om op voort te bouwen.
3. Het werk is **ephemeer**: het hoeft geen eigen bordkaart, deliverable of attachbare
   pane te zijn (niemand hoeft het los te inspecteren of over te nemen).
4. Het past binnen **hetzelfde abonnement/model** als de parent.

Typische gevallen: read-heavy fan-out over de codebase (`Explore`-subagents), een
`Plan`-subagent voor een deelontwerp, en een **verse-context review vóór `move_card Done`**
(de FCR uit [`reviewer-agent-decision.md`](./reviewer-agent-decision.md)).

Gebruik **async kanban-decompositie** zodra *één* van deze geldt:

1. Het werk is **groot/langlopend** genoeg dat een aparte sessie de context-overhead
   verdient (uren, of ≥ een handvol onafhankelijke implementatie-brokken).
2. Het moet **bordzichtbaar en auditbaar** zijn (een mens wil de eenheid volgen, of hij
   moet crash-overlevend zijn).
3. Een mens moet de eenheid **live kunnen overnemen** (attachbare pane).
4. De eenheid draait beter op een **ander abonnement/model/provider** dan de parent.
5. Er zijn **echte `depends_on`-contracten** tussen brokken die over sessiegrenzen lopen.

Typisch geval: precies waar de analyst-persona vandaag voor bestaat — "refactor X in 4
stappen waarvan stap 2 op stap 1 wacht", uitgevoerd door N executors.

**Grensgeval — geneste decompositie.** Een executor die zijn eigen kind-kaart té groot
vindt, splitst *niet* zelf async door (dat is de analyst-fase, en een tweede
orchestratielaag in-proces is bewust afgewezen — zie §4). Hij gebruikt synchrone subagents
voor de fan-out binnen zijn sessie, óf `report_impediment` als de kaart écht opnieuw
gedecomponeerd moet worden. De async-decompositie blijft één laag diep aan de bordkant.

## 4. Waarom async de ruggengraat blijft (en synchroon dat niet vervangt)

[`upstream-agent-teams-decision.md`](./upstream-agent-teams-decision.md) wees een
*concurrerende in-proces orchestratielaag* (upstream's Agent Team Presets) bewust af: twee
antwoorden op "hoe laat ik agents samenwerken" naast elkaar is dubbel onderhoud en
verwarrend. Diezelfde logica geldt hier, maar met een cruciaal onderscheid:

- Een synchrone subagent **is geen concurrerende orchestratielaag** — hij orkestreert geen
  losse, bordzichtbare, durable sessies. Hij is een *in-run hulpmiddel* van één sessie.
  Daarom botst hij niet met kanban-dispatch; hij leeft er netjes onder.
- Zou je de *decompositie zelf* (analyst→kind-kaarten) vervangen door synchrone subagents,
  dan verlies je in één klap board-zichtbaarheid, crash-overleving, human-takeover en
  cross-subscription — precies de vier eigenschappen waarvoor de async-laag bestaat. Dat is
  een regressie, geen vereenvoudiging.

Conclusie: synchroon en async zitten op **verschillende lagen** (in-run hulp vs.
board-orchestratie), niet op dezelfde laag als rivalen. Ze mogen — en moeten — naast elkaar
bestaan.

## 5. Beslissing

**De status quo volstaat architectonisch. Er wordt géén nieuw synchroon
delegatie-mechanisme toegevoegd, en de async kanban-decompositie blijft ongewijzigd de
ruggengraat voor werk-decompositie.**

Onderbouwing:

- Het synchrone patroon **bestaat al** (`engineer.md`-persona-regel + `dispatching-parallel-agents`-skill + `Agent`-tool-agenttypes).
- Het heeft al **één concrete, gescopete toepassing in de pijplijn**: de
  feature-compliance-review (FCR) als subagent-call vóór `move_card Done`
  ([`reviewer-agent-decision.md`](./reviewer-agent-decision.md)) — die kaart staat op
  zichzelf en wordt hier **niet** opnieuw voorgesteld.
- De grens tussen de twee is helder en al deels vastgelegd; er is geen ontbrekende
  machinerie, alleen ontbrekende *sturing*.

**Het enige echte gat is bewuste sturing.** De `engineer.md`-persona noemt subagents in
één regel zonder een *wanneer*; er is geen gedocumenteerde grens tussen "spin een subagent
op" en "maak een kind-kaart". Zonder die grens riskeer je misbruik in beide richtingen: een
engineer die async decompositie in-proces namaakt (verliest durability), of een analyst die
iets ephemeers als kind-kaart opknipt (betaalt onnodige context-overhead). Dat gat wordt
gedicht met **één kleine doc-/persona-kaart** (§7), niet met nieuwe code.

## 6. Wat we bewust NIET doen

1. **De async-decompositie vervangen of dupliceren met synchrone subagents.** Zou de vier
   kern-eigenschappen (durability, board-zichtbaarheid, human-takeover, cross-subscription)
   opofferen — §4.
2. **Een tweede in-proces orchestratielaag bouwen** (roster/presets/team-launch). Al
   afgewezen in [`upstream-agent-teams-decision.md`](./upstream-agent-teams-decision.md);
   niets in deze analyse verandert dat.
3. **Een aparte `reviewer`-persona of Review-kolom.** De FCR is een subagent-call, geen
   sessie — al zo besloten in [`reviewer-agent-decision.md`](./reviewer-agent-decision.md).
4. **Geneste async-decompositie aan de bordkant** (een executor die zelf kind-kaarten
   spawnt). De decompositie blijft één laag diep; diepere splitsing gaat via
   `report_impediment` terug naar een mens/analyst — §3, grensgeval.

## 7. Voorgestelde vervolgkaart

Precies één kaart, laag-risico, doc-/persona-only (geen code-machinerie):

> **[chore] Documenteer de synchroon-subagent vs. async-kind-kaart-beslisheuristiek in de
> personas.**
>
> Voeg de beslisheuristiek uit §3 van dit document toe aan `.claude/agents/engineer.md`
> (en, waar relevant, `analyst.md`): wanneer gebruik je een synchrone `Task`/`Agent`-subagent
> binnen de sessie, en wanneer hoort iets een aparte kanban-kind-kaart te zijn. Verwijs naar
> dit beslisdocument als bron van waarheid.
>
> **Acceptance criteria:**
> - `engineer.md` bevat een korte, expliciete "subagent vs. kind-kaart"-richtlijn met de
>   vier synchroon-condities en de vijf async-triggers uit §3 (of een beknopte parafrase),
>   niet alleen de huidige één-regel-vermelding.
> - De grens "een executor decomponeert niet zelf async door" (§3, grensgeval) staat
>   expliciet in de persona.
> - Een verwijzing naar `docs/cockpit/sync-vs-async-delegation-decision.md` als bron.
> - Geen code-/schema-wijziging; puur persona-/doc-tekst.

De reeds-bestaande FCR-vervolgkaart uit
[`reviewer-agent-decision.md`](./reviewer-agent-decision.md) is de concrete *toepassing* van
het synchrone patroon en staat los van bovenstaande sturing-kaart — die wordt hier bewust
niet gedupliceerd.

## 8. Wanneer heroverwegen

- **Als synchrone fan-out in de praktijk breed en herhaald wordt** en agents er structureel
  mee worstelen (verkeerde model-keuze, oncontroleerbare kosten), kan een lichte
  in-sessie-conventie of budget-guardrail alsnog een aparte kaart worden — dan met
  empirisch bewijs, niet speculatief.
- **Als een executor stelselmatig kind-kaarten té groot krijgt** en `report_impediment`
  te grof blijkt, heropen dan de vraag naar geneste decompositie — maar dat is een
  wijziging aan de *async*-laag (bijv. een executor die een sub-analyst-kaart aanmaakt),
  niet aan het synchrone model.
