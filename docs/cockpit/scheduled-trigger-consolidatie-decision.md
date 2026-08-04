---
title: "Beslissing — één tijd-trigger: de klok maakt de kaart, de kaart is het werk"
type: decision
status: decided
---

# Beslissing — één tijd-trigger: de klok maakt de kaart, de kaart is het werk

> Companion van kanban-kaart *"Analyse - scheduled messages"* (`0767c57a…`).
> Aanleiding, letterlijk: *"Twee maal scheduled messages implementatie is niet
> overzichtelijk. Behoud 1 wenselijke opzet. […] Kanban zorgt voor zichtbaarheid
> van de taak, en is gealigneerd op de normale flow. Maar misschien moet de kaart
> pas gespawned worden wanneer ze nodig is? Nu staat ze daar maar..."*
>
> Herziet de gelaagde "A + B naast elkaar"-keuze uit
> [`recurring-cadence-proposal.md`](./recurring-cadence-proposal.md) §2 en §6.

---

## 1. De uitkomst in één alinea

**Kanban blijft de enige uitvoeringsweg.** Een terugkerende taak wordt niet langer
weken vooraf als kaart op het bord gezet, en wordt ook niet in een lang-levende
sessie geïnjecteerd. In plaats daarvan krijgt de klok dezelfde rol die een GitHub-
webhook vandaag al heeft: **een trigger die op het juiste moment een kaart aanmaakt**,
waarna de gewone auto-dispatch het overneemt. De tmux-injectieroute van de
scheduled-messages-feature wordt met pensioen gestuurd; de gedeelde sessie-substraat
eronder (`session_registry`, `tmux_inject`, hook-ingest, auto-resume) blijft
volledig staan, want die draagt kanban-dispatch en Agent Mail.

Daarmee is het antwoord op beide helften van de kaart:

- *"Behoud 1 wenselijke opzet"* → **kaart + dispatcher**, niet sessie-injectie.
- *"Moet de kaart pas gespawned worden wanneer ze nodig is?"* → **ja**, en dat is
  precies het mechanisme dat de tweede implementatie overbodig maakt.

---

## 2. Wat er vandaag dubbel staat

| | **A — kanban-kaart met `scheduled_at`** | **B — scheduled message** |
|---|---|---|
| Eenheid van werk | Kaart op het bord | Rij in `scheduled_messages` |
| Klok | `is_due()` per dispatch-tick (10 s) | APScheduler-job in het proces |
| Uitvoering | Nieuwe sessie in een git-worktree | `tmux send-keys` in een bestaande sessie |
| Herhaling | "chain-of-one-shots": de sessie maakt zijn eigen opvolger | `cron_expr` |
| Zichtbaarheid | Kaart, activity feed, deliverables | Eigen pagina, `delivery_attempts` |
| Bord-integratie | Volledig | Geen |

Ze overlappen op precies één punt — *"doe dit ding op tijdstip T"* — en dat is het
punt waarop een gebruiker moet kiezen zonder dat iets die keuze stuurt. Dat is de
"niet overzichtelijk" uit de kaart.

### 2.1 De derde route bestond al, en koos allang partij

`backend/app/api/v1/webhooks/router.py:1-7` beschrijft zichzelf zo:

> *"Extends the scheduled-messages layer (timer/cron) with an event trigger: an
> external event (e.g. a GitHub PR being opened) **creates a kanban Backlog card**,
> which the existing auto-dispatcher then claims and spawns."*

De event-trigger heeft dus nooit een sessie geïnjecteerd — die maakt een kaart en
laat los. Er is geen inhoudelijke reden waarom een *klok*-trigger dat anders zou
moeten doen dan een *webhook*-trigger. Deze beslissing trekt de tijd-trigger
gelijk met de event-trigger die er al ligt.

---

## 3. Gemeten: wat de twee routes in de praktijk deden

Alle cijfers hieronder zijn afgelezen op **2026-08-04** uit de twee live stores
(`backend/claude_registry.db` en `~/.claude-registry/kanban.db`). Reproductie
onder §3.4.

### 3.1 B heeft nog nooit één keer afgeleverd

| Meting | Waarde |
|---|---|
| Rijen in `scheduled_messages` | **1** (aangemaakt 2026-07-28 16:02) |
| `cron_expr` | `0 9 * * 1` (maandag 09:00 Europe/Brussels), `enabled=1` |
| `last_fired_at` | **NULL** |
| Rijen in `delivery_attempts` | **0** |
| Verstreken maandagen sinds scherpstelling | 1 (2026-08-03) |

De feature is dus vijf weken scherp geweest, heeft één geplande afvuurmoment
gehad, en heeft nul keer geleverd.

