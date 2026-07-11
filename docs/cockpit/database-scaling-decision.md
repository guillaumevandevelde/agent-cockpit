# Beslissing: database-plafond — SQLite-concurrency-grens vs. Postgres

**Datum:** 2026-07-11
**Status:** besloten (read-only spike; geen implementatie/migratie in deze kaart)
**Trigger:** kanban-spike "database-plafond" — kind-kaart van de tech-stack-evaluatie
t.o.v. het platformdoel (parent `fa76d74a`). Zusje-docs:
`orchestration-substrate-decision.md` (Spike 1),
`spec-driven-development-fase-0-decision.md`. **Spike 3 (schema-migraties/Alembic)
`depends_on` deze beslissing** — zie §7.

**TL;DR:** **Blijven op SQLite. Nu goedkoop hardenen (één metric toevoegen), niet
migreren.** De premisse "de multi-agent-schrijfbelasting ontgroeit SQLite" houdt op
dit moment geen stand: (a) de backend is één uvicorn-proces, dus alle writes
serialiseren al in-process via een `asyncio.Lock` vóórdat SQLite's file-lock
überhaupt in beeld komt; (b) de write-hete domeinen zijn over **twee losse
SQLite-bestanden** gepartitioneerd die nooit met elkaar contenden; en (c) vrijwel
alle "database is locked"-incidenten in de git-historie zijn **test-harness**-
artefacten (NullPool + pytest-asyncio per-test-eventloops), niet productie-runtime.
Het echte plafond wordt in de praktijk éérst geraakt door tmux/subprocess-spawn,
LLM-rate-limits en host-CPU — niet door SQLite-write-contentie. De migratie naar
Postgres is een reële optie, maar hangt aan een **scherpe trigger** (§6), niet aan
een open ja/nee. De hoogste-waarde-zet nu is de trigger *observeerbaar* maken: tel
runtime-`database is locked` als metric, want die is vandaag onzichtbaar.

---

## 1. Vraagstelling

Het platform draait N concurrente agents die allemaal schrijven (dispatch-claims,
presence, usage-ingest, agent-mail) tegen SQLite. WAL + `busy_timeout` mitigeren,
maar de git-historie toont terugkerende "database is locked"-flakes en
pragma-parity-fixes rond dispatch. SQLite is ideaal voor single-host/local-first;
de vraag: ontgroeit de multi-agent-ambitie het, en of/wanneer is een migratiepad
naar Postgres nodig?

Read-only beslissings-spike. Geen codewijziging behalve dit doc.

## 2. Huidige configuratie (read-only geverifieerd)

### 2.1 Twee SQLite-bestanden, twee engines — een al aanwezige write-partitie

Dit is het meest onderbelichte feit in de vraagstelling en het herformuleert het
hele contentie-verhaal: er is **geen** "één SQLite-bestand". Er zijn er twee, elk
met een eigen engine, connection-pool en WAL-bestand:

| DB-bestand | Engine / Base | Schrijf-domeinen | Aard |
|---|---|---|---|
| `~/.claude-registry/kanban.db` | `kanban_engine` / `KanbanBase` (`app/kanban/db.py`) | dispatch-claims, card-moves, comments, deliverables, **+ append-only op-log** | portable, sync-baar (bord is één-per-machine) |
| `backend/claude_registry.db` (of `~/.claude-registry`-anker) | `engine` / `Base` (`app/database.py`) | presence-events, usage-ingest, agent-mail, scheduled-messages, sandcastle, projects, backups | device-local (tmux-targets, absolute paden) |

