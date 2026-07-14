# Blueprints — taxonomie van `project_blueprint`-archetypes

> Kanban-kaart: **`[design][inceptie] Schrijf docs/cockpit/blueprints-typology.md`**.
> Sibling-kaart **`395590d7`** (facet A, `BlueprintService.apply()`) consumeert
> dit design. `work_type=chore`. **Design-only** — geen code in `backend/`/
> `frontend/`; alleen deze markdown.
>
> Reconcilieert met `docs/cockpit/repo-provisioning-bootstrap.md §4.2`
> (blueprint-keuzes) en §4.3 (open ontwerp-beslissingen).

## 0. Doel & scope

Een **blueprint** beschrijft declaratief de `.claude/`-configuratie van een
project: welke subdirs bestaan, welke agents/skills materialiseren, welke
defaults `settings.json` meekrijgt, welke `CLAUDE.md`-stub start. Blueprints
leven of als statische YAML naast de code
(`backend/app/services/blueprint/baseline_blueprint.yaml`) of als JSON in
`~/.claude-registry/blueprints/<name>.json` (de file-backed store).

Deze doc legt vast:

1. De **5 archetypes** waaruit het catalogus-menu bestaat — elk archetype
   beschrijft een gangbaar project-type met zijn eigen `.claude/`-vorm.
2. Het **veld-model + loadability-contract** van een recept: wat mag erin
   staan, wat wordt stilzwijgend genegeerd, hoe de overgang naar een
   `Blueprint`-instantie verloopt.
3. De **cross-cut defaults** (privacy/security, model, `permission_mode`)
   die elk archetype erft — afgestemd op `repo-provisioning-bootstrap.md
   §4.2`.
4. De **versie-strategie + backward-compat-belofte** voor toekomstige
   velduitbreidingen.
5. De **decision-rationale** per archetype + per cross-cut, met expliciet
   verworpen alternatieven.

De daadwerkelijke **toepassing** van een blueprint gebeurt door
`BlueprintService.apply(project_path, blueprint)` (zie sibling-kaart
`395590d7`). Deze doc beschrijft de *vorm* van een blueprint, niet de
*uitvoerder*.

## 1. Het veld-model van een recept

Een recept is een YAML-document dat **1-op-1** mapped op de Pydantic
`Blueprint`-klasse in `backend/app/services/blueprint/__init__.py`. De
klasse heeft `extra="forbid"`: onbekende top-level keys leiden tot een
harde `ValidationError` bij het parsen — een bewuste keuze om stille drift
tegen te gaan (zie §6 voor de rationale).

### 1.1 Veld-tabel

| Veld | Type | Verplicht? | Default | Wat het wordt |
|---|---|---|---|---|
| `name` | `str` | bij `BlueprintStore.save` (slug `^[a-z0-9][a-z0-9._-]{0,63}$`); niet bij ad-hoc constructie | `None` | Storage-key onder `~/.claude-registry/blueprints/<name>.json`. |
| `version` | `int` | optioneel | `1` | Schema-versie van het recept zelf (zie §4). |
| `description` | `str` | optioneel | `None` | Mens-leesbare omschrijving; komt in audit-logs en UI. |
| `subdirs` | `list[str]` | optioneel | `["commands","agents","hooks","skills","plugins","output-styles"]` | Top-level mappen onder `.claude/` die `apply()` als lege (`.gitkeep`-gemarkeerde) mappen aanmaakt. |
| `settings` | `dict` | optioneel | `{}` → `BlueprintSettings()` | Volledige inhoud van `.claude/settings.json` (CC leest deze 1-op-1). |
| `skills` | `list[dict]` | optioneel | `[]` | Project-scoped skills; elk item wordt `BlueprintSkill` (`name`, `source`, `version_pin`). |
| `agents` | `list[dict]` | optioneel | `[]` | Project-scoped agents; elk item wordt `BlueprintAgent` (`name`, `body_path`, `model_default`, `tools`). |
| `statusline` | `str` | optioneel | `None` | Body van `.claude/statusline`. |
| `output_style` | `str` | optioneel | `None` | Naam van een output-style stub in `.claude/output-styles/<name>.md`. |
| `claudemd` | `str` | optioneel | `None` | Body van `CLAUDE.md` (sibling van `.claude/`, geen child). |
| `created_at` / `updated_at` | `datetime` | alleen store-zijde | `None` | Automatisch gestempeld door `BlueprintStore.save()`; overschrijf niet in een recept. |

