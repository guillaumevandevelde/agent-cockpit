---
title: "Analyse — kan het abonnement zichzelf vrijgeven en de sessies herstarten na een limiet?"
type: analysis
status: active
---

# Analyse — abonnement automatisch vrijgeven en sessies herstarten na een sessielimiet

**Datum:** 2026-07-30
**Kaart:** `9935076cbdf34d61b1f90bfc55bbffc0` "analyse - Manier om subscription en bijhorende
sessies na sessielimiet automatisch terug te starten"
**Scope:** read-only bevindingen op `master` (worktree `k-analyse-manie-a0f8`), gegrond op de
backend-logs en de kanban-DB van deze box (2026-07-23 → 2026-07-30).

**Vraag (gebruiker):**
> "Kan je nazien of het mogelijk is om wanneer een 5h of weekly sessie limiet verloopt je
> automatisch de subscription weer kan vrijgeven en de bijhorende sessies terug kan starten.
> Het idee is dat dan de loop kan blijven draaien en zo de autonomie verhoogt word van de
> toepassing."

Voorganger: [`sessie-limiet-auto-dispatch-analyse.md`](./sessie-limiet-auto-dispatch-analyse.md)
(2026-07-23) — die analyseerde de **detectie**kant. Deze analyse kijkt naar de **vrijgave-**
en **herstart**kant, ná de vijf ingrepen (R1–R5) die uit dat doc voortkwamen.
Verwant: [`spillover-per-kolom-decision.md`](./spillover-per-kolom-decision.md),
[`subscription-pool-dispatch-analyse.md`](./subscription-pool-dispatch-analyse.md).

---

## 0. TL;DR

**Ja, het kan — en het is al gebouwd.** De volledige keten bestaat en is vandaag end-to-end
werkend gemeten: limiet-detectie → per-provider pause → kaart naar `To Resume` met de
reset-tijd als `scheduled_at` → pause verloopt vanzelf → kaart wordt automatisch opnieuw
gedispatcht. Er is dus **geen nieuw mechanisme nodig**; de vraag van de kaart is
architecturaal al beantwoord met "ja".

**Maar in de dominante praktijksituatie doet het systeem vandaag het tegenovergestelde van
wat de kaart vraagt: het abonnement wordt bij de reset niet vrijgegeven, maar opnieuw
vergrendeld.** Gemeten, hard bewijs (§2):

> Sessie `k-update-readme-e85e` had een Anthropic-limiet met reset **05:20 (+02:00)**.
> Om **exact 03:20:04 UTC** — het moment van de reset — sprong de pause-deadline van
> `2026-07-28T05:20:00+02:00` naar `2026-07-29T05:20:00+02:00`. **+24 uur, op het moment
> dat het abonnement vrij had moeten komen.**

De oorzaak is één ontbrekende eigenschap: **het limiet-signaal is niet idempotent.** De
transcript-detector uit R1 leest elke ~10 s de staart van het transcript opnieuw, ziet
dezelfde (inmiddels oude) limietmelding staan, en behandelt die als een *nieuwe* limiet.
Elke behandeling zet de pause-deadline opnieuw. Gemeten: **15 996 keer** hetzelfde signaal
opnieuw afgehandeld, waarvan **11 396 in één venster van 9 uur**.

Vijf gaten, in volgorde van impact:

| # | Gat | Gemeten effect |
|---|---|---|
| **A** | Limiet-signaal is niet idempotent — elke tick her-armeert de pause | 15 996 her-behandelingen; pause-deadline schuift mee met `now` |
| **B** | Reset-parser rolt een *verstreken* tijd naar morgen — ook bij een oude melding | +24 u vergrendeling op het resetmoment zelf |
| **C** | Voortgangs-liveness (R3) slaat sessies met een levende tmux-pane over — precies de limiet-case | **0** firings ooit, tegenover ~16 000 limiet-detecties |
| **D** | Spillover is nog steeds dode code — geen pool geconfigureerd | **0** spillover-logregels; elke limiet betekent nog altijd "wachten" |
| **E** | Een onhervatbare `To Resume`-kaart gooit een exception die de héle dispatch-tick afbreekt | 103 afgebroken ticks |

