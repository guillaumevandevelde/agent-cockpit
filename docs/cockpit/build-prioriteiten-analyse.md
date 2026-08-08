---
title: "Bouw-prioriteiten: wat eerst, wat te integreren, wat kan wachten"
type: analysis
status: active
---

# Bouw-prioriteiten: wat eerst, wat te integreren, wat kan wachten

> Kanban-kaart: **`Analyse - Prioriteiten om te bouwen`**
> (`069ad411f4274197bcaa5db7fd5912f6`).
>
> Deze doc is een **beslissings-/prioriteitsanalyse** — geen implementatie.
> Ze kijkt naar `CLAUDE.md` + de doelstelling, de huidige applicatie (code
> én kanban-bord), en een externe ecosysteem-scan (web, juli 2026), en
> beantwoordt drie vragen: (1) **wat is vandaag nog nodig om te bouwen?**
> (2) **welke externe applicaties/tools zijn de moeite om te integreren?**
> (3) **wat moeten we éérst bouwen?**
>
> **Verhouding tot de `[synthese]`-kaart in Todo** (*"Consolideer het
> antwoord: hoe geschikt is Cockpit als app-bouwplatform en wat is
> nodig"*). Die kaart synthetiseert de vier facet-analyses (A inceptie /
> B bootstrap / C portfolio / D veilig-bouwen) *binnen* het
> app-bouwplatform-doel.
>
> **Deze** doc is een niveau breder: ze weegt die app-bouwtrack af tegen
> de rest van het platform (CI-fundament, zelfverbeter-schuld,
> substraat-robuustheid) én tegen de buitenwereld. En levert één geordende
> prioriteitsstack. Waar ze elkaar raken wint de synthese-kaart voor de
> *interne* facetvolgorde; deze doc positioneert die volgorde binnen het
> geheel.

## 1. Bevinding in één alinea (TL;DR)

Het platform is **technisch volwassen en breed** — kanban-autodispatch,
multi-agent decompositie, Agent Mail, worktree-isolatie, Sandcastle en
scheduled-messages draaien of zijn code-compleet. Het zwaartepunt van het
werk dat *nog* nodig is, ligt niet in méér losse features maar in **drie
dingen op rij**.

(P0) het **fundament weer groen krijgen** — CI staat uit door GitHub-billing én
er is bewust géén lokale push-gate, dus er is op dit moment *geen enkel*
kwaliteitsvangnet.

(P1) de **"andere-applicaties"-track end-to-end sluiten** als één dunne
verticale slice (inceptie → bootstrap → één echte externe app op het bord)
i.p.v. de facetten los verder te verbreden.

(P2) **veilig-bouwen (facet D) vóór** we een gebouwde, mogelijk niet-vertrouwde
app daadwerkelijk laten *draaien* (RunService / Preview-URL) — de
buitenwereld leverde in 2026 concrete bewijslast (het Cline-token-exfil-
incident, "Docker is niet genoeg") dat die volgorde niet optioneel is.

Daarboven zweeft één **strategisch risico** dat de economie van het hele
platform raakt: Anthropic's **april-2026-beleid** dat Pro/Max-abonnementen
loskoppelt van *continue, geautomatiseerde* agent-vraag. Dat maakt het
`CLAUDE.md`-kernprincipe "agent-onafhankelijk" van een nette belofte tot
een **operationele hedge** die actief onderhouden moet worden.

## 2. Waar staat de applicatie vandaag (geverifieerd)

### 2.1 Wat draait of code-compleet is

| Laag | Staat |
|---|---|
| **Kanban v1 + auto-dispatch** | Draait. Claim-before-spawn, worktree-isolatie, per-project opt-in, per-project cap. |
| **Multi-agent (analyst → executors)** | Draait. Parent → kind-kaarten met dependency-DAG + plan-attachment. |
| **Agent Mail** | Draait. Durable per-repo identiteit, cross-session mailbox. |
| **Scheduled-messages (fase 2)** | Code-compleet (Tasks 1–11); **Task 12 runtime-e2e = mensenwerk** (`docker compose up` + `claude`-login). |
| **Sandcastle** | Docker-sandbox-provider aanwezig; security-hardening **in uitvoering** (engineer-kolom). |
| **Portfolio-laag (facet C)** | `kind`-tag, `PortfolioService`, `/portfolio/overview`, `PortfolioPage` **gedeeltelijk geland** (Done + één Backlog-follow-up voor stale-state). |
| **Veilig-bouwen (facet D)** | **In uitvoering**: SecretStore, Sandcastle resource-caps, `ProjectSecurityPolicy`, risk_class-taxonomie. Grootste openstaande cluster. |

