---
title: "Analyse — Run vasthouden + gebufferde events over een transport-onderbreking"
type: analysis
status: decided
---

# Analyse — Run vasthouden + gebufferde events over een transport-onderbreking

**Datum:** 2026-07-21
**Kaart:** `805d747f…` (kind van Lemma-analyse `b00f3705…`)
**Status:** Analyse afgerond — het letterlijke Lemma-patroon wordt **niet** overgenomen;
twee scoped vervolgkaarten op de plek waar het onderliggende probleem wél echt is.

**Verwant:**
[`lemma-platform-analyse.md` §4.1](./lemma-platform-analyse.md) (bron),
[`headless-stream-json-transport-spike.md`](./headless-stream-json-transport-spike.md),
[`kanban-dispatch-spec.md`](./kanban-dispatch-spec.md),
[`human-takeover-headless-decision.md`](./human-takeover-headless-decision.md).

---

## TL;DR

1. **Het hold-window-patroon is niet overdraagbaar zoals gesteld.** Lemma's
   `_HeldRun` bestaat omdat hun daemon een *remote websocket* bezit die kan
   wegvallen terwijl daemon én subprocess blijven leven. Bij ons is er geen
   scheidbare verbinding: het transport ís de eigendomsrelatie. Als de eigenaar
   leeft, is er niets onderbroken; als de eigenaar dood is, is er niets om vast
   te houden. Een grace-window heeft daartussen geen plek. Zie §2–3.
2. **Voor het transport dat we daadwerkelijk draaien (`worktree`/tmux) is het
   probleem al opgelost** — niet met een buffer, maar met een architectuurkeuze:
   de agent draait in een onafhankelijke procesboom en liveness wordt élke tick
   opnieuw afgeleid uit `tmux ls`. Er is geen in-memory eigendom om te verliezen.
   Geverifieerd: dit project staat op `transport:…=worktree`. Zie §3.1.
3. **Het echte gat zit in het `headless`-transport**, dat vandaag opt-in en
   ongebruikt is. Daar is de run wél een kind van het backend-proces, is liveness
   wél in-memory, en gaan events wél over een pipe die met de backend sterft.
   Zie §3.2.
4. **Twee concrete bevindingen verdienen een kaart** (§6): een bereikbare
   orphan-bug in `run_headless` die twee agents op één branch kan zetten, en de
   ontbrekende herstart-overleving van het headless-transport. Beide zijn
   *headless*-kaarten, geen hold-window-kaarten.
5. **De gevraagde grace-window-parameter vervalt** met het patroon zelf; de
   buffer-cap blijft relevant in gewijzigde vorm (een on-disk event-log-cap) en
   is hier **gemeten**, niet geschat. Zie §5.

---

## 1. Wat de kaart vroeg, en waarom het antwoord kantelt

De kaart neemt als premisse: *"vandaag is een onderbroken run verloren werk"*, en
stelt voor om Lemma's `_RunEventSink` + `_HeldRun` + `_reap_expired_held_runs`
over te nemen als levenscyclus-patroon.

Die premisse klopt niet zonder kwalificatie. "Een onderbroken run" is geen
enkelvoudig geval — het gedrag hangt volledig af van **wie de agent-procesboom
bezit**. Wij hebben drie transports met drie verschillende eigendomsmodellen, en
het antwoord verschilt per transport. De analyse hieronder is daarom
transport-gestratificeerd; dat is de eigenlijke scope-bepaling die deze kaart
vroeg.

---

## 2. Waarom Lemma een hold-window nodig heeft en wij niet

Lemma's daemon (`lemma-cli/lemma_cli/daemon/runner.py`, Apache-2.0) heeft drie
partijen: een **remote server**, een **lokale daemon**, en een **lokaal
agent-subprocess**. De websocket tussen server en daemon kan wegvallen terwijl
de andere twee gewoon doorlopen. Precies die asymmetrie maakt `_HeldRun`
zinvol: er is een levend subprocess zonder afnemer voor zijn events, dus je
buffert de events en houdt het proces vast tot iemand reattacht.

