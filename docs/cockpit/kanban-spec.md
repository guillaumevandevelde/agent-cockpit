---
title: "Kanban — Spec: per-project bord met agent-zelfbediening"
type: spec
status: active
---

# Kanban — Spec: per-project bord met agent-zelfbediening

> Status: ontwerp goedgekeurd (2026-06-13), geïmplementeerd in v1. Datamodel- en
> sync-keuzes zijn vastgelegd via brainstorm; zie de beslissingstabel onderaan.

> **Bron van waarheid:** dit document + `kanban-plan.md` zijn leidend voor het v1-bord
> (passief). De actieve lagen erboven — auto-dispatch en multi-agent — leven in
> `kanban-dispatch-spec.md` en `multi-agent-kanban.md`. Geen recente
> superpowers-tegenhanger voor v1 zelf (de superpowers-tegenhangers documenteren de
> latere lagen). Zie `00-orientation.md` → *Documenten* voor de drie-bomen-regel.

## Doel & filosofie

Per project een **kanban-bord** waar **agents zelf kaarten oppikken** en **deliverables
eraan binden**. Werken via kanban is **optioneel** per project, maar wordt de **hoofdwerking**.

Twee dragende principes:

1. **Local-first.** Cockpit blijft lokaal draaien zonder cloud-afhankelijkheid. Het bord
   werkt volledig op één toestel; cross-device **sync** is een later te activeren laag, niet
   een voorwaarde. Het bord moet blijven werken als een remote store onbereikbaar is.
2. **Agent-zelfbediening via MCP.** De agent (een draaiende Claude Code-sessie) leest en
   schrijft het bord via MCP-tools die Cockpit aanbiedt — echt "zelf oppikken", in-context,
   zo fijnmazig als nodig.

## Afbakening: bord-domein vs. toestel-lokale data

De scheidslijn die cross-device sync afdwingt:

- **Bord-domein (draagbaar, syncbaar):** kaarten, kolommen, deliverables-als-referenties,
  comments, project-identiteit. Bevat **geen** machine-lokale data.
- **Toestel-lokale data (nooit gesynct):** `scheduled_messages`, tmux-targets, absolute
  projectpaden, sessie-state. Blijft in de bestaande lokale Cockpit-SQLite.

Het bord-domein leeft in een **eigen, configureerbare store** achter een repository-laag,
zodat default-lokaal → remote later een pure config-switch is.

## 1. Kolommen & kaartlevenscyclus

Vaste set in v1 (aanpasbare kolommen = latere uitbreiding):

```
Backlog → Analysis → Todo → Doing → Review → Done
```

| Kolom | Betekenis |
|---|---|
| **Backlog** | Ruwe ideeën/taken, nog niet opgepakt. |
| **Analysis** | Een agent onderzoekt, scope't, levert bevindingen/plan (brug naar de bestaande brainstorm/plans-flow). Resultaat: kaart wordt implementatie-rijp. |
| **Todo** | Klaar om te bouwen, wacht op een claim. |
| **Doing** | Geclaimd, in uitvoering. |
| **Review** | Werk klaar, deliverable gekoppeld, wacht op menselijk nazicht. |
| **Done** | Afgerond. |

## 2. Datamodel (twee lagen)

Eigen store (default lokaal SQLite-bestand, los van `claude_registry.db`), via SQLAlchemy.
**Alembic-migraties** voor dit domein vanaf v1 (een toekomstige primary kun je niet wissen).

### 2a. Transport & historie — `kanban_ops` (append-only)

Bron van waarheid voor sync én meteen de activiteiten-feed per kaart. Nooit gemuteerd.

| Veld | Type | Toelichting |
|---|---|---|
| `op_id` | string (PK) | `<device_id>:<seq>`, globaal uniek, idempotentie-sleutel |
| `device_id` | string | toestel dat de op aanmaakte |
| `seq` | int | monotone teller per toestel |
| `hlc` | string | hybrid logical clock (zie §4) — totale ordening |
| `project_key` | string | zie §6 |
| `entity_type` | string | `card` \| `comment` \| `deliverable` |
| `entity_id` | string | doel-entiteit (UUID) |
| `op_type` | string | `create` \| `move` \| `update` \| `claim` \| `release` \| `comment` \| `attach` |
| `payload` | JSON | op-specifieke data (bv. `{column}`, `{title}`, `{kind, ref}`) |
| `created_at` | datetime | wall-clock van aanmaak |

### 2b. Gematerialiseerde state (afgeleid — voor snelle reads/UI)

**`kanban_cards`**

| Veld | Type | Toelichting |
|---|---|---|
| `id` | string (PK) | UUID |
| `project_key` | string | |
| `title` | string | LWW-veld (draagt eigen HLC) |
| `description` | text | markdown; LWW-veld (eigen HLC) |
| `column` | string | LWW-veld (eigen HLC) |
| `rank` | string/float | orde binnen kolom; LWW-veld (eigen HLC) |
| `priority` | string? | optioneel |
| `labels` | JSON? | optioneel |
| `claimed_by` | string? | agent/sessie-id + device van de claimer |
| `claimed_at` | datetime? | |
| `created_at` / `updated_at` | datetime | |
| `*_hlc` | string | per-veld HLC voor de LWW-velden (`title`, `description`, `column`, `rank`) |

