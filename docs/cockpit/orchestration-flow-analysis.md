---
title: "Orchestration-flow — is onze flow robuust genoeg? — analyse"
type: analysis
status: active
---

# Orchestration-flow — is onze flow robuust genoeg? — analyse

> Kanban-kaart: **"Analyse - orchestration flow"**
> Vraag: *"Look at our flow, is it good enough, is following more robust?"* met een
> voorgestelde flow, een per-agent-job-model, "structured state between steps, not full
> chat history", en een lijst van wat een goede orchestrator moet tonen.
>
> Dit is een analyse-kaart (leaf design-deliverable). DoD = dit beslisdocument met een
> eerlijke nulmeting + concrete vervolgkaarten voor de échte gaten. Geen feature-code in
> deze kaart.

## 1. Wat wordt er voorgesteld?

De kaart stelt drie dingen tegelijk voor en vraagt of onze flow ze haalt:

1. **Een lineaire flow met per-agent-jobs:**
   `spec → clarify → plan → implement small diff → test → review → accept/reject → completed`,
   waarbij "each agent has a job" — analyst draft plan, engineer implementeert, tester
   reviewt tegen de spec, deterministische tools draaien tests, mens keurt grote
   wijzigingen goed. **Niet** "everyone reads everything".
2. **Structured state tussen stappen, niet volledige chat-history.** De overdracht
   tussen stappen is een gestructureerde toestand, geen doorgegeven transcript.
3. **Een observeerbare orchestrator** die per stap toont: welke taak elk model kreeg,
   welke context het ontving, welke files wijzigden, welke tests draaiden, wat faalde,
   wat geaccepteerd werd, en welk model elke stap deed.

De centrale claim om te toetsen: is dit **robuuster** dan wat we nu hebben?

## 2. Geverifieerde nulmeting — de voorgestelde flow ligt al grotendeels in de repo

Elke rij hieronder is geverifieerd in code/docs, niet uit geheugen. De verrassing van de
nulmeting: **onze flow ís al grotendeels de voorgestelde flow.** De per-agent-job-scheiding,
de gestructureerde-state-overdracht en het merendeel van de orchestrator-observability
bestaan al — verspreid over kanban, personas, deliverables en CI.

| Voorgestelde stap | Wat er vandaag is | Bron | Robuust? |
|---|---|---|---|
| **spec** | `intake-authoring`-skill: `brainstorming` + `writing-plans` → intake-kaart met `spec`- + `plan`-deliverables. | `intake-authoring-flow-decision.md`, `.claude/skills/intake-authoring/` | ✅ voor mens-geïnitieerd werk; autonome kaarten slaan 'm over (zie §4) |
| **clarify** | Alleen reactief: `report_impediment(question, options)` → kaart naar `Impediment`, claim vrij, mens beslist async. Geen proactieve clarify-loop in autonome dispatch. | `kanban-dispatch-spec.md` §"Reporting a human-decision impediment" | ⚠️ bewuste trade-off, geen defect (zie §4) |
| **plan** | `analyst`-persona splitst parent in kind-kaarten + `add_plan_attachment` (plan-markdown + `depends_on`-DAG). | `multi-agent-kanban.md`, `.claude/agents/analyst.md` | ✅ |
| **implement small diff** | `engineer`-persona voert één kaart end-to-end uit in een geïsoleerde git-worktree; één kaart = één branch = één scoped diff. | `kanban-dispatch-spec.md` §"Scope = git worktree", `.claude/agents/engineer.md` | ✅ ("small diff" = per-kaart-scope) |
| **test** | `iteration-loop` preset `verify` (frontend `lint && build`) als verplichte end-of-card gate + CI (`ruff` + `pytest` + frontend) als backstop-na-push. Deterministische tools, geen model. | `engineer.md` §6, `.github/workflows/quality.yml` | ✅ |
| **review** | `/code-review` (code-**quality** op de diff, in-sessie) + CI. **De spec-compliance-review (FCR) is beslist maar niet gebouwd.** | `reviewer-agent-decision.md`, `engineer.md` §6 | ❌ **echt gat — §3.1** |
| **accept/reject** | `ship_mode` `direct` vs `pull-request` (mens-gate); analyse-kaarten: uitkomst-poort (`decomposed`/`not_feasible`/`no_action_needed`); post-Done twijfel: `request_review` → nieuwe analyse-kaart. | `analysis-outcome-contract-decision.md`, `reviewer-agent-decision.md` §"Lichtere alternatieven" | ✅ |
| **completed** | `Done` + `Awaiting Subtasks`-parkeerkolom voor parents + afgeleide `completed`-status. | `analyse-levenscyclus-decision.md` | ✅ |

