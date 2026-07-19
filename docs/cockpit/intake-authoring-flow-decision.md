---
title: "Interview-/intake-authoring-flow: van vrij gesprek naar ingevulde intake-kaart"
type: decision
status: decided
---

# Interview-/intake-authoring-flow: van vrij gesprek naar ingevulde intake-kaart

**Datum:** 2026-07-14
**Status:** besloten
**Kaart:** `f2fe8548…`
**Uitkomst:** **`superpowers:brainstorming` + `writing-plans`** in een dunne `intake-authoring`-skill. Niet spec-kit (zware dep, dubbele orkestratie). `intake_kind` vervalt voor de MVP.

> Kanban-kaart: **`[analysis][inceptie] Interview-/intake-authoring-flow: vrij
> gesprek → ingevulde intake-kaart (spec + plan)`**
> (`f2fe854803924e5cbb875bdddc2c4ef5`). Leaf-spike: deze doc *is* de deliverable.
> Vervolg op beslissing `646f5860` ([`intake-kind-decision.md`](./intake-kind-decision.md)),
> gescoped door een expliciet mensantwoord. Bouwt voort op
> [`product-inceptie-pipeline.md`](./product-inceptie-pipeline.md) §2.3 (gat A) en
> [`intake-card-routing-analysis.md`](./intake-card-routing-analysis.md) §4.1.
>
> Alle code-claims zijn geverifieerd op deze branch (`k-analysis-ince-eb86`), niet
> uit het geheugen.

## 0. De vraag in één paragraaf

De *achterkant* van de inceptie — een ingevulde intake-kaart omzetten in een echt
project (map + git + `.claude/`-seed + eerste Backlog-kaart) — bestaat al als
`create_project_from_intake` (`inception_service.py`). Wat ontbreekt is de
**voordeur**: een flow die een **vrij gesprek** omzet in die ingevulde
intake-kolom-kaart (met `spec` + `plan`) die `create_project_from_intake` als input
neemt. Dit is "gat A" uit inceptie-pipeline §2.3. Deze doc kiest de tool, legt het
output-contract vast, beslist de mens-in-de-lus-vorm, en beslist over `intake_kind`.

## 1. Wat al bestaat (geverifieerd) — het gat is kleiner dan het lijkt

| Bouwsteen | Status | Bewijs |
|---|---|---|
| **`intake`-kolom** | Bestaat; wordt nooit gedispatched | `COLUMNS` bevat `intake`; `_DISPATCH_COLUMNS = ("Backlog","To Resume")` (routing-analyse §1a) |
| **`create_project_from_intake`** | Bestaat; canonieke geboorte | `inception_service.py`; `mcp_server.py:644`; eist kaart in `intake`-kolom |
| **`spec`-deliverable-kind** | Bestaat | `attach_deliverable(kind="spec", ref=<md body>)`; `mcp_server.py:340`; conventions §3 |
| **`plan`-deliverable-kind** | Bestaat | `add_plan_attachment` (met kinderen) of `attach_deliverable(kind="plan")` (childless); conventions §3 |
| **`create_card` / `attach_deliverable`** | Bestaat als MCP-tools | `mcp_server.py` |
| **`superpowers:brainstorming` + `writing-plans`** | Bestaat, geïnstalleerd | gesprek→design→plan naar markdown (inceptie-pipeline §3.1) |
| **`intake_kind`-veld** | **Bestaat niet** | `grep intake_kind backend/ frontend/` → leeg |

**Gevolg:** de flow hoeft géén nieuwe geboorte-mechaniek te bouwen. Alle bouwstenen
(intake-kolom, deliverable-kinds, kaart-creatie, project-geboorte) staan er. De
enige ontbrekende schakel is een **orkestrator** die het gesprek voert en de output
als `spec` + `plan` op een intake-kaart landt. Dat is een klein stuk werk — zie §7.

## 2. AC#1 — Tool-keuze: `spec-kit` vs. `superpowers` vs. eigen flow

### 2.1 De kern-observatie die de keuze vereenvoudigt

