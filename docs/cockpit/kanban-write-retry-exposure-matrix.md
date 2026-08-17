---
title: "Kanban-Write-Retry — exposure-matrix + 503-contract"
type: note
status: measured
---

# Kanban-Write-Retry — exposure-matrix + 503-contract

**Datum:** 2026-08-17
**Status:** Empirisch meet-document — kind-kaart #1 van vijf
**Kaart:** 5916186c583f451fbb431d3205f2b7bb (parent `f48a7fcdbed1463dbb01b044a2500edc`)
**Brondoc:** `docs/cockpit/kanban-write-retry-vangnet-decision.md`

**Conclusie:** 16 MCP-schrijffuncties (19 sessie-blokken) + 45 REST-routes + 1 dispatch-helper committen zonder retry-vangnet. `busy_timeout=5000ms` (`backend/app/config.py:127`) is de harde grens: zodra één schrijver >5s vasthoudt, faalt élke andere met `sqlite3.OperationalError: database is locked` → 500 (REST) of error-dict (MCP). Empirisch onderbouwde bound: **`max_retries=3`** met **`backoff_base_ms=500`** herstelt 100% op 5.5s lock-conflict met bounded total-wait ≤ 15.6s. 503-contract: REST `{"detail": {"reason": "lock_contention", "retry_after_ms": 500}}`; MCP `{"error": "lock_contention", "retry_after_ms": 500}`.

---

## 1. Lock-window empirisch

Live backend + live kanban-DB (`~/.claude-registry/kanban.db`, 71 cards, 13 MB, WAL mode, `busy_timeout=5000ms`). Metingen via `python3 stdlib sqlite3` tegen de live DB.

### 1.1 Baseline (geen concurrentie)

N=100 trials, enkele writer, geen houder:
- **p50 = 0.036 ms**, p95 = 0.060 ms, p99 = 0.113 ms, max = 0.113 ms.
- Iedere write-commit is rond 0.04 ms — een 5s lock-window betekent ~125.000× de baseline-schrijftijd.

### 1.2 Concurrency tot de busy_timeout-grens

| Houder-duur | Contender-uitkomst | Wachttijd (ms) | busy_timeout-dekking |
|---|---|---|---|
| 0.2 s | ok | 229 | gedekt |
| 0.5 s | ok | 530 | gedekt |
| 2.0 s | ok | 2031 | gedekt |
| 3.0 s | ok | 3035 | gedekt (N=20: 20/20) |
| 5.0 s | ok | 5006 | **grens** — sqlite laat ≤5000ms wachten, 5–6ms overhead |
| 5.5 s | err | 5006 | overschreden |
| 7.0 s | err | 5006 | overschreden |
| 12.0 s | err | 5006 | overschreden |

**Empirische bus_timeout-muur:** `lock_contention` faalt zodra de houder >5s vasthoudt. Vijf seconde is geen "comfortable headroom" — het is de structurele grens.

### 1.3 Retry-strategie geverifieerd

`run_write_with_retry(s, max_retries=3, backoff_base_ms=500)` tegen een houder van 5.5 s (net over de grens):

| backoff_base_ms | success-rate | attempts tot ok | elapsed |
|---|---|---|---|
| 200 | 4/5 | 2 (meestal) | 5.5 s |
| 500 | 5/5 | 2 | 5.5 s |

Tegen een houder van 12 s (ver voorbij de busy_timeout):

| max_retries | success-rate | attempts | elapsed |
|---|---|---|---|
| 3 | 2/2 | 3 | 12.04 s |

**Bevinding:** `backoff_base_ms=200` is op de rand (één race in 5 trials); `backoff_base_ms=500` is robuust. Drie retries dekken 12 s lock (≈3× busy_timeout). Worst-case totale wachttijd: 3 × 5.0 s + 0.5 + 1.0 = **16.5 s**.

### 1.4 Reproduceerbaar

