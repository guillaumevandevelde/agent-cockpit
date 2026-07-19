---
title: "`spec_doc`-producent + B↔C-join — ontwerp (leaf design-deliverable)"
type: spec
status: active
---

# `spec_doc`-producent + B↔C-join — ontwerp (leaf design-deliverable)

**Datum:** 2026-07-17
**Status:** ontworpen — gedecomponeerd in 2 vervolgkaarten
**Kaart:** `bb1f61aa…` ([plans-window] uitgesteld deel — B↔C-join), kind van `a70a9272…`
**Bron:** [`plans-feature-decision.md`](./plans-feature-decision.md) §5 stap 3 + §8.2 + §10;
menselijke NB 2026-07-17: *"de B↔C-join via `spec_doc` is GEEN gratis stap (0× gepopuleerd) —
lever B en C eerst náást elkaar."*

## TL;DR

De B↔C-join wil kaart-plan-attachments (B) correleren met `docs/cockpit/`-docs (C) via
`card.metadata["spec_doc"]` (`SPEC_DOC_META_KEY`). Dat anker is vandaag **0× gepopuleerd**;
de enige writer is een handmatig veld in `CardDrawer.tsx`. De join is dus niet onhaalbaar,
maar rust op een anker zonder producent — exact de "infra zonder producent"-kwaal van
`kanban_plans` zelf.

**Uitkomst:** de join is **haalbaar én de moeite waard**, maar niet als je eerst de
consument bouwt. De juiste volgorde is producent → adoptie → join. Bovendien blijkt de
producent méér waard dan alleen deze join: het `spec_doc`-anker heeft een **tweede
consument** (Fase-2 drift-detectie) die even hard verhongert. Daarom `outcome=decomposed`
in twee kaarten:

1. **`c0cccd74…`** — producent voor `spec_doc` in de analyst-decompositie-fase (fundament).
2. **`725fbdd3…`** (dep: 1) — B↔C-correlatie in `/plans/overview`, mét adoptie-pre-check-gate.

## 1. Wat de join precies is (en wie B/C zijn)

De reeds-gemergede stap-1-aggregator (`GET /plans/overview`, kaart `885d0b61…`) retourneert
twee **ongejoinde** secties:

| Sectie | Inhoud | Bron | Scope |
|---|---|---|---|
| **B** `cards` | `plan`/`plan_ref`-deliverables op kaarten | kanban-DB | per `project_key` |
| **C** `docs` | `docs/cockpit/*.md`-index | git-tree (filesystem) | repo-wide |

`plan`-deliverables landen op de **parent** van een decompositie; `plan_ref`-deliverables op
**elk kind** (`add_plan_attachment` in `mcp_server.py`). B is dus effectief "elke kaart in
een multi-agent-decompositie". De **join** wil per C-doc de B-kaarten tonen die dat doc
implementeren, via `card.metadata["spec_doc"]` — het repo-relatieve pad (of URL) dat
spec-driven-development Fase 1 als machinaal anker definieerde
([`spec-driven-development-analysis.md`](./spec-driven-development-analysis.md) §6).

## 2. Waarom het anker leeg is — geverifieerd

`SPEC_DOC_META_KEY = "spec_doc"` is gedefinieerd in `backend/app/kanban/schemas.py:31` en
gespiegeld in `frontend/src/features/kanban/types.ts:203`. De **enige** writer is
`SpecLinkSection` in `CardDrawer.tsx:759` — een handmatig UI-veld dat een mens inline zet.
Geen agent, geen automatisering, geen dispatch-stap schrijft het. Resultaat: 0 kaarten
dragen het (bevestigd in `plans-feature-decision.md` §8.2). Dit is dezelfde pathologie als
`kanban_plans`: gedefinieerde infra, geen producent.

## 3. De structurele kern die het ontwerp stuurt

