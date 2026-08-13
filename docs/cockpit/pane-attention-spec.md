---
title: "Spec — Pane-gerichte attentie: Bridge ↔ Presence exacte koppeling"
type: spec
status: superseded
---

> **Superseded op 2026-08-13.** De feature die dit document beschrijft is uit
> Agent Cockpit verwijderd tijdens de opruiming naar de kern. Wat er precies
> weg is, waarom, en welke gedragsverandering dat opleverde staat in
> [`kern-terugbrengen-plan.md`](./kern-terugbrengen-plan.md). Dit document
> blijft staan als beslisspoor; behandel de inhoud niet als huidige toestand.

# Spec — Pane-gerichte attentie: Bridge ↔ Presence exacte koppeling

**Datum:** 2026-06-11
**Status:** ontwerp goedgekeurd, klaar voor implementatieplan
**Bouwt voort op:** de bestaande "Aandacht vragen"-feature (`useAttentionNotifications`,
`AttentionContext`, commit `5553e79`).

## Probleem

De gebruiker werkt voornamelijk vanuit de **Agent Bridge** (CC Bridge), niet de
Presence-pagina. Wanneer een sessie input nodig heeft, moet:

1. de notificatie naar **exact de juiste bridge-sessie** leiden, en
2. het **in de Bridge zelf** duidelijk zijn wélke sessie input nodig heeft.

De moeilijkheid: Bridge en Presence gebruiken verschillende identiteiten.

| | Presence | Agent Bridge |
|---|---|---|
| Sleutel | Claude `session_id` (UUID uit hooks) | `tmux_target` + `pane_id` (`%0`) + `pid` |
| Kent | `project_path` (cwd), status, "waiting for input" | cwd, pid, command — **géén** Claude session_id |
| Bron | CC **hooks** (push) | tmux/PTY-discovery (poll) |

Onderzochte koppel-opties en waarom ze afvallen:

- **cwd-join:** niet eenduidig — de gebruiker draait regelmatig meerdere sessies in
  dezelfde map.
- **`/proc/<pid>/fd` → transcript:** `claude` houdt de transcript-file niet als open fd
  vast; niet betrouwbaar.
- **proces-environment:** `claude` zet geen session-id in z'n env.

Wél beschikbaar: een **CC-hook draait mét `$TMUX_PANE`** in z'n environment (geverifieerd:
een `claude`-proces onder tmux heeft `TMUX_PANE=%0`). Discovery legt de stabiele
`pane_id` al vast (`discovery.py:18`). Dat geeft een exacte brug.

## Kernidee

Laat de hook `$TMUX_PANE` meesturen → sla het op als `tmux_pane` op de presence-sessie →
join **`presence.tmux_pane == bridge.pane_id`**. Exact en stabiel, ook bij meerdere
sessies per map.

## Aanpak (gekozen uit 3 alternatieven)

Gekozen: **① hook-verrijking met `$TMUX_PANE`**. Afgewezen: ② per-pane PTY-schermlezen
(heuristisch, fragiel, alleen voor bekeken panes), ③ cwd-join (lost de "welke pane"-vraag
niet op bij meerdere sessies per map).

## Componenten

### 1. Hook verrijken (de join-sleutel)

De huidige presence-hook is een **HTTP-hook** (`{"type":"http","url":...}`); die kan geen
env-variabelen meesturen. We bieden een **command-hook**-variant aan die de payload van
stdin neemt, `$TMUX_PANE` toevoegt en naar `/presence/events` post:

```
jq -c --arg p "$TMUX_PANE" '. + {tmux_pane:$p}' \
  | curl -sf -X POST http://localhost:8000/api/v1/presence/events \
         -H 'Content-Type: application/json' -d @-
```

- Het config-snippet-endpoint (`presence.py`, `config-snippet`) en de `ConnectDialog`
  genereren deze command-variant (naast of in plaats van de HTTP-variant).
- **Veld-mapping verifiëren:** CC's command-hook levert JSON op stdin met mogelijk andere
  veldnamen dan de HTTP-hook-body (bv. `tool_response` vs `tool_result`). Tijdens
  implementatie controleren dat de payload die naar `/events` gaat de velden bevat die
  `PresenceEventIn` verwacht. Dit is een expliciete test.
- **Vereist `jq` en `curl`** op het systeem van de gebruiker (WSL Ubuntu: standaard
  aanwezig / triviaal te installeren).
- Sessies buiten tmux: `$TMUX_PANE` leeg → `tmux_pane` leeg → geen bridge-badge (graceful
  degradatie, geen fout).

### 2. Backend — pane opslaan & exposen

- `PresenceEventIn` (`schemas.py`): veld `tmux_pane: Optional[str] = None`.
- `PresenceSession` (ORM, `models/database.py`): nullable kolom `tmux_pane: str | None`.
- `process_event` (`presence_service.py`): `session.tmux_pane = payload.get("tmux_pane")`
  wanneer aanwezig (niet overschrijven met leeg als al gezet).
