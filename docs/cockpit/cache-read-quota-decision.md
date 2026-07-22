---
title: "Telt cache_read mee in het Claude-abonnementsquotum? — gecontroleerde meting"
type: decision
status: decided
---

# Telt `cache_read` mee in het Claude-abonnementsquotum?

**Datum:** 2026-07-21
**Status:** besloten
**Uitkomst:** ✅ **Scenario B: `cache_read` telt NIET mee** — gemeten, niet geïnfereerd.
**Kaart:** `[spike] Beslecht met een meting: telt cache_read mee in het
Claude-abonnementsquotum?` (`97e623f9b8d14466a31ebe502e401950`), kind van
`token-saver-mechanismen-decision.md`'s decompositie (parent `3abcd501…`).

## TL;DR

`cache_read_input_tokens` telt **niet** (of verwaarloosbaar) mee in het
Claude-abonnements 5h-quotum. Gemeten met een gecontroleerde injectie-meting op
dit echte abonnement (Pro): een effectief gewicht van **w = 0,014** per
cache_read-token t.o.v. een ongecachte input-token — statistisch
ononderscheidbaar van 0. Het sterkste bewijs heeft geen model nodig: **twee
intervallen bewogen allebei exact 4 procentpunt terwijl hun cache_read
1,8× verschilde** (2,05 M vs. 3,69 M) en hun ongecachte volume vrijwel gelijk was.

