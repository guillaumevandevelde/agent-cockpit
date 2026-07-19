---
title: "Per-persona MCP-tool-allowlist — analyse & beslissing"
type: decision
status: decided
---

# Per-persona MCP-tool-allowlist — analyse & beslissing

**Datum:** 2026-07-15
**Status:** besloten
**Kaart:** `28e1558e…`
**Uitkomst:** **NO-GO — geen vervolgkaart.** De premisse ("alle 19 schemas in élke system-prompt") is achterhaald: Claude Code 2.1.210 defert MCP-schemas achter `ToolSearch`, waardoor de 19 tools **388 tokens** kosten i.p.v. ~4.994 — 1,1% van een 36.660-token baseline; de CLI vangt al ~92%. Het voorgestelde mechanisme bestaat bovendien niet: `--allowedTools`/`--disallowedTools` zijn permissie-poorten, geen schema-filters (`--allowedTools` kost netto **+109** tokens). Alleen een rol-gescopete MCP-mount zou grip geven, voor max ~184 tokens (0,5%) — tegenover permanente plumbing en een faalmodus die de leaf-spike-analyst (die de unie van beide rolsets nodig heeft) op de ship-stap breekt. Ook de matrix zelf is minder scheidbaar dan aangenomen: de **verplichte** `session-retro` roept `create_card` aan. Misfire-preventie hoort server-side (bestaand `{"error": …}`-patroon), niet in een allowlist. **Heropenen** alleen als de meting in §7 van 388 → ~5.000 springt.

> **Type:** analyse/beslisdoc (leaf spike). Bron-kaart: *"[analysis] Per-persona
> MCP-tool-allowlist voor gedispatchte sessies"* (`28e1558e0edf457caee4629870deba3e`),
> afkomstig van [`token-optimization-analysis.md`](./token-optimization-analysis.md) §4 **R3**.
>
> Verwant: [`kanban-conventions.md`](./kanban-conventions.md),
> [`multi-agent-kanban.md`](./multi-agent-kanban.md).

## TL;DR — **NO-GO**

R3 stelde voor de 19 `cockpit-kanban`-MCP-tools per rol te filteren, omdat "alle 19
schemas in de system-prompt van élke sessie landen". **Die premisse is empirisch onjuist
voor de geïnstalleerde CLI (Claude Code 2.1.210).** Claude Code laadt MCP-toolschemas
niet meer eager: ze worden *deferred* achter `ToolSearch` en kosten alleen nog een naam
in de system-prompt.

Gemeten in exact de configuratie die een gedispatchte sessie draait:

| | Tokens |
|---|---|
| Volledige sessie-baseline (mét kanban-MCP) | **36.660** |
| Dezelfde sessie zonder énige MCP-server | **36.272** |
| **Werkelijke kost van alle 19 kanban-tools** | **388** (≈ 20/tool) |
| Kost als de schemas wél eager geladen werden | ~4.994 |

De optimalisatie die R3 wil, is dus **al gedaan** — door de CLI, niet door ons. Ze vangt
al ~92% (4.994 → 388). Wat overblijft is **388 tokens op een 36.660-token baseline: 1,1%**.
Een perfecte rol-allowlist zou daarvan hooguit de helft schrappen (**~184 tokens, 0,5%**).

Bovendien: de twee CLI-flags die R3 als mechanisme aanwees (`--allowedTools` /
`--disallowedTools`) zijn **permissie-poorten, geen schema-filters** — ze verwijderen geen
enkel schema. `--allowedTools` maakt het zelfs **duurder** (+109 tokens; de regels zelf
worden als prompttekst geïnjecteerd).

En de rol-matrix zelf blijkt veel minder scheidbaar dan R3 aannam: de **verplichte**
`session-retro` aan het eind van élke dispatch roept `create_card` aan — precies een van
de tools die R3 als "executor gebruikt dit nooit" bestempelde.

