# Product-inceptie: van gesprek naar spec + plan die een project seedt

> Kanban-kaart: **`[analyse] Product-inceptie: van gesprek naar spec +
> implementatieplan die een project seed`** (facet A van de parent-kaart
> *"Deze applicatie als platform om andere applicaties te bouwen"*,
> `8db831a0df6d42689c5b26325b6cbecc`).
>
> Deze doc is een **analyse** — geen implementatie. De actionabele gaten
> worden hieronder expliciet gemaakt en in §7 als concrete
> **Backlog-follow-ups** gefileerd (door de uitvoerende sessie van deze
> kaart, niet door dit document zelf).

## 1. De vraag in één paragraaf

Kan een gebruiker een **nieuwe applicatie** laten ontstaan door met Cockpit
te praten — dus: een conversationele intake eindigt in een **persistente
spec** én een **implementatieplan**, en van daaruit wordt automatisch een
**nieuw project** aangemaakt (repo + configuratie + project-specifieke
agents/skills), waarna het werk verder binnen dat nieuwe project wordt
opgepakt?

Het korte antwoord (verder onderbouwd): de **bouwstenen** voor twee van de
drie stappen bestaan al; de **inceptie** (vrij gesprek → spec-artefact) en
de **geboorte** (spec → nieuw project + repo) ontbreken. De pipeline is
vandaag een **U-vorm**: er is een idee, er is materiaal om dat idee te
bewerken, maar het komt niet als project terecht op het kanban-bord.

## 2. Wat kan vandaag al — en wat níet

Alles in deze sectie is geverifieerd in de repo op een werkende branch
(`k-analyse-produ-219f`, commit `19c0380`), niet uit het geheugen.

### 2.1 Wat er al staat

| Bouwsteen | Waar | Wat het wel/niet doet |
|---|---|---|
| **Plans-feature** | `backend/app/services/plan_service.py`, `frontend/src/features/plans/` | CRUD + zoeken op markdown in `~/.claude/plans/`. Browser + getPlanStats + getPlanSessions (koppelt plannen aan sessies via `slug`-veld in JSONL). Geen koppeling naar code, geen driftdetectie, geen "plan → kaart"-relatie — het is een bestandsbrowser met een mooie UI. |
| **`superpowers:brainstorming` skill** | `~/.claude/plugins/.../skills/brainstorming/SKILL.md` | Dialogue-flow die uitkomt op een user-approved design, geschreven naar `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`. Heeft een harde gate: *geen implementatie voor user-approval*. Output is **markdown op disk**, niet geregistreerd in Cockpit. |
| **`superpowers:writing-plans` skill** | `…/skills/writing-plans/SKILL.md` | Schrijft TDD-implementatieplan naar `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`. Idem: **markdown op disk**, niet geregistreerd. |
| **Multi-agent kanban (analyst → executors)** | `docs/cockpit/multi-agent-kanban.md`, `backend/app/kanban/dispatch.py` | Een parent-kaart wordt door de analyst-fase gesplitst in N kind-kaarten met een dependency-DAG + een plan-attachment. Dit is **de facto al een "spec → plan → uitvoering"-pijplijn** (zie `spec-driven-development-analysis.md` §2.3). Maar: hij draait binnen een **bestaand** project en begint bij een **bestaande** kaart — geen free-form intake. |
| **Per-project configuratiebeheer** | `backend/app/services/agent_service.py`, `commands.py`, `hooks.py`, `mcp.py`, `permissions.py`, … + `/api/v1/projects/{id}/config` | Alle `.claude/`-artefacten (agents, skills, MCP, commands, plugins, hooks, permissions, output styles, statusline) zijn per-project te beheren. CRUD bestaat. Wat ontbreekt: een **bootstrap** die deze artefacten voor een nieuw project invult. |
| **Kanban auto-dispatch** | `docs/cockpit/kanban-dispatch-spec.md` | Per-project opt-in (`KanbanMeta:autodispatch:<key>`). Cap = 1 kaart per project. Voor een **nieuw** project moet die toggle ook aan; daar is geen sjabloonpad voor. |
| **Sessie-spawning** | `spawn_session` (tmux), CC Bridge, Agent Mail | Cross-session coördinatie, durable mailbox. Kan in een nieuw project meteen ingezet worden — geen extra werk. |

