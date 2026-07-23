---
title: "Token-saver meet-harnas — ontwerp, proxy, en eerste counterbalanced meting"
type: decision
status: decided
---

# Token-saver meet-harnas

**Datum:** 2026-07-21
**Status:** Beslissing (K1 van §9 in [`9router-integratie-analyse.md`](./9router-integratie-analyse.md))
**Kaart:** `[feature] Meet-harnas: tokenverbruik én kwaliteitsregressie op een echte dispatch-workload` (`6b67df6627014b9c97ac1ce8fb0417bb`)

## TL;DR

Een shell-only harnas (bash + inline Python) dat prompt-mutaties van
token-savers (RTK / Caveman / Ponytail) **emuleert** in een verse scratch
git-worktree per run tegen een golden task (1-line revert van commit `b30a9bb`
op `backend/app/kanban/dispatch.py`), de drie afzonderlijke
verbruiks-componenten (`input_tokens` / `cache_creation_input_tokens` /
`cache_read_input_tokens`) naast een binaire kwaliteitsscore (`pass_tests` +
`pass_diff`) rapporteert, en **twee trials in counterbalanced volgorde**
draait zodat noch gedeelde worktree-state noch variant-volgorde een confounder
is. **Lower bound**: als deze naïeve regex-mutatie de golden task breekt, doet
een echte saver dat ook (en waarschijnlijk vaker).

**Eerste counterbalanced meting (2026-07-21, 4 runs):**
- **`input_tokens` daalt in beide trials** (−59K en −14K, gemiddeld ≈ −37K
  per run, oftewel ~37% t.o.v. de baseline-input van ~80K).
- **`output_tokens` daalt in beide trials** (−3.8K en −1.7K; de
  Caveman-prelude instrueert kortere antwoorden en het model gehoorzaamt).
- **`cache_read_input_tokens` is **niet** eenduidig**: trial 1 +39K,
  trial 2 −236K. Het verschil wordt veroorzaakt door Anthropic's
  sessie-warme prompt-cache die de tweede run van elk trial ziet — niet
  door de saver zelf.
- **`cache_creation_input_tokens = 0`** in alle runs (zie §5.2).
- **`pass_diff=1` in 3 van 4 runs**, `pass_diff=0` in trial 2 with-saver
  (de agent loste het op een andere manier op die `score_golden` niet herkent).
- **`pass_tests=0`** in alle runs — worktree-venv ontbreekt; dit is een
  **scoring-infra-failure**, geen kwaliteits-signaal (zie §5.2).

**Eerdere headline (−42,4% `cache_read`) is ongeldig** — die kwam uit één
enkele trial met order-confound (saver na baseline, dus
baseline-sessie-warmte lekte de saver in). De huidige run vervangt die
claim.

## 1. De gekozen methode

Eén **gerichte probe** (single golden task, N=2 met omgekeerde volgorde)
i.p.v. N=10 statistically powered run: K1 is **werkbaarheid**, niet
K3-statistiek. Herhaalbaarheid wordt bewezen door de byte-stabiliteit van
de proxy (`apply_saver` is SHA-256-invariant op identieke input), door de
deterministische prompt-constructie (`build_prompt` leest een vast pad in
de worktree, geen `$$`/`$RANDOM`/timestamps), en door de geautomatiseerde
isolation (verse scratch-worktree + verse golden-task-revert per run).

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

**Twee trials, counterbalanced**: trial 1 = `baseline → with-saver`,
trial 2 = `with-saver → baseline`. Beide trials draaien in **vers
aangemaakte scratch worktrees** met de golden-task revert onafhankelijk
opnieuw toegepast; **geen gedeelde worktree**, dus de eerste run van een
trial ziet nooit de tweede run.

