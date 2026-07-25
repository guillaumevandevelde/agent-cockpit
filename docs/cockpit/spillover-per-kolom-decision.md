---
title: "Beslissing — spillover zonder de per-persona provider-verdeling op te offeren"
type: decision
status: decided
---

# Beslissing — spillover zonder de per-persona provider-verdeling op te offeren

**Datum:** 2026-07-23
**Status:** besloten
**Kaart:** `2688bf8087e64213ac0dff1af509abad`
**Uitkomst:** **GO op vorm B, uitgebreid tot per-kolom staarten — de pool wordt een spillover-*keten* in plaats van een routing-*pin*, met `column.default_provider` als impliciete eerste entry.**

✅ **Geïmplementeerd** in 2026-07-25 (kaart `0172e94d…`):
- `resolve_effective_provider_and_model` bouwt nu `[column.default_provider(K)] ++ [pool-entries minus K's default]`
  via de nieuwe helper `_build_spillover_candidates` en geeft die aan `pick_pool`.
- `cli_id` is doorgegeven in de helper, de resolver, de column-settings-wrapper en beide
  call sites (`_dispatch_pool_picker` + `_gate_pool_picker`) zodat de synthetische kop
  niet door de cli-filter van de router wordt weggefilterd.
- `provider_source` is eerlijk: `column_default` wanneer de kop wint, `pool` alleen
  bij een echte uitwijk.
- 8 nieuwe tests in `test_subscription_pool_dispatch.py` (waaronder de AC-scenario
  uit kaart `0172e94d…`); 6 bestaande pin-tests bijgewerkt naar de nieuwe semantiek.
- UI-tekst in `SubscriptionPoolDialog.tsx` en deze doc bijgewerkt; per-kolom-staart
  (kaart `b36ca702…`) en UI-spillover-zichtbaarheid (`7411d25e…`) blijven open.

**Trigger:** kanban-kaart `2688bf80…` "[analyse] Spillover vs. per-persona provider — de pool
overrulet stilzwijgend elke kolom-default", kind van
[`sessie-limiet-auto-dispatch-analyse.md`](./sessie-limiet-auto-dispatch-analyse.md) §4.

Verwant: [`subscription-pool-dispatch-analyse.md`](./subscription-pool-dispatch-analyse.md)
(de analyse die de override-claim voor het eerst opschreef),
[`subscription-flexibiliteit-analyse.md`](./subscription-flexibiliteit-analyse.md) (het
ontwerp dat de pool voorschreef — fase 0/1a/1b/2),
[`kanban-model-override.md`](./kanban-model-override.md) (model-precedentie).

---

## 0. TL;DR

De pool is vandaag een **routing-pin**: eenmaal geconfigureerd bepaalt hij de provider van
élke kolom, ongeacht welke persona er draait. Dat is exact de rol die de
`ActiveSubscriptionOverride` al vervult — en die de pool bovendien **uitschakelt** zodra
hij aan staat. Twee knoppen, één taak; en de duurste van de twee (de pool, met zijn
drempels en volgorde) is precies degene die je niet aan kunt zetten zonder de bewuste
per-persona-verdeling te slopen.

De beslissing draait die rol om. De pool wordt een **geordende uitwijk-keten**, geen pin:

```
effectieve keten voor kolom K = [ column.default_provider(K) ]  ++  [ pool-entries, minus K's default ]
                                 ^ impliciete kop                  ^ de staart die de operator instelt
```

`analyst` blijft dus op Anthropic/opus starten, `engineer` op MiniMax — precies zoals nu —
en pas wanneer die kop gepauzeerd of boven drempel is, schuift de router door naar de
staart. `global_override` blijft er onverkort bovenop staan.

Twee gevolgen die de kaart niet vroeg maar wel bijten zodra je die keten aanzet, zijn als
aparte kaarten gefiled: het model-alias `opus` van de `analyst`/`reviewer`-kolom **lekt**
vandaag mee naar een MiniMax-spawn (§3.1), en het reactieve limiet-pad resolvet de
"gelimiteerde provider" via een pool-blinde helper (§3.2). Beide zijn al vandaag
bereikbaar via de `global_override`-knop — het zijn geen nieuwe risico's van deze
beslissing, wel blokkades voor de waarde ervan.

