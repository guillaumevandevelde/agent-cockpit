---
title: "LiteLLM-pilot — gemeten uitkomst van de sidecar-route"
type: analysis
status: decided
---

# LiteLLM-pilot — gemeten uitkomst van de sidecar-route

**Datum:** 2026-07-27
**Kaart:** `bbfcb365…` "[spike] LiteLLM-pilot: handmatige CC-sessie via de proxy, zonder Cockpit-wijziging"
**Parent:** `27cdc2bd…` — [`9router-integratie-analyse.md`](./9router-integratie-analyse.md) §11
**Gemeten met:** LiteLLM `1.93.0` (pip, `litellm[proxy]`), `claude`-CLI, upstream MiniMax
via zijn OpenAI-compatibele API. Alles loopback, geen Cockpit-code gewijzigd.

---

## 0. Uitkomst in één alinea

**De sidecar-route gaat door — de vertaling werkt.** Format-translatie is gemeten en
klopt: een volledige `claude`-sessie met Read + Edit + Bash liep end-to-end over de
proxy naar een OpenAI-formaat upstream, en een moedwillig uitgelokte mid-sessie
rate-limit werd door de router opgevangen zonder dat de sessie omviel. Dat is precies
de voorwaarde die §11.3 stelde, en die is nu gehaald in plaats van aangenomen.
**Maar de economische aanname eronder klopt niet.** Door de proxy is `cache_read`
structureel **0** — de prompt-cache is volledig weg, niet gedegradeerd — en dat kost
**14× meer belastbare input-tokens per beurt** dan dezelfde sessie rechtstreeks op
Anthropic. De sidecar is daarmee geen goedkopere manier om Cockpit-sessies te draaien;
hij is een manier om *andere backends* te bereiken voor sessies waar die keuze bewust
is. Het failover-argument uit §4 — "houd een stervende sessie in leven" — overleeft
deze meting alleen in afgezwakte vorm: de sessie blijft leven, maar valt terug naar
een cache-loze wereld waar elke resterende beurt de volle prompt opnieuw betaalt.

---

## 1. Opzet en afwijking van de kaart

De kaart vroeg één API-key-provider uit `{Groq, Cerebras, DeepSeek, NVIDIA NIM,
OpenRouter}`. **Gebruikt is MiniMax** — de enige API-key-provider waarvoor op deze host
een key van de gebruiker aanwezig is (`MINIMAX_API_KEY` in `backend/.env`, gelezen via
`settings.minimax_api_key`, zie `backend/app/services/agentic_cli/endpoints.py:285-292`).
Voor de vijf genoemde providers is geen account/key beschikbaar en een agent-sessie kan
er geen aanmaken.

**Waarom dat de meting niet ondergraaft.** De vraag van de kaart is niet
"werkt provider X", maar "overleeft agentic verkeer de OpenAI ↔ Anthropic
format-translatie". MiniMax is voor die vraag een geldige stand-in: er is bewust
tegen zijn **OpenAI-compatibele** `/v1`-endpoint gemeten, niet tegen zijn
Anthropic-native endpoint — precies de vertaalslag die Groq/Cerebras/NIM/OpenRouter
ook nodig hebben. De provider-specifieke kant (welk endpoint LiteLLM per provider
aanroept) is apart en wél voor alle vijf gemeten, zie §6.

**Kosten.** De pilot heeft credits van de gebruiker verbruikt op zijn eigen
MiniMax-account: 5 proxy-sessies + ~15 losse requests, samen ruim onder 250k
input-tokens op een goedkoop model. Er is geen subscription-OAuth gebruikt; §2.2 van
het analysedoc is onverkort gerespecteerd.

### Config (de volledige gebruikte `config.yaml`)

```yaml
model_list:
  - model_name: pilot-model
    litellm_params:
      model: openai/MiniMax-M2.1
      api_base: https://api.minimax.io/v1
      api_key: os.environ/MINIMAX_API_KEY
    model_info: {id: upstream-primary}
  - model_name: pilot-fallback
    litellm_params:
      model: openai/MiniMax-M2.5
      api_base: https://api.minimax.io/v1
      api_key: os.environ/MINIMAX_API_KEY
    model_info: {id: upstream-fallback}

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY

litellm_settings:
  drop_params: true

router_settings:
  fallbacks: [{"pilot-model": ["pilot-fallback"]}]
  num_retries: 1
```

