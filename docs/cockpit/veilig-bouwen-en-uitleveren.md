---
title: "Veilig bouwen & uitleveren van willekeurige apps — isolatie, secrets, CI en run/deploy"
type: reference
status: active
---

# Veilig bouwen & uitleveren van willekeurige apps — isolatie, secrets, CI en run/deploy

> Kanban-kaart: **`[analyse] Veilig bouwen & uitleveren van willekeurige apps: isolatie, secrets, CI en run/deploy`** (facet D van de parent-kaart *"Deze applicatie als platform om andere applicaties te bouwen"*, `8db831a0df6d42689c5b26325b6cbecc`).
>
> Deze doc is een **analyse** — geen implementatie. De actionabele gaten worden in §6 als concrete **Backlog-follow-ups** gefileerd door de uitvoerende sessie van deze kaart (niet door dit document zelf).
>
> **Lees dit ook:** `docs/cockpit/orchestration-substrate-decision.md` (beslissing tmux + scraping-residu vs. headless-SDK, scope van wat D hier niet opnieuw bediscussieert), `docs/cockpit/sandcastle-integration-plan.md` (architectuur van de containertransport), `docs/cockpit/kanban-followups.md` §I4b (write-anywhere-oppervlak — déze facet erft het eigenaarschap). MECE-zusjes: `product-inceptie-pipeline.md` (A), `repo-provisioning-bootstrap.md` (B), `portfolio-orchestratie.md` (C, indien af).

## 1. De vraag in één paragraaf

Cockpit moet agentisch willekeurige applicaties kunnen **bouwen**, **draaien** (lokaal preview) en **uitleveren** (deploy). Dat veronderstelt vier zaken die vandaag niet of nauwelijks bestaan:

1. **Sterke isolatie** tussen wat een agent in een project-repo doet en de host (en andere projecten), zodat een misstap in een splinternieuwe app niet het hele platform meeneemt.
2. **Per-project secrets/environment**, zonder dat die in de eerste de beste `.env` van een product-project belanden of in Cockpits eigen SQLite lekken.
3. **CI-bootstrap** op nieuwe repos — zonder dat elke agent handmatig `.github/workflows/quality.yml` moet uitvinden, en zonder dat de kwaliteitsbarrière per project verschilt.
4. **Een draai-/preview-/deploypad** voor het product dat de agent gebouwd heeft — anders blijft het een repo die "build successfully" zegt maar nooit iets aan een gebruiker laat zien.

Het korte antwoord (verder onderbouwd): **alle vier ontbreken vandaag in essentie** — er is wel een **transportsubstraat** (tmux + Sandcastle + de geplande host-extensie, zie §3) waarin ze ingebouwd kúnnen worden, maar de services eromheen (auth-model, secrets-store, CI-template, run/preview-pad) moeten nog ontworpen en gebouwd worden. Het is een reeks **grote ontwerp-beslissingen met diepe consequenties** voor de blast-radius van het platform, niet een kwestie van "even een service toevoegen". Daarom staat dit facet bewust vóór de eerste auto-build van een product-project — *wachten tot er een slachtoffer is* zou een dure manier zijn om te leren waar de grenzen liggen.

## 2. Wat kan vandaag al — en wat níet

### 2.1 Isolatie — wat er is, en wat ontbreekt

**Wat er wél is:**

- **Worktree-isolatie per claim.** `dispatch.make_worktree_transport` (`backend/app/kanban/dispatch.py:1091`) maakt voor élke sessie een verse git-worktree `.claude/worktrees/<session_name>` van `origin/master` af. Dat is *branch*-isolatie (geen cross-branch mutaties) en *bestands*-isolatie binnen één repo, niet host-isolatie. De agent werkt op zijn eigen werkfiles, en `worktree remove --force` ruimt op als de sessie sterft.
- **Sandcastle container-isolatie.** `SandcastleConfig` (`backend/app/models/sandcastle.py`) kent `sandbox_provider ∈ {docker, podman, vercel, no-sandbox}`; `check_health()` peilt `docker_available`/`podman_available`; `pick_default_sandbox_provider` kiest een echte runtime als die bestaat. De runner wordt aangesproken via Node-subprocess (`scripts/sandcastle_runner.mjs`), die op zijn beurt `docker run`/`podman run` aanroept. Dit is **de eerste echte host-isolatie** in de stack — maar hij is per-project opt-in en heeft "no-sandbox" als ORM-default (`SandcastleConfig.sandbox_provider` default `"no-sandbox"`).
- **SpawnTransport-protocol.** Het pluggable `SpawnTransport` maakt een derde transport (podman/remote, headless-SDK) mogelijk zonder bestaande code te raken — zie `orchestration-substrate-decision.md` §5 voor het incrementele pad.
- **MemoryLimitExceeded.** Sessies weigeren te spawnen boven een geheugen-drempel (`dispatch.make_worktree_transport` checkt `session_registry.can_add_session()` voor de spawn). Dit is een *resource*-guard, geen isolatie in security-zin.

**Wat er níet is:**

- **Geen process- of user-namespace-isolatie voor de worktree/hot-path.** Een agent-sessie in `worktree_transport` draait als dezelfde OS-gebruiker, in dezelfde shell-omgeving, met dezelfde home-dir, dezelfde `~/.claude/.credentials.json`, dezelfde `~/.aws/credentials`, dezelfde SSH-socket. Een agent in een splinternieuw product-project kan dus `~/.aws/credentials` lezen als die toevallig bestaat, of een ander product-project aanspreken via het bestandssysteem. Werkboom-isolatie is geen security-isolatie.
- **Geen network-namespace-isolatie.** Geen `docker run --network=none` voor pure lees-agents; geen egress-allowlist per project. Een agent met `--dangerously-skip-permissions` (de default; zie §2.2) kan ongestraft outbound bellen naar alles wat de host kan.
- **Geen resource-cap per project.** `MemoryLimitExceeded` is globaal; één project kan de hele machine opeten voor de andere de kans krijgt. Geen per-project CPU-quota, geen disk-quota, geen process-count-cap.
- **Geen "blast-radius-budget".** Geen max aantal gelijktijdige projecten dat de host aankan; geen early-stop als één project disproportioneel resources slurpt.

**Conclusie isolatie:** de **granulariteit** (per-project, per-claim) is goed, de **sterkte** (gewoon OS-processen op de host) is zwak. Sandcastle's containerpad is de upgrade-as, maar staat vandaag op "opt-in per project" met "no-sandbox" als default — een agent-build op een splinternieuw project draait dus *niet* in een container tenzij iemand dat expliciet heeft aangezet.

### 2.2 Permission-model — de default is "alles mag"

