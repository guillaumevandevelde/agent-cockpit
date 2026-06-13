# Ontwerp: notificatie bij voltooide beurten > 5s

**Datum:** 2026-06-13
**Status:** Goedgekeurd voor implementatieplanning

## Probleem

Bij een lang antwoord ("ik kook even mijn antwoord") glijdt de aandacht van de
gebruiker weg. Hij wil een desktopnotificatie krijgen wanneer een beurt klaar
is, maar alleen wanneer die merkbaar lang duurde — korte antwoorden moeten geen
notificatie geven (ruis).

## Huidige toestand

Het attention-notificatiesysteem (`frontend/src/hooks/useAttentionNotifications.ts`)
vuurt vandaag een **"🟡 wacht op je input"**-notificatie bij élke transitie naar
status `stopped`, ongeacht hoe lang de beurt duurde. De `Stop`-hook zet de sessie
op `stopped` (`backend/app/services/presence_service.py`), en elke voltooide
beurt eindigt met een `Stop`.

De backend kent beide tijdstippen die nodig zijn om de beurtduur te bepalen:
- `UserPromptSubmit` — start van de beurt
- `Stop` — beurt klaar

Beide worden al opgeslagen als rijen in `presence_events`.

## Gewenst gedrag

Eén notificatie. Die vuurt enkel wanneer de beurt:
1. **écht voltooid is** (`Stop`), én
2. **langer dan 5 seconden** duurde (gemeten van `UserPromptSubmit` tot `Stop`).

## Gekozen aanpak

De backend berekent de beurtduur bij het `Stop`-event uit de reeds opgeslagen
events en geeft die mee in de presence-response. De frontend laat de bestaande
"stopped"-notificatie enkel nog vuren wanneer de duur ≥ 5s is.

Betrouwbaar (serverklok, werkt ook als de tab op de achtergrond stond) en
**geen DB-schemawijziging** (puur berekend, geen nieuwe kolom — dit project heeft
geen migratiesysteem; een nieuwe kolom zou een db-wipe vereisen).

### Verworpen alternatieven

- **Frontend meet zelf** (wall-clock tussen "actief" en "stopped"): geen
  backend-wijziging, maar fragiel — de frontend moet de start-transitie
  betrouwbaar zien en vergist zich bij een sessie die al actief was toen de tab
  openging.
- **Echte DB-kolommen** (`turn_started_at`, `last_turn_duration_s`): properste
  datamodel, maar vereist een db-wipe (geen migraties). Overkill.

## Wijzigingen

### Backend — `app/services/presence_service.py`

- In `process_event`, in de `Stop`-tak: query het meest recente
  `UserPromptSubmit`-tijdstip voor deze sessie
  (`SELECT max(timestamp) FROM presence_events WHERE session_id = ? AND event_type = 'UserPromptSubmit'`)
  en bereken `turn_duration_s = (now − prompt_tijd).total_seconds()`.
  - Geen match → `None`.
  - tz-naïeve waarden uit SQLite behandelen als UTC, zoals elders in dit bestand
    (vgl. de `bucket_start`-afhandeling).
  - De huidige `Stop`-eventrij (bovenaan `process_event` toegevoegd) is van type
    `Stop`, niet `UserPromptSubmit`, dus die wordt niet meegeteld.
- `_to_response` krijgt een optionele param `turn_duration_s: float | None = None`.
  Enkel de `Stop`-tak geeft een berekende waarde door; alle andere oproepen
  (incl. `get_all_sessions`-snapshot) geven `None`.

### Backend — `app/models/schemas.py`

- `PresenceSessionResponse` krijgt veld `last_turn_duration_s: float | None = None`
  (Pydantic-only, géén ORM-kolom).

### Frontend — `src/types/presence.ts`

- `PresenceSession` krijgt veld `last_turn_duration_s?: number`.

### Frontend — `src/hooks/useAttentionNotifications.ts`

- Constante `LONG_TURN_THRESHOLD_S = 5`.
- In `detectAttention`, de `stopped`-tak: alleen `push`en wanneer
  `next.last_turn_duration_s != null && next.last_turn_duration_s >= LONG_TURN_THRESHOLD_S`.
- Body wordt `Antwoord klaar na ${Math.round(next.last_turn_duration_s)}s`.
- Titel blijft de bestaande "wacht op je input"-lijn; `tag` onveranderd
  (`${session_id}:input`).

## Data-flow

```
UserPromptSubmit (opgeslagen in presence_events)
   → … werk (PreToolUse / PostToolUse / …) …
   → Stop
        → backend: query laatste UserPromptSubmit-tijdstip → turn_duration_s
        → WS session_update met last_turn_duration_s
   → frontend detectAttention: gate op LONG_TURN_THRESHOLD_S
        → Notification (alleen bij duur ≥ 5s)
```

## Randgevallen

- **Geen voorafgaande `UserPromptSubmit`** (zeldzaam): duur `None` → geen
  notificatie.
- **Duur < 5s**: geen notificatie (gewenst).
- **Beurt met lange tool-run > 5s**: notificatie (gewenst — aandacht was sowieso
  weg).
- **`error`-pad** en **permission/narrative**-notificaties: ongemoeid, eigen
  takken in `detectAttention`.
- **Eerste keer dat een sessie gezien wordt** (incl. snapshot bij connect):
  blijft stil via de bestaande seeding-logica (`prev` ontbreekt).
- **Mid-beurt permission-wachttijd** telt mee in de duur: aanvaardbaar — ook dan
  was de aandacht weg.

## Testen

- **Backend (pytest):**
  - `Stop` na een `UserPromptSubmit` levert een positieve `last_turn_duration_s`.
  - `Stop` zonder voorafgaande `UserPromptSubmit` levert `None`.
  - tz-naïef opgeslagen prompt-tijdstip levert een correcte (niet-negatieve) duur.
- **Frontend:** geen testopzet aanwezig → manueel verifiëren (lange beurt geeft
  notificatie met "na Ns"; korte beurt < 5s geeft geen notificatie).