De scheiding is bewust (`config.py`: "Kept apart from database_url, which holds
device-local data"). Gevolg voor concurrency: **de twee write-heetste domeinen —
board-claims vs. presence/usage/mail — delen geen file-lock.** Een storm
presence-events blokkeert nooit een dispatch-claim en omgekeerd. Dat is een reële,
al-bestaande horizontale partitie die de effectieve write-druk per bestand halveert
t.o.v. de "alles op één bestand"-framing in de kaart.

### 2.2 Pragma-config (identiek in beide prod-engines)

Beide engines zetten via een `connect`-listener exact dezelfde pragma's
(`app/database.py:26`, `app/kanban/db.py:28`):

```
PRAGMA journal_mode=WAL          # lezers blokkeren schrijvers niet (en omgekeerd)
PRAGMA synchronous=NORMAL        # fsync alleen bij checkpoint, niet elke commit
PRAGMA foreign_keys=ON
PRAGMA busy_timeout=5000         # settings.sqlite_busy_timeout_ms; retry tot 5s bij lock
```

- **WAL**: één schrijver + N gelijktijdige lezers per bestand; schrijvers
  serialiseren onderling.
- **synchronous=NORMAL**: elke commit is een goedkope WAL-append (~0,1–1 ms op
  lokale disk); fsync alleen bij checkpoint. Dit is de standaard-WAL-aanbeveling en
  maakt honderden kleine writes/sec haalbaar.
- **busy_timeout=5000**: een schrijver die het bestand gelockt vindt, retryt tot 5 s
  vóór hij `OperationalError: database is locked` gooit. Onder single-proces-async
  wordt dit zelden geraakt (zie §3).

### 2.3 De echte serialisatie zit vóór SQLite: één proces + één `asyncio.Lock`

De backend is **één uvicorn-proces** (async, single-worker in de dev-/compose-
setup). Twee gevolgen die de kaart-premisse ("N agents schrijven allemaal tegen het
bestand") nuanceren:

1. **Alle board-mutaties funnelen door één in-process HLC-klok-lock.**
   `apply_operation` (`app/kanban/operations.py:87`) neemt `_clock_lock`
   (`asyncio.Lock`) om HLC + seq toe te kennen. Elke board-write is event-sourced:
   de mutatie schrijft **zowel de card-rij als een `kanban_ops`-append** (HLC-
   geordend, voor sync). Dat verdubbelt de write-volume op `kanban.db`, maar
   serialiseert die writes al in Python — SQLite's file-lock wordt voor de eigen
   writes van de backend vrijwel nooit de bindende factor.
2. **De claim is atomair op DB-niveau.** `claim_card` gebruikt sinds `231845f` een
   enkele `UPDATE ... WHERE claimed_by IS NULL` + rowcount-check (i.p.v.
   read-check-write). First-wins onder concurrente claims zonder app-lock; de
   TOCTOU-race is dicht. Dit is een *correctheids*-fix, geen contentie-fix —
   relevant omdat het laat zien dat de write-hete paden al lock-vrij race-safe zijn.

De honest framing: het "single-writer"-plafond van SQLite is voor de backend zelf
grotendeels een non-issue omdat de app-laag al één-schrijver-per-proces is. Het
plafond wordt pas hard zodra er een **tweede schrijvend proces** op hetzelfde
bestand komt (§6, trigger 1).

## 3. Wat zegt de git-historie écht? (concurrency-incidenten)

De kaart noemt "terugkerende database-is-locked-flakes en pragma-parity-fixes".
Klopt — maar bij nalezen zijn die vrijwel allemaal **test-harness-artefacten**, niet
productie-runtime-falen. Dat is een belangrijke herkadering van het risico.

| Commit | Wat | Productie of test? |
|---|---|---|
| `2d3827e` | "database is locked" / "no such table" / "Event loop is closed"-flakes in `test_kanban_dispatch.py` (~25–40% faalrate). Root cause: **test**-pragma-listener zette alleen `foreign_keys`, miste WAL/busy_timeout; onder NullPool's per-checkout connection-churn faalde transiënte contentie meteen i.p.v. te retryen. Fix: prod-pragma's spiegelen + `gc.collect()` vóór reset. | **Test** |
| `31fde55` / `19c0380` | `test_kanban_db_pragmas.py`: parity-test die toekomstige drift tussen test- en prod-pragma-listener rood maakt. | **Test** |
| `e5235ba` | Full-suite-deadlock: gedeelde in-memory-connectie gebonden aan een gesloten event-loop. Fix: file-DB + NullPool. | **Test** |
| `6ab42b2` | StaticPool-ROLLBACK-ordening in dispatch-transport-resolutie. | **Test** (StaticPool = test-fixture) |
| `fcec9eb` | Flaky presence-websocket-tests via cancellation-safe DB-access. | **Test** |
| `b73c519`, `c6bfb94`, `93762b1` | Tests lekken rijen in de gedeelde DB / isoleren git-env. | **Test** |
| `231845f` | Atomaire claim-UPDATE — TOCTOU-race. | **Productie** (correctheid, geen lock-contentie) |

**Conclusie uit de historie:** er is **geen** gedocumenteerd geval van
productie-runtime dat op een uitgeputte `busy_timeout` liep. De "database is
locked"-pijn is een eigenschap van de **test-infrastructuur** (NullPool geeft elke
sessie een verse connectie → rapid open/close-churn; pytest-asyncio geeft elke test
een eigen loop), die de productie-engine (aiosqlite, default-pool, één loop, één
proces) niet heeft. De parity-test (`31fde55`) bestaat juist om die test-specifieke
val dicht te houden. Dit versterkt de "blijven op SQLite"-kant: het aangevoerde
bewijs voor een plafond is grotendeels een test-artefact.

## 4. Kwantificering van de schrijf-concurrency-grens

Zonder een load-test is dit een beredeneerde bovengrens, niet een gemeten getal —
maar de orde van grootte is eenduidig.

**Write-bronnen, naar frequentie:**

- **Presence-hooks (hoogste frequentie).** Elke agent vuurt per tool-call meerdere
  hook-events (`PreToolUse`, `PostToolUse`, `Notification`, `Stop`) naar
  `presence_service.process_event`, elk een write op `claude_registry.db`. Ruwe
  schatting: een actieve agent doet ~1 tool-call/2–5 s → ~0,5–2 events/s. Bij 10
  agents: ~5–20 writes/s. Plus periodieke, **throttled** maintenance (idle-check,
  event-pruning) — bewust niet per event.
- **Op-log + card-writes (kanban.db).** Alleen bij daadwerkelijke board-mutaties
  (claim, move, comment, deliverable). De dispatch-poll draait elke 10 s en claimt
  hooguit een handvol kaarten. Zeer laagfrequent; elke mutatie = 2 writes (card +
  op).
- **Usage-ingest.** Periodiek/batch, niet per-token.
- **Agent-mail.** Cross-session-berichten — laagfrequent.

**Grensberekening.** Een kleine WAL-commit met `synchronous=NORMAL` kost ~0,1–1 ms.
De single-writer-duty-cycle bij B writes/s ≈ B × 0,5 ms. Merkbare contentie
(schrijvers wachten structureel op elkaar, `busy_timeout`-venster wordt een reële
fractie) begint pas als de duty-cycle richting ~50–100% loopt → grofweg
**enkele honderden tot ~1000 kleine writes/s per bestand**. Vertaald naar agents,
bij een realistische ~1–2 presence-writes/s per agent op het drukste bestand
(`claude_registry.db`), ligt de "begint merkbaar te knijpen"-zone rond de
**orde van tientallen tot ~100 gelijktijdig actieve agents** — en dan nog vooral op
het presence-bestand, niet op het board-bestand.

**De dominante realiteit:** ver vóór dat SQLite-plafond raakt het platform andere
muren — tmux/subprocess-spawn-kost per sessie, LLM-rate-limits (waar de codebase al
uitgebreide 429-afhandeling voor heeft), en host-CPU/geheugen voor N `claude`-
processen. De schrijf-concurrency van SQLite is op de huidige single-host-scope
**niet de bindende beperking.**

**Twee footguns die de effectieve grens kunstmatig verlagen** (hardening-doelen,
§6):

1. **Lang-vastgehouden write-transacties.** `get_db` commit't aan het *einde* van de
   request (`app/database.py:46`). Doet een handler traag niet-DB-werk tussen flush
   en commit, dan houdt hij het writer-venster onnodig lang open. Kort houden =
   headroom.
2. **Een tweede schrijvend proces.** Zodra iets buiten het ene uvicorn-proces op
   hetzelfde bestand schrijft (een CLI, een losse worker, een tweede host), is
   SQLite's file-lock + 5 s `busy_timeout` ineens wél de bindende factor. Dit is de
   harde architecturale muur (§6, trigger 1).

## 5. Portabiliteit: wat SQLAlchemy-async al meeneemt vs. wat SQLite-specifiek is

Als/wanneer Postgres in beeld komt, is de migratiekost sterk asymmetrisch verdeeld.

**Portable (SQLAlchemy-async abstraheert het — nagenoeg gratis):**

- ORM-modellen (`Mapped` + `mapped_column`), async-sessions, het gros van de
  queries.
- De atomaire claim `update().where(claimed_by.is_(None))` + rowcount werkt
  ongewijzigd op Postgres — en kan daar zelfs upgraden naar
  `SELECT ... FOR UPDATE SKIP LOCKED` voor echte row-level-claim-concurrency.
- **JSON-kolommen** (`depends_on`, `metadata`, `column_overrides`, `payload`,
  `labels`): SQLAlchemy's `JSON`-type mapt op Postgres naar `JSONB` — portable, en
  Postgres-`JSONB` is rijker (indexeerbaar, query-baar). Geen herwerk nodig.

**SQLite-specifiek (moet expliciet geport worden):**

- **De pragma-`connect`-listeners** (WAL/synchronous/foreign_keys/busy_timeout in
  beide engines + de parity-test + de test-fixture-listener). Postgres heeft deze
  niet nodig; ze worden vervangen door pool-/isolation-config. De hele
  fail-open-retry-mentaliteit rond `busy_timeout` vervalt.
- **De hand-gerolde additieve migraties.** `_migrate_terminology_columns`
  (`app/database.py:68`) en `_ensure_card_columns`/`_ensure_column_table`/
  `_ensure_work_type_mapping_table` (`app/kanban/db.py:98`) gebruiken
  `PRAGMA table_info(...)` + `ALTER TABLE ... RENAME/ADD COLUMN` via
  `exec_driver_sql`. `PRAGMA` is SQLite-only; op Postgres worden dit
  Alembic-migraties. **Dit is precies het raakvlak met Spike 3.**
- **`INSERT ... ON CONFLICT`-upserts** (work-type-mappings): syntax lijkt op
  Postgres maar de dialect-details verschillen; SQLAlchemy's
  `dialect-specific insert` dekt dit, maar het is niet nul-werk.
- **Het sync-/portabiliteitsmodel.** `_migrate_legacy_sqlite` gebruikt
  `sqlite3.backup`; het "bord is één bestand dat je kunt kopiëren/syncen"-ontwerp
  (HLC-op-log, `sync-hlc-freeze-vs-prune.md`) is een *SQLite-design-keuze*. Een
  Postgres-bord verandert het hele "één portable bestand per machine"-verhaal naar
  een netwerk-service — dat is geen mechanische port maar een architectuur-shift die
  het portabiliteitsdoel van `kanban.db` raakt. **Dit is het zwaarste, meest
  onderschatte deel van een eventuele migratie.**

## 6. Beslissing + trigger-condities

**Beslissing: blijven op SQLite met gerichte hardening nu. Migreren naar Postgres
alléén wanneer een van de onderstaande triggers vuurt — geen open ja/nee.**

### Hardening nu (goedkoop, koopt headroom, onafhankelijk van migratie)

1. **Maak de trigger observeerbaar (hoogste waarde).** Tel runtime-
   `OperationalError: database is locked` / `busy_timeout`-uitputting als een metric
   (bv. een counter in de bestaande APM/health-laag), **exclusief** de test-harness.
   Vandaag is dit signaal volledig onzichtbaar — je kunt trigger 2 niet meten. Dit
   is de belangrijkste enkele actie: hij verandert "we denken dat SQLite ooit knelt"
   in een meetbare drempel.
2. **Houd write-transacties kort.** Audit `get_db`'s commit-aan-einde-van-request;
   verplaats traag niet-DB-werk buiten het writer-venster (§4, footgun 1).
3. **Bewaak de single-writer-invariant.** Documenteer expliciet dat maar één proces
   `kanban.db` / `claude_registry.db` mag schrijven; de config verankert al één bord
   per machine, maar de invariant staat nergens als contract.
4. **(Optioneel) WAL-checkpoint-tuning** als het `-wal`-bestand onder aanhoudende
   load groeit.

### Trigger-condities — migreer naar Postgres wanneer ÉÉN hiervan geldt

- **Trigger 1 — Multi-proces/multi-host-schrijvers (harde muur).** Zodra de backend
  ophoudt één uvicorn-proces te zijn: een tweede schrijf-capabel proces op hetzelfde
  bestand (horizontale schaal, een losse worker, of een multi-host-deploy). SQLite's
  file-lock + 5 s `busy_timeout` overleeft concurrente-proces-write-contentie niet
  netjes. **Dit raakt Spike 1 (orchestratie):** verschuift het procesmodel naar
  out-of-process-schrijvers, dan vuurt deze trigger automatisch.
- **Trigger 2 — Aanhoudende productie-`database is locked` (meetbaar via hardening
  1).** Definieer een SLO: als de backend op runtime (test-harness uitgesloten)
  `database is locked` logt boven een drempel (bv. > enkele keren/dag), of één
  dispatch-claim/presence-write faalt ná uitputting van de 5 s `busy_timeout`, dan is
  het write-duty-cycle de single-writer ontgroeid. Dit is het *echte* signaal —
  data, geen anekdote.
- **Trigger 3 — Aanhoudend > ~25–50 gelijktijdig actieve agents (watch-drempel).**
  Ruwe headroom-schatting (§4); op deze schaal wordt het presence-write-tempo +
  op-log-verdubbeling een reële fractie van wall-clock. Geen harde muur maar een
  "ga nu meten"-drempel die trigger 2 vooruit loopt.
- **Trigger 4 — Feature-eis die SQLite slecht bedient.** Echte multi-host-toegang,
  netwerk-toegang tot de DB, row-level-claim-concurrency (`SKIP LOCKED`), of
  online-schema-migraties zonder downtime op schaal.

Zolang géén trigger vuurt, is migreren premature complexiteit: het ruilt de
local-first/portable/zero-ops-eigenschappen van SQLite (die het platformdoel op de
huidige single-host-scope juist goed bedienen) in voor een netwerk-service met
operationele overhead, zonder tussentijdse waarde.

## 7. Overdracht naar Spike 3 (schema-migraties / Alembic)

Spike 3 `depends_on` deze beslissing. De consumeerbare output:

- **Richting = "blijven op SQLite tot een trigger vuurt".** Dat betekent voor Spike
  3: de hand-gerolde additieve migraties (`_migrate_terminology_columns`,
  `_ensure_card_columns`, …) blijven voorlopig het migratie-substraat, en de gotcha
  "schema changes require deleting the db" blijft staan zolang je binnen additieve
  `ALTER TABLE ADD COLUMN` blijft.
- **Maar de trigger-conditie maakt Alembic dubbel relevant.** §5 laat zien dat de
  hand-gerolde migraties het zwaarste SQLite-specifieke stuk zijn én dat ze frontaal
  botsen met de succescriteria *reproduceerbaar/controleerbaar/auditbaar*. Alembic is
  de voor de hand liggende fit **onafhankelijk van Postgres** — het maakt de huidige
  SQLite-schema-evolutie auditbaar — én het is de voorwaarde voor een latere
  Postgres-port. Spike 3 moet dus beslissen: Alembic nú invoeren (voor auditbaarheid
  op SQLite, en als voorbereiding op de trigger) vs. uitstellen tot een trigger
  vuurt. Deze spike beveelt "nu invoeren" niet dwingend aan — dat is Spike 3's
  scope — maar levert het argument dat de migratie-richting het *niet* blokkeert.

## 8. Voorgestelde vervolgkaarten (tekst; niet in deze kaart aangemaakt)

> Deze spike maakt géén kanban-kaarten aan. Onderstaande zijn voorstellen die een
> mens kan prioriteren.

1. **[feature] Metric: runtime-`database is locked` / busy_timeout-uitputting.**
   Tel productie-write-lock-falen (test-harness uitgesloten) in de APM/health-laag.
   Maakt trigger 2 (§6) meetbaar. Kleinste, hoogst-renderende actie uit deze spike.
2. **[chore] Audit lang-vastgehouden write-transacties in `get_db`.** Verifieer dat
   geen handler traag niet-DB-werk binnen het writer-venster doet; verplaats waar
   nodig. Onafhankelijk uitvoerbaar.
3. **[chore] Documenteer de single-writer-invariant** als expliciet contract in
   `CLAUDE.md`/`config.py` (één schrijvend proces per DB-bestand), zodat trigger 1
   niet per ongeluk gepasseerd wordt.
4. **[analysis] Spike 3: Alembic-migratie-substraat.** `depends_on` deze kaart;
   consumeert §7. Beslis: Alembic nu (SQLite-auditbaarheid + Postgres-voorbereiding)
   vs. uitstellen.

## 9. Bewust buiten scope

- **De sync-/HLC-op-log-mechaniek** (`sync-hlc-freeze-vs-prune.md`) — orthogonaal;
  wel geraakt door een eventuele Postgres-port (§5) maar geen onderdeel van deze
  ja/nee.
- **Sandcastle/container-isolatie** — andere as dan het DB-substraat.
- **Frontend & FastAPI/Pydantic** — best-in-class, geen actie (conform het
  parent-plan).
- **APScheduler in-process** — adequaat voor single-host; wordt vanzelf herzien als
  Spike 1 het procesmodel raakt (wat óók trigger 1 hier vuurt).
