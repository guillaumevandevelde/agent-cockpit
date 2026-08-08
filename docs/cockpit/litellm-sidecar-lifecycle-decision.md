---
title: "LiteLLM als sidecar — lifecycle, faalgedrag en scope van het kritieke pad"
type: decision
status: decided
---

# LiteLLM als sidecar — lifecycle, faalgedrag en scope van het kritieke pad

**Datum:** 2026-07-27
**Status:** besloten
**Kaart:** `2bfefe2241f54bfab9723f8f4c9a03e1`
**Uitkomst:** De LiteLLM-sidecar draait als **opt-in, gesuperviseerde derde service onder `cockpit.sh`**. Dispatch is **fail-closed** op een dode proxy en de terugval gebeurt één laag hoger — in de pool-router vóór de spawn — in plaats van in een error-handler ná de fout.

✅ **Geïmplementeerd (kaart `893033c6…`, V1):** opt-in `cockpit.sh`-service met `/health/liveliness`-watchdog, eigen venv met `litellm==1.93.0` + `prisma` gepind in `config/litellm/requirements.txt`, conditionele doctor-check die `check-litellm-hardening.sh` hergebruikt, `*.example`-configsjabloon + gitignored real config. De upgrade-procedure uit §7 staat nu inline in het requirements-bestand zelf.

✅ **Geïmplementeerd (kaart `424c23d4…`, V2, herevalideerd 2026-08-04):** een derde bron in `_paused_providers_for_pool` (`backend/app/kanban/dispatch.py:1208-1226`) — een per-endpoint `GET base_url`-probe met 30 s TTL-cache, fail-soft op timeout/DNS/connection refused (`bij twijfel = beschikbaar`).

Het herstel hoort in de selectie, niet in de error-handler, precies zoals §3.2 voorschrift: bij een dode proxy in de vangnet-modus pauzeert de pool de provider. `_pick_pool_choice` blijft de dode vangnet teruggeven — de "laatste val-terug"-tak in `pick_subscription_for_cli` (`subscription_pool.py:236-249`) verandert niet — maar `has_available_spillover` keert `False` terug zodra de gekozen entry zelf gepauzeerd is. Daardoor parkeert de reactieve limiet-lus de kaart tot de proxy weer bereikbaar is, in plaats van door te schuiven naar diezelfde dode proxy. **Geen uitwijk** — de kaart wacht op reset.

De expliciete-pin-tak blijft fail-closed: `resolve_effective_provider_and_model` raadpleegt de pause-merge niet — die loopt via `MAX_DISPATCH_FAILURES` naar Impediment mét de echte fout. Dekking: `tests/test_dispatch_endpoint_reachability_pause.py` (vier unit-tests op de pause-merge zelf plus drie end-to-end-tests op de dispatch-flow).

---

## 0. De zes antwoorden in één tabel

| Vraag | Antwoord |
|---|---|
| **Q1** Beheert `cockpit.sh` de lifecycle? | **Ja — als derde `watch_service`, maar alleen wanneer een config-bestand aanwezig is.** Geen config = geen service = niets verandert voor wie de sidecar niet gebruikt. |
| **Q2** Fail-open of fail-closed? | **Fail-closed.** Geen impliciete terugval naar Anthropic bij de spawn. De *herstel*-tak zit vóór de spawn in de pool-router: een dode proxy pauzeert de provider en de spillover-gate (`has_available_spillover`) ziet dat er geen echte uitwijk meer is — kaart wacht op reset, geen stille substitutie. Niet in een `except`-blok. |
| **Q3** Health-check in `cockpit-doctor.sh`? | **Ja, conditioneel + advisory (`WARN`).** Doctor draait `check-litellm-hardening.sh` en telt `FAIL`-regels — hetzelfde hergebruikpatroon als checks 5/6/7. Slaat over wanneer geen sidecar geconfigureerd is. |
| **Q4** Welke dispatch-lanes mogen erdoor? | **Twee: expliciete pin (`column_overrides` / kaart-provider) en de *laatste* pool-entry (vangnet).** Verboden: `column.default_provider` en de globale active-subscription-override — die twee maken het verplicht. |
| **Q5** Keys uitgeven en opruimen? | **Eén master key in de project-scoped SecretStore; geen per-sessie virtuele keys.** Die vereisen een LiteLLM-database (gemeten), en wat ze zouden opleveren — attributie — heeft een goedkopere bron (response-headers). |
| **Q6** Versie pinnen? | **Ja, exact: `litellm==1.93.0` + `prisma`.** Niet uit algemene voorzichtigheid, maar omdat een upstream-hernoeming van een config-flag de hardening-check **stil hol** maakt. |