Drie onafhankelijke redenen, elk op zich voldoende. Geen vervolgkaart. Zie [§6](#6-gono-go).

## 1. Vraag & scope

R3's claim, letterlijk: *"Alle 19 schemas landen in de system-prompt van élke sessie,
ongeacht rol. Een executor roept nooit `add_plan_attachment` of
`create_project_from_intake` aan; een analyst (modus 1) nooit `attach_deliverable`."*
Voorgesteld: onderzoek `--allowedTools` of een rol-gefilterde MCP-toolset.

Dit doc beantwoordt de vier acceptatiecriteria van de kaart: een tool→rol-matrix (§3), het
filtermechanisme + meting (§2, §4), de leaf-spike-complicatie (§5), en een go/no-go (§6).

**Meetopstelling.** Alle metingen draaien in deze repo-worktree, met de echte `.mcp.json`
(`cockpit-kanban` via SSE) — dus exact wat `dispatch.py` een sessie geeft. `~/.claude.json`
heeft **geen** globale `mcpServers`, dus er lekt niets persoonlijks in de meting.
Reproductie in [§7](#7-reproductie).

## 2. Meting — wat kosten 19 schemas werkelijk?

### 2.1 De schemas op zichzelf

Rechtstreeks uit de server (`mcp.list_tools()`, som van `name` + `description` +
`inputSchema`): **19.978 chars ≈ 4.994 tokens**. De zwaarste vier zijn `create_card` (~698),
`create_project_from_intake` (~609), `report_impediment` (~421) en `update_card` (~380).

Dát is het getal waar R3 impliciet op mikte — en het is een reëel bedrag. De vraag is of
een sessie het ook echt betaalt.

### 2.2 Wat een sessie werkelijk betaalt

Totale input (`input + cache_creation + cache_read`) van een minimale `claude -p`-run,
tweemaal herhaald met **identieke** uitkomst (geen ruis):

| Configuratie | Totale input-tokens | Δ |
|---|---|---|
| Mét kanban-MCP (= dispatch-config) | 36.660 | — |
| `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` | 36.272 | **−388** |

**388 tokens voor 19 tools = ~20 tokens per tool.** Dat is een naam plus een fragment —
geen schema. Ter vergelijking: één `create_card`-schema alleen is al ~698 tokens.

### 2.3 Waarom: ToolSearch-deferral

Het `system/init`-event bevestigt het mechanisme: de sessie adverteert **50 tools, waarvan
19 kanban**, en `ToolSearch` staat in de lijst. De schemas worden pas opgehaald wanneer een
tool daadwerkelijk nodig is. Elke sessie in deze repo ziet dit ook direct in haar eigen
`<system-reminder>`: *"The following deferred tools are now available via ToolSearch. Their
schemas are NOT loaded."*

Dit is dezelfde techniek die `token-optimization-analysis.md` §3 al als *"wat doen we goed
(behouden)"* noemde voor de Google-/Atlassian-connectors. De observatie die §3 miste: **de
CLI past 'm ook toe op onze eigen `cockpit-kanban`-server.** R3 en §3 spreken elkaar dus
tegen; §3 had gelijk.

## 3. Tool→rol-matrix (AC1)

Afgeleid uit wat de persona's + de verplicht/aangeboden skills daadwerkelijk aanroepen —
niet uit wat een rol "logisch" zou moeten doen. Skills tellen mee omdat de
session-end-workflow ze injecteert: `session-retro` is **verplicht** (stap 6 van élke
dispatch), `git-ship` en `flag-problem` worden elke sessie aangeboden.

| Tool | Engineer | Analyst modus 1 | Analyst leaf (modus 2) | Waarvandaan |
|---|:--:|:--:|:--:|---|
| `resolve_project_key` | ✅ | ✅ | ✅ | flag-problem, session-retro |
| `list_cards` | ✅ | ✅ | ✅ | flag-problem/retro dedupe-pass |
| `get_card` | ✅ | ✅ | ✅ | context-lookup |
| `create_card` | ✅ | ✅ | ✅ | **session-retro (verplicht)**, flag-problem |
| `comment` | ✅ | ✅ | ✅ | persona's + retro no-op |
| `move_card` | ✅ | ✅ | ✅ | exit-signaal |
| `report_impediment` | ✅ | ✅ | ✅ | alle persona's |
| `attach_deliverable` | ✅ | ❌ | ✅ | git-ship; analyst-persona verbiedt 'm expliciet in modus 1 |
| `add_plan_attachment` | ❌ | ✅ | ✅ | analyst-decompositie + leaf-clausule |
| `open_gate` | ✅ | ❌ | ❌ | engineer-persona |
| `request_review` | ❌ | ✅ | ✅ | analyst-persona (review-kaarten) |
| `update_card` | ~ | ~ | ~ | incidenteel, geen persona schrijft 't voor |
| `create_project_from_intake` | ❌ | ❌ | ❌ | alleen `intake-authoring` (interactief, **niet** gedispatcht) |
| `reopen_card` | ❌ | ❌ | ❌ | menselijke actie |
| `claim_card` / `release_card` | ❌ | ❌ | ❌ | dispatcher; sessie krijgt de kaart al geclaimd |
| `set_resume` / `redispatch_card` | ❌ | ❌ | ❌ | dispatch-interne flows |
| `ping` | ❌ | ❌ | ❌ | diagnostiek |

**De matrix ondermijnt R3's eigen voorbeelden.** R3 noemde `create_card` impliciet als
executor-overbodig ("een executor roept nooit `add_plan_attachment` of
`create_project_from_intake` aan" — met `create_card` in dezelfde geest). Maar de
verplichte `session-retro` filet `[self-improve]`-kaarten via **`create_card`**, en
`flag-problem` doet een dedupe-pass via **`list_cards`** + **`resolve_project_key`**. Een
allowlist gebouwd op de intuïtieve rolverdeling had de retro van élke engineer-sessie
gebroken.

Wat écht per rol exclusief is, is de smalle staart: `add_plan_attachment` (analyst),
`open_gate` (engineer), `attach_deliverable` (niet-modus-1). Negen van de negentien zijn
door *niemand* nodig — maar die negen zijn rol-**onafhankelijk** overbodig, dus daar is
geen *per-persona* mechanisme voor nodig (zie §6).

## 4. Filtermechanisme — bestaat het? (AC2)

Vier kandidaten in Claude Code 2.1.210:

| Mechanisme | Wat het doet | Filtert schemas? | Gemeten |
|---|---|:--:|---|
| `--allowedTools` | permissie-**allow**-regels | ❌ | **36.769** (+109 vs baseline) |
| `--disallowedTools` | permissie-**deny**-regels | ❌ | 36.650 (−10 ≈ ruis) |
| `--tools` | beperkt de **built-in** set | ❌ (raakt MCP niet) | n.v.t. |
| `--strict-mcp-config` + `--mcp-config` | kiest **welke servers** laden | ✅ maar per *server* | 36.272 (−388, álle 19 weg) |

**Conclusie: de flags uit R3 zijn permissie-poorten, geen schema-filters.** Ze bepalen of
een call *mag*, niet of het schema *meegestuurd* wordt. `--allowedTools` is zelfs
contraproductief: de regels landen als extra prompttekst (+109 tokens), dus de "besparing"
is netto negatief.

Het enige mechanisme met echte grip is `--strict-mcp-config`, maar dat werkt op
**server**-granulariteit: alles of niets. Tool-granulariteit binnen één server bestaat niet
als CLI-flag.

### 4.1 Het mechanisme dat wél zou werken — en waarom het niet loont

Omdat `cockpit-kanban` **onze eigen** server is (`FastMCP("cockpit-kanban")`, gemount op
`/kanban-mcp` in `main.py`), kunnen we rol-gescopete mounts maken —
`/kanban-mcp/engineer/sse` met een subset — en dispatch per rol een eigen `--mcp-config`
geven. Technisch haalbaar, volledig in eigen beheer, tool-namen blijven stabiel zolang de
servernaam `cockpit-kanban` blijft.

Het loont alleen niet. De hele taart is **388 tokens**. Engineer mist 9 van de 19 tools →
`9/19 × 388 ≈ **184 tokens**`, oftewel **0,5% van een 36.660-token baseline**. Daarvoor
zou je een tweede mount-tree, een rol→toolset-registry, dispatch-plumbing en een
config-per-rol moeten bouwen en onderhouden — permanent, met een nieuwe faalmodus (§5).
De verhouding kost/baat is niet grensgeval; ze is twee ordes van grootte mis.

## 5. Waarom een rol-matrix te grof is (AC3)

De kaart waarschuwde hiervoor, en de waarschuwing is terecht — sterker nog, **deze sessie
is het tegenvoorbeeld.** De leaf-spike-analyst (modus 2, zie de override in `analyst.md`)
draait onder `card.agent='analyst'` maar heeft de **unie** van beide rollen nodig:
`attach_deliverable` (engineer-only in de naïeve matrix) *en* `add_plan_attachment` +
`create_card` (analyst-only). Een allowlist gekeyd op `"analyst"` had deze sessie —
inclusief het opleveren van dít document — geblokkeerd.

Erger: modus 1 en modus 2 zijn **niet te onderscheiden aan de persona**. Beide laden
`analyst.md`. Het verschil zit in dispatch-state (`analyst_agent_id` gezet of niet). Een
allowlist zou dus niet op de persona kunnen keyen maar op een afgeleide dispatch-conditie —
precies het soort impliciete koppeling dat stilletjes verkeerd gaat zodra iemand de
routing aanpast. De faalmodus is bovendien asymmetrisch en naar: een te enge allowlist
faalt **midden in** een sessie, ná het werk, op de ship-stap — het duurst mogelijke moment.

De kaart adviseerde daarom "begin ruim". Dat advies is intern inconsistent met het doel:
ruim beginnen betekent bijna alles toelaten, en de besparing is al maximaal 184 tokens bij
een *perfecte* allowlist. Een ruime allowlist bespaart nagenoeg niets én draagt nog steeds
het blokkeerrisico.

## 6. Go/no-go

**NO-GO.** Geen vervolgkaart. Drie onafhankelijke gronden:

1. **De premisse is achterhaald.** ToolSearch-deferral vangt al ~92% (4.994 → 388 tokens).
   R3 optimaliseert een probleem dat de CLI heeft opgelost.
2. **Het voorgestelde mechanisme bestaat niet.** `--allowedTools`/`--disallowedTools` zijn
   permissie-poorten; ze strippen geen schemas. `--allowedTools` kost netto **+109 tokens**.
3. **De baat is 0,5%, het risico is een gebroken ship-stap.** Max ~184 tokens op 36.660,
   tegenover permanente plumbing en een faalmodus die leaf-spikes breekt (§5).

**Wat R3 vervangt.** De token-hefboom zit niet in de MCP-tools (388 tokens, 1,1%) maar in
de **~36k baseline** eromheen. De andere kaarten uit dezelfde analyse blijven onverkort
gelden en zijn ordes van grootte groter: `d17b6e6a` (R1, Sonnet-default — 5× input-prijs),
`a738497d` (R2, CLAUDE.md < 200 regels — nu 230). Effort hoort daarheen.

**Niet-token-argument (misfire-preventie), apart gewogen.** R3's tweede motief was
"voorkomt misfires". Dat overleeft de token-analyse niet als *allowlist*-argument: de
persona's verbieden de verkeerde calls al in proza, en §5 laat zien dat een harde
allowlist juist een legitieme sessie-vorm breekt. Zou een misfire zich in de praktijk
voordoen, dan hoort de guard **server-side** — het patroon bestaat al in `mcp_server.py`
(`{"error": "summary_required"}`, `{"error": "invalid_ref"}`): de tool weigert zelf, met
een leesbare fout, zonder het schema te verbergen of de sessie te breken. Dat is een
reactie op waargenomen gedrag, niet op speculatie — en er is nu geen bewijs van zulke
misfires. Geen kaart.

**Wanneer heropenen** (per [`reopen-completed-decision-analysis.md`](./reopen-completed-decision-analysis.md)):
alleen als de deferral verdwijnt. Concrete trigger: de meting in §7 laat de Δ van 388 naar
~5.000 springen (CLI-regressie, of ToolSearch uitgeschakeld). Dan verandert 1,1% in ~13%
en is R3 opeens wél de moeite. Tot dan is dit beslist.

## 7. Reproductie

De meting is één commando; draai 'm als je vermoedt dat de deferral weg is.

```bash
# Δ = de werkelijke kost van alle 19 kanban-MCP-tools. Verwacht ~388.
# Springt dit naar ~5000, dan is de deferral weg → heropen deze beslissing (§6).
tot() { python3 -c "import sys,json;u=json.load(sys.stdin)['usage'];print(u.get('input_tokens',0)+u.get('cache_creation_input_tokens',0)+u.get('cache_read_input_tokens',0))"; }
A=$(claude -p "Reply with exactly: ok" --model sonnet --output-format json | tot)
B=$(claude -p "Reply with exactly: ok" --model sonnet --output-format json \
      --strict-mcp-config --mcp-config '{"mcpServers":{}}' | tot)
echo "met_mcp=$A  zonder_mcp=$B  delta=$((A-B))"
```

Gemeten op 2026-07-15, Claude Code **2.1.210**, worktree `k-analysis-per-69ad`:
`met_mcp=36660  zonder_mcp=36272  delta=388` — tweemaal identiek.

De schema-grootte zelf (~4.994 tokens) komt uit `mcp.list_tools()`; zie §2.1.