- **`skip_permissions` default = `True`** (`backend/app/kanban/dispatch.py:198`: `return True  # default: bypass permissions`). Dit is een bewuste, gedocumenteerde keuze voor de meta-project-flow (Cockpit dat zichzelf bouwt op zijn eigen repo), maar het is **geen eigenschap van de kaart of het project** — het is een default die *elk* nieuw project erft zodra `RepoBootstrapService` (facet B) autodispatch aanzet. Voor een product-project betekent dit: agent kan eigen `~/.bashrc` overschrijven, kan in een willekeurige directory buiten het project schrijven, kan een shell-spinning-detour inzetten om alle andere sessies op de host te slim af te zijn.
- **Een REST/MCP-toggle bestaat** (`POST /api/v1/kanban/skip_permissions` met `SkipPermissionsRequest{project_key, enabled}`; `dispatch.set_skip_permissions`) — maar er is geen UI-flow om 'm aan te zetten, geen audit-log wie 'm wanneer flipte, geen waarschuwing bij default-aan.
- **`KanbanMeta` bewaart alles als plaintext** in `kanban.db` — geen encrypted-at-rest, geen integriteit-check. Voor vandaag acceptable (geen secrets hierin, alleen booleans), maar wel een dimensionering van "hoe naïef de storage is".

### 2.3 API-oppervlak — wie mag wat?

**Auth — alleen een token-toggle, geen identity-laag.**

`RequireApiTokenMiddleware` (`backend/app/main.py:129-158`) doet **alleen** een bearer-token-check wanneer `settings.api_token` is gezet — en die staat op `None` by default (`config.py:69`). Resultaat op een default-install:

| Pad | Default | Met `API_TOKEN` gezet |
|---|---|---|
| `GET /health` | open | open (whitelist) |
| `/api/v1/health` | open | open (whitelist) |
| `/api/v1/*` (alles behalve whitelist) | **open** | bearer-token |
| `/kanban-mcp` (MCP SSE) | **open** | bearer-token |
| CORS `allow_origins` | `["http://localhost:5173"]` | idem |
| `host` (uvicorn-bind) | `"127.0.0.1"` | idem |

**Concrete gevolgen:**

- Een onauth API op `127.0.0.1:8000` betekent: elk proces op de host kan alles — schrijf-`.mcp.json` (zie I4b), dispatch-spawn, projecten toevoegen/verwijderen, agents in een willekeurig project commando's geven. Dat is op een single-user devbox oké; op een shared host of na een reverse-proxy die naar `0.0.0.0` exposed is het een vrijbrief.
- `scripts/build.sh` zegt letterlijk `--host 0.0.0.0` in zijn docs-string, en `cockpit.sh start` heeft een `--host` flag die `0.0.0.0` accepteert. Er is **geen waarschuwing** als je dit doet zonder `API_TOKEN` te zetten.
- De MCP-mount op `/kanban-mcp` is ook onauth by default, en stelt een agent (als die `~/.claude.json`/`<repo>/.mcp.json` trust) in staat om kanban-acties uit te voeren namens het device — een supply-chain-aanvalsvector als een malafide `.mcp.json` in een checkout de gebruiker overhaalt.

**I4b — write-anywhere voor `.mcp.json` (deprecating maar nog niet opgelost).** `MCPConfigService._write_project_mcp_config` (`backend/app/services/mcp_config_service.py:232-241`) schrijft naar `get_project_mcp_config_file(project_path)` — waar `project_path` *van de client afkomt*. Er is geen validatie dat dit pad onder een van de geregistreerde `projects` ligt. Een POST naar `/api/v1/mcp/servers/{...}/enable` met `project_path=/etc/cockpit-target` schrijft daar een `.mcp.json` met de opgegeven `mcpServers`-dict. Atomic-write-corruptie is gefixt; het *auth* + *pad-allowlist*-gat niet. Zie `docs/cockpit/kanban-followups.md` §I4b — eigenaarschap ligt sinds 2026-06-14 bij deze facet.

### 2.4 Sandcastle ↔ Dispatch ↔ (toekomst) host/remote — wie doet wat

Drie spawn-kanalen bestaan vandaag of zijn in ontwerp:

| Transport | Wat het is | Wat het wel/niet isoleert | Status |
|---|---|---|---|
| **`worktree`** (default) | `tmux new-session -c .claude/worktrees/<name> claude --dangerously-skip-permissions …` | Branch + files binnen één repo. Geen host-isolatie. | productie, default |
| **`sandcastle`** | Node-subprocess → `@ai-hero/sandcastle` → docker/podman container met agent in werkboom | Container-isolatie (filesystem, pid, network-opties afhankelijk van sandcastle-config). Geen memory-cap of egress-policy per default. | productie, per-project opt-in |
| **`host` (remote)** | SSH naar geregistreerde remote host, dispatch-spawn daar | Isolatie verplaatst zich naar de remote — beter voor blast-radius, maar vereist SSH-credentials (eigenaar: facet D). | UI klaar (`api/v1/hosts/router.py`), dispatch-integratie nog niet |
| **headless-SDK** (gepland) | `claude -p --output-format stream-json`, geen TUI | Geen TUI, dus geen scraping-residu. Geen container tenzij expliciet. | ontwerp (zie `orchestration-substrate-decision.md` §5) |

**Conclusie transportsubstraat:** het abstractielaagje klopt — `SpawnTransport` is precies de goede plaats om later een `headless` of een `host` transport bij te pluggen zonder de dispatch-logica te raken. Wat ontbreekt is een **beslissing welk transport standaard is voor welk project-type**, en een **CI/observability-laag die het effectief maakt** (zie §2.5).

### 2.5 Per-project secrets & environment

**Inventarisatie van wat er is:**

- `CredentialsService` (`backend/app/services/credentials_service.py`) beheert **alleen** MCP-OAuth-tokens in `~/.claude/.credentials.json` (mode 0o600). Geen per-project scoping, geen encryptie, geen audit. Bruikbaar als *reference-implementatie* van "schrijf met mode 600" maar niet als project-secrets-store.
- `OAuthService` (`backend/app/services/oauth_service.py`, 10k) doet waarschijnlijk OAuth-flows voor providers — niet geverifieerd voor deze doc, maar duidelijk ook globaal, niet per-project.
- `MCPConfigService.SENSITIVE_PATTERNS` + `ConfigService.sensitive_keys` *maskeren* sleutels in API-responses (read-side scrubbing) — geen opslag-encryptie, geen write-side protectie.
- `Settings.minimax_api_key` (config.py:86) is **proces-env-only** (commentaar: "Cockpit never stores this in the database") en wordt door `spawn_session` server-side in de tmux-env geïnjecteerd. Dit is het **enige** voorbeeld in de stack van "secret hoort bij een project/runtime, leeft niet in de DB" — en het zit er per (provider, MiniMax), niet per project.
- `agentic_cli/provider_env.py` leest AWS-region/AWS-profile uit env-vars (geen secrets) en aanvaardt MiniMax-key als caller-resolved input. De commentaar zegt letterlijk: *"the caller (e.g. a secrets store) is responsible"* — er is dus **geen secrets-store vandaag**, alleen de afspraak dat de caller er een heeft.

