---
title: "Beslissing: ACP-adaptertransport voor OpenCode — go/no-go bij de geopende trigger-poort"
type: decision
status: decided
---

# Beslissing: ACP-adaptertransport voor OpenCode — go/no-go bij de geopende trigger-poort

**Datum:** 2026-07-28
**Status:** besloten (gemeten spike; geen implementatie in deze kaart)
**Kaart:** `a4a091fa3f6b4e209efed6014ac1ee4f` — "[spike][transport][GEPOORT — niet nu]
ACP-adaptertransport als SpawnTransport-sibling" = [`acp-transport-decision.md`](./acp-transport-decision.md) §6 kaart 5.
**Uitkomst:** **GO op een ACP-backed `SpawnTransport`-sibling voor `open-code`.** De ACP-adapter
van OpenCode is first-party, volwassen en gemeten werkend over stdio; de native route
(`opencode run --format json`) leverde in dezelfde niet-TTY-context **nul bytes**. NO-GO blijft
staan op "één ACP-client dekt N vendors" — dat is een tweede beslissing, niet deze.

**Trigger:** de poort uit [`acp-transport-trigger-gate.md`](./acp-transport-trigger-gate.md) is
**opengegaan** — zie §1.

**Verwant:** [`acp-transport-decision.md`](./acp-transport-decision.md) (§6 kaart 5 — de gepoorte
opdracht), [`acp-transport-trigger-gate.md`](./acp-transport-trigger-gate.md) (waarom de kaart op
2026-07-15 terecht níét beantwoord werd), [`structured-events-schema.md`](./structured-events-schema.md)
(het ACP-isomorfe event-model dat hergebruikt wordt),
[`headless-stream-json-transport-spike.md`](./headless-stream-json-transport-spike.md) (de eerste
transport-sibling), [`build-prioriteiten-analyse.md`](./build-prioriteiten-analyse.md) §3.3 / P3
(de hedge die de trigger ís).

---

## TL;DR

Op 2026-07-15 werd deze kaart gedispatcht vóór zijn eigen poort en correct gesloten met *geen
actie — trigger niet gevuurd*. **Die poort staat nu open**: de `open-code`-CLI heeft tussen
2026-07-27 en 2026-07-28 **37 kaarten naar Done gebracht** (§1). P3's eis — *"één tweede provider
die een kaart **afrondt** als proof-of-hedge"* — is daarmee ruimschoots vervuld, en de spike-vraag
is voor het eerst beantwoordbaar in plaats van een gok.

Het antwoord, gemeten en niet geschat:

1. **OpenCode 1.18.8 ships een first-party ACP-server** (`opencode acp`, een top-level commando in
   `--help`). Het is geen third-party shim; de `agentInfo` in het handshake-antwoord is
   `{"name":"OpenCode","version":"1.18.8"}` — dezelfde binary die het bord vandaag al dispatcht.
2. **De adapter is volwassen genoeg.** Een volledige `initialize` → `session/new` →
   `session/prompt`-cyclus liep end-to-end over stdio en schreef daadwerkelijk het gevraagde
   bestand. De geadverteerde `sessionCapabilities` (`resume`, `fork`, `list`, `close`) dekken
   exact de modi die `open_code.py:115-165` vandaag via TUI-vlaggen bouwt.
3. **`session/request_permission` werkt en is de getypeerde gating-haak die facet D vroeg** — mits
   OpenCode's permission-config op `ask` staat. Met de default-config vuurt hij *niet* (§3.3); dat
   is een configuratie-precondititie, geen ontbrekende feature. De payload draagt `kind`,
   `locations[].path` én een volledige unified diff — rijker dan wat pane-scraping ooit kan geven.
4. **De native route is in deze context níét bruikbaar gebleken.** `opencode run --format json`
   produceerde over een pipe (geen TTY) **0 bytes** binnen 100 s, ook voor een triviale prompt
   zonder tools, en ook met gesloten stdin. Dat is precies de contextvorm waarin een
   `SpawnTransport` draait. Zie §3.4 voor wat hier wél en niet uit volgt.