A en B zijn samen de kern: ze veranderen een zelfherstellende pauze in een pauze die
zichzelf verlengt zolang een gelimiteerde sessie blijft staan. C is het vangnet dat dit had
moeten opvangen en structureel niet vuurt. Fix A+B+C en de loop uit de kaart sluit zichzelf;
D is de bovenop-winst (uitwijken i.p.v. wachten), E een aparte robuustheidslek.

---

## 1. Wat er al is — en dat het werkt

De keten die de kaart vraagt bestaat volledig:

| Stap | Waar | Status |
|---|---|---|
| Limiet detecteren (mid-sessie, beide vendors) | `detect_transcript_rate_limits`, `dispatch.py:5092` | ✅ werkt |
| Abonnement pauzeren, per provider | `handle_rate_limit_signal` → `set_paused_until`, `dispatch.py:4985` | ✅ werkt |
| Pause **automatisch vrijgeven** bij reset | `is_dispatch_paused`, `dispatch_pause.py:86-109` | ✅ self-clearing |
| Sessie herstarten in eigen pane | `try_pane_resume`, `dispatch.py:4500-4523` | ✅ werkt |
| Kaart terug op het bord met reset-tijd | `move_limited_session_to_resume` → `_move_to_resume` | ✅ werkt |
| Kaart **automatisch herdispatchen** na reset | `_DISPATCH_COLUMNS` (`To Resume` eerst) + `_is_due`, `dispatch.py:5267/5302` | ✅ werkt |

`is_dispatch_paused` is expliciet self-clearing: zodra de deadline verstreken is wordt de
rij gewist en geeft hij `False` terug, *"so the very next tick (not a separately scheduled
job) picks dispatch back up automatically after the usage limit resets"*
(`dispatch_pause.py:88-92`). Er is dus geen aparte "vrijgave-job" nodig — dat deel van de
vraag is al opgelost.

**Live geverifieerd tijdens deze analyse.** Kaart `e9089ecad8e64b19a25bdf59804b70de` stond
sinds 2026-07-25 in `To Resume` met een verwijzing naar een inmiddels opgeruimde worktree:

```
21:09:21  spawn failed for card e9089ecad8… — ValueError: Could not resolve project
          directory for '…k-problem-drag-3072'   (compenserende ops: release, resume-
          pointers gewist, dispatch_failures 0 → 1)
21:09:34  dispatched card e9089ecad8… → verse worktree k-problem-drag-84c8, kolom engineer
```

13 seconden van kapotte resume-pointer naar draaiende sessie, zonder tussenkomst. Het
herstelpad wérkt. Reproductie van de eindtoestand:

```bash
python3 -c "
import sqlite3; c=sqlite3.connect('/home/vdvgu/.claude-registry/kanban.db')
print(c.execute(\"select column,claimed_by,dispatch_failures,resume_project_folder \
from kanban_cards where id='e9089ecad8e64b19a25bdf59804b70de'\").fetchone())"
```

**Conclusie van §1:** de vraag "is het mogelijk" is met ja beantwoord door de bestaande
code. De rest van dit doc gaat over waarom het in de praktijk toch niet gebeurt.

---

## 2. Gat A + B — de pause verlengt zichzelf in plaats van te verlopen

### 2.1 Het mechanisme

`detect_transcript_rate_limits` draait elke dispatch-tick (~10 s) en leest per geclaimde
kaart de staart van het transcript. Staat daar een limietmelding, dan roept het
`handle_rate_limit_signal` aan (`dispatch.py:5092`). Er is **geen enkele controle of dit
signaal al eerder behandeld is** — geen "reeds afgehandeld"-vlag, geen vergelijking met de
vorige detectie, geen dedupe op de reset-tijd. De lus is:

```
tick  →  transcript-staart bevat nog steeds de oude limietmelding
      →  handle_rate_limit_signal()
      →  set_paused_until(pause_until, provider)        # dispatch.py:4985, ONVOORWAARDELIJK
      →  (10 s later opnieuw, en opnieuw, en opnieuw…)
```

