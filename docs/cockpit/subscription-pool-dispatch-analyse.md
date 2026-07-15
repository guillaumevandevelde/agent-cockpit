# Analyse — subscription-pool × auto-dispatcher × kolom-toewijzing

**Datum:** 2026-07-15
**Status:** Analyse (read-only bevindingen op commit `766a020`) — implementatie in vervolgkaarten
**Trigger:** kanban-kaart `75d3366d…` "Analyse - kanban subscription pool". Gebruiker:
> "Ik zie niet in hoe dit werkt met de auto dispatcher en de kolom toewijzing?
> Alvast, dit is een te groot element om op het kanban board te staan, Maakt alles
> te onoverzichtelijk."

Verwant: [`subscription-flexibiliteit-analyse.md`](./subscription-flexibiliteit-analyse.md)
(het ontwerp dat de pool voorschreef — fase 0/1a/1b/2),
[`kanban-dispatch-spec.md`](./kanban-dispatch-spec.md) (dispatch + per-provider pause),
[`kanban-model-override.md`](./kanban-model-override.md) (model-precedentie),
[`subscriptions.md`](./subscriptions.md) (credential-pagina).

---

## 0. TL;DR — het antwoord op beide vragen

**Vraag 1 — "hoe werkt dit met de auto dispatcher en de kolom toewijzing?"**

De pool en de kolom-toewijzing zijn **twee orthogonale assen** die elkaar op precies
één punt raken:

- De **kolom** bepaalt *wie* het werk doet (persona/prompt: `analyst` vs `engineer`).
  De pool heeft daar **nul** invloed op.
- De **pool** bepaalt *waar het op draait* (`provider` + optioneel `model`). Die keuze
  wordt gemaakt **nadat** de kolom al vastligt.
- Het enige raakvlak: zodra een pool is ingesteld, **overschrijft de pool stilzwijgend
  `column.default_provider` van élke kolom** — de per-kolom provider-instelling wordt
  dan dode configuratie, zonder dat de kolom-editor dat ergens toont.

Dat de gebruiker het niet ziet werken, is dus terecht: **er is geen zichtbare koppeling,
en de onzichtbare koppeling die er wél is (pool overrulet kolom-defaults) is nergens in de
UI aangegeven.**

**Vraag 2 — "te groot element op het board"**

Terecht, en sterker dan de gebruiker vermoedt. De pool is de enige bord-brede
*configuratie* die een volledige Card boven het bord kreeg (alle zusjes —
ShipMode, Autodispatch, Transport, Active-subscription-override — zijn compacte
toolbar-knoppen). Bovendien: **het feature levert vandaag niet wat de grote UI belooft.**
Vier geverifieerde defects (§3) degraderen de pool tot "kies altijd entry #1" — functioneel
identiek aan de bestaande one-click `ActiveSubscriptionOverride`-knop, maar met een
4-velden-per-rij editor. **Grote UI, statisch gedrag.** De onoverzichtelijkheid is een
symptoom; de dode bedrading is de oorzaak.

---

## 1. Deel A — Hoe de pool op de auto-dispatcher aansluit (het ontwerp)

Alles gebeurt in `dispatch._run_card` (`backend/app/kanban/dispatch.py`). De relevante
volgorde per gedispatchte kaart:

| # | Stap | Code | Wat het bepaalt |
|---|---|---|---|
| 1 | CLI-id resolven | `dispatch.py:2186` | **welke CLI** spawnt (`claude-code`, `codex`, …) |
| 2 | Kolom/persona resolven | `dispatch.py:2216` (`_phase_target_agent`) | **welke kolom + prompt** (`analyst`/`engineer`) |
| 3 | Pool bevragen | `dispatch.py:2254-2259` (`_pick_pool_choice`) | **welk abonnement** (provider + model) |
| 4 | Provider kiezen | `dispatch.py:2282-2288` | de precedentieketen hieronder |
| 5 | Model kiezen | `dispatch.py:2298-2306` | idem, model-as |
| 6 | Spawnen | `dispatch.py:2328` | `card_transport(cli_id=…, provider=…, model=…)` |

### 1.1 De precedentieketen (provider)