- `PresenceSessionResponse` (`schemas.py`) + `_to_response`: `tmux_pane` exposen.

⚠️ **DB-reset vereist.** Geen migratiesysteem (`create_all`); een nieuwe kolom komt niet
automatisch in een bestaande tabel. De presence-DB (`backend/claude_registry.db`) moet
verwijderd worden zodat de tabel opnieuw wordt aangemaakt. Presence-historie is
wegwerp-monitoringdata, dus dataverlies is acceptabel.

### 3. Frontend — live join in de Bridge

- `frontend/src/types/presence.ts`: `tmux_pane?: string` op `PresenceSession`.
- `CCBridgePage` abonneert óók op de presence-WS (hergebruik `usePresenceWebSocket`) en
  houdt een `Map<pane_id, PresenceSession>` bij (alleen entries met `tmux_pane`).
- Per bridge-sessie wordt attentie afgeleid via `presenceByPane.get(session.pane_id)`.
  De bridge-lijst pollt elke 5s; de presence-WS pusht statuswissels real-time.
- Attentie-status afgeleid uit de presence-sessie (zelfde logica als de notificatie-hook):
  - 🟡 **wacht op input**: `status === 'stopped'`
  - 🔴 **fout**: `status === 'error'`
  - 🔐 **notificatie**: verse `last_narrative_at`

### 4. Visuele indicatoren in de Bridge

- **Sessielijst** (`SessionList.tsx` / `SessionCard.tsx`): per rij een badge/stip
  (🟡/🔴/🔐) wanneer de gematchte presence-sessie aandacht vraagt. Attentie-sessies
  optioneel bovenaan gesorteerd.
- **Aangehaakte pane** (`TerminalView.tsx`, pane-header): ring/indicator wanneer die pane
  input nodig heeft.

### 5. Notificatie → exacte pane aanhaken

- `useAttentionNotifications.ts`: `session_update` bevat nu `tmux_pane`. Klik op een
  notificatie → navigeer naar `/cc-bridge?attach=<pane_id>`. Heeft de sessie geen
  `tmux_pane` → fallback naar `/presence?session=<session_id>` (bestaand gedrag).
- `CCBridgePage` leest `?attach=<pane_id>`, zoekt de ontdekte sessie met dat `pane_id`,
  en **haakt z'n `tmux_target` aan in het grid + focust** die pane. Grid vol (max 4) →
  oudste target eruit (`addTarget`-gedrag uitbreiden naar "vervang oudste"). Pane nog niet
  ontdekt (poll-race) → na `refresh()` opnieuw proberen; query-param daarna opruimen zodat
  hij niet herhaaldelijk aanhaakt.

## Data-flow (samengevat)

```
claude (in tmux pane %0)
  └─ hook (command) ─ voegt $TMUX_PANE toe ─→ POST /presence/events {..., tmux_pane:"%0"}
        └─ process_event: PresenceSession.tmux_pane = "%0"
              └─ broadcast session_update {..., tmux_pane:"%0"} via WS
                    ├─→ useAttentionNotifications: desktop-notificatie, klik → /cc-bridge?attach=%0
                    └─→ CCBridgePage: presenceByPane["%0"] → badge op rij + pane met pane_id "%0"
```

## Scope-grenzen (YAGNI)

- De Presence-pagina blijft ongemoeid; alleen de pijplijn wordt hergebruikt. Niets
  verwijderen.
- Geen PTY-schermlezen.
- Geen multi-provider-specials: Codex-sessies hebben geen CC-hooks → geen `tmux_pane` →
  geen badge (graceful). Out of scope om dat op te lossen.

## Testen

- **Backend (pytest):**
  - `process_event` slaat `tmux_pane` op en `_to_response` exposet het.
  - Leeg/afwezig `tmux_pane` overschrijft geen bestaande waarde.
  - config-snippet genereert de command-hook-variant met `$TMUX_PANE`.
- **Frontend:** `npm run build` (tsc) + `npm run lint` clean. Geen test-harness.
- **Runtime-validatie:** met echte tmux-sessies in de Bridge — badge verschijnt op de
  juiste rij/pane; notificatie haakt de juiste pane aan. Vergt `docker compose up` +
  ingelogde `claude` + omgezette hook.

## Open punten / risico's

- **Command-hook veld-mapping** (zie §1) is de grootste onbekende; eerst valideren vóór de
  rest erop bouwt.
- `jq`/`curl`-afhankelijkheid in de hook. Alternatief zonder `jq` (pure curl) is mogelijk
  maar minder robuust; `jq` heeft de voorkeur.
- Query-param-opruiming na auto-attach om herhaald aanhaken te voorkomen.
