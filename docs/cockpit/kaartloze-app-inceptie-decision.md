---
title: "Kaartloze app-inceptie: interview → geboorte, zonder intake-kaart"
type: decision
status: decided
---

# Kaartloze app-inceptie: interview → geboorte, zonder intake-kaart

**Datum:** 2026-07-29
**Status:** besloten (uitgevoerd 2026-07-29 … 2026-08-04)
**Kaart:** `b9e6365a…` (route), `1fa1b693…` (skill), `d0531c12…` (sloop)
**Uitkomst:** **Nee — optie 3, kaartloze geboorte.** De route is in zijn hele
levensduur **nul keer** voor een echt idee gebruikt: precies één kaart stond ooit
in `intake` (`eae625cc…`, een test-fixture) en géén enkele kaart droeg een
`Promoted to project`-spoor.

Herziet [`product-inceptie-pipeline.md`](./product-inceptie-pipeline.md) §4
(optie 2) en
[`intake-authoring-flow-decision.md`](./intake-authoring-flow-decision.md) §3.

> **Nagekomen doc (kaart `d0531c12…`).** Dit beslisdoc hoorde bij de eerste kaart
> van het spoor te landen maar is toen niet gecommit, terwijl code, skills en
> harnassen er al naar verwezen (18 verwijzingen over `backend/`, `docs/`,
> `.claude/skills/` en `scripts/`). De sloopkaart heeft 'm alsnog geschreven uit
> de kaartteksten, de `new-app`-skill en de geïmplementeerde code. De beslissing
> zelf is van 2026-07-29; de tekst hieronder is de reconstructie ervan.

## 1. De vraag

`product-inceptie-pipeline.md` §4 koos in 2026-07-11 **optie 2 — twee-staps
intake**: een idee wordt eerst een kaart in een nieuwe `intake`-kolom op het
meta-project, met een `spec`- en een `plan`-deliverable; daarna klikt een mens
**Promote** en maakt `create_project_from_intake` het echte project aan.

Die vorm is volledig gebouwd (kaart `c33b2f14` + `0260dbcd`). De vraag van
2026-07-29: **verdient de tussenkaart zijn bestaan?**

## 2. Wat de praktijk liet zien

Gemeten op `~/.claude-registry/kanban.db` (2026-07-29, herhaald 2026-08-04 vlak
vóór de sloop):

| Signaal | Waarde |
|---|---|
| Kaarten die ooit in `intake` stonden | **1** — `eae625cc…`, `'MyApp intake'`, project_key `meta` (een test-fixture) |
| Kaarten met een `Promoted to project`-spoor | **0** |
| Projecten met een `intake`-kolomrij | 1 (alleen het cockpit-project zelf) |

De route is dus in zijn hele levensduur **nul keer** voor een echt idee gebruikt.
Dat is geen adoptieprobleem dat je met UI-werk oplost — het is een teken dat de
tussenstap niet op de plek zit waar het werk gebeurt.

## 3. Waarom de tussenkaart niet verdient te blijven

1. **De kaart draagt geen informatie die het project niet zelf kan dragen.** Spec
   en plan zijn de deliverables; het nieuwe project is hun natuurlijke thuis. Als
   ze als repo-bestanden landen (`docs/specs/…`, `docs/plans/…`) zijn ze
   versiebeheerd, reviewbaar en vindbaar op de plek waar eraan gewerkt wordt.
2. **De kaart staat op het verkeerde bord.** Een intake-kaart leeft op het
   meta-project terwijl hij over een ander project gaat — precies het
   kip-en-ei-bezwaar dat `product-inceptie-pipeline.md` §2.3 bij optie 1 al
   noteerde. Optie 2 verplaatste dat bezwaar naar een eigen kolom in plaats van
   het op te lossen.
3. **De Promote-klik is een gate zonder beslissing.** Op het moment van klikken is
   het ontwerp al goedgekeurd (sectie voor sectie, in het interview). De klik
   voegt geen oordeel toe; hij voegt een stap toe waar de sessie kan sneuvelen.
4. **Twee bewaarplaatsen voor één artefact.** Plan-markdown in een
   `kind="plan"`-deliverable én straks in de repo betekent twee bronnen die uit
   elkaar kunnen lopen.

## 4. De gekozen vorm

Een interactief **`new-app`-interview** (`.claude/skills/new-app/`) draait
`superpowers:brainstorming` → goedgekeurd design en `superpowers:writing-plans` →
TDD-plan, en schrijft na **elke goedgekeurde sectie** naar een durabele
scratch-map `~/.claude-registry/interviews/<slug>/` (buiten elke git-repo, zodat
een gecrashte sessie of een opgeruimde worktree het interview niet wist;
`/new-app --resume <slug>` pikt de dialoog weer op).

Is het interview klaar, dan roept de skill **`create_project_from_interview`** aan
(MCP + `POST /api/v1/kanban/projects/from-interview`). Die actie:

1. valideert de payload (lege `spec_md`/`plan_md` → `ValueError`);
2. `mkdir` + `git init` + `BlueprintService.apply()` voor de `.claude/`-seed;
3. schrijft LICENSE + `docs/specs/<YYYY-MM-DD>-<slug>-design.md` +
   `docs/plans/<YYYY-MM-DD>-<slug>-plan.md` **vóór** de eerste commit, zodat die
   commit ze vastlegt;
4. registreert het pad via `ProjectService.add_project`;
5. zet `autodispatch` volgens de `BootstrapPolicy`;
6. maakt de eerste Backlog-kaart met `metadata["spec_doc"]` → het repo-relatieve
   pad van het design-doc.

Alles atomisch: elke fout rolt filesystem, `Project`-rij, autodispatch-meta en de
gedeeltelijke kanban-kaart terug.

**Traceability zonder kaart.** Waar optie 2 een `plan_ref`-deliverable terug naar
de intake-kaart legde, doet `metadata["spec_doc"]` dat werk nu — en het wijst naar
een bestand in de repo zelf, niet naar een kaart op een ander bord. De
drie-bomen-regel uit `00-orientation.md` blijft gerespecteerd: de kaart leeft in
de kanban-DB, in het nieuwe project.

**Mens-in-de-lus blijft.** `intake-authoring-flow-decision.md` §4.2 bepaalde dat
de goedkeuring een echte dialoog is en géén `report_impediment` (dat is één vraag
met een vaste optielijst, en het beëindigt de sessie). Dat blijft ongewijzigd
gelden: het interview draait native interactief, buiten de dispatcher.

## 5. Wat er níet verandert

- **`WORK_TYPES`** blijft vierwaardig. Er komt geen `work_type="intake"` —
  [`intake-card-routing-analysis.md`](./intake-card-routing-analysis.md) §2 wees
  dat al af en die afwijzing blijft staan (nu permanent: er zijn geen
  intake-kaarten meer om te routeren).
- **`intake_kind`** komt er niet.
  [`intake-kind-decision.md`](./intake-kind-decision.md) stelde het veld uit tot
  er een consument zou zijn; die consument is met de kolom verdwenen.
- **De toolkeuze** uit `intake-authoring-flow-decision.md` §4.1
  (`superpowers:brainstorming` + `writing-plans`, niet spec-kit) draagt de
  `new-app`-skill ongewijzigd door.
- **`BlueprintService`, `RepoBootstrapService`, `BootstrapPolicy`** blijven de
  geboorte-bouwstenen; alleen de aanroeper verandert.

## 6. De sloop (kaart `d0531c12…`)

Uitgevoerd nadat de kaartloze route én de `new-app`-skill werkten, zodat de repo
op elk moment werkend bleef:

- **Backend:** `intake` uit `COLUMNS`; `ensure_intake_column` + zijn regel in de
  kolom-backfill; `POST /projects/from-intake` +
  `CreateProjectFromIntakeRequest/Response`; MCP-tool
  `create_project_from_intake`; `InceptionService.create_project_from_intake`;
  de `intake`-tak in de hold-classificatie van `portfolio_service.py`. De
  `{intake_card_id}`-placeholder in `BootstrapPolicy.first_commit_message` is
  vervallen (hij had geen aanroeper meer en zou op `.format()` een `KeyError`
  geven).
- **Frontend:** `PromoteToProjectDialog.tsx` verwijderd; `onPromote` uit
  `Board`/`Column`/`CardItem`; `isIntake`; `kanbanApi.createProjectFromIntake`;
  de intake-cases uit `CardItem.test.tsx`; de ProjectsPage-copy wijst nu naar
  `/new-app` in plaats van naar de intake-kolom.
- **Scripts:** `FIXED_COLUMNS` in `check-kanban-conventions.sh` en zijn harnas.
- **Data:** de ene test-fixture-kaart + de `intake`-kolomrij verwijderd (na
  hernieuwde telling; DB-backup in `~/.claude-registry/backups/`).
- **Docs:** dit doc, plus `superseded`-status op de drie intake-docs en een
  herzien-banner op `product-inceptie-pipeline.md` §4/§9 en
  `new-project-startup-flow.md`.

## 7. Heropenen bij

- Er ontstaat een gebruikswens voor **ideeën die wél eerst op een bord moeten
  wachten** (bijvoorbeeld een portfolio-triage waarin meerdere ideeën tegen
  elkaar afgewogen worden vóór er één gebouwd wordt). Dat is een ander probleem
  dan inceptie en verdient een eigen vorm — niet een teruggedraaide intake-kolom.
- De kaartloze route blijkt in de praktijk **onbruikbaar zonder UI** (de flow
  heeft vandaag bewust geen knop; hij loopt via `/new-app` in een interactieve
  sessie). Een UI-ingang die `create_project_from_interview` aanroept is additief
  en vereist deze beslissing niet te heropenen.
