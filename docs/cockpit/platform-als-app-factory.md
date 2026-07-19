---
title: "Cockpit als app-fabriek: consolidatie van vier facet-analyses"
type: analysis
status: active
---

# Cockpit als app-fabriek: consolidatie van vier facet-analyses

> Synthese-kaart (facet E, `c980a926119649d2af1b2f66274fff36`) van de
> ouderkaart *"Deze applicatie als platform om andere applicaties te
> bouwen"* (`8db831a0df6d42689c5b26325b6cbecc`). Consolideert:
> [`product-inceptie-pipeline.md`](./product-inceptie-pipeline.md) (facet A),
> [`repo-provisioning-bootstrap.md`](./repo-provisioning-bootstrap.md) (facet B),
> [`portfolio-orchestratie.md`](./portfolio-orchestratie.md) (facet C),
> [`veilig-bouwen-en-uitleveren.md`](./veilig-bouwen-en-uitleveren.md) (facet D).
>
> **Methodologische noot.** De ouderkaart en vrijwel alle in de vier
> facet-comments genoemde Backlog-follow-up-kaarten zijn tussen
> 2026-07-11 en 2026-07-17 door een bulk `Clear-Done`-actie van het
> bord verwijderd (kanban_ops-log: drie sweeps op 2026-07-12 06:10,
> 2026-07-12 10:22 en 2026-07-14 11:24, elk tientallen `delete`-ops in
> één transactie). De kaarten zelf zijn dus niet meer op te vragen via
> `get_card`/`list_cards`; deze synthese is gereconstrueerd uit de
> **durable `kanban_ops`-audit-log** (`~/.claude-registry/kanban.db`,
> tabel `kanban_ops`), waarin `create`/`comment`/`move`-rijen de
> card-delete overleven. Dat dit nodig was is zelf een bevinding — zie
> de flag-problem-melding aan het eind van de sessie die deze kaart
> heeft opgeleverd.

## 1. De oorspronkelijke vraag

> Hoe geschikt is Cockpit vandaag om er **agentisch andere
> applicaties** mee te bouwen en te beheren, en wat is er nog nodig?

