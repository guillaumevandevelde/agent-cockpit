---
title: "Product-analyses uniformeren — template, skill of eigen agent?"
type: decision
status: decided
---

# Product-analyses uniformeren — template, skill of eigen agent?

**Datum:** 2026-07-22
**Status:** besloten
**Kaart:** `8394f725…`
**Uitkomst:** **Een skill (`product-analysis`), geen aparte agent — en het sjabloon leeft ín die skill.** De vier ad-hoc product-analyses tot nu toe (`openhands-analyse.md` 2026-07-13, `jira-lessen-analyse.md` 2026-07-14, `9router-integratie-analyse.md` 2026-07-19, `lemma-platform-analyse.md` 2026-07-21) zijn allemaal door de **bestaande** analyst-persona in modus 2 geproduceerd en convergeerden vanzelf al op dezelfde 8–11 H2-structuur — wat ontbrak was niet een rol maar een **procedure**. Wat tussen die runs wél dreef, waren precies de dingen die een sjabloon niet vastlegt: `openhands-analyse.md` §7 zette de vervolgtaken als prozalijst neer ("niet in deze kaart aangemaakt") terwijl `lemma-platform-analyse.md` §7 ze als echte kind-kaarten aanmaakte — exact de faalklasse die `analyse-orphaned-followups-audit.md` al documenteerde als "analyses die tot niets geleid hebben". Een eigen persona is mechanisch goedkoop (`_persona_for_card`, `dispatch.py:1187-1198`, leest `.claude/agents/<card.agent>.md` — nul backend-wijziging) maar **onbereikbaar zonder handwerk**: `WORK_TYPES` ligt vast op `analysis` / `feature` / `bug` / `chore` (`schemas.py:35`), dus een product-analyse-kaart blijft op `work_type` = `analysis` en zou per kaart een handmatig `agent`-veld vereisen, terwijl een skill zichzelf herkent aan de kaarttekst die de gebruiker tóch al schrijft ("Product analyse - <url>"). Bovendien zou een tweede persona **189 van de 383 regels** van `analyst.md` dupliceren die niets met product-analyse te maken hebben (outcome-contract, product-taal, worktree-scope, projectconventies) en die in sync moeten blijven met de Done-poort in `mcp_server.py`.

> **Type:** beslisdoc (analyst leaf-spike, modus 2). Bron-kaart: *"Product analyse template"*
> (`8394f725355b4fccbe4d1233df705ce6`).
>
> Verwant: [`analyse-orphaned-followups-audit.md`](./analyse-orphaned-followups-audit.md),
> [`analysis-outcome-contract-decision.md`](./analysis-outcome-contract-decision.md),
> [`sync-vs-async-delegation-decision.md`](./sync-vs-async-delegation-decision.md).

---

## 1. Wat de kaart vroeg

> "Regelmatig vraag ik je om een toepassing te vergelijken met deze toepassing om er zaken
> uit te leren en nieuwe functionaliteiten te ontdekken. Ik geef je dan een url mee en vraag
> dan te leren uit de applicatie met opvolg taken of analyses.
>
> Idealiter kunnen we dit meer uniform doen, kan via een template of een agent specifiek voor
> product analyses, aangevuld met mogelijks wenselijke skills. Bekijk wat de voorkeur heeft."

Drie kandidaten, één keuze gevraagd: **template**, **eigen agent**, of **skill(s)**.

## 2. Wat er vandaag al gebeurt (gemeten, 2026-07-22)

De loop draaide al vier keer, volledig ad hoc:

| Doc | Datum | Regels | H2's | Vervolgkaarten |
|---|---|---|---|---|
| [`openhands-analyse.md`](./openhands-analyse.md) | 2026-07-13 | 285 | 10 | **proza** — §7 "voorgesteld; niet in deze kaart aangemaakt" |
| [`jira-lessen-analyse.md`](./jira-lessen-analyse.md) | 2026-07-14 | 235 | 8 | **proza** — §6 "voor de mens om als kaarten in te plannen" |
| [`9router-integratie-analyse.md`](./9router-integratie-analyse.md) | 2026-07-19 | 410 | 11 | §9 vervolgkaarten + register-rij |
| [`lemma-platform-analyse.md`](./lemma-platform-analyse.md) | 2026-07-21 | 370 | 10 | **kaarten** — §7 "in deze sessie aangemaakt" |