**Wat ontbreekt voor "willekeurige apps":**

1. **Geen per-project env-isolatie.** Een agent-spawn in project A erft automatisch alle env-vars van het backend-proces — inclusief alles wat de gebruiker ooit in zijn shell had staan (`AWS_*`, `GITHUB_TOKEN`, `OPENAI_API_KEY`, …). Voor een product-project dat bv. een Stripe-call moet doen is er geen mechanisme om "alleen `STRIPE_KEY` voor project A" te injecteren zonder de hele process-env te exposen.
2. **Geen secrets-store.** Geen vault-integratie (HashiCorp Vault, AWS Secrets Manager, age-encrypted file, 1Password CLI, dotenv-vault, …). Geen project-scoped `.env.local` met mode 600.
3. **Geen audit-trail** van welke sessie welke env-var las. Geen "toon mij wat project A tussen 14:00 en 15:00 aan secrets trok".
4. **Geen `.env`-template-conventie.** Geen `.env.example` in `cockpit-baseline` blueprint (facet B) die uitlegt welke vars veilig in `.env` mogen, welke in de secrets-store moeten.

### 2.6 CI-bootstrap — de kwaliteitsbarrière per project

**Inventarisatie:**

- `quality.yml` (`.github/workflows/quality.yml`) is een complete CI-pipeline voor *deze* repo: ruff + mypy + bandit + pytest + openapi-snapshot + frontend lint/test/build + Playwright e2e. Hij draait op push+PR naar `master`.
- Geen CI-template/templating: `quality.yml` is hand-geschreven voor deze repo. Er is geen "kopieer deze file bij geboorte"-flow.
- Voor een splinternieuw product-project betekent dit: zonder handwerk is er *geen* CI, en daarmee geen PR-gate. Een agent kan direct naar `master` mergen (in `direct` ship-mode, default voor meta; zie `dispatch.SHIP_MODES`).
- `release.yml` is een manual-release-workflow (build frontend, GitHub release) — niet relevant voor product-projecten, maar illustreert dat er geen "kies-welke-CI-stappen"-abstractie bestaat.

**Gap:** er is nul infrastructuur om in een nieuwe repo een CI-bootstrap uit te rollen. Facet B's `BootstrapPolicy` noemt het als open design-beslissing (`repo-provisioning-bootstrap.md` §4.3, punt 4); D erft de implementatie: een `CITemplateService` die een set standaard-workflows kan renderen naar `.github/workflows/` van een pasgeboren project.

### 2.7 Run/preview/deploy van de gebouwde app

**Inventarisatie:**

- `scripts/cockpit.sh start` start *Cockpit zelf* (backend + frontend) — niet een willekeurige product-app.
- Sandcastle biedt `run()` (agent in container, branch-management, completionSignal) — een run *van de agent*, niet een run *van het gebouwde product*. Er is geen "spawn de FastAPI-server die de agent zojuist schreef en laat 'm op een URL draaien".
- Geen `deploy`-service. Geen koppeling met Vercel/Netlify/Cloudflare/Render/Fly/Railway. Geen Docker-image-build + push naar GHCR/DockerHub. Geen statische-site-publish.
- Geen "preview URL per kaart"-notion. Een PR met een werkende demo bestaat niet — de frontend-`isolated-component-preview` recipe uit `docs/cockpit/isolated-component-preview.md` is een *dev*-workaround voor het verifiëren van een UI-wijziging, niet een preview van een product.
- Geen health-check-loop voor "de app die ik bouwde draait nog". Geen restart-on-crash. Geen log-aggregaat.

**Gap:** een product-project kan vandaag eindigen met "tests groen, gebouwd, klaar voor merge" maar zonder dat er ooit een URL was waar een mens de app kon zien werken. Dat isoleert bouwen van valideren op een manier die voor agentisch bouwen funest is — een agent kan een UI bouwen die compileert maar bij eerste klikken blanco blijft, en dat merkt niemand tot een mens 'm probeert.

## 3. Sandcastle + de transport-seam — positionering

Sandcastle is vandaag **de** host-isolatieprimitive in de stack, en de architectuur (`docs/cockpit/sandcastle-integration-plan.md`) positioneert 'm expliciet als "headless/automated agent runs" — complementair aan CC Bridge (interactief) en Agent Bridge (multi-provider discovery).

**Wat Sandcastle vandaag dekt:**

- Container-isolatie via docker/podman/vercel als sandbox-provider.
- Per-project `SandcastleConfig` met `sandbox_provider`, `agent_provider`, `model`, `branch_strategy`, `docker_image`, `permission_mode`, timeouts.
- Auto-sync tussen `KanbanMeta:transport:<key>` en `SandcastleConfig.enabled` (`dispatch._sync_sandcastle_enabled`).
- Health-endpoint (`GET /sandcastle/health`) dat runtime-beschikbaarheid peilt.
- Parallel-runs met batch-aggregatie, log-streaming, cancel-via-SIGTERM-procesgroep.

**Wat Sandcastle níet dekt (relevant voor facet D):**

- **Geen container image default.** `DEFAULT_DOCKER_IMAGE = "sandcastle:local"` (`sandcastle_service.py:24`) — er wordt aangenomen dat de host al een image gebouwd heeft (`scripts/sandcastle_runner.mjs` documentatie). Een verse host kan dus "Sandcastle aanzetten" en falen bij de eerste run omdat de image ontbreekt. Dit is een eerste-boot-frictie die in een app-factory-flow om een bootstrap-stap vraagt.
- **Geen network-policy.** Geen `--network=none` voor low-trust runs, geen egress-allowlist, geen DNS-allowlist. Een agent in een Sandcastle-container kan vandaag dezelfde outbound calls doen als zonder container.
- **Geen resource-cap per run anders dan wall-clock.** Geen `--memory`, geen `--cpus`, geen `--pids-limit`. De globale `MemoryLimitExceeded` van de dispatcher is een host-drempel, geen container-quota.
- **Geen rootless-default.** Standaard draait een container als root binnenin (`docker run` default-gedrag). Een agent die uitbreekt naar de host heeft root op de container; of dat door escape ook root op de host wordt hangt af van de docker-daemon-config, die deze repo niet afdwingt.
- **Geen read-only-rootfs.** De container-FS is beschrijfbaar; een agent kan binaries downloaden en eigen tooling installeren — niet per se ongewenst, maar geen default-deny.

**De transport-seam (tmux → podman/remote → headless-SDK):**

`SpawnTransport` is een abstractielaag met drie smaken vandaag (`worktree`, `sandcastle`) en twee gepland (`headless` voor SDK-runs zonder TUI; `host` voor remote SSH-uitvoering). Voor facet D is de relevante vraag **niet "moeten we één transport kiezen" maar "welke project/policy-klasses krijgen welke transport"**:

| Project-type | Aanbevolen transport | Reden |
|---|---|---|
| **Cockpit zelf (de meta-repo)** | `worktree`, `skip_permissions=true` | Trusted human-in-the-loop; laat agent de hele stack aanraken; directe feedback via Bridge. |
| **Eerste product-project, splinternieuw** | `sandcastle` (docker), `skip_permissions=false` | Container-isolatie als default; conservatieve permission-mode; netwerk-policy aan. |
| **Volwassen product-project met eigen CI + tests** | `worktree` of `headless`, `skip_permissions=policy` | Als CI + tests de kwaliteitsbarrière zijn, is de permission-mode minder load-bearing; host-isolatie wordt door de test-runner + container-CI geleverd. |
| **Untrusted / externe code** | `sandcastle` + `network=none` | Sterkste isolatie: geen netwerk, read-only-rootfs, geen host-mount. |

Hier zit een **echte ontwerpbeslissing** die B en D samen moeten maken: `BootstrapPolicy` (facet B) zou een `default_transport` moeten zetten op basis van een `risk_class` per project (low/medium/high). Het security-model hangt daar direct aan: een "high"-project dat zijn worktree-transport gebruikt zonder container is een aanvalsvector.

**Headless-SDK (gepland, zie `orchestration-substrate-decision.md`):** lost het scraping-residu op en levert structured events, maar voegt *geen* host-isolatie toe — een headless-SDK-sessie zonder container is even onveilig als een tmux-sessie zonder container. De twee assen (transport = isolatie; substraat = observability) zijn orthogonaal en moeten apart gekozen worden.

## 4. De gewenste veiligheidslagen (end-to-end)

### 4.1 Defended-in-depth, in volgorde van waar de bom valt

```
[A] API-ingang              ← auth (token / identity), rate-limit, pad-allowlist
[B] Project-grens           ← per-project transport-policy, secrets-scope, resource-quota
[C] Sessie-grens            ← container/image, network-policy, permission-mode, resource-cap
[D] Werk-werkboom-grens     ← git-worktree (bestaat), idempotency, branch-hygiene
[E] Output                  ← audit-log, secrets-scrubbing, e2e-verifiable delivery (CI + preview)
```

Elke laag vangt de fouten van de vorige. Vandaag ontbreken [A] (gedeeltelijk — token bestaat, default staat uit), [B] (policy bestaat niet), [C] (Sandcastle-image + network-policy ontbreken), [E] (audit/deploy ontbreken).

### 4.2 API-ingang [A]

- **Nu:** optionele bearer-token, default uit, `host=127.0.0.1`, CORS naar `localhost:5173`.
- **Doel:** één deploy-profiel ("localhost dev", "shared-host", "reverse-proxy public") met een verplichte waarschuwing + automatische hardening voor de niet-default-profielen. Voor "reverse-proxy public" moet token + CORS-strictness + pad-allowlist alle drie aan staan, niet configureerbaar los van elkaar.
- **Eerste stap:** I4b pad-allowlist (zie kanban-followups.md §I4b) — `MCPConfigService._write_project_mcp_config` moet weigeren als `project_path` niet in de projects-tabel staat. Dit is een eenvoudige, hoge-impact fix en de facto de eerste security-PR voor D.

### 4.3 Project-grens [B]

- **Nu:** geen expliciete policy per project; defaults gedragen zich als "alles aan, alles gedeeld".
- **Doel:** een `ProjectSecurityPolicy`-dataclass met:
  - `risk_class ∈ {meta, product-staging, product-prod, untrusted}`
  - `default_transport` afgeleid van `risk_class` (zie §3 tabel)
  - `default_skip_permissions` afgeleid van `risk_class` (meta=true, product-staging=false, product-prod=false, untrusted=false)
  - `secrets_scope_id` (lege string = globaal, anders naam van een secrets-store-entry)
  - `resource_quota` (max_memory_mb, max_concurrent_sessions, max_disk_mb)
  - `network_policy` (allow|deny|allowlist) — doorgegeven aan de transport
- **Storage:** `KanbanMeta` (zelfde patroon als `autodispatch:`/`skip_permissions:`/`transport:`) of een nieuwe `ProjectSecurityProfile`-tabel. De eerste is consistenter met de huidige device-local-aanpak; de tweer is nodig zodra policies portfolio-breed (facet C) gesynchroniseerd moeten worden.
- **Eerste stap:** policy-dataclass + risico-classificatie + een veilige default voor "nieuw product-project" (low-trust, container, conservative permissions, geen global-env).

### 4.4 Sessie-grens [C]

- **Nu:** worktree = OS-proces zonder extra grenzen; sandcastle = container zonder network/pids/memory-cap en zonder read-only-rootfs.
- **Doel (per transport):**

  | Transport | Default hardening |
  |---|---|
  | `worktree` (meta, trusted) | Geen container, maar expliciet *geen* secrets-injection, audit-log van alle `subprocess`-aanroepen door de agent. |
  | `sandcastle` (product-staging, prod) | `docker run --memory=... --cpus=... --pids-limit=... --read-only-rootfs --tmpfs=/tmp --network=restricted` met egress-allowlist uit `ProjectSecurityPolicy.network_policy`. Geen host-mount behalve expliciet toegestane. Image met minimale toolchain (geen curl/wget tenzij policy zegt van wel). |
  | `sandcastle + network=none` (untrusted) | Idem, plus `--network=none`, geen env-injection behalve een `SAFE_*`-set. |
  | `headless-SDK` | Zelfde policy-toepassing als het onderliggende container-profiel (headless zonder container = worktree-policy). |

- **Eerste stap:** Sandcastle `SandcastleConfig` uitbreiden met `memory_limit_mb`, `cpu_quota`, `pids_limit`, `read_only_rootfs`, `network_mode`, `egress_allowlist`. Plus een bootstrap-stap die een `sandcastle:local`-image kan *bouwen* als die ontbreekt (zie §4.6).

### 4.5 Per-project secrets & env [B+E]

**Ontwerp-doel:** een agent in project A ziet alléén de env-vars die expliciet voor project A bestemd zijn. Niets van de host-omgeving lekt door.

**Voorgestelde architectuur (drie lagen):**

1. **Secret-store** — een vault met project-scoped entries. Concrete keuzes (gescoord):

   | Optie | Plus | Min |
   |---|---|---|
   | **age-encrypted file in `~/.claude-registry/secrets/<project_key>.age`** met passphrase uit keyring | Geen externe dienst, eenvoudig te backup'en, mode 600 file | Sleutel-rotatie is handwerk, geen RBAC |
   | **`pass` (GPG)** met `~/.password-store/cockpit/<project>/<name>` | Standaard Unix-tool, GPG-key-rotatie mogelijk | Vereist GPG-setup, niet overal geïnstalleerd |
   | **`sops` + age of KMS** | Native YAML/JSON-versleuteling, audit-vriendelijk | Extra dependency, leercurve |
   | **Externe vault (Vault, AWS SM, Doppler, 1Password CLI)** | RBAC, audit, rotatie, scoping | Vereist credentials + netwerk-toegang = extra attack-surface |

   **Voorkeur voor MVP:** age-encrypted file. Geen externe dienst, geen netwerk-afhankelijkheid, bewezen primitive. Migratie naar sops of externe vault is triviaal omdat de abstractie (`SecretStore.get(project_key, name) -> str | None`) portable is.