De melding blijft aan de staart staan zolang de sessie niets nieuws schrijft — en een
gelimiteerde sessie schrijft per definitie niets nieuws. De detector houdt zichzelf dus in
stand.

**Gemeten volume:**

| Marker | Aantal |
|---|---|
| `transcript-tail rate limit detected` | **15 996** |
| `rate-limit signal handled` | **15 996** |
| `unrecognized usage-limit message format` (subset zonder parsebare reset-tijd) | **10 062** |
| Werkelijk *onderscheiden* limiet-events daarachter | enkele tientallen |

```bash
cd /home/vdvgu/claude-cockpit
for p in "transcript-tail rate limit detected" "rate-limit signal handled" \
         "unrecognized usage-limit"; do
  echo "$p: $(grep -h "$p" logs/backend/*.log | wc -l)"
done
```

### 2.2 Wat dat met de deadline doet — twee smaken

**Onparsebare reset-tijd (MiniMax) → glijdende deadline.** Zonder reset-tijd valt
`handle_rate_limit_signal` terug op `now + FALLBACK_PAUSE_HOURS` (5 u). Omdat `now`
elke tick opschuift, schuift de deadline mee: de pauze verloopt **nooit** zolang de
sessie blijft staan. Gemeten op 2026-07-27/28, drie sessies tegelijk:

```
22:03:44  k-bijlage-kunne-fe49  pause_until=2026-07-28T03:03:44   (now+5u)
02:41:34  k-bijlage-kunne-fe49  pause_until=2026-07-28T07:41:33   (now+5u)
06:39:54  k-self-improve-6cb7   pause_until=2026-07-28T11:39:54   (now+5u)
```

8 u 36 min onafgebroken her-armeren, 11 396 keer in dat venster. Van de 10 062
onparsebare meldingen zijn er **9 962** de MiniMax-vorm
(`API Error: Request rejected (429) · Token Plan usage limit reached: …`), die
principieel géén reset-tijd bevat — daar is de fallback de enige route (zie §3).

In datzelfde venster staat een dispatch-gat van **01:03 → 06:38** (5 u 35 min zonder één
enkele dispatch). Dat is consistent met een pauze die niet verloopt; ik noteer het als
**correlatie plus mechanisme**, niet als bewijs — de logregel `dispatched card …` noteert
`cli_id` (`claude-code` / `open-code`), niet de abonnement-provider, dus welk pause-slot
precies dichtzat is uit deze logs niet te herleiden.

**Parsebare reset-tijd (Anthropic) → sprong van +24 u.** Hier is de deadline een absolute
tijd, dus her-armeren is idempotent — *tot het resetmoment passeert*. `parse_reset_time`
sluit af met (`auto_resume.py:180-182`):

```python
# If reset time is in the past, it's tomorrow
if reset_time <= now:
    reset_time += timedelta(days=1)
```

Dat klopt voor een **verse** melding ("resets 5:20am" om 23:00 → morgen 05:20). Het is
fout voor een **oude** melding die opnieuw geparsed wordt nadat haar reset al voorbij is:
dan schuift dezelfde melding een volle dag door. Gemeten, sessie `k-update-readme-e85e`:

```
FLIP 2026-07-28T00:00:34Z  →  pause_until=2026-07-28T05:20:00+02:00
FLIP 2026-07-28T03:20:04Z  →  pause_until=2026-07-29T05:20:00+02:00
```

`05:20 +02:00` is `03:20 UTC`. De sprong staat op `03:20:04 UTC` — **vier seconden na de
reset**. Precies op het moment dat het abonnement vrij kwam, werd het voor nog eens 24 uur
vergrendeld. Dit is letterlijk het tegendeel van wat de kaart vraagt.

