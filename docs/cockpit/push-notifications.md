# Echte push-notificaties (PWA + Web Push/VAPID)

Cockpit stuurt **echte OS-push** naar je telefoon of desktop wanneer een sessie je
aandacht nodig heeft — óók als het tabblad dicht is. Dit bouwt voort op de bestaande
presence-attentiedetectie: dezelfde hook-events die de Presence-dashboard voeden,
triggeren nu een service-worker-push.

## Hoe het werkt

```
Claude Code hook ─▶ POST /api/v1/presence/events ─▶ presence_service
                                                   └▶ push_service.schedule_dispatch()
                                                        └▶ pywebpush ─▶ browser push service ─▶ service worker ─▶ OS-notificatie
```

- **Frontend**: `public/sw.js` (service worker) + `public/manifest.webmanifest` maken de
  app installeerbaar. `usePushSubscription` registreert de SW, vraagt toestemming en
  schrijft in via `PushManager.subscribe()` met de VAPID public key.
- **Backend**: `push_service.py` beheert VAPID-sleutels, slaat subscriptions op
  (`push_subscriptions`-tabel) en verstuurt via `pywebpush`. De presence-webhook roept
  `schedule_dispatch()` aan (fire-and-forget, blokkeert de hook-respons niet).

## Categorieën (per apparaat te muten)

| Categorie    | Trigger (hook-event)                    |
|--------------|-----------------------------------------|
| `input`      | `Stop` (wacht op input) + `Notification` met een vraag/permission |
| `completion` | `SessionEnd` (sessie beëindigd)         |
| `error`      | `PostToolUse` met een niet-nul exit code |

Muting staat per subscription in de DB en wordt via `PATCH /api/v1/push/preferences`
bijgewerkt. De "Stuur test"-knop (`POST /api/v1/push/test`) negeert muting.

## VAPID-sleutels

Standaard genereert de server bij eerste gebruik een keypair en cachet dit in
`~/.claude-registry/vapid.json`. Voor een multi-host of gecontroleerde deploy kun je
ze vastzetten via env:

```bash
export VAPID_PUBLIC_KEY="<base64url application server key>"
export VAPID_PRIVATE_KEY="<PKCS8 PEM private key>"
export VAPID_SUBJECT="mailto:jij@voorbeeld.com"
```

## ⚠️ HTTPS vereist (vooral iOS)

Web Push werkt alleen in een **secure context**:

- **Desktop (Chrome/Firefox/Edge)**: `localhost` telt als secure — push werkt lokaal.
- **iOS (Safari 16.4+)**: push werkt **alleen** als de app als PWA is geïnstalleerd
  ("Deel → Zet op beginscherm") **én** via HTTPS wordt geserveerd. `http://<lan-ip>`
  werkt niet.
- **Android (Chrome)**: HTTPS vereist buiten localhost.

Zet Cockpit dus achter HTTPS als je vanaf je telefoon push wilt ontvangen. Opties:

- **Tailscale** met `tailscale serve` / MagicDNS + HTTPS-certificaat.
- Een reverse proxy (Caddy/nginx/Traefik) met een echt of intern TLS-certificaat.

Stel bij een reverse proxy ook `PUBLIC_BASE_URL` in zodat de hook-snippets het juiste
publieke adres gebruiken.

## API-endpoints

| Methode | Pad                             | Doel                             |
|---------|---------------------------------|----------------------------------|
| GET     | `/api/v1/push/vapid-public-key` | Application server key ophalen    |
| POST    | `/api/v1/push/subscribe`        | Subscription opslaan (idempotent) |
| PATCH   | `/api/v1/push/preferences`      | Categorie-muting bijwerken        |
| POST    | `/api/v1/push/unsubscribe`      | Subscription verwijderen          |
| POST    | `/api/v1/push/test`             | Testnotificatie naar alle apparaten |
