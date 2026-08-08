---
title: "Analyse — waarom sessies op hun abonnementslimiet blijven hangen (en de auto-dispatcher ze niet terugpakt)"
type: analysis
status: active
---

# Analyse — waarom sessies op hun abonnementslimiet blijven hangen

**Datum:** 2026-07-23
**Kaart:** `92734942b9d34516845fa882993a42ec` "Analyse - sessie limiet vs auto dispatch"
**Scope:** read-only bevindingen op `master` (worktree `k-analyse-sessi-56d6`), aangevuld
met een meting op de echte transcript- en backend-log-historie van deze box.

**Trigger (gebruiker):**
> "Al meerdere malen dit pogen te fixen, maar het zit nog steeds niet goed. Sessies lopen
> nog steeds tegen hun subscriptie sessie limiet aan zowel minimax als anthropic. Deze
> blijven dan staan, ik moet ze manueel gaan herstarten na hun limiet. Zorgt voor heel wat
> overhead, en zaken die maar half afgewerkt raken."

Verwant: [`subscription-pool-dispatch-analyse.md`](./subscription-pool-dispatch-analyse.md),
[`subscription-flexibiliteit-analyse.md`](./subscription-flexibiliteit-analyse.md),
[`kanban-dispatch-spec.md`](./kanban-dispatch-spec.md),
[`headless-stream-json-transport-spike.md`](./headless-stream-json-transport-spike.md),
[`9router-integratie-analyse.md`](./9router-integratie-analyse.md) (§ "mid-sessie-failover").

---

## 0. TL;DR

**De vorige fixes zijn niet fout — ze zitten alleen allemaal aan de verkeerde kant van het
gat.** Cockpit heeft een uitgebreide, goed geteste *reactie* op een limiet (per-provider
pause, `To Resume`-move, spillover-router, activity-comments). Wat ontbreekt is het
**signaal**: de gebeurtenis "deze sessie heeft zijn limiet geraakt" bereikt de backend
vrijwel nooit.

Gemeten over 2026-07-15 → 2026-07-23 (§1):

| Meting | Waarde |
|---|---|
| Echte limiet-events in gedispatchte sessies | **65** (41 Anthropic 5h, 24 MiniMax Token-Plan) |
| Backend-log-regels van het hook-limietpad (`🚦 Rate-limit hit … moved to To Resume`) | **0** |
| Backend-log-regels van een spillover (`🔀 … spilling over`) | **0** |
| Reaper-opruimingen met limiet-classificatie | **1** — en dat was een **false positive** op een gezonde, werkende sessie |
| Mediane tijd tussen limiet-hit en volgende activiteit in dezelfde sessie | **5,7 h** (p75 11,0 h; max 32,4 h) |
| Sessies die na de limiet nooit meer iets deden | **7 van 65** |

Detectiegraad is dus in de praktijk **≈ 0%**, en de kosten zijn niet
theoretisch. Op 2026-07-22 om 13:22 raakten drie sessies tegelijk de
Anthropic-limiet (reset 19:50). Hun transcripts hervatten pas de volgende
ochtend rond 08:00 — ~12 uur stilstand op een reset die na 6,5 uur voorbij was.

De vier oorzaken, in volgorde van impact:

1. **Het primaire detectiekanaal bestaat niet in de werkelijkheid.** Cockpit luistert op de
   Claude Code `Notification`-hook. Een abonnementslimiet komt niet als Notification binnen
   maar als een gewoon assistant-bericht in het transcript met `isApiErrorMessage: true`
   (§2.1). Er is dus niks te classificeren.
2. **Het secundaire kanaal (reaper pane-scan) kijkt alleen naar sessies die nog nooit een
   hook stuurden.** Een sessie die na twee uur werk zijn limiet raakt, is
   allang uit die verzameling. De pane-scan kan structureel alleen limieten
   in de eerste ~120 s vangen (§2.2).
3. **"Leeft" = "de tmux-sessie bestaat".** Een gelimiteerde sessie blijft open op het
   limiet-scherm, dus de claim wordt nooit vrijgegeven en de kaart komt nooit terug op het
   bord (§2.3). Er is geen enkele voortgangs- of stilstands-check.
4. **Waar de pane-scan wél toeslaat, matcht hij losse substrings (`"429"`, `"api error"`)
   op willekeurige pane-tekst** — één waargenomen geval waarin een gezonde, werkende sessie
   werd gekilld, dispatch 5 h gepauzeerd en de kaart als mislukt geteld (§2.4). Dit is
   letterlijk het "zaken die maar half afgewerkt raken" uit de kaart.