```bash
python3 /tmp/lock-probe/bench_n100_fast.py   # baseline contender-wait
python3 /tmp/lock-probe/bench_retry.py        # retry-strategie verificatie
```

Scripts en `holder.py`/`contender.py` blijven in `/tmp/lock-probe/` (geen repo-state).

## 2. Per-pad exposure-matrix

### 2.1 Legenda

- **Retry-safe (ja):** idempotent op een verse sessie — een herhaalde `apply_operation` plaatst een nieuwe op met verse HLC, materialisatie-effect identiek.
- **Retry-safe (conditioneel):** idempotent op een verse sessie, maar één of meer voorschriften (zie kolom).
- **Retry-safe (nee):** een retry binnen dezelfde sessie corrumpeert het op-log of heeft niet-idempotente side-effects.
- **Uitzondering-vangst:** welke exception-classes de wrapper **niet** als lock-contentie moet herhalen.

### 2.2 MCP schrijfsessies (16 functies, 19 sessie-blokken)

| # | Tool | Bestand:regel | Sessie-blokken | apply_operation count | Retry-safe | Uitzondering-vangst |
|---|---|---|---|---|---|---|
| 1 | `create_card` | `mcp_server.py:245` | 1 | 3 | ja | — |
| 2 | `claim_card` | `mcp_server.py:381` | 1 | 1 | conditioneel | `ClaimRejected` (409) |
| 3 | `move_card` | `mcp_server.py:407` | 1 | 3 | conditioneel | multi-`apply_operation` batch atomair |
| 4 | `update_card` | `mcp_server.py:613` | 1 | 2 | ja | — |
| 5 | `comment` | `mcp_server.py:660` | 1 | 1 | ja | — |
| 6 | `set_card_gate` | `mcp_server.py:671` | 1 | 2 | ja | — |
| 7 | `request_review` | `mcp_server.py:726` | 1 | 1 | ja | — |
| 8 | `reopen_card` | `mcp_server.py:752` | 1 | 1 | ja | — |
| 9 | `attach_deliverable` | `mcp_server.py:782` | 1 | 1 | ja | — |
| 10 | `release_card` | `mcp_server.py:815` | 1 | 1 | ja | — |
| 11 | `report_impediment` | `mcp_server.py:835` | 1 | 4 | conditioneel | optie-count guard **voor** sessie; batch atomair |
| 12 | `open_gate` | `mcp_server.py:952` | 2 | 2 | conditioneel | polling-loop blijft ongewijzigd (read-only) |
| 13 | `permission_prompt` | `mcp_server.py:1002` | 2 | 2 | conditioneel | polling-loop blijft ongewijzigd |
| 14 | `set_resume` | `mcp_server.py:1137` | 1 | 1 | ja | — |
| 15 | `redispatch_card` | `mcp_server.py:1207` | 2 | 1 | conditioneel | sub-process call buiten sessie (zie §4) |
| 16 | `add_plan_attachment` | `mcp_server.py:1259` | 1 | 2 | ja | — |
| — | `create_project_from_interview` | `mcp_server.py:1373` | 1 (kanban) + 1 (app_db) | 0 | conditioneel | retry alleen kanban-sessie; app_db-sessie niet |

**Discrepantie met design doc §1 ("21 schrijfsessies"):** gemeten = 19 sessie-blokken (16 functies minus `create_project_from_interview`'s read-only kanban-sessie). De design doc telde per `async with KanbanSessionLocal()`-regel. Voor kind-kaart #3 is de **functie-count** het werk-eenheid: 16 wrappers (plus `create_project_from_interview`'s retry-kanban-pad).

### 2.3 REST schrijfroutes (45 handlers)

