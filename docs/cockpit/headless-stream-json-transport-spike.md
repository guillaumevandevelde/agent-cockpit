---
title: "Spike: headless `stream-json`-transport (Claude) achter `SpawnTransport`"
type: analysis
status: decided
---

# Spike: headless `stream-json`-transport (Claude) achter `SpawnTransport`

**Datum:** 2026-07-15
**Status:** besloten — **GO** op de stream-json-transport, met één blokkerende voorwaarde (§5).
**Trigger:** kanban-spike "[spike][transport] Prototype headless stream-json-transport (Claude)
achter `SpawnTransport`" — [`acp-transport-decision.md`](./acp-transport-decision.md) §6 kaart 3
(= [`orchestration-substrate-decision.md`](./orchestration-substrate-decision.md) §6 kaart 1,
verrijkt). `depends_on`: kaart 2 → [`structured-events-schema.md`](./structured-events-schema.md).

**Verwant:** [`acp-transport-decision.md`](./acp-transport-decision.md) (de transport-keuze die
deze kaart invult), [`structured-events-schema.md`](./structured-events-schema.md) (het
ACP-isomorfe schema waarop we mappen),
[`orchestration-substrate-decision.md`](./orchestration-substrate-decision.md) (§2.1 vier-in-één
identiteit, §2.3 scraping-residu).

---

## TL;DR

**GO op de stream-json-transport** — het event-contract is empirisch geverifieerd en levert
precies de getypeerde signalen waarvoor `orchestration-substrate-decision.md` §2.3 vandaag
terminal-tekst scrape't. Twee bevindingen sturen de implementatie:

1. **Blokkerend (§5):** een headless run matcht **geen van de twee liveness-bronnen** in
   `reap_stale_claims` (`dispatch.py:2823` — `if name in live_sessions or name in
   sandcastle_live`). Zonder een **derde liveness-bron** wordt elke headless-kaart op de
   eerstvolgende tick gereap't en opnieuw gedispatcht — een dispatch-loop. Dit is geen
   hypothese: de docstring van `reap_stale_claims` documenteert dat sandcastle exact deze bug
   had en 'm oploste door `sandcastle_live` toe te voegen. Het precedent is de oplossing.
2. **De "vier-in-één identiteit" breekt níet** (§5.1). Van de vier (tmux-naam, `agent:`-claim,
   git-branch, worktree-dir) is er maar **één** tmux-specifiek: de tmux-naam als *liveness-orakel*.
   De andere drie zijn strings die headless onveranderd overleeft. De naam blijft de spil; alleen
   het orakel verandert. Dat is een aanzienlijk kleinere ingreep dan de kaarttekst suggereert.

Daarnaast: het ACP-isomorfe schema van kaart 2 **kan de belangrijkste bevinding niet
representeren** (§4.1). `stream-json` emit een getypeerd `rate_limit_event` — precies het signaal
dat de 429-substring-scrape overbodig maakt — en dát event heeft geen ACP-tegenhanger en dus geen
slot in het zes-varianten-schema. Het schema heeft een uitbreiding nodig vóór de transport erop
kan mappen.

---

## 1. Wat wél en niet gemeten is (lees dit vóór §6)

Eerlijkheid over de scope, want de kaart vraagt "gemeten betrouwbaarheid":

**Wel gedaan.** Twee directe probes van `claude -p --output-format stream-json --verbose`
(CLI-versie **2.1.209**) tegen een echte subscription, met volledige capture van de
event-stream. Daarmee is het **event-contract** empirisch vastgesteld (§3) — niet uit
documentatie afgeleid — en is de mapping op het ACP-isomorfe schema (§4) getoetst tegen echte
payloads i.p.v. veronderstelde. De liveness-/reaper-analyse (§5) is een read-only codelezing van
het daadwerkelijke dispatch-pad.

**Niet gedaan.** Er is **geen autonoom-gedispatchte executor-kaart door een aangesloten derde
`SpawnTransport`-sibling gedraaid.** Dat vereist productiewijzigingen aan de load-bearing
dispatcher (transport + liveness-bron + parser) — implementatiewerk, geen spike. Op een gedeelde
box met concurrente sessies zou een half-aangesloten transport bovendien precies de dispatch-loop
uit §5 op het echte bord veroorzaken.

