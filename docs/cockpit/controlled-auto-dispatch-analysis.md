---
title: "Controlled auto-dispatch — selectief dispatchen per soort werk"
type: analysis
status: active
---

# Controlled auto-dispatch — selectief dispatchen per soort werk

> Status: **analyse + aanbeveling.** Input voor kanban-kaart `92c39a31…`
> ("Controlled auto dispatch", 2026-07-19). Bouwt voort op
> `kanban-dispatch-spec.md` en `work-type-routing-analysis.md`.

## De wens

> "Ik had momenteel enkel graag analyse kaarten gedispatched, maar ik heb niet de
> nodige functionaliteit om dit te doen."

Vertaald: auto-dispatch is vandaag **aan of uit** per project. De operator wil een
tussenstand — laat de dispatcher draaien, maar alleen voor één soort werk (nu:
analyse), zonder het bord leeg te trekken met engineer-sessies.

## 1. Huidige stand van zaken (geverifieerd in de code)

### 1a. Wat er al is aan dispatch-rem

De dispatcher heeft al vijf onafhankelijke filters. Een kaart is dispatchbaar
iff `_is_due` AND `not _awaiting_plan_ref` AND `not _is_gated` AND
`meets_dep_prerequisites` AND de doelkolom niet aan zijn cap zit:

| Rem | Mechanisme | Granulariteit |
|---|---|---|
| Autodispatch-toggle | `KanbanMeta["autodispatch:<project_key>"]` (`dispatch.py:245-274`) | **per project, alles-of-niets** |
| Usage-limit-pauze | `dispatch_pause.py`, per provider | per provider, tijdelijk |
| `scheduled_at` | `_is_due` (`dispatch.py:241`) | per kaart, op tijd |
| `metadata.gated_on` | `_is_gated` (`dispatch.py:2517`) | per kaart, handmatig |
| Per-kolom cap | `KanbanColumn.max_sessions` → `_column_max_sessions` (`dispatch.py:2233`) | **per kolom, numeriek** |

### 1b. Waarom de wens vandaag niet uitvoerbaar is

De laatste rij is bijna precies het gevraagde instrument. `work_type` routeert al
naar een persona-kolom (`_resolve_work_type_fallback` → `_phase_target_agent`,
`dispatch.py:918-959` / `:171-190`): `analysis` → `analyst`, feature/bug/chore →
`engineer`. Op dit bord bestaan die kolommen ook echt, met caps:

```
analyst   max_sessions=2
engineer  max_sessions=2
reviewer  max_sessions=2
```

"Alleen analyse dispatchen" is dus per constructie: **zet de cap van `engineer` en
`reviewer` op 0, laat `analyst` staan.** De cap-gate in `dispatch_project`
(`dispatch.py:3650-3657`) slaat een kaart met een volle doelkolom over en gaat
door met de volgende kandidaat — een gepauzeerde engineer-kolom laat de
analyst-kolom dus *niet* verhongeren.

Het werkt alleen niet, om één regel:

```python
# dispatch.py:2247 — _column_max_sessions
return {r.name: r.max_sessions for r in rows
        if r.max_sessions is not None and r.max_sessions > 0}
```

`0` valt uit de dict, `col_cap` wordt `None`, en de kolom dispatcht
**ongelimiteerd**. `0` betekent vandaag dus het tegenovergestelde van wat een
operator zou verwachten. De UI bevestigt dat: `ColumnSettingsDialog.tsx:222-234`
klemt de stepper op minimaal 1 (`Math.max(1, …)`, `disabled` bij ≤1) en de
"∞"-knop schrijft letterlijk `0` weg als "geen limiet"
(`ColumnSettingsDialog.tsx:268` rendert `0` als `∞`).

**Kortom: de waarde die "blokkeer deze kolom" zou moeten betekenen, is bezet als
sentinel voor "onbeperkt".** Dat is het hele gat.

## 2. Opties

### Optie A — `max_sessions = 0` betekent "kolom gepauzeerd"

Hergebruikt het bestaande veld, de bestaande UI-plek en de bestaande gate.

- **Voor:** geen schemawijziging (beslissend — zie §3), één helper
  (`_column_max_sessions`) voedt alle drie de dispatch-paden (`dispatch_project`,
  `dispatch_all_pending` `:3964`, retry-pad `:4364`), dus één fix dekt ze
  allemaal. Composeert met de bestaande caps: 0 is gewoon het degenerate geval
  van een cap.
- **Tegen:** semantische omkering van een bestaande opgeslagen waarde. "Geen
  limiet" moet verhuizen van `0` naar `null`.
- **Migratierisico: nul op dit apparaat.** Geteld in
  `~/.claude-registry/kanban.db`: 7 kolomrijen, waarvan 3 met een cap (allemaal
  `2`), **0 rijen met waarde `0`**. Reproductie:
  `python3 -c 'import sqlite3,os; p=os.path.expanduser("~/.claude-registry/kanban.db"); c=sqlite3.connect("file:"+p+"?mode=ro", uri=True); [print(r) for r in c.execute("select project_key,name,max_sessions from kanban_columns")]'`

