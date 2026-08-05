**Datum:** 2026-08-05
**Status:** besloten
**Kaart:** `4f56f169e3184c71a079912a8c0f456a`
**Uitkomst:** Drie CI-gates blijven advisory op korte termijn, één gate wordt hard, één gate verdwijnt; semgrep krijgt SARIF-upload; CI-naar-bord-terugkoppeling loopt via een mens-getriggerde `file_ci_drift_cards.py` naast de bestaande drift-report; de direct-mode-route krijgt een aparte auto-fix-workflow.

> Deze beslissing antwoordt twee gekoppelde vragen tegelijk: **welke CI-gates mogen nog
> falen**, en **hoe komt een bevinding automatisch terug op het bord**. Een
> gate blijft advisory als hij of te lage informatiewaarde heeft voor de
> doorvoerkost, of als de baseline nog te groot is om hard-making verantwoord
> te maken — allebei gemeten of onderbouwd, niet ingeschat.

## 1. Per-gate-uitkomst

De huidige zes advisory-stappen staan in twee workflows:

- `.github/workflows/quality.yml`: `mypy`, de `bandit -f txt`-duplicaat, de
  superpowers-promotion-ledger, de hele `e2e`-job.
- `.github/workflows/security.yml`: `semgrep scan`.

Het `drift-report.yml`-plan (9 signal-only stappen op `|| true`) is geen gate
maar een wekelijks overzicht — die blijft signaal-only, want fileen van
kaarten is een aparte stap (zie §3).

### Tabel

| Gate | Huidige stand | Beslissing | Reden |
|---|---|---|---|
| `mypy app --ignore-missing-imports` | advisory | **advisory** (blijft) | Baseline **275 errors in 46 files** lokaal gemeten 2026-08-05; hard-making blokkeert élke ship. Opbouw via baseline-gated vergelijking is een aparte follow-up. |
| `bandit -r app -lll` | hard | **hard** (blijft) | Alleen low-confidence; lage false-positive-rate; al hard. |
| `bandit -r app -f txt` (duplicaat) | advisory | **weg** | Duplicaat van de `-lll`-vorm die óók al draait. De `-lll`-filter laat enkel lage severity door; een medium/high hit zou sowieso in een andere band vallen. Schrappen kost nul detectie. |
| Superpowers-promotion-ledger | advisory | **advisory** (blijft) | Docs-drift-detectie (`docs/superpowers/{plans,specs}` ↔ `README.md`); geen blocker-waarde voor ships. |
| `e2e` (Playwright) | advisory | **advisory** (blijft) | Browser-flake op GitHub-hosted runners is reëel; een "X op Y rood"-signaal in het cockpit-board vangt het patroon op zonder de bouw om zeep te helpen. |
| `semgrep scan` | advisory, geen SARIF | **advisory + SARIF-upload** | Geen lokale baseline (semgrep niet geïnstalleerd); SARIF-upload naar Security-tabblad is de laagdrempelige zichtbaarheid — geen build-fail, wel een pane waar ze staan. Vereist `security-events: write` (zie permissions-rij). |

**Niet aangeraakt:** ruff, pytest, OpenAPI-snapshot, gitleaks, alle hard-gates
in `backend-lint` / `backend-tests` / `frontend` — die hebben bewezen waarde
en de huidige parallelle structuur (commit `0b2a4f4e…`) voorkomt het oude
lint-blokkeert-tests-patroon al.

### Wat hard-making op mypy zou kosten (niet gedaan)

Twee routes overwogen:

1. **Baseline-commit + diff-only.** Een baseline-tak vastpinnen, mypy alleen
   laten falen op fouten die niet in de baseline zitten. Vereist een wrapper
   om mypy-uitvoer tegen de baseline te filteren — enige bestaande variant is
   `scripts/pytest-baseline.sh` / `pytest-compare.sh` voor pytest. De
   mypy-baseline is **275 entries**; mechanisch op te lossen, maar het
   bouwt een tweede baseline-discipline die we nog niet hebben. Niet
   blocking-genoeg om deze beslissing uit te breiden.