**Wat dat betekent voor "gemeten betrouwbaarheid".** n=2 probes is een *smoke-test*, geen
betrouwbaarheidsmeting: beide runs exit'ten 0 en emitten exact één terminale `result`, maar dat
is te weinig om een faalpercentage op te baseren. De go/no-go hieronder rust dus **niet** op een
gemeten betrouwbaarheidscijfer, maar op iets sterkers en goedkopers: het *contract* is nu bekend
en getypeerd, en de één blokkerende faalmodus is gevonden vóórdat er code voor geschreven werd.
Dat is wat deze spike moest opleveren — de reap-loop zou de implementatiekaart stil hebben
geraakt. De echte betrouwbaarheidsmeting hoort bij de eerste aangesloten run (§7, kaart 2).

## 2. De seam (read-only geverifieerd)

`SpawnTransport` is een smal `Protocol` (`backend/app/kanban/dispatch.py:1539`):

```python
class SpawnTransport(Protocol):
    def __call__(self, *, directory: str, prompt: str, session_name: str,
                 cli_id: str = "claude-code", provider: str = "anthropic",
                 model: str | None = None) -> dict: ...
```

Twee siblings vandaag: `make_worktree_transport` (worktree + tmux, `:1554`) en
`sandcastle_transport` (`:1628`). De keuze loopt per kaart/project via
`get_transport_for_card` / `get_transport_for_project` (`:3662` / `:3588`). Een derde sibling
hangt hier — de seam zelf is inderdaad zo goedkoop als `orchestration-substrate-decision.md` §4.4
beloofde. De kosten zitten **niet** in de seam maar in de liveness-laag eromheen (§5).

## 3. Het gemeten event-vocabulaire

Twee probes, `claude` 2.1.209, `--output-format stream-json --verbose`:

| Probe | Prompt | Exit | Events | Duur | Kosten | `result.subtype` |
|---|---|---|---|---|---|---|
| 1 | triviaal, geen tools | `0` | 19 | 2.758 ms | $0.0185 | `success` |
| 2 | één `Bash`-toolcall | `0` | 28 | 5.834 ms | $0.0215 | `success` |

Waargenomen top-level `type`-waarden: **`system`**, **`assistant`**, **`user`**,
**`rate_limit_event`**, **`result`**. `system` splitst via `subtype` in `init`, `hook_started`,
`hook_response`, `thinking_tokens`.

De vier die ertoe doen:

**`system/init`** — het eerste event. Draagt o.a. `session_id`, `cwd`, `model`,
`permissionMode`, `tools`, `mcp_servers`, `slash_commands`, `claude_code_version`. Dit is
tegelijk de **readiness-indicator** en de **sessie-handle**: hij komt vóór elk ander event, dus
`wait_for_pane_ready`'s box-drawing-scrape (§2.3) heeft hier geen equivalent nódig — je weet dat
de run leeft omdat hij zich voorstelt. De `session_id` is direct bekend, wat de
Bridge↔Presence-identiteitsbrug (`$TMUX_PANE`-join) in dit pad overbodig maakt.

**`assistant`** — `message.content[]` met `type` ∈ {`thinking`, `text`, `tool_use`}, plus
per-chunk `usage`. Gemeten `tool_use`:

```json
{"type":"tool_use","id":"toolu_01XMwWtq71h2Mo9Sj6fK1xZS","name":"Bash",
 "input":{"command":"echo hello","description":"Run echo hello"}}
```

**`user`** — draagt de `tool_result`:

```json
{"type":"tool_result","tool_use_id":"toolu_01XMwWtq71h2Mo9Sj6fK1xZS",
 "is_error":false,"content":"hello"}
```

**`rate_limit_event`** — **de belangrijkste vondst.** Getypeerd, ongevraagd, mid-run:

```json
{"type":"rate_limit_event","session_id":"…","rate_limit_info":{
  "status":"allowed_warning","resetsAt":1784070000,"rateLimitType":"five_hour",
  "utilization":0.97,"isUsingOverage":false,"surpassedThreshold":0.9}}
```

**`result`** — terminaal, precies één per run: `subtype` (`success` | `error_max_turns` |
`error_during_execution`), `is_error`, `num_turns`, `duration_ms`, `total_cost_usd`, `usage`
(incl. cache-tokens en `service_tier`), en het finale `result`-antwoord.

