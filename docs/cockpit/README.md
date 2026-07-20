---
title: "`docs/cockpit/` — de canonieke spec-boom (index)"
type: index
status: active
---

# `docs/cockpit/` — de canonieke spec-boom (index)

> **Dit is de single source of truth voor "hoe werkt de fork Claude Cockpit vandaag".**
> Bij twijfel of overlap: **lees het cockpit-document eerst.** Er zijn nog twee andere
> doc-bomen, maar geen van beide is leidend:
>
> | Boom | Rol | Leidend? |
> |---|---|---|
> | **`docs/cockpit/`** | Langlevende fork-architectuur, ontwerp, beslissingen, follow-ups. Topic-naam, niet gedateerd. | **Ja — canoniek.** |
> | `docs/superpowers/{plans,specs}/` | Werkoutput van `superpowers:writing-plans` / `brainstorming`. Eén gedateerd paar per taak. **Promoot naar `docs/cockpit/` zodra het werk landt** — zie [`../superpowers/README.md`](../superpowers/README.md) voor het promotie-contract + de ledger. | Nee — werkoutput. |
> | `docs/plans-legacy/` | Pre-fork claude-deck plans (gearchiveerd 2026-07-10). | **Nee — legacy**, zie [`../plans-legacy/README.md`](../plans-legacy/README.md). |
>
> De achtergrond bij deze consolidatie staat in
> [`spec-driven-development-fase-0-decision.md`](./spec-driven-development-fase-0-decision.md)
> en de bredere analyse in [`spec-driven-development-analysis.md`](./spec-driven-development-analysis.md).

## Leidend document per feature

Per functioneel gebied: welk cockpit-document is canoniek, en welke superpowers-plan/spec
is de uitvoerings-/ontwerp-tegenhanger (referentie, niet leidend).

