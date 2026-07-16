# Beslissing: `intake_kind` nu toevoegen, of YAGNI?

**Datum:** 2026-07-14
**Status:** besloten
**Kaart:** `646f5860…`
**Uitkomst:** **Geen standalone veld nu** (geen enum zonder lezer), maar het echte gemis — de interview-/intake-authoring-flow — krijgt één `analysis`-vervolgkaart waarin `intake_kind` mét consument meekomt. ↩︎ afgesloten door `intake-authoring-flow-decision.md`.

> Kanban-kaart: **`[beslissing] Intake-kaarten: nu een optioneel intake_kind-veld
> toevoegen, of YAGNI?`** (`646f5860…`). Leaf-spike: deze doc *is* de deliverable.
> Bouwt voort op [`intake-card-routing-analysis.md`](./intake-card-routing-analysis.md)
> §5 (de open productvraag) en [`product-inceptie-pipeline.md`](./product-inceptie-pipeline.md)
> §4 (de twee-staps intake).
>
> De beslissing hieronder is genomen op basis van een **expliciet mensantwoord**
> op de impediment van deze kaart (zie §1). Alle code-claims zijn geverifieerd op
> deze branch, niet uit het geheugen.

## 1. Het mensantwoord (autoritatief)

De productvraag was binair gesteld — **A. YAGNI** (geen `intake_kind` nu) vs.
**B. nu toevoegen**. De mens antwoordde niet met A of B, maar **herformuleerde de
vraag**:

> *"Niet geheel duidelijk (Bekijk hoe dit beter kan in de toekomst), gaat dit over
> de intake van nieuwe projecten? Ik was aan het denken om mogelijks via spec-kit
> eerst een interview flow te doorlopen, maar sta zeker open voor andere
> oplossingen. Daarna moet er inderdaad wel een punt zijn waar je de intake start
> met opzet en implementatie, als het daarvoor is dan is A geen geldig argument, we
> hebben iets nodig."*

Drie signalen zitten hierin:

1. **Scope-vraag:** "gaat dit over de intake van nieuwe projecten?" → de mens leest
   *intake* als **project-intake** (een nieuwe applicatie laten ontstaan), niet als
   een los labelveld.
2. **Richting:** een **interview-flow** (mogelijk `spec-kit`, maar open voor
   alternatieven) die een gesprek omzet in opzet + implementatie.
3. **Beslis-criterium:** *als het veld dié transitie (intake → opzet + implementatie)
   dient, dan is YAGNI geen geldig argument — "we hebben iets nodig".*

## 2. Twee betekenissen van "intake" die door elkaar liepen

De kaart en het mensantwoord gebruikten "intake" voor **twee verschillende dingen**.
Ze uit elkaar trekken lost de beslissing op.

| # | Betekenis | Wat het is | Status vandaag |
|---|---|---|---|
| **(a)** | `intake_kind` als **sub-variant-tag** | Een optioneel enum-veld `brainstorm / customer-discovery / legacy-import` op een intake-kolom-kaart, bedoeld "voor toekomstige persona-routing" (doel 3 in de routing-analyse). | **Bestaat niet** (`grep intake_kind` → leeg). **Niets leest het.** |
| **(b)** | De **intake→opzet→implementatie-flow** | Een gesprek/interview (spec-kit of gelijkwaardig) → spec + plan → een nieuw project dat op het bord verschijnt. | **Deels gebouwd.** De *transitie* bestaat al; de *voordeur* ontbreekt (zie §3). |

De kaart vroeg letterlijk naar **(a)**. Het mensantwoord ("we hebben iets nodig") gaat
over **(b)**. Dát is de kern: het YAGNI-argument werd toegepast op (a), maar de mens
maakt zich zorgen over (b).

## 3. Wat van (b) al bestaat — en wat écht ontbreekt

Geverifieerd op deze branch:

- **De transitie "intake → opzet + implementatie" is al gebouwd.** De MCP/REST-actie
  `create_project_from_intake` (`backend/app/services/inception_service.py`,
  `POST /api/v1/kanban/projects/from-intake`,
  `mcp__cockpit-kanban__create_project_from_intake`) neemt een ingevulde intake-kaart
  en zaait er een nieuw project mee: map + git-init + `.claude/`-blueprint +
  projectregistratie + autodispatch-toggle + eerste Backlog-kaart met `plan_ref`.
  Vanaf daar neemt de bestaande multi-agent-flow (analyst → executors) het over.
  Dit is precies "het punt waar je de intake start met opzet en implementatie" waar
  de mens naar vroeg — **dat punt bestaat al.**
- **Wat ontbreekt is de vóórfase — de voordeur.** Er is geen flow die een *vrij
  gesprek* omzet in de ingevulde intake-kaart (de spec + plan) die
  `create_project_from_intake` als input verwacht. Dat is exact "gat A" uit
  `product-inceptie-pipeline.md` §2.3 (intake, geen artefact) en precies waar de mens
  aan `spec-kit`-interview denkt.

Met andere woorden: de mens vreest dat er "niets" is voor de intake→implementatie —
maar de achterkant (project-geboorte) staat er al; het is de **interview-/authoring-
voorkant** die nog moet komen.

## 4. Waarom `intake_kind` in isolatie tóch YAGNI is — én waarom dat hier niet de
## discussie beslecht

`intake_kind` (betekenis a) als los veld nu bouwen levert **niets** op dat de mens
vroeg:

- Niets leest het (geen persona-router, geen UI-gedrag, geen dispatch-effect — de
  intake-**kolom** dekt "niet-dispatchen" al volledig af, zie routing-analyse §1a).
- Het is geen onderdeel van de intake→implementatie-transitie; die transitie
  (`create_project_from_intake`) kent geen sub-varianten en heeft ze niet nodig.

Dus: een kaal `intake_kind`-veld toevoegen zou pure vooruit-modellering zijn — de
klassieke YAGNI-val. **Maar** de mens verwerpt terecht dat YAGNI hier het laatste
woord heeft: het echte gemis (b) mag niet onder YAGNI verdwijnen.

**De synthese die beide verzoent:** de drie sub-varianten
(`brainstorm` / `customer-discovery` / `legacy-import`) **zíjn** interview-modi. Een
brainstorm-intake, een klant-discovery-intake en een legacy-import-intake doorlopen
elk een ander gesprek. Zodra de interview-flow uit (b) bestaat, is *die flow* de
concrete lezer waar het YAGNI-argument op wachtte — dan landt `intake_kind` mét een
consument in plaats van als dood veld. `intake_kind` is dus geen aparte feature; het
is een **ontwerp-detail bínnen** de interview-flow.

## 5. Besluit

**Geen los `intake_kind`-veld nu (optie A voor betekenis a blijft staan), én het
echte gemis (b) wordt niet onder YAGNI weggeschoven: er komt één concrete
vervolgkaart voor de interview-/intake-authoring-flow, waarin `intake_kind` als
interview-modus-discriminator wordt meegenomen.**

Concreet:

1. **`intake_kind` wordt NIET als standalone kaart geïmplementeerd.** Er komt geen
   kaal enum-veld zonder lezer. (Dit is optie A voor betekenis (a) — correct, want
   het veld alleen geeft de mens niets van wat hij vroeg.)
2. **Er wordt één vervolgkaart aangemaakt — een `analysis`-kaart — voor de
   interview-/intake-authoring-flow** (betekenis b): evalueer `spec-kit` (de mens
   noemt het expliciet) tegen alternatieven, en ontwerp hoe een vrij gesprek een
   ingevulde intake-kaart (spec + plan) produceert die de bestaande
   `create_project_from_intake` als input neemt. **`intake_kind` wordt daarin
   meegenomen** als discriminator van de interview-modus — het landt dus mét een
   consument. Zie §6 voor de scope van die kaart.
