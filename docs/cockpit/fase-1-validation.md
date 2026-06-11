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

Noteer per punt wat werkt / niet werkt. Bij ❌ op 3 of 4: beschrijf precies wat misgaat
(bv. tmux-pane-mapping, verkeerde target, send-keys timing). Dat stuurt het fase-2-ontwerp —
mogelijk hebben we een eigen dunne injectie/spawn-laag nodig i.p.v. claude-deck's bestaande.

- Punt 1:
- Punt 2:
- Punt 3 ⭐:
- Punt 4 ⭐:
- Punt 5:
- Punt 6:

**Gate-conclusie:** ⬜ groen (door naar fase 2 / writing-plans) · ⬜ aanpassing nodig
