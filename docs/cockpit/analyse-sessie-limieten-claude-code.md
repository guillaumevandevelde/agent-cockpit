# Analyse: sessie-limieten blokkeren auto-dispatch nog steeds

**Datum:** 2026-07-07
**Status:** Analyse / root-cause — niets hiervan is gebouwd, dit is input voor vervolgkaarten.
**Trigger:** kanban-kaart "Analyse - Sessie limieten Claude Code": *"De sessie limieten van
de subscriptie blokkeren nog steeds de autonomie van de applicatie. De sessies op de agent
bridge vallen stil op sessie limiet bereikt en daarna kunnen ze niet meer verder."*

---

## 1. De vraag

Sessies die de kanban auto-dispatcher opstart via de agent bridge (tmux) vallen stil zodra
Claude Code de account-brede sessie-limiet raakt, en komen daarna niet meer vanzelf verder —
de auto-dispatch lijkt geblokkeerd te blijven. Gevraagd: analyseer grondig waarom, en wat we
eraan kunnen doen.

## 2. Belangrijkste bevinding: de bestaande oplossing is er al — maar staat uit

Dit is geen leeg blad. Er bestaat al een volledig uitgewerkte, geteste pijplijn die precies dit
scenario zou moeten afvangen (`backend/tests/test_dispatch_pause.py`,
`backend/tests/test_auto_resume.py`):

1. Claude Code's eigen **`Notification`-hook** vuurt wanneer een sessie de limiet raakt, met
   een bericht in de vorm `"You've hit your session limit · resets 11:10pm (Europe/Brussels)"`.
2. `hook_script.render_hook_command`/`settings_hooks_block`
   (`backend/app/services/scheduling/hook_script.py:13-31`) genereren de shell-hook die dit
   bericht POST't naar `/api/v1/scheduled-messages/hook-event`.
3. Die endpoint (`backend/app/api/v1/scheduled_messages/router.py:149-193`) herkent het bericht
   via `auto_resume_service.is_limit_notification` (`backend/app/services/scheduling/auto_resume.py:37`),
   roept `move_limited_session_to_resume()` aan (`backend/app/kanban/dispatch.py:834-864`) —
   die verplaatst de kaart naar de vaste kolom **"To Resume"**, killt de vastzittende tmux-sessie
   en **geeft de claim vrij** (`dispatch.py:774-818`, `_move_to_resume`) — en zet daarna een
   **globale dispatch-pauze** tot het geparste reset-tijdstip
   (`backend/app/kanban/dispatch_pause.py`), zodat de dispatch-tick niet blijft respawnen tegen
   dezelfde muur.
4. Optioneel (per project, opt-in) kan `auto_resume_service.schedule_resume()` daarna zelf een
   vervolgsessie spawnen op het reset-tijdstip en `"Continue where you left off."` injecteren
   (frontend-toggle: `frontend/src/features/scheduled-messages/components/AutoResumeToggle.tsx`).

**Dit is precies het mechanisme dat de kaart vraagt.** Het probleem is niet dat het ontbreekt —
het probleem is dat het **nooit wordt geactiveerd**.

### 2.1 Root cause, empirisch bevestigd

`render_hook_command`'s eigen docstring zegt: *"Install by adding entries to
`~/.claude/settings.json` under 'hooks'"* — dit is een **handmatige installatiestap**. Gecheckt:

- `settings_hooks_block()` (de functie die het juiste hooks-blok genereert) wordt **nergens**
  in de backend aangeroepen buiten zijn eigen unit-test (`grep -rn settings_hooks_block
  backend/app` levert alleen `hook_script.py` en `test_hook_script.py` op). Er is geen
  installer-script, geen startup-routine, geen API-endpoint en geen knop in de "Hooks"-feature
  (`frontend/src/features/hooks/`) die dit blok ooit wegschrijft.
- De daadwerkelijke `~/.claude/settings.json` op deze machine bevat op dit moment **alleen**
  een ongerelateerde `SessionStart`-hook (`context-mode-cache-heal.mjs`) — geen `Notification`-,
  `Stop`- of `UserPromptSubmit`-hook, en geen enkele verwijzing naar
  `scheduled-messages/hook-event`.
