# Reviewer-agent + review-kolom — wenselijk? Trade-off + beslissing (REVISED)

**Datum:** 2026-07-10
**Status:** herzien
**Kaart:** _zie doc — geen hex-id in dit beslisdoc vastgelegd_
**Uitkomst:** **Wél bouwen, in lichtere vorm** (REVISED): feature-compliance-review als subagent-call binnen dezelfde engineer-sessie vóór `move_card Done`. Geen aparte persona, geen Review-kolom.

> Kanban-kaart: "Onderzoek: reviewer-agent + review-kolom — is dit wenselijk?"
> Eerste iteratie van dit document concludeerde "niet bouwen". Deze revisie
> corrigeert die conclusie op drie punten (zie §"Wat er fout was aan de eerste
> iteratie") en komt uit op **"wél bouwen, in een lichtere vorm"** — een
> feature-compliance review-stap binnen dezelfde engineer-sessie, als
> subagent-call vóór `move_card Done`. Geen aparte `reviewer`-persona, geen
> Review-kolom, geen concurrency-cap-impact.
>
> De aanbevolen verbetering kan in een aparte, kleine Backlog-kaart
> geïmplementeerd worden (zie §"Concrete vervolgkaart"); de scope is bewust
> klein gehouden zodat de kosten uit de eerste analyse (extra sessie,
> concurrency-blokkade, visuele complexiteit) niet opnieuw geïntroduceerd
> worden.

## Context

De vraag is: voegen we bovenop de bestaande kwaliteitsarchitectuur een
**feature-compliance-review** toe die, *met cleared context*, valideert of
de implementatie daadwerkelijk doet wat de kaart vroeg — los van de
code-quality-check die `/code-review` al doet?

## Wat er fout was aan de eerste iteratie

De eerste versie van dit document concludeerde "niet bouwen" op drie punten
die herziening nodig hadden:

1. **Verkeerde aanname: `/code-review` dekt feature-compliance.**
   `/code-review` is een slash-command dat de diff leest en "issues en
   improvement suggestions" produceert — een code-quality-review. Het leest
   de oorspronkelijke kaart-beschrijving niet als eisen-set, valideert niet
   of de implementatie de use-case dekt, en toetst niet of de feature
   integreert zoals beloofd. Dat is een *andere vraag* dan
   "is dit wat de kaart vroeg?". De eerste iteratie behandelde die twee
   als inwisselbaar; dat zijn ze niet.

2. **Onderschatting van het cleared-context-effect.**
   "Zelfde model, andere prompt" is een cosmetische beschrijving. Het
   substantieve verschil is: de engineer-sessie heeft de auteur-context
   opgebouwd (de bedoeling, de mislukte pogingen, de workarounds, de
   aannames). Die context introduceert motivated reasoning — je leest je
   eigen werk welwillend omdat je de intentie kent. Een reviewer-sessie
   met cleared context, gevoed met alleen (kaart-beschrijving + diff),
   wordt gedwongen om *vanaf de eisen* te lezen in plaats van *vanaf de
   reis ernaar toe*. Dat is geen detail; dat is precies waarom externe
   reviewers (mens of AI) andere dingen zien dan de auteur die zijn eigen
   werk nakijkt.

3. **Verkeerd geframed autonomie-argument.**
   De eerste iteratie stelde een gate voor als "autonomy-removing". Dat
   klopt alleen als de gate meer frictie toevoegt dan dat hij
   handmatige verificatie wegneemt. Een betrouwbare pre-Done
   feature-compliance-review is precies het tegenovergestelde: hij
   verwijdert (of reduceert) de handmatige stap die jij als mens nu doet
   om te valideren "doet dit wat de kaart vroeg?". Zonder die laag moet
   jij dat blijven doen; mét die laag doet een sessie het al en kan jij
   je richten op uitzonderingen. Dat is autonomie in de zin die
   `CLAUDE.md` bedoelt: "voert werkzaamheden steeds autonomer uit".

De rest van deze iteratie hertrekt de afweging met deze drie correcties
meegenomen.

## Geverifieerde stand van zaken

Alle vier de bullets uit de kaart-beschrijving zijn in code/doku
geverifieerd:

1. **Geen aparte Review-kolom.** Vaste kolommen (`Backlog`, `Impediment`,
   `Done`, `To Resume`) + één kolom per `.claude/agents/*.md`-persona
   (`sync_agent_columns`, `kanban-dispatch-spec.md`). Er bestaan vandaag
   alleen `analyst.md` en `engineer.md`.
2. **Kwaliteitsbewaking zit al op twee niveaus in de engineer-sessie:**
   - `iteration-loop preset verify` — verplichte end-of-card gate
     (frontend `npm run lint && npm run build`).
   - `/code-review effort medium` — slash-command dat de diff reviewt
     op code-quality. Pre-ship, sinds `d95b0b6`/`dde58db`,
     `engineer.md` §6.
3. **`_IMPEDIMENT_AGENTS` is al opgeruimd** (`35beb16`).
4. **Persona-infrastructuur is al low-friction** voor het geval we ooit
   een aparte Reviewer-persona zouden willen — al is dat nu niet de
   aanbeveling (zie "Wat we NIET bouwen").

## Wat lost de feature-compliance-review op?

De feature-compliance-review (kortweg FCR) is een **subagent-call binnen
de engineer-sessie**, direct vóór `move_card Done`, met deze prompt:

> Je reviewt een feature-implementatie tegen zijn oorspronkelijke
> specificatie. Inputs: de oorspronkelijke kaart-titel, -beschrijving, en
> de diff tegen `origin/master`. Vraag: doet de implementatie wat er
> gevraagd werd?
>
> Specifiek:
> - Elke requirement/bullet uit de beschrijving is geïmplementeerd.
> - De API/UI matcht de specificatie (naamgeving, gedrag, edge cases).
> - De implementatie integreert zonder siblings te breken.
> - Het deliverable dat in de samenvatting geclaimd wordt, is
>   daadwerkelijk aanwezig.
>
> Output: OK om te shippen, OF een lijst met blokkerende issues met
> `file:line`-refs. Dit is een **feature-compliance-check**, geen
> code-quality-check — die is al apart gelopen via `/code-review`.

De FCR dekt de drie eigenschappen die de huidige architectuur niet dekt:

| Eigenschap | `/code-review` | `iteration-loop verify` | CI | **FCR** |
|---|---|---|---|---|
| Code-quality (stijl, bugs, eenvoud) | ✅ | ✅ (lint/build) | ✅ (ruff/pytest) | n.v.t. |
| Tracking van gate-uitvoering | n.v.t. | ✅ (`<loop-complete>`) | ✅ (Actions) | n.v.t. |
| Echte geautomatiseerde backstop ná push | n.v.t. | n.v.t. | ✅ (`quality.yml`) | n.v.t. |
| **Cleared-context review** | ❌ (zelfde sessie-context) | n.v.t. | n.v.t. | ✅ (verse subagent) |
| **Feature-vs-spec validatie** | ❌ (leest de spec niet) | n.v.t. | n.v.t. | ✅ |
| **Integratie met bestaande app** | deels (diff-only) | n.v.t. | deels (tests) | ✅ (kaart-spec als anker) |

Het gat dat FCR dicht is de drie ✅-en op de laatste drie rijen: niets
vandaag kijkt met cleared context en de kaart-spec als anker of de
implementatie *de gevraagde feature* is — niet alleen of de *code* goed is.

## Wat we NIET bouwen

De eerste iteratie noemde een volledige Reviewer-persona + Review-kolom
als hypothese; die hypothese houden we hier expliciet af:

- **Geen aparte `.claude/agents/reviewer.md`** — niet nodig. De FCR is
  een subagent-call, geen aparte sessie. Een heel persona-bestand +
  kolom zou alleen zinvol zijn als de FCR ook na `Done` als aparte
  sessie moet kunnen draaien (en dat doet `request_review` al — een
  bestaande mechanisme dat voor *post-Done* twijfel een nieuwe
  analysis-kaart aanmaakt; zie
  `backend/app/kanban/service.py:async def request_review`).
- **Geen Review-kolom** — geen nood aan. De FCR gebeurt binnen dezelfde
  engineer-sessie, direct vóór `move_card Done`; er is geen aparte
  kolom-state nodig tussen "engineer klaar" en "reviewer akkoord".
- **Geen concurrency-cap-impact** — geen aparte sessie betekent geen
  actieve slot in de 1-actieve-sessie-per-project-cap. De FCR-blokkade
  is alleen binnen de engineer-sessie (paar minuten) en sluit de
  volgende kaart-dispatch niet uit.
- **Geen aparte `ship_mode="review"`** — niet nodig. FCR is een
  subagent-call in de bestaande flow; het is geen nieuwe ship-mode.

De kosten uit de eerste iteratie (extra sessie, concurrency-blokkade,
visuele complexiteit, routing-ambiguïteit) zijn dus **niet van toepassing
op deze lichtere versie**. Wat de FCR wél kost is:

| Kosten | Weging |
|---|---|
| Eén extra subagent-call per kaart | Vergelijkbaar met de `/code-review`-call die al loopt; al in-budget. |
| Iets langere doorlooptijd per kaart | Minuten; binnen dezelfde engineer-sessie, geen cap-impact. |
| Mogelijk extra iteraties als FCR blokkeert | Goed nieuws — dat is precies waarvoor de FCR bestaat. |

Dat is alles.

## Past dit bij "zo autonoom mogelijk"?

**Ja, dit is precies wat autonomie betekent.** Een betrouwbare pre-Done
gate die met cleared context valideert of de feature klopt, betekent dat
jij als mens niet meer élke kaart handmatig hoeft na te kijken. De FCR
doet de routine-check; jij richt je op uitzonderingen, edge cases, en de
kaarten die om een menselijke beoordeling vragen. Dat is autonomy-
*enabling*, niet -reducing.

De eerste iteratie had dit omgedraaid: "een gate = minder autonoom". Dat
geldt alleen als de gate frictie toevoegt zonder iets weg te nemen. Hier
neemt hij juist frictie weg (handmatige verificatie).

## Lichtere alternatieven — afweging

Drie alternatieven zijn overwogen, alle inferieur aan de FCR voor dit
specifieke doel:

1. **`ship_mode="pull-request"` forceren voor risicovolle kaarten.**
   Bestaat al. Prima voor kaarten waar een menselijke gate expliciet
   gewenst is — niet hetzelfde als routine feature-compliance. Vervangt
   de FCR niet, want: (a) PR-review is een mens, geen AI, dus je
   verschuift de last in plaats van dat je haar weghaalt; (b) een PR is
   geen anker voor de oorspronkelijke kaart-spec — een reviewer leest
   de PR, niet de kaart.

2. **CI strakker maken** (extra ruff-rule, mutation-tests, contract-
   tests). Prima voor klassen bugs die door de huidige suite glippen —
   niet voor "is dit de feature die we wilden?". CI test of de code
   *werkt* zoals geschreven, niet of hij *doet* wat er gevraagd werd.

3. **`/code-review effort high` of `ultra` oproepen.** Verandert de
   bestaande code-quality-check, niet de feature-compliance-check. De
   slash-command leest nog steeds alleen de diff, niet de kaart-spec.
   Geen oplossing voor het specifieke gat.

Geen van deze dekt wat de FCR dekt.

## Wanneer heroverwegen

- Als de FCR in de praktijk geen blokkeringen oplevert die de
  `/code-review` niet al ving, dan heeft de extra stap geen meerwaarde
  en kan hij teruggetrokken worden — empirisch meetbaar.
- Als de FCR te vaak blokkeert op dingen die jij als mens toch al
  oké vindt (vals-positieven door strenge AI-reviewer), dan de
  drempel verlagen — bv. alleen FCR bij `work_type="feature"` of
  `work_type="bug"`, niet bij `chore`.
- Als een specifieke klasse bugs *door* de FCR + CI glipt, dan is
  CI strakker maken het juiste antwoord (zie alternatief 2 hierboven).
- Voor echte four-eyes-eisen (audit, regulering, externe-impact-code):
  een menselijke reviewer via `ship_mode="pull-request"`, niet een
  AI-reviewer.

## Concrete vervolgkaart

Implementatie van de FCR is klein en geïsoleerd:

- **Bestand**: `.claude/agents/engineer.md`, in de §"Zelf-review"
  sectie, na de `/code-review effort medium`-regel en vóór de
  `_build_ship_instructions`-prompt.
- **Wijziging**: voeg één nieuwe stap toe (vergelijkbaar met de
  bestaande `/code-review`-regel) die een subagent-call doet met de
  prompt uit §"Wat lost de feature-compliance-review op?" hierboven.
- **Optioneel**: dezelfde stap toevoegen aan
  `_build_ship_instructions(ship_mode)` in
  `backend/app/kanban/dispatch.py:673` zodat ook sessies die via
  auto-dispatch spawnen de FCR krijgen.
- **Optioneel, scope**: limiet tot `work_type` in `("feature", "bug")`
  om `chore` en `analysis` overbodige overhead te besparen — empirisch
  te valideren.

Geschat: één engineer-kaart, halve tot hele dag werk inclusief een
empirische check of de FCR inderdaad dingen vindt die `/code-review`
mist (een simpele vergelijking op de eerstvolgende 5-10 kaarten na
introductie is genoeg voor een eerste "werkt dit"-signaal).

## Wat deze kaart doet

Alleen dit document (gereviseerd) + een corresponderende entry in
`kanban-followups.md`. Geen codewijziging in deze kaart; de FCR-
implementatie is een aparte Backlog-kaart (zie vorige sectie).

## Tijdslijn van dit document

| Datum | Wat |
|---|---|
| 2026-07-10 (eerste iteratie) | Conclusie: "niet bouwen". Onderbouwd door `/code-review` + CI + `iteration-loop preset verify` als afdoende poorten. |
| 2026-07-10 (revisie) | Correctie op drie punten (§"Wat er fout was aan de eerste iteratie"): FCR ≠ `/code-review`; cleared context ≠ author-context; gate = autonomy-enabling. Conclusie nu: "wél bouwen, in lichtere vorm — subagent-call binnen engineer-sessie vóór `move_card Done`". Geen aparte persona, geen kolom, geen concurrency-impact. |