```
global_override            (bord-brede pin, fase 0)      ← wint van alles
  > pool_choice.provider   (fase 1b, deze analyse)
  > card.column_overrides[target_agent].provider
  > column.default_provider
  > PROVIDER_ANTHROPIC     (dispatcher-default)
```

Model volgt exact dezelfde keten (`global_override.model > pool_choice.model >
column_override.model > column.default_model > persona-frontmatter`), met de
partiële-override-vorm: een `None` model in de pool-entry **valt door** naar de rest van
de keten in plaats van te pinnen.

### 1.2 Wanneer de pool actief is

```python
# dispatch.py:2256
if pool_entries is not None and not global_override:
    pool_choice = await _pick_pool_choice(...)
```

- **Geen pool** (`None`) → precies het gedrag van vóór fase 1b (backward-compat).
- **Pool + global override** → de override wint; de pool wordt **niet eens bevraagd**.
- **Pool zonder override** → de pool wint van kolom- én per-kaart-config.

> ⚠️ Die tweede regel is de eerste UX-val: de `ActiveSubscriptionOverride`-knop in de
> toolbar **schakelt de hele pool-card eronder stil**, zonder enige visuele indicatie in
> beide componenten. Twee concurrerende abonnement-knoppen, twee visuele registers,
> nul kruisverwijzing.

---

## 2. Deel B — Hoe de pool op de kolom-toewijzing aansluit: **niet**

Dit is de kern van de verwarring. "Kolom-toewijzing" betekent in deze codebase twee
losse dingen, en de pool raakt er maar één van — indirect.

### 2.1 Kolom → persona (de pool doet hier niets)

`_phase_target_agent` (`dispatch.py:2216`) kiest de doelkolom/persona uit: fase
(analyst/executor) → `agent_override` → `card.agent` → `work_type`-mapping → fallback
`engineer`. **De pool komt in deze functie niet voor.** De persona bepaalt de *prompt*,
niet het abonnement. Dat is een bewuste scheiding en ze klopt: *wie* het werk doet en
*op welk abonnement* het draait zijn onafhankelijke keuzes.

### 2.2 Kolom → provider (hier overrulet de pool, onzichtbaar)

`column.default_provider` is de per-kolom abonnement-keuze. Zodra een pool bestaat,
staat `pool_choice.provider` **erboven** in de keten (§1.1) → de kolom-instelling doet
niets meer. De kolom-editor toont dat nergens. Dit is precies wat
[`subscription-flexibiliteit-analyse.md`](./subscription-flexibiliteit-analyse.md) §5
bedoelde ("de gebruiker configureert dit één keer i.p.v. per kolom te morrelen"), maar
het ontwerp beschreef niet dat de nu-genegeerde kolom-knop zichtbaar moest blijven.
Resultaat: twee knoppen die hetzelfde beweren te doen, waarvan er één zwijgend verliest.

### 2.3 Kolom → CLI: al kapot vóór de pool

`column.default_agent` bereikt de dispatcher **helemaal nooit**:

- `get_column_default_agent` wordt alleen gelezen in `router.py:417` — bij een
  **handmatige** kaart-move, om `card.agent` in te vullen als die leeg is.
- Kolommen worden aangemaakt met `default_agent=<kolomnaam>` (`service.py:536`), dus
  `card.agent` wordt `"engineer"` / `"analyst"` — een **persona-naam, geen CLI-id**.
- `_phase_cli_id` filtert `card.agent` tegen `known_clis` (`dispatch.py:133`) en gooit
  `"engineer"` dus weg → fallback `"claude-code"`.

**Gevolg:** de CLI-as is bord-breed effectief vastgeklonken op `claude-code`, tenzij een
kaart expliciet `analyst_agent_id`/`executor_agent_id` zet. Dat is voorbestaande schuld,
maar het verklaart mede waarom "de kolom-toewijzing" en "het abonnement" voor de
gebruiker niet op elkaar lijken aan te sluiten.

---

## 3. Deel C — Wat er vandaag écht gebeurt: vier geverifieerde defects

De pool ís gebouwd (32 groene tests), maar de bedrading naar het usage-signaal is dood.
Alle vier bevindingen zijn read-only geverifieerd op `766a020`.

