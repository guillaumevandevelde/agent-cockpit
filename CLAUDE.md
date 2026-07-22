# ⚠️ Fork: Claude Cockpit — lees dit eerst

Dit is een **fork** van claude-deck, hernoemd naar **Claude Cockpit**. De oorspronkelijke
**scheduled-messages** feature (timer/cron → injectie in CC-sessies via tmux) is
**vrijwel af** — Tasks 1–11 van `docs/cockpit/fase-2-plan.md` zijn geïmplementeerd
(backend-tests groen, frontend build clean), alleen **Task 12 (runtime e2e)** en de
**fase-1 runtime-checklist** (`fase-1-validation.md`) zijn mensenwerk.

Het zwaartepunt van de **actieve** ontwikkeling ligt nu bij de kanban-/multi-agent-laag:

- **Kanban auto-dispatch** — de dispatcher claimt + spawnt Todo-kaarten
  (`docs/cockpit/kanban-dispatch-spec.md`, follow-ups in `kanban-followups.md`).
- **Multi-agent kanban** — analyst-fase splitst parent-kaarten op in kind-kaarten met
  afhankelijkheids-DAG en plan-attachment; executors wachten op hun deps
  (`docs/cockpit/multi-agent-kanban.md`).
- **Kanban string-conventies** — vast kolommen (`COLUMNS` vs `_DISPATCH_COLUMNS`),
  comment-prefix-contract (`**Summary:**`/`**Impediment:**`/`**Resolution:**`/…),
  deliverable-kinds (`pr`/`branch`/`commit`/`link`/`note`/`plan`/`plan_ref`/`spec`).
  Lees vóór je een nieuwe vaste kolom introduceert of een Done/Impediment-comment
  post: `docs/cockpit/kanban-conventions.md`. Validatiescript:
  `scripts/check-kanban-conventions.sh`.
- **Agent Mail** — cross-session berichten tussen willekeurige sessies met durable
  repo-identiteit en inspectable mailbox (`docs/cockpit/agent-mail-spec.md`).

- **Volledige oriëntatie + huidige taak:** `docs/cockpit/00-orientation.md`
- **Is X al beslist?** `docs/cockpit/decisions.md` — chronologisch beslis-register (datum,
  vraag, uitkomst, doc-link, kaart-id) over álle `*-decision.md`-docs en §-forks. **Kijk
  hier vóór je een productbeslissing heropent of opnieuw uitzoekt.** Rond je een
  `[beslissing]`-kaart af, voeg dan een regel toe; `scripts/check-decision-register.sh`
  flag't een beslisdoc zonder register-regel.
- **Scheduled-messages plan (vrijwel af):** `docs/cockpit/fase-2-plan.md`
- **Huidige open pool:** `docs/cockpit/kanban-followups.md`
- **Omgeving:** WSL Ubuntu, Docker (`docker compose up -d` → :8000), tmux, claude CLI.

Hieronder volgt de oorspronkelijke claude-deck-documentatie (codebase-structuur etc.).

---

## Doel & oriëntatie

Agentic developers platform: agents bouwen/beheren (1) externe applicaties — primaire
bestaansreden — en (2) deze codebase continu. De orchestratie-kern (dispatch, worktrees,
agent mail, dependency-DAG, session-lifecycle) is agent- en repo-onafhankelijk ontworpen.
Volledige missietekst, kernprincipes en zelfverbeteringsdoelen: `docs/cockpit/00-orientation.md`.

---

# Claude Cockpit

Web app for managing Claude Code configurations, MCP servers, commands, plugins, hooks, and permissions.

## Commands