```bash
cd /home/vdvgu/claude-cockpit && python3 - <<'PY'
import glob,json,re
rows=[]
for f in glob.glob('logs/backend/*.log'):
    for line in open(f,encoding='utf-8',errors='replace'):
        if 'rate-limit signal handled' not in line or 'k-update-readme-e85e' not in line: continue
        j=json.loads(line); m=re.search(r'pause_until=(\S+)\)', j['message'])
        if m: rows.append((j['timestamp'], m.group(1)))
rows.sort(); prev=None
for t,p in rows:
    if p!=prev: print('FLIP',t,'->',p); prev=p
PY
```

### 2.3 Waarom dit één fix is, niet twee

B (de dag-rollover) is op zichzelf correct gedrag; hij wordt pas schadelijk doordat A
dezelfde melding herhaaldelijk aanbiedt. Een idempotent signaal — "deze limiet, met deze
reset-tijd, voor deze sessie, is al behandeld" — neemt beide symptomen weg. De
dag-rollover verdient daarnaast een expliciete leeftijdsgrens, zodat een melding die
ouder is dan haar eigen reset nooit meer een pauze kan zetten.

✅ Geïmplementeerd (kaart `e279a52b…`) — in twee ronden, want de eerste dekte de
leeftijdsgrens nog niet.

**Ronde 1 — idempotency-gate.** `handle_rate_limit_signal` retourneert `False` zonder
`set_paused_until` / `move_limited_session_to_resume` opnieuw te draaien, zodra de
nieuwe melding identiek is aan de eerder opgeslagen melding voor dezelfde sessie.
Helpers: `is_limit_message_processed` en `clear_limit` in
`app/services/scheduling/session_signals.py`. De recovery-branch in
`detect_transcript_rate_limits` ruimt het signaal op zodra er gewone activiteit ná de
limiet staat — anders zou `record_limit`'s first-write-wins een verse limiet met
andere tekst stilzwijgend inslikken.

**Ronde 2 — herstart-vast signaal plus leeftijdsgrens.** `session_signals` is een
proces-lokale singleton en dus leeg na elke backend-herstart; de eerste tick daarna
las dezelfde transcript-staart als een verse limiet en her-armeerde de pauze alsnog.
Twee lagen sluiten dat af. **Durend geheugen:** `app/kanban/rate_limit_signals.py`
schrijft per sessie een `rate_limit_signal:<sessie>`-rij in `KanbanMeta`. Die rij
bevat de message-digest, het moment waarop de melding geschreven werd en de gezette
deadline. `handle_rate_limit_signal` raadpleegt de rij zodra het in-memory pad niets
weet; een leeftijds-sweep ruimt rijen ouder dan een week op. **Leeftijdsgrens:**
`parse_reset_time` neemt een `now`-referentieklok, en de transcript-sweep geeft daar
de tijdstempel van de limiet-melding zelf in mee (`_tail_rate_limit_entry`). Een
melding waarvan de reset op dát moment al verstreken is zet géén pauze meer. De
dag-rollover zelf blijft ongewijzigd — die was correct voor een verse melding en de
fix haalt 'm alleen uit het herhaal-pad.

---

## 3. Gat B′ — MiniMax noemt geen reset-tijd, dus wachten is blind gokken

De MiniMax-limiet (`Token Plan usage limit reached: Upgrade your Token Plan or purchase
Credits`) bevat geen enkele tijdsaanduiding. Er valt niets te parsen, dus elke MiniMax-
limiet krijgt de vaste `FALLBACK_PAUSE_HOURS = 5` (`auto_resume.py:31`) — ongeacht of het
quotum over tien minuten of pas morgen terugkomt. Twee kanten op fout: te lang wachten
(verloren capaciteit) of te vroeg herstarten (opnieuw tegen de muur).

Dit is de **enige** van de vijf gaten waar de kaart-vraag ("wanneer de limiet verloopt")
niet uit het signaal zelf te beantwoorden is. Het alternatief voor gokken is *proberen*:
een korte pauze, dan één kaart laten proberen, en bij een nieuwe limiet exponentieel
terugschalen. Dat maakt de vrijgave afhankelijk van waarneming in plaats van van een
geraden constante. De Anthropic-kant heeft dit probleem niet — daar staat de reset-tijd in
de melding, en sinds R5 worden zowel de weekly-vorm als de vorm zonder minuten herkend
(`_LIMIT_PATTERN`, `auto_resume.py:17-20`; `hit your weekly limit` in de classifier,
`auto_resume.py:107`).

