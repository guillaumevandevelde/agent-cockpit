---
title: "Subscriptions-pagina — credential-beheer & per-provider quota"
type: reference
status: active
---

# Subscriptions-pagina — credential-beheer & per-provider quota

> **Canoniek** voor de `/subscriptions`-pagina (voorheen `/providers`): waar per-abonnement
> credentials worden ingesteld en hoeveel quota elk abonnement nog heeft. Verzamelt de
> blijvende beslissingen van twee superpowers-taken. Voor bestandsdetails: zie de gelinkte
> specs.

## Waarom een aparte pagina

Credential-configuratie is een **eenmalige, globale** actie — die hoort niet in de "New
Session"-dialog, die conceptueel "spawn een sessie" is en per-sessie herconfiguratie
suggereert. De Subscriptions-pagina is de persistente plek die de gebruiker één keer bezoekt.

- Locatie: `frontend/src/features/subscriptions/` (`SubscriptionsPage.tsx`).
- Nav: item **Subscriptions** in de **Claude Code**-provider-nav-groep (`frontend/src/lib/navigation.ts`),
  naast Config/Sessions/MCP Servers — niet in de provider-agnostische nav, want het platform
  (Anthropic/Bedrock/MiniMax) raakt alleen `claude`-CLI-invocaties.
- Route/naam is generiek gehouden (`Subscriptions`) zodat een tweede provider erbij kan zonder
  rename.

## MiniMax-credential relocatie

**Superpowers-tegenhanger:** [`../superpowers/specs/2026-07-04-minimax-providers-page-design.md`](../superpowers/specs/2026-07-04-minimax-providers-page-design.md)
(design schreef nog `/providers`; de route is inmiddels `/subscriptions`).

De MiniMax API-key Save/Change/Clear-form verhuisde uit de New-Session-dialog naar
`MinimaxCredentialsCard` op deze pagina. Blijvende beslissingen:

- **Scope: alleen MiniMax.** Geen placeholder-secties voor Anthropic/Bedrock — die hebben
  vandaag niets via UI te configureren (Bedrock resolvet AWS-creds uit de host-chain,
  Anthropic heeft niets nodig). YAGNI; de generieke naam laat uitbreiding toe.
- **Dialog wordt read-only consumer.** Wanneer MiniMax niet geconfigureerd is toont de dialog
  een korte notice met link naar `/subscriptions` i.p.v. een inline-form. De
  Endpoint-selector (International/China) blijft in de dialog — dat is een echte per-sessie
  keuze, geen credential.
- **Geen backend-wijziging** voor de relocatie: `GET/POST/DELETE
  /agent-bridge/platforms/minimax/…` bestonden al en echoën de key nooit terug.

## Per-provider usage / quota

> **Voorganger (SUPERSEDED 2026-07-15, kanban-card `64343a81…`):**
> [`../superpowers/specs/2026-07-08-subscription-usage-leftover-design.md`](../superpowers/specs/2026-07-08-subscription-usage-leftover-design.md)
> — het oorspronkelijke ontwerp is **niet** geïmplementeerd zoals gespecificeerd
> (de `PeriodUsage` / `SubscriptionUsageSnapshot`-dataclasses en de
> `subscription_prefs`-tabel die het beschrijft bestaan niet). Deze sectie is de
> canonieke, geleverde vorm; raadpleeg de spec alleen voor historische context.

Doel: één scherm dat toont "hoeveel heb ik op elk abonnement over", zodat de gebruiker die
twee abonnementen parallel draait (Anthropic + MiniMax) kan beslissen waar werk naartoe te
routeren. Blijvende beslissingen:

- **Per-provider weergave, niet geünificeerd.** Elke provider houdt zijn eigen labels
  verbatim (Anthropic: "5h rate" + "Weekly"; MiniMax: wat de API blootgeeft). Equivalentie
  faken zou oneerlijk zijn — de plans handhaven verschillende dingen onder die namen. De
  gedeelde envelop is één rij per periode.
- **Abstractie mirrort `services/agentic_cli/`.** Een `SubscriptionUsageProvider`-ABC met één
  concrete subclass per provider (`backend/app/services/subscriptions/`), zodat een derde
  abonnement (Bedrock) een subclass wordt i.p.v. een gedrifte ad-hoc functie.
- **Anthropic: lokaal + gekozen plan-tier.** Anthropic publiceert geen usage-API voor Pro/Max;
  de provider combineert de lokale `UsageService` (5h-venster uit JSONL-logs) met een
  user-geselecteerde plan-tier die de limiet levert. Plan-tier staat in een `<Select>` op de
  pagina, opgeslagen in SQLite (`subscription_prefs`-tabel), niet in `.env`.
