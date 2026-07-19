---
title: "Analyse — Flexibel & maximaal gebruik van abonnementen (usage-aware dispatch-routing)"
type: analysis
status: active
---

# Analyse — Flexibel & maximaal gebruik van abonnementen (usage-aware dispatch-routing)

**Datum:** 2026-07-13
**Status:** Analyse / ontwerpvoorstel — fase 0/1b/2 zijn geïmplementeerd; **fase 1a niet**
(zie waarschuwing hieronder)

> ⚠️ **Update 2026-07-15 — fase 1b/2 zijn gebouwd op een niet-ingeloste fase 1a.**
> De pool-router is geshipt (`7fb2df2`, `0122a82`), maar het usage-signaal dat hij
> consumeert is dood: `get_provider_for` wordt ge-`await`-ed hoewel hij synchroon is, en
> de provider-registry wordt nooit gevuld. De pool degradeert daardoor tot "kies altijd
> entry #1" — de drempels uit §4/§5 worden nooit geëvalueerd. Volledige bevindingen +
> vervolgkaarten: [`subscription-pool-dispatch-analyse.md`](./subscription-pool-dispatch-analyse.md).
**Trigger:** kanban-kaart "Analyse - Maximaal gebruik abonnementen". Gebruiker:
> "Ik moet nu telkens wanneer ik overschot heb op een abonnement agents wisselen op
> de kolommen. Dit is niet echt praktisch. Analyseer hoe we flexibeler kunnen
> omspringen met de abonnementen."

Verwant: [`spike-claude-code-model-switching.md`](./spike-claude-code-model-switching.md)
(Anthropic ↔ MiniMax switch), [`kanban-model-override.md`](./kanban-model-override.md)
(card/column/persona model-precedentie), [`subscriptions.md`](./subscriptions.md)
(credential-beheer + per-provider usage-pagina, gedeeltelijk ontworpen), en de
per-provider pause in [`kanban-dispatch-spec.md`](./kanban-dispatch-spec.md).

---

## 1. Het probleem — handmatig kolom-per-kolom herconfigureren

De gebruiker draait **meerdere abonnementen naast elkaar** (elk gekoppeld aan een
aparte coding-CLI en/of Claude-Code-backend — zie §3). Elke agent-kolom op het
kanban-bord heeft een *statische* keuze welk abonnement zijn kaarten gebruiken.

Wanneer één abonnement **overschot** (resterende quota) heeft dat anders verloren
gaat, wil de gebruiker werk daarheen verschuiven. Vandaag betekent dat: **elke
agent-kolom handmatig openen en `default_agent` / `default_provider` / `default_model`
omzetten** naar het onderbenutte abonnement — en later weer terugzetten. Dat is:

- **Repetitief & foutgevoelig** — N kolommen × elke keer dat de quota-balans wijzigt.
- **Reactief zonder signaal** — de gebruiker moet zélf onthouden/inschatten waar nog
  overschot zit; er is geen bord dat "abonnement X heeft nog 60% over" toont bij de
  routing-knop.
- **Grofkorrelig** — de switch is per kolom, niet per kaart of per tijdsvenster; je
  kunt niet "vul eerst abonnement A op tot z'n limiet, spill dan naar B" uitdrukken.

De kern: **de abonnement-keuze is vandaag statische configuratie, terwijl quota een
dynamische, aan reset-vensters gebonden grootheid is.** Elke keer dat die twee uit
elkaar lopen, is de mens de handmatige controller.

## 2. Huidige situatie (grounded facts)

Onderzocht: `backend/app/kanban/dispatch.py`, `dispatch_pause.py`,
`backend/app/services/agentic_cli/` (registry + `provider_env.py`),
`backend/app/services/usage_service.py`, `frontend/src/features/subscriptions/`.

### 2.1 Waar de abonnement-keuze vandaag zit

| Laag | Veld | Betekenis |
|---|---|---|
| `KanbanColumn` | `default_agent` | Welke **CLI** de kolom spawnt (`claude-code`, `codex`, `copilot`, `mimo-code`, `open-code`). |
| `KanbanColumn` | `default_provider` | Voor Claude Code: welke **backend/vendor** (`anthropic` \| `bedrock` \| `minimax`) — env-injectie via `provider_env.py`. |
| `KanbanColumn` | `default_model` | Column-brede model-default. |
| `KanbanCard` | `model` | Per-card model-override. |
| `KanbanCard` | `column_overrides` | Per-card, per-kolom `{provider, model}`-override. |

