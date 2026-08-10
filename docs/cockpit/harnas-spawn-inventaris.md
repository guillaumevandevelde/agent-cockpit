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

## 2.5 Alternatieven overwogen en niet gekozen

De vraag "moet dit via branches en branch protection?" hoort bij deze kaart — `scripts/measure-token-saver.sh` had twee eerdere guards die faalden (`GIT_SSH_COMMAND=/bin/false`, `with_scratch_worktree`), en de menselijke reviewer vroeg of branch protection de betere laag was. Vergelijking, in volgorde van waar ze het meest voor de hand lagen:

| Alternatief | Wat het oplost | Wat het NIET oplost | Verdict |
|---|---|---|---|
| **GitHub branch protection op `master`** (vereist PR + review) | Elke `git push origin HEAD:master` van élke actor op élke machine faalt | Voorkomt geen lokale `git commit`, geen werkbestanden op schijf, geen side-effecten op de agent-shell buiten `git`. Voorkomt ook geen push naar een feature-branch (de agent kan een branch pushen, een PR openen, en merges via auto-merge als checks slagen). Vereist GitHub-side config per repo — lokaal repro's van dit incident (zonder `origin`) zijn er niet door gedekt. | **Defense-in-depth, niet primair.** Past bij deze kaart als een aanvullende remote-laag, niet als vervanging van de lokale sandbox. De menselijke reviewer van kaart `5934b954…` bevestigde die volgorde: zonder structurele containment is branch protection een externe muur voor een probleem dat lokaal ontstaat. |
| **Fork-isolatie** (harness pusht naar een persoonlijke/fork remote i.p.v. `origin`) | Elke push belandt op een niet-master branch van een niet-productie repo | Vereist een aparte machine-account + SSH-key in de harness, een aparte remote-URL, en de harness moet `git push` überhaupt proberen — twee extra failure-paden (verkeerde remote, credentials lekken alsnog naar de productie-remote). De agent heeft nog steeds de hele dispatch-prompt in zijn context. | **Te zwaar voor een meet-fixture.** Past bij product-werk waar een persoonlijke scratch-repo al bestaat, niet bij een `git archive`-treetje. |
| **Read-only mount van `.git` / alleen-lezen filesystem wrapper** | Blokkeert `git commit`/`push` in de boom | Blokkeert géén non-git commando's die even goed schade doen (`rm -rf $REPO_ROOT` vanuit de agent-shell, files in `$HOME/.cache` meenemen, REST naar `localhost:8000`). Heeft root/capabilities nodig, breekt op niet-Linux. | **Te smal.** Sandbox wint omdat het élke git-mutatie onmogelijk maakt, niet alleen writes. |
| **MCP-pin / `--strict-mcp-config`** (dispatch-zijde) | Voorkomt dat de agent met onverwachte MCP-servers praat | Heeft geen effect op commando's in de agent-shell. Een harness heeft geen MCP-server-overdrachtsprobleem (zie ook `headless_runner._build_argv`-observatie in §1 buiten-scope). | **Complementair, andere laag.** Niet dubbel met sandbox; hoort bij de dispatch-laag waar het al gedeeltelijk zit. |

**Waarom `make_prompt_sandbox` toch primair is:**

1. **Lokale invariant.** GitHub branch protection beschermt de remote; sandbox beschermt het oppervlak waar de agent zijn commando's draait. De incident-timeline (kaart `5934b954…`) begon lokaal: één `git push origin HEAD:master` vanuit een worktree, geen PR, geen review — een guard die alleen op de remote kant zit, had dit niet voorkomen. Branch protection had deze specifieke push kunnen blokkeren, maar niet de daaropvolgende (vanuit diezelfde agent-shell) `rm -rf` of REST-mutaties, en niet de brittle env-var-familie die de volgende regressie zou zijn.
2. **Geen externe afhankelijkheid.** `make_prompt_sandbox` werkt op een verse checkout zonder `origin`, in een container zonder GitHub, in een fork zonder branch protection, en in een test-harness zonder netwerk. Branch protection werkt niet in die omgevingen.
3. **Structurele bovenop graduele.** De andere drie alternatieven pakken precies één vector (push / commit / MCP) en laten de rest open. `make_prompt_sandbox` pakt de hele klasse "git doet iets in deze boom" in één keer.
4. **Branch protection is complementair.** Niets in deze kaart sluit GitHub branch protection op `master` uit als *extra* remote-laag. De vraag van de reviewer was of het primair kon zijn; het antwoord is nee, maar een toegevoegde remote-laag ernaast is een lage-meerkost-poging die afzonderlijk gefiled kan worden. Dat staat hier zodat een vervolg-kaart de combinatie kan claimen zonder deze te heropenen.

