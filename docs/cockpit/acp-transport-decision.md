# Beslissing: ACP (Agent-Client Protocol) als gestructureerd transport achter `SpawnTransport`

**Datum:** 2026-07-14
**Status:** besloten (read-only spike; geen implementatie in deze kaart)
**Trigger:** kanban-spike "[spike] ACP als gestructureerd transport achter `SpawnTransport`" —
kind-kaart uit [`openhands-analyse.md`](./openhands-analyse.md) §4.1 + §7.1 (hoogste-leverage
overname). Behandelt bewust **één** beslissing samen met de headless-transport-vervolgkaart uit
[`orchestration-substrate-decision.md`](./orchestration-substrate-decision.md) §6 (kaart 1).

**Verwant:**
[`orchestration-substrate-decision.md`](./orchestration-substrate-decision.md) (tmux-scraping vs.
gestructureerde events — de as waarop deze beslissing rust),
[`openhands-analyse.md`](./openhands-analyse.md) (§4.1 introduceerde ACP als overname-kandidaat),
[`build-prioriteiten-analyse.md`](./build-prioriteiten-analyse.md) (§3.3 "tweede
executor-provider"-hedge),
[`spike-claude-code-model-switching.md`](./spike-claude-code-model-switching.md) (provider-switch).

---

## TL;DR

**Conditionele GO op een gestructureerd headless-transport — NO-GO op ACP als het *eerste*
transport.** De richting uit `orchestration-substrate-decision.md` §5 staat: introduceer een
headless/gestructureerd transport náást tmux, gericht op autonoom-gedispatchte sessies, met tmux
als default voor human-in-the-loop. De vraag van deze kaart is *welke concrete invulling* dat
transport krijgt: ACP (JSON-RPC 2.0, externe multi-vendor adapters) of een per-CLI
stream-json-parser die we zelf bezitten.

De aanbeveling is genuanceerd maar sluit de vraag:

1. **Bouw de eerste slice met de per-CLI `claude -p --output-format stream-json`-parser**, niet
   met ACP. Dat is de exacte `orchestration-substrate-decision.md` §6-kaart 1; het is de modus die
   Cockpit vandaag al aanroept (`refresh_claude_model_options_sync` draait `claude -p "/model"`),
   we bezitten de parser volledig, en er is geen extra externe protocol- of adapter-binary-
   dependency.
2. **Ontwerp het interne event-model ACP-*isomorf***: getypeerde events (message-chunk, tool-call,
   plan-update, permission-request, usage/result, error) in de vorm die ACP's `session/update`
   ook hanteert. Zo is ACP een *kandidaat-implementatie van dezelfde capability*, geen aparte
   architectuur.
3. **Stel de échte ACP-adaptertransport uit tot de tweede-executor-provider-onboarding** (de
   `build-prioriteiten-analyse.md` §3.3-hedge), en poort die beslissing op de volwassenheid van
   dié CLI's ACP-adapter versus zijn native stream-json. Op dat moment verdient ACP zijn kost
   (één integratie voor meerdere vendors); vóór dat moment betaal je ACP's integratiekost om een
   Claude-adapter te draaien die dezelfde `claude` inpakt die je rechtstreeks kunt aansturen.

Beide transporten (stream-json én een latere ACP-variant) zijn **opake processen zonder
tmux-pane**. Daarom blijft de attachbare tmux-pane de **default** voor interactief/human-takeover
werk; het gestructureerde transport is *additief* voor autonome dispatch — geen big-bang-migratie
(§5 hieronder).

---

## 1. Vraagstelling

Bouwen we per CLI een eigen stream-json-parser (de huidige `agentic_cli`-lijn), of adopteren we
**ACP** — het Agent-Client Protocol (JSON-RPC 2.0, met bestaande adapters voor Claude Code, Codex
en Gemini CLI) — als het gestructureerde run-transport achter de bestaande `SpawnTransport`-seam?

De kaart eist expliciet dat dit **één** beslissing is, gedeeld met de headless-transport-
vervolgkaart uit `orchestration-substrate-decision.md` §6 (kaart 1: "Prototype headless
stream-json-transport achter `SpawnTransport`"). Dit doc verenigt beide sporen: het is niet
"ACP-transport" *versus* "headless stream-json-transport" als twee losse projecten, maar één
transport-seam met twee kandidaat-implementaties.

Read-only beslissings-spike. Geen codewijziging behalve dit doc.

## 2. De seam die we vullen (read-only geverifieerd)

`SpawnTransport` is een smal `Protocol` (`backend/app/kanban/dispatch.py:1169`):

```python
class SpawnTransport(Protocol):
    def __call__(self, *, directory: str, prompt: str, session_name: str,
                 cli_id: str = "claude-code", provider: str = "anthropic",
                 model: str | None = None) -> dict: ...
```

Vandaag bestaan twee implementaties: `make_worktree_transport` (worktree + tmux via
`runs.spawn.spawn_session`, `dispatch.py:1184`) en de sandcastle-variant. De dispatcher kiest per
kaart/project (`get_transport_for_project`, `get_transport_for_card`). Een derde,
headless/gestructureerd transport hangt hier als sibling — de seam is precies het goedkope
uitbreidingspunt dat `orchestration-substrate-decision.md` §4.4 al identificeerde.

Twee vaststellingen die de kostenafweging sturen:

- **Cockpit draait headless al**: `refresh_claude_model_options_sync` (`dispatch.py:263`) roept
  `claude -p "/model"` aan en parse't de output. De stap naar `claude -p --output-format
  stream-json` als transport is een uitbreiding van iets bestaands, geen nieuw substraat.
- **Cockpit spreekt al JSON-RPC**: `mcp_server_test_service.py` en `kanban/mcp_health.py` doen
  JSON-RPC over stdio/SSE. ACP's JSON-RPC-machinerie is dus niet vreemd terrein — maar dat maakt
  ACP goedkoper, niet gratis (zie §4).

De observability-context uit `orchestration-substrate-decision.md` §2.3 is de reden dát we dit
willen: een substantieel deel van de codebase is *compensatie* voor terminal-scraping
(fail-open `None`-semantiek, dead-on-arrival-machinerie, 429-substring-match, readiness-race).
Een gestructureerd transport levert liveness/exit/rate-limit/usage als getypeerde events en ruimt
dat residu op. Dat voordeel geldt **gelijk** voor stream-json én ACP; het is geen
onderscheidende as tussen de twee.

## 3. De drie opties, expliciet afgezet

| As | (a) Per-CLI stream-json-parser | (b) Headless stream-json (§6 kaart 1) | (c) ACP als transport |
|---|---|---|---|
| **Wat het is** | Per CLI een eigen parser in `agentic_cli`; elke CLI declareert een headless structured-event-modus. | De Claude-specifieke *eerste instantie* van (a): `claude -p --output-format stream-json`. | Eén JSON-RPC 2.0-client die tegen een ACP-*adapterbinary* per CLI praat (`claude-code-acp`, Gemini `--experimental-acp`, Codex-adapter). |
| **Relatie** | De algemene vorm. | = (a) voor Claude, de eerste slice. | Alternatieve *implementatie* van dezelfde capability. |
| **Agent-onafhankelijkheid** | Per CLI een parser die wij bezitten; N parsers. | 1 (Claude). | Eén protocol; onafhankelijkheid als *protocol-eigenschap* — maar in de praktijk nog steeds per-adapter, en de adapter is externe code. |
| **Eigenaarschap / controle** | Volledig van ons. | Volledig van ons. | Gedeeld: protocol-spec (Zed e.a.) + adapter-binary (externe maintainer). Extra installatie/versie-as. |
| **Volwassenheid / risico** | `stream-json` is een stabiele CC-modus, al in gebruik. | Idem. | Jong protocol (2025–2026), spec in beweging; adapter-binaries variëren in rijpheid. |
| **Billing-risico** | Rijdt op abonnement via de echte CLI. | Idem. | Idem — maar de repricing-pauze van juni 2026 raakte expliciet `claude -p`/SDK/**ACP** onder abonnement (`claude-agent-acp#658`); zelfde risico, iets scherper geconcentreerd. |
| **Permission-model** | `--dangerously-skip-permissions` (huidig). | Idem. | ACP heeft `session/request_permission` als getypeerde callback — **potentieel** een echte gating-haak (facet D), niet slechts alles-uit. |
| **Plan/tool-events** | Zelf mappen uit stream-json. | Idem. | `session/update` levert plan-entries + tool-calls first-class; mapt fraai op kanban. |
| **Interop-upside** | Geen. | Geen. | Interoperabel met Zed/VS Code/JetBrains als ACP-*clients*. Maar Cockpit is de orchestrator (zou zelf de *client* zijn die agents consumeert), geen editor — de interop-winst is vooral "hergebruik bestaande adapters", niet "anderen embedden ons". |
| **Human-takeover (tmux)** | Verdwijnt (opaak proces). | Verdwijnt (opaak proces). | Verdwijnt (opaak proces). **Geen** van de drie behoudt de attachbare pane — zie §5. |
| **Eerste-slice-kost** | Parser + subprocess-lifecycle + event→presence-mapping. | Idem, laagste (modus al in gebruik). | Bovenop dat: JSON-RPC-client, ACP-sessielifecycle (`initialize`/`session/new`/`session/prompt`), permission-callbacks, adapter-binary-provisioning. |

### 3.1 Waarom ACP's multi-vendor-belofte deels illusoir is voor de eerste slice

ACP verkoopt "één integratie, elke agent". In de praktijk:

- Elke CLI heeft nog steeds een **eigen adapter** (`claude-code-acp`, Gemini's ingebouwde
  `--experimental-acp`, een Codex-adapter). Je verplaatst het per-CLI-werk naar een *externe
  maintainer* i.p.v. het te elimineren — met winst (jij onderhoudt de parser niet) én verlies
  (jij controleert de rijpheid/bugfix-cadans niet, en er is een extra binary om te installeren en
  te versioneren).
- Voor de **eerste** CLI (Claude) draait `claude-code-acp` dezelfde `claude` in, met een
  JSON-RPC-laag ertussen. Rauw `claude -p --output-format stream-json` levert dezelfde
  getypeerde events zonder die tussenlaag en zonder adapter-dependency.
- De echte ACP-winst — één client die N vendors afdekt — materialiseert pas bij de **N-de**
  vendor. Dat is exact het moment van de tweede-executor-provider-hedge (§3.3), niet nu.

### 3.2 Waar ACP wél structureel wint (en waarom we het niet weggooien)

- **Getypeerd permission-model** (`session/request_permission`): een echte gating-haak i.p.v.
  `--dangerously-skip-permissions`. Dat raakt facet D (`veilig-bouwen-en-uitleveren.md`,
  `SecretStore`) en is een reden om het event-model ACP-isomorf te ontwerpen.
- **First-class plan/tool-updates**: `session/update` met plan-entries mapt natuurlijk op de
  kanban-decompositie.
- **Ecosysteem-momentum**: Zed/JetBrains/VS Code-investering houdt de adapters levend — relevant
  op termijn, niet als eerste-slice-driver.

Deze winsten rechtvaardigen **ACP-vormige events**, niet **ACP-als-eerste-transport**.

## 4. Waarom "stream-json eerst, ACP later" en niet andersom

1. **Laagste risico, hoogste bekende waarde eerst.** De stream-json-modus is stabiel en al in
   gebruik; hij ruimt het scraping-residu (§2) op met code die wij bezitten. `orchestration-
   substrate-decision.md` §5 zette exact deze volgorde al uit; deze spike bevestigt dat ACP die
   volgorde niet omkeert.
2. **Geen premature externe dependency.** ACP koppelt ons aan (a) een protocol-spec in beweging
   en (b) een adapter-binary per CLI. Beide zijn moving parts met een billing-risico dat juni
   2026 concreet werd. Die kost is verdedigbaar zodra hij een tweede vendor ontsluit — niet om
   Claude tegen zichzelf in te pakken.
3. **De seam maakt het omkeerbaar.** Omdat `SpawnTransport` een smal `Protocol` is en het
   event-model ACP-isomorf wordt ontworpen, is een latere ACP-backed transport een *nieuwe
   sibling-implementatie*, geen herschrijving. We verliezen niets door te wachten.
4. **We consumeren agents, we worden niet geconsumeerd.** ACP's grootste interop-troef (editors
   als clients) is voor Cockpit grotendeels irrelevant: wij zijn de orchestrator/client, geen
   editor die een agent embedt. De asymmetrie verkleint ACP's netto-voordeel voor onze seam.

Dit is geen "nooit ACP". Het is: **structured transport = ja, nu; stream-json = de eerste
implementatie; ACP = een gepoorte toekomstige implementatie van dezelfde capability, geactiveerd
door de tweede-provider-hedge.**

## 5. Human-takeover blijft — expliciet (hard acceptance-criterium)

De attachbare tmux-pane is Cockpit's transparantie-troef en een expliciete CLAUDE.md-eis
(`orchestration-substrate-decision.md` §4.5, `openhands-analyse.md` §3). **Geen** van de drie
transportopties behoudt die pane: stream-json én ACP draaien een opaak proces zonder terminal die
een mens live kan overnemen.

Daarom is de beslissing nadrukkelijk **additief, geen migratie**:

- **tmux (`make_worktree_transport`) blijft de default** voor interactief/human-in-the-loop werk.
  Een mens kan `tmux attach`en, live overnemen, en berichten injecteren
  (scheduled-messages, pane-attention).
- Het gestructureerde transport (stream-json nu, evt. ACP later) wordt **alleen** gekozen voor
  *autonoom-gedispatchte* sessies waar geen menselijke overname wordt verwacht en getypeerde
  events het meest opleveren — via de bestaande per-kaart/per-project transport-keuze
  (`get_transport_for_card`).
- De vraag "wat wordt bekijken & overnemen als een sessie geen pane heeft?" is een **eigen
  kaart** (§6, kaart 4 hieronder — overgenomen uit `orchestration-substrate-decision.md` §6
  kaart 4), niet iets dat deze transportbeslissing oplost. Tot die UX bestaat, blijven
  human-takeover-kaarten op het tmux-transport.

> **Bijgewerkt 2026-07-15 — die UX bestaat nu.** Kaart 4 is beslist
> ([`human-takeover-headless-decision.md`](./human-takeover-headless-decision.md)) en corrigeert
> de laatste zin hierboven: er is **géén** categorie "human-takeover-kaarten" die op tmux moet
> blijven. Takeover blijkt een **promotie** te zijn, geen transport-keuze — een headless run wordt
> op afroep via `claude --resume <session_id>` een echte, attachbare tmux-pane mét volledige
> historie (gemeten). De aanname in de §3-tabel dat de pane "verdwijnt" geldt over het *proces*,
> niet over de *sessie*: die leeft in de transcript op schijf en is transport-overspannend.

## 6. Gescopete implementatiekaarten (voorstel; niet in deze kaart aangemaakt)

> Deze spike maakt géén kanban-kaarten aan (leaf-spike-conventie, zoals de zusje-docs). De
> onderstaande vier verenigen de vervolgkaarten van `orchestration-substrate-decision.md` §6 met
> de ACP-beslissing hierboven — het is bewust **één** kaartenset, geen tweede transportspoor. Een
> mens prioriteert en zet ze op het bord. Als `orchestration-substrate-decision.md` §6-kaarten al
> op het bord staan, zijn kaart 1–3 hieronder de **verrijking** ervan (ACP-isomorf event-model),
> geen duplicaat.

1. **[refactor] Vervang pane-scraping-observability door structured signalen.** (= §6 kaart 2)
   Haal `_is_rate_limited_session` (reaper) en `wait_for_pane_ready` (injectie) weg ten gunste
   van getypeerde events/return-codes; behoud de tmux-fallback + fail-open `None`-semantiek voor
   het interactieve pad. Tmux-onafhankelijk uitvoerbaar; **hoogste leverage, laagste risico**.
   *Acceptance:* de 429-detectie en readiness-race verdwijnen voor het autonome pad zonder de
   interactieve tmux-observability te breken.

2. **[feature] `structured_events`/`headless_run`-capability in `agentic_cli`.** (= §6 kaart 3,
   verrijkt) Voeg de capability toe aan `capabilities.py` en laat elke CLI-adapter declareren
   of/hoe hij een headless structured-event-modus heeft. **Ontwerp het event-schema ACP-isomorf**
   (message-chunk / tool-call / plan-update / permission-request / usage-result / error) zodat
   een latere ACP-backed implementatie hetzelfde schema hergebruikt. *Acceptance:* de
   capability-matrix classificeert per CLI de headless-modus; het geëmitteerde event-model is
   gedocumenteerd en ACP-isomorf. *Voorwaarde voor kaart 3.* **→ geïmplementeerd:**
   [`structured-events-schema.md`](./structured-events-schema.md).

3. **[spike] Prototype headless stream-json-transport (Claude) achter `SpawnTransport`.**
   (= §6 kaart 1, verrijkt) Draai één autonoom-gedispatchte executor-kaart via `claude -p
   --output-format stream-json` als derde `SpawnTransport`-sibling. Meet tegen het tmux-pad:
   betrouwbaarheid van liveness/exit-detectie, 429-afhandeling, en of worktree-lifecycle +
   claim-cleanup nog kloppen **zonder de tmux-sessienaam als spil** (de vier-in-één identiteit uit
   `orchestration-substrate-decision.md` §2.1). Map de stream-json-events op het ACP-isomorfe
   schema van kaart 2. *Acceptance:* go/no-go op de stream-json-transport met gemeten
   betrouwbaarheid; het worktree/claim-lifecycle werkt zonder tmux-naam-koppeling.
   *`depends_on`: kaart 2.* **→ uitgevoerd (GO):**
   [`headless-stream-json-transport-spike.md`](./headless-stream-json-transport-spike.md).
   Kernbevindingen: een headless run matcht geen van de twee liveness-bronnen in
   `reap_stale_claims` → een **derde** bron is voorwaarde vooraf (anders dispatch-loop); de
   vier-in-één identiteit breekt niet (alleen het liveness-*orakel* is tmux-gebonden); en het
   event-schema van kaart 2 mist een slot voor `rate_limit_event` — juist het event dat de
   429-scrape vervangt.

4. **[analysis] Human-takeover-UX voor headless sessies.** (= §6 kaart 4) Bepaal wat "bekijken &
   overnemen" wordt als een sessie geen tmux-pane meer heeft: sturen via input-streaming
   (stream-json/ACP) vs. tmux behouden als de interactieve transport. *Acceptance:* een besluit
   over de human-takeover-UX voor het autonome pad, of de expliciete keuze om
   human-takeover-kaarten op tmux te houden. *`depends_on`: kaart 3 (consumeert de
   prototype-bevindingen).* **→ besloten (tmux blijft interactief; takeover = promotie via
   `--resume`, geen input-streaming):**
   [`human-takeover-headless-decision.md`](./human-takeover-headless-decision.md).

5. **[spike, GEPOORT — pas activeren bij tweede-executor-provider-onboarding] ACP-adaptertransport
   als sibling.** Wanneer de `build-prioriteiten-analyse.md` §3.3-hedge een tweede executor-
   provider (bv. Codex/Gemini/gemeterde `claude`) onboardt, evalueer dan de **ACP-adapter van díe
   CLI** tegen zijn native stream-json: is de adapter volwassen genoeg om één ACP-client N vendors
   te laten afdekken? Zo ja, implementeer de ACP-backed transport als nieuwe `SpawnTransport`-
   sibling, hergebruikt het ACP-isomorfe event-model van kaart 2, en benut `session/request_
   permission` als getypeerde gating-haak (facet D). *Acceptance:* een go/no-go op ACP-per-vendor,
   gepoort op adapterrijpheid; bij go een ACP-transport die het bestaande event-model hergebruikt.
   **Bewust niet nu** — deze kaart activeert pas met een concrete tweede provider.

## 7. Bewust buiten scope

- **De hook-kanaal-observability vervangen.** De presence-hooks zijn al de goede, structured helft
  (`orchestration-substrate-decision.md` §2.2-A) en blijven bruikbaar ongeacht het transport.
- **Sandcastle/podman-isolatie.** Orthogonaal (container-isolatie ≠ tmux-vs-headless); een
  headless-run kan later evengoed in een sandbox draaien.
- **De LLM-provider-switch** (`provider_env.py`, `spike-claude-code-model-switching.md`) —
  losstaand van het transport-substraat.
- **Volledige ACP-conformiteit als *server*** (Cockpit als ACP-agent die editors embedden). Wij
  zijn de client/orchestrator; ACP-server-zijn is geen doel.

## 8. Bronnen

- ACP / Agent-Client Protocol — [agentclientprotocol.com](https://agentclientprotocol.com),
  Zed's introductie; adapters: `agentclientprotocol/claude-agent-acp` (`claude-code-acp`), Gemini
  CLI `--experimental-acp`, Codex-adapter.
- Anthropic 2026 billing-pauze (raakt `claude -p`/SDK/**ACP** onder abonnement) —
  [zed.dev/blog/anthropic-subscription-changes](https://zed.dev/blog/anthropic-subscription-changes),
  [claude-agent-acp#658](https://github.com/agentclientprotocol/claude-agent-acp/issues/658).
- Interne: `orchestration-substrate-decision.md` (§2.3 scraping-residu, §4.4 seam-kost, §5
  volgorde, §6 kaarten), `openhands-analyse.md` §4.1/§7.1, `build-prioriteiten-analyse.md` §3.3
  (tweede-provider-hedge), `veilig-bouwen-en-uitleveren.md` (facet D / permission-gating).
</content>
</invoke>
