---
title: "Beslissing — Kaart-modal wordt lees-first (richting A), geen aparte kaart-pagina"
type: decision
status: decided
---

# Beslissing — Kaart-modal wordt lees-first (richting A), geen aparte kaart-pagina

**Datum:** 2026-07-25
**Status:** besloten
**Kaart:** `624f77182396487d92d17f461b5bb1c5`
**Uitkomst:** **Richting A — lees-first modal.** De kaart-modal krijgt één scroll-container en drie zichtbaarheids-lagen (actie-vereist → lezen → operator/telemetrie ingeklapt); richting B (`/kanban/card/:id`) wordt **niet** gebouwd.

Voorafgaande analyse: [`communicatie-en-weergave-analyse.md` §3.2–3.3](./communicatie-en-weergave-analyse.md).

---

## 1. De vraag

De gebruiker meldde "heel wat schuifbalken" bij het openen van een kaart. §3.2 van de
bron-analyse stelde twee deeloorzaken vast (geneste scroll; te veel op één oppervlak) en
liet bewust de *oplossing* open als een echte ontwerp-fork:

| Richting | Kern | Geclaimde trade-off |
|---|---|---|
| **A. Lees-first modal** | Modal toont standaard alleen lees-content; operator/telemetrie achter één afgescheiden gebied; één scroll-container. | Minste bouwwerk. |
| **B. Volledige kaart-pagina** | Diepe content naar `/kanban/card/:id`; modal wordt quick-look. | Meer werk, schaalt beter. |

## 2. Uitkomst — A, en B is niet "later A+"

**Gekozen: A.** Niet omdat B duurder is, maar omdat **B de gemelde klacht niet oplost**.

De klacht is *geneste schuifbalken*. Die ontstaan doordat de modal-container scrollt én
binnen-content een eigen hoogte-cap + `overflow` declareert (§3, hieronder). Een
kaart-*pagina* erft exact dieselfde binnen-content: een `h-[60vh]` xterm-container
(`frontend/src/features/kanban/components/CardRunTab.tsx:110`), een `max-h-[60vh]`
transcript-lijst (`CardRunTab.tsx:192`), een `max-h-72` prompt-`<pre>`
(`frontend/src/features/kanban/components/CardLedgerTab.tsx:127`). Zet je die op een
pagina die zelf scrollt, dan heb je nog steeds twee schuifbalken — alleen zonder modal
eromheen. **Deeloorzaak (1) is een container-contract-probleem, geen
oppervlakte-probleem.** B adresseert alleen deeloorzaak (2).

Daarmee valt de trade-off uit de bron-analyse anders uit dan daar gesuggereerd: A is niet
"de goedkope helft van B", A is **de enige van de twee die het gemelde symptoom raakt**.

**Kosten-asymmetrie, ter bevestiging (niet als hoofdargument).** A is een herindeling
binnen één component. B vraagt bovendien: een nieuwe route naast de bestaande
`?card=<id>`-deeplink-reconciliatie (`frontend/src/features/kanban/KanbanPage.tsx:120-164`,
inclusief de cross-project-`getCard`-fallback), een tweede te onderhouden weergave van
dezelfde kaart, en een fork van de bestaande drawer-testsuite (1675 regels,
`frontend/src/features/kanban/components/CardDrawer.test.tsx`) die vandaag alles
drawer-gerenderd asserteert.

**B blijft goedkoop mogelijk en wordt níet als kaart gefiled.** De diepe tabs zijn al
zelfstandige componenten (`CardRunTab`, `CardTokensTab`, `CardLedgerTab`,
`PlanTabContent`); een latere pagina mount dezelfde componenten. A sluit B dus niet af.
Er is vandaag geen aanleiding voor B, dus er komt geen speculatieve kaart — deze §
is het record dat het overwogen en uitgesteld is.