### D1 — `await` op een synchrone functie → snapshot-pad crasht altijd

```python
# dispatch.py:574
provider = await _registry.get_provider_for(cli=entry.cli, provider=entry.provider)
```

`registry.get_provider_for` is **`def`, niet `async def`** (`registry.py:45`). Elke
aanroep gooit `TypeError: object … can't be used in 'await' expression`. Dat wordt
opgevangen door de belt-and-braces `except Exception` in `_pick_pool_choice`
(`dispatch.py:617-628`) → `snapshots = {}` → "geen signaal" → **entry #1 wint altijd**.

> Al gekaart als `ea7e038b…` "[problem] subscription-pool: `await
> _registry.get_provider_for(...)` awaits a sync function → usage-snapshot path is dead
> code". Deze analyse bevestigt het en voegt de drie defects hieronder toe.

### D2 — De provider-registry wordt nooit gevuld

`_PROVIDERS: dict = {}` (`registry.py:30`) en **`register_provider` wordt nergens
aangeroepen** — niet in app-startup, niet in tests. `AnthropicUsageProvider` en
`MinimaxUsageProvider` bestaan als bestanden (`services/subscriptions/anthropic.py`,
`minimax.py`) maar zijn nooit geregistreerd.

**Dus zelfs met D1 gefixt** blijft `get_provider_for` altijd `None` teruggeven →
snapshots leeg → entry #1 wint altijd. De fase-1a-afhankelijkheid uit de flexibiliteit-
analyse (§5: "1b hangt af van 1a") is in de praktijk **niet ingelost**: 1b is gebouwd op
een 1a die alleen als klassen bestaat, niet als bedrading.

### D3 — `PoolEntry.cli` is dode configuratie

De pool-entry draagt een `cli`-veld; de UI toont er een select voor; validatie eist
niet-leeg (`subscription_pool.py:224`). Maar **`pool_choice.cli` wordt nergens
geconsumeerd** — `cli_id` komt uitsluitend uit `agent_override` /
`analyst_agent_id` / `executor_agent_id` / `card.agent` (`dispatch.py:2186`), vóór de
pool überhaupt bevraagd wordt. Het veld dient alleen als lookup-sleutel voor de
snapshot-map (`f"{entry.cli}:{entry.provider}"`) — die door D1+D2 leeg is.

Dit breekt expliciet met flexibiliteit-analyse §3, die vroeg om een abstractie over
**beide** assen (CLI-as + provider-as). Alleen de provider-as landt.

Mitigerend: `CLI_OPTIONS` in de UI bevat vandaag alleen `claude-code`
(`SubscriptionPool.tsx:25-27`), dus de gebruiker kan de dode as niet per ongeluk
kiezen. Het veld suggereert echter een capability die er niet is.

### D4 — De allow-list sluit precies de CLI's uit waarvoor §6.3 geschreven is

`_ALLOWED_POOL_PROVIDERS = (anthropic, bedrock, minimax)` (`subscription_pool.py:50`).
Een `codex`- of `copilot`-abonnement is dus **niet uitdrukbaar** in de pool (422 bij
opslaan). Tegelijk beschrijft `_is_above_threshold` zorgvuldig de "geen signaal →
behandel als beschikbaar"-tak (`subscription_pool.py:98-101`) die volgens
flexibiliteit-analyse §6.3 juist voor **Codex/Copilot** bedoeld was. Die tak is
onbereikbaar voor zijn doelgroep.

### D5 (test-gat) — waarom dit groen shipte

`test_subscription_pool_dispatch.py:84-105` patcht `pick_subscription` **op de
bronmodule**, met de docstring "zodat elke importeur (inclusief de dispatcher-binding)
de testversie ziet". Dat klopt niet: `dispatch.py:38-42` doet
`from app.kanban.subscription_pool import … pick_subscription`, wat de naam **bij import
in de dispatch-namespace bindt**. De monkeypatch raakt die binding niet → de
geïnjecteerde snapshots bereiken de dispatcher nooit.

De 11 dispatch-integratietests passeren daarom op het *degenererende* gedrag: er is
**geen enkele** integratietest "entry #1 boven drempel → spill naar entry #2". De enige
usage-aware dispatch-test die er is (`test_paused_provider_in_pool_falls_through`) leunt
op de per-provider pause — een pad dat wél echt async is en dus wél werkt.

### 3.1 Netto-effect

| Beloofd (UI + docstrings) | Werkelijk gedrag |
|---|---|
| Geordende pool, usage-aware | Statisch: **entry #1**, altijd |
| Per-entry drempel spilt door | Drempel wordt nooit geëvalueerd (snapshots leeg) |
| Spillover-bij-limiet (fase 2) | Alleen via de pause-tak; de drempel-tak is dood |
| Pool abstraheert CLI + provider | Alleen provider + model landen |

**De pool is vandaag functioneel gelijk aan `ActiveSubscriptionOverride`** (een
bord-brede pin), met meer UI en meer beloftes. Dat is de onderbouwing van de
gebruikersklacht: het element kost bord-ruimte voor gedrag dat een bestaande toolbar-knop
al levert.

---

## 4. Deel D — De UI-klacht: te groot voor het bord

Geverifieerd in `KanbanPage.tsx`:

```tsx
// KanbanPage.tsx:259-261
<div className="flex flex-col h-full gap-4 overflow-hidden">
  <DispatchPauseBanner />
  <SubscriptionPool projectKey={projectKey} />   {/* ← volledige Card, altijd zichtbaar */}
  ...
