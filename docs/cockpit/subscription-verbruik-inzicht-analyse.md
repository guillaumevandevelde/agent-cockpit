# Analyse — Inzicht in verbruik per subscription (inkantelen vs. Langfuse)

**Datum:** 2026-07-15
**Status:** Analyse / beslisdocument — aanbeveling **inkantelen**, Langfuse afgewezen voor
dit doel (conditioneel bewaard, §5.3)
**Trigger:** kanban-kaart `a410468d…` "Analyse - Inzicht in verbruik per subscription".
Gebruiker:
> "Graag had ik wat inzichten verkregen in het verbruik van mijn subscripties. Kunnen we
> dit inkantelen of zouden we hiervoor beter langfuse koppelen aan de applicatie?"

Verwant: [`subscription-flexibiliteit-analyse.md`](./subscription-flexibiliteit-analyse.md)
(usage-aware routing; §2.4 heterogeen signaal),
[`subscription-pool-dispatch-analyse.md`](./subscription-pool-dispatch-analyse.md) (dode
fase-1a), [`subscriptions.md`](./subscriptions.md) (per-provider quota-pagina, ontworpen
niet gebouwd).

---

## 1. Antwoord in het kort

**Inkantelen. Niet Langfuse.** Drie redenen, in volgorde van gewicht:

1. **Langfuse beantwoordt de gestelde vraag structureel niet.** Langfuse meet
   **kosten in $** per LLM-call. Een Pro/Max-abonnement is **vast tarief** — het
   $-bedrag dat Langfuse zou berekenen is een *contrafeitelijke API-prijs*, niet wat de
   gebruiker betaalt. "Hoeveel van mijn 5h-venster is op?" is een **quota**-vraag, en
   quota is geen concept dat Langfuse kent (§5.2).
2. **Er is geen instrumentatiepunt.** Cockpit doet **zelf nul LLM-calls** — het spawnt
   CLI-processen die hun eigen calls doen. Er is geen SDK-grens waar een Langfuse-client
   tussen past. Alle drie de omwegen (proxy / OTel / batch-ETL) zijn slecht, en de
   proxy-variant is bovendien auth- en ToS-riskant op subscription-auth (§5.2).
3. **De data ligt er al — het probleem is een ontbrekende dimensie, geen ontbrekende
   store.** `UsageService` parst de JSONL-logs al tot tokens+kosten per dag/maand/sessie/
   5h-block, mét `model_breakdowns`. Wat ontbreekt is één **attributie-dimensie**
   (`model → subscription`) en een UI die 'm toont. Dat is een kleine, lokale ingreep —
   geen extra stack (§6).

**En passant blootgelegd: een gekwantificeerde correctheidsbug.** De bestaande
`AnthropicUsageProvider` telt vandaag **MiniMax-tokens mee** in zijn Anthropic-schatting.
Op deze host is dat **36,9% van alle tokens** (§4.2). De routing-drempels die daarop
gebouwd zijn, meten dus het verkeerde. Dat is het scherpste argument vóór inkantelen: de
attributie-dimensie is niet alleen een UI-wens, ze is **nu al stuk in de dispatcher**.

---

## 2. Wat de gebruiker eigenlijk vraagt — twee vragen, niet één

"Inzicht in verbruik per subscription" is ambigu, en de twee lezingen hebben
**verschillende** antwoorden. Ze uit elkaar houden is de kern van deze analyse.

| # | Vraag | Grootheid | Waarom hij ertoe doet | Langfuse? |
|---|---|---|---|---|
| **V1** | "Hoeveel van abonnement X is **op**, en hoeveel heb ik nog?" | **Quota** t.o.v. een plan-limiet (5h-venster, weekly) | Stuurt routing: waar kan ik nú werk heen sturen zonder tegen een limiet te lopen | ❌ kent geen quota |
| **V2** | "Waar **gaat** mijn verbruik naartoe — welke kaart/persona/model vreet tokens?" | **Tokens/kosten** per dimensie, historisch | Stuurt optimalisatie: Sonnet-vs-Opus-defaults, dure persona's | ⚠️ deels, maar zie §5 |

**V1 is de primaire vraag** — hij volgt uit de context (de gebruiker draait Anthropic +
MiniMax parallel en wilde eerder al "flexibeler omspringen met abonnementen", zie
[`subscription-flexibiliteit-analyse.md`](./subscription-flexibiliteit-analyse.md) §1).
V1 is exact wat Langfuse **niet** kan (§5.2).

