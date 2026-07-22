---
title: "Analyse — is auto-dispatch vastgeklonken aan het Anthropic-abonnement?"
type: analysis
status: active
---

# Analyse — is auto-dispatch vastgeklonken aan het Anthropic-abonnement?

**Datum:** 2026-07-21
**Kaart:** `03734e9037a4455883550a70516ec9d7` "Analyse - Dispatch"
**Scope:** read-only bevindingen op de werkboom van `master` (branch `k-analyse-dispa-7d38`)

**Trigger (gebruiker):**

> "Is de dispatch functionaliteit vandaag hard gekoppeld aan de anthropic subscriptie?
> Indien zo, dan is dat geheel niet wenselijk. Bekijk een oplossing hiervoor.
> Mogelijks moeten we enkel voor dispatch naar een goedkoop api based model gaan, dit
> lijkt doenbaar met iets simpel. Maar indien het deterministisch kan, dan lijkt mij dat
> nog wenselijker."

Verwant: [`subscription-pool-dispatch-analyse.md`](./subscription-pool-dispatch-analyse.md),
[`subscription-flexibiliteit-analyse.md`](./subscription-flexibiliteit-analyse.md),
[`kanban-dispatch-spec.md`](./kanban-dispatch-spec.md),
[`9router-integratie-analyse.md`](./9router-integratie-analyse.md),
[`subscriptions.md`](./subscriptions.md).

---

## 0. TL;DR — het antwoord op beide vragen

**Vraag 1 — "is dispatch hard gekoppeld aan het Anthropic-abonnement?"**

**Nee, niet architecturaal — maar in de praktijk wel voor de route die je vraagt.**
Het woord "dispatch" dekt drie lagen die los van elkaar staan, en alleen de derde
verbrandt abonnement:

| Laag | Wat het doet | Gekoppeld aan Anthropic? |
|---|---|---|
| **A — dispatch-besluit** | kaart kiezen, claimen, persona/kolom bepalen, model/provider-precedentie, prompt bouwen, dep-DAG, reaper | **Nee. Nul koppeling, nul LLM-calls.** |
| **B — spawn-env** | welke CLI + welke credentials/endpoint het proces meekrijgt | **Nee.** Vier providers ondersteund. |
| **C — executor-sessie** | het echte werk; hier gaat quota op | **Ja, als default** — niet als slot. |

**Vraag 2 — "enkel voor dispatch naar een goedkoop API-based model … maar deterministisch
is wenselijker"**

**De dispatcher is al volledig deterministisch.** Er valt niets te "verplaatsen naar een
goedkoop model", omdat de dispatcher zelf geen enkel model raadpleegt: er zit geen
LLM-SDK in `backend/requirements.txt` (geen `anthropic`, geen `openai`, geen `litellm`)
en nergens in `backend/app` staat een uitgaande model-call. Kaartkeuze, persona-routing
en model-/provider-precedentie zijn gewone Python-if-ketens. De gewenste eindtoestand
("deterministisch") is dus de *bestaande* toestand — dat deel van de kaart vraagt geen werk.

Wat wél ontbreekt is de andere helft: **de executor-sessies op een goedkoop,
API-key-gebaseerd endpoint kunnen zetten.** De machinerie daarvoor bestaat al
(`provider = "anthropic-compatible"` + een endpoint-registry met `base_url`/`model`/
credential-naam), maar is **alleen bereikbaar vanuit de interactieve New-Session-dialog,
niet vanuit de auto-dispatcher.** Dat is het echte gat, en het is klein: de resolutie
bestaat, ze is alleen niet doorgetrokken naar het dispatch-pad.

**Aanbeveling:** vier vervolgkaarten (§7) die het `anthropic-compatible`-pad
doortrekken naar auto-dispatch, de configuratie fail-fast valideren, het
headless-transport op provider-pariteit brengen, en het in de kanban-UI zichtbaar
maken. Geen nieuw datamodel, geen schema-migratie, geen LLM in de dispatcher.

---

## 1. Laag A — het dispatch-besluit is al deterministisch

Geverifieerd, niet aangenomen:

- `backend/requirements.txt` bevat geen enkele LLM-SDK (fastapi, sqlalchemy, httpx,
  apscheduler, mcp, …).
- `grep -rn "import anthropic|from anthropic|openai|litellm|messages.create|/v1/messages"
  backend/app` → **nul treffers.**

