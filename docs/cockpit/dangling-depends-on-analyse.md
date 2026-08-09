---
title: "Analyse — verweesde `depends_on` blokkeren kaarten permanent en onzichtbaar"
type: analysis
status: active
---

# Analyse — verweesde `depends_on` blokkeren kaarten permanent en onzichtbaar

**Kaart:** `ea7a8e5a` "Analysis - Blocked cards" · **Datum:** 2026-07-17 · **Type:** leaf design-deliverable (analyst modus 2)

## 0. Vraag

> "Heel wat blocked kaarten in backlog, niet duidelijk waarom ze blocked zijn. Ik
> betwijfel of ze zelf als wees blocked zijn achtergelaten… Bekijk grondig of deze
> dependencies nog werken en maak ze zichtbaarder."

Kort antwoord: **de vermoeden klopt.** Van de 8 Backlog-kaarten met een `depends_on`
zijn er **4 permanent geblokkeerd door verwezen naar kaarten die niet meer bestaan**
— terwijl het werk waarvan ze afhangen in werkelijkheid **al gemerged/afgerond** is.
Ze dispatchen daardoor nooit, en de enige zichtbare reden op het bord is een cryptisch
`Blocked by: (missing)`. De andere 4 zijn gezond geblokkeerd (wachten op een levende
zuster-kaart). Dit doc legt het mechanisme uit, inventariseert alle gevallen, herstelt
de 4 wezen direct, en levert 3 vervolgkaarten voor de structurele fix.

## 1. Root cause — twee samenwerkende bugs

### 1.1 De dep-resolver faalt *closed* op een ontbrekende parent

`backend/app/kanban/dep_resolver.py` (`meets_dep_prerequisites`):

```python
for parent_id in deps:
    parent = cards_by_id.get(parent_id)
    if parent is None:
        return False          # ← een ontbrekende parent = "niet Done" = fail-closed
    if getattr(parent, "column", None) != "Done":
        return False
return True
```

Fail-closed is op zich **de juiste keuze** (een onbekende dep mag geen kaart per ongeluk
laten spawnen). Maar het maakt een verwezen (deleted) dep-id ononderscheidbaar van een
dep die nog niet Done is. Een kaart met een dangling `depends_on` is daarmee
**voor altijd** ondispatchbaar: de parent bestaat niet meer, dus wordt hij nooit "Done".

Deze gate zit op álle dispatch-paden: de auto-tick (`dispatch_project`),
`dispatch_all_pending` (dispatch.py:3747) en `redispatch_all_orphans` (dispatch.py:3811).
Er is geen pad dat een dangling-dep-kaart alsnog oppikt.

> ✅ Geïmplementeerd (kaart `76d70fd5`) — de auto-tick (`dispatch_project`) faalt
> niet langer *stil* op een dangling dep. Bij een `depends_on`-id dat nergens op het
> bord bestaat (`dep_resolver.dangling_dep_ids` tegen de board-brede
> `service.all_card_ids`-orakel) verplaatst `dispatch._flag_dangling_dep_card` de
> kaart naar Impediment met een actionable `**Dangling dependency:** `-comment + het
> rode `error`-label, in plaats van 'm eindeloos in Backlog te laten hangen. Een
> gezonde niet-Done-dep (id bestáát, kolom ≠ Done) blijft een stille skip, en een
> cross-project-dep wordt niet als dangling geflagd (board-brede existentie). De
> bulk-paden (`dispatch_all_pending`/`redispatch_all_orphans`) blijven bewust een
> stille skip — die zijn expliciete operator-acties, geen achtergrond-tick.

### 1.2 `clear_column` / card-delete verwijderen Done-kaarten zonder dep-besef

`backend/app/api/v1/kanban/router.py:1392` (`clear_column`, achter de **"Clear Done"**-knop
in `KanbanPage.tsx`):

```python
async def clear_column(payload):
    cards = await service.list_cards(s, payload.project_key, column=payload.column)
    for card in cards:
        await apply_operation(s, op_type="delete", entity_type="card", ...)  # hard delete
```

Er is **geen** controle of een te verwijderen kaart voorkomt in de `depends_on` van een
andere, nog niet-Done kaart. Zodra "Clear Done" een afgeronde parent verwijdert, klapt
elke afhankelijke kaart van *"dep satisfied (parent in Done)"* om naar
*"dep missing → fail-closed → permanent blocked"*. De single-card delete heeft dezelfde
blinde vlek (de bestaande `_blocking_card_ids`-helper in `service.py:126` wordt alleen
gebruikt voor het `blocking`-**lijstfilter**, niet als delete-guard).