**`kanban_deliverables`** — puur additief.

| Veld | Type | Toelichting |
|---|---|---|
| `id` | string (PK) | UUID |
| `card_id` | string (FK) | |
| `kind` | string | `pr` \| `branch` \| `commit` \| `link` \| `note` |
| `ref` | string | **portabele** referentie: PR-URL, of git-remote+SHA, of vrije link/tekst — **nooit** een lokaal pad |
| `created_at` | datetime | |

> Comments worden gematerialiseerd uit `kanban_ops` (`op_type=comment`); een aparte
> `kanban_comments`-view/tabel mag, maar is optioneel — de op-log ís de comment-bron.

## 3. Eén mutatiepijplijn

**Alle** wijzigingen — van MCP én van de UI — lopen door één `apply_operation`-service:

1. ken een nieuwe HLC toe (monotoon t.o.v. de laatst geziene HLC);
2. append naar `kanban_ops` (met `op_id` voor idempotentie);
3. werk de gematerialiseerde state bij volgens het op-type-beleid (§4).

Reads komen **altijd** uit de gematerialiseerde tabellen. Dit ene schrijf-pad is centraal
testbaar en maakt sync triviaal: bij sync verenig je vreemde ops en **herpas** je ze
**idempotent in HLC-volgorde** (toepassen van een reeds verwerkte `op_id` is een no-op).

## 4. HLC & conflictbeleid

**Hybrid Logical Clock (HLC):** combineert wall-clock met een logische teller + `device_id`,
zodat de ordening **totaal en deterministisch** is én ruwweg tijd-gealigneerd, ondanks
klok-drift tussen toestellen. Replay in HLC-volgorde → elk toestel convergeert naar exact
dezelfde eind-state.

Resolutie per op-type bij materialisatie:

| Op-type | Beleid |
|---|---|
| **move** (`column`, `rank`) | LWW per veld via HLC. Overschreven move blijft in de log (zichtbaar in historie). |
| **claim** | **Conditioneel** ("claim indien onclaimed"). Eerste claim (HLC-volgorde) wint; latere claim wordt no-op + genereert een afgeleid **`claim_rejected`**-signaal voor dat toestel/agent ("al geclaimd → kies een andere"). |
| **update** content (`title`, `description`) | LWW per veld via HLC. **Verliezer blijft bewaard in de activiteiten-log** (niets gaat verloren). v1 toont een subtiele "gelijktijdig bewerkt op toestel B — zie historie"-markering wanneer goedkoop detecteerbaar. |
| **comment** / **attach** (deliverable) | Puur additief, mergen altijd zonder conflict. |
| **release** | Heft claim op (LWW via HLC). |

**Upgrade-pad (niet v1):** echte concurrency-detectie voor content-conflicten via een
`parent_hlc` per content-edit (causale tracking) + een interactieve merge-UI. v1 garandeert
"niets verloren" via de log; interactieve merge is later.

## 5. MCP-server (de agent-interface)

Cockpits FastAPI-backend **mount een MCP-endpoint** (HTTP/SSE) via de Python `mcp`/`fastmcp`
SDK. *(Nieuw: vandaag beheert de backend enkel MCP-config, hij serveert nog geen MCP.)*

Tools:

| Tool | Effect |
|---|---|
| `list_cards(project, column?)` | lees |
| `get_card(id)` | lees (incl. activiteiten-feed + deliverables) |
| `create_card(project, title, description, column=Backlog)` | `create`-op |
| `claim_card(id)` | conditionele `claim`-op |
| `move_card(id, column, summary?)` | `move`-op; `summary` verplicht bij `column="Done"`/`"Impediment"`, gepost als comment |
| `update_card(id, fields)` | `update`-op |
| `comment(id, text)` | `comment`-op |
| `attach_deliverable(id, kind, ref)` | `attach`-op |
| `release_card(id)` | `release`-op |

Boundary: de agent praat met **localhost** → backend → **lokale store**. De agent ziet
**nooit** DB/sync-credentials (die blijven server-side, via `credentials_service`). Bij
`claim`/`create` geeft de agent z'n **project- + sessie-context** mee, zodat de UI
"kaart X wordt bewerkt door sessie Y" kan tonen (haakt in op bestaande sessions/presence).

### Initiatief-laag (wat zet de agent áán)

- **v1: autonoom via instructie** — een project-`CLAUDE.md`-passage of een skill instrueert
  de agent om bij afronden zelf de volgende kaart te raadplegen/claimen.
- **Later (optioneel): push-bij-idle** — hergebruik de bestaande delivery engine om bij
  `Stop`-hook de volgende kaart te injecteren. Geen v1.

