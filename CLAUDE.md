# ⚠️ Fork: Agent Cockpit — lees dit eerst

Dit is een **fork** van claude-deck, hernoemd naar **Agent Cockpit**. Het zwaartepunt van de **actieve** ontwikkeling ligt nu bij de kanban-/multi-agent-laag:

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
- **Externe credentials voor spikes** — een kaart die een betaalde of key-gated
  provider meet, noemt de verwachte env-var of `credential_name` én het
  resolutiepad. Controleer SecretStore-namen read-only met
  `GET /api/v1/secrets/?project_key=<project-key>`; de response bevat namen,
  nooit waarden. Zie §3c van `docs/cockpit/kanban-conventions.md`.
- **Agent Mail** — cross-session berichten tussen willekeurige sessies met durable
  repo-identiteit en inspectable mailbox (`docs/cockpit/agent-mail-spec.md`).
- **Taalgebruik** — élke tekst die een mens leest volgt één norm: docs,
  `CLAUDE.md`, persona's, skills, kaarttitels en Done-samenvattingen. De norm:
  conclusie eerst, maximaal 40 woorden per zin, diepte achter een verwijzing die
  zegt wát daar staat, en een kaart-id nooit als enige onderbouwing. Meet vóór je shipt met
  `scripts/check-doc-readability.py --file <pad>`. Norm en woordenlijst:
  `docs/cockpit/taalgebruik-conventies.md`. Schrijf je nieuwe uitleg en wordt die
  langer dan drie alinea's, zet de diepte dan in een eigen document en houd de
  conclusie plus de link in de hoofdtekst.

- **Volledige oriëntatie + huidige taak:** `docs/cockpit/00-orientation.md`
- **Is X al beslist?** `docs/cockpit/decisions.md` — chronologisch beslis-register (datum,
  vraag, uitkomst, doc-link, kaart-id) over álle `*-decision.md`-docs en §-forks. **Kijk
  hier vóór je een productbeslissing heropent of opnieuw uitzoekt.** Rond je een
  `[beslissing]`-kaart af, voeg dan een regel toe; `scripts/check-decision-register.sh`
  signaleert een beslisdoc zonder register-regel.
- **Scheduled-messages plan (vrijwel af):** `docs/cockpit/fase-2-plan.md`
- **Huidige open pool:** `docs/cockpit/kanban-followups.md`
- **Omgeving:** WSL Ubuntu, Docker (`docker compose up -d` → :8000), tmux, claude CLI.

Hieronder volgt de oorspronkelijke claude-deck-documentatie (codebase-structuur etc.).

---

## Doel & oriëntatie

**Agent Cockpit is een persoonlijke agent-cockpit: één toegangspoort tot een divers
team agents.** De eigenaar dient werk in, de agents voeren het uit, en de cockpit meldt
terug wat aandacht vraagt. Dat werk is niet alleen software bouwen — ook onderzoek en
kenniswerk lopen door dezelfde machinerie, met een lichter ceremonieprofiel. De cockpit
levert de uitvoeringsvloer (kanban-dispatch, multi-agent-decompositie, worktrees, agent
mail, dependency-DAG, session-lifecycle). De orchestratie-kern is agent- en
repo-onafhankelijk ontworpen, zodat elke agent-runtime en elk doel-repo eronder past.

De toepassing draait op de eigen machine en is van overal bereikbaar via een tunnel of
tailnet; uitvoering verhuist niet naar een server. Meldingen vuren alleen bij een
blokkade die de eigenaar nodig heeft of bij iets dat stuk is.

> **Herzien op 2026-08-15.** Dit was eerder "een agentic software factory". Die framing is
> vervangen; het geërfde Claude Code-beheerpaneel is nu een kostenpost die krimpt, geen
> product. Beslissing en onderbouwing: [`docs/cockpit/cockpit-richting-decision.md`](./docs/cockpit/cockpit-richting-decision.md).

Volledige missietekst, kernprincipes en zelfverbeteringsdoelen:
`docs/cockpit/00-orientation.md`.

---

# Agent Cockpit

