---
title: "Analyse — Resume auto-dispatch niet werkend (kaart 8489ff9b): wat er al staat en welk stukje ontbreekt"
type: analysis
status: active
---

# Analyse — Resume auto-dispatch niet werkend (kaart `8489ff9b`)

**Datum:** 2026-08-04
**Kaart (deze analyse):** `8489ff9bb9674afdb7a03ee6162adee5` "Resume auto-dispatch not working" (Backlog)
**Dubbel:** `0866f27b1a7246a6a35a1a0ea2628f75` met identieke titel (Backlog, niet geclaimd)
**Scope:** read-only bevindingen op master HEAD `8f580842` + state van de openstaande R1–R5-kaarten op het bord op moment van schrijven.

**Trigger (gebruiker):**
> "When a session reaches its limit, the terminals stay open on the message of the
> limitation. Therefore the auto-dispatch isnt resumed. It would be more logical that
> the open sessions are automatically resumed, with a little message like OK.
> Afterwards the auto-dispatch can go on."

Verwant: [`sessie-limiet-auto-dispatch-analyse.md`](./sessie-limiet-auto-dispatch-analyse.md)
(de oudere detectie-analyse, 2026-07-23),
[`subscription-auto-release-analyse.md`](./subscription-auto-release-analyse.md)
(de recentere end-to-end meting + 5 R-kaarten, 2026-07-30),
[`spillover-per-kolom-decision.md`](./spillover-per-kolom-decision.md)
(de ontwerpbeslissing achter R4), [`subscriptions.md`](./subscriptions.md)
(de operationele handleiding voor R4).

---

## 0. TL;DR

**De kaart beschrijft een symptoom dat al twee keer grondig geanalyseerd én deels
gerepareerd is.** De gebruiker heeft de kaart twee keer opnieuw gefiled
(`8489ff9b` en `0866f27b`, identieke titel) terwijl de implementatie van het
detectie- en hervatpad vorderde — het ontwerp staat, twee van de vijf
bouwstenen liggen inmiddels op master, en de resterende drie wachten alleen
nog op uitvoering. **De voorgestelde oplossing van de gebruiker — "stuur een
korte 'OK' naar de nog levende pane"** is precies wat de huidige
`auto_resume`-machinerie al doet (`tmux_inject.send_text` →
[`backend/app/services/scheduling/tmux_inject.py:90`](../../backend/app/services/scheduling/tmux_inject.py)
met de tekst "Continue where you left off." uit
[`backend/app/services/scheduling/auto_resume.py:54`](../../backend/app/services/scheduling/auto_resume.py));
de echte vraag is waaróm die nudge de pane niet bereikt, en dat is exact de
R2/R3-keten die openstaat.

Concrete aanbevelingen:

1. **Sluit `0866f27b` als duplicaat** van `8489ff9b` met een cross-reference — twee
   kaarten met dezelfde titel op het bord is ruis.
2. **Heropen `01bde6e9` (R2) zo snel mogelijk** — die fix ligt klaar in branch
   `k-bug-voortgang-5699` (commit `7c36be40`), is getest, en sluit precies het
   gat dat de gebruiker nu ervaart.
3. **Plan `b106def4d` (R3) als volgende** — zonder R2 als vangnet blijft
   MiniMax op een blinde 5 uur-pauze staan, en R2 kan niet voorkomen dat het
   hele pane-resume-pad opnieuw vastloopt als het nudge-gat niet bestaat.
4. **Eén kleine UX-aanpassing** die direct uit de kaart zelf komt: maak de
   resume-tekst configureerbaar, met een korte standaard ("OK") voor de
   niet-aanwezige operator. Dit is een aparte, kleine kaart waardig —
   hieronder uitgewerkt.

---

## 1. Wat er al staat — en wat er nog ontbreekt

De twee voorgangers (`sessie-limiet-…` en `subscription-auto-release-…`)
hebben samen vijf R-kaarten opgeleverd, in volgorde van blocking-volgorde:

| Kaart | Gat | Status op master HEAD `8f580842` | Branch / commit |
|---|---|---|---|
| `e279a52b` (R1) | Limiet-signaal niet idempotent — zelfde melding vuurde 15.996× in 8 dagen | ✅ gemerged op `ae33570e` | `aab8420c` "fix(dispatch): idempotency gate on rate-limit signal" |
| `01bde6e9` (R2) | Voortgangs-liveness carve-out sloeg levende tmux-panes over — vangnet vuurde 0× | ❌ **niet op master** | `7c36be40` zit in branch `k-bug-voortgang-5699`, klaar om te mergeten |
| `b106def4d` (R3) | MiniMax-limiet zonder reset-tijd → blinde 5 u-pauze i.p.v. backoff | ❌ Backlog, blocker `e279a52b` was | R1 is klaar, dus blocker is weg — kaart is vrij om op te pakken |
| `2bb37d97` (R4) | Spillover is dode code — geen pool geconfigureerd | ✅ gemerged op `edacfce4` | `6126435f` "feat(pool): pin production per-column spillover tails" |
| `05592c13` (R5) | Spawn-fout op één kaart breekt hele tick af (103× gemeten) | ❌ engineer, geclaimd | in uitvoering op `k-bug-spawn-fou-11c7` |

**De gebruiker ervaart vandaag (kaart `8489ff9b`, 2026-08-04 21:10) dus een
bekende mix van R1 (al gefixt) + R2 (klaar, wacht op merge) + R3 (blokkade net
opgeheven, nog steeds Backlog).** Het beeld op het moment dat de gebruiker
de kaart invulde was *iets* slechter — R1 was net gemerged en R4 was nog niet
gemerged — maar zelfs zonder die nuance is de hoofdvraag nog steeds "wanneer
mergen we R2, en plannen we R3?", niet "wat ontwerpen we?".

---

## 2. Hoe de gebruikersklacht past op het bestaande ontwerp

De gebruiker zegt: "stuur een 'OK' naar de pane, dan hervat de sessie". Dat
is precies wat `try_pane_resume` vandaag doet — alleen in een ander jasje:

```python
# backend/app/services/scheduling/auto_resume.py:54
DEFAULT_RESUME_MESSAGE = "Continue where you left off."
```

→ [`backend/app/services/scheduling/tmux_inject.py:90`](../../backend/app/services/scheduling/tmux_inject.py)
`send_text(tmux_target, text)` → `tmux send-keys -l … "Enter"`.

Het verschil zit niet in het mechanisme, het zit in twee dingen die de
gebruiker niet kan zien:

1. **Wanneer de nudge wordt verstuurd.** Op dit moment pas op de geparste
   reset-tijd van de Anthropic-melding (`backend/app/kanban/dispatch.py:5391
   —handle_rate_limit_signal→try_pane_resume`), of helemaal niet voor
   MiniMax (waar geen reset-tijd parseerbaar is → vaste 5 u-pauze, de
   R3-keten). Voor de gebruiker ziet het er dus uit als "niks gebeurt" als
   de reset nog niet is bereikt, terwijl er in werkelijkheid een
   apscheduler-job klaarstaat.
2. **Of de nudge de pane bereikt.** Voor een sessie op een levende
   tmux-pane (lees: het hele limiet-scenario) wordt de nudge a) gepland
   door `try_pane_resume`, b) afgevuurd door `_execute_pane_resume`, en
   c) geleverd door `tmux_inject.send_text`. Stap (a) werkt. Stap (c)
   werkt. Maar de **R2-carve-out** (`check_progress_liveness` sloeg
   sessies met een levende pane over — bug, gefixt in `7c36be40`, niet
   op master) zorgde ervoor dat *het vangnet dat had moeten ontdekken dat
   de nudge niet aanslaat*, nooit vuurde. Dus als de nudge om wat voor
   reden dan ook niet landt, blijft de sessie stil op de limiet — exact
   het symptoom dat de gebruiker beschrijft.