Wélke tool het gesprek ook voert, de **uitgang is dezelfde adapter**: neem de
geproduceerde design- + plan-markdown, en land die als `spec`- + `plan`-deliverable
op een intake-kolom-kaart (§3). Die adapter is ~onvermijdelijk en tool-onafhankelijk.
De tool-keuze reduceert dus tot één vraag: *welke authoring-engine levert het beste
gesprek→design→plan tegen de laagste bouw- en koppelkost, gegeven dat de adapter er
sowieso komt?*

### 2.2 De drie kandidaten

**A. `spec-kit` (mens-genoemd).** GitHub's Spec-Driven-Development-toolkit: een
externe CLI (`specify init`) die slash-commands (`/specify`, `/plan`, `/tasks`) en
een vaste repo-layout (`.specify/`, `specs/`, `spec.md` + `plan.md` + `tasks.md`)
scaffoldt, met de AI-agent als uitvoerder.

- **Voor:** doelgericht voor gesprek→spec→plan; volwassen SDD-structuur; expliciet
  door de mens genoemd.
- **Tegen (zwaarwegend):**
  1. **Scaffoldt ín een repo — maar hier bestáát de repo nog niet.** spec-kit's hele
     model is "init in een bestaande repo". De intake speelt vóór de
     project-geboorte (het kip-en-ei van §2.3). We zouden spec-kit tegen een
     wegwerp-map moeten draaien en de output eruit vissen.
  2. **Output = markdown-bestanden op disk**, precies hetzelfde "Cockpit indexeert
     het niet"-probleem als de superpowers-skills (§3.1 zwak) — dus de adapter is
     nog steeds nodig. spec-kit bespaart de adapter niet.
  3. **Tweede orkestratielaag.** spec-kit's `/tasks` + zijn eigen agent-wrapping
     dupliceren onze kanban-kind-kaarten en onze dispatch/analyst-flow. We zouden
     twee parallelle "spec→plan→taken"-machines onderhouden.
  4. **Zware externe dependency** (aparte CLI, aparte templates, aparte
     directory-conventies) voor een stap die we grotendeels al bezitten.
- **Netto:** hoge koppelkost + gedupliceerde orkestratie + repo-bestaat-nog-niet-
  mismatch, en de adapter is er alsnog. Slechte trade.

**B. `superpowers:brainstorming` + `writing-plans` (aanbevolen kern).** Al
geïnstalleerd en in gebruik. `brainstorming` doet exact gesprek→user-approved design
met harde approval-gate; `writing-plans` doet design→TDD-plan.

- **Voor:** nul nieuwe dependency; dialoog-discipline (één vraag per keer, HARD-GATE
  tegen vroeg implementeren) is al opgelost en beproefd; conceptueel hergebruiken we
  deze skills al in de analyst-fase; output-vorm (design-md + plan-md) mapt 1-op-1 op
  `spec` + `plan`-deliverable.
- **Tegen:** output landt op een vaste disk-locatie (`docs/superpowers/specs/`,
  `…/plans/`), niet in kanban (§3.1) — precies waar de dunne adapter voor is; de
  approval-gates zijn interactief-menselijk (zie §4).

**C. Eigen minimale flow.** Zelf een gesprek-skill schrijven die direct als
`spec`/`plan` op de intake-kaart landt.

- **Voor:** volledige controle; geen adapter (landt direct in kanban-vorm).
- **Tegen:** herbouwt de dialoog-discipline die `brainstorming` al perfect doet;
  meer bouw- + onderhoudskost; verliest de beproefde vraag-per-beurt-structuur. Puur
  reinventen.

### 2.3 Besluit AC#1

> **Kies B, verpakt als een dunne Cockpit-native adapter.** Bouw een nieuwe skill
> (werktitel `intake-authoring`, in `.claude/skills/`) die **`superpowers:brainstorming`
> als gespreks-/design-engine en `superpowers:writing-plans` als plan-engine
> aanroept**, en hun markdown-output via de bestaande MCP-tools als `spec` + `plan`
> op een intake-kolom-kaart landt (§3). **Niet `spec-kit`** — het scaffoldt in een
> nog-niet-bestaande repo, dupliceert onze orkestratie, en de adapter blijft toch
> nodig. **Niet een volledig eigen flow** — `brainstorming` lost het moeilijke deel
> (dialoog-discipline + approval-gate) al op; opnieuw bouwen is verspilling.