**Aanbeveling:** bouw de ACP-backed transport als vierde `SpawnTransport`-sibling, gescopet op
`open-code`. Hergebruik `structured_events.py` ongewijzigd — de isomorfie is gemeten en klopt op
vier van de zes ACP-varianten, met twee gemeten gaten (§4).

---

## 1. De poort is open — bewijs

[`acp-transport-trigger-gate.md`](./acp-transport-trigger-gate.md) §1 formuleerde de poort scherp:
niet een tweede *abonnement*, maar een tweede **CLI-vendor**, want ACP-adapters zijn per CLI. Die
as had op 2026-07-15 nog niet bewogen: *"Alleen `claude-code` dispatcht in de praktijk"*.

Dat is niet langer waar. Uit de backend-dispatch-logs (`logs/backend/run-*.log`, regels van de
vorm `dispatched card <id> (<kolom>) -> session <naam> (transport: worktree, provider: <cli>)`):

| CLI | dispatches | kaarten naar Done met deze CLI als láátste dispatch |
|---|---|---|
| `claude-code` | 486 | — |
| **`open-code`** | **45** | **37** |

De 37 kaarten liepen van `2026-07-27 19:29` tot `2026-07-28 13:35`; de meeste zijn na afronding
van het bord opgeruimd, maar hun Done-move staat in `kanban_ops`. Voorbeelden die elders in de
repo als afgerond gedocumenteerd staan: `4358fe0a…` (de product-taal-conventie, `kanban-conventions.md`
§5), `3027671c…` (de remote-branch-hygiëne-regel in `CLAUDE.md`), `c06a3a2a…`
(`recipe-writing-conventions.md`).

Het mechanisme dat dit mogelijk maakte is `_cli_id_for_opencode_provider`
(`backend/app/kanban/dispatch.py:146-171`): kiest de precedence-keten een OpenCode-hosted
subscription (`opencode-go` / `opencode`-Zen), dan schakelt de spawn-transport naar de
`open-code`-CLI. Kaart `4279448c…` draagt daar het zichtbare spoor van —
`dispatch_provider='opencode-go'`, `dispatch_model='glm-5.2'`.

**Conclusie:** P3's proof-of-hedge is geleverd, 37×. De vraag van §6 kaart 5 is niet langer
prematuur.

## 2. Wat er gemeten is

Alle metingen zijn gedaan tegen de geïnstalleerde binary op deze host, OpenCode **1.18.8**
(`opencode --version`). Reproductiecommando's staan in §7. Geen enkele claim in dit doc over
OpenCode's gedrag rust op documentatie of geheugen — alles hieronder is een waargenomen respons.

### 2.1 De ACP-server bestaat en is first-party

`opencode --help` noemt `acp` als top-level commando:

```
opencode acp                 start ACP (Agent Client Protocol) server
```

Dat plaatst OpenCode in dezelfde categorie als de agents waarvoor ACP bedoeld is: de adapter is
niet een externe brug die `opencode` inpakt, maar een modus van de CLI zelf.

### 2.2 Handshake-respons (`initialize`)

```json
{"protocolVersion":1,
 "agentCapabilities":{
   "loadSession":true,
   "mcpCapabilities":{"http":true,"sse":true},
   "promptCapabilities":{"embeddedContext":true,"image":true},
   "sessionCapabilities":{"close":{},"fork":{},"list":{},"resume":{}}},
 "authMethods":[{"id":"opencode-login","name":"Login with opencode",
                 "description":"Run `opencode auth login` in the terminal"}],
 "agentInfo":{"name":"OpenCode","version":"1.18.8"}}
```

Twee dingen zijn hier operationeel belangrijk voor Cockpit:

- **`sessionCapabilities.resume` / `.fork`** — Cockpit's `SpawnCommandOptions` kent precies de
  modi `plain` / `resume` / `fork` (`backend/app/services/agentic_cli/open_code.py:125-137`, waar
  ze vandaag naar `--session` / `--session --fork` vertalen). ACP dekt beide native, dus de
  resume-/fork-semantiek van het bord overleeft de transportwissel.
