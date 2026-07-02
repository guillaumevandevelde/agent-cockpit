# Self-healing dev supervisor

**Datum:** 2026-06-12
**Status:** Goedgekeurd ontwerp — klaar voor implementatieplan

## Probleem

De backend (`uvicorn app.main:app --reload`) crasht af en toe tijdens development.
`scripts/dev.sh` start backend + frontend op de achtergrond en doet op het eind
`wait -n`: zodra **één** van de twee processen stopt, ruimt `cleanup` meteen ook de
ander op en valt alles stil. Gevolg: bij een backend-crash moet alles handmatig
herstart worden. Bovendien gaat uvicorn-output enkel naar de terminal, dus de
crash-oorzaak wordt nergens bewaard.

## Doelen

1. **Auto-restart** — backend herstart automatisch (met backoff) als hij crasht,
   zonder dat de frontend mee sneuvelt.
2. **Crash-logs bewaren** — output naar logbestanden zodat de echte crash-oorzaak
   achteraf te diagnosticeren en te fixen is (auto-restart maskeert anders een bug).
3. **Overleeft terminal sluiten** — draait gedetacheerd zodat het blijft lopen ook
   als de terminal sluit.
4. **Log-retentie** — crash-/run-logs ouder dan 7 dagen worden automatisch verwijderd.

## Context / constraints

- Draait rechtstreeks in WSL Ubuntu (geen Docker), user `guillaume`.
- **systemd is niet actief** in deze WSL (PID 1 = `init(Ubuntu)`, `systemctl --user`
  is offline, geen `[boot] systemd=true` in `/etc/wsl.conf`). Een systemd
  user-service zou systemd aanzetten + WSL-herstart + linger vereisen — te grote
  ingreep. Daarom: gedetacheerde bash-supervisor i.p.v. systemd.
- `dev.sh` bevat al een herbruikbare `kill_tree()` om een procesboom op te ruimen.

## Gekozen aanpak

Gedetacheerde bash-supervisor in een nieuw control-script `scripts/cockpit.sh`,
naast het bestaande `dev.sh`. `dev.sh` blijft de "attached" variant (Ctrl+C) voor
snel debuggen in de terminal; `cockpit.sh` is de zelfhelende, gedetacheerde variant.

Verworpen alternatieven:
- **systemd user-services** — vereist WSL-config wijzigen + herstart; te zwaar.
- **process-manager tool** (pm2/honcho/supervisord) — extra dependency, overkill
  voor twee processen.

## Componenten

### `scripts/cockpit.sh` — control-script (CLI)

Subcommando's:

| Commando            | Gedrag |
|---------------------|--------|
| `start`             | Prunet logs >7 dagen, start de gedetacheerde supervisor (`setsid`), schrijft supervisor-PID naar een PID-file, keert meteen terug naar de prompt. Weigert te starten als er al een supervisor draait. |
| `stop`              | Leest PID-file, killt de supervisor + volledige procesboom (hergebruikt `kill_tree`-logica), verwijdert PID-file. |
| `restart`           | `stop` gevolgd door `start`. |
| `status`            | Toont of supervisor / backend / frontend draaien (op basis van PID-files). |
| `logs [backend\|frontend]` | `tail -f` op `latest.log` van de gevraagde service (default backend). |

Ondersteunt de bestaande `--host`-optie van `dev.sh` zodat binden op bv. `0.0.0.0`
mogelijk blijft.

### Supervisor-loop (intern, gestart door `start`)

Bewaakt backend en frontend elk met een eigen restart-loop:

- Proces crasht → log exit-code naar `supervisor.log`, wacht volgens **backoff**
  (1s → 2s → 5s, gecapt op 5s), herstart het proces. De ander blijft ongemoeid.
- **Crash-loop-guard**: crasht een proces ≥5× binnen 30s, dan stopt de supervisor
  met dát proces te herstarten en logt duidelijk `crash-loop, gestopt — kijk in de
  log`. Voorkomt oneindig spinnen op een echte bug. De teller reset zodra een
  proces langer dan 30s stabiel draait.
- Bij signaal (SIGTERM/SIGINT van `stop`) ruimt de supervisor beide procesbomen op
  en stopt netjes.

## Data flow / logging

Loglayout onder een gitignored `logs/`-map in de projectroot:

```
logs/
├── backend/
│   ├── run-<timestamp>.log   # output van één (her)start
│   └── latest.log            # symlink → meest recente run-log
├── frontend/
│   ├── run-<timestamp>.log
│   └── latest.log
└── supervisor.log            # exits/restarts: "[tijd] backend exited code=139, restart #3"
```

- Elke (her)start van een service krijgt een nieuw `run-<timestamp>.log`; `latest.log`
  wijst er als symlink naar.
- `supervisor.log` noteert elke exit met exit-code en restart-teller, plus
  crash-loop-stops.
- **Retentie**: bij elke `start` worden bestanden in `logs/` ouder dan 7 dagen
  verwijderd via `find logs -type f -mtime +7 -delete` (dode `latest.log`-symlinks
  worden opgeruimd/herschreven bij de volgende run).
- `logs/` toevoegen aan `.gitignore`.

## Error handling

- `start` terwijl er al een supervisor draait → weiger met duidelijke melding +
  hint naar `status`/`restart`.
- `stop` zonder draaiende supervisor → idempotent, geen fout (ruim stale PID-file op).
- Stale PID-file (proces bestaat niet meer) → als niet-draaiend behandelen.
- Crash-loop → herstarten gestopt voor dat proces, duidelijk gelogd; de andere
  service en de supervisor blijven draaien zodat `status`/`logs` blijven werken.

## Testen

- `start` → `status` toont alles up; sluit terminal → proces draait nog (nieuwe
  shell, `status` bevestigt).
- Backend handmatig killen (`kill <backend-pid>`) → supervisor herstart hem; frontend
  blijft draaien; `supervisor.log` toont de exit + restart.
- Backend snel laten falen (bv. tijdelijke import-error) → na ≥5 crashes binnen 30s
  stopt de guard en logt het; geen oneindige loop.
- `stop` → alle processen weg, PID-files opgeruimd; `stop` opnieuw → geen fout.
- Logretentie: een bestand met mtime >7 dagen in `logs/` wordt door `start` verwijderd.

## Out of scope (YAGNI)

- systemd / WSL-bootconfiguratie.
- Auto-restart bij volledige WSL-herstart (geen daemon-bij-boot).
- Logrotatie binnen één run (per-run-bestanden volstaan; retentie dekt opruimen).
- Alerts/notificaties bij crashes.
