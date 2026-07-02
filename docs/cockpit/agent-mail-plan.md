# Agent Mail — inter-agent communicatie (analyst-plan)

> **Status:** analyse afgerond, klaar voor implementatie door `developer`.
> **Card:** "Agent Mail — inter-agent communicatie" (kanban, priority medium).
> **Auteur:** analyst-sessie `k-claude-cockpi-4658`, 2026-06-19.

## 1. Wat is gevraagd (scope)

Uit de card: *"Durable per-repo identiteiten, structured context requests, handoffs, en
een inspectable team mailbox. Complementeert de kanban-workflow."*

Agent Mail is een **berichtenlaag tussen agents**, naast (niet in plaats van) de
kanban-flow. Vier bouwstenen:

1. **Durable per-repo identiteiten** — vandaag is de enige identiteit het *efemere*
   tmux-sessie-label `agent:k-…-4658` (sterft met de sessie). We willen een stabiele
   identiteit per repo die sessie-churn overleeft.
2. **Structured context requests** — een agent kan een andere agent gericht om context
   vragen (getypeerd verzoek met subject/body, optioneel aan een card gekoppeld), met een
   antwoord-pad. Vandaag bestaat alleen `report_impediment` (dumpt naar de Impediment-kolom).
3. **Handoffs** — expliciete overdracht van werk + context van agent A naar agent B.
   Vandaag is overdracht impliciet: een kolomwissel, waarna de volgende agent *koud*
   opnieuw context moet afleiden (zie `build_card_prompt`: elke spawn start cold).
4. **Inspectable team mailbox** — een UI waar de mens alle inter-agent-berichten ziet.

### In scope (v1)
- `AgentIdentity` + `AgentMessage` tabellen in de kanban-store.
- Service-laag (`mail.py`), REST-endpoints, Pydantic-schemas.
- MCP-tools zodat agents mail kunnen sturen/lezen.
- Dispatch/prompt-integratie: agent krijgt zijn **durable handle** in de prompt + eventuele
  openstaande handoff/context-berichten voor de card worden **inline** meegegeven (warme
  start i.p.v. koud).
- Frontend: **Mailbox**-pagina + mail-indicator in de `CardDrawer`.
- Tests (backend) + spec-doc.

### Out of scope (v1) — bewust uitgesteld
- **Proactieve push/injectie** in een *levende* sessie (de "push-on-idle initiative layer"
  uit `kanban-followups.md`, via de scheduled-messages delivery-engine op de `Stop`-hook).
  v1 is **pull**: agents checken hun inbox op natuurlijke momenten. Wel zo *ontworpen* dat
  push later bovenop kan.
- **Multi-device sync** van mail. Mail leeft bewust **buiten de op-log** (zie §3), consistent
  met het bevroren sync-spoor (`sync-hlc-freeze-vs-prune.md`).
- Cross-repo mail, bijlagen/bestanden, rijke threading voorbij request→response, en
  access-control/auth (lokaal single-user trust-model).

## 2. Bestaande patronen om op te bouwen (codebase-oriëntatie)

| Onderdeel | Locatie | Relevantie voor Agent Mail |
|---|---|---|
| Kanban-store (apart van app.database) | `backend/app/kanban/db.py` (`KanbanBase`, `KanbanSessionLocal`, `kanban_engine`) | Mail-tabellen komen **hier** op `KanbanBase`. |
| Materialized state buiten de op-log | `KanbanColumn`, `KanbanMeta` in `models.py` + directe CRUD in `service.py` | **Precedent**: niet elke kanban-tabel zit in de op-log. Mail volgt dit (directe CRUD, géén `apply_operation`). |
| Op-log mutatiepijplijn | `operations.py::apply_operation` | Bewust **niet** gebruiken voor mail (zie §3). |
| Read-side service | `service.py` (directe SQLAlchemy selects) | Stijl-template voor `mail.py`. |
| Agent-identiteit vandaag | claim `agent:<session>`; `_mint_session_name`; persona = `.claude/agents/<col>.md`; kolomnaam == agentnaam | Handle leidt af van persona/rol (analyst, developer, testing, code-review). |
| MCP-server voor agents | `mcp_server.py` (FastMCP "cockpit-kanban", SSE op `/kanban-mcp`) | Nieuwe mail-tools komen hier; identiteit expliciet als argument (zoals `claim_card(claimed_by)`). |
| Prompt-bouw bij spawn | `dispatch.py::build_card_prompt` | Hier injecteren we de handle + inline pending mail. |
| `.mcp.json`-provisioning | `kanban/router.py::enable` | Tools zijn automatisch beschikbaar (zelfde SSE-server); geen extra provisioning nodig. |
| Additieve sqlite-migraties | `db.py::_ensure_*` (handmatig, geen Alembic) | Nieuwe **tabellen** maakt `create_all` vanzelf; alleen ALTERs vragen guards. Mail = alleen nieuwe tabellen → laag risico. |
| Frontend kanban-feature | `frontend/src/features/kanban/` (Board/Column/CardItem/CardDrawer, `api.ts`, `types.ts`) | Template voor `features/mailbox/`. Volg `CLICKABLE_CARD`, `MODAL_SIZES`, `MarkdownRenderer`. |
| Workflow-handoff | `workflow.py` + `.claude/workflows/card-flow.json` | Mail **complementeert** dit; geen wijziging aan de flow-regels nodig. |

