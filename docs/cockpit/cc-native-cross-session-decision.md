---
title: "CC 2.1.224 native cross-session SendMessage + ListAgents — adopt, integrate, or position against Agent Mail?"
type: decision
status: besloten
---

**Datum:** 2026-08-10
**Status:** besloten
**Kaart:** `f8f2684747b449839221480aec73d974`
**Uitkomst:** Optie A — keep. Agent Mail blijft de kanban-coupled autoritatieve record; CC 2.1.224's native `SendMessage`/`ListAgents` als transport-agnostisch broertje ernaast, geen integratie of deprecatie.

# CC 2.1.224 native cross-session SendMessage + ListAgents — adopt, integrate, of position against Agent Mail?

## Aanleiding

Claude Code 2.1.224 (7 aug 2026) shipte native cross-session messaging
tussen willekeurige Claude Code-sessies op macOS en Linux, plus een
`ListAgents` discovery-primitive. Twee nieuwe settings:
`crossSessionInbound` (gate voor binnenkomende berichten op een sessie
met bypassed permissions) en `dialogExpiry` (auto-cleanup van onafgehaalde
berichten). Geinstalleerde CLI op deze box: **2.1.226** — de feature is
live.

## Bron

[CHANGELOG.md op anthropics/claude-code](https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md)
— verbatim 2.1.224 entries relevant voor deze afweging:

- *Added cross-session `SendMessage`: Claude Code sessions can now message
  each other, on any of your machines, with `ListAgents` to discover them
  (macOS and Linux).*
- *Added `crossSessionInbound` and `dialogExpiry` settings: cross-session
  messages sent to a session running with bypassed permissions are held
  for your approval, and messages to other sessions auto-deliver.*
- *Fixed `SendMessage` reporting "Message sent" when the write to a
  teammate's inbox had actually failed; failed deliveries are now
  reported as errors.*

2.1.225 follow-up relevant (verbatim):

- *Fixed cross-session messages staying parked without a notice or
  expiry in headless sessions and during startup.*
- *SendMessage can now start a conversation with your Remote Control
  sessions on other machines by name (`ListAgents` shows them as
  `name [ref]`), instead of only replying after they message you first.*

2.1.226: alleen bug-fixes, geen cross-session-wijzigingen.

## Capability-mapping tegen Agent Mail (`docs/cockpit/agent-mail-spec.md`)

| Capability | CC 2.1.224 native | Cockpit Agent Mail |
|---|---|---|
| Cross-session messaging | `SendMessage`, macOS/Linux | `agent_mail_send_message`, `agent_mail_reply`; werkt op alle platforms via Codex CLI + Claude Code |
| Discovery | `ListAgents` (live sessies, by name) | `agent_mail_list_team()` (durable repo-members, met `mailbox_status`) |
| Permission-gating inbound | `crossSessionInbound` setting | Geen auth op interne API (bewust, lokaal trust-model); externe API heeft bearer-token + rate-limit |
| Auto-expire | `dialogExpiry` setting | `mail_receipts.read_at`/`acked_at`; geen TTL — blijft tot expliciete cleanup |
| Structured requests (context/handoff/answer) | Geen — platte messages | Eersteklas: `mail_messages.kind` ∈ `message`/`broadcast`/`context_request`/`handoff`/`answer`, met `request_status` lifecycle |
| Wakeability (tmux-pane nudge) | Niet expliciet — context die in een runway-Claude-Code-sessie binnenkomt wordt door dezelfde sessie opgepakt | `auto_nudge_members` (30s cooldown per member) + `queue_inbox_check` (handmatig); hergebruikt `tmux_inject.send_text` |
| Inspectable mailbox UI | Alleen via CC's eigen UI (lijst per sessie) | Eigen `frontend/src/features/agent-mail/` (Team/Requests/Install-tabs + inbox/messages REST API) |
| Durable per-repo identity | Niet — CC native is identity-loos, sessions ontdekken elkaar via `name` | `mail_team_members` met `repo_id` (sha1 van git-common-dir), één per repo |
| Kanban-audit Koppeling | Geen | `kanban_ops` activity-feed entries; berichtenpaar traceerbaar per dispatched run |
| External-tool orchestratie | Niet — alleen CC↔CC | `/api/v1/external-agent-mail/*` + `MailExternalActor` token-model (loopback-registratie, SHA-256 bearer, 30/60s rate-limit) |
| Cross-machine | Ja — "any of your machines" | Codex CLI nudge als de pane op een andere machine draait; geen canonieke cross-machine-message-bus |