**Eerlijke nuance:** dit is geen bewijs dat de code stuk is. `fase-2-plan.md`
Task 12 (runtime e2e) is nooit afgerond — B is code-compleet maar nooit in
bedrijf genomen. Het punt is niet "B werkt niet", het punt is dat B vijf weken
lang niets deed terwijl A ondertussen twee cadansen droeg, en dat de reden
daarvoor deels structureel is (§3.2).

### 3.2 De gemiste maandag is structureel, niet incidenteel

De scheduler is `AsyncIOScheduler()` zonder jobstore-argument
(`backend/app/services/scheduling/scheduler.py:24`) — dus de **in-memory**
jobstore. Jobs overleven het proces niet. Bij elke boot herregistreert
`backend/app/main.py:203-207` de enabled rijen, en `schedule_cron`
(`scheduler.py:50-56`) bouwt een verse `CronTrigger`, die zijn eerstvolgende
afvuurmoment **vanaf nu** berekent. Een afvuurmoment dat passeerde terwijl het
proces uit stond, bestaat daarna niet meer: er is geen misfire om te detecteren,
want er was geen job. `misfire_grace_time=3600` beschermt alleen binnen een
draaiend proces.

Dat is precies wat er gebeurde. Backend-logs rond de gemiste maandag:

```
logs/backend/run-20260731-233850-202108-0.log   laatste regel  2026-08-01T08:54:53Z
logs/backend/run-20260803-203940-1998-0.log     eerste regel   2026-08-03T18:39:42Z
```

Maandag 2026-08-03 09:00 Europe/Brussels = 07:00Z valt midden in dat gat: de
backend stond uit. Bij de boot om 18:39Z berekende de `CronTrigger` de
*volgende* maandag (2026-08-10). De run van 08-03 is stilzwijgend overgeslagen —
geen rij, geen fout, geen signaal.

**A heeft dat probleem per constructie niet.** `is_due(card)`
(`backend/app/kanban/dep_resolver.py:154-172`, waar `dispatch._is_due` naar
delegeert — `backend/app/kanban/dispatch.py:5630-5637`) is geen timer maar een
predikaat: `fire_at <= now`, opnieuw geëvalueerd bij élke tick van 10 s. Een kaart
die due werd tijdens een downtime, wordt bij de eerste tick ná de boot alsnog
opgepakt. Voor een cockpit die op een laptop draait en niet 24/7 aanstaat, is dat
verschil geen detail maar de hele bruikbaarheid.

> Nuance voor volledigheid: `trigger_type="once"` heeft wél een klein vangnet.
> `schedule_once` (`scheduler.py:42-48`) bouwt een `DateTrigger` op een tijdstip
> in het verleden; met `misfire_grace_time=3600` vuurt die alsnog als de boot
> binnen een uur na het gemiste moment valt. Voor `cron` bestaat dat vangnet niet.

### 3.3 A draagt de cadans vandaag — maar de kéten in de prompt is het zwakke deel

A is in gebruik: 10 kaarten hebben ooit een `scheduled_at` gehad, waaronder de twee
levende cadansen (market-research, po-digest). Maar de recursie zit in
prompt-tekst (`recurring-cadence-proposal.md` §4.2, "chain-of-one-shots": de
sessie maakt zelf zijn opvolger), en dat deel faalt aantoonbaar op twee manieren:

1. **De po-digest-keten staat stil.** Kaart `d5b363dd…` (*"[analysis] Wekelijkse
   product-owner-digest — 2026-W32"*, `scheduled_at` 2026-08-03T08:00+02:00) heeft
   `parent_card_id` gezet en **nul deliverables**, dus `held_reason` =
   `awaiting_plan_ref`. De opvolger is als kind aangemaakt zonder dat er ooit een
   `plan_ref` op kwam, en wordt daarmee stil uit dispatch gehouden. De kaart ziet
   er ongeclaimd en ongestart uit en dispatcht nooit — de bekende
   `_awaiting_plan_ref`-val.
2. **De market-research-keten sloeg een week over.** De opvolger `d2f3a10d…` is
   aangemaakt op dinsdag 2026-07-28 met `scheduled_at` **2026-08-10**, terwijl
   §3 van het voorstel expliciet "eerstvolgende maandag, gerekend vanaf nu"
   voorschrijft — dat was 2026-08-03. Eén week stil verdampt.

Beide zijn faalvormen van *"een LLM-sessie berekent en plaatst zijn eigen
opvolger"*, niet van het kaartmodel. Een server-side trigger die de volgende
occurrence uit een cron-expressie afleidt, kan geen van beide fouten maken.

### 3.4 Reproductie

```bash
# B: rijen + afleveringen (registry-DB)
python3 -c "import sqlite3;c=sqlite3.connect('backend/claude_registry.db');\
print(c.execute('select count(*) from scheduled_messages').fetchone(),\
c.execute('select count(*) from delivery_attempts').fetchone())"

# A: kaarten met een schedule (kanban-DB)
python3 -c "import sqlite3,os;c=sqlite3.connect(os.path.expanduser('~/.claude-registry/kanban.db'));\
[print(r) for r in c.execute('select id,title,column,scheduled_at,held_reason from kanban_cards where scheduled_at is not null')]"

# De downtime rond maandag 2026-08-03 09:00 (=07:00Z)
ls -la logs/backend/run-2026073*.log logs/backend/run-2026080*.log
```

> `sqlite3` als CLI bestaat niet op deze box — vandaar de `python3 -c`-vorm.

---

## 4. Waarom kanban wint, en niet "allebei behouden"

`recurring-cadence-proposal.md` §2 koos bewust *gelaagd*: A nu, B als "evolutie",
C als noodpad, met de motivering *"de drie vullen elkaar aan in robuustheid, niet
in functionaliteit"*. Die redenering was verdedigbaar toen B nog onbeproefd was.
Nu er meetdata is, houdt ze geen stand:

- **De beloofde winst van B was spawn-overhead besparen en context warm houden.**
  Dat is een optimalisatie op een pad dat vijf weken lang nul keer gelopen is,
  terwijl A ondertussen tientallen kaarten door de dispatcher heeft geduwd.
  Optimaliseren vóór het pad bestaat is de verkeerde volgorde.
- **B's uitvoeringsmodel is onzichtbaar op het bord.** Een injectie in een
  bestaande sessie produceert geen kaart, geen claim, geen activity feed en geen
  deliverable. Dat is exact de zichtbaarheid die de kaartauteur als kwaliteit van
  A benoemt (*"Kanban zorgt voor zichtbaarheid van de taak, en is gealigneerd op
  de normale flow"*).
- **B's herhaalgedrag is fragiel op deze host** (§3.2), en dat is niet weg te
  configureren zonder een persistente jobstore te introduceren — nieuwe
  infrastructuur voor een pad dat we juist willen opheffen.
- **Twee routes betekent twee keer de vraag "welke gebruik ik?"** bij elke nieuwe
  terugkerende taak, plus twee UI's, twee datamodellen en twee sets tests.

### 4.1 Wat B kan dat A niet kan — en waarom dat geen blocker is

Eerlijk gezegd heeft B één capaciteit die A mist: *"typ op tijdstip T deze tekst
in een sessie die al draait"*, inclusief `when_busy=wait_until_idle` en
`target_kind=session|sandcastle`. Dat is een echte, andere behoefte dan "start dit
werk".

Die behoefte wordt niet weggegooid: `tmux_inject.send_text` blijft bestaan en
wordt al gedeeld door kanban-dispatch en Agent Mail
(`backend/app/services/agent_mail_service.py`). Wat verdwijnt is de *geplande*
variant ervan — de combinatie klok + injectie — met nul gemeten gebruik. Komt de
behoefte terug met een concreet scenario, dan is ze bovenop de bewaarde bouwstenen
opnieuw te maken; ze hoeft niet vijf weken werkloos scherp te staan.

---

## 5. De gekozen opzet

### 5.1 Eén regel

> **Een tijdstip triggert het aanmaken van een kaart. De kaart is het werk. De
> dispatcher voert uit.**

### 5.2 Wat dat concreet betekent

| Onderdeel | Besluit |
|---|---|
| **Terugkerende taak** (market-research, po-digest) | Server-side trigger met een cron-expressie, waarvan de actie `create_card` is — spiegelbeeld van `webhook_triggers`. De kaart bestaat pas vanaf het moment dat ze gedaan moet worden. |
| **Chain-of-one-shots in de skill-prompt** | Vervalt. De volgende occurrence komt uit de cron-expressie, niet uit een LLM-berekening. |
| **`scheduled_at` op een kaart** | **Blijft**, maar niet meer als herhaalmechanisme. Twee legitieme rollen houdt het over: (a) de resume-race-guard die de dispatcher zelf stempelt (`backend/app/kanban/mcp_server.py:1241-1281`), en (b) een mens die een *bestaande* kaart handmatig "niet vóór T" zet. Beide zijn kortcyclisch — geen kaart die weken vooruit staat te wachten. |
| **tmux-injectie als geplande actie** | Vervalt: `delivery.py` en `crud.py` (samen 254 regels, **nul importeurs buiten het package**) plus de scheduled-messages CRUD-laag. |
| **Gedeelde sessie-substraat** | **Ongewijzigd.** Zie §5.3 — dit is de val die deze opruiming makkelijk kan slopen. |

### 5.3 Wat je níet mag weghalen (`services/scheduling/` is geen feature-map)

De mapnaam suggereert dat alles eronder bij scheduled messages hoort. Dat klopt
voor exact twee bestanden. De rest is de gedeelde sessie-substraat van de hele
cockpit:

| Module | Ook geïmporteerd door |
|---|---|
| `session_registry.py` | `kanban/dispatch.py`, `kanban/acp_transport.py`, `kanban/session_recovery.py`, `kanban/headless_runner.py`, `services/sandcastle_service.py`, `config.py` |
| `session_signals.py` | `kanban/dispatch.py` (4×) |
| `tmux_inject.py` | `kanban/dispatch.py`, `services/agent_mail_service.py` |
| `auto_resume.py` | `kanban/dispatch.py`, `kanban/headless_runner.py`, `kanban/acp_transport.py` |
| `pending_queue.py` | `kanban/dispatch.py` (3×), `api/v1/sessions.py` |
| `session_resolver.py` | `kanban/dispatch.py` |
| `idle_state.py` | `api/v1/presence.py` |
| `hook_installer.py` / `hook_script.py` | `main.py`, `api/v1/status.py` |
| `scheduler.py` | `main.py`, `kanban/dispatch.py`, `api/v1/backup.py` (kanban-dispatch-tick, stale-detection, auto-backup) |
| **`delivery.py`, `crud.py`** | **niemand** — dit zijn de enige twee exclusieve modules |

Dezelfde val zit in de **router**: `backend/app/api/v1/scheduled_messages/router.py`
bevat naast de CRUD ook `/hook-event`, `/hooks-status`, `/hooks-install` en
`/auto-resume/{cwd}`. Die zijn niet van scheduled messages — `/hook-event` is het
ingest-punt waar élke Claude Code-sessie zijn lifecycle-events naartoe POST
(`hook_script.py:23` bakt die URL in het hook-commando in `~/.claude/settings.json`),
en dat voedt idle-state, presence, auto-resume en de kanban-dispatcher. Ze staan
alleen onder deze prefix omdat de hooks ooit vóór kanban bestonden. Ze verhuizen
mee naar een eerlijk genoemde router; ze verdwijnen niet.

### 5.4 Zichtbaarheid: "nu staat ze daar maar"

De klacht klopt, met één correctie op wat de UI vandaag doet. Een Backlog-kaart met
een toekomstige `scheduled_at` is niet onzichtbaar: `CardItem.tsx:498-502` rendert
een ⌛-chip met de lokale tijd. Wat er wél scheef zit, is de ready-state ernaast:
`KanbanPage.tsx:297-308` mapt `held_reason === "scheduled"` bewust op
`readyState: "ready"`, en die badge draagt de tooltip *"No open dependencies"*
(`ReadyStateBadge.tsx:143`). De dispatcher hóudt de kaart tegen, maar het bord
presenteert haar als dispatchbaar werk. Een kaart die pas over zes dagen mag
starten, telt zo mee in "wat ligt er klaar".

Met §5.2 verdwijnt de hoofdmoot van dat probleem vanzelf — cadanskaarten bestaan
dan pas op hun eigen ochtend. De handmatige "niet vóór T"-vorm blijft bestaan en
verdient een eigen ready-state in plaats van `ready`.

---

## 6. Wat dit vervangt

- **`recurring-cadence-proposal.md` §2 en §6** — de gelaagde "A nu, B als
  evolutie"-lijn. §3 (cadans: maandag 09:00 Europe/Brussels, zelf-corrigerend) en
  §5 (pauzeer-/override-mechanismen) blijven inhoudelijk overeind; alleen het
  *mechanisme* verschuift van chain-of-one-shots naar een server-side trigger.
- **Backlog-kaart `a4d9f8b6…`** (*"Migrate weekly market-research trigger to
  scheduled-messages (fase 2)"*) — die kaart voert de tegenovergestelde migratie
  uit en is met deze beslissing achterhaald. Ze wordt gesloten als *superseded*.

---

## 7. Vervolgkaarten

| # | Kaart | Afhankelijkheid |
|---|---|---|
| 1 | Server-side terugkerende trigger: cron → `create_card` | — |
| 2 | Beide levende cadansen migreren + chain-of-one-shots uit de skills | na 1 |
| 3 | Injectie-route uitfaseren; hook-/auto-resume-endpoints veiligstellen | na 1 |
| 4 | `scheduled` krijgt een eigen ready-state op het bord | — |

Zie de kind-kaarten van `0767c57a…` voor de acceptance criteria.
