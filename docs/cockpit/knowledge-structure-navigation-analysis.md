---
title: Kennisopbouw & navigatie — hoe structureren we de docs-berg
type: analysis
status: proposed
card: 9ef2d1930285477ab53a869bc7a9b36d
related:
  - spec-driven-development-analysis.md
  - plans-feature-decision.md
  - 00-orientation.md
---

# Kennisopbouw & navigatie — hoe structureren we de docs-berg (leaf spike)

**Datum:** 2026-07-18
**Status:** voorgesteld (aanbeveling + gedecomponeerd in vervolgkaarten)
**Kaart:** `9ef2d193…` "Analysis - kennisopbouw en navigatie"

> Vraag (gebruiker): *"Was naar de plans sectie aan het kijken en ik raak daar niet
> aan uit. Is een wilde hoop plannen zonder enige structuur. Heb jij hier zelf een
> meerwaarde aan? Ik niet momenteel. Wat kunnen we doen? Analyseer breed, zoek op het
> internet over repo kennis structuren die door machine en mens goed navigeerbaar zijn.
> Moeten we een kennisgraaf (Understand-Anything) gebruiken of betere mappen structuren,
> of okf.md?"*
>
> Dit is een leaf-spike (modus 2): DoD is dit doc met een gemeten diagnose + een concrete
> aanbeveling, plus scoped vervolgkaarten. Geen feature-code in deze kaart.

## TL;DR

De kennis van dit platform is **niet ongestructureerd — ze is menselijk-geïndexeerd en
machine-onleesbaar.** `docs/cockpit/` (84 docs) wordt vandaag genavigeerd via drie
hand-onderhouden lagen (README-indextabel, `decisions.md`-register, promotie-ledger) plus
grep op bestandsnaam-conventies (`*-decision.md`). Die aanpak schaalt niet meer:

| Gemeten (2026-07-18, in deze worktree) | Waarde | Betekenis |
|---|---|---|
| `docs/cockpit/*.md` met YAML-frontmatter | **0 / 84** | niets is machine-filterbaar op `type`/`status`/`datum` |
| docs gelinkt vanuit de hand-onderhouden README-index | **43 / 84** | **de helft van de docs staat in geen enkele index** |
| root `llms.txt` (machine-instapkaart) | **afwezig** | een agent heeft geen kanonieke instap, alleen grep |
| `docs/cockpit`, `superpowers`, `plans-legacy` in de VitePress-site | **`srcExclude`d** (`config.ts:12-16`) | de héle actieve fork-kennis (~113 docs) rendert nergens als site — enkel ruwe markdown |

De "wilde hoop plannen zonder structuur" is dus letterlijk: een platte map van 84 docs,
waarvan >de helft in geen index staat, geen enkele met machineleesbare metadata, en géén
van alle zichtbaar in de mensvriendelijke docs-site. Beide lezers — de mens die bladert
en de agent die zoekt — vallen terug op grep.

**Aanbeveling:** niet een kennisgraaf bouwen en niet de mappen groot herschikken, maar de
**goedkoopste laag die beide lezers tegelijk bedient**: een minimale, OKF-compatibele
**frontmatter-ruggengraat** (`type`/`status`/…) op elke doc, plus een **gegenereerde
index + `llms.txt`** die de hand-onderhouden README vervangt. De kennisgraaf
(Understand-Anything) is een *code*-navigatietool die een *ander* probleem oplost dan de
docs-berg; hij blijft als expliciet uitgestelde, voorwaardelijke spike bewaard — niet nu.

## 1. Wat er vandaag al staat (geverifieerd)

Het is belangrijk te erkennen dat de repo al een doordachte, maar volledig **handmatige**
kennisarchitectuur heeft — het probleem is niet "geen structuur", het is "structuur die
alleen een mens die 'm onderhoudt kan lezen, en die drift":

- **`docs/cockpit/README.md`** — een indextabel "leidend document per feature" +
  thematische lijst van beslisdocumenten. Hand-onderhouden; dekt vandaag **43/84** docs.
