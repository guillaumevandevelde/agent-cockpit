---
title: "Agent Mail — upstream sync (adapted port)"
type: spec
status: active
---

# Agent Mail — upstream sync (adapted port)

> Cross-session berichten tussen willekeurige, losstaande Claude Code/Codex CLI-sessies
> (verschillende terminals/repo's) — durable per-repo identiteit, structured messages
> en een inspectable mailbox-UI. Wakeability via tmux was onderdeel van eerdere
> revisies (verwijderd 2026-08-15, kaart `64b259f6…`); cross-session nudgen loopt nu
> via Claude Code's eigen `SendMessage` of Codex' pane-nudge, buiten Agent Mail.

## Herkomst en scope-beslissing

Geport uit upstream `adrirubio/claude-deck`, commits `52a5da1` (MVP) t/m `c246726`
(compose-UX-fix), met twee bewuste uitzonderingen:

- **`945d0bd`/`ee0d9e9` ("same-repo participants") — niet geport.** Die leunen op een
  aparte upstream-feature (Agent-Team-*presets/slots*, `agent_team_presets`/
  `agent_team_slots`) die niet in deze fork zit en niet in de gevraagde commit-lijst
  stond. Deze fork heeft al een ander groeperings-concept (`RunGroup`/`RunMembership`
  in `services/runs/groups.py`, cwd-gebaseerde auto-grouping) — het upstream
  preset/slot-model erbovenop zetten zou twee botsende groeperings-concepten opleveren.
  **v1 = één durable member per repo** (upstream's oorspronkelijke MVP-vorm, vóór
  945d0bd). Same-repo multi-participant is een expliciete, latere follow-up als de
  behoefte zich concreet aandient.
- **`6e1546f` (Codex plan snapshots) — niet geport.** Functioneel losstaand van Agent
  Mail (leest `update_plan`-events voor de bestaande Plans-feature); toevallig
  chronologisch aangrenzend in upstream's geschiedenis. Aparte toekomstige kaart.

Deze fork had eerder al een kanban-scoped "Agent Mail" (identity = kanban-rol,
mail gekoppeld aan cards) — bewust verwijderd in `3cb8450` toen de multi-rol dispatch
(analyst/developer/testing/code-review) samenvoegde tot één `engineer`-rol. Dát concept
bestond alleen om contextverlies tussen die rollen te overbruggen en is niet meer nodig.
Dít Agent Mail is iets anders: **machine-breed, tussen willekeurige sessies**, niet aan
kanban-dispatch gekoppeld.

## Wat Agent Mail niet is

Claude Code 2.1.224 shipte native `SendMessage`/`ListAgents` — ad-hoc
cross-session messaging tussen willekeurige CC-sessies op macOS/Linux,
zonder Cockpit of kanban. Agent Mail vervangt die niet; beide bestaan
naast elkaar met aanvullende scope.

Agent Mail voert een `kind`-taxonomie
(`context_request`/`handoff`/`answer`) met `request_status`-lifecycle,
een durable roster per repo, een externe-actor-API en een mailbox-UI.
CC native heeft dat geen van alle: sessies ontdekken elkaar via `name`
en wisselen platte berichten uit.

**Correctie 2026-08-13 (kaart `30d45e5f…`).** Een eerdere versie van
deze paragraaf schreef dat elk bericht in `kanban_ops` landt als
activity-feed-regel. Dat staat niet in de code — geen agent-mail-module
schrijft kanban-state, en `MailMessage`
(`backend/app/models/agent_mail.py:76-98`) heeft geen kaart-id en geen
run-id. De claim is hier verwijderd.

**Uitkomst 2026-08-13: dunner maken.** De vier-assen-meting (bereik,
duurzaamheid, zichtbaarheid, externe toegang) draaide de eerdere
keep-beslissing om. Nul berichten in 36 dagen, en de native laag
adresseert onze gedispatchte sessies fijner dan wij — per sessie in
plaats van per repo, met tmux-doel erbij. De roster- en install-laag
blijft; de externe-actor-laag (commit `14472c35`) en wake-lus (commit
`6a03dc83`, deze kaart) zijn weg. Meting en onderbouwing staan in
[`cc-native-cross-session-decision.md` § Herziening 2026-08-13](cc-native-cross-session-decision.md).

## Wat hergebruikt wordt (i.p.v. herbouwd)

Upstream bouwde voor Agent Mail een aantal subsystemen die deze fork al **in andere vorm**
heeft. Adapted port = upstream's datamodel/servicelogica overnemen, maar de transport-/
infra-lagen aan bestaande Cockpit-primitieven hangen:

| Upstream bouwde | Cockpit heeft al | Actie |
|---|---|---|
| Eigen stdio MCP-shim (`mcp_shim/agent_mail_server.py`) + `codex mcp add`-installer per provider | Generieke, bearer-token-authed MCP-server (`app/mcp_server`, Streamable-HTTP op `/api/v1/mcp-server`, `MCPAccessToken` met `agent_name`) | Mail-tools registreren op de bestaande server (`app/mcp_server/tools/agent_mail.py`). Geen apart transport, geen Codex `mcp add`-installer nodig. |
| Eigen tmux `send-keys`-nudge | Geen wake-lus meer in Agent Mail — cross-session nudgen loopt buiten deze module (Claude Code `SendMessage`, Codex pane-nudge) | Verwijderd 2026-08-15 (kaart `64b259f6…`). |
| Eigen tmux-pane-scanner voor "observed sessions" | `app/services/runs/discovery.py::discover_agent_sessions()` — scant al panes + matcht `claude-code`/`codex-cli`-processen | Hergebruiken in `sync_observed_sessions()`. |
| Repo-identiteit (`repo_utils.py`, git-common-dir-gebaseerd) | Geen equivalent | Nieuw, bijna 1-op-1 geport (~40 regels). |
| Claude Code hook-installer (curl-based, additive merge in `settings.json`) | Vergelijkbaar patroon in `scheduling/hook_installer.py`, maar andere events/URL | Nieuwe kleine module, zelfde idempotente-merge-vorm. |
| Codex CLI hook-installer (`~/.codex/hooks.json` editen) + `codex mcp add` | Codex is al eersteklas provider (`providers/codex_cli.py`, `get_codex_home()`) maar geen bestaande hook-installer | Hooks.json-editing geport; MCP-registratie **niet** (zie boven — geen aparte shim nodig). |
| ~~`MailExternalActor` token/rate-limit-model voor externe tools~~ ✅ Geïmplementeerd (kaart `5fca30d0…`): verwijderd — geen externe actor ooit geregistreerd | Geen equivalent (MCPAccessToken is voor MCP-toolcalls, niet voor een generieke bearer-authed REST-facade per actor) | ~~Nieuw, zoals upstream~~ — verwijderd. |
| Frontend `agent-mail`-feature (11 bestanden, ~2.260 regels) | `CLICKABLE_CARD`, `MODAL_SIZES`, `MarkdownRenderer`, `MarkdownPreviewToggle`, `sonner` (al aanwezig) | Geport, met 2 gerichte fixes: body/charter-velden door `MarkdownPreviewToggle` (upstream gebruikt inconsistent plain `Textarea` terwijl de read-kant wél `MarkdownRenderer` gebruikt); Requests-tab message-cards door `CLICKABLE_CARD` (upstream gebruikt losse knoppen). |

## Datamodel (`backend/app/models/agent_mail.py`, hoofd-DB via `Base`)

Nieuwe tabellen, `create_all` volstaat (geen migratie-framework, alleen nieuwe tabellen):

- **`mail_team_members`** — durable identiteit, één per repo. `id` PK, `repo_id` (uniek,
  sha1 van git-common-dir via `derive_repo_identity`), `repo_path`, `repo_name`,
  `display_name`, `role`, `charter`, `created_at`, `updated_at`. Geen `team_preset_id`/
  `team_slot_id` (zie scope-beslissing).
- **`mail_agent_sessions`** — efemere sessie gekoppeld aan een member. `id` PK,
  `member_id` FK CASCADE, `provider` (`claude-code`/`codex-cli`/`unknown`), `source`
  (`hook`/`mcp`/`observed`), `session_key` (uniek), `cwd`, `tmux_target`, `pane_id`,
  `pid`, `mailbox_status` (`connected`/`observed`/`offline`), `activity`, `last_seen_at`
  (indexed), `created_at`.
- **`mail_messages`** — `id` PK, `thread_root_id` FK zelf-referentieel, `kind`
  (`message`/`broadcast`/`context_request`/`handoff`/`answer`), `sender_member_id` FK
  SET NULL, `recipient_member_id` FK CASCADE (null = broadcast), `subject`, `body_markdown`,
  `payload` (JSON), `request_status` (`pending`/`answered`/`acknowledged`, alleen
  voor `context_request`/`handoff`), `created_at` (indexed).
- **`mail_receipts`** — per-ontvanger read/ack-state. `id` PK, `message_id` FK CASCADE,
  `member_id` FK CASCADE, `read_at`, `acked_at`, `created_at`.
  `UNIQUE(message_id, member_id)`.

## Service-laag (`backend/app/services/agent_mail_service.py`)

Directe SQLAlchemy, singleton `agent_mail_service = AgentMailService()`.
Geen in-process throttle-state meer sinds de wake-lus verwijderd is
(commit `6a03dc83`); single-instance lokale deployment is geen beperking.

Kernfuncties (zie upstream-analyse voor volledige signatures, 1-op-1 geport minus
team-slot-integratie):

- **Identiteit**: `get_or_create_repo_member(db, cwd)` (via `derive_repo_identity`),
  `register_session(db, request)` — hook/MCP-entrypoint, upsert member + session.
- **Liveness**: `heartbeat_session`, `mark_session_offline`, `heartbeat_member_mcp_session`,
  `_effective_status` (TTL/PID-gebaseerd: hook 180s, MCP 3600s + PID-liveness, observed
  300s).
- **Discovery-sync**: `sync_observed_sessions(db)` — roept
  `runs.discovery.discover_agent_sessions()` (hergebruik, zie boven), matcht
  gevonden panes aan bestaande hook/MCP-sessies via PID-ancestry + repo-identiteit
  (voorkomt dubbele leden voor dezelfde logische sessie).
- **Messaging**: `send_message` (valideert kind, sender-exclusiviteit member/actor,
  `answer` vereist pending `context_request`-root gericht aan sender), `get_inbox`,
  `mark_read`, `ack_message` (sluit request-lifecycle), `get_thread`, `list_root_messages`.
- **Wakeability**: bewust géén wake-lus. Berichten worden bezorgd via
  `mail_messages` + `mail_receipts`; levende sessies checken hun inbox via de
  `SessionStart`-/`UserPromptSubmit`-hooks of via de MCP-tool
  `agent_mail_check_inbox`. Een apart tmux-nudge-pad leverde nul verzonden
  berichten en geen actieve caller — verwijderd 2026-08-15 (kaart
  `64b259f6…`). Cross-session nudgen blijft mogelijk via Claude Code's
  `SendMessage` of Codex' eigen pane-nudge; die paden lopen buiten Agent Mail.
- **Prompt-context**: `build_session_start_context` (identiteit + roster + inbox-
  samenvatting voor de `SessionStart`-hook), `build_prompt_submit_context` (one-liner
  reminder voor `UserPromptSubmit`, alleen als er unread/pending is).

## REST API (`backend/app/api/v1/agent_mail.py`, prefix `/agent-mail`)

Geen auth — matcht de bestaande unauthenticated-local posture (zelfde als kanban,
scheduled-messages). Endpoints: `GET /team`, `PATCH /members/{id}`, `POST/GET /messages`,
`GET /messages/{id}/thread`, `POST /messages/{id}/read`, `POST /messages/{id}/ack`,
`POST /agent/register`, `GET /agent/inbox`,
`POST /hooks/{session-start,user-prompt-submit,session-end,post-tool-use}`,
`GET /install/status`, `POST/DELETE /install/claude-code/*`, `POST/DELETE
/install/codex/*`, `GET /install/snippets`.

## MCP-tools (`backend/app/mcp_server/tools/agent_mail.py`, geregistreerd op de
bestaande gedeelde MCP-server)