Daarnaast twee kleinere, maar echte gaten: de **weekly-limiet wordt niet herkend** en de
reset-tijd-parser mist de vorm zonder minuten (`resets 9pm`) (§3), en de **spillover naar
het tweede abonnement is dode code** omdat er geen subscription-pool geconfigureerd is
(§4).

Aanbevolen richting (§5): verplaats het signaal naar het **transcript** (structureel veld,
werkt voor beide vendors, mid-sessie én bij spawn), **hervat in de bestaande pane** in
plaats van kill+respawn, en voeg een **voortgangs-liveness** toe. Zo lost elke
vastgelopen sessie zichzelf op — ook de oorzaken die we nog niet opgesomd
hebben.

---

## 1. Meting — wat er werkelijk gebeurt

### 1.1 Hoe vaak, en met welke tekst

Alle limiet-events staan in de Claude Code-transcripts als assistant-berichten met
`isApiErrorMessage: true`. Over de volledige historie op deze box:

| Aantal | Tekst (genormaliseerd) | Vendor |
|---|---|---|
| 98 | `You've hit your session limit · resets N:Npm (Europe/Brussels)` | Anthropic |
| 54 | `API Error: Request rejected (429) · Token Plan usage limit reached: …` | MiniMax |
| 39 | `You've hit your session limit · resets N:Nam (Europe/Brussels)` | Anthropic |
| 13 | `You've hit your session limit · resets Nam (Europe/Brussels)` | Anthropic — **zonder minuten** |
| 7 | `You've hit your session limit · resets Npm (Europe/Brussels)` | Anthropic — **zonder minuten** |
| 4 | `You've hit your weekly limit · resets Npm (Europe/Brussels)` | Anthropic — **weekly** |

Beperkt tot het venster waarvoor we ook backend-logs hebben (2026-07-15 →):
**41 Anthropic + 24 MiniMax = 65 events**, gemiddeld ~8 per dag.

**Reproductie:**

```bash
cd ~/.claude/projects && python3 - <<'PY'
import json,glob
from collections import Counter
k=Counter()
for f in glob.glob('**/*.jsonl',recursive=True):
  for line in open(f,encoding='utf-8',errors='replace'):
    if 'isApiErrorMessage' not in line: continue
    try: d=json.loads(line)
    except Exception: continue
    if not d.get('isApiErrorMessage'): continue
    c=d.get('message',{}).get('content')
    t=' '.join(b.get('text','') for b in c if isinstance(b,dict)) if isinstance(c,list) else str(c)
    tl=t.lower()
    for name,needle in (('anthropic-5h','session limit'),
                        ('anthropic-weekly','weekly limit'),
                        ('minimax-tokenplan','token plan')):
      if needle in tl and (d.get('timestamp') or '')>='2026-07-15': k[name]+=1
print(dict(k))
PY
```

### 1.2 Hoeveel de backend ervan zag: niets

Elk pad dat op een limiet reageert logt op INFO/WARNING. Over álle backend-logs
(`logs/backend/run-*.log`, 2026-07-15 → 2026-07-23):

```bash
cd /home/vdvgu/claude-cockpit/logs/backend
for p in "Rate-limit hit" "spilling over" "unrecognized usage-limit" "hit a rate limit"; do
  echo "$p: $(grep -h "$p" *.log | wc -l)"
done
```

| Marker | Code-pad | Hits |
|---|---|---|
| `🚦 Rate-limit hit — session … moved to To Resume` | `move_limited_session_to_resume` (Notification-hook) | **0** |
| `🔀 … spilling over to the next subscription` | fase-2 spillover | **0** |
| `unrecognized usage-limit message format` | hook-pad, onparsebare reset-tijd | **0** |
| `stuck session … hit a rate limit` | reaper pane-scan | **1** |

65 echte limieten → 1 reactie, en die ene was fout (§2.4). Merk op dat
`session_signals.record_limit` op **DEBUG** logt, dus die regel bewijst niets; de vier
markers hierboven zijn wél INFO/WARNING en dekken elk pad dat een limiet daadwerkelijk
zou afhandelen.

### 1.3 Wat het kost