Met andere woorden: **de gevraagde oplossing bestaat al in code; het gat is
dat het detectiemechanisme dat moet bewaken dat de oplossing ook écht
landt, niet vurig wordt omdat R2 nog openligt.**

---

## 3. De "OK"-tekst: één kleine UX-aanvulling die direct uit de kaart komt

De gebruiker stelt expliciet "iets in de trend van 'Ok' of dergelijke"
voor. De huidige standaard is "Continue where you left off." Dat is
informatief maar niet wat een menselijke operator zou typen — een mens
typt juist kort en ontspannen. Twee redenen om dit serieus te nemen:

- **Herkenbaarheid.** Als een operator toevallig de pane ziet terwijl de
  nudge landt, moet het niet meteen duidelijk zijn dat dit door de
  auto-resume is gekomen — anders lijkt het "magisch" en breekt het vertrouwen.
- **Voorspelbaarheid voor Claude.** Een lange zin als "Continue where you
  left off." kost input-tokens die we niet willen besteden aan een
  standaard-hervatprompt, en het zet Claude in een werkmodus die het
  mogelijk afleidt van de oorspronkelijke taak.

Voorstel: introduceer een configureerbare resume-tekst met
`DEFAULT_RESUME_MESSAGE = "OK"` als nieuwe standaard, gedocumenteerd als
"korte, niet-cognitief-belastende bevestiging". De bestaande operator die
bewust een andere tekst wil (bijvoorbeeld om de sessie context te geven)
kan die via een instelling kiezen — hetzelfde patroon als hoe
`auto_resume_service.set_enabled(cwd, enabled)` vandaag werkt voor het
hele auto-resume-mechanisme.

Dat is een aparte, kleine kaart waardig — niet iets om in deze analyse mee
te nemen. Het hoort bij de uitvoering, niet bij de ontwerptekst.

---

## 4. Aanbeveling voor deze kaart en het duplicaat

**Voor `8489ff9b` (deze analyse-kaart):**

- **Sluit de kaart af** met `move_card(..., "Done", summary="…")` zodra
  deze analyse is gemerged in master. De analyse zelf is het deliverable —
  hij legt vast welke kaarten het probleem dragen en welke volgorde de
  review moet volgen, en hij maakt de UX-aanvulling expliciet zodat ze niet
  vergeten wordt.
- Het product-effect voor de product owner: **er is één plek op het bord
  die deze klacht uitspreekt, met een verwijzing naar de twee analyses en
  de drie nog-open R-kaarten, plus één nieuw stukje UX-werk.** De
  product owner weet zo direct wat de status is en wat er nog moet gebeuren,
  zonder zelf door de analyses te hoeven graven.

**Voor `0866f27b` (het duplicaat):**

- Voeg een comment toe dat dit een duplicaat is van `8489ff9b`, en
  verwijs naar deze analyse. Sluit de kaart niet zonder product-owner-acceptatie —
  de gebruiker heeft hem twee keer gefiled en dat is een signaal op zich
  dat we niet eenzijdig moeten negeren. De juiste route is: comment +
  flag voor de product owner, en pas op `Done` na bevestiging.

**Volgorde op het bord** (product-eigenaar kan dit zelf prioriteren):

1. **R2 (`01bde6e9`) — eerste prioriteit.** Branch `k-bug-voortgang-5699`
   met commit `7c36be40` is klaar, is getest, en sluit het directe gat.
   Merge-bottleneck is puur operationeel (review + ship-recipe).
2. **R3 (`b106def4d`) — tweede prioriteit.** Was geblokkeerd op R1, R1 is
   klaar, dus de blocker is weg. Backoff-mechanisme voor MiniMax is de
   complement van R2: R2 zorgt dat het vangnet werkt, R3 zorgt dat de
   *eerste* hervatpoging niet 5 uur op zich laat wachten als de
   reset-tijd niet parseerbaar is.