Trade-off die we bewust accepteren: we blijven afhankelijk van de
superpowers-plugin. Dat is nu al zo (de analyst-fase leunt erop), dus het voegt geen
nieuwe koppeling toe. Blijkt de plugin ooit te wringen, dan is optie C de
terugval — de adapter en het output-contract (§3) veranderen daarbij niet, alleen de
engine erachter.

## 3. AC#2 — Output-contract (geverifieerd tegen `inception_service.py`)

**Wat `create_project_from_intake` daadwerkelijk leest** (geen aanname —
`inception_service.py:73-224`):

- **Input-parameters:** `intake_card_id`, `project_name`, `target_path`. Meer niet.
- **Harde precondities:** de kaart moet in de `intake`-kolom staan (anders
  `ValueError`, `inception_service.py:89`); `target_path` mag niet bestaan; geen
  Project-rij op dat pad.
- **Wat het van de kaart overneemt:** `title`, `description`, `meta` (metadata) →
  gekopieerd naar de eerste Backlog-kaart van het nieuwe project
  (`inception_service.py:196-203`).
- **Wat het NIET doet:** het **parst de `spec`/`plan`-deliverable-bodies niet**. Het
  legt onvoorwaardelijk een `plan_ref` van de nieuwe kaart terug naar de intake-kaart
  (`inception_service.py:215-224`). De spec/plan-bodies dienen dus **downstream
  traceability**: de analyst die straks de eerste Backlog-kaart van het nieuwe
  project oppakt, volgt de `plan_ref` terug en leest daar de spec + het plan.

**Het contract dat de flow moet opleveren** is daarmee scherp en minimaal:

1. Eén kaart in de **`intake`-kolom** van het meta-project (`create_card(project=<meta>,
   column="intake", title=…, description=…)`). Titel + beschrijving = de kern van
   het idee (worden overgenomen naar het nieuwe project).
2. Een **`spec`-deliverable** met de brainstorming-design-doc als markdown-body
   (`attach_deliverable(card_id, kind="spec", ref=<design-md>)`).
3. Een **`plan`-deliverable** met het writing-plans-plan als markdown-body. Let op:
   `add_plan_attachment` eist kind-kaarten (`mcp_server.py:562`, parent→children); een
   intake-kaart heeft die niet. Land het plan daarom via `attach_deliverable(card_id,
   kind="plan", ref=<plan-md>)` — de childless-route, identiek aan `spec`. (De
   executor verifieert dat de backend `kind="plan"` via `attach_deliverable`
   accepteert; conventions §3 staat het toe, `DELIVERABLE_KINDS` valideert het niet
   weg omdat plan/spec buiten de korte enum vallen.)

**De flow eindigt hier.** Hij roept `create_project_from_intake` **niet zelf** aan —
dat is de bestaande Promote-actie (de knop verschijnt al zodra de kaart in de
`intake`-kolom staat, routing-analyse §1a; `CardItem.tsx`). De flow **hergebruikt**
de geboorte; hij herbouwt haar niet. Hij **eindigt exact waar
`create_project_from_intake` begint**: een promotebare intake-kaart.

## 4. AC#3 — Mens-in-de-lus: interactief, niet `report_impediment`

### 4.1 De structurele mismatch

`brainstorming`'s approval-gates zijn **conversationeel en veel-beurts**:
section-by-section approval, "is dit goed zo?", visual-companion-terugkoppeling —
tientallen beurten vrije dialoog. `report_impediment` is het tegenovergestelde: het
stelt **één** vraag met een **vaste `options`-lijst** en **beëindigt de sessie**
(claim vrijgegeven). Je kunt een vrije brainstorm-dialoog niet in een reeks
single-shot impediment-gates persen zonder de sessie tientallen keren te doden en te
herstarten — dat breekt de dialoog-discipline die juist de reden was om `brainstorming`
te kiezen.

