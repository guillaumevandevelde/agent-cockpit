---
title: "Routing van intake-kaarten — analyse & ontwerpbesluit"
type: analysis
status: superseded
---

# Routing van intake-kaarten — analyse & ontwerpbesluit

> ⚠️ **Achterhaald door
> [`kaartloze-app-inceptie-decision.md`](./kaartloze-app-inceptie-decision.md)
> (2026-07-29).** De hele vraag van deze analyse — hoe voorkom je dat een
> intake-kaart door de auto-dispatcher wordt opgepakt — is vervallen: de
> `intake`-kolom bestaat niet meer (kaart `d0531c12…`), net zomin als de
> Promote-knop en `create_project_from_intake`. Een nieuw app-idee loopt nu
> via de kaartloze `new-app`-skill en raakt het meta-bord nooit. De conclusie
> die dit doc bereikte — **géén** `work_type="intake"` in `WORK_TYPES` —
> blijft geldig en is nooit gebouwd. Bewaard als achtergrond bij die keuze.

> Kanban-kaart: **`[work-type][inceptie] Routing van intake-kaarten — niet door
> auto-dispatch oppakken`** (`071172d76feb422f8c39a47d5f9d80dc`). Leaf-spike:
> deze doc *is* de deliverable. Bouwt voort op
> [`work-type-routing-analysis.md`](./work-type-routing-analysis.md) (het
> `work_type`-routing-besluit dat de kaart als voorwaarde noemt) en
> [`product-inceptie-pipeline.md`](./product-inceptie-pipeline.md) §4 (optie 2:
> de `intake`-kolom).
>
> Alles in §1 is **geverifieerd in de code** op branch `k-work-type-inc-49aa`,
> niet uit het geheugen.

## De vraag (van de kaart)

Introduceer een nieuwe `work_type="intake"` (of gelijkwaardig) zodat:

1. een intake-kaart **niet** door de auto-dispatcher wordt opgepakt (brainstorming
   heeft human-gates);
2. een intake-kaart expliciet **mens-werk** is (de gebruiker vult 'm in en klikt
   zelf *Approve & expand*);
3. binnen intake onderscheid mogelijk is tussen sub-varianten (brainstorm,
   klant-discovery, legacy-import) voor toekomstige persona-routing.

## 1. Huidige stand van zaken (geverifieerd)

### 1a. De `intake`-kolom bestaat al — en wordt al nooit gedispatched

`product-inceptie-pipeline.md` §4 koos **optie 2**: een aparte kanban-kolom
`intake`. Die is geïmplementeerd (kaart `c33b2f14`):

- `COLUMNS = ["intake", "Backlog", "Impediment", "Done", "To Resume"]`
  (`backend/app/kanban/schemas.py:13`) — `intake` is een **vaste** kolom.
- `ensure_intake_column()` (`service.py:558`) back-fillt de kolom idempotent op
  het meta-project; `create_project_from_intake()` (`mcp_server.py:644`) promoot
  een intake-kaart naar een nieuw project en eist expliciet dat de kaart in de
  `intake`-kolom staat (`mcp_server.py:661,675`).

De auto-dispatcher pakt een intake-**kolom**-kaart vandaag al **niet** op — via
*beide* scan-paden in `dispatch.py`:

1. **Nieuwe/hervatte kaarten:** `_next_card()` scant alleen
   `_DISPATCH_COLUMNS = ("Backlog", "To Resume")` (`dispatch.py:1617,1675`).
   `intake` zit daar niet bij.
2. **Wees-kaarten (orphans):** de fallback in `_next_card()` en de bulk-paden
   eisen `c.column not in COLUMNS` (`dispatch.py:1693-1696`). `intake` *zit* in
   `COLUMNS` en valt dus buiten de wees-scan.

**Conclusie:** doel 1 (niet auto-dispatchen) is voor een kaart die fysiek in de
`intake`-kolom staat **al volledig afgedekt** — er is geen `work_type`-regel voor
nodig. Doel 2 (mens-werk + *Approve & expand*) is eveneens geïmplementeerd:
`CardItem` toont de Promote-knop alleen als `column === "intake"`
(`CardItem.tsx:151,298`), en `create_project_from_intake` is de expand-actie.

### 1b. `work_type` is een persona-routing-dimensie met een 1-op-1-invariant

Het `work_type`-besluit uit `work-type-routing-analysis.md` §2A is
geïmplementeerd, maar **strikt als routing-label**: elke `work_type` mapt op
precies één **persona**.

- `WORK_TYPES = ["analysis", "feature", "bug", "chore"]` (`schemas.py:27`).
- `WORK_TYPE_PERSONA_DEFAULTS` mapt elk daarvan op een persona (`analysis→analyst`,
  de rest `→engineer`, `schemas.py:36-39`).
