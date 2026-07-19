---
title: "Agent Mail — upstream sync (adapted port)"
type: spec
status: active
---

# Agent Mail — upstream sync (adapted port)

> Cross-session berichten tussen willekeurige, losstaande Claude Code/Codex CLI-sessies
> (verschillende terminals/repo's) — durable per-repo identiteit, structured messages,
> een inspectable mailbox-UI, wakeability via tmux, en een externe REST-orchestratie-API
> voor lokale tools zoals OpenClaw.

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

## Wat hergebruikt wordt (i.p.v. herbouwd)

Upstream bouwde voor Agent Mail een aantal subsystemen die deze fork al **in andere vorm**
heeft. Adapted port = upstream's datamodel/servicelogica overnemen, maar de transport-/
infra-lagen aan bestaande Cockpit-primitieven hangen:

| Upstream bouwde | Cockpit heeft al | Actie |
|---|---|---|
| Eigen stdio MCP-shim (`mcp_shim/agent_mail_server.py`) + `codex mcp add`-installer per provider | Generieke, bearer-token-authed MCP-server (`app/mcp_server`, Streamable-HTTP op `/api/v1/mcp-server`, `MCPAccessToken` met `agent_name`) | Mail-tools registreren op de bestaande server (`app/mcp_server/tools/agent_mail.py`). Geen apart transport, geen Codex `mcp add`-installer nodig. |
| Eigen tmux `send-keys`-nudge (`_send_tmux_inbox_check`) | `app/services/scheduling/tmux_inject.py::send_text()` — zelfde literal+Enter-tweestap | Hergebruiken i.p.v. dupliceren. |
| Eigen tmux-pane-scanner voor "observed sessions" | `app/services/runs/discovery.py::discover_agent_sessions()` — scant al panes + matcht `claude-code`/`codex-cli`-processen | Hergebruiken in `sync_observed_sessions()`. |
| Repo-identiteit (`repo_utils.py`, git-common-dir-gebaseerd) | Geen equivalent | Nieuw, bijna 1-op-1 geport (~40 regels). |
| Claude Code hook-installer (curl-based, additive merge in `settings.json`) | Vergelijkbaar patroon in `scheduling/hook_installer.py`, maar andere events/URL | Nieuwe kleine module, zelfde idempotente-merge-vorm. |
| Codex CLI hook-installer (`~/.codex/hooks.json` editen) + `codex mcp add` | Codex is al eersteklas provider (`providers/codex_cli.py`, `get_codex_home()`) maar geen bestaande hook-installer | Hooks.json-editing geport; MCP-registratie **niet** (zie boven — geen aparte shim nodig). |
| `MailExternalActor` token/rate-limit-model voor externe tools | Geen equivalent (MCPAccessToken is voor MCP-toolcalls, niet voor een generieke bearer-authed REST-facade per actor) | Nieuw, zoals upstream. |
| Frontend `agent-mail`-feature (11 bestanden, ~2.260 regels) | `CLICKABLE_CARD`, `MODAL_SIZES`, `MarkdownRenderer`, `MarkdownPreviewToggle`, `sonner` (al aanwezig) | Geport, met 2 gerichte fixes: body/charter-velden door `MarkdownPreviewToggle` (upstream gebruikt inconsistent plain `Textarea` terwijl de read-kant wél `MarkdownRenderer` gebruikt); Requests-tab message-cards door `CLICKABLE_CARD` (upstream gebruikt losse knoppen). |

Wakeability-gedrag: upstream's uiteindelijke, gecorrigeerde staat (na `5d83b1d`) wordt
direct geïmplementeerd — nudge-eligibility = `provider in {"claude-code", "codex-cli"}`,
niet Codex-only (dat was een tussentijdse bug, meteen goed geport).

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
- **`mail_external_actors`** — durable identiteit voor externe orchestratie-tools.
  `id` PK, `actor_key` (uniek), `display_name`, `kind` (default `external_tool`),
  `description`, `token_hash` (SHA-256), `created_at`, `last_used_at`.
- **`mail_messages`** — `id` PK, `thread_root_id` FK zelf-referentieel, `kind`
  (`message`/`broadcast`/`context_request`/`handoff`/`answer`), `sender_member_id` FK
  SET NULL, `sender_actor_id` FK SET NULL (exclusief met `sender_member_id`),
  `recipient_member_id` FK CASCADE (null = broadcast), `subject`, `body_markdown`,
  `payload` (JSON), `request_status` (`pending`/`answered`/`acknowledged`, alleen
  voor `context_request`/`handoff`), `created_at` (indexed).
- **`mail_receipts`** — per-ontvanger read/ack-state. `id` PK, `message_id` FK CASCADE,
  `member_id` FK CASCADE, `read_at`, `acked_at`, `created_at`.
  `UNIQUE(message_id, member_id)`.

## Service-laag (`backend/app/services/agent_mail_service.py`)

Directe SQLAlchemy, singleton `agent_mail_service = AgentMailService()` (in-process
throttle-state voor auto-nudge-cooldown, net als upstream — reset bij restart,
acceptabel voor single-instance lokale deployment).

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
- **Wakeability**: `_session_can_nudge` (`source=="observed"`, provider in
  `{claude-code, codex-cli}`, heeft `tmux_target`, status nog `observed`),
  `_nudge_session_for_member`, `_wake_member` (hergebruikt `tmux_inject.send_text`),
  `auto_nudge_members` (best-effort, 30s cooldown per member, ná `db.commit()` van het
  bericht — delivery en wake zijn ontkoppeld), `queue_inbox_check` (handmatige wake, geen
  cooldown), `wake_members_with_results` (synchrone wake-rapportage voor de externe API).
- **Prompt-context**: `build_session_start_context` (identiteit + roster + inbox-
  samenvatting voor de `SessionStart`-hook), `build_prompt_submit_context` (one-liner
  reminder voor `UserPromptSubmit`, alleen als er unread/pending is).

## REST API (`backend/app/api/v1/agent_mail.py`, prefix `/agent-mail`)

Geen auth — matcht de bestaande unauthenticated-local posture (zelfde als kanban,
scheduled-messages). Endpoints: `GET /team`, `PATCH /members/{id}`, `POST/GET /messages`,
`GET /messages/{id}/thread`, `POST /messages/{id}/read`, `POST /messages/{id}/ack`,
`POST /members/{id}/queue-inbox-check`, `POST /agent/register`, `GET /agent/inbox`,
`POST /hooks/{session-start,user-prompt-submit,session-end,post-tool-use}`,
`GET /install/status`, `POST/DELETE /install/claude-code/*`, `POST/DELETE
/install/codex/*`, `GET /install/snippets`.

## Externe orchestratie-API (`backend/app/api/v1/external_agent_mail.py`, prefix
`/external/agent-mail`)