- **`docs/cockpit/decisions.md`** — chronologisch beslis-register (datum, vraag, uitkomst,
  doc-link, kaart-id). Bewaakt door `scripts/check-decision-register.sh` (flag't elk
  `*-decision.md` dat ontbreekt).
- **`docs/superpowers/README.md`** — promotie-ledger: gedateerde `plans/`+`specs/`
  werkoutput die "promoot" naar een topic-genoemd `docs/cockpit/`-doc zodra werk landt.
  Bewaakt door `scripts/check-superpowers-promotions.sh` (advisory).
- **Bestandsnaam-conventies als impliciete types** — `*-decision.md`, `*-analyse.md`/
  `*-analysis.md`, `*-spec.md`, `*-plan.md`, `*-spike*.md`. Deze zíjn de facto een
  type-taxonomie, maar bestaan alleen in de bestandsnaam, niet als queryeerbaar veld.
- **`docs/.vitepress/`** — een echte VitePress-site, maar die rendert **alleen** de
  upstream claude-deck user-docs (`guide/`, `api/`, `features/`). De actieve fork-kennis
  is expliciet uitgesloten (`srcExclude: ['plans-legacy/**','superpowers/**','cockpit/**']`).

Kortom: er zijn al drie "kennisgraaf-lite"-lagen (getypeerde links in prozatabellen +
advisory check-scripts). Ze werken zolang een mens ze bijwerkt — en ze driften zodra dat
niet gebeurt (43/84 is het bewijs).

## 2. De drie opties die de kaart noemt — getoetst aan dít probleem

### Optie 1 — Kennisgraaf (Understand-Anything)
`Egonex-AI/Understand-Anything` zet **broncode** om in een interactieve kennisgraaf
(`.ua/knowledge-graph.json`) via tree-sitter (structurele feiten: imports, calls,
definities) + LLM-samenvattingen, met een dashboard. Primair een **code-onboarding /
codebase-navigatie**-tool.

*Waarom het hier niet past als docs-oplossing:*
- Het target is **code**, niet de prozaberg. De pijn van de gebruiker is de *plannen/docs*,
  en die zijn geen call-graph.
- Het is een **afgeleid, gegenereerd artefact** dat staleness introduceert: regenereren bij
  elke commit, een build-stap, een dependency en een dashboard erbij. Dat is precies het
  soort zware infra dat deze repo elders bewust mijdt (advisory scripts i.p.v. harde gates —
  zie `spec-driven-development-analysis.md` §7 "vermijd theater").
- Onafhankelijk onderzoek (AST-KG vs. file-exploration over 31 repos) meet dat een
  code-KG **~10× minder tokens** kost maar **lagere antwoordkwaliteit** haalt (83% vs. 92%
  voor file-exploration), en vooral wint op *graaf-native* vragen (hub-detectie,
  caller-ranking). Dat zijn code-vragen, geen docs-vragen.

*Verdict:* mis-getarget en te zwaar voor de docs-berg. **Wel** potentieel nuttig als
*aparte* code-navigatie-hulp als agents ooit meetbaar worstelen met de code — bewaard als
voorwaardelijke spike (kaart 3), niet nu.

### Optie 2 — Betere mappenstructuur
De platte 84-file-map is de zichtbare pijn. Twee subvarianten:
- **Diátaxis** (tutorials/how-to/reference/explanation) — een documentatie-framework voor
  *user-facing* docs. Past slecht: `docs/cockpit/` is grotendeels *beslissingen*,
  *analyses* en *specs* voor makers, niet leer-/taakdocs voor eindgebruikers.