Dit bevestigt wat [`subscription-verbruik-inzicht-analyse.md`](./subscription-verbruik-inzicht-analyse.md)
(beslissing `a410468d`, 2026-07-15) al vaststelde: **Cockpit doet per ontwerp nul
LLM-calls — het spawnt CLI's.** De hele besluitketen in `dispatch.py` — welke kolom,
welke persona, welk model, welke provider, welke kaart aan de beurt is — is
deterministische code met een expliciete precedentie-ladder
(`dispatch.py:3237-3288`).

**Gevolg voor de kaart:** de premisse "misschien moeten we voor dispatch naar een
goedkoop API-model" berust op het beeld dat de dispatcher zelf redeneert. Dat doet hij
niet. Een goedkoop model toevoegen aan laag A zou de determinisme-eigenschap die de
gebruiker expliciet prefereert juist *weghalen*. **Niet doen.**

---

## 2. Laag B — wat vandaag al vendor-onafhankelijk werkt

`backend/app/services/agentic_cli/provider_env.py` kent vier providers:

| Provider | Auth | Bereikbaar vanuit auto-dispatch? |
|---|---|---|
| `anthropic` | abonnement-OAuth van de `claude`-CLI (lege env) | ✅ (default) |
| `bedrock` | AWS-credential-chain op de host | ✅ |
| `minimax` | API-key (`ANTHROPIC_AUTH_TOKEN` uit settings) | ✅ |
| `anthropic-compatible` | vrije `base_url` + `model` + API-key | ❌ **alleen interactief** |

MiniMax bewijst het punt in productie: een API-key-gebaseerde, niet-Anthropic vendor
draait vandaag via dezelfde `claude`-CLI, puur door `ANTHROPIC_BASE_URL` +
`ANTHROPIC_AUTH_TOKEN` te zetten. **De koppeling aan Anthropic zit dus niet in de CLI en
niet in het transport — alleen in de default.** Die default is drie hard-coded
fallbacks: `or PROVIDER_ANTHROPIC` op `dispatch.py:1140`, `:3242` en `:3480`.

Er zijn bovendien al drie carriers om die default te overrulen, elk zonder
schema-migratie (JSON in `KanbanMeta`): board-brede subscription-pin, subscription-pool,
en per-kaart `column_overrides`. Plus `KanbanColumn.default_provider` als
kolom-instelling.

---

## 3. Vier geverifieerde gaten

### G1 — `anthropic-compatible` is onbereikbaar vanuit auto-dispatch

Het worktree-transport bouwt zijn spawn-opties zonder endpoint-velden:

```python
# backend/app/kanban/dispatch.py:2419-2423
options = SpawnCommandOptions(
    directory=worktree_path, mode="plain", prompt=prompt,
    skip_permissions=skip_permissions, worktree_path=worktree_path, repo_path=repo,
    provider=provider, model=model,          # <- geen endpoint_base_url / endpoint_auth_token
)
```

De endpoint-resolutie (`endpoint:<project_key>:<naam>` → `base_url` + credential) bestaat
wél, maar uitsluitend in de REST-handler van de interactieve spawn
(`backend/app/api/v1/runs/router.py:497-545`), omdat die de DB-sessie bezit. Het
dispatch-pad heeft óók een DB-sessie, maar roept die resolutie nooit aan.

**Gevolg:** precies de route die de kaart vraagt — auto-dispatch naar een goedkoop
API-endpoint (eigen Anthropic API-key, LiteLLM-router, of welk Anthropic-compatibel
endpoint dan ook) — werkt in de New-Session-dialog en **niet** voor de agents die het
bord leegwerken.

✅ **Geïmplementeerd (kaart 293d1faa…):** dispatch roept nu dezelfde `resolve_compatible_endpoint`-helper aan als REST (`endpoints.py:201`), zodat de twee paden niet meer kunnen driften. De `SpawnCommandOptions` worden voorzien van `endpoint_name` / `endpoint_base_url` / `endpoint_auth_token`, en het `provider_env`-pad ontvangt een geldige `base_url`. Dekking: `tests/test_dispatch_compatible_endpoint.py`.

### G2 — de pool accepteert een provider die dispatch niet kan uitvoeren

`subscription_pool.py:73-77` laat `anthropic-compatible` toe als pool-provider:

```python
_ALLOWED_POOL_PROVIDERS = (
    PROVIDER_ANTHROPIC, PROVIDER_BEDROCK, PROVIDER_MINIMAX,
    PROVIDER_COMPATIBLE,
)
```

Zet je zo'n entry, dan resolvet dispatch `provider="anthropic-compatible"`, bereikt
`build_provider_env` zónder `base_url` (G1) en die gooit bewust een `ValueError`
(`provider_env.py`, compatible-tak). De spawn faalt, de claim wordt vrijgegeven,
`dispatch_failures` telt op, en na `MAX_DISPATCH_FAILURES` landt de kaart in Impediment
(`dispatch.py:3299-3330`). Netto: **een configuratie die de API accepteert produceert
stil falende dispatches.**