### 2.1 "Structured state, not chat history" — dit is al een sterkte, geen gat

De overdracht tussen stappen leunt vandaag **niet** op doorgegeven transcript. Elke stap
schrijft durabele, gestructureerde toestand die de volgende stap (mogelijk een verse
sessie op een ander model) leest:

- **Kaart-velden** — `title`/`description`/`metadata` (incl. `metadata["spec_doc"]` als
  machine-leesbare kaart→spec-link, `SPEC_DOC_META_KEY`).
- **Plan-attachments** — plan-markdown + `depends_on`-DAG (`add_plan_attachment`).
- **Deliverables** — `pr`/`branch`/`commit`/`spec`/`plan`/`plan_ref`/`link`/`note` als
  portable refs.
- **Activity-feed met prefix-contract** — `**Summary:**` / `**Impediment:**` /
  `**Resolution:**` / `**Outcome:**` (`kanban-conventions.md`).
- **Agent Mail** — gestructureerde cross-session-berichten met durabele repo-identiteit.
- **ACP-isomorf event-model** — `structured_events.py` (getypeerde `tool_call` /
  `usage_result` / `plan_update` / … events) als datavloer voor een headless transport.

Dit is precies het "sessies zijn efemeer, de gestructureerde toestand niet"-principe uit
[`spec-driven-development-analysis.md`](./spec-driven-development-analysis.md) §3: een verse
executor herwint context uit het bord, niet uit een oud transcript. **In deze dimensie is
onze flow al robuuster dan de lineaire schets** — de schets noemt structured-state als
doel; wij hebben er zes lagen van.

## 3. De twee échte gaten

### 3.1 Gat 1 — de "review against the spec"-stap bestaat niet (beslist, niet gebouwd)

Dit is het scherpste, meest concrete gat, en het correspondeert exact met de
`review`-stap uit de voorgestelde flow ("one agent reviews against the spec").

**Wat er is:** de engineer draait `/code-review` (code-quality op de diff) +
`iteration-loop verify` + CI. Alle drie kijken naar of de **code goed/werkend** is.

**Wat er ontbreekt:** niets valideert met *cleared context* en de kaart-spec als anker of
de implementatie **de gevraagde feature** is. Dat is een andere vraag dan "is de code
goed?" — en het is precies waar
[`reviewer-agent-decision.md`](./reviewer-agent-decision.md) (2026-07-10, status *herzien*)
een uitkomst op heeft: **"Wél bouwen, in lichtere vorm"** — een feature-compliance-review
(FCR) als subagent-call binnen de engineer-sessie, vlak vóór `move_card Done`. Geen aparte
persona, geen kolom, geen concurrency-impact.

**Verificatie dat het niet gebouwd is:** `grep -rniE 'feature.compliance|FCR'` over
`.claude/` en `backend/app/kanban/dispatch.py` levert **niets** op; `engineer.md` §6
(regel 95-110) bevat alleen `iteration-loop verify` / `/code-review` / `simplify` /
`investigate` — geen FCR-stap. De beslissing is dus een **verweesde beslissing**: het doc
+ `kanban-followups.md` §153 beschrijven 'm, maar er staat geen live kaart op het bord en
geen implementatie in de flow.

Dit is de grootste robuustheidswinst die de kaart vraagt, en hij is al ontworpen. →
**vervolgkaart, zie §5.**

### 3.2 Gat 2 — geen enkel scherm dat de run per stap toont

De derde eis van de kaart ("a good orchestrator should show…") is vandaag **verspreid**
over meerdere oppervlakken en op geen enkel punt gestitcht tot één per-run-beeld:

| Wat de kaart wil zien | Waar het vandaag leeft | Gestitcht? |
|---|---|---|
| welke taak elk model kreeg | kanban-kaart (`title`/`description`) | — |
| welke context het ontving | `dispatch.build_card_prompt` (de spawn-prompt) | ❌ niet in UI zichtbaar |
| welke files wijzigden | `branch`/`pr`-deliverable (via git, niet in-app diff) | ❌ extern |
| welke tests draaiden / wat faalde | CI (GitHub Actions) + `.claude/state/iteration-<card>.txt` | ❌ extern |
| wat geaccepteerd werd | `Done` + `**Outcome:**`-comment | deels |
| welk model elke stap deed | `card.model`/`column.default_model`-override | deels |

