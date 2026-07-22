---
title: "Spike: Claude Code model-switching (Anthropic ↔ MiniMax) — ADR"
type: decision
status: decided
---

# Spike: Claude Code model-switching (Anthropic ↔ MiniMax) — ADR

**Date:** 2026-07-04
**Status:** Decided (build-vs-integrate) — implementation not started
**Trigger:** kanban-kaart "Claude Code model switch" — analyse van twee externe tools
(jolehuit/clother en morphllm.com/claude-code-router → musistudio/claude-code-router)
om te bepalen welke geschikt is om binnen Agent Cockpit te schakelen tussen een
Anthropic-abonnement en een MiniMax-abonnement, met adaptief gedrag.

Dit is een losstaand initiatief, orthogonaal aan de lopende scheduled-messages
fase-1/fase-2-scope (`00-orientation.md`, `fase-1-validation.md`, `fase-2-spec.md`) —
geen van beide blokkeert de ander.

---

## 1. Wat er gevraagd wordt

- Ondersteuning voor meerdere configuraties: Anthropic-subscriptie (met bijhorende
  modellen, o.a. Sonnet 5) én een MiniMax-subscriptie, naast elkaar bruikbaar.
- **Adaptief gedrag**, met twee concrete voorbeelden op de kaart:
  1. Taak-type-routing binnen één sessie: analyse met Anthropic Sonnet 5,
     uitvoering met MiniMax.
  2. Limiet-gedreven failover: bij het bereiken van de Anthropic-sessielimiet,
     automatisch overschakelen naar MiniMax.

## 2. Bestaande situatie in Cockpit (grounded facts)

Onderzocht: `backend/app/api/v1/providers.py`, `codex_config.py`,
`backend/app/services/agentic_cli/`, `frontend/src/features/dashboard/components/EnhancedProviderCards.tsx`.

- `providers.py`/`services/agentic_cli/` is een **agentic-CLI-registry** (welke
  coding-agent — Claude Code, Codex, OpenCode, MiMoCode — draait in een
  tmux-pane), **geen** LLM-vendor-abstractie. Er is geen "kies je LLM-provider"
  concept in deze laag (wel een "kies je CLI"-concept — zie `terminology.md`).
- Het enige bestaande mechanisme dat wél Claude Code's *backend-provider* omschakelt
  is `provider_env.py` (`build_provider_env`, voorheen `platform_env.py`/`build_platform_env`):
  een `provider`-veld (`"anthropic"` | `"bedrock"`) dat env vars (`CLAUDE_CODE_USE_BEDROCK`,
  `AWS_REGION`, `AWS_PROFILE`, `ANTHROPIC_MODEL`) injecteert via
  `SpawnCommandOptions` wanneer een Claude Code tmux-sessie gespawned wordt. Dit
  is precies het patroon waar een MiniMax-optie op zou aansluiten.
- Repo-wide search: **geen** bestaande referenties naar "minimax",
  "claude-code-router", `ANTHROPIC_BASE_URL` of `ANTHROPIC_AUTH_TOKEN`. Dit is
  nieuw terrein.
- **Update (interview 2026-07-04, gebruiker heeft een MiniMax-account):**
  MiniMax's eigen documentatie (`platform.minimax.io/docs/token-plan/claude-code`)
  bevestigt dat hun endpoint **rechtstreeks Anthropic-Messages-API-compatible**
  is — geen CCR of andere tussenlaag nodig voor de kale switch. Gedocumenteerde
  config (in `~/.claude/settings.json`, of dus evengoed als env vars bij het
  spawnen van een sessie):
  - `ANTHROPIC_BASE_URL`: `https://api.minimax.io/anthropic` (internationaal) of
    `https://api.minimaxi.com/anthropic` (China)
  - `ANTHROPIC_AUTH_TOKEN`: de MiniMax-API-key
  - `ANTHROPIC_MODEL`: `MiniMax-M3[1m]`
  - `CLAUDE_CODE_AUTO_COMPACT_WINDOW`: `1000000`
  - MiniMax's docs waarschuwen expliciet dat bestaande `ANTHROPIC_AUTH_TOKEN`/
    `ANTHROPIC_BASE_URL` eerst gewist moeten worden om conflicten te vermijden.

  Dit is **exact dezelfde vorm** als de bestaande Bedrock-branch in
  `provider_env.py` (env vars zetten, geen gateway-proces). Dit verandert de
  aanbeveling: de kale "switch tussen Anthropic- en MiniMax-subscriptie"-vraag
  op de kaart heeft **geen CCR nodig** — dat kan met een directe uitbreiding van
  `provider_env.py`. CCR blijft wél nodig voor het *adaptieve* deel (scenario-
  routing binnen één sessie, auto-failover) — zie herziene aanbeveling in §6.