```bash
# Install
./scripts/install.sh             # Setup venv, install deps, create dirs (Python 3.11+, Node 18+)

# Development
./scripts/dev.sh                 # Start both backend + frontend servers (attached, Ctrl+C to stop)
cd backend && source venv/bin/activate && uvicorn app.main:app --reload --port 8000  # Backend only
cd frontend && npm run dev       # Frontend only (port 5173)

# Self-healing dev stack (detached supervisor: auto-restart on crash, logs to logs/, survives terminal close)
./scripts/cockpit.sh start       # Start backend+frontend supervised (auto-installs missing/stale deps)
./scripts/cockpit.sh status      # Show supervisor/backend/frontend status
./scripts/cockpit.sh logs backend  # Follow backend logs (or: logs frontend)
./scripts/cockpit.sh restart     # Stop, then start
./scripts/cockpit.sh stop        # Stop supervisor + all processes
bash scripts/test_cockpit.sh     # Test the supervisor (bash harness)

# Build
./scripts/build.sh               # Production frontend build → frontend/dist
cd frontend && npm run build     # Same as above

# Test
bash backend/test_commands_api.sh                         # Curl-based API tests
bash scripts/test_pytest_baseline.sh                      # Bash tests for pytest-baseline / pytest-compare scripts
bash scripts/test_baseline_bash_tests.sh                  # Bash tests for baseline-bash-tests / compare-bash-tests scripts
bash scripts/test_check_analysis_outcomes.sh              # Bash tests for check-analysis-outcomes.sh
bash scripts/test_check_decision_register.sh              # Bash tests for check-decision-register.sh
bash scripts/test_check_doc_links.sh                      # Bash tests for check-doc-links.sh
bash scripts/test_check_kanban_conventions.sh             # Bash tests for check-kanban-conventions.sh (synthetic SQLite fixtures)
bash scripts/test_check_litellm_hardening.sh              # Bash tests for check-litellm-hardening.sh (synthetic stdlib http.server fake proxy)
bash scripts/test_check_problem_card_staleness.sh         # Bash tests for check-problem-card-staleness.sh
bash scripts/test_check_schema_rename_coverage.sh         # Bash tests for check-schema-rename-coverage.sh
bash scripts/test_check_test_harness_coverage.sh         # Bash tests for check-test-harness-coverage.sh
bash scripts/test_measure_cache_read_quota.sh            # Bash tests for measure-cache-read-quota.sh (offline price-reconstruction + fit-CLI asserts)
bash scripts/test_run_single_test.sh                      # Bash tests for run-single-test.sh
bash scripts/test_list_orphan_bridge_sessions.sh           # Bash tests for list-orphan-bridge-sessions.sh (uses real tmux sessions)
bash scripts/test_sweep_dangling_depends_on.sh             # Bash tests for sweep_dangling_depends_on.py (synthetic SQLite fixtures)
bash scripts/test_sweep_dangling_plan_refs.sh             # Bash tests for sweep_dangling_plan_refs.py (synthetic SQLite fixtures)
bash scripts/test_generate_doc_index.sh                    # Bash tests for generate-doc-index.py (synthetic frontmatter fixtures)
bash scripts/test_worktree_gc.sh                          # Bash tests for worktree-gc.sh
bash scripts/test_worktree_trap.sh                        # Bash tests for scripts/lib/worktree-trap.sh (mktemp-d cleanup trap)

# Single-test run = the documented exception to feedback_no_local_pytest (<1.5s; zie kaart ed09173c).
bash scripts/run-single-test.sh tests/test_x.py                  # whole file
bash scripts/run-single-test.sh tests/test_x.py::test_y          # one test
bash scripts/run-single-test.sh tests/test_x.py -k "param_id"    # pytest -k filter

# Docs / decision register
./scripts/check-decision-register.sh          # Flag any docs/cockpit/*-decision.md missing from decisions.md (advisory; --strict = exit 1)
./scripts/check-doc-frontmatter.sh            # Flag docs/cockpit/*.md zonder OKF-frontmatter of met onbekende type/status (advisory; --strict = exit 1)
./scripts/check-doc-links.sh                  # Flag relatieve Markdown-links in docs/cockpit/*.md met ontbrekend target (advisory; --strict = exit 1)
./scripts/check-test-harness-coverage.sh      # Flag scripts/test_*.sh niet in de # Test-blok van CLAUDE.md (of vice-versa); advisory + --strict (zie kaart 5e988e4e)
./scripts/generate-doc-index.py               # Regenereer de README-index (100% dekking, gegroepeerd op type + status-badges) + docs/cockpit/llms.txt uit de frontmatter
./scripts/generate-doc-index.py --check --strict  # Faal als de gegenereerde index/llms.txt out-of-sync is met de frontmatter (advisory zonder --strict)

# Analysis outcome sweeper (vangnet voor het REST-gat + historische voorraad)
./scripts/check-analysis-outcomes.sh          # Flag Done-analyses zonder Outcome-comment/label/kinderen (advisory; --strict = exit 1; --since YYYY-MM-DD voor historic-grens)

# Dangling-dep sweepers (vangnet voor verweesde kanban-references — advisory; --strict = exit 1; JSON op stdout)
./scripts/sweep_dangling_depends_on.py        # Flag niet-Done kaarten waarvan een depends_on-id naar een niet-bestaande kaart verwijst
./scripts/sweep_dangling_plan_refs.py         # Flag plan_ref-deliverables waarvan de parent of het plan niet meer resolvet

# Lint
cd frontend && npm run lint      # ESLint

# Pytest baseline (attribute pre-existing failures on origin/master — kanban card 4c7c5346)
./scripts/pytest-baseline.sh                # Capture pre-existing failures (idempotent, cached 24h)
./scripts/pytest-compare.sh                 # Run pytest + classify: pre-existing / NEW / FIXED

# Bash-test baseline (attribute pre-existing scripts/test_*.sh failures on origin/master — kanban card ecea763e)
./scripts/baseline-bash-tests.sh            # Capture pre-existing bash-test failures (idempotent, cached 24h)
./scripts/compare-bash-tests.sh             # Run scripts/test_*.sh + classify: pre-existing / NEW / FIXED

# Version
./scripts/bump-version.sh <major|minor|patch>  # Sync version across VERSION, package.json, pyproject.toml
```