Bij ons ontbreekt de middelste partij. De backend ís de daemon én de afnemer, en
het "transport" is geen verbinding maar een eigendomsrelatie (een tmux-server,
een pipe, of een DB-rij). Daaruit volgt een tweedeling zonder tussengebied:

> **Als de eigenaar leeft, is er niets onderbroken. Als de eigenaar dood is, is
> er niemand om vast te houden.**

Een grace-window bemiddelt in Lemma tussen die twee toestanden. Bij ons bestaat
die tussentoestand niet, en een timer introduceren die op niets wacht is geen
robuustheid maar een extra faalmodus.

**Wat wél overdraagbaar is** is niet het mechanisme maar het *doel*: een run mag
niet sterven omdat de backend herstart. Lemma bereikt dat met hold+buffer omdat
hun run al losgekoppeld is. Wij bereiken hetzelfde door **het eigendom los te
koppelen** — en voor twee van onze drie transports is dat al gebeurd.

---

## 3. Onderbrekingsmatrix per transport

De vier onderbrekingsklassen die de kaart noemt (backend-herstart, crash van de
runner, host-reboot, netwerk) tegen onze drie transports. Dit is AC 1.

| Onderbreking | `worktree` (tmux, **in gebruik**) | `headless` (opt-in, ongebruikt) | `sandcastle` |
|---|---|---|---|
| **Backend-herstart** (`cockpit.sh restart`, watchdog, crash) | Run overleeft volledig. Claim staat in de DB, liveness wordt opnieuw afgeleid uit `tmux ls`. Geen verlies. | **Run sterft.** `kill_tree` reapt de hele descendant-boom; ook zonder dat sluit de stdout-pipe en sterft het kind. Registry is in-memory en is weg. | Run overleeft; liveness komt uit de `SandcastleRun`-rij. |
| **Crash van de runner-task** | N.v.t. — er is geen in-process runner. | **Orphan.** `run_headless`' `finally` geeft slot + registry vrij maar termineert het proces niet → onzichtbaar levend proces + re-dispatch. Zie §4. | N.v.t. |
| **Host-reboot** | Proces is echt weg. `session_recovery` hervat uit het transcript — correct, niets om vast te houden. | Idem, maar zonder transcript-pad (§3.2). | Container-afhankelijk. |
| **Netwerk** | N.v.t. lokaal. De enige echte netwerklaag is CLI↔Anthropic; die handelt de CLI zelf af en zijn `rate_limit`-event bereikt ons al getypeerd. | Idem. | Wel relevant, maar buiten deze kaart. |

### 3.1 Waarom het tmux-pad al klopt

Twee eigenschappen doen het werk, en ze zijn het vermelden waard omdat ze precies
de rol vervullen die `_HeldRun` bij Lemma speelt:

- **De tmux-server is geen kind van de backend.** `kill_tree` in
  `scripts/cockpit.sh` loopt de descendants van de supervisor af; de tmux-server
  staat daar niet in. Een backend-herstart is voor een lopende agent onzichtbaar.
- **Liveness is afleidbaar, niet bewaard.** `_live_sessions()` vraagt tmux elke
  tick opnieuw. Er is geen in-memory waarheid die een herstart kan verliezen —
  wat een hold-window bij Lemma juist moet compenseren.

Dat is dezelfde robuustheid, bereikt met minder machinerie. Dit vervangen door een
hold-window zou een downgrade zijn.

**Eén nuance, bewust geen kaart.** Hook-events worden fire-and-forget bezorgd
(`curl -s -f --connect-timeout 0.25 -m 1 … || true` in `~/.claude/settings.json`):
een POST die faalt is stil weg. Dat is letterlijk "events zonder buffer". De
blast radius is echter klein en zelf-begrenzend: `_spawn_times` en
`_spawn_received_hooks` leven in hetzelfde backend-proces, dus bij een herstart
verdwijnen de verwachting én het signaal samen en ontstaat er geen vals-stuck
sessie. Er blijft een smal venster (backend bereikbaar-maar-traag, hook-POST
verloopt op de 1s-cap) waarin een sessie na `STUCK_SESSION_TIMEOUT_S=120` als
stuck wordt gelezen. Maar `reap_stale_claims` grijpt alleen in als de pane
óók een rate-limit-patroon matcht, dus een gemiste hook alleen kost niets.

