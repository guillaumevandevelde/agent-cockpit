---
title: "FCR-reviewer krijgt expliciete commitreferentie in geïsoleerde worktree"
type: design
status: approved
---

# FCR-reviewer krijgt expliciete commitreferentie in geïsoleerde worktree

**Datum:** 2026-07-22
**Kaart:** `491c7ba1bb7b4843ae3c2fed88a6ba20`
**Ship mode:** `direct`
**Iteratie:** 1 (single-shot fix)

## Probleem

De huidige FCR-prompt (zowel in `.claude/agents/engineer.md` §6 als
geïnlined in `backend/app/kanban/dispatch.py::_build_ship_instructions`)
geeft als enige "diff"-input mee: "**de committed diff tegen
`origin/master`**". Concrete observatie (kaart `491c7ba1`):

- Een FCR draait in een **verse worktree op `origin/master`** (de
  detached-worktree pre-flight in `_build_ship_instructions` doet
  precies dát). In die worktree is `HEAD == origin/master`; de werkboom
  is clean. Wanneer de reviewer "de diff tegen origin/master" probeert
  te bepalen vanuit zíjn eigen `HEAD`, ziet hij **een lege diff** en
  concludeert dan vals "implementatie ontbreekt" — precies het
  tegenovergestelde van wat er werkelijk staat (in een branch-werkboom
  verderop in de lineage).
- De reviewer heeft geen enkele anchor die hem vertelt **welke
  commit** de implementatie bevat. `HEAD` is ongeschikt (zie boven);
  een tag/range zou kunnen, maar is fragiel onder merged branches. De
  enige robuuste oplossing is een **immutable commit-hash** als input.
- Een tweede FCR met een nu-wél-meegegeven commit-hash werd toen
  alsnog gedraaid — duplicaat werk, vertraging, dubbele review-context.

## Doel

De FCR-subagent ontvangt de **immutable commit-hash** van de
implementatie als verplichte input, krijgt expliciete commando's om
die commit te reproduceren (`git show <HASH>`,
`git diff origin/master..<HASH>`), en weigert een content-oordeel als
de hash ontbreekt of niet resolveert (actionable foutmelding, geen
vals OK/NIET OK).

## Acceptatiecriteria (uit kaart 491c7ba1, verbatim)

1. **FCR-instructies bevatten expliciet de huidige commit-hash.**
2. **Een reviewer in een verse worktree kan de committed diff
   reproduceren via `git show` en `git diff`.**
3. **Een stale reviewer-`HEAD` leidt niet langer tot een vals
   "implementatie ontbreekt"-oordeel.**
4. **`git show` / `git diff` zijn expliciet in de FCR-prompt
   aanwezig** (letterlijke commando-vorm).
5. **Als de hash ontbreekt of niet resolveert → actionable error,
   geen content-oordeel.**

## Aanpak — minimale, getargete prompt-uitbreiding

### Wijziging #1 — engineer-persona §6 FCR-block (`engineer.md`)

Twee replacements in het FCR-blok tussen L136 en L206:

1. **Invoerzin** (rond L140):

   OUD: `... en de committed diff tegen `origin/master`.`
   NIEUW: `... en — expliciet — de huidige commit-hash die de
          implementatie bevat (typisch `git rev-parse HEAD`,
          literal meegegeven door de engineer; default: voor een
          sessie die net een FCR-triggerende commit heeft gemaakt).`

2. **Subagent-prompt blockquote** (rond L167–L181): de literal prompt
   die de engineer uitvoert, krijgt twee nieuwe clausules:

   - "**Bron-van-waarheid: de commit-hash, niet je eigen HEAD of de
     werkboom-state.**" + de twee reproduceer-commando's.
   - "**Actionable refusal als de hash ontbreekt of niet resolveert.**"
     + regel dat een lege diff met non-empty requirements per
     definitie een blokkade is.

### Wijziging #2 — dispatch-prompt (`dispatch.py::_build_ship_instructions`)