## 6. Optionaliteit & project-identiteit

- **Opt-in per project.** Een project krijgt een bord wanneer je kanban inschakelt; daarbij
  wordt de `cockpit-kanban` MCP-server voor dat project geregistreerd (via Cockpits bestaande
  MCP-beheer). Projecten zonder dat → geen kanban. Optioneel, gratis.
- **Project-sleutel (`project_key`).** Toestel-onafhankelijk, zodat het bord later over
  toestellen klopt: **git-remote-URL** (genormaliseerd), met **handmatige slug-fallback**
  voor repo's zonder remote.

## 7. UI — feature-module `frontend/src/features/kanban`

- **Bord-pagina** per project (projectkiezer via bestaande `ProjectContext`): kolommen met
  kaarten, drag-to-move, kaarten met de `CLICKABLE_CARD`-conventie.
- **Kaart-detail (drawer/modal, `MODAL_SIZES`)**: beschrijving via `MarkdownRenderer`,
  activiteiten-feed (uit de op-log), deliverables, "claimed by", comments.
- **Aanmaak/bewerk** kaart (`MarkdownPreviewToggle` voor de beschrijving).
- **Enable-kanban-toggle** per project (zet opt-in + MCP-registratie).

Backend-route: `backend/app/api/v1/kanban/router.py`, geaggregeerd in `router.py`.
Frontend volgt het feature-module-patroon (page + components + API + types).

## 8. Sync-laag (gebouwd maar niet geactiveerd in v1)

De op-log maakt sync **vendor-neutraal**: de transport hoeft enkel "geef ops sinds cursor X"
te kunnen. Kandidaten voor de latere primary: **Turso/libSQL embedded replica** (lokale
SQLite-replica, offline-capabel, achtergrond-`sync()`) of zelf-gehoste **`sqld`**; of een
dom **REST push/pull** van ops. Append-only → merge = union, dus de transport hoeft geen
conflictlogica te kennen (die zit in de materialisatie, §4).

v1 levert: lokale op-log + materialisatie + **alle** conflict-logica, volledig werkend op
één toestel. De remote primary + cross-device sync is een latere config-switch achter de
repository-laag.

## 9. Architectuur-afhankelijkheden

```
UI ───────────────┐
                  ├──► apply_operation ──► kanban_ops (append)
MCP-server ───────┘            │                 │
   ▲                          ▼                 ▼
   │                   materialisatie    (later) sync-laag ──► remote primary
agent (localhost)      (kanban_cards,            ▲
                        deliverables)            └── union ops, idempotente replay
```

Elke unit apart testbaar: store/repository (in-memory of tmp-SQLite), `apply_operation`
(pure functie van op → state-delta), MCP-tools (mock de service), HLC (deterministisch),
sync (mock transport).

## 10. Testing

- **Unit** — HLC-monotoniciteit & ordening; `apply_operation` per op-type; conditionele
  claim (eerste wint, tweede `claim_rejected`); LWW per veld; idempotente replay
  (zelfde `op_id` tweemaal = no-op).
- **Convergentie** — twee op-logs met divergerende ops → na union+replay identieke state op
  beide "toestellen" (gesimuleerd).
- **Integratie** — MCP-tool-calls → ops → gematerialiseerde state; REST-routes idem.
- **E2e (handmatig, WSL)** — echte CC-sessie met de `cockpit-kanban` MCP-server: agent
  claimt een kaart, koppelt een deliverable (PR-URL), zet op Review; UI toont de update.

## 11. Niet-doelen (YAGNI v1)

- Aanpasbare kolommen.
- Push-bij-idle injectie (latere initiatief-laag, reuse delivery engine).
- Interactieve merge-UI / causale `parent_hlc`-conflictdetectie.
- Multi-user / permissies / RBAC.
- De remote primary daadwerkelijk draaien (alleen sync-klaar maken).

## Beslissingstabel

| Onderwerp | Keuze |
|---|---|
| Agent-interface | **MCP-tools** (HTTP/SSE), opt-in via MCP-registratie per project |
| Initiatief v1 | **Autonoom via instructie**; push-bij-idle later |
| Opslagfilosofie | **Local-first**, bord als eigen configureerbaar domein |
| Sync/conflict | **Append-only op-log + HLC**, per-op-type resolutie |
| Claim-correctheid | **Conditionele claim** (eerste HLC wint, tweede `claim_rejected`) |
| Kolommen | **Vast**: Backlog → Analysis → Todo → Doing → Review → Done |
| Kaart-creatie | **Mens + agents beide** |
| Deliverables | **Portabele referenties** (PR/branch/commit/link/note), geen lokale paden |
| Project-sleutel | **Git-remote-URL**, slug-fallback |
| Migraties | **Alembic** voor het bord-domein (vanaf v1) |
| Sync-scope v1 | **Lokaal, sync-klaar** (remote = latere config-switch) |