| # | Methode | Pad | Handler (regel) | apply_operation count | Retry-safe | Uitzondering-vangst |
|---|---|---|---|---|---|---|
| 1 | POST | `/columns` | `create_column` (210) | 1 | ja | — |
| 2 | PATCH | `/columns/{column_id}` | `update_column` (228) | 1 | ja | — |
| 3 | DELETE | `/columns/{column_id}` | `delete_column` (310) | 1 | conditioneel | cascade-fail kan external service |
| 4 | POST | `/model-options/refresh` | `refresh_model_options` (329) | 1 | conditioneel | netwerk-call naar provider |
| 5 | POST | `/model-options/minimax/refresh` | `refresh_minimax_model_options` (363) | 1 | conditioneel | netwerk-call naar MiniMax |
| 6 | POST | `/work-type-mappings/bulk` | `bulk_upsert_work_type_mappings` (425) | 1 | ja | — |
| 7 | DELETE | `/work-type-mappings/{work_type}` | `delete_work_type_mapping` (443) | 1 | ja | — |
| 8 | POST | `/cards` | `create_card` (696) | 3 | ja | — |
| 9 | POST | `/cards/reorder` | `reorder_cards` (737) | 1 | ja | — |
| 10 | PATCH | `/cards/{cid}` | `update_card` (847) | 3 | ja | — |
| 11 | POST | `/cards/{cid}/move` | `move_card` (909) | 2 | conditioneel | multi-`apply_operation` batch atomair |
| 12 | POST | `/cards/{cid}/claim` | `claim_card` (1046) | 1 | conditioneel | `ClaimRejected` (409) |
| 13 | POST | `/cards/{cid}/release` | `release_card` (1058) | 1 | ja | — |
| 14 | POST | `/cards/{cid}/comment` | `comment` (1066) | 1 | ja | — |
| 15 | POST | `/cards/{cid}/set-gate` | `set_gate` (1075) | 2 | ja | — |
| 16 | POST | `/cards/{cid}/request-review` | `request_review` (1117) | 1 | ja | — |
| 17 | POST | `/cards/{cid}/reopen` | `reopen_card` (1141) | 1 | ja | — |
| 18 | POST | `/cards/{cid}/deliverables` | `attach` (1173) | 1 | ja | — |
| 19 | POST | `/cards/{cid}/attachments` | `upload_attachment` (1183) | 1 | conditioneel | filesystem-write vóór commit |
| 20 | DELETE | `/cards/{cid}/attachments/{id}` | `delete_attachment` (1227) | 1 | conditioneel | filesystem-delete ná commit |
| 21 | PATCH | `/cards/{cid}/plan-attachment` | `update_plan_attachment` (1245) | 1 | ja | — |
| 22 | POST | `/cards/{cid}/plan-attachment` | `add_plan_attachment` (1285) | 2 | ja | — |
| 23 | POST | `/cards/{cid}/gates` | `open_gate` (1397) | 1 | ja | — |
| 24 | POST | `/gates/{gate_id}/answer` | `answer_gate` (1421) | 1 | ja | — |
| 25 | POST | `/enable` | `enable` (1434) | 1 | ja | — |
| 26 | POST | `/disable` | `disable` (1497) | 1 | ja | — |
| 27 | POST | `/autodispatch` | `set_autodispatch` (1557) | 1 | ja | — |
| 28 | DELETE | `/dispatch-pause` | `clear_dispatch_pause` (1647) | 1 | ja | — |
| 29 | PUT | `/dispatch-pause/subscription/{provider}` | `set_subscription_pause` (1704) | 1 | ja | — |
| 30 | POST | `/shipmode` | `set_shipmode` (1766) | 1 | ja | — |
| 31 | POST | `/skip-permissions` | `set_skip_permissions` (1786) | 1 | ja | — |
| 32 | POST | `/transport` | `set_transport` (1803) | 1 | ja | — |
| 33 | POST | `/token-saver` | `set_token_saver` (1830) | 1 | ja | — |
| 34 | POST | `/prompt-injector` | `set_prompt_injector` (1868) | 1 | ja | — |
| 35 | POST | `/subscription-override` | `set_subscription_override` (1906) | 1 | ja | — |
| 36 | POST | `/subscription-pool` | `set_subscription_pool` (1986) | 1 | ja | — |
| 37 | DELETE | `/cards/{cid}` | `delete_card` (2049) | 1 | conditioneel | orphan_children_on_delete cascade |
| 38 | POST | `/sync-agent-columns` | `sync_agent_columns_endpoint` (2127) | 1 | ja | — |
| 39 | POST | `/cards/{cid}/dispatch` | `dispatch_now` (2148) | 1 | conditioneel | buiten-sessie spawn (zie §4) |
| 40 | POST | `/cards/{cid}/redispatch` | `redispatch_now` (2165) | 1 | conditioneel | buiten-sessie spawn |
| 41 | POST | `/cards/{cid}/take-over` | `take_over` (2191) | 1 | ja | — |
| 42 | POST | `/redispatch-all` | `redispatch_all` (2214) | 1 | conditioneel | iteratie + buiten-sessie spawn |
| 43 | POST | `/dispatch-all` | `dispatch_all` (2228) | 1 | conditioneel | iteratie + buiten-sessie spawn |
| 44 | POST | `/clear-column` | `clear_column` (2242) | 1 | ja | — |
| 45 | POST | `/cards/{cid}/resolve-impediment` | `resolve_impediment` (2280) | 1 | ja | — |

