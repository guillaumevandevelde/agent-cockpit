---
title: "CC 2.1.224 native cross-session SendMessage + ListAgents — adopt, integrate, or position against Agent Mail?"
type: decision
status: decided
---

**Datum:** 2026-08-10, herzien 2026-08-13
**Status:** besloten (herzien)
**Kaart:** `f8f2684747b449839221480aec73d974`, herziening `30d45e5ffdb249b79c69f80f6853eec3`
**Uitkomst:** Optie B — dunner maken. De 2026-08-10 uitkomst (A, keep) is vervangen; zie
[§ Herziening 2026-08-13](#herziening-2026-08-13--vier-assen-gemeten-uitkomst-naar-b).
Agent Mail houdt zijn roster en install-laag, en verliest zijn berichten-, wake- en
externe-actor-laag. Nog steeds geen integratie met de native laag.

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

## Keuze: (a) — vervangen op 2026-08-13

> Deze keuze is achterhaald. Lees
> [§ Herziening 2026-08-13](#herziening-2026-08-13--vier-assen-gemeten-uitkomst-naar-b):
> de dragende premisse (kanban-koppeling) staat niet in de code, en de meting
> draaide de uitkomst naar B. De tekst hieronder blijft staan als vastlegging
> van wat er op 2026-08-10 besloten is.

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

---

## Herziening 2026-08-13 — vier assen gemeten, uitkomst naar B

**Uitkomst: dunner maken.** Agent Mail houdt zijn roster- en install-laag en
verliest zijn berichten-, wake- en externe-actor-laag. Drie redenen, alle drie
gemeten op 2026-08-13:

1. De premisse onder de 2026-08-10 keuze — "Agent Mail is kanban-gekoppeld" —
   staat niet in de code.
2. In 36 dagen zijn er nul berichten verstuurd, terwijl de sessieregistratie
   1540 keer vuurde.
3. De native laag adresseert onze gedispatchte sessies wél, met tmux-doel erbij.

### 1. De premisse die niet klopt

`agent-mail-spec.md` en de keep-onderbouwing hierboven schrijven dat elk bericht
in `kanban_ops` landt als activity-feed-regel, gekoppeld aan een `repo_id` en een
gedispatchte run. Dat gebeurt nergens.

```bash
grep -rn "kanban\|activity" backend/app/services/agent_mail_service.py \
  backend/app/services/external_agent_mail_service.py \
  backend/app/api/v1/agent_mail.py backend/app/mcp_server/tools/agent_mail.py
```

De enige `kanban`-treffer is een docstring-vergelijking in
`backend/app/mcp_server/tools/agent_mail.py:4`. Geen import van kanban-state,
geen schrijfpad. `MailMessage` (`backend/app/models/agent_mail.py:76-98`) heeft
geen kaart-id en geen run-id; de kolommen zijn afzender, ontvanger, `kind`,
`body_markdown`, `payload` en `request_status`. Het woord `activity` in
`backend/app/api/v1/agent_mail.py:202-210` is een sessie-statusregel, bewaard op
`mail_agent_sessions.activity` — bijvoorbeeld `edited dispatch.py`. Dat is geen
kanban-op.

Daarmee vervalt de "audit-winst die de native laag niet kan repliceren". Die
winst bestaat niet.

### 2. De vier assen

#### As 1 — bereik: welke sessies zijn adresseerbaar

Agent Mail adresseert een **repo**, niet een sessie. De identiteit is
`identity_key = f"repo:{repo_id}"` (`backend/app/services/agent_mail_service.py:61-69`)
en de ontvanger van een bericht is een lid
(`MailMessage.recipient_member_id`, `backend/app/models/agent_mail.py:88-90`).
Alle worktrees van deze repo delen dus één adres. Gemeten in de registry-DB:
2 leden tegenover 1540 sessieregistraties.

De native laag adresseert een **levend proces**, met een naam afgeleid van de
werkmap. Gemeten met `ListAgents` op 2026-08-13, verbatim:

```
k-research-onde-6e3e-6f [3f0f08]  ·  interactive  ·  busy  ·  tmux k-research-onde-6e3e:@3.%3
k-bug-rest-post-0007-ea [e18e50]  ·  interactive  ·  busy  ·  tmux k-bug-rest-post-0007:@4.%4
claude-cockpit-9c [c65ab2]  ·  interactive  ·  idle  ·  started 14m ago
```

Dat zijn twee gedispatchte Cockpit-worktree-sessies plus de interactieve sessie
van de operator. De registratie ligt in `~/.claude/sessions/<pid>.json` met
`name`, `cwd`, `status` en `messagingSocketPath`.

**Conclusie as 1: native adresseert fijner dan wij.** Wij kunnen niet één
worktree-sessie aanschrijven, native wel — inclusief het tmux-doel dat onze
eigen wake-laag zelf moet opzoeken. Kruis-machine dekt native ook (2.1.225).

#### As 2 — duurzaamheid: overleeft een bericht een crash

Agent Mail wint deze as. Berichten zijn rijen in de registry-DB
(`MailMessage`, `backend/app/models/agent_mail.py:76-98`) met per ontvanger een
lees-/ack-rij (`MailReceipt`, `:100-108`, uniek per bericht-lid-paar). Geen TTL,
dus ze overleven een herstart van backend en sessie.

De native laag doet geen store-and-forward. Bezorging is een schrijfactie naar
een socket van het ontvangende proces: `/run/user/1000/cc-socks/<pid>.sock`,
gemeten als `tmpfs` met `findmnt -no FSTYPE /run/user/1000`. Sterft de ontvanger,
dan faalt de bezorging — 2.1.224 maakte precies dat zichtbaar ("failed deliveries
are now reported as errors"). `dialogExpiry` ruimt vastgehouden berichten op.

**Conclusie as 2: alleen wij zijn duurzaam.** Met nul berichten in de tabel is er
tot nu toe niets duurzaams bewaard.

#### As 3 — zichtbaarheid: kan een mens de mailbox inzien

Agent Mail wint deze as. Er is een eigen pagina
(`frontend/src/features/agent-mail/AgentMailPage.tsx:42`, tabs Team/Requests/Install
op `:272-274`), een route (`frontend/src/App.tsx:76`) en een menu-item
(`frontend/src/lib/navigation.ts:66`). De REST-kant leest berichten en threads
(`backend/app/api/v1/agent_mail.py:68` en `:73`).

Native berichten zijn alleen zichtbaar in de transcript van de ontvangende
sessie. Er is geen derde-partij-venster.

**Conclusie as 3: alleen wij hebben een venster.** Dat venster toont vandaag een
lege inbox en een roster van twee leden, waarvan er één een restrij van een
probe is (`repo_name` = `tmp`, `repo_path` = `/tmp`).

#### As 4 — externe toegang: kan een tool buiten de CLI berichten sturen

Agent Mail wint deze as op papier. Er is een aparte facade met twaalf routes
(`backend/app/api/v1/external_agent_mail.py:59-172`), een actor-model met
bearer-token (`backend/app/services/external_agent_mail_service.py:69` en `:99`)
en een limiet van 30 berichten per 60 seconden.

Native is uitsluitend CLI-naar-CLI. Er is geen externe API.

**Conclusie as 4: alleen wij hebben externe toegang.** Gemeten: nul actors ooit
geregistreerd, dus nul externe tools die 'm gebruiken.

### 3. De gebruiksmeting

```bash
python3 -c "
import sqlite3
con=sqlite3.connect('file:/home/vdvgu/claude-cockpit/backend/claude_registry.db?mode=ro',uri=True)
for t in ('mail_team_members','mail_agent_sessions','mail_messages','mail_receipts','mail_external_actors'):
    print(t, con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0])
"
```

Uitslag op 2026-08-13:

| Tabel | Rijen |
|---|---|
| `mail_team_members` | 2 |
| `mail_agent_sessions` | 1540 |
| `mail_messages` | **0** |
| `mail_receipts` | **0** |
| `mail_external_actors` | **0** |

Het oudste lid is aangemaakt op 2026-07-08, het jongste is bijgewerkt op
2026-08-13. De laag draait dus 36 dagen mee en registreert onafgebroken
sessies, maar er is nooit één bericht doorheen gegaan.

**Waarom nul: de laag is nooit aangesloten op de dispatch.** Geen enkele
persona en geen enkele dispatch-prompt noemt Agent Mail:

```bash
grep -rn "agent_mail\|Agent Mail" .claude/agents/*.md \
  backend/app/kanban/dispatch.py backend/app/kanban/analyst_prompt.py
```

Die zoekopdracht geeft nul treffers. Een gedispatchte sessie krijgt de acht
MCP-tools uit `backend/app/mcp_server/tools/agent_mail.py:35-186` dus wel
aangeboden, maar hoort nergens dat ze bestaan of waarvoor ze dienen. Nul is
daarmee "nooit aangesloten", niet "geprobeerd en afgewezen".

### 4. Twee routes, en waarom het dunner maken wordt

**Route 1 — alsnog aansluiten.** Persona- en prompttekst schrijven zodat
sessies de tools gaan gebruiken. De reden om dat te doen was de kanban-audit uit
§1. Die bestaat niet, dus deze route vraagt eerst het bouwen van de koppeling die
we dachten te hebben — en levert daarna een tweede bericht-kanaal naast een
native kanaal dat onze sessies vandaag al fijner adresseert.

**Route 2 — dunner maken.** De drie assen waarop wij winnen zijn duurzaamheid,
zichtbaarheid en externe toegang. Alle drie hebben nul inhoud. De as waarop
native wint, bereik, is de as die de laag überhaupt bestaansreden gaf.

Route 2 wint. Wat blijft en wat verdwijnt:

**Blijft** — dit draagt zijn gewicht met 1540 registraties en toont ook
Codex-CLI-sessies, iets wat `ListAgents` niet doet:

- `MailTeamMember` en `MailAgentSession`, plus de status- en discovery-logica
  (`backend/app/services/agent_mail_service.py:61-353`).
- De hook-installer onder `backend/app/services/agent_mail/` en de
  install-routes (`backend/app/api/v1/agent_mail.py:221-259`).
- De Team- en Install-tab, en de MCP-tools `agent_mail_whoami` en
  `agent_mail_list_team` (`backend/app/mcp_server/tools/agent_mail.py:36` en
  `:47`).

**Verdwijnt** — drie brokken, elk met een eigen vervolgkaart, elk met een
volledige caller-sweep door frontend én backend in de acceptance criteria:

| Brok | Kern-bestanden | Vervolgkaart |
|---|---|---|
| Externe orchestratie-API + actor-model | `backend/app/api/v1/external_agent_mail.py`, `backend/app/services/external_agent_mail_service.py`, `MailExternalActor` | kind 1 |
| Wake-/nudge-lus | `agent_mail_service.py:346-353`, `:589-678`, route `:93` in `api/v1/agent_mail.py` | kind 2 |
| Berichten-kern + mailbox-UI | `MailMessage`, `MailReceipt`, zes MCP-tools, `ComposeDialog`/`RequestsTab`/`ThreadDialog` | kind 3 |

De drie hangen aan elkaar via echte consumptie, niet via volgorde: de externe
service roept `wake_members_with_results` aan
(`backend/app/services/external_agent_mail_service.py:147`) en `send_message`
(`:127`). Kind 2 wacht dus op kind 1, kind 3 op allebei.

### 5. Wat deze herziening niet verandert

- **Geen integratie met de native laag.** De afwijzing van optie (c) hierboven
  blijft staan: er is geen openbaar wire-format om tegenaan te bouwen.
- **De roster blijft van ons.** `ListAgents` toont alleen Claude Code; onze
  registratie ziet ook Codex CLI.

### 6. Heropenen bij

- Een externe tool meldt zich aan als actor. Dan krijgt as 4 inhoud en is de
  externe API geen dode code meer.
- De native laag krijgt een publiek gedocumenteerd protocol. Dan wordt optie (c)
  opnieuw interessant.