## Architecture

```
backend/                  # FastAPI + async SQLAlchemy + aiosqlite
├── app/
│   ├── main.py          # FastAPI app, CORS, lifespan
│   ├── config.py        # pydantic-settings (defaults in code, no .env required)
│   ├── database.py      # Async SQLAlchemy engine + session
│   ├── api/v1/          # 30 route modules (router.py aggregates all), incl. subdir routers:
│   │                    #   cc_bridge/, kanban/, runs/, sandcastle/, scheduled_messages/
│   ├── models/          # database.py (ORM), schemas.py (Pydantic)
│   ├── services/        # 58 service files (business logic), incl. subdirs:
│   │                    #   agentic_cli/, agent_mail/, blueprint/, runs/, scheduling/, templates/
│   └── utils/           # path_utils, file_utils

frontend/                 # React 19 + Vite + TypeScript + shadcn/ui
├── src/
│   ├── App.tsx          # Routes (29 pages)
│   ├── features/        # Feature modules (26 dirs, each with page + components + API + types)
│   ├── components/      # layout/, shared/, ui/ (19 shadcn components)
│   ├── hooks/           # useApi, useProjects, useSessionsApi, useUsageApi
│   ├── contexts/        # ProjectContext, ThemeContext
│   ├── types/           # Shared TypeScript types (15 files)
│   └── lib/             # api.ts, constants.ts, utils.ts
```

### Features

Config, MCP Servers, MCP Server (registry), Commands, Plugins, Hooks, Permissions, Agents, Agent Performance, Skills, Memory, Context, Projects, Backup, Output Styles, Status Line, Sessions, CC Bridge, Kanban, Scheduled Messages, Plans, Presence, Sandcastle, APM, Usage, Updates, Dashboard

### API Routes

All under `/api/v1/`: config, projects, cli, mcp, mcp-server, commands, plugins, hooks, permissions, agents, agent-activity, backup, output-styles, statusline, sessions, usage, memory, context, plans, presence, providers, codex-config, status, apm, files, plus subdir routers: cc-bridge, agent-bridge, kanban, scheduled-messages, sandcastle

## Key Decisions

- **Backend**: FastAPI + async SQLAlchemy + aiosqlite + SQLite
- **Frontend**: React 19 + Vite 7 + TypeScript + TailwindCSS + shadcn/ui
- **Database**: SQLite at `backend/claude_registry.db` (auto-created via `create_all`, no migrations)
- **API**: RESTful `/api/v1/`, Vite proxies `/api` → `http://localhost:8000`
- **CORS**: `localhost:5173`

## Code Style

