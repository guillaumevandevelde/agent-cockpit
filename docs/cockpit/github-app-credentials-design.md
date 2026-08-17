---
title: "GitHub App credential binding voor push-identity"
type: design
status: active
---

<!--
Bron-analyse voor kanban-kaart bf635110badd4198b0725265789ecfc1 (kind van
fork-strategy-claude-deck-316 §4.2). Bouwt voort op de keuzes in
github-issue-webhook.md (SecretStore-pattern, HMAC-secret-gebaseerde
authenticatie). Cross-references: upstream PR #316 PR2-plan en
git_credential_helper.py, cockpit-richting-decision.md §6 (krimp-regime),
git-ship/SKILL.md + ship_prompt.py::_build_ship_instructions mirror.
-->

# GitHub App credential binding voor push-identity

**Effect:** een gedispatchte sessie die `git push` doet vanuit de worktree
authentiseert zich als GitHub App, niet als de machine-gebruiker. De push is
herleidbaar naar de App, niet naar een persoonlijke credential die per ongeluk
in een log of tmux-env kan lekken.

**Kaart:** `bf635110badd4198b0725265789ecfc1` (kind van
[`fork-strategy-claude-deck-316.md`](./fork-strategy-claude-deck-316.md) §4.2).

**TL;DR.** Per-project drie SecretStore-entries (`GITHUB_APP_ID`,
`GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_INSTALLATION_ID`). Eén
backend-service `GithubAppAuthService` (~150 regels) mint kort-levende
installation tokens via JWT. Eén credential-helper (~75 regels, stdlib-only)
in `backend/mcp_shim/git_credential_helper.py` belt één nieuw endpoint
`POST /api/v1/github/credential`. De ship-recipe zet hij-scope credential
helper op de worktree als `COCKPIT_GITHUB_APP_ENABLED=true` én het project
de drie secrets heeft. PR-creatie, kernel-bound pane-check en workspace-lease
zijn aparte kind-kaarten — die nemen we hier niet mee.

## 1. Probleem

De huidige ship doet `git push origin HEAD:master` vanuit de worktree
([`.claude/skills/git-ship/SKILL.md` stap 4a](../../.claude/skills/git-ship/SKILL.md),
gespiegeld in
[`backend/app/kanban/ship_prompt.py:1020`](../../backend/app/kanban/ship_prompt.py)).
Dat werkt omdat git de credential van de lokale omgeving pakt — `~/.gitconfig`
of ssh-agent. Twee gevolgen:

- **Geen herleidbare actor.** GitHub toont de commit als
  `<machine-gebruiker>`, niet als de sessie die de commit heeft gemaakt.
- **Geen onvervalsbaar push-recht.** Een token-lek in een tmux-env of
  prompt levert eenmalig push-recht op de hele repo.

Een GitHub App installatie per repo geeft beide: kort-levende tokens die
alleen `Contents: write` op één installatie kunnen, en een author-tag
`(app)` achter de commit.

## 2. Wat we van upstream wel en niet overnemen

Upstream PR #316 PR2 implementeert dit patroon volledig — 9 taken, 100+
tests, 6000+ regels — voor een wereld met agent_teams, PR-creatie,
peer-process-isolatie, kernels-deriving current owner, en workspace-leases.
Die laag hebben wij niet: we mergen direct naar master, elke sessie is
dezelfde machine, geen apart proces per agent.

**Wel overnemen:**

- `git_credential_helper.py` (75 regels, stdlib) — patroon en pad
  (`backend/mcp_shim/`).
- `GithubAppAuthService` (gehalveerd: JWT + installation lookup + cache,
  geen per-purpose token-splitsing, geen revoke-loop).
- 4-veld config (`github_app_id`, `github_app_private_key_path`,
  `github_app_bot_login`, plus `installation_id` in SecretStore).
- URL-scoped `credential.https://github.com.<owner>.helper` op de worktree.

**Bewust niet overnemen** (PR2 in upstream, elders in onze fork):

- **PR-creatiepad** — wij mergen direct. Upstream's
  `pr_ready`/`pr_opened`-discriminator, `GithubVerificationService` (1124
  regels), `GithubClient` (383 regels) en `list_pulls_for_head` met
  paginatie zijn voor App-authored PRs, niet voor onze `git push` flow.
- **Kernel-bound pane-resolutie** — `peer_process.py` (291 regels) en de
  `parent_walk`-budget-correctie (16 voor credential, 32 standaard) hoort
  bij upstream's agent_teams-onderzoek. Onze sessies delen één tmux-server,
  geen per-pane-proces.
- **Workspace-lease + provisioning** — `GithubWorkspaceService` (1176
  regels) en de G1–G3 fasen zijn kind-kaart
  [`a2268cd256944398bfec1da170b0de09`](./kanban-followups.md) (workspace
  lease).
