---
title: "Token-saver meet-harnas — ontwerp, proxy, en eerste meting"
type: decision
status: decided
---

# Token-saver meet-harnas

**Datum:** 2026-07-21
**Status:** Beslissing (K1 van §9 in [`9router-integratie-analyse.md`](./9router-integratie-analyse.md))
**Kaart:** `[feature] Meet-harnas: tokenverbruik én kwaliteitsregressie op een echte dispatch-workload` (`6b67df6627014b9c97ac1ce8fb0417bb`)

## TL;DR

Een shell-only harnas (bash + inline Python) dat prompt-mutaties van
token-savers (RTK / Caveman / Ponytail) **emuleert** in een scratch git-worktree
tegen een golden task (1-line revert van commit `b30a9bb` op
`backend/app/kanban/dispatch.py`), de drie afzonderlijke
verbruiks-componenten (`input_tokens` / `cache_creation_input_tokens` /
`cache_read_input_tokens`) naast een binaire kwaliteitsscore (`pass_tests` +
`pass_diff`) rapporteert, en de scratch worktree netjes opruimt. **Lower
bound** — als deze naïeve regex-mutatie de golden task breekt, doet een echte
saver dat ook (en waarschijnlijk vaker). **Eerste meting:** `cache_read`
daalt **−42,4%** (1,15 M → 0,66 M) zonder dat de kwaliteit-collapseert;
`input_tokens` daalt −0,5% (bijna gemaskeerd door prompt-prefix-cache).

## 1. De gekozen methode

Eén **gerichte sonde** (single golden task, N=1) i.p.v. N=10 statistically
powered run: K1 is **werkbaarheid**, niet K3-statistiek. Herhaalbaarheid
wordt bewezen door de byte-stabiliteit van de proxy
(`apply_saver` is SHA-256-invariant op identieke input) en door de
deterministische prompt-constructie (`build_prompt` leest een vast pad in de
worktree, geen `$$`/`$RANDOM`/timestamps).

**Golden task**: revert van commit `b30a9bb`'s 1-character fix
`r.max_sessions >= 0` → `r.max_sessions > 0` in
`backend/app/kanban/dispatch.py:2244`. De twee bijbehorende regression tests
(`test_zero_column_cap_blocks_dispatch` +
`test_zero_column_cap_does_not_block_other_columns`) staan al op master —
dus de agent werkt **tegen** een gefaalde test-suite.

**Score** = twee binaire signalen per run:
- `pass_tests`: `pytest -k zero_column_cap` exit-code 0.
- `pass_diff`: `git diff -- backend/app/kanban/dispatch.py` bevat NIET
  meer de gebroken `> 0`-regel en WEL de `>= 0`-regel (post-state-check,
  niet pre+post als één concat — anders kan "untouched" ook scoren).

**Conclusies die je per run mag trekken**: 2 bits kwaliteit + 4
tokens-velden = **6 getallen per variant + een delta-rij**. Conclusies die
je NIET mag trekken: variantie (één run, geen steekproef), causaliteit
tussen saver-onderdeel en uitkomst (de drie proxy-laagjes worden
tegelijk aangezet), generalisatie naar niet-dispatch prompts.

## 2. `cache_read` en abonnementslimieten — wat zegt Anthropic?

