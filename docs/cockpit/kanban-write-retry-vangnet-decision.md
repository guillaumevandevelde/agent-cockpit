---
title: "Kanban-Write-Retry-Vangnet — beslisdocument"
type: decision
status: decided
---

# Kanban-Write-Retry-Vangnet — beslisdocument

**Datum:** 2026-08-17
**Status:** Analyse / beslisdocument (leaf-spike; geen implementatie in deze kaart)
**Kaart:** f48a7fcdbed1463dbb01b044a2500edc
**Uitkomst:** Vijf follow-up kind-kaarten; helper-beslissing één laag boven de sessie-factory; agent-failure-response is 503 met retry-hint.

**Trigger:** kanban-kaart "[problem] Lock-contentie-vangnet dekt alleen create_card; MCP-schrijfpaden van de fabriek zijn onbeschermd".

**Verwant:**
- `backend/tests/test_kanban_dispatch_lock_during_spawn.py` (het enige bestaande lock-release-contract),
- `backend/app/kanban/db.py` (de sessie-factory waar de helper bij hoort),
- `backend/app/kanban/mcp_server.py` (21 schrijfsessies, nul retry-vangnet),
- `backend/app/api/v1/kanban/router.py` (45 schrijfroutes, nul retry-vangnet),
- `backend/app/kanban/dispatch.py` (de dispatch-tick met `_flag_dangling_dep_card` in een `continue`-pad zonder eigen commit).

---

## TL;DR

1. **De premise van de kaart klopt niet.** Drie artefacten die hij noemt bestaan niet: `run_write_with_retry` in `db.py`, `test_kanban_write_retry.py`, en `test_dispatch_write_lock_release.py`. Geen enkele create_card-route of welke andere schrijfroute dan ook heeft vandaag een retry-vangnet. Bron: `grep -rn "run_write_with_retry" backend/` → 0 hits; `ls backend/tests/test_kanban_write_retry.py` → `No such file`.

2. **De échte fix die al shipte is commit `fd381651`.** `_run_card` commit tussen de claim/move/pending_spawn_session-schrijfacties en de synchrone `card_transport(...)`-spawn-call, zodat de write-lock vrijkomt vóór de ~30-40s spawn-blokkade. Vastgelegd in `backend/tests/test_kanban_dispatch_lock_during_spawn.py` (kaart `a2d15978d897436ca992e22f9ba23ba6`). Dat dekt precies één lock-window: de dispatch-tick.

3. **De rest van de ~70 schrijfpaden is onbeschermd.** 21 `KanbanSessionLocal()` schrijfsessies in `mcp_server.py`, 45 REST-schrijfroutes in `router.py`, en de `_flag_dangling_dep_card` continue-pad-schrijfacties in `dispatch.py:6602`. Zolang één daarvan >5s lock vasthoudt (de `busy_timeout`), faalt élke andere schrijver met `sqlite3.OperationalError: database is locked` → 500 (REST) of error-dict (MCP). Dat is een stilval-klasse: de agent laat een geclaimde kaart achter.

4. **De fix ligt op drie niveaus.** Eén: een `run_write_with_retry`-helper in `db.py` met een smal contract (alleen `OperationalError("database is locked")` herhalen, bounded retries, geen retry binnen een open transactie). Twee: alle 21 MCP + 45 REST writes door die helper halen. Drie: een agent-facing 503-respons zodat een agent retry-instructie krijgt in plaats van een dode sessie.

5. **De uitvoering komt uit vijf kind-kaarten.** Eén analyse-kind om de échte exposure te meten, drie feature-kinderen (helper bouwen, MCP wrappen, REST wrappen), en één feature-kind voor het agent-failure-response-contract + dispatch-prompt. Geen add_plan_attachment-relatie met deze ouder-kaart — dat is analyst-decompositie-territorium. Ze worden als Backlog-kaarten aangemaakt met `parent_card_id` op deze kaart, geen plan_ref.