### 4.2 Besluit AC#3

> **De authoring-/interview-fase draait INTERACTIEF, buiten de autonome
> dispatcher** — een dedicated interactieve Claude Code-sessie met de mens present.
> Dit is consistent met de bestaande semantiek: intake-kaarten zijn per definitie
> mens-werk en worden nooit gedispatched (routing-analyse §1a). De fijnmazige
> brainstorming-approval blijft dus native interactief (skill-eigen gates).
>
> **`report_impediment` is NIET de gate voor de vrije dialoog.** Het blijft
> gereserveerd voor grove beslis-vorken die wél tot een vaste optielijst reduceren
> (bijv. "welk project-archetype?" als dat later nodig blijkt) — niet voor de
> ideatie zelf.

### 4.3 Dedupe tegen inceptie-pipeline §7 punt 7

Follow-up §7 pt 7 vroeg: *"Hoe vertalen we brainstorming-user-approval naar
`report_impediment`-flows?"* — **dit besluit lost die vraag op** met een principiële
"niet forceren": de fijnmazige approval hoort niet in `report_impediment` (structurele
mismatch, §4.1); alleen grove forks passen erin. §7 pt 7 wordt daarmee **beantwoord,
niet gedupliceerd** — als er een aparte kaart voor bestaat, kan die met een verwijzing
naar deze §4 gesloten worden. Er komt géén nieuwe kaart voor.

## 5. AC#4 — `intake_kind`: vervalt voor de MVP

De vraag: heeft déze flow modus-onderscheid nodig? De drie kandidaat-modi uit de
routing-analyse:

- **`brainstorm`** (greenfield idee → gesprek → spec + plan) — dit *is* de MVP-flow;
  `brainstorming` past er exact op.
- **`customer-discovery`** — structureel dezelfde dialoog→spec, met een andere
  vragenset. Geen aparte code-tak nodig; een variant-prompt binnen dezelfde skill.