| Feature / gebied | Leidend document (canoniek) | Superpowers-tegenhanger (referentie) |
|---|---|---|
| **Naamgeving / glossary** | [`terminology.md`](./terminology.md) | — |
| **Oriëntatie / repo-map** | [`00-orientation.md`](./00-orientation.md) | — |
| **Start een nieuw spec-driven project** (intake → Promote → geboorte) | [`new-project-startup-flow.md`](./new-project-startup-flow.md) | `specs/...` van de intake-pipeline zijn via dat doc ontsloten |
| **Scheduled messages** | [`fase-2-spec.md`](./fase-2-spec.md) (spec) + [`fase-2-plan.md`](./fase-2-plan.md) (plan) | `specs/2026-06-13-scheduled-session-resume-design.md`, `plans/2026-06-14-scheduled-session-resume.md` |
| **Scheduled — runtime-checklist** | [`fase-1-validation.md`](./fase-1-validation.md) | — |
| **Kanban v1 (passief bord)** | [`kanban-spec.md`](./kanban-spec.md) + [`kanban-plan.md`](./kanban-plan.md) | — |
| **Kanban auto-dispatch** | [`kanban-dispatch-spec.md`](./kanban-dispatch-spec.md) | `specs/2026-06-15-kanban-agents-design.md`, `specs/2026-06-29-kanban-dispatch-transport-design.md`, `specs/2026-06-27-kanban-mcp-robustness-design.md`, `specs/2026-07-03-card-edit-provider-dropdown-design.md` |
| **Kanban model-override** | [`kanban-model-override.md`](./kanban-model-override.md) | `specs/2026-07-10-kanban-model-override-design.md`, `plans/2026-07-10-kanban-model-override.md` |
| **Multi-agent kanban** | [`multi-agent-kanban.md`](./multi-agent-kanban.md) | `specs/2026-07-08-multi-agent-kanban-design.md`, `plans/2026-07-08-multi-agent-kanban.md` |
| **Agent Bridge (Runs)** | [`agent-bridge.md`](./agent-bridge.md) | `specs/2026-05-29-agent-bridge-bedrock-platform-design.md`, `specs/2026-06-12-agent-bridge-session-rename-design.md`, `specs/2026-06-12-resume-worktree-sessions-design.md`, `specs/2026-06-29-agent-bridge-image-paste-design.md` |
| **Subscriptions-pagina** | [`subscriptions.md`](./subscriptions.md) | `specs/2026-07-04-minimax-providers-page-design.md` ¹ |
| **Abonnement-flexibiliteit (usage-aware routing)** | [`subscription-flexibiliteit-analyse.md`](./subscription-flexibiliteit-analyse.md) | — |
| **Subscription-pool × dispatch × kolommen (bevindingen)** | [`subscription-pool-dispatch-analyse.md`](./subscription-pool-dispatch-analyse.md) | — |
| **Skill stats** | [`skill-stats.md`](./skill-stats.md) | `specs/2026-06-27-skill-stats-design.md` |
| **Beslis-register (alle genomen beslissingen)** | [`decisions.md`](./decisions.md) | — |
| **Kanban follow-up pool** | [`kanban-followups.md`](./kanban-followups.md) | — |
| **Kanban string-conventies (vast kolommen, comment-prefixes, deliverable-kinds)** | [`kanban-conventions.md`](./kanban-conventions.md) | — |
| **Kaart-referenties (id kopiëren, deep-link, klikbare verwijzingen)** | [`card-references-analysis.md`](./card-references-analysis.md) | — |
| **Agent Mail** | [`agent-mail-spec.md`](./agent-mail-spec.md) | `plans/2026-07-08-agent-mail-implementation.md` |
| **Pane-gerichte attentie** | [`pane-attention-spec.md`](./pane-attention-spec.md) + [`pane-attention-plan.md`](./pane-attention-plan.md) | — |
| **Sandcastle** | [`sandcastle.md`](./sandcastle.md) + [`sandcastle-integration-plan.md`](./sandcastle-integration-plan.md) | — |
| **Spec-driven development (SSOT)** | [`spec-driven-development-analysis.md`](./spec-driven-development-analysis.md) + [`spec-driven-development-fase-0-decision.md`](./spec-driven-development-fase-0-decision.md) | `specs/2026-07-05-code-drift-detection-design.md`, `plans/2026-07-05-code-drift-detection.md` |
| **Work-type → routing** | [`work-type-routing-analysis.md`](./work-type-routing-analysis.md) | — |
| **Sync / HLC-laag** | [`sync-hlc-freeze-vs-prune.md`](./sync-hlc-freeze-vs-prune.md) | — |

> ¹ `specs/2026-07-08-subscription-usage-leftover-design.md` is **SUPERSEDED**
> (kanban-card `64343a81…`, 2026-07-15) en staat niet meer als actieve
> superpowers-tegenhanger in deze tabel — de cockpit-kant
> ([`subscriptions.md`](./subscriptions.md), sectie *Per-provider usage / quota*)
> is de geleverde, canonieke vorm. De spec is bewaard voor historische context;
> raadpleeg het SUPERSEDED-banner bovenaan vóór je er tegen implementeert.

### Beslisdocumenten (ADR-achtig, geen feature-spec)

> **Zoek je "is X al beslist, en wat kwam eruit?" — begin bij het beslis-register:
> [`decisions.md`](./decisions.md).** Dat is de canonieke, chronologische index over álle
> beslissingen (datum, vraag, uitkomst, doc-link, kaart-id). De lijst hieronder is de
> thematische ingang op dezelfde documenten; het register is de chronologische.
> `scripts/check-decision-register.sh` bewaakt dat elk `*-decision.md` in het register staat.

Deze cockpit-documenten leggen een **richtingsbeslissing** vast; ze zijn canoniek voor
"waarom hebben we X wel/niet gedaan":