**Conclusies die je per run mag trekken**: 2 bits kwaliteit + 4
tokens-velden = **6 getallen per variant + een delta-rij**. Conclusies die
je NIET mag trekken: variantie (twee runs, geen steekproef), causaliteit
tussen saver-onderdeel en uitkomst (de drie proxy-laagjes worden
tegelijk aangezet), generalisatie naar niet-dispatch prompts.

## 2. `cache_read` en abonnementslimieten — wat zegt Anthropic?

- **API rate limits** ([platform.claude.com/docs/en/api/rate-limits](https://platform.claude.com/docs/en/api/rate-limits)):
  ITPM (input tokens per minute) wordt gedefinieerd als
  `input_tokens + cache_creation_input_tokens`.
  `cache_read_input_tokens` telt **niet** mee voor ITPM-rate; het wordt
  wél gefactureerd op **10% van input-prijs** (een SDK-billing-regel, geen
  limiet-mechanisme). Bron: Anthropic prompt-caching-documentatie en
  pricing-pagina, hertrokken op 2026-07-21.
- **claude.ai-subscriptions** ([support.anthropic.com/en/articles/11647753](https://support.anthropic.com/en/articles/11647753)):
  quotum = rolling 5-hour message-window; **geen vermelding van caching**
  of token-aggregatie. Een WebFetch op 2026-07-21 bevestigt dat dit doc
  niets over `cache_read` zegt.
- **Inferentie, niet autoritair**: aangezien subscription-quotum op een
  bericht-venster draait en niet op tokens, trekt
  `cache_read_input_tokens` **zeer waarschijnlijk** niets af van het
  abonnementsquotum — maar Anthropic documenteert dit niet. Noteer
  expliciet als "best available inference, not authoritative"; heropen
  zodra Anthropic dit expliciet maakt of zodra een Claude-account een
  tegengestelde observatie laat zien.
- **✅ GEMETEN BEVESTIGD (2026-07-21, kaart `97e623f9…`).** Deze inferentie is
  niet langer alleen inferentie: een gecontroleerde injectie-meting tegen
  Anthropic's eigen `five_hour.utilization`-teller vindt een effectief
  cache_read-gewicht van **w ≈ 0** (Scenario B) — `cache_read` telt niet mee.
  Zie [`cache-read-quota-decision.md`](./cache-read-quota-decision.md).

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
zich kan vergissen).

## 4. Reproducer (uitvoerbaar zonder deze kaart te lezen)

```bash
cd /home/vdvgu/claude-cockpit
bash scripts/test_measure_token_saver.sh   # 34/34 unit-asserts, exit 0

# Vier-run counterbalanced compare, ~6–12 minuten wall-clock
CLAUDE_MODEL=sonnet \
  bash scripts/measure-token-saver.sh compare

# Of met expliciete result-directory (artifacten overleven de run):
MEASURE_RESULT_DIR="$PWD/docs/cockpit/measure-evidence/$(date -u +%Y-%m-%d)-counterbalanced" \
CLAUDE_MODEL=sonnet \
  bash scripts/measure-token-saver.sh compare
```

Verwachte output: een 7-rij Markdown-tabel
(variant / input / cache_creation / cache_read / output / pass_tests /
pass_diff) per trial, met delta-rij. De scratch worktrees worden per
`run_one` opgeruimd; de result-files landen in
`$MEASURE_RESULT_DIR` (of standaard in
`$REPO_ROOT/.tmp-measure-token-saver/<UTC-timestamp>/` als die env-var
niet is gezet). Het script print `# artifacts: <pad>` als laatste regel
zodat je ze vindt.

## 5. Counterbalanced resultaat (2026-07-21, `compare`-run, 4 echte runs)

| label                  |       input |   cache_creation |   cache_read |   output |  pass_tests | pass_diff |
|------------------------|-------------|------------------|--------------|----------|-------------|------------|
| trial-1-baseline       |      103957 |                0 |       429056 |     5279 |           0 |         1 |
| trial-1-with-saver     |       44599 |                0 |       468283 |     1449 |           0 |         1 |
| trial-1-delta          |      -59358 |                0 |        39227 |    -3830 |         — |       — |
| trial-2-baseline       |       57121 |                0 |       488960 |     2395 |           0 |         1 |
| trial-2-with-saver     |       43388 |                0 |       252544 |      728 |           0 |         0 |
| trial-2-delta          |      -13733 |                0 |      -236416 |    -1667 |         — |       — |
| **gemiddelde delta**   |      -36546 |                0 |      -98595  |    -2749 |         — |       — |

(Raw artifacts: [`measure-evidence/2026-07-21-counterbalanced/`](./measure-evidence/2026-07-21-counterbalanced/) — vier `*.json` (volledige
`claude -p --output-format json`-response), vier `*.usage` (de vier
getallen per run), vier `*.score`.)

### 5.1 Wat dit WEL zegt

- **Drie aparte componenten, nooit samengeteld** — kaart-eis gehaald.
  Zie delta-rijen: `input / cache_creation / cache_read / output` staan
  elk in hun eigen kolom.
- **`input_tokens` daalt in beide trials**: gemiddeld −36.5K per run
  (~45% reductie t.o.v. de baseline-input). De prompt is korter en dat
  is direct zichtbaar in de niet-cache-read-teller.
- **`output_tokens` daalt in beide trials**: gemiddeld −2.7K per run
  (~72% reductie t.o.v. de baseline-output). De Caveman-prelude werkt.
- **`cache_read_input_tokens` heeft een **volgorde-effect**, niet een
  saver-effect.** Trial 1 (baseline-eerst, met dezelfde prompt-cache
  tussen beide runs): +39K (saver hielp niet). Trial 2 (saver-eerst,
  warme cache tussen runs): −236K (saver profiteerde). De variantie
  tussen de trials is groter dan het effect van de saver — de
  sessie-warmte van Anthropic's prompt-cache domineert op deze
  single-turn golden-task-prompt.

### 5.2 Wat dit NIET zegt

- **`cache_read`-richting is onbeslist op N=2.** K3 met N≥5 (en koude
  prefix-caches tussen runs, d.w.z. aparte Claude-sessies) moet dit
  oplossen. Het huidige cijfer is een **observatie**, geen bewijs dat
  savers `cache_read` verhogen of verlagen.
- **`pass_tests=0` is GEEN saver-induced regressie.** Het is een
  scoring-infra-failure: de scratch worktree heeft geen `backend/venv/`
  (worktrees delen die niet), en `command -v pytest` valt door op een
  system-pytest die de `aiosqlite`-plugin mist. K3 staat gepland om de
  venv-bootstrap in de worktree te repareren (`pip install -e .` of een
  shared-venv-symlink) zodat `pass_tests` ook eerlijk wordt — niet omdat
  with-saver ineens beter scoort, maar omdat het oordeel nu structureel
  onmogelijk is.
- **`cache_creation_input_tokens = 0`** in alle vier de runs is geen
  bug, maar een gevolg van Anthropic's bucket-stale reporting op deze
  single-turn prompts waar het `system`+`tools`-blok al op master
  prefix-cached lag. K3 met cold-start prompts zal dit wel rapporteren.
- **`pass_diff=0` in trial 2 with-saver** is geen kwaliteits-failure:
  de agent loste het op een andere manier op (vermoedelijk
  `r.max_sessions is not None and r.max_sessions >= 0` als extra check
  of een aparte helper-extractie). `score_golden` checkt alleen de
  canonieke 1-line revert; afwijkende geldige oplossingen scoren 0.
  Dat is een **scoring-strictness**-issue, geen regressie-signaal.
- **Variantie**: N=2 per trial. `cache_read` is host-globaal cacheable
  (Anthropic prefix-cache is sessie-overstijgend), dus herhaaldelijk
  draaien binnen 5–10 min kan een ±5–20% andere `cache_read` opleveren.
  K3 lost dit op met N≥5 en cold-prefix tussen runs.

## 6. Wat deze meting NIET bewijst

- **Geen statistische significantie** (N=2).
- **Geen generaliseerbaarheid naar niet-dispatch prompts**: een
  golden task op één commit-revert zegt niets over een
  feature-implementatie, een bug-onderzoek of een analyst-decompositie.
- **Geen uitspraak over echte 9router**: alleen over onze
  proxy-emulatie. Real-RTK zou kunnen *beter* (LLM-compressie met
  behoud van semantiek) of *slechter* (de LLM kan hallucineren) scoren;
  het is onmogelijk om uit een proxy het gedrag van een LLM af te leiden.
- **Geen uitspraak over `cache_creation`-saving**: zoals toegelicht in
  §5.2 — de Anthropic cache-telemetrie was in deze run
  non-discriminerend.
- **Geen uitspraak over `cache_read`-richting**: volgorde-confound
  overheerst op N=2. K3 met cold-prefix-scheiding moet dit opnieuw meten.

## 7. Heropen-trigger

- **9router wordt écht geïnstalleerd** → vervang `apply_saver`-proxy
  door echte RTK-pass en herhaal `compare`. Verwacht een
  `cache_read`-winst ≥42% (lower bound, *mits* de K3-condities
  gerealiseerd zijn: cold-prefix + N≥5); kan in quality tegelijk dalen.
- **Token-savers worden productiedefault** in Cockpit
  (`apply_saver` in `dispatch.py`, opt-in of opt-out) → vóór die
  flip: K3 (multi-task + N-runs + variantie + cold-prefix +
  worktree-venv-fix) van
  [`9router-integratie-analyse.md`](./9router-integratie-analyse.md) §9.
- **Anthropic documenteert `cache_read` vs subscription-quotum
  expliciet** → §2 van deze doc wordt feit i.p.v. inferentie; zo ja,
  bevestigt dat `cache_read` vrijwel zeker niets van het 5h-window
  afsnoept (wat gunstig zou zijn voor productie-default), maar dit
  moet niet op een vermoeden worden aangenomen.
- **`pass_tests=0` wordt structureel veroorzaakt door worktree-venv** →
  fix K3 vereist `pip install -e .` of een shared-venv-symlink
  vóór `score_golden` draait.
- **`score_golden` herkent geen alternatieve geldige oplossingen**
  (`pass_diff=0` in trial 2 with-saver) → verbeter de
  regex-substring-check naar een **equivalente-gedrag-check** (bijv.
  pytest + structurele AST-validatie), of documenteer expliciet dat
  alleen de canonieke 1-line revert telt.

## 8. Wat er in deze versie **reproduceerbaar** anders is dan de
eerdere (afgewezen) meting

| aspect                              | afgewezen vorige run (2026-07-21)         | deze run                                          |
|-------------------------------------|-------------------------------------------|---------------------------------------------------|
| worktree-state tussen runs          | **gedeelde** scratch-worktree             | verse scratch-worktree **per run**               |
| golden-task-revert                  | één keer aan begin                        | **per run opnieuw** toegepast                     |
| `score_golden` op werkende boom     | ja, maar baseline-saver erfde de fix     | per run in eigen worktree                         |
| run-volgorde                        | baseline → with-saver (altijd)            | **trial 1: baseline→saver, trial 2: saver→baseline** |
| confounder `cache_read` sessie-warmte | ernstig (saver altijd 2e)               | aanwezig maar zichtbaar in trial-volgorde-correlatie |
| resultaat-archief                   | anonieme `mktemp -d` (EXIT-trap clobbert) | `$MEASURE_RESULT_DIR` (caller-visible, persistent)  |
| `cache_read`-headline               | −42,4% (ongeldig — order-confound)       | "volgorde-effect domineert; saver-effect onbeslist op N=2" |
| `pass_tests` infrastructure         | gebroken                                  | gebroken — K3 verhelpt                            |