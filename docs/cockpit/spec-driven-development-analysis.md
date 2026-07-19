---
title: "Spec-driven development als single source of truth — analyse"
type: analysis
status: active
---

# Spec-driven development als single source of truth — analyse

> Kanban-kaart: **"Analyse - spec driven development as single source of truth"**
> Vraag: *"Om consistentie van de applicatie te garanderen zou iedere functionele
> wijziging moeten voortvloeien uit een spec-plan en uitgevoerd worden via een
> implementatieplan. Iedere verdere wijziging die impact heeft op een functionaliteit
> zou die spec dan moeten updaten. Enzovoort. Analyseer grondig: is dit wenselijk,
> haalbaar? Wees kritisch. Indien positief: schrijf een implementatieplan hoe we deze
> loop kunnen garanderen."*
>
> Dit is een analyse-kaart: DoD is een beslisdocument met een concrete aanbeveling +
> een gefaseerd implementatieplan (het **wat** en **waarom**; het **hoe** is voor de
> executor). Geen feature-code in deze kaart.

## 1. Wat wordt er precies voorgesteld?

De kern is een **gesloten lus** tussen spec en code:

1. Elke **functionele** wijziging vloeit voort uit een **spec-plan** (het *wat/waarom*).
2. De uitvoering gebeurt via een **implementatieplan** (het *hoe*).
3. Elke latere wijziging die een functionaliteit raakt, **werkt de bijbehorende spec
   bij** — zodat de spec op elk moment de waarheid over het gedrag is.
4. De spec is daarmee de **single source of truth** (SSOT): code volgt spec, niet
   andersom.

Het is nuttig om twee sterktes te onderscheiden, want ze hebben totaal verschillende
kosten:

- **Sterke vorm (letterlijk):** de lus is **verplicht en mechanisch afgedwongen**. Geen
  functionele diff mag mergen zonder spec-referentie; geen spec-rakende diff mag mergen
  zonder spec-update. Een gate blokkeert overtredingen.
- **Zwakke/pragmatische vorm:** spec-first is de **default** voor functioneel werk, met
  de kanban-kaart + het gecommitte doc als SSOT-eenheid, en **drift-detectie (signaal),
  geen harde preventie** voor de prozalaag. Tests + OpenAPI-snapshot blijven de
  mechanisch-afgedwongen *uitvoerbare* spec-vloer.

Deze analyse beoordeelt beide en beveelt de pragmatische vorm aan.

## 2. Geverifieerde stand van zaken (code + docs)

Alle punten hieronder zijn geverifieerd in de repo, niet uit het geheugen:

1. **Er zijn vandaag al drie parallelle spec-/plan-bomen — SSOT is structureel nog
   níet bereikt.** `docs/00-orientation.md:82-95` benoemt letterlijk: `docs/cockpit/`
   (canoniek, "bron van waarheid voor hoe de fork vandaag werkt"),
   `docs/superpowers/{plans,specs}/` (16 plans + 19 specs; taak-specifieke werkoutput),
   en `docs/plans/` (legacy claude-deck, "niet meer gebruiken"). De oriëntatie
   waarschuwt expliciet voor een **"tweede waarheid"**. Een *single* source of truth
   afdwingen terwijl er drie bomen concurreren is een contradictie: **consolidatie is
   een voorwaarde**, geen detail.

2. **De "Plans"-feature is een bestandsbrowser, geen spec-motor.**
   `backend/app/services/plan_service.py` doet CRUD + zoeken over markdown in
   `~/.claude/plans/` en linkt sessies via een `slug`-veld in JSONL
   (`get_plan_sessions`, `:294`). Er is **geen koppeling naar de code** die een plan
   beschrijft en **geen driftdetectie**. Als SSOT-infrastructuur is dit vandaag leeg.

3. **De kanban-laag produceert al per-taak "specs".** De analyst-persona splitst een
   kaart en schrijft via `add_plan_attachment` een plan-markdown + dep-graph; de kaart
   draagt zelf `title` + `description` (`backend/app/kanban/models.py:38-39`,
   `KanbanCard`). De multi-agent-flow (`docs/cockpit/multi-agent-kanban.md`) is dus **de
   facto al een "spec → implementatieplan → executor"-pijplijn** — alleen niet
   geformaliseerd als SSOT en niet gekoppeld aan een langlevend doc.

4. **Er bestaat al één werkende, mechanische spec-drift-gate.** CI (`.github/workflows/
   quality.yml`) draait `python scripts/check_openapi_snapshot.py`: dit diff't het live
   FastAPI-OpenAPI-contract tegen een gecommitte `backend/openapi.snapshot.json` en
   **faalt de build** als de API-vorm wijzigt zonder dat de snapshot is bijgewerkt
   (`backend/scripts/check_openapi_snapshot.py:40-49`). Dit is het bewijs-in-huis dat
   spec-als-artefact-in-CI werkt — **én** het toont meteen het kostenmodel: het werkt
   alleen omdat het contract *machinaal afleidbaar* is uit de code.