Te speculatief voor een kaart; hier genoteerd voor als het ooit wél bijt.

### 3.2 Waar het gat echt zit: het headless-transport

`headless_transport` spawnt via `asyncio.create_subprocess_exec` **zonder**
`start_new_session=True`, dus het `claude`-proces zit in de procesboom van
uvicorn. Drie gevolgen, alle drie in `backend/app/kanban/headless_runner.py`:

1. **Eigendom is fataal gekoppeld.** `kill_tree` ruimt het kind mee op; en zelfs bij
   een kale uvicorn-crash sluit de stdout-pipe waardoor het kind op zijn
   volgende write een `EPIPE`/`SIGPIPE` krijgt.
2. **Liveness is in-memory.** `_headless_processes` is een module-dict
   (`headless_runner.py:64`). Na een herstart is die leeg, dus
   `live_headless_sessions()` meldt élke headless run als dood en de reaper
   ruimt de claim op.
3. **Events zijn vluchtig.** Het event-verkeer loopt door de pipe naar
   `_consume_stream`; alleen `rate_limit` heeft een durabel effect
   (`set_paused_until`). De rest wordt gelogd. Er is dus vandaag weinig
   *event*-verlies om te bufferen — het verlies is de **run**.

Belangrijk voor de prioriteit: geverifieerd in `kanban_meta` staat dit project op
`transport:git:github.com/guillaumevandevelde/claude-cockpit = worktree`. Het
headless-pad is dus **latent**, niet actief pijnlijk. Dat maakt §6.2 een
"repareren vóór adoptie"-kaart, geen incident.

---

## 4. Bevinding: bereikbare orphan-bug in `run_headless`

Los van het hold-vraagstuk kwam één echte bug boven die precies de faalmodus
veroorzaakt die de kaart wil vermijden — verloren werk plus dubbel werk.

`map_stream_event` laat een onbekend payload-type bewust ongewijzigd door
(`{"type": ptype, **payload}`) zodat `parse_structured_event` een
`ValidationError` opgooit met de originele payload erin — een expliciet
gedocumenteerde debug-keuze. Maar `_consume_stream`'s leeslus vangt die
exception niet, en `run_headless`' `finally` doet dit:

```python
finally:
    _headless_processes.pop(session_name, None)
    session_registry.release_external(session_name)
```

Het subprocess wordt **niet** getermineerd. De volledige keten:

> Claude emit een event-type dat wij niet mappen (bv. een `system`-subtype
> buiten `init`, of een nieuw type na een CLI-update). Dat geeft een
> `ValidationError`. De registry-entry verdwijnt, het slot komt vrij, en
> **het proces leeft door**.
>
> `live_headless_sessions()` meldt de sessie dood. `reap_stale_claims`
> verplaatst de kaart naar *To Resume* of geeft de claim vrij. Dat leidt
> tot her-dispatch. En dus tot **twee agents in dezelfde worktree op
> dezelfde branch**.

De exception verdwijnt bovendien vrijwel geruisloos: de task-`done_callback` is
`_headless_start_tasks.discard`, die de exception niet ophaalt, dus hij komt pas
bij GC in de log als "Task exception was never retrieved".

Dit is dezelfde klasse als kaart `4ed4edb9…` (MCP-disconnect → claim-release
terwijl de sessie leeft), maar met een andere root cause: daar is de liveness-bron
te gevoelig, hier is hij te vergeetachtig. Beide horen apart gefixt.

