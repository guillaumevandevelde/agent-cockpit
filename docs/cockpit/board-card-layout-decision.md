---
title: "Beslissing — Bordkaart-layout: titel leest volledig, metadata scant op één regel, lege lanes worden rails"
type: decision
status: decided
---

# Beslissing — Bordkaart-layout: titel leest volledig, metadata scant op één regel, lege lanes worden rails

**Datum:** 2026-07-28
**Status:** besloten
**Kaart:** `1fafd87c19e54ef1aa48936e8759ce06`
**Uitkomst:** **De bordkaart wordt een leesoppervlak voor zijn titel en een scanoppervlak voor de rest.** De titel krijgt de volle kaartbreedte en vijf regels (was twee, náást de statusbadges); de metadata zakt naar één regel die afkapt met een fade in plaats van naar drie regels te wrappen; lege kolommen klappen zichzelf in tot een 40px-rail en elke kolom is handmatig in/uit te klappen (bewaard in `localStorage`). Een tabel-/lijstweergave van het bord is afgewezen, en de kaart-modal is bewust buiten scope gebleven — die loopt via `kaart-modal-leesfirst-decision.md`.

---

## 1. De klacht, en waarom de vorige ronde 'm niet oploste

De kaart zegt: *"Layout van cards nog steeds suboptimaal — kan niet alles lezen
en nog veel te vaak scrollen."* Het woord *"nog steeds"* verwijst naar
`b4985b42…` ("Kaarten nog altijd niet overzichtelijk"), die in commit
`6bce632` drie dingen deed: de kaarttitel kreeg `line-clamp-2`, lanes gingen van
`min-w-56` naar `min-w-64`, en de modal kreeg `MODAL_SIZES.XL` met één
scroll-container.

Twee van die drie maakten de gemelde klacht **erger**:

- `line-clamp-2` is precies "kan niet alles lezen" — het kapt de titel af op
  twee regels.
- `min-w-64` (256px) per lane maakte het bord bréder, en dus horizontaal
  schuiven waarschijnlijker.

Dat is geen kritiek op die kaart — de modal-helft ervan was winst — maar het
verklaart waarom dezelfde mens twee keer dezelfde klacht schreef.

## 2. Meting vóór (dit is wat de klacht in cijfers is)

Gemeten op het echte bord (`http://localhost:5173/kanban`, 74 kaarten, 7
kolommen, 2026-07-28), read-only via Playwright — reproductie-commando in §6.

| Meting | 1280×800 | 1440×900 | 1920×1080 |
|---|---|---|---|
| Horizontaal te schuiven | **888px** | **728px** | **248px** |
| Lanebreedte (alle 7) | 256px | 256px | 256px |
| Afgekapte titels | **38 / 49** | 38 / 49 | 38 / 49 |
| Mediane kaarthoogte | 144px | 144px | 144px |
| Zichtbare Backlog-kaarten | 3 / 16 | 4 / 16 | 5 / 16 |

Drie onafhankelijke oorzaken achter die cijfers:

1. **De titel had de ruimte niet.** Titellengte op dit bord: mediaan 96 tekens,
   p90 130, max 180; 62 van de 74 titels zijn langer dan 60 tekens. In een lane
   van 256px passen ~70 tekens in twee regels — de *mediane* kaart werd dus
   afgekapt. Bovendien stond naast de titel een `shrink-0`-badgecluster
   (`Done` / `To Resume` / impediment-status), die ~110px van de titelbreedte
   afsnoepte op precies de kolommen met de meeste kaarten.
2. **De metadata was hoger dan de titel.** De metaregel was `flex-wrap`: bij
   drie vrije labels ("tokens", "providers", "prompt-injectie") wrapte hij naar
   drie regels. Een kaart van 144px besteedde daarmee ~70px aan chips en ~40px
   aan de titel waarvoor iemand de kaart opzoekt.
3. **Lege lanes hielden hun volle breedte.** 7 lanes × 256px + gaps = 1864px.
   Twee ervan (`reviewer` 0 kaarten, `analyst` 1) hielden elk 256px vast terwijl
   Backlog (16) en Impediment (11) verticaal overliepen.

## 3. Gekozen richting

### 3a. De titel is leescontent — die krijgt de ruimte

De statusbadges verhuizen van naast de titel naar de metaregel, waardoor de
titel de volle kaartbreedte krijgt (`CardItem.tsx`, titel-`div` met
`data-testid="card-title"`). De clamp gaat van 2 naar **5** regels, plus
`break-words` (een backticked flag of branchnaam mag de lane niet oprekken) en
een `title`-tooltip met de volledige titel voor wat de clamp alsnog afkapt.

Vijf, niet drie en niet zes: de clamp is een **plafond, geen gereserveerde
hoogte**, dus een korte titel kost nog steeds één regel. Gemeten op het echte
bord op 1440×900 (dezelfde kaarten, alleen de clamp gewijzigd):