3. **De intake→opzet→implementatie-transitie is al af** (`create_project_from_intake`).
   De vervolgkaart bouwt de vóórfase, niet die transitie opnieuw — ze **eindigt** waar
   `create_project_from_intake` **begint**.

Waarom een `analysis`-kaart en geen directe implementatiekaart? De mens is
uitgesproken onzeker ("Niet geheel duidelijk", "Bekijk hoe dit beter kan", "sta zeker
open voor andere oplossingen"). Spec-kit is een *kandidaat*, geen beslissing. Een
interview-flow-ontwerp met een externe-tool-keuze (spec-kit vs. de bestaande
`superpowers:brainstorming`-skill vs. iets eigens) vereist eerst scope-bepaling vóór
een executor 'm zonder context kan bouwen — dat is exact wat `work_type="analysis"`
routeert (naar de analyst-persona, met 📊-badge).

## 6. Scope van de vervolgkaart (acceptatiecriteria-niveau)

> Titel: **`[analysis][inceptie] Interview-/intake-authoring-flow: vrij gesprek →
> ingevulde intake-kaart (spec + plan)`**. `work_type=analysis`. Kolom: `Backlog`.

De analyst die deze kaart oppakt levert een decision-doc + kind-kaarten die dekken:

- **Tool-keuze:** evalueer `spec-kit` (mens-genoemd) tegen de bestaande
  `superpowers:brainstorming` + `superpowers:writing-plans`-skills (die al een
  gesprek → design → plan doen, zie inceptie-pipeline §3.1) en tegen een eigen
  minimale flow. Beargumenteer één keuze; documenteer de trade-offs. Open vraag,
  geen vooraf-vastgelegde uitkomst.
- **Output-contract:** de flow eindigt met een intake-kolom-kaart die een `spec`- én
  `plan`-deliverable draagt, precies in de vorm die `create_project_from_intake`
  vandaag als input verwacht (verifieer die vorm tegen `inception_service.py`). De
  flow **hergebruikt** die actie; hij herbouwt de project-geboorte niet.
- **Mens-in-de-lus:** brainstorming/interview heeft harde approval-gates (inceptie-
  pipeline §2.2 punt 6). Beslis: draait de flow interactief (buiten de autonome
  dispatcher) of via `report_impediment`-gates? Dit hangt samen met de bestaande
  follow-up "Hoe vertalen we brainstorming-user-approval naar
  `report_impediment`-flows?" (inceptie-pipeline §7 punt 7) — dedupe daartegen.
- **`intake_kind` als interview-modus:** neem het optionele veld
  (`brainstorm / customer-discovery / legacy-import`) mee **als de flow onderscheid
  tussen interview-modi nodig heeft** — dan volgt de backend/frontend-spec uit
  `intake-card-routing-analysis.md` §4.1 (optioneel nullable veld, alleen zinvol op
  intake-kolom-kaart, `WORK_TYPES` ongemoeid). Blijkt tijdens de analyse dat de flow
  geen modus-onderscheid nodig heeft, dan vervalt `intake_kind` definitief — en dan
  was YAGNI met terugwerkende kracht correct. Zó wordt de beslissing empirisch, niet
  op voorhand geraden.

## 7. Wat expliciet NIET verandert door dit besluit

- **`WORK_TYPES` / persona-routing** blijft vierwaardig (`analysis/feature/bug/chore`).
  De afwijzing van `work_type="intake"` uit `intake-card-routing-analysis.md` §2 blijft
  staan.
- **De intake-kolom** blijft de source-of-truth voor "niet auto-dispatchen"
  (routing-analyse §1a — al werkend, geen actie).
- **`create_project_from_intake`** blijft ongewijzigd de canonieke project-geboorte.
- **Geen `intake_kind`-migratie/schemaverandering** nu — die valt (indien nodig)
  binnen de vervolgkaart, niet erbuiten.