De **datavloer** bestaat (`structured_events.py` levert getypeerde `tool_call` /
`usage_result` / `error`-events per run), maar er is **geen consumerende timeline-view**
die per kaart de keten prompt → files → tests → outcome → model aan elkaar rijgt. CC
Bridge/Sessions tonen *live* sessies; de kanban-activity-feed toont *samenvattings*comments;
APM is een module-installer, geen run-ledger. Een verse mens die "wat is er met kaart X
gebeurd en waarom?" vraagt, moet vandaag vier plekken bezoeken.

Of dit een nieuw scherm verdient dan wel een uitbreiding van de bestaande `CardDrawer`, en
welke databronnen precies gestitcht worden (consumeert 't `structured_events`, of aggregeert
't board + CI + deliverable?), is scope-werk. → **vervolgkaart met `work_type="analysis"`,
zie §5.**

## 4. Eén bewuste trade-off — géén gat, wél expliciet maken

De **clarify**-stap uit de voorgestelde flow is in autonome dispatch **reactief**, niet
proactief: een engineer/analyst die vastloopt op iets dat alleen een mens kan beslissen
gebruikt `report_impediment(options=[…])` — de kaart gaat naar `Impediment`, de claim komt
vrij, de sessie eindigt, en een mens beslist async (`kanban-dispatch-spec.md`).

Dit is **opzettelijk** en al meermaals bekrachtigd (memory `feedback_blocked_decision_impediment_with_options`,
`sync-vs-async-delegation-decision.md`): een gedispatchte sessie mag **nooit blokkeren** op
een open gate — de oude blokkerende `open_gate`-poll was de oorzaak van "wedged session →
worktree reaped"-verliezen (kanban-kaart 28b578ba). De voorgestelde synchrone "clarify"-stap
zou die faalmodus terugbrengen. **Onze reactieve variant is hier de robuustere keuze, niet
de zwakkere.** Dit is dus geen vervolgkaart — het is een plek waar de voorgestelde flow
*minder* robuust is dan wat we hebben, en dat is het waard om expliciet vast te leggen.

## 5. Aanbeveling & wat deze kaart oplevert

**Oordeel: de flow is goed genoeg en op de meeste assen al robuuster dan de lineaire
schets** — de per-agent-job-scheiding, de gestructureerde-state-overdracht en het
accept/complete-eind zijn geïmplementeerd en op beslisdocumenten verankerd. De voorgestelde
flow is geen upgrade; hij is grotendeels een beschrijving van wat er al staat, met twee
concrete uitzonderingen die het waard zijn om te dichten:

1. **Bouw de al-besliste FCR** (spec-compliance-review) — de ontbrekende `review`-stap.
2. **Scope een per-kaart run-ledger** — de ontbrekende orchestrator-observability.

Beide worden als Backlog-kaarten aangemaakt (zie hieronder). Ze zijn **onafhankelijk**
(geen `depends_on`): de FCR raakt de engineer-flow, de run-ledger raakt de observability-UI.

**Niet opnieuw openen** (al beslist, bewust afgewezen — voor de volledigheid):

- Aparte `reviewer`-persona + `Review`-kolom → afgewezen ten gunste van de lichtere
  in-sessie FCR (`reviewer-agent-decision.md`).
- Zwaar declaratief workflow-orchestration-paradigma / Agent Team Presets →
  afgewezen als concurrerend paradigma naast kanban-dispatch
  (`upstream-agent-teams-decision.md`, `spike-declarative-workflow-orchestration.md`).
- tmux + CLI vervangen door een SDK/headless-substraat → "incrementeel abstraheren, niet
  migreren" (`orchestration-substrate-decision.md`); het ACP-isomorfe event-model is de
  gekozen stap in die richting.

### Vervolgkaarten

- **`[review] Feature-compliance review (FCR) als pre-Done subagent-call bouwen`**
  (`feature`) — implementeer de al-besliste FCR uit `reviewer-agent-decision.md`
  §"Concrete vervolgkaart".
- **`[observability] Per-kaart run-ledger: stitch prompt → files → tests → outcome → model`**
  (`analysis`) — scope + ontwerp het ontbrekende orchestrator-scherm uit §3.2.
</content>
</invoke>