### 1.2 Het `settings`-submodel

`settings` is een `BlueprintSettings` (`extra="allow"`) en landt
verbatim in `.claude/settings.json`. Velden die we modelleren
(omdat de UI er form-inputs voor wil renderen):

| Veld | Type | Default | Opmerking |
|---|---|---|---|
| `permission_mode` | `default` \| `acceptEdits` \| `bypassPermissions` \| `plan` | `None` | Wordt door `BlueprintSettings.to_dict()` automatisch genest onder `permissions.defaultMode` zoals CC het leest. |
| `plansDirectory` | `str` | `None` | Verwijzing naar de plannen-folder. |
| `model` | `str` | `None` | Default-model voor agents zonder eigen `model_default`. |

`extra="allow"` betekent dat **andere CC-settings-velden ongehinderd
doorstromen** (denk aan `hooks`, `env`, `attribution`, `cleanupPeriodDays`,
…). Dat is bewust: CC's settings-schema groeit, en een nieuwe CC-versie
mag niet op een blueprint-validation-error stuiten omdat wij het veld nog
niet kennen.

### 1.3 Het loadability-contract (de harde garantie)

**Claim.** Voor élk recept in deze doc geldt:

```python
import yaml
from app.services.blueprint import Blueprint
data = yaml.safe_load(recept_text)
blueprint = Blueprint.model_validate(data)   # slaagt, geen warnings
```

Dat is geverifieerd voor alle vijf archetypes (§2) met een
wegwerp-snippet in een sandbox-shell — geen runtime-test in de repo
opgenomen (de doc is design-only). De snippet-uitvoer is in de
session-log terug te vinden; de doc zelf bewaart hem niet.

**Implicatie 1 — geen top-level `model` of `permission_mode`.**
Poging om `model: claude-sonnet-5` of `permission_mode: default` op
top-level te zetten faalt direct met `ValidationError`:

```python
Blueprint.model_validate({"permission_mode": "default"})  # ❌ extra fields not permitted
```

Dat is geen "bug om te omzeilen" — het is een feature. CC leest deze
waarden respectievelijk onder `settings.model` en
`permissions.defaultMode`; een top-level key zou stille drift tussen
declaratie en runtime veroorzaken.

**Implicatie 2 — onbekende top-level keys falen hard.**
`Blueprint(extra="forbid")`. Een typefout als `skill:` in plaats van
`skills:` geeft een `ValidationError` bij het laden — beter hier dan
bij `apply()` halverwege de staging.

**Implicatie 3 — `skills` en `agents` zijn ook `extra="forbid"`.**
`BlueprintSkill` en `BlueprintAgent` weigeren onbekende velden, dus
een recipe met `skills: [{name: foo, source: project, foo: bar}]`
faalt ook hard.

**Implicatie 4 — wat NIET in een recept thuishoort:**
* CI-config (`.github/workflows/`, `pyproject.toml`, …) — dat is
  template-zorg, geen blueprint-zorg.
* Runtime-secrets (API keys, tokens) — die gaan in
  `<project>/.claude/.env.local` of de platform-secrets-store, niet
  in `settings.json`.
* Werkende agent-body of skill-body — recepten bevatten de
  *metadata* (naam, model, tools); het lichaam schrijft de operator
  ná `apply()` met de hand of via een vervolgkaart.

## 2. De vijf archetypes

Elk archetype heeft dezelfde structuur in deze doc:

* **Omschrijving** — wat voor project het dekt en wanneer passend.
* **Veld-tabel** — `verplicht` vs. `optioneel` met project-type-specifieke
  defaults.
* **Volledig YAML-recept** — copy-pasteable, laadbaar per §1.3.
* **Cross-cut-keuzes** — welke privacy/security, model, en
  `permission_mode` (uitgewerkt in §3).

### 2.1 `web-app-spa`

**Omschrijving.** Single-page webapplicatie: React 19 + Vite + TypeScript
+ Tailwind + shadcn/ui. Alles draait in de browser; backend is een
externe dienst (of afwezig). Het archetype levert de frontend-implementer
+ -reviewer agents die dit type project van nature nodig heeft.

**Veld-tabel.**