**Persoonlijke cockpit voor een divers team agents**: werk indienen, autonoom laten
uitvoeren — software én kenniswerk — en gedoseerd terugkoppeling krijgen. De web-app
beheert daarnaast nog de Claude Code-omgeving zelf (configuraties, MCP servers,
commands, plugins, hooks, permissions). Dat deel is geërfd van claude-deck en krimpt
volgens de snoeiregels in
[`docs/cockpit/cockpit-richting-decision.md`](./docs/cockpit/cockpit-richting-decision.md) §6.

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
> Note for authors writing `scripts/test_check_*.sh` harnesses: real-state
> assertions must be SPECIFIC, not tautological. Assert the exact clean-state
> line emitted by the SUT (e.g. `grep -qE "^OK: <expected phrase>"`) — never
> `grep -qE "^OK:|WARNING:"`, which passes in both broken and fixed states
> (the smoking gun behind self-improve card e5136a3f). If a WARN branch is
> acceptable, document the carve-out at the assertion site, not in the grep
> itself.
bash backend/test_commands_api.sh                         # Curl-based API tests
ls scripts/test_*.sh     # family-level reference — check-test-harness-coverage.sh (kaart 5e988e4e, glob-form uit 8c7cfc14) dekt het hele scripts/test_*.sh spectrum
bash scripts/test_po_digest_source.sh                  # PO-digest collector (mechanische helft)
bash scripts/test_check_file_size_ratchet.sh           # Omvangsratel: bestanden boven 800 regels mogen niet groeien (architectuur.md regel 3)
bash scripts/test_check_doc_readability.sh             # Leesbaarheidsnorm-meter (zie de Taalgebruik-regel hieronder)
bash scripts/test_check_decision_doc_anchors.sh        # file:line-anker-check op beslisdocs (zie taalgebruik-conventies.md §4, kaart `1bf9b52d…`)
bash scripts/test_sweep_unchecked_implemented_markers.sh   # ✅-marker-sweep (kaart 21a349bc… — recipe-writing-conventions §2)
bash scripts/test_sweep_stale_interviews.sh            # Stale-interview-sweeper (interviews/<slug>/ vangnet; zie kaartloze-app-inceptie-decision.md §5)
bash scripts/test_sweep_orphaned_parents_awaiting_subtasks.sh   # Vangnet voor parents die in `Awaiting Subtasks` stranden zonder kinderen (kaart 400d6a77…)
bash scripts/test_sweep_awaiting_plan_ref_overdue.sh   # Vangnet voor kinderen die hun `plan_ref` nooit krijgen (kaart 2341a40e…)

# Single-test run = the documented exception to feedback_no_local_pytest (<1.5s; zie kaart ed09173c).
bash scripts/run-single-test.sh tests/test_x.py                  # whole file
bash scripts/run-single-test.sh tests/test_x.py::test_y          # one test
bash scripts/run-single-test.sh tests/test_x.py -k "param_id"    # pytest -k filter

# Single frontend test (vitest). Local binary, not `npx` — `npx` is blocked by
# the malicious-package-scan hook on this box. The `( cd frontend && ... )`
# subshell keeps the cwd-trap at bay (zie Werkomgeving in worktree §3).
( cd frontend && ./node_modules/.bin/vitest run src/path/to/X.test.tsx )
( cd frontend && ./node_modules/.bin/vitest run )                # hele frontend-suite (~10s)
# `scripts/run-single-test.sh foo.test.tsx` weigert met een pointer naar deze vorm.

# Schemamigraties (alembic — twee gescheiden geschiedenissen)
cd backend && python -m app.migrate_cli          # Beide stores naar head; maakt eerst een momentopname van het bord. Draait ook vanuit `cockpit.sh start`.
cd backend && python -m alembic --name kanban revision --autogenerate -m "<wat er wijzigde>"   # Revisie na een modelwijziging (of --name registry)
cd backend && python -m alembic --name kanban history                                          # Wat is er al gedraaid
cd backend && python scripts/check_migrations_current.py    # CI-poort: verse DB naar head en vergelijken met de modellen

# Architectuurgrenzen (docs/cockpit/architectuur.md)
cd backend && lint-imports                     # Twee importcontracten: transport is een blad; het domein importeert geen subprocess (ratel met alleen-krimpende uitzonderingslijst in pyproject.toml)
./scripts/check-add-job-callers.sh             # Geen belofte zonder rij: `_sched.add_job` alleen in scheduler.py + twee geratelde plekken
./scripts/check-file-size-ratchet.sh           # Een bestand boven 800 regels mag niet groeien; krimp schuift de baseline mee omlaag
./scripts/check-file-size-ratchet.sh --update  # Baseline bijwerken ná krimp (weigert groei vast te leggen)

