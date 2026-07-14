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
| **Scheduled messages** | [`fase-2-spec.md`](./fase-2-spec.md) (spec) + [`fase-2-plan.md`](./fase-2-plan.md) (plan) | `specs/2026-06-13-scheduled-session-resume-design.md`, `plans/2026-06-14-scheduled-session-resume.md` |
| **Scheduled — runtime-checklist** | [`fase-1-validation.md`](./fase-1-validation.md) | — |
| **Kanban v1 (passief bord)** | [`kanban-spec.md`](./kanban-spec.md) + [`kanban-plan.md`](./kanban-plan.md) | — |
| **Kanban auto-dispatch** | [`kanban-dispatch-spec.md`](./kanban-dispatch-spec.md) | `specs/2026-06-15-kanban-agents-design.md`, `specs/2026-06-29-kanban-dispatch-transport-design.md`, `specs/2026-06-27-kanban-mcp-robustness-design.md`, `specs/2026-07-03-card-edit-provider-dropdown-design.md` |
| **Kanban model-override** | [`kanban-model-override.md`](./kanban-model-override.md) | `specs/2026-07-10-kanban-model-override-design.md`, `plans/2026-07-10-kanban-model-override.md` |
| **Multi-agent kanban** | [`multi-agent-kanban.md`](./multi-agent-kanban.md) | `specs/2026-07-08-multi-agent-kanban-design.md`, `plans/2026-07-08-multi-agent-kanban.md` |
| **Agent Bridge (Runs)** | [`agent-bridge.md`](./agent-bridge.md) | `specs/2026-05-29-agent-bridge-bedrock-platform-design.md`, `specs/2026-06-12-agent-bridge-session-rename-design.md`, `specs/2026-06-12-resume-worktree-sessions-design.md`, `specs/2026-06-29-agent-bridge-image-paste-design.md` |
| **Subscriptions-pagina** | [`subscriptions.md`](./subscriptions.md) | `specs/2026-07-04-minimax-providers-page-design.md`, `specs/2026-07-08-subscription-usage-leftover-design.md` |
| **Abonnement-flexibiliteit (usage-aware routing)** | [`subscription-flexibiliteit-analyse.md`](./subscription-flexibiliteit-analyse.md) | — |
| **Skill stats** | [`skill-stats.md`](./skill-stats.md) | `specs/2026-06-27-skill-stats-design.md` |
| **Kanban follow-up pool** | [`kanban-followups.md`](./kanban-followups.md) | — |
| **Kanban string-conventies (vast kolommen, comment-prefixes, deliverable-kinds)** | [`kanban-conventions.md`](./kanban-conventions.md) | — |
| **Agent Mail** | [`agent-mail-spec.md`](./agent-mail-spec.md) | `plans/2026-07-08-agent-mail-implementation.md` |
| **Pane-gerichte attentie** | [`pane-attention-spec.md`](./pane-attention-spec.md) + [`pane-attention-plan.md`](./pane-attention-plan.md) | — |
| **Sandcastle** | [`sandcastle.md`](./sandcastle.md) + [`sandcastle-integration-plan.md`](./sandcastle-integration-plan.md) | — |
| **Spec-driven development (SSOT)** | [`spec-driven-development-analysis.md`](./spec-driven-development-analysis.md) + [`spec-driven-development-fase-0-decision.md`](./spec-driven-development-fase-0-decision.md) | `specs/2026-07-05-code-drift-detection-design.md`, `plans/2026-07-05-code-drift-detection.md` |
| **Work-type → routing** | [`work-type-routing-analysis.md`](./work-type-routing-analysis.md) | — |
| **Sync / HLC-laag** | [`sync-hlc-freeze-vs-prune.md`](./sync-hlc-freeze-vs-prune.md) | — |

### Beslisdocumenten (ADR-achtig, geen feature-spec)

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

## Regels

1. **Nieuw ontwerp-/besliswerk voor de fork** hoort in `docs/cockpit/` (topic-naam, niet gedateerd),
   óf begint als superpowers-werkoutput die **promoot** zodra het werk landt.
2. **Legacy niet aanraken**: schrijf niets nieuws in `docs/plans-legacy/`.
3. **Promotie is zichtbaar én controleerbaar**: elke superpowers-plan staat in de ledger van
   [`../superpowers/README.md`](../superpowers/README.md); `scripts/check-superpowers-promotions.sh`
   flag't (advies, niet-blokkerend) elke plan/spec die nog niet in de ledger geregistreerd is.