## Drie posities (+ onderbouwing per as)

### (a) Keep — Agent Mail als kanban-coupled autoritatieve record; CC native als transport-agnostisch broertje

**Onderhoud:** 0 regels — Agent Mail blijft wat het is. CC native wordt
niet aangeraakt. Bestaande integratie-paden (MCP, hooks, installer,
frontend) gaan door.

**Productverhaal:** Agent Mail richt zich op *werkende autonoom
gedispatchte agent-sessies* die met elkaar en met de kanban praten —
elke message is gekoppeld aan een `repo_id` + heeft een kanban-activity-
feed entry + optioneel een `kind` (request/handoff/answer). CC native
richt zich op een *mens die zelf meerdere Claude Code-sessies open heeft*
op verschillende machines — ad-hoc, geen kanban, geen audit. Twee
complementaire gebruikers, twee verschillende tools. Een gedispatchte
engineer-sessie op deze Cockpit weet niet dat CC native live is en
hoeft dat ook niet — Agent Mail's `SendMessage`-equivalent is al een
MCP-tool die de sessie zelf kan oproepen.

**Bewijs dat ze niet overlappen:** CC native heeft geen `kind`-taxonomie
(`context_request`/`handoff`/`answer`), geen durable team-roster, geen
kanban-deliverable-koppeling, geen externe-tool-actor-model, geen
Frontend met inbox-UI binnen Cockpit, geen wakeability-loop (alleen
passieve inbox-aanwas). De kanban-coupling is precies wat de kaart
expliciet noemt als "the audit/observability win CC's native cannot
replicate".

**Beperking die wel erkend moet worden:** een gebruiker die buiten
Cockpit meerdere Claude Code-sessies op verschillende machines draait
én ze met elkaar wil laten praten, gebruikt vandaag terecht CC native.
Agent Mail verplicht hem eerst een Cockpit op te zetten. Acceptabel:
onze doelgroep is operator-die-Cockpit-gebruikt, niet
willekeurige-Claude-Code-gebruiker.

### (b) Deprecate — laat CC native de runtime afhandelen, houd alleen kanban-coupled audit

**Onderhoud:** schrappen van ~23 taken uit
`docs/superpowers/plans/2026-07-08-agent-mail-implementation.md` plus de
bijbehorende 11 frontend-bestanden + 4 MCP-tools + 5 backend touch
points. Fors eenmalig werk, daarna nul onderhoud aan de runtime.

**Productverhaal:** breekt. CC native praat niet met de kanban, niet met
de activity-feed, niet met deliverables. Een bericht dat binnenkomt in
een gedispatchte sessie laat geen spoor na op de kaart — geen audit,
geen pairing met de regel waar het over ging. De
`request`/`handoff`/`answer`-lifecycle (engineer-sessie A vraagt
engineer-sessie B om context, B antwoord, A leest antwoord en past
fix toe) is in CC native niet eens uit te drukken. Dat is geen
"audit verliezen" — dat is een **workflow verliezen** die vandaag
bestaat. De winst van deprecation weegt daar niet tegenop.

### (c) Integrate — surface CC's `SendMessage`/`ListAgents` als MCP-tools die Agent Mail delegeert

**Onderhoud:** 1 nieuwe wrapper-laag (`mcpx`-over CC-side protocol) +
mapping van CC's `name`-identity op onze `repo_id`-identity + 2 nieuwe
MCP-tools (`agent_mail_send_message_native`, `agent_mail_list_agents_native`)
+ failure-mode als de CC-side verandert. Onderhoudsverplichting op een
externe, niet-gedocumenteerde wire-format die Anthropic zonder
changelog-notificatie kan wijzigen.