### 2.2 Bord-momentopname (juli 2026)

- **Impediment (3, mens-beslissing):** CI uit door billing · go/no-go repo
  privé→publiek · market-research-trigger-migratie (wacht op fase-2 Task 12).
- **In uitvoering (engineer, 4):** facet-D security (Sandcastle-caps,
  SecretStore) + facet-B `BlueprintApply`-engine.
- **Backlog (25):** overwegend facet-D (RunService, Preview-URL,
  DeployTarget, CITemplate, audit-log, env-injectie), facet-portfolio
  (stale-state), facet-inceptie (blueprints-typology,
  brainstorm-to-impediment-bridge), en een **dikke laag `[self-improve]`-
  schuld** (≈11 kaarten).

**Lezing:** de "andere-applicaties"-pijplijn is *geanalyseerd* (facet-docs
A/B/C/D bestaan) en *begint te landen* (portfolio deels, bootstrap +
security in uitvoering), maar is **nog nergens end-to-end bewezen** met één
echte externe app. Tegelijk stapelt zich zelfverbeter-schuld op die de
autonome sessies zelf trager/brozer maakt.

## 3. Externe ecosysteem-scan (web, juli 2026)

Vijf bevindingen die de prioriteiten direct raken. Per bevinding: *wat het
is* → *wat het voor Cockpit betekent*.

1. **Anthropic-abonnementsbeleid (4 april 2026).** Anthropic koppelde
   Pro/Max-abonnementen los van *third-party* agent-harnasses (enforcement
   begon bij OpenClaw, "expanding to all third-party harnesses"); later
   deels hersteld, maar programmatisch gebruik loopt nu via een **vast,
   niet-doorrollend maandkrediet**, niet de algemene abonnementspool. De
   expliciete rationale: *"subscriptions were never designed for the kind
   of continuous, automated demand these tools generate."*. Dat is letterlijk
   de reden van Anthropic voor het beleid.
   → **Voor Cockpit het belangrijkste externe signaal.** Cockpit spawnt de
   *officiële* Claude Code CLI (de first-party harnas, niet OpenClaw), dus
   het valt vandaag waarschijnlijk binnen de lijnen. Maar autodispatch
   genereert precies het *continue, geautomatiseerde* verbruik dat het
   beleid target. Twee gevolgen:

   (a) "agent-onafhankelijk" moet een **werkende hedge** zijn, geen slogan
   — de `agentic_cli`-capability-matrix en een tweede provider die
   daadwerkelijk een kaart kan afronden verdienen prioriteit.

   (b) de economie van "N parallelle sessies op één Max-abo"
   moet expliciet bewaakt worden (dit voedt de portfolio-cap uit facet C).

2. **GitHub Spec Kit** (`github/spec-kit`, ~111k⭐, MIT, agent-agnostisch,
   30+ agents). Structureert AI-codering als **specify → plan → tasks →
   implement**. Spec-driven development (SDD) is in 2026 mainstream geworden
   (Spec Kit, AWS Kiro, OpenSpec, BMAD, Google Antigravity).
   → Dit is **exact facet A (inceptie) + de spec-driven-track**. Cockpit's
   `analyst → plan-attachment → kind-kaarten` *ís* al een specify→plan→tasks-
   pijplijn. Aanbeveling: **aligneren/interopereren i.p.v. heruitvinden** —
   leen Spec Kit's fasestructuur en artefact-vorm voor de inceptie-flow
   (`brainstorm-to-impediment-bridge`, `blueprints-typology`), zodat een
   Cockpit-gegenereerde spec herkenbaar is voor de bredere SDD-wereld.