Tweede helft van hetzelfde gat: `KanbanColumn.default_provider` kent **geen enkele
validatie** (`service.py:539` neemt elke string aan). Een typefout in de kolom-instelling
geeft dezelfde faalcyclus.

✅ **Geïmplementeerd (kaart 293d1faa…):** drie opslag-grenzen weigeren nu een onvolledige configuratie **vóór** de eerste dispatch: (1) `_ALLOWED_OVERRIDE_PROVIDERS` is uitgebreid met `PROVIDER_COMPATIBLE`, en `set_active_subscription_override` weigert compatibel zonder `endpoint_name` of met een onbekende naam; (2) `set_subscription_pool` doet hetzelfde voor pool-entries, inclusief endpoint-existence-check; (3) `_validate_default_provider` in `service.py` (aangeroepen door `create_column`/`update_column`) weigert een niet-allowlist-waarde met HTTP 422 — `router.py:168-218`. Defense-in-depth: `resolve_compatible_endpoint` blijft `ValueError` raisen voor onbekende endpoint-namen zodat een corrupte KanbanMeta-rij ook op dispatch-tijd wordt geweigerd (zie `test_dispatch_raises_when_endpoint_name_unknown`).

De kanban-UI verbergt dit vandaag toevallig — `PROVIDER_LABELS` in
`frontend/src/features/kanban/types.ts:29-33` kent maar drie providers — maar de UI is
geen validatiegrens; de REST-API is dat.

### G3 — het headless-transport is de facto Anthropic-only

```python
# backend/app/kanban/headless_runner.py:303
provider_env = build_provider_env(provider, model=model, cli_id="claude-code")
```

Drie dingen ontbreken t.o.v. het worktree-pad: geen `minimax_api_key`, geen
`minimax_base_url`, geen `base_url`/`auth_token` — en `cli_id` is hard-coded in plaats
van de meegegeven waarde. Praktisch:

- `provider="minimax"` onder headless zet wél `ANTHROPIC_BASE_URL` maar **geen**
  auth-token → de sessie authenticeert niet.
- `provider="anthropic-compatible"` gooit meteen.

Headless is vandaag niet de default (`DEFAULT_TRANSPORT = "worktree"`,
`dispatch.py:224`), maar het is wél het strategische pad (stream-json → ACP, zie
[`acp-transport-decision.md`](./acp-transport-decision.md)). Het mag niet
Anthropic-only geboren worden.

### G4 — geen gat, maar een correct opgeloste val (ter referentie)

`_effective_model` (`dispatch.py:1050`) laat de persona-frontmatter-`model:` (aliassen als
`opus`/`sonnet` — Anthropic-vocabulaire) alleen vallen als de provider Anthropic is.
Zonder die poort zou een `--model opus` de `ANTHROPIC_MODEL=MiniMax-M3` van de provider-env
overschrijven. Dit is dus **al** goed afgehandeld en hoeft geen kaart; het staat hier
zodat een lezer het gat niet opnieuw vermoedt.

---

## 4. Aanbeveling

**Trek het bestaande `anthropic-compatible`-pad door naar auto-dispatch, uitsluitend op
de API-key-tier.** Dat is één resolutie-stap plus drie doorgegeven velden — geen nieuw
datamodel, geen migratie.

Ontwerpkeuze die de vervolgkaarten meekrijgen (aanname, expliciet gemaakt): **de
endpoint-naam wordt gedragen door de bestaande JSON-carriers** — pool-entry, board-brede
override en per-kaart `column_override` — omdat die alle drie JSON in `KanbanMeta` of op
de kaart zijn en dus migratie-vrij uitbreidbaar. `KanbanColumn.default_provider` is een
echte kolom; een endpoint-veld daar vereist een schema-wijziging en dit project heeft
geen migratiesysteem ("schema changes require deleting the db"), dus dat valt buiten
scope tot er een aantoonbare behoefte is.

### 4.1 Verhouding tot drie eerdere beslissingen — dit heropent er geen