---

## 1. Wat de kaart beweert vs. wat de code zegt

De oorspronkelijke beschrijving stelt:

> "POST /kanban/cards gaf 500 met `database is locked` omdat de dispatch-tick één write-transactie vasthield over zijn hele resolutiefase. Die oorzaak is gefixt, en `create_card` kreeg een retry + 503. De rest niet."
> "Vertrekpunt is de helper en zijn documentatie in `backend/app/kanban/db.py`; de bestaande tests `backend/tests/test_kanban_write_retry.py` en `backend/tests/test_dispatch_write_lock_release.py` leggen het huidige contract vast…"

Drie onverifieerbare beweringen in dat blok:

| Bewering | Bron die het zou bewijzen | Werkelijke staat |
|---|---|---|
| `run_write_with_retry` in `db.py` | `backend/app/kanban/db.py` | **Niet aanwezig.** `db.py` defineert alleen de engine, `KanbanSessionLocal`, `_migrate_legacy_sqlite`, `_sqlite_path`, `init_kanban_db`. Geen retry-helper. |
| `test_kanban_write_retry.py` | `backend/tests/` | **Niet aanwezig.** `ls backend/tests/test_kanban_write_retry.py` → `No such file`. |
| `test_dispatch_write_lock_release.py` | `backend/tests/` | **Niet aanwezig.** De relevante tests heten `test_kanban_dispatch_lock_during_spawn.py` (16 KB) en `test_kanban_dispatch_event_loop_unblocked.py` (9 KB), beide gericht op de spawn-tick-lock-release uit commit `fd381651`. |
| `create_card` heeft retry + 503 | `backend/app/api/v1/kanban/router.py:696` | **Niet aanwezig.** `create_card` opent `KanbanSessionLocal()`, doet `apply_operation(...)`, commit. Geen wrapper, geen retry, geen 503. |

Wat de code wél laat zien:

- **commit `fd381651`** — pre-spawn commit in `_run_card`. De dispatch-tick commit tussen de claim-schrijfactie en de sync spawn-call. `_run_card` voert deze commit bij elke claim/move-batch uit tot spawn-ready.
- **`_flag_dangling_dep_card` (dispatch.py:6282)** — schrijft comment + update + move in één blok, gevolgd door `continue` zonder eigen commit. De writes landen bij de eerstvolgende commit van de buitenste dispatch-tick (regels 2316, 2374, 5003, 5011, 5776). Geen retry-vangnet, geen lock-vrijgave tussendoor.

De werkelijke flow is dus: één lock-window gedicht (de dispatch-tick), de overige ~70 schrijfpaden onbeschermd.

## 2. Wat er feitelijk kapot kan

Een willekeurige write-call houdt de SQLite write-lock vast vanaf `await s.execute(...)` tot `await s.commit()`. De `busy_timeout` is 5000ms (`backend/app/config.py:127`). Als een tweede schrijver binnen dat venster dezelfde verbinding probeert, wacht hij 5s en gooit dan `sqlite3.OperationalError: database is locked`. Geen bestaande handler vangt die fout (`grep -rn OperationalError backend/app/` → 0 hits in handler-bestanden).

Op dat moment:

- **REST-route**: `OperationalError` bubbelt via FastAPI naar een 500. Geen retry. Geen contract voor de client.
- **MCP-tool**: idem, maar met error-dict-vorm. Geen retry.

De agent in die sessie ziet een 500, gaat ervan uit dat het werk mislukt is, en laat de kaart geclaimd achter. De dispatch-loop detecteert de zwevende claim via `dispatch_failures` of de reaper, niet via lock-contentie zelf. Detectie → uren later.

Dat is de stilval-klasse die de kaart terecht aanwijst — alleen de oorzaak-diagnose klopt niet.

## 3. De drie open beslissingen

### 3.1 Waar hoort het chokepoint?