- **Frontend**: ESLint + TypeScript strict mode (`noUnusedLocals`, `noUnusedParameters`). Path alias `@/*` → `./src/*`
- **No impure calls in render**: the react-compiler ESLint rule rejects `Date.now()` / `Math.random()` (etc.) called directly in a component's render body — including as an inline argument expression, e.g. `formatLabel(Date.now())` inside JSX/render. Move the impure call inside the helper function itself instead (see `isFutureSchedule` in `frontend/src/features/kanban/components/CardItem.tsx`), otherwise `npm run lint` fails with `Cannot call impure function during render`.
- **Backend**: Type hints throughout, async/await patterns, pydantic models for validation
- **Test doubles: patch where the consumer looks; assert the double fired.** `from app.module import name` binds the function object into the consumer's namespace **at import time**, so a patch on the *source* module (`monkeypatch.setattr(src_module, "name", patched)`) does **not** reach that binding — the consumer keeps calling the original. Three rules to make this class of no-op patch impossible to write *or* detect: (1) patch the consumer, (2) or switch the consumer to module-attribute access, (3) always assert the double fired. Concrete failure + reviewer grep-recept: `docs/cockpit/test-doubles-convention.md` (zie ook [subscription-pool-analyse §3](./docs/cockpit/subscription-pool-dispatch-analyse.md) / kanban-kaart `ea7e038b…`).

## UI Conventions

- **Clickable cards**: All clickable Card components must use the `CLICKABLE_CARD` constant from `@/lib/constants`. This gives a consistent `border-2 hover:border-primary/50` orange border hover effect, plus `cursor-pointer`, `transition-colors`, and `focus-visible:ring-2` for keyboard a11y. Action buttons inside clickable cards must use `e.stopPropagation()` and keyboard handlers must support Enter/Space.
- **Modal sizes**: Use `MODAL_SIZES.SM`, `MODAL_SIZES.MD`, or `MODAL_SIZES.LG` from `@/lib/constants` for dialog sizing.
- **Markdown rendering**: Use `<MarkdownRenderer>` from `@/components/shared/MarkdownRenderer` for read-only markdown display. Use `<MarkdownPreviewToggle>` from `@/components/shared/MarkdownPreviewToggle` for editable markdown with Edit/Preview tabs.
- **Browser-verifying a UI change when the shared dev stack is busy**: if `./scripts/cockpit.sh start` refuses to start because another concurrent session already holds ports 8000/5173, don't wait for it and don't use the live kanban board as a screenshot fixture. Follow `docs/cockpit/isolated-component-preview.md` — a scratch Vite entry on an unused port mounts just the changed component (with a minimal fixture), then Playwright screenshots it in light + dark.

## Git Workflow

Ship-recipes (sync, frontend-gate, detached-worktree merge, PR-poll, worktree-gc,
regels) leven in **`.claude/skills/git-ship/SKILL.md`** — bron van waarheid. Dezelfde
tekst wordt via `_build_ship_instructions` in `backend/app/kanban/dispatch.py`
geïnlined in de dispatch-prompt voor agents die de skill niet kunnen lezen. Wijzig
de skill **en** sync de dispatch.py-mirror in dezelfde commit (zie de drift-val uit
Done-kaart `d9447e49`).

- **Worktree-gc** (`scripts/worktree-gc.sh`): reclaimt merged-but-never-Done
  worktrees — alleen als (a) clean, (b) gemerged in master, en (c) niet
  vastgehouden door een actieve `agent:` claim (kaart niet in Done/Impediment).
  `cockpit.sh start` nudged wanneer leftovers bestaan.
- **Harness-script scratch worktrees** moeten `scripts/lib/worktree-trap.sh`
  sourcen en `with_scratch_worktree <repo> WT` gebruiken in plaats van
  `WT="$(mktemp -d -p "$REPO_ROOT")/wt-$$"` + handmatige EXIT-trap. De naive
  `mktemp -d -p` shape laat de `.tmp.<id>`-parent in de werkboom achter bij elke
  iteratie (`ls` ziet 'm niet, dus accumulatie bleef onopgemerkt — kaart
  `5c508644…` moest er zes handmatig `mv`-en). De helper bezit zowel het
  worktree als zijn `tmp-<id>`-parent en ruimt beide op in alle EXIT-paden
  (success, error, signal). Belangrijk: `with_scratch_worktree` moet in de
  parent-shell draaien (redirect stdout naar een tempfile of lees een
  globale variabele) — `$()` zandbakst het in een subshell waar de
  geïnstalleerde trap verloren gaat. De helper defaultt op `HEAD`; een harnas
  dat een vaste baseline nodig heeft geeft die als derde argument mee, bv.
  `with_scratch_worktree "$REPO_ROOT" WT origin/master`.
