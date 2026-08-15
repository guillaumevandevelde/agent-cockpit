---
title: "Agent Mail — roster + discovery"
type: spec
status: active
---

# Agent Mail — roster + discovery

> Per-repo, durable identity for arbitrary Claude Code / Codex CLI sessions,
> plus a roster tab, install hooks, and two MCP tools (`agent_mail_whoami`,
> `agent_mail_list_team`). Messaging, mailbox state, request lifecycles, and
> the
> Codex/Claude-Code wake-lus are gone (kaart `46930d26…`, commit `6a03dc83` +
> de externe-actor-API weg in commit `14472c35`); cross-session nudging
> loopt via Claude Code's native `SendMessage` of Codex' pane-nudge, buiten
> Agent Mail.

## Herkomst en scope-beslissing

Geport uit upstream `adrirubio/claude-deck`, met drie bewuste uitzonderingen
en drie lagen die later dunner zijn geworden:

- **`945d0bd`/`ee0d9e9` ("same-repo participants") — niet geport.** Die
  leunen op een aparte upstream-feature (Agent-Team-*presets/slots*) die
  niet in deze fork zit. Fork-groepering loopt via `RunGroup`/`RunMembership`
  in `services/runs/groups.py`. **v1 = één durable member per repo.**
- **`6e1546f` (Codex plan snapshots) — niet geport.** Functioneel losstaand
  van Agent Mail; aparte toekomstige kaart.
- **Berichten-kern (uitkomst 2026-08-13, kaart `30d45e5f…`)** — verwijderd
  door deze kaart (`46930d26…`): `mail_messages`/`mail_receipts`, zes
  bericht-MCP-tools (`check_inbox`/`send_message`/`reply`/`ack_message`/
  `request_context`/`create_handoff`), de mailbox-frontend
  (`ComposeDialog`/`RequestsTab`/`ThreadDialog`), de message-REST-routes
  en de bericht-helft van `build_session_start_context` /
  `build_prompt_submit_context`. Nul berichten in 36 dagen; CC 2.1.224
  shipte native `SendMessage`/`ListAgents` die onze gedispatchte sessies
  fijner adresseren dan wij — per sessie i.p.v. per repo, met tmux-doel
  erbij. Wat blijft: roster, discovery, hooks, install, twee MCP-tools.
  Volledige onderbouwing: [`cc-native-cross-session-decision.md` §
  Herziening 2026-08-13](cc-native-cross-session-decision.md).

## Wat Agent Mail niet is

Claude Code 2.1.224 shipte native `SendMessage`/`ListAgents` — ad-hoc
cross-session messaging tussen willekeurige CC-sessies op macOS/Linux,
zonder Cockpit of kanban. Agent Mail vervangt die niet; beide bestaan
naast elkaar met aanvullende scope.

Agent Mail levert een per-repo identiteit + roster, MCP-registratie
voor Claude Code/Codex, en een Team-tab die ook Codex-sessies toont —
iets wat de native `ListAgents` niet doet. Geen berichten-laag meer in
deze module.

## Wat hergebruikt wordt (i.p.v. herbouwd)

Upstream bouwde voor Agent Mail een aantal subsystemen die deze fork al
**in andere vorm** heeft:

| Upstream bouwde | Cockpit heeft al | Actie |
|---|---|---|
| Eigen stdio MCP-shim (`mcp_shim/agent_mail_server.py`) + `codex mcp add`-installer per provider | Generieke, bearer-token-authed MCP-server (`app/mcp_server`, Streamable-HTTP op `/api/v1/mcp-server`, `MCPAccessToken` met `agent_name`) | Mail-tools registreren op de bestaande server (`app/mcp_server/tools/agent_mail.py`). Geen apart transport, geen Codex `mcp add`-installer nodig. |
| Eigen tmux-pane-scanner voor "observed sessions" | `app/services/runs/discovery.py::discover_agent_sessions()` — scant al panes + matcht `claude-code`/`codex-cli`-processen | Hergebruiken in `sync_observed_sessions()`. |
| Repo-identiteit (`repo_utils.py`, git-common-dir-gebaseerd) | Geen equivalent | Nieuw, bijna 1-op-1 geport (~40 regels). |
| Claude Code hook-installer (curl-based, additive merge in `settings.json`) | Vergelijkbaar patroon in `scheduling/hook_installer.py`, maar andere events/URL | Nieuwe kleine module, zelfde idempotente-merge-vorm. |
| Codex CLI hook-installer (`~/.codex/hooks.json` editen) + `codex mcp add` | Codex is al eersteklas provider (`providers/codex_cli.py`, `get_codex_home()`) maar geen bestaande hook-installer | Hooks.json-editing geport; MCP-registratie niet (zie boven — geen aparte shim nodig). |
| ~~Mail-berichten-modellen + mailbox-frontend + zes bericht-MCP-tools~~ | — | ✅ Verwijderd 2026-08-15 (kaart `46930d26…`): nul berichten in 36 dagen, rooster + sessies-ontdekking is het hele verhaal. |
| ~~`MailExternalActor`-model + externe REST/``hooks`` API~~ | — | ✅ Verwijderd 2026-08-15 (commit `14472c35`): geen externe actor ooit geregistreerd. |
| ~~Wakeability/``wake_members``-lus + tmux nudge-pad~~ | — | ✅ Verwijderd 2026-08-15 (commit `6a03dc83`): geen actieve caller; cross-session nudging loopt via Claude Code `SendMessage` of Codex pane-nudge, buiten deze module. |
| Frontend `agent-mail`-feature (roster, member-edit, install, help) | `CLICKABLE_CARD`, `MODAL_SIZES`, `MarkdownRenderer`, `MarkdownPreviewToggle`, `sonner` (al aanwezig) | Geport in afgeslankte vorm: alleen Team- + Install-tab, lid-edit-dialoog en Help; ``ComposeDialog``/``RequestsTab``/``ThreadDialog`` zijn weg. |

## Datamodel (`backend/app/models/agent_mail.py`, hoofd-DB via `Base`)

Twee tabellen, `create_all` volstaat (geen migratie-framework):

- **`mail_team_members`** — durable identiteit, één per repo. `id` PK,
  `repo_id` (uniek, sha1 van git-common-dir via `derive_repo_identity`),
  `repo_path`, `repo_name`, `display_name`, `role`, `charter`,
  `created_at`, `updated_at`. Geen `team_preset_id`/`team_slot_id`
  (zie scope-beslissing).
- **`mail_agent_sessions`** — efemere sessie gekoppeld aan een member.
  `id` PK, `member_id` FK CASCADE, `cli` (`claude-code`/`codex-cli`/
  `unknown`), `source` (`hook`/`mcp`/`observed`), `session_key` (uniek),
  `cwd`, `tmux_target`, `pane_id`, `pid`, `mailbox_status`
  (`connected`/`observed`/`offline`), `activity`, `last_seen_at`
  (indexed), `created_at`.

**Dode tabellen na de 2026-08-15-sloopronde**: `mail_messages` en
`mail_receipts` blijven bestaan in de live DB tot de volgende alembic
`op.drop_table` (geen migratie-framework hier, dus bewust niet aangeraakt
in deze commit — kaart `46930d26…` §"Gedeeld risico"). Nieuwe code leest
ze niet meer en schrijft er niets in.

## Service-laag (`backend/app/services/agent_mail_service.py`)

Directe SQLAlchemy, singleton `agent_mail_service = AgentMailService()`.

- **Identiteit**: `get_or_create_repo_member(db, cwd)` (via
  `derive_repo_identity`), `register_session(db, request)` —
  hook/MCP-entrypoint, upsert member + session.
