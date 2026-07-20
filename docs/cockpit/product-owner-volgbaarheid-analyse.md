---
title: "Analyse — Volgbaarheid van het project voor de product owner"
type: analysis
status: active
---

# Analyse — Volgbaarheid van het project voor de product owner

**Datum:** 2026-07-18
**Status:** Analyse / aanbeveling — één rol-herkadering + drie buildable follow-up-kaarten
**Kaart:** `75c0952f14d74b3caca2e757a725b991`
**Uitkomst:** Niet "meer lezen" maar een PO-rollup-laag bouwen zodat de mens alleen nog op product-owner-hoogte hoeft in te grijpen; gedecomposeerd in 3 Backlog-kaarten.

**Trigger:** kanban-kaart `75c0952f…` — *"Bevinding - Moeilijkheid om het project te volgen"*.
Gebruiker:
> "Vandaag heb ik het moeilijk om de groei en opbouw van dit project te volgen. Heb
> mogelijks het gevoel dat ik tegen te veel van de nadelen van jouw lange autonomie aan
> het aanlopen ben, de gevolgen van vibe coding. Denk je dat er manieren zijn dat we
> hierop kunnen verbeteren? Technieken, samenvattingen, taal, formulering, je moet mij
> zien als de product owner?"

Verwant: [`00-orientation.md`](./00-orientation.md) (statische onboarding),
[`decisions.md`](./decisions.md) (beslis-register), [`jira-lessen-analyse.md`](./jira-lessen-analyse.md)
(bord-lessen), [`knowledge-structure-navigation-analysis.md`](./knowledge-structure-navigation-analysis.md)
(doc-navigatie — maar dat is *agent*-facing, zie §2).

---

## 1. Antwoord in het kort

De eerlijke herkadering eerst, want die bepaalt alle aanbevelingen: **het doel is niet dat
jij álles volgt — dat is bij deze snelheid noch haalbaar noch wenselijk.** Meerdere agents
produceren per dag beslissingen, kaarten, docs en commits. De review-bandbreedte van één
mens op *kaart-/code-hoogte* schaalt daar structureel niet mee mee. Dat is geen tekortkoming
van "vibe coding" die je met meer discipline wegwerkt — het is een **eigenschap van de
opzet**. Wie op merge-hoogte probeert te volgen wat op autonomie-snelheid wordt geproduceerd,
verdrinkt gegarandeerd.

Het gevoel dat je beschrijft — "ik loop tegen de nadelen van lange autonomie aan" — is dus
geen falen van jou als product owner. Het is een **ontbrekende laag** in het systeem: alle
informatie bestaat (register, kaarten, retros, commits), maar niets ervan is *vorm gegeven
voor een product owner*. Het is geschreven agent-naar-agent, voor continuïteit en
engineering-audit. Niemand heeft ooit de laag gebouwd die zegt: *"dit is wat er deze week
veranderde en waarom het jou aangaat."*

De remedie is daarom **niet "lees meer", maar "verklein wat je moet lezen tot precies dat wat
alleen jij kunt beslissen"**, en dat in jouw taal presenteren. Concreet:

1. **Rol-herkadering** (§3) — de mindset-shift van *stroom-volgen* naar *sturen op
   checkpoints*. Dit is de kern; zonder deze verschuiving lost geen enkele tool het op.
2. **Drie buildables** (§5) — een wekelijkse **PO-digest**, een **"wacht op jou"-wachtrij**,
   en een **product-taal-conventie** voor Done-samenvattingen en impediment-vragen.

De rest van dit doc onderbouwt waarom, en levert de drie kaarten op.

---

## 2. Diagnose — waarom het nú schuurt

Het probleem is een **hoogte-probleem** en een **taal-probleem**, niet een data-probleem.

**a. De artefacten zijn agent-facing, niet PO-facing.** Alles wat het systeem produceert is
geoptimaliseerd voor de vólgende agent of voor engineering-audit:

| Artefact | Waarvoor het geoptimaliseerd is | Waarom het de PO niet bedient |
|---|---|---|
| `decisions.md` | Chronologisch, append-only register — "is X al beslist?" voor een agent | Dicht, technisch, nieuwste-eerst; geen "waarom dit ertoe doet"-laag |
| Done-`summary` op kaarten | De volgende agent context geven | Leidt met de engineering-verandering, niet met de productbetekenis |
| `session-retro`-kaarten | Zelfverbetering vastleggen | Produceren *méér* kaarten — vergroten de stroom die je al niet bijhoudt |
| Kanban-bord | Granulaire werkeenheid per agent | 50+ kaarten; geen roll-up naar "waar staan we" |
| `00-orientation.md` | Onboarding | Statisch — vertelt de opzet, niet wat er sinds gisteren gebeurde |
| Dashboard-pagina | Configuratie-inventaris (agents, hooks, permissions, skills) | Toont *config*, geen project-*activiteit* — er is geen "wat gebeurt er"-view |