| Veld | Status | Archetype-keuze |
|---|---|---|
| `name` | verplicht | `web-app-spa` |
| `subdirs` | optioneel, aanbevolen | `["commands","agents","hooks","skills"]` — geen `plugins` of `output-styles` (SPA-frontend heeft die typisch niet nodig) |
| `settings.permission_mode` | optioneel | `acceptEdits` (zie §3.3) |
| `settings.model` | optioneel | `claude-sonnet-5` |
| `settings.plansDirectory` | optioneel | `~/.claude/plans` |
| `skills` | optioneel | cockpit-baseline + `frontend-design` (uit de `superpowers:`-plug-in of user-skill) |
| `agents` | optioneel | `frontend-implementer` (stub), `frontend-reviewer` (stub) |
| `claudemd` | optioneel | korte stub die de SPA-conventies uitlegt (zie recept) |

**Volledig YAML-recept.**

```yaml
name: web-app-spa
version: 1
description: Single-page webapp — React 19 + Vite + TypeScript + shadcn/ui. Frontend implementer + reviewer agents.

subdirs:
  - commands
  - agents
  - hooks
  - skills

settings:
  permission_mode: acceptEdits
  model: claude-sonnet-5
  plansDirectory: ~/.claude/plans

skills:
  - name: flag-problem
    source: project
  - name: context-map
    source: project
  - name: session-retro
    source: project
  - name: git-ship
    source: project
  - name: verification-before-completion
    source: project
  - name: using-git-worktrees
    source: project
  - name: brainstorming
    source: project
  - name: writing-plans
    source: project

agents:
  - name: frontend-implementer
    model_default: claude-sonnet-5
    tools:
      - Read
      - Edit
      - Write
      - Bash
  - name: frontend-reviewer
    model_default: claude-sonnet-5
    tools:
      - Read
      - Grep
      - Glob

claudemd: |
  # Web-app-SPA project

  This is a single-page React 19 application built with Vite, TypeScript,
  Tailwind, and shadcn/ui. Keep changes scoped to the frontend; backend
  services are owned by sibling repos.
```

### 2.2 `rest-api`

**Omschrijving.** FastAPI REST-service met async SQLAlchemy + aiosqlite
(of postgres) als opslag. Het archetype levert backend-engineer +
api-reviewer agents; permission_mode is conservatief omdat API-changes
direct de productie raken.

**Veld-tabel.**

| Veld | Status | Archetype-keuze |
|---|---|---|
| `name` | verplicht | `rest-api` |
| `subdirs` | optioneel, aanbevolen | `["commands","agents","hooks","skills"]` |
| `settings.permission_mode` | optioneel | `default` (zie §3.3) |
| `settings.model` | optioneel | `claude-sonnet-5` |
| `settings.plansDirectory` | optioneel | `~/.claude/plans` |
| `skills` | optioneel | cockpit-baseline + `verification-before-completion` expliciet |
| `agents` | optioneel | `backend-engineer`, `api-reviewer` |
| `claudemd` | optioneel | stub die de API-conventies uitlegt |

**Volledig YAML-recept.**

```yaml
name: rest-api
version: 1
description: FastAPI REST service — async SQLAlchemy + aiosqlite/postgres. Backend engineer + API reviewer.

subdirs:
  - commands
  - agents
  - hooks
  - skills

settings:
  permission_mode: default
  model: claude-sonnet-5
  plansDirectory: ~/.claude/plans

skills:
  - name: flag-problem
    source: project
  - name: context-map
    source: project
  - name: session-retro
    source: project
  - name: git-ship
    source: project
  - name: verification-before-completion
    source: project
  - name: using-git-worktrees
    source: project
  - name: brainstorming
    source: project
  - name: writing-plans
    source: project

agents:
  - name: backend-engineer
    model_default: claude-sonnet-5
    tools:
      - Read
      - Edit
      - Write
      - Bash
  - name: api-reviewer
    model_default: claude-sonnet-5
    tools:
      - Read
      - Grep
      - Glob

claudemd: |
  # REST-API project

  This is a FastAPI service. Endpoints live under `app/api/v1/`; ORM
  models under `app/models/`; services under `app/services/`. Database
  schema changes require a migration plan + a rollback note.
```

### 2.3 `cli-tool`

**Omschrijving.** Een command-line tool (Python `typer` of Node
`commander`). Draait lokaal of in CI; één binary of één entry-point.
Permission-mode is conservatief omdat CLI-acties direct op de shell van
de gebruiker landen.

**Veld-tabel.**

