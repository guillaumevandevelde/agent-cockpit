# 'Updates' (self-update) feature — past die nog bij Cockpit's missie?

**Datum:** 2026-07-09
**Status:** besloten
**Kaart:** _zie doc — geen hex-id in dit beslisdoc vastgelegd_
**Uitkomst:** **Houden, zoals het is.** Geen aanpassing aan `scripts/update.sh`, router of page.

> Kanban-kaart: "Beslis of de 'updates' (self-update) feature nog past bij Cockpit's missie".
> DoD van de kaart: eerst vaststellen wat `scripts/update.sh` vandaag doet en of de
> feature in de fork-context nog zinvol is, **voordat** dit als gewone implementatiekaart
> behandeld wordt.

## Context

De kaartbeschrijving framet de feature als **overgeërfd van upstream claude-deck** en suggereert
dat `scripts/update.sh` mogelijk van `upstream` zou pullen. Beide aannames kloppen niet — de
feature is **volledig fork-built** en pullt al van de fork zelf. Voor de zekerheid zijn ze
hieronder apart geverifieerd.

### 1. Is de feature overgeërfd van upstream?

Nee. `git log upstream/master -- 'scripts/update.sh' 'frontend/src/features/updates'
'backend/app/api/v1/update'` levert **niets** op. De merge-base tussen onze `master` en
`upstream/master` is `42429f3` ("release: prepare v1.3.0", 2026-06-09) — alles daarna op
onze kant is onafhankelijk. Het ontstaan van de feature is één commit, volledig in deze
repo:

| Commit | Datum | Wat |
|---|---|---|
| `1bf5920` | 2026-07-02 | `feat(updates): in-UI 1-click self-update with SSE progress and auto-rollback` (853 insertions, 8 files) |
| `a216a54` | 2026-07-02 | `fix(update.sh): wrap git merge/rebase output in JSON event lines` |
| `a971d4c` | 2026-07-02 | `fix(update.sh): correct log_event JSON — ${3:-{}} leaked a literal brace` |
| `5676701` | 2026-07-02 | `fix(scripts): mark update.sh executable in git` |
| `da589a4` | 2026-07-03 | `Merge branch 'k-extraheer-fet-1848'` (refactor) |
| `23c1ab3` | 2026-07-08 | `chore(backend): widen ruff ruleset, land mechanical fixes` |
| `35f7a50` | 2026-07-08 | `refactor(frontend): migrate 10 more components off fetch boilerplate` |

De fix-cyclus (twee JSON-encoder-bugs, één executable-bit, één merge-output-wrapper) zit
typerend in de "first 48 uur" van een nieuwe feature en is daarna opgehouden. Niets in de
history wijst op upstream-herkomst.

### 2. Wat doet `scripts/update.sh` vandaag — precies?

Het script is 154 regels, staat in `scripts/update.sh`, en is een dunne orchestrator. Volgorde
met citaten:

| Fase | Wat | Pullt van |
|---|---|---|
| preflight | "Werkmap is niet schoon" + git-availability check | n.v.t. |
| pulling | `git fetch origin` (regel 65), gevolgd door `git merge --ff-only origin/$CURRENT_BRANCH` op master/main, of `git pull --rebase` op andere branches (regels 76-94) | **`origin`**, d.w.z. `git@github.com:guillaumevandevelde/claude-cockpit.git` — **niet** `upstream` |
| building | `cd frontend && npm install && npm run build` | n.v.t. |
| installing | `pip install -q -r requirements-dev.txt` (en `npm install` als `backend/package-lock.json` bestaat) | n.v.t. |
| healthcheck | `curl /api/v1/health` met 12 retries × 5 s | n.v.t. |
| done / error | streamt JSON-event naar SSE | n.v.t. |
| rollback (bij failure) | `git reset --hard "$PREVIOUS_HEAD"` + rebuild + opnieuw pip install | n.v.t. |

De kaartbeschrijving vreesde dat "updaten in de oorspronkelijke claude-deck-zin" (upstream-release
pullen) dubbelzinnig was geworden — dat scenario is hier **niet van toepassing**: het script pullt
al uitsluitend van `origin` (deze fork), niet van `upstream`. Geen aanpassing nodig op dat vlak.

### 3. Hoe verhoudt zich dat tot de fork's release-flow?

Cockpit heeft een eigen GitHub-releases-flow via `.github/workflows/release.yml` (handmatig
getriggerd met `version`-input: valideert semver, `npm ci`, `npm run build`, maakt
GitHub-release aan met tarball). Dat is de **release-auteur-flow** — iemand met schrijfrechten
op de repo die een getagd release wil publiceren. `scripts/update.sh` is de
**release-consumer-flow** — een Cockpit-gebruiker die "geef me de laatste master" wil. Het
zijn verschillende kanten van dezelfde cyclus en ze zijn met opzet gescheiden: releases
geven een vast, audit-baar versiepunt (tags + tarball), `git pull origin/master` geeft de
loopende ontwikkeling.