Drie observaties, en ze sturen de beslissing:

**(a) De vorm was al bijna uniform.** Openhands en Lemma delen letterlijk dezelfde
sectienamen (`TL;DR`, *"Wat is X"*, *"Wat we concreet kunnen overnemen (gerangschikt op
leverage)"*, *"Wat we bewust NIET overnemen"*, *"Aanbeveling"*, *"Vervolgkaarten"*,
*"Bewust buiten scope"*, *"Bronnen"*). Een sjabloon codificeert dus iets wat de agent
al vanzelf reproduceerde — het lost het goedkoopste deel van het probleem op.

**(b) Wat wél dreef, is gedrag, geen lay-out.** Twee van de vier runs lieten de
vervolgtaken als prozalijst achter. Dat is niet een ontbrekende sectie — beide docs
*hébben* die sectie — maar een ontbrekende procedurestap ("maak ze aan als kind-kaarten,
hang er een `plan_ref` aan"). Het is exact de faalklasse uit
[`analyse-orphaned-followups-audit.md`](./analyse-orphaned-followups-audit.md), waar de
gebruiker al eerder aankaartte dat analyses "tot niets geleid hebben".

**(c) De duurste fout zat in de premisse, niet in de structuur.** In alle drie de
externe-productanalyses was de premisse in de kaart onjuist of half onjuist, en het
*corrigeren* ervan was de waardevolste output: 9Router *"lijkt matuurder"* bleek een
categoriefout (inference-router per request vs. onze spawn-configurator per sessie);
Lemma *"doet grotendeels hetzelfde maar matuurder"* kreeg een eigen weerleggingssectie
(§2); OpenHands *"kan niet overweg met abonnementen"* moest herzien worden (§3). Ook dat
is procedure ("toets de premisse voor je vergelijkt"), niet lay-out.

**(d) De kaartvorm bestaat al de facto.** Backlog-kaart `87b99d2d…` heet
*"Product analyse - https://github.com/donkruger/Kanban"* — titel + URL, geen
beschrijving. De gebruiker schrijft die vorm al; het aangrijpingspunt voor herkenning
is er dus, zonder dat er iets aan de kaart-UI hoeft te veranderen.

## 3. De drie kandidaten, met hun werkelijke kosten

| | **Template** | **Eigen agent/persona** | **Skill** |
|---|---|---|---|
| Legt vast | Output-vorm | Rol + output-vorm + procedure | Procedure + output-vorm (sjabloon zit erin) |
| Backend-wijziging | nee | **nee** (`_persona_for_card` leest `.claude/agents/<agent>.md`, `dispatch.py:1187-1198`) | nee |
| Hoe wordt het bereikt? | agent moet 't zelf gaan zoeken | **`card.agent` handmatig per kaart** — `WORK_TYPES` ligt vast (`schemas.py:35`), dus er bestaat geen `work_type='product-analysis'`; en de project-brede `work_type→persona`-mapping ombuigen zou **álle** analyse-kaarten omleiden | skill-`description` matcht de kaarttekst die de gebruiker toch al schrijft |
| Duplicatie | geen | **189 van 383 regels** van `analyst.md` (modus-2-contract r27–114, product-taal + outcome-contract r186–243, projectconventies + worktree-scope r341–383) | geen |
| Dekt faalklasse (b) proza-follow-ups | nee | ja | ja |
| Dekt faalklasse (c) ongetoetste premisse | nee | ja | ja |
| Precedent in deze repo | — | 3 persona's (`analyst`/`engineer`/`reviewer`), alle drie **fase**-rollen | 10 skills, waaronder `market-research` dat de *sweep*-variant van precies deze loop al dekt |

Twee dingen springen eruit.

**Het sjabloon is geen alternatief maar een onderdeel.** Het lost (a) op — het deel dat al
werkte — en niets van (b) of (c). Als los bestand in `docs/cockpit/` zou het bovendien de
doc-index en de frontmatter-check vervuilen (`type`-enum kent geen "template"). Het hoort
dus ín de skill, zoals `market-research` zijn kaart-sjabloon ín de skill heeft staan.

**De persona faalt op bereikbaarheid, niet op prijs.** Mechanisch is 'm toevoegen gratis:
`_persona_for_card` probeert eerst `.claude/agents/<card.agent>.md`. Maar er is geen
routeringspad dat 'm automatisch kiest — de enige knop is het `agent`-veld per kaart, en
dat is precies het handwerk dat de kaart wil wegnemen. Daar bovenop komt de duplicatie:
de helft van `analyst.md` is rol-generiek en zou meelopen in een tweede bestand dat in
sync moet blijven met de outcome-poort in `mcp_server.py` — dezelfde driftval die
`git-ship`/`dispatch.py` al kent.

Een skill heeft die twee problemen niet: hij hangt aan de *taak*, niet aan de rol, en de
bestaande persona (analyst modus 2) blijft de enige plek waar het rolcontract staat.

## 4. Beslissing

**Skill `product-analysis`, met het sjabloon erin. Geen nieuwe persona. Geen los
template-bestand.**

Concreet geleverd in deze kaart:

1. **`.claude/skills/product-analysis/SKILL.md`** — de procedure in acht stappen: scope
   pinnen + premisse woordelijk citeren → hún feiten gronden (met datum + sha; inclusief
   de `default_branch`- en zsh-quoting-vallen) → **ónze** kant gronden met `file:line` in
   plaats van uit het geheugen → vergelijken op *laag* i.p.v. op *label* → filteren op
   leverage met een verplichte "wat we bewust NIET overnemen"-sectie → doc schrijven →
   (bij een go/no-go) register-rij + vier-veld-header → dedupe + kind-kaarten +
   **altijd** `add_plan_attachment` → `Done` met een `outcome`.
2. **`.claude/skills/product-analysis/templates/analyse-doc.md`** — het doc-skelet,
   afgeleid uit de vier bestaande docs, met de frontmatter-keuze (`analysis` vs
   `decision`) expliciet.
3. **Een verwijzing in `.claude/agents/analyst.md` (modus 2)**, zodat de dispatch-prompt
   zelf naar de skill wijst en herkenning niet alleen van de skill-`description` afhangt.
   De Python-fallback `ANALYST_PROMPT` (`backend/app/kanban/analyst_prompt.py`) is
   **geen** woordelijke spiegel maar een verkorte variant (9.971 vs. 22.432 tekens,
   gemeten) en geldt alleen voor projecten zónder eigen `analyst.md` — daar bestaat de
   skill toch niet. Er is dus geen mirror-verplichting zoals bij `git-ship`/`dispatch.py`.

De twee harde gedragsregels bovenaan de skill zijn de vertaling van faalklassen (b) en
(c): *de premisse van de gebruiker is een hypothese* en *vergelijk op laag, niet op
label*.

## 5. Wat we bewust NIET doen

- **Geen `product-analyst`-persona.** Zie §3. Heropenen als het gedrag écht rol-niveau
  wordt — zie §6.
- **Geen nieuwe `work_type`.** `WORK_TYPES` is een gesloten enum met een uitgewerkte
  routerings-semantiek ([`work-type-routing-analysis.md`](./work-type-routing-analysis.md));
  een vijfde waarde toevoegen raakt de mapping-tabel, de UI-filters en de outcome-poort
  voor precies nul routeringswinst. `analysis` klopt gewoon.
- **Geen los sjabloonbestand in `docs/cockpit/`.** De `type`-enum van
  `check-doc-frontmatter.sh` kent geen "template", en `generate-doc-index.py` zou 'm als
  echt document indexeren.
- **Geen automatische kaart-detectie in de backend** (bv. "titel begint met *Product
  analyse*" → forceer skill). Herkenning op vrije tekst in de dispatcher is een nieuwe,
  stille faalmodus; de skill-`description` plus de persona-verwijzing dekken hetzelfde
  zonder code.
- **`market-research` blijft ongewijzigd.** Die skill disclaimt de enkel-doel-variant al
  expliciet ("that's a single-card plan, not a sweep") — dat gat is nu gevuld in plaats
  van dat de sweep-skill wordt opgerekt.

## 6. Heropenen wanneer?

- De skill accreteert gedrag dat écht op rolniveau zit — een afwijkende ship-workflow, een
  ander model, of een eigen outcome-contract. Dán is een persona het juiste niveau, en de
  skill-tekst is dan al de body ervan.
- De dispatcher krijgt een routeringssignaal fijner dan `work_type` (bv. labels of
  `metadata.kind` als routeringsinvoer). Dan verdwijnt het bereikbaarheidsbezwaar tegen
  een eigen persona uit §3.
- Twee opeenvolgende product-analyses draaien mét de skill beschikbaar en negeren 'm
  alsnog. Dan is herkenning het probleem, niet de vorm — en verschuift de oplossing naar
  de dispatch-prompt (of naar de kaartvorm, zie vervolgkaart in §7).

## 7. Vervolgkaarten (in deze sessie aangemaakt)

Beide zijn kind-kaarten van `8394f725…` en onderling onafhankelijk (`depends_on_graph`
leeg; wél allebei een `plan_ref` via de plan-attachment).

1. `bc6b266c…` — **Kaartvorm voor product-analyses in de `intake-authoring`-skill.** De
   gebruiker levert vandaag titel + URL en verder niets (`87b99d2d…`), waardoor de
   premisse, de focusvragen en de gewenste diepgang elke keer geraden of nagevraagd
   moeten worden. De skill kan de premisse alleen toetsen als die er staat.
   ✅ Geïmplementeerd (kaart `bc6b266c…`): `.claude/skills/product-analysis-card/SKILL.md`
   (oorspronkelijk in `intake-authoring`; uitgesplitst toen die skill `new-app` werd)
   heeft een tweede, vooruitkijkende vorm — titel `Product analyse - <naam of URL>`,
   `Backlog` (geen promote), `work_type="analysis"`, en vier vaste beschrijvings-velden
   (`URL/product` / `Premisse/aanleiding` / `Focusvragen` met `geen — gebruik de
   standaard` als escape / `Diepgang`). De `product-analysis`-skill leest diezelfde
   labels 1-op-1 in stap 1 en behoudt zijn bestaande bare-title-default voor legacy
   kaarten (`87b99d2d…` blijft ongewijzigd).
2. `d5072884…` — **Canonieke capability-baseline van Cockpit.** Stap 3 van de skill
   ("grond ónze kant") wordt nu in elke analyse opnieuw uitgeschreven
   (`openhands-analyse.md` §2, `lemma-platform-analyse.md` §1/§3,
   `9router-integratie-analyse.md` §4). Eén onderhouden basislijndoc maakt die stap
   goedkoper en houdt de uitspraken over ons eigen product consistent tussen analyses.
   ✅ Geïmplementeerd (kaart `d5072884…`):
   [`cockpit-capability-baseline.md`](./cockpit-capability-baseline.md) — 8
   capability-gebieden met `file:line` per claim, meetdatum + commit-sha bovenaan;
   stap 3 van de skill wijst er nu naar als startpunt-met-herverificatieplicht.

## 8. Bronnen

- Kaart `8394f725355b4fccbe4d1233df705ce6` — *"Product analyse template"*.
- De vier bestaande analyses: [`openhands-analyse.md`](./openhands-analyse.md),
  [`jira-lessen-analyse.md`](./jira-lessen-analyse.md),
  [`9router-integratie-analyse.md`](./9router-integratie-analyse.md),
  [`lemma-platform-analyse.md`](./lemma-platform-analyse.md).
- Code, gemeten 2026-07-22: `backend/app/kanban/dispatch.py:1187-1198`
  (`_persona_for_card`), `backend/app/kanban/schemas.py:35` (`WORK_TYPES`),
  `backend/app/kanban/schemas.py:43-48` (`WORK_TYPE_PERSONA_DEFAULTS`),
  `backend/app/kanban/analyst_prompt.py` (verkorte fallback).
- Bestaande skills als vormprecedent: `.claude/skills/market-research/SKILL.md`,
  `.claude/skills/flag-problem/SKILL.md`.