Voor lokale tools (bv. OpenClaw) die niet via MCP draaien. Twee-laags trust: actor-
*registratie* (`POST /actors`) alleen via loopback, geen credential; alle andere routes
vereisen `Authorization: Bearer <token>` (SHA-256-hash, `hmac.compare_digest`). Per-actor
rate limit 30 berichten/60s (in-memory, 429 + `Retry-After`). Ownership-isolatie: een
actor ziet alleen threads die hij zelf startte. Synchrone wake-rapportage in de response
(`delivery_state`, per-ontvanger `wake_attempted/wake_succeeded/wake_method`).

Routes: `POST /actors`, `GET /actors/me`, `GET /members`, `POST /messages`,
`POST /broadcasts`, `POST /context-requests`, `POST /handoffs`,
`POST /threads/{id}/replies`, `GET /threads/{id}`, `GET /requests/{id}/status`,
`GET /requests/{id}/wait?timeout_seconds=` (bounded long-poll, max 30s),
`POST /requests/{id}/ack`.

De interne `/agent-mail/messages`-route kan `sender_actor_id` niet spoofen (schema
accepteert het veld niet) — berichten via de interne route worden altijd toegeschreven
aan de mailbox-eigenaar zelf, nooit aan een external actor.

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
6. **Externe orchestratie-API** (`external_agent_mail.py` + actor-model).
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
- Wakeability werkt alleen voor `source=="observed"` tmux-sessies van `claude-code`/
  `codex-cli`; niet-tmux-sessies (bv. Sandcastle-containers) blijven pull-only
  (`check_inbox`).

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