- [`reviewer-agent-decision.md`](./reviewer-agent-decision.md) — reviewer-agent + review-kolom.
- [`reopen-completed-decision-analysis.md`](./reopen-completed-decision-analysis.md) — completed kaart heropenen met context.
- [`updates-feature-decision.md`](./updates-feature-decision.md) — self-update-feature.
- [`plans-feature-decision.md`](./plans-feature-decision.md) — waarom de Plans-pagina leeg is (verweesde `kanban_plans`-tabel, geen live writer) + aanbeveling om 'm te herbestemmen tot read-only mensvenster op de spec-/plan-laag (analyst-plan-attachments + `docs/cockpit/`-docs) en de tabel uit te faseren.
- [`upstream-agent-teams-decision.md`](./upstream-agent-teams-decision.md), [`upstream-docker-removal-decision.md`](./upstream-docker-removal-decision.md), [`upstream-presence-removal-decision.md`](./upstream-presence-removal-decision.md) — upstream-overname-keuzes.
- [`recurring-cadence-proposal.md`](./recurring-cadence-proposal.md) — cadans zelfverbeteringsonderzoek.
- [`build-prioriteiten-analyse.md`](./build-prioriteiten-analyse.md) — geordende bouw-prioriteitsstack (P0–P4) + externe ecosysteem-scan (Spec Kit, OpenHands, sandbox-consensus, Anthropic-abonnementsbeleid).
- [`openhands-analyse.md`](./openhands-analyse.md) — diepere OpenHands-analyse: wat overnemen/leren (ACP-transport, path/keyword-skills, event-automations) + herziening van de "geen abonnementen"-premisse.
- [`acp-transport-decision.md`](./acp-transport-decision.md) — go/no-go op ACP vs. per-CLI stream-json-parser als gestructureerd transport achter `SpawnTransport` (verenigt de headless-transportbeslissing uit `orchestration-substrate-decision.md` §6): conditionele go op stream-json eerst, ACP-isomorf event-model, ACP-adapter gepoort op tweede-provider-onboarding.
- [`spike-claude-code-model-switching.md`](./spike-claude-code-model-switching.md), [`spike-declarative-workflow-orchestration.md`](./spike-declarative-workflow-orchestration.md) — spikes/ADR's.
- [`analyse-orphaned-followups-audit.md`](./analyse-orphaned-followups-audit.md) — audit van voltooide analyses zonder aangemaakte vervolgkaarten: inventaris + verdict per analyse, 10 orphan-kaarten alsnog aangemaakt (per-provider pause, transport-laag, portfolio-migratie), 2 open beslissingen naar Impediment.
- [`jira-lessen-analyse.md`](./jira-lessen-analyse.md) — kritische analyse "wat leren we van JIRA": drie primitieven die reële pijn oplossen (getypeerde bi-directionele links, zichtbaarheid van bestaande `depends_on`/parent-relaties, deep-linkbare full-page kaartweergave) + geprioriteerde aanbevelingen (P0–P2) en expliciete non-doelen (sprints/story-points/custom-field-schema's bewust niet overnemen).

<!-- BEGIN GENERATED DOC INDEX (scripts/generate-doc-index.py) — DO NOT EDIT BY HAND -->

## Volledige index (gegenereerd)

> **Afgeleid uit de frontmatter — niet met de hand bewerken.** Regenereer met `scripts/generate-doc-index.py`; `scripts/generate-doc-index.py --check --strict` bewaakt de drift. Dekt **alle 90 docs** (elke `docs/cockpit/*.md`), gegroepeerd op `type` met een `status`-badge.

### Index (2)

| Document | Status |
|---|---|
| [`docs/cockpit/` — de canonieke spec-boom (index)](./README.md) | 🟢 active |
| [Beslis-register — alle genomen productbeslissingen (index)](./decisions.md) | 🟢 active |

### Reference (24)

| Document | Status |
|---|---|
| [Claude Cockpit — oriëntatie (lees dit eerst)](./00-orientation.md) | 🟢 active |
| [Trigger-poort: ACP-adaptertransport (§6 kaart 5) — status bij premature dispatch](./acp-transport-trigger-gate.md) | 🟡 proposed |
| [Agent Bridge — spawn, terminal-relay & per-sessie configuratie](./agent-bridge.md) | 🟢 active |
| [Blueprints — taxonomie van `project_blueprint`-archetypes](./blueprints-typology.md) | 🟢 active |
| [ProjectBootstrapPolicy — de \"cockpit-defaults\" van repo-bootstrap](./bootstrap-policy.md) | 🟢 active |
| [Brainstorm-to-impediment-bridge — van real-time dialogue naar `report_impediment`-flows](./brainstorm-to-impediment-bridge.md) | 🟢 active |
| [CITemplateService — drie GitHub-Actions-templates voor pasgeboren projecten](./ci-templates.md) | 🟢 active |
| [Fase 1 — Validatiechecklist (werkt claude-deck onder WSL?)](./fase-1-validation.md) | 🟢 active |
| [Isolated component preview (light + dark screenshot)](./isolated-component-preview.md) | 🟢 active |
| [Kanban-DB conventions](./kanban-conventions.md) | 🟢 active |
| [Kanban — known follow-ups (post-v1)](./kanban-followups.md) | 🟢 active |
| [Kanban model-override — card/column/persona model-precedentie](./kanban-model-override.md) | 🟢 active |
| [Multi-agent kanban — smoke-test cookbook](./multi-agent-kanban.md) | 🟢 active |
| [Portfolio-cap policy: waarde, scope, failure-mode](./portfolio-policy.md) | 🟢 active |
| [Product-inceptie: van gesprek naar spec + plan die een project seedt](./product-inceptie-pipeline.md) | 🟢 active |
| [Repo-provisioning & project-bootstrap: van kanban-artefact naar werkende app-repo](./repo-provisioning-bootstrap.md) | 🟢 active |
| [`risk_class`-taxonomie + classifier voor `ProjectSecurityPolicy`](./risk-class-taxonomie.md) | 🟢 active |
| [Sandcastle Integration](./sandcastle.md) | 🟢 active |
| [Skill Stats — per-project skill-gebruik](./skill-stats.md) | 🟢 active |
| [Structured events / `headless_run` — ACP-isomorf event-schema](./structured-events-schema.md) | 🟢 active |
| [Subscriptions-pagina — credential-beheer & per-provider quota](./subscriptions.md) | 🟢 active |
| [Terminology — canonieke woordenlijst](./terminology.md) | 🟢 active |
| [Test-doubles convention — patch where the consumer looks](./test-doubles-convention.md) | 🟢 active |
| [Veilig bouwen & uitleveren van willekeurige apps — isolatie, secrets, CI en run/deploy](./veilig-bouwen-en-uitleveren.md) | 🟢 active |

### Spec (6)

| Document | Status |
|---|---|
| [Agent Mail — upstream sync (adapted port)](./agent-mail-spec.md) | 🟢 active |
| [Fase 2 — Spec: Scheduled messages](./fase-2-spec.md) | 🟢 active |
| [Kanban auto-dispatch — spec](./kanban-dispatch-spec.md) | 🟢 active |
| [Kanban — Spec: per-project bord met agent-zelfbediening](./kanban-spec.md) | 🟢 active |
| [Spec — Pane-gerichte attentie: Bridge ↔ Presence exacte koppeling](./pane-attention-spec.md) | 🟢 active |
| [`spec_doc`-producent + B↔C-join — ontwerp (leaf design-deliverable)](./spec-doc-producer-design.md) | 🟢 active |

### Plan (5)

| Document | Status |
|---|---|
| [Claude Cockpit — Fase 2: Scheduled Messages — Implementation Plan](./fase-2-plan.md) | 🟢 active |
| [Kanban Implementation Plan](./kanban-plan.md) | 🟢 active |
| [Pane-Targeted Attention Implementation Plan](./pane-attention-plan.md) | 🟢 active |
| [Portfolio-migratie: bestaande projecten bij de kind-introductie](./portfolio-migration-plan.md) | 🟢 active |
| [Sandcastle Integration Plan — Claude Cockpit](./sandcastle-integration-plan.md) | 🟢 active |

### Decision (27)

| Document | Status |
|---|---|
| [9Router-integratie — analyse & beslissing](./9router-integratie-analyse.md) | 🔵 decided |
| [Beslissing: ACP (Agent-Client Protocol) als gestructureerd transport achter `SpawnTransport`](./acp-transport-decision.md) | 🔵 decided |
| [Beslissing — De analyse-levenscyclus op het bord: parkeerkolom, subtaak-rollup, statusvocabulaire](./analyse-levenscyclus-decision.md) | 🔵 decided |
| [Beslissing — De analyse-fase krijgt een afdwingbaar uitkomst-contract](./analysis-outcome-contract-decision.md) | 🔵 decided |
| [Beslissing — Leaf-spike maakt zijn eigen vervolgkaarten aan (autonomie i.p.v. review-round-trip)](./autonomous-leaf-spike-followup.md) | 🔵 decided |
| [Beslissing — Code-kennisgraaf (Understand-Anything) voor code-navigatie](./code-knowledge-graph-navigation-decision.md) | 🔵 decided |
| [Beslissing: database-plafond — SQLite-concurrency-grens vs. Postgres](./database-scaling-decision.md) | 🔵 decided |
| [Beslissing: headless SessionEnd-retro voor niet-gedispatchte sessies](./headless-session-retro-decision.md) | 🔵 decided |
| [Beslissing: human-takeover-UX voor headless sessies](./human-takeover-headless-decision.md) | 🔵 decided |
| [Interview-/intake-authoring-flow: van vrij gesprek naar ingevulde intake-kaart](./intake-authoring-flow-decision.md) | 🔵 decided |
| [Beslissing: `intake_kind` nu toevoegen, of YAGNI?](./intake-kind-decision.md) | 🔵 decided |
| [Beslissing: orchestratie-substraat — tmux + CLI-scraping vs. Claude Agent SDK / headless](./orchestration-substrate-decision.md) | 🔵 decided |
| [Per-persona MCP-tool-allowlist — analyse & beslissing](./per-persona-mcp-allowlist-decision.md) | 🔵 decided |
| [Plans-feature — analyse & richting (leaf spike)](./plans-feature-decision.md) | 🟡 proposed |
| [Completed beslissing weerleggen + heropenen met context — beslisdocument](./reopen-completed-decision-analysis.md) | 🔵 decided |
| [Reviewer-agent + review-kolom — wenselijk? Trade-off + beslissing (REVISED²)](./reviewer-agent-decision.md) | 🔵 decided |
| [Per-kaart run-ledger — scope & ontwerp — beslissing](./run-ledger-decision.md) | 🔵 decided |
| [Beslissing: schema-migratiesysteem — `create_all` + handmatige renames vs. Alembic](./schema-migrations-decision.md) | 🔵 decided |
| [Spec-driven development — Fase 0 beslissing (consolidatie spec-boom)](./spec-driven-development-fase-0-decision.md) | 🔵 decided |
| [Spike: Claude Code model-switching (Anthropic ↔ MiniMax) — ADR](./spike-claude-code-model-switching.md) | 🔵 decided |
| [Spike: declaratieve multi-agent workflow-orchestratie — ADR](./spike-declarative-workflow-orchestration.md) | 🔵 decided |
| [Sync + HLC-laag: bevriezen vs. snoeien — trade-off + beslissing](./sync-hlc-freeze-vs-prune.md) | 🔵 decided |
| [Synchrone sub-agent-delegatie vs. async kanban-decompositie — beslisdocument](./sync-vs-async-delegation-decision.md) | 🔵 decided |
| ['Updates' (self-update) feature — past die nog bij Cockpit's missie?](./updates-feature-decision.md) | 🔵 decided |
| [Upstream Agentic Agent Teams — adopt or not? Trade-off + beslissing](./upstream-agent-teams-decision.md) | 🔵 decided |
| [Upstream verwijderde Docker-support — overnemen? Trade-off + beslissing](./upstream-docker-removal-decision.md) | 🔵 decided |
| [Upstream verwijderde Presence — overnemen? Trade-off + beslissing](./upstream-presence-removal-decision.md) | 🔵 decided |

### Analysis (26)

| Document | Status |
|---|---|
| [Audit: voltooide analyses zonder aangemaakte vervolgkaarten](./analyse-orphaned-followups-audit.md) | 🟢 active |
| [Bouw-prioriteiten: wat eerst, wat te integreren, wat kan wachten](./build-prioriteiten-analyse.md) | 🟢 active |
| [Kaarten refereerbaar maken — analyse](./card-references-analysis.md) | 🟢 active |
| [Communicatie & weergave — analyse](./communicatie-en-weergave-analyse.md) | 🟢 active |
| [Controlled auto-dispatch — selectief dispatchen per soort werk](./controlled-auto-dispatch-analysis.md) | 🟢 active |
| [Analyse — verweesde `depends_on` blokkeren kaarten permanent en onzichtbaar](./dangling-depends-on-analyse.md) | 🟢 active |
| [Spike: headless `stream-json`-transport (Claude) achter `SpawnTransport`](./headless-stream-json-transport-spike.md) | 🔵 decided |
| [Routing van intake-kaarten — analyse & ontwerpbesluit](./intake-card-routing-analysis.md) | 🟢 active |
| [Wat kunnen we leren van JIRA? — kritische analyse](./jira-lessen-analyse.md) | 🟢 active |
| [Kennisopbouw & navigatie — hoe structureren we de docs-berg](./knowledge-structure-navigation-analysis.md) | 🟡 proposed |
| [Nieuw project spec-driven starten — is dit al ondersteund?](./new-project-startup-flow.md) | 🟢 active |
| [Analyse — OpenHands: wat kunnen we overnemen of leren?](./openhands-analyse.md) | 🟢 active |
| [Orchestration-flow — is onze flow robuust genoeg? — analyse](./orchestration-flow-analysis.md) | 🟢 active |
| [Cockpit als app-fabriek: consolidatie van vier facet-analyses](./platform-als-app-factory.md) | 🟢 active |
| [Portfolio-orchestratie: meerdere product-apps beheren naast het meta-platform](./portfolio-orchestratie.md) | 🟢 active |
| [Portfolio ↔ security overdracht — drie open vragen voor facet D](./portfolio-security-handoff.md) | 🟡 proposed |
| [Analyse — Volgbaarheid van het project voor de product owner](./product-owner-volgbaarheid-analyse.md) | 🟢 active |
| [Terugkerende cadans voor het zelfverbeteringsonderzoek — voorstel](./recurring-cadence-proposal.md) | 🟡 proposed |
| [Test-gespawnde agent-bridge-sessies blokkeren auto-dispatch — analyse](./spawn-test-bridge-sessions-analyse.md) | 🟢 active |
| [Spec-driven development als single source of truth — analyse](./spec-driven-development-analysis.md) | 🟢 active |
| [Spike — per-sessie credential-/HOME-isolatie voor meerdere accounts binnen één vendor](./spike-same-vendor-multi-account-isolation.md) | 🔵 decided |
| [Analyse — Flexibel & maximaal gebruik van abonnementen (usage-aware dispatch-routing)](./subscription-flexibiliteit-analyse.md) | 🟢 active |
| [Analyse — subscription-pool × auto-dispatcher × kolom-toewijzing](./subscription-pool-dispatch-analyse.md) | 🟢 active |
| [Analyse — Inzicht in verbruik per subscription (inkantelen vs. Langfuse)](./subscription-verbruik-inzicht-analyse.md) | 🟢 active |
| [Token-optimalisatie — analyse & aanbevelingen](./token-optimization-analysis.md) | 🟢 active |
| [Card work-type → agent-routing — analyse & aanbevelingen](./work-type-routing-analysis.md) | 🟢 active |

<!-- END GENERATED DOC INDEX -->
## Regels

1. **Nieuw ontwerp-/besliswerk voor de fork** hoort in `docs/cockpit/` (topic-naam, niet gedateerd),
   óf begint als superpowers-werkoutput die **promoot** zodra het werk landt.
2. **Legacy niet aanraken**: schrijf niets nieuws in `docs/plans-legacy/`.
3. **Promotie is zichtbaar én controleerbaar**: elke superpowers-plan staat in de ledger van
   [`../superpowers/README.md`](../superpowers/README.md); `scripts/check-superpowers-promotions.sh`
   flag't (advies, niet-blokkerend) elke plan/spec die nog niet in de ledger geregistreerd is.