- Per-project overrides leven in de tabel `kanban_work_type_mappings`
  (`models.py:177`), beheerd via `WorkTypeMappingDialog.tsx`.
- `work_type_mapping_for_project()` (`service.py:687`) garandeert dat het
  resultaat **elke** entry in `WORK_TYPES` bevat — callers lezen direct zonder
  `.get()`-guard, en droppen defensief elke rij waarvan de `work_type` niet meer
  in `WORK_TYPES` zit (`service.py:700-702`).
- Bij dispatch bepaalt `get_work_type_persona()` /
  `_resolve_work_type_fallback()` (`service.py:707`, `dispatch.py:494`) de
  persona als `card.agent` leeg is.

**De invariant:** *elke waarde in `WORK_TYPES` is een geldige persona-router die
naar een bestaand `.claude/agents/<persona>.md`-bestand leidt.* De frontend
`WORK_TYPE_PERSONA_DEFAULTS` (`types.ts:80`) en de mapping-dialog gaan er hard van
uit dat de gebruiker voor elke `work_type` een persona kan kiezen.

### 1c. De sub-varianten zijn een orthogonale dimensie

`brainstorm` / `klant-discovery` / `legacy-import` beschrijven **wat voor soort
intake** een kaart is — niet *welke persona* 'm draait (een intake-kaart draait
per definitie *geen* persona; het is mens-werk). Ze staan dus loodrecht op de
`work_type→persona`-as.

## 2. Het probleem met de letterlijke lezing (`work_type="intake"`)

`intake` als vijfde `WORK_TYPES`-waarde toevoegen botst frontaal met de invariant
uit §1b:

- **Backend:** `work_type_mapping_for_project()` zou `intake` in de map opnemen en
  een persona-lookup forceren voor iets dat geen persona *heeft*.
  `get_work_type_persona("intake")` zou naar de `engineer`-fallback vallen — dus
  een intake-kaart in Backlog zou alsnog naar engineer routeren, precies het
  tegenovergestelde van doel 1.
- **Frontend:** `WORK_TYPE_PERSONA_DEFAULTS` is `Record<WorkType, string>` — het
  toevoegen van `intake` dwingt een persona-waarde af, en de `WorkTypeMappingDialog`
  zou een zinloze persona-dropdown voor "intake" tonen.
- **Semantiek:** de dispatch-skip zou een *special-case* in de persona-resolver
  worden ("behalve intake"), terwijl de `intake`-**kolom** die skip al schoon en
  zonder uitzondering levert.

Kortom: `work_type="intake"` propt twee niet-verwante concepten (persona-routing
én "niet-dispatchen") in één enum en breekt daarmee een expliciet bewaakte
invariant. De kaart laat via *"(of gelijkwaardig)"* ruimte voor een beter model —
dit besluit maakt daar gebruik van.

## 3. Ontwerpopties

### Optie A — `intake` als vijfde `work_type` (letterlijke lezing) ❌
Breekt de §1b-invariant (zie §2). Vereist special-casing in de persona-resolver,
de mapping-dialog én de per-project mapping-tabel. **Afgeraden.**

### Optie B — Kolom = source of truth voor "niet-dispatchen"; sub-type apart ✅ (aanbevolen)
De `intake`-**kolom** blijft de enige bron van waarheid voor "dit is mens-werk,
niet dispatchen" — dat werkt vandaag al (§1a). De sub-varianten worden een
**nieuw, apart, optioneel veld** `intake_kind`
(`brainstorm | customer-discovery | legacy-import`), alleen betekenisvol op een
intake-kolom-kaart. `work_type` blijft ongemoeid (puur persona-routing).

- **Voor:** respecteert de §1b-invariant volledig; geen special-case in dispatch;
  sub-varianten krijgen een schone eigen dimensie die toekomstige persona-routing
  kan lezen (doel 3); minimale blast-radius.
- **Tegen:** wijkt af van de letterlijke kaart-tekst (`work_type="intake"`) —
  gedekt door *"(of gelijkwaardig)"*.

### Optie C — Optie B + belt-and-suspenders dispatch-guard
Als B, plus een goedkope defense-in-depth in de dispatcher: sla een kaart met
`intake_kind != NULL` (of, equivalent, afkomstig uit de intake-kolom) óók over als
'ie ooit *misplaatst* in Backlog belandt. Vandaag onmogelijk via de normale flow
(intake-kaarten worden in de intake-kolom aangemaakt), dus dit is puur vangnet.

## 4. Besluit & aanbeveling

**Kies optie B, met de guard uit optie C als losse, lager-geprioriteerde
follow-up.**

Concreet betekent dat voor de oorspronkelijke acceptatiecriteria van de kaart:

| Kaart-AC | Uitkomst na dit besluit |
|---|---|
| `work_type="intake"` erkend in enum + dispatcher-skip | **Herzien:** géén `intake` in `WORK_TYPES`. Dispatch-skip loopt via de `intake`-**kolom** (al werkend, §1a). "Of gelijkwaardig" gehonoreerd. |
| Skip-logica gedocumenteerd + getest | **Ja** — via een regressietest die borgt dat `_next_card()` een kaart in de `intake`-kolom nooit selecteert, óók met autodispatch aan. |
| Frontend kent intake + ander icoon + ander invul-veld | **Ja** — gekoppeld aan `column === "intake"` (bestaand `isIntake`-patroon), plus de nieuwe `intake_kind`-selector. |
| Consistent met `work-type-routing-analysis.md`-besluit | **Ja, juist door B** — `work_type` blijft zuiver persona-routing, exact zoals dat besluit het definieerde. |

### 4.1 Implementatiespec voor de follow-up executor-kaart(en)

Onderstaande is scoped tot **acceptatiecriteria-niveau**; het *hoe* is aan de
executor. Aanbevolen als één kaart (klein, samenhangend) of desgewenst
gesplitst in backend / frontend.

**Backend (`work_type` ongemoeid laten):**
- Nieuw optioneel veld `intake_kind: str | None` op `KanbanCard`
  (`models.py`), `CardCreate`/`CardUpdate`/`CardResponse` (`schemas.py`), met een
  enum-constante `INTAKE_KINDS = ["brainstorm", "customer-discovery",
  "legacy-import"]`. Nullable (geen migratiesysteem — nieuwe nullable kolom is
  schema-compatibel; zie CLAUDE.md "No database migration system").
- Validatie: `intake_kind` mag alleen gezet worden op een kaart in de
  `intake`-kolom (of leeg zijn). Bij een niet-intake-kaart → 422 of stil negeren
  (executor kiest; documenteer de keuze).
- **Regressietest** (`backend/tests/`, pytest-asyncio): een kaart in de
  `intake`-kolom wordt door `_next_card()` **niet** geselecteerd, ook als
  autodispatch voor het project aan staat en de kaart onclaimed + due is. Dekt AC
  "dispatcher-skip getest". (Test-intent: borg het gedrag zodat een latere
  refactor van `_DISPATCH_COLUMNS`/`COLUMNS` de skip niet stilletjes breekt.)

**Frontend:**
- `CardEditDialog`: toon een `intake_kind`-`Select` (brainstorm /
  customer-discovery / legacy-import) **alleen** wanneer de kaart in de
  `intake`-kolom staat; verberg/negeer 'm anders. Gebruik het bestaande
  `Select`-patroon (`CardEditDialog.tsx:243-258`) en `MODAL_SIZES` conventies.
- `CardItem`: geef intake-kaarten een eigen icoon (bestaand `isIntake`-pad,
  `CardItem.tsx:151`). `WORK_TYPE_ICONS` blijft ongemoeid (geen vijfde entry).
- Types: `INTAKE_KINDS` + `IntakeKind` in `types.ts` naast (niet in) `WORK_TYPES`.

**Optionele follow-up (lagere prioriteit) — defense-in-depth guard:**
- Laat de dispatcher óók een kaart overslaan met `intake_kind != NULL` als die
  buiten de intake-kolom belandt (optie C). Alleen relevant als er ooit een pad
  ontstaat dat intake-kaarten in Backlog aanmaakt.

### 4.2 Wat expliciet **niet** verandert
- `WORK_TYPES` / `WORK_TYPE_PERSONA_DEFAULTS` / `WorkTypeMappingDialog` blijven
  vierwaardig en persona-gericht.
- De dispatch-skip-code hoeft **niet** gewijzigd (de kolom levert 'm al) — behalve
  de optionele guard uit 4.1.
- De *inhoud* van een intake-kaart (wat de gebruiker invult) blijft out-of-scope
  (kaart-AC "Out of scope"; dat is `product-inceptie-pipeline.md` §4).

## 5. Open vraag voor de mens

Eén beslissing valt buiten wat de code kan dicteren en hoort bij de
product-eigenaar: **is `intake_kind` (§4.1) nu al nodig, of is de `intake`-kolom
alléén voldoende tot er een concrete persona-routing-behoefte per sub-variant
is?** De sub-varianten hebben pas *effect* zodra iets ze leest (doel 3 is
expliciet "zodat toekomstige persona-routing er wat mee kan" — vandaag leest niets
ze). Als YAGNI zwaarder weegt dan vooruit-modelleren, kan de `intake_kind`-follow-up
worden uitgesteld en levert dit besluit alleen de bevestiging + regressietest op
dat de intake-kolom de dispatch-skip al correct afhandelt.