Er is dus letterlijk **geen scherm en geen document dat een product owner opent om te weten
"wat is er veranderd en wat betekent het"**. De `knowledge-structure`-kaarten
(frontmatter → index → `llms.txt`) lossen doc-navigatie op, maar expliciet *voor agents* —
niet voor een mens die op producthoogte wil oriënteren.

**b. Er is geen "wacht op jou"-verzameling.** De dingen die écht alleen jij kunt beslissen —
open impediments met een product-fork, go/no-go-poorten, review-verzoeken — liggen verspreid
over kolommen (`Impediment`, gate-flags, `reviewed_card_id`). Op dit moment staan er
bijvoorbeeld twee product-beslissingen op jou te wachten in `Impediment` (repo publiek maken;
CI-billing) die functioneel niets met elkaar te maken hebben en die je alleen vindt door het
bord af te struinen. De finite, hoog-hefboom set — "de N dingen die de motor stilzetten tot
jij beslist" — is nergens als één lijst zichtbaar.

**c. De sturing zit op de verkeerde plek in de pijplijn.** De vibe-coding-pijn ontstaat als
je probeert te sturen bij de *merge* (werk is al af; je kunt alleen nog goed- of afkeuren, en
afkeuren voelt als verspilling). De hoogste hefboom van een product owner zit bij de
*intake*: welke kaarten er zijn, in welke volgorde, tegen welke productdoelen. Daar bepaal je
*wat* er gebeurt in plaats van *achteraf* te oordelen over hoe het gebeurde.

---

## 3. Rol-herkadering — "zie mij als de product owner"

Je vraagt letterlijk om als product owner gezien te worden. Dat is precies de juiste vraag,
want het legt een **taalfout in de rolverdeling** bloot. Een product owner die de *groei en
opbouw* probeert te volgen, opereert op ontwikkelaarshoogte — dat is een categoriefout die
gegarandeerd overbelast. Het expliciete contract:

| | **Jij (product owner) bezit** | **De agents bezitten** |
|---|---|---|
| **Vraag** | *Wat* & *waarom* | *Hoe* |
| Concreet | Richting, prioriteit, go/no-go op product-forks, acceptatie van "af genoeg" | Implementatie, volgorde, decompositie, engineering-kwaliteit, tests |
| Hoogte | Product-uitkomst | Kaart / code / commit |
| Ritme | Checkpoints (bv. wekelijks) | Continu, autonoom |
| Signaal dat je moet ingrijpen | Iets staat op jou te wachten (§2b), of de richting klopt niet meer | — |

**De kern van de shift:** je hoeft de stroom niet te vólgen, je moet 'm *stúren*. Volgen is
continu en put uit; sturen is periodiek en gericht. Alle drie de buildables hieronder dienen
deze ene verschuiving: ze reduceren "ik moet alles bijhouden" tot "ik grijp in op de finite
set beslissingen die van mij zijn, in mijn taal, op mijn ritme."

Dit is óók waarom "meer autonomie" en "meer grip" geen tegenpolen hoeven te zijn: hoe scherper
de laag die precies-jouw-beslissingen omhoog duwt, hoe verder de agents autonoom kunnen lopen
zónder dat jij het spoor bijster raakt.

---

## 4. De vier assen die je noemde

Je noemde vier hefbomen — *technieken, samenvattingen, taal, formulering*. Hier gemapt op wat
er moet gebeuren, met per as het onderscheid tussen "bouwen" (→ §5-kaart) en "gewoon doen"
(gewoonte/conventie).

### 4.1 Samenvattingen → een roll-up-laag (bouwen)

Het ontbrekende stuk. Twee vormen, samen kaart **A** en **B** in §5:

- **Push — wekelijkse PO-digest.** Eén automatisch gegenereerd overzicht per week dat vier
  vragen beantwoordt in producttaal: *wat is er opgeleverd?*, *welke richtingsbeslissingen
  zijn genomen?*, *wat staat er op jou te wachten?*, *is er iets van koers veranderd?* Niet
  de kaart-titels dumpen — cureren en vertalen. De infrastructuur ligt er al: dezelfde
  `scheduled_at` + auto-dispatch + chain-of-one-shots als de `market-research`-skill (zie
  [`recurring-cadence-proposal.md`](./recurring-cadence-proposal.md)). Dit is een nieuwe
  *skill + trigger-kaart*, geen nieuwe infra.
- **Pull — "wacht op jou"-wachtrij.** Een scherm/sectie die alles aggregeert dat op de mens
  geblokkeerd is: `Impediment`-kaarten met een `question`/`options`, open gates,
  review-verzoeken (`reviewed_card_id`), en `_awaiting_plan_ref`-vastzitters. Zodat de
  finite beslis-set één klik weg is i.p.v. verspreid over het bord.

