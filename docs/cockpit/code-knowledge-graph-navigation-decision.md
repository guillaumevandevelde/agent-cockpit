---
title: "Beslissing — Code-kennisgraaf (Understand-Anything) voor code-navigatie"
type: decision
status: decided
---

# Beslissing — Code-kennisgraaf (Understand-Anything) voor code-navigatie

**Datum:** 2026-07-18
**Status:** Beslissing (`not_feasible` — voorwaardelijk heropenbaar)
**Kaart:** `[knowledge-structure] (uitgestelde spike) Code-kennisgraaf evalueren voor code-navigatie` (`a431894193e842c5b9a1d45e2471ba3b`)
**Uitkomst:** **NO-GO nu — `not_feasible`, trigger niet gevuurd.**

Deze kaart is de bewaarde, VOORWAARDELIJKE code-navigatie-poot van de fork uit
`knowledge-structure-navigation-analysis.md` (kaart `9ef2d193…`). De eerste
acceptatie-eis is een poort: *"Bevestig eerst of de trigger geldt — is er gemeten
bewijs dat code-navigatie (niet docs) de bottleneck is? Zo niet → `not_feasible`."*
Er is **geen** zulk gemeten bewijs, en de platform-telemetrie kan het op dit moment
structureel niet eens produceren. Dus: `not_feasible`.

---

## 1. Wat deze kaart vroeg

De originele analyse (`knowledge-structure-navigation-analysis.md` §2/§4.3) beval de
code-kennisgraaf (Egonex-AI / Understand-Anything: tree-sitter + LLM →
`.ua/knowledge-graph.json`) **AF** als docs-oplossing — het target is *broncode*,
niet de prozaberg, en het voegt zware staleness-gevoelige infra + een build-stap toe
die deze repo elders bewust mijdt (geen DB-migraties, `create_all`; geen
pre-push-gate; minimalistische toolchain). Extern onderzoek in die analyse mat
bovendien **lagere** antwoordkwaliteit dan file-exploration (83% vs 92%), met winst
enkel op graaf-native code-vragen (call-graphs, impact-analyse).

De fork bewaarde één voorwaardelijke poot: *"Trek deze kaart alleen als
code-navigatie (NIET docs) een gemeten pijn wordt."* Deze kaart is die poot. Ze is
per constructie **geen commitment** — ze mag alleen uitgevoerd worden als de trigger
vuurt. De acceptatie-eisen zijn expliciet dat, zonder gemeten signaal, `not_feasible`
het legitieme eindpunt is.

## 2. Methode

Read-only verificatie van de trigger-conditie op deze repo (2026-07-18):

1. **Docs-sweep** — `grep -rilE "call-graph|impact-analys|code-navigat|understand-anything|tree-sitter|knowledge-graph|too many tool"` over `docs/`.
2. **Board-sweep** — `list_cards` op `Backlog` en `Impediment`; inspectie van de sibling-`[knowledge-structure]`-kaarten.
3. **Telemetrie-audit** — wat meten `usage_service.py`, `dispatch_usage_service.py` en de "APM"-feature daadwerkelijk? Kan een van hen "code-navigatie-vragen" isoleren?

## 3. Bevindingen — de trigger is niet gevuurd

**3.1 Geen doc registreert gemeten code-navigatie-pijn.** De docs-sweep vond geen
enkel `docs/`-artefact dat meet dat agents te veel tool-calls/tokens verbranden op
structurele code-vragen (call-graphs, impact-analyse). Het dichtstbijzijnde meetdoc,
`token-optimization-analysis.md`, lokaliseert de tokenkost bij **geïnjecteerde
statische context** (CLAUDE.md, persona, ship-instructies, MCP-schemas) — niet bij
code-navigatie tijdens een sessie.

**3.2 Geen kaart registreert code-navigatie-pijn.** De enige andere
`[knowledge-structure]`-kaarten op `Backlog` zijn de **docs**-poot, niet
code-navigatie:

- `25bfe803…` — *Frontmatter-ruggengraat + backfill docs/cockpit* (YAML-frontmatter
  op 84 docs, machine-filterbaar op type/status).
- `340a3010…` — *Gegenereerde index + llms.txt uit frontmatter*.

