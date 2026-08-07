---
title: "Beslissing: eerlijke 410 Gone op onbekende MCP-sessie na backend-reload"
type: decision
status: decided
---

# Beslissing: eerlijke 410 Gone op onbekende MCP-sessie na backend-reload

**Datum:** 2026-08-07
**Kaart:** `ae19ced1d18646609739cfbb8ff694dd`
**Status:** besloten — **session-aware wrapper rond `sse.handle_post_message` die 410 Gone + JSON
teruggeeft; geen state-survival-solution.**

## Aanleiding

In een interactieve sessie faalt élke `cockpit-kanban`-MCP-tool met
`MCP error -32602: Invalid request parameters`, ook `ping` (nul argumenten). De call bereikt de
handler niet eens: `ping`, `create_card` met twee verplichte velden en `create_card` met een
bogus project-key geven alle drie exact `-32602`. Een onbekende project-key hoort
`{"error": "unknown_project_key"}` te geven, dus de call sterft vóór de handler. De
backend-access-log toont in hetzelfde tijdvak géén request van deze sessie op `/kanban-mcp/`,
terwijl gedispatchte sessies er wel als `202 Accepted` instaan. REST-fallback werkt meteen.

De MCP-sessie loopt over SSE (`GET /kanban-mcp/sse` + `POST /kanban-mcp/messages/?session_id=…`).
Sessiestate zit in `SseServerTransport._read_stream_writers: dict[UUID, MemoryObjectSendStream]`
— pure in-memory. Tijdens een sessie reloadt uvicorn 8× (`--reload`, één schrijfactie per
wijziging onder `backend/app/`), en elke worker-respawn gooit die state weg. De client heropent
de SSE-stream niet en blijft zijn dode `session_id` gebruiken; de `SseServerTransport` ziet geen
writer en antwoordt `Response("Could not find session", status_code=404)`. De MCP-client doet
`response.raise_for_status()`, en Claude Code's adapter wikkelt die non-2xx in
`MCP error -32602: Invalid request parameters` — de errorcode is misleidend, want de params zijn
goed; de *sessie* is weg.

Zusterprobleem van `f625ce2f` (autodispatch overleefde de reload ook niet) — die was gefixt door
state-survival in `_registered_project_paths`; deze kaart vraagt hetzelfde patroon voor
SSE-sessies.

## Onderzochte opties

| | Oplossing | Blast radius | Wanneer goed |
|---|---|---|---|
| **A** | Sessiestate buiten-proces (disk of Redis) overleven een reload. | Elke gedispatchte agent hervat naadloos midden in een tool-call. | N × schrijven per sessie = N roundtrips + persistence-fail-mode. |
| **B** | **Client-side SSE-reconnect** in Claude Code. | Idem aan A maar aan de andere kant. | We bezitten Claude Code niet; bug reports buiten onze release-cadans. |
| **C** | **Eerlijke foutmelding** zodat de agent "MCP-sessie weg" herkent. | Geen state-survival — sessie blijft dood — maar de agent krijgt een actionable hint en kan zelf reconnecten (Claude Code doet dit al). | Minimaal: 1 module, ~80 regels. |

## Het besluit: **C — eerlijke foutmelding via session-aware wrapper.**

Geen state-survival (A) en geen client-side fix (B). Beide beloven sessiecontinuïteit die
we niet kunnen waarmaken zonder persistente infra die we nu niet hebben. C levert 90% van
de productwaarde (agent weet nu wanneer z'n sessie weg is en kan reconnecten — Claude Code's
SSE-client heropent automatisch) voor ~5% van de complexiteit.

De wrapper hangt als een Starlette Mount-applicatie aan `/kanban-mcp/messages/` en checkt de
`session_id` query-param tegen `sse._read_stream_writers` voordat hij de oorspronkelijke
`handle_post_message` aanroept:

- **Bekende sessie** → delegate naar het origineel (202 Accepted, response via SSE).
- **Onbekende sessie** → `410 Gone` met JSON-body
  `{"error": "session_not_found", "message": "De MCP-sessie is niet meer geldig. De backend
  is herladen terwijl de SSE-connectie open stond…", "session_id": "<hex>", "reconnect_required":
  true}`. Status `410` in plaats van `404` om "weg" semantisch te onderscheiden van "fout pad"
  (RFC 7231 §6.5.9).
- **Geen `session_id`** → `400` met `{"error": "missing_session_id", …}` (was plain text
  "session_id is required").
- **Ongeldige UUID** → `400` met `{"error": "invalid_session_id", "session_id": <echoed>}` (was
  plain text "Invalid session ID").