Drie opties, van buiten naar binnen:

**A. Per-handler wrapper.** 21+45 plekken elk apart wikkelen. Voordeel: expliciet, leesbaar, dispatcher-vriendelijk. Nadeel: 67 aanraakpunten, 67 kansen om het verkeerd te doen.

**B. Eén laag onder de handlers, rond de `KanbanSessionLocal()`-factory.** Een wrapper-coroutine die sessie + commit + retry in één keer afhandelt. Voordeel: één plek, automatisch bereik voor alle toekomstige writes. Nadeel: verbergt retry-semantiek voor de aanroeper; sessie-werkende code (`attached session`-API's zoals `apply_operation`) wordt lastiger.

**C. Rond de `apply_operation`-aanroeper.** De meeste writes roepen `apply_operation` aan. Voordeel: één plek, alle writes via dezelfde primitive. Nadeel: `apply_operation` gebruikt HLC-ordered-claim-checks die niet idempotent herhaald mogen worden op een sessie na een gedeeltelijke commit — een retry binnen dezelfde sessie corrumpeert het op-log.

**Voorkeur: A met gestandaardiseerde wrapper.** De grond is concreet, leesbaar, en de wrapper dwingt per-handler-nadenken over idempotentie af. Een per-handler wrapper die de gedeelde helper `run_write_with_retry` aanroept, combineert het beste van beide: één bron van waarheid voor het retry-gedrag, 67 keer aangeroepen. Dit is wat kind-kaart #2 oplevert.

### 3.2 Welke paden zijn veilig te herhalen?

`apply_operation` (de `kanban_ops`-schrijver) is append-only-CRDT-achtig: een nieuwe op met HLC, een materialisatie van het effect. Een herhaalde schrijfactie op een verse sessie is idempotent (de op krijgt een nieuwe HLC, het materialisatie-effect is hetzelfde). Daarom is retry op een verse sessie veilig voor:

- `create_card`, `update_card`, `comment`, `attach_deliverable`, `set_card_gate`, `request_review`, `reopen_card`, `release_card`, `add_plan_attachment`, `set_resume`;
- de REST-routes die door `apply_operation` heen gaan.

Niet direct veilig óf met extra voorwaarden:

- **`claim_card`** — heeft `ClaimRejected`-semantiek. Een retry binnen een verse sessie na een crash is correct (de claim is gelukt of niet, idempotent), maar de wrapper moet `ClaimRejected` niet als lock-fout interpreteren. De exception moet boven blijven.
- **`move_card` met reviewer-gate redirect** — `mcp_server.move_card:537-553` zet de agent op `reviewer` als reviewer-gate actief is. Multi-`apply_operation`-batch in één transactie (move + outcome side-effects + reviewer-redirect). De ganse batch moet atomair of niet.
- **`report_impediment`** — beweegt naar Impediment, opent een gate, en releaset de claim. Drie `apply_operation`-calls + een `service.create_gate`. Idem batch-atomiciteit.
- **`create_project_from_interview`** — opent twee sessies (kanban + app DB). Alleen de kanban-sessie gaat door de wrapper.

Conclusie: de wrapper retry't de héle batch in een verse sessie, nooit incrementeel. Dat is wat kind-kaart #2 contractueel vastlegt.

### 3.3 Wat is het juiste faalantwoord voor een agent?

Een mens krijgt 500, klikt opnieuw. Een agent heeft een gestructureerde instructie nodig, anders valt hij terug op de gedocumenteerde REST-fallback die hetzelfde probleem heeft.

Drie opties:

- **A. 503 + retry-hint.** De REST-route gooit `HTTPException(503, detail={"reason": "lock_contention", "retry_after_ms": 500})`. De MCP-tool geeft `{"error": "lock_contention", "retry_after_ms": 500}`. De agent ziet de retry-hint, wacht, en probeert opnieuw.
- **B. Interne retry vóór failure.** De wrapper retry't al 3× met exponentiële backoff voordat hij 503 gooit. De agent merkt het niet.
- **C. Beide.** Wrapper retry't intern (max 3 pogingen, bounded totale wachttijd), geeft 503 alleen bij uitputting met een retry-hint.

**Voorkeur: C.** De meeste lock-contentie is <500ms (de andere schrijver commit binnen een normaal schrijven). De wrapper handelt dat stilletjes af. Een echte 5s+ lock-window is een system-issue, geen transient — daar heeft de agent een expliciete instructie voor nodig (wacht, of escaleer via `report_impediment`).

## 4. Implementatie: vijf kind-kaarten

### Kind 1 — Analyse: meet de échte write-exposure

`work_type='analysis'`. Geen afhankelijkheden. Output: een per-write retry-safety matrix (welke write houdt >5s vast, welke is idempotent, welke niet), en een 503-contract-spec. De matrix voedt kind-kaart #2 (helper-ontwerp) en #3/#4 (wrap-strategie).

Acceptance criteria:

- Per write een tabel-rij: pad, gemeten lock-window (p50/p99 over N=100 synthetische runs), retry-safe (ja/nee/conditioneel), uitzondering-vangst nodig (lijst).
- De 503-respons-vorm (REST + MCP) gedeelte van het contract.
- Een empirisch onderbouwde bound op `max_retries` en `backoff_base_ms` (gemeten, niet geschat).

### Kind 2 — Feature: bouw `run_write_with_retry` in `db.py`

`work_type='feature'`. Depends op kind 1. Output: de helper in `backend/app/kanban/db.py`, plus `backend/tests/test_kanban_write_retry.py` (de naam die de oorspronkelijke kaart al noemde). Het contract:

✅ Geïmplementeerd (kaart `29a4c7eb9c534fdaafa71050d63325f6`): helper in `db.py` + 6 tests in `test_kanban_write_retry.py` (de vijf uit de acceptance-criteria plus een budget-bound-pintest).

- Herhaalt alleen `sqlalchemy.exc.OperationalError` waarvan de oorzaak `database is locked` is (gefilterd op substring in `str(exc.orig)`).
- Bounded retries (default 3, configureerbaar).
- Bounded totale wachttijd (default 2s, configureerbaar).
- Geen retry binnen een al geopende sessie — de wrapper opent zelf een verse `KanbanSessionLocal()` per poging.
- Niet-lock `OperationalError` (bv. schema-mismatch) wordt **niet** herhaald, bubble-up als voorheen.
- `ClaimRejected` (uit `claim_card`) wordt **niet** herhaald, bubble-up als business-logica-fout.

Acceptance criteria:

- `test_kanban_write_retry.py` dekt: retry-then-success, retry-exhausted, non-lock-error-niet-herhaald, sessie-wordt-verse-geopend-per-poging, `ClaimRejected`-blijft-bubble-up.
- Docstring beschrijft het contract en de drie klassen errors met hun gedrag.
- Geen wijzigingen aan `apply_operation` of `service.py`.

### Kind 3 — Feature: wikkel de 21 MCP schrijfpaden

`work_type='feature'`. Depends op kind 2. Output: alle `async def <tool>(...)` schrijffuncties in `mcp_server.py` die `KanbanSessionLocal()` direct openen, gaan door `run_write_with_retry`. Polling-loops (read-only `get_gate` in `open_gate`/`permission_prompt`) blijven ongewijzigd.

Acceptance criteria:

- 21 schrijfsessies gewikkeld; `grep -A8 "async with KanbanSessionLocal()" mcp_server.py` toont dat de wrapper de sessie opent, niet meer de directe factory.
- `claim_card` behoudt zijn `ClaimRejected`-vertaling (de exception wordt gevangen en omgezet naar `{error: already_claimed, owner}`).
- `report_impediment`'s gate-open + claim-release + move-batch blijft atomair.
- Bestaande tests in `backend/tests/` blijven groen.

### Kind 4 — Feature: wikkel de 45 REST schrijfroutes

`work_type='feature'`. Depends op kind 2. Output: idem kind 3 maar dan voor de 45 POST/PATCH/DELETE-handlers in `router.py`. Bijzondere aandacht voor:

- `_reload`-poisoning (de `_reload`-docstring in `router.py`): post-commit `_reload` levert pre-mutatie state bij een verse-sessie-retry. De wrapper moet de retry binnen een verse sessie doen, niet binnen de handler-scope.
- `create_project_from_interview` opent twee sessies (kanban + app DB). De wrapper retry't alleen de kanban-sessie; de app-DB-sessie blijft zoals hij is.

Acceptance criteria:

- 45 routes gewikkeld; voorbeeld van één route inline-diff in de PR-omschrijving.
- `test_kanban_dispatch_lock_during_spawn` blijft groen (de pre-spawn-commit-invariant).
- Geen nieuwe 503-paden zonder het 503-contract uit kind #1.

### Kind 5 — Feature: agent-failure-response + dispatch-prompt

`work_type='feature'`. Depends op kind 2, 3, 4. Output: wanneer `run_write_with_retry` op is, retourneert de REST-route `HTTPException(503, detail={"reason": "lock_contention", "retry_after_ms": 500})` en de MCP-tool `{"error": "lock_contention", "retry_after_ms": 500}`. De dispatch-prompt voor agents krijgt een korte sectie "Bij 503 met `lock_contention`: wacht `retry_after_ms` en probeer opnieuw; escaleer via `report_impediment` als het na 3 pogingen niet lukt."

Acceptance criteria:

- Zowel REST als MCP produceren het gestructureerde 503-antwoord.
- `docs/cockpit/kanban-conventions.md` (of een nieuw `docs/cockpit/agent-failure-response.md`) documenteert het contract.
- De dispatch-prompt-template (`.claude/agents/engineer.md` of equivalent) bevat de retry-instructie.

## 5. Verificatie

- `backend/tests/test_kanban_write_retry.py` groen.
- `backend/tests/test_kanban_dispatch_lock_during_spawn.py` groen (geen regressie op de pre-spawn-commit-invariant).
- REST + MCP beide produceren 503 met gestructureerd contract na een retry-uitputting (test via een mock-session die altijd `OperationalError` gooit).
- Een synthetische load-test met 5 parallelle schrijvers die elk 30s op een lock vasthouden, toont aan dat de 5e schrijver binnen 3× retry slaagt (bounded totale wachttijd < 2s).

## 6. Out of scope

- Multi-device sync (`docs/cockpit/sync-hlc-freeze-vs-prune.md`) — de retry-helper verandert niets aan de HLC-claim semantics, alleen aan lock-contentie.
- Andere writes dan kanban — `app.database` (registry-DB) heeft eigen engine en eigen `busy_timeout`. Een analoog vangnet voor de registry-DB is een follow-up als blijkt dat de lock-contentie daar ook optreedt.
- Migratie naar Postgres — een andere write-architectuur lost de lock-vraag fundamenteel op, maar is een project op zich.

## 7. Follow-up kind-kaarten

Deze zijn na het shippen van dit document als Backlog-kaarten aangemaakt met `parent_card_id=f48a7fcdbed1463dbb01b044a2500edc`:

1. `Meet de echte write-exposure (matrix + 503-contract)` (work_type=analysis)
2. `Bouw run_write_with_retry in db.py + tests` (work_type=feature)
3. `Wikkel de 21 MCP schrijfpaden in mcp_server.py` (work_type=feature)
4. `Wikkel de 45 REST schrijfroutes in router.py` (work_type=feature)
5. `Agent-failure-response 503-contract + dispatch-prompt` (work_type=feature)