**Hybride afgewezen.** "Modal voor lezen + pagina voor diep" is B met een extra
synchronisatie-oppervlak: dezelfde twee weergaven, plus de vraag welke van de twee de
`?card=`-deeplink opent. De winst zit in laag 3 van §4, en die is binnen de modal te
halen.

## 3. Enumeratie — waar de schuifbalken vandaan komen

Geverifieerd tegen de huidige code (branch `k-kaart-modal-w-0e55`, `origin/master`-basis).

### 3.1 De buitenste container

`CardDrawer.tsx:1217` rendert `<DialogContent className={MODAL_SIZES.LG}>` =
`max-w-4xl max-h-[90vh] overflow-y-auto` (`frontend/src/lib/constants.ts:8`). De
basis-`DialogContent` is `grid ... gap-4` (`frontend/src/components/ui/dialog.tsx:39`), dus
**elke sectie is een grid-rij binnen diezelfde scroll-container** — inclusief de
`DialogHeader`. De kaart-titel scrollt weg zodra je naar de tabs beweegt.

### 3.2 Geneste scroll-containers binnen die container

| # | Locatie | Declaratie | Wanneer zichtbaar |
|---|---|---|---|
| 1 | `CardRunTab.tsx:110` | `h-[60vh] … overflow-hidden` rond `<TerminalView>`; xterm rendert zijn **eigen** viewport-scrollbar erbinnen | Run-tab, live-modus |
| 2 | `CardRunTab.tsx:192` | `max-h-[60vh] overflow-y-auto pr-1` (transcript-lijst) | Run-tab, transcript-modus |
| 3 | `CardLedgerTab.tsx:127` | `<pre className="max-h-72 overflow-auto …">` (dispatch-prompt) | Ledger-tab, `<details>` open |
| 4 | `MarkdownPreviewToggle.tsx:39` | `overflow-auto` + `minHeight` op de preview-pane; gebruikt door `EditablePlan` (`CardDrawer.tsx:774`) met `defaultTab="preview"` | Plan-tab op een parent-kaart |
| 5 | `MarkdownPreviewToggle.tsx:31` | `<textarea … resize-y>` — eigen native scrollbar | Plan-tab, Edit-subtab |
| 6 | `PreviewPane.tsx:112` | `<iframe className="h-[50vh] …">` — de ingebedde app scrollt zichzelf | Done-kaart, ná "Run this branch" |
| 7 | `MarkdownRenderer.tsx:59-64` | fenced code-blocks gaan door `SyntaxHighlighter`, die zijn eigen `overflow: auto` inline zet → **horizontale** scrollbar per code-block | Beschrijving (`CardDrawer.tsx:1320`), Done-banner (`:348`), plan/spec-deliverable (`:273`, `:288`), gate-vraag (`:1259`) |

Niet-scrollend maar wel relevant voor de "wildgroei"-indruk: `CardTokensTab.tsx:144` en
`CardLedgerTab.tsx:156` wikkelen een `w-full`-tabel in `overflow-hidden` — die klippen,
ze scrollen niet.

**Twee zijn legitiem, vijf niet.** #1 (xterm) en #6 (iframe) zijn echt
viewport-gebonden widgets: een terminal-emulator en een vreemde app kunnen niet
"meegroeien" met een pagina. #2, #3, #4, #5 zijn caps die er zijn omdát de modal-container
al scrollt — ze bestaan om te voorkomen dat één tab de modal oneindig lang maakt, en zijn
daarmee de directe oorzaak van de dubbele schuifbalk.

### 3.3 Te veel op één oppervlak

Op een Done-kaart stapelt `CardDrawer.tsx:1301-1308` vóór de beschrijving vier volle
blokken: `DoneSummaryBanner`, `CardPreviewControl` (dat een 50vh-iframe kán bevatten),
`RequestReviewControl` (textarea) en `ReopenControl` (textarea). De **beschrijving** —
waarvoor de mens de kaart opende — staat dus onder ~twee textareas en potentieel een halve
viewport aan iframe. De deliverables (het resultaat) zitten bovendien achter een tab
(`:1408`), terwijl de 7-tabs-rij (`:1392-1406`) telemetrie en lees-content door elkaar zet.

