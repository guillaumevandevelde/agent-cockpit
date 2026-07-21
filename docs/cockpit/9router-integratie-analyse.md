---
title: "9Router-integratie — analyse & beslissing"
type: decision
status: decided
---

# 9Router-integratie — analyse & beslissing

**Datum:** 2026-07-19
**Status:** besloten
**Kaart:** `27cdc2bd…` "Analysis - Integration 9router"
**Bron:** <https://github.com/decolua/9router> (MIT, master @ `0513bf39`, gemeten 2026-07-19)

**Uitkomst in één alinea.** **NO-GO op "integreren als geheel"; conditionele GO op
"ernaast draaien", maar dan als één nieuwe, opt-in provider-entry achter de
bestaande `provider_env.py`-naad — niet als default en niet met
subscription-OAuth.** De vraag "is 9router matuurder dan onze provider-laag?"
berust op een categoriefout: 9router is een **inference-router** (routeert per
*request*), Cockpit's provider-laag is een **spawn-configurator** (kiest per
*sessie*, vóór het proces start). Ze concurreren niet — ze zitten op
verschillende lagen. Wat 9router wél biedt en Cockpit mist is
**format-translatie**, en dát is precies wat toegang tot gratis/goedkope
non-Anthropic backends ontsluit voor de `claude`-CLI. De prijs is een
credential-honeypot, prompt-mutatie op de agent-hot-path, en een
ToS-/ban-risico dat exact het Anthropic-abonnement raakt waar álle dispatch op
draait. Vandaar: smalle naad, opt-in, API-key-tier only.

---

## 1. Wat de kaart vroeg

> "Ik zou graag nog meer gebruik kunnen maken van verschillende providers,
> idealiter degene die gratis modellen aanbieden. […] integreren als geheel?
> Naast deze applicatie draaien als provider router? Wat met de bestaande
> provider functionaliteit? Deze applicatie lijkt meer matuur te zijn? Wees
> kritisch, maak niet te snel een conclusie!"

Vier deelvragen, elk apart beantwoord in §5–§8.

## 2. Wat 9Router feitelijk is

Een lokale Next.js-applicatie (Node 20+, SQLite) die op `:20128` een
LLM-endpoint aanbiedt en requests doorstuurt naar 40+ upstream-providers, met
drie-traps-fallback (subscription → cheap → free).

**Geverifieerde technische feiten** (bron: repo-tree + `next.config.mjs` @
`0513bf39`, niet alleen de README):

| Feit | Bewijs |
|---|---|
| Exposeert **Anthropic-native** `/v1/messages` **en** `/v1/messages/count_tokens` | `src/app/api/v1/messages/route.js`, `.../count_tokens/route.js` |
| Exposeert ook OpenAI-compat `/v1/chat/completions`, `/v1/responses`, `/v1/embeddings`, audio/images/video | `src/app/api/v1/**` |
| `ANTHROPIC_BASE_URL=http://localhost:20128/v1` werkt | `next.config.mjs` rewrite `/v1/v1/:path*` → `/api/v1/:path*` — de client plakt zelf `/v1/messages` erachter |
| Format-translatie OpenAI ↔ Claude ↔ Gemini ↔ Cursor ↔ Kiro ↔ Vertex | README §Format Translation |
| Licentie MIT | `gh api repos/decolua/9router → license.spdx_id` |

Dat eerste punt is belangrijk en corrigeert een voor de hand liggende
aanname: de README's Claude-Code-sectie (`~/.claude/config.json` met
`anthropic_api_base`) is **stale/onjuist** — Claude Code leest die sleutels niet
— maar de onderliggende *URL-vorm* klopt wél dankzij de rewrite. De juiste
configuratie is `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`, precies de twee
env-vars die `build_provider_env` vandaag al zet voor MiniMax.

### 2.1 De token-savers

Drie mechanismen, alle drie **prompt-muterend**:

- **RTK** — comprimeert `tool_result`-payloads (git diff, grep, ls, tree) vóór ze naar het model gaan. **Default ON.**
- **Caveman** — injecteert een "terse-speak"-systeemprompt, claim: tot 65% minder output-tokens.
- **Ponytail** — injecteert een "lazy senior dev, YAGNI-first"-prompt.