Dat is precies de richting die de originele analyse **wél** aanbeval (docs-navigatie
via structuur, geen code-kennisgraaf). Geen enkele kaart op `Backlog`/`Impediment`
beschrijft code-navigatie als een gemeten bottleneck.

**3.3 De telemetrie kan het signaal niet eens produceren.** Beslissender dan "niemand
heeft het gemeten": het platform kan de meting op dit moment **niet leveren** zonder
bespoke instrumentatie die niet bestaat.

- `usage_service.py` / `dispatch_usage_service.py` aggregeren tokens **per sessie /
  per model / per dag** (`input/output/cache_creation/cache_read`). Er is geen
  dimensie die een turn categoriseert als "structurele code-vraag" vs "docs-vraag"
  vs "implementatie". Tokens-per-sessie kan een code-navigatie-bottleneck dus niet
  isoleren.
- De "APM"-feature (`apm_service.py`, `docs/features/apm.md`) is een **Agent Package
  Manager** (dependency-management: `apm.yml`, install/sync) — géén
  performance-monitoring. Ze meet niets over tool-calls of code-vragen.

Er is geen tool-call-teller, geen per-turn-categorisatie, geen
"tool-calls-per-code-vraag"-metriek. De trigger vraagt om *gemeten* bewijs; het
meetpunt zelf ontbreekt.

**3.4 De externe evidentie leunt al tegen.** De originele analyse mat lagere
antwoordkwaliteit voor de graaf dan voor file-exploration (83% vs 92%), met winst
enkel op graaf-native code-vragen. Zelfs als er een klein code-navigatie-signaal
zou zijn, zou de kwaliteitsregressie én de staleness-/build-infra dat moeten
overtreffen — en daar is nu geen enkel getal voor.

## 4. Waarom niet alsnog de volledige evaluatie draaien

De acceptatie-eisen scoop de volledige Understand-Anything-evaluatie (setup-kost,
regeneratie-cadans, integratie, gemeten token/tool-call-winst op ≥3 echte
code-vragen) **expliciet achter de trigger**: "Zo ja: evalueer …". De kaart is een
bewust *uitgestelde, voorwaardelijke* spike — ze uitvoeren zonder gevuurde trigger is
werk tegen het eigen contract van de kaart, en zou precies het
"chars/4-schatting-als-feit"-antipatroon riskeren dat de kaart verbiedt (er zijn geen
echte code-navigatie-sessies om tegen te meten). `not_feasible` is hier het
ontworpen, legitieme eindpunt — geen escape hatch.

## 5. Heropen-trigger (wanneer wél)

Heropen deze beslissing zodra **gemeten** bewijs bestaat dat *code-navigatie* (niet
docs) een bottleneck is. Concreet, minimaal één van:

1. **Per-turn/per-vraag token- of tool-call-telemetrie** bestaat (bv. de
   run-ledger-poot `4ce329cd…` of een nieuwe categorisatie-dimensie op
   `dispatch_usage_service`) én toont dat structurele code-vragen (call-graphs,
   impact-analyse, "wie roept X aan") aantoonbaar veel meer tool-calls/tokens kosten
   dan een baseline.
2. **≥3 reële sessies** waarin een agent aantoonbaar veel fan-out-tool-calls
   verbrandt op een structurele code-vraag die een kennisgraaf in één query zou
   beantwoorden — met de sessie-refs als bewijs.

Zonder zo'n signaal blijft de docs-poot (frontmatter + gegenereerde index, kaarten
`25bfe803…`/`340a3010…`) de juiste, lichtere investering, en blijft de
code-kennisgraaf NO-GO.

## 6. Reproductie

```bash
# 3.1 — geen doc meet code-navigatie-pijn:
grep -rilE "call-graph|impact-analys|code-navigat|understand-anything|tree-sitter|knowledge-graph|too many tool" docs/    # → leeg

# 3.3 — telemetrie-granulariteit (tokens per sessie/model/dag, geen vraag-categorie):
grep -nE "def |tool_call|category|question" backend/app/services/dispatch_usage_service.py backend/app/services/usage_service.py
head -1 docs/features/apm.md    # → "# APM (Agent Package Manager)" — dependency-manager, geen perf-monitoring

# 3.2 — sibling knowledge-structure-kaarten zijn de docs-poot, niet code-navigatie:
#   list_cards(Backlog) → 25bfe803 (frontmatter-backfill), 340a3010 (index+llms.txt)
```