Identiteit is een **expliciet argument** (net als upstream en de eerder verwijderde
kanban-variant — de gedeelde MCP-server threadt de bearer-token-context nog niet door
naar individuele tool-calls; spoofbaar maar acceptabel in het lokale single-user
trust-model, gedocumenteerd zoals upstream dat ook doet).

- `agent_mail_whoami()` — registreert/ververst eigen sessie, retourneert eigen member +
  unread/pending counts.
- `agent_mail_list_team()` — lijst van alle members (naam, rol, status, repo).
- `agent_mail_check_inbox(unread_only=True, limit=20)`.
- `agent_mail_send_message(to_member_id, body, subject="")`.
- `agent_mail_reply(thread_root_id, body)` — detecteert automatisch of dit een
  `answer` (pending context_request aan zichzelf) of gewoon `message` is.
- `agent_mail_ack_message(message_id)`.
- `agent_mail_request_context(to_member_id, topic, why_needed="", files_or_symbols=None)`.
- `agent_mail_create_handoff(to_member_id, summary, files=None, next_steps=None)`.

## Hooks / install

**Claude Code**: nieuwe module `app/services/agent_mail/hook_installer.py`, zelfde
additieve-merge-vorm als `scheduling/hook_installer.py` maar voor de 4 Agent-Mail-events
(`SessionStart`, `UserPromptSubmit`, `SessionEnd`, `PostToolUse`), commando's die POSTen
naar `/api/v1/agent-mail/hooks/{event}` en (voor SessionStart/UserPromptSubmit) de
JSON-response als `hookSpecificOutput.additionalContext` laten renderen door Claude Code
zelf (curl-body wordt letterlijk doorgegeven, geen apart shim-script nodig voor Claude
Code — alleen voor Codex, zie hieronder, is een echt executable nodig).

