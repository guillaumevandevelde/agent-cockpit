---
title: "Beslissing: orchestratie-substraat — tmux + CLI-scraping vs. Claude Agent SDK / headless"
type: decision
status: decided
---

# Beslissing: orchestratie-substraat — tmux + CLI-scraping vs. Claude Agent SDK / headless

**Datum:** 2026-07-11
**Status:** besloten (read-only spike; geen implementatie in deze kaart)
**Kaart:** _zie doc — geen hex-id in dit beslisdoc vastgelegd_
**Uitkomst:** **Incrementeel abstraheren** — niet migreren en niet bevriezen. Headless/gestructureerd transport náást tmux; tmux blijft default voor human-in-the-loop.

**Trigger:** kanban-spike "orchestratie-substraat" — kind-kaart van de
tech-stack-evaluatie t.o.v. het platformdoel (parent `fa76d74a`). Zusje-docs:
`headless-session-retro-decision.md`, `spike-claude-code-model-switching.md`.

**TL;DR:** **Incrementeel abstraheren, niet migreren en niet bevriezen.** De
tmux + interactieve-CLI-aanpak is niet "fout" — hij levert precies één eigenschap
die het platformdoel expliciet eist (een *echte* CC-sessie die een mens live kan
overnemen) en die geen enkel headless-alternatief gratis teruggeeft. Maar de
*observability* hangt voor een deel aan terminal-scraping, en dát is de brosse
kern die het doel ("robuust, transparant, agent-onafhankelijk") ondermijnt. De
juiste zet is de **spawn-transport uitbreiden met een headless/SDK-variant** via
de al bestaande `SpawnTransport`-Protocol + `agentic_cli`-laag, gericht op de
*autonoom-gedispatchte* sessies, terwijl tmux de default blijft voor
interactief/human-in-the-loop werk. De hoogste-leverage eerste stap is niet het
spawn-model maar het **weghalen van de scraping-residu uit de observability-laag**.

---

## 1. Vraagstelling

De kern van het platformdoel is autonome multi-agent-orkestratie. Die draait nu
op tmux `send-keys` + subprocess-gespawnde interactieve `claude`-CLI in
git-worktrees, met terminal-scraping voor een deel van de sessiestatus. De vraag:
is dit het ideale substraat t.o.v. het doel, met de Claude Agent SDK /
headless-modus (`claude -p --output-format stream-json`) als belangrijkste
alternatief?

Dit is een beslissings-spike. Geen codewijziging behalve dit doc.

## 2. Huidige aanpak (read-only geverifieerd)

### 2.1 Hoe een sessie ontstaat — tmux + interactieve TUI

De dispatcher claimt een Todo/Analysis-kaart *als de sessie die 'm gaat doen*
(claim-before-spawn), en spawnt via een pluggable `SpawnTransport`:

- `dispatch.make_worktree_transport` → `runs.spawn.spawn_session` →
  `subprocess.run(["tmux", "new-session", "-d", "-s", <name>, "-c", <worktree>,
  <env-flags>, <shell_command>])`.
- Het `shell_command` is de door `agentic_cli` gebouwde command line, bv. voor
  Claude Code: `claude --worktree <name> --dangerously-skip-permissions <prompt>`
  (`claude_code.ClaudeCodeCli.build_spawn_command`). De **volledige prompt is een
  argv naar een interactieve TUI** — geen structured input-kanaal.
- De sessie-identiteit is één ~20-char naam die tegelijk (a) de tmux-sessienaam,
  (b) het `agent:<name>` claim-label, (c) de git-branch én (d) de worktree-dir is
  (`dispatch._mint_session_name`, `<=20` char discipline). Alle vier moeten exact
  gelijk blijven, anders breekt cleanup — een gedocumenteerde bron van bugs
  (collision-fallback in `spawn._session_name_for` die stilletjes hernoemt en de
  claim wees achterlaat).

### 2.2 Hoe status wordt waargenomen — hybride push + scrape

Belangrijk nuance-punt: de observability is **geen pure scraping**. Er zijn twee
kanalen, en alleen het tweede is bros:

**Kanaal A — CC-hooks (push, semi-structured).** Claude Code POST't
hook-events (`PreToolUse`, `PostToolUse`, `Notification`, `Stop`, `SessionEnd`,
…) naar `presence_service.process_event`. Dit is de *goede* helft: structured
JSON, push-based, dekt de gewone sessievoortgang (welke tool, welk bestand,
"waiting for input"). De pane-attention-feature (`pane-attention-spec.md`) hangt
`$TMUX_PANE` aan de hook zodat Presence en de Bridge op exact dezelfde pane
joinen.