Voor de vraag "past deze feature bij Cockpit's missie?" is alleen de consumer-flow relevant
— dat is wat de knop doet.

## Past de feature bij Cockpit's missie?

Ja, met een kleine documentatiebijwerking. Drie onafhankelijke redenen:

1. **De fork heeft een single-server dev-flow** (`./scripts/cockpit.sh start` draait de stack
   supervised op de hoofd-checkout). Een "haal de laatste master" in één klik met
   auto-rollback is precies wat je wilt in die flow: sneller en veiliger dan handmatig
   `git pull && npm run build && pkill -f uvicorn && ./scripts/cockpit.sh start`. De
   healthcheck + `git reset --hard` rollback maken het veiliger dan een handmatige `git
   pull`, die de gebruiker half in een kapotte staat kan achterlaten.

2. **De worktree-kanban-flow neemt dit niet over.** Engineers werken in
   `.claude/worktrees/` (zie `docs/cockpit/kanban-dispatch-spec.md`), en hun werk
   landt uiteindelijk via merge in `master` op de hoofd-checkout. De
   developer-machine waar Cockpit zelf op draait is typisch wél de hoofd-checkout
   op `master`. Voor diegene is "pull latest, rebuild, restart" — wat deze knop doet —
   de juiste handeling; de worktree-flow is er voor de agenten, niet voor de host.

3. **De feature is klein, getest, en wordt al onderhouden.** 853 insertions op dag 1,
   6 fix-/refactor-commits sindsdien, eigen `docs/features/updates.md`
   (commit `f54a078`), eigen `backend/tests/test_update_api.py` (≥7 cases op
   status + run + error paths), gerefereerd in de frontend-navigatie en `App.tsx`. Dit
   is geen dode code die is blijven hangen — hij is in actief gebruik, gedekt, en
   gedocumenteerd.

## Aanbeveling

**Houden, zoals het is.** Geen code-aanpassing aan `scripts/update.sh`, de backend
router, of de frontend page. De implementatie is al correct afgestemd op de fork's
realiteit (pullt van `origin`, gebruikt `VERSION` + `bump-version.sh`, respecteert
de branch-state met `merge --ff-only` op master en `pull --rebase` elders).

**Eén kleine docs-bijwerking wel:** `CLAUDE.md`'s featurelijst (regel 133) noemt
"Config, MCP Servers, …, Dashboard" — 26 features — maar **niet** "Updates", terwijl
de feature wél in de navigatie, in `App.tsx`, en in `docs/features/updates.md` staat.
Dat is een drift, geen functioneel probleem. Deze kaart dicht die door "Updates"
aan die lijst toe te voegen. Geen aanpassing aan `docs/index.md` (de hero-features
daar zijn een selectie, niet een volledige lijst), geen aanpassing aan `README.md`
(Quick Start noemt Docker, niet de update-knop — die is een post-install actie).

`kanban-followups.md` krijgt één nieuwe sectie die naar dit document wijst,
analoog aan de Presence- en Docker-secties.

### Wanneer heroverwegen

- **Als de fork ooit een echte "deploy"-flow krijgt** (multi-host, getagde releases
  uitrollen via Ansible/Chef/Terraform, niet "pull op de dev-machine") is `update.sh`
  niet meer het juiste gereedschap voor productie-updates. In dat scenario kan
  `update.sh` blijven voor de dev-ervaring en komt er een aparte
  release-tarball-installer naast — geen reden om de huidige knop te schrappen.
- **Als de fork ooit van Git naar een ander VCS migreert** is de git-aanname in
  het script (merge/rebase) dood. Vervanging zou een aparte kaart zijn, niet
  een reden om nu al te verwijderen.
- **Als `release.yml` ooit een `--install` artefact publiceert** (een getarlde
  versie met installatie-instructies) en de Cockpit-gebruiker dat verkiest boven
  `git pull`, kan de Update-pagina beide flows aanbieden. Tot dan is `git pull`
  de simpelste, meest natuurlijke optie voor een single-server setup.

## Wat deze kaart doet

1. Dit document + een nieuwe sectie in `docs/cockpit/kanban-followups.md` die er
   naar verwijst.
2. Eén-regel toevoeging aan `CLAUDE.md`'s featurelijst: "Updates" tussen "Sandcastle"
   en "APM" (alfabetisch consistent met de bestaande volgorde).
3. Geen codewijziging: de beslissing is "niets aan de feature zelf, alleen de
   documentatie ervan".