- Het eigen implementatieplan documenteert dit al als een niet-afgevinkte, handmatige
  prerequisite: `docs/superpowers/plans/2026-06-14-scheduled-session-resume.md:1239` —
  *"Prereqs: ... the scheduling hooks installed in `~/.claude/settings.json` (the app exposes
  `settings_hooks_block`; confirm they POST to `/api/v1/scheduled-messages/hook-event`)."*
  `docs/cockpit/00-orientation.md` bevestigt dat de bijbehorende "Task 12 — runtime e2e" nooit
  is afgerond.

**Conclusie:** de hele limiet-detectie-pijplijn is vandaag **dode code** vanuit het perspectief
van de applicatie — hij wordt nooit gevoed, dus nooit getriggerd. Zonder de hook:

- Er is **geen enkel** ander detectiepad. `capture_pane_preview()`
  (`backend/app/services/agent_bridge/discovery.py:91`) wordt alleen gebruikt voor UI-previews;
  `agent_activity._infer_status()` matcht alleen op `"waiting for"/"permission"/"approve"/
  "error"/"failed"` — niets over limieten/quota. Er bestaat geen polling-loop die tmux-pane-tekst
  scant op limiet-strings.
- De **dode-sessie-reaper** (`reap_stale_claims`, `dispatch.py:872`) grijpt alleen in wanneer een
  tmux-sessie écht weg is (crash, reboot, handmatig gesloten). Een sessie die op de limiet-prompt
  blijft hangen, **blijft leven** in tmux — de CLI sluit niet af — dus de reaper ziet niets mis.
- Resultaat: de kaart blijft voor altijd geclaimd in zijn agent-kolom, bezet permanent één van
  de beperkte concurrency-slots per project (`session_registry`/`DEFAULT_MAX_SESSIONS`). Naarmate
  meer sessies de account-brede limiet raken (ze delen allemaal dezelfde 5-uurs-muur), rakan
  steeds meer slots permanent bezet door vastzittende sessies, tot de auto-dispatcher voor het
  hele project — en uiteindelijk elk project op dit toestel — feitelijk stilvalt. Dat is exact
  het gerapporteerde symptoom.

### 2.2 Tweede-orde risico, ook ná het oplossen van 2.1

Zelfs met de hook correct geïnstalleerd is de matching fragiel:

- `_LIMIT_PATTERN` (`auto_resume.py:17-20`) herkent uitsluitend het exacte format
  `"hit your session limit ... resets HH:MM(am/pm) (Timezone)"` — de 5-uurs rol-limiet.
  Anthropic's **wekelijkse/model-specifieke caps** (Pro/Max) of een afwijkende bewoording in een
  latere Claude Code CLI-versie zouden een ander berichtformaat kunnen hebben (bv. geen vast
  kloktijdstip, "resets in 6 days"). In dat geval retourneert `parse_reset_time()` `None`.
- Kijkend naar `scheduled_messages/router.py:171-180`: `set_paused_until` wordt **alleen**
  aangeroepen als `parsed` niet `None` is. Bij een onherkend format wordt de kaart nog wel naar
  "To Resume" verplaatst (dat gebeurt onvoorwaardelijk) en de claim vrijgegeven — maar de
  **globale dispatch-pauze wordt overgeslagen**. Dat is precies het scenario waar
  `dispatch_pause.py`'s eigen docstring voor waarschuwt: zonder pauze blijft de tick nieuwe
  kaarten spawnen die vrijwel onmiddellijk tegen dezelfde account-brede muur aanlopen — "spinning
  and burning the account's remaining requests."
- "To Resume" is een **vaste, niet-automatisch-opgepikte kolom**: een kaart komt daar pas
  automatisch weer vandaan als (a) de per-project `auto-resume`-opt-in aanstaat (standaard
  **uit** — `AutoResumeService._enabled` default `False`), of (b) een mens `redispatch_card`
  aanroept. Zonder opt-in is "auto-dispatch hervat vanzelf" dus niet waar, ook al werkt de
  detectie perfect.

## 3. Opties

### Optie A — De ontbrekende hook-installatie automatiseren (fixt de root cause)