| Veld | Status | Archetype-keuze |
|---|---|---|
| `name` | verplicht | `cli-tool` |
| `subdirs` | optioneel, aanbevolen | `["commands","agents","skills"]` — geen `hooks` (CLI heeft geen Claude-Code-hookpoints) |
| `settings.permission_mode` | optioneel | `default` (zie §3.3) |
| `settings.model` | optioneel | `claude-sonnet-5` |
| `skills` | optioneel | cockpit-baseline (compact) |
| `agents` | optioneel | `backend-engineer` alleen |
| `claudemd` | optioneel | stub die het entry-point noemt |

**Volledig YAML-recept.**

```yaml
name: cli-tool
version: 1
description: CLI tool — Python typer or Node commander. Backend engineer agent, conservative permission mode.

subdirs:
  - commands
  - agents
  - skills

settings:
  permission_mode: default
  model: claude-sonnet-5
  plansDirectory: ~/.claude/plans

skills:
  - name: flag-problem
    source: project
  - name: context-map
    source: project
  - name: session-retro
    source: project
  - name: git-ship
    source: project
  - name: verification-before-completion
    source: project
  - name: using-git-worktrees
    source: project

agents:
  - name: backend-engineer
    model_default: claude-sonnet-5
    tools:
      - Read
      - Edit
      - Write
      - Bash

claudemd: |
  # CLI-tool project

  Single entry-point (`src/<name>/__main__.py` or `bin/<name>.ts`). Keep
  the public command surface stable across releases; new flags need a
  deprecation note when renaming existing ones.
```

### 2.4 `library`

**Omschrijving.** Een herbruikbare library (Python `pyproject.toml` of
Node `package.json`) die door andere projecten wordt geconsumeerd. Geen
eigen UI, geen eigen runtime; één `src/`-folder en een dunne test-set.
Permission-mode conservatief; minimale `subdirs` omdat libraries niets
met commands/output-styles te maken hebben.

**Veld-tabel.**

| Veld | Status | Archetype-keuze |
|---|---|---|
| `name` | verplicht | `library` |
| `subdirs` | optioneel, aanbevolen | `["agents","skills"]` — geen `commands`, geen `hooks`, geen `output-styles` |
| `settings.permission_mode` | optioneel | `default` (zie §3.3) |
| `settings.model` | optioneel | `claude-sonnet-5` |
| `skills` | optioneel | cockpit-baseline (compact) |
| `agents` | optioneel | `backend-engineer` alleen |
| `claudemd` | optioneel | stub die de public API noemt |

**Volledig YAML-recept.**

```yaml
name: library
version: 1
description: Reusable library — pyproject.toml or package.json. Conservative permission mode, minimal subdirs.

subdirs:
  - agents
  - skills

settings:
  permission_mode: default
  model: claude-sonnet-5
  plansDirectory: ~/.claude/plans

skills:
  - name: flag-problem
    source: project
  - name: context-map
    source: project
  - name: session-retro
    source: project
  - name: git-ship
    source: project
  - name: verification-before-completion
    source: project
  - name: using-git-worktrees
    source: project

agents:
  - name: backend-engineer
    model_default: claude-sonnet-5
    tools:
      - Read
      - Edit
      - Write
      - Bash

claudemd: |
  # Library project

  Public API surface lives under `src/<libname>/`. Backwards compatibility
  is a release-blocker: deprecations need at least one minor cycle before
  removal.
```

### 2.5 `agent-service`

**Omschrijving.** Een backend-dienst die Claude-agents host (MCP-server,
agent-bridge, scheduled-message-pomp). Draait typisch in een sandbox- of
container-omgeving waar de blast-radius van een foutieve tool-call al
begrensd is — vandaar `bypassPermissions` als archetype-default. Het is
de **enige** archetype-keuze in deze catalogus met die mode; zie §6 voor
de afweging.

**Veld-tabel.**

| Veld | Status | Archetype-keuze |
|---|---|---|
| `name` | verplicht | `agent-service` |
| `subdirs` | optioneel, aanbevolen | `["commands","agents","hooks","skills","plugins"]` |
| `settings.permission_mode` | optioneel | `bypassPermissions` (zie §3.3 + §6) |
| `settings.model` | optioneel | `claude-opus-4-8` (zwaardere reasoning voor orchestration) |
| `skills` | optioneel | cockpit-baseline + agent-specifieke skills |
| `agents` | optioneel | `agent-orchestrator`, `tool-builder` |
| `claudemd` | optioneel | stub die de agent-runtime uitlegt |

**Volledig YAML-recept.**