- **`mcpCapabilities`** — ACP geeft de client een getypeerd kanaal om MCP-servers aan een sessie
  mee te geven (`session/new` → `mcpServers`). Dat is het mechanisme waarlangs `cockpit-kanban`
  straks aan een ACP-sessie hangt, in plaats van via een `.mcp.json` op schijf.

### 2.3 Een volledige prompt-cyclus (`session/new` + `session/prompt`)

`session/new` gaf een sessie-id plus — onverwacht en nuttig — een `configOptions`-blok met de
volledige modelcatalogus (`opencode-go/glm-5.2`, `opencode-go/grok-4.5`, …) en de actuele keuze.
Dat is een getypeerde model-selectie-surface die Cockpit's provider/model-resolutie kan voeden
zonder de catalogus zelf bij te houden.

De prompt *"Create a file named probe.txt containing exactly the word HELLO"* leverde deze
`session/update`-varianten op, en het bestand werd daadwerkelijk geschreven (inhoud: `HELLO`):

| ACP `sessionUpdate` | aantal in de meting |
|---|---|
| `agent_thought_chunk` | 62 |
| `agent_message_chunk` | 15 |
| `tool_call` | 1 |
| `tool_call_update` | 2 |
| `usage_update` | 1 |
| `available_commands_update` | 1 |

De `tool_call_update`-reeks doorliep `pending` → `in_progress` → `completed`, met
`kind:"edit"` en `locations:[{"path":"…/probe.txt"}]`.

Het `session/prompt`-antwoord sloot af met:

```json
{"stopReason":"end_turn",
 "usage":{"inputTokens":308,"outputTokens":15,"totalTokens":29143,
          "thoughtTokens":20,"cachedReadTokens":28800}}
```

### 2.4 `session/request_permission` — gemeten, mét precondititie

In de eerste meting (§2.3, default-config) vuurde `session/request_permission` **niet**: de
write-tool liep van `pending` naar `completed` zonder gate. Een tweede meting met een
`opencode.json` in de werkdirectory die `{"permission":{"edit":"ask","bash":"ask"}}` zet, gaf wél
exact één permission-request vóór dezelfde write:

```json
{"method":"session/request_permission",
 "params":{"sessionId":"ses_…","toolCall":{
   "toolCallId":"call_…","title":"…/gated.txt","kind":"edit","status":"pending",
   "locations":[{"path":"…/gated.txt"}],
   "rawInput":{"filepath":"…/gated.txt","diff":"Index: …\n--- …\n+++ …"}}}}
```

Na het beantwoorden met `{"outcome":{"outcome":"selected","optionId":"<allow…>"}}` liep de turn
door en werd `gated.txt` geschreven.

**Dit is het antwoord op facet D uit de acceptatiecriteria.** De haak bestaat, is getypeerd, en
draagt een unified diff — een gate-UI kan de voorgestelde wijziging tonen vóór ze landt. De
precondititie is dat de spawn een permission-config meekrijgt die niet op auto-allow staat; dat is
een bewuste keuze die Cockpit per lane al maakt (`skip_permissions` in `kanban_meta`).

### 2.5 De native route gaf geen output

Drie runs, elk met `timeout` en output naar een pipe (geen TTY):

| commando | stdin | exit | bytes op stdout |
|---|---|---|---|
| `opencode run --format json "Create a file…"` | geërfd | 124 (timeout 150 s) | 0 |
| `opencode run --format json --model opencode-go/glm-5.2 "Reply with exactly: OK"` | geërfd | 124 (timeout 100 s) | 0 |
| `opencode run --model opencode-go/glm-5.2 "Reply with exactly: OK"` (geen `--format`) | geërfd | 124 (timeout 100 s) | 0 |
| `opencode run --format json --model opencode-go/glm-5.2 "Reply with exactly: OK"` | `< /dev/null` | 124 (timeout 110 s) | 0 |