- **`legacy-import`** — structureel **anders**: geen vrije ideatie, maar "wijs naar
  bestaande code → analyseer → destilleer een spec". Dit zou wél een aparte tak (en
  dus een discriminator) vergen — maar het valt buiten de mens-vraag ("**vrij
  gesprek** → intake"), die per definitie de brainstorm-modus is.

**De MVP-flow is single-mode (brainstorm/greenfield). Hij leest geen
modus-discriminator.** Per de eigen AC#4-regel van de kaart ("geen modus-onderscheid
nodig → `intake_kind` vervalt definitief, YAGNI dan met terugwerkende kracht
correct"):

> **`intake_kind` wordt NIET gebouwd als onderdeel van deze flow.** De MVP heeft
> geen consument voor het veld, dus het zou opnieuw een dood veld zijn — precies de
> val die `intake-kind-decision.md` §5 wilde vermijden ("het landt dus mét een
> consument"). Dit is de empirische uitkomst waar dat besluit op wachtte:
> gemeten aan de daadwerkelijke flow is `intake_kind` nu YAGNI.

Nuance (eerlijk): `legacy-import` is een reële toekomstige modus die het veld wél zou
nodig hebben. Maar of die modus ooit gebouwd wordt is zelf een toekomstige
productbeslissing — niet iets om nu vooruit te modelleren. Het veld wordt daarom
**samen met de eerste structureel-andere tweede modus** (legacy-import) geïntroduceerd
als die concreet op de rol komt, mét zijn eerste echte lezer, volgens de
implementatiespec die al klaarligt in routing-analyse §4.1. Tot dan bestaat het niet.
Dit sluit `intake-kind-decision.md` naadloos af: dat besluit stelde het veld uit en
bond het aan déze flow; déze flow stelt empirisch vast dat de MVP het niet nodig heeft.

## 6. Besluit — samengevat

| AC | Besluit |
|---|---|
| **#1 Tool** | `superpowers:brainstorming` + `writing-plans` als engine, verpakt in een nieuwe dunne `intake-authoring`-skill. Niet spec-kit (zware dep, repo-bestaat-nog-niet, dubbele orkestratie, adapter blijft nodig). Niet eigen flow (herbouwt opgeloste dialoog-discipline). |
| **#2 Output** | Kaart in `intake`-kolom + `spec`-deliverable (design-md) + `plan`-deliverable (plan-md, via `attach_deliverable` childless). Flow eindigt promotebaar; roept `create_project_from_intake` niet zelf aan. Geverifieerd: die actie parst de bodies niet — ze dienen downstream-traceability via `plan_ref`. |
| **#3 Mens-in-lus** | Interactieve sessie buiten de dispatcher (intake = mens-werk). `report_impediment` niet voor de vrije dialoog (structurele mismatch); alleen voor grove forks. Lost §7 pt 7 op — geen nieuwe kaart. |
| **#4 `intake_kind`** | Vervalt voor de MVP (single-mode, geen lezer → YAGNI met terugwerkende kracht correct). Komt pas terug mét legacy-import als eerste consument, volgens routing-analyse §4.1. Sluit `intake-kind-decision.md` af. |

## 7. Follow-up implementatiekaart (acceptatiecriteria-niveau)

> Wordt door deze leaf-spike als concrete Backlog-kaart gefileerd, zodat ze in de
> dispatch-pool voor menselijke triage komt. Eén kaart — klein en samenhangend;
> de meeste bouwstenen bestaan al (§1).

**Titel:** `[feature][inceptie] intake-authoring-skill: gesprek → intake-kaart (spec + plan)`
**`work_type`:** `feature`. **Kolom:** `Backlog`.

**Acceptatiecriteria:**

1. Een nieuwe skill `intake-authoring` (in `.claude/skills/`, met `SKILL.md`) die,
   **interactief** aangeroepen (mens present, buiten de autonome dispatcher):
   a. `superpowers:brainstorming` draait tot een user-approved design;
   b. `superpowers:writing-plans` draait tot een TDD-plan;
   c. via de `cockpit-kanban` MCP-tools een kaart in de **`intake`-kolom** van het
      meta-project aanmaakt (`create_card`), en de design-md als `kind="spec"` +
      de plan-md als `kind="plan"` deliverable eraan hangt (`attach_deliverable`).
2. De opgeleverde kaart is **promotebaar** door de bestaande `create_project_from_intake`
   (Promote-knop): staat in de `intake`-kolom, heeft een zinvolle titel + beschrijving.
   De skill roept `create_project_from_intake` **niet zelf** aan — de mens klikt Promote.
3. Verifieer dat de backend `attach_deliverable(kind="plan", ref=<body>)` op een
   childless kaart accepteert (conventions §3 zegt ja; `add_plan_attachment` kan niet
   want dat eist kind-kaarten). Documenteer de gekozen route in de skill.
4. Een korte trigger voor de mens: minimaal gedocumenteerd in de skill-beschrijving
   ("gebruik deze skill om een nieuw app-idee als intake-kaart te authoren"). Een UI-knop
   (bijv. op de Projects-pagina of boven de intake-kolom) is **optioneel/nice-to-have**
   en mag een aparte kaart worden — niet blokkerend voor deze.

**Out of scope:** `intake_kind`-veld (§5, vervalt); repo-creatie/gh-auth/blueprints
(facet B/D); `WORK_TYPES`/persona-routing (blijft vierwaardig);
`create_project_from_intake` zelf (bestaat, ongewijzigd).

## 8. Wat expliciet NIET verandert

- **`create_project_from_intake`** — ongewijzigd de canonieke geboorte; de flow
  eindigt waar zij begint.
- **`WORK_TYPES` / persona-routing** — blijft vierwaardig; geen `work_type="intake"`.
- **De `intake`-kolom** — blijft source-of-truth voor "niet auto-dispatchen".
- **`intake_kind`** — wordt nu niet gebouwd (§5); geen schema-verandering.
- **De superpowers-skills** — worden hergebruikt, niet geforkt of aangepast.