## 4. Doel-indeling (A)

Eén verticale volgorde, drie lagen zichtbaarheid:

1. **Actie vereist — altijd zichtbaar, bovenaan.** Open gates (`:1249`),
   impediment-control (`:1310`), "choice recorded" (`:1275`). Dit is waarom je de kaart
   opent als er iets van je gevraagd wordt; het mag nooit ingeklapt zijn.
2. **Lezen — standaard zichtbaar.** Done-banner, beschrijving, subtasks, **en de
   deliverables-lijst inline** (uit de tab-rij gepromoveerd: "wat is er uitgekomen" is
   lees-content, geen telemetrie).
3. **Operator & telemetrie — één ingeklapt gebied.** Provider-select + Dispatch/Re-dispatch
   + Edit + Claim/Release + Delete (`:1331-1390`), spec-link (`:1323`), preview-control,
   request-review, reopen, en de resterende tabs (Activity / Plan / Screenshots / Ledger /
   Tokens / Run). **Niets verdwijnt — het verhuist** (acceptatiecriterium 4).

**Scroll-contract.** Precies één scroll-container: de body tussen een vaste
`DialogHeader` en de modalrand. Een kind mag géén eigen hoogte-cap + `overflow`
declareren, tenzij het een viewport-gebonden widget is (#1 en #6 uit §3.2) — dan wordt het
volvlak binnen de body gerenderd en scrollt de body zelf niet.

**Default-open-regel.** Laag 3 start ingeklapt, **behalve** wanneer de kaart door een agent
geclaimd is (`card.claimed_by` begint met `agent:`, `CardDrawer.tsx:1063`). Vandaag springt
de modal voor zo'n kaart automatisch naar de Run-tab (`:1069`); die eigenschap moet
behouden blijven, en een auto-geselecteerde tab in een ingeklapt gebied zou onzichtbaar zijn.

**In-repo precedent voor de container.** `MemoryEditor.tsx:167` +
`:189` doet dit al: `${MODAL_SIZES.LG} h-[80vh] flex flex-col` op de `DialogContent` plus
`<div className="flex-1 min-h-0 … overflow-auto">` als body. Zie ook
`MCPServerDetailDialog.tsx:141` en `SkillDetailDialog.tsx:167`. Geen nieuw patroon
uitvinden.

**`MODAL_SIZES.LG` niet aanpassen** — 12 andere dialogen gebruiken die constante;
`overflow-y-auto` eruit slopen raakt ze allemaal. Override lokaal op de `DialogContent` van
deze ene modal.

**`MarkdownPreviewToggle` niet globaal aanpassen** — 8 consumers
(`MemoryEditor`, `AgentEditor`, `HookEditor`, `CardEditDialog`, `ComposeDialog`,
`ThreadDialog`, `MemberEditDialog`, `MarkdownEditor`). Bron #4/#5 uit §3.2 wordt lokaal
opgelost (opt-in prop of een andere weergave in de Plan-tab), niet door de gedeelde
component te herdefiniëren.

## 5. Vervolgkaarten

| # | Kaart | id | work_type | depends_on |
|---|---|---|---|---|
| 1 | Kaart-modal: één scroll-container (vaste header + één scrollende body) | `72476d8e…` | feature | — |
| 2 | Kaart-modal: lees-first herindeling in drie lagen | `c81fb67d…` | feature | kaart 1 |

De afhankelijkheid is een echt contract, geen volgorde-voorkeur: kaart 2 plaatst zijn
lagen *binnen* de body-container die kaart 1 introduceert, en de "volvlak"-modus voor de
Run-tab uit kaart 2 bestaat alleen als kaart 1's scroll-contract er is.