---

## 1. Wat er sinds het schrijven van deze kaart gemeten is

De kaart is geschreven op 2026-07-19 en herschreven van 9router naar LiteLLM op
2026-07-21. Ze eindigt met: *"Zonder die twee is dit een beslissing op aannames."*
Beide voorwaarden zijn inmiddels vervuld, en dat verandert het gewicht van de zes
vragen wezenlijk:

- **`333af652…`** leverde de generieke naad: een `anthropic-compatible` provider die
  data-driven is (`provider_env.py:240-261` — één tak die `base_url`/`model`/
  `auth_token` van de caller krijgt en niets zelf opzoekt), met endpoints als
  KanbanMeta-rijen (`endpoints.py`) en credential-resolutie via de project-scoped
  SecretStore (`endpoints.py:232`). **Voorwaarde 1 van de kaart is dus structureel
  gehaald**: LiteLLM is één configuratie-rij. Er staat nergens LiteLLM-vormige code
  in Cockpit, en elk antwoord hieronder is zo gekozen dat dat zo blijft.
- **`bbfcb365…`** leverde de meting
  ([`litellm-pilot-meting.md`](./litellm-pilot-meting.md)): de vertaling werkt
  (tool-use end-to-end), prompt-integriteit is byte-exact, failover houdt een
  sessie in leven, de vijf hardening-eigenschappen zijn groen tegen een echte
  proxy. **Maar `cache_read` is via de proxy structureel 0**, ~14× meer
  belastbare input per beurt dan een warme Anthropic-cache.

Die laatste bevinding is de belangrijkste input voor deze beslissing, en niet omdat
ze de sidecar afkeurt. Ze **herdefinieert waar hij thuishoort**: de sidecar is geen
goedkopere manier om Cockpit-sessies te draaien, hij is een manier om *andere
backends* te bereiken en om een stervende sessie in leven te houden. Een component
die per definitie een minderheid van het verkeer draagt, verdient geen plek op het
kritieke pad — en dat is precies wat voorwaarde 2 van de kaart al vroeg. De meting
maakt van die voorzichtigheid een onderbouwd feit in plaats van een houding.

> **Correctie op een anker in de kaarttekst.** De kaart verwijst naar
> `provider_env.py:114-141` voor de `anthropic-compatible`-tak. Die regels bevatten
> vandaag `_build_opencode_endpoint_env`; de bedoelde tak staat op
> `provider_env.py:240-261`. Het bestand is sinds het schrijven van de kaart
> gegroeid van ~260 naar 419 regels.

### 1.1 Twee economieën, niet één

De pilot rekent in "belastbare input-token-equivalenten" met Anthropic's
gepubliceerde **API**-tarieven (`cache_read` = 0,1× input). Voor Cockpit is dat niet
de enige relevante economie: dispatch draait vandaag op een **abonnement**, en
[`cache-read-quota-decision.md`](./cache-read-quota-decision.md) heeft gemeten dat
`cache_read` daar een effectief gewicht van **w ≈ 0,014** heeft — statistisch
ononderscheidbaar van nul.

Dat maakt het contrast in quotum-termen nóg scherper. Een warme beurt kost
bijna niets van het quotum; via de proxy is elke beurt volle verse input. Maar
het maakt de proxy tegelijk **relevanter voor precies één scenario**: het
quotum is op.

Op dat moment kost de directe route niet "meer quotum" maar **niets meer** —
de sessie sterft. Verkeer via de proxy raakt het Anthropic-quotum helemaal
niet; het kost geld op een ander account. De vergelijking is dan niet "14×
meer tokens" maar "geld op een goedkope upstream versus een dode sessie plus
een re-dispatch".

Dat is de enige verhouding waarin de sidecar onvoorwaardelijk wint, en het is
exact de vangnet-rol uit Q4.

