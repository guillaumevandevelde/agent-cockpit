---
title: "Nieuw project spec-driven starten — is dit al ondersteund?"
type: analysis
status: active
---

# Nieuw project spec-driven starten — is dit al ondersteund?

> Kanban-kaart: **"Analysis - New project startup flow"**
> (`27b79a92482842e8ba482e31bf27edec`, work_type=analysis).
> Vraag van de gebruiker: *"Momenteel is het mij nog niet duidelijk hoe een
> nieuw project spec-driven kan starten vanuit deze applicatie. Wordt dit al
> optimaal ondersteund? Moet er nog werk gebeuren? Is het gewoon nog niet
> duidelijk?"*
>
> Dit is een **analyse** (modus 2 leaf-deliverable): dit doc is het antwoord.
> De concrete resterende gaten worden als Backlog-vervolgkaarten gefileerd
> (outcome `decomposed`), niet door dit doc geïmplementeerd.

## 0. Het korte antwoord op de drie deelvragen

1. **"Wordt dit al optimaal ondersteund?"** — De *volledige* spec-driven
   startup-pijplijn is **geïmplementeerd en werkt**, inclusief een
   mens-gericht instappunt. Er is geen ontbrekend bouwblok in het happy path.
2. **"Moet er nog werk gebeuren?"** — Ja, maar het is **afhechtwerk, geen
   bouwwerk**: twee ontworpen-maar-niet-aangesloten beleidslagen
   (`BootstrapPolicy`, `risk_class`→defaults) zorgen dat een splinternieuw
   product-project vandaag de **permissieve meta-project-defaults erft**.
   Beide zijn geverifieerd nog steeds open (§3) en staan **niet** meer als
   kaart op het bord (facet-E-follow-ups zijn door een Clear-Done-sweep
   verdwenen).
3. **"Is het gewoon nog niet duidelijk?"** — **Dit is de kern van de vraag.**
   De pijplijn is over ~8 docs + een skill verspreid; er is geen enkel
   top-level "zo start je een nieuw spec-driven project"-instappunt. Dat de
   eigenaar zelf niet wist dat de flow bestaat, is het bewijs: **de
   plumbing bestaat, de *vindbaarheid* is het echte gat** (§4).

Dit doc is geen nieuwe analyse — het is een **actueel-verifieerde
consolidatie** van het bestaande beslismateriaal, geschreven om precies deze
vraag te beantwoorden. De diepe analyse leeft in
[`product-inceptie-pipeline.md`](./product-inceptie-pipeline.md) (facet A) en
[`platform-als-app-factory.md`](./platform-als-app-factory.md) (facet E,
synthese). Alle codeverwijzingen hieronder zijn geverifieerd tegen de
huidige `master` (2026-07-18), niet uit het geheugen.

## 1. De end-to-end flow zoals die vandaag bestaat

Een gebruiker start vandaag een nieuw, spec-driven project zó — elke stap is
geïmplementeerd:

| # | Stap | Wie | Waar in de code |
|---|---|---|---|
| 1 | **Idee → interview.** Een vrij gesprek over een app-idee wordt via de `new-app`-skill omgezet in een design-doc (`superpowers:brainstorming`) en een TDD-plan (`superpowers:writing-plans`), incrementeel weggeschreven naar een durabele scratch-map. Er komt **geen kaart** op het meta-bord. | Mens, interactieve sessie | `.claude/skills/new-app/SKILL.md` |
| 2 | **Kaartloze geboorte.** De skill roept `create_project_from_interview` aan met spec + plan + titel + beschrijving. Geen tussenkaart, geen Promote-klik. | Skill → MCP/REST | `POST /api/v1/kanban/projects/from-interview`, `mcp__cockpit-kanban__create_project_from_interview` |
| 3 | **Geboorte-actie.** `InceptionService.create_project_from_interview` maakt een map + git-repo aan, seedt `.claude/` via `BlueprintService.apply` (baseline-blueprint), schrijft spec + plan als repo-bestanden (`docs/specs/<datum>-<slug>-design.md`, `docs/plans/<datum>-<slug>-plan.md`) vóór de eerste commit, registreert het pad via `ProjectService.add_project`, zet `autodispatch` volgens de `BootstrapPolicy`, en maakt de eerste Backlog-kaart met `metadata["spec_doc"]` naar het design-doc. Atomisch: elke fout rolt filesystem + Project-rij + kanban-kaart terug. | Systeem | `backend/app/services/inception_service.py`, `backend/app/services/blueprint/{apply_engine,baseline,store}.py` |
| 5 | **Autonome opbouw.** Vanaf hier neemt de bestaande multi-agent kanban-flow het over: de eerste Backlog-kaart wordt door de analyst-fase in kind-kaarten gesplitst en door executors gebouwd — dezelfde machine als het meta-project zelf gebruikt. | Dispatcher | `backend/app/kanban/dispatch.py`, `multi-agent-kanban.md` |