## 3. Kernbeslissing: mail buiten de op-log

**Beslissing: dedicated tabellen in de kanban-store, NIET de op-log.**

Afweging:
- *Op-log hergebruiken* (`entity_type="mail"`): gratis activity-feed/replay/sync, maar
  koppelt mail aan card-lifecycle, vervuilt de card-activity, en sleept de hele
  CRDT/HLC-machinerie mee voor iets dat niet card-scoped hoeft te zijn.
- *Dedicated tabellen* (zoals `KanbanColumn`/`KanbanMeta` al doen): schone scheiding, simpele
  queries, eigen lifecycle. Geen sync — maar sync is **FROZEN** en YAGNI (zie followups).

Het tweede past bij de expliciete maturiteitsrichting ("minder machine voor de huidige
scope"). Als sync ooit herleeft, krijgt mail zijn eigen seam of wordt het gemigreerd — dat
is een bewuste, latere keuze. **Flag dit expliciet in de spec.**

## 4. Datamodel

In `backend/app/kanban/models.py` (op `KanbanBase`):

```python
class AgentIdentity(KanbanBase):
    __tablename__ = "agent_identities"
    id: str (pk, uuid hex)
    project_key: str (index)          # durable per-repo scope
    handle: str                       # "analyst" | "developer" | "testing" | "code-review" | "human"
    display_name: str | None
    last_session: str | None          # tmux-sessie die de identiteit nu belichaamt (bv. k-…-4658)
    created_at: datetime
    last_seen_at: datetime | None
    # UNIQUE(project_key, handle)

class AgentMessage(KanbanBase):
    __tablename__ = "agent_messages"
    id: str (pk, uuid hex)
    project_key: str (index)
    from_handle: str
    to_handle: str | None             # None = broadcast naar het hele team
    kind: str                         # "context_request" | "context_response" | "handoff" | "note"
    subject: str
    body: str (Text)
    card_id: str | None (index)       # optionele koppeling aan een kanban-card
    in_reply_to: str | None           # message-id waarop dit antwoordt (request→response thread)
    status: str                       # "unread" | "read" | "answered"
    created_at: datetime (index)
    read_at: datetime | None
```

Identiteit = `(project_key, handle)` met handle = de **rol** (durable: een nieuwe
developer-sessie erft de developer-mailbox; een handoff die wacht terwijl geen developer
leeft, blijft staan tot de volgende developer-sessie hem leest → async handoff werkt).
Trade-off om te benoemen: twee gelijktijdige developer-sessies delen één mailbox. Acceptabel,
want `_project_is_busy` (dispatch.py) laat normaal max één agent-card per project tegelijk toe.

`db.py`: nieuwe tabellen worden door `create_all` aangemaakt; **geen** `_ensure_*`-guard nodig
(die zijn alleen voor ALTER op bestaande tabellen). Voeg wél een korte comment toe dat mail
buiten de op-log valt.

## 5. Service-laag — `backend/app/kanban/mail.py` (nieuw)

Directe SQLAlchemy, stijl van `service.py` (geen `apply_operation`). Functies:

- `ensure_identity(s, project_key, handle, *, session=None) -> AgentIdentity`
  upsert op `(project_key, handle)`; werkt `last_session`/`last_seen_at` bij.
- `list_identities(s, project_key) -> list[AgentIdentity]`
- `send_message(s, project_key, from_handle, to_handle, kind, subject, body, card_id=None, in_reply_to=None) -> AgentMessage`
  valideert `kind`; bij `context_response` met `in_reply_to`: zet het bron-request op `answered`.
- `list_inbox(s, project_key, handle, *, unread_only=False, include_broadcast=True) -> list[AgentMessage]`
  berichten met `to_handle == handle` OF (`to_handle is None` en `include_broadcast`).
- `list_sent(s, project_key, handle)` / `list_thread(s, root_message_id)` (request + responses).
- `get_message(s, message_id)` / `mark_read(s, message_id, reader_handle) -> AgentMessage`
  (zet `status="read"`, `read_at=now` als reader de recipient is).
- `pending_for_card(s, project_key, card_id, handle) -> list[AgentMessage]`
  ongelezen handoff/context-berichten voor (card, recipient) — gebruikt door dispatch-prompt.

Validatieconstanten (`MESSAGE_KINDS`, `MESSAGE_STATUSES`) in `schemas.py`.

## 6. Pydantic-schemas — `backend/app/kanban/schemas.py`

Toevoegen: `IdentityResponse`, `MessageResponse` (`from_attributes`), `SendMessageRequest`
(`from_handle`, `to_handle?`, `kind`, `subject`, `body`, `card_id?`, `in_reply_to?`),
`MarkReadRequest` (`reader_handle`), `EnsureIdentityRequest`.

## 7. REST API — `backend/app/api/v1/kanban/`

Voeg een **mail-subrouter** toe (`kanban/mail_router.py`, prefix `/kanban/mail`,
geïnclude vanuit `router.py`) of breid de bestaande kanban-router uit. Endpoints:

| Method + path | Doel |
|---|---|
| `GET  /kanban/mail/identities?project_key=` | lijst identiteiten |
| `POST /kanban/mail/identities` | upsert identiteit (UI = `human`) |
| `GET  /kanban/mail/inbox?project_key=&handle=&unread_only=` | inbox |
| `GET  /kanban/mail/messages?project_key=&card_id=` | mail per card (voor CardDrawer) |
| `POST /kanban/mail/messages` | bericht versturen |
| `GET  /kanban/mail/messages/{id}/thread` | request→response-thread |
| `POST /kanban/mail/messages/{id}/read` | markeer gelezen |

Alle reads via `KanbanSessionLocal`, zoals de rest van de kanban-router. Geen auth (matcht
de bestaande unauthenticated-local posture; `enable`-allowlisting blijft een aparte followup).

## 8. MCP-tools voor agents — `backend/app/kanban/mcp_server.py`

Thin wrappers over `mail.py`. Identiteit **expliciet** als argument (MCP/SSE is stateless;
de server weet niet wie belt — net als `claim_card(claimed_by)`). Spoofbaar maar acceptabel
in het lokale trust-model; documenteer dit.

- `send_mail(project, from_handle, to_handle, kind, subject, body, card_id=None)`
- `request_context(project, from_handle, to_handle, subject, body, card_id=None)`
  → `send_message(kind="context_request")`.
- `respond_context(project, from_handle, in_reply_to, body)`
  → `send_message(kind="context_response", in_reply_to=…)` + zet request op `answered`.
- `handoff(project, from_handle, to_handle, subject, body, card_id)` → `kind="handoff"`.
- `check_inbox(project, handle, unread_only=True)` → lijst (compacte dicts).
- `read_mail(message_id, reader_handle)` → markeer gelezen + return body.

> **Let op (memory `kanban-mcp-writes-fail`):** nieuwe MCP-tools worden pas zichtbaar na een
> **backend-restart**; de draaiende server reflecteert geen nieuwe tools. Herstart na Phase 3
> en verifieer via een echte tool-call. Hand-edit nooit `kanban.db`.

## 9. Dispatch / prompt-integratie — `backend/app/kanban/dispatch.py`

1. In `_run_card`, ná het claimen: `ensure_identity(s, project_key, handle=target_agent,
   session=name)` zodat de durable identiteit zijn live sessie kent.
2. In `build_card_prompt` (signature uitbreiden met `handle` + `pending_mail`):
   - Voeg een **"# Agent Mail"**-sectie toe: *"Je durable handle in dit project is
     `<handle>`. Check je inbox met `check_inbox`; vraag gericht context met
     `request_context`; draag werk over met `handoff`."*
   - Render eventuele `pending_for_card(...)`-berichten **inline** (warme start), en markeer
     ze als gelezen bij dispatch.
3. Caller (`_run_card`) haalt `pending_mail` op vóór `build_card_prompt`.

Dit is de hoogste-waarde, laagste-kost integratie: de volgende agent start *warm* met de
overgedragen context i.p.v. koud opnieuw af te leiden.

## 10. Frontend — `frontend/src/features/mailbox/` (nieuw) + CardDrawer

- `types.ts`, `api.ts` (fetch-wrappers naar `/api/v1/kanban/mail/*`), `MailboxPage.tsx`.
- Componenten: `MessageList` (filter op handle/kind/status), `MessageThread`
  (request→response), `ComposeDialog` (mens stuurt als `human`; `MODAL_SIZES`,
  `MarkdownPreviewToggle` voor de body). Body weergeven met `MarkdownRenderer`.
- `App.tsx`: route `/mailbox`; sidebar-nav-entry in `components/layout/`.
- `kanban/components/CardDrawer.tsx`: een **Mail**-tab/indicator die
  `GET /kanban/mail/messages?card_id=` toont (berichten die naar deze card verwijzen).
- **Memory `frontend-served-from-dist-at-8000`:** na frontend-wijzigingen `npm run build`
  draaien — een server-restart toont de wijziging niet.

## 11. Tests + docs

- `backend/tests/test_kanban_mail.py`: identity-upsert (durability over sessies),
  send/inbox/broadcast, request→response zet `answered`, `mark_read`, `pending_for_card`.
  - **Memory `kanban-tests-drop-live-db` (KRITIEK):** kanban-fixtures doen `drop_all` op de
    relatieve `./kanban.db`. Draai pytest **altijd vanuit de worktree-backend-dir**, nooit de
    main checkout, anders wis je het live board. Gebruik dezelfde geïsoleerde test-DB-fixture
    als de bestaande kanban-tests. (Memory `shared-venv-cd-to-worktree-backend`: venv staat in
    de main checkout; `cd` naar de worktree-backend.)
  - Memory `backend-suite-db-isolation-flake`: een paar sqlalchemy-tests flaken op
    collectievolgorde in een volledige run — niet jouw regressie; herdraai geïsoleerd.
- `docs/cockpit/agent-mail-spec.md`: korte spec (model, tools, pull-vs-push, mail-buiten-op-log).
- Frontend: `npm run lint` + `npm run build` groen.

## 12. Implementatievolgorde (fasen + dependencies)

1. **Model + store** (`models.py`, comment in `db.py`). — fundament, blokkeert al het andere.
2. **Service + schemas** (`mail.py`, `schemas.py`). — hangt op 1.
3. **REST API** (`mail_router.py`, `router.py`). — hangt op 2; los testbaar via curl.
4. **MCP-tools** (`mcp_server.py`). — hangt op 2; **backend-restart** daarna.
5. **Dispatch/prompt-integratie** (`dispatch.py`). — hangt op 2; raakt gedeelde
   `build_card_prompt` (bestaande dispatch-tests bijwerken).
6. **Frontend mailbox + CardDrawer** (`features/mailbox/*`, `App.tsx`, `CardDrawer.tsx`).
   — hangt op 3; `npm run build`.
7. **Tests + spec-doc + ship.**

Fasen 3/4/5 zijn na 2 parallel mogelijk, maar 4 en 5 raken bestaande modules — sequentieel
mergen.

## 13. Risico's / aandachtspunten

1. **Kanban-tests wissen het live board** als ze vanuit de verkeerde CWD draaien — strikt
   vanuit de worktree-backend draaien (memory).
2. **Nieuwe MCP-tools vragen een backend-restart** voordat ze werken (memory).
3. **MCP statelessness → identiteit is spoofbaar** (expliciet argument). Acceptabel lokaal;
   documenteren.
4. **Identiteits-granulariteit** (rol vs sessie). Rol gekozen voor durability; concurrente
   gelijke rollen delen een mailbox (zeldzaam door de busy-cap).
5. **Mail zit buiten de op-log → niet gesynct.** Consistent met frozen sync; flaggen voor als
   sync herleeft.
6. **Geen migration-framework**, maar alleen nieuwe tabellen → `create_all` volstaat.
7. **`frontend/dist`**: `npm run build` na frontend-wijzigingen (memory).
8. **Concurrent sessies op dezelfde checkout** (memory): hercheck git-state vóór merge/push.
9. **Pull-only v1**: agents moeten *geïnstrueerd* worden hun inbox te checken (prompt-sectie);
   zonder push wordt mail anders pas bij de volgende spawn gelezen. De inline-pending-mail in
   de dispatch-prompt dekt het belangrijkste geval (handoff bij card-pickup).

## 14. Definition of done

- Mail-tabellen + `mail.py` + REST + MCP-tools + dispatch-prompt-integratie + Mailbox-UI +
  CardDrawer-mail-indicator werkend.
- Backend-tests groen (vanuit worktree-backend); `npm run lint` + `npm run build` groen.
- Een agent kan via MCP `request_context`/`handoff`/`check_inbox`; de mens ziet alles in de
  Mailbox-UI. Een handoff op een card verschijnt warm in de prompt van de opvolgende agent.
- `docs/cockpit/agent-mail-spec.md` aanwezig.
- Geship volgens ship mode **direct** (merge naar master).