# Docs / decision register
./scripts/check-decision-register.sh          # Flag any docs/cockpit/*-decision.md missing from decisions.md (advisory; --strict = exit 1)
./scripts/check-doc-frontmatter.sh            # Flag docs/cockpit/*.md zonder OKF-frontmatter of met onbekende type/status (advisory; --strict = exit 1)
./scripts/check-doc-links.sh                  # Flag relatieve Markdown-links in docs/cockpit/*.md, .claude/skills/**/*.md en .claude/agents/*.md met ontbrekend target (advisory; --strict = exit 1)
./scripts/check-test-harness-coverage.sh      # Flag scripts/test_*.sh niet in de # Test-blok van CLAUDE.md (of vice-versa); advisory + --strict (zie kaart 5e988e4e)
./scripts/check-ci-health.sh                   # CI-doesn't-run / consecutive-red Acts-detector (zie kaart 4cae38ff…); advisory + --strict; fixtures via CI_HEALTH_FIXTURES_DIR=<dir>
./scripts/check-doc-readability.py            # Meet de leesbaarheidsnorm uit docs/cockpit/taalgebruik-conventies.md: zinnen >40 woorden, alinea's >150 woorden, hybride werkwoorden (advisory; --strict = exit 1)
./scripts/check-doc-readability.py --file <pad>    # Eén bestand, met file:line per hit — draai dit vóór je een *.md-wijziging shipt
./scripts/check-pretooluse-bg-agent-test.sh      # CI-gate dat faalt zodra `.claude/settings.json` een niet-lege `hooks.PreToolUse` heeft zonder bijbehorende achtergrond-agent-test (marker of testbestand); no-op zolang de hook-lijst leeg is (kaart a712f5c6…, CC 2.1.222)
./scripts/generate-doc-index.py               # Regenereer de README-index (100% dekking, gegroepeerd op type + status-badges) + docs/cockpit/llms.txt uit de frontmatter
./scripts/generate-doc-index.py --check --strict  # Faal als de gegenereerde index/llms.txt out-of-sync is met de frontmatter (advisory zonder --strict)

# Analysis outcome sweeper (vangnet voor het REST-gat + historische voorraad)
./scripts/check-analysis-outcomes.sh          # Flag Done-analyses zonder Outcome-comment/label/kinderen (advisory; --strict = exit 1; --since YYYY-MM-DD voor historic-grens)

# Dead Where:-pointers in kaart-Evidence-blokken (kaart 500d0948…, follow-up op 549ef4d6…)
./scripts/check-card-where-paths.sh           # Flag open kaarten waarvan een `Where:`-pad niet bestaat; strip `:line`/`::symbol`/`#anchor` (advisory; --strict = exit 1)
./scripts/check-card-where-paths.sh --card=<id>  # Authoring-time check op één net gefilede kaart (bereikt ook Done-kaarten)

# Kanban-meta vs. security-profile conflict check (zichtbaarheid voor load-bearing overrides — kaart d5642a57…)
./scripts/check-kanban-meta-security-conflicts.sh   # Flag KanbanMeta skip_permissions/transport overrides die het project_security_profiles-risicoprofiel tegenspreken (advisory; --strict = exit 1)

# Dispatch resolver-usage self-check (gate dat ad-hoc provider/model lookups in dispatch.py flagt — kaart 931855b0…)
./scripts/check-dispatch-resolver-usage.sh          # Flag ad-hoc provider/model lookups in backend/app/kanban/dispatch.py die de canonieke resolve_effective_provider_and_model omzeilen (advisory; --strict = exit 1)

# Git per-worktree admin files getrackt in de repo-root (breekt ELKE ship — kaart 7dd8a3dd…)
./scripts/check-worktree-admin-files.sh             # Flag getrackte HEAD/index/MERGE_*/commondir/gitdir/AUTO_MERGE/ORIG_HEAD in de repo-root (advisory; --strict = exit 1)

# Dangling-dep sweepers (vangnet voor verweesde kanban-references — advisory; --strict = exit 1; JSON op stdout)
./scripts/sweep_dangling_depends_on.py        # Flag niet-Done kaarten waarvan een depends_on-id naar een niet-bestaande kaart verwijst
./scripts/sweep_dangling_plan_refs.py         # Flag plan_ref-deliverables waarvan de parent of het plan niet meer resolvet
./scripts/sweep_orphaned_deliverables.py      # Flag kaarten met ≥1 deliverable die niet in een terminale kolom staan, geen levende claim hebben, en geen Summary-comment hebben — precies de klasse die de dispatch-orphan-fallback stil herdispatched (kaart 4a60048365004d808e2dbfdd9551afe4, a4a091fa… als voorbeeld)

# Remote-branch sweeper (vangnet voor merged-maar-niet-verwijderde branches op `origin`; volgt op de direct-mode ship-recipe fix uit kanban-kaart `3027671c…` — advisory; --strict = exit 1; JSON op stdout; nudge vanuit `cockpit.sh start`)
./scripts/sweep_merged_remote_branches.py      # Flag refs/remotes/<remote>/* branches die `git cherry <base> <ref>` met 0 `+`-regels beantwoorden (volledig gemerged)

