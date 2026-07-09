# Claude Cockpit — oriëntatie (lees dit eerst)

Dit is een **fork van [adrirubio/claude-deck](https://github.com/adrirubio/claude-deck)**
met een eigen identiteit: **Claude Cockpit**. De `upstream` git-remote wijst naar claude-deck
(voeg later je eigen `origin` toe als je een GitHub-fork aanmaakt).

## Doel

Claude-deck levert al sessie-**monitoring** (Sessions / CC Bridge) — dat dekt "welke CC-sessie
wacht op mijn input" grotendeels. Daar bovenop bouwt Claude Cockpit twee samenhangende
lagen:

1. **Scheduled-messages** (vrijwel af) — boodschappen klaarzetten met een eenmalige
   **timer** of terugkerende **cron**, die op het geplande moment in een Claude
   Code-sessie worden geïnjecteerd (via tmux `send-keys`).
2. **Kanban als hoofdwerking** (huidige actieve track) — een poll-loop die Todo-kaarten
   autonoom claimt + spawnt, met multi-agent decompositie (analyst → executors) en
   Agent Mail voor cross-session coördinatie.

## Huidige staat

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

- **`fase-1-validation.md`** — de runtime-checklist die we nog moeten afronden (sessie-discovery + send-keys + spawn op WSL bevestigen).
- **`fase-2-plan.md`** — 12 TDD-tasks voor de scheduled-messages feature; Tasks 1–11 geïmplementeerd, Task 12 = runtime e2e.
- **`fase-2-spec.md`** — het volledige ontwerp van de scheduled-messages feature.
- **`kanban-dispatch-spec.md`** — auto-dispatcher: claim-before-spawn, worktree-isolatie, opt-in per project.
- **`kanban-spec.md` + `kanban-plan.md`** — v1-bord (passief) en het plan waaruit het is voortgekomen.
- **`multi-agent-kanban.md`** — analyst-fase + plan-attachment + kind-kaart-dependencies (smoke-test cookbook).
- **`agent-mail-spec.md`** — Agent Mail: herkomst uit upstream, fork-aanpassingen, datamodel.
- **`kanban-followups.md`** — de huidige open pool (work-type routing, sync-HLC, upstream-keuzes).
- Plan-/projectpagina in de kennisvault (Windows-zijde, los van deze repo):
  `C:\dev\obsidian\Personal\Projects\Claude Cockpit\claude-cockpit.md`.
