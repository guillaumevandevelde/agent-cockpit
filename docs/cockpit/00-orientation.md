---
title: "Agent Cockpit — oriëntatie (lees dit eerst)"
type: reference
status: active
---

# Agent Cockpit — oriëntatie (lees dit eerst)

Dit is een **fork van [adrirubio/claude-deck](https://github.com/adrirubio/claude-deck)**
met een eigen identiteit: **Agent Cockpit**. De `upstream` git-remote wijst naar claude-deck
(voeg later je eigen `origin` toe als je een GitHub-fork aanmaakt).

## Doel

Claude-deck levert al sessie-**monitoring** (Sessions / CC Bridge) — dat dekt "welke CC-sessie
wacht op mijn input" grotendeels. Daar bovenop bouwt Agent Cockpit twee samenhangende
lagen:

1. **Scheduled-messages** (vrijwel af) — boodschappen klaarzetten met een eenmalige
   **timer** of terugkerende **cron**, die op het geplande moment in een Claude
   Code-sessie worden geïnjecteerd (via tmux `send-keys`).
2. **Kanban als hoofdwerking** (huidige actieve track) — een poll-loop die Todo-kaarten
   autonoom claimt + spawnt, met multi-agent decompositie (analyst → executors) en
   Agent Mail voor cross-session coördinatie.

## Huidige staat

> **Wil je een nieuw app-idee spec-driven starten?** Het end-to-end-pad
> (de `new-app`-skill: interview → kaartloze geboorte van een nieuwe project-repo
> met geseede `.claude/`) staat in één doc:
> **[`docs/cockpit/new-project-startup-flow.md`](./new-project-startup-flow.md)**.
> Het Projects-scherm in de UI heeft dezelfde hint, met link naar dat doc.

### Scheduled-messages — fase 2 vrijwel af

Het implementatieplan staat in **`fase-2-plan.md`** (12 TDD-tasks). **Tasks 1–11 zijn
geïmplementeerd via TDD**: backend-tests groen, frontend build clean. Resterend is alleen
**Task 12 — runtime e2e** (en de **fase-1 runtime-checklist** in `fase-1-validation.md`):
dat vergt `docker compose up` + `claude` login (twee handmatige stappen).

**Open punt voor review:** `permission_mode` = `default|acceptEdits|bypass` (afgestemd op
echte `claude`-flags i.p.v. de spec-labels safe/accept-edits/autonomous).

### Kanban / multi-agent / agent-mail — actieve track

Bovenop het passieve kanban-bord (v1) is een volledig autonome werkstroom gebouwd:

- **Kanban auto-dispatch** — een APScheduler-poll die Todo-kaarten claimt als
  `agent:<session>`, naar Doing verplaatst, en een Claude Code-sessie in een git-worktree
  spawnt. Per-project opt-in (`autodispatch:<project_key>`). Zie
  `kanban-dispatch-spec.md`.
- **Multi-agent kanban** — voor kaarten die eerst analyse verdelen: de **analyst**-fase
  splitst een parent-kaart op in N kind-kaarten met een dependency-DAG en een
  `plan`-attachment; de dispatcher spawnt kind-kaarten pas zodra hun deps in `Done`
  staan. Zie `multi-agent-kanban.md` (smoke-test cookbook).
- **Agent Mail** — durable per-repo identiteit, structured messages tussen willekeurige
  sessies, inspectable mailbox-UI, en wakeability via tmux. Geport uit upstream
  (`adrirubio/claude-deck`), aangepast aan deze fork (geen preset/slot-laag). Zie
  `agent-mail-spec.md`.

De huidige open pool aan follow-ups + work-in-progress staat in
**`kanban-followups.md`** — dat is de ingang voor nieuwe kaarten.

### Kaartherkomst: `[research]`-kaarten in Backlog

Backlog-kaarten met titel-prefix **`[research] <samenvatting>`** komen **niet** uit de
analyst/executor-decompositiestroom, maar worden automatisch aangemaakt door de periodieke
**`market-research`-skill** (`.claude/skills/market-research/SKILL.md`, Step 5): een
naar-buiten-gerichte ecosysteem-scan (concurrerende open-source agent-platforms,
GitHub-topics, changelogs) die findings omzet in concrete, gescopete Backlog-kaarten.
De skill zet zelf al een passend `work_type` op elke kaart, dus ze routeren normaal — de
prefix is puur een herkomstlabel. Voor het cadans-/trigger-mechanisme (wanneer de scan
draait) zie **`recurring-cadence-proposal.md`**.

## Omgeving

- **WSL Ubuntu 25.10**, non-root user with sudo.
- Geïnstalleerd: **tmux 3.6**, **Node 20.20**, **git 2.53**, **claude CLI 2.1.173**.
- **Docker** via Docker Desktop WSL-integratie — moet aan staan voor deze distro.
- `claude` moet ingelogd zijn (run `claude` eenmalig en volg browser-auth).

## Draaien

```bash
# Optie A (gekozen): Docker
docker compose up -d        # UI op http://localhost:8000

# Optie B: manueel (fallback)
./scripts/install.sh        # venv + deps (Python 3.11+, Node 18+)
./scripts/dev.sh            # backend :8000 + frontend :5173
```

## Kernbeslissingen (scheduled-messages)

| Onderwerp | Keuze |
|---|---|
| Leveringsmodel | A — injecteren in live/ge-spawnde **interactieve** sessies (tmux `send-keys`) |
| Geen lopende sessie | **spawn** er een (tmux, in projectmap) |
| Bezige sessie | **wachten tot idle** (via CC-hooks), dan injecteren |
| Autonomie spawn | **per taak instelbare** permission-modus, veilige default |
| Scheduler | **in-process** in de FastAPI-backend (APScheduler + SQLite-jobstore) |
| Container-isolatie | uitgesteld (apart project) |

## Documenten

Voor de canonieke naamgeving van de kernbegrippen (Agent / Provider / CLI / Model / Run) zie
**`terminology.md`** — bron van waarheid voor naamgeving, vastgelegd door kind-kaart 1 van het
terminologie-parent-project. Code is inmiddels naar deze glossary gerenamed
(`services/agentic_cli/`, `services/runs/`, `RunGroup`/`RunMembership`, `provider_env`/`build_provider_env`); deze kaart
sweepet alleen de resterende docs-verwijzingen bij.

Er zijn **drie plan-/spec-bomen** in `docs/`, maar precies **één is canoniek**. De
volledige, afdwingbare index staat in **[`docs/cockpit/README.md`](./README.md)**; de
regel welke leidend is:

| Boom | Doel | Leidend? |
|---|---|---|
| `docs/cockpit/` | Langlevende fork-architectuur, ontwerp, beslissingen, follow-ups. Topic-naam, niet gedateerd. | **Ja — bron van waarheid voor "hoe werkt de fork vandaag".** Index: [`README.md`](./README.md). |
| `docs/superpowers/{plans,specs}/` | Werkoutput van de `superpowers:writing-plans` / `superpowers:brainstorming`-skills: één paar `<datum>-<naam>-design.md` + `<datum>-<naam>.md` per taak. **Promoot naar `docs/cockpit/` zodra het werk landt** — promotie-contract + ledger in [`../superpowers/README.md`](../superpowers/README.md), advies-check `scripts/check-superpowers-promotions.sh`. | Nee — taak-specifieke werkoutput. |
| `docs/plans-legacy/` | Pre-fork claude-deck plans (gearchiveerd 2026-07-10, voorheen `docs/plans/`). Geen kanban-inhoud. | **Nee — legacy, niet meer gebruiken.** Zie [`../plans-legacy/README.md`](../plans-legacy/README.md). |

Bij overlap tussen cockpit en superpowers (bijv. kanban, scheduled-messages, agent-mail):
**lees `docs/cockpit/` eerst**, en gebruik `docs/superpowers/` alleen om de TDD-stappen
of ontwerp-rationale van één specifieke taak te volgen. Een superpowers-plan dat in
`docs/cockpit/` is samengevat is geen "tweede waarheid" — het cockpit-document is
canoniek en het plan is de uitvoering ervan.

> **Consolidatie-achtergrond:** deze drie-bomen-regel is in Fase 0 van de spec-SSOT-lijn
> van proza naar *afdwingbaar* getild — zie
> [`spec-driven-development-fase-0-decision.md`](./spec-driven-development-fase-0-decision.md).

### Documenten-overzicht

- **`fase-1-validation.md`** — de runtime-checklist die we nog moeten afronden (sessie-discovery + send-keys + spawn op WSL bevestigen).
- **`fase-2-plan.md`** — 12 TDD-tasks voor de scheduled-messages feature; Tasks 1–11 geïmplementeerd, Task 12 = runtime e2e. Leidend voor scheduled-messages; superpowers-tegenhanger: `docs/superpowers/specs/2026-06-13-scheduled-session-resume-design.md` + `…/plans/2026-06-14-scheduled-session-resume.md`.
- **`fase-2-spec.md`** — het volledige ontwerp van de scheduled-messages feature.
- **`kanban-spec.md` + `kanban-plan.md`** — v1-bord (passief) en het plan waaruit het is voortgekomen. Geen recente superpowers-tegenhanger (v1 is gerealiseerd).
- **`kanban-dispatch-spec.md`** — auto-dispatcher: claim-before-spawn, worktree-isolatie, opt-in per project. Leidend voor de dispatch-laag. Gerelateerd: `…/superpowers/specs/2026-06-15-kanban-agents-design.md` (persona + shipmode) + `…/superpowers/specs/2026-06-29-kanban-dispatch-transport-design.md` (transport-seam).
- **`multi-agent-kanban.md`** — analyst-fase + plan-attachment + kind-kaart-dependencies (smoke-test cookbook). Leidend voor de multi-agent flow; gerelateerd: `…/superpowers/specs/2026-07-08-multi-agent-kanban-design.md` + `…/superpowers/plans/2026-07-08-multi-agent-kanban.md`.
- **`agent-mail-spec.md`** — Agent Mail: herkomst uit upstream, fork-aanpassingen, datamodel. Leidend; gerelateerd: `…/superpowers/plans/2026-07-08-agent-mail-implementation.md`.
- **`kanban-followups.md`** — de huidige open pool (work-type routing, sync-HLC, upstream-keuzes).
- **`kanban-conventions.md`** — canonieke string-conventies van het kanban-DB (vast kolommen `COLUMNS` vs dispatch-allow-list `_DISPATCH_COLUMNS`, `ensure_intake_column`/`ensure_analyst_column`-helpers, comment-prefix-contract voor `**Summary:** `/`**Impediment:** `/`**Resolution:** `/`**Revisit:** `/… , deliverable-kinds `pr`/`branch`/`commit`/`link`/`note`/`plan`/`plan_ref`/`spec`). **Lees dit vóór je een nieuwe vaste kolom introduceert of een Done/Impediment-comment post.** Validatiescript: `scripts/check-kanban-conventions.sh`.
- Plan-/projectpagina in de kennisvault (Windows-zijde, los van deze repo):
  `C:\dev\obsidian\Personal\Projects\Claude Cockpit\claude-cockpit.md`.
