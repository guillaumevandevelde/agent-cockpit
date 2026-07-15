# Structured events / `headless_run` — ACP-isomorf event-schema

**Status:** geïmplementeerd (schema-fundament). Bron: [`acp-transport-decision.md`](./acp-transport-decision.md) §6 kaart 2.
**Voorwaarde voor:** kaart 3 (prototype headless `claude -p --output-format stream-json`-transport achter `SpawnTransport`).

Deze kaart legt **twee** dingen vast: (1) een `headless_run`-capability in de
`agentic_cli`-matrix waarmee elke CLI-adapter declareert of/hoe hij een headless
structured-event-modus heeft, en (2) een intern event-model dat **ACP-isomorf** is,
zodat een latere ACP-backed transport hetzelfde schema hergebruikt in plaats van een
tweede event-vocabulaire uit te vinden.

## 1. `headless_run`-capability

Toegevoegd aan `CAPABILITY_KEYS` in
[`backend/app/services/agentic_cli/capabilities.py`](../../backend/app/services/agentic_cli/capabilities.py).
Elke CLI-adapter classificeert de headless-modus expliciet (geen enkele valt terug op de
`normalize_capability_matrix()` `unknown`-default zonder rationale):

| CLI          | state         | mechanisme (in `reason`)                                   |
|--------------|---------------|------------------------------------------------------------|
| claude-code  | `supported`   | `claude -p --output-format stream-json` (kaart-3 doel)     |
| codex-cli    | `supported`   | `codex exec --json` (JSONL event-stream)                   |
| open-code    | `supported`   | `opencode serve` event-API                                 |
| mimo-code    | `unknown`     | niet geverifieerd tegen dit event-model                    |
| copilot-cli  | `unsupported` | geen gedocumenteerde headless structured-event-modus       |

`supported` telt mee in `capability_flags()` (net als `read_only`/`write_capable`); `unknown`
en `unsupported` niet. Het "hoe" leeft in het `reason`-veld, conform de bestaande matrix-conventie
(bv. "OpenCode fork is available via --fork flag"). Dit is een classificatie van wat de CLI
*aanbiedt*, niet van wat Cockpit al *geïmplementeerd* heeft — de eerste implementatie (Claude
stream-json) volgt in kaart 3.

## 2. Event-model (ACP-isomorf)

Gedefinieerd in
[`backend/app/services/agentic_cli/structured_events.py`](../../backend/app/services/agentic_cli/structured_events.py)
als Pydantic-modellen met een discriminated union op `type`. Acht varianten — zes ACP-isomorf
plus twee gedocumenteerde **super-set**-uitbreidingen (zie §2.1):

| `type`                 | ACP-tegenhanger                                                     |
|------------------------|---------------------------------------------------------------------|
| `message_chunk`        | `session/update` → `agent_message_chunk` / `user_message_chunk` / `agent_thought_chunk` |
| `tool_call`            | `session/update` → `tool_call` / `tool_call_update`                 |
| `plan_update`          | `session/update` → `plan`                                          |
| `permission_request`   | `session/request_permission` (request)                             |
| `usage_result`         | `session/prompt`-result (`stopReason`) + usage                     |
| `error`                | JSON-RPC 2.0 error-object                                          |
| `rate_limit`           | *(geen — gedocumenteerde super-set; zie §2.1)*                      |
| `session_init`         | *(geen — gedocumenteerde super-set; zie §2.1)*                      |

**Isomorfie, geen identiteit.** ACP gebruikt `camelCase` JSON-keys; dit model gebruikt
`snake_case` (Cockpit/Python-conventie). Alleen de casing is de vertaling die een toekomstige
ACP-adapter doet — de *structuur en semantiek* zijn 1-op-1. Elk event draagt een optionele
`session_id` zodat een gemultiplexte transport events aan de juiste headless-run toewijst.

### Kernvelden per event

- **`message_chunk`** — `role` (`assistant`/`user`/`thought`), `text`.
- **`tool_call`** — `tool_call_id`, `title`, `kind`, `status`
  (`pending`/`in_progress`/`completed`/`failed`), `raw_input`, `raw_output`.
- **`plan_update`** — `entries[]` met `content`, `priority` (`high`/`medium`/`low`),
  `status` (`pending`/`in_progress`/`completed`).
- **`permission_request`** — `tool_call_id`, `title`, `options[]` met `option_id`, `name`,
  `kind` (`allow_once`/`allow_always`/`reject_once`/`reject_always`). Dit is ACP's getypeerde
  gating-haak (facet D uit de transport-beslissing).
- **`usage_result`** — `stop_reason`, `input_tokens`, `output_tokens`, `total_tokens`,
  `cost_usd`.
