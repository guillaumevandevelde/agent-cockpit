# Beslissing: schema-migratiesysteem — `create_all` + handmatige renames vs. Alembic

**Datum:** 2026-07-11
**Status:** besloten (read-only spike; geen implementatie/migratie in deze kaart)
**Kaart:** _zie doc — geen hex-id in dit beslisdoc vastgelegd_
**Uitkomst:** **Alembic invoeren**, forward-only en SQLite-first.

**Trigger:** kanban-spike "schema-migratiesysteem" — kind-kaart van de
tech-stack-evaluatie t.o.v. het platformdoel (parent `fa76d74a`). Zusje-docs:
`orchestration-substrate-decision.md` (Spike 1),
`database-scaling-decision.md` (Spike 2). **Deze spike (Spike 3) `depends_on`
Spike 2** — die afhankelijkheid is inmiddels *opgelost*: zie §2.4 en §6.

**TL;DR:** **Voer Alembic nu in, forward-only en SQLite-first.** De
afhankelijkheid van de DB-beslissing is niet meer open — Spike 2 heeft
"blijven op SQLite tot een trigger vuurt" besloten, dus "uitstellen tot ná de
DB-beslissing" is moot: díé beslissing is er al, en ze *blokkeert* Alembic niet
(migratie-richting is portable, `database-scaling-decision.md` §7). De reële
keuze is dus alleen nog **nu vs. later-op-eigen-merites**. Het echte data-verlies-
risico vandaag is beperkt: de hand-gerolde migraties zijn **additief en
niet-destructief** (`ALTER TABLE ADD COLUMN` / `RENAME COLUMN`, idempotent), dus
ze verliezen géén data. Het pijnpunt is (a) **auditbaarheid/reproduceerbaarheid**
— er is geen versiegeschiedenis, ordening of downgrade, en de migratielogica is
verspreide imperatieve Python over twee bestanden — en (b) de "**delete the
db**"-gotcha die élke niet-additieve wijziging (kolom droppen, type wijzigen,
`NOT NULL` toevoegen aan bestaande tabel, backfill) op data-verlies laat
uitkomen. Dát is de scherpe trigger. De aanbeveling is Alembic **als
gesanctioneerd pad voor de volgende schemawijziging** in te voeren, de bestaande
schema's onmiddellijk te **baseline-stampen**, maar de bestaande idempotente
`_ensure_*`/`_migrate_*`-helpers **niet** te herschrijven — die zijn de
historische baseline. De grootste valkuil is niet de code maar het
**"geen lokale pytest"-beleid** gekruist met het feit dat de tests hun schema uit
`create_all`/ORM-metadata bouwen, niet uit migraties: zonder een CI-guard drift
de migratieketen ongemerkt weg van de modellen (§4.3).

---

## 1. Vraagstelling

Er is geen migratiesysteem. Het schema ontstaat via
`Base.metadata.create_all` + een reeks hand-gerolde, idempotente
`ALTER TABLE`-functies, en de CLAUDE.md-gotcha zegt letterlijk "schema changes
require deleting the db". Dat botst met de platform-succescriteria
*reproduceerbaar, controleerbaar, auditbaar* en met het zelf-evolutie-principe
(agents die schema wijzigen zonder data te verliezen). Alembic is de voor de
hand liggende fit bij SQLAlchemy. Vraag: invoeren nu / uitstellen tot ná de
DB-beslissing / niet nodig — met onderbouwing en met expliciete afhankelijkheid
van de database-spike.

Read-only beslissings-spike. Geen codewijziging behalve dit doc.

## 2. Huidige aanpak (read-only geverifieerd)

### 2.1 `create_all` + idempotente hand-migraties, over twee engines/bestanden

Precies zoals bij Spike 2 zijn er **twee** SQLite-bestanden, elk met een eigen
engine en een eigen migratieroutine bij startup:

| DB-bestand | Init-functie | Migratie-helpers |
|---|---|---|
| `backend/claude_registry.db` (`Base`) | `init_db` (`app/database.py:59`) | `_migrate_terminology_columns` (`:68`) |
| `~/.claude-registry/kanban.db` (`KanbanBase`) | `init_kanban_db` (`app/kanban/db.py:81`) | `_ensure_card_columns` (`:98`), `_ensure_column_table` (`:140`), `_ensure_work_type_mapping_table` (`:181`) |

Het mechanisme in beide gevallen:

1. `conn.run_sync(<Base>.metadata.create_all)` — maakt ontbrekende **tabellen**
   aan vanuit de ORM-modellen. `create_all` **wijzigt nooit bestaande tabellen**
   (geen kolommen toevoegen, geen types veranderen); het is puur "create if not
   exists".
