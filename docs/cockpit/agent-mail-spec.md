# Agent Mail — spec

> Inter-agent communicatie naast de kanban-flow: durable identiteiten, structured
> context requests, handoffs, en een inspectable team mailbox.
> Implementatie volgt `docs/cockpit/agent-mail-plan.md`.

## Model

Twee tabellen in de kanban-store (`KanbanBase`), **bewust buiten de op-log** — net
als `KanbanColumn`/`KanbanMeta`. Directe CRUD via `app/kanban/mail.py`, géén
`apply_operation`.

- **`AgentIdentity`** — `(project_key, handle)` uniek. De `handle` is de durable
  **rol** (`analyst` | `developer` | `testing` | `code-review` | `human`), niet de
  efemere tmux-sessie. Een nieuwe developer-sessie erft de developer-mailbox; een
  handoff die wacht terwijl geen developer leeft blijft staan tot de volgende
  developer-sessie hem leest → **async handoff** werkt. `last_session`/`last_seen_at`
  houden bij welke sessie de identiteit nu belichaamt.
- **`AgentMessage`** — `from_handle` → `to_handle` (None = broadcast naar het team),
  `kind` (`context_request` | `context_response` | `handoff` | `note`), `subject`,
  `body`, optionele `card_id`, `in_reply_to` (request→response thread), en `status`
  (`unread` | `read` | `answered`).

Granulariteits-trade-off: twee gelijktijdige developer-sessies delen één mailbox.
Acceptabel omdat `_project_is_busy` (dispatch) normaal max één agent-card per project
tegelijk toelaat.

## Service (`app/kanban/mail.py`)

`ensure_identity` (upsert), `list_identities`, `send_message` (valideert `kind`; een
`context_response` met `in_reply_to` zet het bron-request op `answered`),
`list_inbox` (recipient + broadcast), `list_sent`, `list_for_card`, `list_thread`,
`get_message`, `mark_read` (alleen als de reader de recipient is — broadcasts mag
iedereen lezen), `pending_for_card` (ongelezen handoff/context_request voor een
(card, recipient), voor de warme dispatch-start).

## REST (`/api/v1/kanban/mail/*`)

`GET/POST identities`, `GET inbox`, `GET/POST messages`, `GET messages/{id}/thread`,
`POST messages/{id}/read`. Geen auth — matcht de bestaande unauthenticated-local
posture.

## MCP-tools (`cockpit-kanban`)

`send_mail`, `request_context`, `respond_context`, `handoff`, `check_inbox`,
`read_mail`. Identiteit is een **expliciet argument** (MCP/SSE is stateless — de
server weet niet wie belt, net als `claim_card(claimed_by)`). Spoofbaar, maar
acceptabel in het lokale single-user trust-model.

> Nieuwe MCP-tools worden pas zichtbaar na een **backend-restart** (memory
> `kanban-mcp-writes-fail`). Herstart en verifieer via een echte tool-call.

## Dispatch-integratie

Bij het claimen van een card registreert `_run_card` de durable identiteit voor de
doelrol (`ensure_identity(..., agent_session=name)`) en haalt het openstaande
handoff/context-mail voor (card, rol) op. `build_card_prompt` rendert een
**`# Agent Mail`**-sectie met de durable handle + die berichten **inline** (warme
start i.p.v. koud opnieuw afleiden) en markeert ze als gelezen bij dispatch.

## Frontend

`features/mailbox/` — `MailboxPage` (inbox per handle, filter op kind, unread-only),
`ComposeDialog` (mens stuurt als `human`), `MessageThread` (request→response). De
`CardDrawer` krijgt een **Mail**-tab die de mail toont die naar die card verwijst.

## Pull vs push (v1 = pull)

v1 is **pull**: agents checken hun inbox op natuurlijke momenten (de prompt instrueert
ze met `check_inbox`), en de inline-pending-mail in de dispatch-prompt dekt het
belangrijkste geval (handoff bij card-pickup). Proactieve **push** in een levende
sessie (via de scheduled-messages delivery-engine op de `Stop`-hook) is bewust
uitgesteld, maar het model is zó ontworpen dat push later bovenop kan.

## Mail buiten de op-log → niet gesynct

Mail leeft bewust buiten de op-log en wordt dus **niet gesynct**, consistent met het
bevroren sync-spoor (`sync-hlc-freeze-vs-prune.md`). Als sync ooit herleeft, krijgt
mail zijn eigen seam of wordt het gemigreerd — een bewuste, latere keuze.
