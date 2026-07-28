# Plans B↔C Correlation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Laat de product owner in het Plans-venster per cockpit-doc zien welke kanban-kaarten het doc implementeren, en vanaf een B-rij terugnavigeren naar het gekoppelde doc.

**Architecture:** Verrijk de bestaande `/plans/overview`-projectie in één SQL-round-trip via een LEFT JOIN van projectkaarten naar `plan`/`plan_ref`-deliverables. Dezelfde rijen leveren bestaande B-items en een gededupliceerde `spec_doc`-correlatie voor C-items; URL-specs worden genegeerd. De frontend rendert de verrijkte response in de overview en haalt diezelfde overview naast doc-content op voor een refresh-veilige detailweergave.

**Tech Stack:** FastAPI, async SQLAlchemy, Pydantic, React 19, TypeScript, React Router, Vitest/Testing Library.

## Global Constraints

- Geen nieuw datamodel en geen cache.
- `spec_doc` moet exact gelijk zijn aan het repo-relatieve C-docpad.
- `http://`- en `https://`-specs zijn niet correleerbaar.
- Projectscoping blijft gelden voor alle kaartdata; de docs-index blijft repo-wide.
- Geen lokale volledige pytest-suite; alleen `scripts/run-single-test.sh` voor het geraakte backend-testbestand.
- Frontendwijzigingen vereisen gerichte Vitest, `npm run lint` en `npm run build` vóór ship.

---

### Task 1: Backend contract en correlatie

**Files:**
- Modify: `backend/tests/test_api_plans_overview.py`
- Modify: `backend/app/models/schemas.py:1735-1794`
- Modify: `backend/app/api/v1/plans.py:1-227`

**Interfaces:**
- Produces: `CorrelatedCardItem(card_id: str, card_title: str)`, `CardPlanItem.spec_doc: str | None`, `DocSpecItem.implemented_by: list[CorrelatedCardItem]`.
- Produces: `_list_plan_overview_data(project_key: str) -> tuple[list[CardPlanItem], dict[str, list[CorrelatedCardItem]]]`.

- [ ] Voeg een testhelper toe die kaartmetadata via de bestaande card-create payload kan zetten.
- [ ] Schrijf drie falende API-tests: exact match vult `implemented_by` en B-`spec_doc`; ontbrekende match geeft `[]`/`null`; URL-specs worden uitgesloten.
- [ ] Draai `bash scripts/run-single-test.sh tests/test_api_plans_overview.py -k 'spec_doc or correlation'` en bevestig dat de nieuwe assertions falen.
- [ ] Voeg de Pydantic-types en defaults toe.
- [ ] Vervang de inner join door één project-scoped LEFT JOIN met het deliverable-kind in de ON-clause, zodat kaarten zonder plan-deliverable wel correlaties leveren maar geen B-rij.
- [ ] Lees `card.meta.get("spec_doc")` defensief, accepteer alleen niet-lege strings die niet met `http://` of `https://` beginnen, dedupliceer op kaart-id per docpad, en sorteer kaartlinks deterministisch.
- [ ] Geef de correlatiemapping mee aan `_list_cockpit_docs`, zodat elk bestaand doc-item alleen exact-matchende kaarten krijgt.
- [ ] Draai het gerichte backend-testbestand en bevestig groen.

### Task 2: Frontend beide richtingen

**Files:**
- Modify: `frontend/src/types/plans.ts`
- Modify: `frontend/src/features/plans/PlansPage.test.tsx`
- Create: `frontend/src/features/plans/PlanDetailPage.test.tsx`
- Modify: `frontend/src/features/plans/PlansPage.tsx`
- Modify: `frontend/src/features/plans/PlanDetailPage.tsx`

**Interfaces:**
- Consumes: `CardPlanItem.spec_doc` en `DocSpecItem.implemented_by` uit Task 1.
- Produces: klikbare doclink op B-rij, kaartlinks op C-rij en detailpagina.

- [ ] Breid TypeScript-types en testfixtures uit met `CorrelatedCardItem`, `spec_doc`, en `implemented_by`.
- [ ] Schrijf falende overview-tests voor de tekst “Implemented by cards” en voor de omgekeerde B-doclink; verifieer dat inner links `stopPropagation()` gebruiken.
- [ ] Schrijf een falende detailtest die doc-content plus overview mockt en de gekoppelde kaartlink verwacht.
- [ ] Implementeer compacte linkregels binnen de bestaande Cards; navigeer kaartlinks naar `/kanban?card=<id>` en docs naar `/plans/<encoded-path>`.
- [ ] Laat `PlanDetailPage` doc-content en overview ophalen via bestaande hookfuncties en zoek het huidige doc-item op path; behoud bestaande loading/error voor doc-content.
- [ ] Draai `npm test -- --run src/features/plans/PlansPage.test.tsx src/features/plans/PlanDetailPage.test.tsx` en bevestig groen.

### Task 3: Bron-documentatie en verificatie

**Files:**
- Modify: `docs/cockpit/plans-feature-decision.md`

**Interfaces:**
- Consumes: volledige backend/frontendimplementatie.
- Produces: duurzame resolutieregel voor kaart `725fbdd35bfa413e98c24315d0a174d1`.

- [ ] Voeg bij §8.2/§10 een korte `✅ Geïmplementeerd`-resolutie toe die de gemeten adoptie en shipped correlatie benoemt.
- [ ] Draai frontend `npm run lint && npm run build`.
- [ ] Voer `iteration-loop verify` uit en herstel regressies.
- [ ] Commit alle wijzigingen met een gerichte feature-commit.
- [ ] Voer de verplichte FCR tegen de commit-hash uit; herstel blockers en herhaal tot OK.
- [ ] Sync, merge direct naar master, push, koppel branch-deliverable, voer session-retro uit en zet de kaart producttalig op Done.