2. **Promotie in tranches.** Eerst 0 errors in de top-3 meest-getroffen
   bestanden (`dispatch.py`, `session_recovery.py`, `mcp_server.py`, `main.py`)
   hard maken; daarna uitbreiden. Voordelen: voortgang is zichtbaar, en het
   dwingt tot triage per module. Nadeel: één HIT tegelijk betekent ook één
   commit-per-file aan strakke type-discipline, wat voor `dispatch.py` een
   grote refactor-payload heeft. Buiten scope; eigen follow-up.

Beide routes blijven staan als §5 follow-ups.

## 2. CI-naar-bord-terugkoppeling

Het kernprobleem: CI heeft geen kanban-credentials. Drie routes overwogen:

1. **Self-hosted runner met cockpit-toegang.** Draait de CI in hetzelfde
   netwerk als de lokale backend. Nadelen: infra-belasting (één extra runner
   onderhouden, secrets beheren), security-surface (GitHub-vonk → lokale
   backend), en één failure-modus waar geen cockpit is draait geen filing.
   **Afgewezen** op kosten/baten; één bot-runner die alles kan is een te
   zwaar middel voor een melding.
2. **Pull-model vanuit cockpit zelf.** De cockpit polt periodiek GitHub via
   `gh` en filet kaarten op basis van bevindingen. Voordeel: cockpit houdt
   zijn eigen credentials. Bestaande infrastructuur: `scripts/check-ci-health.sh`
   doet de helft al (detecteert "CI didn't run" + opeenvolgend rood), en
   `backend/scripts/file_spec_drift_cards.py` is het sjabloon voor
   idempotente Backlog-filing vanuit een samenvatting. Nadeel: vereist dat
   iemand de cron draait (nu handmatig, na een drift-report).
3. **Inbound webhook met tight-token.** CI POST naar een nieuw
   `POST /api/v1/kanban/ingest`-endpoint met een gedeelde token.
   Nadelen: nieuw attack-surface op de cockpit, secrets-rotation,
   envelope-format-keuze (SARIF? Drift-summary?), en
   double-filing-risico. **Afgewezen** omdat route 2 hetzelfde effect
   bereikt zonder inbound.

**Gekozen: route 2, uitgebreid.** Een nieuwe `scripts/file_ci_drift_cards.py`
wordt het CI-tegenhanger van `file_spec_drift_cards.py`:

- leest het weekly drift-report overzicht terug (`/tmp/*.md` patterns
  die de drift-report al schrijft);
- leest recente rode `quality.yml`-runs via `gh run list` (zelfde shape als
  `check-ci-health.sh`);
- filet één `[ci-drift]` Backlog-kaart per bevinding, met dezelfde
  idempotente dedup en `parent_card_id` naar de oorspronkelijke kaart
  waar mogelijk;
- draait óók als aparte handmatige stap (zoals de spec-drift-tegenhanger
  vandaag).

Geen nieuwe cockpit-endpoints, geen nieuwe secrets, geen inbound surface.

**Open punt:** of dit commando óók in een eigen cadans-trigger-kaart
(`scheduled_at`) hoort te landen, vergelijkbaar met hoe `recurring-cadence-proposal.md`
periodieke sweeps plant. Het maakt het fileen automatisch in plaats van
handmatig. Bewust **niet** in deze beslissing beslecht — `scheduled-trigger-consolidatie-decision.md`
heeft de canonieke cadans-keuze en dit volgt dezelfde pad.

## 3. Direct-mode terugkoppeling

`auto-fix-on-red-ci.yml` filtered expliciet op
`event == 'pull_request'`. Een direct-merge naar `master` sluit geen PR en
vangt dus geen comment of label. Dit is een reëel gat nu de directe route
volwaardig alternatief is (zie CLAUDE.md).

