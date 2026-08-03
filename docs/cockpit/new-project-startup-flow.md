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
| 1 | **Idee → intake-kaart.** Een vrij gesprek over een app-idee wordt via de `intake-authoring`-skill omgezet in één kaart in de `intake`-kolom van het meta-project, met een `spec`-deliverable (design-doc uit `superpowers:brainstorming`) en een `plan`-deliverable (TDD-plan uit `superpowers:writing-plans`). **Vervangen (kaart `1fa1b693…`):** de voordeur is nu de kaartloze `new-app`-skill — interview → durabele scratch-map → `create_project_from_interview`, zónder tussenkaart. Stap 2-3 hieronder gelden alleen nog voor de oude intake-route, die naast de nieuwe blijft bestaan tot de sloop-kaart landt. | Mens, interactieve sessie | `.claude/skills/new-app/SKILL.md` |
| 2 | **Intake-kolom, buiten de dispatcher.** De `intake`-kolom is een vaste kolom die de auto-dispatcher bewust overslaat (`_DISPATCH_COLUMNS = ("Backlog", "To Resume")`). Intake is per definitie mensenwerk. | Board | `backend/app/kanban/schemas.py:21` (`COLUMNS`), `kanban-conventions.md` §1 |
| 3 | **Promote-knop → geboorte.** De gebruiker klikt **Promote** op de intake-kaart; dat roept `create_project_from_intake` aan. | Mens, één klik | `frontend/src/features/kanban/components/PromoteToProjectDialog.tsx`, `POST /api/v1/kanban/projects/from-intake` (`router.py:1013`) |
| 4 | **Geboorte-actie.** `InceptionService.create_project_from_intake` maakt een map + git-repo aan (`RepoBootstrapService`), seedt `.claude/` via `BlueprintService.apply` (baseline-blueprint), registreert het pad via `ProjectService.add_project`, flipt `autodispatch` aan, en kopieert de intake-kaart naar de eerste Backlog-kaart van het nieuwe project. | Systeem | `backend/app/services/inception_service.py`, `backend/app/services/repo_bootstrap_service.py`, `backend/app/services/blueprint/{apply_engine,baseline,store}.py` |
| 5 | **Autonome opbouw.** Vanaf hier neemt de bestaande multi-agent kanban-flow het over: de eerste Backlog-kaart wordt door de analyst-fase in kind-kaarten gesplitst en door executors gebouwd — dezelfde machine als het meta-project zelf gebruikt. | Dispatcher | `backend/app/kanban/dispatch.py`, `multi-agent-kanban.md` |

**Dit is een echte, gesloten pijplijn:** vrij idee → spec + plan → nieuw
gitrepo met geseede `.claude/` → autonome dispatch. Het "spec-driven"-deel is
letterlijk ingebakken: de intake-kaart *draagt* een `spec`- én `plan`-
deliverable voordat het project geboren wordt, en die reizen mee als eerste
Backlog-kaart. Zie de design-beslissing in
[`product-inceptie-pipeline.md`](./product-inceptie-pipeline.md) §9 (Optie 2 —
twee-staps intake, gekozen 2026-07-11, kaart `c33b2f14`) en de
intake-interview-keuze in
[`intake-authoring-flow-decision.md`](./intake-authoring-flow-decision.md)
(`decisions.md`-regels 2026-07-14).

## 2. Wat geverifieerd aanwezig is (bouwstenen)

Steekproef tegen de huidige codebase — alles bestaat:

- `intake`-kolom in `COLUMNS` (`schemas.py:21`), dispatcher-skip intact.
- `InceptionService` + REST-endpoint + `create_project_from_intake` MCP-tool.
- `BlueprintService` (`baseline.py`, `apply_engine.py`, `store.py`,
  `baseline_blueprint.yaml`) — seedt de `.claude/`-laag van een nieuw project.
- `RepoBootstrapService` + `repo_bootstrap.py` — atomic mkdir + git init +
  eerste commit.
- `PromoteToProjectDialog.tsx` + `BlueprintsPage.tsx` — de UI-oppervlakken.
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