- **Closed-issue reconciliation** — afhankelijk van
  [`github-issue-webhook.md`](./github-issue-webhook.md) PR-flow.

## 3. Configuratie

### 3a. Process-env (deployment-side)

[`backend/app/config.py:40`](../../backend/app/config.py) krijgt drie
nieuwe Settings-velden, elk optioneel met lege default:

```python
github_app_id: str = ""                    # numeriek App-id als string
github_app_private_key_path: str = ""      # absolute pad naar PEM
github_app_bot_login: str = ""             # "<slug>[bot]", bv. "my-cockpit[bot]"
github_app_token_refresh_margin_seconds: int = 300
```

Lege default = App-mode is uit. Een service die op een ongezette
`github_app_id` checkt, geeft `configured() == False` terug en de
ship-recipe valt door naar ambient (huidige gedrag: ssh-agent /
`~/.gitconfig`). Geen backend-restart nodig om App-mode per project aan
of uit te zetten — alleen de SecretStore-entries veranderen.

### 3b. SecretStore (per project)

Drie namen, gezet via
[`PUT /api/v1/secrets/{project_key}/{name}`](../../backend/app/api/v1/secrets.py:130):

```bash
curl -X PUT "http://localhost:8000/api/v1/secrets/<project_key>/GITHUB_APP_ID" \
     -H 'Content-Type: application/json' \
     -d '{"value":"<numeriek-app-id>"}'

curl -X PUT "http://localhost:8000/api/v1/secrets/<project_key>/GITHUB_APP_PRIVATE_KEY" \
     -H 'Content-Type: application/json' \
     --data-binary @private-key.pem

curl -X PUT "http://localhost:8000/api/v1/secrets/<project_key>/GITHUB_APP_INSTALLATION_ID" \
     -H 'Content-Type: application/json' \
     -d '{"value":"<numeric>"}'
```

De private key gaat als platte tekst de SecretStore in — die versleutelt
met scrypt + ChaCha20-Poly1305
([`secrets_store.py:147-208`](../../backend/app/services/secrets_store.py)),
chmod 600, atomic rename. De REST-API geeft nooit waarden terug bij
`GET /?project_key=…`. **Alle drie** zijn verplicht: als er één ontbreekt
is App-mode uit voor dat project, en de ship-recipe valt door.

De `bot_login` is afgeleid van het App-id (formaat `<slug>[bot]`) —
NIET in SecretStore, want niet-geheim. Verificatie via GitHub-API
(`/users/{bot_login}`).

## 4. De service: `GithubAppAuthService`

[`backend/app/services/github_app_auth_service.py`](../../backend/app/services/github_app_auth_service.py)
(nieuw, ~150 regels). Drie verantwoordelijkheden:

1. **JWT minten** — RS256 over `{iat, exp, iss=app_id}` met
   `github_app_private_key_path`. `exp` is `now + 9 min`,
   `iat` is `now - 30 s` (GitHub-bandbreedte).
