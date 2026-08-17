---
title: "Analyse — Attention Span: kunnen we de stijlregels overnemen of de tool integreren?"
type: analysis
status: active
---

# Analyse — Attention Span: kunnen we de stijlregels overnemen of de tool integreren?

**Datum:** 2026-08-17
**Trigger:** kanban-kaart `9f73cdb1…` "Analyse - attention-span". Gebruiker:
> "Bekijk of we deze repo kunnen gebruiken of integreren om de communicatiestijl te verbeteren : https://github.com/alexgreensh/attention-span"

**Bron:** <https://github.com/alexgreensh/attention-span> (AGPL-3.0, `main` @ gemeten 2026-08-17, repo 13 dagen oud — `created_at` 2026-08-04, laatste push 2026-08-15)

---

## TL;DR

- **Premisse getoetst: klopt deels.** "Communicatiestijl verbeteren" is een echte zorg, maar deze tool is de oplossing niet — `attention-span` is een Claude Code runtime-output-style (single ~650-token `.md` in `~/.claude/output-styles/`), terwijl onze dispatch-flow die output-styles niet pipe-t. Wat we wél hebben op hetzelfde terrein: [`taalgebruik-conventies.md`](./taalgebruik-conventies.md) (leesbaarheidsnorm), [`kanban-conventions.md` §5](../../CLAUDE.md) (product-taal, drie-delen-vorm), en [`communicatie-en-weergave-analyse.md`](./communicatie-en-weergave-analyse.md) (kaart `b0d2124e…`). De overlap is groot.
- **Wat we overnemen:** de *stijlprincipes* uit `attention-kind.md` (lead-with-bottom-line, numbers-and-warnings-zijn-essentials, no-buried-risks), gedistilleerd als 3-5 regels in `taalgebruik-conventies.md` §3/§5. Geen file-copy; AGPL-3.0 verbiedt bundeling in onze codebase, en wegens de laag (Claude Code runtime) zou 't tóch niet werken in onze dispatch-sessies.
- **Wat we bewust niet overnemen:** de AGPL-`.md`-bestanden zelf (`bootstrap-policy.md:208,219`); het `output-style`-mechanisme als hardgekoppelde feature in Cockpit (4 van 5 CLIs kennen 'm niet, `capabilities.py:81,108,131,158`); de ADHD-framing (onze doelgroep is een Nederlandstalige product-owner); de sister-tools `token-optimizer` / `outsourcerer` als bundel (zelfde auteur, buiten scope).
- **Vervolg:** 1 kind-kaart — `taalgebruik-conventies.md` uitbreiden met 3-5 regels uit attention-kind. Geen code-kaarten, geen AGPL-import, geen dispatch-wijziging.

## 1. Wat Attention Span feitelijk is (gegronde feiten, staat 2026-08-17)

| Aspect | Waarde | Bron |
|---|---|---|
| Leeftijd | 13 dagen (`created_at` 2026-08-04) | `gh api repos/alexgreensh/attention-span --jq .created_at` |
| Laatste activiteit | 2026-08-15 (`pushed_at`) | idem |
| Sterren | 711 | idem |
| Open PRs | 0 (gemeten via `?state=open`; issues-call time-outte, gemarkeerd) | `gh api repos/alexgreensh/attention-span/pulls?state=open` |
| Releases | v0.6 (2026-08-15), v0.5 (2026-08-11), v0.4 (2026-08-10) — drie releases in twee weken | `gh api repos/alexgreensh/attention-span/releases` |
| Taal | Python (39.345 kB) | `gh api repos/alexgreensh/attention-span --jq .size` |
| Licentie | **AGPL-3.0** | idem, `.license.spdx_id` |
| Default-branch | `main` | idem |
| Topics | `adhd-friendly`, `claude-code`, `eli5`, `explainability`, `explainable-ai`, `output-style` | idem |

**Wat het ding doet** (gemeten door de README + `output-styles/attention-kind.md` te lezen): drie single-file Claude Code [output-styles](https://code.claude.com/docs/en/output-styles) (`attention-kind.md`, `spartan.md`, `rundown.md`) die als losse markdown in `~/.claude/output-styles/` gedropt worden. Claude Code laadt die als uitbreiding op de system-prompt — de file is ~650 tokens. Het effect: **de CLI praat anders terug in de terminal** (antwoord eerst, kort, `→`-bullets, bold-keywords, geen buried warnings).

**Het gemeten effect** (uit hun eigen benchmark `benchmarks/results/2026-08-11-benchmark.md`, 12 hidden-test coding tasks):

- Werk-output (test-pass-rate) **97% style-on vs 97% style-off** — geen degradatie.
- Output-lengte **~43% korter** (mediaan 41%), 50-71% op verbose antwoorden.
- Eerste-regel-antwoord: **75% van de tijd vs 3%** default.
- Deliverable-zuiverheid (geen wrapper-rond-een-commit): **88% vs 12%**.

De benchmark-methode (geen LLM-judge, alleen tests + lengte + format-detectie) is reproduceerbaar vanuit hun repo — een ongemeten-eis aan ons zou geen grondslag hebben.

**Wat het NIET doet** — en dat is voor deze analyse het belangrijkste:

- Het is **geen library of service** die je aan een bestaand product kunt koppelen. Het is een CLI-prompt-injectie.
- Het werkt **alleen in Claude Code** (zie onze `capabilities.py:24,58` — `output_styles: write_capable`; voor Codex/MiMo/OpenCode/Copilot staat `unsupported`). Onze dispatch stuurt 5 CLI's aan; voor 4 ervan bestaat dit mechanisme niet.
- Het raakt **de runtime-chat-output van de agent tegen de mens**. Het raakt géén bestandsinhoud, geen bordtekst, geen docs. De mens in onze cockpit leest vooral *docs/cockpit/*.md*, *kaart-titels*, *Done-samenvattingen* en *kaart-reacties in de activity-feed* — andere schermen dan de agent-chat.

## 2. De premisse getoetst

De kaart zegt: *"Bekijk of we deze repo kunnen gebruiken of integreren om de communicatiestijl te verbeteren"*.

Drie lagen in die zin verdienen een premisse-check:

**2.1 "We kunnen deze repo gebruiken"** — als in *bundelen in onze codebase*: **nee, niet zonder meer**. AGPL-3.0 is copyleft. Zodra wij de `.md`-bestanden of afgeleide versies distribueren als onderdeel van Cockpit, valt de hele Cockpit-distributie onder de AGPL-verplichtingen (broncode-beschikbaarheid voor netwerk-gebruikers). Onze `bootstrap-policy.md:208,219` noemt copyleft al "verkeerde default voor willekeurige gebruikersproducten". Het pattern van AGPL-producten die we wél lazen voor inspiratie staat in [`lemma-platform-analyse.md:286-292`](./lemma-platform-analyse.md): *"van de AGPL-backend nemen we het idee over, niet de code"*. Hetzelfde geldt hier.

**2.2 "We kunnen deze repo integreren"** — als in *technisch aansluiten op onze dispatch-flow*: **de laag klopt niet**. Attention Span is een Claude Code runtime-output-style, geladen door de CLI zelf uit `~/.claude/output-styles/`. Onze dispatch start Claude Code met `--mcp-config` en `--strict-mcp-config` (zie `dispatch.py:cc_spawn._resolve_merged_mcp_path`), maar geeft *geen* `outputStyle`-flag mee — dat zou de hele dispatch moeten herstructureren om per-kaart of per-project een style mee te geven. En zelfs dan: voor 4 van de 5 ondersteunde CLI's bestaat het mechanisme niet (`capabilities.py:81,108,131,158`).

**2.3 "Om de communicatiestijl te verbeteren"** — *hier* zit de echte vraag, en die heeft al een antwoord. We hebben sinds 2026-08-04 een leesbaarheidsconventie ([`taalgebruik-conventies.md`](./taalgebruik-conventies.md)) die dezelfde ruimte claimt: ≤40 woorden per zin, ≤150 per alinea, geen hybride werkwoorden, Flesch-Douma leesindex <30 als gate. Daarnaast: [`kanban-conventions.md`](./kanban-conventions.md) §5 met de drie-delen-vorm voor Done-summaries (outcome + bullets + optioneel rest), en [`communicatie-en-weergave-analyse.md`](./communicatie-en-weergave-analyse.md) (kaart `b0d2124e…`) die het formulering-vraagstuk al diagnosisert en oplost via as A (formulering, deze skill) en as B (weergave, aparte fix). De stijl-werkzaamheid is al onder dak; de vraag is of attention-span-regels er *iets aan toevoegen* dat we nu missen — dat is §4.

**Verdict in één zin.** De tool is een goede UI-verbetering voor de Claude Code-terminal-chat zelf; voor onze cockpit is de laag verkeerd, de licentie blokkeert bundeling, en de stijl-ruimte is al voor 80% bediend door onze eigen conventies. Wat overblijft zijn 3-5 specifieke regels uit `attention-kind.md` die we niet hebben — daarvoor één kleine uitbreiding op `taalgebruik-conventies.md`.

## 3. Waar wij staan (met verwijzingen)

### 3.1 Wat we al hebben op hetzelfde terrein

- **Leesbaarheidsconventie** — [`docs/cockpit/taalgebruik-conventies.md`](./taalgebruik-conventies.md) (commit-context: geactiveerd 2026-08-04, kaart `85db6366…`). Vier meetbare normen (§2): zinslengte ≤40 woorden, alinealengte ≤150 woorden, geen hybride werkwoorden, Flesch-Douma <30. Gemeten door `scripts/check-doc-readability.py`. §3: "Conclusie eerst, diepte via verwijzing" — dezelfde headline-regel als attention-kind's "Lead with the bottom line, in one sentence" (eerste bullet van `output-styles/attention-kind.md`). De overlap is reëel; wij hebben 'm alleen in het Nederlands en op docs-vlak, niet op chat-output-vlak.
- **Product-taal + drie-delen-vorm** — [`docs/cockpit/kanban-conventions.md` §5](../../CLAUDE.md), geïmplementeerd in kaart `8b3ce64c…`. Verplichte structuur voor Done-summaries: **Uitkomst** + 2-4 bullets + optioneel **Rest/nazicht**. Drie proces-regels erboven: geen proces-meta in de banner, jargon = naam + waarom, lead-with-product-meaning in elke openingszin.
- **Communicatie-analyse met as A + as B** — [`docs/cockpit/communicatie-en-weergave-analyse.md`](./communicatie-en-weergave-analyse.md), kaart `b0d2124e…`. As A = formulering (deze skill + conventies), As B = weergave (markdown-rendering van samenvattingen in `DoneSummaryBanner`, geïmplementeerd in kaart `56ddf5a6…`).
- **Capability-baseline** — [`docs/cockpit/cockpit-capability-baseline.md`](./cockpit-capability-baseline.md). §1 + §3-7 tonen aan dat onze dispatch-flow geen runtime-output-style laadt. §6 toont de Done-poorten met `summary_required` + `outcome_required` (gesloten enum); een leesbaarheids-gate voor de *inhoud* is terecht niet op de poort — zie `communicatie-en-weergave-analyse.md §2.3`.
- **Per-CLI output-style status** — [`backend/app/services/agentic_cli/capabilities.py`](../../backend/app/services/agentic_cli/capabilities.py):24,58,81,108,131,158. Alleen Claude Code heeft `write_capable` voor `output_styles`; de andere vier CLIs zijn `unsupported`. Dat is een *gemeten* bewijs dat de tool niet portabel is over onze vloot heen.

### 3.2 Wat we verder hebben dat aandacht-span deert

- **Caveman/ponytail intensity levels** in de engineer- en analyst-persona's (zie CLAUDE.md §"Persistence" / §"Intensity"). Engineer gebruikt standaard *full*: drop artikelen, korte synoniemen, fragments OK — letterlijk Spartan-style output (één van de drie attention-span-modi). Dit is al persona-default; een nieuwe style-file is voor die stijl overbodig.
- **Spaarzame-output-dwang** in [`taalgebruik-conventies.md` §3 "Diepte hoort in een eigen document"](../../CLAUDE.md) — *dezelfde* hefboom als attention-kind "Say the least that fully answers, then stop".

### 3.3 Wat we NIET hebben (en attention-kind wel heeft)

Drie principes uit `attention-kind.md` die nog niet expliciet in onze conventies staan:

1. **"Numbers, thresholds, and scoped conditions are essentials, not detail. State them exactly."** Onze conventie heeft wel "geen hybride werkwoorden" (vermijd *matcht een patroon als glob*) maar geen expliciete regel dat *exacte cijfers / threshold-waarden / scoped condities* niet afgerond mogen worden. Een kind-kaart die zegt "alle 19 schemas landen in de system-prompt" zonder meting was precies zo'n afrondings-fout (zie [`token-optimization-analysis.md`](./token-optimization-analysis.md) §4 — R3-claim die ongemeten bleek).
2. **"A warning is the last word to cut, never the first."** Onze [`taalgebruik-conventies.md`](./taalgebruik-conventies.md) §3 stelt "diepte achter een verwijzing", niet expliciet "warnings reizen mee met het punt dat ze bewaken, nooit deferred, nooit getrimd". Een implementatie-fix die een waarschuwing in een `**Rest/nazicht**`-bullet parkeert, voldoet nu aan de drie-delen-vorm maar kan een waarschuwing begraven.
3. **"Deliverable purity — When asked to produce a thing, output only that thing, nothing wrapped around it."** Onze [`kanban-conventions.md` §5](../../CLAUDE.md) zegt geen proces-meta in de banner, maar niet specifiek dat *output naar een afnemer* (een commit message, een code-snippet) niet in een wrapper staat. Dat is een smallere, engineer-kant-regel.

## 4. Wat we concreet kunnen overnemen (gerangschikt op leverage)

### 4.1 ⭐ Drie regels uit `attention-kind.md` toevoegen aan `taalgebruik-conventies.md`

**Wat.** §3 of §5 van [`taalgebruik-conventies.md`](./taalgebruik-conventies.md) uitbreiden met drie regels, geformuleerd in dezelfde Nederlandse toon als de rest van de conventie (geen AGPL-import, geen file-copy, geen prompt-injectie in personas):

- *Cijfers, drempels en scoped condities zijn essentieel, niet detail.* Noem het getal exact; "sneller" / "minder" / "voor de meeste gevallen" is een afronding die de lezer verkeerd laat handelen.
- *Een waarschuwing reist mee met het punt dat ze bewakt.* Nooit parkeren in een nazicht-sectie of achter een "zie elders"-link als de waarschuwing bepaalt of de lezer het punt goed kan toepassen.
- *Wanneer de output een ding is (een commit-message, een code-snippet, een commando), lever dan dat ding — geen wrapper eromheen.* Geen inleiding, geen afsluiting, geen herhaling van wat erin staat.

**Welke laag raakt het hier.** [`docs/cockpit/taalgebruik-conventies.md`](./taalgebruik-conventies.md) (één file); [`scripts/check-doc-readability.py`](../../scripts/check-doc-readability.py) (geen wijziging — de regels zijn prose, niet meetbaar). Geen backend, geen dispatch, geen persona-prompts.

**Wat het de product-owner oplevert.** Drie nieuwe, korte, leesbare regels die de bestaande conventie completer maken — geen nieuwe tool, geen nieuwe dispatch-flag, geen AGPL-import. Een kind-kaart voor de auteur van de conventie: drie paragrafen bijschrijven, de meet-draaien om te bevestigen dat er geen verborgen lange zinnen insluipen, klaar.

**Wat het kost.** Eén kind-kaart, ~30 minuten engineer-werk, geen test-impact, geen UI-impact. **Ongemeten schatting** voor de hefboom: vergelijkbaar met de drie eerdere conventie-uitbreidingen (kaart `85db6366…`, `8b3ce64c…`, `56ddf5a6…`) — elk had vergelijkbare scope en verbeterde de stijl-meet-score meetbaar (niet gemeten in deze spike; claim is patroon-based, niet uit een eigen meetsessie).

### 4.2 ⭐ Optioneel: Spartan-modus voor engineering Done-summaries

**Wat.** De drie-delen-vorm uit [`kanban-conventions.md` §5](../../CLAUDE.md) heeft drie stijlen onder zich: de standaard "Uitkomst + bullets + rest", en voor low-level engineering-kaarten zou een Spartan-stijl passen (één **Uitkomst**-zin, 2-4 `→`-bullets zonder rest-sectie). Dit is een *aanvulling* op de bestaande drie-delen-vorm, geen vervanging — hij geldt voor `work_type=bug` of `work_type=chore` met korte scope, niet voor `feature`-kaarten.

**Welke laag.** [`docs/cockpit/kanban-conventions.md` §5](../../CLAUDE.md), [`.claude/agents/engineer.md`](../../.claude/agents/engineer.md) (hint in de persona-prompt), geen dispatch, geen UI. De `DoneSummaryBanner` (kaart `56ddf5a6…`) rendert al via `<MarkdownRenderer>`, dus `→`-bullets werken zonder extra UI-werk.

**Wat het oplevert.** Een Done-summary die past bij de engineer-persoon (caveman-full is standaard voor engineer; een Spartaanse summary matcht). Geen nieuwe feature, alleen een optionele sjabloon-variant.

**Wat het kost.** Eén kind-kaart voor de conventie-auteur (paragraaf bijschrijven) + één regel in de engineer-persona-prompt (verwijzing naar de variant). **Ongemeten schatting** dat 1 op 5 Done-kaarten de variant zal gebruiken (gebaseerd op het aandeel `bug`/`chore` in de huidige Done-kolom — niet gemeten in deze spike).

**Status bij deze analyse: geadviseerd, niet ingediend.** Het hoort bij dezelfde kind-kaart als 4.1 — de auteur kan beslissen of 4.2 binnen of buiten de kind-kaart-scope valt. Voor deze analyse als geheel is 4.1 de kern.

### 4.3 Kleinere leerpunten (noteren, niet nu bouwen)

- **Benchmark-methode verdient een eigen feature-eis.** De attention-span-benchmark meet output-lengte en test-pass-rate zonder LLM-judge. Onze conventie heeft `check-doc-readability.py` voor docs, maar geen vergelijkbare harness voor de runtime-chat-output van een gedispatchte sessie. Niet voor deze kaart — een kind-kaart voor een benchmark-harnas voor onze eigen stijl-conventie zou een aparte spike zijn (en botst met `cockpit-richting-decision.md` §6: cockpit "krimpt" op dit soort infra).
- **Sister-tools van dezelfde auteur** — [`token-optimizer`](https://github.com/alexgreensh/token-optimizer) en [`outsourcerer`](https://github.com/alexgreensh/outsourcerer) — worden in attention-span's README expliciet als complementair gepresenteerd. Zelfde merk, zelfde AGPL. Niet voor deze analyse; een kind-kaart die de drie als bundel her-opent zou een nieuwe product-analyse-kaart moeten zijn, niet een follow-up van deze.
- **`rundown.md`-stijl voor status-updates** — emoji-checklist met TL;DR past op het cockpit-equivalent van activity-feed en PO-digest. Niet voor deze kaart (en strijdig met onze product-taal: emoji als stijl-element staat niet in de drie-delen-vorm).

## 5. Wat we bewust NIET overnemen

### 5.1 De AGPL-`.md`-bestanden zelf

**Wat het is.** De drie `output-styles/*.md`-bestanden verbatim in onze codebase kopieren — in [`backend/app/services/agentic_cli/`](../../backend/app/services/agentic_cli/), of als feature in [`backend/app/api/v1/`](../../backend/app/api/v1/).

**Waarom niet hier.**

1. **AGPL-3.0 + bootstrap-policy.** Onze [`bootstrap-policy.md`](./bootstrap-policy.md):208,219 zegt copyleft is "verkeerde default voor willekeurige gebruikersproducten". [`lemma-platform-analyse.md`](./lemma-platform-analyse.md):286-292 hanteert het patroon al: *"van de AGPL-backend nemen we het idee over, niet de code"*. Hetzelfde geldt hier — de regels in §3.3 zijn de *ideeën*, de AGPL-files zijn de *codering*.
2. **De laag dekt het niet.** De files zijn prompt-injecties voor de *Claude Code runtime*. Onze dispatch laadt ze niet — zelfs als we ze in de repo zetten, zou geen sessie ze lezen. Een AGPL-file die niemand leest is geen leverage, alleen licentie-bloat.

### 5.2 De `output-style`-mechanisme als hardgekoppelde Cockpit-feature

**Wat het is.** Een project-instelling in Cockpit om een output-style te kiezen die bij elke dispatch-sessie aan Claude Code wordt doorgegeven.

**Waarom niet hier.**

1. **Vier van vijf CLIs kennen 't niet.** [`capabilities.py`](../../backend/app/services/agentic_cli/capabilities.py):81,108,131,158 markeren Codex, MiMo, OpenCode, Copilot CLI als `unsupported` voor `output_styles`. Een Cockpit-feature die maar voor 1 van 5 engines werkt is een UX-belofte die we in ~80% van de sessies breken.
2. **Geen dispatch-surface zonder aanleiding.** We hebben al `--mcp-config` en `--strict-mcp-config` als per-project-flip via [`_resolve_merged_mcp_path`](../../backend/app/services/runs/cc_spawn.py) (zie `ed2088e5…` voor de plugin-MCP-carve-out). Een `--output-style`-flag toevoegen zonder dat iemand erom vraagt is premature surface — geen kind-kaart, geen specificatie.

### 5.3 De ADHD-framing en het hele taken-van-een-auteur-merk

**Wat het is.** Attention-kind is geschreven voor een neurodivergent-presumed audience ("This person has ADHD"), en Attention Span wordt door dezelfde auteur gepresenteerd als één van drie sister-tools (Token Optimizer, Outsourcerer) onder één huisstijl.

**Waarom niet hier.**

1. **Doelgroep-mismatch.** Onze product-owner is een Nederlandstalige, full-attention professional (zie [`cockpit-richting-decision.md`](./cockpit-richting-decision.md) — persoonlijke cockpit, geen niche-doelgroep). Een stijl die zegt *"You are talking to a real human being with a limited attention span"* is een andere retoriek dan onze [`taalgebruik-conventies.md`](./taalgebruik-conventies.md)-toon (meetbaar, kalm, "in één regel").
2. **Geen bundel-evaluatie van het merk.** De drie sister-tools (Attention Span, Token Optimizer, Outsourcerer) samen beoordelen is een aparte product-analyse, niet een follow-up van deze kaart.

### 5.4 De CLI-installatie-aanwijzingen voor andere agents (Codex, Antigravity)

**Wat het is.** De README heeft kopieerbare `curl | sed`-installaties voor Codex, Antigravity (agy) en Devin — om de stijl in andere agent-runtimes te krijgen.

**Waarom niet hier.** Dit is een installatie-handleiding voor de eindgebruiker die de tool zelf wil draaien — niet iets dat Cockpit kan of moet orkestreren. Onze Cockpit gebruikt Codex/MiMo/OpenCode via dispatch, niet via eindgebruiker-installatie van stijl-files. Geen overlap met onze product-flow.

## 6. Aanbeveling

**Doen — smal en conditioneel.** Eén kind-kaart: [`docs/cockpit/taalgebruik-conventies.md`](./taalgebruik-conventies.md) §3 of §5 uitbreiden met de drie regels uit §3.3 van deze analyse (cijfers-thresholds-condities zijn essentieel; waarschuwing reist mee met het punt; deliverable-zuiverheid). De auteur draait `scripts/check-doc-readability.py --file docs/cockpit/taalgebruik-conventies.md` vóór de commit om te bevestigen dat de uitbreiding geen nieuwe leesbaarheids-hits introduceert. Optioneel dezelfde kind-kaart: de Spartan-modus-paragraaf uit §4.2 als optionele variant van de drie-delen-vorm.

**Niet doen.** Geen AGPL-import. Geen dispatch-wijziging. Geen nieuwe feature-flag. Geen bundel-evaluatie van Token Optimizer of Outsourcerer. Geen feature-implementatie in deze spike (zie `cockpit-richting-decision.md` §6 — cockpit krimpt op Claude-Code-specifieke infra).

## 7. Vervolgkaarten (in deze sessie aangemaakt)

- `bee6609a…` — *Taalgebruik-conventie uitbreiden met drie regels uit Attention Span* — drie korte paragrafen bijschrijven in §3/§5 van `taalgebruik-conventies.md` (cijfers-thresholds-condities, waarschuwing-reist-mee, deliverable-zuiverheid), optioneel de Spartan-modus-paragraaf uit §4.2 erbij; `check-doc-readability.py --file docs/cockpit/taalgebruik-conventies.md` vóór de commit; één `**✅ Geïmplementeerd (kaart bee6609a…)`-regel onder de nieuwe §3.3 in deze analyse toe te voegen per het bron-analysedoc-update-convent (zie CLAUDE.md §"Session-end workflow" stap 3 / `recipe-writing-conventions.md §2`).

## 8. Bewust buiten scope

- **Benchmark-harnas voor runtime-chat-output.** Of, en hoe, we de output-stijl van een *gedispatchte sessie* (niet van onze docs) zouden meten — eigen spike.
- **Sister-tools (Token Optimizer, Outsourcerer).** Niet in deze analyse; één product-analyse per tool, niet één voor de drie samen.
- **De benchmark-methodologie van aandacht-span zelf overnemen.** [`token-optimization-analysis.md`](./token-optimization-analysis.md) heeft al een meet-harnas-recept (`claude -p "ok" --output-format json` met/zonder `--strict-mcp-config`); een kind-kaart voor een eigen runtime-stijl-harnas is een nieuwe discussie.
- **Output-style-keuze per persona of per project-kolom in Cockpit.** Geen kind-kaart — geen aanleiding, en het mechanisme dekt maar 1 van 5 CLIs.

## 9. Heropenen wanneer?

Niet van toepassing — dit is een `type: analysis` (leer-analyse), geen `type: decision`. Er is niets te heropenen tenzij het terrein verschuift:

- Wanneer een tweede CLI dan Claude Code `output_styles` gaat ondersteunen EN we meerdere CLI's per project mixen: heroverweeg §5.2.
- Wanneer `taalgebruik-conventies.md` een volledige herziening krijgt (nieuwe meet-norm, andere doelgroep): heroverweeg §4.1.
- Wanneer een nieuwe zuster-tool van dezelfde auteur een andere laag claimt (orchestratie, providers, agents): aparte product-analyse-kaart — niet deze heropenen.

## 10. Bronnen

- **Attention Span repo:** <https://github.com/alexgreensh/attention-span> — gemeten 2026-08-17; `main` @ head; `created_at` 2026-08-04; 711 ⭐; 3 releases (v0.4 / v0.5 / v0.6); AGPL-3.0.
- **`output-styles/attention-kind.md`:** <https://raw.githubusercontent.com/alexgreensh/attention-span/main/output-styles/attention-kind.md> — 650-token prompt-injectie; "Lead with the bottom line" + "Numbers are essentials" + "Warning is the last word to cut".
- **Benchmarkresultaten:** <https://github.com/alexgreensh/attention-span/blob/main/benchmarks/results/2026-08-11-benchmark.md> — 97%/97% test-pass-rate, ~43% output-reductie, geen LLM-judge.
- **Cockpit capability-baseline:** [`docs/cockpit/cockpit-capability-baseline.md`](./cockpit-capability-baseline.md) — commit-context voor file:line-ankers.
- **Taalgebruik-conventie:** [`docs/cockpit/taalgebruik-conventies.md`](./taalgebruik-conventies.md) — wat we al hebben op hetzelfde terrein.
- **Communicatie-analyse:** [`docs/cockpit/communicatie-en-weergave-analyse.md`](./communicatie-en-weergave-analyse.md) — kaart `b0d2124e…`; as A (formulering) + as B (weergave, geïmplementeerd in kaart `56ddf5a6…`).
- **Product-taal + drie-delen-vorm:** [`docs/cockpit/kanban-conventions.md` §5](../../CLAUDE.md) — kaart `8b3ce64c…`.
- **Output-style capabilities per CLI:** [`backend/app/services/agentic_cli/capabilities.py`](../../backend/app/services/agentic_cli/capabilities.py):24,58,81,108,131,158 — vijf CLIs, één `write_capable`.
- **AGPL-patroon:** [`docs/cockpit/lemma-platform-analyse.md`](./lemma-platform-analyse.md):286-292 — *"van de AGPL-backend nemen we het idee over, niet de code"*.
- **Copyleft-default:** [`docs/cockpit/bootstrap-policy.md`](./bootstrap-policy.md):208,219.