**Codex CLI**: `~/.codex/hooks.json` editen (geen bestaand Cockpit-equivalent) — vereist
wél een klein Python-shim-script (`backend/mcp_shim_lite/agent_mail_hook.py`, geport van
upstream's `agent_mail_hook.py`, want Codex's hooks.json heeft een echt argv nodig, geen
shell-curl-one-liner). **Geen** aparte Codex MCP-registratie (`codex mcp add`) — Codex
verbindt met de bestaande gedeelde Cockpit-MCP-server via dezelfde bearer-token-config
als elke andere Codex MCP-integratie in deze fork.

Backup-before-mutation via het bestaande `Backup`-model/patroon, zoals upstream.

## Frontend (`frontend/src/features/agent-mail/`)

Poort van de 11 upstream-bestanden (types, api, utils, `AgentMailPage`, `TeamTab`,
`RequestsTab`, `ComposeDialog`, `ThreadDialog`, `InstallTab`, `MemberEditDialog`,
`AgentMailHelpDialog`), met twee gerichte aanpassingen aan fork-conventies:

1. `body_markdown`/charter-invoervelden (`ComposeDialog`, `ThreadDialog`-reply,
   `MemberEditDialog`) door `MarkdownPreviewToggle` i.p.v. upstream's plain `Textarea`
   — consistent met hoe de read-kant (`MarkdownRenderer`) het al rendert.