## 3. Tool A — Clother (jolehuit/clother)

Go-binary die de officiële Claude Code CLI wrapt. Bij elke aanroep herkent het
zijn eigen invocation-naam (`clother-native`, `clother-zai`, `clother-kimi`, ...),
zet de bijpassende Anthropic-compatibele env vars, en start dan de echte
`claude`-binary. Config/secrets in `~/.local/share/clother/secrets.env`;
installatie via Homebrew of een curl-installer die symlinks aanmaakt.

- 15+ providers met naam (Anthropic, Z.AI, Kimi, Alibaba, DeepSeek, ...) + lokale
  backends (Ollama, LM Studio); MiniMax niet met naam genoemd, mogelijk wel
  bereikbaar via de OpenRouter-integratie of een custom endpoint.
- Expliciete quote uit de README: **"No automatic switching: Tool requires
  explicit provider selection; no adaptive failover."**
- Granulariteit is per-terminal-invocation (welke launcher je typt), niet
  per-request — er is geen begrip van "deze taak is analyse, die is uitvoering"
  binnen één sessie.

**Conclusie**: geschikt voor een mens die handmatig per terminalsessie een
provider kiest. Kan structureel geen van beide adaptieve voorbeelden op de kaart
waarmaken — dat is precies wat de tool zelf uitsluit.

## 4. Tool B — Claude Code Router (musistudio/claude-code-router, "CCR")

Draait als een lokale HTTP-gateway (default `localhost:8080`) waar Claude Code
via `ANTHROPIC_BASE_URL` naartoe wijst; de gateway vertaalt Anthropic-formaat
requests naar het formaat van de geconfigureerde provider (OpenAI-compatible,
Anthropic Messages, Gemini, OpenRouter, DeepSeek, SiliconFlow, Moonshot, Mistral,
Z.AI, Bailian, **MiniMax met naam genoemd**, en custom endpoints).

Er zijn twee generaties op npm (`@musistudio/claude-code-router`):
- **1.0.x** (tot recent de hoofdversie): headless CLI (`ccr` command),
  configuratie via een lokaal `config.json` met een `Providers`-array en een
  `Router`-object met scenario-keys (`default`, `background`, `think`,
  `longContext`, `webSearch`) — exact de vorm die "analyse met Sonnet 5,
  uitvoering met MiniMax" mogelijk maakt door `default`/`think` op Anthropic te
  zetten en `background` op MiniMax.
- **2.0.0** (huidige `latest`-tag): de README is herschreven rond een
  Electron-desktop-app ("Claude Code Router Desktop") met SQLite-backed config
  via een GUI (Providers/Routing/Server/Agent Config-panelen). De README noemt
  hier geen `ccr code`-commando, geen JSON-schema en geen scenario-routing meer
  expliciet — configuratie is nu "entirely through the desktop UI". **Belangrijk
  voor ons**: het npm-package van 2.0.0 bevat nog steeds een `ccr`-CLI-bin
  (`dist/cli.js`, zelfde als 1.x) — headless draaien lijkt dus nog mogelijk, maar
  is niet meer gedocumenteerd en moet hands-on geverifieerd worden (zie
  vervolgkaart 1).
- Features die in de huidige README genoemd worden: "fallback routing", "API key
  rotation", conditionele/model-prefix routingregels — dit is de kandidaat voor
  het limiet-gedreven-failover-scenario, maar het README specificeert niet
  expliciet of dit ook triggert op een Anthropic rate-limit/sessielimiet-response
  (vs. alleen op statisch geconfigureerde condities) — open vraag, zie §9.

**Conclusie**: dit is een echte request-level router met scenario-based
model-assignment en fallback-routing als kernfeature — structureel de match voor
beide adaptieve voorbeelden op de kaart.

## 5. Vergelijkingstabel