`stderr` was in alle vier de gevallen leeg.

De laatste rij is de belangrijke controle: het ligt **niet** aan `--format json`. Plain
`opencode run` gedraagt zich identiek. Zie §3.4 voor de interpretatie — en voor wat hier
uitdrukkelijk *niet* uit volgt.

## 3. De afweging: adapter vs. native

### 3.1 De vraag zoals de kaart hem stelde

> *"evalueer de ACP-adapter van díe CLI tegen zijn native stream-json — is de adapter volwassen
> genoeg om één ACP-client N vendors te laten afdekken?"*

Die formulering bundelt twee vragen die uit elkaar horen, en het antwoord verschilt per vraag:

| vraag | antwoord |
|---|---|
| A. Is OpenCode's ACP-adapter volwassen genoeg om Cockpit's transport voor **`open-code`** te dragen? | **Ja** — §3.2 |
| B. Kan één ACP-client daarmee **N vendors** afdekken (ACP als universeel transport)? | **Nog niet beslisbaar** — §3.5 |

### 3.2 Voor `open-code`: ACP wint, en niet nipt

| as | ACP (`opencode acp`) | native (`opencode run --format json`) |
|---|---|---|
| werkt over stdio-pipes zonder TTY | **ja, gemeten** (§2.3) | **niet waargenomen** (§2.5) |
| incrementele events tijdens de turn | ja — 81 `session/update`-notificaties | n.v.t. (geen output) |
| getypeerde permission-gate | ja — `session/request_permission` (§2.4) | geen equivalent gedocumenteerd |
| resume / fork | ja — `sessionCapabilities` | via CLI-vlaggen |
| MCP-injectie per sessie | ja — `session/new.mcpServers` | via config op schijf |
| token-usage per turn | ja — `usage`-blok in het resultaat | n.v.t. |
| schema-stabiliteit | ACP-spec, versioned (`protocolVersion:1`) | CLI-eigen, ongespecificeerd |

De doorslaggevende as is de eerste. Een `SpawnTransport` ís een subprocess met pipes en zonder
TTY (`headless_runner.py` spawnt via `asyncio.create_subprocess_exec`). ACP werkte daar in de
eerste poging; de native modus leverde in vier pogingen niets.

### 3.3 Wat dit *niet* zegt over facet D

De default-config vuurt geen permission-request (§2.4). Een implementatie die aanneemt dat de gate
er altijd is, bouwt een gate die stil nooit dichtgaat — hetzelfde faalpatroon als een
`grep -qE "^OK:|WARNING:"`-assertie die in beide toestanden slaagt. De ACP-transport moet de
permission-config dus **expliciet meegeven** bij spawn en dat in een test vastleggen, niet erop
vertrouwen dat OpenCode's default hem aanzet.

### 3.4 Eerlijkheid over de native meting

De 0-byte-observatie is reproduceerbaar en in vier vormen bevestigd, maar **niet root-caused**.
Plausibele oorzaken die deze spike niet heeft uitgesloten: TTY-detectie in `opencode run`, een
interactieve keuze die op stdin wacht zonder naar stderr te schrijven, of een trage
model-round-trip voorbij de timeout. Wat de meting wél hard maakt:

- het ligt niet aan `--format json` (plain `run` doet hetzelfde);
- het ligt niet aan een ontbrekend model (`--model opencode-go/glm-5.2` expliciet gepind);
- het ligt niet aan een niet-werkende installatie of ontbrekende auth — dezelfde binary, dezelfde
  credentials en dezelfde werkdirectory-vorm leverden via ACP wél een volledige, geslaagde turn.

Voor de transportkeuze is dat genoeg: de route die aantoonbaar werkt in de doelcontext verslaat de
route die dat niet doet. Voor de capability-tabel is het níét genoeg om `headless_run` op
`unsupported` te zetten — daarom is dat een aparte kaart (§6) en geen conclusie hier.