2. Daarna draaien de hand-migraties, die het gat dichten dat `create_all`
   laat vallen voor tabellen die al bestonden vóór een kolom werd toegevoegd:
   ze lezen `PRAGMA table_info(<tabel>)`, vergelijken met de gewenste kolommen,
   en doen additieve `ALTER TABLE ADD COLUMN` / `RENAME COLUMN` /
   `RENAME TO` via `exec_driver_sql`.

De hand-migraties zijn zorgvuldig **idempotent** — elke stap slaat over als de
bron ontbreekt of het doel al bestaat, wat zowel verse installs (waar
`create_all` de nieuwe vorm al produceerde) als half-afgemaakte migraties dekt.
Voorbeelden: `provider`→`cli` en `agent_teams`→`run_groups` renames
(`_migrate_terminology_columns`), en de ~15 additieve kolommen op `kanban_cards`
(`agent`, `transport`, `depends_on`, `work_type`, `metadata`, `model`,
`column_overrides`, …) in `_ensure_card_columns`. Terminologie-drivers staan in
`docs/cockpit/terminology.md`.

### 2.2 Waarom "delete the db" in de gotcha staat

De additieve helpers dekken exact één klasse wijziging: **een nieuwe nullable
kolom of een pure hernoeming**. Alles daarbuiten heeft geen pad:

- kolom **droppen** of van **type** veranderen,
- een `NOT NULL`-kolom **zonder default** toevoegen aan een tabel met bestaande
  rijen,
- een kolom **backfillen** met afgeleide data,
- een **constraint** toevoegen/wijzigen,
- tabellen **samenvoegen/splitsen**.

SQLite's `ALTER TABLE` is beperkt (geen `DROP COLUMN`/`ALTER COLUMN` vóór 3.35,
en zelfs daarna geen type-wijziging), dus zo'n wijziging vereist het klassieke
"12-stappen"-recept (nieuwe tabel + `INSERT ... SELECT` + swap) — met de hand
geschreven, per geval. In de praktijk kiest de gotcha voor het alternatief: gooi
de dev-DB weg en laat `create_all` 'm vers opbouwen. Voor een **dev**-DB is dat
prima; voor de **productie**-DB's (`claude_registry.db` met usage/presence/
agent-mail-historie; `kanban.db` met de HLC-op-log en alle board-historie) is
"delete the db" **data-verlies** — die bestanden kunnen niet weggegooid worden
(`database.py:85`: "the kanban DB … cannot be dropped").

### 2.3 De twee risico's, scherp benoemd

- **Auditbaarheid/reproduceerbaarheid (het hoofdrisico vandaag).** Er is geen
  versiebegrip: een DB "weet" niet welke schema-revisie ze draait, er is geen
  geordende, reviewbare keten van wijzigingen, geen downgrade, en de wijzigings-
  logica is verspreide imperatieve Python die je moet *lezen* om te weten wat er
  gebeurd is. Dit botst frontaal met de succescriteria *reproduceerbaar/
  controleerbaar/auditbaar*: je kunt een schema-toestand niet deterministisch
  reproduceren uit een revisienummer, en een reviewer kan een schemawijziging
  niet als een geïsoleerde, versiebeheerde diff beoordelen.
- **Data-verlies bij niet-additieve wijzigingen (latent, wordt acuut bij de
  eerste zulke wijziging).** Zolang alle wijzigingen additief zijn, verliest het
  huidige systeem geen data. De eerste keer dat iemand een kolom moet droppen,
  een type wijzigen of backfillen, is er alleen "delete the db" (= data-verlies)
  óf opnieuw een bespoke hand-gerolde tabel-rebuild. Dat is een **anti-patroon
  voor zelf-evolutie**: een agent die veilig schema wil wijzigen heeft geen
  gesanctioneerd, dataveilig pad.

> **Nuance t.o.v. de kaart-framing.** De kaart noemt "data verliezen" als het
> primaire risico. Read-only geverifieerd is dat vandaag *begrensd*: de
> bestaande migraties zijn niet-destructief. Het echte, nú-aanwezige tekort is
> auditbaarheid; het data-verlies is een *latente* muur die pas bij de eerste
> niet-additieve wijziging valt. Beide pleiten voor Alembic, maar via
> verschillende urgenties — dat onderscheid stuurt het "nu vs. later" (§6).

### 2.4 Afhankelijkheid van Spike 2 — opgelost

