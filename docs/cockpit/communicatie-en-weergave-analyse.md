# Communicatie & weergave — analyse

> **Type:** analyse (leaf design-deliverable). **Kaart:** `b0d2124e…`.
> **Uitkomst:** `decomposed` — 3 vervolgkaarten (zie §6).
> **Scope:** hoe agents naar de mens **schrijven** (formulering) én hoe het bord
> die tekst **toont** (weergave). Niet in scope: de *structuur* van de
> `docs/cockpit/`-boom zelf — die hoort bij de knowledge-structure-kaarten
> `25bfe803…` (frontmatter-ruggengraat) en `340a3010…` (gegenereerde index).

## 1. De klacht, scherp gesteld

De kaart geeft één concreet voorbeeld: een `✅ Completed`-samenvatting die
"faalt op alle punten — zowel moeilijk leesbaar door de **weergave** als de
**formulering**". Dat zijn twee losse problemen die je los moet oplossen:

| As | Vraag | Waar zit het |
|---|---|---|
| **A. Formulering** | *Schrijft* de agent op het juiste detailniveau, met structuur? | Persona-prompts + skills (agent-kant) |
| **B. Weergave** | *Toont* het bord die tekst leesbaar, zonder schuifbalken-wildgroei? | `frontend/src/features/kanban/` (UI-kant) |

Een goede samenvatting die als platte muur tekst wordt gerenderd blijft
onleesbaar (as B faalt). Een mooi gerenderde samenvatting die het verkeerde
vertelt blijft ruis (as A faalt). Beide moeten kloppen.

## 2. As A — waarom de voorbeeld-samenvatting faalt

De geplakte samenvatting (±200 woorden, één alinea) faalt op vier meetbare
punten:

1. **Verkeerd detailniveau.** Ze mengt wat de mens wíl weten (*"33 van 35
   follow-ups al geïmplementeerd; het gat is 2 policy-lagen aansluiten"*) met
   **proces-meta** die de mens niet hoeft te lezen: *"FCR kwam terug met OK",
   "session-retro: 1 self-improve-kaart gefiled, 1 problem-finding gededupet",
   "gereconstrueerd uit de durable kanban_ops-audit-log", "comment technisch
   geslaagd al bestaat de kaart niet meer"*. Proces-meta is boekhouding — het
   hoort in de activity-feed/retro-kaarten, niet in de banner die de mens
   naleest.
2. **De kern ligt begraven.** Het belangrijkste feit (bijna alles bestaat al;
   er rest een klein aansluit-gat) staat middenin de muur, niet vooraan.
3. **Muur tekst, geen structuur.** Eén alinea, geen openingszin, geen bullets,
   geen scheiding tussen *wat veranderde* / *wat het betekent* / *wat rest*.
4. **Onverklaard jargon.** `RepoBootstrapService`, `SecretStore`,
   `Clear-Done-sweep`, `FCR`, `kanban_ops-audit-log` — interne namen gedumpt
   zonder te zeggen waaróm ze ertoe doen voor de lezer.

### 2.1 Voorgestelde vorm — outcome-first, drie delen

Eén vaste, uniforme vorm voor élke mens-gerichte samenvatting (Done-summary,
Impediment-vraag, kaart-comment die de mens naleest):

```markdown
**Uitkomst.** <één zin: wat is nu waar dat eerst niet waar was>

- <wat & waarom, op het niveau dat de lezer schikt — 2-4 bullets>
- <…>

**Rest / nazicht (optioneel).** <wat open blijft of wat de mens wil checken>
```

Regels die de vier faal-punten sluiten:

- **Outcome eerst** (sluit #2): de eerste zin is de conclusie, niet de aanloop.
- **Proces-meta eruit** (sluit #1): FCR, retro, dedup, audit-log-archeologie
  horen níet in de samenvatting. De reviewer-gate en retro-skill hebben hun
  eigen kanalen.
- **Bullets, geen muur** (sluit #3): structuur is verplicht, geen optie.
- **Jargon = naam + waarom** (sluit #4): noem een interne component alleen als
  je erbij zegt wat 'ie voor de lezer betekent, anders laat je 'm weg.

> Deze samenvatting-vorm is ook toegepast op de Done-move van *deze* kaart —
> de analyse is haar eigen eerste testgeval.

### 2.2 Skill, geen agent

De kaart vraagt: *"kan mogelijks via skills en/of een agent specifiek voor
communicatie."* Aanbeveling: **een skill, geen aparte agent.**

Een communicatie-**agent** is het verkeerde gereedschap. Volgens
[`sync-vs-async-delegation-decision.md`](./sync-vs-async-delegation-decision.md)
verdient iets alleen een eigen agent/kaart als het async, bord-zichtbaar,
crash-overlevend of los-dispatchbaar hoort te zijn. Een samenvatting schrijven
is nét het tegenovergestelde: het gebeurt **inline**, aan het eind van elke
sessie, door de persona die het werk deed. Een aparte agent zou een
dispatch-cyclus + context-overhead toevoegen voor iets dat binnen de
bestaande sessie hoort.

De juiste vorm is een **skill** — dezelfde shape als de bestaande
`session-retro`-skill die óók vlak vóór `move_card → Done` draait. De skill
codificeert de §2.1-vorm en wordt aangeroepen op het ship-pad; de
persona-prompts (engineer/analyst/reviewer) en de `move_card`-summary-guidance
verwijzen ernaar.

### 2.3 Verwachting temperen — instructie ≠ contract

De analyse-outcome-historie leert een harde les
([`analysis-outcome-contract-decision.md`](./analysis-outcome-contract-decision.md)
§5): twee rondes "los prompt-instructie" erodeerden onder context-druk aan het
eind van het budget; pas een **gesloten enum op de poort** maakte er een
contract van. Voor prós­a-kwaliteit is een even harde gate onhaalbaar — je kunt
"leesbaarheid" niet enum'en. Wees dus eerlijk over de hefboom:

- **Wel doen:** de skill + template (goedkoop, direct) **en** de weergave zó
  maken dat structuur beloond wordt (§3.1). Als een gestructureerde markdown-
  samenvatting er goed uitziet en een muur tekst er slecht uitziet, trekt de
  weergave de auteur vanzelf naar structuur. Dat is een zachtere maar echtere
  hefboom dan een prosa-gate.
- **Niet doen:** een prosa-kwaliteit-gate bouwen die "muur tekst" probeert te
  detecteren. Vals-positieven en een kat-en-muis-spel; verspilde infra.
- **Later, alleen bij drift:** een advisory check (bijv. "Done-summary is één
  alinea zonder markdown-structuur → warning") in de stijl van
  `check-analysis-outcomes.sh` — advies, geen blokkade.

## 3. As B — waarom de weergave faalt

Twee concrete, in code aanwijsbare oorzaken.

### 3.1 De samenvatting wordt als platte tekst gerenderd, niet als markdown

`DoneSummaryBanner` in
[`frontend/src/features/kanban/components/CardDrawer.tsx:349`](../../frontend/src/features/kanban/components/CardDrawer.tsx)
rendert de samenvatting als:

```tsx
<div className="text-foreground whitespace-pre-wrap">{summary}</div>
```

`whitespace-pre-wrap` = platte tekst met behoud van newlines. **Geen markdown.**
Zelfs een perfect gestructureerde samenvatting (bullets, vetgedrukte outcome)
toont als letterlijke `**Uitkomst.**`-asterisken en `-`-streepjes. Dezelfde file
gebruikt élders al `<MarkdownRenderer>` (o.a. voor de kaart-beschrijving op
regel 1311 en het plan-deliverable op 273) — de fix is één component
verwisselen. **Dit is de goedkoopste, hoogste-hefboom-ingreep van de hele
analyse**, en hij is de voorwaarde die §2.1 z'n effect geeft: pas als markdown
rendert, loont het om de samenvatting te structureren.

### 3.2 Alles propt in één modal → schuifbalken-wildgroei

`CardDrawer` is één `Dialog` met `MODAL_SIZES.LG` =
`max-w-4xl max-h-[90vh] overflow-y-auto`
([`constants.ts:8`](../../frontend/src/lib/constants.ts)). Binnen die éne
scroll-container stapelt de modal, verticaal, voor een Done-kaart:

open gates → Done-banner → preview-control → review-control → reopen-control →
beschrijving → spec-link → subtasks → actie-knoppenrij → een `Tabs`-blok met
**7 tabs** (Deliverables / Screenshots / Activity / Plan / Ledger / Tokens /
Run).

Dat is de "heel wat schuifbalken"-klacht, met twee deeloorzaken:

1. **Geneste scroll.** De modal scrollt (`overflow-y-auto`), én binnen-content
   (Plan-tab, Activity-tab, een lange `MarkdownRenderer`) kan zélf scrollen →
   dubbele schuifbalken.
2. **Te veel op één oppervlak.** De modal is tegelijk lees-view (wat gebeurde
   er?), operator-console (dispatch/edit/delete/claim) én telemetrie
   (Run-transcript, Tokens, Ledger). Voor de mens die alleen wíl nalezen wat er
   klaar is, ligt dat begraven onder operator-chrome.

### 3.3 Richting (niet dichtgetimmerd — dit is een eigen ontwerp-fork)

De *diagnose* is hard; de *oplossing* is een UX-beslissing met een echte fork,
dus die krijgt een eigen analyse-kaart (§6, kaart 3) i.p.v. hier voorbarig te
kiezen. De kandidaten:

| Richting | Kern | Trade-off |
|---|---|---|
| **A. Lees-first modal** | Modal toont standaard alleen mens-relevante lees-content (titel, status-banner als markdown, beschrijving, subtasks, deliverables); operator-/telemetrie-tabs (Run/Tokens/Ledger/Edit) achter één afgescheiden gebied of secundaire view. Eén scroll-container. | Minste bouwwerk; lost geneste scroll + "te veel op één oppervlak" op zonder nieuw route-model. |
| **B. Volledige kaart-pagina** | Diepe content naar een eigen route (`/kanban/card/:id`); de `?card=<id>`-deeplink bestaat al. Modal wordt een lichte quick-look. | Meer werk (nieuw route + navigatie), maar schaalt beter als kaarten rijker worden. |

Aanbeveling als startpunt voor die kaart: **A** (goedkoper, lost de gemelde
pijn direct op), met B als vervolg zodra kaart-content verder groeit. De
kaart beslist definitief.

## 4. As C — uniformisering (en wat al gedekt is)

De kaart noemt "uniformisering". Twee lagen:

- **Vocabulaire** — al gedekt door
  [`terminology.md`](./terminology.md) (5 canonieke kernbegrippen).
- **Doc-*structuur*** (frontmatter, index, discoverability) — al belegd bij
  Backlog-kaarten `25bfe803…` en `340a3010…`. **Niet dupliceren.**
- **Schrijf-*stijl*** (detailniveau, outcome-first, jargon-regel) — dít is het
  gat. Het is breder dan alleen Done-summaries: dezelfde §2.1-regels gelden
  voor Impediment-vragen en mens-gerichte comments. De skill uit §2.2 is de
  uniformiserende drager.

## 5. Aanbeveling in één oogopslag

1. **Weergave eerst** (§3.1): render de Done-summary als markdown. Klein,
   zelfstandig, en het maakt structuur zichtbaar zodat de rest loont.
2. **Dan de stijl-skill** (§2.2): codificeer de outcome-first drie-delen-vorm
   in een skill + wire 'm in het ship-pad en de persona-prompts.
3. **Apart uitzoeken** (§3.3): de modal-herindeling is een eigen ontwerp-fork →
   eigen analyse-kaart.

Geen aparte communicatie-agent; geen prosa-kwaliteit-gate.

## 6. Vervolgkaarten

| # | Kaart | id | work_type | depends_on |
|---|---|---|---|---|
| 1 | Render Done-summary (banner) als markdown i.p.v. platte `whitespace-pre-wrap` | `56ddf5a6…` (nieuw) | feature | — |
| 2 | Schrijfstijl-conventie / `writing-summaries`-skill (outcome-first) + wire in ship-pad & personas | `4358fe0a…` (bestaand, verrijkt) | chore | kaart 1 |
| 3 | Kaart-modal weergave: lees-first, minder schuifbalken (ontwerp-fork A vs B) | `624f7718…` (nieuw) | analysis | — |

**Dedup op kaart 2.** Een parallelle analyse
(`docs/cockpit/product-owner-volgbaarheid-analyse.md`) had al een bijna
identieke kaart `4358fe0a…` ("Product-taal-conventie voor Done-summaries &
impediment-options") op Backlog gezet. In plaats van een duplicaat te laten
staan (die bovendien dezelfde persona-prompts + `move_card`-guidance zou
raken → merge-conflict) is de skill-mechaniek, de outcome-first-template, de
"skill-geen-agent"- en "geen-prosa-gate"-beslissingen en het rendering-contract
**in die bestaande kaart gevouwen** (comment + `depends_on`).

**Waarom kaart 2 → kaart 1 wacht:** kaart 2 leert auteurs hun samenvatting in
markdown te structureren; die output wordt pas leesbaar geconsumeerd zodra
kaart 1 markdown rendert. Zonder kaart 1 toont de nieuwe template letterlijke
asterisken in de banner — een echt consumptie-contract, dus een echte
`depends_on`. Kaart 3 staat los (geen contract met 1/2).