- **Liveness**: `heartbeat_session`, `mark_session_offline`,
  `heartbeat_member_mcp_session`, `_effective_status`
  (TTL/PID-gebaseerd: hook 180s, MCP 3600s + PID-liveness,
  observed 300s).
- **Discovery-sync**: `sync_observed_sessions(db)` — roept
  `runs.discovery.discover_agent_sessions()` (hergebruik, zie boven),
  matcht gevonden panes aan bestaande hook/MCP-sessies via PID-ancestry
  + repo-identiteit (voorkomt dubbele leden voor dezelfde logische
  sessie).
- **GC**: `_gc_stale_repo_members(db)` — verwijdert leden met een
  `repo_path` die niet meer op disk bestaan (pytest-tmp_path,
  scratchpad-dirs; nooit echte repo's).
- **Prompt-context**: `build_session_start_context` levert identiteit +
  roster als blok dat in elke gedispatchte sessie wordt geïnjecteerd.
  Geen inbox-samenvatting meer; berichten-zorg is uit deze module.

## REST API (`backend/app/api/v1/agent_mail.py`, prefix `/agent-mail`)

Geen auth — matcht de bestaande unauthenticated-local posture (zelfde als
kanban, scheduled-messages). Endpoints:

- `GET /team`, `PATCH /members/{id}`
- `POST /agent/register`
- `POST /hooks/session-start`, `POST /hooks/session-end`,
  `POST /hooks/post-tool-use`
- `GET /install/status`,
  `POST/DELETE /install/claude-code/*`,
  `POST/DELETE /install/codex/*`,
  `GET /install/snippets`

## MCP-tools (`backend/app/mcp_server/tools/agent_mail.py`)

Geregistreerd op de bestaande gedeelde Cockpit-MCP-server. Identiteit
is een **expliciet argument** (net als upstream en de eerder verwijderde
kanban-variant — de gedeelde MCP-server threadt de bearer-token-context
nog niet door naar individuele tool-calls; spoofbaar maar acceptabel in
het lokale single-user trust-model, gedocumenteerd zoals upstream dat
ook doet).

- `agent_mail_whoami(cwd, session_key)` — registreert/ververst eigen
  sessie, retourneert eigen member + repo.
- `agent_mail_list_team(cwd, session_key)` — lijst van alle members
  (naam, rol, status, repo), inclusief Codex-sessies waar
  `ListAgents` ze niet laat zien.

## Hooks / install

**Claude Code**: `app/services/agent_mail/hook_installer.py`, zelfde
additieve-merge-vorm als `scheduling/hook_installer.py` maar voor de 3
resterende Agent-Mail-events (`SessionStart`, `SessionEnd`,
`PostToolUse`); commando's POSTen naar
`/api/v1/agent-mail/hooks/{event}` en `SessionStart` geeft de
JSON-response terug als
`hookSpecificOutput.additionalContext` (Claude Code geeft curl-body
letterlijk door).

**Codex CLI**: `~/.codex/hooks.json` editen + klein Python-shim-script
(`backend/mcp_shim_lite/agent_mail_hook.py`, geport van upstream)
omdat Codex's hooks.json een echt argv nodig heeft, geen
shell-curl-one-liner. **Geen** aparte Codex MCP-registratie — Codex
verbindt met de bestaande gedeelde Cockpit-MCP-server.

Backup-before-mutation via het bestaande `Backup`-model/patroon, zoals
upstream.

## Frontend (`frontend/src/features/agent-mail/`)

Afgeslankte poort van de oorspronkelijke 11 upstream-bestanden. Wat
blijft: `AgentMailPage`, `TeamTab`, `MemberEditDialog`, `InstallTab`,
`AgentMailHelpDialog`, types in `@/types/agentMail`, en de api-wrapper
in `agent-mail/api.ts`. Twee tabbladen: Team en Install.

Drie message-only bestanden verwijderd in deze kaart
(`ComposeDialog.tsx`, `RequestsTab.tsx`, `ThreadDialog.tsx`);
`Requests`-tab en `New request`-knop weg uit `AgentMailPage.tsx`. Het
`MemberEditDialog` rendert de rooster-rij via de gedeelde
`<MarkdownPreviewToggle>` (fork-conventie t.o.v. upstream's plain
`Textarea`).

Route `/agent-mail`, sidebar-item (`Mail`-icoon).

## Tests

`backend/tests/agent_mail/` — `test_api.py` (register + team),
`test_discovery_sync.py` (observed sessions),
`test_hooks_api.py` (lifecycle hooks),
`test_mcp_tools.py` (whoami + list_team),
`test_registration.py`, `test_team_and_context.py`
(session-context + GC). Plus losse model- en schema-tests in
`backend/tests/test_agent_mail_model.py` /
`test_agent_mail_schemas.py`. Frontend: `npm run lint` + `npm run build`
groen.

## Implementatievolgorde (historisch)

1. **Datamodel** (`models/agent_mail.py`) + `repo_utils.py` — fundament.
2. **Service-laag** (`agent_mail_service.py`) + Pydantic-schemas.
3. **Interne REST API** (`api/v1/agent_mail.py`).
4. **MCP-tools** op de bestaande gedeelde server — backend-restart nodig.
5. **Hooks + install** (Claude Code additive-merge-installer;
   Codex hooks.json + shim).
6. ~~Externe orchestratie-API + actor-model~~ — ✅ verwijderd (commit
   `14472c35`).
7. **Frontend** (Team + Install, lid-edit, help).
8. ~~Berichten-kern + mailbox-frontend + zes MCP-tools~~ — ✅ verwijderd
   (kaart `46930d26…`).

## Bekende beperkingen (bewust)

- Zichtbaarheid is machine-breed; elk lokaal lid is zichtbaar voor elk
  ander lid.
- Eén team-member per repo (git-worktrees van dezelfde repo delen één
  member). Same-repo multi-participant: expliciete follow-up, niet in
  v1.
- Geen auth op de interne API (matcht bestaande unauthenticated-local
  posture).
- MCP-identiteit is een spoofbaar expliciet argument (zelfde trade-off
  als overal elders in dit fork's lokale MCP-tools).
- Wakeability is geen Agent-Mail-verantwoordelijkheid meer. Cross-
  session nudging loopt via Claude Code's `SendMessage` of Codex'
  pane-nudge, buiten deze module.
- De tabellen `mail_messages` en `mail_receipts` blijven in de live DB
  staan tot een expliciete alembic-migratie (geen framework in deze
  module); ze zijn dood — nieuwe code leest of schrijft ze niet.

## Implementatienotities (na de bouw)

- **`mcp.call_tool()` retourneert een tuple, geen platte lijst.** De
  geïnstalleerde FastMCP-versie retourneert met `convert_result=True`
  een `(content_blocks, structured_result)`-tuple. `tools/call` in
  `/api/v1/mcp-server` itereert dit resultaat verkeerd; gefixt in een
  aparte, expliciet gelabelde commit met een regressietest die de bug
  reproduceert.
- **Test-DB is niet per-test geïsoleerd.** `Base.metadata.create_all`
  reset niets; rijen accumuleren over de hele pytest-run. Assertions
  die "alle rijen in tabel X" tellen zijn fragiel — scope ze op
  specifieke, `tmp_path`- of `uuid4`-afgeleide identiteiten.
- **Live smoke-test i.p.v. browser-UI-test.** Standaard dev-poorten
  (8000/5173) waren bezet; in plaats daarvan een geïsoleerde
  `uvicorn`-instantie op een vrije poort voor live curl-smoke-test van
  de REST- en MCP-flow.
- Verder geen afwijkingen van het oorspronkelijke plan — alle
  basis-taken zijn geïmplementeerd zoals gespecificeerd in
  `docs/superpowers/plans/2026-07-08-agent-mail-implementation.md`; de
  latere dunner-maak-stappen staan in deze commit-message-geschiedenis.