5. **Tests zijn de bestaande uitvoerbare spec.** `quality.yml` dwingt `pytest`
   (backend) en `npm run test:coverage` + `build` (frontend) af. Een test pint gedrag
   vast op een manier die CI kan controleren — dat is de enige spec-vorm die vandaag
   **mechanisch** wordt gegarandeerd.

6. **Tussen een `docs/cockpit/`-prozaspec en de code die het beschrijft bestaat geen
   enkele link.** Niets detecteert dat een functionele diff een spec veroudert. De
   consistentie leunt vandaag volledig op menselijke/agent-discipline.

**Kernconclusie van de nulmeting:** het idee is voor ~40% al gerealiseerd (kanban
plan-attachments + OpenAPI-snapshot + tests), maar de *prozalaag* (`docs/cockpit/`) is
losgekoppeld van de code én versnipperd over drie bomen. De vraag is dus niet "vanaf
nul een spec-systeem bouwen", maar "de bestaande bouwstenen tot een gesloten lus
smeden — en hoe hard we die lus afdwingen".

## 3. Is het wenselijk?

### Argumenten vóór

- **Sluit direct aan bij de platform-doelstelling.** CLAUDE.md eist "reproduceerbaar,
  controleerbaar, auditbaar" en "continue zelfverbetering". Een durende spec is precies
  de audit-trail + het geheugen dat dat mogelijk maakt.
- **Sessies zijn efemeer; specs zijn dat niet.** Dit is het sterkste argument in *deze*
  context. Autonome multi-agent-sessies sterven en verliezen hun redenering. Een
  gecommitte spec is het **institutionele geheugen** waaruit een verse sessie context
  herwint zonder het oude transcript. Zonder durende spec herontdekt elke executor het
  wiel (precies het "genoeg context"-gat uit `reopen-completed-decision-analysis.md`).
- **Consistentie bij parallelle agents.** Meerdere gedispatchte sessies die tegelijk aan
  overlappende functionaliteit werken hebben een gedeeld referentiepunt nodig; anders
  divergeren ze.

### Argumenten tegen (kritisch)

- **Spec-rot maakt de sterke vorm gevaarlijker dan geen spec.** Een SSOT die drift is
  *actief misleidend*: autonome agents behandelen 'm als waarheid en bouwen op verouderde
  aannames. De helft "werk de spec bij bij elke rakende wijziging" is de saaie,
  ondankbare helft die **altijd het eerst rot**. Een spec-systeem is precies zo goed als
  de zwakste update-discipline.
- **Snelheidsbelasting op klein werk.** Veel diffs zijn bugfixes, refactors, tuning
  waar een verplichte spec-vooraf pure overhead is. Voor een snel bewegende solo-fork
  kan een harde spec-gate de doorlooptijd meer schaden dan de consistentie helpt.
- **Prozaspec ↔ code is niet mechanisch verifieerbaar.** Je kúnt niet bewijzen dat een
  markdown-spec de code beschrijft, zonder óf een uitvoerbare spec (tests / snapshot,
  zie punt 4-5) óf een LLM-jury (probabilistisch, kost tokens, is te "gamen"). Een harde
  gate die alleen checkt dát er een spec-link ís — niet of de inhoud *klopt* — levert
  **theater**: groene vinkjes zonder garantie.
- **"Functionele wijziging" is niet scherp te definiëren.** Waar ligt de grens tussen
  een refactor en een gedragsverandering? Elke gate die op die grens staat, produceert
  false positives (frictie) of false negatives (ontsnappingen).

### Oordeel wenselijkheid

**Het dóel (specs als durende SSOT, gekoppeld aan code) is wenselijk en past bij het
platform. De letterlijke sterke vorm (verplichte, mechanisch afgedwongen lus op elke
wijziging) is dat níet — te duur, te rot-gevoelig, en deels theater.** De waarde zit in
de durende spec + drift-*signaal*, niet in een blokkerende gate op de prozalaag.

## 4. Is het haalbaar? — de crux: "de lus garanderen"

"Garanderen" is het moeilijke woord. Je kunt een prozaspec-↔-code-correspondentie niet
hard garanderen. De reële opties, van goedkoop/zwak naar duur/sterk:

| Optie | Wat het garandeert | Kosten / risico |
|---|---|---|
| **A. Link-gate** — commit/PR die functionele paden raakt moet een spec-doc refereren (git-trailer of CI-check) | Dat er een *link* bestaat | Goedkoop te bouwen, maar bewijst niets over inhoud → **theater-risico** |
| **B. Uitvoerbare spec** — tests + OpenAPI-snapshot (bestaat al) | Gedrag ↔ spec voor de *gedekte* oppervlakte, mechanisch | Sterkste, goedkoopste échte garantie; dekt alleen wat getest/getypeerd is |
| **C. Drift-signaal** — CI/agent flag't een functionele diff waarvan de gelinkte spec niet is aangeraakt; routeert naar een "spec-update"-kaart | Aandacht op waarschijnlijke drift (advies, geen blok) | Middelmatige kosten; false positives; **verplettert de velocity niet** |
| **D. LLM-spec-review** — agent vergelijkt diff met gelinkte spec en meldt divergentie | Probabilistische prozadekking | Tokens per wijziging, te gamen, vereist menselijke adjudicatie |

De haalbaarheids-conclusie: **de sterke, mechanische garantie bestaat alleen voor de
uitvoerbare laag (B) — en die is er al.** Voor de prozalaag is het maximaal haalbare een
**signaal (C)**, eventueel later verrijkt met een LLM-review (D). Optie A alleen is
theater. Dit stuurt de aanbeveling rechtstreeks.

## 5. Aanbeveling

**Positief, maar in de pragmatische vorm en gefaseerd — niet de maximalistische harde
gate.** Concreet, in volgorde van fundament naar franje:

- **Fase 0 — Consolideer naar één canonieke spec-boom.** Zolang drie bomen concurreren
  is "single source of truth" een leugen. Maak `docs/cockpit/` expliciet canoniek,
  demoteer/archiveer `docs/plans/` (legacy) en definieer `docs/superpowers/` scherp als
  *werkoutput die naar `docs/cockpit/` promoot zodra werk landt* (de oriëntatie zegt dit
  al — maak het afdwingbaar/zichtbaar). Zonder Fase 0 heeft de rest geen anker.
  *Raakvlak:* de bestaande Backlog-kaart **"Docs-sweep + consistentiecontrole
  terminologie"** overlapt en kan hierin opgaan of eraan voorafgaan.
- **Fase 1 — Maak de kanban-kaart de spec-eenheid en link kaarten aan hun spec-doc.**
  De analyst schrijft al plan-attachments; formaliseer dat het plan-attachment /
  beslisdoc **de** spec is, en geef elke functionele kaart een expliciete link naar het
  canonieke `docs/cockpit/`-doc dat ze implementeert of bijwerkt (hergebruik het
  bestaande `deliverable`-mechanisme met `kind="link"`, of een `meta`-veld — geen nieuw
  datamodel). Dit is het fundament: zonder link-tussen-kaart-en-spec is driftdetectie
  onmogelijk.
- **Fase 2 — Drift-signaal (Optie C), geen harde gate.** Een lichte check (in CI of een
  scheduled-agent) die functionele diffs flag't waarvan de gelinkte spec niet mee-
  wijzigde, en dat routeert naar een `[spec-update]`-Backlog-kaart. Leun voor het
  mechanische deel op het bewezen **OpenAPI-snapshot-patroon** (punt 4). Advies, niet
  blokkerend — behoud velocity.
- **Behoud de uitvoerbare-spec-vloer (Optie B) as-is en versterk 'm marginaal.** Tests +
  OpenAPI-snapshot blijven de enige *harde* garantie; dat is een feature, geen tekort.
- **Later/optioneel — Fase 3: LLM-spec-consistentie-review (Optie D)** op PR's voor de
  prozalaag, menselijk geadjudiceerd. Alleen bouwen als Fase 1-2 aantoonbaar waarde
  leveren.

**Waarom niet de sterke vorm nu:** een verplichte, blokkerende spec-gate op elke
functionele wijziging koopt schijn-consistentie (link-theater) ten koste van velocity en
introduceert een rot-gevoelige SSOT die autonome agents actief misleidt. Dezelfde afweging
als in `reviewer-agent-decision.md`: geen parallel zwaar systeem bouwen zonder bewezen
baat; combineer bestaande bouwstenen (kanban plan-attachments + OpenAPI-snapshot + tests +
git) tot een lus met een *signaal*, en escaleer pas naar afdwinging als de data zegt dat
drift een reëel, terugkerend probleem is.

## 6. Implementatieplan — hoe we de lus (pragmatisch) garanderen

> Richting, geen dwingend recept. Elke fase = een eigen dispatchbare kaart; Fase N+1
> hangt af van Fase N (`depends_on`). De executor bevestigt of weerlegt de aannames.

**Fase 0 — Consolidatie (voorwaarde)**
- *Wat:* één canonieke boom (`docs/cockpit/`); `docs/plans/` gearchiveerd/gemarkeerd als
  legacy; `docs/superpowers/` expliciet "werkoutput → promoot bij landing". Een korte
  `docs/cockpit/README.md` of index die zegt welk doc leidend is per feature.
- *Waarom:* "single" kan niet met drie concurrerende bomen. Fundament voor elke link.