**Dit is een echte, gesloten pijplijn:** vrij idee → spec + plan → nieuw
gitrepo met geseede `.claude/` → autonome dispatch. Het "spec-driven"-deel is
letterlijk ingebakken: spec én plan landen als repo-bestanden in het nieuwe
project vóór de eerste commit, en de eerste Backlog-kaart wijst er via
`metadata["spec_doc"]` naar. Zie de design-beslissing in
[`kaartloze-app-inceptie-decision.md`](./kaartloze-app-inceptie-decision.md)
(optie 3 — kaartloze geboorte, gekozen 2026-07-29), die
[`product-inceptie-pipeline.md`](./product-inceptie-pipeline.md) §9 (optie 2 —
twee-staps intake) en
[`intake-authoring-flow-decision.md`](./intake-authoring-flow-decision.md) §3
herziet. De `intake`-kolom en de Promote-route zijn gesloopt in kaart
`d0531c12…`.

## 2. Wat geverifieerd aanwezig is (bouwstenen)

Steekproef tegen de huidige codebase — alles bestaat:

- `InceptionService` + REST-endpoint + `create_project_from_interview` MCP-tool.
- `BlueprintService` (`baseline.py`, `apply_engine.py`, `store.py`,
  `baseline_blueprint.yaml`) — seedt de `.claude/`-laag van een nieuw project.
- `RepoBootstrapService` + `repo_bootstrap.py` — atomic mkdir + git init +
  eerste commit.
- `BlueprintsPage.tsx` — het UI-oppervlak voor de blueprint-laag. (De
  `PromoteToProjectDialog.tsx` is met de intake-route verwijderd; de
  geboorte-flow heeft geen UI-knop meer — hij loopt via `/new-app`.)
- `new-app`-skill (voorheen `intake-authoring`) — de gedocumenteerde voordeur.

Facet E (`platform-als-app-factory.md` §2.1) telt dit uitputtend op en
concludeert: van de 35 oorspronkelijke facet-follow-ups zijn er **33 gemerged**;
Cockpit heeft zichzelf agentisch tot app-fabriek omgebouwd.

## 3. Wat er nog moet gebeuren — afhechtwerk, geverifieerd nog open

Dit zijn **geen nieuwe** bevindingen; het zijn facet-E §2.2/§3-items,
opnieuw geverifieerd tegen de huidige code en bevestigd nog steeds open —
én, kritisch, **niet meer als kaart op het bord** (Backlog/Impediment
gecontroleerd op 2026-07-18: geen kaart over deze twee gaten).

### 3.1 `BootstrapPolicy` is niet aangesloten (hoog)

`backend/app/services/bootstrap_policy.py` bestaat als volledig ontworpen +
geteste dataclass, maar de docstring zegt letterlijk *"intentionally not
imported by any production module yet"* — en een grep bevestigt: noch
`inception_service.py` noch `repo_bootstrap_service.py` importeert of
consumeert het. Gevolg: een nieuw project krijgt **ad-hoc defaults uit de
bootstrap-code-paden**, niet het gedelibereerde beleid (autodispatch-uit-bij-
geboorte, MIT-license-default, first-commit-template, geen-CI-bij-geboorte).
Zolang deze wiring ontbreekt is elk ander geboortebeleid dode letter voor
nieuwe projecten. Bron: [`bootstrap-policy.md`](./bootstrap-policy.md),
facet E §2.2 punt 1 + §3 item 1.

