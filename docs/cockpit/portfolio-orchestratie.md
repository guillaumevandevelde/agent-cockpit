# Portfolio-orchestratie: meerdere product-apps beheren naast het meta-platform

> Kanban-kaart: **`[analyse] Portfolio-orchestratie: meerdere product-apps
> beheren naast het meta-platform`** (facet C van de parent-kaart *"Deze
> applicatie als platform om andere applicaties te bouwen"*,
> `8db831a0df6d42689c5b26325b6cbecc`).
>
> Deze doc is een **analyse** — geen implementatie. De actionabele gaten
> worden hieronder expliciet gemaakt en in §7 als concrete
> **Backlog-follow-ups** gefileerd (door de uitvoerende sessie van deze
> kaart, niet door dit document zelf).

## 1. De vraag in één paragraaf

Kan Cockpit N zelfgebouwde applicaties als een **portfolio** opvolgen
naast zichzelf — waarbij het *meta-platform* (claude-cockpit zelf, zijn
eigen backlog, eigen self-improve-cyclus) en de *product-projecten* (de
applicaties die door inceptie+bootstrap geboren worden) expliciet
gescheiden maar toch samen-beheerd worden, met een portfolio-breed
overzicht en een vorm van cross-project dispatch-governance die voorkomt
dat één product-project het hele memory-budget opslokt terwijl een
ander stilletjes stilstaat?

Het korte antwoord (verder onderbouwd): de **multi-project-
infrastructuur** is vandaag al sterk — kanban + autodispatch draaien
*per `project_key`* (afgeleid van de git-remote), de board per project
is volledig onafhankelijk, en de enige gedeelde resources zijn
bestandssysteem + memory-budget. Maar er is **geen expliciet onderscheid
tussen meta-project en product-projecten**, **geen portfolio-breed
overzicht of dashboard**, en **geen cross-project dispatch-governance**
— de cap is 1 kaart per project, maar niets stuurt hoe die ene kaart
over N projecten verdeeld wordt, niets voorkomt dat 5 product-projecten
tegelijk het memory-budget vullen terwijl een 6e wacht, en niets
waarschuwt de gebruiker als een product-project dagenlang onbewogen
achterblijft omdat de cap permanent door een ander wordt opgeëist.

## 2. Wat kan vandaag al — en wat níet

Alles in deze sectie is geverifieerd in de repo op een werkende branch
(`k-analyse-portf-be4f`), niet uit het geheugen.

### 2.1 Wat er al staat (de uitvoerings-/beheerlaag)

| Bouwsteen | Waar | Wat het wel/niet doet |
|---|---|---|
| **Project-key-resolutie** | `backend/app/kanban/project_key.py:38-45` | `git remote get-url origin` → `git:<host>/<path>`; fallback `slug:<basename>`. **Device-onafhankelijk** (dezelfde key op alle machines die dezelfde remote zien). |
| **Project-registratie** | `backend/app/services/project_service.py:45-89` `add_project` | `INSERT INTO projects (name, path)` — al-bestaande paden worden geregistreerd, geen directory-creatie. |
| **Per-project kanban-board** | `KanbanCard.project_key` (`backend/app/kanban/models.py:37`, indexed) | Volledige isolatie: opsomming, opvolging, deliverables, activity-feed zijn *per project*. Geen cross-project joins in de hot paths. |
| **Auto-dispatch, per-project, per-device opt-in** | `backend/app/kanban/dispatch.py:139-148`, `:185-192` (`autodispatch:<project_key>`) | `KanbanMeta`-toggle; elk device beslist zelf of het voor die key spawnt. **Bewust niet in de synced op-log**: dispatch is een device-lokale activiteit. |
| **Per-project concurrency cap** | `backend/app/kanban/dispatch.py:1305-1327`, `dispatch_project` cap-check | 1 kaart per project tegelijk; per-kolom `max_sessions`-override (`backend/app/kanban/models.py:137-139`). |
| **Hardware-aware globaal session-budget** | `backend/app/services/memory_monitor.py:99-150` + `SessionRegistry.effective_max_sessions` | Hard ceiling op aantal *gelijktijdige* sessies — geen per-project of per-rol weging; verlaagt zichzelf als memory onder druk komt. |
| **Cross-project session-recovery budget** | `backend/app/kanban/session_recovery.py:111-121` | Bij startup worden resumable cards teruggezet, **begrensd door het globale session-budget** (niet door per-project caps). Voorkomt dat één project met veel dead claims het hele budget opslokt. |
| **Agent Mail** (cross-session, niet cross-project) | `docs/cockpit/agent-mail-spec.md` | Durabele mailbox tussen willekeurige sessies. Wel: cross-project wanneer beide projecten dezelfde Agent Mail installatie delen. Niet: portfolio-view, niet: dispatch-governance. |
| **`/api/v1/projects` (lijst)** | `backend/app/api/v1/projects.py:25-30` | Vlakke `GET /projects` zonder groepering, filter, of meta-tag. |