### 2.2 Wat er structureel ontbreekt

Een geverifieerde greppel door de backend (geen `git init`, geen
`gh repo create`, geen scaffold/templating in `app/services/`), plus de
aanwezigheid van `ProjectService.add_project` die enkel een **bestaand pad**
registreert (`backend/app/services/project_service.py:45-89`), bevestigt:

1. **Geen repo-creatie of -scaffold.** Er is letterlijk geen code in het
   platform die een map initialiseert, een `.git` opzet, een template
   uitspreidt, of een bestaande GitHub-repo kloont. `add_project` is een
   `INSERT INTO projects (name, path)`. Kies je deze ontbrekende stap als
   losse feature: hij raakt **GitHub-integratie, secrets, en
   branch-strategie** — dat is de overlap met facet D.

2. **Geen intake-flow die een vrij gesprek omzet in een Cockpit-artefact.**
   De brainstorming-skill eindigt met een markdown-bestand in
   `docs/superpowers/specs/`. Cockpit weet niet dat het bestaat. Er is
   geen koppeling `(spec, plan) → project → kanban-kaart`.

3. **Geen "maak project aan vanuit plan"-startpunt.** Zowel in de
   frontend (`AddProjectDialog.tsx`: *"Track any folder as a project"* —
   dus: wijst naar een bestaande folder) als in de backend
   (`POST /api/v1/projects`, `ProjectCreate(ProjectBase)` met alleen
   `name` + `path`) is er geen variant die een pad *aanmaakt*.

4. **Geen formele "spec"- of "intake"-deliverable.** Het kanban-schema
   kent `plan` als deliverable-kind (via `add_plan_attachment`); een
   companion `spec` ontbreekt. De brainstorming-output heeft dus geen
   canonieke plek in het kanban-model.

5. **Geen "spec ↔ plan ↔ implementatie"-trace over projectgrenzen heen.**
   Binnen één project plakken `plan_ref`-deliverables kinderen aan het
   plan-attachment van hun parent. Zodra een **nieuw** project wordt
   geseed met **kind-kaarten** die voor *dat* project bestemd zijn, moet
   die binding ook de project-grens over — daar is vandaag niets voor.

6. **Brainstorming-vereisten botsen met autonome dispatch.** De skill
   heeft menselijke gates (visual companion, section-by-section approval).
   Een agent-sessie kan die niet uitvoeren — dat moet of vertaald worden
   naar `report_impediment`-flows, of de intake moet **buiten** de agent
   blijven.

### 2.3 De "U-vorm" samengevat

```
vandaag                       gewenst
─────────                     ────────
vrij idee           ─┐
                    │  ← gat A (intake, geen artefact)
spec (markdown)     ─┤
                    │  ← bestaand: writing-plans skill (markdown)
plan (markdown)     ─┤
                    │  ← gat B (geen koppeling plan ↔ kanban
                    │     én geen project-creatie)
(kind-)kaarten op   ─┤     bestaand: multi-agent kanban + dispatch
bestaand project    ─┘
```

Het platform bezit dus de **middelste twee transformaties** al (in
markdown-vorm), maar de **in- en uitgang** ontbreken.

## 3. Grenen aan de huidige bouwstenen

### 3.1 `superpowers:brainstorming`

- **Sterk:** dialoog-discipline, één vraag per keer, HARD-GATE tegen
  vroegtijdig implementeren, gestructureerde design-secties, expliciete
  scope-check vóór het design.
- **Zwak voor inceptie:**
  - Het eindproduct is een design-doc op een **vaste locatie**
    (`docs/superpowers/specs/`) die Cockpit **niet indexeert**.
  - Het push-model is "schrijf het op en ga verder" — er is geen hook
    zoals een `on_spec_approved`-event.
  - De user-approval-aannames ("the user approves the design") gaan niet
    op als de intake wordt gedispatched in een agent-sessie — dan moet
    het via `report_impediment` met gestructureerde opties.
  - De design-doc is een "wat + waarom", niet een "wat + waarom + hoe" —
    hij bevat geen implementatieplan. De design → plan-overgang is een
    tweede skill en een tweede user-approval-cycle.
  - Het process-flow diagram eindigt bij *"invoke writing-plans skill"*;
    er is geen instructie voor wat er **na** implementatie met de doc
    moet gebeuren (promotie naar canonieke boom — zie
    `00-orientation.md` → *drie-bomen-regel*).