**Gevolg voor het token-saver-spoor:** **Scenario B** uit
[`token-saver-mechanismen-decision.md`](./token-saver-mechanismen-decision.md)
§2.3 is de werkelijkheid. RTK-winst ≈ **1,6%**, Caveman-winst ≈ **6,7%**.
**Caveman (output-reductie) is dus het sterkste mechanisme**, niet RTK. De
register-inferentie bij [`token-saver-meet-harnas.md`](./token-saver-meet-harnas.md)
(2026-07-21, "telt waarschijnlijk niet mee — best available inference, not
authoritative") wordt hiermee **bevestigd én geüpgraded van inferentie naar
meting**. De sterkste tegen-evidentie —
[claude-code#24147](https://github.com/anthropics/claude-code/issues/24147)
("cache reads = 99,93% of usage quota") — beschrijft **token-boekhouding**
(`ccusage`-achtige tellers die cache_read meesommeren), niet de werkelijke
quotum-consumptie; dit onderscheid is precies wat de meting isoleert.

## 1. Waarom een meting en niet nóg een webresearch-ronde

De kaart eiste expliciet een **gecontroleerde meting**, geen webresearch — de
evidentie op het web was al uitgeput en tegenstrijdig (§2 van de
mechanismen-decision). De vraag is een pure **observatie-vraag**: giet een bekend,
sterk gedifferentieerd token-profiel het systeem in en lees af wat de
quotum-teller doet. Dat is precies wat hier gebeurt.

## 2. Het autoritatieve signaal — en waarom Cockpit's eigen signaal het niet is

De teller is Anthropic's eigen OAuth-usage-endpoint:

```
GET https://api.anthropic.com/api/oauth/usage
Authorization: Bearer <claudeAiOauth.accessToken uit ~/.claude/.credentials.json>
anthropic-beta: oauth-2025-04-20
```

Het geeft `five_hour.utilization` (0–100) — **exact de teller die `/usage` in een
Claude Code-sessie toont**, server-side berekend door Anthropic, met een
`resets_at`-timestamp voor het rollende 5h-venster.

> **Cockpit's `subscription_pool`-signaal kan deze vraag NIET beantwoorden.**
> De kaart noemde het als alternatief, maar `AnthropicUsageProvider`
> (`backend/app/services/subscriptions/anthropic.py:126-131`) telt zélf
> `cache_read_tokens` bij het totaal op vóór het door de plan-tier-limiet deelt.
> Dat signaal *veronderstelt* dus Scenario A — het gebruiken om A vs. B te
> beslechten is circulair. Alleen Anthropic's server-side teller is neutraal.
> **Follow-up hieruit:** als Scenario B klopt, meet die provider het verbruik
> structureel te hoog (§6).

## 3. Meetopzet

**Kernidee.** Utilization stijgt met verbruik. Als we over een tijdsinterval het
*exacte* token-verbruik per bucket kennen (uit de JSONL-transcripts) én de
utilization-sprong meten, dan kunnen we voor elk kandidaat-boekhoudmodel
uitrekenen wat het *had moeten* voorspellen. Het model dat klopt over
intervallen met **sterk verschillende cache_read-aandelen** is het echte.

**Drie kandidaat-modellen** (gewicht per token, relatief aan één ongecachte
input-token; de niet-cache_read-gewichten liggen vast op de geverifieerde
prijs-ratio's uit §4):

| Model | input | cache_creation | output | cache_read | betekenis |
|---|---|---|---|---|---|
| **A** | 1 | 1 | 1 | **1** | cache_read telt vol mee |
| **C** | 1 | 2 | 5 | **0,1** | kosten-gewogen (10%-prijs) |
| **B** | 1 | 2 | 5 | **0** | cache_read is gratis |

**Twee databronnen:**
- **Utilization-trace** — `five_hour.utilization` elke ~1 min bemonsterd
  (`measure-cache-read-quota.sh sample`).
- **Token-ledger** — per assistant-turn de vier buckets uit
  `~/.claude/projects/**/*.jsonl`, gefilterd op `model` startend met `claude`
  (een concurrente **MiniMax-M3**-workload op dezelfde box draaide óók in de
  logs — die raakt het Anthropic-quotum niet en is uitgefilterd), gededupt op
  `(message.id, requestId)` tegen resume/compact-dubbels.

**Gecontroleerde injectie.** De achtergrond-workload (5 concurrente sessies) zat
constant op ~90–97% cache_read-aandeel — te weinig contrast om de modellen te
scheiden. Daarom twee injectie-armen met tegengestelde skew:
- **Lage-cache_read-arm** — losse `claude -p`-calls met een uniek geprefixte
  ~60 K-token payload: hoge `cache_creation`, minimale `cache_read` (7 K).
- **Hoge-cache_read-arm** — `claude -p --resume <sid>` herhaald: een hervatte
  sessie herleest de volledige gecachte conversatie-prefix, dus **~104 K
  cache_read per call bij ~15 non-cache_read-tokens** (ratio 666:1 over 26 calls).

## 4. Geverifieerde prijstabel (kalibratie-anker)

De niet-cache_read-gewichten zijn geen aanname: Claude Code rapporteert per
`-p`-run een `costUSD` in `modelUsage`. Uit één burst (`cc=96.435, cr=7.293,
in=2, out=4`) reconstrueert de tabel de gerapporteerde `$0,9681065` **tot op 7
cijfers** (`scripts/measure-cache-read-quota.sh verify-pricing`):

| bucket | prijs (Opus 4.8) | ratio t.o.v. input |
|---|---|---|
| input | $5 / M | 1 |
| output | $25 / M | 5 |
| cache_creation (1h TTL) | $10 / M | 2 |
| cache_read | $0,50 / M | **0,1** |

Dit levert model **C** (kosten-gewogen) zijn gewichten en fixeert de andere drie
buckets in alle modellen, zodat cache_read de enige vrije vormparameter is.

## 5. Resultaat

### 5.1 De vijf intervallen

Alle intervallen ≥ 60 s (kortere zijn onbetrouwbaar door de lag tussen
consumptie, JSONL-write en server-teller). `nonCR(w)` = prijs-gewogen
niet-cache_read-volume (`input + 2·cache_creation + 5·output`):

| interval (UTC) | Δutil | nonCR(w) | cache_read | CR-aandeel |
|---|---:|---:|---:|---:|
| 20:37:36→20:41:36 | 10 pp | 313.885 | 2.968.688 | 90,4% |
| 20:41:36→20:47:14 | 17 pp | 773.218 | 5.518.066 | 87,7% |
| 20:47:14→20:51:13 | 12 pp | 556.089 | 1.396.276 | 71,5% |
| 20:51:27→20:54:10 |  4 pp | 168.450 | 2.049.435 | 92,4% |
| 20:54:10→20:56:01 |  4 pp | 177.891 | 3.690.337 | 95,4% |

### 5.2 De model-fit

Kleinste-kwadraten over de vijf intervallen, cache_read-gewicht `w` als vrije
parameter:

| `w` | SSE | interpretatie |
|---|---:|---|
| **0,00** | **8,97** | cache_read is gratis |
| 0,10 | 23,11 | kosten-gewogen (10%) |
| 0,50 | 72,69 | half gewicht |
| 1,00 | 89,98 | telt als gewone input |

**Beste fit: `w = 0,014`** (SSE 7,86) — statistisch ononderscheidbaar van nul.
De SSE loopt **monotoon en steil** op naarmate `w` groeit: model A (vol
meetellen) is met SSE 89,98 een orde slechter dan B. Zelfs de kosten-gewogen
tussenpositie C (SSE 23,11) is ~2,6× slechter dan B.

### 5.3 Het model-vrije bewijs (de sterkste regel)

De laatste twee intervallen zijn een natuurlijk gecontroleerd experiment:

| | interval 4 | interval 5 | verschil |
|---|---:|---:|---|
| Δutil | **4 pp** | **4 pp** | **identiek** |
| nonCR(w) | 168.450 | 177.891 | +5,6% |
| cache_read | 2.049.435 | 3.690.337 | **+80% (1,8×)** |

Interval 5 verbruikte **1,64 miljoen méér cache_read-tokens** dan interval 4, bij
vrijwel gelijk ongecacht volume — en de utilization bewoog **exact evenveel**.
Als cache_read meetelde (model A), had interval 5 ~7,8 pp moeten stijgen i.p.v.
4. Dit heeft geen regressie of kalibratie nodig: het weerlegt A direct.

## 6. Antwoord op de kaart-eisen

- **Binair antwoord met bewijs** ✅ — **Scenario B**: `cache_read` telt niet mee.
  Bewijs: §5.2 (model-fit `w≈0`) + §5.3 (model-vrije natuurlijke control).
- **`token-saver-mechanismen-decision.md` §2 bijgewerkt** ✅ — Scenario B is de
  werkelijkheid; Caveman > RTK op headroom-winst.
- **Tegenstrijdige register-regel gecorrigeerd** ✅ — de
  `token-saver-meet-harnas.md`-inferentie is nu **gemeten bevestigd**; het
  register krijgt een regel voor deze kaart.
- **Nieuwe follow-up (buiten scope van deze spike):**
  `AnthropicUsageProvider.get_usage` telt `cache_read_tokens` mee in het totaal
  waartegen `drempel_gebruikt` wordt berekend. Als cache_read ~96% van het
  verbruik is en géén quotum kost, overschat de provider het verbruik dramatisch
  — de drempel-gebaseerde spillover in `subscription_pool` pauzeert abonnementen
  veel te vroeg. Dit is een aparte Backlog-kaart (§8).

## 7. Reproductie

```bash
# 1. Verifieer de prijstabel (offline, deterministisch)
bash scripts/measure-cache-read-quota.sh verify-pricing

# 2. Bemonster utilization (≥60s interval — het endpoint heeft een eigen rate limit)
bash scripts/measure-cache-read-quota.sh sample /tmp/trace.ndjson 90 40 &

# 3. Injecteer een hoge-cache_read-arm via resume (session_id uit een eerdere -p-run)
bash scripts/measure-cache-read-quota.sh amplify <session_id> 20

# 4. Fit het cache_read-gewicht over de intervallen
bash scripts/measure-cache-read-quota.sh fit /tmp/trace.ndjson
# => "BEST FIT w = 0.0xx  => cache_read does NOT count"

# Bash-tests (offline):
bash scripts/test_measure_cache_read_quota.sh
```

**Rauwe utilization-trace van deze meting** (2026-07-21, Pro-abonnement, Opus 4.8):

```
20:37:36Z 44%   20:41:36Z 54%   20:47:14Z 71%   20:51:13Z 83%
20:51:27Z 86%   20:54:10Z 90%   20:56:01Z 94%   20:56:13Z 94%
```

De token-ledger komt uit de lokale JSONL-transcripts (`~/.claude/projects/`), die
niet in de repo staan; de per-interval-tabel in §5.1 is daarom het auditeerbare
record van de token-kant.

## 8. Beperkingen & heropen-triggers

- **Eén abonnement, één tier (Pro), één sessie-venster.** De boekhoud-*regel*
  (wél/niet meetellen) is een platform-eigenschap en zeer waarschijnlijk
  tier-invariant, maar de meting zelf is N=1 op Pro. Een Max-account dat het
  tegendeel meet → heropenen.
- **Concurrente-workload-confound.** De achtergrond-sessies zijn geen fout maar
  gratis signaal (ze varieerden het profiel); wel maakt hun lag de sub-minuut
  intervallen onbruikbaar (daarom de ≥60s-filter). De model-vrije control (§5.3)
  is immuun voor dit confound.
- **Anthropic wijzigt de boekhouding** (bv. cache_read gaat meetellen in een
  toekomstige plan-structuur) → de meting herhalen; `resets_at`/`utilization`
  blijven de bron.
- **Heropenen** ook zodra de `AnthropicUsageProvider`-overschatting (§6) wordt
  aangepakt — dan verschuift wat Cockpit zelf als "drempel" leest.