### 2.2 Wat er structureel ontbreekt (de portfolio-/governance-laag)

Een gerichte greppel door `backend/app/` + `docs/cockpit/` levert **nul**
implementaties of ontwerpdocumenten die expliciet over *portfolio*,
*meta-vs-product*, of *cross-project dispatch-governance* gaan:

| Gezocht | Gevonden |
|---|---|
| `portfolio` in backend of docs | **0 hits** (het woord komt alleen voor in de titels van facet-A/B en deze facet zelf). |
| `is_meta` / `kind` / `role` op `projects`-rij | **0 hits.** `Project` (`backend/app/models/database.py:16-33`) heeft alleen `id/name/path/is_active/last_accessed/created_at/updated_at` — geen onderscheid tussen meta- en product-projecten. |
| `global_max_sessions` / `portfolio_cap` / `priority` over projecten | **0 hits** in dispatch of service-laag. De cap is altijd per-project of per-kolom, niet over projecten heen. |
| `cross-project` aggregatie-endpoint | **0 hits.** Geen `GET /projects/portfolio/stats`, geen `GET /dashboard/overview`. |
| `presence` per project / cross-project presence | Alleen per-pane/session presence (`presence_service.py`), niet per-project-aggregaat. |
| Tags / labels / kind op project-rij | **0 hits.** Projecten zijn naamloze, ongetagde rijen. |

De structuur is dus: **technisch sterk per-project, maar portfolio-besturing
moet *elders* worden uitgevonden**.

### 2.3 Het probleem in concreet gedrag

Wat er in de praktijk misgaat zodra er ≥ 2 product-projecten naast het
meta-project bestaan:

1. **Geen onderscheid meta ↔ product.** Een kaart op het
   claude-cockpit-project ("`[self-improve] dispatch-pause edge case`")
   en een kaart op een toekomstig product-project
   ("`webapp-X: fix login button`") delen dezelfde dispatch-bus, dezelfde
   `KanbanMeta`-namespace, dezelfde cap-mechaniek. De gebruiker moet zelf
   onthouden welk project welk doel dient.

2. **Geen portfolio-cap.** Als 5 product-projecten tegelijk
   autodispatch-enabled zijn en elk een card in Todo heeft, vult elk
   *onafhankelijk* zijn cap (1). Vijf sessies tegelijk — tot het
   memory-budget eruit klapt. Het memory-budget is de enige rem, en die
   is *binair* (pas als `is_warning`/`is_critical` wordt de limiet
   verlaagd, `memory_monitor.py:119-147`), niet stuurbaar.

3. **Geen prioriteit/fairness over projecten.** Stel: project A heeft 50
   Backlog-kaarten, project B heeft 1 kritieke `Impediment`-
   resolve-kaart. Project A's tick pakt kaart 1; project B's tick pakt
   ook kaart 1 — als die in dezelfde 10-seconden-cyclus vallen, kan
   project B wachten tot A klaar is. Er is geen "geef kritieke kaarten
   voorrang, ongeacht project"-logica.

4. **Geen "wie loopt achter"-zicht.** Een product-project kan dagenlang
   onbewogen in de Backlog staan — niet door een bug, maar gewoon omdat
   een ander project de dispatch-aandacht opeist. De gebruiker ziet dit
   pas door elke project-pagina afzonderlijk te openen.

5. **Geen portfolio-breed "wat is de status"-endpoint.** Voor een
   zelfgebouwd cockpit-dashboard ("portfolio-board" met één rij per
   product-project, met kolommen Backlog/Todo/Doing/Done/Impediment
   counts + laatste activiteit) moet je vandaag N aparte `GET /cards`
   calls doen en zelf aggregeren. Een frontend-component om dat te
   renderen ontbreekt eveneens.