`spec_doc` heeft twee semantieken die in Fase 1 samengevat werden als "het doc dat de kaart
implementeert/**bijwerkt**":

- **implements** (voorwaarts) — de kaart bouwt iets dat in doc X ontworpen staat; de kaart
  *edit doc X meestal niet*. Alleen kenbaar op het moment dat iemand de kaart met kennis van
  X aanmaakt.
- **updates** (achterwaarts) — de kaart *wijzigt* doc X; kenbaar uit het git-diff bij ship.

Die twee wijzen naar **verschillende producenten**. En ze bepalen of de join iets oplevert:
een correlatie "doc X ← kaarten die X implementeren" is precies de *implements*-semantiek.
De *updates*-semantiek levert "doc X ← de kaart die X schreef", wat vaak één spec-authoring-
kaart is — minder interessant als groepering.

Daarom is de **voorwaartse, implements-link de juiste te produceren link**, en het moment
waarop die kenbaar is, is decompositie/aanmaak — niet ship.

## 4. Producent-ontwerp: analyst-decompositie schrijft `spec_doc`

**Aanbevolen primaire producent:** de analyst-fase (Modus 1) zet `metadata["spec_doc"]` op
een kind-kaart wanneer dat kind een concreet `docs/cockpit/*.md`-doc implementeert/bijwerkt,
op het `create_card`-moment.

Waarom dáár:

1. **Rijkste context, precies dan.** De analyst heeft net de bron-analyse gelezen/geschreven;
   hij *weet* welk doc elk kind aanstuurt. Elk later moment moet dat opnieuw afleiden.
2. **Juiste semantiek.** Het is de voorwaartse implements-link — de enige die de join
   niet-triviaal maakt.
3. **Geen nieuw datamodel.** `create_card` én `update_card` accepteren al een `metadata`-dict
   (geverifieerd in `mcp_server.py:155` / `:443`). Dat is exact de Fase-1-premisse: hergebruik
   de `metadata`-bag, geen kolom.
4. **Bestaande uitzondering blijft gelden.** Een kind wiens spec de **plan-attachment zélf**
   is (`plan`/`plan_ref`) heeft geen expliciete link nodig (Fase-1-regel). De producent vult
   `spec_doc` alleen wanneer een *los* canoniek C-doc het kind aanstuurt.

Dit is een **prompt-/gedrags**-producent (de analyst-persona volgt de instructie), net als de
rest van het analyst-contract. Het `outcome`-enum is wél hard gepoortd; `spec_doc` niet — een
harde gate is scope-creep voor deze stap en niet nodig om adoptie te starten.

### 4.1 Waarom niet ship-diff als primaire producent

Een mechanische alternatief: bij Done het gemergede diff inspecteren en, als het precies één
`docs/cockpit/*.md` aanraakte, dat als `spec_doc` schrijven. Volledig automatisch, git-
gegrond, geen persona-afhankelijkheid. **Maar** het vangt de *updates*-semantiek (het doc dat
de kaart *editte*), niet *implements* — precies de zwakkere kant uit §3. Een kaart die doc X
implementeert edit X doorgaans niet, dus ship-diff mist juist de interessante links. Bewaard
als **prose-alternatief / optionele backfill**, niet als kaart: het is een zwakker signaal met
andere semantiek, en meer kaarten hier is premature scope (guard tegen Backlog-spam).

## 5. De tweede consument die de waarde verandert

`spec_doc` is niet alleen het anker voor deze join. `find_spec_drift_for_card`
(`backend/scripts/drift_checks.py:311`) — de Fase-2 spec-drift-detector — leest exact
`card.metadata["spec_doc"]` om te bepalen of een functionele diff zijn gelinkte spec
meebewoog. Die detector kan vandaag **niets** flaggen omdat geen kaart een anker draagt.

Daarmee is de producent uit §4 **niet "infra voor de join"**, maar de ontbrekende producent
waar het héle spec-driven-development-spoor (Fase 1 → Fase 2) op wacht. De join is één
downstream-begunstigde; drift-detectie is de andere. Dit tilt de producent van "twijfelachtig
de moeite waard" naar "duidelijk de moeite waard, los van de join".

## 6. De join bovenop de producent (`/plans/overview`-uitbreiding)

Zodra `spec_doc` gepopuleerd raakt, is de correlatie een kleine uitbreiding van de bestaande
stap-1-endpoint — geen nieuw datamodel:

- **Backend:** per C-docpad de lijst van B-kaart-ids/titels waarvan `spec_doc == dat repo-
  relatieve pad`. URL-specs (`http(s)://…`) zijn niet-correleerbaar → overslaan (spiegelt
  `find_spec_drift_for_card`, dat URL-specs eveneens uitsluit, `drift_checks.py:337`).
  Hergebruik de bestaande single-SQL-join-stijl uit `_list_card_plan_items`.
- **Frontend:** in het Plans-venster-detail (voortbouwend op de herbestemming, kaart
  `9e33a359…`) "geïmplementeerd door kaarten: […]" op een C-doc en de omgekeerde link op een
  B-kaart. Geen nieuwe pagina.

### 6.1 De adoptie-gate — de anti-trap

De join-kaart mag de val die deze hele analyse aandreef **niet herhalen**: een consument
bouwen op een leeg anker. Daarom is de **eerste** acceptatie-stap van kaart `725fbdd3…` een
*meting*, geen code: draai het meet-commando uit de producent-kaart (telt kaarten met niet-
leeg `spec_doc`). Is de telling ~0, dan **stopt** de executor met een gemotiveerd comment/
impediment i.p.v. een lege correlatie te shippen. Pas bij een niet-triviale telling wordt de
UI gebouwd. Zo wacht de consument op *echte* adoptie, niet alleen op het bestaan van de
producent-code.

## 7. Feasibility-oordeel

**Haalbaar en de moeite waard — mits producent-eerst, join-achteraf-met-gate.** De join op
zichzelf is een nice-to-have groepering bovenop wat de aggregator al náást elkaar toont; de
*producent* draagt het echte gewicht, want die activeert óók Fase-2 drift. `not_feasible`
zou oneerlijk zijn (de join is technisch triviaal zodra het anker vult); tegelijk zou de
join *nu* bouwen de trap herhalen. De decompositie codeert precies die volgorde.

## 8. Decompositie (kinderen van `bb1f61aa…`)

| # | Kaart | Type | Dep | Kern |
|---|---|---|---|---|
| 1 | `c0cccd74…` Producent voor `spec_doc` | feature | — | analyst-decompositie zet `metadata["spec_doc"]` op kind-kaarten die een `docs/cockpit/`-doc implementeren; + meet-commando voor adoptie. Voedt óók Fase-2 drift |
| 2 | `725fbdd3…` B↔C-correlatie in `/plans/overview` | feature | 1 | groepeer C-docs met implementerende B-kaarten; **adoptie-pre-check eerst** — stop bij ~0 populatie |

De kern-levering (B+C náást elkaar, kaarten `885d0b61…`/`9e33a359…`/`528c5ca2…`) is
onafhankelijk en blokkeert nergens op — deze twee kaarten zijn puur additief.

## 9. Bekende risico's

- **Smalle populatie.** De implements-link vult alleen voor kaarten die een *los* C-doc
  aansturen; kinderen wiens spec de plan-attachment zélf is dragen (correct) geen link. De
  adoptie-gate van kaart 2 is de eerlijke uitweg als die slice te dun blijft — dan is een
  gemotiveerde stop een legitiem eindpunt, niet een mislukking.
- **Prompt-producent = gedragsafhankelijk.** Geen harde gate; adoptie ramp-t geleidelijk en
  alleen voor nieuw-aangemaakte kaarten. Dat is aanvaard: de meting maakt de ramp *zichtbaar*
  i.p.v. te gokken.
- **Ship-diff-verleiding.** Een executor kan geneigd zijn de mechanische ship-diff-backfill
  als "de" producent te bouwen; §4.1 legt uit waarom dat de verkeerde semantiek vangt —
  expliciet buiten scope van kaart 1.

## 10. Meet-commando (adoptie-teller)

De adoptie-gate van kaart-2 (`725fbdd3…`, §6.1) moet een **echt getal** lezen — hoeveel
kaarten dragen vandaag een niet-leeg `spec_doc`? — i.p.v. een schatting. De kanban-DB leeft op
`~/.claude-registry/kanban.db` (zie `backend/app/config.py`), `metadata` is een JSON-kolom op
`kanban_cards` (`backend/app/kanban/models.py:122`). Eén SQL-regel telt de populatie:

```bash
sqlite3 ~/.claude-registry/kanban.db \
  "SELECT COUNT(*) FROM kanban_cards
   WHERE COALESCE(TRIM(json_extract(metadata, '\$.spec_doc')), '') != '';"
```

`json_extract(metadata, '$.spec_doc')` haalt het anker uit de bag; `COALESCE(TRIM(...), '') != ''`
sluit zowel `NULL` (geen key / geen metadata) als lege/whitespace-strings uit, zodat alleen
kaarten met een echt gevuld pad tellen. Uitsplitsing per doc (welke docs geïmplementeerd worden)
voor een rijkere gate:

```bash
sqlite3 ~/.claude-registry/kanban.db \
  "SELECT json_extract(metadata, '\$.spec_doc') AS spec_doc, COUNT(*) AS n
   FROM kanban_cards
   WHERE COALESCE(TRIM(json_extract(metadata, '\$.spec_doc')), '') != ''
   GROUP BY spec_doc ORDER BY n DESC;"
```

Staat de `sqlite3`-CLI niet op de box (zoals op de huidige WSL-dev-omgeving), gebruik dan de
venv-python — zelfde query, zelfde getal:

```bash
/home/vdvgu/claude-cockpit/backend/venv/bin/python -c "
import sqlite3, os
c = sqlite3.connect(os.path.expanduser('~/.claude-registry/kanban.db'))
print(c.execute(\"SELECT COUNT(*) FROM kanban_cards WHERE COALESCE(TRIM(json_extract(metadata,'\$.spec_doc')),'') != ''\").fetchone()[0])
"
```

Kaart-2 draait deze telling als **eerste acceptatie-stap**: telling ~0 → gemotiveerde stop
(comment/impediment), i.p.v. een lege correlatie te shippen; niet-triviale telling → bouw de UI.
