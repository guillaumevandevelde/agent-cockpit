# Claude Cockpit — oriëntatie (lees dit eerst)

Dit is een **fork van [adrirubio/claude-deck](https://github.com/adrirubio/claude-deck)**
met een eigen identiteit: **Claude Cockpit**. De `upstream` git-remote wijst naar claude-deck
(voeg later je eigen `origin` toe als je een GitHub-fork aanmaakt).

## Doel

Claude-deck levert al sessie-**monitoring** (Sessions / CC Bridge) — dat dekt "welke CC-sessie
wacht op mijn input" grotendeels. De **net-nieuwe** uitbreiding die we bouwen is een
**scheduled-messages feature**: boodschappen klaarzetten met een eenmalige **timer** of een
terugkerende **cron**, die op het geplande moment in een Claude Code-sessie worden
geïnjecteerd (via tmux `send-keys`).

## Huidige fase: FASE 2 — IMPLEMENTATIE (offline TDD)

Fase 1 is **code-level groen** (zie `fase-1-validation.md`): discovery + spawn bestaan al in
claude-deck, send-keys-injectie is triviaal via tmux. Het **implementatieplan** staat in
**`fase-2-plan.md`** (12 TDD-tasks).

- **Nu bouwbaar (offline, geen Docker/login):** Tasks 1–6 + 10 — datamodel, schemas,
  tmux-inject, idle-state, session-resolver, hook-script. Bouw via TDD, commit per task.
- **Vergt runtime (`docker compose up` + `claude` login):** Tasks 7–9 (delivery e2e) + 12
  (e2e-validatie), plus de **runtime-validatie** van fase 1.
- **Open punt voor review:** `permission_mode` = `default|acceptEdits|bypass` (afgestemd op
  echte `claude`-flags i.p.v. de spec-labels safe/accept-edits/autonomous).

**Backend-tests draaien:** `cd backend && ./venv/bin/pytest -q` (venv door env-bootstrap gezet).

## Omgeving

- **WSL Ubuntu 25.10**, user `guillaume` (non-root, sudo).
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

## Kernbeslissingen (fase 2)

| Onderwerp | Keuze |
|---|---|
| Leveringsmodel | A — injecteren in live/ge-spawnde **interactieve** sessies (tmux `send-keys`) |
| Geen lopende sessie | **spawn** er een (tmux, in projectmap) |
| Bezige sessie | **wachten tot idle** (via CC-hooks), dan injecteren |
| Autonomie spawn | **per taak instelbare** permission-modus, veilige default |
| Scheduler | **in-process** in de FastAPI-backend (APScheduler + SQLite-jobstore) |
| Container-isolatie | uitgesteld (apart project) |

## Documenten

- **`fase-1-validation.md`** — de checklist die je NU uitvoert.
- **`fase-2-spec.md`** — het volledige ontwerp van de scheduled-messages feature (ná validatie).
- Plan-/projectpagina in de kennisvault (Windows-zijde, los van deze repo):
  `C:\dev\obsidian\Personal\Projects\Claude Cockpit\claude-cockpit.md`.