**Beslissing:** een tweede, lichte workflow `auto-fix-on-red-direct.yml`:

- Trigger: `workflow_run` op Quality, conditie `event == 'push'` (de directe
  push naar master) en `conclusion == 'failure'`.
- Actie: open een GitHub Issue met de commit-SHA, de gefaalde jobs, en de
  `@claude fix the failing checks`-tekst — zelfde body als de PR-route, maar
  op een issue omdat er geen PR is om op te posten.
- Label: `auto-fix-direct-attempted` om dubbele issues per commit te
  voorkomen.

De cockpit-side haalt deze issues op via dezelfde `gh`-pull-laag als
`check-ci-health.sh` — geen tweede pad. Issues zijn al eerste-klas
artefacten in de cockpit; één filterregel volstaat.

**Niet gedaan:** de directe route omleiden naar de PR-route. Dat is een
workflow-herontwerp (vereist een fork-PR-pattern bij élke direct merge),
en het contract uit CLAUDE.md is dat direct mode volwaardig is. Issue als
terugkoppeling-oppervlak is de lichtere ingreep.

## 4. Permissions-impact

SARIF-upload vereist `security-events: write` op de workflow. De huidige
`security.yml` heeft dat niet expliciet — de SARIF-upload-actie faalt
stil als de permissie ontbreekt. Toevoegen aan de `semgrep`-job is een
één-regel-wijziging; bewust niet in deze beslissing ingrijpen omdat
permissions-kaarten hun eigen sweep-traject hebben.

## 5. Vervolgkaarten

| Titel | Type | rationale |
|---|---|---|
| `bandit -f txt`-duplicaat verwijderen uit quality.yml | chore | §1 rij 3; één-regel-wijziging in `quality.yml`. |
| Semgrep SARIF-upload toevoegen aan security.yml | chore | §1 rij 6 + §4; één-regel `permissions`-blok + één stap. |
| Direct-mode auto-fix-workflow (`auto-fix-on-red-direct.yml`) | feature | §3; nieuwe workflow-file met issue-aanmaak. |
| `scripts/file_ci_drift_cards.py` schrijven | feature | §2; tegenhanger van `file_spec_drift_cards.py`. |
| Mypy-baseline-reductieplan (per module: dispatch → recovery → mcp → main) | analysis | §1 "Wat hard-making op mypy zou kosten"; vereist een eigen spike met baseline-discipline. |

## 6. Bekende risico's

- **Direct-mode auto-fix issues kunnen spam worden** als een reeks pushes
  elk een rode run produceert. Mitigatie: het `auto-fix-direct-attempted`-label
  sluit duplicaten per commit-SHA, maar niet per failure-patroon. Een
  opeenvolgend-rood-detector (`check-ci-health.sh`-vorm) hoort in dezelfde
  filing-laag.
- **`file_ci_drift_cards.py` heeft geen vangnet** als `gh` faalt (auth,
  netwerk). De bestaande `check-ci-health.sh` heeft wel fixtures-modus
  (`CI_HEALTH_FIXTURES_DIR`); die discipline overnemen.
- **Advisory blijft advisory** in GitHub-Actions-terminologie zolang de
  workflow `conclusion == 'success'` toelaat met `continue-on-error: true`.
  Een echte "advisory dashboard" vereist of een splittings-job óf een
  custom check-run. Bewust niet in scope.

## 7. Heropenen

- Bij een mypy-baseline onder, zeg, **50 errors** wordt het hard-maken
  opnieuw overwogen — de promotie-tranches uit §1 worden dan klein genoeg
  om in één of twee sprints te landen.
- Bij een evidence dat advisory-steps een echte bug misten die hard-steps
  hadden gevangen — conventie is dan `request_review` op een Done-kaart
  die wel shipped met de rotte gate.
- Bij een aanpassing van `scheduled-trigger-consolidatie-decision.md` die
  een ander cadans-mechanisme kiest dan de mens-getriggerde filing.