> **Ongemeten claim.** "Save 20-40% tokens" is een **vendor-claim uit de
> README**, niet door mij gemeten. Ik heb 9router niet gedraaid; er is in dit
> onderzoek geen token-meting gedaan. Behandel het getal als marketing tot het
> gereproduceerd is (recept: `claude -p "ok" --output-format json` met en zonder
> `ANTHROPIC_BASE_URL` naar 9router, verschil in
> `input + cache_creation + cache_read` — zelfde methode als
> [`per-persona-mcp-allowlist-decision.md` §7](./per-persona-mcp-allowlist-decision.md#7-reproductie)).
> §9 bevat de kaart die dit meet vóórdat er iets op wordt gebouwd.

### 2.2 De "gratis" tiers — hoe ze werkelijk werken

Dit is de kern van de gebruikersvraag ("idealiter degene die gratis modellen
aanbieden") en tegelijk het meest risicovolle deel. Drie categorieën, en ze zijn
**niet** gelijkwaardig:

| Tier | Mechanisme | Oordeel |
|---|---|---|
| **Kiro AI** ("Claude 4.5 + GLM-5 + MiniMax, unlimited FREE") | OAuth via AWS Builder ID / Google / GitHub; de token van een IDE-product wordt hergebruikt om requests van willekeurige andere clients te serveren | ⛔ **Grijs tot ToS-schendend.** Onbeperkt Claude serveren via andermans product-OAuth is geen bedoeld gebruik. Ban-risico op het gekoppelde account. |
| **Subscription-tier** (Claude Pro/Max, Copilot, Cursor) | Idem: subscription-OAuth doorgeven aan een zelfgebouwde proxy | ⛔ **Raakt ons kritieke pad.** Zie §4. |
| **OpenCode Free** ("no auth") | Geen authenticatie, auto-fetch van modellen | ⚠️ Ondoorzichtig — geen SLA, geen garantie, onbekende dataretentie |
| **Vertex $300 credits** | Echte GCP-credits, eigen account | ✅ Legitiem, maar eindig (90 dagen) en dat kan Cockpit vandaag al via een eigen key |
| **API-key-providers** (OpenRouter, Groq, Cerebras, DeepSeek, Together, …) | Je eigen key bij je eigen account | ✅ **Legitiem.** Dit is de tier die het gebruikersdoel haalt zonder ToS-risico. |

De README bevat — geverifieerd met grep op `disclaimer|not affiliated|at your
own risk|violat` — **nul** juridische disclaimer, ToS-waarschuwing of
ban-risiconotitie, terwijl het project in zijn eigen GitHub-omschrijving
"Unlimited FREE AI coding" adverteert. Voor een project dat zijn kernpropositie
op andermans OAuth bouwt is die stilte zelf een signaal.

## 3. "Lijkt matuurder" — getoetst aan cijfers

De kaart stelt dit als vermoeden. Het is **niet houdbaar** zoals geformuleerd.
Gemeten via de GitHub-API op 2026-07-19:

| Signaal | Waarde | Interpretatie |
|---|---|---|
| Sterren | 22.689 | Indrukwekkend — maar zie leeftijd |
| **Repo-leeftijd** | **aangemaakt 2026-01-05 → ~6,5 maanden** | ⚠️ 22,7k sterren in 6 maanden = **hype-snelheid**, geen maturiteit |
| Versie | **v0.5.35** (73+ releases in 6,5 maand) | ⚠️ Nog steeds `0.x`; geen stabiliteitsbelofte |
| **Open PR's** | **519** | 🚩 Grootste rode vlag — een niet-verwerkte bijdrage-berg |
| Open/gesloten issues | **700 open / 592 gesloten** | 🚩 Meer open dan ooit gesloten; achterstand groeit |
| Contributors | ~185 | Brede instroom, zie open-PR-cijfer voor de verwerking ervan |
| Taal | JavaScript 99,6%, geen types | ⚠️ Cockpit-backend is volledig getypeerd Python |

**Conclusie: 9router is *breder*, niet *matuurder*.** Het dekt 40+ providers waar
Cockpit er 3 dekt — dat is dekkingsbreedte, een andere as dan maturiteit. Op de
assen die maturiteit meten (stabiele API, verwerkte bijdrage-stroom, dalende
issue-schuld, typeveiligheid, leeftijd) scoort het zwakker dan de laag waarmee
het vergeleken wordt. De vergelijking is bovendien scheef: 9router's 40 providers
zijn 40 × "een HTTP-endpoint met een andere payload-vorm", terwijl Cockpit's
provider-laag `agentic_cli/` bevat — CLI-detectie, capability-probing,
MCP-inventaris, doctor, usage-parsing per CLI. Andere klus, ander aantal.

## 4. Het architecturale kernpunt: twee verschillende lagen

Dit is waarom "integreren als geheel" de verkeerde vraag is.

```
Cockpit vandaag:                       Met 9router ernaast:

kanban-dispatch                        kanban-dispatch
  │ pick_subscription()  ← per SESSIE    │ pick_subscription()  ← per SESSIE
  ↓                                      ↓
build_provider_env()                   build_provider_env(provider="ninerouter")
  ANTHROPIC_BASE_URL=…                   ANTHROPIC_BASE_URL=localhost:20128/v1
  ↓                                      ↓
spawn `claude` CLI                     spawn `claude` CLI
  ↓                                      ↓
Anthropic / Bedrock / MiniMax          9router  ← per REQUEST
                                         ↓ fallback subscription→cheap→free
                                       40+ backends
```

Cockpit routeert op **sessie-granulariteit**: `pick_subscription()`
(`backend/app/kanban/subscription_pool.py`) kiest vóór de spawn één entry uit een
geordende pool met per-entry drempel, slaat gepauzeerde providers over, en valt
terug op de laatste entry. Dat is functioneel *hetzelfde idee* als 9router's
drie-traps-fallback — alleen een laag hoger en één moment eerder.

Het verschil dat er operationeel toe doet: **Cockpit kan niet mid-sessie
failoveren.** Loopt de `claude`-sessie op 40% van een kaart tegen een limiet, dan
sterft die sessie, blijft de `agent:`-claim hangen, ziet de reaper de dode claim,
en volgt release + re-dispatch — met verlies van kaartcontext en werk. Precies
het faalpad dat CLAUDE.md's gotchas beschrijven. 9router zou dat transparant
binnen dezelfde sessie opvangen.

**Dat is de enige echte, unieke opbrengst.** Niet "meer providers" (die kunnen
ook via `provider_env.py`), maar *continuïteit binnen een lopende agent-sessie* —
plus de format-translatie die non-Anthropic backends überhaupt bruikbaar maakt
voor de `claude`-CLI.

### 4.1 Het precedent dat dit raakt

Op **2026-07-15** (kaart `a410468d…`,
[`subscription-verbruik-inzicht-analyse.md`](./subscription-verbruik-inzicht-analyse.md) §5.2)
is de proxy-route al eens afgewogen en afgewezen, letterlijk:

> "Voor het **Anthropic-abonnement** — de subscription waar de vraag over gáát —
> betekent dit subscription-OAuth-verkeer door een zelfgebouwde MITM duwen.
> ❌ **Afgewezen.** Auth- en ToS-risico op precies het abonnement dat we willen
> meten."

Dat precedent is hier **niet** integraal van toepassing (het ging over
observability, niet over capaciteit), maar de *risicoredenering* is één-op-één
overdraagbaar. Daarom is de scheidslijn in §5 getrokken waar hij getrokken is:
9router mag API-key-verkeer dragen, maar **niet** het Anthropic-subscription-OAuth.
Zo blijft de beslissing van 2026-07-15 staan in plaats van er stilzwijgend
overheen te lopen.

### 4.2 Cockpit-specifiek risico: prompt-mutatie op de agent-hot-path

Dit risico is groter voor Cockpit dan voor een interactieve IDE-gebruiker, en
verdient een eigen paragraaf.

Cockpit's dispatch-prompt is een **precisie-instrument**: persona-contract,
kaarttekst, ship-instructies, worktree-scope-regels, MCP-toolafspraken. RTK
comprimeert `tool_result`-payloads (git-diff-output, grep-resultaten) en
Caveman/Ponytail injecteren gedragssturende systeemprompts — *default ON* voor
RTK.

Concrete faalmodi:

1. **RTK comprimeert een `git diff`** die een agent gebruikt om te beslissen wat
   hij commit → verkeerde commit-inhoud, stil.
2. **Ponytail ("lazy senior dev, YAGNI-first")** botst frontaal met een
   engineer-persona die TDD en volledige acceptatiecriteria moet leveren.
3. **Caveman (terse output)** degradeert precies de artefacten die dit bord als
   deliverable rekent: Done-summaries, analysedocs, impediment-vragen.

Een mens in een IDE ziet zulke degradatie meteen en corrigeert. Een autonome
sessie die 40 minuten doorloopt niet — die levert stilletjes slechter werk en
markeert de kaart als Done. **Alle drie de token-savers moeten uit staan** in
elke Cockpit-integratie, en dat moet afgedwongen/geverifieerd zijn, niet
aangenomen.

### 4.3 Credential-concentratie en cloud-sync

9router bewaart OAuth-tokens en API-keys van *alle* gekoppelde providers in een
lokale SQLite. Dat maakt het één honeypot voor het volledige
credential-oppervlak. Bovendien: `CLOUD_URL` staat default op
`https://9router.com` voor "cloud sync across devices (encrypted)". Encrypted of
niet — dat is een uitgaand pad naar een derde partij vanaf de host waar álle
sleutels liggen. In elke integratie moet cloud-sync **expliciet uit**, en de
poort mag niet aan `0.0.0.0` hangen (de README's eigen VPS-recept doet dat wél:
`HOSTNAME=0.0.0.0`).

## 5. Beslissing 1 — "Integreren als geheel?" → **NEE**

Afgewezen, om vier onafhankelijke redenen die elk op zich al volstaan:

1. **Verkeerde taal/stack.** 12 MB JavaScript + Next.js 16 + React 19 + een
   tweede SQLite naast een getypeerde Python/FastAPI-backend met zijn eigen
   SQLite. Twee ORM's, twee migratieverhalen, twee dev-servers.
2. **Cockpit doet per ontwerp nul LLM-calls.** Het spawnt CLI's. Het absorberen
   van een inference-router zou een fundamenteel nieuwe verantwoordelijkheid
   binnenhalen die de architectuur bewust níet heeft — hetzelfde argument dat
   `subscription-verbruik-inzicht-analyse.md` §5.2 al maakte.
3. **Onderhoudslast.** 519 open PR's en een groeiende issue-achterstand
   overnemen als vendored code betekent dat elke upstream-fix een handmatige
   merge wordt. MIT staat het toe; dat maakt het nog geen goed idee.
4. **Nul opbrengst boven "ernaast draaien".** Alles wat 9router biedt, biedt het
   via zijn HTTP-endpoint. Er is geen functie die vendoring ontsluit.

## 6. Beslissing 2 — "Ernaast draaien als provider-router?" → **JA, conditioneel en smal**

**GO**, maar uitsluitend in deze vorm:

- **Eén nieuwe provider-entry** `ninerouter` in `provider_env.py`, naast
  `anthropic` / `bedrock` / `minimax`. Implementatie is vormgelijk aan de
  bestaande MiniMax-tak: `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` +
  `ANTHROPIC_MODEL`. De naad bestaat al; dit is een kleine, lokale ingreep.
- **Opt-in, nooit default.** Niet in de default-pool. Een gebruiker zet 'm
  bewust in `subscription_pool` of als `dispatch_provider`.
- **Alleen de API-key-tier** (OpenRouter, Groq, Cerebras, DeepSeek, Vertex met
  eigen credits). **Niet** Kiro, **niet** OpenCode-Free, **niet**
  subscription-OAuth-tiers — dat houdt §4.1 overeind.
- **Token-savers uit** (RTK/Caveman/Ponytail), **cloud-sync uit**, **loopback-only**
  binding. Geverifieerd, niet aangenomen.
- **Meten vóór bouwen.** De 20–40%-claim is ongemeten (§2.1); die meting is de
  eerste kaart, en de rest hangt eraan.

**Het serieuze alternatief, eerlijk benoemd: LiteLLM.** Het exposeert eveneens een
Anthropic-native `/v1/messages` waar `ANTHROPIC_BASE_URL` op kan wijzen, routeert
naar OpenAI/Gemini/Vertex/Bedrock/Azure, is Python (past bij deze backend), en is
ouder en institutioneel steviger dan een repo van 6,5 maand. Wat het **niet**
heeft is 9router's OAuth-gebaseerde gratis-tiers — en dat is precies de tier die
§2.2 afwijst. **Voor de scope die wij overhouden (API-key-tier) zijn ze
functioneel inwisselbaar, en dan wint LiteLLM op maturiteit.** Daarom is de
provider-entry in §9 opzettelijk generiek gehouden (een
"OpenAI/Anthropic-compatibel endpoint"-provider) en is er een aparte
vergelijkings-kaart: 9router en LiteLLM zijn dan twee configuraties van dezelfde
naad, geen twee verbouwingen. Wie 9router's gratis-tiers per se wil, kiest
bewust het ToS-risico — dat is een mensbeslissing, geen agent-beslissing.

## 7. Beslissing 3 — "Wat met de bestaande provider-functionaliteit?" → **BEHOUDEN, ongewijzigd**

De vraag veronderstelt overlap die er grotendeels niet is. "Provider" is in deze
codebase **twee verschillende dingen**:

| Laag | Bestand | Betekenis | Overlap met 9router |
|---|---|---|---|
| **CLI-registry** | `services/agentic_cli/` (~1.400 regels: claude-code, codex, copilot, opencode, mimo) + `api/v1/providers.py` (800 regels) | *Welk CLI-binary* draait de agent — detectie, capabilities, doctor, MCP-inventaris, plugin-inventaris | **Geen.** 9router raakt dit niet. |
| **Model-backend** | `services/agentic_cli/provider_env.py` (~260 regels) | *Welk inferentie-endpoint* praat de CLI mee | **Hier**, en alleen hier. |
| **Usage/pool** | `services/subscriptions/`, `kanban/subscription_pool.py` | Quota-signaal + sessie-keuze | Conceptuele overlap, andere granulariteit (§4) |

Het overgrote deel van de "provider-functionaliteit" (de CLI-registry) staat
volledig los van 9router en wordt door niets vervangen. De aanraking beperkt
zich tot één tak in één bestand van ~260 regels. Dat is meteen het beste
argument vóór de smalle variant: de naad bestaat al, is al twee keer gebruikt
(bedrock, minimax), en `_ALLOWED_POOL_PROVIDERS` documenteert zelf dat een
provider toevoegen "one edit plus this tuple" is.

Eén reëel aandachtspunt: de **usage-attributie**. `AnthropicUsageProvider` telde
eerder al MiniMax-tokens mee (gekwantificeerd op 36,9% in kaart `a410468d…`). Een
vierde provider die *meerdere* upstreams achter één endpoint verbergt, maakt
`model → subscription`-attributie strikt moeilijker: alle 9router-verkeer ziet er
lokaal identiek uit. Dat is geen blokkade, wel een expliciete
`betrouwbaarheid="onbekend"`-plicht — het bestaande contract in
`subscriptions/base.py` heeft daar al taal voor ("no fabrication") en die moet
hier gerespecteerd worden in plaats van een getal te verzinnen.

✅ **Geïmplementeerd (kaart `390756e6…`):** de router-subscription
`claude-code:anthropic-compatible` krijgt een eigen
`RouterUsageProvider` (`backend/app/services/subscriptions/router.py`)
die `betrouwbaarheid="onbekend"` met `bron="router_eindpunt:…"`
teruggeeft — geen cijfer, geen fabricage. Geregistreerd via
`register_provider` zodat de Subscriptions-pagina en de
pool-router dezelfde eerlijke snapshot zien. Attributie-tests
bewijzen dat router-upstream-modellenamen (`gpt-4o`,
`gemini-1.5-pro`, `llama-*`, …) consequent naar
`UNKNOWN_SUBSCRIPTION_ID` gaan i.p.v. bij `claude-code:anthropic`
mee te lekken — het regressie-schild tegen de `a410468d…`
36,9%-vervuiling.

## 8. Wat dit de gebruiker concreet oplevert

De onderliggende wens was "meer providers, liefst gratis". Eerlijke stand:

- ✅ **Legitiem gratis/goedkoop wordt bereikbaar** — OpenRouter's free-tier-modellen,
  Groq, Cerebras, DeepSeek, Vertex-credits — via één nieuwe provider-entry, mét
  format-translatie zodat de `claude`-CLI ze kan gebruiken. Dat kan Cockpit
  vandaag niet.
- ✅ **Mid-sessie-failover** wordt mogelijk — het faalpad uit §4 (sessie sterft op
  een limiet, claim blijft hangen, re-dispatch verliest werk) verdwijnt voor
  verkeer dat via de router loopt.
- ⛔ **"Unlimited free Claude 4.5"** komt er niet — dat is de Kiro/OAuth-tier, en
  die kost een ban-risico op het account waar de hele dispatch-pijplijn op
  draait. Dat is geen gunstige ruil. Dit is het deel waar de kaart om vroeg en
  waar het antwoord "nee" is.
- ❓ **20–40% tokenbesparing** — ongemeten vendor-claim; en de savers die 'm zouden
  leveren zijn juist degene die §4.2 uitzet. Netto verwachting binnen onze scope:
  **0%**, tot een meting anders uitwijst.

## 9. Vervolgkaarten

Vijf kind-kaarten, gefaseerd zodat het meetwerk vóór het bouwwerk komt.

| # | Kaart | Hangt af van |
|---|---|---|
| K1 | Meet de RTK/token-saver-claim met een reproduceerbaar recept | — |
| K2 | Vergelijk 9router vs. LiteLLM voor de API-key-tier (spike, kiest de backend) | K1 |
| K3 | Generieke `ninerouter`-provider-entry in `provider_env.py` + pool-allowlist | K2 |
| K4 | Hardening-checklist + doctor-check (savers uit, cloud-sync uit, loopback-only) | K3 |
| K5 | Usage-attributie: eerlijk `betrouwbaarheid="onbekend"` voor router-verkeer | K3 |

K1 zonder deps omdat de meting losstaat van de keuze. K2 hangt aan K1 omdat de
meetuitslag de vergelijking voedt. K3 hangt aan K2 omdat de spike bepaalt *welk*
endpoint de entry krijgt. K4 en K5 hangen beide aan K3 (ze hardenen respectievelijk
attribueren wat K3 oplevert) en zijn onderling onafhankelijk — parallel uitvoerbaar.

## 10. Heropenen wanneer?

Deze beslissing is niet "nooit". Heropen bij een van deze triggers:

1. **9router wordt matuur** — `1.x`, open-PR-berg < 100, gesloten > open issues,
   en een expliciete ToS-/disclaimer-sectie. Dan verschuift §3.
2. **De gratis-tiers worden legitiem** — een provider biedt een officieel gratis
   quotum onder eigen voorwaarden i.p.v. via product-OAuth. Dan vervalt §2.2's ⛔.
3. **Cockpit gaat zélf LLM-calls doen** — dan verandert §5's argument 2, en dan
   vuurt overigens ook de §5.3-trigger van
   [`subscription-verbruik-inzicht-analyse.md`](./subscription-verbruik-inzicht-analyse.md).
4. **K1 meet een substantiële besparing** zonder gedragsdegradatie — dan wordt
   §4.2's "alle savers uit" een genuanceerder "RTK aan, prompt-injectors uit".

---

**Meet-verantwoording.** Alle cijfers in §3 komen uit de GitHub-API op
2026-07-19 (`gh api repos/decolua/9router`, `search/issues`, paginering-headers
voor de PR-telling). De technische feiten in §2 komen uit de repo-tree en
`next.config.mjs` op commit `0513bf39`, niet uit de README. De tokenbesparings-
claim in §2.1 is **niet gemeten** en is als zodanig gelabeld.