✅ **Geïmplementeerd (kaart `470d0a90…`)** — `capabilities.py:134` is bijgewerkt zodat de
`headless_run`-beschrijving voor `open-code` (a) `opencode acp` als first-party ACP-server noemt,
(b) de `opencode run --format json`-route expliciet als 0-byte gemarkeerd laat, en (c) `opencode
serve` los benoemt als HTTP/SSE-pad in plaats van de pipe-gebaseerde headless-route. De
`codex-cli`-rij (`capabilities.py:88`) is in dezelfde commit als **ongemeten** gemarkeerd omdat de
Codex-CLI op deze host niet op PATH stond — alleen de prior-tekst blijft staan tot een reproduce
de claim verifieert.

### 3.5 Waarom vraag B (N vendors) open blijft

`acp-transport-decision.md` §6 zette ACP's belofte scherp: *één integratie voor meerdere vendors*.
Die belofte is met één gemeten vendor niet te toetsen. Wat we nu weten is dat OpenCode's adapter
goed genoeg is voor OpenCode. Of Codex' ACP-oppervlak (`codex-cli`, waarvan
`capabilities.py:88` de native `codex exec --json` route noteert) hetzelfde event-vocabulaire
dekt, is een tweede meting met een tweede binary — en dus een tweede kaart.

De aanbeveling is daarom bewust **niet** "vervang de stream-json-transport door ACP". De
claude-code-transport blijft wat hij is (`headless_runner.py:643`); ACP komt ernaast te staan als
sibling voor `open-code`. Dat is exact de additieve vorm die
`orchestration-substrate-decision.md` §5 voorschrijft.

## 4. Isomorfie-check tegen `structured_events.py`

De belofte van §6 kaart 5 was dat een ACP-transport het bestaande event-model hergebruikt in
plaats van een tweede vocabulaire uit te vinden. Die belofte is nu toetsbaar tegen echte ACP-events
in plaats van tegen de spec. `backend/app/services/agentic_cli/structured_events.py:17-27` legt de
mapping vast; hier is ze naast de **gemeten** OpenCode-events:

| gemeten ACP-event (§2.3/§2.4) | `StructuredEventType` | dekt het? |
|---|---|---|
| `agent_thought_chunk` | `message_chunk` (`role=thought`) | ✅ |
| `agent_message_chunk` | `message_chunk` (`role=assistant`) | ✅ |
| `tool_call` / `tool_call_update` | `tool_call` | ✅ — `status`, `kind`, `raw_input`, `raw_output` zijn alle vier aanwezig in de payload |
| `session/request_permission` | `permission_request` | ✅ — inclusief `options[].optionId` ↔ `PermissionOption.option_id` |
| `session/prompt` result (`stopReason` + `usage`) | `usage_result` | ✅ |
| **`usage_update`** (mid-turn) | `context_usage` | ✅ — variant toegevoegd in kaart `edbb8b91…` |
| **`available_commands_update`** | — | ❌ **geen counterpart** |
| *(niet waargenomen)* `plan` | `plan_update` | ⚠️ niet gemeten — de probe-taak was te klein voor een plan |

**Vijf van de zes gemapte varianten zijn gemeten en kloppen.** Het model draagt de ACP-events
zonder aanpassing; de casing (`camelCase` → `snake_case`) is inderdaad de enige vertaling, zoals
de docstring beweert.