**Bijvangst:** `system/hook_started` / `hook_response` bewijzen dat de **presence-hooks óók in
headless-modus vuren**. Kanaal A uit `orchestration-substrate-decision.md` §2.2 blijft dus
intact onder de nieuwe transport — dat is geen aanname meer.

## 4. Mapping op het ACP-isomorfe schema (kaart 2)

| `stream-json` | ACP-isomorf ([`structured_events.py`](../../backend/app/services/agentic_cli/structured_events.py)) | Status |
|---|---|---|
| `assistant` → content `text` | `message_chunk` (`role=assistant`) | ✅ 1-op-1 |
| `assistant` → content `thinking` | `message_chunk` (`role=thought`) | ✅ 1-op-1 |
| `user` → content `text` | `message_chunk` (`role=user`) | ✅ 1-op-1 |
| `assistant` → content `tool_use` | `tool_call` (`tool_call_id`=`id`, `title`=`name`, `raw_input`=`input`, `status=in_progress`) | ✅ 1-op-1 |
| `user` → content `tool_result` | `tool_call` (`tool_call_id`=`tool_use_id`, `raw_output`=`content`, `status`=`completed`/`failed` ← `is_error`) | ✅ 1-op-1 |
| `result` (`is_error=false`) | `usage_result` (`stop_reason`←`subtype`, `cost_usd`←`total_cost_usd`, tokens←`usage`) | ✅ 1-op-1 |
| `result` (`is_error=true`) | `error` (`message`←`result`) | ⚠️ geen JSON-RPC `code` |
| `rate_limit_event` | **— geen slot —** | ❌ **schema-gat** |
| `system/init` | **— geen slot —** | ❌ **schema-gat** |
| *(geen producer)* | `plan_update` | ⚠️ geen native producer |
| *(geen producer)* | `permission_request` | ⚠️ geen producer in dit pad |

### 4.1 Drie gaten, één ervan pijnlijk ironisch

**(a) `rate_limit_event` heeft geen slot — en dát is het event dat de hele oefening
rechtvaardigt.** Kaart 2 ontwierp zes varianten naar ACP's `session/update`-vocabulaire. ACP
heeft geen quota-/rate-limit-notificatie, dús heeft ons schema die ook niet. Maar `stream-json`
emit hem wél, en het is precies het signaal waarvoor `_is_rate_limited_session` vandaag
pane-tekst scrape't (§2.3). Het schema kan zijn eigen belangrijkste use-case niet uitdrukken.

Dat is geen fout van kaart 2 — het is de **grens van de isomorfie-strategie**: door ACP als vorm
te nemen, erf je ACP's *blinde vlekken*. De remedie is klein en behoudt de strategie: voeg een
`rate_limit`-variant toe als **bewuste, gedocumenteerde super-set van ACP** (een latere
ACP-adapter laat 'm simpelweg leeg), i.p.v. het event te wringen in `error` (het ís geen fout —
`status: allowed_warning` betekent "toegestaan") of in `usage_result` (dat is terminaal, dit niet).

**(b) `system/init` heeft geen slot** — terwijl het operationeel het nuttigste event is
(readiness + `session_id`). ACP's tegenhanger is de `session/new`-*response*, geen
`session/update`; vandaar de afwezigheid. Zelfde remedie: een `session_init`-variant.

**(c) `plan_update` en `permission_request` hebben geen producer** in dit pad.
- `plan_update`: Claude kent geen native plan-event. De dichtstbijzijnde proxy is een
  `TodoWrite`-`tool_use`, waarvan `input.todos` structureel op `entries[]` mapt. Dat is een
  *interpretatie* van een toolcall, geen protocol-event — de executor moet die keuze bewust maken.
- `permission_request`: bestaat niet onder `-p` + `--dangerously-skip-permissions`. Hij vereist
  `--permission-prompt-tool` of het bidirectionele control-protocol
  (`--input-format stream-json`). **Conclusie voor facet D:** ACP's getypeerde gating-haak komt
  **niet** gratis mee met stream-json, anders dan `acp-transport-decision.md` §3.2 impliceert.
  Dat is een argument dat op de ACP-poort-kaart (§6 kaart 5) thuishoort, niet hier.