Spike 3 `depends_on` Spike 2 omdat de Alembic-aanbeveling de DB-richtingskeuze
consumeert. Die keuze is inmiddels gemaakt: **blijven op SQLite tot een trigger
vuurt** (`database-scaling-decision.md` §6). Twee consumeerbare gevolgen:

1. **Geen Postgres-port forceert Alembic op korte termijn.** Het argument "je
   hebt Alembic nodig zodra je naar Postgres gaat" is niet *nu* dwingend.
2. **Maar de migratie-richting blokkeert Alembic ook niet, en de audit-baat is
   Postgres-onafhankelijk.** Spike 2 §7 stelt expliciet: Alembic is "de voor de
   hand liggende fit *onafhankelijk van Postgres*" — het maakt de huidige
   SQLite-schema-evolutie auditbaar én is de voorwaarde voor een latere port. De
   afhankelijkheid resolvet dus naar: **wacht níét op een DB-trigger; beoordeel
   Alembic op zijn eigen SQLite-audit-merites.**

## 3. Alembic — wat het oplost en wat het kost

### 3.1 Wat het oplevert

- **Versiebeheerde, geordende migratieketen** (revisie-DAG met `down_revision`),
  elk een reviewbaar Python-bestand in `versions/`. Dekt de auditbaarheids-eis
  direct: een DB draagt zijn `alembic_version`, en een schema-toestand is
  deterministisch reproduceerbaar uit een revisie.
- **`upgrade`/`downgrade`** — een dataveilig, gesanctioneerd pad voor
  niet-additieve wijzigingen (Alembic's `batch_alter_table` implementeert exact
  het SQLite "recreate-table"-recept dat je anders met de hand schrijft), dus de
  "delete the db"-gotcha vervalt voor de gevallen die er pijn doen.
- **`autogenerate`** — diff tussen ORM-metadata en de live-DB → migratie-skelet.
  Ook bruikbaar als **drift-detector** in CI (§4.3).
- **Portable** naar Postgres wanneer/als een Spike 2-trigger vuurt — dezelfde
  keten, `batch`-mode vervalt daar. Alembic vervangt dan óók de SQLite-specifieke
  `PRAGMA`/`exec_driver_sql`-migraties die Spike 2 §5 als "het zwaarste
  SQLite-specifieke stuk" aanmerkt.

### 3.2 Wat het kost

- **Twee ketens.** Er zijn twee engines/Bases/bestanden. Alembic ondersteunt dat
  (multi-database template, of twee `version_table`/branch-labels), maar het is
  meer dan de standaard-quickstart: elke keten heeft zijn eigen `env.py`-target
  en baseline.
- **Initiële baseline tegen bestaande DB's** — zie §4.1; het load-bearing,
  eenmalige stuk.
- **Dev-workflow-verandering** — zie §4.2.
- **CI + het "geen lokale pytest"-beleid** — zie §4.3; de subtielste kost.
- **Onderhoud van een `alembic/`-map, `env.py`, en de dependency** (`alembic`
  staat nog niet in `requirements*.txt`).

## 4. Integratie-analyse (de vier concrete raakvlakken)

### 4.1 Initiële baseline-migratie tegen de bestaande DB

De productie-DB's dragen al het **volledige** huidige schema (via `create_all` +
alle hand-migraties). Je mag ze niet opnieuw "from scratch" laten opbouwen. De
correcte baseline is:

1. Genereer één **baseline-revisie** die het huidige schema beschrijft
   (`alembic revision --autogenerate` tegen een verse, volledig-gemigreerde DB,
   of handmatig als "initial"). Dit is de `down_revision = None`-wortel.
2. Op **bestaande** DB's: draai géén `upgrade` (het schema is er al), maar
   `alembic stamp head` — dat schrijft alleen de `alembic_version`-rij zonder
   DDL. Vanaf dan zijn bestaande installaties "at baseline".
3. Op **verse** installs: `alembic upgrade head` bouwt vanaf leeg.
4. **Overgang van de hand-migraties.** De bestaande `_ensure_*`/`_migrate_*`
   moeten na baseline niet dubbel-werken. Aanbevolen: laat ze *staan* (ze zijn
   idempotent en dus harmless op een reeds-gemigreerde DB) en verbied *nieuwe*
   regels erin — nieuwe wijzigingen gaan voortaan uitsluitend via een
   Alembic-revisie. De helpers herschrijven/verwijderen is aparte, latere
   opruiming, geen voorwaarde voor invoering. (Dit is de "forward-only"-scoping
   uit de TL;DR.)