| Eerdere beslissing | Waarom dit voorstel er niet mee botst |
|---|---|
| **`a410468d` (2026-07-15)** — proxy-op-subscription-auth NO-GO | Dat verwierp het *tunnelen van abonnement-OAuth* door een proxy (auth-/ToS-risico). Dit voorstel raakt uitsluitend de **API-key-tier**: een eigen sleutel op een eigen endpoint. |
| **`27cdc2bd` (2026-07-19)** — 9router NO-GO als geheel, conditionele GO "ernaast" | Consistent: alleen de API-key-tier, opt-in, nooit default. Dit voorstel bouwt geen router; het maakt de bestaande endpoint-slot bruikbaar, waar een LiteLLM/9router-instantie dan ín past. |
| **`290f6fb7` (2026-07-14)** — same-vendor multi-account NO-GO | Dat ging over **meerdere OAuth-abonnementsaccounts** binnen één vendor, wat `CLAUDE_CONFIG_DIR`/`HOME`-isolatie en per-account logins vereist. Een API-key-endpoint is een ander mechanisme: één env-var, geen credential-dir-isolatie, geen login — en is al als eigen slot gemodelleerd (`services/subscriptions/registry.py:113`, `claude-code:anthropic-compatible`). |

### 4.2 Wat dit voorstel expliciet **niet** doet

- Geen LLM-call in de dispatcher (§1 — dat zou determinisme wegnemen).
- Geen abonnement-OAuth door een proxy (§4.1).
- Geen tweede Anthropic-abonnementsaccount (§4.1).
- Geen wijziging aan de default: `anthropic` blijft de fallback. Dit levert
  **optionaliteit**, geen omschakeling.

---

## 5. Kosten-claim: expliciet ongemeten

Deze analyse doet **geen** gekwantificeerde besparingsclaim. Er is in deze spike niets
gemeten: geen token-verbruik per provider, geen €-vergelijking, geen kwaliteitsregressie
van een goedkoper model op echte kaarten. De rechtvaardiging is **niet** "dit bespaart
X%", maar: *vandaag stopt al het autonome werk zodra één abonnement leeg is, en er is
geen knop om het elders te laten landen.* Dat is een beschikbaarheids-/optionaliteitsargument,
en dat staat los van prijs.

Wie later wél een besparing wil claimen: het meetrecept staat in
[`token-saver-meet-harnas.md`](./token-saver-meet-harnas.md) (drie verbruikscomponenten
apart, nooit opgeteld) en
[`per-persona-mcp-allowlist-decision.md`](./per-persona-mcp-allowlist-decision.md) §7.
Kwaliteitsregressie van een goedkoop model op autonome kaarten is daarbij het échte
risico, niet de prijs — een kaart die tweemaal opnieuw moet, kost meer dan hij bespaart.

---

## 6. Vervolgkaarten — en wat het bord al had

Een dedup-pass over `Backlog`/`Impediment` liet zien dat er al een hele lijn
vendor-onafhankelijkheid loopt onder parent `27cdc2bd…` (de 9router-analyse):
`333af652…` (de `anthropic-compatible`-naad), `bbfcb365…` (LiteLLM-pilot),
`8222fee8…` (endpoint-catalogus), `66180bc9…` (gratis-lanes bedraden),
`2f3776dd…` (per-CLI endpoint-vertaling), `8f40d443…` (pool gepind op claude-code).
Deze analyse maakt daar **twee** kaarten bij en corrigeert er **één**.

| # | Kaart | Deps |
|---|---|---|
| K1 | Endpoint-resolutie doortrekken naar auto-dispatch + fail-fast validatie (G1 + G2) | — |
| K2 | Headless-transport op provider-pariteit (G3) | K1 |

K2 wacht op K1: het consumeert dezelfde resolutie-helper die K1 oplevert.

**Correctie op `66180bc9…` ("Gratis-lanes bedraden").** Die kaart stelt: *"Beide
mechanismen bestaan al … Dit is dus bedraden en aantonen, geen nieuw routeringsconcept."*
Voor de `column_overrides`-helft klopt dat; voor `anthropic-compatible` **niet** — G1
laat zien dat de spawn-env voor die provider vanuit dispatch niet eens gebouwd kán
worden. Zijn eerste acceptatiecriterium ("bereikt de spawn-env, geverifieerd op de
daadwerkelijke `ANTHROPIC_BASE_URL`") is vandaag onhaalbaar. Er is een comment op die
kaart geplaatst; K1 levert wat ze veronderstelt.

**Waarom K1 los staat en niet in `66180bc9…` opgaat:** die kaart hangt via `depends_on`
achter de endpoint-catalogus (`8222fee8…`) en daarmee achter de LiteLLM-pilot
(`bbfcb365…`). De threading-fix heeft geen van beide nodig — één handmatig
geconfigureerd endpoint volstaat. K1 achter die keten parkeren zou de enige echte
blokkade voor auto-dispatch-naar-een-eigen-endpoint onnodig maanden uitstellen.