- **Doc-type-submappen** (`decisions/`, `specs/`, `analyses/`, `plans/`) — past inhoudelijk
  beter, maar een fysieke herschikking van 84 files **breekt de tientallen cross-links**
  (README, `00-orientation.md`, onderlinge `[..](./x.md)`-refs, en scripts die
  `docs/cockpit/*-decision.md` grep'en). Hoge kosten, hoog regressie-risico, en het lost
  de *machine*-leesbaarheid niet op.

*Verdict:* de type-taxonomie is juist, maar realiseer 'm als **frontmatter-veld
(`type:`) = virtuele map**, niet als fysieke verhuizing. Zelfde navigatiewinst, geen
gebroken refs.

### Optie 3 — okf.md (Open Knowledge Format)
OKF is een **minimale, git-native markdown-conventie** (geen tool, geen SDK, geen
build-stap): mappen met `.md`, YAML-frontmatter met `type`/`title`, standaard markdown —
"agent-first", direct parseerbaar door Claude/Codex/Cursor zonder adapter.

*Waarom dit het beste past:* het is bijna exact wat `docs/cockpit/` al **is** (markdown in
git, topic-genoemd), plus precies het ontbrekende stuk (**machineleesbare frontmatter**).
De adoptiekost is minimaal en volledig omkeerbaar; het sluit naadloos aan op de bestaande
filosofie (index + register + ledger zijn al hand-gemaakte OKF-achtige conventies). De enige
echte gaten die OKF dicht: (a) frontmatter i.p.v. bestandsnaam-conventie, en (b) een
gegenereerde i.p.v. hand-onderhouden index.

*Verdict:* **de ruggengraat van de aanbeveling.**

## 3. De bredere stand van de kunst (webonderzoek)

Het veld convergeert in 2026 op één principe: **documentatie heeft twee lezers — de mens
en de coding-agent** — en de winnende aanpak bedient beide van één markdown-bron:

- **`llms.txt`** — een root-bestand (analoog aan `robots.txt`/`sitemap.xml`) dat een
  docs-verzameling samenvat voor agents en naar de belangrijkste pagina's linkt. Wordt de
  facto standaard als machine-instapkaart.
- **Markdown-native + frontmatter** wint van zware doc-platforms voor agent-consumptie:
  clean, structureel, geen middleware.
- **Kennisgraaf vs. kennisbank is zelden binair** — productiesystemen combineren: kennisbank
  (markdown) voor volumineuze semantische retrieval, kennisgraaf voor relationeel redeneren
  en verklaarbaarheid. Voor *prozadocs* is de kennisbank-kant het werkpaard; de graaf is een
  code-concern.

Dit bevestigt de richting: markdown-bron + frontmatter + `llms.txt`, kennisgraaf uitgesteld.

## 4. Aanbeveling — gelaagd, goedkoop, omkeerbaar

Bouw geen parallel systeem; formaliseer wat er al is. Fundament → franje:

1. **Frontmatter-ruggengraat (OKF-compatibel).** Definieer een minimaal schema en backfill
   alle `docs/cockpit/`-docs (dit doc draagt het al als voorbeeld):
   ```yaml
   ---
   title: <mensleesbare titel>
   type: decision | spec | analysis | plan | reference | index
   status: proposed | active | decided | superseded
   card: <host-card-id, optioneel>
   supersedes / superseded_by: <bestandsnaam, optioneel>
   related: [<bestandsnaam>, ...]
   ---
   ```
   `type` is de virtuele map (vervangt de bestandsnaam-conventie); `status` maakt
   proposed/superseded machine-queryeerbaar i.p.v. begraven in proza-banners. Voeg een
   advisory `scripts/check-doc-frontmatter.sh` toe (zelfde filosofie als de bestaande
   check-scripts).

2. **Gegenereerde index + `llms.txt`.** Eén generator die de frontmatter leest en
   (a) de README-indextabel **regenereert** (gegroepeerd op `type`, met `status`-badges,
   100% dekking i.p.v. 43/84), en (b) een `docs/cockpit/llms.txt` (of root) emit als
   machine-instapkaart. Dit vervangt de hand-onderhouden, driftende tabel door een
   afgeleide-van-de-waarheid. De human-site-kant (cockpit in VitePress opnemen door de
   `srcExclude` te lichten + een gegenereerde sidebar) is een optionele uitbreiding op
   dezelfde generator — genoemd, niet als aparte kaart.

3. **Kennisgraaf: uitgesteld & voorwaardelijk.** Understand-Anything blijft bewaard als een
   losse spike, alleen te trekken als *code*-navigatie (niet docs) een gemeten pijn wordt.
   Niet nu — hij lost de gestelde vraag niet op en voegt zware, staleness-gevoelige infra toe.

**Waarom niet de graaf of de grote verhuizing:** de graaf target code, niet de docs-berg,
en kost onderhoud dat deze repo elders bewust mijdt; een fysieke mappen-herschikking breekt
tientallen refs voor nul machine-winst. De frontmatter-ruggengraat + generator geeft beide
lezers exact wat ze missen, met de kleinste en meest omkeerbare ingreep.

## 5. Meet-noot (eerlijk over wat gemeten is)

Alle getallen in §TL;DR/§1 zijn **gemeten** in deze worktree, reproduceerbaar:

```bash
# frontmatter-dekking
for f in docs/cockpit/*.md; do [ "$(head -1 "$f")" = "---" ] && echo "$f"; done | wc -l
ls docs/cockpit/*.md | wc -l
# index-dekking
grep -oE '\]\(\./[a-z0-9-]+\.md' docs/cockpit/README.md | sort -u | wc -l
# site-exclusie
grep -n cockpit docs/.vitepress/config.ts
```

De onderzoeks-claims over kennisgraaf-tokenkosten/antwoordkwaliteit (83% vs. 92%,
~10× tokens) komen uit extern webonderzoek (§Bronnen), **niet** uit een meting op deze repo —
ze onderbouwen "graaf is code-getarget, geen gratis docs-winst", niet een precieze
belofte voor Cockpit.

## 6. Vervolgkaarten (aangemaakt in deze sessie, Backlog)

Deze leaf-spike maakt de aanbeveling actiegericht via drie Backlog-kaarten (proposals — een
mens prioriteert/kill't ze; ze worden niet auto-gedispatcht vanuit Backlog):

| # | Kaart | Type | Dep | Kern |
|---|---|---|---|---|
| 1 | `[knowledge-structure] Frontmatter-ruggengraat + backfill docs/cockpit` | chore | — | minimaal OKF-schema (§4.1) + backfill 84 docs + advisory `check-doc-frontmatter.sh` |
| 2 | `[knowledge-structure] Gegenereerde index + llms.txt uit frontmatter` | feature | 1 | generator regenereert README-index (100% dekking, gegroepeerd op type/status) + emit `llms.txt`; optioneel cockpit in VitePress |
| 3 | `[knowledge-structure] (uitgestelde spike) Code-kennisgraaf evalueren` | analysis | — | Understand-Anything strikt voor *code*-navigatie; alleen trekken als code (niet docs) een gemeten pijn wordt |

Kaart 2 hangt op kaart 1 (heeft frontmatter nodig om uit te genereren). Kaart 3 is
onafhankelijk en bewust `not_feasible`-baar: het is de bewaarde "andere poot" van de fork,
niet een commitment.

## Bronnen (webonderzoek 2026-07-18)

- [OKF — Open Knowledge Format](https://okf.md) — git-native, agent-first markdown-conventie.
- [Egonex-AI/Understand-Anything](https://github.com/Egonex-AI/Understand-Anything) — codebase → interactieve kennisgraaf (tree-sitter + LLM).
- [AI Documentation Tools 2026 (CallMissed)](https://www.callmissed.com/en/blog/ai-documentation-tools-2026) — twee-lezer-principe, llms.txt.
- [Optimizing API docs for AI agents — llms.txt guide (Fern)](https://buildwithfern.com/post/optimizing-api-docs-ai-agents-llms-txt-guide)
- [How to Structure Projects for AI Agents and LLMs (Mastra)](https://mastra.ai/blog/how-to-structure-projects-for-ai-agents-and-llms)
- [Reliable Graph-RAG for Codebases: AST-Derived vs LLM-Extracted KGs (arXiv)](https://arxiv.org/pdf/2601.08773) — token/kwaliteit-tradeoff.
- [Knowledge Base vs Knowledge Graph for LLM Systems (Kloia)](https://www.kloia.com/blog/knowledge-base-vs-knowledge-graph-llm) — hybride is de norm.
