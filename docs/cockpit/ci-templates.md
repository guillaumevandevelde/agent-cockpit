---
title: "CITemplateService — drie GitHub-Actions-templates voor pasgeboren projecten"
type: reference
status: active
---

# CITemplateService — drie GitHub-Actions-templates voor pasgeboren projecten

> Kanban-kaart: **`[feature][D] CITemplateService + drie GitHub-Actions-templates`**
> (`c66a93a20c0a48448fa5e1904d478e55`, facet D, follow-up #7 van
> `docs/cockpit/veilig-bouwen-en-uitleveren.md` §6).
>
> Ontwerp-anker: `veilig-bouwen-en-uitleveren.md` §4.6 (CI-bootstrap). Service
> + REST-oppervlak, niet de bootstrap-integratie zelf — die wordt door
> `RepoBootstrapService` (facet B, kaart `dceb60ab5352`) op deze service
> aangesloten zodra die aan de beurt is.

## 0. Probleem

Een splinternieuw product-project heeft vandaag geen CI, tenzij iemand met de
hand `.github/workflows/quality.yml` schrijft. `quality.yml` van `claude-cockpit`
zelf is hand-geschreven, niet getemplated — dus élk Cockpit-geboren project
begint zonder quality-gate. Dit facet levert de ontbrekende bouwsteen: een
service die uit een catalogus van parametrische GitHub-Actions-workflows een
file rendert naar `.github/workflows/<profile>.yml` van een willekeurige repo.

## 1. Drie profielen

| Profile         | Bestand                              | Job       | Parameters                                        | Use-case |
|-----------------|--------------------------------------|-----------|---------------------------------------------------|----------|
| `python-strict` | `.github/workflows/python-strict.yml`| `backend` | `python_version`, `requirements_dev_path`         | Pure-Python service of FastAPI-app. Mirrors `claude-cockpit`'s backend-deel van `quality.yml`: `actions/setup-python`, `pip install -r`, `ruff check`, `pytest -q`. |
| `node-strict`   | `.github/workflows/node-strict.yml`  | `frontend`| `node_version`                                    | React/Vite/Next.js SPA. Mirrors het frontend-deel van `quality.yml`: `actions/setup-node`, `npm ci`, `npm run lint`, `npm run test:coverage`, `npm run build`. |
| `minimal`       | `.github/workflows/minimal.yml`      | `hello`   | (geen)                                            | Hello-world pipeline: één job die "Hello from a CI-template-generated workflow." print. Voor projecten die wel een quality-gate willen *signaleren* maar nog geen taal-specifieke toolchain hebben. |

Alle drie reageren op `push` en `pull_request` tegen `master` — dezelfde
trigger-config als `claude-cockpit`'s eigen `quality.yml`.

**Out of scope** (per de kaart): GitLab CI / CircleCI, image-build-pipelines,
`release.yml`. `claude-cockpit`'s eigen `release.yml` blijft hand-geschreven
onderhouden — die is template-onafhankelijk.

## 2. Defaults

Als `apply()` zonder `parameters` wordt aangeroepen (of de REST-equivalent
zonder `parameters`-blok in de body), valt de service terug op deze
ingebakken defaults. Ze zijn bewust conservatief: iedereen kan ze per-call
overschrijven, niemand wordt gedwongen om ze op te geven.

| Parameter              | Default              | Bron                                |
|------------------------|----------------------|-------------------------------------|
| `python_version`       | `"3.11"`             | `CITemplateService._DEFAULT_PYTHON_VERSION` |
| `requirements_dev_path`| `"requirements-dev.txt"` | `CITemplateService._DEFAULT_REQUIREMENTS_DEV_PATH` |
| `node_version`         | `"22"`               | `CITemplateService._DEFAULT_NODE_VERSION` |

## 3. Service-API

`backend/app/services/ci_templates/__init__.py`:

```python
from app.services.ci_templates import CITemplateService, CITemplateApplyResult

svc = CITemplateService()

# Catalogus
templates = svc.list_templates()
# → [CITemplateInfo(name='minimal', filename='minimal.yml', parameters=(), ...),
#    CITemplateInfo(name='node-strict', ..., parameters=('node_version',)),
#    CITemplateInfo(name='python-strict', ...)]

# Render + write
result = svc.apply(
    project_path="/path/to/project",
    profile="python-strict",
    python_version="3.12",
    requirements_dev_path="requirements-dev.txt",
    force=False,
)
# → CITemplateApplyResult(
#     profile='python-strict',
#     project_path='/path/to/project',
#     written_file='.github/workflows/python-strict.yml',
#     skipped_existing=False,
#     force=False,
#   )
```

`CITemplateService.apply` is **idempotent**: bestaat het doelbestand al én is
`force=False`, dan wordt er niets geschreven en geeft `CITemplateApplyResult`
`written_file=None` + `skipped_existing=True` terug. Met `force=True` wordt
overschreven én wordt `force=True` expliciet gelogd in de auditregel
(`logger.info(..., " CI template %r applied to %s: wrote %s force=True", ...)`).

De Jinja2-`Environment` is geconfigureerd met `StrictUndefined`: een typo in
een parameternaam laat de render stuklopen in plaats van een lege string
renderen — typos moeten op het ontwikkelaarsscherm verschijnen, niet op de
CI-runner.

## 4. REST-oppervlak

Onder `/api/v1/ci/templates`:

| Methode | Pad                                          | Doel                                                |
|---------|----------------------------------------------|-----------------------------------------------------|
| `GET`   | `/api/v1/ci/templates`                       | `CITemplateListResponse{templates: [CITemplateInfo]}` — catalogus incl. parameter-namen |
| `POST`  | `/api/v1/ci/templates/{profile}/apply`       | `CITemplateApplyRequest{project_path, force?, parameters?}` → `CITemplateApplyResponse{profile, project_path, written_file?, skipped_existing, force}` |

Foutafhandeling:

- onbekende `profile` → `404 CITemplateProfileUnknown` (FastAPI `HTTPException(404)`)
- Jinja-renderfout (ontbrekende parameter / syntax) → `400 CITemplateRenderFailed`
- overige servicefout → `500`

Voorbeeld:

```bash
curl -X POST http://localhost:8000/api/v1/ci/templates/python-strict/apply \
  -H 'Content-Type: application/json' \
  -d '{
    "project_path": "/home/me/projects/my-fastapi-app",
    "parameters": {"python_version": "3.12"}
  }'
```

## 5. Een nieuwe profile toevoegen

Nieuwe profiles zijn bedoeld als **druppelsgewijze uitbreiding**, niet als
service-edit. Procedure:

1. Drop een `<profile>.yml.j2` in `backend/app/services/ci_templates/`.
2. Voeg een `CITemplateInfo`-entry toe aan de `_PROFILES`-tuple in dezelfde
   module, met de juiste `name`, `description`, `filename` en `parameters`.
3. Voeg een `defaults`-blok toe aan `CITemplateService._defaults_for` als de
   nieuwe profile parameters heeft die defaults willen.
4. Schrijf render-tests: `test_render_<profile>_produces_valid_yaml` +
   minstens één parameter-substitutie-test.
5. Schrijf een apply-test (happy-path + idempotency + force).

De `list_templates()` en `apply()`-code blijven ongewijzigd; de catalogus is
de single source of truth.

## 6. Volgende stappen (niet in deze kaart)

- **Bootstrap-integratie** (facet B, kaart `dceb60ab5352`): `RepoBootstrapService`
  krijgt een veld `ci_profile` op `BootstrapPolicy` (facet B, kaart
  `02b07a0f2984`) en roept `CITemplateService.apply(..., profile=policy.ci_profile)`
  aan tijdens geboorte. Default profile = `None` (geen CI bij geboorte,
  zoals vandaag).
- **Image-build-template** (facet D, follow-up #11): aparte profile voor
  projects die naar GHCR/DockerHub willen pushen.
- **`release.yml`-templating**: nu hand-geschreven in deze repo; als er
  meerdere projects dezelfde release-flow willen, kan `CITemplateService`
  met een `release`-profile uitgebreid worden.