**V2 is grotendeels al gedekt** door de bestaande usage-feature (`/usage`: daily,
monthly, sessions, blocks, cost-charts, export) — die mist alleen de subscription-as. De
kaart-as van V2 loopt al apart via
`8a2ad986…` "[feature] Per-dispatch tokentelemetrie koppelen aan de usage-feature"
(§8 — geen duplicaat).

---

## 3. Huidige situatie (grounded)

Onderzocht: `backend/app/services/usage_service.py`, `pricing_service.py`,
`services/subscriptions/{base,registry,anthropic,minimax,unknown}.py`,
`backend/app/kanban/{dispatch,subscription_pool}.py`,
`backend/app/api/v1/usage.py`, `frontend/src/features/{usage,subscriptions}/`, en de
feitelijke JSONL-logs onder `~/.claude/projects/`.

### 3.1 Wat er al staat

| Laag | Status | Detail |
|---|---|---|
| `UsageService` | ✅ gebouwd, volwassen | Parst `~/.claude/projects/**/*.jsonl` → `daily` / `monthly` / `sessions` / `blocks` (5h). ~900 regels, caching (5 min TTL, `usage_cache`-tabel). Afgeleid van `ccusage` (MIT). |
| `DailyUsage.model_breakdowns` | ✅ bestaat | `list[ModelBreakdown]` — **per-model** token+kostensplit per dag. Dit is de bestaande haak voor attributie. |
| `SessionBlock.models` | ⚠️ alleen namen | `list[str]` — wél wélke modellen in het block zaten, **géén** per-model tokensplit op block-niveau. Dit is precies wat V1 nodig heeft (§7.1). |
| `PricingService` | ✅ gebouwd | LiteLLM-afgeleide prijstabel, `get_model_pricing()` met exact/prefix/normalized/fuzzy match. |
| `/api/v1/usage` | ✅ 7 endpoints | `summary`, `daily`, `sessions`, `monthly`, `blocks`, `export`, `cache/invalidate`. **Geen enkele kent een `subscription`- of `provider`-parameter.** |
| `UsagePage` | ✅ gebouwd | Summary-cards, daily/monthly/cost-charts, blocks-view, sessions-tabel, export. **Bord-breed, geen subscription-as.** |
| `SubscriptionUsageProvider` (ABC) | ✅ gebouwd | `{beschikbaar, drempel_gebruikt, bron, betrouwbaarheid}`. Eerlijk ontworpen: `betrouwbaarheid` ∈ `exact|schatting|onbekend`, geen cross-vendor normalisatie. |
| `subscriptions/registry.py` | ⚠️ alleen stubs | `register_default_providers()` vult de registry met `UnknownUsageProvider` — honest no-signal. De **echte** `AnthropicUsageProvider` wordt nooit geregistreerd (geen plan-tier-config-pad). |
| `SubscriptionsPage.tsx` | ❌ 16 regels | Alleen `MinimaxCredentialsCard`. **Nul verbruik-weergave.** Dit is het gat dat de kaart benoemt. |
| Langfuse / OTel | ❌ afwezig | `grep -ri langfuse` → 0 hits. Geen OTel-export, geen telemetrie-config. Groenveld. |

### 3.2 Subscription-identiteit

Vastgelegd in [`decisions.md`](./decisions.md) (2026-07-14): een subscription is een
**`{cli, provider}`**-paar — vendor-divers; same-vendor multi-account is NO-GO. De
concrete pool vandaag: `claude-code:anthropic`, `claude-code:bedrock`,
`claude-code:minimax`. Elk paar = eigen auth + eigen quota-venster.

---

## 4. De kern: de attributie-dimensie ontbreekt (en dat is nú al stuk)

### 4.1 Het mechanisme

MiniMax draait **via dezelfde `claude`-CLI** als Anthropic — alleen met
`ANTHROPIC_BASE_URL` omgezet (`provider_env.py`). Gevolg: een MiniMax-sessie schrijft naar
**dezelfde** `~/.claude/projects/**/*.jsonl`-boom als een Anthropic-sessie. De JSONL kent
geen provider-veld; het enige onderscheidende signaal is `message.model`.

`UsageService.get_block_usage()` heeft **geen model- of provider-filter** — het somt alle
entries op:

```python
# usage_service.py — get_block_usage(): geen enkele provider/model-filter
entries = await self.get_all_usage_entries(project_path)
blocks  = await self.identify_session_blocks(entries)
```

