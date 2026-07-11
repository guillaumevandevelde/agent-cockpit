# Repo-provisioning & project-bootstrap: van kanban-artefact naar werkende app-repo

> Kanban-kaart: **`[analyse] Repo-provisioning & project-bootstrap: nieuwe
> app-repo aanmaken, scaffolden, configureren en registreren`** (facet B van
> de parent-kaart *"Deze applicatie als platform om andere applicaties te
> bouwen"*, `8db831a0df6d42689c5b26325b6cbecc`).
>
> Deze doc is een **analyse** — geen implementatie. De actionabele gaten
> worden hieronder expliciet gemaakt en in §6 als concrete
> **Backlog-follow-ups** gefileerd (door de uitvoerende sessie van deze
> kaart, niet door dit document zelf).

## 1. De vraag in één paragraaf

Zodra een idee is omgezet in een **kanban-artefact** (spec + plan, zie facet
A — `product-inceptie-pipeline.md`) ontstaat de **geboorte-stap**: er moet
een **nieuwe app-repo** op de filesystem verschijnen, met een projectskelet,
met `.claude/`-configuratie + project-specifieke agents/skills klaargezet,
geregistreerd in Cockpit, en autodispatch aan — zodat het werk meteen
*binnen dat project* wordt opgepakt.

Het korte antwoord (verder onderbouwd): **er is letterlijk nul code in de
backend die een pad aanmaakt, `git init` aanroept, een template uitspreidt,
of een `.claude/` folder seedt.** Alle bouwstenen die deze stap
*mogelijk* zouden maken bestaan (per-project CRUD voor agents/skills/
settings/commands, autodispatch-toggle, project-registratie, worktree-spawn)
— alleen de **assemblage** ontbreekt. Dit is het grootste structurele gat
en de natuurlijke bouwplaats voor facet B.

## 2. Wat kan vandaag al — en wat níet

Alles in deze sectie is geverifieerd in de repo op de werkende branch
(`k-analyse-repo-2631`).

### 2.1 Repo-creatie, scaffolding, `.claude/`-seeding — status: **afwezig**

Een gerichte greppel door `backend/app/` levert niets op dat lijkt op
"Gebruiker drukt op 'Nieuw project', er verschijnt een werkende repo":

| Gezocht | Gevonden |
|---|---|
| `git init` aanroep in backend | **0 hits.** `subprocess`-aanroepen voor `git` in `backend/app/kanban/dispatch.py` (regel 1115 `git fetch`, 1130 `git worktree remove`) — beide beheer-bestaande-repo, geen aanmaak. |
| `gh repo create` of `gh`-integratie | **0 hits.** Niet in services, niet in MCP, niet in tasks. |
| Scaffolding / template / blueprint-runtime | **0 hits** in `app/services/`. `template_string` verschijnt in `app/services/runs/attachments.py` (prompt-template literal) — ongerelateerd. `template=` in plugin-API calls — geen code-skelet. |
| `ProjectService.add_project` | **Alleen DB-INSERT.** `backend/app/services/project_service.py:45-89`: `INSERT INTO projects (name, path)`; geen `mkdir`, geen `git init`, geen file-creatie. |
| `POST /api/v1/projects` (`add_project` router) | Precies dezelfde surface; geen variant die een pad *aanmaakt* (`backend/app/api/v1/projects.py:34-41`). |
| Frontend `AddProjectDialog.tsx` | Title: *"Add Folder Manually"*. Description: *"Track any folder as a project, even if it has no Claude Code configuration yet."* Dialoog heeft geen "Create new" of "Scaffold" pad. |

**Conclusie:** het ontbreken van repo-creatie/scaffolding is niet een gat in
een bestaande flow — het is een **complete afwezigheid van een flow**.

### 2.2 Wat er wél al staat (de bouwstenen)

| Bouwsteen | Locatie | Wat het wel/niet doet |
|---|---|---|
| `ProjectService.add_project` | `backend/app/services/project_service.py:45` | Registreert een **bestaand** pad in de SQLite `projects`-tabel. Geen directory-creatie, geen git, geen `.claude/`-initialisatie. |
| Per-project agents-CRUD | `backend/app/services/agent_service.py:344` `create_agent(agent, project_path=…)` | Schrijft `<project>/.claude/agents/<name>.md`. **Moet per agent apart aangeroepen worden** — er is geen `seed_agents(blueprint)`-shape. |
| Per-project skills (registry) | `backend/app/services/skills_registry_service.py:257` `install_skill(source, project_path=…)` | Wrapt `npx skills add <gh-repo>` met `--global` of project-cwd. Geen declaratieve "geef me een lijst met skill-names→sources". |
| Per-project commands | `backend/app/api/v1/commands.py:72` `create_command` | Eén slash-command tegelijk; geen batch. |
| Project settings | `backend/app/services/config_service.py:286` `update_settings` | Schrijft `~/.claude/settings.json` of `<project>/.claude/settings.json`. Idempotent via merge. |
| `resolve_project_key` | `backend/app/kanban/project_key.py:38` | `git remote get-url origin` → `git:<host>/<path>`; fallback `slug:<basename>`. Wordt **geheel gepassief** gelezen; er is geen API om de key bij te werken nadat een remote toegevoegd is. |
| `set_autodispatch(session, project_key, enabled)` | `backend/app/kanban/dispatch.py:174` | Schrijft `KanbanMeta(autodispatch:<key>)`. **Bestaat**, maar als interne helper — geen REST/MCP-surface die de UI gebruikt tijdens bootstrap. |
| `spawn_session(... worktree-mode)` | `backend/app/services/runs/spawn.py:141` | Spawn een agent-sessie in een worktree. Werkbaar zodra het project bestaat + autodispatch aan staat. |
| `.claude/`-pad-helpers | `backend/app/utils/path_utils.py:155-199` | `get_project_claude_dir`, `get_project_agents_dir`, `get_project_skills_dir`, `get_project_settings_file`, `get_project_claude_md_file`. Reeds aanwezig — pure pad-derivatie, geen creatie. |

### 2.3 Wat er structureel ontbreekt (deze facet, scope B)

1. **Geen "maak directory aan op disk" service.** Zelfs de meest primitieve
   `mkdir -p /home/me/projects/foo` ontbreekt — er is nul service die voor
   projecten naar disk schrijft behalve de bestaande CRUD die naar een
   al-bestaand pad schrijft.

2. **Geen git-initialisatie.** `subprocess.run(["git", "-C", path, "init"])`
   of equivalent: nergens. Een nieuwe app-repo is dus per definitie geen
   git-repo totdat iemand 'm handmatig initieert.

3. **Geen GitHub-remote-creatie.** `gh repo create` is een fundamenteel
   andere handeling dan lokaal initialiseren — die wordt expliciet
   weggelaten uit scope B en gedelegeerd aan facet D (veiligheid/auth),
   maar de **orchestratie-aansluiting** (waar in de bootstrap-keten
   `gh repo create` ingrijpt, hoe de nieuwe `project_key` wordt
   ge-herderiveerd) is B's verantwoordelijkheid.

4. **Geen project-template / starter-content.** Zelfs als B een
   skeleton-init-service bouwt: er is geen `templates/python-fastapi/`,
   geen `templates/react-vite/`, geen "Hello World" om in te zetten. Het
   eerste kind dat wordt geboren krijgt dus een lege folder — niet
   onacceptabel, maar wel de design-beslissing "leeg vs. minimaal".

5. **Geen `.claude/`-seeding-pipeline.** CRUD werkt per artefact-type
   (agent, skill, command, hook, settings, statusline, output-style).
   Een **declaratieve blauwdruk** die *"deze combinatie hoort bij dit
   project-type"* uitdrukt, ontbreekt. Dit is waar facet A's
   `BlueprintService` (Backlog-kaart `395590d7`) op leunt — B moet de
   **apply-engine** leveren waar die service naar delegateert.

6. **Geen atomic-create met rollback.** Een bootstrap die halverwege faalt
   (git-init gelukt, maar registry-row mislukt) laat een orphan-folder
   achter. Atomic-transaction over filesystem + git + DB + git-remote +
   meta-write is een echte ontwerp-uitdaging (geen echte transacties
   over die grenzen); vereist staging/cleanup-strategie.

7. **Geen first-commit-conventie.** Lege repo's met één lege commit vs.
   initiele README-stub vs. eerste plan-attachment — keuzes die door
   downstream tooling (worktree-spawn, status-checks) geraakt worden.

8. **Geen project-key-migratiepad.** Een project dat als
   `slug:my-cool-app` start (post-init, pre-remote) krijgt een
   *ander* key zodra `gh repo create` + `git remote add origin` lukt
   (`git:github.com/.../my-cool-app`). De huidige `KanbanMeta`-keys
   (`autodispatch:slug:my-cool-app`, `shipmode:slug:my-cool-app`, …)
   worden daardoor mismatch. Er is geen "rename-keys-on-key-change"
   mechanisme.

9. **Geen "boot de autodispatch"-trigger.** Wanneer wordt
   `KanbanMeta:autodispatch:<key>` op `1` gezet? Bij geboorte? Bij
   eerste Backlog-kaart? Bij expliciete UI-actie? Dit is een lifecycle-
   beslissing met security-implicaties (autodispatch-aan =
   bypass-permissions default).

10. **Geen portfolio-aware naamgeving.** Twee product-projecten die
    allebei `slug:my-app` willen heten op hetzelfde device botsen. B
    ontwerpt het namespace-beleid; facet C (portfolio) maakt 't
    cross-device uniform.

### 2.4 Waar facet B bewust niét over gaat (MECE-grens)

- **Inceptie/intake-flow** → facet A
- **`kanban.create_project_from_intake`-MCP-actie als user-facing API** →
  facet A (Backlog-kaart `0260dbcd`)
- **`BlueprintService` data-model + REST-CRUD + frontend** → facet A
  (Backlog-kaart `395590d7`)
- **Portfolio-cap, cross-project dispatch-governance, dashboard** →
  facet C
- **GitHub-auth, secrets, sandbox, CI-runner, run/preview/deploy** → facet D

Drie plekken raken een **overlap** met een andere facet: `gh repo
create`, key-migratie op remote-add, en het feit dat
`BlueprintService.apply` door B's code wordt aangeroepen. De taakverdeling
is: A ontwerpt de user-flow + datamodel, **B ontwerpt en bouwt de
onderliggende engine**, A gebruikt 'm. Dat vermijdt twee motoren voor
dezelfde stap.

## 3. De gewenste bootstrap-keten (end-to-end)

### 3.1 Visueel

```
intake-card (goedgekeurd in facet A)
    │
    ▼
kanban.create_project_from_intake(intake_card_id)   ◀── facet A
    │
    │   facet A delegeert naar facet B:
    ▼
RepoBootstrapService.bootstrap_from_plan(
    intake_card, blueprint_id
)
    │
    ├── 1. atomic-create: staging-directory + plan
    │       ├── mkdir -p <staging>
    │       ├── git init -b main
    │       ├── schrijf .gitignore (+ optioneel LICENSE/README)
    │       ├── eerste commit: "chore: bootstrap from intake <card-id>"
    │       └── [optioneel] gh repo create + git remote add origin
    │
    ├── 2. blueprint-apply (.claude/ seed)
    │       ├── BlueprintService.apply(project_path, blueprint)
    │       │       ├── update_settings(...)
    │       │       ├── create_agent() × N
    │       │       ├── install_skill() × M  (npx skills add ...)
    │       │       ├── create_command() × K
    │       │       ├── CLAUDE.md stub
    │       │       └── idempotency-check (geen overschrijf bestaande)
    │       └── rollback-hook op halverwege-failure
    │
    ├── 3. project-registratie
    │       ├── ProjectService.add_project(path=staging)
    │       ├── resolve_project_key(staging) (post-remote)
    │       └── [indien key gewijzigd] rename KanbanMeta-keys
    │
    ├── 4. autodispatch + skip-permissions toggle
    │       ├── set_autodispatch(session, new_key, True)
    │       └── set_skip_permissions(session, new_key, ??)
    │
    ├── 5. carry-over kaarten
    │       └── intake-kaart 1-op-1 over als Backlog-kaart in nieuw
    │           project + plan_ref-deliverable behouden
    │
    └── 6. promote staging → final path (mv of cp -a)
            └── bij failure in welke stap dan ook: rm -rf staging,
                geen project-row, geen kanban-kaart
```

### 3.2 Eigenschappen van deze keten

- **Atomic-or-nothing.** Elke stap kan falen; de keten heeft een enkele
  cleanup-pad dat *alle* side-effects ongedaan maakt. Geen halve
  projecten, geen orphan-registry-rows.
- **Idempotent blueprint-apply.** `BlueprintService.apply` moet twee keer
  achter elkaar draaien zonder schade (handig voor retry na halve
  failure, en voor projecten die later alsnog van blueprint willen
  wisselen).
- **Key-stability.** De autodispatch-meta-keys blijven hangen aan de
  *finale* project_key, niet aan een tussentijdse slug. Migratie van
  slug→git-key moet transactioneel met de bootstrap gebeuren.
- **Policy-keuzes aan oppervlak.** Steps die beleidsbeslissingen in zich
  bergen (autodispatch-default aan/uit, first-commit-message, .gitignore-
  template-keuze, key-migratie-strategie) komen terecht in één
  `ProjectBootstrapPolicy`-config, niet versnipperd door de code.

## 4. Template- & blueprint-opties met trade-offs

Een **blueprint** (zie facet A) beschrijft *welke configuratie* een project
krijgt. Een **template** beschrijft *welke starter-content* (code/
folderstructuur/CI-files) de repo krijgt. Ze zijn onafhankelijk: een
React-app kan met blueprint "minimal" of "agent-rich" geboren worden,
identieke code, andere `.claude/`.

### 4.1 Template-keuzes

| Template | Wat erin zit | Wanneer passend |
|---|---|---|
| **`empty`** | Alleen `.gitignore`, `.claude/`, README-stub. | Wanneer de gebruiker het skelet zelf wil bouwen. |
| **`python-fastapi`** | `pyproject.toml`, `app/`, `tests/`, FastAPI-router-stub, ruff+pytest config. | REST-API's, micro-services. |
| **`react-vite-ts`** | Vite scaffold, Tailwind/shadcn setup, `eslint.config.js`, `tsconfig.json`. | SPAs, dashboards. |
| **`monorepo-pnpm`** | `pnpm-workspace.yaml`, `packages/*`, turbo/ nx-ondersteuning. | Meerdelige apps. |
| **`cli-typer`** | `pyproject.toml`, `src/<name>/__main__.py`, click/typer-stub. | CLI-tools. |
| **`library`** | `pyproject.toml` of `package.json`, `src/`, `tests/`, public-API-stub. | Herbruikbare modules. |

**Voorgestelde MVP-set:** leeg + python-fastapi + react-vite-ts. Drie
is genoeg om de template-pipeline te valideren; de rest is uitbreiding.

**Trade-off templates vs. live-templates (zoals `create-react-app`):**

| Aspect | Eigen templates | Externe tools (`npm create vite@latest`) |
|---|---|---|
| Voorspelbaarheid | Hoog: vastgelegd in deze repo. | Laag: upstream kan morgen wijzigen. |
| Reviewbaar | Triviale diff. | Onmogelijk up-to-date. |
| Vertraging bij nieuwe frameworks | Ja — handwerk om toe te voegen. | Nee — ondersteund door community. |
| Lock-in | Tegen deze fork. | Tegen de hele Node-ecosystem. |

**Aanbeveling:** eigen templates *voor* de cockpit-default-types
(python-fastapi/react-vite) + een escape-hatch "improvise" die
`npm create …@latest -- --yes` uitvoert voor de rest. De escape-hatch
is gevaarlijk (geen review), maar nuttig voor power-users.

### 4.2 Blueprint-keuzes (overlap met facet A)

| Blueprint | Welke agents | Welke skills | Permission-mode |
|---|---|---|---|
| **`cockpit-baseline`** | (geen project-eigen agents, alleen Claude Code default) | `flag-problem`, `context-map`, `session-retro`, `git-ship` | `default` |
| **`webapp-rich`** | `frontend-reviewer`, `frontend-implementer` (stub-persona's) | bovenstaande + vercel-labs/agent-skills fragmenten | `acceptEdits` |
| **`cli-minimal`** | (geen) | alleen `git-ship`, `verification-before-completion` | `default` |
| **`service-fullstack`** | `backend-engineer`, `frontend-engineer`, `tester` | alle baseline + per-framework subsets | `bypassPermissions` |

**Voorgestelde MVP-set:** alleen `cockpit-baseline`. De rest is een latere
design-iteratie op basis van de eerste gebruikerservaring.

### 4.3 Open ontwerp-beslissingen (input nodig van synthese)

1. **Wordt `autodispatch` standaard aan of uit bij geboorte?**
   *Pro aan:* direct productief, matches de meta-project-flow.
   *Pro uit:* security-default-deny voor een splinternieuw project; gebruiker
   zet 'm zelf aan.
2. **`bypassPermissions` als default permission-mode, of juist conservatief?**
   Volgt direct uit 1, maar is een aparte vraag omdat dispatch deze waarde
   kan overriden per sessie.
3. **Eerste commit: leeg of met README-stub?**
   Lege commit + README-stub bovenop lijkt me het minste werk voor
   downstream tooling (worktree-mode heeft een commit nodig om te kunnen
   branchen).
4. **Wordt CI-bootstrap meegeleverd in de template?**
   `.github/workflows/quality.yml` kopiëren vanuit claude-cockpit zelf is
   verleidelijk maar leidt tot drift (upstream verbetert de workflow
   voortdurend). Beter: apart facet D-traject, of pas bij eerste release.

## 5. Hergebruik van bestaande primitives

Deze facet **voegt niets** toe aan primitives die al bestaan — het
**lijmt** ze aan elkaar. Inventarisatie van wat hergebruikt wordt versus
wat nieuw is:

### 5.1 Hergebruikt (geen wijziging)

- `ProjectService.add_project` — voor de registry-row.
- `AgentService.create_agent(... project_path=...)` — voor project-eigen agents.
- `SkillsRegistryService.install_skill(... project_path=...)` — voor project-eigen skills.
- `CommandService.create_command` — voor project-eigen commands.
- `ConfigService.update_settings` — voor `.claude/settings.json`.
- `set_autodispatch(session, project_key, True)` — voor de toggle.
- `resolve_project_key` — voor de finale key.
- `backend/app/utils/path_utils.py:155-199` — voor pad-helpers.

### 5.2 Nieuw (facet B's bijdrage)

| Component | Verantwoordelijkheid | Tests |
|---|---|---|
| `RepoBootstrapService` | Orchestreert de keten (stap 1-6 hierboven). | E2E: happy path + elke failure-stap faalt schoon. |
| `TemplateService` + `templates/`-folder | Levert starter-content per template-naam. | Snapshot-tests op gegenereerde folders. |
| `BootstrapPolicy` (dataclass) | De beleids-toggles uit §4.3, geconcentreerd. | Unit-tests op policy-resolutie. |
| Atomic-create primitives in `app/utils/repo_utils.py` (of vergelijkbaar) | `mkdir staging`, `mv staging final`, `git init`, eerste commit, `.gitignore` schrijven. | TDD met `tmp_path`-fixtures. |
| Key-migratie helper | Detecteert slug→git-key overgang, hernoemt `KanbanMeta`-keys. | Fixture met twee meta-rows. |
| `gh repo create`-orchestratie (bovenop D's credential model) | Roept `subprocess.run(["gh", "repo", "create", ...])` aan, vangt afwezige auth af, geeft deferred-modus terug. | Mock `subprocess.run`; auth-missing branch. |

### 5.3 Wijzigingen aan bestaande code (klein)

- `ProjectService.add_project` (of een uitbreiding `add_project_or_create`):
  optioneel `create_if_missing=True` die de directory aanmaakt **zonder**
  git — laat git aan `RepoBootstrapService`. Hier is een design-call:
  of `add_project` blijft puur DB, of een "register+create"-variant
  komt erbij. **Voorkeur:** puur houden, orchestratie in
  `RepoBootstrapService`.
- `set_autodispatch` blijft intern; `RepoBootstrapService` is de enige
  aanroeper tijdens bootstrap.
- Geen frontend-wijziging voor facet B an sich — de "Create new project"-
  knop komt uit facet A.

## 6. Actionabele gaten → Backlog-follow-ups

Deze sectie lijst de gaten die het bouwwerk vormen. **Niet** door dit
document geïmplementeerd — door de uitvoerende sessie van deze kaart
worden ze als concrete Backlog-kaarten aangemaakt (met `work_type`,
`metadata.facet="B"`, en korte acceptatiecriteria) zodat ze in de
dispatch-pool terechtkomen voor menselijke triage.

1. **`[feature][bootstrap] RepoBootstrapService: atomic mkdir+git init
   + first commit + .gitignore + README-stub`** — TDD met `tmp_path`,
   happy path + elke failure-stap. Geen `gh`, geen `.claude/`, geen
   project-registratie — puur de filesystem+git-stap. Dit is de
   atomaire grondsteen waar alles op leunt.
2. **`[feature][bootstrap] gh repo create-flow + key-migratie na
   remote-add`** — TDD: mock `subprocess`, missing-auth-graceful
   fallback; helper `migrate_project_keys(old_slug_key, new_git_key)`
   die alle `KanbanMeta`-keys met `META_PREFIX + old` hernoemt naar
   `META_PREFIX + new`. Auth-model zelf → facet D.
3. **`[feature][bootstrap] templates/ folder + TemplateService** —
   catalogus van project-starters (lege, python-fastapi, react-vite),
   `TemplateService.list_templates()` + `render(template_name, path, vars)`.
   Idempotent (overschrijf-nooit zonder `--force`). Drie concrete
   templates met werkende test-snapshots.
4. **`[feature][bootstrap] BlueprintApply-engine (de motor achter
   BlueprintService.apply uit facet A)`** — Pure orchestration: roept
   bestaande CRUD aan in de juiste volgorde, heeft rollback bij halverwege
   failure, is idempotent. Geen data-model (dat is facet A's
   `395590d7`); B levert de uitvoerder.
5. **`[design][bootstrap] ProjectBootstrapPolicy — de 'cockpit-defaults'-
   configuratie`** — één `BootstrapPolicy`-dataclass met de open
   beslissingen uit §4.3: autodispatch-default, permission-mode,
   first-commit-message, .gitignore-keuze, CI-bootstrap ja/nee.
   `work_type=analysis`. Resultaat = `docs/cockpit/bootstrap-policy.md`
   met de besluiten + de rationale, plus een prototype van de
   `BootstrapPolicy`-dataclass die de implementatie-kaarten consumeren.
6. **`[feature][bootstrap] cockpit-baseline blueprint: lijst van agents/
   skills die élk product-project meekrijgt`** — implementatie van de
   `cockpit-baseline`-blueprint (zie §4.2). Bevestigt welke Skills uit
   de huidige set (flag-problem, context-map, session-retro, git-ship,
   verification-before-completion, superpowers:brainstorming) universeel
   zijn vs. project-specifiek. **Out of scope:** andere blueprints
   (`webapp-rich` etc.); die volgen zodra cockpit-baseline landt.
7. **`[feature][bootstrap] KanbanMeta key-migratie helper** — pure
   utility, los van de gh-flow (`#2`); ook zelfstandig nodig voor
   toekomstige "ik hernoem mijn repo"-flows. `migrate_project_keys(
   old_key, new_key)` met uitgebreide unit-tests op edge cases (geen
   overlap, volledige overlap, lange keys, idempotency).

## 7. Niet in deze facet (expliciete out-of-scope)

- **Inceptie/intake-flow, intake-kolom, MCP-actie
  `kanban.create_project_from_intake`** → facet A (al gefileerd in Backlog).
- **`BlueprintService` data-model + REST + UI + typologie-design** →
  facet A (al gefileerd in Backlog).
- **`work_type="intake"`-routing** → facet A.
- **`brainstorming`-user-approval-vertaling** → facet A.
- **Kanban-deliverable-kind `spec`** → facet A.
- **Portfolio-cap, cross-project dispatch-governance, portfolio-dashboard** →
  facet C.
- **GitHub-auth credentials, secrets-model, sandbox/transports,
  CI-runner, run/preview/deploy, write-anywhere-beveiliging** → facet D.
- **Database-migratie-systeem (relevant als `KanbanMeta`-schema
  wijzigt; voor key-hernoeming is dat *niet* nodig — keys blijven
  varchar)** → apart traject (zie Backlog-kaart `4b6a3846`).

## 8. Relatie met de andere facetten

(MECE + overlapkaart.)

- **Facet A** (intake): levert de *trigger*. Facet B bouwt de *uitvoerder*.
  De user-facing API-call `create_project_from_intake` (A-kaart
  `0260dbcd`) is een dunne wrapper rond `RepoBootstrapService.bootstrap_from_plan`
  (deze facet). Spec / plan / kanban-deliverable: A. Repo + `.claude/` + keys: B.
- **Facet C** (portfolio): consumeert de key-migratie-helper van B, en
  ontvangt autodispatch-events die B afvuurt. C ontwerpt de
  cross-project-cap; B maakt 'm per-project mogelijk.
- **Facet D** (veiligheid): bezit het auth-model achter `gh repo create`
  (welke token, welke scope, multi-user); B's `gh`-orchestratie leent
  dat. D bezit sandbox/CI/secrets; B neemt die configuraties over van
  `BootstrapPolicy`.

## 9. Kernbevinding (voor de ouder-comment)

> Cockpit heeft vandaag **alle bouwstenen** voor een werkende
> project-bootstrap (per-project CRUD voor agents/skills/commands/
> settings, `add_project`-registry, `set_autodispatch`-toggle, worktree-
> spawn), maar **nul assemblage**: nergens `git init`, `mkdir`,
> `gh repo create`, scaffolder, noch een `.claude/`-seeding-pipeline.
> De gewenste keten is atomic-create met rollback, zes stappen breed
> (mkdir → git init → blueprint-apply → register → autodispatch-aan →
> carry-over-card), waarbij stap 2-6 elk één of meer nieuwe services
> vergen die elk bestaande primitives orkestreren zonder ze te wijzigen.
> Templates (lege/python-fastapi/react-vite) én blueprints
> (cockpit-baseline eerst) zijn aparte ontwerp-assen; één MVP-set is
> voldoende om de pipeline te valideren. Zeven actionabele gaten zijn
> als Backlog-follow-ups gefileerd (geen overlap met de al gefilede
> facet-A-kaarten); geen daarvan wordt door deze kaart geïmplementeerd.
