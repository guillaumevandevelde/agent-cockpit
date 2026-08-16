---
title: "Kernharding — duurzame toestand, schemamigratie en een afdwingbare architectuurgrens"
type: spec
status: active
---

# Kernharding — ontwerp

**Datum:** 2026-08-15
**Volgt uit:** [`cockpit-richting-decision.md`](./cockpit-richting-decision.md) §5

Dit ontwerp beschrijft de drie onderdelen die vóór het waarde-werk moeten landen. Ze zijn onafhankelijk te bouwen, maar de volgorde is bewust: alembic eerst, want de andere twee wijzigen het schema.

---

## 1. Alembic voor twee databases

### 1.1 De uitgangssituatie

Er zijn al migraties, met de hand geschreven en over drie bestanden verspreid:

| Bestand | Regels | Handgeschreven migratielogica |
|---|---|---|
| `app/database.py` | 219 | `_migrate_subscription_prefs_shape`, `_migrate_project_columns`, `_migrate_terminology_columns` |
| `app/kanban/db.py` | 355 | `_ensure_card_columns`, `_ensure_column_table`, `_ensure_work_type_mapping_table` |
| `app/services/scheduling/schema_guard.py` | 68 | volledig — vangt ontbrekende kolommen op tijdens bedrijf |

Ruwweg 460 van die 642 regels zijn zelfgebouwde schemamigratie zonder versiebegrip. Het bewijs dat dit niet houdt staat in de code zelf: `schema_guard.py` bestaat om tijdens bedrijf op te vangen dat een kolom ontbreekt.

Alembic is hier dus een vervanging die netto krimpt, geen toevoeging.

### 1.2 Opzet

De twee stores hebben elk een eigen `DeclarativeBase` — `Base` in `app/database.py`, `KanbanBase` in `app/kanban/db.py`. Die scheiding past op alembic's meerdere-databases-vorm: één `alembic.ini` met twee secties, twee revisiemappen, elk een eigen `env.py`. Aansturen met `alembic --name kanban upgrade head`.

SQLite kan nauwelijks `ALTER TABLE`. Elke revisie die een kolom wijzigt of verwijdert gebruikt daarom `batch_alter_table`. Dat staat in de revisiesjabloon, zodat het niet bij de eerste kolomwijziging opnieuw ontdekt wordt.

### 1.3 Bestaande databases baselinen

Beide databases bestaan en zijn gevuld. Ze mogen niet opnieuw worden aangemaakt.

1. Genereer de basisrevisie met autogenerate tegen een **verse** database uit de modellen.
2. Constateer bij een bestaande database dat de tabellen er zijn en `alembic_version` ontbreekt, en zet dan een stempel met `alembic stamp head`.
3. Draai vóór dat stempel een **driftcontrole die kan weigeren**: vergelijk het werkelijke schema met de modelmetadata en stop bij een verschil.

Stap 3 is niet optioneel. Het huidige schema komt voort uit `create_all` plus vier handgeschreven `_ensure_*`-functies. Een stempel op een afwijkende database maakt die afwijking permanent onzichtbaar.

### 1.4 Waar het draait

`scripts/cockpit.sh start` voert `upgrade head` uit voor beide stores. De applicatie weigert te starten bij een versieverschil in plaats van stil door te draaien. Geen automatische migratie vanuit de app zelf — luid falen is hier veiliger dan behulpzaam zijn.

> **Verduidelijkt bij de uitvoering (2026-08-15).** Deze eis botste met §1.6, dat de testsuite bewust op `create_all` laat draaien: 87 testbestanden starten de app via `TestClient`, en een harde controle liet die allemaal vallen. Opgelost door de controle smaller te maken. Een database zonder `alembic_version` wordt overgeslagen — dat is de `create_all`-vorm. Een database die er wél onder staat maar achterloopt wordt geweigerd. Dat is de enige vorm waarin de faalmodus bestaat.

Vóór elke upgrade komt er een momentopname van de kanban-database. Dat is bestaande code: `backup_service._snapshot_kanban_db` maakt al een WAL-veilige kopie.

### 1.5 Wat verdwijnt

Elke handgeschreven migratie wordt één revisie, waarna de functie weg mag. `schema_guard.py` verdwijnt in zijn geheel, want zijn bestaansreden vervalt zodra het schema versiebegrip heeft.

Eén uitzondering blijft staan: `_migrate_legacy_sqlite` verplaatst het bord van een oude bestandslocatie naar de huidige. Ondanks de naam is dat geen schemamigratie, dus alembic vervangt hem niet.

### 1.6 Testen