**Idee:** `settings_hooks_block()` bestaat al en genereert exact het juiste blok. Er ontbreekt
alleen een manier om het daadwerkelijk in `~/.claude/settings.json` te krijgen zonder handmatig
JSON te knippen-en-plakken. Twee vormen, niet-exclusief:
1. Een **installer-script** (`scripts/install-cc-hooks.sh` of een subcommando van
   `cockpit.sh`) dat het blok non-destructief merget in `~/.claude/settings.json` (bestaande
   hooks — zoals de huidige `SessionStart`-hook voor iets anders — laten staan, alleen de vier
   ontbrekende event-types toevoegen/aanvullen).
2. Een **"Verify hooks" check** in de backend (bv. bij startup, of een `/api/v1/status`-veld)
   die leest of `~/.claude/settings.json` de vier verwachte hook-commando's bevat, en zo niet,
   dit zichtbaar meldt op de Dashboard/Status-pagina — zodat deze silent failure nooit meer
   weken onopgemerkt blijft.

**Effort:** klein (S) — puur een schrijf-actie op een bestaand, correct gegenereerd blok, plus
een leesbare healthcheck.

**Risico:** het schrijven naar `~/.claude/settings.json` is een gedeeld, globaal bestand (ook
gebruikt door interactieve `claude`-sessies buiten Cockpit) — de merge moet strikt additief zijn
(nooit een bestaande hook-key overschrijven, alleen ontbrekende event-entries toevoegen) om geen
ongerelateerde hooks (zoals de huidige `context-mode-cache-heal.mjs`) te breken.

### Optie B — Detectie robuuster maken tegen berichtvariatie (fixt 2.2)

**Idee:** `is_limit_notification`/`parse_reset_time` verbreden zodat een onherkend limiet-bericht
niet stilzwijgend de globale pauze overslaat. Concreet:
1. Als de tekst wél op een limiet lijkt (bv. bevat "limit" + "reset") maar het exacte
   klok-tijd-format niet parseert, val terug op een **vaste, conservatieve pauze** (bv. 5 uur —
   dezelfde `SESSION_DURATION_HOURS` die `usage_service.py` al hanteert voor Anthropic's
   billing-blocks) in plaats van helemaal geen pauze.
2. Log dit geval expliciet (`comment` op de kaart, of een backend-warning) zodat een nieuw
   berichtformaat zichtbaar wordt in plaats van een silent no-op.

**Effort:** klein (S) — een fallback-tak in bestaande, al geteste functies.

**Risico:** een te ruwe fallback-pauze kan de dispatcher onnodig lang stil leggen als het
eigenlijk om een korte/andere melding ging — daarom eerst loggen/waarschuwen vóór je een harde
fallback-pauze inbouwt (zelfde voorzichtigheidsprincipe als optie B in de eerdere
multi-abonnementen-analyse).

### Optie C — Auto-resume opt-in standaard aanzetten voor auto-dispatch-projecten

**Idee:** als een project auto-dispatch aan heeft staan (`list_autodispatch_projects`), is de
verwachting impliciet al "dit moet zelfstandig doorlopen" — de huidige aparte, standaard-uit
`auto-resume`-toggle per project is dan een verrassende extra handmatige stap. Overweeg: laat
auto-dispatch-enabled projecten automatisch ook auto-resume-enabled zijn (of maak dit één
instelling in plaats van twee).

**Effort:** klein (S) — een defaultwaarde-wijziging plus eventueel UI-tekst die de koppeling
uitlegt.

**Risico:** sommige gebruikers zetten mogelijk bewust auto-dispatch aan zonder dat ze willen dat
er 's nachts automatisch een vervolgsessie met `acceptEdits`-permissies wordt gespawned na een
limiet-reset — dit raakt het autonomie-niveau, dus vraagt een expliciete keuze/communicatie, geen
stille gedragswijziging.

### Optie D — Concurrency-slot vrijgeven ook zónder geldige hook, als vangnet

**Idee:** een aanvullend, hook-onafhankelijk vangnet: als `reap_stale_claims` een sessie ziet die
al langer dan X minuten "idle"/onveranderd pane-output toont (via `capture_pane_preview`) zonder
dat de kaart is voltooid, behandel die net als een dode sessie voor de concurrency-cap (los van
de vraag of het specifiek een limiet-bericht was). Dit vangt niet alleen limiet-stalls op, maar
ook elke andere reden waarom een sessie muurvast komt te zitten zonder te crashen.