Byte-identiek dezelfde wijziging in `feature_compliance_review` (rond
L1951–L2011). De drift-guard
(`backend/tests/test_fcr_prompt_drift.py`) dwingt af dat beide
mirrors in dezelfde commit wijzigen — anders faalt de test luid.

### Wijziging #3 — drift-guard invariants + coverage-sanity test

In `backend/tests/test_fcr_prompt_drift.py`:

- Bestaande invariant `"input: diff against origin/master"` met anchor
  `"diff tegen"` wordt **vervangen** door twee new-and-tighter
  invariants:
  - `"input: explicit commit hash"` → anchor `"commit-hash"`
  - `"input: SHA-anchored diff command against origin/master"` →
    anchor `"origin/master.."`
- Twee nieuwe contract-invariants worden toegevoegd:
  - `"reproducibility command: git show <commit-hash>"` → anchor
    `"git show"`
  - `"missing/unresolvable commit-hash → actionable refusal"` →
    anchor `"unresolvable commit-hash"`
- `test_fcr_invariants_list_covers_the_required_inputs.required_inputs`
  wordt uitgebreid met `"input: explicit commit hash"` en `"input:
  SHA-anchored diff command against origin/master"`.

Totaal aantal invariants in de lijst: 13 → 16.

### Wijziging #4 — bron-analysedoc footnote