---

## 4. Gat C — het vangnet dat nooit vuurt

R3 uit de voorgaande analyse voegde voortgangs-liveness toe: een geclaimde sessie waarvan
het transcript niet groeit is stilstaand, en wordt na een drempel vrijgegeven. De
motivatie in de eigen docstring is exact onze case
(`dispatch.py`, `check_progress_liveness`):

> *"The pane check alone misses a session that hit its subscription limit, crashed in a
> loop, parked on a permission prompt, or is hung on a network timeout — the pane (and the
> claim) stays alive forever."*

De implementatie doet echter dit (`dispatch.py:6535`):

```python
if name in live_sessions or name in sandcastle_live or name in headless_live:
    continue
```

`live_sessions` is de tmux-momentopname van de tick. En een gelimiteerde `claude` **exit
niet** — hij toont de fout en keert terug naar zijn prompt, dus zijn tmux-sessie staat in
`live_sessions`. De carve-out sluit daarmee structureel precies de verzameling uit
waarvoor het vangnet gebouwd is. Wat overblijft — een claim zonder levende tmux-sessie —
is exact het werkterrein van `reap_stale_claims`, dat al eerder in dezelfde tick draait.

**Gemeten: `progress-liveness` komt 0 keer voor in de volledige loghistorie**, tegenover
~16 000 limiet-detecties.

```bash
grep -h "progress-liveness" /home/vdvgu/claude-cockpit/logs/backend/*.log | wc -l   # → 0
```

Dit is geen kleine regressie: het is het enige voorgestelde vangnet dat niet afhangt van
het correct herkennen van een specifieke foutmelding, en dus het enige dat gaten A/B/D én
alles wat we niet opgesomd hebben zou opvangen.

---

## 5. Gat D — spillover is nog altijd dode code

Uitwijken naar een tweede abonnement is de grootste autonomie-winst die er is: geen enkele
wachttijd in plaats van 5 uur. De machinerie is gebouwd (`subscription_pool.py`,
`has_available_spillover:274`, `get_subscription_pool:438`), en de per-kolom-staarten uit
[`spillover-per-kolom-decision.md`](./spillover-per-kolom-decision.md) zijn op 2026-07-29
gemerged (commit `c5cf5ba1`). Maar er is nog steeds **geen pool geconfigureerd**:

```bash
python3 -c "
import sqlite3; c=sqlite3.connect('/home/vdvgu/.claude-registry/kanban.db')
print([k for k,_ in c.execute(
  \"select key,value from kanban_meta where key like 'subscription_pool%'\")])"   # → []
```

`get_subscription_pool()` geeft dus `None`, `has_available_spillover()` geeft `False`, en
elke limiet betekent per definitie "wachten". Consistent met **0** `spilling over`-
logregels over de hele historie. De ontwerpknoop die dit ooit blokkeerde is inmiddels
doorgehakt (kolom-default als impliciete eerste pool-entry), dus wat rest is
configureren + verifiëren — geen ontwerpwerk meer.

✅ Geïmplementeerd (kaart `2bb37d97…`, 2026-08-04) — per-kolom tails geïnstalleerd op
`git:github.com/guillaumevandevelde/agent-cockpit`. Effect: een limiet op de kop wijkt nu
direct uit naar de staart in plaats van op de reset te wachten — behalve op `reviewer`, dat
bewust blijft wachten:

| Kolom | Head (impliciet, kolom-default) | Tail (spillover-target) | Gedrag op limiet |
|---|---|---|---|
| `engineer` | `minimax` | `[anthropic @ drempel=0.9]` | MiniMax raakt limiet → direct uitwijken naar Anthropic (geen wachttijd) |
| `analyst` | `anthropic` | `[minimax @ drempel=0.9]` | Anthropic raakt limiet → direct uitwijken naar MiniMax |
| `reviewer` | `anthropic` | `[]` (bewust leeg) | Anthropic raakt limiet → wacht op reset (kwaliteit > snelheid) |