---

## 2. Q1 — Lifecycle: `cockpit.sh` beheert hem, opt-in

**Beslissing.** `cockpit.sh` krijgt een derde gesuperviseerde service `litellm`, die
**alleen** start wanneer er een sidecar-config op de verwachte plek ligt. Zonder dat
bestand is er geen service, geen log-directory en geen gedragsverandering — een
ontwikkelaar die de sidecar niet gebruikt merkt er niets van.

**Waarom niet handwerk.** Een handmatig gestarte proxy sterft met de terminal die hem
startte. Dat is exact het faalpad dat de kaart als "het ergste" aanmerkt: stil dood,
kaarten falen zonder duidelijke oorzaak. Handwerk maakt van elke reboot,
SSH-disconnect en `Ctrl+C` een stille storing.

**Waarom niet onvoorwaardelijk.** Een service die altijd meestart maakt de sidecar de facto
verplicht en zet zijn credentials in de omgeving van elke gesuperviseerde child —
inclusief de backend. Cockpit's spawn-contract merget `os.environ` bewust **nooit**
in de spawn-env (`provider_env.py:316-319`, `:326-337`), dus dat lekt niet door naar
agent-sessies; maar het verbreedt wel het oppervlak zonder dat er iets tegenover
staat. Opt-in houdt voorwaarde 2 overeind.

**Waarom dit goedkoop is.** `watch_service` (`scripts/cockpit.sh:142-196`) is volledig
generiek: `watch_service <naam> <cmd> [health_url]`, met restart-backoff,
per-run-logbestanden, een `latest.log`-symlink en — belangrijk — een optionele
`health_watch`-watchdog die het proces kilt zodra het niet meer antwoordt. LiteLLM's
`/health/liveliness` past daar direct op. De service toevoegen is één bewaakte regel
naast `watch_service backend` / `watch_service frontend`
(`scripts/cockpit.sh:323-324`), geen nieuwe machinerie.

**Eén eigenschap om te kennen, niet te veranderen.** `watch_service` geeft het op na
vijf opeenvolgende snelle crashes (`max_fails=5`, `scripts/cockpit.sh:148`) en logt
dat alleen naar `supervisor.log`. Een structureel kapotte proxy herstart dus **niet**
eeuwig — hij verdwijnt geruisloos. Dat is correct gedrag voor een supervisor en
precies de reden dat Q3 (de doctor-check) geen luxe is maar het sluitstuk: de
supervisor stopt met proberen, doctor is wat dat zichtbaar maakt.

**Install en config horen bij deze service** (zie ook Q6): een eigen venv met een
gepinde install-set, en een config-bestand dat niet in de repo hoort omdat er een
`master_key` in leeft — een `*.example`-sjabloon wel.

---

## 3. Q2 — Faalgedrag: fail-closed, met de terugval één laag hoger