# ✅-marker-sweeper (vangnet voor `✅ Geïmplementeerd`-regels zonder waargenomen effect — kaart `21a349bc…`; conventie: recipe-writing-conventions.md §2; advisory; --strict = exit 1; JSON op stdout)
./scripts/sweep_unchecked_implemented_markers.py   # Flag `✅ Geïmplementeerd`/`✅ Uitgevoerd`-markers in docs/cockpit/*.md zonder `Effect:`-zin in dezelfde alinea (venster stopt op de eerste blanco regel, max 12 regels)

# Lint
cd frontend && npm run lint      # ESLint

# Pytest baseline (attribute pre-existing failures on origin/master — kanban card 4c7c5346)
./scripts/pytest-baseline.sh                # Capture pre-existing failures (idempotent, cached 24h)
./scripts/pytest-compare.sh                 # Run pytest + classify: pre-existing / NEW / FIXED

# Bash-test baseline (attribute pre-existing scripts/test_*.sh failures on origin/master — kanban card ecea763e)
./scripts/baseline-bash-tests.sh            # Capture pre-existing bash-test failures (idempotent, cached 24h)
./scripts/compare-bash-tests.sh             # Run scripts/test_*.sh + classify: pre-existing / NEW / FIXED

# Ruff baseline (attribute pre-existing `ruff check` hits on origin/master — kanban card 7678afc4…)
./scripts/ruff-baseline.sh                  # Capture pre-existing ruff hits (idempotent, cached 24h)
./scripts/ruff-compare.sh                   # Run ruff + classify: pre-existing / NEW / FIXED

# Project-key re-key (na een repo-rename op de forge — zie rebrand-decision.md §2.3)
./scripts/migrate-project-key.py --new-remote <url>                     # Dry-run: tel de te herschrijven rijen
./scripts/migrate-project-key.py --new-remote <url> --apply --update-remote  # Herschrijf + zet origin om in één run