**Nuloptie afgewezen**, maar niet zonder kosten: spillover ruilt "5,7 h wachten" in voor
"nu verder op een zwakker model". Daarom is de staart **opt-in en per kolom** — een lege
staart op `reviewer` betekent "wacht liever op de reset dan mijn review te downgraden", en
dat is de operator's beslissing, niet die van de dispatcher.

---

## 1. De override-claim: **bevestigd**, met drie nuances

De kaart vraagt om bevestiging of weerlegging op de **huidige** code, niet op het oudere
analysedoc. Geverifieerd op `master` @ `9f91211`.

### 1.1 De keten zelf

`resolve_effective_provider_and_model` (`backend/app/kanban/dispatch.py:1180`) is sinds
kaart `8da646d8…` de enige plek waar de provider-precedentie leeft; zowel `_run_card`
(`dispatch.py:3505`) als de kolom-instellingen-UI (`resolve_column_effective_model`,
`dispatch.py:1305`) delegeren ernaartoe. De kern:

```python
# dispatch.py:1233-1244
pool_entries = await get_subscription_pool(session, project_key)
pool_choice: PoolEntry | None = None
if pool_entries is not None and not global_override and pick_pool is not None:
    pool_choice = await pick_pool(pool_entries)
column_default_provider = await get_column_default_provider(session, project_key, target_agent)
provider = (
    (global_override or {}).get("provider")
    or (pool_choice.provider if pool_choice else None)   # ← 1240
    or override_provider
    or column_default_provider                            # ← 1242
    or PROVIDER_ANTHROPIC
)
```

`target_agent` (de kolom) wordt **uitsluitend** gebruikt voor de kolom-lookups op regel
1237; de pool-lookup op 1233 is project-breed en kent de kolom niet. Regel 1240 staat
boven regel 1242 in dezelfde `or`-keten. **De claim klopt: één geconfigureerde pool
overschrijft `column.default_provider` van élke kolom.**

De *enige* bestaande ontsnapping is niet per kolom maar per CLI:
`pick_subscription_for_cli` geeft `None` terug wanneer geen enkele entry op de
gedispatchte `cli_id` matcht (`subscription_pool.py:229-231`), en dán valt de keten wél
terug op de kolom-default. Een pool met alleen `claude-code`-entries — wat de UI als enige
kan produceren, want het `cli`-veld is uit het frontend-formulier verwijderd
(`SubscriptionPoolDialog.tsx:31-36`) — vangt dus élke kolom af.

### 1.2 Nuance 1 — alleen de *provider* wordt geforceerd, het model niet altijd

Het model loopt via een aparte keten (`dispatch.py:1247-1253` →
`_effective_model`, `dispatch.py:1116-1137`). Een `PoolEntry` met `model=None` laat
`column_default_model` staan. Een pool `[{anthropic, model:null}, {minimax, model:null}]`
laat de `analyst`-kolom dus nog steeds `opus` gebruiken — maar wél tegen de provider die
de pool koos. Dat is geen verzachting maar een verzwaring: zie §3.1.

### 1.3 Nuance 2 — de pool is vandaag onbereikbaar zodra de override aan staat