## 5. De vier-in-één identiteit — wat er écht breekt

De kaart vraagt of worktree-lifecycle + claim-cleanup kloppen "zonder de tmux-sessienaam als
spil". Het gemeten antwoord is genuanceerder dan de vraag.

`reap_stale_claims` (`dispatch.py:2823`):

```python
if name in live_sessions or name in sandcastle_live:
    continue
```

Liveness kent **exact twee bronnen**: `_live_sessions()` (`tmux list-sessions`, `:1988`) en
`_live_sandcastle_sessions()` (DB-query op `SandcastleRun.status in (pending, running)`, `:1705`).
Een headless run zit in **geen van beide**. Gevolg: de reaper ziet een claim zonder levende
sessie, en releaset 'm — of verplaatst 'm naar "To Resume" — terwijl het proces gewoon draait.
De volgende tick dispatcht 'm opnieuw. **Dispatch-loop.**

Dit is exact de bug die sandcastle had; `reap_stale_claims`' eigen docstring zegt het letterlijk:

> *"Sandcastle cards have no tmux session, so without the second source every sandcastle card
> would be reaped on the very next tick and re-dispatched in a loop."*

De oplossing is dus geen ontwerpvraag maar een precedent: een **derde bron**
`_live_headless_sessions()`, in de vorm van `_live_sandcastle_sessions()` (inclusief zijn
defensieve `except → set()`: falen maakt de reaper *eager*, nooit *blind*).

### 5.1 De naam blijft de spil — alleen het orakel verandert

De "vier-in-één identiteit" (§2.1: tmux-naam = `agent:`-claim = branch = worktree-dir) suggereert
dat headless alle vier breekt. Dat klopt niet. Van de vier is er **één** tmux-gebonden:

| Facet | Headless | Waarom |
|---|---|---|
| `agent:<name>`-claimlabel | ✅ ongewijzigd | Een string in de DB; `_claimant_session` (`:1845`) parse't 'm zonder tmux aan te raken. |
| git-branch `<name>` | ✅ ongewijzigd | Worktree-lifecycle is `git worktree add -b <name>` (`:1580`) — transport-agnostisch. |
| worktree-dir `<name>` | ✅ ongewijzigd | Idem; `.claude/worktrees/<name>`. |
| tmux-sessienaam `<name>` | ❌ bestaat niet | **Alleen dit.** En zijn enige load-bearing rol is *liveness-orakel*. |

Dus: het worktree-/claim-lifecycle werkt **wel** zonder tmux-naam-koppeling, mits het
liveness-orakel wordt vervangen. De naam blijft nuttig als correlatie-sleutel over de andere
drie. Netto is de ingreep één nieuwe liveness-bron — niet een herziening van de identiteit.

### 5.2 Wat tmux-only is en stilletjes uitschakelt

Deze paden worden voor headless **dode code** (geen bug, wel bewust te weten):

- `stuck_names = session_registry.get_stuck_sessions(live_sessions, …)` (`:2796`) filtert op de
  tmux-live-set. Headless-namen zitten er niet in → nooit "stuck" → ze vallen door naar de
  reap-tak. De hele stuck-detectie werkt dus niet voor headless.
- `_capture_pane_content` (`:1871`) → `None` zonder pane; `_is_rate_limited_session` (`:1892`)
  wordt nooit bereikt. **Vervangen door `rate_limit_event`** — dat is de winst, niet een verlies.
- `mark_spawned` (`:1612`) dekt het gat spawn→eerste-hook. Onder headless is dat gat er niet:
  `system/init` is het eerste event van de stream zelf.

## 6. Gemeten vergelijking tegen het tmux-pad

### 6.1 429-afhandeling — de duidelijkste winst

| | tmux-pad (vandaag) | headless (gemeten) |
|---|---|---|
| Detectie | substring-match op laatste 20 pane-regels (`_is_rate_limited_session`) | getypeerd `rate_limit_event` |
| Wanneer | **ná** de 429, en alleen bij een sessie die leeft-maar-hookloos is | **vóór** rejectie: `status: allowed_warning` bij `utilization: 0.97` |
| Reset-tijd | onbekend → vaste gok `FALLBACK_PAUSE_HOURS` (`:2836`, expliciet "conservative") | exact: `resetsAt` (unix-ts) + `rateLimitType: five_hour` |
| Drift-risico | twee detectors (`_is_rate_limited_session` + `auto_resume`) moeten in sync blijven | één bron |