**Productverhaal:** één uniforme naam voor de gebruiker. Verliest het
onderscheid tussen "message binnen Cockpit's audit" en "message via
je losse Claude Code-sessie". Dat onderscheid is voor de kanban wel
degelijk zinvol — zie (a): een bericht aan een gedispatchte agent
moet aan die kan, een bericht aan een externe CC-sessie niet.

**Kost versus baten:** ~2-3 dagen eenmalig + permanent risico dat
CC's protocol verandert. Geen meetbaar voordeel ten opzichte van (a) —
een gebruiker die beide wil, kan vandaag al CC native aan zijn
eigen Claude Code-sessie geven naast Agent Mail aan zijn
gedispatchte sessie.

## Keuze: (a)

Agent Mail blijft. CC 2.1.224 native krijgt een **externe erkenning**
in `docs/cockpit/agent-mail-spec.md` ("Wat Agent Mail niet is"), geen
wijziging aan de code.

Twee aanvullende beslissingen die deze keuze meteen meeneemt:

1. **Geen automatische deprecation.** De kanban-coupling is een
   workflow-feature, niet alleen audit — zie (b) onderbouwing.
2. **Geen integratie (c).** De draagbare-formaat-pijn (Anthropic's
   `SendMessage` heeft geen openbaar wire-format dat wij kunnen
   documenteren — de changelog noemt alleen de gebruikerskant) plus
   de ingebakken risico's bij elke Anthropic-side wijziging wegen
   niet op tegen de marginale UX-winst.

## Verhouding tot kanban-followups.md "Upstream Agent Team Presets — deliberately NOT adopted" (2026-07-08)

Die eerdere beslissing wees upstream's Agent Team Presets /
launch-orchestration af als een **concurrerend orchestratie-paradigma**
naast kanban-dispatch. CC 2.1.224 native is **geen** orchestratie-
paradigma — het is een transport-primitive voor messaging. De twee
redeneringen verschillen:

- Team Presets concurreren *op het vlak* waar Cockpit kanban-dispatch
  al de autoriteit heeft (wie doet wat, wanneer, op welke pane).
- CC native messaging concurreert *niet* op dat vlak — het zit een
  laag lager (transport van tekst tussen sessies), niet in de
  dispatch-flow.

De non-adoption-precedent legt dus **geen** gewicht in de schaal
voor deze beslissing; we zijn niet "weer upstream aan het
afwijzen", we zijn "upstream's nieuwe transport-primitive
 erkennen naast onze bestaande applicatie-feature".

## Onderhoudsverhaal voor de gekozen route

**Wat we niet doen (geen nieuwe code):**

- Geen MCP-tool die CC native aanroept (afgewezen: (c) onderbouwing).
- Geen verandering aan Agent Mail's `mail_messages`, `mail_team_members`,
  `mail_receipts` schema.
- Geen wijziging aan de 4 bestaande MCP-tools, de frontend, de
  hooks, of de externe API.

**Wat we wel doen (één heel klein doc-pad):**

- Eén kruisverwijzing-paragraaf in `docs/cockpit/agent-mail-spec.md`
  onder "Wat Agent Mail niet is": CC 2.1.224 native is een
  transport-agnostisch broertje voor ad-hoc cross-machine messaging
  buiten Cockpit om, zonder kanban-coupling. Link naar deze
  beslissing. Volgt mee in een vervolgkaart of direct in deze kaart
  als de registrant dat vraagt; voor deze kaart is alleen het
  beslisdoc + register-rij de deliverable.

**Heropenen** bij:

- CC native krijgt een **kanban-bridge** (een publieke hook die
  berichten als kanban-activity-ops op een kaart zet) — dan wordt
  Agent Mail's audit-monopolie uitgehold en moet (a) herzien worden.
- Agent Mail blijkt in de praktijk **écht onderbenut** door
  dispatched sessies (geen inbound messages, geen handoffs, geen
  context-requests gemeten over een release) — dan wegen de
  onderhoudskosten niet meer op tegen de workflow-winst, en wordt
  deprecation (b) heroverwogen.
- CC's protocol + wire-format worden **publiek gedocumenteerd**
  door Anthropic, zodat (c) zijn externe-afhankelijkheids-risico
  verliest — dan wordt integratie (c) heroverwogen.