`AnthropicUsageProvider.get_usage()` consumeert dat rechtstreeks en deelt door de
Anthropic-plan-tier:

```python
# subscriptions/anthropic.py
blocks       = await self._usage_service.get_block_usage(active=True)
active_block = blocks.active_block
total_tokens = (active_block.input_tokens + active_block.output_tokens
                + active_block.cache_creation_tokens + active_block.cache_read_tokens)
drempel_gebruikt = total_tokens / self._plan_tier_limit_tokens   # ← MiniMax zit hierin
```

**`total_tokens` bevat MiniMax-tokens.** De "Anthropic-schatting" is opgeblazen met
verbruik dat het Anthropic-abonnement nooit geraakt heeft.

### 4.2 Gekwantificeerd op deze host

Alle `~/.claude/projects/**/*.jsonl`, gesommeerd per `message.model` over
`input + output + cache_creation + cache_read`:

| Model | Berichten | Tokens | Aandeel |
|---|---:|---:|---:|
| `claude-sonnet-5` | 24.347 | 3.404.785.169 | 43,7% |
| **`MiniMax-M3`** | **32.845** | **2.876.848.758** | **36,9%** |
| `claude-opus-4-8` | 15.441 | 1.248.429.964 | 16,0% |
| `claude-sonnet-4-6` | 3.277 | 228.189.979 | 2,9% |
| `claude-haiku-4-5-20251001` | 947 | 32.386.002 | 0,4% |
| `<synthetic>` | 218 | 0 | 0,0% |
| **Totaal** | **77.075** | **7.790.639.872** | |

**36,9% van alle getelde tokens is MiniMax** en wordt vandaag aan Anthropic toegerekend.
Dit is een historisch totaal, geen momentopname van één 5h-block — de fout in een concreet
actief block hangt af van de mix op dat moment en kan hoger of lager liggen. De
*structurele* fout staat los van de exacte grootte: de dimensie ontbreekt gewoon.

> **Nuance — waarom de $-views hier grotendeels aan ontsnappen.** `PricingService` kent
> geen `MiniMax-M3`-prijs (geen exact/prefix/normalized/fuzzy hit tegen de
> claude-sleutels) → prijs `None` → **kosten ≈ 0** voor MiniMax-entries. De $-grafieken
> zijn daardoor per ongeluk grotendeels Anthropic-only. De **token**-gebaseerde
> subscription-schatting — precies wat V1 en de routing gebruiken — is dat **niet**. Dat
> is de bug. (Keerzijde: MiniMax-kosten zijn daardoor onzichtbaar, wat een tweede,
> kleinere lacune is.)

### 4.3 Consequentie voor de al-geshipte routing

De pool-router (fase 1b/2) is gebouwd om op `drempel_gebruikt` te sturen. Vandaag is dat
signaal dubbel gebroken: (a) de registry bevat alleen `UnknownUsageProvider`-stubs → altijd
`onbekend` → in de praktijk "kies entry #1" (al vastgesteld in
[`subscription-pool-dispatch-analyse.md`](./subscription-pool-dispatch-analyse.md)); en (b)
zodra iemand (a) fixt door de echte `AnthropicUsageProvider` te registreren, **activeert
hij meteen bug 4.1** en gaat de router sturen op een met ~37% vervuild getal.

**Dat maakt de attributie-fix een harde voorwaarde**, geen nice-to-have: hij moet vóór
(of samen met) de registry-fix landen, anders ruil je "geen signaal" in voor "fout
signaal" — en fout signaal is erger, want het ziet er betrouwbaar uit.

### 4.4 Dekking per subscription — wat is haalbaar?

| Subscription | Bron | Attribueerbaar? | Kwaliteit |
|---|---|---|---|
| `claude-code:anthropic` | JSONL, `model` matcht `claude-*` | ✅ ja | `schatting` — plan-limiet is niet gepubliceerd; weekly al helemaal niet |
| `claude-code:minimax` | JSONL, `model` matcht `MiniMax-*` | ✅ ja | `schatting` lokaal; potentieel `exact` via MiniMax' remote API |
| `claude-code:bedrock` | JSONL, model-id ambigu | ⚠️ **te verifiëren** | Bedrock is pay-per-token — quota is er niet eens; kosten wél |
| `codex` / `copilot` / `open-code` | eigen logs, buiten `~/.claude` | ❌ nee | `codex_usage_context_service` zegt zelf: "not a stable usage source". Eerlijk leeg laten |