**Effort:** middel (M) — vereist een nieuw idle-timeout-concept met pane-diffing, waar vandaag
geen watchdog voor bestaat (bevestigd: `idle_state.py` heeft geen tijdsgebonden staleness-check).

**Risico:** een generieke idle-timeout kan een legitiem lang-draaiende, stille tool-call (bv. een
lange build) verwarren met een echte stall — vereist zorgvuldige drempel-tuning en is dus een
grotere, apart te valideren kaart. Behandel als vangnet ná optie A/B, niet als vervanging.

## 4. Aanbeveling — gefaseerd

| Fase | Optie | Waarom eerst | Effort |
|---|---|---|---|
| 1 | **A** — hook-installatie automatiseren + zichtbare healthcheck | Dit is de daadwerkelijke root cause: alle bestaande, geteste machinery is vandaag dood omdat niemand het bericht ooit ontvangt. Zonder dit heeft geen van de andere opties enig effect. | S |
| 2 | **B** — fallback-pauze bij onherkend limiet-bericht | Voorkomt dat een toekomstige wijziging in Anthropic's berichttekst (weekly caps, CLI-versiedrift) dezelfde silent-failure herhaalt. | S |
| 3 | **C** — auto-resume en auto-dispatch koppelen (of expliciet toelichten) | Sluit de laatste handmatige stap tussen "kaart geparkeerd in To Resume" en "autonome doorloop" — maar vraagt een bewuste UX-keuze. | S |
| 4 | **D** — generiek idle-timeout-vangnet | Waardevol als achtervang voor niet-limiet-gerelateerde stalls, maar een grotere, apart te ontwerpen kaart (nieuw staleness-concept). | M |

**Kernboodschap:** dit is geen ontwerpprobleem — de architectuur (hook → detectie → kaart naar
"To Resume" → claim vrij → globale pauze → optionele auto-resume) is al correct gebouwd en
getest. Het enige dat de autonomie vandaag blokkeert, is dat **de hook die dit alles voedt nooit
is geïnstalleerd** in `~/.claude/settings.json`. Fase 1 alleen al zou het gerapporteerde probleem
moeten oplossen; fase 2–4 maken de pijplijn robuust tegen toekomstige randgevallen.

## 5. Open vragen / risico's

- **Scope van de merge in `~/.claude/settings.json`**: dit bestand is gedeeld met elke
  interactieve `claude`-sessie op dit toestel (inclusief deze sessie zelf) — een installer moet
  strikt additief zijn en bestaande hooks (zoals de huidige `SessionStart`-cache-heal-hook)
  ongemoeid laten.
- **`jq`/`curl`-beschikbaarheid**: `hook_script.py`'s eigen docstring noemt dit al als vereiste
  in de sessie-omgeving (WSL Ubuntu heeft beide) — een installer/healthcheck kan dit meteen
  meecontroleren.
- **Weekly/model-caps zijn niet gevalideerd**: er is geen voorbeeldbericht van Anthropic's
  wekelijkse limiet beschikbaar in deze analyse — optie B's fallback-pauze-duur (voorstel: 5 uur,
  gelijk aan `usage_service.SESSION_DURATION_HOURS`) is een aanname die bij implementatie
  geverifieerd moet worden tegen een echt wekelijks-limiet-bericht zodra er een gevangen wordt.

## 6. Voorgestelde vervolgkaarten

1. "Installeer/verifieer de scheduling-hooks automatisch in `~/.claude/settings.json`" (optie A,
   fase 1) — inclusief een zichtbare healthcheck op Dashboard/Status.
2. "Fallback-pauze bij onherkend usage-limiet-bericht" (optie B, fase 2).
3. "Koppel auto-resume-opt-in aan auto-dispatch-projecten (of maak de relatie expliciet in de
   UI)" (optie C, fase 3).
4. "Generiek idle-timeout-vangnet voor muurvast zittende agent-sessies" (optie D, fase 4 —
   spike/ontwerp eerst, los van limiet-detectie).