2. **Installation lookup** — één keer per project bij eerste
   credential-vraag, met de JWT bovenop. `GET /repos/{owner}/{repo}/installation`
   (zie
   [upstream `github_app_auth_service.py:resolve_installation`](https://github.com/adrirubio/claude-deck/blob/master/backend/app/services/github_app_auth_service.py)).
3. **Installation-token mint + cache** —
   `POST /app/installations/{id}/access_tokens` geeft een token met
   eigen `expires_at`. Cache in process-local dict, sleutel
   `(installation_id, host)`. Margin
   `github_app_token_refresh_margin_seconds` (default 300 s) voorkomt
   dat we op de grens zitten.

Errors zijn stable codes, secrets-vrij:

| Code | Betekenis |
|---|---|
| `app_auth_unconfigured` | Een van `github_app_id`, `private_key_path`, `bot_login` ontbreekt |
| `app_installation_lookup_failed` | GitHub 5xx of netwerk-fout |
| `app_not_installed` | Mint op een onbekende installation_id |
| `app_token_mint_failed` | GitHub weigert token (rate-limit, suspended App) |

Geen token, JWT of private-key mag in een logregel, audit-row of
HTTP-response body verschijnen. Alleen `installation_id` (numeriek,
niet-geheim) komt in de cache-sleutel.

**Niet in scope:** per-purpose token-splitsing (upstream heeft `push`
vs `pull_request`), token-revocation bij workspace-release (geen lease),
of multi-worker cache-coördinatie. Process-local is prima voor één
backend-worker; als we later schalen is dat een aparte zorg.

## 5. Credential-helper

[`backend/mcp_shim/git_credential_helper.py`](../../backend/mcp_shim/git_credential_helper.py)
(nieuw, ~75 regels, stdlib only — geen `httpx`, geen `app`-imports).
Patroon verbatim van upstream: leest stdin `protocol=…\nhost=…\npath=…`,
belt één endpoint, schrijft `username=x-access-token\npassword=<jwt>\n`
naar stdout.

```bash
git config --worktree credential.https://github.com.<owner>.helper \
    '!python3 /abs/path/to/git_credential_helper.py --deck-url <loopback> --project-key <pk>'
```

**Twee afwijkingen van upstream:**

- Geen `--workspace-token` flag. Onze sessie is de backend; de
  credential-helper belt localhost zonder bearer. Het geheim zit in de
  SecretStore, niet in een pane-token.
- Geen `peer_process`-check. Geen per-pane-proces, geen kernel-walk.

De loopback-URL is `http://127.0.0.1:{settings.port}/api/v1/github/credential`
— zelfde resolutie als `deck_base_url()` in upstream. Helper is een
losse executable, geen MCP-import — anders trekt `git push` de hele
backend-dependency-tree mee.

## 6. Endpoint

`POST /api/v1/github/credential` (nieuw, in
[`backend/app/api/v1/github/router.py`](../../backend/app/api/v1/github/router.py)).
Body:

```json
{ "project_key": "git:github.com/owner/repo",
  "protocol": "https", "host": "github.com", "path": "owner/repo.git" }
```

Response 200:

```json
{ "username": "x-access-token", "password": "<installation-token>" }
```

Faalcodes:

| Status | Code | Wanneer |
|---|---|---|
| 503 | `app_auth_unconfigured` | Een van de drie SecretStore-entries ontbreekt voor `project_key` |
| 503 | `app_not_installed` | Installatie bestaat niet voor `owner/repo` |
| 503 | `app_token_mint_failed` | GitHub weigert mint |
| 400 | `invalid_request` | Geen `project_key`, of `host != "github.com"` |

Loopback-only: requests van niet-`127.0.0.1` krijgen 403 voor ze de
service aanraken. De credential-helper draait in dezelfde tmux-server,
geen extern verkeer.

`host != "github.com"` weigering is een harde preconditie — de helper
is alleen voor GitHub Apps. Een helper voor `gitlab.com` zou een
andere service zijn, niet deze.

## 7. Worktree-configuratie: wanneer én hoe

De ship-recipe ([`.claude/skills/git-ship/SKILL.md`](../../.claude/skills/git-ship/SKILL.md)
stap 4a, mirror in
[`ship_prompt.py::_build_ship_instructions`](../../backend/app/kanban/ship_prompt.py:1020))
krijgt een nieuwe stap tussen `git worktree add` en `git push`:

```bash
# Alleen als COCKPIT_GITHUB_APP_ENABLED=true (env-flag)
# EN het project heeft de drie SecretStore-entries.
if [ "$COCKPIT_GITHUB_APP_ENABLED" = "true" ] && \
   curl -fsS "http://127.0.0.1:8000/api/v1/secrets/?project_key=$PROJECT_KEY" \
       | jq -e '.names | contains(["GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_INSTALLATION_ID"])' \
       > /dev/null; then
  # 1. Forceer HTTPS push-URL op deze worktree (scoped, niet op main checkout).
  git -C "$WT" remote set-url origin "https://github.com/$OWNER/$REPO.git"
  # 2. URL-scoped credential helper op deze worktree.
  git -C "$WT" config --worktree \
      "credential.https://github.com.$OWNER" \
      "!python3 '$ABS_HELPER' --deck-url 'http://127.0.0.1:8000' --project-key '$PROJECT_KEY'"
fi
```

**HTTPS-push-URL-eis.** Git roept HTTP-credential-helpers alleen aan
voor HTTPS-remotes. Onze default remote is SSH
(`git@github.com:guillaumevandevelde/agent-cockpit.git`); zonder
overschrijven zou de helper niet vuren en de push alsnog met ssh-agent
gaan. De `remote set-url` op de worktree (`--worktree`-scope) is
gelimiteerd tot die checkout — de hoofd-checkout blijft SSH voor de
menselijke workflow.

**Cleanup.** Na `git push` succesvol is, of bij `git push` failure,
haalt de ship-recipe de worktree-config weg:

```bash
git -C "$WT" config --worktree --unset "credential.https://github.com.$OWNER" || true
git -C "$WT" config --worktree --unset remote.origin.url || true
```

Anders blijft de helper-config rondslingeren op opvolgende sessies die
dezelfde worktree-mount gebruiken.

## 8. Tests

Twee lagen, mirroring van de twee beveiligingsgrenzen:

**Helper-laag** (`backend/tests/mcp_shim/test_git_credential_helper.py`,
~12 tests, ~3 s):

- Stdin/stdout contract: één `protocol=…\nhost=…\n`-input → exact
  `username=x-access-token\npassword=<token>\n\n` op stdout.
- `get`-operatie vuurt de HTTP-call; andere operations zijn silent no-op.
- Backend geeft 503 → helper exit 1 + stderr `Claude Deck could not
  provide a GitHub credential`.

**Auth-service-laag** (`backend/tests/test_github_app_auth_service.py`,
~10 tests, ~2 s):

- `configured()` vals-positief op partial config (1 van 3).
- `resolve_installation` 404 → None, 5xx → `app_installation_lookup_failed`.
- Mint-cache: tweede call binnen TTL = 1 GitHub-call, niet 2.
- Secrets-vrij logging: `caplog` reguliere expressie `r"eyJ|token|key"`
  geeft 0 treffers in info-niveau.
- Endpoint integratie: `httpx.AsyncClient` met mock-transport, vier
  foutcodes (503 app_auth_unconfigured, 503 app_not_installed, 503
  mint, 400 invalid_request) + 200.

**Ship-recipe-laag** (`backend/tests/test_ship_recipe_drift.py`):

- Het conditionele helper-config-blok in `_build_ship_instructions` is
  byte-identiek aan de `.claude/skills/git-ship/SKILL.md`-mirror
  (drift-val: kaart `d9447e49`).
- Bestaande `test_direct_mode_*` tests blijven groen op ambient-mode —
  `COCKPIT_GITHUB_APP_ENABLED` default is `false`.

## 9. Aanvaardingscriteria

- `git push` vanuit een App-mode-worktree logt op GitHub de author als
  `<bot-login>`, niet als machine-user.
- Een ambient-mode-worktree (geen secrets of env-flag uit) gedraagt
  zich identiek aan voor de migratie — geen helper, geen URL-rewrite.
- De drie SecretStore-entries bestaan atomic-write + chmod 600;
  één ontbrekende entry valt schoon door op ambient.
- Een stale App-token (zojuist gemint, al verlopen) forceert re-mint
  binnen de refresh-margin, niet in de request-path.
- Loopback-only enforcement op het credential-endpoint: een verzoek
  van `0.0.0.0` of elders wordt 403 voor de service aanraken.

## 10. Bewust buiten scope

- **PR-creatie** — wij mergen direct. Een App-authored PR via
  `gh pr create` is een flow die geen direct-mode mergebot nodig heeft.
- **Frontend-UI voor App-setup** — via de bestaande secrets-REST,
  geen nieuw scherm. Analoog aan
  [`github-issue-webhook.md` §6](./github-issue-webhook.md).
- **Multi-worker cache-coördinatie** — process-local cache, één
  backend-worker. Schaalvergroting is een latere zorg.
- **Per-purpose token-splitsing** — upstream heeft `push` (contents
  write) vs `pull_request` (contents read + PRs write); wij hebben
  alleen push nodig.
- **Workspace-lease / observed-owner** — kind-kaart
  `a2268cd256944398bfec1da170b0de09`.
- **Closed-issue reconciliation via PR-state** — afhankelijk van
  PR-creatiepad.
- **Auto-installatie van de GitHub App** — handmatig door de operator,
  analoog aan upstream's PR2 rollout. Setup-recept in
  `docs/deploy/github-app-rollout.md` (deze kaart implementeert de code,
  niet de deployment).

## 11. Bronnen

- [Upstream PR #316 PR2-plan](https://github.com/adrirubio/claude-deck/blob/master/docs/superpowers/plans/2026-08-12-pr2-distinct-commit-and-pr-identity.md)
  (master @ 2026-08-14, de canonieke bron voor de helper- en service-patronen)
- [Upstream `git_credential_helper.py`](https://github.com/adrirubio/claude-deck/blob/master/backend/mcp_shim/git_credential_helper.py)
  (75 regels, verbatim patroon overgenomen)
- [Upstream `github_app_auth_service.py`](https://github.com/adrirubio/claude-deck/blob/master/backend/app/services/github_app_auth_service.py)
  (gehalveerd: geen per-purpose, geen revoke-loop)
- [`docs/cockpit/github-issue-webhook.md`](./github-issue-webhook.md)
  (SecretStore-pattern + HMAC-secret-aanpak)
- [`docs/cockpit/cockpit-richting-decision.md`](./cockpit-richting-decision.md) §6
  (krimp-regime — directe rechtvaardiging voor de "niet overnemen"-lijst)
- [`backend/app/kanban/ship_prompt.py:1020`](../../backend/app/kanban/ship_prompt.py)
  (de ship-instructions-mirror die deze kaart uitbreidt)
- [`.claude/skills/git-ship/SKILL.md`](../../.claude/skills/git-ship/SKILL.md)
  (de andere mirror — beide in lockstep bij te werken)
- [GitHub Apps: installations + permissions](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation)
  (mint-flow specificatie)