| | Clother | Claude Code Router (CCR) |
|---|---|---|
| Vorm | Go binary, exec-wrapper | Lokale HTTP-gateway/proxy |
| Switch-granulariteit | Per terminal-invocation (expliciete keuze) | Per-request (scenario/regel-gebaseerd) |
| MiniMax | Niet met naam (evt. via OpenRouter) | Met naam ondersteund |
| Adaptief/auto-switch | Expliciet **niet** ("no adaptive failover") | Routing rules + fallback targets (aanwezig; exacte trigger-conditie nog te verifiëren) |
| Analyse-vs-uitvoering routing binnen 1 sessie | Nee (1 model per sessie) | Ja (`default`/`think` vs `background` scenario's, in de 1.x-vorm) |
| Config-vorm | `secrets.env` + CLI-launcher-symlinks | `config.json` (1.x, headless) of SQLite via desktop-UI (2.x) — `ccr`-CLI-bin blijft aanwezig |
| Past op Cockpit's bestaande uitbreidingspunt | Nee — vervangt hoe `claude` aangeroepen wordt | Ja — is niets meer dan een `ANTHROPIC_BASE_URL`-switch, exact het patroon dat `provider_env.py` al hanteert voor Bedrock |

## 6. Aanbeveling (herzien na interview 2026-07-04)

**Twee lagen, niet één tool-keuze:**

**Laag 1 — kale subscriptie-switch (geen CCR nodig).** MiniMax's endpoint is
rechtstreeks Anthropic-Messages-compatible (§2). Dit dekt het eerste deel van de
kaart ("ondersteuning van verschillende configuraties") met exact het bestaande
`provider_env.py`-patroon: een nieuwe `PROVIDER_MINIMAX`-branch die
`ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_MODEL` zet, net zoals de
Bedrock-branch dat vandaag doet. Kleinste, laagste-risico stap — geen extern
proces, geen nieuwe dependency.

**Laag 2 — adaptief gedrag (Claude Code Router, niet Clother).** Voor de twee
adaptieve voorbeelden op de kaart (analyse-vs-uitvoering-routing binnen één
sessie, auto-failover bij sessielimiet) is per-request routing nodig, niet
per-sessie env vars — dat kan geen van beide tools puur via env vars, maar CCR
biedt het als kernfeature (scenario-routing + fallback-routing), terwijl
Clother's eigen README dit expliciet uitsluit ("no adaptive failover"). CCR
draait dan als lokale HTTP-gateway waar Claude Code via `ANTHROPIC_BASE_URL`
naartoe wijst — nog steeds hetzelfde `provider_env.py`-patroon, alleen wijst de
URL nu naar de CCR-gateway in plaats van rechtstreeks naar MiniMax.

**Caveat**: richt op de 1.x-achtige headless/`config.json`-werkwijze, niet op de
2.0-desktop-UI-flow — Cockpit draait in WSL zonder Electron-GUI. Het `ccr`-CLI-bin
blijft aanwezig in het 2.0.0-npm-package volgens de registry-metadata, maar dit
moet hands-on bevestigd worden vóórdat een vervolgkaart hierop bouwt.

**Beslissingen uit interview (2026-07-04):**
- CCR draait als **één gedeeld achtergrondproces** voor alle sessies/projecten
  (niet per-project) — analoog aan hoe sandcastle nu draait.
- Automatische failover bij sessielimiet is **nice-to-have**, geen MVP-blocker;
  Laag 1 (handmatige switch) levert de kernwaarde, Laag 2 (adaptief) volgt erna
  op medium prioriteit.

## 7. Concreet integratievoorstel (voor de vervolgkaarten, niet nu bouwen)

**Laag 1 (direct, geen CCR):**
- Nieuwe constante `PROVIDER_MINIMAX` naast `PROVIDER_ANTHROPIC`/
  `PROVIDER_BEDROCK` in `provider_env.py` (na de rename; daarvoor:
  `PLATFORM_MINIMAX`/`PLATFORM_ANTHROPIC`/`PLATFORM_BEDROCK` in
  `platform_env.py`).
- `build_provider_env` krijgt een branch die, wanneer `provider ==
  PROVIDER_MINIMAX`, `ANTHROPIC_BASE_URL` (international/China-varianten),
  `ANTHROPIC_AUTH_TOKEN` en `ANTHROPIC_MODEL=MiniMax-M3[1m]` zet — hergebruik
  van de bestaande `SpawnCommandOptions`-injectiemechaniek, geen nieuwe.
- UI: uitbreiden van de bestaande provider-selector (waar Bedrock nu gekozen
  wordt, zie `EnhancedProviderCards.tsx`/`useProviders.ts`) met een
  "MiniMax"-optie.

**Laag 2 (later, CCR voor adaptief gedrag):**
- CCR draait als één **gedeeld achtergrondproces** (beslist, zie §6),
  vergelijkbaar met hoe sandcastle wordt aangestuurd
  (`sandcastle-integration-plan.md`), met een `config.json` die de
  scenario→model-mapping vastlegt (`default`/`think` → Anthropic Sonnet 5,
  `background` → MiniMax) plus een fallback-regel voor rate-limit-condities.
- Wanneer een sessie voor CCR-routing kiest, wijst `provider_env.py` z'n
  `ANTHROPIC_BASE_URL` naar de lokale CCR-gateway in plaats van rechtstreeks
  naar MiniMax.
- Geen wijziging aan `providers.py`/`codex_config.py` — die blijven
  agent-CLI-registries, ongerelateerd aan dit voorstel.

## 8. Vervolgkaarten (aangemaakt op het bord, zie §10)

1. **`PROVIDER_MINIMAX` toevoegen aan `provider_env.py`** (Laag 1) — directe
   subscriptie-switch, geen CCR, analoog aan de bestaande Bedrock-branch + tests.
2. **CCR headless-haalbaarheid verifiëren** (Laag 2, voorwaarde voor kaart 3/4) —
   installeer `@musistudio/claude-code-router` in de WSL-omgeving, bevestig dat
   `ccr` start/config laadt zonder de Electron-UI, documenteer het exacte
   `config.json`-schema zoals het vandaag daadwerkelijk werkt.
3. **CCR als gedeeld achtergrondproces opzetten** (Laag 2) — lifecycle-beheer
   (start/stop/bewaking) analoog aan sandcastle; scenario-routingconfig
   (default/think → Anthropic, background → MiniMax).
4. **Adaptief-switch-gedrag (auto-failover bij sessielimiet) valideren** (Laag 2,
   medium prioriteit, nice-to-have) — hands-on bevestigen dat CCR's
   fallback-routing daadwerkelijk overschakelt bij een rate-limit/sessielimiet-
   response van Anthropic (niet alleen bij statisch geconfigureerde condities),
   vóórdat dit als betrouwbaarheidsmechanisme aan gebruikers wordt beloofd.

## 9. Open vragen — opgelost via interview (2026-07-04)

- ~~Gedeeld achtergrondproces vs. per-project?~~ → **Gedeeld proces**, analoog
  aan sandcastle (zie §6).
- ~~Is MiniMax's endpoint Anthropic-Messages-compatible?~~ → **Ja**, bevestigd
  via `platform.minimax.io/docs/token-plan/claude-code` (zie §2-update). Dit
  maakt Laag 1 mogelijk zonder CCR.
- ~~Hoe belangrijk is auto-failover voor de eerste versie?~~ → **Nice-to-have**,
  geen MVP-blocker; wel als kaart aangemaakt op medium prioriteit (kaart 4).
- **Nog open (voor vervolgkaart 4)**: triggert CCR's "fallback routing"
  daadwerkelijk op een 429/limiet-response, of is het alleen regel-gebaseerd op
  request-kenmerken (model-prefix, taak-categorie)? Bepaalt of het
  sessielimiet-auto-switch-scenario out-of-the-box werkt of een extra laag
  nodig heeft — te verifiëren hands-on, niet iets de gebruiker vooraf kan weten.

## 10. Kanban-vervolgkaarten

Aangemaakt in de `Backlog`-kolom van dit project, gelinkt aan deze spike:
- "MiniMax provider toevoegen aan provider_env.py (directe switch, geen CCR)"
- "Claude Code Router (CCR) headless-haalbaarheid verifiëren in WSL"
- "CCR als gedeeld achtergrondproces opzetten voor scenario-routing"
- "Adaptief-switch-gedrag (auto-failover bij sessielimiet) valideren via CCR — medium prioriteit"

## 11. Verificatieresultaat vervolgkaart 2 — CCR headless-haalbaarheid (2026-07-06)

Hands-on getest in de WSL-omgeving met `@musistudio/claude-code-router` op
npm-versies 1.0.73, 2.0.0 én 3.0.0 (huidige `latest`, niet meer 2.0.0 zoals in
§4 verondersteld — het package is inmiddels opnieuw gepivot).

### 11.1 Headless-CLI: bevestigd voor 1.0.73 en 2.0.0

Beide starten via `ccr start` een pure Node/Fastify-gateway, geen Electron,
geen browser-open. Geverifieerd: `ccr --help` toont `start`/`stop`/`code`/`ui`
als losse commando's; `ccr start` laadt `~/.claude-code-router/config.json`,
opent de geconfigureerde poort (`127.0.0.1:8080` in de test), en `curl
http://127.0.0.1:8080/providers` retourneert de geconfigureerde providers
exact zoals in `config.json` opgegeven. `ccr code` zet `ANTHROPIC_BASE_URL=
http://127.0.0.1:${port}` en start de echte `claude`-CLI — precies het
`provider_env.py`-patroon. Voor deze twee versies raakt niets buiten het eigen
`~/.claude-code-router/`-mapje aan.

### 11.2 config.json-schema (1.0.73/2.0.0, zoals het vandaag werkt)

```json
{
  "PORT": 8080,
  "HOST": "127.0.0.1",
  "APIKEY": "shared-secret-for-the-gateway-itself",
  "Providers": [
    {
      "name": "anthropic-direct",
      "api_base_url": "https://api.anthropic.com/v1/messages",
      "api_key": "sk-ant-...",
      "models": ["claude-sonnet-5"]
    },
    {
      "name": "minimax",
      "api_base_url": "https://api.minimax.io/anthropic/v1/messages",
      "api_key": "<minimax-api-key>",
      "models": ["MiniMax-M3"]
    }
  ],
  "Router": {
    "default": "anthropic-direct,claude-sonnet-5",
    "background": "minimax,MiniMax-M3",
    "think": "anthropic-direct,claude-sonnet-5",
    "longContext": "anthropic-direct,claude-sonnet-5",
    "webSearch": "anthropic-direct,claude-sonnet-5"
  }
}
```

Bevestigd via een live start + `curl /providers`: `Providers[].name`/
`api_base_url`/`api_key`/`models` zijn de enige verplichte velden. `Router`
scenario-keys (`default`/`background`/`think`/`longContext`/`webSearch`) zijn
strings van de vorm `"<provider-name>,<model>"`.

### 11.3 MiniMax zonder transformer — bevestigd

`@musistudio/llms` (de onderliggende gateway-library) registreert transformers
voor met naam genoemde protocollen (`anthropic`, `gemini`, `deepseek`,
`openai`, `openrouter`, `groq`, ...) — **geen enkele met "minimax" in de naam**.
Een Provider-entry zonder `transformer`-veld wordt als natief
Anthropic-Messages-formaat (`/v1/messages`) behandeld. Omdat MiniMax's
endpoint al Anthropic-Messages-compatible is (bevestigd in §2), is een kale
Provider-entry zoals hierboven voldoende — geen custom transformer-script
nodig, exact zoals verwacht.

### 11.4 ⚠️ Belangrijkste bevinding: 3.0.0 patcht ongevraagd live configs van andere tools

`3.0.0` is **geen Electron-app** (geen `electron`-dependency; wél nieuw:
`better-sqlite3`) — het is nog steeds een Node-CLI + optionele web-UI
(`ccr ui`), dus in die zin ook headless-bruikbaar. Maar het introduceert een
**auto-onboarding-mechanisme** dat niet in enige README staat en dat tijdens
dit onderzoek per ongeluk de echte `~/.claude/settings.json` en
`~/.codex/config.toml` van deze machine heeft aangepast:

- **Trigger, exact gereproduceerd**: als `~/.claude-code-router/config.sqlite`
  nog niet bestaat (dus de allereerste `ccr start` van v3 op die `$HOME`) **én**
  er al een legacy `~/.claude-code-router/config.json` met geconfigureerde
  `Providers` aanwezig is (bv. achtergelaten door een eerdere 1.x/2.x-test),
  dan migreert 3.0.0 dit automatisch naar een SQLite-"profile" **en activeert
  dat profiel meteen voor elke gedetecteerde client-CLI** (`~/.claude` →
  Claude Code, `~/.codex` → Codex CLI) — zonder bevestigingsvraag.
- Reproductie: met een lege `$HOME` (geen legacy `config.json`) blijft `ccr
  start` volledig passief (alleen management-API op poort 3458, geen
  gateway-poort, geen configs aangeraakt) — ook op de allereerste start. Zodra
  een legacy `config.json` met providers vooraf aanwezig is op een verse
  `$HOME`, triggert dezelfde `ccr start` wél de auto-patch (herhaald
  bevestigd in een geïsoleerde `$HOME` in de scratchpad).
- Gepatcht werd: `~/.claude/settings.json` kreeg een `env`-blok
  (`ANTHROPIC_BASE_URL`/`ANTHROPIC_API_BASE_URL`/
  `CLAUDE_AGENT_API_BASE_URL=http://127.0.0.1:8080`) plus een `apiKeyHelper`
  die naar een door CCR gegenereerd script wijst; `~/.codex/config.toml` kreeg
  een `[model_providers.claude-code-router]`-blok en
  `model_provider = "claude-code-router"`. Beide bestanden zijn **de live,
  gedeelde configs** die elke Claude Code- / Codex-sessie op deze machine
  gebruikt — dus elke sessie die in dat venster gestart werd, zou
  geauthenticeerd hebben tegen de (placeholder-key) gateway in plaats van de
  echte Anthropic-/Codex-auth, en gefaald zijn. Dit is wat de "kapotte sessie"
  tijdens dit onderzoek veroorzaakte.
- **Geen blijvende schade**: `~/.claude/.credentials.json` (de echte
  OAuth-subscriptietokens) is nooit aangeraakt — alleen de routing-configuratie
  werd tijdelijk omgezet. `~/.claude/settings.json` bleek zelf al terug te zijn
  gezet naar de originele inhoud tegen de tijd dat dit werd opgemerkt
  (vermoedelijk CCR's eigen shutdown/toggle-logica); de overige
  `*.ccr-*`-backupbestanden en de volledig door CCR aangemaakte
  `~/.codex/config.toml`/`~/.claude-code-router/`-boel zijn nadien opgeruimd.

**Consequentie voor kaart 3 ("CCR als gedeeld achtergrondproces opzetten")**:
als CCR daadwerkelijk als gedeeld achtergrondproces gaat draaien op deze
multi-agent-machine, moet vervolgkaart 3 expliciet omgaan met dit
auto-onboarding-gedrag — bv. door **nooit een legacy `config.json` met
providers achter te laten voordat 3.0.0 voor het eerst start**, door 3.0.0's
auto-integratie met andere CLI's expliciet uit te zetten (nog te vinden of
zo'n vlag bestaat), of door bewust op 1.0.73/2.0.0 te blijven zitten — die
raken audit-baar niets aan buiten hun eigen configmap.

### 11.5 Werkt dit met een Anthropic-abonnement, of alleen met API-keys?

**Alleen API-keys — geen enkele laag hier ondersteunt het delen van een
Anthropic Pro/Max-abonnement.** Claude Code's abonnement authenticeert via
OAuth-browser-login (`~/.claude/.credentials.json`); zodra
`ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`/`apiKeyHelper` gezet wordt (wat
zowel CCR als de kale MiniMax-switch uit §2 doen) slaat Claude Code de
OAuth-abonnementsroute voor die sessie volledig over en authenticeert in
plaats daarvan met de opgegeven token als kale API-key tegen het opgegeven
endpoint. Bevestigd via code-search: CCR's bundel bevat geen enkele
Anthropic-OAuth-logica (de enige OAuth-code erin is Google's, voor de
Gemini-provider) en de npm-omschrijving van het package luidt letterlijk *"Use
Claude Code without an Anthropics account and route it to another LLM
provider"*. MiniMax-toegang loopt via MiniMax's eigen API-key/account (zie
§2) — volledig los van de Anthropic-subscriptie. Conclusie: het bestaande
Anthropic-abonnement blijft gewoon werken zolang een sessie geen
`ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` override heeft; er is geen manier
om abonnements-"credits" te delen tussen Anthropic en MiniMax via CCR of de
directe switch — beide vervangen de auth-methode volledig in plaats van hem
te bemiddelen.