### 3.2 Plans-feature

- **Sterk:** plannen zijn vindbaar in de UI, gekoppeld aan sessies via
  slug, met zoeken + stats.
- **Zwak voor inceptie:**
  - De CRUD is **bestandsgebaseerd** op `~/.claude/plans/` — niet in de
    SQLite-kanban-DB. Dat betekent: plannen zijn geen first-class
    kanban-entiteiten.
  - Geen relatie naar projecten (`resolve_plans_dir(project_path)`
    gebruikt alleen de `plansDirectory`-setting).
  - Geen relatie naar kaarten (geen `card_id` of `parent_card_id` op een
    plan-record — alleen een impliciete link via sessie-slug in JSONL).
  - Voor een inceptie-pipeline zou de Plans-feature dus ofwel met kanban
    moeten fuseren, of haar bestandsmodel moeten inruilen voor een
    DB-model.

### 3.3 Multi-agent kanban (analyst → kinderen)

- **Sterk:** dit is letterlijk een `spec (kaart-beschrijving) → plan
  (deliverable) → N executors (kind-kaarten met deps)`-machine. Het is
  dus **niet** alsof er nul implementatie-ervaring is met deze
  drie-staps-vorm.
- **Zwak voor inceptie:**
  - De analyst werkt op een **bestaande** kaart in een **bestaand**
    project. Een eerste-kaart-van-een-nieuw-project is een kip-en-ei.
  - De cap is **1 kaart per project** in dispatch (`kanban-dispatch-spec.md`
    §*Concurrency cap*). Een "intake"-kaart op het meta-project
    concurreert met alle andere meta-werk-kaarten.
  - Het analyst-persona is een sessie, geen dialoog met een mens. Voor
    een brainstorm-stap die vragen stelt aan een mens past dat niet.
  - Het plan-attachment is markdown in de kanban-DB, niet de
    `docs/superpowers/plans/`-files. Twee gescheiden
    plan-opslagplaatsen bestaan dus — en dat is precies de drie-bomen-
    inconsistentie die `00-orientation.md` al signaleert.

### 3.4 Per-project `.claude/`-beheer

- **Sterk:** alle CRUD om een project van agents, skills, MCP-servers,
  etc. te voorzien is aanwezig. Project-lokale bestanden worden geschreven
  via `<project>/.claude/...`.