Per limiet-event: tijd tussen de limiet en het eerstvolgende transcript-item van
diezelfde sessie (>60 s later, om echo's van hetzelfde moment weg te filteren).

| | Waarde |
|---|---|
| n (events met latere activiteit) | 58 |
| mediaan | **5,67 h** |
| p25 / p75 | 1,57 h / 11,05 h |
| max | 32,43 h |
| gaps > 5 h (langer dan de conservatieve fallback-pause) | **31 van 58** |
| sessies zonder enige vervolgactiviteit | **7 van 65** |

**Meetmethode + beperkingen (expliciet):** de "gap" is een *bovengrens* op wachttijd —
hij bevat de legitieme wachttijd tot de reset (max 5 h voor het Anthropic-blok). Dat
meer dan de helft van de gaps groter is dan 5 h betekent dat de wachttijd níét door de
reset gedomineerd wordt maar door "een mens moest het merken". Een sessie die is
opgegeven en waarvan de kaart in een verse worktree opnieuw gedispatcht werd, telt hier
als "nooit hervat" (7 gevallen) — dat is precies het "half afgewerkt"-scenario.
Reproductie: het script in §1.1 uitbreiden met de index van de volgende regel; het
volledige script staat in de kaart-transcript van deze analyse.

---

## 2. De vier oorzaken

### 2.1 Het primaire kanaal — de `Notification`-hook — draagt dit signaal niet

De keten die de backend gebouwd heeft:

```
CC Notification-hook  →  POST /scheduled-messages/hook-event
                      →  auto_resume_service.classify_notification()  == "limit"
                      →  session_signals.record_limit()
                      →  set_paused_until(provider=…)
                      →  move_limited_session_to_resume()  →  "To Resume" + kill tmux
```

Alles daarin werkt en is getest. Het probleem zit vóór stap 1: **een abonnementslimiet
verschijnt niet als Notification.** In het transcript is het een assistant-bericht:

```json
{"type":"assistant","isApiErrorMessage":true,
 "message":{"content":[{"type":"text","text":"You've hit your session limit · resets 2:40pm (Europe/Brussels)"}]},
 "timestamp":"2026-07-22T08:24:29.288Z"}
```

De `Notification`-hook vuurt bij permission-prompts en bij "waiting for your input" na
idle — met een template-tekst die géén van de needles in `classify_notification`
bevat, dus die landt in de `"other"`-bak en wordt stil weggegooid
(`router.py:300`). Er komt geen enkele payload binnen die de limiet-tekst draagt.

Twee bijkomende observaties op dit pad:

- De geïnstalleerde hook in `~/.claude/settings.json` is de **oude** vorm — hij stuurt
  `notification_type` niet mee, terwijl `render_hook_command`
  (`hook_script.py:24-35`) dat sinds CC 2.1.198 wél genereert. `GET /hooks-status`
  meldt `installed: true` omdat het alleen op *aanwezigheid* van een event-key checkt,
  niet op *inhoud* van het commando. Drift is dus onzichtbaar. (Niet de hoofdoorzaak —
  ook mét `notification_type` komt er geen limiet-notification — maar wel een
  observability-gat dat een volgende hook-wijziging opnieuw stil laat mislukken.)
- De enige plek waar Cockpit een limiet *wel* typed binnenkrijgt is de headless
  stream-json-transport: `RateLimitEvent` → `_on_rate_limit_event`
  (`headless_runner.py:997-1056`). Dat pad is correct en betrouwbaar — maar
  `transport:git:github.com/…` staat op `worktree` (tmux), dus het draait nooit.

### 2.2 Het secundaire kanaal kijkt alleen naar de eerste 120 seconden

`reap_stale_claims` doet de pane-scan uitsluitend voor namen in `stuck_names`
(`dispatch.py:4236`), en `stuck_names` komt uit:

```python
# session_registry.get_stuck_sessions
name in live_session_names
and now - spawned_at >= timeout_s        # 120 s
and name not in self._spawn_received_hooks
```

`_spawn_received_hooks` wordt gevuld zodra de sessie **één** hook-event stuurt — wat
binnen enkele seconden na `SessionStart` gebeurt. Daarna is de sessie permanent uit de
stuck-verzameling. Gevolg: de pane-scan kan alleen een limiet vangen die toeslaat
vóórdat de sessie ook maar één hook stuurde — het "429 bij eerste invocatie"-geval
waar hij oorspronkelijk voor gebouwd is. **Elke mid-sessie-limiet valt erbuiten**, en
dat is de dominante vorm: alle 65 gemeten events zitten in transcripts met substantiële
voorafgaande inhoud.

Bijkomend: `_spawn_times` en `_spawn_received_hooks` zijn **in-memory**. Na een
backend-herstart (de supervisor doet dat routineus) is `_spawn_times` leeg, dus
`get_stuck_sessions()` geeft niets terug en het pad is óók voor verse sessies dood tot
de volgende spawn.

### 2.3 Liveness is "de tmux-sessie bestaat" — en die bestaat nog

De reaper heeft precies één liveness-criterium (`dispatch.py:4261`):

```python
if name in live_sessions or name in sandcastle_live or name in headless_live:
    continue
```

`live_sessions` komt uit `tmux list-sessions`. Een gelimiteerde `claude` **exit niet** —
hij toont de foutmelding en keert terug naar het invoerveld. De pane blijft dus voor
altijd bestaan, de kaart blijft `claimed_by = agent:<naam>` in zijn agent-kolom, en er is
geen enkele andere controle die daar ooit op ingrijpt: `_claim_age_seconds` bestaat wel
maar wordt alleen gebruikt om een *dode* sessie als dead-on-arrival te classificeren
(`dispatch.py:4509`), nooit om een levende-maar-stilstaande claim te verlopen.

Dit is de directe verklaring voor "deze blijven dan staan, ik moet ze manueel gaan
herstarten": er is per ontwerp geen actor die dat doet.

### 2.4 Waar de pane-scan wél toeslaat, is hij onbetrouwbaar

`_is_rate_limited_session` hergebruikt `auto_resume_service.is_limit_notification`, en die
matcht losse substrings op de tekst:

```python
"hit your session limit" in text or any(
    needle in text for needle in
    ("api error", "429", "token plan", "usage limit", "request rejected"))
```

Toegepast op de **laatste 20 regels ruwe tmux-pane-inhoud** van een agent die code leest,
tests draait en HTTP-statussen print. Waargenomen gevolg (2026-07-22 08:06:21,
`run-20260722-095619-2861-0.log:947`):

```
stuck session k-spike-meet-wa-d450 hit a rate limit
(pane: '❯ PLAN CONTEXT — read this first Plan deliverable: 27033503b1ad43d49ddca47548dacfce
 Parent card: 38d32e94c0484d7ca0a4b09dccc22e42 ● Two findings already. Let me fix the
 endpoint path and register the ');
pausing dispatch for 5h, killing tmux, releasing claim
```

Die sessie was aan het werk ("Two findings already. Let me fix the endpoint path…"). Ze
werd gekilld, dispatch werd 5 uur gepauzeerd, en de kaart kreeg een
`dispatch_failures`-bump (1/3 richting Impediment). **Eén false positive kost dus meer dan
een gemiste detectie.**

Welke needle er precies matchte is niet meer te achterhalen: de logregel kapt af op de
**eerste** 200 tekens van de capture, terwijl de match ergens in de volle 20 regels zat.
Dat is een tweede, zelfstandig observability-gat — een detector die kan misvuren en niet
logt *waaróm*, is niet debugbaar.

---

## 3. Twee kleinere, echte gaten in de classificatie

Deze bijten pas als §2 opgelost is, maar ze zijn goedkoop en nu al meetbaar fout.

**3.1 De weekly-limiet wordt niet herkend.** `"You've hit your weekly limit · resets 9pm
(Europe/Brussels)"` (4 waarnemingen) bevat geen van de needles: niet
`"hit your session limit"`, niet `"usage limit"`, niet `"429"`. Classificatie → `"other"`
→ stil weggegooid. Een weekly-limiet is bovendien het geval waarin *wachten tot reset* het
duurst is — precies daar is er nul afhandeling.

**3.2 De reset-tijd-parser mist de vorm zonder minuten.**

```python
_LIMIT_PATTERN = re.compile(
    r"hit your session limit.*?resets\s+(\d{1,2}:\d{2}(?:am|pm)?)\s*\(([^)]+)\)", re.I)
```

vereist `H:MM`. In de praktijk komt ook `resets 9pm` / `resets 8am` voor: **20 van de 157**
Anthropic-berichten (13%). Die vallen terug op de vaste `FALLBACK_PAUSE_HOURS = 5`
dispatch-pause, ongeacht of de echte reset over 20 minuten of over 4 uur is. Twee kanten
op fout: te lang wachten (verloren capaciteit) of te vroeg herstarten (opnieuw tegen de
muur). Dezelfde regex is ook aan `"hit your session limit"` gekoppeld en zou de
weekly-vorm uit 3.1 dus sowieso niet parsen.

**3.3 De weekly-vorm mét datum parste niet (gefixt 2026-08-03).** Nadat
3.1/3.2 gefixt waren bleef één vorm over. De weekly-limiet zet een **datum**
vóór de klok-tijd omdat de reset dagen weg kan liggen — `"You've hit your
weekly limit · resets Aug 3, 7pm (Europe/Brussels)"` (waargenomen: `Jul 27`,
`Aug 3`). De regex verwachtte de tijd direct na `resets`, dus dit viel terug op
de blinde `FALLBACK_PAUSE_HOURS = 5`-gok. Gemeten
gevolg op 2026-08-01: kaarten `efdc8f4f…` en `dfac67d3…` werden bij de backend-herstart
van 2026-08-03 20:39 door de reaper geparkeerd tot 01:40 — terwijl hun echte reset
(3 aug 19:00) op dat moment al **anderhalf uur voorbij** was.

Drie wijzigingen:

1. `_LIMIT_PATTERN` heeft een optionele `(?P<month>…)\s+(?P<day>…),` groep; `_resolve_year`
   kiest het jaar dat het dichtst bij `now` ligt (Dec 31 ↔ Jan 1).
2. Een **gedateerde** reset rolt niet door naar morgen als hij in het verleden ligt — dat
   verleden-tijdstip is juist het signaal "de limiet is al opgeheven, dispatch nu". Alleen
   de ongedateerde vorm houdt de `+1 dag`-regel, want daar is de datum echt onbekend.
3. De reaper gokte per definitie (`reap_stale_claims` zag alleen pane-content). Hij leest
   nu eerst het transcript van de dode sessie via `_transcript_reset_time` en gebruikt de
   `FALLBACK_PAUSE_HOURS`-gok alleen nog als daar geen parsebare reset-tijd in staat.
   `try_pane_resume` clampt een reset in het verleden naar `now + 1s`, anders dropt
   APScheduler de nudge als misfire en blijft de kaart op `pane_resume_pending` hangen.

---

## 4. De spillover is dode code (geen pool geconfigureerd)

Fase 2 van [`subscription-flexibiliteit-analyse.md`](./subscription-flexibiliteit-analyse.md)
bouwde precies wat hier nodig is: raakt abonnement A zijn limiet, dan wordt de kaart
*direct* herdispatchbaar op abonnement B in plaats van te wachten op de reset
(`_pool_spillover_available` → `has_available_spillover`). Dat pad is nooit gelopen:

```bash
python3 -c "
import sqlite3;c=sqlite3.connect('/home/vdvgu/.claude-registry/kanban.db')
print([k for k,_ in c.execute('select key,value from kanban_meta')])"
```

geeft **geen** `subscription_pool:<project_key>`-rij. `get_subscription_pool()` geeft dus
`None`, `has_available_spillover()` geeft `False`, en elke limiet betekent per definitie
"wachten". Dat is consistent met de 0 spillover-logregels in §1.2.

Providers komen vandaag uit de **kolom-defaults**: `engineer → minimax`,
`analyst → anthropic`, `reviewer → anthropic`. Beide abonnementen zijn dus wél in gebruik,
maar strikt per persona — een gelimiteerde engineer-kaart kan niet naar Anthropic
uitwijken en omgekeerd.

**Hier zit een echte ontwerpspanning**, en die is niet triviaal: zodra er een pool
geconfigureerd wordt, **overschrijft die stilzwijgend `column.default_provider` van élke
kolom** ([`subscription-pool-dispatch-analyse.md`](./subscription-pool-dispatch-analyse.md)
§0). Een pool aanzetten om spillover te krijgen, gooit dus de bewuste
per-persona-verdeling (opus-reviewer op Anthropic, engineer op MiniMax) weg.

**Aanname die ik hier maak** (in plaats van de vraag terug te leggen): spillover is de
moeite waard, maar mag de per-persona-verdeling niet opofferen. De pool moet dus
per-kolom kunnen gelden, óf de kolom-default moet als impliciete eerste pool-entry
fungeren. Welke van de twee, is een scope-vraag met genoeg eigen gewicht om als
`work_type='analysis'`-kaart te lopen (C6) — niet iets om hier in één zin te beslissen.
Tot dat opgelost is blijft "wachten tot reset" het gedrag, en is snelle, betrouwbare
detectie + automatisch hervatten (§5) de volledige winst.

> ✅ **Beslist (kaart `2688bf80…`, 2026-07-23):** de override-claim hierboven is bevestigd
> op de huidige code (`dispatch.py:1233/1240/1242`), en de scope-vraag is beslecht op
> **de kolom-default als impliciete eerste pool-entry**, later uitgebreid met een
> per-kolom staart. De pool wordt daarmee een spillover-*keten* in plaats van een
> routing-*pin*. Zie [`spillover-per-kolom-decision.md`](./spillover-per-kolom-decision.md)
> voor de afweging, de twee latente defects die pas bijten zodra je de pool aanzet, en de
> vervolgkaarten.

---

## 5. Aanbevolen richting

Vijf ingrepen, in prioriteitsvolgorde. Ze staan hieronder als *wat* en *waarom*; het
*hoe* is aan de uitvoerende kaarten.

### R1 — Verplaats het signaal naar het transcript (kaart C1)

Het transcript is de enige bron die alle vier de eigenschappen heeft die we
nodig hebben. Hij bevat de limiet **altijd**, voor **beide vendors**,
**mid-sessie én bij spawn**, en het is een **gestructureerd veld**
(`isApiErrorMessage: true`) in plaats van een substring in schermafval. De
mapping worktree → transcript bestaat al en is in gebruik
(`session_recovery._resolve_resume_target`), dus de detector kan per geclaimde kaart de
staart van het transcript lezen en de laatste api-error classificeren.

Belangrijk contract: alleen reageren wanneer de api-error het **laatste betekenisvolle
event** is. Staat er daarna gewone assistant/user-activiteit, dan is de sessie zelf al
hersteld en mag er niets gebeuren.

> ✅ **Geïmplementeerd (kaart `c8ad1ea8…`).** `detect_transcript_rate_limits`
> (`backend/app/kanban/dispatch.py`) draait elke dispatch-tick per project. Het
> leest per geclaimde kaart alleen de staart van het transcript
> (`_read_transcript_tail_entries`, 64 KiB) en meldt een limiet zodra de
> laatste conversationele entry een `isApiErrorMessage: true` is die als
> limiet classificeert (`_tail_rate_limit_entry`).
>
> De reactie is uitgetrokken uit de Notification-hook naar een gedeelde
> `handle_rate_limit_signal` (per-provider `set_paused_until` +
> `move_limited_session_to_resume`), zodat beide kanalen exact hetzelfde
> afhandelpad delen — geen tweede reactiepad.

### R2 — Hervat in de bestaande pane in plaats van kill + respawn (kaart C3)

Vandaag is de reactie op een limiet: tmux killen, kaart naar `To Resume`, later
`claude --resume`. Dat werkt, maar het is de duurste variant — en het is niet wat de
gebruiker met de hand doet. Die typt gewoon "continue" in de nog levende pane. De
machinerie daarvoor bestaat al en is in productie voor scheduled-messages
(`tmux_inject.wait_for_pane_ready` + `send-keys`).

Voordeel: nul contextverlies, nul worktree-churn, geen `dispatch_failures`-bump, en de
kaart hoeft het bord niet te verlaten. Kill + `To Resume` blijft de fallback wanneer de
pane weg is of de nudge twee keer niet aanslaat.

Te verifiëren aanname voor de uitvoerder: dat `claude` na een limiet-fout
invoer accepteert zonder herstart. Dat is wat de handmatige workflow
suggereert, maar het is niet gemeten. Verder: dat een nudge vóór de reset niet
meer dan één extra mislukte call kost. Backoff en een maximum aantal pogingen
zijn daarom onderdeel van de kaart.

> ✅ **Geïmplementeerd (kaart `e2116332…`)** — `try_pane_resume` +
> `_execute_pane_resume` + `handle_rate_limit_signal`
> (`backend/app/kanban/dispatch.py`) plannen een apscheduler-job op
> `reset_time + margin*attempts` met max 3 pogingen. De bestaande
> `tmux_inject`-machinerie levert de nudge af zonder de sessie te killen.
> De fallback naar kill + To Resume blijft staan voor de drie
> vangnet-routes uit de acceptance criteria (pane gone, niet ready,
> max attempts).

Een tweede pass op 2026-07-25 (`commit e9ff0a1` → productie) mat echter twee
ontwerp-bugs die gemeten regressie veroorzaakten (36 events × ~30 s tot
fallback, 0 echte nudges, 108 misleidende comments, 2 losse
keystroke-injecties in hergebruikte panes). De dispatch-tick interpreteerde
dezelfde in-transcript limit als een nieuwe re-hit en brandde het
attempt-budget op vóór de eerste nudge kon vuren. En de fallback cancelde het
apscheduler-job niet.

Fix toegepast in een vervolg op dezelfde kaart: een `pane_resume_fired`-vlag
differentieert "scheduled, wacht op fire" van "gevuurd, monitor re-limit", en
> `_pane_resume_fallback_to_kill` ruimt het apscheduler-job op voor
> het naar To Resume gaat. De kern-aanname ("`claude` accepteert na
> een limiet invoer en hervat productief") is daarmee eindelijk meetbaar
> in productie — gemeten uitkomst volgt zodra de eerste echte reset
> passeert.

### R3 — Voortgangs-liveness: stilstand is óók dood (kaart C4)

Dit is het enige voorstel dat niet afhangt van het correct herkennen van een specifieke
foutmelding, en daarom het waardevolste vangnet. Vandaag is "leeft" = "pane bestaat". Voeg
een tweede criterium toe: groeit het transcript van een geclaimde sessie N minuten niet,
dan is de sessie **stilstaand** — zichtbaar maken op de kaart, en na een ruimere drempel de
claim vrijgeven / naar `To Resume`.

Dat vangt de limiet-case, maar ook alles wat we niet opgesomd hebben: een crash-loop, een
wachtende permission-prompt, een sessie die op een netwerk-timeout hangt. Het verandert het
systeem van "zelfherstellend voor bekende fouten" naar "zelfherstellend, punt".

✅ **Geïmplementeerd** (kaart f0953a11…): `check_progress_liveness` in `app/kanban/dispatch.py`
draait elke tick ná `detect_transcript_rate_limits`, vergelijkt het transcript-mtime van
elke `agent:`-claimed kaart met de vorige observatie, post één "stilstaand"-comment bij
`PROGRESS_LIVENESS_SIGNAL_SECONDS=30min` en released via `_move_to_resume` bij
`PROGRESS_LIVENESS_ACTION_SECONDS=60min`. Sandcastle / headless transports behouden hun
eigen liveness-bron (carve-out in de skip-set).
**Effect: 0 `progress-liveness`-logregels over de volledige backend-historie**
(gemeten 2026-08-04 via `grep -h "progress-liveness" logs/backend/*.log | wc -l`),
tegenover ~16.000 limiet-detecties. Negatief bewijs — de skip-set bevat
`live_sessions`, en een gelimiteerde `claude` exit niet, dus de detector sluit
precies de doel-verzameling uit waarvoor hij gebouwd is. Vervolg nodig:
skip-set beperken tot transports die hun eigen liveness-bron hebben; gevolg
van kaart `21a349bc…` die de ✅-conventie invoert (zie
[`recipe-writing-conventions.md`](./recipe-writing-conventions.md) §2).

### R4 — Maak de pane-detector veilig (kaart C5)

Zodra R1 er is, hoeft de pane-substring-scan alleen nog het geval te dekken waarvoor hij
gebouwd is: een sessie die crasht vóór er een transcript is. Beperk hem daartoe, en log
welke needle matchte plus het volledige matchvenster — een detector die een gezonde sessie
kan killen en niet vertelt waarom, hoort niet in de reaper.

> ✅ **Geïmplementeerd (kaart `3a8f27a4…`).** De pane-scan is nu pre-transcript-only via
> `_session_has_transcript`: zodra de sessie een transcript met inhoud heeft, is de
> transcript-tail detector (`detect_transcript_rate_limits`) leidend en doet de pane-scan
> niets. De needles zijn strenger: losse `"429"` of `"api error"` zijn niet meer voldoende;
> alleen single-phrase matches (`hit your session limit`, `hit your weekly limit`,
> `token plan`) of co-occurring combo's (`api error`+`429`, `request rejected`+`429`)
> triggeren. Bij een match logt `_cleanup_stuck_session` nu `needle=…` + de bijbehorende
> pane-regel in plaats van de afgekapte 200-char prefix. Bare `HTTP/2 429` uit een curl
> faalt niet langer op een gezonde sessie (de false positive van 2026-07-22).

### R5 — Herken alle limiet-vormen (kaart C2)

Klein, geïsoleerd, meetbaar: `hit your weekly limit` toevoegen, `resets 9pm` (zonder
minuten) laten parsen, en de patronen loskoppelen van de `session`-variant zodat de parser
ook op de weekly-vorm werkt. Los van R1 waardevol, want elke detector die er ooit komt
consumeert dezelfde classifier.

### Wat ik bewust níét voorstel

- **Proactief niet-dispatchen op basis van quota-signalen.** De signalen zijn er wel
  (`SubscriptionUsage`), maar Anthropic publiceert geen usage-API voor Pro/Max. De
  waarde is een schatting uit lokale JSONL met een gegokte plan-tier-limiet.
  MiniMax geeft zonder `probe_url` `onbekend` terug. Op zo'n signaal een
  dispatch-beslissing bouwen ruilt een detecteerbaar probleem in voor een
  onzichtbaar probleem (kaarten die niet starten zonder dat iemand weet waarom).
- **Overstappen op de headless transport om `RateLimitEvent` te krijgen.** Technisch is dat
  het schoonste signaal. Het is een transport-migratie met eigen gevolgen (geen
  attachbare pane, human-takeover-verhaal) — veel te grote hefboom voor dit
  probleem. R1 levert hetzelfde signaal zonder migratie.

---

## 6. Waarom de vorige pogingen niet geholpen hebben

Ter kalibratie, want de kaart begint met "al meerdere malen dit pogen te fixen":

| Eerdere ingreep | Wat het toevoegde | Waarom het niet hielp |
|---|---|---|
| Per-provider dispatch-pause (`dispatch_pause.py`) | Reactie: alleen het geraakte abonnement bevriezen | Wordt aangeroepen door detectors die niet vuren |
| `move_limited_session_to_resume` + `To Resume` + `scheduled_at` | Reactie: kaart terug op het bord met reset-tijd | Idem — 0 aanroepen in 8 dagen |
| MiniMax-varianten in `is_limit_notification` | Classificatie: `429` / `Token Plan` erbij | Classificatie was nooit het knelpunt; de payload komt niet aan |
| Reaper stuck-session pane-scan | Tweede detector | Alleen voor sessies zonder hooks; vangt geen mid-sessie-limiet, en misvuurt |
| Fase-2 spillover-router | Reactie: uitwijken i.p.v. wachten | Geen pool geconfigureerd → altijd `False` |
| Typed `RateLimitEvent` (headless) | Betrouwbaar signaal | Transport staat op `worktree`; pad draait niet |

Het patroon is consistent: **zes ingrepen aan de reactie- en classificatiekant, nul aan de
signaalkant.** Vandaar dat elke ronde "beter" voelde en niets veranderde.

---

## 7. Vervolgkaarten

Aangemaakt als kind-kaarten van `92734942…`:

| Kaart | Titel | Hangt af van |
|---|---|---|
| `c8ad1ea8…` | `[bug] Limiet-detectie mist elke mid-sessie limiet — detecteer op het transcript` (R1) | — |
| `d5d9161e…` | `[bug] Limiet-patronen: weekly-limiet en "resets 9pm" worden niet herkend` (R5) | — |
| `e2116332…` | `[feature] Hervat een gelimiteerde sessie in zijn eigen pane i.p.v. kill + respawn` (R2) | `c8ad1ea8…` |
| `f0953a11…` | `[feature] Voortgangs-liveness: een geclaimde sessie zonder transcript-groei is stilstaand` (R3) | — |
| `3a8f27a4…` | `[bug] Pane-substring-detector kilde een gezonde sessie — beperken tot pre-transcript + logbaar maken` (R4) | `c8ad1ea8…` |
| `2688bf80…` | `[analyse] Spillover vs. per-persona provider: pool overrulet stilzwijgend elke kolom-default` (§4) | — |

De detector-, patroon-, liveness- en spillover-kaarten zijn onafhankelijk en kunnen
parallel. De pane-hervattingskaart (`e2116332…`) consumeert het detectiesignaal uit
`c8ad1ea8…`; de pane-detector-kaart (`3a8f27a4…`) mag pas versmallen zodra `c8ad1ea8…`
de dekking overneemt.