Twee stappen vooruit, niet één: de gok wordt een **exacte** `resetsAt`, én het signaal komt
**preventief** binnen (`surpassedThreshold: 0.9`) i.p.v. reactief. Het tmux-pad kán per
constructie niet waarschuwen vóór de limiet — er staat pas iets in de pane als het al mis is.

### 6.2 Liveness/exit-detectie

| | tmux-pad | headless |
|---|---|---|
| Leeft? | `tmux list-sessions` bevat de naam | proces leeft / stream open |
| Klaar? | sessie verdwijnt — succes en crash zien er **identiek** uit | `result.subtype` + `is_error` + exitcode |
| Ambiguïteit | tmux-hiccup ⇒ fail-open `None` nodig (`:1988`), anders reap je levende claims | exitcode is eenduidig |
| Dead-on-arrival | `DEAD_ON_ARRIVAL_SECONDS`-heuristiek (`:229`) | overbodig: geen `system/init` ⇒ nooit gestart |

De kwalitatieve winst is echt, maar §1 geldt: n=2 probes bewijst dat het contract *bestaat* en
*consistent* is, niet hoe vaak het faalt.

## 7. Go/no-go

**GO** — met de derde liveness-bron (§5) als **voorwaarde vooraf**, niet als follow-up. Een
transport die zonder die bron landt, produceert een dispatch-loop op het echte bord.

Waarom GO ondanks n=2: de spike moest twee dingen vaststellen — *is het event-contract goed
genoeg om het scraping-residu te vervangen?* (ja, aantoonbaar: §6.1 is strikt beter dan de
huidige gok) en *breekt de identiteit?* (nee: §5.1, één orakel i.p.v. vier facetten). Beide
antwoorden zijn nu empirisch/read-only hard. De resterende onzekerheid (faalpercentage onder
last) is niet goedkoper te kopen met méér probes — die meet je op de eerste aangesloten run.

Het blijft **additief**, conform `acp-transport-decision.md` §5: tmux blijft default voor
human-in-the-loop; headless is alleen voor autonoom-gedispatchte kaarten. De human-takeover-UX
is een aparte, al bestaande kaart (`80c812af`).

## 8. Vervolgkaarten

Aangemaakt op het bord in deze sessie (leaf-spike-clausule):

1. **[feature][transport] `rate_limit` + `session_init` in het ACP-isomorfe event-schema** —
   de twee gaten uit §4.1 (a)/(b), als gedocumenteerde super-set van ACP. Voorwaarde voor 2.
2. **[feature][transport] Headless stream-json-transport + derde liveness-bron** — de derde
   `SpawnTransport`-sibling, mét `_live_headless_sessions()` in dezelfde kaart (§5: los geleverd
   is het een dispatch-loop). `depends_on`: 1.

Bestaand, niet gedupliceerd: **`80c812af` [analysis][transport] Human-takeover-UX voor headless
sessies** (= `acp-transport-decision.md` §6 kaart 4) — consumeert deze bevindingen; becommentarieerd
met §4.1(c) en §5.2.

Niet aangemaakt (blijft §-proza tot er een concrete aanleiding is): het `plan_update`-gat (§4.1c)
— een `TodoWrite`-interpretatie is speculatief tot de transport draait; en de
`permission_request`/facet-D-vraag, die op de gepoorte ACP-kaart (`acp-transport-decision.md` §6
kaart 5) thuishoort.

## 9. Bewust buiten scope

- **Het scraping-residu vervangen in het tmux-pad** (`acp-transport-decision.md` §6 kaart 1) —
  onafhankelijk uitvoerbaar, raakt deze transport niet.
- **De hook-kanaal-observability** — §3 toont dat hooks óók headless vuren; geen reden eraan te
  komen.
- **Sandcastle/podman** — orthogonaal; een headless run kan later evengoed in een sandbox.
- **ACP als daadwerkelijk transport** — gepoort op de tweede-provider-hedge
  (`acp-transport-decision.md` §6 kaart 5). §4.1(c) levert daar een nieuw argument voor aan.
</content>