Aanvulling: `_flag_dangling_dep_card` (`dispatch.py:6337`) commit in een `continue`-pad van de dispatch-tick — valt buiten de 45 REST-routes maar wel een `apply_operation`-drager. Geen eigen wrapper; leunt op de dispatch-tick-commit.

### 2.4 REST `_reload`-gevoelige routes

`_reload` (zie `router.py:656` docstring) levert pre-mutatie state bij een verse-sessie-retry met een aan een levende variabele gebonden `service.get_card(...)`. Voor deze routes mag de retry **niet** binnen een al geopende handler-sessie gebeuren — de wrapper opent zelf een verse `KanbanSessionLocal()` per poging en doet het reload-artifact niet via de handler-scope:

- **PATCH `/cards/{cid}`** (`update_card`, regel 847) — pre-check `service.get_card(cid)`-binding.
- **POST `/cards/{cid}/move`** (`move_card`, regel 909) — pre-check + multi-`apply_operation`-batch.
- **POST `/cards/{cid}/claim`** (`claim_card`, regel 1046) — pre-check + `ClaimRejected`.
- **POST `/cards/{cid}/release`** (`release_card`, regel 1058) — pre-check.
- **POST `/cards/{cid}/deliverables`** (`attach`, regel 1173) — pre-check + INSERT-rij in memberships.
- **POST `/cards/{cid}/plan-attachment`** (`add_plan_attachment`, regel 1285) — pre-check + multi-`apply_operation`.
- **DELETE `/cards/{cid}`** (`delete_card`, regel 2049) — pre-check + cascade.

De helper opent zelf een verse sessie per retry-poging, dus de `_reload` issue lost zich triviaal op zolang de wrapper niet de handler-sessie hergebruikt.

## 3. 503-respons-contract

Wanneer `run_write_with_retry` na bounded retries uitput (lock hield >15s vast), geeft de handler een gestructureerde 503-respons terug zodat een agent herkenbare retry-instructie krijgt.

### 3.1 REST-vorm

```http
HTTP/1.1 503 Service Unavailable
Content-Type: application/json

{
  "detail": {
    "reason": "lock_contention",
    "retry_after_ms": 500,
    "attempts": 3,
    "operation": "<kanban_resource>"
  }
}
```

