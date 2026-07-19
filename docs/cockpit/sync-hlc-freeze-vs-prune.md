---
title: "Sync + HLC-laag: bevriezen vs. snoeien — trade-off + beslissing"
type: decision
status: decided
---

# Sync + HLC-laag: bevriezen vs. snoeien — trade-off + beslissing

> Uit de maturiteitsanalyse (§3, punt 3). DoD van de kaart: **eerst een geschreven
> trade-off + aanbeveling, daarna pas implementatie.** Dit document is dat eerste deel;
> de aanbeveling is in deze PR ook geïmplementeerd (zie "Wat deze PR doet").

## Context

De board-store kent twee lagen:

- **Op-log** (`kanban_ops`): append-only operatie-log. Bron van waarheid + activiteiten-feed.
- **Gematerialiseerde staat** (`kanban_cards`, `kanban_deliverables`): afgeleide,
  snel-leesbare projectie van de op-log.

Daar bovenop zit multi-device CRDT-machinerie:

| Onderdeel | Locatie | Status vandaag |
|---|---|---|
| Hybrid Logical Clock | `hlc.py` | **Actief gebruikt** (ordening, replay-sleutel) |
| Per-veld `*_hlc` + LWW | `models.py`, `operations.py` | **Actief**, maar guards vuren nooit bij één device |
| Sync-seam (`ops_since`, `ingest_ops`, `SyncTransport`, `LocalNoopTransport`) | `sync.py` | **Dood**: geen enkele productie-aanroeper |

`kanban-followups.md` zegt het zelf: *"Phase K is only scaffolding today"*. Er is geen
tweede toestel en geen sync-roadmap (de containerized-sessions-richting gaat over
sandboxing, niet over multi-device). De LWW-conditiefix uit de review was een symptoom
van "te veel machine voor de huidige scope".

## Wat draagt de laag vandaag écht bij?

Belangrijk om de YAGNI-vraag eerlijk te beantwoorden — niet álles is speculatief:

1. **Op-log** → activiteiten-feed (frontend `CardDrawer` toont `activity`, met `hlc` als
   React-key) en audit/event-source. **Reëel gebruikt.**
2. **HLC als totale ordening** → `rematerialize()` replayt `ORDER BY hlc`, idempotent.
   Ook de default `rank` van een kaart is z'n create-HLC. **Reëel gebruikt.**
3. **Claim-arbitrage** → `claim`/`release` met `claim_hlc` beslist "wie het eerst had".
   Dit draait de **live** auto-dispatch: meerdere agents/sessies + de UI schrijven
   gelijktijdig via één backend; dubbel-claimen moet voorkomen worden. **Reëel gebruikt.**
4. **Per-veld `*_hlc` LWW-guards** (`title/description/column/rank`) → bij één in-proces
   klok met een asyncio-lock is elke nieuwe `tick()` strikt groter dan alle vorige, dus
   de guard wijst nóóit een live write af. **Slapend.** (De test
   `test_stale_move_is_ignored_by_lww` injecteert kunstmatig een far-future HLC om de
   tak überhaupt te raken.)
5. **Sync-seam** (`sync.py`) → geen aanroeper, geen schema-koppeling. **Volledig dood.**

Conclusie: het "onvolwassen" gevoel komt vooral van (5) — code die een werkende
sync-feature suggereert maar niets doet — en deels van (4), guards die nooit vuren.
(1)–(3) zijn geen dood gewicht.

## Optie 1 — Bevriezen

Alles laten staan, expliciet als dormant markeren, niet verder bouwen tot een 2e toestel
bestaat.

- **Voor:** nul risico, nul codewijziging behalve documentatie.
- **Tegen:** `sync.py` blijft een vals signaal ("er is sync") en een plek waar latente
  bugs schuilen; het deel van de klacht dat het meest terecht is ("oppervlak dat stuk kan")
  blijft staan.

## Optie 2 — Snoeien (volledig)

Terug naar simpele LWW: per-veld `*_hlc` weg, sync-scaffolding weg, op-log behouden als
activiteiten-feed.

- **Voor:** kleinste mentale model; geen ongebruikte CRDT-velden.
- **Tegen:**
  - Raakt de **zwaarst geteste, meest load-bearing** module (`operations.py`):
    `claim/release/move/update`-materialisatie moet herschreven naar "nieuwste op wint",
    en ~6 tests (LWW/stale/claim) moeten om.
  - De per-veld-HLC is de *enige* echte CRDT-correctheid die je weggooit; als multi-device
    er ooit komt is dit duur om opnieuw te bouwen — terwijl het nú bijna gratis stil staat.
  - Geen migratie-systeem: de 5 `*_hlc`-kolommen blijven als wees-kolommen in de live DB
    staan (worden getolereerd, maar het is cosmetische schuld), of vereisen handmatige
    DB-chirurgie op een niet-geback-upte board.
  - Hoog risico op een werkend, single-user product voor magere winst.

## Optie 3 (aanbevolen) — Snoei het dode, bevries de kern

Splits de beslissing langs de werkelijke breuklijn in plaats van langs het binaire fork:

- **Snoei `sync.py`** (de enige écht dode, nooit-aangeroepen scaffolding — het "oppervlak
  dat stuk kan") en z'n tests.
- **Bevries** HLC + op-log + per-veld LWW als bewust-dormante kern, mét documentatie
  *waarom* ze blijven (activiteiten-feed, totale ordening voor replay, claim-arbitrage).

### Waarom dit de beste keuze is

- Adresseert de **kern van de klacht** (dood, onvolwassen oppervlak) met de
  **laagste risico/winst-verhouding**: `sync.py` verwijderen raakt geen schema, geen live
  DB, geen materialisatie-pad.
- **Volledig omkeerbaar** en een **strikte subset** van optie 2: het sluit niets af. Een
  latere kaart kan alsnog per-veld-HLC verwijderen — maar dan als bewuste keuze om
  CRDT-gereedheid op te geven, niet als bijvangst.
- Behoudt de drie onderdelen die vandaag echt werk doen, inclusief de claim-arbitrage waar
  de live auto-dispatch op leunt.

### Wanneer heroverwegen

- **Multi-device wordt echt** → ontdooi: herimplementeer een echte `SyncTransport`
  (Turso/libSQL embedded replica, `sqld`, of REST push/pull) + Alembic-migraties vóór een
  niet-wisbare remote primary. De op-log en HLC zijn dan precies het juiste fundament.
- **Multi-device wordt definitief afgeschreven** → voer optie 2 uit (per-veld-HLC eruit),
  als bewuste vereenvoudiging.

## Aanbeveling

**Optie 3.** Snoei `sync.py` nu; bevries en documenteer de HLC/op-log/LWW-kern als dormant.

## Wat deze PR doet

1. Verwijdert `backend/app/kanban/sync.py` (`ops_since`, `ingest_ops`, `SyncTransport`,
   `LocalNoopTransport`).
2. Verwijdert de bijbehorende sync-tests in `test_kanban_service.py`
   (`ops_since`, ingest-foreign convergentie, idempotente replay). De HLC-ordening- en
   `rematerialize()`-tests blijven (de kern blijft werken).
3. Markeert de kern expliciet als **dormant** in `hlc.py`, `models.py` en `operations.py`,
   met een verwijzing naar dit document.
4. Werkt `kanban-followups.md` bij: het sync-onderdeel verwijst nu naar deze beslissing.

Geen schemawijziging; `rematerialize()` blijft ongewijzigd werken.