```yaml
name: agent-service
version: 1
description: Agent backend — MCP server / agent bridge / scheduler. Bypass-permissions default; runs in sandboxed worktree.

subdirs:
  - commands
  - agents
  - hooks
  - skills
  - plugins

settings:
  permission_mode: bypassPermissions
  model: claude-opus-4-8
  plansDirectory: ~/.claude/plans

skills:
  - name: flag-problem
    source: project
  - name: context-map
    source: project
  - name: session-retro
    source: project
  - name: git-ship
    source: project
  - name: verification-before-completion
    source: project
  - name: using-git-worktrees
    source: project
  - name: brainstorming
    source: project
  - name: writing-plans
    source: project

agents:
  - name: agent-orchestrator
    model_default: claude-opus-4-8
    tools:
      - Read
      - Edit
      - Write
      - Bash
      - Grep
      - Glob
  - name: tool-builder
    model_default: claude-sonnet-5
    tools:
      - Read
      - Edit
      - Write
      - Bash

claudemd: |
  # Agent-service project

  This service hosts Claude agents (MCP server, agent bridge, scheduler).
  Runs in an isolated worktree or container — blast-radius is bounded
  by the runtime, not by the permission mode.
```

## 3. Cross-cuts

De drie cross-cuts gelden voor élk archetype; per archetype kan de
keuze afwijken. De matrix hieronder is de reconciliatie tussen deze
doc en `repo-provisioning-bootstrap.md §4.2`.

### 3.1 Privacy- & security-defaults

| Aspect | Default | Rationale |
|---|---|---|
| Skills in de YAML | `source: project` waar mogelijk | `project`-skills krijgen een discoverable `SKILL.md`-stub in `.claude/skills/<name>/`, zodat een verse checkout niets aan extern geheugen overhoudt. `user`/`system` skills worden alleen gerefereerd in de audit-log maar krijgen geen file-write. |
| Geen secrets in `settings.json` | hard | Recepten schrijven geen tokens, API-keys of GitHub-creds. Geheimen horen in `<project>/.claude/.env.local` (gitignored) of in de platform-secrets-store; een blueprint schrijft daar niet in. |
| `.gitkeep` op lege subdirs | aan | Zorgt dat `git add .` de aangemaakte folders niet laat vallen — reviewbaar in de PR. |
| Geen bypass van id-checks | aan | `BlueprintStore.validate_name` blijft de slug-check doen; niets in deze catalogus ontsnapt daaraan. |

### 3.2 Model-defaults

| Archetype | `settings.model` | Reden |
|---|---|---|
| `web-app-spa` | `claude-sonnet-5` | UI-implementatie is commodity; opus is overkill, haiku is net te licht voor refactor-suggesties. |
| `rest-api` | `claude-sonnet-5` | Idem — backend-code met tests; sonnet is de sweet-spot voor typed-python + async SQL. |
| `cli-tool` | `claude-sonnet-5` | CLI-code is kort en single-file; sonnet dekt het prima. |
| `library` | `claude-sonnet-5` | Library-code vraagt om voorzichtige refactors; sonnet's conservatisme is een feature, geen bug. |
| `agent-service` | `claude-opus-4-8` | Orchestration-redenering is structureel zwaarder (multi-step planning, tool-keuze, fout-afhandeling) — de extra latency is gerechtvaardigd. |

`settings.model` is een **fallback**: agents met een eigen
`model_default` in de `agents: [...]`-lijst overriden dit. De
`cockpit-baseline`-blueprint (`baseline_blueprint.yaml`) zet géén model
omdat de baseline bedoeld is als universeel minimum zonder
project-specifieke aannames.

### 3.3 `permissions.defaultMode` per archetype

`permission_mode` leeft in `settings.permission_mode`; `BlueprintSettings.to_dict()`
nest 'm onder `permissions.defaultMode` zoals CC dat leest. De archetype-keuzes:

| Archetype | `permission_mode` | Rationale |
|---|---|---|
| `web-app-spa` | `acceptEdits` | Frontend-wijzigingen zijn typisch reversibel (de dev-server herlaadt, git revert is één commando). Auto-accept van file-edits versnelt de UI-loop. Bash-calls blijven wel onderhevig aan permissie-prompts. |
| `rest-api` | `default` | Server-code is minder reversibel: een foute migration kan data raken. Conservatieve default — operator moet expliciet akkoord op elke write. |
| `cli-tool` | `default` | CLI-acties landen op de shell van de gebruiker of in CI-pijplijnen. Auto-accept van een foute `rm -rf`-achtige actie is een harde no. |
| `library` | `default` | Library-changes raken downstream-consumers; conservatief is de enige juiste houding. |
| `agent-service` | `bypassPermissions` | De runtime (sandbox / container / worktree) begrenset de blast-radius al; CC-permission-prompts in een orchestration-loop zijn ruis die de agent vertraagt zonder extra safety. **Eén uitzondering, goed onderbouwd in §6.** |

