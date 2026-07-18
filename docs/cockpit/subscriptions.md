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
