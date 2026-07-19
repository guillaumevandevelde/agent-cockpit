---
title: "Upstream verwijderde Presence — overnemen? Trade-off + beslissing"
type: decision
status: decided
---

# Upstream verwijderde Presence — overnemen? Trade-off + beslissing

**Datum:** 2026-07-08
**Status:** besloten
**Kaart:** _zie doc — geen hex-id in dit beslisdoc vastgelegd_
**Uitkomst:** **Niet overnemen.** Presence blijft in Cockpit staan zoals het is.

> Kanban-kaart: "BESLISSING: upstream verwijderde legacy Presence-feature — meenemen in
> Cockpit?" DoD van de kaart: eerst nagaan of Cockpit Presence nog actief gebruikt of
> uitgebreid heeft, en of verwijderen breaking zou zijn, **voordat** dit als gewone
> implementatiekaart behandeld wordt.

## Context

Upstream (`adrirubio/claude-deck`) faseerde Presence uit met twee commits:

| Commit | Wat |
|---|---|
| `588cf6c` | Disable legacy Presence ingestion by default (Fixes #207) |
| `b4e3e87` | Remove legacy Presence feature — schrapt API, service, DB-kolommen, alle frontend-bestanden (Fixes #208) |

Beide commits zitten op `upstream/master`, **niet** op onze `master`. De merge-base tussen
onze fork en upstream is `42429f3` ("release: prepare v1.3.0") — alles daarna, inclusief de
hele Presence-geschiedenis én de latere verwijdering, is op upstream's kant onafhankelijk
verder ontwikkeld. Dit is dus geen "we liepen achter op een cleanup", maar twee
architecturen die na het fork-punt uit elkaar gegroeid zijn.

## Heeft Cockpit Presence nog actief gebruikt/uitgebreid?

Ja, aantoonbaar en recent. `git log master --not upstream/master -- '*presence*'` toont
eigen (niet-upstream) werk, met commits tot vorige week:

- `5553e79` feat(presence): attention notifications for sessions needing input
- `227537d` docs(cockpit): spec for pane-targeted attention (Bridge<->Presence exact join)
- `6b81c9f` feat(presence): store/expose tmux_pane and emit command-hook snippet
- `529786c` fix(presence): command hook normalises CC tool_response/prompt field names
- `eeb6098` fix(presence): auto-remove completed sessions after 5 minutes (2026-06-22)
- `ae6d6c0` fix(db): cascade-delete presence events with their session (2026-06-30)
- `e5c006c` fix(frontend): derive presence hook URL from window.location.origin (2026-07-02)
- `fcec9eb` fix: stop hanging/flaky presence-websocket tests via cancellation-safe DB access

## Zou verwijderen breaking zijn?

Ja — Presence is geen losstaand eindpunt meer, het voedt een **actieve kernfeature**:

**CC Bridge's attention-indicator loopt rechtstreeks over de Presence-websocket.**
`frontend/src/features/cc-bridge/useAttentionByPane.ts` gebruikt
`usePresenceWebSocket`/`PresenceSession` (uit `@/hooks/usePresenceWebSocket` en
`@/types/presence`) om per `tmux_pane` te bepalen welke sessie aandacht nodig heeft; dat
wordt gejoined tegen Agent Bridge-sessies. `frontend/src/hooks/useAttentionNotifications.ts`
gebruikt hetzelfde `PresenceSession`-type en navigeert bij een klik zelfs naar
`/presence?session=...`. `docs/cockpit/pane-attention-spec.md`/`pane-attention-plan.md`
beschrijven dit expliciet als "Bridge<->Presence exact join" — een doelbewust ontwerp, geen
toevalstreffer.

Verder dragen `tmux_pane`/presence-achtige velden ook door in de Scheduled Messages-laag
(`backend/app/services/scheduling/session_registry.py`,
`backend/app/api/v1/scheduled_messages/router.py`) — precies de feature die deze fork nu
bovenop CC Bridge bouwt (zie `00-orientation.md`). Presence weghalen zou dus niet alleen een
UI-paneel schrappen, maar een databron onder de huidige hoofdinitiatiefketen (Bridge →
attention → scheduled messages) wegtrekken.

## Aanbeveling

**Niet overnemen.** Presence blijft in Cockpit staan zoals het nu is; upstream's
verwijdering is **niet** een cleanup die wij gemist hebben, maar het gevolg van hun eigen,
divergerende richting (waarin Presence kennelijk overbodig werd — mogelijk doordat hun
Agent-Mail/Agent-Bridge-stack de aanwezigheidsfunctie op een andere manier oploste). Bij ons
is Presence juist de databron voor een live-feature (attention-indicator in CC Bridge) die
recent nog actief onderhouden is. Overnemen van de verwijdering zou die feature stuk maken
zonder vervanging.

`CLAUDE.md` noemt Presence terecht nog als bestaande feature/route — geen wijziging nodig.

### Wanneer heroverwegen

- Als CC Bridge's attention-indicator ooit herbouwd wordt op een andere databron (bv.
  rechtstreeks op Agent Bridge sessie-state in plaats van de aparte Presence-ingestion),
  dan vervalt de belangrijkste afhankelijkheid en kan Presence als aparte, bewuste kaart
  opnieuw beoordeeld worden.
- Als upstream's eigen reden voor verwijdering (issue #207/#208, niet ingezien in dit
  onderzoek omdat de issues niet in deze repo-checkout zitten) een probleem beschrijft dat
  ook bij ons speelt (bv. een beveiligings- of resource-leak-issue in de ingestion), dan is
  dat een aparte bugfix-kaart op onze eigen Presence-code — geen reden om de hele feature te
  schrappen.

## Wat deze kaart doet

Alleen dit document + een verwijzing in `kanban-followups.md`. Geen codewijziging: de
beslissing is "niets doen", d.w.z. bewust niet overnemen van upstream's verwijdering.