Gestart als `litellm --host 127.0.0.1 --port 4000 --config config.yaml`.

---

## 2. Hardening — alle vijf eigenschappen groen

`scripts/check-litellm-hardening.sh --url http://127.0.0.1:4000 --config-yaml <pad> --strict`
tegen de draaiende proxy, exit 0:

```
PASS reachability: GET http://127.0.0.1:4000/health/liveliness → 200
PASS binding: port 4000 is bound only to loopback
PASS auth: unauthenticated POST /v1/messages → 401 (master_key enforced)
PASS no prompt-mutation: no success_callback / failure_callback / guardrails / plugins / transform scripts attached.
PASS no telemetry/external-sync: no external callbacks / alerting / non-local database_url.
PASS credentials: no plaintext api_key values in model_list (os.environ/* or credential_list only).
```

Dit is de eerste keer dat die check tegen een échte LiteLLM heeft gedraaid; §11 hing
daarop. De check zelf hoefde niet aangepast — hij deed wat hij beloofde.

### 2.1 Installatie-val: `litellm[proxy]` alleen levert HTTP 500 op elke auth-afwijzing

Bij de eerste run **faalde** de auth-check, met een misleidend signaal:

```
FAIL auth: unauthenticated POST /v1/messages → 500 — proxy accepted a request with no Authorization header.
```

De check las de statuscode goed; de conclusie in de foutregel ("proxy accepted") was
onjuist. De proxy **wees af** — in de log staat `Exception: No api key passed in.` —
maar LiteLLM's eigen auth-exception-handler crasht daarna bij het classificeren van de
fout:

```
File .../litellm/proxy/auth/auth_exception_handler.py:163 in _handle_authentication_error
File .../litellm/proxy/db/exception_handler.py:46 in is_database_connection_error
ModuleNotFoundError: No module named 'prisma'
```

`pip install 'litellm[proxy]'` trekt `prisma` niet mee. Na `pip install prisma`
(zonder DB, zonder `prisma generate`) werd exact dezelfde request **401** met een nette
`auth_error`-body. Reproductie: start de proxy, `curl -X POST
http://127.0.0.1:4000/v1/messages -d '{...}'` zonder `x-api-key`; met prisma → 401,
zonder → 500.

**Gevolg voor de sidecar-instructie:** de install-set is
`pip install 'litellm[proxy]' prisma`, niet `litellm[proxy]` alleen. Het is
fail-closed in beide gevallen — er gaat geen request naar boven — maar in de
500-variant ziet een operator een servercrash waar een authenticatiefout hoort te
staan, en faalt de hardening-gate op een correct geconfigureerde proxy.

Restant, niet opgelost: een **verkeerde** key geeft `400 {"error":{"message":"No
connected db."}}` in plaats van 401, omdat LiteLLM na een master-key-mismatch
doorvalt naar de virtual-key-lookup in de database. Ook fail-closed, ook een
verkeerde statuscode. Zonder DB is dat het gedrag; met DB niet getest.

✅ **Geïmplementeerd (kaart `1941bb10…`):** de install-set staat nu expliciet in
het `Usage:`-blok van `scripts/check-litellm-hardening.sh` en in §11 van
[`9router-integratie-analyse.md`](./9router-integratie-analyse.md), met één
regel rationale. De auth-check zelf klasseert 4xx/5xx non-401/403 als WARN
in plaats van FAIL — alleen een echte 2xx zonder `Authorization` drukt nog
de tekst "proxy accepted". Tests Tasks 10 (fail-closed 500) en 11 (fail-closed
400 `no_db_connection`) bewijzen de carve-out.

---

## 3. Prompt-integriteit — behavioraal geverifieerd, niet aangenomen

§4.2 is de zwaarste zorg van het analysedoc: een router die `tool_result` comprimeert
of gedragsprompts injecteert degradeert een autonome sessie **stil**. De config-check
(§2) kijkt alleen of er callbacks/guardrails geconfigureerd staan. Dat is niet genoeg —
het bewijst niet dat de code niets doet.