2. `RequestsTab`-message-cards door `CLICKABLE_CARD` i.p.v. losse "Thread"-knoppen.

Route `/agent-mail`, sidebar-item (`Mail`-icoon). Presence-pagina blijft — upstream
verving die sidebar-link in dezelfde commit, maar Presence bestaat hier nog volop en
wordt niet aangeraakt.

## Tests

`backend/tests/agent_mail/` — model/service/messaging/registry/hooks-API/external-API,
in de stijl van upstream's testsuite maar zonder team-slot-scenario's. Frontend:
`npm run lint` + `npm run build` groen (geen bestaand testbestand-patroon voor losse
features in dit fork behalve `*.test.tsx` waar aanwezig — volg dat waar zinvol).

## Implementatievolgorde

1. **Datamodel** (`models/agent_mail.py`) + `repo_utils.py` — fundament.
2. **Service-laag** (`agent_mail_service.py`) + Pydantic-schemas.
3. **Interne REST API** (`api/v1/agent_mail.py`) — los testbaar via curl.
4. **MCP-tools** op de bestaande gedeelde server — **backend-restart nodig** voordat
   nieuwe tools zichtbaar zijn (bekend patroon in dit fork).
5. **Hooks + install** (Claude Code additive-merge-installer; Codex hooks.json + shim).
6. ~~**Externe orchestratie-API** (`external_agent_mail.py` + actor-model)~~ ✅ Geïmplementeerd (kaart `5fca30d0…`): verwijderd.
7. **Frontend** (`features/agent-mail/*`, route, sidebar) — `npm run build` na afloop.
8. **Tests + docs + ship** (direct-mode merge naar master).