De vier facet-analyses (2026-07-11) beantwoordden dit apart voor
inceptie (A), bootstrap (B), portfolio (C) en veiligheid (D). Elke
facet leverde een analysedoc + een set gescopete Backlog-follow-ups.
**Wat deze synthese toevoegt ten opzichte van de vier facet-comments op
de ouderkaart:** in de zes dagen tussen de facet-analyses en deze
consolidatie is vrijwel de **volledige** in de facetten geïdentificeerde
roadmap al door autonome kanban-dispatch **geïmplementeerd en gemerged**
— dit verandert het eindoordeel wezenlijk ten opzichte van wat de
facet-comments op het moment van schrijven meldden ("analyse + gaten
gefileerd, geen implementatie"). Dat is zelf het belangrijkste
synthese-resultaat: **Cockpit heeft, agentisch, zichzelf grotendeels tot
app-fabriek omgebouwd** in de tijd die het kostte om deze synthese-kaart
te dispatchen.

## 2. Eindoordeel: hoe geschikt is Cockpit vandaag?

**Kort antwoord.** Cockpit kan vandaag, zonder verdere implementatie,
het volledige pad lopen: mens-goedgekeurd idee → intake-kaart →
spec + plan → nieuw gitrepo met baseline-`.claude/`-configuratie,
scaffold-template, git-historie en registratie in het portfolio →
autonome kanban-dispatch die het nieuwe project verder opbouwt via
dezelfde analyst→executor-machine als het meta-project zelf gebruikt.
Het kan meerdere van zulke projecten naast zichzelf **zien** (portfolio-
dashboard) en heeft de bouwstenen om ze **veilig** te bouwen, te draaien
en uit te leveren (secrets-store, container-hardening, CI-templates,
preview-runs, GHCR-deploy, audit-log).

**Maar** — en dit is de kern van het resterende gat — die bouwstenen
zijn geleverd als **losse, correct geïmplementeerde onderdelen**, niet
als een **eind-tot-eind aangesloten veiligheidsbeleid**. Een
splinternieuw product-project erft vandaag nog de **permissieve
meta-project-defaults** (`skip_permissions=True`, geen risk-class-
gestuurde transport- of secrets-keuze, geen CI bij geboorte) totdat een
mens of een vervolgkaart de bewust ontworpen, maar nog niet
aangesloten, policy-laag erin schakelt. Met andere woorden: Cockpit is
**functioneel** een app-fabriek, maar nog niet **veilig-by-default**
een app-fabriek.

### 2.1 Wat vandaag al werkt (per facet, geverifieerd in de huidige codebase)

| Facet | Bouwsteen | Status | Bewijs |
|---|---|---|---|
| A — Inceptie | `intake`-kolom + `kanban.create_project_from_intake` (Optie 2 uit A§4) | ✅ geïmplementeerd | `backend/app/services/inception_service.py`, `POST /api/v1/kanban/projects/from-intake` |
| A | `BlueprintService` (baseline-blueprint + apply-engine) | ✅ | `backend/app/services/blueprint/{baseline,apply_engine,store}.py` |
| A | Plans ↔ kanban-DB fusie (drie-bomen-probleem opgelost) | ✅ | `kanban_plans`-tabel, `KanbanPlan`-model |
| A | Deliverable-kind `spec` (companion van `plan`) | ✅ | kanban-schema |
| A | Blueprint-typologie + brainstorm→`report_impediment`-vertaling (design-only) | ✅ (docs) | `docs/cockpit/blueprints-typology.md`, `docs/cockpit/brainstorm-to-impediment-bridge.md` |
| B — Bootstrap | `RepoBootstrapService` (atomic mkdir + git init + eerste commit + .gitignore) | ✅ | `backend/app/services/repo_bootstrap_service.py`, `repo_bootstrap.py` |
| B | `gh repo create`-flow + key-migratie (`migrate_project_keys`) | ✅ | zelfde service-laag |
| B | `TemplateService` (`empty` / `python-fastapi` / `react-vite-ts`) | ✅ | `backend/app/services/templates/` |
| B | `BlueprintApply`-engine (motor achter A's `BlueprintService.apply`) | ✅ | `blueprint/apply_engine.py` |
| B | `cockpit-baseline`-blueprint (8 universele proces-skills) | ✅ | `blueprint/baseline_blueprint.yaml` |
| B | `BootstrapPolicy`-dataclass + rationale-doc | ⚠️ **ontworpen, niet aangesloten** | `backend/app/services/bootstrap_policy.py` — docstring zegt letterlijk *"intentionally not imported by any production module yet"* |
| C — Portfolio | `projects.kind` (meta/product/archived) + `priority`-kolom | ✅ | `backend/app/models/database.py:30-33` |
| C | `PortfolioService` + `GET /portfolio/overview` + `PortfolioPage.tsx` | ✅ | `backend/app/services/portfolio_service.py`, `frontend/src/features/portfolio/` |
| C | Portfolio-cap in `run_dispatch_tick` | ⚠️ **geïmplementeerd, uit by default** | `dispatch.py:3906` gate op `settings.portfolio_cap_enabled` (`config.py:92`, default `False`) |
| C | Portfolio-cap-policy (waarde/scope/failure-mode) + migratie-plan (docs) | ✅ | `docs/cockpit/portfolio-policy.md`, `docs/cockpit/portfolio-migration-plan.md` |
| C | Stale-project-detectie | ✅ | (follow-up card Done; niet apart heronderzocht) |
| D — Veiligheid | I4b pad-allowlist voor `.mcp.json`-write | ✅ | `MCPConfigService` |
| D | Sandcastle resource-caps + `read_only_rootfs` + `network_mode` + image-bootstrap | ✅ | `backend/app/models/sandcastle.py:30-34` |
| D | `SecretStore` (age-file) + REST CRUD | ✅ | `backend/app/services/secrets_store.py`, `/api/v1/secrets` |
| D | Env-injectie-filter in `spawn_session` (geen `os.environ`-merge meer) | ✅ | `backend/app/services/runs/spawn.py` + `cc_spawn.py`, kwargs `project_key`/`runtime`/`extra_env` |
| D | `ProjectSecurityProfile` (risk_class-model) + REST + frontend-editor | ✅ | `backend/app/services/security_profile_service.py`, `/security`-pagina |
| D | `CITemplateService` (python-strict/node-strict/minimal) | ✅ (REST-only) | `backend/app/services/ci_templates/`, `GET/POST /api/v1/ci/templates` |
| D | `RunService` + preview-URL + "Run this branch" | ✅ | `backend/app/services/run_service.py` |
| D | `security_audit`-tabel + `GET /api/v1/security/audit` | ✅ | `backend/app/services/security_audit_service.py` |
| D | `DeployTarget` + GHCR-MVP | ✅ | `backend/app/services/deploy.py`, `/api/v1/deploy` |
| D | `risk_class`-taxonomie (design-doc) | ✅ | `docs/cockpit/risk-class-taxonomie.md` |

Van de **35 Backlog-follow-ups** die de vier facetten samen filedden
(A: 8, B: 7, C: 8, D: 12) zijn er dus feitelijk **33 volledig
geïmplementeerd en gemerged**, en zijn er **2 bewust als los onderdeel
opgeleverd zonder de laatste integratiestap** (zie §2.2). Geen enkele
is `not_feasible` gesloten of blijft onbehandeld in Backlog staan.

### 2.2 Wat nog structureel ontbreekt — de échte resterende gaten

Dit zijn **geen nieuwe** bevindingen — het zijn de eigen open vragen
van de facetten (met name D§4.3/§5.2 en B§4.3), nu bevestigd als nog
steeds open door de huidige code te lezen:

1. **`BootstrapPolicy` is niet aangesloten op `RepoBootstrapService`/
   `InceptionService`.** Het beleid (autodispatch uit bij geboorte,
   geen CI bij geboorte, MIT-license-default, …) is volledig
   ontworpen en getest als losstaand type (facet B, follow-up #5), maar
   wordt door geen enkele productie-aanroep geconsumeerd — de eigen
   docstring zegt dit expliciet. Een nieuw project krijgt dus vandaag
   ad-hoc defaults uit `RepoBootstrapService`'s eigen code-paden, niet
   de gedeliberateerde policy.
2. **`ProjectSecurityProfile`/`risk_class` stuurt nog niets aan.**
   `get_skip_permissions()` (`backend/app/kanban/dispatch.py:293-297`)
   retourneert nog onvoorwaardelijk `True` als er geen expliciete
   `KanbanMeta`-override is — er is geen pad dat een `product-staging`-
   risk_class naar `skip_permissions=False` vertaalt, zoals facet D
   zelf aanbeval (D§5.2: *"`skip_permissions` default voor
   product-projecten: **False** — een agent die niet 'alleen-lezen-mag'
   op een splinternieuw project is een aanvalsvector"*). Evenzo is
   `SecretStore.get(project_key, …)` nog niet de bron van
   `spawn_session`'s `extra_env`-parameter — dat gat benoemt facet D's
   eigen doc al expliciet in §4.5 ("*de `extra_env`-parameter vult
   vandaag het gat dat `SecretStore.get(...)` straks vult*").
3. **Portfolio-cap staat uit.** `settings.portfolio_cap_enabled = False`
   (`config.py:92`) — de mechaniek is klaar en getest, maar een
   product-project kan vandaag nog steeds het hele memory-budget
   opeisen totdat iemand de flag omzet (bewust gefaseerd, zie C's
   follow-up-comment: *"achter een feature-flag zodat de rollout
   gefaseerd kan"*).
4. **CI-bootstrap bij geboorte is een bewuste nee, geen gat.**
   `BootstrapPolicy.ci_bootstrap = False` — `CITemplateService` bestaat
   en werkt (REST-oppervlak), maar wordt niet automatisch aangeroepen
   tijdens `RepoBootstrapService`. Dit is een **expliciete
   beleidskeuze** (bootstrap-policy.md §1.5: *"deferred to facet-D
   CITemplateService"*, wat in de praktijk betekent: pas als de
   BootstrapPolicy-wiring (gat #1) er is, kan deze knop automatisch
   omgaan).

Facet D's eigen kernbevinding blijft dus letterlijk actueel: *"de
resource-cap, de env-isolatie en de auth-default zijn de drie
single-line fixes met de hoogste impact"* — het verschil is dat de
onderliggende **mechanismen nu bestaan**; wat ontbreekt is de laatste
schakel die ze *default aan* zet voor een nieuw product-project.

### 2.3 Uit expliciete facet-scope gehouden (blijft zo, geen actie hier)

Ter volledigheid — dit zijn geen gaten die deze synthese toevoegt, maar
bewuste, herbevestigde out-of-scope-grenzen uit de facetten zelf:
multi-tenancy/RBAC (D§5.3/§7), een echte network-egress-proxy (D§5.3),
compliance-frameworks (D§5.3), MCP-server-trust-model (D§7),
headless-SDK-transport (eigenaar blijft
`orchestration-substrate-decision.md`), cross-device sync voor
portfolio-werk (C§8, bevroren), en bredere deploy-targets dan GHCR
(D§4.7 p.4 — "de grote stap", bewust ná F1+F2).

## 3. Geprioriteerde roadmap

Omdat vrijwel de volledige oorspronkelijke roadmap al is geland, is de
**resterende** roadmap kort en betreft uitsluitend het **aan elkaar
knopen** van reeds bestaande, correct werkende onderdelen — geen nieuwe
services.

| # | Item | Prioriteit | Waarom nu | Bron |
|---|---|---|---|---|
| 1 | `BootstrapPolicy` daadwerkelijk laten consumeren door `RepoBootstrapService`/`InceptionService` (autodispatch-uit-bij-geboorte, first-commit-template, license-default etc. worden dan echt toegepast i.p.v. alleen gedocumenteerd) | **Hoog** | Zonder deze stap is elke andere policy-beslissing (incl. #2) dode letter voor nieuwe projecten | facet B §4.3, follow-up #5 |
| 2 | Risk-class-gestuurde defaults aansluiten: `ProjectSecurityProfile.risk_class` → `skip_permissions`-default + `default_transport` + `SecretStore`-gevoede `extra_env` in `spawn_session` | **Hoog** | Dit is letterlijk de drie "single-line fixes met hoogste impact" die facet D al identificeerde; de bouwstenen bestaan, de koppeling niet | facet D §4.3/§4.5/§5.2, follow-ups #5/#6 |
| 3 | Besluit + uitvoering: `portfolio_cap_enabled` omzetten naar `True` (na validatie dat de cap-waarde/failure-mode uit `portfolio-policy.md` in de praktijk klopt) | **Middel** | Mechaniek is klaar; dit is een go/no-go, geen bouwwerk | facet C §5, follow-up #3/#4 |
| 4 | Besluit: wanneer `ci_bootstrap` aan mag (afhankelijk van #1) — bijv. altijd aan voor `python-fastapi`/`react-vite-ts`-templates, uit voor `empty` | **Middel** | Volgt direct uit #1; zonder wiring van BootstrapPolicy is dit nog niet aan te zetten | facet B §4.3 p.4, facet D §4.6 |
| 5 | Bredere Deploy-targets (Vercel/Fly/Render), echte egress-allowlist, multi-tenancy/RBAC | **Laag / bewust later** | Expliciet uit scope gehouden door facet D zelf; vereist een nieuwe policy-ronde, geen "vergeten" item | facet D §4.7 p.4, §5.3 |

**Samengevat:** de synthese-aanbeveling is **niet** "bouw meer" maar
**"schakel aan wat er al ligt"** — items 1 en 2 zijn de enige twee
stukken implementatiewerk die nog nodig zijn om van "kan een
product-project bouwen met dezelfde permissieve defaults als het
meta-project" naar "kan een product-project bouwen met een veilig,
risk-class-passend beleid" te gaan. Items 3 en 4 zijn beleidsbesluiten
op reeds werkende schakelaars. Item 5 is een bewuste volgende
iteratie, geen huidig gat.

## 4. Referenties

- Facet-docs: [`product-inceptie-pipeline.md`](./product-inceptie-pipeline.md) (A),
  [`repo-provisioning-bootstrap.md`](./repo-provisioning-bootstrap.md) (B),
  [`portfolio-orchestratie.md`](./portfolio-orchestratie.md) (C),
  [`veilig-bouwen-en-uitleveren.md`](./veilig-bouwen-en-uitleveren.md) (D).
- Beleidsdocs die uit de follow-ups zijn opgeleverd:
  [`bootstrap-policy.md`](./bootstrap-policy.md),
  [`portfolio-policy.md`](./portfolio-policy.md),
  [`portfolio-migration-plan.md`](./portfolio-migration-plan.md),
  [`risk-class-taxonomie.md`](./risk-class-taxonomie.md),
  [`blueprints-typology.md`](./blueprints-typology.md),
  [`brainstorm-to-impediment-bridge.md`](./brainstorm-to-impediment-bridge.md),
  [`portfolio-security-handoff.md`](./portfolio-security-handoff.md),
  [`ci-templates.md`](./ci-templates.md).
- Belangrijkste (inmiddels van het bord geruimde, maar in `kanban_ops`
  herleidbare) follow-up-kaarten: facet A — `c33b2f14` (inceptie-pipeline
  umbrella), `0260dbcd` (`create_project_from_intake`); facet B —
  `dceb60ab` (`RepoBootstrapService`), `02b07a0f` (`BootstrapPolicy`-design);
  facet C — `86c96fbd` (`kind`/`priority`-kolom), `567fe02d`
  (portfolio-cap in dispatch); facet D — `828b7b25` (`SecretStore`),
  `b5c71e0c` (env-injectie-filter), `ea9b824f` (`ProjectSecurityProfile`).
- Ouderkaart: `8db831a0df6d42689c5b26325b6cbecc` (verwijderd van het bord
  door Clear-Done; comments gereconstrueerd uit `kanban_ops`).