```

- **Onvoorwaardelijk gerenderd**, bóven de titel en de toolbar, binnen de
  `flex flex-col h-full`-container → elke pixel gaat rechtstreeks van de
  kolom-hoogte af.
- **Groeit met het aantal entries**: per subscription een bordered rij met 4
  form-velden (CLI / Provider / Model / Drempel) + reorder-knoppen
  (`SubscriptionPool.tsx:141-259`). Drie abonnementen ≈ het halve bord weg.
- **Zelfs leeg** rendert het een Card met header, beschrijving van 4 regels en een
  dashed empty-state (`SubscriptionPool.tsx:129-139`) — permanente kosten voor een
  feature die de meeste sessies niet aanraken.
- **Inconsistent met zijn zusjes**: `ShipModeToggle`, `SkipPermissionsToggle`,
  `AutodispatchToggle`, `DefaultTransportSelect` en `ActiveSubscriptionOverride` zijn
  allemaal compacte toolbar-controls (`KanbanPage.tsx:270-276`). De page heeft al een
  bewezen patroon voor zwaardere config: een **knop die een dialog opent** ("Columns",
  "Work Types" — `KanbanPage.tsx:277-286`).

**Aanbeveling: toolbar-knop → dialog**, volgens het bestaande Columns/Work-Types-patroon,
en niet naar `/subscriptions` verhuizen.

Afweging (aanname, gedocumenteerd i.p.v. stilzwijgend): `/subscriptions` is de canonieke
plek voor abonnement-*credentials*, maar die pagina is **globaal**, terwijl de pool
`project_key`-scoped is (`subscription_pool:<project_key>` in `KanbanMeta`). De pool
verhuizen zou daar een project-selector introduceren en de pool losweken van de
dispatch-config-cluster (ship mode, autodispatch, transport) waar hij conceptueel bij
hoort. De dialog op de Kanban-pagina houdt scope en plaatsing consistent. Een link
vanaf `/subscriptions` naar de board-pool blijft nuttig — maar dat is een pointer, geen
verhuizing.

Bij die verhuizing hoort ook het oplossen van de dubbele-knop-val uit §1.2: pool en
`ActiveSubscriptionOverride` horen in **één** dialog, met een expliciete regel die de
effectieve keten toont ("Override actief → pool staat uit").

---

## 5. Aanbeveling — volgorde

De gebruiker vraagt om begrijpelijkheid; de defects maken begrijpelijkheid onmogelijk
(de UI beschrijft gedrag dat de code niet uitvoert). Daarom: **eerst waarmaken of
terugtrekken, dan pas opruimen.**

| Volgorde | Wat | Waarom |
|---|---|---|
| **1** | **Bedrading repareren** (D1+D2+D5): `get_provider_for` niet awaiten, providers registreren bij startup, en een integratietest die de drempel-spill écht bewijst. | Zonder dit is de pool een dure alias voor de override-knop. D5 eerst schrijven — de test moet nú rood zijn. |
| **2** | **UI verhuizen naar dialog** + pool/override in één scherm met zichtbare precedentie. | Lost de letterlijke klacht op. Onafhankelijk van 1 — geen gedeeld contract. |
| **3** | **`PoolEntry.cli` eerlijk maken** (D3+D4): óf de CLI-as echt doorvoeren naar `cli_id` + de allow-list openen, óf het veld schrappen tot het werkt. | Kleinste stap; hangt af van 1 (de snapshot-sleutel is `cli:provider`). |

Kolom-defaults (`default_provider`) laten staan én zichtbaar maken dat ze genegeerd
worden zolang een pool bestaat — dat hoort in stap 2.

## 6. Vervolgkaarten (aangemaakt 2026-07-15 vanaf kaart `75d3366d…`)

| # | Kaart | Scope | Afhankelijkheid |
|---|---|---|---|
| 1 | `ea7e038b…` **[problem] usage-bedrading repareren** (D1+D2+D5) | `await` op sync `get_provider_for`, lege provider-registry, en de rode integratietest die beide bewijst | geen — begin hier |
| 2 | `5ec1c138…` **[feature] pool + active-override naar één toolbar-dialog** (§4) | plaatsing + zichtbare precedentie | onafhankelijk van #1 |
| 3 | `0b3ad6e2…` **[chore] `PoolEntry.cli` doorvoeren of schrappen** (D3+D4) | dode CLI-as + te enge allow-list | **hangt af van #1** (snapshot-sleutel is `cli:provider`) |

Kaart #1 bestond al als D1-kaart (`ea7e038b…`); daar is een comment met de D2/D5-context
op geplaatst i.p.v. een duplicaat aan te maken (dedup-pass volgens de `flag-problem`-
discipline). #2 en #3 zijn nieuw aangemaakt vanaf deze analyse.

De `depends_on` van #3 op #1 staat als tekst in de kaartbeschrijving: de MCP
`create_card` accepteert vandaag geen `depends_on` ondanks dat het onderliggende
`CardCreate`-schema het wél kent (bekende bug, kaart `1778ea36…`).

---

## 7. Uitvoering — D3+D4: `PoolEntry.cli` geschrapt (kaart `0b3ad6e2…`)

**Datum:** 2026-07-15
**Status:** Beslist + geïmplementeerd op branch `k-chore-poolent-aac6`.

### 7.1 De gekozen richting — schrappen, niet doorvoeren

Van de twee opties in de acceptatiecriteria is **(b) schrappen** gekozen:

- **`PoolEntry.cli` is weg** uit dataclass, schema, Pydantic, validatie,
  storage-serialisatie, UI.
- **De snapshot-lookup-sleutel** `f"{cli}:{provider}"` is gereduceerd tot
  `f"{POOL_CLI}:{provider}"`, met `POOL_CLI = "claude-code"` als
  module-constante.
- **Een migratie-shim** in `_deserialize_entries` accepteert nog steeds
  rijen die `cli` per entry meedragen en strip't het veld op read, zodat
  een opgeslagen rij uit een pre-fix build (of een POST van een stale
  UI-bundle) niet de dispatcher wurgt.

### 7.2 Waarom niet (a) doorvoeren

(a) had drie lastige randvoorwaarden, die stuk voor stuk buiten de
`chore`-scope vielen:

1. **`cli_id` doorvoeren in de precedentieketen** — `dispatch.py:2393-2399`
   resolved `cli_id` uit `agent_override` / `analyst_agent_id` /
   `executor_agent_id` / `card.agent` *vóór* de pool bevraagd wordt
   (`pool_entries` op regel 2464). Om `pool_choice.cli` ergens te laten
   landen zou een nieuwe cascade-regel nodig zijn ("pool_choice.cli mag
   het eerdere `cli_id` overriden"), met bijbehorende test voor het
   precedence-conflict tussen een expliciete `analyst_agent_id` en de
   pool-keuze.
2. **De allow-list openen voor codex/copilot** — `_ALLOWED_POOL_PROVIDERS`
   staat op `(anthropic, bedrock, minimax)`. Codex gebruikt vendor
   `codex`; copilot `copilot`. Beide bestaan nog niet als
   `SubscriptionUsageProvider`, dus stap 1 (D2) was eigenlijk pas
   afgerond voor de drie claude-vendoren — voor codex/copilot zou de
   hele `register_default_providers`-keten opnieuw moeten.
3. **Een integratietest die bewijst dat een pool-entry met een andere
   `cli` daadwerkelijk die CLI spawnt** — dat vereist een werkende
   `codex-cli` / `copilot-cli` `SpawnTransport`, en die zijn niet
   aanwezig in de werkende stack. De test zou een echte CLI moeten
   aanroepen of een dummy-transport moeten registreren — beide zijn
   een nieuwe testoppervlak.

Bovenop de technische last is de scope: dit is een `[chore]`, geen
`[feature]`. Het kaartdoel was "promised-bleeding UI wegwerken", niet
"de pool een tweede as laten routeren". (a) hoort dus bij een
toekomstige `[feature] PoolEntry.cli: doorvoeren naar cli_id + transport
voor codex/copilot` — out of scope voor deze kaart.

### 7.3 Waarom (b) veilig is

- **De CLI is vandaag al effectief vastgeklonken op `claude-code`**:
  `column.default_agent` bereikt de dispatcher nooit
  (`_phase_cli_id` filtert `card.agent` tegen `known_clis`; "engineer"
  wordt dus weggegooid en de fallback `"claude-code"` wint — analyse §2.3).
- **De registry seedt uitsluitend `claude-code:{provider}` stubs**
  (`registry.py:79-83`): het verwijderen van `cli` als entry-veld
  verandert geen feitelijke runtime-gedrag, want de lookup-key was
  altijd al `claude-code:anthropic` / `claude-code:bedrock` /
  `claude-code:minimax`.
- **De UI beloofde al niets anders**: `CLI_OPTIONS` stond op één entry
  (`SubscriptionPoolDialog.tsx` voor deze fix), met expliciete
  docstring-verwijzing naar deze kaart. De gebruiker kon dus nooit een
  niet-claude-code-CLI kiezen — de dode as was niet eens bereikbaar.

### 7.4 Migratiecontract

Twee tests bewaken de overgang:

- `test_deserialize_tolerates_legacy_cli_field` (storage-laag): een
  KanbanMeta-rij met `cli` per entry deserialiseert schoon; de
  `PoolEntry` heeft geen `cli`-veld maar is verder intact
  (`provider`, `model`, `drempel`).
- `test_post_subscription_pool_strips_legacy_cli_field` (API-laag): een
  `POST /api/v1/kanban/subscription-pool` met een body waar elke entry
  nog `cli` bevat, wordt geaccepteerd (200) en de `GET` direct
  erachter toont een rij *zonder* `cli`. Een stale UI-bundel hoeft
  dus niet eerst een hard-refresh te krijgen voor de gebruiker kan
  opslaan.

### 7.5 Netto-effect op de tabel uit §3.1

| Beloofd (UI + docstrings) | Werkelijk gedrag — pre-fix | Werkelijk gedrag — post-fix |
|---|---|---|
| Geordende pool, usage-aware | Statisch: entry #1, altijd | Idem (D1+D2 loste de drempel-tak al op) |
| Per-entry drempel spilt door | Drempel wordt nooit geëvalueerd | Idem |
| Spillover-bij-limiet (fase 2) | Alleen via de pause-tak | Idem |
| Pool abstraheert CLI + provider | Alleen provider + model landen | Alleen provider + model landen — **CLI-as is geschrapt, niet verborgen** |

De UI belooft niets meer dat de code niet doet (`SubscriptionPoolDialog`
toont geen CLI-select meer). Als in een latere feature-kaart de
CLI-as alsnog doorgevoerd wordt, kan `PoolEntry.cli` als
terugkerend veld worden geïntroduceerd met de huidige
`POOL_CLI`-constante als aanbevolen default.

