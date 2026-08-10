---
title: "Harnassen die een agent spawnen — inventarisatie en sandbox-status"
type: decision
status: decided
---

# Harnassen die een agent spawnen

**Datum:** 2026-08-10
**Status:** Beslissing
**Kaart:** `[self-improve] Harnassen die een agent spawnen in een tree van deze repo kunnen master muteren — inventariseer en sandbox ze` (`ee905064…`)

## TL;DR

Eén **structurele** sandbox (`git archive`-export zonder `.git`, zonder remote, buiten `$REPO_ROOT`) is de enige containment die de **kaart-vormige** meetarm betrouwbaar maakte (kaart `5934b954…`, incident 2026-08-10, revert `2e0eb256`). De drie niet-kaart-vormige meetarmen (`baseline` / `with-saver` / `real-saver`) draaien vandaag in een **linked git worktree binnen `$REPO_ROOT`**, beschermd door precies de `GIT_SSH_COMMAND=/bin/false`-guard die op de kaart-vormige arm bewezen ontoereikend was. Zij sandboxen krijgen dezelfde `make_prompt_sandbox`-helper, zodat **elke variant** van de meet-harnas één structurele invariant deelt. `measure-cache-read-quota.sh` blijft ongewijzigd (alleen `amplify` raakt een live quota en wordt al door `--allowedTools ""` ingedamd; die invariant borgen we met een source-grep in de bestaande test).

## 1. Inventarisatie van agent-spawnende scripts

| # | Script / pad | Werkdirectory | Prompt bevat mutatie-recept? | Huidige containment | Oordeel | Sandbox-actie |
|---|---|---|---|---|---|---|
| 1 | `scripts/measure-token-saver.sh card-baseline` | `make_prompt_sandbox` → `$HOME/.cache/cockpit-measure-sandbox/trial-N-card-baseline` | **Ja** — `build_card_prompt` rendert het echte ship-recept | ✅ structureel (geen `.git`, geen remote) | Veilig | Geen (al gesandboxed) |
| 2 | `scripts/measure-token-saver.sh card-injector` | idem | **Ja** — idem, met Caveman+Ponytail-slices | ✅ idem | Veilig | Geen (al gesandboxed) |
| 3 | `scripts/measure-token-saver.sh baseline` | `with_scratch_worktree` → `$REPO_ROOT/tmp-XXXX/wt-$$` (linked worktree) | Nee vandaag (`build_prompt` zonder ship-recept), maar `lib:82-108` is een **prompt-eigenschap** — één edit herhaalt het incident | ⚠️ Alleen `GIT_SSH_COMMAND=/bin/false` op de claude-aanroep | **Gat** | `make_prompt_sandbox` ipv worktree |
| 4 | `scripts/measure-token-saver.sh with-saver` | idem | idem | idem | **Gat** | idem |
| 5 | `scripts/measure-token-saver.sh real-saver` | idem + schrijft `<wt>/.claude/settings.json` | idem | idem | **Gat** | idem (`.claude/`-write werkt in een archive-tree) |
| 6 | `scripts/measure-cache-read-quota.sh amplify` | cwd = operator-cwd (typisch repo-root) | Nee — `"Reply with exactly one word: OK"` (1-turn, geen tool-oppervlak) | ✅ `--allowedTools ""` op de claude-call (geen tool ⇒ geen schrijfoppervlak) | Veilig | Geen; invariant borgen met source-grep |
| 7 | `scripts/test_acp_smoke.py` | `tempfile.mkdtemp(prefix="acp-smoke-")` (buiten repo) | Nee — hardcoded 1-turn micro-prompt | ✅ `subprocess.run` gemonkeypatcht naar no-op + `mcpServers: []` | Veilig | Geen |

**Buiten scope van deze kaart** (niet-scripts, kwamen uit de inventarisatie maar raken de kaart-eis niet):

- `backend/app/kanban/dispatch.py::make_worktree_transport` (kanban-dispatcher) — eigen worktree-pin en risk-class defaults (`meta`→bypass, `product`/`untrusted`→`sandcastle`).
- `backend/app/services/agentic_cli/headless_runner.py::_build_argv` — mist `--strict-mcp-config` (één van zes spawn-paden zonder MCP-pin). Vangnet-vlag, géén blocker; wordt als losse observatie naar de kaart-activity gepost zodat een vervolg-kaart 'm kan oppakken.

## 2. Waarom één uniform sandbox-pad

Drie redenen, in volgorde van gewicht:

1. **Structurele invariant.** `make_prompt_sandbox` heeft drie eigenschappen die geen env-var of git-config kan bieden: (a) geen `.git`-directory in de boom (geen `git push` mogelijk, geen `git remote` om te lezen), (b) geen parent-repo om in te lopen (bestand `archive` exporteert zichzelf, niet het hele `.git`-historie), (c) buiten `$REPO_ROOT` zodat een `cd ..` nooit de echte repo opent. De `GIT_SSH_COMMAND`-guard die de niet-kaart-vormige arm vandaag beschermt, overleefde de shell-grens niet (kaart 5934b954…); die les is precies de aanleiding van deze kaart.
2. **Toekomstige varianten.** De meet-harnas krijgt vandaag of morgen een zesde variant. Eén uniform pad betekent dat die variant **automatisch** dezelfde invariant erft, zonder dat de auteur moet onthouden "oh, dit is een worktree-arm, geen card-arm".
3. **Kleinste verschil.** De `real-saver`-arm is de enige die een write in de boom doet (`.claude/settings.json`); een archive-tree is een gewone directory met schrijfrechten, dus die arm blijft zonder aanpassing werken. `score_golden` doet alleen `grep` + `pytest` (geen `git diff`), dus de git-loosheid is geen obstakel.

## 3. Wijzigingen

### 3.1 `scripts/measure-token-saver.sh` — `run_one` uniform sandboxen

- Verwijder de `with_scratch_worktree`-tak uit `run_one`. Elke variant krijgt `make_prompt_sandbox`.
- Vereenvoudig `release_run_tree` tot `cleanup_prompt_sandbox` (sandboxed=1 altijd).
- Geen wijziging aan `apply_real_saver` (schrijft `<sandbox>/.claude/settings.json`, werkt in archive).

### 3.2 `scripts/test_measure_token_saver.sh` — structurele test uitbreiden

- Bestaande 6 sandbox-asserts voor `make_prompt_sandbox` blijven (dekken §1 rij 1+2).
- Nieuwe asserts voor §1 rij 3-5: een stub-claude-run via `compare` laat na afloop zien dat de vier `run_one`-bomen **geen `.git`** bevatten en **geen `git rev-parse --show-toplevel`** oplossen. Vangt een regressie die de worktree-tak per ongeluk terugbrengt.

### 3.3 `scripts/measure-cache-read-quota.sh` + test — invariant borgen

- Geen code-wijziging aan het script zelf.
- `scripts/test_measure_cache_read_quota.sh` krijgt één extra assert: `grep -q '"--allowedTools" ""' "$REPO_ROOT/scripts/measure-cache-read-quota.sh"`. Borgt dat de containment niet stilletjes wegvalt bij een refactor.

### 3.4 Doc-regel over env-var

Eén nieuwe paragraaf in `docs/cockpit/token-saver-meet-harnas.md` §1 (boven §1.1 "De gekozen methode", na de TL;DR):

> **Twee beveiligingen die hier niet werken en één die wel.**
> Een omgevingsvariabele (`GIT_SSH_COMMAND=/bin/false` op de claude-aanroep) is geen slot — die overleeft de shell niet waarin de agent zelf `git` draait (kaart 5934b954…, revert `2e0eb256`). Een scratch git worktree is geen sandbox — een linked worktree deelt per constructie de `.git/config` (en daarmee de remotes én de credentials) van de parent, en staat bovendien binnen de repo. Een `git archive`-export is de enige structurele beveiliging die we hier gebruiken: geen `.git`, geen remote, geen credentials, en de export ligt buiten `$REPO_ROOT` zodat een `cd ..` nooit de echte repo opent. Zie `harnas-spawn-inventaris.md` §1 voor de volledige tabel.

## 4. Wat deze wijziging NIET doet

- Geen promotion naar default-on voor welke lane dan ook.
- Geen wijziging aan `build_prompt` / `render_card_prompt` / `apply_saver` / `apply_real_saver`. De prompt-inhoud verandert niet; alleen de **containment** verandert.
- Geen audit van de overige (backend) spawn-paden. Die hebben eigen guards (worktree-pin, risk-class defaults, MCP-pinning) en vallen onder de dispatch-laag, niet onder meet-/test-harnassen.
- Geen `with_scratch_worktree`-helper verwijderen — andere code (cockpit.sh, worktree-trap-self) gebruikt 'm.

## 5. Foutafhandeling

- `make_prompt_sandbox` faalt closed (exit 1 op een missende `backend/app/kanban/dispatch.py` of een onverwachte `.git`-entry). `run_one` schrijft een `.missing`-marker en returnt 0 (huidige gedrag).
- `cleanup_prompt_sandbox` faalt nooit (`|| true`).
- De bestaande timeout-handling en `pass_tests=0`-carve-outs blijven ongewijzigd.

## 6. Teststrategie (TDD)

1. **Eerst falende test.** Nieuwe asserts in `test_measure_token_saver.sh` die na een `compare`-run controleren dat de vier `run_one`-bomen geen `.git` hebben. Falen op huidige code (worktree-tak).
2. **Implementatie.** `run_one` altijd via `make_prompt_sandbox`.
3. **Verificatie.** Bestaande 68 unit-asserts + 6 sandbox-asserts + de 4 nieuwe invariants + 1 `--allowedTools ""`-grep. Alles groen.

## 7. Open punten

- De `headless_runner._build_argv`-missende `--strict-mcp-config` wordt **niet** in deze kaart opgelost. Het is een gerelateerde observatie uit de inventarisatie die om een eigen onderzoek vraagt (andere spawn-laag, andere test-aanspraken); als losse observatie gepost op de kaart-activity-feed.