| clamp | afgekapte titels | mediane kaarthoogte | zichtbare Backlog-kaarten |
|---|---|---|---|
| 3 | 27 / 40 | 98px | 5 |
| 4 | 12 / 40 | 115px | 4 |
| **5** | **6 / 40** | **115px** | **4** |
| 6 | 3 / 40 | 115px | 3 |

Vijf regels halveren de afkapping van vier regels **zonder één pixel extra
mediane hoogte**; zes kost een zichtbare kaart voor drie titels. Daar knikt de
curve, dus daar ligt de keuze.

### 3b. De metadata is scancontent — die blijft op één regel

De metaregel is `flex` zonder `flex-wrap`, met `overflow-hidden` en een
mask-fade op de rechterrand. Chips staan in volgorde van scanwaarde:
status → ready-state → **deliverables** → subtasks → work_type → priority →
labels → multi-agent → agent → provider → schedule → claim. Deliverables staan
hoog omdat "wat is er uitgekomen" op een Done-kaart het eerste is dat een
operator na de titel zoekt — dat mag nooit de chip zijn die wegvalt.

Drie bijbehorende keuzes:

- **Labels: maximaal twee, de rest wordt `+N`** met de weggelaten labels in de
  tooltip. Labels waren de grootste hoogtedrijver en de minst voorspelbare
  (0–4 per kaart).
- **Twee dubbele chips verdwijnen.** `Completed` naast ✅ Done is dezelfde
  informatie twee keer — `readyState === "completed"` wordt gezet dán en alleen
  dán als de kolom `Done` is (`frontend/src/features/kanban/KanbanPage.tsx:243-245`),
  dus de chip voegt niets toe. Idem `Impeded` naast een specifieke
  `needs answer` / `dispatch failed`-chip; ontbreekt die specifieke status (oudere
  kaart zonder op-log-status), dan blijft de generieke chip staan.
- **Quick-actions (Redispatch, Promote) staan in hun eigen regel**, niet in de
  afkappende regel: een half afgekapte knop is een knop die je niet kunt
  klikken. Die regel rendert alleen voor de twee toestanden die er één hebben.

De fade is geen decoratie: een hard afgekapte chip ("hi" in plaats van "high")
leest als een renderbug, een uitgefadede chip leest als "hier staat meer".

### 3c. Lanes: lege lanes worden rails, elke lane is in te klappen

- Een lane zonder kaarten start als **40px-rail** met naam (verticaal) en
  aantal, en blijft een drop-target — een kaart erop slepen werkt zonder eerst
  uit te klappen.
- Elke lane heeft een chevron in zijn header om handmatig in te klappen; de rail
  klapt met één klik weer uit.
- Een expliciete keuze (welke richting ook) overschrijft de
  lege-lane-default en wordt bewaard in `localStorage`
  (`kanban-collapsed-columns`, per kolom-id). Corrupte of geblokkeerde storage
  valt terug op de default in plaats van het bord mee te nemen.
- Uitgeklapte lanes verdelen de beschikbare breedte gelijk (`flex-1`, dus
  `flex: 1 1 0%`) met een **vloer van 208px** (`min-w-52`). Een lane is dus zo
  breed als er ruimte voor is — 293px bij vijf lanes op 1920×1080, 208px zodra ze
  anders dunner geknepen zouden worden — en pas ónder die vloer schuift het bord
  horizontaal. De oude vaste `min-w-64` (256px) legde die vloer boven wat een
  laptopviewport kan dragen; dat is precies waarom het bord op elke realistische
  breedte horizontaal schoof.

## 4. Meting ná

Zelfde bord, zelfde meetmethode (§6), met de nieuwe layout:

| Meting | vóór (1440×900) | ná (1440×900) | ná (1920×1080) |
|---|---|---|---|
| Horizontaal te schuiven | 728px | **56px** | **0px** |
| Afgekapte titels | 38 / 49 (78%) | **8 / 50 (16%)** | 1 / 50 (2%) |
| Mediane kaarthoogte | 144px | **115px** | 107px |
| Zichtbare Backlog-kaarten | 4 / 16 | 4 / 17 | 5 / 17 |

De lanestaat hoort bij die cijfers, want ze bepalen de breedte-rekening. Bij de
ná-meting op 1440×900 waren `engineer` en `reviewer` beide leeg en dus rails:
5 uitgeklapte lanes × 208px + 2 rails × 40px + 6 gaps × 12px = 1192px tegen
1136px viewport = de 56px die de tabel noemt. (De vóór-meting in §2 dateert van
een uur eerder, toen `engineer` nog 3 kaarten had; kaarten bewegen tussen de
metingen, lanebreedtes niet.)

Op een deterministische fixture met dezelfde bordvorm (7 lanes waarvan één leeg,
49 kaarten, titels op de lange kant) meet dezelfde layout 0/49 afgekapte titels,
mediaan 115px en **0px** horizontaal schuiven op 1440×900: 6 uitgeklapte lanes ×
216px + 1 rail × 40px + 72px gaps = 1408px, precies de containerbreedte.