Kost: **eenmalig, begrensd, mechanisch.** Het risico zit niet in de baseline
zelf maar in stap 4 verkeerd doen (dubbele DDL of een gat tussen `stamp` en de
eerste echte revisie).

### 4.2 Ontwikkelworkflow

Vandaag: model wijzigen → `create_all` pakt nieuwe *tabellen*; voor een nieuwe
*kolom* een regel aan `_ensure_card_columns` toevoegen (of, voor iets
niet-additiefs, de dev-DB weggooien). Met Alembic:

1. Model wijzigen in `app/models/` of `app/kanban/models.py`.
2. `alembic revision --autogenerate -m "..."` → skelet, **altijd handmatig
   nakijken** (autogenerate mist server-defaults, sommige constraint-/index-
   wijzigingen, en data-migraties).
3. `alembic upgrade head` lokaal om de migratie te testen.
4. Revisiebestand committen mét de modelwijziging.

Dit is discipline-zwaarder dan "voeg een regel toe", maar het is de standaard
SQLAlchemy-workflow en het is precies wat auditbaarheid koopt. **Belangrijk
frictiepunt:** `create_all` blijft in de tests bestaan (§4.3), dus dev en test
bouwen schema *anders* dan productie zou moeten (migraties). Die tweedeling moet
bewust gemanaged worden, niet impliciet.

### 4.3 CI-impact (`quality.yml`) × het "geen lokale pytest"-beleid — de kern

Twee feiten die samen de subtielste kost vormen:

- **De tests bouwen hun schema uit `create_all`/ORM-metadata, niet uit
  migraties.** `backend/tests/conftest.py:64` doet
  `conn.run_sync(Base.metadata.create_all)`; kanban-tests patchen de engine en
  bouwen óók uit metadata. Alembic-migraties draaien in de suite dus **niet**
  mee. Gevolg: de migratieketen kan ongemerkt **wegdriften** van de modellen —
  tests blijven groen (ze gebruiken de modellen), terwijl `alembic upgrade head`
  op een productie-DB een ander schema oplevert.
- **"Geen lokale pytest"-beleid** (memory `feedback_no_local_pytest`): backend-
  verificatie draait **alleen in CI** (`quality.yml`: ruff + `pytest -q`, plus
  `check_openapi_snapshot.py`, bandit, mypy-advisory). Een ontwikkelaar draait
  migraties dus niet noodzakelijk lokaal; de enige betrouwbare gate is CI.

Daarom, als Alembic ingevoerd wordt, **moet** CI twee dingen borgen (anders is de
audit-winst schijn):

1. **`alembic upgrade head` op een verse DB draait schoon** — bewijst dat de
   keten van leeg naar head loopt.
2. **`alembic check` / autogenerate-diff is leeg** — een guard die faalt zodra de
   modellen en de migratieketen uiteenlopen (analoog aan het bestaande
   `check_openapi_snapshot.py`-patroon: een snapshot-drift-test). Dit is de
   *enige* bescherming tegen de metadata-vs-migratie-drift die het
   create_all-in-tests-ontwerp introduceert.

Zonder (2) verergert Alembic de reproduceerbaarheid in plaats van 'm te
verbeteren: je hebt dan twee bronnen van waarheid (modellen + keten) die stil uit
elkaar lopen. **Dit is de belangrijkste enkele voorwaarde van deze spike.** De
CI-kost is één extra job (of stappen in de bestaande `backend`-job); goed
te dragen, maar niet optioneel.

### 4.4 Interactie met de twee-bestanden-architectuur

Beide DB's evolueren onafhankelijk (verschillende schemadomeinen, verschillende
release-cadans). Twee losse Alembic-ketens (elk eigen `version_table`) sluit
daar netjes op aan en houdt de HLC-op-log/kanban-portabiliteit (Spike 2 §5)
gescheiden van de device-local `claude_registry.db`. Geen reden ze te
verenigen.

## 5. Alternatieven kort gewogen

- **Status quo houden.** Goedkoopst, maar laat de auditbaarheids-eis onvervuld en
  laat de niet-additieve muur staan. Acceptabel *alleen* zolang alle wijzigingen
  additief blijven — een aanname die het platformdoel (zelf-evoluerende agents
  die schema wijzigen) juist wil doorbreken.
- **`create_all` + een zelfgebouwd versietabelletje.** Reïmplementeert Alembic-
  lite (versie-rij, geordende stappen) met de hand — meer maatwerk, minder
  ecosysteem, geen `autogenerate`/`batch`. Niet aan te raden: Alembic ís het
  SQLAlchemy-canon.