Het ene overgebleven gat is `available_commands_update` — voor kaart 2 (de ACP-transport zelf)
wel relevant, maar geen model-uitbreiding: een ACP-adapter kan dit intern als `message_chunk`
met een vast stramien doorgeven zonder dat het schema ervoor open moet. `usage_update`
(`{"used":29108,"size":200000,"cost":{…}}`) is een **mid-turn** contextvenster-signaal — Cockpit
had daar geen event voor, terwijl het precies het soort signaal is waar `rate_limit` voor
claude-code voor bestaat (vroegtijdig pauzeren i.p.v. achteraf een 429 rapen). ✅ Geïmplementeerd
in kaart `edbb8b91…` als `context_usage`-variant: de bestaande `usage_result` (terminaal) en de
nieuwe `context_usage` (mid-turn) lezen dezelfde wire-velden maar betekenen verschillende dingen,
dus krijgen ze verschillende variants. De bestaande claude-code stream-json-mapper emitteert
`context_usage` niet (geen equivalent mid-turn signaal in Claude's stream); dat is geen bug.

## 5. Verdict

**GO** op een ACP-backed `SpawnTransport`-sibling voor `open-code`, met deze scope:

- **Wel:** een vierde transport naast `worktree`, `sandcastle` en `headless`, die `opencode acp`
  als subprocess spawnt, de JSON-RPC-stream naar `StructuredEvent` mapt, en
  `session/request_permission` als gate-haak aanbiedt.
- **Wel:** hergebruik van `structured_events.py` met één nieuwe variant (`context_usage`) voor
  `usage_update`.
- **Niet:** vervanging van de claude-code stream-json-transport. Die blijft.
- **Niet:** een uitspraak dat ACP nu het universele transport voor alle vendors is (§3.5).
- **Niet:** tmux vervangen voor human-in-the-loop werk — ongewijzigd t.o.v.
  `orchestration-substrate-decision.md` §5.

## 6. Vervolgkaarten

Aangemaakt als kind-kaarten van `a4a091fa…` in dezelfde sessie:

1. **ACP-transport als vierde `SpawnTransport`-sibling voor `open-code`** — de bouwkaart.
   Hangt af van (2), want de event-variant moet er zijn vóór de mapper hem kan emitteren.
2. **`usage_update`-variant toevoegen aan het structured-event-model** — de kleine
   model-uitbreiding uit §4, los uitvoerbaar en zonder afhankelijkheden.
3. **`headless_run`-capability voor `open-code` corrigeren naar de gemeten werkelijkheid** —
   `capabilities.py:134` noemt vandaag alleen `opencode serve`, niet de ACP-server, en de native
   `run --format json`-route is in §2.5 niet werkend waargenomen.

## 7. Reproductie

Alle metingen draaiden tegen `opencode 1.18.8` op deze host, vanuit een lege werkdirectory.

**Handshake (gratis — geen model-call):**

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{"fs":{"readTextFile":false,"writeTextFile":false}}}}' \
  | timeout 15 opencode acp
```

**Volledige turn + permission-gate:** schrijf in de werkdirectory een `opencode.json` met
`{"permission":{"edit":"ask","bash":"ask"}}`, spawn `opencode acp` met pipes, en stuur
achtereenvolgens `initialize`, `session/new` (met `cwd` + `mcpServers:[]`) en `session/prompt`.
Beantwoord elke inkomende `session/request_permission` met
`{"outcome":{"outcome":"selected","optionId":"<een allow-optie>"}}`. Zonder dat antwoord blokkeert
de turn — wat op zichzelf bevestigt dat de gate load-bearing is.

**Native-controle:**

```bash
timeout 100 opencode run --format json --model opencode-go/glm-5.2 "Reply with exactly: OK" > out.jsonl 2> out.err
wc -c out.jsonl   # gemeten: 0
```

**Poort-bewijs (§1)** — tel de dispatches per CLI uit de backend-logs:

```bash
grep -ho 'provider: [a-z-]*' logs/backend/run-*.log | sort | uniq -c
```

## 8. Bewust buiten scope

- **De ACP-transport bouwen.** Dit is een spike met een go/no-go als deliverable; de bouw is
  kaart (1) uit §6.
- **De native 0-byte-observatie root-causen.** §3.4 legt uit waarom de transportkeuze daar niet op
  wacht, en waarom de capability-tabel dat wél apart moet uitzoeken.
- **Codex' ACP-oppervlak meten.** Vraag B (§3.5) vraagt een tweede binary en een tweede meting.
- **De trigger-poort-infrastructuur repareren.** `acp-transport-trigger-gate.md` §4 filede daar al
  een aparte `[problem]`-kaart voor; deze kaart is juist het geval waarin de poort *terecht*
  openging.
