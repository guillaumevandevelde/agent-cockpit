---
title: "Refactor-frontend-orphan-sweep — backend-Pydantic-rename of -veldverwijdering"
type: reference
status: active
---

# Refactor-frontend-orphan-sweep — backend-Pydantic-rename of -veldverwijdering

> **Bron van waarheid voor de frontend-orphan-sweep.** De korte aanwijzing staat
> in `CLAUDE.md` onder *Code Style* en verwijst hierheen. Lees dit document vóór
> je een backend-Pydantic-schema-veld hernoemt of verwijdert.

## De conclusie

Een engineer-sessie die een `backend/app/models/<x>_schemas.py`-veld hernoemt of
verwijdert, draait vóór de commit een frontend-orphan-sweep:

```bash
# Per verwijderd of hernoemd veld: geen TS/TSX-reference meer over.
grep -rn "<verwijderde_of_oude_naam>" frontend/src/
```

Geen treffers = schoon. Treffers = of de TS-beller aanpassen, of de
veldverwijdering terugdraaien. **Nooit** een Pydantic-veld verwijderen en een
halfjaar later `npm run build` zien falen — dat is precies het scenario dat
deze sweep sluit.

## Waarom dit misgaat zonder de sweep

De backend-test (`pytest`) ziet het verwijderde veld niet meer en is groen.
De OpenAPI-snapshot (`scripts/check_openapi_snapshot.py`) ziet het ook niet.
De frontend haalt zijn types uit `frontend/src/types/<feature>.ts` (hand-typed
of gegenereerd uit OpenAPI) en zijn runtime-gebruik uit `frontend/src/features/
<feature>/utils.ts` of component-files. Een Pydantic-veld dat wegvalt leidt
daar tot:

- **`TS2304: Cannot find name '<veld>'`** — directe reference, build faalt
  onmiddellijk.
- **`TS2339: Property '<veld>' does not exist on type ...`** — via `as`-cast
  of property-access, bouwt soms door maar explodeert in productie.
- **Stale UI-badge of label-helper** — semantisch dode code die geen
  compileer-fout geeft maar een onbereikbaar pad rendert.

Geen van die drie wordt gevangen door backend-pytest of door de bestaande
OpenAPI-snapshot. De frontend-orphan-sweep is de canonieke sluiting; zonder
sweep bleef de master-tak van agent-cockpit in 2026-08 wekenlang op een
rode `npm run build`, tot een toevallige frontend-tikkende ship de failure
onthulde (kaart `39778adba3e74d2b8fd6f786344f4a8d`, voorbeeld-orphan:
`sender_actor_kind` → `senderTypeLabel` in `frontend/src/features/agent-mail/
utils.ts`, gefixt in commit `6ac13b84`).

## Wanneer draaien

**Verplicht** bij élke diff die een `models/*_schemas.py`-veld verwijdert of
hernoemt — ook als de diff "alleen backend" lijkt. De frontend-typelaag is een
afgeleide van de Pydantic-schemas; een schema-wijziging zonder frontend-aanpassing
is per definitie een halve refactor.

**Niet verplicht** bij:

- Een puur toegevoegd veld (`Optional[...]` met default `None`) — geen
  bestaande reference verandert.
- Een alias-only wijziging (`Field(alias=...)`) zonder veldnaam-rename.
- Een interne service-layer refactor zonder schema-aanraking.

Twijfel? Draai de sweep. Duur: één grep over `frontend/src/` per veld.

## De sweep — drie stappen, machine-checkbaar

```bash
# 1. Verzamel de set verwijderde/hernoemde velden uit de diff.
#    Voor elke naam: check of de frontend er nog naar refereert.
#
#    In één grep:
grep -rn -E '\b(<veld1>|<veld2>|<veld3>)\b' frontend/src/
#
# Geen output = schoon. Elke hit = onderzoek; óf de TS-beller bijwerken,
# óf het veld terugdraaien.
```

**Breder dan `frontend/src/`** als de wijziging een gedeelde primitive raakt:
ook `frontend/` (losse config-files, scripts, e2e-tests) en
`backend/tests/` voor fixtures die frontend-responses nabouwen.

**Let op aliassen.** Een Pydantic-veld kan onder een `Field(alias="...")`-
naam leven — de frontend referencet dan de alias, niet de veldnaam. Draai de
grep op beide vormen.

**Let op string-literals.** Een veldnaam die in een error-message of
`Intl.DisplayNames`-key staat, komt niet voor als property-access maar als
string. De `\b`-word-boundary hierboven dekt dat al; dubbelcheck met de
regex zonder anchors als je twijfelt.

## De aanvulling op `scripts/check-schema-rename-coverage.sh`

De bestaande `scripts/check-schema-rename-coverage.sh --strict` (zie
engineer-persona §5) dekt **backend → backend**: Pydantic-rename, ORM-kolom-
rename, OpenAPI-shape-wijziging. Het zoekt in `backend/app/` en
`backend/tests/`. Voor backend → **frontend** is die coverage er niet — die
bewuste leemte is wat deze sweep vult.

Beide checks draaien dus bij een rename-diff:

```bash
# Backend → backend (bestaand)
bash scripts/check-schema-rename-coverage.sh --strict

# Backend → frontend (deze conventie)
grep -rn -E '\b(<verwijderde_of_oude_naam>)\b' frontend/src/
```

Geen treffers op beide = klaar om te committen.

## Bron

- Kaart die deze conventie forceerde: `39778adba3e74d2b8fd6f786344f4a8d`
  (k-self-improve-1616, 2026-08-15).
- Concrete voorbeeld-orphan: `MailMessageResponse.sender_actor_kind` →
  `senderTypeLabel` in `frontend/src/features/agent-mail/utils.ts:30-31`
  (referentie verwijderd door `6ac13b84`).
- Aanvullend op: engineer-persona §5 (Pydantic-rename check) en
  `scripts/check-schema-rename-coverage.sh` (backend-coverage).