## 3. Wijzigingen

### 3.1 `scripts/measure-token-saver.sh` — `run_one` uniform sandboxen

- Verwijder de `with_scratch_worktree`-tak uit `run_one`. Elke variant krijgt `make_prompt_sandbox`.
- Vereenvoudig `release_run_tree` tot `cleanup_prompt_sandbox` (sandboxed=1 altijd).
- Geen wijziging aan `apply_real_saver` (schrijft `<sandbox>/.claude/settings.json`, werkt in archive).

### 3.2 `scripts/test_measure_token_saver.sh` — structurele test uitbreiden + negative-control-regression-guard

- Bestaande 6 sandbox-asserts voor `make_prompt_sandbox` blijven (dekken §1 rij 1+2).
- De inline `SANDBOX_CHECK`-blok uit de eerste versie had een **routeer-bug**, gevlagd in reviewer-ronde 2 (kaart `ee905064…`). De Python schreef violations naar **STDOUT** en de wrapper redirecte die naar `/dev/null`; de bash besliste pass/fail op de **stderr-bestandsgrootte** (altijd leeg). De correcte exit-code werd met `|| true` gemaskeerd. Resultaat: `ok` op zowel gezonde als gebroken run-trees — exact het type "assertion that passes in both broken and fixed states" dat `CLAUDE.md` # Test-blok expliciet verbiedt voor `scripts/test_check_*.sh`-auteurs.
- Nieuwe vorm: **één wrapper-functie** `assert_sandbox_invariants` met (a) python-heredoc inline zodat violations naar **STDERR** gaan (niet stdout, niet /dev/null), (b) bash die pass/fail op de python-exit-code vertakt (niet op de stderr-bestandsgrootte), (c) returnwaarde is de python-exit-code zodat de test verder kan chainen. Eén primitief, twee callsites: positieve controle (Task 5: `compare`-smoke levert de vier sandbox-bomen, wrapper gaat `ok`) en negatieve controle (Task 5b: een handgemaakte `claude.log` met een boom mét `.git`, wrapper moet `bad`).
- Negative-control asserts bewijzen dat (i) python exit 1 op een gebroken log, (ii) violations op stderr staan, (iii) de violation-lijst de juiste invariant-tekens draagt, en (iv) precies één `bad()`-rij wordt geregistreerd — het tegenovergestelde van wat de bug deed. De summary-teller houdt expliciet rekening met die ene verwachte `bad()`-rij (`EXPECTED_BAD_AT_END`) zodat een werkende negatieve controle de suite niet foutief laat falen.

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

1. **Eerst falende test.** Nieuwe asserts in `test_measure_token_saver.sh` die na een `compare`-run controleren dat de vier `run_one`-bomen geen `.git` hebben EN dat een handgemaakte gebroken `claude.log` door dezelfde wrapper als `bad` wordt geregistreerd (negative control). Vóór de fix faalden de negatieve-controle-asserts omdat de buggenante wrapper `ok` meldde op een gebroken input.
2. **Implementatie.** `run_one` altijd via `make_prompt_sandbox`; SANDBOX_CHECK-bash herschreven tot één wrapper die violations op STDERR zet en pass/fail op exit-code baseert (niet op stderr-bestandsgrootte).
3. **Verificatie.** Bestaande unit-asserts + 6 sandbox-asserts + 6 nieuwe invariants (1 positieve + 5 negatieve-controle) + 1 `--allowedTools ""`-grep. Alles groen; de summary-teller sluit de ene *verwachte* `bad()`-rij van de negatieve controle uit zodat het script met exit 0 eindigt.

## 7. Open punten

- De `headless_runner._build_argv`-missende `--strict-mcp-config` wordt **niet** in deze kaart opgelost. Het is een gerelateerde observatie uit de inventarisatie die om een eigen onderzoek vraagt (andere spawn-laag, andere test-aanspraken); als losse observatie gepost op de kaart-activity-feed.