Daarom een **capture-upstream**: een lokale OpenAI-compatibele server die de exacte
body opschrijft die LiteLLM doorstuurt, en een vast antwoord teruggeeft. Doorheen
gestuurd: een Anthropic-format request met markers in elk gevoelig veld — system-prompt,
tool-description, tool-parameter-description, user-content, en een opgevulde
`tool_result` van 422 tekens (het veld dat 9router's RTK juist comprimeert).

| Marker in | Aangekomen bij upstream |
|---|---|
| `system` | ✅ verbatim |
| tool `description` | ✅ verbatim |
| tool parameter `description` | ✅ verbatim |
| user `content` | ✅ verbatim (inclusief de woordenlijst die een compressor zou inkorten) |
| `tool_result` content | ✅ verbatim — **422 tekens verzonden, 422 tekens doorgestuurd** |

Geen compressie, geen injectie, geen herschrijving. **§4.2's faalmodus is bij LiteLLM
in deze configuratie afwezig, en dat is nu gemeten in plaats van aangenomen.** Dit is
het punt waarop LiteLLM zich structureel onderscheidt van 9router: daar staat RTK
*default aan*, hier is er geen equivalent dat aanstaat.

---

## 4. Tool-use door de format-translatie — de hoofdvraag

**Werkt.** Twee volledige `claude`-sessies over de proxy, `--output-format json`,
`is_error: false`, en het bestand op schijf achteraf gecontroleerd.

Protocol-niveau eerst, los van de CLI — een Anthropic `/v1/messages`-request met een
`tools`-array leverde een correct vertaalde tool-call op:

```json
"content": [{"type":"tool_use","id":"call_function_wpv9k6c1ik5w_1",
             "name":"get_weather","input":{"location":"Ghent"}}],
"stop_reason": "tool_use"
```

Daarna end-to-end met de CLI:

```
ANTHROPIC_BASE_URL=http://127.0.0.1:4000 ANTHROPIC_AUTH_TOKEN=<master-key> \
ANTHROPIC_MODEL=pilot-model \
claude -p "Read target.txt, Edit 'pending'→'done', run 'cat target.txt' with Bash, report" \
  --output-format json --dangerously-skip-permissions --model pilot-model
```

Resultaat: `num_turns: 4`, `is_error: false`, en `target.txt` op schijf bevatte daarna
werkelijk `STATUS: done`. Read, Edit en Bash zijn alle drie uitgevoerd en hun
resultaten kwamen correct terug bij het model. Streaming werkt (de CLI streamt; `ttft_ms`
werd gerapporteerd).

**Voorwaarde die de kaart niet noemde maar wel bindend is:** de model-alias moet in
`model_list` staan én via `ANTHROPIC_MODEL` worden meegegeven. De CLI stuurt anders een
`claude-*`-modelnaam die de proxy niet kent.

**Kleine translatie-verliespost:** MiniMax-M2.1 is een reasoning-model; zijn
`reasoning_content` komt **niet** terug als Anthropic `thinking`-block. Het telt wel mee
in `output_tokens`. Bij een korte `max_tokens` levert dat een geldige respons met
`content: []` en `stop_reason: "max_tokens"` — zichtbaar leeg antwoord, geen fout.
Niet blokkerend, wel een verrassing waard voor wie een reasoning-model achter de
sidecar hangt.

---

## 5. Failover — de sessie blijft leven

Opzet zodat de limiet **mid-sessie** valt en niet vooraf: een lokale shim staat als
primaire upstream in `model_list` en stuurt de eerste 2 requests door naar de echte
MiniMax; alles daarna krijgt een echte `HTTP 429` met `rate_limit_exceeded`. De
fallback-entry wijst rechtstreeks naar MiniMax.

```yaml
router_settings:
  fallbacks: [{"pilot-model": ["pilot-fallback"]}]
  num_retries: 0
```

Sessie: 5 stappen (2× Read, Edit, Bash, rapporteren). Shim-log:

```
[flaky] request #1 -> forwarded to real upstream
[flaky] request #2 -> forwarded to real upstream
[flaky] request #3 -> 429 (limit simulated)
[flaky] request #4 -> 429 (limit simulated)
```

CLI-resultaat: `num_turns: 5`, `is_error: false`, correcte eindtekst, en `target.txt`
op schijf werkelijk gewijzigd. **De sessie is over de limiet heen gelopen zonder
onderbreking, zonder retry-zichtbaarheid aan de clientkant.**

Bevestiging per request in de response-headers:

```
x-litellm-model-id: upstream-healthy
x-litellm-model-group: pilot-fallback
x-litellm-attempted-fallbacks: 1
```

Dit is het enige punt waarop de router-route iets levert dat Cockpit vandaag niet
heeft: de bestaande spawn-configurator kiest één upstream vóór het proces start
(`resolve_provider_env` in `backend/app/services/agentic_cli/provider_env.py:183-247`
zet `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` eenmalig) en kan halverwege niet meer
wisselen. Dat voordeel is nu aangetoond en niet langer theoretisch.

---

## 6. Provider-prefix bepaalt het upstream-endpoint — val voor de breedte-eis

Gemeten door elke provider-prefix naar dezelfde capture-upstream te laten wijzen en te
kijken welk pad LiteLLM aanroept:

| `litellm_params.model` | Upstream-endpoint dat LiteLLM aanroept |
|---|---|
| `openai/<model>` | `POST /v1/responses` — de **Responses API** |
| `groq/<model>` | `POST /v1/chat/completions` |
| `cerebras/<model>` | `POST /v1/chat/completions` |
| `deepseek/<model>` | `POST /anthropic/v1/messages` |
| `nvidia_nim/<model>` | `POST /v1/chat/completions` |
| `openrouter/<model>` | `POST /v1/chat/completions` |

De val: de voor de hand liggende config voor "een OpenAI-compatibel endpoint" is
`model: openai/<naam>` + `api_base: https://api.groq.com/openai/v1`. Die combinatie
stuurt naar **`/v1/responses`**, dat Groq, Cerebras en NVIDIA NIM niet implementeren —
resultaat is een 404 die leest als "provider onbereikbaar" in plaats van "verkeerde
prefix". Met de eigen prefix routeert elk van de vier naar `/v1/chat/completions`, wat
ze wél hebben.

**De breedte-eis van §11.3 houdt dus stand**, maar de provider-entry moet de
prefix expliciet configureerbaar maken; een generieke `openai/`-prefix is niet
de veilige default. DeepSeek is bovendien een bijzonder geval: LiteLLM stuurt dat
naar DeepSeek's Anthropic-native endpoint, dus daar is er helemaal geen
format-translatie.

*(De `groq/`-probe gaf HTTP 500 op mijn capture-shim omdat die een kaal
`chat.completion`-object teruggeeft dat Groq's response-transformer niet accepteert —
een artefact van de shim. Het gemeten signaal is het aangeroepen **pad**, en dat kwam
in alle zes gevallen aan.)*

---

## 7. `cache_read` — de bevinding die de businesscase omdraait

**Meet-recept van de kaart** (`claude -p "ok" --output-format json`, verschil in
`input + cache_creation + cache_read`), zelfde methode als
[`per-persona-mcp-allowlist-decision.md` §7](./per-persona-mcp-allowlist-decision.md#7-reproductie):

| Run | `input` | `cache_creation` | `cache_read` | som |
|---|---:|---:|---:|---:|
| Rechtstreeks Anthropic, koude cache | 2 | 8.209 | 7.370 | 15.581 |
| Rechtstreeks Anthropic, 2e run | 2 | 5.306 | 10.307 | 15.615 |
| Rechtstreeks Anthropic, **warme cache** | 2 | 0 | **15.613** | 15.615 |
| **Via LiteLLM-proxy** | **22.162** | **0** | **0** | **22.162** |

En in de sessies mét tool-use, over 4 respectievelijk 5 beurten:
`input_tokens: 89.709 / 90.102`, `cache_creation: 0`, `cache_read: 0`. Ruwweg 22k
verse input **per beurt**, elke beurt opnieuw.

De prompt-cache is niet gedegradeerd maar **volledig afwezig**. Dat is ook logisch:
`cache_control`-breakpoints zijn een Anthropic-concept dat in de vertaling naar
OpenAI-formaat geen doel heeft, en de MiniMax-respons draagt geen cache-velden terug.

**Wat dat kost, in belastbare input-token-equivalenten.** Met Anthropic's
gepubliceerde vermenigvuldigers (`cache_read` = 0,1× input, `cache_write` = 1,25×
input — *dat deel is gepubliceerde tariefstructuur, niet door mij gemeten*):

- Rechtstreeks, warme cache: 2 + 15.613 × 0,1 ≈ **1.563** equivalenten per beurt
- Via de proxy: **22.162** equivalenten per beurt
- → **≈14× meer belastbare input per beurt**

Dit is nadrukkelijk een **token**-vergelijking, geen euro-vergelijking: een sidecar-
provider rekent per token veel minder dan Anthropic, dus 14× meer tokens kan in geld
alsnog goedkoper uitvallen. Wat het wél definitief omzeep helpt, is de redenering "de
sidecar bespaart tokens". Dat doet hij niet — hij vermenigvuldigt ze, en de
besparing moet volledig uit het lagere tarief komen.

**Gevolg voor het failover-argument.** Failover naar een sidecar-upstream is niet
gratis en ook niet goedkoop: op het moment van omschakelen valt de cache weg en betaalt
elke resterende beurt de volle prompt. Bij een sessie die halverwege een limiet raakt
is dat nog steeds beter dan sterven — een verloren sessie kost de hele kaartcontext én
een re-dispatch. Maar het is een noodrem, geen bedrijfsmodel.

`cache_read` vóór en ná de uitgelokte failover: **0 en 0**. Er is niets verloren gegaan
bij het omschakelen, omdat er nooit iets te verliezen was.

---

## 8. `count_tokens` — antwoordt, maar het getal klopt niet

`POST /v1/messages/count_tokens` bestaat en geeft **HTTP 200**. De CLI zal er dus niet
op stukvallen. Het getal is alleen niet bruikbaar:

| Input | `input_tokens` |
|---|---:|
| `"hi"` | 131 |
| `"The quick brown fox jumps over the lazy dog."` | 140 |
| Een langere zin (12 woorden) | 141 |

De *delta's* volgen de inhoud redelijk (9 tokens voor negen woorden), maar er zit een
constante bodem van ~129 tokens onder die er niet hoort. LiteLLM telt hier lokaal, met
een tokenizer en een overhead-aanname die niet bij de daadwerkelijke upstream horen.

Dit is de "stille gedragsverandering" waar de kaart voor waarschuwde, in zijn
onaangenaamste vorm: **geen 404 die opvalt, maar een plausibel ogend fout getal.** Wie
`count_tokens` gebruikt om te beslissen of iets nog in het contextvenster past, krijgt
een systematisch te hoge schatting en compacteert te vroeg. Niet blokkerend voor de
sidecar, wel iets om te weten voordat iemand erop leunt.

---

## 9. Verbruiksattributie per upstream — er is een bruikbare bron

Voor attributie-kaart `390756e6…`: `betrouwbaarheid="onbekend"` is **niet** structureel
het eerlijke antwoord. Er zijn drie bronnen, met verschillende prijskaartjes:

| Bron | Beschikbaar zonder DB? | Wat het geeft |
|---|---|---|
| Response-headers | ✅ ja | `x-litellm-model-id` (exact de upstream uit `model_info.id`), `x-litellm-model-group`, `x-litellm-attempted-fallbacks`, `x-litellm-attempted-retries`, `x-litellm-call-id` |
| Proxy-log | ✅ ja | per request de gekozen deployment + de usage van de respons |
| `GET /spend/logs` | ❌ nee | `500 "Database not connected. Connect a database to your proxy"` |

De headers zijn de sterkste DB-vrije bron: ze benoemen **per request** welke fysieke
upstream heeft geleverd, en of dat via een fallback ging. Dat is precies de granulariteit
die de MiniMax-vermenging (36,9%) destijds miste.

Twee waarschuwingen:

1. **Kosten zijn 0,0 tenzij je zelf prijzen registreert.** Bij het opstarten:
   `register_model: model=openai/MiniMax-M2.1 not in built-in cost map`, en vervolgens
   `x-litellm-response-cost-original: 0.0` op elke respons. Token-*aantallen* kloppen
   wel; bedragen moeten uit `model_info.input_cost_per_token` komen.
2. **De `claude`-CLI geeft die headers niet door.** Zijn eigen
   `total_cost_usd` rekent met Anthropic-tarieven op een niet-Anthropic model —
   de tool-use-sessie rapporteerde `$0,46` voor werk dat op MiniMax een fractie
   daarvan kost. Dat cijfer is **onbruikbaar** en mag niet in een usage-view
   terechtkomen. Attributie moet aan de proxykant gebeuren, niet uit de CLI-output.

---

## 10. Advies

**De sidecar-route gaat door.** De heropen-trigger uit §11.6 — "werkt de vertaling niet
betrouwbaar voor agentic verkeer, dan valt de route terug op Anthropic-native-only" —
is **niet** getriggerd. Tool-use overleeft de translatie, sessies lopen end-to-end,
failover houdt een sessie in leven, prompt-integriteit is byte-exact, en de vijf
hardening-eigenschappen zijn groen tegen een echte proxy. De breedte-eis van §11.3
blijft overeind, mits de provider-prefix expliciet configureerbaar is (§6).

**Met drie correcties op de aannames eronder:**

1. **De sidecar is geen besparing.** `cache_read` is 0 en blijft 0; per beurt gaat er
   ~14× meer belastbare input doorheen dan bij een warme Anthropic-cache. Elke
   businesscase moet volledig steunen op het lagere tarief van de sidecar-provider, niet
   op tokenbesparing. Een sidecar-sessie op een duur model is onvoorwaardelijk duurder
   dan dezelfde sessie rechtstreeks.
2. **Failover is een noodrem, geen strategie.** Hij redt een sessie die anders sterft —
   dat is echte winst, want een dode sessie kost de kaartcontext plus een re-dispatch —
   maar hij verplaatst de sessie naar een cache-loze wereld. "Standaard alles door de
   router" is daarmee expliciet af te raden.
3. **Attributie kan, maar aan de proxykant.** De response-headers zijn de bron; de
   `total_cost_usd` van de CLI is bij een sidecar-model gewoon fout en moet nergens in
   een usage-view landen.

**Concreet voor de provider-entry (kaart K3):** vormgelijk aan de MiniMax-tak zoals §6
van het analysedoc voorschrijft, met drie extra eisen die uit deze meting volgen —
prefix expliciet configureerbaar, `ANTHROPIC_MODEL` verplicht (de alias moet in
`model_list` staan), en de proxy-install inclusief `prisma`.

---

## 11. Reproductie

```bash
python3 -m venv lvenv
./lvenv/bin/pip install 'litellm[proxy]' prisma      # prisma is NIET optioneel — zie §2.1

export MINIMAX_API_KEY=<key>            # of GROQ_API_KEY / CEREBRAS_API_KEY / …
export LITELLM_MASTER_KEY=sk-local-...
./lvenv/bin/litellm --host 127.0.0.1 --port 4000 --config config.yaml   # config: §1

bash scripts/check-litellm-hardening.sh --url http://127.0.0.1:4000 \
     --config-yaml config.yaml --strict                                 # verwacht: exit 0

# tokenmeting (§7) — draai beide in dezelfde map
claude -p "ok" --output-format json                                     # rechtstreeks
ANTHROPIC_BASE_URL=http://127.0.0.1:4000 ANTHROPIC_AUTH_TOKEN=$LITELLM_MASTER_KEY \
  ANTHROPIC_MODEL=pilot-model claude -p "ok" --output-format json --model pilot-model

# tool-use (§4) en failover (§5): zie de shim-scripts beschreven in §3 en §5
```

**Meet-verantwoording.** Alle token- en statuscijfers in dit document komen uit runs op
2026-07-27 tegen LiteLLM `1.93.0` op deze host. Eén getal is *niet* gemeten en als
zodanig gelabeld: de omrekening van tokens naar "belastbare equivalenten" in §7 gebruikt
Anthropic's gepubliceerde cache-vermenigvuldigers (0,1× / 1,25×), geen eigen
prijsmeting. De uitspraak "Groq/Cerebras/NIM implementeren `/v1/responses` niet" is een
gevolgtrekking uit het gemeten routeergedrag van LiteLLM (§6) plus hun publieke
API-oppervlak, niet uit een request tegen die providers zelf — daarvoor ontbrak een key.