- **Geen lokale pre-push gate** (sinds 2026-07-05): full pytest + lint/build liep
  in CI (`quality.yml`). Backend pytest + ruff en frontend lint/test/build draaien
  in CI als backend/frontend-gate; draai zelf de frontend-checks voor ships die
  `frontend/` raken (zie git-ship §2). `scripts/cockpit-doctor.sh` is de
  read-only health-check.
- **Remote branch hygiene**: `delete_branch_on_merge` is enabled (2026-07-07),
  dus PR-branches ruimen zichzelf op bij merge. Branches van PRs die nooit
  mergen stranden op `origin` — handmatige `git cherry master origin/<branch>`
  + delete.

## CI/CD

GitHub Actions workflows in `.github/workflows/`:
- `claude.yml` — Claude Code integration (triggers on @claude mentions)
- `codeql.yml` — CodeQL security analysis
- `quality.yml` — backend (ruff + pytest) and frontend (lint + test + build), on push/PR to `master`
- `release.yml` — Manual release (builds frontend, creates GitHub release)

## Gotchas

- No `.env` file needed — all config has defaults in `backend/app/config.py`
- Database lives at `backend/claude_registry.db`, created automatically on first run
- No database migration system — schema changes require deleting the db
- Backups stored in `~/.claude-registry/backups/`
- `rm` is blocked via `.claude/settings.json` (`Bash(rm:*)` deny) — use `mv` to move unwanted files outside the repo, or `git clean -f -- <path>` for untracked files, instead
- **`pkill -f` / `pgrep -f` in een gedispatchte sessie is zelf-vallend.** De dispatcher spawnt elke sessie als `claude --dangerously-skip-permissions --model <ali> <VOLLEDIGE PROMPT>` — de hele persona + kaarttekst staat letterlijk in `/proc/<pid>/cmdline` van de eigen shell (zie `backend/app/services/agentic_cli/claude_code.py:82-83`, prompt wordt als positional `argv`-element doorgegeven). Elk patroon dat toevallig een woord uit die prompt matcht, raakt daardoor de **eigen sessie** of een **concurrente gedispatchte sessie** op deze gedeelde box. Symptomen uit deze sessie zelf: `pkill -9 -f "probe4"` killde de eigen shell (het woord stond in de eigen cmdline); `pgrep -af "stream-json"` matchte de eigen `claude`-sessie omdat "stream-json" in de kaarttekst voorkwam. Blast radius: eigen sessie killen → de `agent:` claim op de kaart blijft hangen → reaper ziet de dode claim → release + re-dispatch (kaartcontext + werk verloren); erger nog, `pkill -f claude` of `pkill -f stream-json` zou ook andere agents' sessies op deze box omleggen. **Gebruik in gedispatchte sessies nooit `pkill -f`/`pgrep -f` met een patroon dat in je prompt kan voorkomen.** Veilige alternatieven: (1) **PID** — bewaar de PID van een zelf gestart proces (`echo $!` direct na spawn, of schrijf 'm naar een pidfile) en kill die specifieke PID met `kill $PID`; (2) **uniek token** — plak een zelf-gegenereerd token dat nergens in je prompt voorkomt in zowel het commando als de cmdline van het doelproces, bv. `pkill -f "myjob-$(uuidgen)"`. Voor eenmalig lokaal opruimen buiten een dispatch-context: gebruik een exacte processnaam zonder `-f` (`pkill nginx`, niet `pkill -f nginx`).
- **`git stash apply stash@{N}` is unsafe in shared multi-session worktrees.** `git stash list` is per-worktree, not per-session: two Claude Code sessions dispatched into the same worktree (or a resumed session in a worktree a prior session used) see each other's stashes, and the dispatcher does not always clean up a prior session's stash — especially on the impediment/failure exit path. A `stash@{0}` you did not create yourself can silently be a stale stash left by a session that ended hours ago. Applying it can produce merge conflicts, and a follow-up `git reset --hard` to abort that apply can **delete your own uncommitted files** — this burned 7 modified files in one session (kaart `31c30dbb…`). Before `git stash apply stash@{N}`, verify ownership first: `git stash show -p stash@{N}` or pick by message (`git stash list --format='%gd %s'`). Better yet, skip stash entirely — for a read-only "is this failure pre-existing on `origin/master`" check, use `scripts/pytest-baseline.sh` + `scripts/pytest-compare.sh` (or the `iteration-loop` skill's `pytest-attr` preset), which diff against a detached `origin/master` worktree and never touch your working tree.
- **Backend log timestamps zijn UTC ISO 8601** (`"2026-07-14T08:49:10.867Z"`, `Z`-suffix). Kanban-DB `created_at`/activity-timestamps zijn óók UTC, dus een log-dive vanaf een kaart-timestamp kan direct gedaan worden zonder `+2u`-correctie. Logs van vóór 2026-07-14 (`logs/backend/run-*.log` met prefix-datum) zijn nog in lokale CEST (`09:49:10` = UTC `07:49:10`); check de datum in de bestandsnaam om de era te bepalen.
- **Kanban-router: een vastgehouden `service.get_card`-pre-check vergiftigt de post-commit `_reload`.** `service.get_card` doet `selectinload(deliverables, attachments)` en de sessie draait met `expire_on_commit=False`. Een loader-optie her-populeert géén relationship die al geladen is op een instance in de identity-map (dat vereist `populate_existing()`), dus `_reload` geeft de **pre-mutatie**-collectie terug. Het bijt alleen als *beide* gelden: (1) het pre-check-resultaat is aan een **levende variabele** gebonden — de identity-map houdt weak refs, dus een ongebonden `if await service.get_card(...) is None:` wordt direct opgeruimd en triggert dit níet; en (2) de op wijzigt collectie-**membership** (INSERT/DELETE van een deliverable/attachment-rij) — een ORM-UPDATE van een al geladen rij synchroniseert wél, waardoor `update_plan_attachment` veilig is ondanks zijn gebonden `card`. Schrijf je een handler die een deliverable/attachment toevoegt of verwijdert, doe de existence-check dan met `await s.get(KanbanCard, cid)` (relationships blijven unloaded, zie `upload_attachment`), of `s.expire_all()` na de commit. Volledige uitleg: de `_reload`-docstring in `backend/app/api/v1/kanban/router.py`.
- **`gh api`-URL's met een query-string zijn in zsh verplicht dubbelgequote.** De dispatch-shell is zsh (zie omgevingsblok), en `?` globt standaard. Een ongequote `gh api "repos/OWNER/REPO/git/trees/main?recursive=1"` faalt met `(eval):1: no matches found: …` — het commando **draait nooit**, maar de foutmelding leest als "lege API-respons" en kost je een ronde om te ontdekken wat er werkelijk gebeurde. Quote dus altijd: `gh api "repos/OWNER/REPO/git/trees/main?recursive=1"` (dubbele quotes om de hele URL), of gebruik enkele quotes als je shell-variabelen wilt interpoleren. Idem voor `?`-parameters in raw-URL's (`curl "https://raw.githubusercontent.com/.../main/README.md"`). Geldt voor élke sessie die `gh api` met query-parameters gebruikt (research-kaarten, market-research-sweeps, integratie-analyses).
- **GitHub default-branch ≠ `main`.** Deze repo én een flink deel van de populaire ecosystemen (zoals de 9router-repo in kaart `27cdc2bd…`) gebruiken nog `master` als default. `raw.githubusercontent.com/OWNER/REPO/main/README.md` geeft dan een 404 die lijkt op "repo bestaat niet". Resolve de default branch expliciet met `gh api repos/OWNER/REPO --jq .default_branch` en interpoleer die in plaats van `main` te gokken. Voorbeeld-patroon: `BRANCH=$(gh api repos/O/R --jq .default_branch); gh api "repos/O/R/contents/README.md?ref=$BRANCH"`.