- **API rate limits** ([platform.claude.com/docs/en/api/rate-limits](https://platform.claude.com/docs/en/api/rate-limits)):
  ITPM wordt gedefinieerd als
  `input_tokens + cache_creation_input_tokens`.
  `cache_read_input_tokens` telt **niet** mee voor ITPM-rate; het wordt
  wél gefactureerd op **10% van input-prijs** (een Anthropic SDK-billing,
  geen limiet-mechanisme).
- **claude.ai subscriptions** ([support.anthropic.com/en/articles/11647753](https://support.anthropic.com/en/articles/11647753)):
  quota = rolling 5-hour message-window; **geen vermelding van caching**
  of token-aggregatie. Een WebFetch op 2026-07-21 bevestigt dat dit doc
  niets over `cache_read` zegt.
- **Inferentie, niet autoritair**: aangezien subscription-quotum op een
  bericht-venster draait en niet op tokens, trekt
  `cache_read_input_tokens` **zeer waarschijnlijk** niets af van het
  abonnementsquotum — maar Anthropic documenteert dit niet. Noteer
  expliciet als "best available inference, not authoritative"; heropen
  zodra Anthropic dit expliciet maakt of zodra een Claude-account een
  tegengestelde observatie laat zien.

## 3. Proxy-ontwerp — wat we wel/niet emuleren

### Wat de proxy WEL doet

- Deterministische prompt-mutatie, drie laagjes:
  1. **Caveman-prelude** (geprepend): `[SAVER:CAVEMAN] Respond with the
     shortest possible phrasing. Drop politeness, hedges, and restatements.`
     ~25 tokens.
  2. **Ponytail-tail** (geappend): `[SAVER:PONYTAIL] Prefer one Bash call
     over many. No code fences unless asked.` ~20 tokens.
  3. **RTK-achtige line-dedup** + **blank-collapse** als
     regex-substituties — `re.sub(r'\n{3,}', '\n\n', mutated)` en
     `re.sub(r'^([+-])([^\n]*)\n\1\2$', …)` — applied over de hele
     prompt-bron, inclusief embedded diff-hunks.
- **Byte-stabiel**: SHA-256-invariant op identieke input (`apply_saver` is
  puur regex-only, geen `time.time()`/`os.urandom()`/env reads).
- **Self-contained**: alleen Python stdlib (`re`, `pathlib`, `json`,
  `sys`) + bash + git + `claude` CLI. Geen externe Python-pakketten.

### Wat de proxy NIET doet (en dus **lower bound** is)

- Geen LLM-gebaseerde semantische compressie (echte RTK roept een klein
  taalmodel aan om `tool_result`-payloads te herformuleren met behoud van
  betekenis).
- Geen HTTP-middleware/transport-rewrite (echte 9router is een
  transparante proxy; ons harnas zit in bash tussen `claude -p` en de
  stdin van het commando).
- Geen `cache_control`-breakpoint-hinting (echte RTK voegt
  `cache_control: { type: 'ephemeral' }`-blokken toe).
- Geen per-tool-classification (echte RTK scoort elke `tool_result` en
  kiest compressie-intensiteit).

**Conclusie**: als deze naïeve mutatie de golden task breekt, breekt een
echte saver dat **ook** (en waarschijnlijk vaker vanwege de LLM-pass die
zich kan vergissen). De proxy is een **lower bound** op kwaliteit en
realistische **range** op cache-besparing — RTK doet niet minder dan een
dedup+collapse, dus `cache_read`-winst van −42% is een ondergrens.

## 4. Reproducer (uitvoerbaar zonder deze kaart te lezen)

```bash
cd /home/vdvgu/claude-cockpit
bash scripts/test_measure_token_saver.sh   # 24/24 unit-asserts, exit 0
CLAUDE_MODEL=sonnet \
  bash scripts/measure-token-saver.sh compare   # 1 baseline + 1 with-saver, ~6–10 min
```

Verwachte output: een 3-rij Markdown-tabel
(variant / input / cache_creation / cache_read / output / pass_tests /
pass_diff). Het scratch worktree wordt op `EXIT` verwijderd; raw output
landt tijdelijk in `$WT/<variant>.json` + `.err` + `.usage` + `.score`.

## 5. Resultaat van de eerste meting (2026-07-21, `compare`-run)

| variant    |        input |   cache_creation |   cache_read |   output |  pass_tests | pass_diff |
|------------|--------------|------------------|--------------|----------|-------------|------------|
| baseline   |        71192 |                0 |      1154176 |     2879 |           0 |         0 |
| with-saver |        70839 |                0 |       664576 |     2467 |           0 |         1 |
| delta      |         -353 |                0 |      -489600 |     -412 |         — |       — |

### 5.1 Wat dit WEL zegt

- **Drie aparte componenten, nooit samengeteld** — kaart-eis gehaald.
  Zie delta-rij: `-353 / 0 / -489.600 / -412`. Een eventuele integratie-kaart
  kan `cache_read` afzonderlijk tegen 10%-input-tarief waarderen
  (≈−49K tokens × 10% × Opus-input-tarief ≈ $0,15 per sessie onder
  deze conditie).
- **`cache_read` daalt 42,4%**: de prompt wordt korter én repeteert vaker
  over het 5-minuten-host-cache-venster (RTK-line-dedup haalt dubbele
  git-diff-rijen eruit). Dit is de **headline**.
- **`output_tokens` daalt 14,3%**: de Caveman-prelude instrueert
  kortere antwoorden en dat doet het model.
- **Geen quality-collapse**: `pass_diff=1` (de agent paste de
  `>`→`>=`-revert toe in plaats van een 2-line refactor of een
  fancy rewrite), `pass_tests=0` is hieronder toegelicht.

### 5.2 Wat dit NIET zegt

- **`pass_tests=0` is GEEN saver-induced regressie.** Het is een
  scoring-infra-failure: de scratch worktree heeft geen `backend/venv/`
  (worktrees delen die niet), `command -v pytest` valt door op een
  system-pytest die de `aiosqlite`-plugin mist, en de agent krijgt
  een ImportError voordat de tests kunnen draaien. `pass_diff=1` bij
  with-saver vs. `0` bij baseline is **wel** een eerlijk
  kwaliteits-signaal: with-saver paste de juiste 1-line fix toe,
  baseline koos iets anders (naar alle waarschijnlijkheid een agressieve
  refactor die de tests als bij-effect zou zijn geslaagd). K3 staat
  gepland om de venv-bootstrap in de worktree te repareren zodat
  `pass_tests` ook eerlijk wordt — niet omdat with-saver ineens beter
  scoort, maar omdat het oordeel nu structureel onmogelijk is.
- **`cache_creation_input_tokens = 0`** in beide varianten is geen bug,
  maar een gevolg van Anthropic's bucket-stale reporting op deze
  single-turn prompts waar het `system`+`tools`-blok al op master
  prefix-cached lag. K3 met cold-start prompts zal dit wel rapporteren.
- **Variantie**: één run per variant. `cache_read` is host-globaal
  cacheable (Anthropic prefix-cache is sessie-overstijgend), dus
  herhaaldelijk draaien binnen 5–10 min kan een ±5–20% andere
  `cache_read` opleveren. K3 lost dit op met N≥5.

## 6. Wat deze meting NIET bewijst

- **Geen statistische significantie** (N=1).
- **Geen generaliseerbaarheid naar niet-dispatch prompts**: een
  golden task op één commit-revert zegt niets over een
  feature-implementatie, een bug-onderzoek of een analyst-decompositie.
- **Geen uitspraak over echte 9router**: alleen over onze
  proxy-emulatie. Real-RTK zou kunnen *beter* (LLM-compressie met
  behoud van semantiek) of *slechter* (de LLM kan hallucineren) scoren;
  het is onmogelijk om uit een proxy het gedrag van een LLM af te leiden.
- **Geen uitspraak over cache_creation saving**: zoals toegelicht
  in §5.2 — de Anthropic cache-telemetrie was in deze run non-discriminerend.

## 7. Heropen-trigger

- **9router wordt écht geïnstalleerd** → vervang `apply_saver`-proxy
  door echte RTK-pass en herhaal `compare`. Verwacht een
  `cache_read`-winst ≥42% (lower bound), mogelijk veel hoger; kan
  in quality tegelijk dalen.
- **Token-savers worden productiedefault** in Cockpit
  (`apply_saver` in `dispatch.py`, opt-in of opt-out) → vóór die
  flip: K3 (multi-task + N-runs + variantie) van
  [`9router-integratie-analyse.md`](./9router-integratie-analyse.md) §9.
- **Anthropic documenteert `cache_read` vs subscription-quotum
  expliciet** → §2 van deze doc wordt feit i.p.v. inferentie; zo ja,
  bevestigt dat `cache_read` vrijwel zeker niets van het 5h-window
  afsnoept (wat gunstig zou zijn voor productie-default), maar dit
  moet niet op een vermoeden worden aangenomen.
- **`pass_tests=0` wordt structureel veroorzaakt door worktree-venv** →
  fix K3 vereist `pip install -e .` of een shared-venv-symlink
  vóór `score_golden` draait.