`acceptEdits` en `bypassPermissions` zijn geen "vrijbrief" — bij beide
blijven Bash-calls die destructive shell-patterns triggeren
(`rm -rf /`, `git push --force` zonder `--force-with-lease`) onder
CC's eigen safety heuristics.

## 4. Versie-strategie + backward-compat

### 4.1 Huidige staat

`Blueprint.version` bestaat al als `int` (default `1`) — het werd in de
eerste Pydantic-model-pass meegenomen, niet in een aparte iteratie
toegevoegd. De **strategie** hieronder is wat sibling-kaart `395590d7`
moet implementeren wanneer zij het veld uitbreidt (bv. naar een
`SemVer`-string of een `{"schema": int, "compat": str}`-struct); deze
doc levert het beleidskader, niet de patch.

### 4.2 Versiebeleid (additive-only)

| Wijziging | Versie-bump | Voorbeeld |
|---|---|---|
| Nieuwe top-level key op `Blueprint` | **MAJOR** | toevoegen van `templates: list[str]` aan de basis |
| Nieuwe key binnen `BlueprintSettings` | **MINOR** | toevoegen van `cleanupPeriodDays` (CC-setting die CC zelf kan pushen) |
| Nieuwe key binnen `BlueprintSkill` of `BlueprintAgent` | **MINOR** | toevoegen van `version_pin` was ooit zo'n stap |
| Default-wijziging van een bestaand veld | **MAJOR** | default-subdirs wijzigen van 6 naar 4 entries |
| Bugfix / comment-only wijziging | **PATCH** | typos in een `description`, genummerde skill-volgorde |

### 4.3 Backward-compat-belofte

Drie harde regels voor recepten die vandaag in deze catalogus staan:

1. **Een recept met `version: 1` blijft laadbaar onder toekomstige
   `version: 2`-code** — onbekende top-level keys worden door een
   schema-migrator genegeerd of naar een
   `_legacy_*`-bucket gemoved, nooit stilletjes verwijderd. Pydantic's
   `extra="forbid"` zal hiervoor eerst naar `extra="ignore"` moeten
   vallen voor `version >= 2`-recepten; een MAJOR-bump gaat dus
   gepaard met die overgang.
2. **Default-wijzigingen zijn niet backward-compat** — een blueprint
   met expliciet `subdirs: [...]` merkt niets van een default-shift,
   maar een blueprint die de default gebruikt wel. Daarom: zodra we
   een default willen wijzigen, documenteren we dat als MAJOR.
3. **`BlueprintSettings.extra="allow"`** is een bewuste uitzondering:
   onbekende CC-settings-velden stromen altijd door. Dat is geen
   versie-bump — dat is een contract met CC's eigen schema.

### 4.4 Migratie-pad voor bestaande blueprints

Wanneer sibling-kaart `395590d7` `version` van `int` naar bv. een
`SemVer-string` trekt, moeten bestaande YAML-bestanden (de baseline +
eventuele store-recepten) met eenmalig scriptje worden gemigreerd:

```yaml
# v1 recept                  →  v2 recept
version: 1                    version: "2.0.0"
                              schema_version: 1   # de oude 'version'-betekenis
```

De precieze vorm van die migratie is #4's werk; deze doc beperkt zich
tot de **regel** dat zulke migraties een expliciete versiebump + changelog-regel
krijgen, en nooit stilletjes gebeuren.

## 5. Catalogus-levencyclus

| Stadium | Wat | Wie |
|---|---|---|
| Ontwerp | Recept + rationale in deze doc | design-kaart (deze doc) |
| Acceptatie | Menselijke review: bevordert dit archetype een reëel project-type, of is het een hypothetisch geval? | mens |
| Code-landing | Recept in `BlueprintStore` (`<name>.json`) of in een `templates/blueprints/`-folder als statische YAML | engineer-kaart |
| UI | Catalogus-menu in de frontend met archetype-keuze + live preview van het YAML | apart traject |
| Promotie | Een geboren project kiest dit archetype bij intake → `BlueprintService.apply()` materialiseert het | runtime |