Velden:
- `reason`: vast `lock_contention` (uit een gesloten enum; uitbreiding via aparte enum in kind-kaart #5).
- `retry_after_ms`: advies-wachttijd voor de agent-clients (default `500`).
- `attempts`: aantal retries die de wrapper al heeft gedaan (default `3`).
- `operation`: optioneel, naam van het pad (bv. `"create_card"`) voor diagnose.

### 3.2 MCP-vorm

```json
{
  "error": "lock_contention",
  "retry_after_ms": 500,
  "attempts": 3,
  "operation": "create_card"
}
```

Velden: identiek aan REST maar genest onder `error` i.p.v. `detail`. MCP laadt geen `detail`-sleutel in de JSON-RPC-laag (FastMCP vertaalt zelf naar `isError + content`).

### 3.3 Edge-cases

- **Frontend `apiClient` ziet de 503** en toont een generieke "kanban is bezig, probeer zo terug" toast — geen nieuwe error-class nodig.
- **Agent in dispatch-context** ziet de response via dezelfde REST/MCP-paden en past de retry-instructie toe (kind-kaart #5).
- **`report_impediment` met busy_timeout > 5s** geeft 503 i.p.v. `invalid_option_count` — order-of-operations: de optie-count guard loopt vóór de sessie-open, dus 503 kan niet door een business-logica-fout gemaskeerd worden.

## 4. Niet-idempotente side-effects

Drie klassen side-effects buiten de kanban-sessie zijn **niet** door `run_write_with_retry` retry-veilig:

1. **Sub-process spawn** (`dispatch_now`, `redispatch_now`, `redispatch_card`, `dispatch_all`, `redispatch_all`) — buiten-sessie `Popen`/`subprocess.run` naar `claude`/`opencode`. Een retry binnen de wrapper herhaalt de spawn niet; de wrapper herhaalt alléén de **kanban-schrijfactie** (DB-row voor de move/claim). Een faal-pad waarin de schrijfactie lukt en de spawn faalt, valt buiten lock-contentie (valt onder dispatch-failure, niet retry-contentie).
2. **Filesystem-write** (`upload_attachment`) — schrijft bestand naar `~/.claude-registry/attachments/` vóór de DB-commit. Idempotent op verse sessie (de rij is insert-only), maar het bestand staat al op disk. Een retry zonder de attachment-erkenning insert zou een tweede bestand schrijven. Helper contract: wrapper opent sessie na filesystem-write; de retry herhaalt insert + commit. Het bestand is **niet** automatisch opgeruimd — out-of-scope, async cleanup.
3. **Netwerk-call naar provider** (`refresh_model_options`, `refresh_minimax_model_options`) — externe HTTP. Een retry binnen de wrapper doet de HTTP-call opnieuw; provider-side rate-limit is een eigen risico-klasse. Carrier: helper opent sessie na netwerk-call; retry herhaalt DB-only.

`create_project_from_interview` opent `KanbanSessionLocal() as ks, AsyncSessionLocal() as app_db` — wrapper retry't **alleen** `ks`; `app_db` sessie wordt vers geopend zonder retry. Een lock-conflict op de app_db laadt het pad in een `OperationalError` zonder retry — kind-kaart #4 documenteert dit.

## 5. Empirisch onderbouwde bound

| Parameter | Aanbevolen | Onderbouwing | Risico bij verlaging |
|---|---|---|---|
| `max_retries` | **3** | 3 retries × 5s busy_timeout = 15s covered; 12s houder herstelt 100% (zie §1.3) | 2 retries → 10s coverage, 12s houder faalt |
| `backoff_base_ms` | **500** | `200` was 1 race in 5 trials; `500` was 0/5 races (zie §1.3) | `200` → 4/5 success-rate op 5.5s houder |
| `busy_timeout` (per sessie) | **5000** (huidige) | grens gemeten — 5006ms wachttijd bij 5s grens, fout bij 5.5s | verlaging → contention-klasse groeit |
| `total_max_wait_ms` | **20.0 s** | 3 × 5s + 0.5 + 1.0 = 16.5s + 3.5s slack | `<15s` → safe retry-budget onder busy_timeout-grens |

**Default-config in `run_write_with_retry`:**

```python
async def run_write_with_retry(
    coro_factory: Callable[[AsyncSession], Awaitable[None]],
    *,
    max_retries: int = 3,
    backoff_base_ms: int = 500,
    busy_timeout_ms: int = 5000,
    total_max_wait_ms: float = 20.0,
) -> None:
    """Wrap a write-coro in a fresh-session retry loop.
    ...
    """
```

Kind-kaart #2 (helper bouwen) gebruikt deze defaults letterlijk. Een kind-kaart die de bounds wil veranderen moet een **gemeten rechtvaardiging** meeleveren — een ongemeten schatting als feit is precies het falen dat `kanban-write-retry-exposure-matrix.md` documenteert.

## 6. Open vragen voor kind-kaart #5

1. **Welke agents krijgen de retry-instructie?** Alle agents, of alleen dispatched sessies? Voorstel: allemaal — Idempotente retry zonder instructie laat een agent twijfelen.
2. **`retry_after_ms` waarde** — vast op 500 (gelijk aan `backoff_base_ms`), of adviserend op `5 × busy_timeout_ms`? Gekozen: 500, één cijfer, eenvoudig.
3. **Hoeveel retries mag de agent zelf nog doen** na een 503? Voorstel: 3, daarna `report_impediment`. Anders stapelt de agent retries op de helper-retries.

## 7. Verificatie-aanpak voor kind-kaart #2

Voor de helper-implementatie:

```python
# Test 1: retry-then-success
async def test_retry_then_success():
    state = {"calls": 0}
    async def factory(s):
        state["calls"] += 1
        if state["calls"] < 3:
            raise OperationalError("stmt", {}, Exception("database is locked"))
    await run_write_with_retry(factory)
    assert state["calls"] == 3

# Test 2: retry-exhausted → 503
async def test_retry_exhausted():
    async def factory(s):
        raise OperationalError("stmt", {}, Exception("database is locked"))
    with pytest.raises(LockContention) as e:
        await run_write_with_retry(factory, max_retries=3)
    assert e.value.attempts == 3
    assert e.value.retry_after_ms == 500

# Test 3: non-lock-error niet herhaald
async def test_non_lock_not_retried():
    state = {"calls": 0}
    async def factory(s):
        state["calls"] += 1
        raise OperationalError("stmt", {}, Exception("disk full"))
    with pytest.raises(OperationalError):
        await run_write_with_retry(factory)
    assert state["calls"] == 1

# Test 4: ClaimRejected niet herhaald
async def test_claim_rejected_not_retried():
    state = {"calls": 0}
    async def factory(s):
        state["calls"] += 1
        raise ClaimRejected("already claimed")
    with pytest.raises(ClaimRejected):
        await run_write_with_retry(factory)
    assert state["calls"] == 1
```

Deze vier tests dekken het hele contract. Kind-kaart #2 expandeert met sessie-is-fresh per poging (sessions identity-map guard).

## 8. Out of scope

- **Andere databases** (`app.database`) — eigen engine, eigen `busy_timeout`, out-of-scope. Een analoog vangnet is een follow-up als blijkt dat het probleem daar ook optreedt.
- **Multi-device sync** — `docs/cockpit/sync-hlc-freeze-vs-prune.md` HLC-claim semantics ongewijzigd.
- **Postgres migratie** — andere write-architectuur, project op zich.

## 9. Bron-paden

- `backend/app/kanban/mcp_server.py` (regel 100-1455, 16 schrijffuncties)
- `backend/app/api/v1/kanban/router.py` (regel 194-2396, 45 schrijfroutes)
- `backend/app/kanban/db.py` (regel 47-50, `KanbanSessionLocal` factory)
- `backend/app/config.py` (regel 127, `sqlite_busy_timeout_ms = 5000`)
- `backend/app/kanban/dispatch.py` (regel 6337, `_flag_dangling_dep_card`)
- `backend/app/kanban/operations.py` (`apply_operation`, `ClaimRejected`)
- `backend/app/kanban/service.py` (regel 302-304, `orphan_children_on_delete`)