2. **Project-scoped env-injection** — bij spawn krijgt de agent-env *alleen*:
   - de expliciet geconfigureerde vars uit `ProjectSecurityPolicy.secrets_scope_id` (bijvoorbeeld `STRIPE_KEY=…`)
   - een `COCKPIT_PROJECT_KEY=<key>` zodat de agent weet in welke project-context 'ie draait
   - een `COCKPIT_RUNTIME=<worktree|sandcastle|headless|host>` zodat tooling kan beslissen
   - **NIET**: de hele `os.environ` van het backend-proces.

   `spawn_session` in `services/runs/spawn.py` moet vandaag filteren; vandaag erft het de parent-env grotendeels. Dit is een kritieke, kleine codewijziging met grote security-impact — zie `agentic_cli/provider_env.py` voor het patroon (expliciet `env["ANTHROPIC_AUTH_TOKEN"] = cleaned_key`, niet `env.update(os.environ)`).

   **Geïmplementeerd contract** (kanban-kaart `b5c71e0c28c4481aa47569b3fc5b9489`,
   follow-up #5 hieronder). Beide `spawn_session`-implementaties — de
   agent-bridge in `services/runs/spawn.py` en de legacy Claude-Code bridge
   in `services/runs/cc_spawn.py` — aanvaarden vandaag drie nieuwe
   keyword-only kwargs:

   | Kwarg | Type | Doel |
   |---|---|---|
   | `project_key` | `str \| None` | Wanneer gezet: geïnjecteerd als `COCKPIT_PROJECT_KEY`. |
   | `runtime` | `str \| None` | Geïnjecteerd als `COCKPIT_RUNTIME`; default `"worktree"` voor backward compat. |
   | `extra_env` | `dict[str, str] \| None` | Caller-resolved secrets. Hier landt straks `SecretStore.get(project_key, name)` zodra follow-up #4 shipet. |

   De gespawnde tmux-sessie krijgt **alleen** deze drie inputs (plus de
   provider-env die `build_provider_env` al expliciet bouwt). De backend's
   `os.environ` wordt nooit meer gemerged in de tmux-argv: een host-var als
   `STRIPE_KEY` uit de shell van de operator komt niet meer door bij de
   agent, ook niet als die per ongeluk in de parent-env staat. Een
   monkeypatch-test (`test_spawn_session_does_not_inherit_os_environ`) zet
   een uniek-genaamde host-var en assertt dat 'ie niet in de tmux-argv
   verschijnt — dit is de canary voor de security-fix.

   Backward compat: bestaande call-sites (dispatcher, REST bridge, ~12
   bestaande pytest-tests) blijven werken — `runtime`/`project_key`/`extra_env`
   zijn allemaal optioneel met None-defaults. Een bestaande spawn zonder deze
   kwargs krijgt alleen `COCKPIT_RUNTIME=worktree` extra; alle andere gedrag
   blijft identiek.

3. **Audit-log** — bij elke env-injectie een regel in `kanban.db` (of een aparte `security_audit`-tabel): `project_key`, `secret_name`, `session_name`, `injected_at`. Geen secret-waarde in de log. Bruikbaar voor "wie gebruikte wat wanneer" en voor rotatie-triggers.

   **Geïmplementeerd contract** (zelfde kaart). Beide `spawn_session`-
   implementaties roepen vandaag een `_record_audit(project_key, runtime,
   session_name, env_var_names)` hook aan. De hook schrijft **alleen
   var-namen, geen waarden** — een `STRIPE_KEY_A` komt in de log, de
   waarde `sk_live_a` niet. Vandaag is de implementatie een `logger.info`
   met de namen gesorteerd in de message; zodra follow-up #10 shipet
   (`security_audit`-tabel + endpoint) wordt dit één rij per spawn met
   `kind=env_inject`, dezelfde var-namen-lijst. Het hook-contract is
   bewust klein gehouden zodat de swap geen API-breuk is.

**Eerste stap:** een `SecretStore`-interface (`get/put/list/delete`) met een age-file-implementatie. Daarna de filter in `spawn_session`. Daarna de audit-log.

### 4.6 CI-bootstrap [E, deels C]

**Doel:** een splinternieuw product-project heeft vanaf geboorte een CI-gate die vergelijkbaar is met die van `claude-cockpit` zelf (lint + test + build op PR), zonder dat een mens 'm moet schrijven.

**Voorgestelde aanpak:**

- Een `CITemplateService` met een set standaard-workflows:
  - `quality.yml` — test/lint/build, parametrisch over taal-detectie (python vs node vs go).
  - `security.yml` — bandit/semgrep/npm-audit/dependabot, asynchroon (waarschuwt, blokkeert niet).
  - `release.yml` — opt-in, voor projecten die naar GHCR/DockerHub willen pushen.
- Templates leven in `docs/cockpit/ci-templates/` (Markdown-bestanden met `{{var}}`-placeholders) of, liever, in `backend/app/services/ci_templates/*.yml.j2` (Jinja) zodat ze server-side gerenderd kunnen worden tijdens `RepoBootstrapService.bootstrap_from_plan` (facet B).
- De gebruiker kan een `CITemplateService.apply(project_path, profile="python-strict"|"node-strict"|"minimal")` aanroepen; idempotent (best overschrijft niet zonder `--force`).
- Geen koppeling met externe CI-runners vandaag — alleen GitHub Actions. Dat is een eerlijke scope voor MVP; GitLab CI / CircleCI zijn latere uitbreidingen als dat portfolio-behoeften rechtvaardigt.

**Eerste stap:** drie templates schrijven (python-strict, node-strict, minimal), een `CITemplateService` met render+idempotency, en een knop in `RepoBootstrapService` om ze te apply'en tijdens geboorte (of als losse handmatige actie ná geboorte).

### 4.7 Run/preview/deploy [E]

**Doel:** een agent kan een app bouwen *en* valideren dat 'ie draait. Zonder dit is "de app is af" een leugen.

**Voorgestelde aanpak, in volgorde van leverage:**

1. **`RunService` — spawn de gebouwde app in een sandbox.** Neemt een `project_path` + commando (bijv. `uvicorn app.main:app --port 8001`), draait 'm in een Sandcastle-container (reuse het bestaande sandbox-mechanisme, nieuwe use-case), exposeert de poort op `127.0.0.1:<random>` via docker-network, en streamt logs naar een file. Geen deploy — alleen lokaal draaien om te checken.
2. **Preview-URL per kaart.** Een nieuwe kolom `/preview?card_id=…` die een iframe toont van de lopende `RunService`-instantie voor die kaart. Of, liever: een "Run" actie op een Done-kaart die een preview start en de URL als activity-comment post.
3. **Health-check-loop** binnen `RunService`: als de app binnen N seconden niet `/health` (of een configureerbaar pad) retourneert, markeer de run als `failed` en post een activity-comment. Voorkomt dat een vastgelopen app een open URL heeft.
4. **Deploy** is de grote stap en hoort **niet** bij MVP:
   - Vereist credentials per provider (Vercel-token, Fly-token, AWS, GCP, …) — dat is een volledige secrets-store-integratie.
   - Vereist domein-keuzes en DNS-config — niet iets wat een agent autonoom kan doen zonder beleidskader.
   - Vereist kosten-governance — als de agent "deploy naar AWS" doet op een splinternieuw project kan dat onverwachte kosten genereren.
   - **Eerste zinvolle sub-stap:** een `DeployTarget`-abstractie met één MVP-implementatie: "container-image build + push naar GHCR" (volgt op CI-bootstrap). Dat levert iets dat in een vervolgkaart als deploy-target kan dienen zonder de hele cloud-provider-integratie.

**Eerste stap:** `RunService` met sandbox-reuse + health-check-loop + een UI-actie "Run this branch".

### 4.8 Observability voor security-events [E]

**Doel:** iemand (mens of agent) kan terugzoeken "wat is er gebeurd" na een incident.

**Vandaag:** activity-feed van kanban-kaarten + Presence-events van agent-sessies. Geen security-specifieke log.

**Voorstel:**

- Breid de activity-feed uit met `kind=security` entries voor: skip_permissions flip, transport-keuze, env-injectie (zonder de waarde), Sandcastle config-wijziging, secrets-store-mutaties.
- Een apart `security_audit`-endpoint (`GET /api/v1/security/audit?project_key=…&since=…`) dat de security-events als leesbare stream teruggeeft. Geen schrijf-API; alleen de activity-feed schrijft.
- Geen SIEM-integratie in MVP — alleen lokaal leesbaar.

**Implementatie (F1, geleverd):**

De `security_audit`-tabel leeft in `backend/claude_registry.db` (de app-DB, niet `kanban.db`) — security-data heeft eigen retentie en mag niet mengen met feature-activiteit. De tabel heeft de volgende shape:

| kolom | type | rol |
|---|---|---|
| `id` | int pk | autoincrement |
| `kind` | string(64), indexed | enum: `skip_permissions_flip`, `transport_change`, `autodispatch_change`, `secrets_put`, `secrets_delete`, `env_inject`, `sandcastle_config_change`, `run_start`, `run_stop`, `security_profile_change` |
| `project_key` | string(512), indexed | scope van de actie (foreign-key-achtig naar het project) |
| `actor` | string(256) | wie heeft de actie gedaan (`dispatch-api`, `run-service`, `secrets-api`, `sandcastle-api`, `security-profile-api`) |
| `payload_ref` | JSON | **enkel referenties** (naam, before/after, session-of-instance, env_var_names, …) — nooit een secret-waarde. Een sentinel-check weigert rijen waarvan de payload een bekende secret-prefix (`sk_`, `ghp_`, `xox`, `AKIA`, `AIza`, `ya29.`) bevat. |
| `at` | DateTime(timezone=True), indexed | UTC; samengestelde index `(project_key, at)` voor de project-tijdlijn-query |

**Endpoint** — `GET /api/v1/security/audit`:

```
?project_key=<key>&kind=<enum>&since=<iso>&until=<iso>&limit=<1..1000>
```

Antwoord: `{"entries": [...], "total": <pre-limit>, "limit": <effective>}`. Alleen lezen; geen POST/PUT/DELETE. Filters componeren met AND; limit klemt op `MAX_LIMIT=1000`.

**Invulpunten** — schrijven gebeurt uitsluitend via deze productie-paden, elk via `security_audit_service.record(...)` (best-effort: een gefaalde audit faalt nooit de onderliggende actie):

| Invulpunt | `kind` | Payload-sleutels | Broncode |
|---|---|---|---|
| `set_skip_permissions` (kanban) | `skip_permissions_flip` | `enabled` | `backend/app/kanban/dispatch.py` |
| `set_default_transport` (kanban) | `transport_change` | `before`, `after` | `backend/app/kanban/dispatch.py` |
| `set_autodispatch` (kanban) | `autodispatch_change` | `enabled` | `backend/app/kanban/dispatch.py` |
| `SecretStore.put` (REST) | `secrets_put` | `name` | `backend/app/api/v1/secrets.py` |
| `SecretStore.delete` (REST) | `secrets_delete` | `name` | `backend/app/api/v1/secrets.py` |
| `spawn_session` env-inject | `env_inject` | `session_or_instance`, `runtime`, `env_var_names` (nooit values) | `backend/app/services/runs/spawn.py` |
| `RunService.start` | `run_start` | idem | `backend/app/services/run_service.py` |
| `RunService.stop` | `run_stop` | idem | `backend/app/services/run_service.py` |
| `SandcastleService.update_config` / `toggle_config` | `sandcastle_config_change` | `changed: {field: {before, after}}` | `backend/app/services/sandcastle_service.py` |
| `SecurityProfileService.upsert` / `patch` (risk-class transitie) | `security_profile_change` | `field`, `before`, `after` | `backend/app/services/security_profile_service.py` |

**Out-of-scope (bewust):** SIEM-export, lange-termijn-retentie, externe audit-systemen. De endpoint blijft lokaal leesbaar; aggregatie buiten de cockpit is een latere facet.

## 5. Gefaseerde aanpak + trade-offs

### 5.1 Drie fasen, bewust klein gehouden

| Fase | Inhoud | Voorwaarde voor de volgende |
|---|---|---|
| **F1 — Lockdown** | I4b pad-allowlist; Sandcastle resource-caps (memory/cpu/pids); Sandcastle `network_mode` + read-only-rootfs opties; secrets-store-interface + age-file-implementatie; filter in `spawn_session` zodat env-injectie project-scoped is; audit-log voor security-events. | F1 maakt het *mogelijk* om een splinternieuw product-project te draaien zonder de hele host te riskeren. |
| **F2 — Run/Preview** | `RunService` met Sandcastle-reuse; preview-URL per kaart; health-check-loop; "Run this branch"-actie. `CITemplateService` met drie templates; knop in bootstrap om te apply'en. | F2 maakt het *zinvol* om een gebouwde app te valideren voor merge. |
| **F3 — Deploy** | `DeployTarget`-abstractie + MVP-implementatie (GHCR-image push); portfolio-policy-sync (facet C); remote-host-transport voor product-projecten; multi-vault-ondersteuning in `SecretStore`. | F3 maakt het *compleet* — een agent kan bouwen + valideren + uitleveren, maar alleen na expliciete portfolio-policy-go. |

### 5.2 Trade-offs (de keuzes die gemaakt moeten worden)

| Keuze | Optie A | Optie B | Aanbeveling |
|---|---|---|---|
| **Auth-default voor `0.0.0.0`-binding** | Warn-and-continue | Refuse-to-start | **Refuse-to-start** — een onauth API op een public-bind is een line-crossing. |
| **Sandcastle default voor nieuwe product-projecten** | `no-sandbox` (huidige default) | `docker` (auto-detect) | **`docker`** — als Docker beschikbaar is, gebruik 'm; anders val terug op `no-sandbox` met expliciete UI-warning. |
| **`skip_permissions` default voor product-projecten** | `True` (huidige default) | `False` (policy-gated) | **`False`** — een agent die niet "alleen-lezen-mag" op een splinternieuw project is een aanvalsvector; conservatief defaulten is goedkoper dan herstellen. |
| **Secret-store MVP** | age-encrypted file | externe vault | **age** — geen externe dienst, geen netwerk, portable. Migratie naar vault is later triviaal. |
| **CI-template MVP** | hand-geschreven copy uit claude-cockpit | parametrische Jinja-templates in `backend/app/services/ci_templates/` | **Jinja-templates** — anders is elk project een fork van deze repo's CI en dat drift. |
| **Deploy MVP** | "Run in sandbox only" | "Build + push to GHCR" | **Alleen Run** — Deploy zonder secrets-store is een ramp; F1 moet eerst. |
| **Headless-SDK in security-context** | headless als default voor untrusted | headless als extra mode naast container | **Headless + container** — headless zonder container is geen isolatie. |
| **Per-project audit-log scope** | globaal in kanban.db | apart `security_audit`-tabel | **Apart** — security-data heeft andere retentie/beveiliging nodig; niet mixen met feature-activiteit. |

### 5.3 Wat NIET in deze facet (expliciete out-of-scope)

- **Sandcastle-image-building-flow.** Wel een gat (zie §3); de oplossing is een eigen kaart, geen uitbreiding hier.
- **Multi-tenancy / echte multi-user.** Cockpit is single-user-by-design vandaag; dat veranderen is een portfolio-architectuurproject op zich. Hier: scope blijft "één gebruiker, één of meer projecten, één host".
- **Network-egress-proxy met policy-engine.** Een echte egress-allowlist (bijv. via Squid/Cilium/eBPF) is een aparte architectuur-investering. Sandcastle's `--network=restricted` + een `--add-host`/firewall-script is het MVP-niveau; een echte proxy komt later.
- **Compliance-frameworks (SOC2, ISO27001).** Deze facet maakt de *technische* basis om dat soort audits te doorstaan, maar audit-werk zelf is consultancy.
- **Headless-SDK-transport.** Orthogonaal, eigenaar: `orchestration-substrate-decision.md` §6.
- **Portfolio-policy-sync.** Eigenaar: facet C.
- **MCP-server hardening (naast I4b).** Voorbeeld: een MCP-server die door een product-project wordt geregistreerd kan zelf malicious zijn. Dit is een apart spoor (MCP-trust-model), geen D-verantwoordelijkheid.

## 6. Actionabele gaten → Backlog-follow-ups

Deze sectie lijst de gaten die het bouwwerk vormen. **Niet** door dit document geïmplementeerd — door de uitvoerende sessie van deze kaart worden ze als concrete Backlog-kaarten aangemaakt (met `work_type`, `metadata.facet="D"`, en korte acceptatiecriteria) zodra dit doc op `master` staat, zodat ze in de dispatch-pool terechtkomen voor menselijke triage.

1. **`[security][D] I4b pad-allowlist voor `.mcp.json`-write.** —
   `MCPConfigService._write_project_mcp_config` moet weigeren als `project_path` niet
   in de projects-tabel staat. Test: onbekend pad → 400/403; bekend pad → ok.
   Kleine PR (~30 regels + tests). Erft het eigenaarschap uit `kanban-followups.md`
   §I4b. **Eerstvolgende security-PR.**
2. **`[security][D] Sandcastle resource-caps + read-only-rootfs.** —
   `SandcastleConfig` uitbreiden met `memory_limit_mb`, `cpu_quota`,
   `pids_limit`, `read_only_rootfs`, `network_mode`, `egress_allowlist`. Door-
   geven aan `docker run`/`podman run` in `sandcastle_runner.mjs`. Test: een run
   met `memory_limit_mb=256` sterft bij OOM; run met `read_only_rootfs=true`
   kan `/etc/hostname` niet overschrijven.
3. **`[security][D] Sandcastle default-image-bootstrap.** — Een `--build-image`
   actie die de `sandcastle:local` image bouwt op de host als die ontbreekt, en
   een `check_health`-verbetering die "image missing" als expliciete status
   teruggeeft in plaats van generieke failure. Lost de eerste-boot-frictie van
   §3 op.
4. **`[security][D] `SecretStore`-interface + age-file-implementatie.** —
   Pure CRUD (`get/put/list/delete`) op `~/.claude-registry/secrets/<project_key>.age`,
   mode 600 op de file, passphrase uit user-keyring of env-var. Geen netwerk,
   geen externe dienst. Tests: roundtrip, ontbrekende file, verkeerde passphrase,
   concurrency op één project_key.
5. **`[security][D] Per-project env-injectie in `spawn_session`.** —
   Vandaag erft de tmux-env de hele `os.environ`. Vervang door expliciete set:
   alleen de secrets-store-entries voor die `project_key`, plus
   `COCKPIT_PROJECT_KEY`/`COCKPIT_RUNTIME`. Test: spawn in project A ziet
   `STRIPE_KEY_<B>` niet, wel `STRIPE_KEY_<A>` als geconfigureerd.
   **Deel geïmplementeerd** (kaart `b5c71e0c28c4481aa47569b3fc5b9489`):
   de filter + audit-hook in beide `spawn_session`-functies shippen;
   `SecretStore`-koppeling blijft gaten omdat #4 nog niet shipet — de
   `extra_env`-parameter vult vandaag het gat dat `SecretStore.get(...)`
   straks vult. Zie §4.5 hierboven voor het contract.
6. **`[security][D] `ProjectSecurityPolicy`-dataclass + storage.** —
   Eén dataclass met `risk_class`, `default_transport`, `default_skip_permissions`,
   `secrets_scope_id`, `resource_quota`, `network_policy`. Storage in een nieuwe
   `ProjectSecurityProfile`-tabel (portfolio-sync-ready, in tegenstelling tot
   `KanbanMeta`). REST-CRUD. Tests: default-policy voor een nieuw product-project
   is `risk_class=product-staging`, `default_skip_permissions=False`,
   `default_transport=sandcastle`.
7. **`[security][D] `CITemplateService` + drie templates.** — ✅ Geleverd
   (kaart `c66a93a20c0a`). Jinja-templates voor `python-strict`, `node-strict`,
   `minimal` in `backend/app/services/ci_templates/`. `apply(project_path, profile=…)`
   schrijft naar `.github/workflows/`, idempotent. Test: render + idempotency +
   parameter-substitutie. Wordt aangeroepen door `RepoBootstrapService` (facet B).
   REST-oppervlak: `GET /api/v1/ci/templates`, `POST /api/v1/ci/templates/{profile}/apply`.
   Volledige doc: `docs/cockpit/ci-templates.md`.
8. **`[feature][D] `RunService` — spawn de gebouwde app in een sandbox.** —
   Hergebruikt Sandcastle's container-mechaniek voor een ander doel: een
   FastAPI/Node/etc.-server starten in een container, poort exposen op
   `127.0.0.1:<random>`, logs streamen. Health-check-loop met configureerbaar
   pad + timeout. Geen deploy, alleen lokaal draaien om te valideren.
9. **`[feature][D] Preview-URL per kanban-kaart + "Run this branch"-actie.** —
   UI-actie op een Done-kaart: start een `RunService`-instantie voor de branch
   van die kaart, post de URL als activity-comment, toon 'm in een
   `PreviewPane`-component. Test: actie → container start → URL werkt →
   container stopt bij "Stop preview".
10. **`[security][D] Security-audit-log + endpoint.** —
    `security_audit`-tabel met `kind`, `project_key`, `actor`, `payload_ref`
    (geen secrets-waarden), `at`. Invulpunten: `set_skip_permissions`,
    `set_transport`, `set_autodispatch`, `SecretStore.put/delete`, `RunService`
    start/stop. REST-endpoint `GET /api/v1/security/audit?project_key=…&since=…`.
    Geen write-API; alleen via de genoemde invulpunten.
11. **`[security][D] `DeployTarget`-abstractie + MVP-implementatie (GHCR).** —
    Pas ná F1+F2. Abstracte `DeployTarget.deploy(project_path, tag)`; MVP: bouw
    een container-image met de app + push naar `ghcr.io/<owner>/<repo>:<tag>` via
    `docker buildx` + `docker push`. Geen cloud-deploy, geen DNS, geen runtime-
    provisioning. Eerste zinvolle sub-stap naar echte deploy zonder de hele
    cloud-provider-integratie.
12. **`[design][D] `risk_class`-taxonomie + classifier voor `ProjectSecurityPolicy`.** —
    Een aparte analyse-kaart die uitzoekt: welke signalen bepalen of een
    project `meta`, `product-staging`, `product-prod`, of `untrusted` is?
    Repo-eigenaar? Eerste-commit-domein? Aantal agent-sessies? Handmatige
    tagging? Dit is geen implementatie; de uitkomst voedt de default-policy
    uit follow-up #6. Resultaat = `docs/cockpit/risk-class-taxonomie.md`.

## 7. Niet in deze facet (expliciete out-of-scope)

- **Inceptie/intake-flow, `BlueprintService`-data-model, kanban-A-kaarten** → facet A.
- **Repo-creatie, scaffolds, `RepoBootstrapService`, `BlueprintApply`-engine, key-migratie** → facet B.
- **Portfolio-cap, cross-project dispatch-governance, portfolio-dashboard, sync tussen devices** → facet C.
- **Headless-SDK-transport, observability-scraping-residu** → `orchestration-substrate-decision.md` §6.
- **Sandcastle-image-building-flow + advanced features (parallel runs, SSE-log-streaming)** → losse Sandcastle-trajecten (zie `sandcastle-integration-plan.md` §7-8).
- **MCP-server-trust-model** (een kwaadaardige MCP-server in een geregistreerd project) → apart spoor; geen D-verantwoordelijkheid.
- **Multi-tenancy / echte multi-user / RBAC** → portfolio-architectuurproject.
- **Compliance (SOC2/ISO27001)** → consultancy; D legt het fundament.

## 8. Relatie met de andere facetten

(MECE + overlapkaart — gedeeltelijk herhaald uit A en B voor de leesbaarheid.)

- **Facet A** (intake): levert het *moment* waarop een project ontstaat en daarmee de eerste policy-keuze (`risk_class`). A's `BlueprintService.apply` (Backlog-kaart `395590d7`) is de aanroeper van D's `CITemplateService.apply` (follow-up #7): blueprint = welke agents/skills/permissions; CI-template = welke quality-gates. Beide worden in `RepoBootstrapService` (B) aan elkaar gelijmd.
- **Facet B** (repo-provisioning): voert de bootstrap uit, **inclusief** de eerste `ProjectSecurityPolicy`-instantie (follow-up #6) op basis van `risk_class` (follow-up #12). B's `gh repo create`-flow heeft de `gh`-credentials uit D's `SecretStore` nodig (follow-up #4); B's `RepoBootstrapPolicy` heeft `default_transport`/`default_skip_permissions` uit D's policy.
- **Facet C** (portfolio): erft de portfolio-policy-sync via de `ProjectSecurityProfile`-tabel (follow-up #6) — D kiest *bewust* voor een eigen tabel i.p.v. `KanbanMeta` om portfolio-sync mogelijk te maken. C kan ook "alle product-projecten moeten sandcastle-transport hebben" als portfolio-rule afdwingen.
- **Sandcastle:** D voegt *security*-features toe aan Sandcastle (resource-caps, network-mode, read-only-rootfs); Sandcastle's overige roadmap (parallel-runs, SSE-log-streaming, agent-provider-wijzigingen) blijft bij Sandcastle.
- **Headless-SDK (transport-seam):** D geeft geen mening over welke projecten headless worden, alleen over welke *security-policy* op een headless-run van toepassing is (gelijk aan het onderliggende container-profiel).

## 9. Kernbevinding (voor de ouder-comment)

> Cockpit heeft vandaag **een transportsubstraat** waarin isolatieprincipes
> ingebouwd kúnnen worden (worktree + Sandcastle + de al ontworpen remote/SDK-
> seam), maar **mist de services eromheen**: geen API-auth-by-default, geen
> write-anywhere-allowlist (I4b), geen per-project secrets-store, geen env-
> injectie-filter in `spawn_session`, geen project-security-policy, geen CI-
> bootstrap-flow, geen run/preview-pad voor het gebouwde product. Sandcastle
> biedt container-isolatie maar staat default op `no-sandbox` en heeft geen
> resource-caps, network-policy of read-only-rootfs. De **resource-cap, de
> env-isolatie en de auth-default** zijn de drie single-line fixes met de
> hoogste impact; de **secret-store, de ProjectSecurityPolicy, de
> CI-templates en de RunService** zijn de vier ontwerp-investeringen die de
> stap maken van "kan een app bouwen" naar "kan een app veilig bouwen,
> valideren en uitleveren". Twaalf actionabele gaten zijn als Backlog-follow-
> ups gefileerd, gegroepeerd in drie fasen (Lockdown → Run/Preview → Deploy)
> zodat de eerste helft al waarde levert zonder de tweede af te wachten. Geen
> overlap met de al gefilede facet-A- en facet-B-kaarten; geen daarvan wordt
> door deze kaart geïmplementeerd.