Alle drie foutpaden produceren nu JSON — uniform parsebaar voor tooling.

## Waarom geen state-survival (A)?

Twee redenen:

1. **Architectuurkosten.** Een sessie is alleen een `MemoryObjectSendStream`-writer; om die te
   serialiseren heb je ofwel een broker (Redis / een tweede proces) of een on-disk ringbuffer
   nodig. De broker is een nieuwe dependency en een tweede store om te backuppen; de ringbuffer
   voegt latency aan elke tool-call toe. Geen van beide past bij "één uur werk om een agent
   niet te laten struikelen".
2. **Multi-worker.** Zodra we ooit achter meerdere uvicorn-workers draaien, lost een lokale
   disk-file het probleem niet meer op — alle workers moeten dezelfde file lezen, en SSE is
   stateful-by-design (één writer per connectie). De state-survival-route eindigt onvermijdelijk
   bij Redis of Sticky Sessions aan de load-balancer, en dat is een aparte architectuurbeslissing
   die we hier niet maken.

De waarheid is: het is *beter* als een sessie stervet bij een reload. Een agent die midden in een
kaart zit, wil liever één schoon reconnect dan een tool-call die tegen een bevroren toestand
praat. C dwingt dat af en geeft de agent een duidelijk signaal.

## Waarom geen 404 → 410?

`404 Not Found` zegt "ik ken dit pad niet". `410 Gone` zegt "ik kende dit, maar het is
verwijderd" — exact wat hier gebeurt. Een agent (of een mens die de foutmelding leest) kan uit
de statuscode al afleiden dat de *eigen* request-structuur goed was. De MCP-client blijft een
non-2xx zien en de error body wordt zichtbaar in het tool-resultaat — dat is de
`reconnect_required: true` vlag die de agent kan inspecteren.

## Waar het past in de codebase

- **Nieuwe module `app/kanban/mcp_transport.py`** — `build_session_aware_sse_app(kanban_mcp)`
  bouwt een Starlette-app equivalent aan `kanban_mcp.sse_app()` met alleen de `/messages/`-Mount
  vervangen. De `/sse`-Route is bit-voor-bit gelijk aan FastMCP's eigen (incl. de
  `sse_endpoint(request) → handle_sse(scope, receive, send)`-wikkel — anders start de
  SSE-handshake niet).
- **`app/main.py:357-385`** — vervangt `app.mount("/kanban-mcp", kanban_mcp.sse_app())` door
  `app.mount("/kanban-mcp", build_session_aware_sse_app(kanban_mcp))`. Geen verdere wijzigingen.
- **Test-suite** — `tests/test_kanban_mcp_session_410.py` dekt alle vier paden: bekend,
  onbekend, ontbrekend, ongeldig-UUID. Plus het end-to-end reload-scenario: sessie
  geregistreerd → sessie weg → POST → 410. 5 tests, alle groen; de bestaande
  `test_kanban_mcp_mount.py` + `test_kanban_mcp_health.py` blijven groen (de wrapper laat
  het SSE-handshake-pad ongewijzigd).

## Beperkingen / niet-gedaan

- **Geen auto-reconnect aan onze kant.** We vertrouwen op Claude Code's SSE-client om na een
  410 de stream te heropenen. Als een toekomstige MCP-client dat niet doet, moet die kant de
  reconnect-logica krijgen — niet wij.
- **Geen metric/log voor "MCP-sessie weg"-frequentie.** Eén `logger.info` per 410 is genoeg voor
  debug-sporen, maar we tellen niet. Een operationele meting hoort in `cockpit-mcp-health` of
  in de bestaande backend-statussen, niet in deze fix.
- **Geen state-survival.** Zoals boven: bewust niet gedaan. Een opvolger-kaart die het wel wil
  proberen moet beginnen met de vraag of de huidige single-process-architectuur überhaupt
  meerdere workers aankan — dat is een andere beslissing.

## Heropenen

- Een agent die structureel sessies verliest tijdens normaal gebruik (niet-reload) → sessie wordt
  eerder gesloten dan gedacht, bug in FastMCP/SSE, opvolger-kaart.
- Multi-worker uvicorn-deployments die om sticky sessions vragen → niet met deze fix op te
  lossen; aparte architectuur-kaart.
- De MCP-client upgrade naar een versie die `-32602` niet meer als generieke error-wrapper
  gebruikt → dan kan de 410-body direct als JSON-RPC-fout zichtbaar worden, dit blijft hoe dan
  ook een verbetering.