### 4.2 Taal & formulering → een product-taal-conventie (deels bouwen, deels gewoonte)

Kaart **C**. De Done-`summary` en de impediment-`options` zijn nú geschreven voor de volgende
agent. Een lichte conventie draait dat om:

✅ Geïmplementeerd (kaart `4358fe0a00e342878bc7a77fd21ffebe`): conventie staat in
`docs/cockpit/kanban-conventions.md` §5 met vóór/na voorbeelden, drift-guard
`backend/tests/test_product_language_convention.py` (10 bronnen × 4 ankers), en
lockstep-updates van `move_card`/`report_impediment` MCP-docstrings +
persona-prompts (engineer/analyst/reviewer) + dispatch-mirrors.

- **Elke Done-`summary` leidt met één zin productbetekenis** vóór de engineering-details:
  *"Product owner kan nu het abonnementsverbruik zien op de Usage-pagina"* vóór *"nieuwe
  `/usage/subscription`-endpoint + `SubscriptionUsageCard.tsx`"*. De persona-prompts en de
  `move_card`-`summary`-instructie zijn de afdwing-plek.
- **Impediment-`options` als product-tradeoffs, niet implementatie-forks.** Niet *"gebruik
  APScheduler of Celery"* maar *"A: sneller nu, meer onderhoud later — B: trager nu, minder
  onderhoud"*. Jij beslist op gevolg, niet op techniek.

### 4.3 Technieken → ritme & sturen-bij-de-intake (gewoonte, geen kaart)

Geen buildable — dit is de rol-herkadering uit §3 in de praktijk:

- **Vast wekelijks checkpoint** i.p.v. continu monitoren. De digest (4.1) is de agenda ervan.
- **Stuur bij de intake, niet bij de merge.** De `intake-authoring`-skill bestaat al — dat is
  jouw hoogste-hefboom-moment. Een kaart goed scopen bij creatie is tien keer waardevoller
  dan een afgeronde kaart afkeuren.
- **Optioneel later: een levend "noord-ster"-één-pager** die jij mede-bezit, zodat losse
  kaarten tegen een richting te toetsen zijn. Bewust *geen* kaart nu — eerst de digest laten
  bewijzen welke richtingsvragen echt terugkomen, dan pas formaliseren (anders bouwen we een
  doc dat niemand bijhoudt).

---

## 5. Buildable follow-up-kaarten

Drie Backlog-kaarten, op acceptatiecriteria-niveau. Geen onderlinge `depends_on` — het zijn
drie onafhankelijke hefbomen; de digest (A) en de wachtrij (B) delen data maar geen
code-contract. Dedup-pass gedaan tegen `Backlog` + `Impediment` (2026-07-18): geen bestaande
kaart raakt PO-facing roll-up/zichtbaarheid — de `knowledge-structure`-kaarten zijn
agent-facing doc-navigatie, de Dashboard-pagina is config-inventaris.

- **Kaart A — Wekelijkse product-owner-digest** (`analysis`): eerst de *inhoud* van de digest
  ontwerpen (welke vier secties, welke bronnen, welk producttaal-register), dan de skill +
  trigger-kaart. `analysis` omdat de exacte inhoud/bronnen scoping vergen vóór een executor
  'm bouwt.
- **Kaart B — "Wacht op jou"-PO-wachtrij** (`feature`): backend-aggregatie + frontend-view die
  alle mens-geblokkeerde items op één plek toont.
  ✅ Geïmplementeerd (kaart `c7ea21b0`): `GET /api/v1/kanban/wachtrij?project_key=...`
  (`backend/app/kanban/service.py::po_wachtrij`) + `WachtrijSection` op de Projects-pagina
  (`frontend/src/features/projects/components/WachtrijSection.tsx`).
- **Kaart C — Product-taal-conventie voor Done-summaries & impediment-options** (`chore`):
  conventie-doc + persona-prompt-aanpassingen zodat samenvattingen met productbetekenis
  leiden en impediment-opties als tradeoffs zijn geformuleerd.

De volledige acceptatiecriteria staan op de kaarten zelf (aangemaakt in dezelfde sessie als
dit doc).

---

## 6. Wat dit NIET is

- **Geen nieuwe monitoring-infra.** Sessions / CC Bridge dekken "welke sessie leeft" al; dit
  gaat over *product*-zichtbaarheid, een andere as.
- **Geen "de PO moet gedisciplineerder lezen".** De hele these is het omgekeerde: het systeem
  moet minder van je lezen vragen, niet meer.
- **Geen vervanging van het beslis-register of het bord.** Die blijven de agent-facing bron
  van waarheid. De digest en de wachtrij zijn een *afgeleide, gecureerde laag* erbovenop — ze
  dupliceren geen data, ze vertalen 'm.