In `docs/cockpit/reviewer-agent-decision.md`, in §"Wat lost de
feature-compliance-review op?" (rond L99–L117), wordt een korte
`✅ Geïmplementeerd (kaart 491c7ba1…)-regel toegevoegd die meldt dat
de FCR-implementatie is verfijnd met expliciete commit-hash +
reproduceer-commando's + actionable refusal. De oorspronkelijke
prompt-quote in dat doc blijft staan als historisch ontwerp-anker;
de live-tekst leeft in de mirrors (`engineer.md` + `dispatch.py`)
en valt daardoor onder de drift-guard.

### NIET gewijzigd

- **Geen runtime-code** buiten de dispatch-prompt zelf (de FCR-call
  was al een subagent-call met cleared context; de SHA wordt door
  de engineer ingevuld in de prompt, niet door code gegenereerd).
- **Geen nieuwe test fixtures of scratch-repo's** — de
  `test_fcr_prompt_drift.py` is al een in-process parametrised
  test; geen extern mock-subsysteem nodig.
- **Geen nieuwe commit-message-conventie** — bestaande
  `Conventional Commits`-stijl blijft (zie CLAUDE.md en
  `docs/cockpit/git-workflow.md`).
- **`scripts/run-single-test.sh`** wordt gebruikt voor de targeted
  run; **geen lokale full pytest** (zie CLAUDE.md "feedback_no_local_pytest").

## Hoe de engineer de SHA resolved

De engineer-sessie resolved de SHA **vlak vóór het spawnen van de
FCR-subagent** met `git rev-parse HEAD`. Dat is:

1. Alleen beschikbaar nadat de FCR-triggerende commit is gemaakt
   (stap 3 van het ship-workflow).
2. Letterlijk ingevuld in de prompt-substitutie `<COMMIT_HASH>` →
   de reviewer leest de SHA als gewone tekst en voert `git show <SHA>`
   uit.
3. Faalt de `git rev-parse HEAD`-call zelf (bv. detached HEAD zonder
   commit? onwaarschijnlijk na stap 3), dan geeft de engineer een
   actionable error aan zichzelf en **gaat niet door met de FCR**.

In dispatch-mode krijgt de gedispatchte sessie dezelfde instructie:
"vul `<COMMIT_HASH>` in met `git rev-parse HEAD`". Dat werkt omdat de
gedispatchte sessie op dat moment in een werkboom zit waar de commit
al is gemaakt (stap 3 van het dispatch-ship-workflow is al vóór stap
4+FCR uitgevoerd — de FCR is namelijk *pre-ship*, na de commit maar
vóór de merge).

## Hoe een reviewer in een vers-`origin/master`-werkboom het kan reproduceren

```bash
# In de FCR-subagent-sessie (cleared context, verse worktree):
HASH="<de literal ingevulde commit-hash>"
git show "$HASH"                             # files & changes in de commit
git diff origin/master.."$HASH"              # cumulatieve delta tegen baseline
```

Als een van beide commando's leeg is waar implementatie te
verwachten is, of `$HASH` niet resolveert via `git show`: stop met
een actionable foutmelding ("`unresolvable commit-hash: <HASH>`")
en geen content-oordeel. Een lege diff met non-empty requirements
is per definitie een reviewer-blokkade, geen OK.

## Teststrategie

1. **TDD-first.** Eerst de vier nieuwe invariants +
   required_inputs-uitbreiding in
   `test_fcr_prompt_drift.py` toevoegen, gerund via
   `scripts/run-single-test.sh backend/tests/test_fcr_prompt_drift.py`
   → verwacht: 4 × 3 = 12 nieuwe failures, 1 sanity-failure (vereiste
   labels), elke failure message noemt mirror + label + anchor.
2. **Mirror-updates in één commit.** Zowel `engineer.md` als
   `dispatch.py` krijgen de nieuwe FCR-prompt in dezelfde commit —
   anders faalt de drift-guard op een van beide mirrors en niet op
   de ander (wat de drift-val is die de guard juist moet vangen).
3. **Re-run targeted test.** Alle 16 invariants × 3 mirrors = 48
   presence-tests moeten groen zijn; sanity-tests
   (`test_fcr_step_runs_before_ship_workflow`,
   `test_fcr_invariants_list_covers_the_required_inputs`,
   `test_drift_detector_fails_when_mirror_loses_a_substring`,
   `test_engineer_md_fcr_step_lives_in_review_section`) moeten ook
   groen zijn.
4. **Geen lokale full pytest** — deze één test-file is de targeted
   gate; CI (`quality.yml`) vangt de rest (zie CLAUDE.md §"feedback_no_local_pytest").
5. **Iteration-loop `verify` preset.** Na de targeted groen-run,
   draait de engineer de `iteration-loop` skill met preset `verify`
   voor de standaard end-of-kaart quality-sweep (zie CLAUDE.md,
   `engineer.md` §6).

## Bron + drift-val

`engineer.md` §6 en `dispatch.py::_build_ship_instructions` zijn al
bewust gedupliceerd (zie commentaar in beide files + drift-guard
`test_fcr_prompt_drift.py` docstring). Dit is by design: een
freshly-spawned agent heeft mogelijk geen filesystem-access om
`.claude/agents/` te lezen, dus de canonieke FCR-prompt wordt ook
inlined in de dispatch-prompt. De drift-guard dwingt sync af; een
edit die één mirror vergeet wordt luid geweigerd.

Zie ook:
- `backend/app/kanban/dispatch.py::_build_ship_instructions` L1926–L1941 —
  docstring + drift-warning commentaar.
- `engineer.md` L202–L206 — drift-warning commentaar.
- `backend/tests/test_fcr_prompt_drift.py` — drift-guard + invariants.

## Ship-stappen (per CLAUDE.md + kaart `491c7ba1`)

1. Commit implementatie + tests in één commit (`FCR…` of
   `feat(kanban): …`; conventionele stijl).
2. Pre-Done FCR in **cleared context** tegen de committed diff
   (immutable SHA — `git rev-parse HEAD` van de implementatie-commit).
   De FCR gebruikt dezelfde nieuwe prompt; de
   acceptance-criteria zijn de "specificatie" ervan.
3. Als de FCR-blockerende issues oplevert: fix in dezelfde sessie,
   re-commit, herhaal FCR tot `OK`.
4. `git fetch origin` (in de worktree); merge door een detached
   throwaway worktree op `origin/master`; `push origin HEAD:master`.
5. `attach_deliverable(kind="branch", ref=<branch-name>)`.
6. Draai `session-retro`.
7. `move_card Done` met product-language summary (kanban-conventie §5).