Operator-handleiding: [`subscriptions.md` § "Subscription-pool inspectie & wijzigen"](./subscriptions.md#subscription-pool-inspectie--wijzigen).
Verificatie (herhaald op 2026-08-06, beide takken schoon): een gesimuleerde limiet op de
**kop** geeft `True` voor `engineer` (`minimax` → `anthropic`) en `analyst`
(`anthropic` → `minimax`), en `False` voor `reviewer` — zowel via de pure router
`subscription_pool.has_available_spillover` als via `dispatch._pool_spillover_available`.
Op 2026-08-04 gaf de dispatch-tak voor `engineer` nog `False`; dat was een toen actieve
per-provider pause op Anthropic, geen defect — een gepauzeerde target is geen geldige
uitwijk. Drie regressietests in
`backend/tests/test_subscription_pool_dispatch.py::test_production_pool_tails_*` pinnen
de installatie + de end-to-end card-move met de 🔀 activity-comment.

---

## 6. Gat E — één onhervatbare kaart breekt de hele tick af

Wanneer een `To Resume`-kaart naar een opgeruimde worktree verwijst, gooit
`resolve_directory` een `ValueError`. `_run_card` vangt die netjes op, past compenserende
ops toe (release, resume-pointers wissen, `dispatch_failures` bumpen) — en doet dan
`raise` (`dispatch.py:5852-5853`). Die exception loopt door tot `run_dispatch_tick`, waar
hij als `dispatch tick failed` gelogd wordt (`dispatch.py:7919`).

De compenserende ops overleven dat wél (de `except`-tak commit expliciet), dus de kaart
herstelt de tick daarna — dat is de 13-seconden-recovery uit §1. Maar de **rest van die
tick wordt overgeslagen**: geen enkele andere kaart wordt in die ronde gedispatcht. Omdat
`To Resume` vóór `Backlog` staat in `_DISPATCH_COLUMNS` (`dispatch.py:5267`), kost elke
onhervatbare kaart een volledige tick voordat de volgende kaart aan bod komt.

**Gemeten: 103 afgebroken ticks** (2026-07-24 → 2026-07-30), piek 74 op één dag.

```bash
grep -h "dispatch tick failed" /home/vdvgu/claude-cockpit/logs/backend/*.log | wc -l
```

Een spawn-fout op één kaart is een kaart-probleem, geen tick-probleem; doorgaan naar de
volgende kandidaat is het juiste gedrag.

✅ **Geïmplementeerd (kaart `05592c13…`).** `dispatch_project`'s while-loop vangt de
compensated spawn-failure nu af (`CardSpawnFailed`) en gaat door naar de volgende
kandidaat; de gefaalde kaart wordt binnen die tick uit de werkverzameling gehaald zodat
hij niet meermaals binnen één tick herkanst wordt.

---

## 7. Aanbevolen richting

In prioriteitsvolgorde. *Wat* en *waarom*; het *hoe* is aan de uitvoerende kaarten.

**R1 — Maak het limiet-signaal idempotent (gat A + B).** Eén limiet-event mag precies één
keer een pauze zetten. Een herdetectie van dezelfde melding mag de deadline niet
verschuiven, en een melding die ouder is dan haar eigen reset-tijd mag helemaal geen pauze
meer zetten. Dit is de enige ingreep die het gemeten +24 u-gedrag wegneemt, en daarmee de
directe beantwoording van de kaart-vraag.

**R2 — Repareer het voortgangs-vangnet (gat C).** Laat `check_progress_liveness` juist wél
naar sessies met een levende pane kijken; dat is de verzameling waarvoor hij bestaat. Dit
is het enige vangnet dat werkt zonder een specifieke foutmelding te herkennen.

✅ Geïmplementeerd (kaart `01bde6e9…`): de carve-out op `dispatch.py` laat
`sandcastle_live` / `headless_live` / `acp_live` met rust (transports met eigen
liveness), maar `live_sessions` (tmux-snapshot) wordt nu meegenomen — de pane
blijft staan bij een abonnementslimiet, het transcript niet. Nieuwe tests
`test_progress_liveness_live_tmux_with_stalled_transcript_{triggers_signal,releases_claim}`
bewaken dat de drempels ook bij levende tmux-panes vuren.

**R3 — Vervang blind wachten door proberen bij MiniMax (gat B′).** Een provider zonder
reset-tijd verdient een korte pauze plus een gecontroleerde herkansing met exponentiële
terugschaling, niet een geraden constante van 5 uur.

✅ Geïmplementeerd (kaart `b106def4…`): nieuwe `app/kanban/rate_limit_backoff.py`
slaat een per-provider teller op in `KanbanMeta` op key
`rate_limit_backoff:<provider>`; `handle_rate_limit_signal` valt bij een
onparseerbare melding op die teller terug in plaats van `FALLBACK_PAUSE_HOURS`,
met een `BACKOFF_SEQUENCE = [120, 240, 480, 960, 1920, 3600]` (2 m → 60 m).
`detect_transcript_rate_limits` reset de teller zodra een sessie op die
provider is hersteld (transcript toont geen limiet meer), en een
`prune_idle_backoffs` ruimt tellers op die langer dan twee uur niet bewogen
hebben. Nieuwe tests in `tests/test_rate_limit_backoff.py` (11) en zes
nieuwe integratietests in `tests/test_transcript_rate_limit_detection.py`
dekken alle acceptatiecriteria: korte initiële pauze, verdubbeling tot
plafond, per-provider, niet-vechten met R1, recovery-reset, en de
parsbare-pad dat de teller ongemoeid laat.

**R4 — Zet de subscription-pool aan (gat D).** De grootste autonomie-winst per eenheid
werk: uitwijken in plaats van wachten. De ontwerpknoop is al doorgehakt; wat rest is
configureren en meten dat er daadwerkelijk gespilloverd wordt.

**R5 — Laat een spawn-fout de tick niet afbreken (gat E).** Ga door naar de volgende
kandidaat in plaats van de ronde op te geven.

### Wat ik bewust níét voorstel

- **Een aparte "vrijgave-scheduler".** De pauze is al self-clearing
  (`dispatch_pause.py:86-109`); een tweede tijdgestuurde actor toevoegen naast een
  correct werkende zelfopruimende check voegt een tweede bron van waarheid toe zonder het
  probleem te raken — het probleem is dat de deadline steeds opnieuw wordt gezet, niet dat
  hij niet wordt opgeruimd.
- **De vaste fallback verhogen of verlagen.** Zolang A niet gefixt is, is de waarde
  irrelevant (hij schuift toch mee met `now`); daarna is de juiste oplossing meten in
  plaats van gokken (R3).

---

## 8. Vervolgkaarten

Aangemaakt als kind-kaarten van `9935076c…`:

| Kaart | Titel | Hangt af van |
|---|---|---|
| `e279a52b…` | `[bug] Limiet-signaal is niet idempotent — pause-deadline schuift mee en springt +24u op het resetmoment` (R1) | — |
| `01bde6e9…` | `[bug] Voortgangs-liveness slaat levende tmux-panes over — het vangnet vuurde nog nooit` (R2) | — |
| `b106def4…` | `[feature] MiniMax-limiet zonder reset-tijd: herkansen met backoff i.p.v. blind 5u wachten` (R3) | `e279a52b…` |
| `2bb37d97…` | `[feature] Configureer de subscription-pool zodat spillover echt vuurt` (R4) | — |
| `05592c13…` | `[bug] Spawn-fout op één kaart breekt de hele dispatch-tick af (103 afgebroken ticks)` (R5) | — |

R1, R2, R4 en R5 zijn onafhankelijk en kunnen parallel. R3 consumeert de
"reeds behandeld"-toestand die R1 introduceert: zonder idempotent signaal vecht een
herkansings-schema tegen de her-armering uit gat A.