✅ **Geïmplementeerd (kaart `d373be64…`)** — aanpassingen in `backend/app/kanban/headless_runner.py`:
(1) een `pydantic.ValidationError` plus `KeyError`/`TypeError`/`AttributeError`/`ValueError` rond `parse_structured_event(map_stream_event(payload))` in `_consume_stream` wordt gelogd met de originele payload erin en `continue`t. Een onbekend event-type of een misvormd payload doodt de run niet meer, exact zoals de bestaande non-JSON-regel-tolerantie.

(2) `run_headless` en `_consume_stream` termineren het subproces in hun `finally`-blok (SIGTERM + 2s-grace + SIGKILL-fallback) **vóór** ze de registry leeghalen en het slot vrijgeven. Zo laat élke exit-pad de subprocess dood achter en ontstaat er geen "dood-gemeld-maar-nog-levend"-venster waar de reaper op kan re-dispatchen.

(3) het `done_callback` logt nu uitzonderingen in plaats van ze stil te laten vallen.

Regressietests in `backend/tests/test_headless_transport.py`: één voor de tolerantie van één onbekend event, één voor het KeyError-pad (misvormd payload), één voor het terminatie-gedrag op een onverwachte exception, één voor de SIGKILL-fallback tegen een SIGTERM-negérend kind. En één voor de zichtbare logging via het nieuwe `_headless_task_done_callback`.

---

## 5. Afbakening en parameters

### 5.1 Afbakening tegenover `session_recovery` (AC 2)

Er is geen overlap, en dat volgt uit één regel:

> **Vasthouden geldt zolang het agent-proces leeft. `session_recovery` / *To
> Resume* geldt zodra het dood is.**

Beide mechanismen lezen dezelfde predikaat — proces-liveness — dus ze kunnen per
constructie niet allebei van toepassing zijn. Er is dan ook geen coördinatielaag
of prioriteitsregel nodig; die zou het ontwerp alleen kwetsbaarder maken.

Wat vandaag misgaat is dan ook geen overlap maar een **vals-negatief op
liveness**: het headless-orakel meldt "dood" terwijl het proces leeft (§3.2 en
§4), waarna `session_recovery`/de reaper het juiste doen op een onjuiste premisse.
Dat maakt de hele investering kleiner dan de kaart aannam — het te repareren
onderdeel is het liveness-orakel, niet een nieuw hold-mechanisme. `session_recovery`
zelf blijft ongewijzigd en correct.

### 5.2 Grace-window (AC 3)

**Vervalt met het patroon.** Er is geen toestand waarin een run wacht op een
reattach. Na de fix van §6.2 is een headless run ofwel levend — en dan
wordt hij bij startup geadopteerd, vóór de dispatch-scheduler draait
(dezelfde ordening die `session_recovery` al gebruikt) — ofwel dood, en
dan geldt het bestaande resume-pad. Een timer daartussen zou een
verzonnen toestand bewaken.

Wat er wél voor terugkomt is een **ordenings**-eis, geen duur: adoptie van
levende headless runs moet in de startup-lifespan vóór de reaper plaatsvinden.

### 5.3 Buffer-cap — **gemeten**, niet geschat (AC 3)

De cap verhuist van "aantal events in geheugen" naar "grootte van het on-disk
event-log per run" (§6.2). Gemeten op onze eigen 998 gedispatchte
sessie-transcripts:

| Metriek | mediaan | p90 | p99 | max |
|---|---|---|---|---|
| Regels (≈ events) per run | 131 | 504 | 995 | 1.970 |
| Bytes per run | 0,36 MB | 1,15 MB | — | 7,70 MB |

Reproductie (read-only):

```bash
cd ~/.claude/projects
find . -path "*worktrees*" -name "*.jsonl" -exec wc -l {} + | grep -v ' total$' \
  | awk '{print $1}' | sort -n \
  | awk '{a[NR]=$1} END{printf "n=%d median=%d p90=%d p99=%d max=%d\n", NR, a[int(NR*0.5)], a[int(NR*0.9)], a[int(NR*0.99)], a[NR]}'
find . -path "*worktrees*" -name "*.jsonl" -printf "%s\n" | sort -n \
  | awk '{a[NR]=$1} END{printf "median=%.2fMB p90=%.2fMB max=%.2fMB\n", a[int(NR*0.5)]/1048576, a[int(NR*0.9)]/1048576, a[NR]/1048576}'
```