**Aanname die geverifieerd moet worden (§7.1):** dat Bedrock-entries een onderscheidbaar
model-id dragen (verwacht: `us.anthropic.claude-…`-prefix vs. kaal `claude-…` voor
first-party). Er is op deze host **geen Bedrock-verkeer**, dus dit is niet empirisch
bevestigd. Als de id's identiek blijken, is Anthropic-vs-Bedrock **niet** uit de JSONL te
scheiden en is een tweede signaal nodig (bv. de dispatcher die de gekozen provider per
sessie wegschrijft). Bouw de mapping daarom achter één functie, niet verspreid.

---

## 5. Langfuse-evaluatie

### 5.1 Wat Langfuse is en goed doet

Open-source LLM-observability. Datamodel: **traces** → **observations/generations**
(prompt, completion, model, tokentelling, latency, kosten). Instrumentatie via SDK
(Python/JS-decorators), framework-integraties (LangChain, OpenAI-SDK-wrapper, LiteLLM) of
een OTel-compatibel ingest-endpoint. Sterk in: prompt-versiebeheer, evals, per-trace
drill-down, kostenattributie per user/feature/tenant.

Het is een goed product. De vraag is niet of het goed is — het is of het **hier** past.

### 5.2 Waarom het hier niet past

**(a) Het meet de verkeerde grootheid.** Langfuse rekent kosten uit als
`tokens × modelprijs`. Bij een **vast-tarief-abonnement** (Pro/Max) is dat getal een
contrafeitelijke API-prijs — interessant als "wat had dit gekost via de API", maar het is
**niet het verbruik van het abonnement**. De limiet die de gebruiker raakt is een
**quota-venster** (5h-rate, weekly), en dat venster:

- is **niet gepubliceerd** door Anthropic voor Pro/Max;
- is **geen** functie van $;
- is **per-abonnement verschillend** van vorm (MiniMax handhaaft iets anders onder een
  andere naam).

Langfuse heeft geen quota-primitief, geen reset-venster en geen plan-tier. Zelfs met
perfecte data erin **kan het V1 niet beantwoorden**. Dat is geen integratie-detail dat je
oplost — het is een modelmismatch.

**(b) Er is geen plek om het in te pluggen.** Cockpit roept geen LLM aan. Het spawnt
`claude` / `codex` als subprocessen; die praten rechtstreeks met hun vendor. Er is geen
functie waar een `@observe`-decorator omheen kan. De drie omwegen:

| Route | Werkt het? | Oordeel |
|---|---|---|
| **Proxy** — `ANTHROPIC_BASE_URL` → LiteLLM/gateway → Langfuse | Alleen voor **API-key**-auth (MiniMax, Bedrock). Voor het **Anthropic-abonnement** — de subscription waar de vraag over gáát — betekent dit subscription-OAuth-verkeer door een zelfgebouwde MITM duwen. | ❌ **Afgewezen.** Auth- en ToS-risico op precies het abonnement dat we willen meten. De moeite niet waard voor data die 20 cm verderop in een JSONL ligt. |
| **OTel** — Claude Code's eigen telemetrie → collector → Langfuse | Claude Code kan OTel exporteren (`CLAUDE_CODE_ENABLE_TELEMETRY`), maar zendt **metrics** (counters als `claude_code.token.usage`), terwijl Langfuse's model **traces/spans** is. Metrics ≠ traces. | ⚠️ **Mismatch** (te verifiëren, §7.1). Als je toch OTel-metrics hebt, is de natuurlijke afnemer Prometheus/Grafana — **niet** Langfuse. |
| **Batch-ETL** — JSONL → Langfuse-SDK | Technisch prima. Maar: je schrijft een ETL om data die je **al lokaal hebt** naar een externe store te duwen, om 'm daarna terug te queryen — en je krijgt er V1 nog steeds niet mee (zie (a)). | ❌ Rondpompen zonder opbrengst. |

**(c) De operationele prijs is niet klein.** Self-hosted Langfuse (v3) is geen container:
het vraagt Postgres + ClickHouse + Redis + een S3-compatibele blobstore. Cockpit is
vandaag **één SQLite-bestand zonder migratiesysteem**
(`backend/claude_registry.db`, `create_all`). Dat is een orde-van-grootte-sprong in
operationele complexiteit — voor een vraag die 'm niet nodig heeft. Langfuse Cloud vermijdt
de ops, maar stuurt dan wél sessie-metadata van een lokaal dev-platform naar een derde
partij.

