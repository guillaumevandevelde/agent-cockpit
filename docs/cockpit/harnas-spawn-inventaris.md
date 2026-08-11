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

Eén **structurele** sandbox (`git archive`-export zonder `.git`, zonder remote, buiten `$REPO_ROOT`) is de containment die alle vijf meetarmen (`baseline` / `with-saver` / `real-saver` / `card-baseline` / `card-injector`) sinds commit `0052f863` delen. De oude `with_scratch_worktree`-tak plus de `GIT_SSH_COMMAND=/bin/false`-guard — die op de kaart-vormige arm bewezen ontoereikend was (kaart `5934b954…`, incident 2026-08-10, revert `2e0eb256`) — zijn weg; `run_one` routeert elke variant onvoorwaardelijk door `make_prompt_sandbox`. `measure-cache-read-quota.sh` blijft ongewijzigd (alleen `amplify` raakt een live quota en wordt al door `--allowedTools ""` ingedamd; die invariant borgen we met een source-grep in de bestaande test).

De **git**-sandbox sluit de ship-recipe. De **bord**-kant vraagt een tweede invariant: `isolate_kanban_writes` weigert outbound verkeer naar `127.0.0.1:8000` én `[::1]:8000` (nftables OUTPUT chain, prioriteit -150, vóór WSL2's `WSLOUTPUT`), zodat een gemeten agent die de REST-fallback uit de dispatch-prompt volgt geen schrijfpad naar het live bord heeft. Per-PID tabelnaam voorkomt de race tussen twee gelijktijdige meet-runs; trap-based cleanup ruimt `release_run_tree` en een harnas-brede EXIT/INT/TERM-trap. Test legt de invariant vast met een echte listener — Task 6 in `test_measure_token_saver.sh`. Faalt closed wanneer `sudo`/`nft` niet beschikbaar zijn — geen run zonder containment.

## 1. Inventarisatie van agent-spawnende scripts

| # | Script / pad | Werkdirectory | Prompt bevat mutatie-recept? | Huidige containment | Oordeel | Sandbox-actie |
|---|---|---|---|---|---|---|
| 1 | `scripts/measure-token-saver.sh card-baseline` | `make_prompt_sandbox` → `$HOME/.cache/cockpit-measure-sandbox/trial-N-card-baseline` | **Ja** — `build_card_prompt` rendert het echte ship-recept | ✅ structureel (geen `.git`, geen remote); ✅ bord-side (nft reject 127.0.0.1:8000 + [::1]:8000) | Veilig | Geen (al gesandboxed) |
| 2 | `scripts/measure-token-saver.sh card-injector` | idem | **Ja** — idem, met Caveman+Ponytail-slices | ✅ idem | Veilig | Geen (al gesandboxed) |
| 3 | `scripts/measure-token-saver.sh baseline` | idem (`run_one` routeert elke variant onvoorwaardelijk door `make_prompt_sandbox`) | Nee vandaag (`build_prompt` zonder ship-recept), maar `lib:82-108` is een **prompt-eigenschap** — één edit herhaalt het incident | ✅ structureel + ✅ bord-side | Veilig | Geen (al gesandboxed) |
| 4 | `scripts/measure-token-saver.sh with-saver` | idem | idem | ✅ idem | Veilig | Geen (al gesandboxed) |
| 5 | `scripts/measure-token-saver.sh real-saver` | idem + schrijft `<sandbox>/.claude/settings.json` | idem | ✅ idem | Veilig | Geen (`.claude/`-write werkt in een archive-tree) |
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

## 2.6 Alternatieven voor de bord-kant — netfilter vs. project-key vs. proxy

De git-sandbox dekt de ship-recipe. De **prompt** die de agent meekrijgt via `build_card_prompt` heeft echter ook een REST-fallback: regel 2513 van `dispatch.py` zegt letterlijk dat bij falende MCP de agent "straight to REST for every subsequent board update" moet, met `http://localhost:8000/api/v1/kanban` als voorbeeld-URL. De harness pinnet MCP expres leeg (`--strict-mcp-config --mcp-config .mcp-empty.json`), dus die fallback-trigger is de **normale** toestand van een meet-run, niet een randgeval. Een gemeten agent heeft dus een tweede mutatie-oppervlak: het live bord, via REST. Kaart `ee905064…` impediment vroeg hierom een oordeel.

| Alternatief | Wat het oplost | Wat het NIET oplost | Verdict |
|---|---|---|---|
| **nftables / iptables rule op `127.0.0.1:8000`** | Elke socket in deze process tree — `curl`, `urllib`, `requests`, raw `connect()` — krijgt een reject van de kernel. Geen env-var, geen PATH-override, geen `python3`-import die het kan omzeilen. | Vereist root of `sudo -n` op de host. WSL2-omgeving: de bestaande `WSLOUTPUT`/`WSLPOSTROUTING` chains blijven staan; onze rule hangt op `priority -150`, vóór `WSLOUTPUT priority filter`. | **Gekozen.** Structureel, lokaal, idempotent. `isolate_kanban_writes` is één `nft add table/chain/rule` drietal; `release_kanban_isolation` ruimt `table inet measure_kanban_isolation` op. |
| **Wegwerp-project-key + server-side write-guard** | Alleen writes die de juiste project-key dragen komen door; een meet-agent die per ongeluk een andere key gebruikt, faalt schoon. | De prompt vraagt niet om een project-key voor `POST /cards/{id}/move` en `POST /cards/{id}/comment` — die bewegen een bestaande kaart op id, project-key is geen parameter op die paden. Een key-guard blokkeert dus alleen `POST /cards` (nieuwe kaarten), niet de comment/move-paden die de prompt expliciet noemt. | **Onvolledig.** Dekt één van de drie REST-paden die de prompt aanwijst; de andere twee blijven open. |
| **`HTTP_PROXY`-env-var naar een sink** | Een `http://sink:9999`-proxy die op alle writes 403 retourneert. Werkt voor `curl` en `requests`. | Env-vars propageren niet betrouwbaar naar de agent-shell die `claude`'s tool-framework spawnt (zelfde les als `GIT_SSH_COMMAND=/bin/false` in §3.4: een env-var is geen slot). En de agent kan ook raw sockets gebruiken, die negeren `HTTP_PROXY`. | **Niet-structureel.** Herhaalt de env-var-familie waarvan deze kaart juist afscheid neemt. |
| **Prompt-patch om de localhost-URL te vervangen** | De agent ziet een onbereikbaar adres, faalt schoon. | Verandert de prefix die `cache_read` meet — de hele reden dat deze harness bestaat is een prefix-meting op de productie-vorm. Een patch-URL maakt de `card-baseline` vs `card-injector`-vergelijking zinloos. | **Breekt de meting.** Alleen verdedigbaar als we de cache-read-vraag loslaten, wat niet het plan is. |
| **Stop de kanban-server tijdens de meet-run** | Elke connectie naar `127.0.0.1:8000` faalt met "connection refused". | Verbreekt óók de cockpit-UI van de operator (als die openstaat), andere agents in flight, de dispatch-loop zelf. Geen lokaal-harnas-isolation; globale verstoring. | **Te breed.** Sandbox wint omdat het alleen deze process tree raakt, niet de hele box. |

**Waarom `nftables` toch primair is**, zelfs met de `sudo`-vereiste:

1. **Geen env-var.** De les van kaart `5934b954…` is dat een omgevingsvariabele de shell-grens niet overleeft. Een kernel-hook wel — elke socket die deze process tree opent, ongeacht via welke library, raakt de OUTPUT chain.
2. **Geen prompt-mutatie.** `cache_read` blijft een prefix-meting op de productie-vorm; de URL in de prompt blijft letterlijk staan.
3. **Geen globale verstoring.** Priority -150 loopt vóór WSL2's `WSLOUTPUT` zonder 'm uit de weg te ruimen; de rule is selectief op daddr+dp ort, niets anders op de host merkt het.
4. **Fail-closed.** Geen `sudo`-toegang? Geen run. Het `.missing`-veld krijgt `kanban isolation failed: …` en de rij verschijnt als `?` in plaats van als een cijfer dat de garantie niet waarmaakt.

**Branch protection op de REST-kant** bestaat niet als GitHub-concept en was ook niet in de vraag; het is een git-side mechanisme. Project-key-isolatie (rij 2) komt terug als server-side guard, niet als client-side harness-zorg.

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

### 3.4 Bord-side isolatie (`isolate_kanban_writes` / `release_kanban_isolation`)

- **Nieuwe helpers in `scripts/lib/measure_token_saver_lib.sh`.** `isolate_kanban_writes` zet een nftables-tabel `measure_kanban_isolation` met één rule: `ip daddr 127.0.0.1 tcp dport 8000 reject` op een OUTPUT chain met `priority -150`. Idempotent (verwijdert een eventuele stale tabel van een eerdere crash). `release_kanban_isolation` doet `nft delete table` — ook idempotent. `probe_kanban_isolated` opent `/dev/tcp/127.0.0.1/8000` als een snelle aanwezigheids-test (gebruikt door de test-suite, niet door de harness zelf).
- **`run_one` in `scripts/measure-token-saver.sh`.** Voor de `claude -p`-aanroep: `isolate_kanban_writes`. Faalt closed: een niet-geïnstalleerde rule schrijft een `.missing`-marker met de `nft`-fout, dezelfde rij die `compare`-runs al als `?` rapporteren. `release_run_tree` (dat eerder alleen sandbox opruimde) doet nu ook `release_kanban_isolation` zodat elke `run_one` netjes eindigt. Een EXIT/INT/TERM-trap op het harnas-niveau doet dezelfde release als safety net voor crashes tussen install en cleanup.
- **Teststrategie (Task 6 in `test_measure_token_saver.sh`):** 10 nieuwe asserts — install, tabel, rule-scope, rule-actie, blokkade-probe, release-tabel, release-idempotent, integratie-cleanup, integratie-meting. Negatieve controle op de blokkade-probe: na release faalt 'ie alleen als er ook geen listener is — wat we vastleggen is "geen rule = kernel doet zijn normale werk".

### 3.5 Doc-regel over env-var

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
3. **Bord-side (§3.4):** Task 6 voegt 10 asserts toe voor `isolate_kanban_writes`/`release_kanban_isolation`: install/cleanup van de nft-tabel, scope van de rule, fail-closed integratie. Negatieve controle op (v): na `release_kanban_isolation` moet een verse `isolate_kanban_writes` opnieuw slagen — anders maskeert een "toevallig toch onbereikbaar"-status een gebroken rule.
4. **Verificatie.** Bestaande unit-asserts + 6 sandbox-asserts + 6 nieuwe invariants (1 positieve + 5 negatieve-controle) + 1 `--allowedTools ""`-grep + 10 bord-side asserts. Alles groen; de summary-teller sluit de ene *verwachte* `bad()`-rij van de negatieve controle uit zodat het script met exit 0 eindigt.

## 7. Open punten

- De `headless_runner._build_argv`-missende `--strict-mcp-config` wordt **niet** in deze kaart opgelost. Het is een gerelateerde observatie uit de inventarisatie die om een eigen onderzoek vraagt (andere spawn-laag, andere test-aanspraken); als losse observatie gepost op de kaart-activity-feed.
- `isolate_kanban_writes` vereist `sudo -n` op de host. Omgevingen zonder `sudo`-toegang (containers zonder cap, externe runners) krijgen een `kanban isolation failed`-rij. Dat is een fail-closed meting (geen cijfers), niet een crash — maar wie de harness op zo'n omgeving wil draaien moet een vervolg-kaart filen voor een `CAP_NET_ADMIN`-pad (user+net namespace via `unshare -Urn` + veth-paar + NAT, bv. via `slirp4netns`). Niet in scope van deze kaart.