- Verse database van basisrevisie naar `head`; daarna schema gelijk aan modelmetadata.
- De driftcontrole weigert aantoonbaar op een database met een ontbrekende kolom.
- De stempelroute laat bestaande gegevens intact, getest met een gevulde kopie.

**Bewuste uitzondering.** De testsuite bouwt haar schema met `drop_all`/`create_all` in `conftest.py`, en 37 testbestanden hangen daaraan. Dat blijft zo, want per test migreren maakt de suite trager zonder iets te bewijzen. In plaats daarvan komt er één CI-controle die `head` vergelijkt met de modelmetadata.

---

## 2. Duurzame toestand

### 2.1 Wat vluchtig mag blijven

Niet alle geheugen is een gebrek. Drie zaken blijven expliciet in het geheugen:

- **`_waiters`** in de drie registries. Dat zijn `asyncio.Event`-objecten, per proces en niet serialiseerbaar.
- **`_panes`** in de session-registry. Tmux-pane-ids komen terug uit tmux zelf; er is al een `_last_reconcile_at`. Herstel door reconciliatie, niet door opslag.
- **`session_signals._limits`**. Verlies van rate-limit-signalen is hinderlijk maar zelfherstellend. Bewust geaccepteerd verlies, hier opgeschreven zodat het geen vergeten gat wordt.

### 2.2 Wat duurzaam moet

Verlies betekent hier dat de fabriek stil een belofte vergeet:

- ~~`pending_queue._queue` — kaarten die op spawn wachten.~~ **Bij de uitvoering weerlegd (2026-08-15):** `enqueue` claimt de kaart niet en verplaatst hem niet, dus die blijft onopgeëist in Todo staan en de dispatch-tick vindt hem elke tien seconden opnieuw. De wachtrij is zelfherstellend; een herstart kost hooguit een retry-vertraging. Duurzaam maken zou werk zijn zonder opbrengst.
- `auto_resume._enabled` en `_messages` — configuratie die de gebruiker via de API zet. **Gebouwd 2026-08-16:** tabel `auto_resume_configs`, de route schrijft door, en `reconciler.hydrate_auto_resume` laadt bij het opstarten terug. **Effect:** auto-resume aanzetten blijft aan staan na een herstart, waar dat eerder stil verviel.
- De pane-resume-belofte. Die heeft zijn rij al — `pane_resume_pending` in de kaart-metadata — maar niets bouwde hem terug. **Gebouwd 2026-08-15:** `services/scheduling/reconciler.py`, plus `pane_resume_cwd` en `pane_resume_message` in dezelfde rij, want zonder die twee is de rij niet genoeg om de job te herbouwen.
- De eenmalige jobs die buiten `scheduler.py` om worden gepland. Op moment van schrijven zijn dat twee plekken, `dispatch.py:5807` en `auto_resume.py:338`; zoek ze bij twijfel opnieuw op met een grep naar `_sched.add_job`.

De duurzame representatie ligt er half. `kanban_cards` heeft al `pending_spawn_session`, `resume_session_id`, `resume_project_folder`, `scheduled_at`, `held_reason`, `held_since` en `held_blocker`. Dit is een omkering, geen nieuwbouw.

### 2.3 De keuze: waar staat de waarheid

**Afgewezen — een `SQLAlchemyJobStore` onder APScheduler.** Jobs overleven dan een herstart, maar APScheduler serialiseert een verwijzing naar de aan te roepen functie. Hernoem je die, dan breken opgeslagen jobs stil. Bovendien ontstaat een tweede waarheid naast de eigen tabellen, en de singletons blijven ongemoeid.

**Gekozen — de database is de waarheid, de scheduler is een cache.** Elke belofte is een rij. Bij het opstarten leest één reconciler die rijen en installeert de jobs opnieuw; achterstallige beloften vuren meteen.

Dit patroon draait hier al. `run_boot_inhaal` voor de recurring triggers is er de bestaande, bewezen toepassing van, gebouwd na de gemiste maandag van 2026-08-03. De keuze generaliseert dat in plaats van er een tweede mechanisme naast te zetten.

### 2.4 Componenten

1. **`app/services/scheduling/reconciler.py`** — één opstartroutine die alle duurzame beloften uit beide stores leest en de bijbehorende jobs installeert. Vervangt de losse `run_boot_inhaal`.
2. **Omgekeerd schrijfpad** — `pending_queue` en `auto_resume` leggen de rij vast vóór ze een job plannen. De dict blijft bestaan, maar als cache.
3. **Eén afdwingbare regel** — `_sched.add_job` mag alleen nog aangeroepen worden vanuit `scheduler.py` en `reconciler.py`. Dat wordt een controle-script in de bestaande `backend-lint`-job.

### 2.5 Gegevensstroom en herstel