**(d) Het dupliceert wat er al staat.** `UsageService` + `PricingService` + `/usage` +
`UsagePage` dekken V2 al grotendeels. Langfuse zou die vervangen — niet aanvullen — en de
migratiekost betaal je bovenop de ops-kost.

### 5.3 Wanneer Langfuse **wél** zou lonen (bewaard, niet aangenomen)

Niet "nooit" — de trigger is een **andere** situatie dan die van vandaag. Langfuse wordt
interessant zodra Cockpit **zelf** LLM-calls doet in plaats van CLI's te spawnen. Concrete
triggers:

- Cockpit krijgt een eigen in-proces agent-loop / SDK-pad (dan is er wél een
  instrumentatiegrens);
- er komt echte **pay-per-token**-API-uitgave die per feature/tenant verantwoord moet
  worden (dan is $-attributie de juiste grootheid);
- er is behoefte aan **prompt-versiebeheer + evals** — daar is geen eigen equivalent voor
  en die functionaliteit nabouwen is dom.

Tot dan: **NO-GO.** Bewaard als conditionele spike (§8, kaart #4) i.p.v. stilzwijgend
begraven — zodat een volgende sessie de vraag niet opnieuw uitzoekt maar de trigger leest.

---

## 6. Aanbeveling — inkantelen, gefaseerd

De ingreep is klein omdat de infrastructuur er is. Er ontbreekt **één dimensie** en **één
scherm**.

```
Fase 1  Attributie-dimensie          model → subscription, in UsageService
        (fixt tegelijk bug §4.1)      ← enabler, alles hangt hieraan
           │
           ├── Fase 2  Verbruik-UI op de Subscriptions-pagina   (V1, de gestelde vraag)
           │
           └── Fase 3  Echte AnthropicUsageProvider registreren  (routing werkt écht)
                       — mag pas ná fase 1, anders fout signaal (§4.3)
```

**Fase 1 — attributie-dimensie (de enabler).** Eén `model → subscription_id`-mapping,
achter één functie. Prefix-gebaseerd, niet exact-match: `provider_env.py` declareert
`MiniMax-M3[1m]`, de JSONL bevat `MiniMax-M3` — exact-match zou hier stil falen. Onbekende
modellen worden **niet geraden**: die krijgen `unknown` en worden zichtbaar apart getoond
(dezelfde eerlijkheidslijn als `betrouwbaarheid`). Daarna: een `subscription`-filter op de
usage-aggregaties, en `AnthropicUsageProvider` die alleen z'n eigen tokens telt.

**Fase 2 — de UI die de gebruiker vroeg.** Eén rij per subscription op
`SubscriptionsPage`: verbruikt / limiet / venster-reset / `betrouwbaarheid`-label. De
bestaande eerlijkheidsregels uit [`subscriptions.md`](./subscriptions.md) blijven staan:
per-provider weergave, geen gefakete cross-vendor equivalentie, eerlijk lege staat waar
geen signaal is (Codex/Copilot).

**Fase 3 — routing op een echt signaal.** Pas ná fase 1 (§4.3).

**Fase 4 (optioneel, niet aanbevolen om nu te doen)** — historie. De JSONL is de enige
store; Claude Code kuist die zelf op (`cleanupPeriodDays`, standaard 30 dagen), dus
"verbruik per subscription over 6 maanden" bestaat niet en kan niet retroactief. Als dat
ooit gewenst is, is een periodieke snapshot naar de bestaande SQLite genoeg — een
tijdreeks-DB is overkill. Bewust **geen kaart**: speculatief tot iemand het mist.

---

## 7. Beperkingen & aannames (eerlijkheid boven volledigheid)

### 7.1 Te verifiëren vóór/tijdens bouw

1. **Bedrock-model-id's.** Aangenomen: onderscheidbaar via `us.anthropic.`-prefix. **Niet
   empirisch bevestigd** — geen Bedrock-verkeer op deze host. Blijken ze identiek aan
   first-party, dan is Anthropic-vs-Bedrock niet uit de JSONL te scheiden en is een tweede
   signaal nodig (dispatcher schrijft gekozen provider per sessie weg). Daarom: mapping
   achter één functie.
2. **Langfuse + OTel-metrics.** De claim "Langfuse ingest traces, niet metrics" is
   load-bearing voor §5.2(b) maar niet in deze sessie geverifieerd tegen de actuele
   Langfuse-docs. Hij verandert de **eindconclusie niet** — §5.2(a) (verkeerde grootheid)
   staat op zichzelf en is dodelijk genoeg. Wie de OTel-route toch wil, verifieert dit
   eerst.
3. **Claude Code's OTel-metriek-namen/attributen** (`claude_code.token.usage`, en of er een
   model/provider-attribuut op zit) zijn versie-afhankelijk. Alleen relevant als iemand de
   Prometheus/Grafana-route wil — dat is een alternatief voor V2, geen antwoord op V1.

