# Fase 2 — Spec: Scheduled messages

> Bouw dit pas nadat **fase 1** groen is (zie `fase-1-validation.md`). Fase 1 is **code-level
> groen** (2026-06-11); runtime-validatie nog te bevestigen vóór release.

## Bevestigde integratiepunten (code-level, 2026-06-11)

Uit de bestaande claude-deck-code (zie `fase-1-validation.md` voor details):

- **Discovery** — `app/services/runs/discovery.py::discover_agent_sessions()` →
  lijst van `{tmux_target, session_name, cwd, pid, status}`. Gebruik dit voor de Session
  Registry: map **project (cwd) → tmux_target**.
- **Spawn** — `app/services/cc_bridge/spawn.py::spawn_session(directory, mode, …, skip_permissions)`
  → `tmux new-session -d -s <naam> -c <dir> 'claude …'`. Wrap dit in de Spawner; **breid uit**
  zodat `permission_mode` mapt op `claude --permission-mode <…>` (nu enkel `skip_permissions`
  → `--dangerously-skip-permissions`).
- **Injectie (send-keys)** — *nieuw, dun*: `tmux send-keys -t <tmux_target> -l "<msg>"` gevolgd
  door `tmux send-keys -t <tmux_target> Enter`. (De bestaande `PtyRelay` is voor de live
  browser-terminal en is hier niet nodig.)
- **Status/idle** — claude-deck levert dit **niet** (discovery zet `status` altijd op `ACTIVE`).
  De Idle Detector (CC-hooks) is daarom net-nieuw en draagt de "wacht-tot-idle"-logica.
- **Patronen** — backend: services + `api/v1/<feature>/router.py`; SQLite/SQLAlchemy in
  `app/models/`; geen migraties (schema via `create_all`). Frontend: feature-module onder
  `frontend/src/features/`.

## Architectuur & componenten (nieuw, bovenop claude-deck)

1. **Schedule store** — tabel `scheduled_messages` in de bestaande SQLite/SQLAlchemy
   (`backend/app/models/`). Bron van waarheid.
2. **Scheduler** — APScheduler in de FastAPI-backend, met persistente SQLAlchemy-jobstore
   (overleeft restarts). Triggers: `DateTrigger` (eenmalig) + `CronTrigger` (terugkerend).
   Levert niet zelf; overhandigt aan de Delivery Engine.
3. **Delivery Engine** — resolvet doelsessie → spawnt indien afwezig → wacht tot idle indien
   bezig → injecteert. Beheert een wachtrij van "pending deliveries".
4. **Session Registry + Idle Detector** — status per project (bezig / idle / wacht-op-input),
   gevoed door CC-hooks + claude-deck's bestaande sessie-discovery (`cc-bridge`/`sessions`).
5. **Session Spawner** — start een CC-sessie in een tmux-pane in de projectmap met de gekozen
   permission-flags. Dunne wrapper rond claude-deck's spawn-capaciteit.
6. **Hook-integratie** — klein hook-script (via `~/.claude/settings.json`) dat sessie-events
   naar de backend POST't.
7. **UI** — nieuwe feature-module in `frontend/src/features/` (lijst + aanmaak/bewerk + leveringslog).

**Afhankelijkheden:** UI → Schedule store + Scheduler; Scheduler → Delivery Engine;
Delivery Engine → Session Registry + Spawner; Idle Detector ← Hook-integratie. Elke unit
apart testbaar (tmux-laag en hook-laag mockbaar).

## Datamodel

**`scheduled_messages`:** `id`, `target_project` (pad), `message` (tekst), `trigger_type`
(`once`|`cron`), `fire_at` (datetime, bij `once`) **of** `cron_expr` + `timezone`
(default `Europe/Brussels`), `permission_mode` (`safe` default | `accept-edits` | `autonomous`),
`enabled` (bool), `status` (`scheduled`|`pending_delivery`|`delivered`|`failed`|`cancelled`),
`on_missing_session` (default `spawn`), `when_busy` (default `wait_until_idle`),
`created_at`/`updated_at`/`last_fired_at`.

**`delivery_attempts`:** `id`, `scheduled_message_id`, `fired_at`, `resolved_session`,
`action` (`used_existing`|`spawned`), `wait_duration`, `delivered_at`,
`outcome` (`success`|`failed`|`timeout`), `error`.

## Leveringsflow (eenmalige timer)

1. **Aanmaak** (UI) → opgeslagen + geregistreerd bij APScheduler (`DateTrigger`).
2. **Afvuren** → status `pending_delivery`, overhandigen aan Delivery Engine.
3. **Resolven** via Session Registry:
   - Geen sessie → Spawner start er een → wacht op session-start-hook → idle → **stuur**.
   - Idle sessie → **stuur** meteen.
   - Bezige sessie → registreer "lever bij idle"; blijft `pending`. Bij `Stop`-hook → **stuur**.
4. **Sturen** = tmux `send-keys` (tekst + Enter) in de doel-pane.
5. **Afronden** → status `delivered`, log de attempt. (Cron: status terug naar `scheduled`.)

## Edge-cases & foutafhandeling

- **Spawn faalt** → `failed` + notificatie + log.
- **Nooit idle** binnen **timeout** (default 30 min, instelbaar) → `timeout` + notificatie;
  **niet** alsnog sturen.
- **Meerdere sessies/project** → neem de **meest recent actieve**. (Geen idle-voorkeur-logica.)
- **Sessie sterft terwijl pending** → her-resolven (mogelijk spawnen).
- **Backend herstart terwijl pending** → APScheduler persistente jobstore + `pending_delivery`-status
  laten hervatten.
- **Cron-overlap** → **coalescing**: nieuwe afvuring overslaan + loggen.

## Idle-detectie via CC-hooks

- `UserPromptSubmit` → `bezig` · `Stop` → `idle` · `Notification` → `wacht-op-input`.
- Events bevatten `session_id` + `cwd`. Session Registry mapt `session_id` ↔ tmux-pane via
  claude-deck's discovery (**in fase 1 geverifieerd**).

## UI

- **Geplande boodschappen** — lijst met status-badges; per rij: doelproject, trigger
  (timer/cron), permission-modus, enable/pause, bewerk/annuleer.
- **Aanmaak/bewerk-form** — projectkiezer, bericht, timer (datum/tijd of "over X") vs cron, permission-modus.
- **Leveringslog** — historie uit `delivery_attempts`.

## Testing

- **Unit** — trigger-berekening (once/cron), state-machine, lever-bij-idle-wachtrij,
  cron-overlap-coalescing, timeout.
- **Integratie** — gemockte tmux + gesimuleerde hook-events → elk leverpad.
- **Idle Detector** — hook-event-sequenties → bezig/idle/wacht-transities.
- **E2e onder WSL (handmatig)** — echte tmux + CC-sessie: plan een boodschap over 1 min,
  observeer injectie; en het spawn-pad met lege projectmap.

## Niet-doelen (YAGNI)

- Geen container-isolatie (apart project).
- Geen headless-model (B); enkel model A.
- Geen multi-user/remote.
- Monitoring-dashboard niet from scratch — claude-deck's bestaande hergebruiken.