**Voorstel: 16 MB per run, met afkap aan de kop (oudste events eerst weg).** Dat
is ~2× de grootste run die we ooit hebben gehad en ~14× de p90, dus in de
praktijk kapt hij nooit af. Hij bestaat om een pathologische loop te
begrenzen, niet om normaal verkeer te knippen. Bij 50 gelijktijdige runs is
de bovengrens 800 MB op schijf — acceptabel voor een lokaal platform, en de
logs worden bij worktree-gc mee opgeruimd.

**Eén expliciete kwalificatie op deze meting:** transcripts zijn een *proxy* voor
stream-json-output, niet hetzelfde formaat. Ze beschrijven dezelfde conversatie
met vergelijkbare granulariteit (hele assistant-berichten, geen token-deltas,
zolang `--include-partial-messages` uit blijft), dus de ordegrootte draagt — maar
de exacte bytes verschillen. De marge van 14× boven p90 is ruim genoeg gekozen om
die onzekerheid te absorberen; wie later `--include-partial-messages` aanzet moet
dit getal opnieuw meten, want token-deltas veranderen de ordegrootte wél.

---

## 6. Vervolgkaarten (AC 4)

Twee kaarten, volledig onafhankelijk — geen `depends_on`. Beide zijn
*headless*-kaarten; het hold-window zelf wordt niet gebouwd.

### 6.1 `run_headless` laat een subprocess achter bij een parse-fout
De bug uit §4. Klein, zelfstandig, en de enige van de twee die een *bestaande*
faalmodus wegneemt (twee agents op één branch) in plaats van een toekomstige
mogelijkheid.

### 6.2 Headless run overleeft een backend-herstart niet
De constructieve vertaling van Lemma's §4.1: eigendom loskoppelen
(`start_new_session=True`), liveness afleidbaar maken uit een durabele bron
in plaats van een module-dict. En het event-verkeer naar een on-disk log
schrijven dat bij adoptie hervat kan worden. Gated op adoptie van het
headless-transport — vandaag staat het project op `worktree`.

### Bewust géén kaart
- **Het hold-window / `_HeldRun`-patroon zelf** — §2: er is geen tussentoestand
  om te bewaken.
- **Gebufferde hook-events voor het tmux-pad** — §3.1: het venster is smal en
  zelf-begrenzend; te speculatief.
- **Ping/pong-heartbeat** — Lemma heeft die nodig omdat een websocket stil kan
  sterven. `tmux ls` en `proc.returncode` liegen niet en hebben geen
  heartbeat nodig.

---

## 7. Licentie

Conform de grens uit [`lemma-platform-analyse.md` §5](./lemma-platform-analyse.md):
alles wat hier uit Lemma is gelezen komt uit `lemma-cli/` (Apache-2.0), waar idee
én vorm overgenomen mogen worden met bronvermelding. Er is in deze analyse geen
code gekopieerd — de conclusie is juist dat het mechanisme niet past. De
AGPL-delen (`lemma-backend/`, `lemma-frontend/`) zijn hier niet geraadpleegd.

---

## 8. Bewust buiten scope

- **Geen runtime-meting van het headless-transport.** Het is opt-in en op dit
  project uitgeschakeld. De uitspraken in §3.2 en §4 zijn afgeleid uit de
  code (`headless_runner.py`, `dispatch.py`, `scripts/cockpit.sh`), niet uit
  observatie van een draaiende headless run. §6.1 vraagt daarom expliciet
  om een regressietest die de faalmodus aantoont vóór de fix.
- **Geen kosten-/besparings-claim.** De enige getallen in dit doc zijn de
  gemeten transcript-groottes van §5.3, met reproductie-commando; er is geen
  token-, geld- of latency-schatting.