## Bekende beperkingen (bewust, gedocumenteerd zoals upstream)

- Zichtbaarheid is machine-breed; elk lokaal lid is zichtbaar voor elk ander lid.
- Eén team-member per repo (git-worktrees van dezelfde repo delen één member).
  Same-repo multi-participant: expliciete follow-up, niet in v1.
- Geen auth op de interne API (matcht bestaande unauthenticated-local posture); externe
  API heeft wél auth (token + rate-limit), maar is expliciet "a local trust boundary,
  not a general network authentication system" (letterlijk zoals upstream documenteert).
- MCP-identiteit is een spoofbaar expliciet argument (zelfde trade-off als overal elders
  in dit fork's lokale MCP-tools).
- Wakeability is geen Agent-Mail-verantwoordelijkheid meer (commit `6a03dc83`).
  Sessies checken hun inbox via de `SessionStart`-/`UserPromptSubmit`-hooks of via
  de MCP-tool `agent_mail_check_inbox`; cross-session nudgen loopt via Claude
  Code's `SendMessage` of Codex' pane-nudge.

## Implementatienotities (na de bouw)

- **`mcp.call_tool()` retourneert een tuple, geen platte lijst.** De bestaande
  `/api/v1/mcp-server`-route (`handle_mcp_post`, `tools/call`) itereerde
  `mcp.call_tool(...)`'s resultaat rechtstreeks alsof het de content-lijst was. De
  geïnstalleerde FastMCP-versie retourneert met `convert_result=True` (wat elke
  `@mcp.tool()` hier impliciet gebruikt, want ze retourneren allemaal een platte
  `str`) een `(content_blocks, structured_result)`-tuple. Dat brak `tools/call` voor
  **elke** tool op de server, niet alleen Agent Mail's — pas ontdekt tijdens een
  live smoke-test van de MCP-tools via de echte HTTP-route (de bestaande testsuite
  testte alleen `mcp.call_tool()` in-process, nooit via `tools/call` over HTTP).
  Gefixt in een aparte, expliciet gelabelde commit (`fix(mcp-server): ...`) met een
  regressietest die de bug reproduceert.
- **Test-DB is niet per-test geïsoleerd.** `Base.metadata.create_all` reset niets;
  rijen accumuleren over de hele pytest-run (en zelfs over herhaalde losse
  `pytest`-aanroepen binnen dezelfde sessie, want de SQLite-file blijft bestaan).
  Assertions die "alle rijen in tabel X" tellen zijn dus fragiel — scope ze op
  specifieke, `tmp_path`-afgeleide of `uuid4`-afgeleide identiteiten in plaats van
  op een verwachte absolute lijst-lengte.
- **`npm install` was nodig** — deze worktree had geen `node_modules`, in
  tegenstelling tot wat de sessie-eind-instructies impliceren. `npm run build`/`lint`
  falen anders meteen met `tsc: not found`.
- **Live smoke-test i.p.v. browser-UI-test.** De standaard dev-poorten (8000/5173)
  waren bezet door een andere gelijktijdige sessie; die is bewust niet aangeraakt.
  In plaats daarvan draaide een geïsoleerde `uvicorn`-instantie op poort 8099 voor
  een live curl-smoke-test van de volledige REST- en MCP-flow (register → send →
  inbox → team-roster, plus `agent_mail_whoami`/`send_message`/`check_inbox` via
  `tools/call`), en een `GET /agent-mail`/`GET /`-check dat de SPA-route correct
  resolvet via de production static-serving-pad. Geen browser-automatisering
  beschikbaar in deze omgeving voor een visuele check.
- Verder geen afwijkingen van het plan — alle 23 taken zijn geïmplementeerd zoals
  gespecificeerd in `docs/superpowers/plans/2026-07-08-agent-mail-implementation.md`.
