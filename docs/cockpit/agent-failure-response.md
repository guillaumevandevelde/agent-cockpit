---
type: conventie
status: actief
---

# Agent-failure-response: het `lock_contention`-contract

Loopt een kanban-schrijfactie vast op databaselock-contentie, dan krijgt de agent
één herkenbare respons met een wachttijd erin — geen kale 500 of stacktrace. De
agent wacht die tijd af, probeert opnieuw, en escaleert via `report_impediment`
na drie eigen pogingen.

Achtergrond en meting: [`kanban-write-retry-vangnet-decision.md`](./kanban-write-retry-vangnet-decision.md)
en [`kanban-write-retry-exposure-matrix.md`](./kanban-write-retry-exposure-matrix.md) §3.

## 1. Wanneer het vuurt

Elke kanban-schrijfactie loopt door `run_write_with_retry`
(`backend/app/kanban/db.py`). Die helper doet zelf drie retries met
exponentiële backoff. Pas als die op zijn, gooit hij `LockContention` — een
subklasse van `OperationalError` met twee velden: `attempts` en
`retry_after_ms` (vast 500).

Niet-lock-fouten (`no such table`, schema-mismatch) en `ClaimRejected` gaan
ongewijzigd door. Die zijn geen contentie en horen niet in dit contract.

## 2. REST-vorm

De centrale handler in `backend/app/main.py` (`lock_contention_handler`) zet
`LockContention` om in:

```http
HTTP/1.1 503 Service Unavailable
Retry-After: 1

{"detail": {"reason": "lock_contention", "retry_after_ms": 500, "attempts": 3}}
```

Eén handler voor alle 45 kanban-schrijfroutes; een route hoeft niets te
vertalen.

## 3. MCP-vorm

Elke MCP-tool is geregistreerd via `_lock_aware_tool()` in
`backend/app/kanban/mcp_server.py`. Die vangt `LockContention` op de
tool-grens en retourneert:

```json
{"error": "lock_contention", "retry_after_ms": 500, "attempts": 3, "operation": "move_card"}
```

`operation` is de toolnaam, handig voor diagnose in de transcript.

## 4. Wat een agent doet

1. Wacht `retry_after_ms` milliseconden.
2. Probeer dezelfde call opnieuw, maximaal drie keer.
3. Lukt het daarna nog niet: `report_impediment` met de vraag wat de
   lock vasthoudt.

Doe geen andere board-mutatie tussendoor — dat vergroot de contentie die je
juist probeert te ontlopen. De instructie staat in de engineer-persona
(`.claude/agents/engineer.md`) en in de dispatch-prompt
(`backend/app/kanban/ship_prompt.py::_build_mcp_fallback_instructions`).

## 5. Frontend

`apiClient` ziet een gewone 503 en toont zijn generieke foutmelding. Er is
bewust geen eigen error-klasse: de gebruiker hoeft alleen te weten dat het bord
even bezig was.

## 6. Test

`backend/tests/test_lock_contention_contract.py` legt alle drie de vormen vast:
de exception, de 503 en de MCP-dict.
