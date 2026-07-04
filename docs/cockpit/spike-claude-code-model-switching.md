# Spike: Claude Code model-switching (Anthropic ↔ MiniMax) — ADR

**Date:** 2026-07-04
**Status:** Decided (build-vs-integrate) — implementation not started
**Trigger:** kanban-kaart "Claude Code model switch" — analyse van twee externe tools
(jolehuit/clother en morphllm.com/claude-code-router → musistudio/claude-code-router)
om te bepalen welke geschikt is om binnen Claude Cockpit te schakelen tussen een
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
`backend/app/services/providers/`, `frontend/src/features/dashboard/components/EnhancedProviderCards.tsx`.

- `providers.py`/`services/providers/` is een **agent-CLI-registry** (welke
  coding-agent — Claude Code, Codex, OpenCode, MiMoCode — draait in een
  tmux-pane), **geen** LLM-vendor-abstractie. Er is geen "kies je LLM-provider"
  concept in deze laag.
- Het enige bestaande mechanisme dat wél Claude Code's *backend-platform* omschakelt
  is `platform_env.py` (`build_platform_env`): een `platform`-veld
  (`"anthropic"` | `"bedrock"`) dat env vars (`CLAUDE_CODE_USE_BEDROCK`,
  `AWS_REGION`, `AWS_PROFILE`, `ANTHROPIC_MODEL`) injecteert via
  `SpawnCommandOptions` wanneer een Claude Code tmux-sessie gespawned wordt. Dit
  is precies het patroon waar een MiniMax-optie op zou aansluiten.
- Repo-wide search: **geen** bestaande referenties naar "minimax",
  "claude-code-router", `ANTHROPIC_BASE_URL` of `ANTHROPIC_AUTH_TOKEN`. Dit is
  nieuw terrein.

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
| Past op Cockpit's bestaande uitbreidingspunt | Nee — vervangt hoe `claude` aangeroepen wordt | Ja — is niets meer dan een `ANTHROPIC_BASE_URL`-switch, exact het patroon dat `platform_env.py` al hanteert voor Bedrock |

## 6. Aanbeveling

**Claude Code Router (CCR), niet Clother.**

De kaart vraagt expliciet om adaptief gedrag — dat is precies wat Clother zelf
uitsluit ("no adaptive failover") en wat CCR als kernfeature aanbiedt
(scenario-routing + fallback-routing). CCR past bovendien naadloos op het
bestaande uitbreidingspunt van Cockpit: het is functioneel niets meer dan een
lokale HTTP-gateway waar Claude Code via `ANTHROPIC_BASE_URL` naartoe wijst —
exact hetzelfde soort env-var-injectie dat `platform_env.py`/
`SpawnCommandOptions` vandaag al doet voor Bedrock (`CLAUDE_CODE_USE_BEDROCK`).
Er is geen nieuwe architectuur nodig, enkel een nieuwe `platform`-waarde.

**Caveat**: richt op de 1.x-achtige headless/`config.json`-werkwijze, niet op de
2.0-desktop-UI-flow — Cockpit draait in WSL zonder Electron-GUI. Het `ccr`-CLI-bin
blijft aanwezig in het 2.0.0-npm-package volgens de registry-metadata, maar dit
moet hands-on bevestigd worden vóórdat een vervolgkaart hierop bouwt.

## 7. Concreet integratievoorstel (voor de vervolgkaart, niet nu bouwen)

- Nieuwe constante `PLATFORM_MINIMAX` (of generieker `PLATFORM_CCR`) naast
  `PLATFORM_ANTHROPIC`/`PLATFORM_BEDROCK` in `platform_env.py`.
- `build_platform_env` krijgt een branch die, wanneer `platform == PLATFORM_CCR`,
  `ANTHROPIC_BASE_URL=http://localhost:<ccr-port>` (en evt.
  `ANTHROPIC_AUTH_TOKEN` als CCR dat vereist) zet — hergebruik van de bestaande
  `SpawnCommandOptions`-injectiemechaniek, geen nieuwe.
- CCR zelf draait niet per-sessie maar als één gedeeld achtergrondproces
  (vergelijkbaar met hoe sandcastle wordt aangestuurd, zie
  `sandcastle-integration-plan.md`), met een `config.json` die de
  scenario→model-mapping vastlegt (`default`/`think` → Anthropic Sonnet 5,
  `background` → MiniMax) plus een fallback-regel voor rate-limit-condities.
- UI: uitbreiden van de bestaande platform-selector (waar Bedrock nu gekozen
  wordt, zie `EnhancedProviderCards.tsx`/`useProviders.ts`) met een
  "MiniMax (via CCR)"-optie — geen nieuwe featuremodule nodig.
- Geen wijziging aan `providers.py`/`codex_config.py` — die blijven
  agent-CLI-registries, ongerelateerd aan dit voorstel.

## 8. Vervolgkaarten (uit scope van deze spike)

1. **CCR headless-haalbaarheid verifiëren** — installeer
   `@musistudio/claude-code-router` in de WSL-omgeving, bevestig dat `ccr`
   start/config laadt zonder de Electron-UI, en documenteer het exacte
   `config.json`-schema zoals het vandaag daadwerkelijk werkt (de 2.0-README
   documenteert dit niet meer expliciet).
2. **`PLATFORM_MINIMAX`/`PLATFORM_CCR` toevoegen aan `platform_env.py`** + tests,
   analoog aan de bestaande Bedrock-branch.
3. **CCR-gateway lifecycle-beheer** — beslissen of Cockpit het CCR-proces zelf
   start/bewaakt (zoals sandcastle) of dat het een door de gebruiker extern
   beheerd proces is waar Cockpit alleen naar verwijst.
4. **Adaptief-switch-gedrag valideren** — hands-on bevestigen dat CCR's
   fallback-routing daadwerkelijk overschakelt bij een rate-limit/sessielimiet-
   response van Anthropic (niet alleen bij statisch geconfigureerde condities),
   vóórdat dit als betrouwbaarheidsmechanisme aan gebruikers wordt beloofd.

## 9. Open vragen voor de vervolgkaarten

- Wil de gebruiker CCR als één gedeeld achtergrondproces voor alle sessies, of
  per-sessie/per-project met een eigen routing-config?
- Heeft MiniMax's API daadwerkelijk een Anthropic-Messages-compatibele of
  OpenAI-compatible endpoint die CCR zonder extra transformer-script kan
  aanspreken? CCR noemt MiniMax met naam, maar het exacte protocol is niet uit
  de huidige README te halen — te verifiëren in vervolgkaart 1.
- Triggert CCR's "fallback routing" daadwerkelijk op een 429/limiet-response, of
  is het alleen regel-gebaseerd op request-kenmerken (model-prefix,
  taak-categorie)? Bepaalt of voorbeeld 2 van de kaart (sessielimiet → auto-switch)
  out-of-the-box werkt of een extra laag nodig heeft.