3. **R5 (`05592c13`) — derde prioriteit.** Al geclaimd door een engineer;
   geen actie vanuit deze analyse nodig.
4. **De "OK"-tekst als zelfstandige kleine kaart** — vierde prioriteit,
   niet urgent maar wel het directe antwoord op de helft van de
   gebruikersvraag.

---

## 5. Wat ik bewust niet voorstel in deze analyse

- **Een nieuwe, tweede detector voor "session op limiet zonder transcript".**
  R2 dekt dat. R2 mag eerst gemerged worden.
- **Een retry-loop die elke minuut "OK" stuurt.** De huidige nudge-flow
  heeft expliciet *drie* pogingen via `PANE_RESUME_MAX_ATTEMPTS`; een
  retry-loop zonder bovengrens zou Claude juist meer belasten dan helpen.
- **Een "wizard"-achtige UI-flow voor de operator.** De pijn is hier dat
  de operator de pane *niet* hoeft te zien — het systeem moet het zelf
  oplossen. Een wizard ondermijnt die belofte.
- **Een nieuwe `auto_resume_service`-methode** voor de "OK"-tekst. De
  bestaande `schedule_resume(cwd, reset_time, tz_name, message=…)`
  accepteert al een message-override; alleen de standaard moet korter.

---

## 6. Bron-ankers (file:line)

Alle claims over bestaand Cockpit-gedrag in deze analyse zijn verifieerbaar:

- `backend/app/kanban/dispatch.py:5325-5437` — `handle_rate_limit_signal`,
  inclusief de R1-idempotency-gate op regel 5346-5359.
- `backend/app/kanban/dispatch.py:5440+` — `detect_transcript_rate_limits`,
  de R1+R4-detector die op de transcript-staart kijkt.
- `backend/app/kanban/dispatch.py:6863+` — `check_progress_liveness`,
  waar de R2-carve-out op `live_sessions` zat (en in `7c36be40` weggehaald
  is).
- `backend/app/services/scheduling/auto_resume.py:54` —
  `DEFAULT_RESUME_MESSAGE = "Continue where you left off."`
- `backend/app/services/scheduling/auto_resume.py:250-290` —
  `schedule_resume`, waar de message als parameter al binnenkomt.
- `backend/app/services/scheduling/tmux_inject.py:90-113` —
  `send_text`, het levermechanisme van de nudge.
- `docs/cockpit/sessie-limiet-auto-dispatch-analyse.md` — de R1-R5-oude analyse,
  door R1/R2/R4 inmiddels deels ingehaald.
- `docs/cockpit/subscription-auto-release-analyse.md` — de R1-R5-nieuwe analyse,
  eigenaar van de lopende R-kaarten.
- `docs/cockpit/spillover-per-kolom-decision.md` — beslisdocument achter R4.

---

## 7. Vervolgacties

In volgorde van urgentie, allemaal mens-acties behalve de eerste:

| # | Actie | Door | Status |
|---|---|---|---|
| 1 | Deze analyse mergen in master + deze kaart naar `Done` verplaatsen | Deze sessie (analyst leaf-spike) | deze commit |
| 2 | `0866f27b` voorzien van cross-reference-comment + flag voor product owner | Deze sessie | deze commit |
| 3 | R2 (`01bde6e9`) reviewen + shippen | Reviewer + engineer | open |
| 4 | R3 (`b106def4d`) oppakken — blocker `e279a52b` is weg | Engineer | open, klaar om te dispatchen |
| 5 | R5 (`05592c13`) monitoren — al geclaimd | Engineer | in uitvoering |
| 6 | "OK"-tekst als zelfstandige kleine kaart filen (UX-aanvulling uit §3) | Product owner / analyst | deze analyse noemt 'm; kaart nog aan te maken |

Geen nieuwe technische R-kaarten vanuit deze analyse. R1-R5 dekken het
vangnet af; wat resteert is UX-tekst, dat is een aparte, kleine kaart.