- **MiniMax: remote API.** Als de probe geen bruikbaar endpoint vindt, shipt de card met een
  eerlijke "MiniMax exposes no usage data"-lege staat — **geen fabricage**.
- **Eerlijkheid boven volledigheid.** Anthropic's weekly-limiet is niet gepubliceerd → toont
  "limit not published" i.p.v. een verzonnen getal; gepubliceerde plan-limieten kunnen driften
  → de card toont een "verify before trusting"-noot. Geen fake progress-bars.
- **5-min in-memory cache per provider**, geïnvalideerd bij plan-tier-wijziging en
  MiniMax-key set/clear. Geen persistente usage-cache, geen auto-refresh polling (fetch op
  mount + tab-focus).

## Zie ook

- [`agent-bridge.md`](./agent-bridge.md) — platform-selectie die deze credentials in de spawn-omgeving injecteert.
- [`terminology.md`](./terminology.md) — Provider / CLI / Model naamgeving.

## Subscription-pool inspectie & wijzigen

De **subscription-pool** is een per-project-configuratie in de kanban-DB die de dispatcher
in staat stelt uit te wijken naar een alternatief abonnement wanneer het huidige
abonnement zijn limiet raakt. De **kop** (eerste entry) is de impliciete kolom-default —
de **staart** is wat de operator hier instelt. Voor ontwerp en rationale zie
[`spillover-per-kolom-decision.md`](./spillover-per-kolom-decision.md); voor de
implementatie-context zie
[`subscription-auto-release-analyse.md` §5](./subscription-auto-release-analyse.md#5-gat-d-spillover-is-nog-altijd-dode-code).

### Inspecteren

Drie gelijkwaardige oppervlakken, allemaal read-only:

**REST:**
```bash
# Board-wide (legacy key)
curl -s "http://localhost:8000/api/v1/kanban/subscription-pool?project_key=<pk>"

# Per-kolom (post-kaart-b36ca702; default fallback is board-wide)
curl -s "http://localhost:8000/api/v1/kanban/subscription-pool?project_key=<pk>&column=engineer"
```

**Rechtstreeks op de DB** (handig voor diagnose; pad is bewust portable gehouden — zie
`backend/app/config.py:21-29`):
```bash
python3 -c "
import sqlite3
c = sqlite3.connect('/home/vdvgu/.claude-registry/kanban.db')
for k, v in c.execute(\"select key, value from kanban_meta where key like 'subscription_pool%' order by key\"):
    print(k, '->', v)
"
```

**UI:** de pool zit achter de **Subscriptions**-knop in de **kanban-bord-toolbar**, niet
op de `/subscriptions`-pagina — die pagina toont alleen usage en credentials en bevat
geen enkele pool-control. Pad: `frontend/src/features/kanban/KanbanPage.tsx:559` →
`components/SubscriptionToolbarButton.tsx:84` →
`components/SubscriptionPoolDialog.tsx`. De dialog heeft een kolom-`<Select>`
(`data-testid="pool-scope-column"`, regel 529); bij een kolom-selectie toont hij
`column.default_provider` als read-only kop-regel (`data-testid="pool-implicit-head"`,
regel 580) plus de staart-entries eronder. Lege staat = "no pool configured — column
defaults apply".

### Wijzigen

Dezelfde drie oppervlakken, met dezelfde write- of clear-semantiek:

**REST** (preferred — valideert tegen de `kanban_columns`-allow-list en faalt-fast op
onbekende kolommen):
```bash
# Engineer: spill van MiniMax naar Anthropic
curl -s -X POST "http://localhost:8000/api/v1/kanban/subscription-pool" \
  -H "Content-Type: application/json" \
  -d '{
    "project_key": "<pk>",
    "column": "engineer",
    "pool": [{"cli": "claude-code", "provider": "anthropic", "model": null, "drempel": 0.9}]
  }'

# Reviewer: bewust leeg ("nooit uitwijken")
curl -s -X POST "http://localhost:8000/api/v1/kanban/subscription-pool" \
  -H "Content-Type: application/json" \
  -d '{"project_key": "<pk>", "column": "reviewer", "pool": []}'

# Wissen — kolom erft weer de board-wide pool
curl -s -X POST "http://localhost:8000/api/v1/kanban/subscription-pool" \
  -H "Content-Type: application/json" \
  -d '{"project_key": "<pk>", "column": "engineer", "pool": null}'
```

`drempel` is een fractie in `(0, 1]`: bij `0.9` slaat de router de entry over zodra
het usage-snapshot ≥ 90% verbruikt is; `1.0` betekent "gebruik tot de per-provider pause
hem raakt". Houd hem conservatief (≥ 0.9) zodat de router pas uitwijkt als de
provider echt bijna op is — niet bij elke kleinere schommeling.

**Storage-laag direct** (niet aanbevolen — geen validatie tegen de kolom-allow-list):
```python
from app.kanban.db import KanbanSessionLocal
from app.kanban.subscription_pool import PoolEntry, set_subscription_pool

async def main():
    async with KanbanSessionLocal() as s:
        await set_subscription_pool(
            s, "<pk>",
            [PoolEntry(provider="anthropic", model=None, drempel=0.9)],
            column="engineer",
        )
        await s.commit()
```

**UI:** dezelfde `SubscriptionPoolDialog` achter de toolbar-knop hierboven; bewaar met de
"Save"-knop, veeg leeg met de prullenbak-knop. Validaties en de
"empty list rejected" guard draaien client-side; een save via de UI komt door dezelfde
REST-endpoint.

### Valideren dat spillover vuurt

Simuleer het scenario dat er echt toe doet: **de kop raakt zijn limiet**. Dat is de kolom-default
(`engineer` → `minimax`, `analyst` → `anthropic`), en de vraag is of de kaart dan uitwijkt naar de
staart in plaats van op de reset te wachten. Kies dus nooit een provider die in geen enkele keten
voorkomt — dat bewijst niets.

De pure router (`subscription_pool.has_available_spillover`) is de canonieke bron — geen
DB-pause-gating, alleen drempel-/paused-provider-logica:
```python
from app.kanban.db import KanbanSessionLocal
from app.kanban import subscription_pool

async def main():
    async with KanbanSessionLocal() as s:
        entries = await subscription_pool.get_subscription_pool(
            s, "<pk>", column="engineer",
        )
    # entries is de STAART: [PoolEntry(provider='anthropic', drempel=0.9, …)]

    # Positief: de kop (minimax = column default) raakt zijn limiet.
    assert subscription_pool.has_available_spillover(
        entries, {}, paused_providers={"minimax"}, cli_id="claude-code",
    ) is True

    # Negatieve controle: kop én staart gepauzeerd → niets om naar uit te wijken.
    assert subscription_pool.has_available_spillover(
        entries, {}, paused_providers={"minimax", "anthropic"}, cli_id="claude-code",
    ) is False
```

Voor `analyst` draai je hetzelfde met kop `anthropic` en staart `minimax`. Voor `reviewer` is de
staart bewust leeg, dus de positieve assertie is daar `is False` — "nooit uitwijken" is de
gewenste uitkomst, geen defect.

Voor de dispatch-zijde (inclusief time-based pause):
```python
from app.kanban.db import KanbanSessionLocal
from app.kanban import dispatch

async def main():
    async with KanbanSessionLocal() as s:
        return await dispatch._pool_spillover_available(
            s, project_key="<pk>", limited_provider="minimax",
            cli_id="claude-code", column="engineer",
        )
# True  = er is een vrije uitwijk; de kaart wordt direct herdispatchbaar.
# False = geen vrije uitwijk; de bestaande per-provider pause (wachten op reset) geldt.
```

Deze tak kan `False` geven terwijl de pure router `True` zegt: dat gebeurt wanneer de staart zélf
al een actieve per-provider pause heeft. Dat is correct gedrag — een gepauzeerde target is geen
geldige uitwijk. Meet daarom altijd beide takken voordat je een `False` als defect leest.

Gemeten op de live board-configuratie (2026-08-06, `project_key`
`git:github.com/guillaumevandevelde/agent-cockpit`), beide takken:

| Kolom | Kop (limiet) | Staart | Uitkomst |
|---|---|---|---|
| `engineer` | `minimax` | `[anthropic@0.9]` | `True` — wijkt uit |
| `analyst` | `anthropic` | `[minimax@0.9]` | `True` — wijkt uit |
| `reviewer` | `anthropic` | `[]` | `False` — wacht, zoals bedoeld |

Een echte `spilling over`-logregel verschijnt op de kaart-activiteit-feed
(`🔀 Rate-limit hit on '<provider>' — spilling over …`) zodra een bestaande sessie
daadwerkelijk zijn limiet raakt; de dispatcher post hem via
`move_limited_session_to_resume` (`backend/app/kanban/dispatch.py:6704-6709`). De
regressietests `test_production_pool_tails_*` in
`backend/tests/test_subscription_pool_dispatch.py` pinnen het end-to-end-pad inclusief
die comment.