### 7.2 Wat inkantelen *niet* oplost

- **De Anthropic-limiet blijft ongepubliceerd.** Het beste dat we kunnen leveren is een
  `schatting` tegen een door de gebruiker gekozen plan-tier. Nooit `exact`. Dit is een
  eigenschap van de wereld, geen tekortkoming van het ontwerp — en de bestaande
  `betrouwbaarheid`-enum benoemt het al eerlijk. **Langfuse lost dit óók niet op** (het
  weet de limiet net zo min).
- **Codex/Copilot/OpenCode blijven leeg.** Geen bruikbare bron. Eerlijk leeg > verzonnen
  vol.
- **Cache-tokens vertroebelen elke ratio.** Cache-reads tellen mee in de token-som maar
  niet 1-op-1 in de limiet die de vendor handhaaft. De schatting is en blijft grof.

---

## 8. Vervolgkaarten

Aangemaakt op Backlog in deze sessie (leaf-spike-clausule). De DAG:

```
#1 attributie-dimensie ──┬── #2 Subscriptions-verbruik-UI
   d160d13f…             │      9bce091a…
                         └── #3 echte AnthropicUsageProvider registreren
                                d404a11f…
```

1. **`d160d13f…` — `model → subscription` attributie-dimensie in UsageService** (enabler;
   fixt §4.1).
2. **`9bce091a…` — Verbruik-per-subscription op de Subscriptions-pagina** (V1 — de
   gestelde vraag). Hangt af van #1.
3. **`d404a11f…` — Echte `AnthropicUsageProvider` registreren i.p.v. de `Unknown`-stub.**
   Hangt af van #1 — vóór #1 zou dit de router op een ~37% vervuild getal laten sturen
   (§4.3).

**Bewust géén kaart:**

- **De Langfuse-heropening.** Een conditionele spike waarvan de trigger (§5.3) niet
  gevuurd heeft, heeft vandaag geen zinvolle acceptatiecriteria en zou als
  niet-dispatchbare ruis in het Backlog blijven staan. Het mechanisme dat heropening
  moet voorkomen is de register-regel in [`decisions.md`](./decisions.md) + §5.3 van dit
  doc — dat is precies waarvoor het register bestaat ("kijk hier vóór je een
  productbeslissing heropent"). Een kaart voegt daar niets aan toe.
- **Fase 4 (historie/tijdreeks)** — speculatief tot iemand het mist (§6).
- **MiniMax-prijs in `PricingService`** — reëel gat (§4.2-nuance) maar het is V2-kosten,
  niet V1-quota; los en klein genoeg om op te pikken wanneer iemand MiniMax-kosten mist.

**Geen duplicaat van `8a2ad986…`** ("[feature] Per-dispatch tokentelemetrie koppelen aan de
usage-feature"): dat is de **kaart**-as van V2 ("welke kaart verbruikte wat"), deze
analyse is de **subscription**-as van V1 ("hoeveel van abonnement X is op"). Ze delen wél
het substraat — beide hangen een dimensie aan dezelfde JSONL-parser — dus #1 is een
gedeelde enabler. Op `8a2ad986…` is een cross-link-comment geplaatst.

## 9. Zie ook

- [`subscription-flexibiliteit-analyse.md`](./subscription-flexibiliteit-analyse.md) — §2.4
  heterogeen signaal, §3 `{cli, provider}`-identiteit.
- [`subscription-pool-dispatch-analyse.md`](./subscription-pool-dispatch-analyse.md) — de
  dode fase-1a die #3 adresseert.
- [`subscriptions.md`](./subscriptions.md) — de per-provider quota-pagina die #2 invult.
- [`decisions.md`](./decisions.md) — register-regel voor deze beslissing.