# Version
./scripts/bump-version.sh <major|minor|patch>  # Sync version across VERSION, package.json, pyproject.toml
```

## Code Style

- **Frontend**: ESLint + TypeScript strict mode (`noUnusedLocals`, `noUnusedParameters`). Path alias `@/*` → `./src/*`
- **No impure calls in render**: the react-compiler ESLint rule rejects `Date.now()` / `Math.random()` (etc.) called directly in a component's render body — including as an inline argument expression, e.g. `formatLabel(Date.now())` inside JSX/render. Move the impure call inside the helper function itself instead (see `isFutureSchedule` in `frontend/src/features/kanban/components/CardItem.tsx`), otherwise `npm run lint` fails with `Cannot call impure function during render`.
- **API endpoints zijn relatief**: `apiClient` / `apiUpload` / `apiAssetUrl` plakken zelf `API_BASE_URL` (`/api/v1/`) vóór hun endpoint-argument. Schrijf dus `apiClient("mcp-server/tokens")`, nooit `apiClient("/api/v1/mcp-server/tokens")` — die tweede vorm bouwt `/api/v1//api/v1/…` en 404't (Starlette normaliseert de dubbele slash niet; symptoom was de "Failed to load tokens"-toast op de MCP Server-pagina, kaart `17d1cabe…`). Hetzelfde geldt voor een per-feature `const BASE` in een `api.ts`. Bewaakt door `no-restricted-syntax` in `frontend/eslint.config.js`; een rauwe `fetch`/`EventSource`/`WebSocket`-URL of een endpoint-string die je aan de gebruiker toont houdt wél het absolute pad.
- **Backend**: Type hints throughout, async/await patterns, pydantic models for validation
- **Test doubles: patch where the consumer looks; assert the double fired.** `from app.module import name` binds the function object into the consumer's namespace **at import time**. A patch on the *source* module (`monkeypatch.setattr(src_module, "name", patched)`) therefore does **not** reach that binding — the consumer keeps calling the original. Three rules to make this class of no-op patch impossible to write *or* detect: (1) patch the consumer, (2) or switch the consumer to module-attribute access, (3) always assert the double fired. Concrete failure + reviewer grep-recept: `docs/cockpit/test-doubles-convention.md` (zie ook [subscription-pool-analyse §3](./docs/cockpit/subscription-pool-dispatch-analyse.md) / kanban-kaart `ea7e038b…`).

## UI Conventions

- **Clickable cards**: All clickable Card components must use the `CLICKABLE_CARD` constant from `@/lib/constants`. This gives a consistent `border-2 hover:border-primary/50` indigo border hover effect, plus `cursor-pointer`, `transition-colors`, and `focus-visible:ring-2` for keyboard a11y. Action buttons inside clickable cards must use `e.stopPropagation()` and keyboard handlers must support Enter/Space.
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
  globale variabele) — `$()` isoleert het in een subshell waar de
  geïnstalleerde trap verloren gaat. De helper defaultt op `HEAD`; een harnas
  dat een vaste baseline nodig heeft geeft die als derde argument mee, bv.
  `with_scratch_worktree "$REPO_ROOT" WT origin/master`.
- **Geen lokale pre-push gate** (sinds 2026-07-05): full pytest + lint/build liep
  in CI (`quality.yml`). Backend en frontend draaien daar als aparte gates; draai
  zelf de frontend-checks voor ships die `frontend/` raken (zie git-ship §2).
  `scripts/cockpit-doctor.sh` is de read-only health-check.
- **`quality.yml` heeft drie parallelle gates, bewust gesplitst** (2026-08-05):
  `backend-lint` (ruff, mypy, bandit, openapi-snapshot), `backend-tests`
  (`pytest -q`) en `frontend`. Eén sequentiële backend-job liet elke lint-fout
  `pytest` overslaan — twee F541's hielden de testsuite vijf dagen tegen terwijl
  elke ship een groene backend-gate claimde die nooit gedraaid had. Binnen
  `backend-lint` draait elke gate met `if: !cancelled()`, zodat één run *alle*
  falende gates toont in plaats van alleen de eerste. Voeg je een backend-gate
  toe, zet 'm dan in `backend-lint` met diezelfde `if`-vorm — niet vóór
  `pytest` in een gedeelde job.
- **Remote branch hygiene**: twee routes, twee mechanismen. **PR-route:**
  `delete_branch_on_merge` is enabled (2026-07-07), dus PR-branches ruimen
  zichzelf op bij merge. **Merge-to-master-route (direct mode):** die sluit
  geen PR, dus `delete_branch_on_merge` vuurt níét — de ship-recipe doet de
  `git push origin --delete "$BRANCH"` zelf, alleen ná een geslaagde push
  (bij een afgewezen push blijft de branch staan voor de PR-fallback). Zonder
  die regel stapelde elke geshipte kaart een dode branch op `origin`
  (kaart `3027671c…`: 7 stuks over 6 weken). Branches van PRs die nooit
  mergen stranden nog steeds op `origin` — handmatige
  `git cherry master origin/<branch>` + delete.

## Gotchas

- No `.env` file needed — all config has defaults in `backend/app/config.py`
- **Twee aparte SQLite-stores** — verwar ze niet bij een board-dive. De **registry-DB** staat op `backend/claude_registry.db` en houdt MCP servers, commands, permissions en plugin-state. Het **kanban-bord** zit in `~/.claude-registry/kanban.db` met `kanban_cards`, `kanban_columns`, `kanban_meta`, de activity-feed én de append-only op-log `kanban_ops` (`backend/app/config.py:21-29` + `:65-69`, "Separate store for the kanban board domain, portable, sync-able, one-per-machine"). Een query tegen de verkeerde geeft `sqlite3.OperationalError: no such table: kanban_meta` (of omgekeerd) en kost een `config.py`-grep. **`sqlite3` is geen shell-tool op deze box** (`command not found: sqlite3`) — query read-only via Python in plaats daarvan, bv.: `python3 -c "import sqlite3,os; con=sqlite3.connect(f'file:{os.path.expanduser(\"~/.claude-registry/kanban.db\")}?mode=ro', uri=True); print(con.execute('SELECT …').fetchall())"`. `mode=ro` is ook de veilige default voor een live WAL-DB.
- **`kanban_ops` overleeft deletes** — als een kaart weg is (`get_card` geeft `not_found`) is de op-log het enige bewijs voor *hoe* 'ie afgesloten werd. Tabel is **`kanban_ops`** (niet `kanban_operations`) en de sleutelkolom is **`entity_id`** (niet `card_id`); kolommen zijn `op_id, device_id, seq, hlc, project_key, entity_type, entity_id, op_type, payload, created_at` (zie `backend/app/kanban/models.py:26-38`). `entity_type` is `card | comment | deliverable`; `op_type` is `create | move | update | claim | release | comment | attach`. `payload` is JSON — bevat bv. `{"column": "Done"}` of `{"title": "…"}`. Voor een verwijderde kaart — vervang `<card_id>` (één regel, copy-pastebaar):
  ```bash
  python3 -c 'import sqlite3,os,json; db=os.path.expanduser("~/.claude-registry/kanban.db"); con=sqlite3.connect(f"file:{db}?mode=ro", uri=True); cid="<card_id>"; rows=con.execute("SELECT op_type, payload FROM kanban_ops WHERE entity_type=\"card\" AND entity_id=? ORDER BY hlc", (cid,)).fetchall(); print(f"{len(rows)} ops for {cid}"); [print(o, json.dumps(p, ensure_ascii=False)) for o,p in rows]'
  ```
- **Schemawijzigingen gaan via alembic** (sinds 2026-08-15; daarvóór moest je de database wissen). Elke store heeft een eigen revisiegeschiedenis: `alembic --name registry …` en `alembic --name kanban …`, aangestuurd vanuit `backend/`. Wijzig je een model, genereer dan een revisie — de CI-poort `scripts/check_migrations_current.py` neemt een verse database naar `head` en faalt zodra modellen en migraties uiteenlopen. **Roep alembic altijd aan als `python -m alembic`**, nooit als bare `alembic`: de testrunner draait de venv-interpreter zonder `venv/bin` op `PATH`. SQLite kan geen `ALTER COLUMN`, dus elke kolomwijziging gaat door `op.batch_alter_table` (staat ook in het revisiesjabloon). Een nieuwe dependency hoort in **`requirements.txt` én `pyproject.toml`** — CI installeert uit de eerste. Achtergrond: [`docs/cockpit/kernharding-design.md`](./docs/cockpit/kernharding-design.md) §1.
- Wil je tóch vanaf nul beginnen: `rm backend/claude_registry.db` wist de registry-state; `rm ~/.claude-registry/kanban.db` wist het hele bord (kaarten, kolommen, autodispatch-meta, activity). Mix ze niet — de één verwijderen verwijdert de andere niet.
- Backups stored at `~/.claude-registry/backups/` (naast de kanban-DB; bewust portable gehouden)
- `rm` is blocked via `.claude/settings.json` (`Bash(rm:*)` deny) — use `mv` to move unwanted files outside the repo, or `git clean -f -- <path>` for untracked files, instead
- **Gebruik `pkill -f`/`pgrep -f` niet in een gedispatchte sessie — je raakt de sessie van een collega-agent.** Sinds Claude Code 2.1.214 weigert `pkill` de eigen CLI (geverifieerd op lokale CLI 2.1.221). Het blijvende risico zit in andermans sessie: de dispatcher zet de hele prompt in de cmdline (`backend/app/services/agentic_cli/claude_code.py:82-83`), dus een `-f`-patroon matcht veel breder dan je denkt.
- Veilige alternatieven + de patronen die op deze box vaak fout gaan staan in [`docs/cockpit/pkill-safety.md`](./docs/cockpit/pkill-safety.md); ook [`docs/cockpit/updates-feature-decision.md:88`](./docs/cockpit/updates-feature-decision.md) blijft riskant op `pkill -f uvicorn`-vorm.
- **Pas nooit een stash toe die je niet zelf maakte — gebruik de baseline-scripts.** `git stash list` is per worktree, niet per sessie, dus `stash@{0}` kan van een sessie van uren geleden zijn. Toepassen geeft conflicten, en een `git reset --hard` om die apply af te breken wist je eigen ongecommitte bestanden — in één sessie 7 stuks (kaart `31c30dbb…`).
  - **Expliciete trigger die hieronder valt:** `git stash` met lege output ("No local changes to save") gevolgd door `git stash pop` zonder eerst `git stash list` te draaien popt alsnog een pre-existing `stash@{0}` van een eerdere sessie. Lege `git stash`-output garandeert geen veilige pop. Altijd `git stash list` vóór elke `pop` of `apply`.
  - **Attributie zonder stash:** "bestond deze failure al op `origin/master`?" beantwoord je voor `scripts/test_*.sh` met `scripts/baseline-bash-tests.sh` + `scripts/compare-bash-tests.sh` (preset `bash-test-attr`) in een detached worktree van master — niet met eigen stash/pop. Idem voor backend pytest (`pytest-attr`) en `ruff check` (`ruff-attr`). Achtergrond + recovery: [`docs/cockpit/git-stash-safety.md`](./docs/cockpit/git-stash-safety.md).
- **Backend log timestamps zijn UTC ISO 8601** (`"2026-07-14T08:49:10.867Z"`, `Z`-suffix). Kanban-DB `created_at`/activity-timestamps zijn óók UTC, dus een log-dive vanaf een kaart-timestamp kan direct gedaan worden zonder `+2u`-correctie. Logs van vóór 2026-07-14 (`logs/backend/run-*.log` met prefix-datum) zijn nog in lokale CEST (`09:49:10` = UTC `07:49:10`); check de datum in de bestandsnaam om de era te bepalen.
- **Kanban-router: een vastgehouden `service.get_card`-pre-check vergiftigt de post-commit `_reload`.** `service.get_card` doet `selectinload(deliverables, attachments)` en de sessie draait met `expire_on_commit=False`. Een loader-optie her-populeert géén relationship die al geladen is op een instance in de identity-map (dat vereist `populate_existing()`), dus `_reload` geeft de **pre-mutatie**-collectie terug. Het bijt alleen als *beide* voorwaarden gelden. **Eén:** het pre-check-resultaat is aan een **levende variabele** gebonden. De identity-map houdt weak refs, dus een ongebonden `if await service.get_card(...) is None:` wordt direct opgeruimd en triggert dit níet. **Twee:** de op wijzigt collectie-**membership**, dus een INSERT of DELETE van een deliverable- of attachment-rij. Een ORM-UPDATE van een al geladen rij synchroniseert wél, waardoor `update_plan_attachment` veilig is ondanks zijn gebonden `card`. Schrijf je een handler die een deliverable/attachment toevoegt of verwijdert, doe de existence-check dan met `await s.get(KanbanCard, cid)` (relationships blijven unloaded, zie `upload_attachment`), of `s.expire_all()` na de commit. Volledige uitleg: de `_reload`-docstring in `backend/app/api/v1/kanban/router.py`.
- **Ongequote zsh-metatekens in commando-argumenten zijn een silent no-op.** De dispatch-shell is zsh (zie omgevingsblok). Twee klassen bijten hier. **Eén — glob-tekens** `?`, `*` en `[`: die matchen standaard tegen de cwd, en bij geen match geeft de shell een onschuldige `no matches found`-fout waardoor het commando **nooit draait** (`grep -rn "FOO" backend/app --include=*.py` → `(eval):1: no matches found: --include=*.py`). **Twee — EQUALS-expansie:** een woord dat met `=` begint wordt vervangen door het pad van het gelijknamige commando, dus `echo ===` faalt met `(eval):1: == not found`. In alle gevallen leest de foutmelding niet als een quoting-fout maar als "leeg resultaat / commando stuk", wat een of meer retries kost om te ontdekken. Fix: quote de hele waarde met enkele of dubbele quotes (`grep --include="*.py"`, `echo "==="`). Twee veel-voorkomende oppervlakken in deze codebase met hetzelfde faalpatroon:
  - **URL-query-strings** (`gh api "repos/OWNER/REPO/git/trees/main?recursive=1"`, `curl "https://.../main/README.md?ref=…"`) — `?` matcht als glob-patroon. `gh api "repos/OWNER/REPO/git/trees/main?recursive=1"` zonder quotes op de URL faalt met `(eval):1: no matches found: …`, en de foutmelding leest als "lege API-respons". Quote de hele URL met dubbele quotes (`gh api "repos/OWNER/REPO/git/trees/main?recursive=1"`), of gebruik enkele quotes als je shell-variabelen wilt interpoleren. Idem voor `?`-parameters in raw-URL's (`curl "https://raw.githubusercontent.com/.../main/README.md"`). Geldt voor élke sessie die `gh api` met query-parameters gebruikt (research-kaarten, market-research-sweeps, integratie-analyses).
  - **Glob in flag-waarden** (`grep --include=*.py`, `--exclude=*.tmp`, `--ext=*.js`, Bash brace expansion) — `*` matcht als glob-patroon. `grep -rn "FOO" backend/app --include=*.py` zonder quotes op de glob faalt met `(eval):1: no matches found: --include=*.py`, en de grep **draait nooit**; de foutmelding leest niet als een quoting-fout maar als "lege grep-respons". Quote de glob-waarde met enkele of dubbele quotes: `grep -rn "FOO" backend/app --include='*.py'`. Frequent in deze codebase: élke verkennende sessie die `grep --include`/`--exclude`/`--ext` gebruikt (veel frequenter dan `gh api`-query-strings).
- **Backticks in dubbelgequote strings zijn command-substitution.** Bash en zsh voeren in ``echo "X: `foo.sh` …"`` `foo.sh` uit in plaats van de tekst letterlijk te printen; als dat script niet eindigt, **hangt** de ogenschijnlijk simpele regel zonder `set -u`- of `pipefail`-trigger. Gebruik single quotes (``echo 'X: `foo.sh` …'``) of escape de backticks (``echo "X: \`foo.sh\` …"``) als variabele-interpolatie nodig is.
- **GitHub default-branch ≠ `main`.** Deze repo én een flink deel van de populaire ecosystemen (zoals de 9router-repo in kaart `27cdc2bd…`) gebruiken nog `master` als default. `raw.githubusercontent.com/OWNER/REPO/main/README.md` geeft dan een 404 die lijkt op "repo bestaat niet". Resolve de default branch expliciet met `gh api repos/OWNER/REPO --jq .default_branch` en interpoleer die in plaats van `main` te gokken. Voorbeeld-patroon: `BRANCH=$(gh api repos/O/R --jq .default_branch); gh api "repos/O/R/contents/README.md?ref=$BRANCH"`.
- **`gh` zonder expliciete repo leest de fork-upstream (`adrirubio/claude-deck`) — CI-, run- en PR-data van de verkeerde repo, zonder foutmelding.** Deze repo heeft twee remotes: `origin` = `guillaumevandevelde/agent-cockpit` (waar de cockpit zelf landt) en `upstream` = `adrirubio/claude-deck` (de fork-bron, zie fork-header boven aan dit bestand). `gh` resolvet zonder repo-argument standaard naar `upstream`, dus `gh repo view` → `{"nameWithOwner":"adrirubio/claude-deck"}`, `gh run list` toont andermans runs, `gh pr list` andermans PR's. **Fix: gebruik altijd de `-R`-flag** — `gh <cmd> -R guillaumevandevelde/agent-cockpit …`. Dat is de enige vorm die op élk subcommando werkt; de repo-**positional** (`gh run list guillaumevandevelde/agent-cockpit`) bestaat alleen voor `gh repo <sub>` en faalt elders met `unknown command "guillaumevandevelde/agent-cockpit" for "gh run list"` / `unknown argument …` (gemeten op gh 2.92.0) — dus geen stille foute data, maar wél een verloren poging. Optioneel eenmalig `gh repo set-default guillaumevandevelde/agent-cockpit` (device-state in `.git/config` — overleeft geen verse checkout of andere machine, dus deze doc is het duurzame deel; `-R` blijft de vorm die je opschrijft).
  - **CI-status opvragen, copy-pasteable** (het recept dat deze val het vaakst triggert):
    ```bash
    gh run list -R guillaumevandevelde/agent-cockpit --limit 15          # laatste runs op origin
    gh run list -R guillaumevandevelde/agent-cockpit --workflow=quality.yml --limit 10   # alleen de Quality-gate
    gh run view -R guillaumevandevelde/agent-cockpit <run-id> --log-failed              # waarom een run rood is
    ```
    Sinds ~2026-07-26 sneuvelen Quality-runs op een spending-limit-block; een `failure` hier is dus niet per definitie jouw diff — check `--log-failed` vóór je 'm aan je kaart toeschrijft.
- **Plugin-haken die naar een plugin-MCP verwijzen breken gedispatchte sessies.** Plugins als `context-mode` registreren een `PreToolUse`-haak op `WebFetch` (en `Bash|curl`) die de call denied en doorverwijst naar `mcp__plugin_<plugin>_<plugin>__ctx_fetch_and_index` (en vrienden). Die MCP-server wordt door `--strict-mcp-config` (kaart `00fa8325`) juist **uitgesloten** van de dispatch — het project-`--mcp-config` laadt alleen wat in de eigen `.mcp.json` staat, niet wat het pluginsysteem zou aanbieden. Resultaat: de sessie wordt op een niet-bestaande tool gewezen, de WebFetch/curl faalt, en de foutmelding leest als "interne plugin-suggestie", niet als "configuratie-gat" — debuggen kost per sessie een paar ontdekkingen.
  - De fix zit in `backend/app/services/runs/cc_spawn.py::_resolve_merged_mcp_path`: wanneer een plugin uit `_MERGED_PLUGIN_KEYS` in `~/.claude/plugins/installed_plugins.json` staat én zijn MCP-server nog niet in het project-`.mcp.json` staat, schrijft de dispatch een merged kopie naar `~/.cache/cockpit-ship/merged-mcp/<project>.mcp.json` en geeft díe aan `--mcp-config`. Andere plugins' MCP-servers blijven buiten de dispatch-sandbox (dat is waarvoor `--strict-mcp-config` bestaat).
  - Treft een sessie dit toch, dan is óf de plugin niet geïnstalleerd (controleer `~/.claude/plugins/installed_plugins.json`), óf de plugin heeft een onverwachte naam (breid `_MERGED_PLUGIN_KEYS` uit), óf hij heeft geen eigen MCP. In dat laatste geval hoort de PreToolUse-haak niet thuis in een dispatch.
  - Achtergrond + de geobserveerde failure (5× WebFetch + 2× curl verloren in de market-research-sweep van 2026-08-13): kanban-kaart `ed2088e5…`.