### 2.2 Hoe de dispatcher vandaag kiest (`dispatch.py`)

Provider-precedentie: `card.column_overrides[col].provider > column.default_provider >
PROVIDER_ANTHROPIC`. Model-precedentie (`_effective_model`): `override_model >
card.model > column.default_model > persona-frontmatter model: > geen --model`.

**Belangrijk: er is nul usage-bewustzijn in deze keten.** De dispatcher leest puur
statische config. Er is geen tak die "kies het abonnement met het meeste overschot"
doet. Dat is precies het gat.

### 2.3 Wat er wél al automatisch gebeurt — de per-provider pause

`dispatch_pause.py` splitst de dispatch-pauze in **per-provider slots**
(`dispatch_paused_until:<provider>`). Wanneer een sessie de account-brede usage-limiet
raakt, pauzeert alleen dát abonnement en verhuist de kaart naar "To Resume" met
`scheduled_at` = reset-tijd. Andere abonnementen blijven doorlopen.

Dit is **reactieve, binaire failover**: bij de limiet → *stop en wacht op reset*. Het
**verschuift werk niet** naar een abonnement dat nog overschot heeft; het zet de kaart
in de wacht. Voor "maximaal gebruik" is dit het halve verhaal: het voorkomt spinnen op
een uitgeputte provider, maar benut een onderbenutte provider niet proactief.

### 2.4 Welk usage-signaal beschikbaar is (heterogeen!)

- **Anthropic (Claude Code):** géén officiële usage-API voor Pro/Max. Alleen
  `UsageService` — een 5h-venster-schatting uit lokale JSONL-logs + een
  gebruiker-gekozen plan-tier. De **weekly**-limiet is niet gepubliceerd. → Overschot
  is een *schatting*, geen exact getal.
- **MiniMax:** remote API; `subscriptions.md` noteert dat als de probe niets bruikbaars
  vindt, er een eerlijke lege staat getoond wordt (geen fabricage).
