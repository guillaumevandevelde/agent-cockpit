# Beslissing — De analyse-levenscyclus op het bord: parkeerkolom, subtaak-rollup, statusvocabulaire

**Datum:** 2026-07-15
**Status:** Beslissing / ontwerp — implementatie belegd op de vervolgkaarten in §7
**Kaart:** "Analyse - koppel vervolgkaarten aan analyse" (`d0089809…`) · **Type:** analyse-leaf-spike

**Trigger (de gebruiker):**

> "Maak een aparte kolom waar de analyse blijft staan tot ze echt compleet is.
> Koppel de vervolgtaken als subtaken aan de analyse, status van deze moet daar ook zichtbaar
> zijn. (Ready, Verandern dispatchin naar in progress, verander blocked naar dependend,
> impedded, completed)
> Kaarten die vandaag naar completed gaan moeten het label completed krijgen."

Verwant en **complementair**: [`analysis-outcome-contract-decision.md`](./analysis-outcome-contract-decision.md)
(kaart `e95729bb…`, dezelfde dag) — dat doc beslist *wat* een analyse moet opleveren om te
mogen afsluiten; dit doc beslist *waar ze staat* en *hoe je dat ziet*. Zie §6 voor de naad.
Eerdere rondes van hetzelfde grondprobleem: [`autonomous-leaf-spike-followup.md`](./autonomous-leaf-spike-followup.md),
[`analyse-orphaned-followups-audit.md`](./analyse-orphaned-followups-audit.md).

---

## 1. Het probleem — "Done" liegt over een analyse

Vandaag verlaat een analyse-kaart de board-flow op het moment dat ze *gedecomponeerd* is,
niet op het moment dat ze *iets heeft opgeleverd*. De analyst-persona schrijft het letterlijk
voor (`analyst_prompt.py`, stap 5):

> "**Parent verplaatsen naar `Done`** met `move_card(parent, "Done", summary="Plan opgesplitst
> in N taken")`. Dat is je exit-signaal."

Het gevolg is een bord dat structureel iets onwaars toont. Op het moment dat de parent groen
kleurt is er **nul** vervolgwerk gedaan — de kind-kaarten staan nog onaangeraakt in `Backlog`.
De kaart die het meest lijkt op "afgerond" is precies de kaart waarvan het werk nog volledig
moet beginnen.

Dat veroorzaakt drie concrete gebreken:

1. **De analyse verdwijnt uit beeld.** `Done` is een firehose (zo benoemd in
   [`decisions.md`](./decisions.md) §-inleiding). Een gedecomponeerde analyse zakt daar binnen
   een dag weg tussen tientallen afgeronde chores. Niemand komt er ooit op terug om te zien of
   de kinderen daadwerkelijk zijn geland.
2. **De koppeling is eenrichtingsverkeer.** `parent_card_id` bestaat op het datamodel
   (`models.py:75`, geïndexeerd) en de kind-kaart draagt een `plan_ref`-deliverable terug naar
   de parent — `CardDrawer.tsx:692–698` leest die en rendert een link *kind → ouder*. De
   omgekeerde weg bestaat niet. **Geen enkele view toont de kinderen van een parent.** De
   gebruiker vraagt exact dit gat te dichten ("status van deze moet daar ook zichtbaar zijn").
3. **Het statusvocabulaire benoemt de interessante toestanden niet.** `ReadyStateBadge.tsx:3`
   kent er drie: `ready | blocked | dispatching`. Er is geen toestand voor "wacht op een mens"
   en geen voor "klaar" — terwijl dat de twee statussen zijn waar een operator naar zoekt.

De kern in één zin: **een analyse is niet klaar als ze gedecomponeerd is, maar het bord doet
alsof van wel.**

## 2. Wat de gebruiker vraagt — ontleed

De opdracht bevat vier eisen. Ze zijn niet allemaal even scherp, dus eerst de ontleding:

| # | Eis | Interpretatie |
|---|---|---|
| A | "aparte kolom waar de analyse blijft staan tot ze echt compleet is" | Een gedecomponeerde parent gaat **niet** naar `Done`, maar parkeert in een nieuwe vaste kolom tot haar kinderen terminaal zijn. |
| B | "koppel de vervolgtaken als subtaken aan de analyse" | De bestaande `parent_card_id`-relatie **zichtbaar maken** op de parent. Geen nieuw datamodel — de data ligt er al. |
| C | "status van deze moet daar ook zichtbaar zijn" | Per kind een statusbadge in die rollup. |
| D | "(Ready, … in progress, … dependend, impedded, completed)" | Het statusvocabulaire wordt vijf waarden, met twee hernoemingen. |
| E | "Kaarten die vandaag naar completed gaan moeten het label completed krijgen" | Zie §4 — de enige echt dubbelzinnige zin. |

Eis A is de motor; B/C/D zijn de zichtbaarheid die A bruikbaar maakt.

## 3. Beslissing — de parkeerkolom `Awaiting Subtasks`

**Een parent-kaart met ≥1 kind-kaart gaat op haar Done-move niet naar `Done`, maar naar de
nieuwe vaste kolom `Awaiting Subtasks`. Ze verlaat die kolom pas — automatisch — wanneer al
haar kinderen in `Done` staan.**

### Waarom een nieuwe vaste kolom, en niet iets bestaands

- **Niet de `analyst`-kolom.** Die bestaat al (`ensure_analyst_column`, `service.py:531`) maar
  is een *agent-kolom*: de dispatcher zet er een kaart neer terwijl de analyse **draait**. Een
  parent die daar blijft staan ná decompositie is niet te onderscheiden van een parent die nog
  geanalyseerd wordt. Dat is precies de verwarring die we oplossen.
- **Niet `To Resume`.** Die zit in `_DISPATCH_COLUMNS` (`dispatch.py:1655`) — een parent daar
  wordt opnieuw gespawnd. Fataal: de analyse zou zichzelf herhalen.
- **Wel een vaste kolom.** Vaste kolommen zijn per definitie nooit auto-dispatched (alleen
  `Backlog` + `To Resume` worden gescand). Een parkeerkolom is dus gratis qua
  dispatcher-werk: toevoegen aan `COLUMNS` (`schemas.py:22`) volstaat en de dispatcher slaat
  hem automatisch over. Dat is exact het gedrag dat we willen —
  [`kanban-conventions.md`](./kanban-conventions.md) §1 legt deze eigenschap vast.

### Waarom de naam `Awaiting Subtasks` en niet `Analysis`