**Fase 1 — Spec-link op kaarten (fundament)**
- *Wat:* elke functionele kaart draagt een verwijzing naar het canonieke spec-doc dat
  ze implementeert/bijwerkt (via `deliverable kind="link"` of `meta`). Analyst-output
  (plan-attachment/beslisdoc) is per definitie de spec. UI toont de spec-link op de
  kaart.
- *Waarom:* zonder machinaal leesbare kaart→spec-link is driftdetectie onmogelijk.
  *Aanname (executor bevestigt):* hergebruik `deliverable`/`meta` i.p.v. nieuw veld.
- *Uitgevoerd (Fase 1):* **aanname bevestigd** — de bestaande `metadata`-bag volstaat,
  geen nieuw kolom-veld nodig. De canonieke kaart→spec-link woont onder
  `card.metadata["spec_doc"]` (repo-relatief pad naar een `docs/cockpit/`-doc, of een
  URL). De sleutel is als één SSOT-constante vastgelegd: `SPEC_DOC_META_KEY` in
  `backend/app/kanban/schemas.py` en gespiegeld in `frontend/src/features/kanban/types.ts`
  — dit is het anker dat Fase 2 machinaal leest. Een analyst-plan-attachment
  (`plan`/`plan_ref`-deliverable) geldt per definitie als de spec, dus zulke kaarten
  hebben geen expliciete link nodig. De `CardDrawer` toont de link (of "Plan-attachment
  geldt als de spec") en laat een mens hem inline zetten/wijzigen.

**Fase 2 — Drift-signaal (hangt af van Fase 1)**
- *Wat:* een check die een functionele diff flag't waarvan de gelinkte spec niet mee-
  wijzigde en een `[spec-update]`-Backlog-kaart aanmaakt. Mechanisch deel gemodelleerd
  naar `check_openapi_snapshot.py`. Advies, niet blokkerend.
- *Waarom:* dit is de "iedere verdere wijziging werkt de spec bij"-helft — maar als
  detecteerbaar signaal i.p.v. rot-gevoelige belofte.
- *Uitgevoerd (Fase 2):* **aanname bevestigd** — de bestaande
  `scripts.drift_checks`-helper-laag volstaat. Pure helpers (`SpecDriftFinding`,
  `parse_diff_path_list`, `find_spec_drift_for_card`) zitten in
  `backend/scripts/drift_checks.py`; CLI-wrapper
  `backend/scripts/check_spec_drift.py` produceert het signaal-samenvatting
  (model: `check_features_docs.py`); companion
  `backend/scripts/file_spec_drift_cards.py` POST één `[spec-update]`-Backlog-kaart
  per bevinding via de kanban REST API (idempotent op titel; `--dry-run` voor
  pre-flight inspectie). De wekelijkse cron in `.github/workflows/drift-report.yml`
  draait de detector signal-only; de card-filer is een aparte mens-getriggerde
  stap omdat de workflow geen kanban-credentials heeft. De "functioneel"-heuristiek
  is bewust grof: pad-prefixes `backend/app/`, `frontend/src/features/`,
  `frontend/src/lib/`; false positives worden geaccepteerd als signaal — precies
  de afspraak in §3 + §4.

**Optioneel — Fase 3: LLM-prozareview**
- Alleen na aangetoonde waarde van 1-2. Buiten scope van de eerste follow-ups.

## 7. Risico's & aandachtspunten (voor de executor)

- **Spec-rot is de hoofdvijand.** Ontwerp elke fase zo dat een verouderde spec
  *zichtbaar* wordt (signaal/kaart), niet stilletjes waarheid claimt.
- **Vermijd theater.** Een gate die alleen "is er een link?" checkt, geeft valse
  zekerheid. Liever een eerlijk *advies-signaal* dan een harde gate die niets over
  inhoud garandeert.
- **Definieer "functioneel" pragmatisch.** Begin met een grove heuristiek (paden/labels)
  en accepteer false positives als advies; ga niet zoeken naar een perfecte grens.
- **Bouw op wat er is.** Kanban plan-attachments, `deliverable`, OpenAPI-snapshot en
  tests bestaan al. Geen parallel spec-datamodel introduceren voordat het bewezen nodig
  is.
- **Menselijke go/no-go op de richting.** Dit is een proces-/strategiebeslissing voor
  het hele platform. De follow-up-kaarten wachten op een expliciete go van de gebruiker
  op deze richting voordat uitvoering start (vergelijk de bestaande go/no-go-kaart voor
  repo-zichtbaarheid).

## 8. Wat deze kaart oplevert

Dit beslisdocument + drie geketende follow-up-kaarten in `Backlog`
(`[spec-ssot] Fase 0/1/2`) die naar dit document verwijzen en via `depends_on` in
volgorde staan. Geen feature-code in deze kaart (analyse-DoD).