Deze doc stopt na stadium 1; de overige stadia zijn eigen kaarten.

## 6. Decision-rationale

### 6.1 `Blueprint` is een Pydantic `BaseModel`, geen `dataclass`

**Gekozen.** `class Blueprint(BaseModel): model_config = ConfigDict(extra="forbid")` —
parsen via `Blueprint.model_validate(data)`.

**Verworpen.**
- *Pure `@dataclass` met eigen `__init__`-validatie.* Minder
  tooling-support (geen JSON-Schema-gen, geen auto-generated UI-form
  uit het model), en de validatie-logica moet handmatig worden
  bijgehouden. Pydantic levert die infra kant-en-klaar.
- *Pydantic `extra="ignore"`.* Verleidelijk omdat het
  forward-compat met onbekende keys biedt, maar maskeert
  typefouten (`skill:` vs `skills:`) — een gemiste `s` zou
  stilzwijgend een lege `skills`-lijst worden in plaats van een
  harde fout. We accepteren de expliciete MAJOR-bump wanneer we
  een nieuwe top-level key willen toevoegen.

### 6.2 `model` en `permission_mode` leven binnen `settings`, niet top-level

**Gekozen.** `settings.model`, `settings.permission_mode` —
`BlueprintSettings.to_dict()` schrijft de tweede automatisch genest
naar `permissions.defaultMode`.

**Verworpen.**
- *Top-level `model` + `permission_mode` op `Blueprint` zelf.*
  Makkelijker om in YAML te lezen, maar het zou twee verschillende
  waarheidsbronnen creëren (CC leest ze onder `settings.*`, wij
  zouden ze top-level definiëren) en Pydantic's `extra="forbid"`
  zou het überhaupt weigeren. De huidige nesting volgt CC's
  eigen settings-schema letterlijk.

### 6.3 `bypassPermissions` als default voor `agent-service` — **de veiligheidsafweging**

**Gekozen.** Alleen `agent-service` heeft `permission_mode:
bypassPermissions`. Alle andere archetypes krijgen `default` (of
`acceptEdits` voor `web-app-spa`).

**Verworpen.**
- *Geen enkel archetype op `bypassPermissions`.* Te conservatief:
  een orchestration-loop die agent-naar-agent pendelt, moet bij
  élke tool-call permission krijgen — dat is ruis die de
  betrouwbaarheid verlaagt zonder safety-winst (de runtime
  sandbox is de échte grens).
- *Alle archetypes op `bypassPermissions`.* Te permissief: een
  library of CLI die op de gebruikersshell draait, heeft die
  sandbox niet — een auto-accept van een foutieve shell-actie zou
  direct op de machine van de gebruiker landen.
- *`bypassPermissions` optioneel maken in elk archetype, met
  cockpit-default `default`.* Klinkt民主ocratisch, maar legt de
  beslissing bij de operator die 't archetype kiest — en die
  heeft doorgaans geen expliciete safety-intentie op het moment
  van archetype-keuze. Beter: de archetype-naam zelf draagt de
  safety-belofte. `agent-service` zegt "ik draai in een
  geïsoleerde runtime"; `web-app-spa` zegt "mijn writes zijn
  lokaal-dev-reversibel". De naam is de feature, niet de
  form-toggle.

**De safety-compensatie.** Zelfs bij `bypassPermissions` blijven
CC's eigen safety-heuristics actief: `rm -rf /`-achtige patronen,
ongedwongen `--force` op `git push`, en writes buiten de
worktree worden door CC zelf geblokkeerd. Daarnaast draait de
dispatch-laag agent-sessies in een **wegwerp-worktree** — zelfs
als de agent alles zou willen slopen, kan hij alleen de eigen
werkbranch raken, niet de machine. De combinatie
sandbox-runtime + worktree-isolatie + CC's eigen heuristics is
wat `bypassPermissions` voor `agent-service` veilig maakt; geen
van die drie is op zichzelf genoeg.

### 6.4 Geen top-level `commands: list[dict]`-veld op `Blueprint`

**Gekozen.** Een apart top-level `commands`-veld met `BlueprintCommand`-schema.

