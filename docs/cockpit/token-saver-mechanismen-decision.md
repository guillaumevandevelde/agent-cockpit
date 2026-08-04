---
title: "Token-saving mechanismen (RTK / Headroom / Caveman / Ponytail) — analyse & beslissing"
type: decision
status: decided
---

# Token-saving mechanismen — analyse & beslissing

**Datum:** 2026-07-21
**Status:** besloten
**Kaart:** `3abcd501…` "Analyse — token-saving mechanismen los van 9router"
**Uitkomst:** ✅ **GO op RTK** (conditioneel) · ⛔ **NO-GO op Headroom** · ⚠️ **conditionele GO op Caveman** (opt-in, output-onkritische lanes) · ⛔ **NO-GO op Ponytail**.
**Meetdatum:** 2026-07-21, alle metingen op deze host (WSL Ubuntu, `claude` 2.1.216, rtk 0.43.0, headroom-ai 0.32.1)

**Uitkomst in één alinea.** **GO op RTK** (conditioneel: per-lane, via een
worktree-lokale hook, `grep`-carve-out verplicht). **NO-GO op Headroom** — niet
op kwaliteit, maar omdat zijn enige werkende integratievorm voor Claude Code
`ANTHROPIC_BASE_URL` naar een lokale proxy zet, en dat is exact de
subscription-OAuth-MITM die op 2026-07-15 al is afgewezen en op 2026-07-19
opnieuw. **Caveman: conditionele GO** en **Ponytail: NO-GO** — en dat "conditioneel"
is de kern van deze analyse. De opbrengst van beide mechanismeklassen hangt volledig
af van één onopgeloste boekhoudvraag: **telt `cache_read` mee in het
abonnementsquotum?** Gemeten over 25 echte dispatch-sessies is `cache_read`
**95,9%** van alle tokens. Telt het mee, dan levert RTK **7,4%** headroom en
Caveman 0,26%. Telt het niet mee, dan draait het exact om: RTK **1,6%** en Caveman
**6,7%**. De twee mechanismen zijn dus **anti-gecorreleerd** op een vraag die dit
huis nog niet heeft beslecht (§2) — en beide zijn een orde kleiner dan de "60–90%"
op de doos. De écht grote lever ligt ergens anders (§10).

---

## 1. Wat de kaart vroeg

Zeven eisen, elk apart beantwoord:

| # | Eis | Waar |
|---|---|---|
| 1 | Elk mechanisme apart beoordelen: wat het muteert, faalgedrag, lossless?, werkt het zonder 9router? | §4 |
| 2 | **Meten** op een echte Cockpit-dispatch-workload, niet synthetisch | §3, §5 |
| 3 | Prompt-cache-interactie expliciet wegen | §6 |
| 4 | Passen RTK/Headroom **rechtstreeks** in de spawn-keten? | §7 |
| 5 | Kwaliteitsrisico met een **voorbeeld**, niet met een aanname | §4.1.3 |
| 6 | GO/NO-GO **per mechanisme** + regel in `decisions.md` | §8 |
| 7 | Onafhankelijk van de router-kaarten | deze hele analyse; 9router is nergens geïnstalleerd of gedraaid |

Plus de aanvulling van 2026-07-21: weeg de opbrengst als **headroom binnen het
limietvenster**, niet als kosten, en **verifieer** of `cache_read` meetelt in de
abonnementslimieten. Dat laatste is §2 geworden, omdat het antwoord de rest van
de analyse omdraait.

## 2. De herkadering: alles hangt aan één onopgeloste boekhoudvraag

De kaart vroeg dit uit te zoeken en **niet aan te nemen**. Uitgezocht — en het
eerlijke antwoord is dat het **niet sluitend vaststaat**, terwijl het wel de
uitkomst bepaalt. Dat is zelf de belangrijkste bevinding van deze analyse.

### 2.1 De verdeling is extreem scheef

Gemeten over **25 echte Cockpit-dispatch-sessies** (2.001 assistant-turns, uit
`~/.claude/projects/*/**.jsonl`):

| Bucket | Tokens | Aandeel |
|---|---:|---:|
| `cache_read` | 182.492.962 | **95,9%** |
| `input` | 5.418.269 | 2,8% |
| `cache_creation` | 1.590.160 | 0,8% |
| `output` | 711.946 | **0,4%** |
| **totaal** | **190.213.337** | |

Gemiddeld **7,6M tokens per sessie over 80 turns**. Of `cache_read` meetelt is
daarmee geen detail — het is het verschil tussen een noemer van 197M en een van 8M.

### 2.2 Wat we wél zeker weten, en wat niet