6. **Self-improve-cyclus is onzichtbaar voor product-projecten.**
   `session-retro` (`docs/cockpit/session-retro/SKILL.md`) levert
   `[self-improve]`-kaarten in Backlog van het project waar de sessie
   draaide. Voor meta-werk (claude-cockpit) hoort die kaart ook op
   claude-cockpit. Voor product-werk hoort hij óók dáár — maar het
   onderscheid is niet eens zichtbaar in het card-row-model; een
   self-improve-card van een product-project *is* gewoon een kaart op
   dat product-project, en het is aan de gebruiker om te beslissen
   of-ie doorschuift.

7. **Geen "work_type='portfolio_admin'" of vergelijkbare routing.**
   `work_type` (`backend/app/kanban/models.py:47`) is een routing-hint
   voor *persona* (engineer / analyst / …), niet voor project-categorie.
   Een portfolio-kaart ("`portfolio: decide which product gets the
   memory budget today`") zou vandaag gewoon naar een engineer-sessie
   dispatchen — geen portfolio-bewustzijn.

## 3. MECE- en overlap-positie t.o.v. de andere facetten

| Facet | Wat het *wel* doet | Wat het *niet* doet (waar C begint) |
|---|---|---|
| **A. Product-inceptie** | Vrij idee → spec + plan → kanban-artefact. | Creëert geen onderscheid tussen projecten; portfolio-cap wordt expliciet als ontwerp-overweging genoemd (§4 optie 2). |
| **B. Repo-provisioning & bootstrap** | Repo + scaffold + `.claude/`-seed + autodispatch-toggle voor één nieuw project. | Zegt niets over hoe N gebouwde projecten samengelegd of bestuurd worden; levert de **per-project primitives** waar C op leunt. |
| **C. Portfolio-orchestratie (deze)** | Onderscheid meta↔product, portfolio-view, cross-project dispatch-governance. | Zegt niets over hoe een project wordt *geboren* (A) of *aangemaakt* (B). |
| **D. Veilig bouwen & uitleveren** | Isolatie, secrets, CI, run/preview/deploy. | Bezit sandbox/CI; C leunt op D voor de vraag "mag project X überhaupt een sessie spawnen met deze secrets". |

**Drie overlap-punten zijn bewust bij één facet gelegd:**

- **`gh repo create` en key-migratie** → B (repo-flow), D (GitHub-auth).
- **Portfolio-aware naamgeving** (`slug:my-app` → cross-device
  uniekheid) → C (de definitie van "uniek"), B (de bootstrap die de
  namespace toepast).
- **Dispatch-pause** (rate-limit-stop) → dispatch.py (blijft zoals het
  is); C voegt hooguit een *per-project* pauze toe voor geplande
  onderhoudsmomenten.

## 4. Drie ontwerp-opties voor portfolio-governance

De drie varianten zijn **uitwisselbaar** in de basis-observability
(portfolio-view) en **oplopend** in de mate van globale sturing. De
synthese-facet beslist welke te kiezen.

### Optie 1 — "Read-only portfolio-view" (minimaal)

**Idee.** Een nieuw read-only REST-endpoint `GET /api/v1/portfolio/overview`
(of `GET /api/v1/projects/portfolio`) levert een aggregaat:

```jsonc
{
  "projects": [
    {
      "id": 1, "name": "claude-cockpit", "path": "...",
      "kind": "meta",                    // ← nieuw
      "autodispatch_enabled": true,
      "totals": {"backlog": 12, "todo": 3, "doing": 1,
                 "impediment": 0, "done_24h": 5},
      "last_activity": "2026-07-11T14:30:00Z",
      "last_dispatch": "2026-07-11T14:25:00Z"
    },
    ...
  ],
  "totals": { ...som over alle projecten... }
}
```

Plus een frontend-pagina `PortfolioPage.tsx` met één rij per project +
aggregaat-kop. Geen schrijfacties, geen dispatch-governance. **Slechts
één nieuw column'tje** (`projects.kind`) nodig, plus een nieuwe service
`PortfolioService.aggregate()` die bestaande queries aanroept.

**Trade-offs.**

| + | − |
|---|---|
| Minimale verbouwing; tast het dispatch-mechaniek niet aan. | Zonder governance zie je het probleem (#1–#7 in §2.3) maar kun je er niets aan doen. |
| Volledig additief; geen breaking changes. | Geen onderscheid in *hoe* meta- en product-kaarten behandeld worden — alleen in *hoe ze gepresenteerd* worden. |
| Bruikbaar als eerste observability-laag, ook voor facet D (security review). | Geen portfolio-cap, geen fairness, geen cross-project prioriteiten. |

### Optie 2 — "Kind-tag + portfolio-cap" (aanbevolen)

**Idee.** Bovenop optie 1 krijgt de `projects`-rij een **kind-tag**
(`meta` | `product` | `archived`, default `product`), en de
dispatch-loop krijgt een **portfolio-cap**:

```
portfolio_cap = min(memory_budget, 4)     // of een config-knob
active = sum(<agent-claimed cards across all projects>)
if active >= portfolio_cap: skip deze tick
else: verdeel rest volgens policy (zie §5)
```

Het `kind`-veld krijgt ook een security-dimensie mee (zie facet D):
alleen `meta`-projecten mogen het *claude-cockpit*-platform zelf
wijzigen; `product`-projecten raken alleen hun eigen subtree. Dit is
**geen** security-implementatie (dat is D), alleen een tag die D en de
gebruiker later kunnen gebruiken voor policies.

**Trade-offs.**

| + | − |
|---|---|
| Lost het echte probleem op: één product-project kan niet meer het hele budget opslokken. | Nieuwe kolom → migratie-load (zelfde patroon als `KanbanColumn.max_sessions`, kan via losse ALTER TABLE). |
| Kind-tag is de missing link waar A (intake), B (bootstrap), D (security), en deze facet op leunen. | Portfolio-cap-waarde is een beleidskeuze — wie kiest 'm? cockpit-default of per-fork-configurabel? |
| Maakt expliciet wat vandaag impliciet is: "claude-cockpit-werk ≠ app-werk". | Frontend `PortfolioPage` moet `kind`-kolom tonen + filter op product-only / meta-only. |

### Optie 3 — "Prioriteits-gewogen fair scheduling" (volledig)

**Idee.** Bovenop optie 2 krijgt de dispatch-loop een **gewogen
round-robin** of **lottery scheduling** over projecten heen: projecten
krijgen een gewicht (priority / urgentie / handmatig ingesteld), de
tick verdeelt het resterende portfolio-budget naar die gewichten.

```
for project in enabled_projects (shuffled by priority):
    if remaining_budget <= 0: break
    dispatch_project(...)
    remaining_budget -= 1
```

Plus **stale-project detection**: een project dat >24 u geen
autodispatch-run heeft gehad terwijl er Backlog-kaarten zijn, krijgt
een "stale" markering op de portfolio-view + (optioneel) een automatisch
`Impediment`-kaartje.

**Trade-offs.**

| + | − |
|---|---|
| Echte cross-project fairness; geen "wie-het-eerst-vraagt-wint". | Complexiteit: scheduler-rewrite, edge cases (1 project = oneindige share? hoe dealen met kind-kaarten?), testoppervlak groeit. |
| Stale-project-detectie geeft gebruiker actionable signaal. | "Priority" over projecten is een nieuwe beleids-as — moet ergens zitten (UI-toggle? per-fork-config?). |
| Lost het "product-project dagen onbewogen"-probleem op. | Kan ongewenste *starvation* veroorzaken als een product-project structureel lage prioriteit krijgt. |

**Vergelijking op de kern-assen:**

| As | Optie 1 (read-only) | Optie 2 (kind + cap) | Optie 3 (fair scheduling) |
|---|---|---|---|
| Lost observability-probleem op? | Ja (volledig) | Ja | Ja |
| Lost cap-conflict op? | Nee | Ja (hard ceiling) | Ja (soft ceiling met weights) |
| Lost fairness-issue op? | Nee | Nee | Ja |
| Lost stale-project-detectie op? | Nee | Nee (kan add-on zijn) | Ja |
| Code-impact (regels) | ~150–250 | ~400–600 | ~800–1200 |
| Schema-impact | 0 (in-memory tag) | 1 kolom (`projects.kind`) | 1–2 kolommen (`kind`, `priority`) |
| Frontend-impact | 1 nieuwe pagina | 1 nieuwe pagina + filter | 1 nieuwe pagina + priority-UI |

**Aanbeveling van deze facet-analyse** (niet van de synthese): optie 2.
Het is het minimale dat de *harde* problemen oplost (cap-conflict +
meta-vs-product-onderscheid) zonder de *zachte* fairness-optimalisatie
mee te nemen — die is waardevol maar kan later als evolutie bovenop 2
worden gebouwd. Optie 1 is een prima interim; optie 3 is een derde
iteratie.

## 5. Drie ontwerp-keuzes voor de portfolio-cap (sub-keuzes bij optie 2/3)

Bij optie 2 (en in mindere mate 3) moeten deze beleids-keuzes ergens
leven:

1. **Waarde.** Vast (bv. `min(memory_budget, 4)`)? Configureerbaar in
   `config.py`? Per-device? Per-fork (configurabel via env)? De
   memory-aware variant is het minst verraderlijk omdat de limiet
   *automatisch* daalt als memory onder druk komt. Een
   configureerbare-override is handig voor power-users.
2. **Scope.** Geldt de cap alleen over autodispatch-enabled projecten,
   of ook over handmatig gestarte sessies? Voorstel: alleen
   autodispatch — handmatige sessies (UI "Start session") zijn
   mens-bewust en omzeilen het portfolio-mechaniek.
3. **Failure-mode.** Als de cap bereikt is: skip de hele tick (huidig
   gedrag), of wacht tot een sessie vrijkomt en dispatch dan alsnog?
   Skip-tick is het minst complex; wait-and-retry is eerlijker maar
   vereist een wachtrij-mechanisme.

## 6. Wat er al aan portfolio-bewustzijn bestaat — en wat ontbreekt

| Vandaag aanwezig | Vandaag afwezig |
|---|---|
| Per-project kanban-isolatie (model + opslag). | Project-`kind` (meta / product / archived). |
| Per-device autodispatch-toggle. | Portfolio-autodispatch-toggle ("alle product-projecten aan/uit"). |
| Per-project concurrency-cap. | Portfolio-cap. |
| Per-project session-recovery-budget (al globaal begrensd). | Stale-project-detectie. |
| Hardware-aware memory-budget. | Policy-gewogen prioriteits-overschrijving van het budget. |
| Per-project activity feed. | Portfolio-feed ("laatste activiteit in welk project"). |
| Agent Mail cross-session (en dus cross-project wanneer gedeelde installatie). | Portfolio-brede notificatie ("drie projecten hebben impediments"). |
| `ProjectContext` (frontend) voor actief project. | `PortfolioContext` voor portfolio-view. |

## 7. Actionabele gaten → Backlog-follow-ups

Deze sectie lijst de gaten die het bouwwerk vormen. **Niet** door dit
document geïmplementeerd — door de uitvoerende sessie van deze kaart
worden ze als concrete Backlog-kaarten aangemaakt (met `work_type` +
`metadata.facet="C"` + korte acceptatiecriteria) zodra dit document is
gemer ged. Volgorde = oplopende impact, niet implementatie-volgorde
(laatste is aan de menselijke triage).

1. **`[feature][portfolio] Project-`kind`-tag (meta/product/archived) +
   `priority`-kolom`** — schema-uitbreiding op de `projects`-tabel
   (`ALTER TABLE`, geen Alembic — patroon volgt `KanbanColumn.max_sessions`
   in `backend/app/kanban/db.py:175-176`). Default `product`. API +
   Pydantic-schema + frontend-toggle. Geen gedragsverandering — alleen
   de tag is beschikbaar voor downstream policies. ~halve dag.

2. **`[feature][portfolio] PortfolioService + GET /portfolio/overview`** —
   nieuwe read-only service die N projecten × hun kanban-stats
   aggregeert in één antwoord (cf. §4 optie 1). Herbruikt
   `service.list_cards` + `kstats.compute_core_stats`. Geen
   schrijfacties. Test met fixture-projecten. Frontend-component
   `PortfolioPage.tsx` met één rij per project. ~1–2 dagen.

3. **`[feature][portfolio] Portfolio-cap in `run_dispatch_tick`** —
   vervangt/aanvulling op de per-project cap-check: vóór de
   `for project_key, project_path in mapping.items():`-loop wordt het
   totaal aantal `agent:`-claims over *alle* projecten geteld;
   als ≥ portfolio_cap, skip de tick. Cap-waarde uit
   `config.py` (default `min(session_registry.effective_max_sessions,
   4)`). Log een audit-regel. Achter een feature-flag
   (`config.portfolio_cap_enabled`) zodat de rollout gefaseerd kan.
   ~1 dag.

4. **`[design][portfolio] Policy-keuzes voor portfolio-cap (waarde,
   scope, failure-mode)`** — work_type=analysis. Leidt tot
   `docs/cockpit/portfolio-policy.md` met de gekozen waarden +
   rationale + alternatieven. Voedt #3 en optioneel #6. Input van
   synthese nodig (of direct menselijke beslissing).

5. **`[feature][portfolio] Stale-project-detectie** — scheduler-taak die
   elke N minuten draait: voor elk autodispatch-enabled project met
   Backlog-kaarten > X uur geen Done-move, post een
   `[portfolio-stale]`-comment op de oudste Backlog-kaart van dat
   project (geen Impediment-move: dat zou dispatch blokkeren — het is
   een signaal, geen blokkade). Threshold + comment-template in
   config. ~halve dag.

6. **`[feature][portfolio] Frontend `PortfolioPage` met kind-filter +
   prioriteits-toggle (alleen bij optie 3)`** — read-only bovenaan;
   onderaan per project: Backlog/Todo/Doing/Done-counts + laatste
   activiteit. Filter `meta-only / product-only / all`. Bij optie 3:
   priority-slider per project die een `priority`-PATCH naar de
   backend stuurt. Afhankelijk van #2; conditioneel op synthese. ~1
   dag.

7. **`[design][portfolio] Meta-vs-product security-grens** — *deze*
   facet legt de tag; **facet D** (veiligheid) ontwerpt wat de tag
   *betekent* voor security-policies. Work_type=analysis op facet D;
   deze facet hoeft niets te doen behalve de tag beschikbaar maken
   (#1) zodat D 'm kan gebruiken.

8. **`[design][portfolio] Hoe migreren we bestaande projecten bij de
   kind-introductie?** — work_type=analysis. Vraag: wordt elk
   bestaand project automatisch `product`, of krijgt het project dat
   over de cockpit-repo zelf gaat een heuristiek ("match project_key
   met de huidige cockpit-`git remote get-url` → `meta`")? Welke
   bestaande kaarten op `meta`-projecten verdienen een audit?

## 8. Niet in deze facet (expliciete out-of-scope)

- **Inceptie/intake-flow, intake-kolom, MCP-actie
  `kanban.create_project_from_intake`** → facet A (al gefileerd in
  Backlog, `0260dbcd`).
- **Repo-creatie, scaffold-templates, gh-auth** → facet B (al
  gefileerd).
- **`BlueprintService` data-model + REST + UI + typologie** → facet A.
- **Per-project security-policies (secrets, sandbox, write-anywhere)** →
  facet D.
- **CI, run/preview/deploy per product-project** → facet D.
- **Cross-device sync** (momenteel bevroren per
  `sync-hlc-freeze-vs-prune.md`) — portfolio-breed werk vereist sync
  *niet* zolang alles op één device draait; komt later terug zodra
  sync herleeft.

## 9. Relatie met de andere facetten

(MECE + overlapkaart.)

- **Facet A** (intake): zegt "een nieuwe product-project wordt geboren";
  deze facet zegt "en dan? hoe zit 'ie in het portfolio?". De
  `intake`-kolom uit A (optie 2) leeft op het meta-project; dat is
  *precies* het meta-vs-product-onderscheid dat C formaliseert.
- **Facet B** (bootstrap): levert de per-project primitives
  (`set_autodispatch`, key-migratie, project-registratie); deze facet
  zegt "en nu het portfolio-view + portfolio-cap erbovenop". B's
  key-migratie-helper is nodig zodra C een project hernoemt (bv. bij
  priority-flip — nee, priority verandert geen key; alleen relevant als
  portfolio-context ooit project-keys gaat *normaliseren*).
- **Facet D** (veiligheid): consumeert de `kind`-tag uit deze facet om
  security-policies te schrijven (meta mag het platform wijzigen,
  product niet). D is eigenaar van de policy; C is eigenaar van de tag.

## 10. Kernbevinding (voor de ouder-comment)

> Cockpit is vandaag **technisch sterk per-project** (kanban + dispatch
> + autodispatch + worktree-isolatie draaien onafhankelijk per
> `project_key`), maar de **portfolio-laag ontbreekt** volledig: geen
> `kind`-tag op projecten (dus geen expliciet meta-vs-product-
> onderscheid), geen portfolio-breed overzicht of dashboard, geen
> cross-project dispatch-cap (één product-project kan het hele
> memory-budget vreten terwijl een ander wacht), geen prioriteit/
> fairness over projecten heen, geen stale-project-detectie. Drie
> architectuurvarianten (read-only-view, kind+cap, fair-scheduling) zijn
> uitgewerkt met oplopende impact; de aanbeveling is variant 2
> (kind-tag + portfolio-cap) als kleinste zinvolle stap, met variant 1
> als interim en variant 3 als latere evolutie. Acht actionabele gaten
> zijn als Backlog-follow-ups benoemd; geen daarvan wordt door deze
> kaart geïmplementeerd.
