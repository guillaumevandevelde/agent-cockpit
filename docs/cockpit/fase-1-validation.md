---
title: "Fase 1 — Validatiechecklist (werkt claude-deck onder WSL?)"
type: reference
status: active
---

# Fase 1 — Validatiechecklist (werkt claude-deck onder WSL?)

Doel: bevestigen dat de dragende claude-deck-capaciteiten werken onder WSL vóór we fase 2
bouwen. De relevante backend-stukken zijn de **`sessions`** en **`cc-bridge`** API-routes
(`backend/app/api/v1/`) en de bijhorende services; in de UI zijn dat de **Sessions** en
**CC Bridge** pagina's.

## Voorwaarden

- [ ] Docker Desktop draait en **WSL-integratie voor Ubuntu** staat aan
      (`docker info` werkt in de Ubuntu-shell).
- [ ] `claude` is ingelogd (`claude` eenmalig gedraaid + browser-auth).

## Opstarten

```bash
cd ~/dev/claude-cockpit
docker compose up -d
# open http://localhost:8000
```

## Checklist

| # | Check | Hoe | Verwacht | Status |
|---|---|---|---|---|
| 1 | **Draait & bereikbaar** | `docker compose up -d`, open de UI | UI laadt op :8000 | ⬜ |
| 2 | **Sessie-discovery** | Start in een **aparte** WSL-tmux een `claude`-sessie in een testproject; open de Sessions/CC Bridge-pagina | De sessie verschijnt in de lijst | ⬜ |
| 3 ⭐ | **Send-keys injectie** | Stuur via CC Bridge tekst/keystrokes naar die sessie | De tekst verschijnt in de tmux-sessie / wordt door CC ontvangen | ⬜ |
| 4 ⭐ | **Spawn** | Laat de UI een nieuwe CC-sessie spawnen in een projectmap | Er ontstaat een nieuwe tmux-sessie met `claude` | ⬜ |
| 5 | **Transcript/status lezen** | Bekijk Sessions/Usage | Historie/transcripts uit `~/.claude/projects` tonen correct (WSL-paden) | ⬜ |
| 6 | **Hook-pad** | Voeg een test-hook toe in `settings.json` die `curl` naar de backend doet | Het event bereikt de backend | ⬜ |

⭐ = **make-or-break**. Punt 3 en 4 moeten ✅ voor de gate.

## Een tmux CC-sessie starten (voor punt 2–3)

```bash
tmux new -s test1 -c ~/dev/claude-cockpit   # of een andere projectmap
claude                                       # binnen de tmux-sessie
# detach met Ctrl-b d; de sessie blijft draaien
```

## Bevindingen

### Code-level validatie (autonoom — 2026-06-11)

Statische analyse van de bestaande bridge-code. Relevante bestanden:
- `backend/app/services/runs/discovery.py` — `discover_agent_sessions()`
- `backend/app/services/cc_bridge/spawn.py` — `spawn_session()` / `kill_session()`
- `backend/app/services/cc_bridge/pty_relay.py` — `PtyRelay` (live terminal)
- `backend/app/api/v1/cc_bridge/router.py` — endpoints

| # | Code-level conclusie |
|---|---|
| 1 | n.v.t. (runtime) |
| 2 | ✅ Discovery = `tmux list-panes -a -F …` → per pane `tmux_target` (`sess:win.pane`), **cwd** (`pane_current_path`), pid, command; provider-match via `is_process_match`. Levert exact de **project(cwd)↔tmux_target**-mapping die fase 2 nodig heeft. |
| 3 ⭐ | ✅ **Haalbaar.** Live terminal gebruikt een PTY-relay (`tmux attach-session` over `pty.openpty`, schrijft naar master-fd) — overkill voor geplande injectie. Fase 2 gebruikt simpelweg **`tmux send-keys -t <target> -l "<msg>"`** + `Enter`. Geen WebSocket/pty nodig. Alles is tmux, dus dit werkt. |
| 4 ⭐ | ✅ **Al geïmplementeerd:** `spawn_session(directory, mode, …, skip_permissions)` → `tmux new-session -d -s <naam> -c <dir> '<claude …>'`, returnt `tmux_target=name:0.0`. Onze Spawner = dunne wrapper. **Aandachtspunt:** vandaag enkel `skip_permissions` (bool → `--dangerously-skip-permissions`); fase 2 moet `permission_mode` (`safe`/`accept-edits`/`autonomous`) mappen op de juiste `claude --permission-mode …`-flags. |
| 5 | ✅ `spawn.py:_resolve_project_directory` leest `~/.claude/projects/<folder>/<id>.jsonl` via `Path.home()` → transcript/cwd-resolutie werkt met WSL-paden. |
| 6 | n.v.t. (runtime) — hooks zijn net-nieuw; zie gat hieronder. |

**Belangrijk gat (stuurt fase 2):** discovery zet `status` **altijd op `ACTIVE`** (geen idle/busy/waiting).
Er is dus **geen** bruikbare status-bron in claude-deck. Onze **Idle Detector via CC-hooks** is
daarmee bevestigd als net-nieuw en noodzakelijk. (`capture_pane_preview` = `tmux capture-pane -p`
bestaat wel, maar pane-tekst parsen is fragiel; hooks zijn de nette bron.)

**WSL-fit:** alle mechanismen zijn puur Linux/tmux/subprocess/pty → native in WSL. De Windows-pijn
(worktrees/tmux) is door de WSL-keuze volledig omzeild.

**Reuse-kaart voor fase 2:**
- Hergebruik `discover_agent_sessions()` voor de Session Registry (project↔target via cwd).
- Hergebruik `spawn_session()` voor de Spawner (+ permission-mode-mapping toevoegen).
- Nieuw: dunne `send_keys(target, text)`-helper (`tmux send-keys`), Idle Detector (hooks), Scheduler, Delivery Engine, datamodel, UI.

### Runtime-validatie (nog te doen — vergt Docker-integratie + `claude` login)

- Punt 1 (draait & bereikbaar):
- Punt 2 (discovery toont live sessie):
- Punt 3 ⭐ (send-keys bereikt sessie):
- Punt 4 ⭐ (spawn maakt tmux+claude):
- Punt 5 (transcript/usage):
- Punt 6 (hook bereikt backend):

**Gate-conclusie:** ✅ **code-level groen** (spawn ✅ aanwezig, send-keys ✅ triviaal, discovery ✅) —
fase 2 mag ontworpen/gepland worden. ⬜ runtime-validatie nog te bevestigen vóór release.