Dit is de vraag waar de kaart terecht op aandringt ("kies expliciet en onderbouw —
impliciet laten is de slechtste uitkomst"), dus eerst het antwoord, dan de
tegenwerping.

**Beslissing: fail-closed.** Wanneer de proxy niet bereikbaar is, valt een dispatch
die de sidecar heeft gekozen **niet** stilzwijgend terug op de directe
Anthropic-route. De kaart faalt, drie keer bounded, en landt daarna zichtbaar in
Impediment met de echte foutmelding.

### 3.1 Waarom fail-closed

1. **Fail-open inverteert de intentie, in beide inzet-modi.** De sidecar wordt om
   precies twee redenen gekozen: *prijs* (een goedkope upstream) of *bereik* (een
   model dat Anthropic niet levert). Bij (a) besteedt een stille terugval juist het
   abonnement dat de operator wilde sparen; bij (b) draait de kaart op een ander
   model dan gevraagd. In allebei de gevallen levert de sessie werk af onder een
   configuratie die niemand heeft gekozen, en markeert ze de kaart als Done.
2. **Een stille substitutie is niet auditeerbaar.** De attributie-kaart `390756e6…`
   heeft router-verkeer bewust `betrouwbaarheid="onbekend"` gegeven in plaats van een
   getal te verzinnen. Fail-open zou daar bovenop het *provider*-veld op de kaart
   laten liegen: er staat `anthropic-compatible`, er draaide Anthropic. Dat maakt
   iedere latere usage-analyse onbetrouwbaar op een manier die niet te detecteren is.
3. **Fail-closed is al geïmplementeerd, correct en luid.** Een spawn die synchroon
   faalt geeft de claim vrij, telt `dispatch_failures` op en zet de kaart terug in de
   bronkolom (`dispatch.py:5331-5360`). Na `MAX_DISPATCH_FAILURES = 3`
   (`dispatch.py:284`) gaat de kaart naar Impediment **met `str(exc)` in de comment**.
   Dat is geen bord dat stilvalt — dat is een bord met één zichtbare kaart en
   de werkelijke fout erbij. Er is geen reden deze provider een uitzondering
   te geven op het contract dat voor elke andere spawn-fout geldt.
4. **Fail-open zou de naad breken.** Een "provider X viel terug op Y"-tak in
   `dispatch.py` is per definitie LiteLLM-vormige logica in Cockpit — precies wat
   voorwaarde 1 verbiedt. Fail-closed kost nul regels code.

### 3.2 De eerlijke tegenwerping, en het antwoord

Voorwaarde 2 van de kaart zegt: *"Eén sidecar ertussen die vastloopt legt het
hele bord plat."* In de **vangnet**-modus lijkt fail-closed dat te veroorzaken.
Staat de proxy als pool-entry en is hij dood, dan verbranden kaarten die hem
trekken drie dispatch-pogingen en landen in Impediment — terwijl ze op
Anthropic gewoon hadden kunnen draaien.

Dat is een reëel bezwaar, en het antwoord is **niet** een fallback in de
error-handler. Het is dat de terugval op de verkeerde laag zou zitten.

De pool-router kiest de provider **vóór** de spawn en merget de
gepauzeerde-set in de drempel-scan
(`subscription_pool.py:236-249`: een gepauzeerde of boven-drempel-entry wordt
overgeslagen). De laatste entry is de val-terug. **Maar die val-terug wordt
ook teruggegeven wanneer die laatste entry zelf gepauzeerd is** — de functie
geeft deterministisch "als ik móét kiezen, dan deze" terug, zodat de caller
weet welk pad de spawn heeft gekozen.

De juiste ingreep is dus: **markeer een onbereikbare proxy als gepauzeerd vóór
de selectie**. Daarmee verandert de `chosen`-uitkomst in de vangnet-topologie
niet (de val-terug blijft de dode vangnet), **maar** `has_available_spillover`
(`subscription_pool.py:274-323`) ziet dat de gekozen entry zelf in de
paused-set zit en geeft `False` terug. De reactieve limiet-lus
(`move_limited_session_to_resume`) parkeert de kaart tot de proxy weer
bereikbaar is.

Dat is een *normale, gelogde pool-observatie*, geen stille substitutie; het
bord gaat niet plat, maar de kaart wacht. Op 2026-08-04 heeft de mens dit
gedrag ("vangnet dood = kaart wacht op reset") bevestigd als gekozen behavior
voor kaart `424c23d4…` — er is geen uitwijk naar een andere provider, de kaart
wacht tot de proxy weer bereikbaar is.

Het injectiepunt bestaat al en is één functie: `_paused_providers_for_pool`
(`dispatch.py:1201-1225`) wordt in `dispatch.py:1248-1275` al gemerged met de
handmatige pauze-lijst van de operator. Een derde bron — "endpoint antwoordt
niet" — hoort in diezelfde merge.

Zo verdeelt het faalgedrag zich precies zoals het hoort:

| Situatie | Gedrag |
|---|---|
| Kaart/kolom **pint** de sidecar expliciet, proxy dood | **Fail-closed.** 3× retry → Impediment met de fout. Er bestaat geen eerlijk substituut voor een expliciete keuze. |
| Sidecar is **vangnet** in de pool, proxy dood | **Geen uitwijk — kaart wacht op reset.** `pick_subscription_for_cli` blijft de dode vangnet teruggeven (de val-terug-tak verandert niet), maar `has_available_spillover` is `False` en de reactieve limiet-lus parkeert de kaart tot de proxy weer bereikbaar is. Herstel vóór de spawn, zichtbaar als pool-observatie. |
| Proxy leeft, upstream loopt tegen een limiet | **LiteLLM's eigen `fallbacks`** vangen het mid-sessie op (gemeten: pilot §5). Cockpit ziet niets en hoeft niets te doen. |

Dat is ook consistent met de conventie uit
[`recipe-writing-conventions.md`](./recipe-writing-conventions.md): auto-recovery
hoort in het uitvoeringspad, niet als proza ná een `exit 1`. Hier betekent dat
letterlijk: het herstel hoort in de selectie, niet in het `except`-blok.

---

## 4. Q3 — Health-check in `cockpit-doctor.sh`: ja, conditioneel en advisory

**Beslissing.** Doctor krijgt een achtste check die `scripts/check-litellm-hardening.sh`
in advisory-modus draait en zijn `FAIL`-regels telt. De check **slaat zichzelf over**
wanneer er geen sidecar-config aanwezig is.

Drie ontwerpkeuzes, elk met een reden:

- **Hergebruik in plaats van dupliceren.** Doctor's checks 5, 6 en 7
  (`cockpit-doctor.sh:76-106`) draaien alle drie een ánder script in dry-run en tellen
  regels. De hardening-check bestaat al, verifieert vijf eigenschappen tegen een
  draaiende proxy, en is met 33 asserts getest. Doctor zijn eigen `curl` laten doen
  zou een tweede, zwakkere waarheid introduceren.
- **`WARN`, geen `FAIL`.** Doctor reserveert exit-1 voor "actief kapotte repo-staat"
  (`cockpit-doctor.sh:113-114`). Een dode optionele sidecar is dat niet — hij is een
  waarschuwing, net als een verweesde worktree of een orphan bridge-sessie.
- **Conditioneel.** Een onvoorwaardelijke check zou op elke box zonder sidecar een
  permanente WARN geven. Dat is erger dan geen check: het traint mensen om
  doctor-output weg te kijken, en dan ziet niemand de WARN die er wél toe doet.

De kaart noemt de stille dode sidecar "het ergste faalpad". Deze check is het
tegengif, en hij sluit precies aan op de crash-loop-gap uit Q1: de supervisor
geeft het na vijf snelle crashes op en zegt dat alleen tegen `supervisor.log`.
Doctor is de plek waar dat als WARN aan de oppervlakte komt. `cockpit.sh
start` draait doctor al.

---

## 5. Q4 — Toegestane dispatch-lanes

**Toegestaan:**

1. **Expliciete pin** — `card.column_overrides[<agent>].provider = "anthropic-compatible"`
   met een `endpoint_name`, of een per-kaart provider-pin. Dit is de bereik-lane:
   een experiment op een ander model, een goedkope chore-lane. Volledig bewust, per
   kaart of per kolom.
2. **Pool-vangnet — uitsluitend als *laatste* entry.** `PROVIDER_COMPATIBLE` staat al
   in `_ALLOWED_POOL_PROVIDERS` (`subscription_pool.py:75-80`). De positie is hier het
   hele mechanisme: `pick_subscription_for_cli` geeft de **laatste** entry terug
   wanneer alles boven drempel of gepauzeerd is (`subscription_pool.py:236-249`). Als
   laatste entry wordt de sidecar dus alléén gekozen op het moment dat er niets anders
   meer is — en dat is exact het moment waarop een cache-loze sessie beter is dan geen
   sessie (§1.1).

**Niet toegestaan:**

3. **`column.default_provider`** van een dispatch-kolom, en
4. **de globale active-subscription-override.**

Allebei zijn bord-brede schakelaars: ze maken de sidecar verplicht voor álle dispatch
en zetten hem daarmee op het kritieke pad. Dat is letterlijk wat voorwaarde 2
verbiedt. Deze grens is **beleid, geen code-gate** — `set_active_subscription_override`
accepteert de provider vandaag gewoon (`dispatch.py:766`). Dat is bewust: een
code-gate zou een operator die precies weet wat hij doet blokkeren op een
configuratie-keuze. De grens staat hier opgeschreven zodat een afwijking een
gedocumenteerde keuze is en geen ongeluk.

**Wat een lane-keuze zou moeten sturen** (uit de meting, niet uit voorkeur): de
sidecar is per beurt duurder in tokens en levert geen prompt-cache. Lange,
tool-zware sessies (engineer-kaarten met veel `Read`/`Bash`) betalen die 14× het
hardst. Korte, enkelvoudige lanes betalen 'm nauwelijks. Wie een lane kiest om
kosten te besparen, moet dus rekenen met het **tarief**verschil van de upstream en
niet met tokenaantallen — de pilot is daar expliciet over.

---

## 6. Q5 — Keys: één master key, geen per-sessie uitgifte

Voorwaarde 3 van de kaart is scherp en klopt: loopback is op deze box geen
isolatiegrens. Elke gedispatchte sessie draait op dezelfde host en kan
`127.0.0.1:<poort>` bereiken.

**Beslissing.** Eén `master_key`, geleverd aan de proxy via zijn eigen env
(`os.environ/LITELLM_MASTER_KEY` in de config, nooit als literal — dat is een
van de vijf eigenschappen die de hardening-check afdwingt). Aan Cockpit wordt
de key geleverd via de **project-scoped SecretStore**, zoals
`resolve_compatible_endpoint` (`endpoints.py:232`) hem al ophaalt. **Geen
per-sessie of per-lane virtuele keys.**

**Waarom niet per sessie**, met de gemeten redenen:

1. **Ze vereisen een database.** De pilot heeft dit tegen een echte proxy vastgesteld:
   zonder DB geeft `GET /spend/logs` een `500 "Database not connected"`, en een
   *verkeerde* key valt door naar de virtual-key-lookup en geeft
   `400 "No connected db."`. Virtuele keys zijn een DB-feature. Een Postgres naast de
   proxy zetten om een key-per-sessie uit te geven is precies de infrastructuur die
   "één configuratie-rij" om zeep helpt.
2. **Wat ze zouden opleveren, heeft een goedkopere bron.** De hoofdreden voor
   per-sessie keys is attributie — en die is DB-vrij beschikbaar via de
   response-headers (`x-litellm-model-id`, `x-litellm-model-group`,
   `x-litellm-call-id`, `x-litellm-attempted-fallbacks`), gemeten in pilot §9. Dat is
   per-request granulariteit, fijner dan een key per sessie.
3. **De grens die de master key trekt, is de grens die er is.** De key komt als
   `ANTHROPIC_AUTH_TOKEN` in de env van de sessies die *via* de proxy draaien. Een
   sessie die niet door de proxy is gerouteerd bereikt hem wel (loopback), maar krijgt
   401. De master key scheidt dus **gerouteerd van niet-gerouteerd** — dat werkt. Wat
   hij niet doet is gerouteerde sessies onderling scheiden. Op een box waar alle
   sessies al hetzelfde bestandssysteem, dezelfde checkout en dezelfde host delen, is
   dat laatste geen echte grens maar de illusie ervan.

**Opruimen.** Er is niets uit te geven, dus niets te garbage-collecten — en dat is
het punt, niet een omissie. Rotatie is één handeling: nieuwe waarde in de env van de
proxy, dezelfde waarde in de SecretStore, `cockpit.sh restart`. Een
uitgifte-mechanisme dat je niet bouwt, is een opruim-mechanisme dat je niet fout kunt
krijgen.

**Heropen dit** zodra de sidecar verkeer draagt voor meer dan één project, of voor een
lane waarvan de credentials niet bereikbaar horen te zijn voor andere lanes. Dan zijn
virtuele keys + DB gerechtvaardigd, en is dat een eigen beslissing met een eigen prijs.

---

## 7. Q6 — Versie pinnen: ja, exact

**Beslissing.** `litellm==1.93.0` en `prisma`, exact gepind, in een eigen
requirements-bestand naast de sidecar-config — **niet** in `backend/requirements.txt`.
Cockpit's backend mag geen `litellm`-dependency krijgen; dat zou de naad die
`333af652…` opleverde meteen weer dichtsmeren.

Drie redenen, oplopend in gewicht:

1. **Elke meting in de pilot is versie-specifiek.** De auth-500-crash bij ontbrekende
   `prisma`, de prefix→endpoint-routeertabel (`openai/` → `/v1/responses`,
   `groq/` → `/v1/chat/completions`), de ~129-token bodem onder `count_tokens` — dat
   zijn allemaal eigenschappen van `1.93.0`. Een ongemerkte upgrade maakt het
   pilot-document ongeldig zonder dat iemand het merkt.
2. **LiteLLM ship't zeer frequent.** Auto-update op een pad waar autonome sessies
   overheen lopen is een gedragsverandering per dag.
3. **De doorslaggevende reden: een upgrade kan de hardening-check stil hol maken.**
   De check verifieert de *afwezigheid* van config-flags (`success_callback`,
   `failure_callback`, `service_callbacks`, `guardrails`, `router_settings.plugins`,
   `alerting`, `database_url`). Hernoemt upstream één van die sleutels, dan zoekt de
   check naar een flag die niet meer bestaat, vindt hem niet, en meldt **PASS** —
   terwijl de eigenschap die hij zou bewaken ongecontroleerd is. Dat is geen
   luidruchtige regressie maar een groen vinkje dat niets meer verifieert, en §11 van
   het analysedoc noemt dat expliciet erger dan geen check.

**Upgrade-procedure** (drie stappen, in deze volgorde): pin ophogen → `prisma`
meeleveren → `check-litellm-hardening.sh --strict` opnieuw draaien tegen de nieuwe
versie → bij twijfel over routering ook de prefix-probe uit pilot §6 herhalen. Een
upgrade zonder groene `--strict`-run is geen upgrade maar een onbekende toestand.

---

## 8. Wat deze beslissing níet oplost

Eerlijk, om te voorkomen dat iemand hier meer uit leest dan er staat:

- **De sidecar wordt hiermee niet goedkoper.** `cache_read` blijft 0. Deze beslissing
  gaat over *waar* hij mag draaien en *hoe* hij faalt, niet over of hij loont.
- **Er draait vandaag nog niets doorheen.** De naad staat (de
  `anthropic-compatible`-provider in `provider_env.py:240-261` + `endpoints.py`) en de
  pilot is gemeten, maar er is nog **geen lane bedraad**: geen pool-entry, geen
  `column_overrides`-pin, geen endpoint-catalogus. Zie de kaart-noot in §9 — dat werk
  staat op dit moment niet op het bord.
- **De lane-grens uit Q4 is beleid, geen gate.** Wie de sidecar als
  `column.default_provider` zet, kan dat. Er staat nu opgeschreven wat dat kost.
- **`count_tokens` blijft een plausibel fout getal** (pilot §8). Deze beslissing raakt
  dat niet; wie erop wil leunen moet het eerst repareren.

---

## 9. Vervolgkaarten

| Kaart | Wat | Hangt af van |
|---|---|---|
| **V1** `893033c6…` | `cockpit.sh`-service (opt-in, `health_url`) + gepinde install-set (`litellm==1.93.0` + `prisma`) + `cockpit-doctor.sh`-check | — |
| **V2** `424c23d4…` | Onbereikbare router-endpoint pauzeert de provider in de pool-selectie (`_paused_providers_for_pool`) | — (zie noot) |

De install-set-correctie uit Q6 (`prisma` verplicht) is bij **V1** ondergebracht:
die kaart is toch degene die de proxy installeert en start, dus de pin, de install-set
en de service horen in één hand.

> **Noot over de kaart-referenties (2026-07-27).** Tijdens deze sessie zijn drie
> kind-kaarten van `27cdc2bd…` van het bord verdwenen: de naad-kaart
> (`333af652…`, Done), de endpoint-catalogus (`8222fee8…`, Backlog) en de
> lane-bedradings-kaart (`66180bc9…`, Backlog). Samen met de pilot-kaart
> (`bbfcb365…`, Done) en de hardening-bugkaart (`1941bb10…`, Backlog) ging het
> bord van 77 naar 69 kaarten.
>
> Of dat opruimwerk of verlies is, is van buitenaf niet vast te stellen. Die
> ids zijn hierboven **niet** als afhankelijkheid gebruikt: een `depends_on`
> naar een niet-bestaande kaart houdt een kind stil uit dispatch.
>
> Het *werk* van `333af652…` en `bbfcb365…` is wel geland en blijft verifieerbaar
> in code en docs (`provider_env.py:240-261`, `endpoints.py`,
> `scripts/check-litellm-hardening.sh`,
> [`litellm-pilot-meting.md`](./litellm-pilot-meting.md)). Wat **niet** meer op
> het bord staat is de lane-bedrading en de endpoint-catalogus uit §11.3 van
> het analysedoc. V2 veronderstelt die en zegt dat expliciet in zijn eigen
> acceptatiecriteria.

---

## 10. Heropen-trigger

Heropen deze beslissing bij een van deze drie:

1. **LiteLLM krijgt DB-vrije virtuele keys** — dan vervalt de hoofdreden onder Q5 en
   worden per-lane keys goedkoop genoeg om te overwegen.
2. **De sidecar draagt verkeer voor meer dan één project** — dan is de gedeelde master
   key wél een echte grens die ontbreekt (Q5, laatste alinea).
3. **Een lane wil de sidecar als default** — dat is Q4's verbod, en het heroverwegen
   ervan vraagt een meting die laat zien dat de proxy net zo beschikbaar is als de
   directe route, niet een aanname daarover.

Wat expliciet **geen** trigger is: een goedkopere upstream. Q2 en Q4 gaan over
beschikbaarheid en auditeerbaarheid; die veranderen niet als de prijs daalt.

---

## 11. Meet-verantwoording

- **Gemeten (elders, hier geciteerd):** alles over LiteLLM's gedrag komt uit
  [`litellm-pilot-meting.md`](./litellm-pilot-meting.md) (2026-07-27, LiteLLM
  `1.93.0`): `cache_read = 0`, de prefix→endpoint-tabel, het DB-vrije
  virtuele-key-gedrag, de response-headers, de auth-500-crash. Het
  `cache_read`-quotumgewicht (w ≈ 0,014) komt uit
  [`cache-read-quota-decision.md`](./cache-read-quota-decision.md).
- **Geverifieerd in deze sessie (leespas, `file:line` in de tekst):** de
  `PROVIDER_COMPATIBLE`-tak, `build_spawn_env`'s os.environ-uitsluiting, het
  spawn-faalpad + `MAX_DISPATCH_FAILURES`, de `paused_providers`-merge, de
  last-entry-val-terug in de pool, `watch_service`'s signatuur/`health_url`/
  `max_fails`, en doctor's WARN-vs-FAIL-contract.
- **Niet gemeten, expliciet als aanname gelabeld:** dat een dode proxy in
  vangnet-modus daadwerkelijk drie dispatch-pogingen kost vóór Impediment is
  *afgeleid* uit het faalpad (`dispatch.py:5331-5360` + `MAX_DISPATCH_FAILURES = 3`),
  niet uitgelokt met een echte dode proxy. De vorm van het faalpad staat vast; het
  precieze aantal pogingen bij een sessie die pas ná de spawn sterft loopt via
  `_release_dead_claim` en kan afwijken.
- **Geen kosten-/besparingsclaim in dit document.** Er wordt nergens een percentage of
  bedrag beweerd; §1.1 vergelijkt twee gemeten grootheden en trekt daar een
  richtingsconclusie uit, geen getal.

---

## 12. Voetnoot — de §11.x-verwijzingen

Bij het uitvoeren van deze kaart bleek dat `9router-integratie-analyse.md` §11
**geen** subsecties had, terwijl er op drie plaatsen naar `§11.2`, `§11.3`,
`§11.5` en `§11.6` werd verwezen. Die plaatsen zijn: deze kaart zelf,
`litellm-pilot-meting.md` (§0 en §10), en §11 van het analysedoc.

De "herziening van 2026-07-21" bestond wél — als een set kind-kaarten
(`8222fee8…`, `66180bc9…`, `d0446fd8…` en de herschreven versie van deze
kaart) — maar is nooit als documenttekst geland.

Dat is dezelfde klasse fout als de `§12/V6`-verwijzing die de Herkomst-noot
van deze kaart al signaleerde: een citaat vooruitlopend op een sectie die
nooit geschreven is. Daarom zijn §11.1–§11.6 in dit ship materieel gemaakt in
het analysedoc. De inhoud is gereconstrueerd uit het enige echte record — de
kaarten — en met een expliciete
noot dat de subsecties later zijn opgeschreven dan de citaten die ernaar wijzen.
