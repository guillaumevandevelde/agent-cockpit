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
als Pydantic-modellen met een discriminated union op `type`. Zes varianten:

| `type`                 | ACP-tegenhanger                                                     |
|------------------------|---------------------------------------------------------------------|
| `message_chunk`        | `session/update` → `agent_message_chunk` / `user_message_chunk` / `agent_thought_chunk` |
| `tool_call`            | `session/update` → `tool_call` / `tool_call_update`                 |
| `plan_update`          | `session/update` → `plan`                                          |
| `permission_request`   | `session/request_permission` (request)                             |
| `usage_result`         | `session/prompt`-result (`stopReason`) + usage                     |
| `error`                | JSON-RPC 2.0 error-object                                          |

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

### Parsen

```python
from app.services.agentic_cli.structured_events import parse_structured_event

event = parse_structured_event({"type": "tool_call", "tool_call_id": "tc1", "status": "completed"})
# -> ToolCallEvent; onbekende `type` of malformed payload -> pydantic.ValidationError
```

De discriminator dispatcht op `type`; een onbekend type of een malformed payload gooit een
`pydantic.ValidationError`.

## 3. Waarom ACP-isomorf en niet ACP-native

Zie [`acp-transport-decision.md`](./acp-transport-decision.md) §3.2 / §4: ACP's structurele
winst (getypeerde plan/tool-events, `session/request_permission` als gating-haak) rechtvaardigt
**ACP-vormige events**, niet **ACP-als-eerste-transport**. Door dit event-model ACP-isomorf te
ontwerpen is een latere ACP-backed transport een *nieuwe implementatie van dezelfde capability*
— niet een tweede event-spoor. De eerste implementatie is Claude's `stream-json` (kaart 3), die
zijn native events op dit schema mapt.