- **Zwak voor inceptie:** geen bootstrap-template ("een web-app krijgt
  deze agents, deze skills, deze statusline; een CLI krijgt dit"). De
  `.claude/`-folder van een nieuw project begint dus leeg — de
  project-specifieke agents die de gebruiker wil ontstaan niet vanzelf.

## 4. Drie ontwerp-opties met trade-offs

De drie hieronder zijn **architectuurvarianten** voor de missing
inceptie-flow. Ze zijn uitwisselbaar in de eerste twee stappen
(intake + artefactregistratie) en verschillen vooral op de derde stap
(project-seed). Implementatie van elk van de drie zou een aparte
implementatie-kaart worden — de vraag voor de synthese-facet is welke te
kiezen.

### Optie 1 — "Spec-as-kaart" (minimaal)

**Idee.** Alles speelt zich af binnen het **meta-project** (waar deze
kaart zelf in staat). Een nieuwe intake-kaart wordt aangemaakt met de
vrije beschrijving van de gebruiker als `description`. De analyst-fase
mag dit keer de **`superpowers:brainstorming`-skill draaien** (in een
**interactieve** sessie — dus geen autonomous permission mode), schrijft
de design-doc weg en mount 'm als een `spec`-deliverable. Vervolgens
gebruikt dezelfde sessie (of een vervolg-kind) de
**`superpowers:writing-plans`-skill**, en het plan wordt als
`plan`-deliverable aan dezelfde kaart gehangen. Een **tweede kind-kaart**
("scaffold project", work_type=chore) krijgt `depends_on` op de
plan-kaart en dispatcht pas zodra het plan in `Done` staat; deze
executor-kaart is de eerste die het eigenlijke *nieuwe project*
aanmaakt.

**Trade-offs.**

| + | − |
|---|---|
| Geen nieuwe infra: hergebruikt analyst-fase en dispatch. | De "spec-as-kaart" leeft op het meta-project; het doelwit (het nieuwe project) bestaat nog niet — dus het kind dat het project zaait heeft geen eigen back-log. |
| Het plan-attachment-mechanisme is al SSOT-sterk (zie `spec-driven-development-analysis.md` §2.3). | De cap van 1 dispatch per project knelt zodra er meerdere intakes tegelijk lopen — eerste observatie van een portfolio-cap (zie facet C). |
| De brainstorming-skill past ongewijzigd in een interactieve sessie. | Een dispatcher-gedreven intake concurreert met alle andere meta-werk-kaarten — geen apart kanaal. |

### Optie 2 — "Twee-staps intake" (aanbevolen voor product-fork)

**Idee.** Introduceer een **nieuwe kanban-kolom `intake`** (naast
Backlog/Todo/Doing/…) op het meta-project. Intake-kaarten zijn **per
definitie mensenwerk**: ze worden niet door de dispatcher opgepakt, de
gebruiker vult ze zelf in via een dialoog in de UI (of vanuit een
externe chat). Bij "Approve & expand" wordt het opgeslagen design
ingeladen als **plan-attachment op een nieuwe kaart in de `Todo`-kolom
van een nieuw project** — waar dat project dan gecreëerd moet worden.
Daarvoor is een nieuwe MCP-actie nodig:
`kanban.create_project_from_intake(intake_card_id) → project_id`. Die
actie:

1. maakt een map aan (lokaal, evt. via `gh repo create` afhankelijk van
   config);
2. initialiseert een git-repo (en eventueel remote);
3. seedt `.claude/` met een **blueprint** (zie §6);
4. registreert het pad via `ProjectService.add_project`;
5. zet `KanbanMeta:autodispatch:<key>` voor het nieuwe project;
6. maakt de canonieke intake-kaart 1-op-1 over als eerste Backlog-kaart
   in het nieuwe project, mét `plan_ref` naar het plan-attachment.

Vanaf daar neemt de **bestaande** multi-agent flow het over: de
backlog-kaart kan via de analyst-fase worden opgesplitst in
implementatie-kaarten binnen het nieuwe project zelf.

**Trade-offs.**

| + | − |
|---|---|
| Duidelijke meta-vs-product-scheiding: intake ≠ dispatch-bus. | Nieuwe kolom → UI-werk + dispatcher-werk (skip-regels). |
| Nieuwe MCP-actie centraliseert alle "geboorte"-logica — geen cherrypicking uit services. | De blueprint is een tweede configuratie-as (welke `.claude/` past bij welk project-type). |
| Hergebruikt de plan-attachment-architectuur ongewijzigd. | Project-aanmaak raakt GitHub-auth (overlap met facet D — secrets/isolatie). |
| Geen dispatch-cap-conflict: intake draait buiten de auto-dispatcher. | De gebruiker moet nog steeds *"Approve & expand"* zeggen — extra UI-element. |

### Optie 3 — "Brainstorm-als-sessie" (agent-native)

**Idee.** De intake is een **speciale sessie-mode**. Vanuit de Projects-
pagina kies je "Start new app", wat een dedicated tmux-sessie opent
zonder een project. De sessie draait `superpowers:brainstorming`
**interactief** met de gebruiker (mens-approval via tmux `send-keys`-
reflectie en `report_impediment`-terugkeer). Bij goedkeurig ontwerp
sprint de sessie door naar `writing-plans`. Aan het einde wordt de
sessie afgesloten met automatische registratie (zelfde actie als optie
2, stap 4–6). De intake-"kaart" is dan een sessie, niet een
kanban-entiteit.

**Trade-offs.**

| + | − |
|---|---|
| Meest agent-native: geen UI-flow forceren voor iets dat de gebruiker ook in een terminal kan. | Hangt af van tmux + CC-Bridge-availability — geen kanban-Trace als de sessie sterft halverwege. |
| Geen nieuwe kolom nodig. | Brainstorming-vereisten (visual companion, section-by-section approval) zijn niet allemaal in `report_impediment` te vangen. |
| | Voor discovery ("wat zit er in mijn intake?") moet elders een log worden bijgehouden. |

**Vergelijking op de drie kern-assen:**

| As | Optie 1 (minimaal) | Optie 2 (twee-staps) | Optie 3 (sessie) |
|---|---|---|---|
| Bootstrapt nieuwe infra? | Minimaal | 1 nieuwe kolom + 1 nieuwe MCP-actie | 1 nieuwe sessie-mode + bootstrap-actie |
| Past binnen bestaande flows? | Volledig | Grotendeels | Grotendeels |
| Hoeveel menselijke UI? | Geen (achter dispatch) | Veel (intake-dialoog) | Min (terminal) |
| First-class traceability? | Zwak (kaart op verkeerd project) | Sterk (dedicated artefact-typering) | Zwak (sessie als opslag) |

**De aanbeveling** (van deze facet-analyse, niet van de synthese) is
optie 2: het is de variant die de drie-bomen-regel respecteert
(plannen leven in kanban, niet in markdown-files), de intake expliciet
mens-werk laat (geen dispatch-conflict), en de geboorte in **één**
schoon afgebakende actie bundelt. Optie 1 is echter een prima
**interim**-implementatie totdat de nieuwe kolom en MCP-actie er zijn.

## 5. Schets van een blauwdruk-laag (`project_blueprint`)

Onafhankelijk van welke optie gekozen wordt: zodra een project wordt
geseed, moeten er `.claude/`-artefacten in. Een **blauwdruk** is een
versie-pinned JSON/YAML die beschrijft:

- welke **skills** meegeleverd worden (pad → user/system-skill);
- welke **agents** (bv. een `planner`/`implementer`/`tester`-persona);
- de **statusline** en eventueel **output-styles**;
- een baseline-`settings.json` (beleid rond permission_mode,
  `plansDirectory`, model defaults);
- optioneel een **kant-en-klare `CLAUDE.md`** met project-context.

De CRUD voor al deze onderdelen bestaat al (zie §2.1); de blauwdruk
is enkel een recept dat ze in samengestelde volgorde aan een leeg
project oplegt. Dit wordt hier expliciet als onderdeel van het
"project-seed" genoemd omdat **zonder blauwdruk het project
functioneel leeg begint** — zelfs als de overige inceptie-mechaniek
klopt.

De detail-uitwerking van blauwdrukken (welke taxonomie, hoe te
versie-pinnen, hoe conflicts met user-eigen `.claude/` op te lossen)
past beter bij **facet B** (repo-provisioning & bootstrap) en is daar
uitgewerkte follow-up.

## 6. Relatie met de andere facetten

(MECE-check.)

- **Facet A (deze)** behandelt uitsluitend de **eerste twee** stappen:
  idee → (spec + plan) → kanban-artefact.
- **Facet B (Repo-provisioning & bootstrap)** behandelt de **derde**
  stap: kanban-artefact → repo + scaffolding + agents/skills +
  autodispatch-toggle. **Voor een synthese is het cruciaal dat B's
  "scaffold:project"-operatie dezelfde is als die waar deze facet naar
  verwijst** — anders ontstaan er twee methodes om een project aan te
  maken.
- **Facet C (Portfolio-orchestratie)** raakt deze facet waar
  intake-kaarten-portfolio-cap (1 per project) knelt — daarom hier
  gekozen voor een aparte `intake`-kolom (optie 2).
- **Facet D (Veilig bouwen & uitleveren)** raakt deze facet waar
  repo-creatie GitHub-auth + secret-handling vereist. Bewust
  weggelaten uit deze facet om overlap te vermijden.

## 7. Actionabele gaten → Backlog-follow-ups

Deze sectie lijst de gaten die het bouwwerk vormen. **Niet** door dit
document geïmplementeerd — door de uitvoerende sessie van deze kaart
worden ze als concrete Backlog-kaarten aangemaakt (met `work_type` en
korte acceptatiecriteria) zodat ze in de dispatch-pool terechtkomen
voor menselijke triage.

1. **`[feature] Inceptie-pipeline: van kanban-kaart naar plan dat een
   project seedt`** — kies tussen optie 1 / 2 / 3 (zie §4), implementeer
   de gekozen + maak een "scaffold:project"-operatie beschikbaar als
   kanban-actie. Resultaat: een mens kan een idee als kaart in
   Cockpit zetten en via dispatch een plan + kind-kaarten krijgen die
   **al in een nieuw project staan**. **Out-of-scope:** repo-creatie
   met GitHub (facet B/D).
2. **`[feature] MCP-actie kanban.create_project_from_intake`** — een
   dedicated actie die (i) pad aanmaakt, (ii) git init / clone, (iii)
   `.claude/` seedt via blauwdruk (zie gat #4), (iv) project
   registreert, (v) autodispatch aanzet, (vi) intake-kaart
   overplaatst. Onderdeel van #1, maar expliciet uitgesplitst
   zodat facet B 'm kan hergebruiken.
3. **`[feature] Plans ↔ kanban-DB fusie`** — de huidige
   `PlanService` schrijft naar `~/.claude/plans/`. Voor first-class
   kanban-plannen moet of een nieuwe tabel komen (FK naar
   `projects.id`), of de bestaande service moet door kanban-vormige
   CRUD vervangen worden. Los ook de drie-bomen inconsistentie op
   (`00-orientation.md` → drie-bomen-regel).
4. **`[feature] project_blueprint — declaratieve `.claude/`-seed`**
   — een JSON/YAML-vorm met skills/agents/settings/CLAUDE.md; een
   nieuwe service `BlueprintService` met `apply(project_path,
   blueprint)`. CRUD via REST + UI. Onderdeel van #1/#2 maar apart
   filetbaar.
5. **`[design] Typologie van blueprints (web-app / CLI / library /
   …)`** — wat zijn de zinvolle archetypes? Welke zijn universeel vs.
   project-specifiek? Welke defaults voor permission_mode? Dit is een
   *design-only*-kaart (work_type=analysis) die de blauwdruk-tabel
   voor #4 vastlegt.
6. **`[work-type] Routing van intake-kaarten`** — als een
   `work_type="intake"` wordt geïntroduceerd, dispatcht de kaart
   naar een **interactieve** sessie (mens-aanwezig voor
   brainstorming-approval), niet naar een autonomous executor. Dit
   hangt af van het lopende
   `[work-type-routing-analysis.md](../../../../../home/vdvgu/claude-cockpit/.claude/worktrees/k-analyse-produ-219f/docs/cockpit/work-type-routing-analysis.md)`
   besluit.
7. **`[design] Hoe vertalen we brainstorming-user-approval naar
   `report_impediment`-flows?`** — welke brainstorming-vragen
   (visual companion, "is dit goed zo?") vertalen zich 1-op-1 naar
   `options=[...]` en welke niet? Wanneer moet de sessie eindigen
   (report_impediment) en wanneer mag ze doordraaien?
8. **`[kanban-schema] Nieuwe deliverable-kind `spec`** — companion
   van het bestaande `plan`-deliverable; maakt
   brainstorming-output first-class in de kanban-geschiedenis en
   in cross-project traceability.

## 8. Niet in deze facet (expliciete out-of-scope)

Ter herinnering — deze facet zegt niets over:

- **Repo-creatie, scaffold-templates, gh-auth** → facet B (en D voor
  secrets/CI).
- **Meerdere projecten naast elkaar beheren, observability, cap
  globaal** → facet C.
- **Isolatie, secrets, CI, run/preview/deploy** → facet D.

De synthese-kaart (E, `c980a926f…`) beslist over implementatie-volgorde
en kruipt door de overlap met B en D.

## 9. Design-beslissing (na synthese)

> **Beslissing (kanban card c33b2f14, 2026-07-11):** **Optie 2 — Twee-staps
> intake** is geïmplementeerd. De intake-kolom is een nieuwe vaste kolom op
> `COLUMNS` (`backend/app/kanban/schemas.py`), de dispatcher slaat 'm
> automatisch over (`_DISPATCH_COLUMNS` blijft `("Backlog", "To Resume")`),
> en de MCP-actie `kanban.create_project_from_intake` (sibling kanban card
> `0260dbcd`) is de canonieke ingang. Zie PR-thread voor de rationale.

### Waarom Optie 2 (en niet 1 of 3)?

De drie opties uit §4 wegen op drie assen:

| As | Optie 1 | Optie 2 (gekozen) | Optie 3 |
|---|---|---|---|
| Past binnen bestaande flows | Volledig | Grotendeels | Grotendeels |
| Bootstrapt nieuwe infra | Minimaal | 1 kolom + 1 MCP-actie | 1 sessie-mode + actie |
| First-class traceability | Zwak (kaart op verkeerd project) | **Sterk** (dedicated kolom + deliverable) | Zwak (sessie als opslag) |
| Past bij drie-bomen-regel | Nee — plan-deliverable blijft op meta-project | **Ja** — kind-kaart staat meteen in het nieuwe project | Nee — plan leeft buiten kanban |

Optie 2 is de enige variant die de drie-bomen-regel uit
`00-orientation.md` respecteert: plannen/kaarten leven in de kanban-DB, niet
in losse markdown-files of tmux-sessies. Optie 1 zou een "kind-kaart" op het
meta-project leggen — letterlijk het kip-en-ei-probleem uit §2.3 in stand
houden. Optie 3 heeft geen first-class kanban-Trace.

### Implementatie-paden (gecoördineerd)

Deze kaart implementeert de **plumbing** (kolom + dispatcher-skip + REST/MCP-actie
+ frontend-knop). De sibling-kaarten uit §7 vullen de details aan:

- **`[feature][inceptie] MCP-actie kanban.create_project_from_intake`**
  (`0260dbcd`) — de canonieke actie. **Geïmplementeerd in PR met deze kaart**
  als één service + REST/MCP-entry-point (zie `backend/app/services/inception_service.py`,
  `POST /api/v1/kanban/projects/from-intake`, `mcp__cockpit-kanban__create_project_from_intake`).
- **`[feature][inceptie] project_blueprint — declaratieve `.claude/`-seed`**
  (`395590d`) — vervangt de `.claude/CLAUDE.md`-placeholder door een echte
  `BlueprintService.apply()` met skills/agents/settings. **Out of scope van
  deze PR**; InceptionService laat expliciet een TODO achter zodat de
  overgang later naadloos is.
- **`[feature][inceptie] Plans ↔ kanban-DB fusie` (`727470a`)** — vandaag
  leeft plan-markdown in een `kind="plan"` deliverable op de intake-kaart
  (kanban-DB, niet in `~/.claude/plans/`). De wire-up via `link_plan_ref`
  werkt daarom al; sibling #3 maakt plannen first-class in plaats van
  deliverable-shape, zonder gedragsverandering.
- **`[work-type][inceptie] Routing van intake-kaarten` (`071172d`)** — intake
  kaarten worden nooit door de dispatcher opgepakt, ook niet als ze later
  een work_type krijgen. `_DISPATCH_COLUMNS` is expliciet `("Backlog", "To Resume")`
  en de `_persona_filename("intake")` resolved naar `None`. **Geen aparte
  work_type nodig in deze PR**.

### Verificatie

`backend/tests/test_inception.py` dekt: happy path, intake-not-in-column,
missing card, target-already-exists, project-already-registered, git-init
fails (rollback), en de twee kruimels (autodispatch-meta geflipt, intake
zonder plan-deliverable). Frontend `CardItem.test.tsx` dekt de Promote-knop
(visible-only-on-intake, klikbaar-zonder-drawer).

## 10. Kernbevinding (voor de ouder-comment)

> Cockpit heeft vandaag **twee** van de drie ontbrekende bouwstenen
> (markdown-only intake via `brainstorming` + een bestaande spec→plan→executors-
> machine in de multi-agent flow), maar de **inceptie-stap** (vrij gesprek →
> spec-artefact dat Cockpit kent) en de **geboorte-stap** (spec/plan → nieuw
> project + repo + `.claude/`-seed) ontbreken volledig. Drie ontwerp-opties
> met oplopende verbouwingskosten zijn uitgewerkt (minimaal / twee-staps
> aanbevolen / sessie-native); één concrete MCP-actie
> (`create_project_from_intake`) en één declaratieve laag
> (`project_blueprint`) vormen samen het kleinste zinvolle
> inceptie-eindresultaat. Acht actionabele gaten zijn als Backlog-follow-ups
> gefileerd — geen daarvan wordt door deze kaart geïmplementeerd.