- **`error`** — `code` (JSON-RPC), `message`, `data`.
- **`rate_limit`** — `status` (`allowed`/`allowed_warning`/`rejected`), `resets_at` (unix-ts),
  `rate_limit_type` (`five_hour`/`seven_day`/`seven_day_opus`/`monthly`), `utilization` (float
  0..1), `is_using_overage` (bool), `surpassed_threshold` (float 0..1). Het
  `status`-veld is verplicht (altijd door de CLI gezet); de rest is best-effort en mag in
  toekomstige CLI-versies ontbreken.
- **`session_init`** — `session_id` (verplicht — het is de readiness-handle), `cwd`, `model`,
  `permission_mode`. Verdere velden die `stream-json` in de toekomst toevoegt worden getolereerd
  door optioneel te zijn.

### Parsen

```python
from app.services.agentic_cli.structured_events import parse_structured_event

event = parse_structured_event({"type": "tool_call", "tool_call_id": "tc1", "status": "completed"})
# -> ToolCallEvent; onbekende `type` of malformed payload -> pydantic.ValidationError
```

De discriminator dispatcht op `type`; een onbekend type of een malformed payload gooit een
`pydantic.ValidationError`.

## 2.1 Super-set-uitbreidingen: `rate_limit` en `session_init`

> **Statuswijziging 2026-07-15 (kaart k-feature-trans-8631):** het "bekende gat" uit de eerdere
> §2.1 is **gedicht** — `rate_limit` en `session_init` zijn nu eerste-klas varianten van de
> discriminated union, met expliciete documentatie dát en wáárom ze buiten ACP's
> `session/update`-vocabulaire vallen. De eerdere sectie-tekst is hieronder vervangen door een
> verantwoording van de gekozen super-set-vorm.

### Waarom twee varianten bewust buiten ACP vallen

Door ACP als vorm te nemen, erf je ACP's *blinde vlekken*. Twee daarvan zijn operationeel
relevant voor onze headless transport, en beide worden nu als **gedocumenteerde super-set**
toegevoegd — niet door ze in een verkeerd bestaand ACP-event te wringen:

- **`rate_limit`** — ACP kent geen quota/rate-limit-notificatie: quota is een CLI-zijde
  concern, geen transport-zijde concern, dus `session/update` draagt er nooit een. Maar
  `claude -p --output-format stream-json` emit een getypeerd `rate_limit_event` mid-run — en
  dát is precies het signaal dat de huidige 429-substring-scrape (`_is_rate_limited_session`)
  overbodig maakt
  ([`headless-stream-json-transport-spike.md`](./headless-stream-json-transport-spike.md) §4.1(a)
  / §6.1). Het is geen `error` (`status: allowed_warning` = toegestaan, niet geweigerd) en geen
  `usage_result` (terminaal; dit is mid-run). De remedie: nieuwe variant. Een toekomstige
  ACP-adapter laat deze variant leeg — dat is geen bug, dat is het contract.

- **`session_init`** — ACP's tegenhanger is de `session/new`-*response*, geen
  `session/update`-notificatie; vandaar de afwezigheid in ACP's kernvocabulaire. Maar
  `stream-json` emit een `system/init` als allereerste event, en dát is precies de
  readiness-handle die de huidige box-drawing-scrape in `wait_for_pane_ready` vervangt (§2.3
  van de substrate-beslissing). Zonder `session_init` heeft de headless transport nergens om
  zijn readiness-signaal op te parkeren; wél erin hebben is geen ACP-beloftebreuk omdat ACP nooit
  een notificatie van die aard heeft beloofd. Zelfde contract: een ACP-adapter mag deze variant
  leeg laten.

### Wat hierdoor **niet** is veranderd

- `plan_update` heeft **geen native producer** in Claude's stream (dichtstbijzijnde proxy:
  `TodoWrite`-`tool_use`). Geen schema-uitbreiding; de executor kiest later bewust of hij die
  toolcall als `plan_update` interpreteert.
- `permission_request` heeft **geen producer** onder `-p` + `--dangerously-skip-permissions`;
  die vereist `--permission-prompt-tool` of het bidirectionele control-protocol. Geen
  schema-uitbreiding — dit is een argument voor de ACP-poort-kaart
  (`acp-transport-decision.md` §6 kaart 5), niet hier.

## 3. Waarom ACP-isomorf en niet ACP-native

Zie [`acp-transport-decision.md`](./acp-transport-decision.md) §3.2 / §4: ACP's structurele
winst (getypeerde plan/tool-events, `session/request_permission` als gating-haak) rechtvaardigt
**ACP-vormige events**, niet **ACP-als-eerste-transport**. Door dit event-model ACP-isomorf te
ontwerpen is een latere ACP-backed transport een *nieuwe implementatie van dezelfde capability*
— niet een tweede event-spoor. De eerste implementatie is Claude's `stream-json` (kaart 3), die
zijn native events op dit schema mapt.