3. **OpenHands** (~76k⭐, model-agnostisch platform: SDK + CLI + GUI + REST,
   multi-agent delegatie, 72% SWE-bench). Conceptueel het dichtstbijzijnde
   open-source alternatief voor wat Cockpit doet.
   → Twee opties, beide hedge-versterkend: (a) OpenHands als **tweede
   executor-provider** achter de bestaande transport-abstractie (concreet
   antwoord op de agent-onafhankelijkheids-eis én op risico #1); (b) leren
   van hun sub-agent-delegatie (TaskToolSet) voor de multi-agent-laag. Niet
   dringend, wel de sterkste kandidaat voor "welke externe app integreren we".

4. **Container Use (Dagger)** + de bredere **sandbox-2026-consensus**. Container
   Use geeft elke agent een eigen container-sandbox + git-worktree — bijna
   1:1 Cockpit's Sandcastle + worktree-model. Bredere consensus: *"Docker is
   niet genoeg"* voor niet-vertrouwde code. De gedeelde kernel is het
   probleem. Productie wil micro-VMs.
   microVMs (Firecracker/Kata) of gVisor.
   → Directe input voor **facet D + Sandcastle-hardening**: de huidige
   Docker-sandbox is prima voor *eigen* code, maar zodra Cockpit een
   *gegenereerde/externe* app draait (RunService, Preview-URL) verschuift het
   dreigingsmodel. De Sandcastle-caps-kaart in uitvoering is de juiste eerste
   stap; een microVM/gVisor-optie hoort op de facet-D-roadmap als de
   isolatie-as, niet als premature herbouw.

5. **Cline-incident (feb 2026)** — een VS Code-extensie met 5M+ users werd via
   een prompt-injection-keten gecompromitteerd. Die keten exfiltreerde
   npm-release-tokens en publiceerde een kwaadaardig pakket.
   → **Concrete bewijslast voor de facet-D-volgorde.** Precies het scenario
   dat `SecretStore` + `Security-audit-log` + per-project env-injectie moeten
   afdekken. Dit verschuift facet-D van "goed idee" naar "voorwaarde vóór we
   niet-vertrouwde app-code laten draaien of secrets aan een sessie geven".

## 4. Strategisch risico dat boven de stack hangt

**Abonnements-economie (bevinding #1) is een platform-risico, geen
feature.** Het hele autonome model leunt op continue `claude`-invocaties.
Als Anthropic de first-party-harnas ook onder "continue geautomatiseerde
vraag" zou throttlen (of het krediet-model uitbreidt), raakt dat de *kern*
van de waardepropositie. Mitigatie is geen nieuwe feature maar het **serieus
nemen van twee dingen die al in de docs staan**:

- De **capability-matrix + headless/tweede-provider-transport** uit
  `orchestration-substrate-decision.md` (§5) — zodat een tweede agent
  (OpenHands, Codex, of `claude` via metered API-key) een kaart écht kan
  afmaken, niet alleen spawnen.
- De **portfolio-cap** uit `portfolio-orchestratie.md` (facet C, optie 2) —
  zodat het verbruik over projecten *bestuurbaar* is i.p.v. impliciet
  begrensd door het memory-budget.

Dit is geen oproep om nú te migreren (de substraat-beslissing zegt
terecht: incrementeel abstraheren, tmux blijft default voor
human-in-the-loop). Het is een oproep om de hedge **niet te laten
verwelken** tot een noodgeval.

## 5. Prioriteitsstack (het antwoord op "wat eerst")

Geordend op *afhankelijkheid en risico*, niet op grootte. Elke laag
ontgrendelt of beveiligt de volgende.

### P0 — Fundament weer groen (blokkeert al het andere)

Er is **nu geen kwaliteitsvangnet**: de lokale pre-push-gate is bewust
verwijderd (CLAUDE.md) *en* CI (`quality.yml`/`security.yml`) faalt binnen
seconden door GitHub-billing. Elke merge naar `master` gebeurt vandaag
volledig ongetoetst. Dit is mens-werk (twee Impediment-kaarten), maar het
is **prioriteit nul**: zonder gate is elke andere prioriteit een gok.

- **Actie (eigenaar):** GitHub-billing herstellen → CI weer groen →
  daarna pas de go/no-go privé→publiek. Zolang CI rood staat is
  "publiek maken" sowieso ongewenst (eerste indruk = twee kapotte badges).

### P1 — "Andere applicaties" end-to-end bewijzen (één dunne slice)

De doelstelling verheft "andere applicaties" tot eersteklas doel, maar het
is **nergens end-to-end aangetoond**. De facet-analyses en losse kaarten
verbreden het oppervlak zonder één keer de hele keten te sluiten. Prioriteit:
**één echte externe app** van intake → bootstrap → autodispatch → eerste
gemergede kaart, als vertical slice die het geheel valideert.

- Sluit de **inceptie-gaten** (facet A: `brainstorm-to-impediment-bridge`,
  `blueprints-typology`) net genoeg om één spec → project te laten geboren
  worden — bij voorkeur met een **Spec-Kit-herkenbare** artefactvorm (§3.2).
- Leun op de **bootstrap** die net geland is (`RepoBootstrapService`) +
  `BlueprintApply` (in uitvoering).
- **Succescriterium:** één niet-Cockpit-repo staat op het bord, kreeg een
  autodispatch-sessie, en heeft één gemergede kaart. Dat is de kleinste
  volledige demonstratie van het eersteklas doel.

### P2 — Veilig-bouwen (facet D) vóór "app daadwerkelijk draaien"

Facet D is de grootste openstaande cluster én de **poortwachter** voor P1's
volgende stap. Zodra Cockpit een gegenereerde app *draait* (RunService) of
*exposeert* (Preview-URL), verschuift het dreigingsmodel naar niet-vertrouwde
code — precies waar de Cline-les (§3.5) en de "Docker-is-niet-genoeg"-
consensus (§3.4) bijten. Volgorde binnen D:

1. **Isolatie + secrets eerst:** Sandcastle resource-caps (in uitvoering),
   `SecretStore`, per-project env-injectie, `ProjectSecurityPolicy`. Dit zijn
   de bestaande engineer/Backlog-kaarten — juiste volgorde, laat ze landen.
2. **Pas daarná de "run/expose"-kaarten** ontgrendelen: `RunService`,
   Preview-URL, DeployTarget. Deze horen *achter* stap 1, niet ervoor.
3. **microVM/gVisor** als bewuste isolatie-as op de roadmap zetten (niet nu
   bouwen) voor het geval externe apps echt niet-vertrouwd worden.

### P3 — Agent-onafhankelijkheid als werkende hedge

Getriggerd door risico #1/§4. Niet dringend deze week, wél voordat het
abonnements-model een noodgeval wordt:

- `headless_run`/`structured_events`-capability in `agentic_cli`
  (substraat-doc §6, kaart 3).
- Eén tweede provider die een kaart *afrondt* (OpenHands-transport of
  `claude` via metered API-key) als proof-of-hedge.
- Portfolio-cap (facet C, optie 2) om verbruik bestuurbaar te maken.

### P4 — Zelfverbeter-schuld opportunistisch wegwerken

≈11 `[self-improve]`-kaarten. Niet als blok, maar meelopend: geef voorrang
aan de kaarten die de **autonome sessies zelf** frictie geven, want die
belasten élke toekomstige kaart. Scherpste kandidaten:

- `list_cards(Backlog)` knalt de MCP-tokencap door ~27 kaarten (elke
  dedupe-pass heeft nu een jq-fallback nodig) — raakt élke analyst/retro-sessie.
- ship-recipe leeft in 3 mirrors zonder drift-test.
- `work_type=analysis` routeert design-deliverable-kaarten naar de
  decompositie-only analyst (het conflict dat *deze* kaart een override nodig
  had) — structureel oplossen i.p.v. per kaart patchen.

## 6. Expliciet antwoord: welke externe applicaties integreren?

| Kandidaat | Integratievorm | Prioriteit |
|---|---|---|
| **GitHub Spec Kit** | Aligneer de inceptie-artefactvorm (specify→plan→tasks) ermee; interop, niet vendoren. Versterkt facet A. | **Hoog** — goedkoop, sluit aan op mainstream SDD. |
| **OpenHands** | Tweede executor-provider achter de transport-abstractie; tegelijk de agent-onafhankelijkheids-hedge (§4). | **Middel** — hoogste strategische waarde, maar na P0/P1. |
| **Container Use / microVM-isolatie (Firecracker/gVisor)** | Input/optie voor Sandcastle-hardening (facet D); microVM als isolatie-as op de roadmap. | **Middel** — gekoppeld aan P2, niet los. |
| **Codex CLI / metered `claude` API-key** | Al deels via `agentic_cli`; afmaken als tweede pad voor de abonnements-hedge. | **Middel** — onderdeel van P3. |

**Niet integreren (bewust):** volwaardige third-party orchestrators
(Ruflo, Claude Squad, OpenClaw) — dat is een *concurrerend* paradigma
(zie de al-genomen `upstream-agent-teams-decision.md`) én valt precies onder
het geblokkeerde abonnements-gebruik uit §3.1. Cherry-pick hooguit
provider-correctheids-bugs, zoals eerder gedaan.

## 7. Wat we nu NIET moeten doen

- **De facetten verder verbreden zonder P1 te sluiten.** Meer losse
  facet-D/portfolio/inceptie-kaarten zonder één end-to-end bewijs vergroot
  het oppervlak zonder de doelstelling aantoonbaar dichterbij te brengen.
- **Substraat-migratie (tmux → headless) als big-bang.** De
  substraat-beslissing staat: incrementeel, tmux blijft default voor
  human-in-the-loop. P3 is de *capability*, niet een migratie.
- **microVM/gVisor nu bouwen.** Roadmap-item achter een echte niet-vertrouwde-
  app-behoefte, niet premature herbouw van Sandcastle.
- **Repo publiek maken vóór CI groen is.** Volgorde is P0, dan pas de go/no-go.

## 8. Kernbevinding (voor de ouder-comment / triage)

> Cockpit heeft geen tekort aan features maar aan **afronding op de juiste
> volgorde**. Bouw eerst het fundament groen (P0: CI-billing + go/no-go —
> mens-werk, maar blokkerend want er is nu géén kwaliteitsgate).
>
> Bewijs daarna de eersteklas "andere-applicaties"-doelstelling **end-to-end
> met één echte externe app** (P1) i.p.v. de facetten verder los te
> verbreden. Laat **veilig-bouwen (facet D) landen vóór** je een gebouwde app
> daadwerkelijk draait of exposeert (P2) — de buitenwereld (Cline-exfil,
> "Docker-is-niet-genoeg") maakt die volgorde dwingend. Houd
> **agent-onafhankelijkheid als werkende hedge** levend (P3) omdat
> Anthropic's april-2026-abonnementsbeleid de economie van continu autonoom
> verbruik raakt. Werk zelfverbeter-schuld die de sessies zélf remt
> opportunistisch weg (P4). Van de buitenwereld zijn **Spec Kit**
> (aligneren, goedkoop) en **OpenHands** (tweede executor = meteen de hedge)
> de twee integraties met de beste verhouding tussen inspanning en
> strategische waarde.

## 9. Bronnen (web-scan juli 2026)

- OpenHands — [theaiagentindex.com](https://theaiagentindex.com/agents/openhands),
  [opensourceaireview.com](https://www.opensourceaireview.com/blog/top-8-open-source-coding-agents-in-2026)
- Container Use (Dagger) + sandbox-consensus —
  [InfoQ](https://www.infoq.com/news/2025/08/container-use/),
  [Northflank](https://northflank.com/blog/how-to-sandbox-ai-agents),
  [Firecrawl](https://www.firecrawl.dev/blog/ai-agent-sandbox)
- Claude Agent SDK / multi-agent orchestratie —
  [platform.claude.com](https://platform.claude.com/docs/en/managed-agents/multi-agent),
  [Shipyard](https://shipyard.build/blog/claude-code-multi-agent/)
- Anthropic Pro/Max third-party-beleid (4 april 2026) —
  [PYMNTS](https://www.pymnts.com/artificial-intelligence-2/2026/third-party-agents-lose-access-as-anthropic-tightens-claude-usage-rules/),
  [VentureBeat](https://venturebeat.com/technology/anthropic-reinstates-openclaw-and-third-party-agent-usage-on-claude-subscriptions-with-a-catch),
  [TNW](https://thenextweb.com/news/anthropic-openclaw-claude-subscription-ban-cost)
- GitHub Spec Kit / spec-driven development —
  [GitHub Blog](https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/),
  [github/spec-kit](https://github.com/github/spec-kit),
  [Microsoft for Developers](https://developer.microsoft.com/blog/spec-driven-development-spec-kit)
</content>
</invoke>