### Optie B — per-project work_type-allowlist

Nieuwe `KanbanMeta`-sleutel (`autodispatch_work_types:<project_key>` = csv),
gefilterd in `_next_card`.

- **Voor:** drukt de wens letterlijk uit ("alleen `analysis`").
- **Tegen:** `_next_card` wordt ook door de handmatige bulkpaden gebruikt — een
  filter daar maakt "Dispatch all" stilletjes selectief, wat een operator die
  bewust op de knop drukt niet verwacht. Dekt bovendien kaarten zónder
  `work_type` niet (die vallen door naar de engineer-fallback en zouden dus of
  altijd, of nooit dispatchen — beide fout). En het duplic**eert** een as die
  de kolom-cap al modelleert.

### Optie C — aparte `paused: bool`-kolom op `kanban_columns`

Semantisch het schoonst: geen sentinel-truc.

- **Tegen, en dit is doorslaggevend:** dit project heeft **geen
  migratiesysteem** (CLAUDE.md: "schema changes require deleting the db"; de
  tabellen komen uit `create_all`). Een kolom toevoegen aan `kanban_columns`
  kost een handmatige `ALTER TABLE` of een DB-wipe, voor precies dezelfde
  expressiviteit die `max_sessions=0` gratis levert.

## 3. Aanbeveling: optie A

Optie A is de enige die de wens vervult zonder schemawijziging én zonder de
handmatige dispatch-paden te veranderen. Concreet:

1. **Backend** — `_column_max_sessions` neemt `0` mee (`>= 0` i.p.v. `> 0`).
   Daarmee wordt `col_cap = 0`, de vergelijking `col_counts.get(…, 0) >= 0` is
   altijd waar, en elke kaart met die doelkolom wordt overgeslagen. Geen andere
   backend-wijziging nodig: de drie cap-call-sites lezen allemaal dezelfde
   helper.
2. **Frontend** — "geen limiet" schrijft `null` i.p.v. `0`; `0` wordt bereikbaar
   als expliciete *Pause*-actie en rendert als "Paused" i.p.v. `∞`.

Daarna is de wens een boardhandeling van tien seconden: engineer → Pause,
reviewer → Pause, analyst blijft op 2.

### Bewuste aanname (fork, best-effort beslist)

Deze aanpak pauzeert **per persona-kolom**, niet per `work_type`. In de praktijk
vallen die samen, met één rand: een kaart met `work_type='feature'` waarop
`analyst_agent_id` gezet is, draait zijn *analyse-fase* in de analyst-kolom en
dispatcht dus door terwijl engineer gepauzeerd is. Dat is hier als **gewenst**
beoordeeld — het ís analysewerk, en de executor-fase blijft netjes hangen tot
engineer weer open gaat. Wie later tóch strikt op `work_type` wil filteren, kan
optie B er bovenop leggen; deze aanbeveling sluit dat niet uit.

### Bewust níet in scope

Een one-click preset op het bord ("alleen analyse") is een comfortlaag bovenop
dezelfde twee kolomacties. Geen kaart — eerst de mechaniek, dan pas beoordelen
of het handmatige pad echt schuurt.

## 4. Randgevallen voor de implementatie

- **Al draaiende sessies worden niet gedood.** De cap is een *dispatch*-gate;
  pauzeren stopt nieuwe spawns, lopende engineer-sessies lopen af. Dat is het
  gewenste gedrag en hoort in de UI-tekst te staan.
- **Handmatige single-card dispatch blijft de cap omzeilen** (bestaand gedrag) —
  dat is de operator-override en moet zo blijven.
- **Backlog-kolom heeft geen cap-UI** (`isBacklog` verbergt Edit/Delete) en is
  geen doelkolom; niet relevant.
- **Verhongering kan niet optreden:** de skip-en-ga-door-lus verwijdert de
  overgeslagen kaart uit de werkset (`dispatch.py:3654-3656`), dus een
  gepauzeerde kolom blokkeert de tick niet.

## 5. Follow-up kaarten

| # | Kaart | Dep |
|---|---|---|
| 1 | Backend: `max_sessions=0` = kolom gepauzeerd | — |
| 2 | Frontend: kolom-pauze in `ColumnSettingsDialog` (`null` = geen limiet) | 1 |

Kaart 2 hangt aan 1 omdat het label "Paused" in de UI pas waar is zodra de
backend `0` daadwerkelijk als blokkade leest; andersom zou de UI liegen.

✅ Geïmplementeerd (kaart `18a97128…`): `ColumnSettingsDialog` schrijft nu `null`
weg voor "geen per-kolom-limiet" (∞-knop), heeft een expliciete **Pause**-knop
die `max_sessions=0` wegschrijft, en rendert de read-only regel als `∞` (null) /
`Paused` (0) / `max n` (n>0). De stepper mag naar 0. Hulptekst + tooltips maken
duidelijk dat pauzeren alleen nieuwe dispatches stopt en lopende sessies niet
afbreekt. Vitest-dekking in `ColumnSettingsDialog.test.tsx`.