**Kanaal B — terminal-scraping (poll, bros).** Voor precies díe signalen die
de hooks *niet* leveren, leest de code de zichtbare pane-tekst:

| Plaats | Wat wordt gescraped | Waarom |
|---|---|---|
| `scheduling/tmux_inject.py` `wait_for_pane_ready` | box-drawing chars (`─ ╭ ╰`) als "TUI is klaar voor input" | vóór `send-keys` injectie, anders wordt de eerste keystroke mid-render gedropt |
| `kanban/dispatch.py` `_capture_pane_content` + `_is_rate_limited_session` | laatste 20 regels op 429/limiet-substrings | de reaper detecteert een sessie die na een rate-limit voor altijd open+idle blijft hangen |
| `services/runs/discovery.py` `capture_pane_preview` | volledige pane-tekst (ANSI) | live preview in CC Bridge / runs-API (xterm.js) |
| `scheduling/auto_resume.py` | pane-tekst op limiet-notificaties | idem 429-detectie langs de hook-kant |

**Liveness** wordt bepaald met `tmux list-sessions` (`dispatch._live_sessions`,
`spawn._running_session_names`): een claim is "levend" ⇔ de tmux-sessie bestaat.
`session_registry.mark_spawned` dekt het gat tussen spawn en het eerste
hook-event (een sessie die op de eerste `claude`-invocatie al een 429 krijgt,
laat de pane open maar vuurt nooit een hook — zonder `mark_spawned` zou de reaper
'm nooit als dood-bij-aankomst herkennen).

### 2.3 Welke code bestaat grotendeels om deze brosheid op te vangen

Dit is de kern van het argument: een substantieel deel van de codebase is
*compensatie* voor het feit dat we tekst uit een terminal moeten lezen i.p.v.
gestructureerde events te consumeren:

- **Fail-open `None`-semantiek overal.** `_capture_pane_content`,
  `_live_sessions`, `tmux_inject._capture_pane` retourneren allemaal `None`/lege
  set bij een *ambigue* tmux-hiccup, met uitgebreide commentaren waarom: één
  transient `tmux`-fout mag nooit als "alle sessies dood" worden gelezen, anders
  reap't de dispatcher levende claims. Dat is defensief programmeren rond een
  onbetrouwbaar observatiekanaal.
- **Dead-on-arrival machinerie.** `DEAD_ON_ARRIVAL_SECONDS`,
  `MAX_DISPATCH_FAILURES`, `_release_dead_claim`, `mark_spawned` — een hele
  toestandsmachine om te onderscheiden "spawn-target is stuk" van "sessie deed
  echt werk en crashte", omdat we dat niet uit een return-code kunnen aflezen.
- **Rate-limit als substring-match.** `_is_rate_limited_session` matcht
  well-known 429-strings in pane-tekst i.p.v. een getypeerde API-fout te vangen;
  `auto_resume` doet hetzelfde langs de andere kant, en beide moeten *in sync*
  gehouden worden (gedeelde detector) om niet te driften.
- **Readiness-race.** `wait_for_pane_ready` + `settle_s` + `-l`
  (literal send-keys) bestaan enkel omdat je in een render'ende TUI typt i.p.v.
  een bericht via een API in te dienen.
- **Identiteits-brug.** De hele pane-attention-spec (`$TMUX_PANE`-join) bestaat
  omdat Bridge (tmux/pane-id/pid) en Presence (Claude session_id) verschillende
  identiteiten hebben — een probleem dat in een SDK-wereld niet bestaat, daar
  ken je de sessie-handle direct.
- **`session-problem-scan`-skill.** Een aparte on-demand sweep voor
  gecrashte/vastgelopen sessies (orphaned `agent:*`-claims zonder levende tmux,
  herhaalde transcript-errors) — nodig omdat er geen betrouwbaar
  proces-lifecycle-signaal is.

### 2.4 Faalmodi (samengevat)

1. Sessie sterft vóór het eerste hook-event → onzichtbaar zonder `mark_spawned`.
2. 429/limiet laat pane open+idle → alleen te vangen via pane-scraping.
3. Naam-collision/truncatie → stille rename, verweesde claim, worktree-lek.
4. `send-keys` racet de TUI-render → gedropte eerste keystroke.
5. Bridge↔Presence identiteitsmismatch → aparte join-machinerie nodig.
6. Ambigue `tmux`-hiccup → risico op reapen van levende claims (vandaar fail-open).

## 3. Het alternatief: Claude Agent SDK / headless

`claude -p --output-format stream-json` (en de programmatische Agent SDK) draaien
een agent-turn **zonder TUI, zonder tmux, zonder scraping**. Je krijgt een stroom
getypeerde JSON-events terug (assistant-messages, `tool_use`, `tool_result`,
`result` met usage/kosten, en getypeerde fouten incl. rate-limits). Cockpit
gebruikt de headless-modus vandaag al voor one-shot queries
(`dispatch.refresh_claude_model_options_sync` draait `claude -p "/model"`), en de
`headless-session-retro-decision.md` heeft een headless `claude -p`-reviewer
onderzocht (en bewust afgewezen — maar om baten/kosten-redenen voor *interactieve*
sessies, niet omdat de techniek niet werkt).

## 4. Vergelijking op de gevraagde assen

### 4.1 Robuustheid (geen terminal-scraping)
**SDK/headless wint duidelijk.** Elk van de scrape-punten uit §2.3 verdwijnt of
wordt triviaal: readiness bestaat niet (je dient een bericht in via een API i.p.v.
te typen in een render'ende TUI), rate-limit wordt een getypeerde fout i.p.v. een
substring-match, liveness wordt een proces-/stream-handle i.p.v.
`tmux list-sessions`, en de dead-on-arrival-toestandsmachine krimpt tot "de
subprocess exit'te met code X". De hele fail-open-defensie rond ambigue
tmux-hiccups is dan niet meer nodig.

### 4.2 Agent-onafhankelijkheid (CLAUDE.md-kernprincipe)
**Genuanceerd — geen eenduidige winnaar, en makkelijk verkeerd ingeschat.**
- De *Claude Agent SDK* is Anthropic-specifiek; er blind op bouwen zou
  agent-onafhankelijkheid op protocol-niveau juist *verminderen*.
- Maar de "onafhankelijkheid" van het huidige tmux-substraat is ondiep: de
  `agentic_cli`-laag kan elk *binary* in een tmux-pane spawnen (claude-code,
  codex-cli, copilot-cli, open-code, mimo-code hebben adapters), maar de
  *observability* (presence-hooks, pane-attention, ready-markers) is de facto
  CC-specifiek. Vandaag geldt: "spawn elk binary in tmux" maar "alleen Claude
  Code is echt observeerbaar".
- De eerlijke framing: headless ruilt de ene soort onafhankelijkheid
  (elke-TUI-in-tmux) voor de andere (een gestructureerd run-protocol per agent).
  De juiste plek om dit te absorberen is de **capability-matrix** in
  `agentic_cli/`: elke CLI declareert of hij een headless structured-event-modus
  ondersteunt (Claude Code: `-p --output-format stream-json`; Codex/OpenCode via
  hun eigen equivalent). Zo blijft agent-onafhankelijkheid een *eigenschap van de
  abstractielaag*, niet van een hard-coupling aan de Anthropic-SDK.

### 4.3 Structured events vs. text-scraping
**Deels al binnen.** Het hook-kanaal (§2.2-A) is nú al de "goede" helft:
structured push-events voor gewone voortgang. Het scraping-residu (§2.2-B) is de
*rest* — precies de signalen die de hooks niet leveren (readiness, 429,
live-preview). Een headless stream-json-run levert álle drie gestructureerd. De
belangrijke observatie: je hoeft niet het hele spawn-model te vervangen om deze as
te winnen — je kunt het scraping-residu vervangen door stream-json-parsing
*terwijl je op tmux blijft* (zie §6, stap 1).

### 4.4 Migratiekost
**Hoog voor een big-bang, laag voor het incrementele pad.** Het tmux-substraat is
load-bearing over `runs/`, `scheduling/`, `presence`, `kanban/dispatch`,
`cc_bridge`. Een volledige migratie raakt vrijwel al die modules
tegelijk — hoog risico, geen tussentijdse waarde. Het incrementele pad (transport
naast transport, capability-gated) levert waarde per slice en houdt tmux als
fallback. De `SpawnTransport`-Protocol + de bestaande transport-keuze
(`worktree` | `sandcastle`, device-local in `KanbanMeta`) is precies het
uitbreidingspunt dat dit goedkoop maakt.

### 4.5 Verlies van "echte CC-sessie in tmux die een mens kan overnemen"
**De sterkste reden om tmux te behouden — direct gekoppeld aan het
transparantie-doel.** De tmux-pane is een *echte, attachbare* terminal: een mens
kan `tmux attach`en en een vastgelopen of interessante sessie live overnemen, 'm
in xterm.js volgen via de Bridge, en berichten injecteren (scheduled-messages,
pane-attention). Een headless/SDK-sessie is een opaak proces — je zou de
"bekijk & stuur"-UX bovenop de event-stream moeten *herbouwen*, en echte
interactieve overname (live in de agent typen) gaat verloren of moet worden
nagebouwd via de SDK-input-streaming. Deze eigenschap bedient het CLAUDE.md-
principe "volledige transparantie" rechtstreeks en is het beslissende argument om
tmux niet weg te gooien — in elk geval niet voor human-in-the-loop werk.

## 5. Aanbeveling

**Incrementeel abstraheren via de bestaande `SpawnTransport` + `agentic_cli`-laag.
Niet migreren, niet bevriezen.** Concreet, in volgorde van leverage:

1. **Haal eerst het scraping-residu uit de observability-laag** (§2.2-B), niet het
   spawn-model. Vervang de 429-pane-scan en de readiness-scrape door
   gestructureerde signalen waar beschikbaar. Dit kan al onder tmux (parse
   stream-json, of leun harder op hooks) en ontkoppelt "hoe we observeren" van
   "hoe we spawnen" — de hoogste waarde tegen het laagste risico.
2. **Introduceer een headless/SDK-transport** als derde `SpawnTransport` naast
   `worktree` (tmux) en `sandcastle`, eerst gericht op *autonoom-gedispatchte*
   sessies waar geen menselijke overname wordt verwacht en structured events het
   meest opleveren. **Houd tmux de default** voor interactief/human-attachable
   werk (§4.5).
3. **Maak agent-onafhankelijkheid een eigenschap van de capability-matrix**: elke
   CLI declareert of hij een headless structured-event-modus heeft, zodat de
   headless-transport nooit hard aan de Anthropic-SDK koppelt.

Netto krijg je: de human-takeover-eigenschap blijft waar hij telt (interactief),
robuuste structured events waar ze tellen (autonome dispatch), en
agent-onafhankelijkheid via de abstractielaag i.p.v. een vendor-lock.

Waarom niet **bevriezen**: het scraping-residu is een terugkerende bron van
brosheid (§2.3–2.4) die frontaal botst met "robuust + transparant".
Waarom niet **volledig migreren**: dat gooit de enige eigenschap weg die het
platformdoel expliciet eist (§4.5) en betaalt een big-bang-migratiekost zonder
tussentijdse waarde (§4.4).

## 6. Voorgestelde vervolgkaarten (tekst; niet in deze kaart aangemaakt)

> Deze spike maakt géén kanban-kaarten aan. Onderstaande zijn voorstellen die een
> mens kan prioriteren en op het bord kan zetten.

1. **[spike/analysis] Prototype headless stream-json-transport achter
   `SpawnTransport`.** Draai één autonoom-gedispatchte executor-kaart via
   `claude -p --output-format stream-json` i.p.v. tmux, achter de bestaande
   transport-keuze. Meet tegen het tmux-pad op: betrouwbaarheid van
   liveness/exit-detectie, 429-afhandeling, en of worktree-lifecycle + claim-
   cleanup nog kloppen zonder tmux-sessienaam als spil. Lever een go/no-go +
   gescopete implementatiekaarten.
2. **[refactor] Vervang de pane-scraping-observability door structured signalen.**
   Haal `_is_rate_limited_session` (reaper) en `wait_for_pane_ready` (injectie)
   weg ten gunste van getypeerde events/return-codes waar beschikbaar; behoud de
   tmux-fallback en de fail-open `None`-semantiek voor het interactieve pad.
   Onafhankelijk van kaart 1 uitvoerbaar (kan onder tmux blijven).
3. **[feature] `headless_run`/`structured_events`-capability in `agentic_cli`.**
   Voeg de capability toe aan de matrix (`capabilities.py`) en laat elke CLI-
   adapter declareren of/hoe hij een headless structured-event-modus ondersteunt.
   Voorwaarde voor een agent-onafhankelijke transport uit kaart 1.
4. **[analysis] Human-takeover-UX voor headless sessies.** Bepaal wat "bekijken &
   overnemen" wordt als een sessie geen tmux-pane meer heeft: sturen via
   SDK-input-streaming vs. tmux behouden als de interactieve transport. `depends_on`
   kaart 1 (consumeert de prototype-bevindingen over wat de event-stream wél/niet
   biedt).

## 7. Bewust buiten scope
- **Het hook-kanaal vervangen.** De presence-hooks zijn al de goede,
  structured helft (§2.2-A) en blijven bruikbaar ongeacht het spawn-model — geen
  reden ze aan te raken.
- **Sandcastle/podman-transport.** Orthogonaal: dat is container-*isolatie*, een
  andere as dan tmux-vs-headless. Een headless-run kan later evengoed in een
  sandbox draaien.
- **De LLM-provider-switch** (Anthropic/Bedrock/MiniMax via `provider_env.py`,
  zie `spike-claude-code-model-switching.md`) — losstaand van het substraat.