**Verworpen.** *`commands` als subdir + de individuele commando's
in `subdirs: ["commands"]` registreren zonder lijst.* De
`BlueprintApplyEngine` heeft een `_apply_commands`-methode die
een `commands`-lijst verwacht; zonder zo'n lijst is er geen
manier om project-scoped slash-commands declaratief te zaaien.
De engine-route komt uit `apply_engine.py:_apply_commands`.

### 6.5 De catalogus heeft 5 archetypes, niet meer (MVP)

**Gekozen.** Vijf archetypes die samen het gros van de
doel-applicaties dekken: SPA, REST, CLI, library, agent-service.

**Verworpen.**
- *Eén universeel `default`-archetype* (zoals de huidige
  `cockpit-baseline`-blueprint suggereert). Handig voor MVP,
  maar verliest de archetype-als-feature-claim: de project-keuze
  ("ik ben een SPA") is een waardevolle intent die we nu in de
  `.claude/`-configuratie weerspiegelen.
- *Een archetype per framework* (`react-vite`, `nextjs-app`,
  `sveltekit`, …). Te vroeg — zonder operationele ervaring met
  de eerste vijf is een fijnmazige taxonomie speculatie. Latere
  follow-ups kunnen sub-archetypes introduceren (bv.
  `web-app-spa-react-vite` onder `web-app-spa`).
- *`< 5` archetypes in de MVP.* Met minder dan vijf mist de
  catalogus zijn nut als "menu waar de operator uit kiest"; de
  drie meest-voorkomende typen (SPA, REST, CLI) plus twee
  edge-cases (library, agent-service) geven genoeg
  spreiding zonder de illusie van volledigheid.

### 6.6 Skills in elk archetype, niet alleen de baseline

**Gekozen.** Elk archetype-recept heeft een `skills: [...]`-lijst
met de cockpit-baseline (flag-problem, context-map, session-retro,
git-ship, verification-before-completion, using-git-worktrees) plus
archetype-specifieke toevoegingen (`brainstorming`, `writing-plans`
voor de meeste; compactere set voor CLI/library).

**Verworpen.**
- *Geen skills in een archetype — verwijs naar cockpit-baseline.*
  Een archetype dat een verse checkout zaait zonder skills zou
  de operator dwingen om de baseline apart te installeren — en
  in een `create_project_from_intake`-flow is dat een extra
  stap die kan falen. Beter: elk archetype is een **complete**
  seed die op zichzelf bruikbaar is.
- *Skills als `source: user` zodat ze niet worden
  weggeschreven.* Verliest de discoverability — de operator
  heeft dan geen `.claude/skills/<name>/SKILL.md` om in git te
  reviewen of in PR's te tonen.

## 7. Acceptance criteria voor deze doc

* [x] 5 archetypes met verplicht/optioneel-veldtabel + volledig
      YAML-recept.
* [x] Veld-model + loadability-contract (§1) — alle
      `Blueprint`-velden gedekt; expliciete "geen top-level
      `model`/`permission_mode`"-claim.
* [x] Cross-cuts (privacy/security, model, permission_mode) —
      §3.1, §3.2, §3.3.
* [x] Versie-strategie + backward-compat-belofte — §4.
* [x] Decision-rationale met verworpen alternatieven — §6,
      inclusief de `bypassPermissions`-veiligheidsafweging (§6.3).
* [x] Elk YAML-recept is verifieerd laadbaar via
      `Blueprint.model_validate(yaml.safe_load(recept))`. Verificatie
      is gedaan met een wegwerp-snippet in de engineer-sessie van
      deze kaart (zie session-log); geen runtime-test in de repo.

## 8. Out of scope (expliciet)

* **`BlueprintService.apply()`-implementatie** — sibling-kaart `395590d7`.
* **`BlueprintCommand`-model + `_apply_commands`-uitbreiding** — die
  pipeline is in `apply_engine.py` aanwezig maar wordt door geen van
  deze 5 archetypes gebruikt; een toekomstige `cli-tool` met
  project-scoped slash-commands kan dit alsnog activeren.
* **Frontend / catalogus-UI** — een aparte design- + implementatie-kaart.
* **`BlueprintStore` migratie-pad** voor de `version: int → semver`-overgang —
  sibling-kaart #4 implementeert dit; deze doc levert het beleidskader.
* **Templates** (`python-fastapi`, `react-vite`, …) — die zijn
  template-zorg (zie `repo-provisioning-bootstrap.md §4.1`), geen
  blueprint-zorg. Een toekomstige koppeling "archetype X start met
  template Y" is een eigen ontwerp-iteratie.