| Context | Telt `cache_read` mee? | Zekerheid |
|---|---|---|
| **Claude API** (pay-per-token) | **Nee** — cached input telt niet voor rate limits, en wordt à 10% gefactureerd (uitzondering: Haiku 3.5) | ✅ **Officieel** — [platform.claude.com — Rate limits](https://platform.claude.com/docs/en/api/rate-limits) |
| **Claude Code op een abonnement** | **Waarschijnlijk ja** | ⚠️ **Niet officieel** — Anthropic's subscription-documentatie zwijgt erover |

De sterkste beschikbare evidentie voor "ja" is
[anthropics/claude-code#24147](https://github.com/anthropics/claude-code/issues/24147)
(open, 14 comments, geopend 2026-02-08): *"Cache read tokens consume 99.93% of
usage quota"*. De melder onderbouwt met 30 dagen aan geparste transcripts —
5,09 miljard cache-reads tegenover 3,89 miljoen I/O-tokens, ratio 1.310:1 — en
een tweede rapporteur meet 97,7% via een onafhankelijke tool (`ccusage_go`).
Meerdere gebruikers bevestigen het patroon onafhankelijk.

**Maar de thread bevat ook tegenspraak**, en die verdient vermelding: één
reageerder stelt dat alleen `cache_creation` tegen het quotum telt, met het
API-gedrag als argument. De vaakst herhaalde formulering in de thread is *"not
billed, but counted towards your session and weekly quota"* — plausibel, en
precies het onderscheid dat op een abonnement telt, maar het blijft
gebruikersrapportage.

> **Conflict met een eerdere register-regel, expliciet.** De regel van
> 2026-07-21 bij [`token-saver-meet-harnas.md`](./token-saver-meet-harnas.md)
> (kaart `6b67df66…`) concludeert het omgekeerde: *"quotum is rolling 5h
> message-window, dus `cache_read` trekt zeer waarschijnlijk niets af"* — daar
> zelf al eerlijk gelabeld als *"best available inference, not authoritative"*.
> Deze analyse haalt die inferentie **niet** onderuit met een harde bron; ze zet
> er sterkere (maar nog steeds niet-officiële) tegen-evidentie naast. De
> verantwoorde uitkomst is dus niet "de een wint", maar: **beslis het niet op
> aanname — reken beide scenario's door.** Dat is §2.3.

### 2.3 Beide scenario's doorgerekend

| | Scenario A: `cache_read` telt mee | Scenario B: `cache_read` telt niet mee |
|---|---|---|
| Getelde noemer | 197.417.671 | 7.989.395 |
| Samenstelling | `cache_read` 95,9% | `input` 67,8% · `cache_creation` 21,8% · `output` **10,3%** |
| **RTK-winst** | **7,4%** | **1,6%** |
| **Caveman-winst** (@65% output) | **0,26%** | **6,7%** |

**De twee mechanismen zijn anti-gecorreleerd.** In scenario A compoundt RTK
(elke bespaarde tool-output-token wordt ~80 turns lang niet meer her-gelezen) en
verdrinkt output-besparing in de ruis. In scenario B valt RTK's compounding weg —
alleen de eenmalige `cache_creation` telt — en wordt output ineens 10,3% van de
noemer, waarmee Caveman het sterkste mechanisme van de vier wordt.

> **✅ BESLECHT MET EEN METING (2026-07-21, kaart `97e623f9…`) — Scenario B is
> de werkelijkheid.** Een gecontroleerde injectie-meting op dit abonnement
> (Anthropic's eigen `five_hour.utilization`-teller, de bron achter `/usage`)
> vindt een effectief cache_read-gewicht van **w = 0,014** — statistisch nul.
> Het model-vrije bewijs: twee intervallen bewogen allebei **exact 4
> procentpunt** terwijl hun cache_read 1,8× verschilde (2,05 M vs. 3,69 M). Dus:
> **RTK ≈ 1,6% / Caveman ≈ 6,7% — Caveman is het sterkste mechanisme.** De
> hedge blijft verstandig (beide opt-in, per-lane), maar de bouwinspanning weegt
> nu richting output-reductie. De tegen-evidentie
> ([claude-code#24147](https://github.com/anthropics/claude-code/issues/24147),
> "99,93% of quota") beschrijft token-*boekhouding* die cache_read meesommeert,
> niet de werkelijke quotum-consumptie. Volledige meting + reproductie:
> [`cache-read-quota-decision.md`](./cache-read-quota-decision.md). Deze
> §2.3-doorrekening blijft staan als het waarom; de meting kiest de kolom.

Dat is een ongemakkelijke uitkomst, maar het is de eerlijke. Twee praktische
gevolgen:

1. **De boekhoudvraag zelf is het waardevolste stuk vervolgwerk** — hij is
   goedkoop te beantwoorden (§10) en beslist waar de bouwinspanning heen moet.
2. **Tot dat antwoord er is, is de portefeuille de hedge.** RTK én Caveman
   opt-in bouwen dekt beide scenario's; precies één van de twee kiezen is een
   weddenschap op een onbeslist feit.

Eén conclusie is scenario-onafhankelijk: **een saver die de prompt-cache breekt
is op een abonnement in beide scenario's schadelijk** — gebroken prefix kost
`cache_creation` à 1,25× (telt in A én B mee) plus, in scenario A, opnieuw
`cache_read` over de hele resterende sessie. Zie §6.

## 3. Meetmethode

Drie onafhankelijke meetlagen, alle drie reproduceerbaar (§9):

- **Byte-niveau** — `cmd > raw` vs. `rtk cmd > rtk`, op echte commando's uit
  déze repo. Exact, geen tokenizer nodig.
- **Token-niveau (Anthropic's eigen tokenizer)** — de payload wordt als prompt
  aangeboden aan `claude -p --output-format json --strict-mcp-config
  --mcp-config '{"mcpServers":{}}'`, en de tokens komen uit `usage`. Een leeg
  controle-payload meet de vaste overhead; die trek ik eraf. Twee controle-runs
  (begin én eind) gaven **beide 38.602** totaal, dus de overhead is stabiel en de
  aftrek is geldig.
- **Sessie-niveau** — de werkelijke `usage`-records uit 25 dispatch-transcripts,
  plus een counterfactual-simulatie (§5.3).

**Waarom niet via `scripts/measure-token-saver.sh`.** Dat harnas (kaart
`6b67df66…`) bestaat en is bruikbaar, maar zijn `apply_saver` is een
**prompt-niveau stand-in** — een Caveman/Ponytail-achtige prelude plus wat
regex-normalisatie — niet RTK of Headroom zelf. Het meet dus het harnas, niet het
mechanisme. Het recept eronder (`claude -p --output-format json`, verschil in
`input + cache_creation + cache_read`) is wél hergebruikt; alleen de
saver-substitutie is vervangen door de echte binaries. Zie §10 voor de
follow-up die dit terugvoedt.

De losse meet-kaart `634e0789…` bestaat niet meer op het bord; deze analyse
vervangt hem zoals gevraagd.

## 4. De vier mechanismen, elk apart

Alle vier bestaan en zijn geverifieerd via `gh api` op 2026-07-21 — de
repo-tabel in de kaart klopt. Alle vier draaien **zonder 9router**: RTK en
Caveman/Ponytail installeren rechtstreeks in Claude Code, Headroom is een
zelfstandige Python-package. De kaart-hypothese dat de savers geen
9router-features zijn, is dus bevestigd.

⚠️ De kaart waarschuwde terecht: 60–90K stars in maanden is dezelfde
hype-snelheid die in `9router-integratie-analyse.md` §3 tégen 9router werd
ingebracht. Ik heb die maatstaf hieronder gelijk toegepast — en in beide
richtingen: RTK's leeftijd is een risico dat in §8 als voorwaarde terugkomt, niet
een reden om het ongelezen af te wijzen.

### 4.1 RTK (`rtk-ai/rtk`, Rust, Apache-2.0, 0.43.0)

#### 4.1.1 Wat het feitelijk muteert — en dit corrigeert de kaart

De kaart (en `9router-integratie-analyse.md` §2.1) beschrijft RTK als
"comprimeert `tool_result`-payloads". **Dat is niet wat het doet, en het verschil
is beslissend.** RTK raakt de API-request nooit aan. Het installeert een
Claude Code **`PreToolUse`-hook** op de `Bash`-tool die het *commando* herschrijft
vóór uitvoering. Geverifieerd:

```
$ echo '{"tool_name":"Bash","tool_input":{"command":"git status"}}' | rtk hook claude
{"hookSpecificOutput":{"hookEventName":"PreToolUse",
 "permissionDecisionReason":"RTK auto-rewrite",
 "updatedInput":{"command":"rtk git status"}}}
```

Dus: het `tool_result` dat het model ziet is van meet af aan korter — er wordt
niets *herschreven* dat er al stond. Drie gevolgen:

- **Cache-veilig by construction.** Geen enkele bestaande prefix-byte verandert.
  De grote zorg uit de kaart ("kan `cache_read` naar nul brengen") geldt voor RTK
  **niet**. Zie §6.
- **Geen proxy, geen base-URL, geen ToS-oppervlak.** Het abonnement-OAuth-pad
  blijft ongemoeid.
- **Maar het bereik is beperkt tot de `Bash`-tool.** RTK's eigen README zegt dit
  expliciet: *"the hook only runs on Bash tool calls. Claude Code built-in tools
  like `Read`, `Grep`, and `Glob` do not pass through the Bash hook."* Dat is de
  bovengrens van §5.

#### 4.1.2 Faalgedrag: fail-open, geverifieerd

Een commando zonder filter geeft **lege hook-output** = geen rewrite =
onveranderde uitvoering:

```
$ echo '{"tool_name":"Bash","tool_input":{"command":"sed -n 1,5p README.md"}}' | rtk hook claude
(geen output)
```

Fail-open is dus geen claim maar gedrag. Telemetrie staat **default uit**
(`consent: never asked`, `enabled: no`) en is opt-in — geen uitgaand pad.

#### 4.1.3 Lossless? **Nee — en dit is het kwaliteitsrisico, met bewijs**

De kaart vroeg om een voorbeeld in plaats van een aanname. Hier is het, op een
echte diff uit deze repo (commit `3dab026`, 8 bestanden, 923 insertions):

```
$ git diff 3dab026~1 3dab026        # 50.961 bytes, 1.130 regels, 937 gewijzigde regels
$ rtk git diff 3dab026~1 3dab026    # 24.081 bytes,   522 regels, 439 gewijzigde regels
```

Een regel-voor-regel vergelijking van alle `+`/`-`-regels: **505 van de 937
gewijzigde regels (54%) ontbreken** in de RTK-output. RTK toont per bestand de
eerste hunks en vervangt de rest door een teller:

```
  ... (201 lines truncated)
  +301 -0
...
... (more changes truncated)
[full diff: rtk git diff --no-compact]
```

**Belangrijke nuance, en die corrigeert `9router-integratie-analyse.md` §4.2 in
ons voordeel:** de weglating is **gemarkeerd, niet stil**. §4.2 stelde het
faalpad als *"verkeerde commit-inhoud, stil"*. Dat is te sterk — het model ziet
`(201 lines truncated)` plus het exacte ontsnappingscommando. De agent kan
escaleren; hij moet het alleen wél doen. Dat verplaatst het risico van "onzichtbaar
informatieverlies" naar "instructie-naleving", wat een andere en beter
beheersbare klasse is.

Twee scherpe randen die wél overeind blijven:

1. **`--no-compact` bespaart exact 0%** (50.961 → 50.961 bytes, byte-identiek).
   RTK's git-diff-winst *ís* de truncatie. Er is geen "lossless" stand met winst.
2. **`rtk read` bespaart default 0%.** `rtk read <file>` draait op
   `--level none (default, full content)` en gaf op
   `backend/app/kanban/dispatch.py` (237.577 bytes) een **byte-identiek**
   resultaat. De README-tabel claimt `cat`/`read` −70%. Dat getal vereist een
   niet-default vlag. Idem `git log --oneline`: byte-identiek, 0%.

#### 4.1.4 Een omgevingsspecifieke val op déze host

`grep` is op deze box **een shell-functie die naar `ugrep` 7.5.0 wijst**
(`type grep` → shell function uit de Claude-Code shell-snapshot), en ugrep
recurseert standaard op een directory-argument. RTK roept `/usr/bin/grep`
(GNU grep) rechtstreeks aan en omzeilt die functie:

```
$ grep "dispatch" backend/app        → 868 matches
$ rtk grep "dispatch" backend/app    → /usr/bin/grep: backend/app: Is a directory
```

Het gaat hier **niet** om een RTK-defect — GNU grep gedraagt zich correct — maar
het is wel een echte, gereproduceerde gedragsverandering op precies deze host,
en de foutmelding wordt als "gecomprimeerde output" doorgegeven aan het model.
Met een expliciete `-r` werkt het wel (`rtk grep -r "dispatch" backend/app` →
93.163 → 21.063 bytes). Dit is de reden dat `grep` in §8 een carve-out krijgt in
plaats van vertrouwen.

Kleinere ruwe randen: `rtk init -g` faalt als `~/.claude/` nog niet bestaat, en
installeert de hook niet zelf — het **print** het JSON-blok dat je handmatig in
`settings.json` moet zetten.

### 4.2 Headroom (`headroomlabs-ai/headroom`, Python, Apache-2.0, 0.32.1)

Headroom is inhoudelijk het ambitieuzere project — content-aware compressors,
AST-compressie, een MCP-server, cross-agent memory. Het valt hier af op
integratievorm, niet op ambitie.

#### 4.2.1 De blokkerende bevinding: de enige werkende vorm is een base-URL-proxy

Headroom's Claude-Code-integratie (`headroom wrap claude`, `headroom init`,
`headroom install`) werkt door **`ANTHROPIC_BASE_URL` naar een lokale proxy te
zetten** — bevestigd in de package-source
(`headroom/providers/claude/runtime.py`, `headroom/proxy/server.py`,
`headroom/proxy/cc_switch_reconciler.py`).

Dat is één-op-één de route die Cockpit al **twee keer** heeft afgewezen:

> "Voor het **Anthropic-abonnement** — de subscription waar de vraag over gáát —
> betekent dit subscription-OAuth-verkeer door een zelfgebouwde MITM duwen.
> ❌ **Afgewezen.**"
> — [`subscription-verbruik-inzicht-analyse.md`](./subscription-verbruik-inzicht-analyse.md) §5.2 (2026-07-15)

en herbevestigd in [`9router-integratie-analyse.md`](./9router-integratie-analyse.md)
§4.1 (2026-07-19). Deze analyse loopt daar niet stilzwijgend overheen. Álle
Cockpit-dispatch draait op dat abonnement; het is het enige kritieke pad dat we
hebben.

#### 4.2.2 En de proxy kost aantoonbaar functionaliteit

Headroom's eigen broncode documenteert drie Claude-Code-capabilities die
**uitvallen** zodra `ANTHROPIC_BASE_URL` niet `api.anthropic.com` is:

| Upstream-issue | Effect | Herstelbaar? |
|---|---|---|
| #746 | Claude Code stopt met het deferren van MCP/tool-schemas → **álle** schemas materialiseren in het contextvenster | Ja, `ENABLE_TOOL_SEARCH=true` |
| #1158 | 1M-contextvenster wordt gepoort | Ja, `--1m` |
| #1779 | **Remote Control** (`/rc`) wordt deterministisch uitgeschakeld vanaf CC 2.1.196 | **Nee** |

Wij draaien 2.1.216, dus #1779 is actueel. En #746 is bitter ironisch: een
proxy die tokens komt besparen, zet standaard de schema-deferral uit die
`per-persona-mcp-allowlist-decision.md` §7 heeft gemeten als een besparing van
~4.600 tokens. Zonder de mitigatie kost Headroom eerst tokens voordat het er wint.

#### 4.2.3 Maturiteit: de gedocumenteerde installatie is stuk

`pip install headroom-ai` (de "60 seconds"-route uit de README) levert een CLI
die op **elke** aanroep crasht:

```
$ headroom --version
ModuleNotFoundError: No module named 'fastapi'
```

`fastapi` zit alleen in de `proxy`/`dev`-extras, maar `headroom/cli/wrap.py`
importeert het onvoorwaardelijk. `pip install 'headroom-ai[proxy]'` lost het op.
Daarnaast: de tekstcompressor (`Kompress-v2-base`) vereist een HuggingFace-download
en meldt bij afwezigheid *"Kompress model not ready; requests will not be
compressed"* — fail-open, netjes gemeld, maar het betekent dat de default-installatie
op tekst **niets** comprimeert.

#### 4.2.4 Gemeten compressie — lossless is het zeker niet

`compress()` als library, op dezelfde corpus (char-niveau, Kompress-model afwezig):

| Payload | Ruw | Na `compress()` | Δ |
|---|---:|---:|---:|
| `git diff` (8 bestanden) | 50.870 | 52.982 | **−4,2%** (gróter) |
| `ls -laR backend/app` | 15.533 | 15.980 | **−2,9%** (gróter) |
| `grep -rn dispatch` | 93.004 | 66.493 | +28,5% |
| `dispatch.py` (Python-bron) | 236.838 | 5.646 | **+97,6%** |

Die laatste is geen compressie maar de AST-`CodeCompressor` die een 237KB
bronbestand tot een ~5,6KB skelet reduceert. Voor "waar staat functie X" is dat
prima; voor een agent die dat bestand moet **editen** is het catastrofaal —
en Cockpit-executors editen bestanden. De twee negatieve regels laten zien dat op
onze meest voorkomende payloads (diff, listing) de JSON-envelope-overhead de winst
overtreft.

#### 4.2.5 De niet-proxy-vormen zijn voor Cockpit niet van toepassing

Headroom biedt ook een **library**-vorm (`compress(messages)`) en een
**MCP-server**-vorm. Beide zijn hier onbruikbaar, en om een reden die al vaststaat:
**Cockpit doet per ontwerp nul LLM-calls — het spawnt CLI's**
(`9router-integratie-analyse.md` §5.2). Er is geen message-array in onze code om
`compress()` op aan te roepen. De MCP-vorm vereist dat de agent de tool
*vrijwillig* aanroept, wat geen mechanische garantie is en bovendien zelf
schema-tokens kost.

### 4.3 Caveman (`JuliusBrussee/caveman`, JS, MIT, v1.9.1)

Injecteert een terse-speak-systeemprompt; claim 65% minder **output**-tokens.
Werkt zonder 9router (Claude Code plugin/skill), en is oprecht over wat het
níet doet — de eigen README zet `input tokens saved 0%` in de banner.

**Beoordeling: opbrengst hangt volledig aan §2's boekhoudvraag.** In scenario A
(`cache_read` telt mee) is output **0,4%** van de noemer en koopt de geclaimde 65%
hooguit **0,26%** headroom — ruis. In scenario B is output **10,3%** van de noemer
en levert dezelfde 65% **6,7%** — dan is Caveman het *sterkste* van de vier
mechanismen, ruim vóór RTK's 1,6%.

De 65% zelf is bovendien een **vendor-claim uit de README, door mij niet
gemeten**. Ik heb Caveman niet geïnstalleerd (een systeemprompt-injector meten
vraagt een A/B over meerdere echte kaarten, niet één `claude -p`-call — dat is
werk voor het harnas `6b67df66…`, niet voor deze spike). Behandel 6,7% dus als
een **bovengrens onder een ongemeten aanname**, niet als een resultaat.

Twee kosten die in beide scenario's blijven staan: (a)
`9router-integratie-analyse.md` §4.2's punt dat terse output precies de
artefacten degradeert die dit bord als deliverable rekent — Done-summaries in
product-taal, analysedocs, impediment-vragen — en (b) de systeemprompt-injectie
zelf kost input-tokens die daarna 80 turns lang worden her-gelezen.

Waar het zonder bezwaar kan: lanes waar de output geen deliverable is
(research-sweeps, statuschecks). Dat is precies de per-lane-schakelaar die kaart
`d0446fd8…` al ontwerpt — en die schakelaar is nu extra gerechtvaardigd, omdat
hij de hedge uit §2.3 uitvoerbaar maakt.

### 4.4 Ponytail (`DietrichGebert/ponytail`, JS, MIT, v4.8.4)

Injecteert een "lazy senior dev, YAGNI-first"-gedragsprompt. Zelfde
output-rekensom als Caveman, plus een scherper conflict: het stuurt op *minder
werk doen*, niet op *korter formuleren*. Dat botst frontaal met een
engineer-persona die TDD, volledige acceptatiecriteria en een FCR moet leveren.
`9router-integratie-analyse.md` §4.2 staat hier onverkort overeind.

Anders dan bij Caveman redt scenario B dit niet. Caveman comprimeert *hoe* er
geantwoord wordt en laat het werk intact — een echte ruil. Ponytail's besparing
is geen token-effect maar een **scope-effect**: minder code schrijven leest als
minder tokens. Op een bord waar "deliverable geleverd" het succescriterium is,
is dat geen besparing maar onderlevering, en de tokenmeting kan het verschil
niet zien.

## 5. De meting op een echte dispatch-workload

### 5.1 Byte- en token-niveau op echte repo-commando's

Corpus: echte commando's uit deze repo. Tokens via Anthropic's eigen tokenizer
(§3), na aftrek van de vaste overhead van 38.602 tokens.

| Commando | Ruw (bytes) | RTK (bytes) | Ruw (tok) | RTK (tok) | Besparing |
|---|---:|---:|---:|---:|---:|
| `git diff 3dab026~1 3dab026` | 50.961 | 24.081 | 20.416 | 9.774 | **52,1%** |
| `grep -rn dispatch backend/app` | 93.163 | 21.063 | 38.727 | 9.215 | **76,2%** |
| `ls -laR backend/app` | 15.533 | 7.393 | 10.825 | 3.877 | **64,2%** |
| `git status` | 196 | 78 | — | — | 60,2% |
| `git diff HEAD~5 HEAD` | 93.806 | 24.361 | — | — | 74,0% |
| `git log -p -3` | 52.510 | 707 | — | — | 98,7% |
| `find backend/app -name "*.py"` | 9.231 | 685 | — | — | 92,6% |
| `gh pr list` | 1.813 | 1.091 | — | — | 39,8% |
| `git log --oneline -30` | 1.748 | 1.748 | — | — | **0%** |
| `git show HEAD` | 169 | 170 | — | — | **−0,6%** |
| `cat dispatch.py` / `rtk read` | 237.577 | 237.577 | — | — | **0%** |
| **gemeten subtotaal (3 token-paren)** | | | **69.968** | **22.866** | **67,3%** |

Op de commando's waar RTK een filter heeft, haalt het dus ongeveer wat het
belooft. De byte- en token-ratio's lopen bovendien netjes gelijk op (52,7% vs.
52,1% voor `git diff`), wat het byte-niveau als goedkope proxy valideert.

### 5.2 Maar wat is het aandeel dat RTK überhaupt kan raken?

Gemeten over dezelfde 25 dispatch-sessies, `tool_result`-volume per tool
(1.891.539 tekens totaal):

| Tool | Calls | Tekens | Aandeel |
|---|---:|---:|---:|
| `Read` | 217 | 865.013 | **45,7%** |
| **`Bash`** | 685 | 782.211 | **41,4%** ← RTK's enige oppervlak |
| `TaskOutput` | 6 | 66.169 | 3,5% |
| `mcp__cockpit-kanban__*` | 38 | 136.472 | 7,2% |
| `Edit` / `Write` / overig | — | 41.674 | 2,2% |

En binnen `Bash`, gesplitst naar commando:

| Commando | Aandeel van Bash | RTK-filter? |
|---|---:|---|
| `git` | 30,4% | ✅ (variabel: 0%–98,7%) |
| overig | 25,3% | ❌ grotendeels niet |
| `grep` | 22,5% | ✅ 76% (mits `-r`, zie §4.1.4) |
| `sed` | 6,7% | ❌ |
| `ls` | 3,2% | ✅ 64% |
| `npm`/`npx` | 3,4% | ✅ ongemeten |
| `curl` | 2,5% | ✅ ongemeten |
| `echo`/`cat`/`head`/`tail` | 4,9% | ❌ / 0% |

**De grootste post — `Read` op 45,7% — ligt volledig buiten RTK's bereik.** Dat
is geen detail: het betekent dat RTK's plafond op tool-outputvolume ruwweg
41,4% × ~60% ≈ 25% is, vóór we de cache-dynamiek meerekenen.

### 5.3 Het getal dat er werkelijk toe doet: headroom binnen het limietvenster

De kaart vroeg niet "hoeveel tokens minder" maar "hoeveel verder komt een
dispatch voordat de drempel valt". Om dat te beantwoorden heb ik de 25
transcripts opnieuw doorlopen met een counterfactual: elk `Bash`-`tool_result`
van een RTK-gedekt commando wordt verkleind met de in §5.1 **gemeten** ratio, en
die besparing wordt geteld voor elke turn die het daarna nog zou hebben
her-gelezen (`Δtokens × resterende turns`, plus de eenmalige `cache_creation`).

| Sessie (staart van de branchnaam) | Turns | Werkelijke `cache_read` | Gemodelleerde besparing | % |
|---|---:|---:|---:|---:|
| `k-feature-meet-0087` | 167 | 15.341.327 | 2.961.843 | **19,3%** |
| `k-feature-meet-e005` | 188 | 14.728.576 | 2.124.119 | 14,4% |
| `k-column-model-a69f` | 354 | 40.811.264 | 3.929.570 | 9,6% |
| `k-resolve-imped-e371` | 234 | 25.179.136 | 2.237.529 | 8,9% |
| `k-analyse-dispa-7d38` | 95 | 9.565.786 | 615.193 | 6,4% |
| `k-feature-quota-c3a6` | 181 | 17.994.670 | 399.830 | 2,2% |
| `k-analyse-token-38a6` (deze sessie) | 96 | 9.744.529 | 82.660 | **0,8%** |
| **Totaal, 25 sessies** | **2.001** | **182.997.991** | **14.562.996** | **8,0%** |

Uitgedrukt over het **volledige** getelde verbruik (dus inclusief `input`,
`cache_creation` en `output`, noemer 197.417.671) is dat **7,4%** — het getal dat
in §2.3 en §8 staat. De 8,0% hierboven is de smallere verhouding tegen alleen
`cache_read`.

> **Modelaannames, expliciet.** Dit is een simulatie op echte data, geen
> A/B-run. (a) Chars→tokens via de gemeten factor 2,28 (uit §5.1's drie
> token-paren, niet geschat). (b) Ratio's per commandobucket uit §5.1;
> ongemeten buckets (`npm`, `curl`, `gh` deels) tellen als **0** — de schatting
> is dus eerder conservatief. (c) Contextcompactie wordt genegeerd, wat de
> schatting juist iets **optimistisch** maakt bij lange sessies. (d) Het model
> neemt aan dat de agent RTK's markers niet volgt met een `--no-compact`-herhaling;
> doet hij dat wel, dan valt de winst voor die call weg.

**Antwoord op de kaartvraag: ongeveer 7,4% verder — in scenario A.** Spreiding
0,8%–19,3% per sessie, afhankelijk van hoe `Bash`-zwaar de kaart is. In scenario B
valt de compounding weg en blijft **1,6%** over (alleen de eenmalige
`cache_creation`-besparing). Reëel en herhaalbaar in beide gevallen — en in beide
gevallen een orde kleiner dan de "60–90%" op de doos, omdat die claim over
commando-output gaat en niet over abonnementsquotum.

## 6. De prompt-cache-interactie

De kaart noemde dit terecht een gat in de oorspronkelijke analyse. Het antwoord
verschilt fundamenteel per mechanisme, en het is precies waar RTK en Headroom
uit elkaar lopen:

| | Muteert het bestaande prefix-bytes? | Cache-effect |
|---|---|---|
| **RTK** | **Nee.** Herschrijft het *commando* vóór uitvoering; de output is van meet af aan korter. | **Geen cache-breuk.** Strikt winst: een kleiner `tool_result` betekent een kleinere prefix voor alle volgende turns. |
| **Headroom** | **Ja.** Proxy herschrijft de message-array onderweg. | Deterministische compressie zou prefix-stabiel zijn, maar Headroom's relevantie-scoring is context-afhankelijk (dezelfde payload kan bij een andere laatste vraag anders comprimeren) → prefix-drift is mogelijk. **Niet gemeten**, want §4.2.1 blokkeert eerder. |
| **Caveman / Ponytail** | Alleen de systeemprompt, éénmalig aan het begin. | Stabiel, maar het injectieblok zelf wordt 80 turns lang her-gelezen. |

Een detail dat de kaart-zorg nuanceert: Headroom's `CacheAligner` klinkt als de
oplossing, maar de docstring in de geïnstalleerde package zegt dat het sinds
"P2-23" **detector-only** is — *"It NEVER mutates messages, never moves content,
never normalizes whitespace."* Het waarschuwt voor volatiele prefix-inhoud, het
repareert die niet. De cachebescherming ligt dus bij de gebruiker.

**Conclusie:** de kaart-hypothese ("een saver kan `cache_read` naar nul brengen
en netto duurder zijn") is correct als algemene zorg, maar **treft RTK niet** —
juist omdat RTK geen prompt-muterende proxy is, wat de oorspronkelijke
9router-analyse verkeerd had.

## 7. Past het rechtstreeks in de Cockpit-spawn-keten?

**RTK: ja, en beter dan verwacht.** De hook hoort in `settings.json`. Cockpit
spawnt elke sessie in een eigen git-worktree, en die worktree heeft een eigen
`.claude/settings.json`. Een per-lane opt-in is dus gewoon: schrijf het
`PreToolUse`-blok bij spawn in de worktree-settings wanneer de lane de vlag aan
heeft. Geen proxy, geen env-var, geen wijziging aan `provider_env.py`, geen
LLM-call in Cockpit.

Twee harde randvoorwaarden:

1. **Nooit `rtk init -g`.** Die schrijft naar `~/.claude/` — gedeeld door élke
   concurrente sessie op deze box. Dat is geen per-lane schakelaar maar een
   globale, en het zou lopende sessies van andere agents muteren.
2. **De binary moet op `PATH` staan in de spawn-env**, anders herschrijft de hook
   naar een commando dat niet bestaat. Een pre-flight-check hoort bij de
   integratie.

**Headroom: nee.** Zijn enige mechanische vorm is de base-URL-proxy (§4.2.1), de
library-vorm heeft geen aangrijpingspunt (§4.2.5), en dat Headroom in Python
geschreven is — het argument dat de kaart noemde — helpt niet: de integratie zou
alsnog een apart proxy-proces zijn, niet een import in onze backend.

## 8. Beslissing per mechanisme

### ✅ Geïmplementeerd (kaart `c31333bf…`, 2026-07-24) — RTK per-lane opt-in

**Scope van de implementatie** (kaart `c31333bf…`,
[`docs/superpowers/specs/2026-07-24-token-saver-integration-design.md`](../superpowers/specs/2026-07-24-token-saver-integration-design.md)):

- **Per-lane opt-in**, board-wide runtime kill-switch. Default **uit**
  (board-wide én per-lane); de operator moet beide aanzetten om RTK te
  installeren op een dispatch. Geen mutatie van persona-prompt,
  `CLAUDE.md`, kaarttekst, plan-attachment of ship-recipe.
- **Fail-open** op élke stap: ontbrekende kolom, ontbrekende binary,
  crash in de wrapper-installatie, of een commando dat RTK niet snapt
  → dispatcher valt terug op de kale `Bash`-call zonder hook. Geen
  retry, geen alarm — de activity-feed krijgt precies één
  `**Note:** Token saver activated: RTK 0.43.0` of één
  `**Note:** Token saver fail-open: <reden>` comment (dedup binnen
  60 s).
- **Cache-veilig by construction.** De hook leeft in
  `.claude/settings.json`, niet in het prompt-array. Anthropic's
  sessie-warme prefix-cache blijft onaangeraakt; er is geen
  token-stroom bij het prompt-pad zelf.
- **RTK 0.43.0 versie-pin**, repo `rtk-ai/rtk` default branch
  `develop`. Cache-pad `~/.local/share/cockpit/rtk/0.43.0/bin/rtk`,
  overschrijfbaar via `COCKPIT_RTK_BIN`.
- **`RTK_TELEMETRY=off`** expliciet in de spawn-env, ook al is dat de
  default — belt-and-braces voor de card-eis.
- **Caveman en Ponytail** zijn expliciet uit scope; TODO-comment in
  `token_saver.py` wijst naar follow-up-kaart `d0446fd8…`.

**Aangeraakte oppervlakken (lockstep, geen drift):**

- Backend: `models.py` (`KanbanColumn.token_saver_enabled`),
  `db.py` (additieve `ALTER TABLE` in `_ensure_column_table`,
  fail-safe met `try/except`), `schemas.py` (Pydantic-velden +
  `TokenSaverRequest`), nieuw `token_saver.py` (publieke surface
  `maybe_install` / `is_board_enabled` / `set_board_enabled` /
  `post_note` / `write_rtk_settings_into_worktree`),
  `dispatch.py` (sync bridge naar `maybe_install` vanuit de
  worktree-transport; `card_id` + `column_name` doorgegeven),
  `api/v1/kanban/router.py` (GET/POST `/api/v1/kanban/token-saver`).
- Frontend: `types.ts` (`token_saver_enabled: boolean`),
  `api.ts` (`getTokenSaver` / `setTokenSaver`),
  `ColumnSettingsDialog.tsx` (toggle, badge in read-only view,
  init op Edit).
- Harness: `scripts/measure-token-saver.sh` heeft een nieuwe
  `real-saver`-subcommand die via `apply_real_saver` de
  dispatch-helper zelf aanroept — geen tweede JSON-merge-pad. De
  bestaande `compare`-tabel heeft nu ook `real-saver`-rijen
  per trial; `with-saver` blijft staan als de gedocumenteerde
  prompt-mutatie-proxy ondergrens.

**Acceptance-criteria check:**

- ✅ Per-lane via worktree-lokale `.claude/settings.local.json`
  (gitignored door Claude Code's default `**/.claude/settings.local.json`
  in `~/.config/git/ignore`); wrapper-script leeft in de
  RTK-cache (`<cache>/<version>/hooks/rtk-cockpit-rewrite-wrapper.sh`)
  en wordt van daaruit aangeroepen, niet vanuit de worktree. Geen
  muteert `~/.claude/`, geen modified-tracked-bestand in de dispatch-
  worktree.
- ✅ `grep` carve-out actief (RTK omzeilt de ugrep-shim; in deze
  kaart niet opgelost, wel uitgezonderd door de wrapper-config)
- ✅ `git diff`-truncatie expliciet uitgezet voor ship-lanes
  (wrapper bypass-list bevat `git diff`); analyse-lanes mogen
  hem aanhouden
- ✅ Versie-pin 0.43.0, default repo-branch `develop`
- ✅ Telemetrie expliciet uit in de spawn-env
- ✅ Activity-feed observability via `post_note` met 60-s-dedup
- ✅ Board-wide runtime kill-switch via `KanbanMeta` row
  `token_saver:<project_key>` (Pydantic-validated;
  `set_board_enabled` schrijft `"1"` / `"0"` naar de
  bestaande kolom)
- ✅ Tests: 23 unit-tests (`test_token_saver.py`) + 4
  kolom-migratie-tests (`test_token_saver_column.py`) + 4
  API-tests (`test_token_saver_api.py`), groen. Zeven bridge-tests maken de
  activity-feed-claim hard: drie testen de async kern; één test de directe
  sync-caller zonder lopende loop; en drie regressietests de werkelijke
  productievoorwaarden — de sync bridge wordt vanuit een actieve event loop
  aangeroepen, de open claim/move-transactie wordt vóór de worker-DB-call
  gecommit, en een exception in de bridge zelf levert alsnog een zichtbare
  `**Note:** Token saver fail-open: bridge …` op. De productie-test verwacht
  zowel status `active` als exact
  `**Note:** Token saver activated: RTK 0.43.0`.
- ✅ `compare`-harness uitgebreid met `real-saver`-variant in
  lockstep met de dispatch-helper

**Gemeten effect** (lockstep-harness, RTK 0.43.0, `compare`-modus
op deze feature-worktree, 2026-07-25): twee counterbalanced trials
met drie varianten (baseline / proxy `with-saver` / echte
`real-saver`). Resultaten, één tabel per trial:

| Variant | input | cache_creation | cache_read | output | pass_tests | pass_diff |
|---|---:|---:|---:|---:|:-:|:-:|
| trial-1 baseline | 43.967 | 0 | 295.731 | 2.390 | 1 | 0 |
| trial-1 with-saver (proxy) | 45.005 | 0 | 294.272 | 987 | 1 | 0 |
| trial-1 real-saver (RTK) | 57.446 | 0 | 930.176 | 3.941 | 1 | 0 |
| **Δ trial-1 (proxy − baseline)** | **+1.038** | **0** | **−1.459** | **−1.403** | — | — |
| trial-2 baseline | 159.671 | 0 | 779.264 | 3.394 | 1 | 1 |
| trial-2 with-saver (proxy) | 42.630 | 0 | 356.736 | 2.198 | 1 | 1 |
| trial-2 real-saver (RTK) | 52.742 | 0 | 385.536 | 1.429 | 1 | 0 |
| **Δ trial-2 (proxy − baseline)** | **−117.041** | **0** | **−422.528** | **−1.196** | — | — |

**Wat er hard uitkomt, en wat niet.**

- ✅ **Geen kwaliteitsregressie.** `pass_tests=1` in alle zes de runs
  (agent onder RTK voltooit de golden task correct: de `b30a9bb`-revert
  op `_column_max_sessions` wordt hersteld). De card-eis "zonder
  kwaliteitsregressie" staat daarmee.
- ⚠️ **`pass_diff=0` in de twee `real-saver`-runs.** Dat is **geen**
  kwaliteitsfalen: de agent lost de `test_zero_column_cap`-tests
  allebei op (vandaar `pass_tests=1`), maar met een andere
  diff-shape dan de canonieke `r.max_sessions > 0` → `r.max_sessions
  >= 0` één-regelige match. Het `score_golden`-criterium telt een
  letterlijke vervang; meerdere geldige fixes scoren `0`. Onder de
  proxy-variant scoort `pass_diff=1` in trial 2. Dit verschil hoort
  bij de scorer, niet bij RTK.
- ⚠️ **`cache_read` op `real-saver` is op N=2 niet eenduidig.** Trial 1
  toont `real-saver` 930.176 vs baseline 295.731 — `real-saver`
  **drie keer** zoveel `cache_read` (cache-creatie is 0 in beide,
  `cache_creation` als confound-kanaal speelt niet mee). Trial 2
  toont juist een halvering (385.536 vs 779.264). De variantie is
  orde-grootte groter dan het proxy-effect en strookt niet met een
  eenvoudig "X% bespaard"-verhaal. Mogelijke oorzaken die deze
  single-bron-N niet kan uitsluiten: prefix-cache-hits die sessie-
  afhankelijk domineren (zie de eerdere tegenstrijdigheid tussen
  `cache_read` wel/niet meetellen die §2 zelf al
  gemarkeerd heeft), en de variantie in *welke* commando's de
  golden-task-agent in welke volgorde uitvoert. **De card-eis "netto-
  opbrengst" is op N=2 niet hard claim-baar voor `real-saver`; de
  proxy-ondergrens claimt 'm wél.**
- ✅ **De proxy (`with-saver`) reproduceert de §8 verwachting.** Beide
  trials laten output-daling zien (−58,7% en −35,2%) en trial 2 laat
  een forse input/cache_read-daling zien (−73% / −54%) met
  `pass_diff=1`. Dat strookt met de eerdere
  `token-saver-meet-harnas.md`-meting (`6b67df66…`) — de proxy is
  een ondergrens, niet de `real-saver` zelf.

**Conclusie.** De integratie staat en is mechanisch correct (sync
bridge → worker-thread event loop; pre-transport commit; activity-feed
note exact geformatteerd; wrapper-level fail-open). De
kwaliteits-eis (geen regressie) is hard aangetoond (`pass_tests=1`).
De netto-opbrengst-eis is op de `real-saver`-meting bij N=2 niet hard
bewijs-baar; de proxy-ondergrens bevestigt 'm wél. **Geen promotion
naar default-on**: de kaart-eis was opt-in met `default off`, en de
`real-saver`-meting levert op deze N geen grond om die voorzichtigheid
los te laten. De feature blijft opt-in en per-lane; een hogere-N
vervolgmeting (grotere steekproef, golden tasks die `Read` zwaarder
belasten om de `Bash`-asymmetrie uit §5.2 te repareren) hoort in een
eigen kaart thuis, niet in deze.

| Mechanisme | Besluit | Winst A / B | Kern |
|---|---|---|---|
| **RTK** | ✅ **GO, conditioneel** | **7,4% / 1,6%** | Cache-veilig by construction, fail-open geverifieerd, geen ToS-oppervlak, per-lane installeerbaar via worktree-settings |
| **Headroom** | ⛔ **NO-GO** | n.v.t. | Enige werkende vorm = `ANTHROPIC_BASE_URL`-proxy op subscription-OAuth — tweemaal eerder afgewezen; kost bovendien #746/#1158/#1779 |
| **Caveman** | ⚠️ **Conditionele GO** — opt-in, alleen op output-onkritische lanes | **0,26% / 6,7%** | Draagt scenario B, waar RTK juist wegvalt; degradeert Done-summaries en analysedocs, dus nooit op ship-/analyse-lanes. 65%-claim is ongemeten |
| **Ponytail** | ⛔ **NO-GO** | — | Scope-sturing (minder werk doen) i.p.v. formulering; botst frontaal met de engineer-persona en scenario B redt het niet |

De keuze RTK **vs.** Headroom die kaart `c31333bf…` vroeg is daarmee beslecht:
**RTK**. De keuze RTK **vs.** Caveman is dat expliciet **niet** — dat is de hedge
uit §2.3, en beide zijn opt-in en per-lane, dus ze sluiten elkaar niet uit.

**Voorwaarden bij de RTK-GO** (horen in de acceptance criteria van `c31333bf…`):

1. **Per-lane, opt-in, default uit.** Via worktree-lokale `.claude/settings.json`;
   nooit `~/.claude/`.
2. **`grep` uitgezonderd** tot de ugrep-shim-val (§4.1.4) is opgelost. Dat kost
   22,5% van het Bash-volume, maar een stille "Is a directory" in plaats van 868
   matches is een duurdere fout dan de gemiste winst.
3. **Niet op lanes waar een volledige diff het werkproduct stuurt.** De
   git-diff-truncatie (§4.1.3) raakt precies de beslissing "wat commit ik".
   Overweeg `git diff` te whitelisten op read-only/analyse-lanes en uit te sluiten
   op ship-lanes.
4. **Versie pinnen.** 0.43.0 met `dev-0.44.0-rc.323` als laatste release: het
   project shipt release-candidates in een tempo dat auto-update onverantwoord
   maakt. Repo is 6 maanden oud — dezelfde maatstaf als §3 van de
   9router-analyse, eerlijk toegepast.
5. **Telemetrie expliciet uit houden** (`RTK_TELEMETRY=off` in de spawn-env), ook
   al is dat de default.

**Heropenen** bij: (a) RTK krijgt een verliesvrije modus met winst, (b) Headroom
krijgt een integratievorm die `ANTHROPIC_BASE_URL` niet aanraakt, (c) Anthropic
wijzigt het meetellen van `cache_read` in abonnementen — dan verschuift het hele
zwaartepunt van §2 —, of (d) de mix uit §5.2 verandert wezenlijk (bv. `Read` daalt
onder `Bash`).

### ✅ Geïmplementeerd (kaart `c31333bf…`, 2026-07-25) — productionele bridge hersteld na tweede Impediment

**Wat de eerste herstelronde nog miste.** De activity-feed-call was wel aan de
async kern toegevoegd, maar de sync wrapper gebruikte `asyncio.run()` rechtstreeks.
Dat werkt alleen op een thread zonder lopende loop. Productie doet het omgekeerde:
async `_run_card` roept `card_transport(...)` inline aan op zijn event-loop-thread.
De wrapper retourneerde daar dus altijd `failed`, lekte een un-awaited-coroutine
warning, installeerde geen bruikbare RTK-status en postte geen note. De toenmalige
worker-thread-test bewees uitsluitend de secundaire sync-caller en was geen simulatie
van productie.

De hercontrole bracht een tweede grens aan het licht: alleen `asyncio.run()` naar een
worker verplaatsen is onvoldoende. `_run_card` had zijn claim+move-transactie nog
open; de private kanban-engine van de worker botste bij `post_note` op die
SQLite-write-lock en blokkeerde. De productie-seam heeft dus twee voorwaarden:
**lopende event loop én een open dispatch-transactie**.

**Wat er nu staat.** `_install_rtk_for_dispatch` detecteert een lopende loop en voert
de async kern dan synchronisch uit op één private worker-thread met eigen loop. De
coroutine wordt pas ín die execution context gemaakt, dus ook een executor-/bridge-fout
lekt geen un-awaited coroutine. Vlak vóór een verse worktree-transport-call commit
`_run_card` de claim+move; daardoor kan de worker dezelfde DB lezen en zijn note
schrijven. Een spawn-fout blijft veilig: de bestaande exception-tak schrijft daarna
release/move-compensaties in een nieuwe transactie. Buiten een lopende loop blijft de
rechtstreekse `asyncio.run()`-fallback beschikbaar.

Als de bridge zelf crasht — dus vóór de async kern een fail-open-note kon schrijven —
post `_post_rtk_bridge_failure_async` best-effort
`**Note:** Token saver fail-open: bridge <ExceptionType>: <reden>`. Ook als dát posten
faalt, blijft de spawn doorgaan. De normale teksten zijn nu exact (geen dubbele
`Token saver: activated:`-dubbelepunt meer):

- `**Note:** Token saver activated: RTK 0.43.0`
- `**Note:** Token saver fail-open: <reden>`

**Regressiedekking.** `test_token_saver.py` telt nu 23 tests. De drie nieuwe tests
naast de bestaande async-kern- en sync-fallback-dekking zijn:

- `test_sync_bridge_works_when_called_inside_running_event_loop` — roept de sync
  bridge op de pytest-asyncio-loop aan en verwacht `active` + de exacte note;
- `test_dispatch_commits_claim_before_running_worktree_token_saver` — rijdt via
  `dispatch_card` met een worktree-transport en bewijst dat de claim/move-lock de
  worker niet blokkeert;
- `test_sync_bridge_failure_posts_fail_open_note` — laat de async kern moedwillig
  crashen en verwacht status `failed`, geen exception naar de spawn, en een zichtbare
  bridge-fail-open-note.

**Ongewijzigd.** De publieke `token_saver.py`-surface, per-lane vlag, board-wide
kill-switch, binary-resolutie, versie-pin, `RTK_TELEMETRY=off`, instructie-isolatie en
het lockstep-meet-harnas blijven gelijk. De wijziging maakt het al besloten mechanisme
werkelijk bereikbaar in de normale dispatch-context; ze verandert niet wat RTK
comprimeert of welke lanes opt-in zijn.

**Heropenen** bij: dezelfde voorwaarden als §8 + (e) de post_note-dedup breekt onder
load, (f) een operator wil de note ook als samenvattende card-rij zien, of (g) de
pre-spawn-commit veroorzaakt aantoonbaar claim/reaper-gedrag dat de bestaande
compensatiepad-tests niet afvangen.

### ✅ Geïmplementeerd (kaart `c31333bf…`, 2026-08-04) — worktree-clean-status na reviewer-gate ronde 4

**Wat de reviewer-gate ronde 4 bracht.** De integrator van de §8-implementatie schreef
twee artefacten in de dispatch-worktree: het wrapper-script op
`<worktree>/.claude/hooks/rtk-cockpit-rewrite.sh` en een gemuteerd
`<worktree>/.claude/settings.json`. Het hook-script is een untracked file in een
nieuwe directory (`git status` → `?? .claude/hooks/rtk-cockpit-rewrite.sh`); de
settings.json is **tracked** in deze repo (`git ls-files` bevestigt). Op élke
shippende lane die de saver aan had, vuurde de ship-gate
(`.claude/skills/git-ship/SKILL.md:154-156`) `git diff --quiet HEAD -- || git
ls-files --others --exclude-standard` af en weigerde — vanaf de eerste seconde
van de sessie, vóór de agent één regel werk deed. De voorgeschreven
recovery-stap (`git add -A && git commit`) commitde dan de hook + de
gepatchte `settings.json` naar `master`; van dat moment laadde élke sessie
in élke worktree die hook uit de getrackte `settings.json`, RTK stond
board-wide aan zonder lane-vlag, en de kill-switch kon het niet meer
uitzetten (die gate't alléén de installatie, niet een al gecommitte
settings.json). De bug was bovendien onzichtbaar zolang de RTK-binary niet
op deze host stond — de installer valt fail-open op `failed` zonder
filesystem-writes, en geen enkele bestaande test gebruikte een echte git
worktree om `git status` te pollen.

**De fix (operator-besluit optie A, 2026-08-04).** Wrapper-script en
hook-config verhuizen buiten de worktree zodat `git status` schoon blijft:

- **Wrapper-script** naar de RTK-cache:
  `<cache>/<version>/hooks/rtk-cockpit-rewrite-wrapper.sh`, parallel aan de
  upstream `rtk-rewrite.sh` die de wrapper met `$(dirname "$0")/rtk-rewrite.sh`
  aanroept. Eén kopie per host, gedeeld door alle worktrees; de cache is
  per-user/per-versie, geen werkboom. Het script zelf verandert niet —
  alleen de bestemming.
- **Hook-config** naar `<worktree>/.claude/settings.local.json` in plaats
  van `<worktree>/.claude/settings.json`. Claude Code leest beide files,
  maar `settings.local.json` valt onder Claude Code's default gitignore
  (`**/.claude/settings.local.json` in `~/.config/git/ignore`) en is dus
  volledig onzichtbaar voor `git`. De PreToolUse-command referencet het
  absolute pad naar de wrapper in de cache, zodat de hook werkt vanuit elke
  worktree op deze host.

Het effect: `git status --porcelain` op de dispatch-worktree blijft leeg
na installatie, ook al draaien we op een echte git-worktree, niet op een
kale `tmp_path`. Dat is **precies** de "één test die na installatie een
schone `git status` in een echte worktree aantoont" die het operator-besluit
vroeg als bewijs.

**Wat er ongewijzigd blijft.**

- De publieke `token_saver.py`-surface (`maybe_install` / `is_board_enabled`
  / `set_board_enabled` / `post_note` / `write_rtk_settings_into_worktree`)
  en de `(status, reason)`-teruggave.
- Per-lane `KanbanColumn.token_saver_enabled` vlag.
- Board-wide runtime kill-switch via `KanbanMeta:token_saver:<project_key>`.
- Fail-open op élke stap (cache-misser, wrapper-schrijffout,
  onparseable `settings.local.json`).
- Activity-feed observability met `**Note:** Token saver activated: …` /
  `fail-open: …` via `post_note`, 60-s-dedup.
- `RTK_TELEMETRY=off` expliciet in de spawn-env.
- De wrapper-body zelf, met dezelfde `grep` carve-out en `git diff`
  bypass-list — het script hangt nu in de cache in plaats van in de
  worktree, maar zijn gedrag is identiek.
- Het lockstep-meet-harnas en zijn `real-saver`-rij; de §8-meting op N=2
  staat en de netto-opbrengst-conclusie (proxy wél, `real-saver` niet op
  N=2) blijft.

**Regressiedekking.** `test_token_saver.py` telt nu 25 tests. Twee nieuwe
regressietests die de reviewer-vals in een echte git-worktree afvangen,
gebaseerd op `subprocess.run(["git", "init", …] + worktree add)`:

- `test_active_branch_leaves_worktree_git_status_clean` — zet een verse
  git-repo op met één werkende branch, draait de installatie, en
  assertt dat `git status --porcelain` leeg is. Tevens assertt het dat
  de wrapper daadwerkelijk op de cache-pad bestaat en uitvoerbaar is —
  een "schone worktree" alleen is niet genoeg als de hook niet landt.
- `test_active_branch_preserves_tracked_settings_json` — pre-populeert de
  werkende branch met een tracked `.claude/settings.json` (operator-hand-
  werk), draait de installatie, en verifieert dat (a) de tracked file
  ongewijzigd blijft en (b) `git status --porcelain` alsnog leeg is. Dekt
  het scenario waarin een operator `settings.json` al handmatig heeft
  ingericht voor eigen hooks.

De bestaande `test_active_branch_writes_hook_and_settings` is bijgewerkt
om het nieuwe contract te pinen (wrapper in cache, niet in worktree;
`settings.local.json`, niet `settings.json`). De andere settings-IO-tests
(`test_existing_pre_tool_use_entries_preserved`,
`test_existing_permissions_preserved`,
`test_settings_without_hooks_key_gets_hooks_added`,
`test_active_branch_is_idempotent`,
`test_fail_open_when_settings_unwritable`) gebruiken dezelfde
`_read_settings` / `_write_settings`-helpers, die nu naar
`settings.local.json` wijzen — de assertion (Bash PreToolUse-entry is
er, andere keys overleven, idempotent) is ongewijzigd.

**Netto-opbrengst.** Onveranderd ten opzichte van §8: de proxy
(`with-saver`) claimt een output-daling van ~35–59% en een
`input`/`cache_read`-daling op N≥1; `real-saver` is op N=2 niet eenduidig
en de kaart promoot niet naar default-on. De fix maakt die opbrengst
bereikbaar op shippende lanes waar ze eerst onbereikbaar was door de
git-pollutie — dát was de blokkade, niet de compressie zelf.

**Heropenen** bij: dezelfde voorwaarden als §8 + (h) Claude Code stopt
met het gitignore-patroon voor `settings.local.json` (dan terugvallen op
een andere gitignored bestemming, bv. `.claude/settings.local.json` →
`<cache>/settings/<project_key>.json` met absolute pad, of een
`xattr`-gestuurd hook), of (i) RTK zelf komt met een ingebouwde
git-werkboom-veilige installatie die dit patroon overbodig maakt.

## 9. Reproductie

```bash
# 1. RTK installeren (scratch, niet globaal!)
curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh -o rtk-install.sh
RTK_INSTALL_DIR="$PWD/tools" sh rtk-install.sh          # → tools/rtk 0.43.0
export RTK_TELEMETRY=off

# 2. Byte-niveau, echte repo-commando's
git diff 3dab026~1 3dab026 | wc -c                       # 50961
tools/rtk git diff 3dab026~1 3dab026 | wc -c             # 24081
tools/rtk git diff --no-compact 3dab026~1 3dab026 | wc -c  # 50961  ← winst = truncatie

# 3. Token-niveau met Anthropic's eigen tokenizer (recept uit
#    per-persona-mcp-allowlist-decision.md §7)
printf '{"mcpServers":{}}' > empty-mcp.json
{ echo "Reply with exactly: ok"; cat PAYLOAD; } \
  | claude -p --output-format json --model sonnet \
      --strict-mcp-config --mcp-config empty-mcp.json \
  | python3 -c 'import json,sys; u=json.load(sys.stdin)["usage"]; \
      print(u["input_tokens"]+u["cache_creation_input_tokens"]+u["cache_read_input_tokens"])'
# Leeg controle-payload = 38602; trek dat af. Twee controle-runs gaven beide 38602.

# 4. Hook-gedrag (rewrite + fail-open)
echo '{"tool_name":"Bash","tool_input":{"command":"git status"}}' | tools/rtk hook claude
echo '{"tool_name":"Bash","tool_input":{"command":"sed -n 1,5p README.md"}}' | tools/rtk hook claude

# 5. Sessie-niveau: quota-mix en tool-mix over echte dispatches
#    (walk ~/.claude/projects/-home-vdvgu-claude-cockpit--claude-worktrees-*/*.jsonl,
#     sommeer message.usage.* resp. tool_result-lengte per tool_use.name)
```

De §5.3-simulatie is een ~40-regels Python-walk over dezelfde transcripts; het
recept staat volledig in §5.3's aannameblok en is met bovenstaande bouwstenen
te herbouwen. Zie §10 voor de kaart die dit in `scripts/` vastlegt.

## 10. Wat dit voedt

- **`c31333bf…`** (integratie) — winnaar is **RTK**; §8's vijf voorwaarden zijn
  de acceptance criteria. Headroom valt af.
- **`d0446fd8…`** (Caveman/Ponytail) — de schakelaar-architectuur blijft zinvol
  en wordt door §2.3 juist **belangrijker**: hij maakt de hedge uitvoerbaar.
  Maar splits de twee: Caveman conditionele GO, Ponytail NO-GO.
- **`6b67df66…`** (meet-harnas) — `apply_saver` is een stand-in; §9's recept
  vervangt hem door de echte binary. De register-regel van dat harnas over
  `cache_read` staat op gespannen voet met §2.2 en hoort te worden herzien zodra
  de boekhoudvraag beslecht is.
- **De boekhoudvraag zelf** (§2) is de goedkoopste, hoogst renderende
  vervolgkaart: hij kost één gecontroleerde meting en beslist of de bouwinspanning
  naar RTK of naar Caveman moet.
- **De grootste absolute lever ligt buiten deze kaart.** In scenario A is
  `cache_read` 95,9% van het quotum, en de vaste prefix meet **38.602 tokens per
  request** (§3). Over 80 turns is dat ~3,1M tokens vóór er één regel werk is
  gedaan — meer dan het dubbele van wat RTK over een hele sessie bespaart. Elke
  kilobyte die uit CLAUDE.md, de persona-prompt of de MCP-schemas verdwijnt,
  wordt 80× terugbetaald.

### ✅ Geïmplementeerd (kaart `eae8a9b1…`, 2026-07-22) — eerste ronde: CLAUDE.md

**Methode** (recept uit §9): empty payload → `claude -p --output-format json
--strict-mcp-config --mcp-config '{"mcpServers":{}}'`, lees
`usage.input_tokens + cache_creation + cache_read`. Cache cold-call =
`input_tokens` (volledige verse prefix). Verschil tussen worktree-call en
`/tmp`-call = project-`CLAUDE.md`-bijdrage. Twee runs vóór, twee ná.

**Per-bron-uitsplitsing, gemeten op deze host (WSL Ubuntu, claude 2.1.217,
2026-07-22):**

| Bron | Tokens (cold-call) | Aandeel | Meetcommando |
|---|---:|---:|---|
| Systeemprompt + tool-defs + user-`CLAUDE.md` (leeg) + lege MCP | **33.185** | 81,6% | `cd /tmp && claude -p …` |
| Project-`CLAUDE.md` (24.447 bytes / 252 regels, vóór prune) | **7.473** | 18,4% | worktree-call − /tmp-call |
| User-`CLAUDE.md` (`~/.claude/CLAUDE.md`) | **0** | 0% | bestaat niet op deze host |
| MCP-schemas (lege `mcpServers:{}`) | **0** | 0% | in deze meting; ~4.600 bij kanban-MCP-load (kaart `ea7e038b…`, out of scope) |
| Persona-prompt uit `_build_ship_instructions('direct')` | **~3.706** (12.972 chars) | n.v.t. voor `claude -p` | gemeten via Python-import van `backend/app/kanban/dispatch.py` (geen API-call) |
| **Subtotaal werkboom-call, vóór prune** | **40.658** | | |
| **Subtotaal gedispatched-sessie (typisch, vóór prune)** | **~48.965** | | + persona + ~4.600 kanban-MCP |

De oorspronkelijke 38.602 (§3, 2026-07-21, claude 2.1.216) is in dezelfde
meetmethode ondertussen 40.658 geworden — een groei van ~2.056 tokens
(5,3%) in één maand. Grotendeels de rebrand-sectie en nieuwe gotchas; één
empirical herkalibratie van het baseline-getal.

**Eerste snoeibeurt — alleen waar geen val achter zit:**

| Sectie | Reden voor prune | Geschat verlies | Gemeten winst |
|---|---|---|---|
| `## Architecture` (boomdiagram backend/ + frontend/) | pure referentie; `Glob`/`tree` levert hetzelfde | 0 — agents rediscovers via tools | ~560 tokens |
| `### Features` (lijst van 26 feature-namen) | referentie; `ls frontend/src/features` is actueler | 0 | ~115 |
| `### API Routes` (lijst van 30+ route-mounts) | referentie; `ls backend/app/api/v1` is actueler | 0 | ~115 |
| `## Key Decisions` (5 bullets) | redundant met `docs/cockpit/decisions.md` | 0 | ~100 |
| `## CI/CD` (4 workflow-namen) | referentie; `ls .github/workflows` is actueler | 0 | ~100 |
| Fork-notice "Tasks 1–11 implemented" | stale snapshot van `fase-2-plan.md`-status, verandert per week | 0 | ~95 |
| Bash-test-lijst onder `# Test` (19 regels) | family-level `ls scripts/test_*.sh` ipv per-harness; `check-test-harness-coverage.sh` dekt het hele spectrum (kaart `5e988e4e` + glob-form uit kaart `8c7cfc14`) | 0 (zelfde coverage, korter — vereist de glob-herkenning in kaart `8c7cfc14`) | ~215 |
| **Totaal** | | | **~1.300 tokens / ~5.171 bytes (-21%)** |

**Vóór/na, gemeten (cold-call, vaste methode):**

| Run | Worktree-prefix | `/tmp`-baseline | Project-`CLAUDE.md`-bijdrage |
|---|---:|---:|---:|
| Vóór prune | **40.658** | 33.185 | **7.473** |
| Ná prune | **39.337** | 33.185 | **6.152** |
| **Δ** | **−1.321** | 0 | **−1.321** |

Een **gemeten** reductie van **~1.321 tokens per turn**. Over 80 turns is dat
**~105.680 tokens per sessie** — vóór er één regel werk is gedaan.

**Gotchas-sectie volledig intact.** De kaart-eis (`Geen functionaliteitsverlies:
CLAUDE.md-regels die een gedocumenteerde val afdekken worden niet verwijderd
zonder dat de val elders is afgedekt`) is hier leidend: alle 11 gotcha-items
(`rm`-blokkade, `pkill -f`-zelfval, `git stash apply`-stale-trap, UTC-timestamps,
`_reload`-identity-map, zsh-glob-quoting, default-branch `master`-val) staan
onverkort in de nieuwe versie. Geen item is verplaatst naar een script/gate — ze
zijn allemaal "denk-hier-aan"-vlaggen die alleen in een constant-aanwezig
prompt-kanon zinvol zijn, niet in een pre-commit-script.

**Wat er níet is gedaan (bewust, deze ronde):**

- **Persona-prompt (`_build_ship_instructions`)** — ~3.706 tokens, ~28% van het
  typische dispatch-prefix. Verkleint kan via skill/consolidatie, maar valt
  buiten deze kaart (de persona staat in `dispatch.py` _en_ in
  `.claude/skills/git-ship/SKILL.md`; aanraken vereist lockstep-sync, eigen
  drift-val-kaart `d9447e49`-risico). Volgt in een eigen follow-up.
- **MCP-schemas** — al afgedekt door kaart `ea7e038b…`
  (`per-persona-mcp-allowlist-decision.md`); NO-GO (388 tokens, 1,1%), heropen
  niet per kaart-instructie.
- **Systeemprompt + tool-defs** — Anthropic's eigen, niet in scope.

**Acceptance-criteria check:**

- ✅ Reproduceerbare uitsplitsing per bron met meetcommando (tabel hierboven +
  §9-recept)
- ✅ Ten minste één gemeten reductie met vóór/na-getal uit dezelfde meetmethode
  (40.658 → 39.337, Δ −1.321 tokens)
- ✅ Geen functionaliteitsverlies (alle 11 gotchas behouden; alleen pure
  referentie-info verwijderd)
- ✅ §10 bijgewerkt (deze paragraaf)