- **Codex / Copilot / OpenCode:** alleen diagnostische context
  (`codex_usage_context_service.py` is beschrijvend, geen schoon "resterend
  quota"-getal). → Voor deze CLIs is er vandaag **geen betrouwbaar overschot-signaal**.
- **De per-abonnement "hoeveel heb ik nog over"-pagina uit
  [`subscriptions.md`](./subscriptions.md) is ontworpen maar (nog) niet gebouwd** —
  `SubscriptionsPage.tsx` toont vandaag alleen de MiniMax-credentials-card. De
  `SubscriptionUsageProvider`-ABC + `subscription_prefs`-tabel bestaan als design, niet
  als code.

**Gevolg voor elke usage-aware router: het overschot-signaal is ongelijk van kwaliteit
per abonnement.** Anthropic = schatting, MiniMax = API-afhankelijk, Codex/Copilot =
vandaag afwezig. Een router mag dus nooit doen alsof alle abonnementen een exact,
vergelijkbaar quota-getal hebben (zie §6).

> ⚠️ **Update 2026-07-15 — het signaal is niet alleen ongelijk, het is ook verkeerd
> toegerekend.** MiniMax draait via dezelfde `claude`-CLI (alleen `ANTHROPIC_BASE_URL`
> om) en schrijft dus naar **dezelfde** `~/.claude/projects/**/*.jsonl`-boom. Omdat
> `UsageService.get_block_usage()` geen model-/provider-filter heeft, telt
> `AnthropicUsageProvider` de MiniMax-tokens mee in zijn Anthropic-schatting — op deze
> host **36,9% van alle tokens**. Wie de dode fase-1a fixt door de echte
> `AnthropicUsageProvider` te registreren, activeert daarmee deze bug: "geen signaal"
> wordt dan "fout signaal", wat erger is omdat het betrouwbaar oogt. De
> `model → subscription`-attributiefix hoort dus vóór (of samen met) de registry-fix.
> Volledige analyse + kwantificering:
> [`subscription-verbruik-inzicht-analyse.md`](./subscription-verbruik-inzicht-analyse.md) §4.

## 3. Twee dimensies van "abonnement"

De gebruiker zegt "**agents** wisselen op de kolommen". In deze codebase betekent
"abonnement wisselen" concreet één van twee (of beide) assen:

1. **CLI-as (`default_agent`)** — elke agentic-CLI hangt aan een eigen account:
   `claude-code` → Anthropic-abonnement, `codex` → OpenAI/ChatGPT-abonnement,
   `copilot` → GitHub-Copilot-abonnement, `mimo-code` → MiniMax-abonnement,
   `open-code` → eigen auth. Dit is de meest waarschijnlijke lezing van "agents
   wisselen".
2. **Provider-as (`default_provider`)** — binnen Claude Code schakelen tussen
   `anthropic` / `bedrock` / `minimax` via env-injectie.

Een goede oplossing abstraheert over **beide** assen onder één begrip
"**subscription**" (een {cli, provider}-paar met eigen auth + eigen quota-venster), en
laat de gebruiker één keer een *pool + beleid* configureren i.p.v. per kolom te
morrelen.

## 4. Ontwerpruimte — vier opties

### Optie A — Subscription-pool + prioriteitsbeleid (usage-aware router) ⭐

Vervang de statische per-kolom `default_agent`/`default_provider` door een
**geordende pool van subscriptions** met per-subscription drempels, bijv.:

```
1. anthropic-max      (prefereer; skip boven 90% van 5h-venster)
2. minimax            (spill hierheen als anthropic vol)
3. codex              (laatste val-terug)
```

Bij dispatch kiest een pure functie `pick_subscription(pool, usage_snapshot)` de
**eerste subscription in prioriteitsvolgorde die nog onder z'n drempel zit**, en zakt
anders door naar de volgende. De gebruiker configureert dit **één keer**; de router
herverdeelt daarna automatisch naarmate quota ebt en vloeit.

- **Lost het kernprobleem direct op:** geen kolom-per-kolom-gemorrel meer.
- **Sluit aan op bestaande precedentie:** de gekozen subscription levert
  `{agent, provider, model}` die exact op de bestaande `dispatch_card`-injectiepunten
  landen; geen nieuwe spawn-mechaniek nodig.
- **Bouwt voort op de per-provider pause (§2.3):** een gepauzeerd/uitgeput abonnement
  wordt in de pool overgeslagen i.p.v. de hele kolom te blokkeren.
- **Kost:** vereist (a) een leesbaar overschot-signaal per subscription (§2.4 — deels
  nog te bouwen) en (b) een pool-configuratie-UI + persistentie. Zie fasering §5.

### Optie B — Spillover-bij-limiet (uitbreiding van de pause)

Kleiner: laat de per-provider pause (§2.3), i.p.v. de kaart naar "To Resume" te zetten
en te wáchten, **eerst de volgende subscription in een fallback-lijst proberen**; pas
pauzeren als álle subscriptions uitgeput zijn.

- **Voordeel:** hergebruikt een bestaand, beproefd mechanisme; puur reactief, geen
  proactief usage-signaal nodig (triggert op de echte 429/limiet-respons).
- **Nadeel:** benut overschot pas *nadat* het voorkeursabonnement z'n muur raakt — het
  maximaliseert niet proactief, en het "vul A eerst helemaal op"-gedrag is precies wat
  je soms *niet* wilt (je wilt misschien A sparen). Minder expressief dan A.
- Is in feite **een deelverzameling van A** (A met alleen een limiet-drempel), dus het
  is eerder een *tussenstap naar* A dan een alternatief.

### Optie C — Globale "actieve subscription"-override (quick win) ⭐ (fase 0)

Eén bord-brede instelling "**route nu alles naar subscription X**" die alle
kolom-defaults overschrijft. De gebruiker flipt **één schakelaar** i.p.v. N kolommen te
editen.

- **Voordeel:** minimale bouw, verwijdert onmiddellijk de N-kolommen-pijn (de letterlijke
  klacht). Precedentie is triviaal: globale override > kolom-default.
- **Nadeel:** nog steeds handmatig en grofkorrelig (alles-of-niets, geen spill, geen
  usage-bewustzijn). Maar het is de goedkoopste directe verlichting en een natuurlijke
  opstap naar A (de globale override wordt later "beleid = pin naar X").

### Optie D — CCR / request-level routing (afgewezen voor dit doel)

Claude Code Router als gateway met fallback-routing. **Afgewezen als antwoord op deze
kaart**, om redenen die de spike al hard maakte
([`spike-claude-code-model-switching.md`](./spike-claude-code-model-switching.md) §11.4–11.5):

- CCR/directe base-URL-switch **deelt geen Anthropic-abonnement** — het vervangt de
  OAuth-auth volledig door een kale API-key (aparte facturatie). Het is dus geen
  "maximaal gebruik van je *abonnementen*", maar een overstap naar API-billing.
- CCR 3.0.0 patcht ongevraagd live `~/.claude/settings.json` — foot-gun op deze
  multi-agent-machine.
- CCR routeert *binnen* Claude Code op request-niveau; onze eenheid van werk is een
  hele gedispatchte sessie op een kolom. De pool-aanpak (A) zit op de juiste
  granulariteit.

CCR blijft relevant voor het *aparte* "adaptief model per taaktype binnen één
sessie"-spoor, niet voor abonnement-quota-balancering.

## 5. Aanbeveling — gefaseerd

| Fase | Wat | Waarom nu / afhankelijkheid |
|---|---|---|
| **0** | **Globale actieve-subscription-override** (Optie C): één bord-brede pin die alle kolom-defaults overschrijft, met een selector op de Subscriptions- of Kanban-instellingen-pagina. | Verwijdert de letterlijke klacht (N-kolommen-edit → 1 klik) met minimale bouw. Geen usage-signaal nodig. |
| **1a** | **Per-subscription overschot-signaal afmaken** — bouw de `SubscriptionUsageProvider`-abstractie uit [`subscriptions.md`](./subscriptions.md) (Anthropic 5h-venster + plan-tier; MiniMax API; eerlijke "onbekend" voor Codex/Copilot). | Voorwaarde voor elke *usage-aware* routing. Bestaat als design, niet als code. |
| **1b** | **Subscription-pool + `pick_subscription()`-router** (Optie A), die 1a consumeert en op de bestaande `dispatch_card`-precedentie landt. Overslaan van gepauzeerde/uitgeputte subscriptions via de bestaande per-provider pause. | Het eigenlijke "maximaal & automatisch". Hangt af van 1a. |
| **2** | **Spillover-bij-limiet** (Optie B) invouwen als drempel-tak van de pool-router. | Nice-to-have bovenop 1b; sluit de reactieve failover-lus. |

**Rode draad:** fase 0 is directe verlichting; fase 1 is de echte oplossing; fase 2 is
afwerking. Elke fase is los waardevol en los shipbaar.

## 6. Belangrijke beperkingen (eerlijkheid boven volledigheid)

1. **Anthropic-overschot is een schatting, geen exact getal.** Geen usage-API; weekly
   ongepubliceerd. De router moet drempels op het 5h-venster + plan-tier baseren en dit
   in de UI als schatting labelen — geen valse precisie (consistent met
   [`subscriptions.md`](./subscriptions.md)'s "verify before trusting").
2. **Quota zijn niet cross-vendor vergelijkbaar.** "60% over op Anthropic" en "60% over
   op MiniMax" meten verschillende dingen (requests vs tokens vs $). De pool werkt
   daarom op **prioriteit + per-subscription drempel**, niet op een geünificeerde
   "meeste-overschot"-score die vendors tegen elkaar afweegt.
3. **Codex/Copilot hebben vandaag geen bruikbaar overschot-signaal.** Voor die
   subscriptions kan de router alleen op de per-provider pause (reactief) en handmatige
   prioriteit leunen tot een signaal bestaat — de pool moet een subscription-zonder-signaal
   netjes aankunnen (behandel als "altijd beschikbaar tot de pause hem raakt").
4. **Aparte facturatie ≠ gedeeld abonnement.** MiniMax/Bedrock via API-key is andere
   billing dan een Anthropic-abonnement; "maximaal gebruik" betekent *elk* abonnement tot
   z'n eigen limiet benutten, niet credits ertussen verschuiven (dat kan technisch niet —
   spike §11.5).

## 7. Open beslissing voor de gebruiker (fork die het ontwerp bijstuurt) — ✅ BESLIST 2026-07-14: A / vendor-diverse

> **Update 2026-07-14 (host-kaart `290f6fb7…`):** deze fork is beslist op **A — Nee, alleen
> multi-vendor**. De gebruiker draait verschillende vendors naast elkaar (Anthropic + MiniMax
> + Codex …), niet meerdere accounts binnen één vendor. Gevolg: subscription = `{cli, provider}`
> blijft; fase 1 zoals hieronder volstaat; de conditionele spike
> [`spike-same-vendor-multi-account-isolation.md`](./spike-same-vendor-multi-account-isolation.md)
> is afgesloten als NO-GO en de C1–C4-decompositie wordt niet geopend. Nul nieuwe kaarten.

Eén vraag bepaalt de exacte vorm van de pool en verdient bevestiging vóór fase 1
gebouwd wordt:

> **Draai je meerdere accounts binnen dezelfde vendor (bv. twee Anthropic-abonnementen
> naast elkaar), of steeds verschillende vendors (Anthropic + MiniMax + Codex + …)?**

- **Verschillende vendors** (waarschijnlijkste lezing, en wat de code vandaag modelleert
  via `default_agent`/`default_provider`): fase 1 zoals hierboven volstaat — de
  subscription = een {cli, provider}-paar.
- **Meerdere accounts binnen één vendor** (bv. 2× Anthropic Max): dit modelleert de code
  **niet** — Claude Code authenticeert via één `~/.claude/.credentials.json` (OAuth). Dan
  is er een extra, zwaardere bouwstap nodig: per-sessie credential-/HOME-isolatie zodat
  twee Anthropic-accounts parallel kunnen draaien. Dat verandert fase 1a/1b substantieel
  en verdient een eigen spike vóór commitment.

Deze analyse dekt de eerste (vendor-diverse) lezing volledig; de tweede is als expliciet
risico/afhankelijkheid gemarkeerd i.p.v. stilzwijgend aangenomen.

## 8. Vervolgkaarten (aangemaakt 2026-07-13 via review-kaart `ce4d2fe0…`)

> **Update 2026-07-13:** deze vervolgkaarten zijn oorspronkelijk *aanbevolen maar niet
> aangemaakt* — een analyse-leaf-spike levert per ontwerp alleen dit beslisdocument op en
> spawnt geen kaarten. Een opvolgende review-kaart (`ce4d2fe0…`) heeft ze alsnog op het
> Backlog aangemaakt, met de fase-DAG als plan-attachment. De open fork uit §7 is beslist
> op **vendor-diverse** (best-effort keuze op verzoek van de gebruiker); het same-vendor-
> multi-account-alternatief is als conditionele spike (#5) bewaard i.p.v. aangenomen.


1. **Globale actieve-subscription-override** (fase 0) — bord-brede pin + selector,
   precedentie boven kolom-defaults, backward-compat (unset = huidig gedrag).
2. **`SubscriptionUsageProvider` afmaken** (fase 1a) — implementeer de ontworpen
   abstractie uit `subscriptions.md`; lever per subscription een genormaliseerde
   `{beschikbaar, drempel-gebruikt, bron, betrouwbaarheid}`.
3. **Subscription-pool + `pick_subscription()`-router** (fase 1b, hangt af van #2) —
   pool-config + persistentie + dispatch-integratie op de bestaande precedentie.
4. **Spillover-bij-limiet** (fase 2, hangt af van #3) — drempel-tak in de router die de
   per-provider pause proactief maakt.
5. *(Voorwaardelijk, alleen als §7 "meerdere accounts binnen één vendor" is)* — spike
   per-sessie credential-/HOME-isolatie voor parallelle same-vendor-accounts. **Spike
   opgeleverd 2026-07-13:** [`spike-same-vendor-multi-account-isolation.md`](./spike-same-vendor-multi-account-isolation.md)
   — conditionele GO (isolatie is goedkoop via `CLAUDE_CONFIG_DIR` op het bestaande
   `spawn.py`-`-e`-injectiepunt). **~~Openstaand~~ → BESLIST 2026-07-14: A / vendor-diverse
   (§7). Spike afgesloten als NO-GO; C1–C4 niet geopend.**