**Samen** vormen 1.1 + 1.2 de val: een volkomen normale operator-actie ("ik ruim de
Done-kolom op") verandert stilletjes een gezonde, vervulde dependency in een permanente,
onzichtbare blokkade. Dit is exact het patroon dat de gebruiker aanvoelde als "als wees
blocked achtergelaten".

### 1.3 De UI maakt het onderscheid niet zichtbaar

`frontend/src/features/kanban/KanbanPage.tsx:225-236` leidt de badge af:

```ts
const parent = cardsById.get(depId);
if (!parent || parent.column !== "Done") {
  blockerTitles.push(parent?.title ?? "(missing)");
}
```

Een verwezen dep en een levende-maar-niet-Done dep produceren allebei dezelfde amber
**"Blocked"**-badge (`ReadyStateBadge.tsx`). De tooltip toont hooguit `Blocked by:
(missing)` — dat vertelt de operator niet dát de blokkade permanent is en menselijk
ingrijpen vraagt, versus tijdelijk (wacht op een zuster die vanzelf Done wordt). "Blocked"
is bovendien hetzelfde woord dat elders (Impediment) "een mens moet ingrijpen" betekent
— zie `analyse-levenscyclus-decision.md` §5 en kaart `f03baadb`.

## 2. Inventaris — alle 8 Backlog-kaarten met `depends_on` (peildatum 2026-07-17)

| Kaart | Titel (kort) | `depends_on` | Staat van de dep | Verdict |
|-------|--------------|--------------|------------------|---------|
| `c980a926` | [synthese] platform-als-app-factory | `0e185cfa`, `4e9a653a`, `9d5d3f2a`, `05512e3d` | **alle 4 verwijderd**; 4 facet-docs bestaan wél | 🔴 wees — werk af, dep dangling |
| `f88e50e5` | [transport] "Take over" (headless→pane) | `f418db32` | **verwijderd**; headless-transport gemerged (`5b4305a`) | 🔴 wees — dep af |
| `04f7c427` | `Awaiting Subtasks`-parkeerkolom | `b4f74609` | **verwijderd**; outcome-poort gemerged (`b2e7333`) | 🔴 wees — dep af |
| `d404a11f` | AnthropicUsageProvider registreren | `d160d13f` | **verwijderd**; MiniMax-attributiefix gemerged (`5e4abae`) | 🔴 wees — dep af |
| `81797046` | Subtaak-rollup op parent | `f03baadb` | levende Backlog-zuster (ready) | 🟢 gezond |
| `59f191ef` | Run-ledger frontend-tab | `aa8158e3` | levende Backlog-zuster (ready) | 🟢 gezond |
| `725fbdd3` | B↔C-correlatie /plans/overview | `c0cccd74` | levende Backlog-zuster (ready) | 🟢 gezond |
| `528c5ca2` | `kanban_plans` uitfaseren | `9e33a359` | in `engineer`-kolom, actief geclaimd (in progress) | 🟢 gezond |

**Meetcommando** (reproduceer de inventaris tegen de kanban-DB):

```bash
# per Backlog-kaart met depends_on: toont elke dep-id + of die nog bestaat en z'n kolom
python3 -c '
import sqlite3
con = sqlite3.connect("file:backend/claude_registry.db?mode=ro", uri=True)
for row in con.execute("SELECT c.id, c.title, c.depends_on FROM kanban_cards c WHERE c.project_key=\"git:github.com/guillaumevandevelde/claude-cockpit\" AND c.column=\"Backlog\" AND c.depends_on IS NOT NULL AND c.depends_on != \"[]\""):
    print(row)
'
# een dep-id is 'dangling' als deze query 0 rijen geeft:
#   SELECT 1 FROM kanban_cards WHERE id='<dep-id>';
```

(De inventaris hierboven is met dit patroon opgebouwd via de MCP `get_card`/REST
`GET /cards/{id}` — een `not_found`/`Not Found` = verwezen id.)

De 🔴-kaarten delen één handtekening: het depended-on werk is **al klaar** (docs bestaan,
commits gemerged), maar de Done-kaart (en bij de synthese óók de umbrella-parent
`8db831a0`) is later verwijderd — vrijwel zeker via "Clear Done" of een handmatige delete.

## 3. Directe reparatie (uitgevoerd door deze analyse)

Voor alle 4 wees-kaarten is het depended-on werk geverifieerd als afgerond, dus de
dependency is *in werkelijkheid vervuld*. De correcte board-reparatie is het legen van de
dangling `depends_on` zodat de kaart weer dispatchbaar wordt. Deze analyse heeft dat
gedaan (`update_card(depends_on=[])`) en op elke kaart een comment achtergelaten met
verwijzing naar dit doc:

- `c980a926` — 4 dangling deps geleegd. **Let op:** de umbrella-parent `8db831a0` én de
  4 zuster-analysekaarten zijn óók verwijderd; AC#2 ("comment op ouderkaart `8db831a0`")
  is daardoor onuitvoerbaar. Comment op de kaart benoemt dit — de executor levert alleen
  het consolidatie-doc `platform-als-app-factory.md` (de 4 facet-docs bestaan als bron).
- `f88e50e5`, `04f7c427`, `d404a11f` — elk 1 dangling dep geleegd; deps zijn gemerged
  werk, dus schone reparatie.

Na deze reparatie zijn de kaarten weer normaal dispatchbaar. Of de features zélf nog
nodig zijn (i.p.v. de deps) valt buiten deze dep-analyse; dat beoordeelt de executor bij
oppik.

## 4. Structurele fix — waarom een reparatie alleen niet volstaat

De reparatie in §3 dweilt; de kraan (§1.2 dep-blinde delete + §1.3 onzichtbaarheid) staat
nog open. Zonder de guard herhaalt de volgende "Clear Done" precies dezelfde schade.
Vervolgkaarten (kinderen van `ea7a8e5a`, zie de aangemaakte Backlog-kaarten):

1. **[bug] Dep-bewuste guard op card-delete + Clear-Done** *(backend, root cause)* — bij
   het verwijderen/legen van een kaart die in de `depends_on` van een andere niet-Done
   kaart staat: strip de dep uit die afhankelijke(n) en post een comment (of weiger,
   ontwerpkeuze), zodat een vervulde dep nooit stil een permanente fail-closed blokkade
   wordt. Hergebruik `_blocking_card_ids` (`service.py:126`) als detectie-seam.
2. **[chore] `sweep_dangling_depends_on`-vangnet** *(scripts)* — advisory sweeper die elke
   niet-Done kaart met een `depends_on` naar een niet-bestaande id flag't, gemodelleerd
   op `scripts/sweep_dangling_plan_refs.py` (zelfde klasse probleem, bestaand precedent).
   Vangt cross-project- en handmatig-edit-gevallen die de guard uit kaart 1 mist.
3. **[feature] Bord: verwezen (dangling) dep onderscheiden van een levende** *(frontend,
   zichtbaarheid)* — laat `ReadyStateBadge`/`KanbanPage.tsx` een dep naar een
   ontbrekende/verwijderde kaart anders tonen (bv. rood "Blocked: missing dep") dan een
   gezonde "wacht op levende zuster". Complementair aan het 5-toestanden-vocabulaire uit
   `f03baadb` (dat `blocked`→`dependent` hernoemt maar het missing-geval niet apart
   behandelt); geen `depends_on` op die kaart — geen echt output-contract, alleen
   inhoudelijke overlap.

Bewust **geen** onderlinge `depends_on` tussen deze 3 kaarten: het zijn onafhankelijke
brokken. (Een valse dep leggen zou precies de anti-pattern zijn die dit doc documenteert.)

## 5. Bronnen

- `backend/app/kanban/dep_resolver.py` — `meets_dep_prerequisites` (fail-closed).
- `backend/app/api/v1/kanban/router.py:1392` — `clear_column` (dep-blinde hard delete).
- `backend/app/kanban/service.py:126` — `_blocking_card_ids` (bestaande detectie-seam).
- `frontend/src/features/kanban/KanbanPage.tsx:217-239` + `ReadyStateBadge.tsx` — badge-afleiding.
- `scripts/sweep_dangling_plan_refs.py` — precedent voor een dangling-ref sweeper.
- `docs/cockpit/analyse-levenscyclus-decision.md` §5 + kaart `f03baadb` — statusvocabulaire.