Een kolom `Analysis` naast de bestaande agent-kolom `analyst` is op een bord twee woorden die
op elkaar lijken en tegengestelde dingen betekenen ("wordt geanalyseerd" vs. "analyse is klaar,
wacht op uitvoering"). `Awaiting Subtasks` zegt letterlijk wat de toestand is en kan met niets
verward worden. De naam beschrijft bovendien de **werkelijke** conditie (wachten op kinderen),
niet het werksoort — wat 'm meteen generiek maakt (§3.1).

### 3.1 De kolom is parent-generiek, niet analyse-specifiek

De trigger was de analyse-fase, maar de conditie "parent met onafgeronde kinderen" is niet aan
`work_type` gebonden. Zowel een **modus-1 multi-agent-decompositie** (analyst splitst een
parent) als een **modus-2 leaf-spike met vervolgkaarten** (dit doc) produceert een parent met
kinderen, en beide lijden vandaag aan hetzelfde liegende `Done`. De regel is daarom bewust
gesteld op **"heeft ≥1 kind-kaart"**, niet op `work_type == "analysis"`.

Dat is geen scope-creep maar het tegendeel: één regel op één plek in plaats van een
analyse-uitzondering die de volgende decompositie-vorm opnieuw moet leren. In de praktijk zijn
vandaag vrijwel alle parents met kinderen analyse-kaarten, dus het waarneembare gedrag is
identiek — de regel is alleen eerlijker geformuleerd.

### 3.2 Wie sluit de parent, en wanneer

**Automatisch, zodra het laatste kind `Done` bereikt.** De trigger hangt aan de bestaande
Done-move van een *kind*: staat er een `parent_card_id` op, controleer dan of álle siblings
`Done` zijn, en zo ja verplaats de parent van `Awaiting Subtasks` naar `Done`.

Dit is bewust géén menselijke beslissing. CLAUDE.md schrijft voor de gebruiker niet uit de
beslisketen te halen, maar "alle kinderen zijn af, dus de ouder is af" is **boekhouding, geen
beslissing** — het is exact de definitie die de gebruiker zelf geeft ("tot ze echt compleet
is"). Een mens die het er niet mee eens is sleept de kaart gewoon terug; het bord werkt zijn
eigenaar niet tegen.

**Wat als een kind in `Impediment` blijft hangen?** Dan blijft de parent staan — correct: de
analyse is aantoonbaar niet compleet. De rollup (§5) toont dat kind als `impeded`, dus de
oorzaak staat op de parent-kaart in plaats van dat de operator hem moet zoeken. Dat is de
parkeerkolom die zijn werk doet: een vastgelopen vervolgtaak maakt de analyse zichtbaar
onafgerond in plaats van stil-verdampt.

## 4. Beslissing — `completed` is een afgeleide status, geen opgeslagen label

Eis E ("Kaarten die vandaag naar completed gaan moeten het label completed krijgen") is de enige
zin met twee lezingen. Ze verdient een expliciet antwoord in plaats van een stille keuze.

- **Lezing A (gekozen):** "label" = de *badge-tekst* in het vocabulaire van eis D. De zin pint
  de afbeelding vast: de kolom die we vandaag `Done` noemen heet in het statusvocabulaire
  `completed`. Er wordt niets opgeslagen.
- **Lezing B (verworpen):** "label" = een echte `labels[]`-entry `completed`, weggeschreven op
  de Done-move.

**Gekozen: A.** Drie redenen, in volgorde van gewicht:

1. **De zin staat in een opsomming van statussen.** De vier andere waarden (`Ready`,
   `in progress`, `dependent`, `impeded`) zijn stuk voor stuk *afgeleid* — geen ervan is een
   opgeslagen label. `completed` als enige uitzondering opslaan breekt de symmetrie van het
   vocabulaire dat de gebruiker in diezelfde ademtocht opsomt. De zin leest als een
   afbeeldingsregel ("Done ≡ completed"), niet als een nieuwe schrijfactie.
2. **Een opgeslagen `completed` is gedenormaliseerde staat die kán driften.** Hij is exact
   equivalent aan `column == "Done"` en voegt nul informatie toe. Maar hij kan wél verkeerd
   staan: `reopen_card` haalt een kaart uit `Done` terug naar `To Resume` en zou het label
   moeten opruimen. Een tweede bron van waarheid voor iets wat de kolom al zegt, is een bug
   die op zijn moment wacht.
3. **Contrast met de labels die wél gerechtvaardigd zijn.** Het zusterdoc kent `not-feasible`
   en `no-action-needed` toe als opgeslagen labels — terecht, want die dragen informatie die de
   kolom **niet** heeft (twee analyses in `Done`, totaal verschillende uitkomst). `completed`
   draagt precies niets bovenop de kolom. Dat is het onderscheid: label opslaan als het de
   kolom aanvult, afleiden als het de kolom herhaalt.

> **Aanname, expliciet.** Bedoelde de gebruiker tóch lezing B — bijvoorbeeld om in de
> Done-firehose op `label:completed` te kunnen filteren — dan is dat een kleine, losstaande
> toevoeging bovenop dit ontwerp (server-side schrijven op de Done-move + opruimen in
> `reopen_card`). Het ontwerp hieronder blokkeert die route niet. Er is bewust **geen kaart**
> voor aangemaakt: hij zou vandaag een label toevoegen dat niets zegt, en de eerste vraag die
> hem rechtvaardigt ("ik wil op completed filteren") is nog niet gesteld. Zie §8.

## 5. Beslissing — het statusvocabulaire wordt vijf afgeleide toestanden

`ReadyStateBadge.tsx` gaat van drie naar vijf waarden. Twee hernoemingen (eis D) plus twee
nieuwe toestanden:

| Status | Was | Betekenis | Afgeleid uit |
|---|---|---|---|
| `completed` | — (nieuw) | Werk is af | `column == "Done"` |
| `impeded` | — (nieuw) | Wacht op een mens | `column == "Impediment"` |
| `in_progress` | `dispatching` | Een agent werkt eraan | `claimed_by` begint met `agent:` |
| `dependent` | `blocked` | Wacht op andere kaarten | open `depends_on` **of** onafgeronde kinderen |
| `ready` | `ready` | Dispatchbaar | geen van bovenstaande |

**Precedentie, hoogste eerst:** `completed` → `impeded` → `in_progress` → `dependent` →
`ready`. Terminale, kolom-gebaseerde toestanden winnen, omdat een kaart met een verlopen claim
in `Done` anders `in_progress` zou tonen. Vandaag geldt al "dispatching wint van ready/blocked"
(`KanbanPage.tsx:127`); dit is diezelfde regel, doorgetrokken.

### Waarom `blocked` → `dependent` meer is dan cosmetica

De hernoeming die de gebruiker vraagt corrigeert een echte begripsfout. `blocked` suggereert
"er is iets mis" — het woord dat elders op dit bord (`Impediment`) betekent dat een mens moet
ingrijpen. Maar de toestand betekent alleen "wacht netjes op een dependency"; dat is de
**gezonde** werking van de DAG, niet een probleem. Met `impeded` als nieuwe, échte
"er-is-iets-mis"-status naast elkaar zou `blocked` ronduit misleidend worden. `dependent` zegt
neutraal wat het is.

Die hernoeming maakt bovendien §3 sluitend: een parent die op haar kinderen wacht is
letterlijk `dependent`. Zo hoeft de parkeerkolom **geen zesde status** te introduceren — het
bestaande, hernoemde vocabulaire dekt hem. `dependent` krijgt daarmee één heldere definitie:
*wacht op andere kaarten*, of dat nu via `depends_on` (siblings) of via `parent_card_id`
(kinderen) loopt. De bestaande `blockerTitles`-tooltip (`ReadyStateBadge.tsx:37`) draagt in
beide gevallen de titels van waar op gewacht wordt.

### Kosten van de hernoeming

Nihil, en dat is bewust nagetrokken: `ReadyState` is **puur frontend**. Geen kolom, geen
API-veld, geen backend-constante — de waarden worden in `KanbanPage.tsx:127–150` afgeleid en in
`ReadyStateBadge.tsx` gerenderd. De enige externe koppeling is het `data-ready-state`-attribuut
(een test-/DOM-hook). Er is dus geen migratie en geen wire-compat-vraag.

## 6. De naad met het uitkomst-contract (`analysis-outcome-contract-decision.md`)

De twee docs raken elkaar op één punt — de Done-move van een analyse — en dat punt moet
eenduidig zijn. De afbakening:

- **Het zusterdoc beslist *of* een analyse mag afsluiten.** `move_card` krijgt een verplichte
  `outcome`-enum: `decomposed` (geverifieerd tegen echte kind-kaarten) / `not_feasible` /
  `no_action_needed`.
- **Dit doc beslist *waar de kaart dan heen gaat*.**

Samen leveren ze één sluitende levenscyclus, en ze passen exact op elkaar:

| `outcome` | Bestemming | Waarom |
|---|---|---|
| `decomposed` | **`Awaiting Subtasks`** | Er zijn kinderen om op te wachten. Dit doc. |
| `not_feasible` | `Done` + label `not-feasible` | Geen kinderen; de analyse ís af. Zusterdoc. |
| `no_action_needed` | `Done` + label `no-action-needed` | Idem. Zusterdoc. |

Dat is geen toeval: `decomposed` is per definitie het geval "er zijn kind-kaarten", en dat is
precies de conditie van §3. De poort verifieert de kinderen al — dezelfde controle bepaalt de
bestemming. **De twee ontwerpen delen één interceptiepunt en één `has children`-check.**

**Daarom is de volgorde dwingend** (en niet louter netjes): beide wijzigen de Done-tak van
`move_card`. Landt de parkeerkolom vóór de poort, dan bouwt hij een eigen interceptie die de
poort daarna moet herschrijven, en twee sessies vechten om dezelfde regels code. Kaart §7 #2
krijgt daarom een echte `depends_on` op de poort-kaart (`b4f74609…`) — een contract, geen
volgorde-voorkeur.

Het label-vocabulaire is door §6 van het zusterdoc expliciet aan déze kaart toegewezen; §4
hierboven is dat antwoord.

## 7. Vervolgkaarten (aangemaakt in deze sessie)

Conform de leaf-spike-follow-up-clausule maakt deze sessie haar eigen vervolgkaarten aan; dit
doc is de verantwoording, de kaarten zijn de uitvoerbare neerslag.

1. **`[feature]` Statusvocabulaire → vijf toestanden** (geen deps) — de hernoemingen +
   `impeded`/`completed` + precedentie. Puur frontend (§5), dus zelfstandig dispatchbaar en
   direct waardevol: `impeded` en `completed` zijn ook zonder de rest winst.
2. **`[feature]` `Awaiting Subtasks`-kolom + parent-levenscyclus** (dep: `b4f74609…`, de
   uitkomst-poort) — de kolom in `COLUMNS`, parken op `decomposed`, auto-sluiten als het
   laatste kind `Done` haalt. Echte afhankelijkheid: deelt het interceptiepunt van de poort
   (§6).
3. **`[feature]` Subtaak-rollup op de parent-kaart** (dep: #1) — de omgekeerde
   `parent_card_id`-view met een statusbadge per kind. Echte afhankelijkheid: rendert het
   vocabulaire uit #1. Bewust **niet** afhankelijk van #2 — een rollup is nuttig op elke
   parent, ook zonder parkeerkolom.

**Bewust géén kaart:**

- *Het opgeslagen `completed`-label* — §4 legt uit waarom het vandaag niets toevoegt.
- *Een nieuw children-endpoint.* Nagetrokken: `KanbanPage` haalt álle kaarten van het project op
  en bouwt al een `cardsById`-map (`KanbanPage.tsx:131`). De kinderen zijn dus client-side al
  aanwezig; `CardDrawer` (`CardDrawer.tsx:860–870`) krijgt vandaag alleen `card` en heeft
  hooguit een extra prop nodig. Kaart #3 blijft daarmee frontend-only. Een
  `?parent_card_id=`-filter op de cards-endpoint zou werk zijn dat niets oplost.

## 8. Wat dit oplost — en wat niet

**Wel:** een gedecomponeerde analyse kan niet meer stil in de Done-firehose zakken; ze staat in
een eigen kolom tot haar vervolgwerk daadwerkelijk is geland. De koppeling parent→kind wordt
voor het eerst zichtbaar, mét status per kind, dus een vastgelopen vervolgtaak maakt de analyse
zichtbaar onaf in plaats van onzichtbaar verdampt. Het vocabulaire benoemt eindelijk de twee
toestanden waar een operator op scant ("wacht op mij" / "klaar") en stopt met `blocked` te
roepen tegen een gezonde dependency.

**Niet:** dit doc maakt de status *zichtbaar*, het maakt de uitkomst niet *verplicht* — dat is
het zusterdoc, en zonder die poort kan een analyse nog steeds zonder kinderen naar `Done` (dan
is er niets om op te wachten en is de parkeerkolom niet van toepassing). De twee samen zijn het
antwoord; elk apart is een helft. Verder beoordeelt de parkeerkolom geen **kwaliteit**: vijf
afgeronde slechte kind-kaarten sluiten de parent net zo goed als vijf goede. Dat blijft
mensenwerk, en hoort dat te blijven.