- **Wachten op een Postgres-trigger en dan Alembic.** Verworpen: koppelt de
  audit-winst onnodig aan een event dat misschien nooit komt (Spike 2: SQLite kan
  lang meegaan), en dwingt dan én de port én de migratie-invoering tegelijk —
  meer risico op één moment.

## 6. Beslissing + trigger

**Beslissing: voer Alembic in — nu, forward-only, SQLite-first — als het
gesanctioneerde pad voor de vólgende schemawijziging. Niet als big-bang-refactor
van de bestaande helpers.**

Onderbouwing:
- De DB-afhankelijkheid is opgelost en blokkeert niet (§2.4); "uitstellen tot ná
  de DB-beslissing" is geen open optie meer.
- De audit-baat is **Postgres-onafhankelijk** en dient een expliciet
  platform-succescriterium; ze accrued onmiddellijk op SQLite.
- De kost is begrensd en eenmalig (baseline-stamp), *mits* de CI-drift-guard
  (§4.3) meekomt — dat is de voorwaarde, niet een nice-to-have.
- Forward-only houdt de invoerkost laag: geen herschrijving van de idempotente
  helpers, geen risicovolle rebuild van bestaande productie-schema's.

**Waarom "nu" en niet "zodra nodig":** het alternatief (wachten tot de eerste
niet-additieve wijziging) dwingt Alembic-invoering + een risicovolle
data-migratie in hetzelfde, gehaaste moment. Alembic *koud* invoeren met alleen
een baseline is veel goedkoper en risicolozer dan het *onder druk* invoeren met
een echte destructieve migratie eraan vast.

**Harde trigger die de urgentie verhoogt (van "nu doen" naar "eerst dit"):** de
eerste concrete niet-additieve schemawijziging (kolom drop/type-wijziging/
`NOT NULL`-backfill/constraint) op een productie-DB. Komt die vóór Alembic er is,
dan is Alembic-invoering geen keuze meer maar een voorwaarde voor die wijziging —
anders is het "delete the db" = data-verlies.

## 7. Voorgestelde vervolgkaarten (tekst; niet in deze kaart aangemaakt)

> Deze spike maakt géén kanban-kaarten aan. Onderstaande zijn voorstellen die een
> mens kan prioriteren. De volgorde is een aanbevolen dep-keten.

1. **[chore] Alembic-scaffolding + dependency.** Voeg `alembic` toe aan
   `backend/requirements-dev.txt` (+ runtime indien init migraties draait),
   scaffold `alembic/` met twee ketens (`Base` en `KanbanBase`), elk eigen
   `version_table`. Nog géén productie-init-wijziging. Voorwaarde voor 2–4.
2. **[feature] Baseline-revisies + stamp-pad.** Eén baseline-revisie per keten
   die het huidige schema beschrijft; init-code stampt bestaande DB's op baseline
   i.p.v. upgraden, en upgradet verse DB's. `depends_on` 1. Load-bearing — §4.1.
3. **[feature] CI-drift-guard (kritisch).** Voeg aan `quality.yml` toe: (a)
   `alembic upgrade head` op een verse DB draait schoon; (b) een
   `alembic check`/autogenerate-diff-stap die faalt bij metadata-vs-migratie-
   drift (patroon: `check_openapi_snapshot.py`). Zonder deze kaart verslechtert
   Alembic de reproduceerbaarheid. `depends_on` 2. — §4.3.
4. **[chore] Bevries de hand-migraties + documenteer de workflow.** Markeer
   `_ensure_*`/`_migrate_*` als "geen nieuwe regels — gebruik een Alembic-
   revisie", werk de CLAUDE.md-gotcha "schema changes require deleting the db"
   bij naar de Alembic-workflow, en documenteer de dev-loop (§4.2). `depends_on`
   2.
5. **[chore] (later, optioneel) Migreer bestaande hand-migraties naar revisies.**
   Puur opruiming/consolidatie; geen functionele winst zolang de helpers
   idempotent en harmless zijn. Laag geprioriteerd.

## 8. Bewust buiten scope

- **De Postgres-port zelf** — Spike 2's domein; Alembic is er de voorwaarde voor
  maar deze spike forceert de port niet.
- **De sync-/HLC-op-log-mechaniek** (`sync-hlc-freeze-vs-prune.md`) — orthogonaal
  aan het schema-migratie-substraat.
- **Herschrijven van de bestaande idempotente helpers** — bewust uitgesteld
  (forward-only, §6); kaart 5 als losse opruiming.
- **Frontend & FastAPI/Pydantic** — best-in-class, geen actie (conform het
  parent-plan).