Belofte ontstaat, rij vastgelegd, job geïnstalleerd, job vuurt, rij afgesloten. Een herstart op elk punt in die keten is veilig: de reconciler leest de rij en installeert opnieuw. De vorm is idempotent omdat de rij de sleutel is.

**Achterstallig bij opstart:** direct uitvoeren. Is de belofte ouder dan een drempel, dan niet stil laten vallen maar als blokkade op de kaart melden. Dat haakt in op de meldingsregel uit de richtingsbeslissing §4.

### 2.6 Testen

Eén testvorm die nu volledig ontbreekt draagt dit onderdeel: maak een belofte, gooi de scheduler hard weg, bouw hem opnieuw op, en toon aan dat de belofte alsnog vuurt. De 314.000 regels bestaande test raken dit gebrek niet.

---

## 3. De architectuurgrens

### 3.1 Gemeten uitgangssituatie

De grenzen bestaan al impliciet en worden grotendeels gerespecteerd:

- `app.api` wordt door precies één bestand geïmporteerd: `main.py`.
- `app/services/` importeert nergens de api-laag.
- Mechanisme in het domein beperkt zich tot vijf bestanden die `subprocess` importeren: `project_key.py`, `session_cleanup.py`, `headless_runner.py`, `session_recovery.py` en `dispatch.py`.

De gelaagdheid hield dus stand op mapniveau. Hij bezweek binnenin één bestand.

### 3.2 De lagen

1. **Transport** — `app/api/`, `app/mcp_server/`.
2. **Domein** — `app/kanban/` en het merendeel van `app/services/`.
3. **Mechanisme** — `app/services/agentic_cli/`, `app/services/scheduling/`, en alles wat `subprocess`, git of tmux aanraakt.
4. **Persistentie** — `app/models/`, `app/kanban/models.py`, `database.py`, `kanban/db.py`.

Deze indeling loopt dwars door `app/services/` heen. Dat is een feit om vast te leggen, zodat de regels per subpakket gelden en niet per hoofdmap.

### 3.3 De regels

| Regel | Status bij invoering |
|---|---|
| Transport is een blad — alleen `main.py` importeert `app.api` en `app.mcp_server` | groen |
| Domein en persistentie importeren nooit transport | groen |
| Het domein raakt geen mechanisme aan — `app.kanban` importeert geen `subprocess` | vijf benoemde uitzonderingen |

De derde regel gaat als ratel. De vijf bestanden staan met naam en datum in de uitzonderingslijst, en die lijst mag alleen korter worden. Zo kan de poort meteen aan zonder vanaf dag één rood te staan, want een poort die altijd rood staat wordt uitgezet.

Bestanden verplaatsen hoort hier niet bij. `headless_runner.py` en `acp_transport.py` zijn feitelijk mechanisme in de domeinmap, maar dat verhuizen is het opruimwerk uit de richtingsbeslissing §6.

### 3.4 De omvangsratel

Geen importregel had `dispatch.py` op 10.110 regels voorkomen — alle imports daarin zijn keurig. Wat ontbrak was een grens op groei.

Daarom: **een bestand dat al boven de 800 regels zit mag niet groeien.** Kleiner worden mag altijd en de drempel schuift mee naar beneden. Nieuwe bestanden boven 800 regels worden geweigerd.

Dit is het directe antwoord op de vraag hoe een bestand van 10.000 regels ontstaat. Elke afzonderlijke toevoeging was verdedigbaar; niemand bewaakte de som. Bijkomend effect: een kaart die `dispatch.py` aanraakt moet er iets uithalen om er iets in te mogen zetten.

### 3.5 Handhaving en document

`import-linter` draait als stap in de bestaande `backend-lint`-job, met de `if: !cancelled()`-vorm die daar al geldt. Configuratie in `backend/pyproject.toml`.

Het document zelf komt in `docs/cockpit/architectuur.md`: de vier lagen, de vier regels, de uitzonderingslijst met datum en reden per regel, en de bepaling uit §2.4 dat er geen `add_job` bestaat zonder vastgelegde rij.

### 3.6 Testen

`import-linter` is zijn eigen test. Voor de omvangsratel komt er een harnas volgens de bestaande conventie. De assertie toetst de exacte schone-toestandsregel van het script en gebruikt nadrukkelijk niet de vorm `grep -qE "^OK:|WARNING:"` — dat is de tautologische val uit kaart `e5136a3f`.

---

## 4. Buiten scope

Dit ontwerp raakt de drie waarde-onderdelen niet: het mobiele venster, de meldingsregel en het ceremonieprofiel krijgen elk een eigen ontwerp. Ook het verplaatsen van bestaande bestanden tussen lagen valt erbuiten, net als het opruimen van de negentien geërfde frontend-features.