`dispatch.py:1235`: `pool_choice` wordt alleen berekend `if ... not global_override`. De
UI maakt dat expliciet (`SubscriptionPoolDialog.tsx:376-386`, "Override actief → pool staat
uit"). Dat is de kern van het "twee knoppen, één taak"-argument in §4: de pool kán
vandaag niets wat de override niet al doet, behalve automatisch doorschuiven — en dat
laatste is precies wat nooit gebeurt (§1.4).

### 1.4 Nuance 3 — er is empirisch geen enkele pool, dus er is geen migratie-risico

Gemeten op de live board-DB (2026-07-23, reproductie in §8):

```
--- alle kanban_meta-sleutels ---   → geen enkele `subscription_pool:`- of
                                       `subscription_override:`-rij
--- kanban_columns ---
analyst   → anthropic / opus  (max_sessions 1)
engineer  → minimax  / —      (max_sessions 2)
reviewer  → anthropic / opus  (max_sessions 2)
```

Dit bevestigt §4 van de bron-analyse op de dag van deze beslissing: `get_subscription_pool()`
geeft `None`, dus `has_available_spillover()` geeft `False`, dus **elke** limiet betekent
per definitie "wachten". Belangrijker voor deze beslissing: er is **nul** bestaande
pool-configuratie in productie, dus de semantiek van de pool herdefiniëren breekt geen
bestaande opstelling. Dat is de goedkoopste moment dat dit ooit nog wordt.

---

## 2. Wat de gebruiker vandaag in de UI ziet

Kort antwoord: **nergens staat dat spillover uit staat, en nergens staat dat een pool de
kolom-defaults overneemt behalve in één regel precedentie-jargon.**

| Oppervlak | Wat het toont | Wat ontbreekt |
|---|---|---|
| Toolbar-knop (`SubscriptionToolbarButton.tsx:60`) | `"Subscriptions"` — zonder pool geen enkel signaal; tooltip zegt `"Subscriptions (column defaults)"` | dat een limiet daardoor altijd "wachten tot reset" betekent |
| Pool-dialoog, kop (`SubscriptionPoolDialog.tsx:360`) | `"Unset — column defaults apply"` | idem |
| Pool-dialoog, lege staat (`:400`) | `"No subscription pool configured — dispatch follows per-column defaults."` | idem — feitelijk juist, maar leest als "alles is prima ingesteld" |
| Pool-dialoog, beschrijving (`:263-272`) | de precedentieregel `global override > pool > per-card column_overrides > column defaults` | dat de pool **geen** kolom-notie heeft; "pool > column defaults" leest als "kolom-defaults gelden nog voor kolommen zonder pool-entry", wat onjuist is |
| Kolom-instellingen | `GET /columns/{id}/effective-model` (`router.py:324`) rendert `provider_source` — dit toont dus wél "pool wint" per kolom | alleen zichtbaar nadat je een specifieke kolom opent; geen bord-breed signaal |
| Kaart-badge (`CardItem.tsx:275-282`) | 🌐 `dispatch_provider` — waar de kaart daadwerkelijk op draaide | achteraf, per kaart |
| Activity-comment `🔀 … spilling over` (`dispatch.py:4004-4009`) | het enige oppervlak dat spillover ooit zou tonen | is nooit gerenderd (0 events in 8 dagen) |

De term "spillover" komt **nul keer** voor in `frontend/src/` (geverifieerd met
`grep -rn "spillover" frontend/src`). Een gebruiker die zich afvraagt waarom een
gelimiteerde kaart 5,7 uur stilstaat heeft geen enkel oppervlak dat hem naar de
pool-dialoog wijst.

---

## 3. Twee defects die pas bijten zodra je de pool aanzet

Beide zijn **vandaag al bereikbaar** met één klik op de `global_override`-knop; ze zijn
dus geen gevolg van deze beslissing, maar ze maken de opbrengst ervan negatief zolang ze
er staan.

### 3.1 Het model-alias van de kolom lekt mee naar de andere vendor

`_effective_model` (`dispatch.py:1116-1137`) gate't de **persona**-fallback op Anthropic —
expliciet, met een goede docstring-rationale — maar niet `column_default_model`:

```python
# dispatch.py:1136-1137
persona_fallback = persona_model if provider in (None, PROVIDER_ANTHROPIC) else None
return override_model or card_model or column_default_model or persona_fallback or None
```

De redenering in de docstring is dat expliciete kolom-defaults "legitiem een
provider-native model mogen noemen". Op dit bord is dat niet zo: `analyst.default_model` en
`reviewer.default_model` staan allebei op `opus`, een Anthropic-abonnements-alias. Zodra de
provider door pool of override op `minimax` uitkomt, gaat `opus` mee als `--model opus`
(`claude_code.py:79-81`) én als `ANTHROPIC_MODEL=opus` (`provider_env.py:106`, waar
`_clean(model) or MINIMAX_DEFAULT_MODEL` de MiniMax-default juist wegdrukt). Dat is
precies de faalmodus die de persona-gate wél afvangt.

Reproduceerbaar zonder pool: zet de board-brede override op MiniMax (de UI stuurt
`{provider, model: null}`, `SubscriptionPoolDialog.tsx:194`) en dispatch een
`analyst`-kaart.

### 3.2 Het reactieve limiet-pad resolvet de provider pool-blind

`move_limited_session_to_resume` bepaalt de zojuist-gelimiteerde provider via
`_provider_for_card` (`dispatch.py:3982` → definitie `dispatch.py:3787-3809`). Die helper
loopt alleen `column_overrides → column default → PROVIDER_ANTHROPIC` — geen
`global_override`, geen pool. Er bestaat al een volledige variant
(`_effective_provider_for_pause_gate`, `dispatch.py:3813`) die precies daarom is
geschreven, en die op vier andere plekken al wordt gebruikt (`:4619`, `:5064`, `:5142`,
`:5517`). Regel 3982 (en `:3132`, `:3908`) zijn niet meegegaan.

Gevolg met een pool aan: de kaart die op MiniMax vastliep pauzeert `anthropic`, en de
spillover-check krijgt de verkeerde `limited_provider` mee — de router mag dan uitwijken
naar de provider die *net* de limiet raakte. De schoonste bron is trouwens geen
her-resolutie maar `card.dispatch_provider` (`models.py:110`, geschreven op
`dispatch.py:3678` met de daadwerkelijk gekozen provider, en al zichtbaar als 🌐-badge).

✅ Geïmplementeerd (kaart 9ff86416…): nieuwe helper
`_limited_provider_for_card` in `dispatch.py` prefereert `card.dispatch_provider`
en valt terug op de volledige precedence-keten via
`_effective_provider_for_pause_gate` wanneer die `None` is (legacy/nooit-gedispatchte
rijen). Drie call-sites zijn gemigreerd: `move_limited_session_to_resume`,
`_provider_for_cwd` (Notification-hook pad), en `_cleanup_stuck_session`
(reaper-pad; kreeg een optionele `project_path`-parameter om de keten te kunnen
lopen). De smalle `_provider_for_card` blijft bestaan als laatste-resort
fallback voor callers zonder `project_path`/`dispatch_provider`-context, en zijn
docstring waarschuwt nu expliciet dat hij pool-/override-blind is.

---

## 4. De opties, gewogen op producteffect

### Optie A — pool die *per kolom* kan gelden

`subscription_pool:<project_key>:<kolom>` met de bord-brede sleutel als fallback. De pure
router en de opslag-validatie blijven ongemoeid; alleen de sleutel-resolutie en een
kolom-kiezer in de dialoog komen erbij.

- **Product-effect:** volledige controle. `reviewer` = `[anthropic]` (nooit uitwijken),
  `engineer` = `[minimax, anthropic]`.
- **Kosten:** de operator moet de kolom-default *dupliceren* als eerste pool-entry.
  Verandert hij de kolom-default en vergeet hij de pool, dan wint de pool stil — exact de
  onzichtbare koppeling die deze hele analyse-lijn heeft veroorzaakt. En de pool-dialoog
  verdrievoudigt in omvang, wat de klacht is waarmee
  [`subscription-pool-dispatch-analyse.md`](./subscription-pool-dispatch-analyse.md)
  begon ("te groot element … maakt alles onoverzichtelijk").

### Optie B — kolom-default als impliciete eerste pool-entry

De pool wordt de **staart**, niet de lijst. De effectieve keten voor kolom K is
`[K's default] ++ [pool minus K's default]`; de router die daarover loopt is de bestaande
`pick_subscription_for_cli` (ongewijzigd — hij krijgt gewoon een andere lijst).

- **Product-effect:** de per-persona-verdeling blijft *by construction* staan; er valt
  niets meer te dupliceren en dus niets meer uit sync te lopen. `provider_source` wordt
  eerlijk: `column_default` wanneer de kop wint, `pool` alleen bij een echte uitwijk.
- **Kosten:** je verliest "pin alles op entry #1 via de pool-volgorde". Dat is geen echt
  verlies — `global_override` doet dat, wint er sowieso van, en zet de pool zelfs
  helemaal uit (§1.3).
- **Restgat:** één bord-brede staart voor alle kolommen. Een `reviewer` die je *niet* naar
  MiniMax wilt laten zakken kun je niet uitzonderen.

### Optie B+ (de gekozen vorm) — B, daarna per-kolom staarten

Eerst de semantiek omdraaien (B), dan de staart per kolom laten instellen met de
bord-brede staart als fallback (het goedkope deel van A). Dat is A's controle **zonder**
A's duplicatie: de kolom-default blijft de enige plek waar "waar start deze persona"
staat, en de per-kolom staart zegt alleen "en waar mag hij heen als dat niet kan".

### Nuloptie — spillover blijft uit

- **Product-effect:** niets verandert; 65 limiet-events in 8 dagen blijven volledig op de
  reset-klok staan (mediaan 5,7 h, p75 11 h — gemeten in
  [`sessie-limiet-auto-dispatch-analyse.md`](./sessie-limiet-auto-dispatch-analyse.md) §1).
- **Argument vóór:** de zusterkaarten (`c8ad1ea8…` detectie, `f0953a11…` voortgangs-liveness,
  `e2116332…` in-pane hervatten) halen het *onnodige* deel van dat wachten er al uit — na
  die kaarten wacht een kaart de echte reset af en hervat vanzelf, in plaats van tot een
  mens ernaar kijkt. De marginale winst van spillover is dus kleiner dan het ruwe
  stilstandsgetal suggereert.
- **Waarom toch afgewezen:** die resterende wachttijd is nog steeds de volle resetduur
  (~5 h Anthropic), terwijl de andere vendor ongebruikt naast de deur staat — dit bord
  gebruikt beide abonnementen al, alleen strikt gescheiden. En de dode pool blijft dan
  permanent dode code die elke volgende analyse opnieuw moet uitzoeken.

---

## 5. De beslissing

**Vorm B+.** Concreet, in twee stappen (kaarten in §7):

1. De pool wordt een spillover-keten met `column.default_provider` als impliciete kop.
   Kolom zonder default → de pool-kop wint (het gedrag van vandaag). Geen pool → de
   kolom-default wint (het gedrag van vandaag). `global_override` blijft er bovenop.
2. De staart wordt per kolom instelbaar, met de bord-brede staart als fallback en een
   lege staart als geldige waarde ("nooit uitwijken").

Drie argumenten, in volgorde van gewicht:

- **De pool-als-pin is redundant.** `global_override` is dezelfde functie met minder
  ceremonie, wint ervan, en schakelt de pool uit terwijl hij aan staat (`dispatch.py:1235`).
  Een tweede pin-knop met een 4-velden-per-rij editor levert niets extra's op.
- **Nul migratie-risico, en dat vervalt.** Er is vandaag geen enkele pool-rij (§1.4). De
  semantiek herdefiniëren kost nu niets; zodra iemand een pool aanzet — precies wat deze
  analyse-lijn wil bereiken — is het een breaking change.
- **De duplicatie in optie A is de bug die we aan het oplossen zijn.** "Zet je
  kolom-provider ook nog eens in de pool" is een nieuwe stille koppeling tussen twee
  configuratieplekken, in een analyse die begon met een stille koppeling tussen twee
  configuratieplekken.

---

## 6. De kost die de kaart niet noemt: spillover is een kwaliteitsafweging

De per-persona-verdeling op dit bord is geen toevalligheid maar een keuze: `engineer` op
MiniMax (volume), `analyst`/`reviewer` op Anthropic/opus (oordeelskwaliteit). Een
`reviewer` die uitwijkt naar MiniMax-M3 draait dus niet "hetzelfde werk elders" maar
**een zwakkere review**. De uitruil is niet "wachten vs. doorwerken" maar "wachten vs.
doorwerken op een lager niveau".

Dit is een **ongemeten inschatting** — er is geen kwaliteitsmeting per provider per
persona in deze codebase, en die opzetten valt buiten deze kaart. Het is wel de reden dat
de staart **opt-in en leeg-by-default** is, en dat stap 2 (per-kolom staart) geen
optionele franje is maar het punt waarop de operator die afweging per persona kan maken.
Een bord-brede staart alleen zou de afweging voor hem maken.

---

## 7. Vervolgkaarten

Aangemaakt als kind-kaarten van `2688bf80…`:

| Kaart | Wat | Hangt af van |
|---|---|---|
| `0172e94d…` | Pool wordt spillover-keten met de kolom-default als impliciete kop (§5 stap 1) | — |
| `98064955…` | Kolom-model-alias mag niet meeliften naar een andere vendor (§3.1) | — |
| `9ff86416…` | Reactief limiet-pad leest de echt gespawnde provider (§3.2) | — |
| `b36ca702…` | Per-kolom spillover-staart (§5 stap 2) | `0172e94d…` |
| `7411d25e…` | UI toont of spillover aan staat en wat de keten per kolom doet (§2) | `0172e94d…` |

De twee defect-kaarten (§3.1, §3.2) hebben **geen** `depends_on`: het zijn zelfstandige
bugs die vandaag al via `global_override` bereikbaar zijn en los van deze beslissing
gefixt horen te worden. De UI- en per-kolom-kaart hangen wél echt af van kaart 1 — hun
contract is de keten-semantiek die kaart 1 vastlegt.

---

## 8. Bewust niet in scope

- **Drempels op de impliciete kop.** Een kolom-default heeft geen `drempel`. Regel: staat
  dezelfde provider óók in de staart, dan erft de kop diens drempel; anders `1.0`
  ("gebruik tot de pause hem raakt") — dat is exact het gedrag van vandaag. Geen nieuw
  configuratieveld.
- **Per-kolom `global_override`.** De board-brede pin blijft board-breed; hij is de
  noodknop, niet de routeringslaag.
- **Kwaliteitsmeting per provider per persona.** Zou §6 van een inschatting naar een
  meting tillen, maar is een eigen meet-harnas-spoor (vgl.
  [`token-saver-meet-harnas.md`](./token-saver-meet-harnas.md)), geen onderdeel van deze
  beslissing.
- **Het `cli`-veld terugbrengen in de pool-UI.** De per-CLI-as bestaat in het datamodel
  (`PoolEntry.cli`) maar niet in het formulier; zolang er één CLI dispatcht is dat geen
  gat.

---

## 9. Reproductie

```bash
# Geen pool, geen override — en de kolom-defaults die de per-persona-verdeling dragen:
python3 -c "
import sqlite3
c = sqlite3.connect('/home/vdvgu/.claude-registry/kanban.db')
print([k for k, _ in c.execute('select key, value from kanban_meta')])
for r in c.execute('select name, default_provider, default_model from kanban_columns order by rank'):
    print(r)
"

# De precedentieketen zelf (pool boven kolom-default, kolom speelt geen rol in de pool-lookup):
sed -n '1233,1244p' backend/app/kanban/dispatch.py

# Het model-lek (§3.1) — alleen de persona-fallback is provider-gated:
sed -n '1136,1137p' backend/app/kanban/dispatch.py

# Het pool-blinde limiet-pad (§3.2):
grep -n '_provider_for_card\|_effective_provider_for_pause_gate' backend/app/kanban/dispatch.py

# Spillover komt nergens voor in de UI:
grep -rn 'spillover' frontend/src ; echo "exit=$?  (1 = geen enkele match)"
```