**Eerlijk over de trade-off:** verticaal scrollen in Backlog verbetert op
1440×900 niet (4 kaarten zichtbaar, vóór en ná) — de mediane kaart werd 20%
korter, maar de titels die nu volledig renderen eten dat deels op. De winst zit
in *wat* die schermvulling nu is: leesbare titels in plaats van chips. Wie op
1440 méér kaarten tegelijk wil zien, klapt lanes in — 3 lanes op 1440 geeft
~350px per lane, en dan zakt de mediane kaart naar twee titelregels. Dat is een
knop die er eerst niet was; er is geen layout die op 1440×900 tegelijk 5 lanes,
volledige titels én korte kaarten geeft.

## 5. Afgewezen alternatieven

- **Tabel-/lijstweergave van het bord** (één regel per kaart, gegroepeerd per
  kolom). Lost lange titels het beste op — een titel van 180 tekens past
  probleemloos op één regel over de volle breedte — maar levert de
  kanban-affordance in (slepen tussen kolommen, kolom-WIP in één blik) en is een
  veel grotere verbouwing dan de klacht rechtvaardigt. Blijft een optie als de
  klacht na deze ronde terugkomt.
- **Titel volledig zonder clamp.** Verwijdert alle afkapping, maar één
  pathologische titel (180+ tekens, en niets houdt een langere tegen) kan dan
  een halve kolom claimen. De clamp op 5 is een plafond dat in de praktijk
  vrijwel niemand raakt.
- **Metadata helemaal weglaten van de bordkaart.** Zou de kortste kaart geven,
  maar het bord is juist de plek waar een operator ready-state, impediment-oorzaak
  en subtask-voortgang scant zonder de modal te openen. Chips comprimeren was
  goedkoper dan ze weghalen.
- **Kleinere titelfont (13px) om meer tekens per regel te krijgen.** Verhoogt de
  informatiedichtheid ten koste van precies de leesbaarheid die de klacht
  aanwijst.
- **`.gitattributes`-achtige "lanes altijd 256px"-vasthouden.** Zie §2, punt 3:
  vaste bredere lanes zijn de directe oorzaak van het horizontale schuiven.

## 6. Reproductie

De metingen komen uit Playwright tegen een draaiend bord; ze zijn read-only
(alleen `page.evaluate` + `getBoundingClientRect`, geen klik of drag die de
bordstaat wijzigt). Kern van het script:

```js
// per viewport: goto /kanban, dan
const board = document.querySelector('[class*="overflow-x-auto"]');
const hScroll = board.scrollWidth - board.clientWidth;                // horizontaal schuiven
const titles = [...document.querySelectorAll('[data-testid="card-title"]')];
const clipped = titles.filter(t => t.scrollHeight > t.clientHeight + 1).length; // afgekapt
const hs = [...document.querySelectorAll('[data-card-id]')]
  .map(c => Math.round(c.getBoundingClientRect().height)).sort((a, b) => a - b);
const median = hs[Math.floor(hs.length / 2)];                          // kaarthoogte
```

Titellengte-statistiek komt uit
`GET /api/v1/kanban/cards?project_key=<key>` (`.items[].title`). Voor het
verifiëren van een layoutwijziging zonder de gedeelde dev-stack te claimen:
[`isolated-component-preview.md`](./isolated-component-preview.md) — een scratch
Vite-entry op een vrije poort mount `Board` met een fixture die de bordvorm
nabootst, plus een Playwright-screenshot in licht en donker.

## 7. Wat hier bewust buiten valt

- **De kaart-modal.** Die heeft zijn eigen beslissing
  ([`kaart-modal-leesfirst-decision.md`](./kaart-modal-leesfirst-decision.md))
  en een lopende uitvoeringskaart (`c81fb67d…`, lees-first herindeling in drie
  lagen). Deze kaart raakt `CardDrawer.tsx` niet, zodat de twee elkaar niet in de
  weg lopen.
- **Kolommen die het bord niet rendert.** Het bord tekent alleen de kolommen die
  in `kanban_columns` staan; kaarten in een kolom zonder rij (op dit bord:
  `To Resume` en `intake`, samen 25+ kaarten) verschijnen nergens. Dat is een
  echte "kan niet alles lezen"-oorzaak, maar een data-/kolomconfiguratieprobleem
  in plaats van een layoutprobleem — apart gefiled.
  ✅ Geïmplementeerd (kaart `4f0677c7…`): `GET /api/v1/kanban/columns` vult de
  vaste `COLUMNS`-rijen van een enabled bord idempotent aan
  (`service.ensure_fixed_columns`), en `Board.tsx` tekent elke overige kolom
  waar kaarten op staan als expliciet gemarkeerde "unconfigured"-lane. Het
  lane-breedtebudget uit §3c draagt dit: een lege lane is een 40px-rail, dus
  `intake` + `To Resume` erbij kost 80px, geen twee volle lanes.