### 3.2 `risk_class` stuurt de dispatch-defaults nog niet aan (hoog)

`get_skip_permissions()` (`backend/app/kanban/dispatch.py:293-297`)
retourneert nog **onvoorwaardelijk `True`** als er geen expliciete
`KanbanMeta`-override is — er is geen pad dat een `product`-project via
`ProjectSecurityProfile.risk_class` naar `skip_permissions=False` vertaalt,
zoals facet D aanbeval (*"skip_permissions default voor product-projecten:
False — een agent die niet 'alleen-lezen-mag' op een splinternieuw project is
een aanvalsvector"*). Evenmin voedt `SecretStore.get(...)` de `extra_env` van
`spawn_session`, of stuurt `risk_class` de `default_transport`. Gevolg: een
splinternieuw product-project erft de **permissieve meta-project-defaults**.
Bron: [`risk-class-taxonomie.md`](./risk-class-taxonomie.md), facet E §2.2
punt 2 + §3 item 2.

### 3.3 Beleidsschakelaars die klaarstaan maar "uit" zijn (middel)

- **Portfolio-cap** staat uit (`settings.portfolio_cap_enabled = False`,
  `config.py`); mechaniek is klaar en getest. Dit is een go/no-go, geen
  bouwwerk — bewust gefaseerd. Bron: [`portfolio-policy.md`](./portfolio-policy.md).
- **CI-bootstrap bij geboorte** is een bewuste *nee* (`BootstrapPolicy
  .ci_bootstrap = False`); kan pas zinvol aangezet worden nadat §3.1 is
  aangesloten. Geen los gat.

## 4. De echte bevinding: vindbaarheid, niet plumbing

De vraag zelf ("het is mij niet duidelijk hoe...") is het signaal. De flow
uit §1 werkt, maar is verspreid over
[`product-inceptie-pipeline.md`](./product-inceptie-pipeline.md),
[`platform-als-app-factory.md`](./platform-als-app-factory.md),
[`intake-authoring-flow-decision.md`](./intake-authoring-flow-decision.md),
[`blueprints-typology.md`](./blueprints-typology.md), de `new-app`-
skill, en een `Promote`-knop op de kanban-kaart. Er is **geen enkel top-level
instappunt** dat een mens vertelt: *"wil je een nieuw app-idee bouwen? Start
hier."* De Projects-pagina's `AddProjectDialog` zegt zelfs expliciet *"Track
any folder as a project"* (`AddProjectDialog.tsx:78`) — dat wijst naar een
*bestaande* map en versterkt de indruk dat "nieuw project = bestaande map
registreren", terwijl de spec-driven-geboorte een heel ander (en verborgen)
pad is.

Dit doc is de eerste helft van de oplossing (één plek die de flow uitlegt).
De tweede helft — een vindbaar UI-/README-instappunt — is een gefileerde
vervolgkaart (§5).

## 5. Vervolgkaarten (outcome `decomposed`)

Drie Backlog-vervolgkaarten worden in dezelfde sessie gefileerd. Ze zijn
onderling **onafhankelijk** (geen `depends_on`-contract; §3.1 wordt best éérst
opgepakt omdat het §3.2's effect op nieuwe projecten scherpstelt, maar elk is
apart implementeerbaar en testbaar):

1. **[feature] Wire `BootstrapPolicy` in de geboorte-flow** — §3.1.
2. **[feature] `risk_class`-gestuurde dispatch-defaults voor product-projecten** — §3.2.
3. **[docs/ux] Vindbaar "start een nieuw spec-driven project"-instappunt** — §4.

De portfolio-cap-go/no-go (§3.3) is bewust **geen** kaart: het is een
beleidsbesluit voor de mens, geen dispatchbaar werk, en leeft al in
[`portfolio-policy.md`](./portfolio-policy.md).
