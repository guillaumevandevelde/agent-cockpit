---
title: "Recipe-writing conventions — auto-recovery hoort in dezelfde if-tak"
type: reference
status: active
---

# Recipe-writing conventions — auto-recovery hoort in dezelfde if-tak

Dit doc vult het gat dat kanban-kaart `efb8187b…` blootlegde: een
auto-recovery-pad dat **na** een `if ! X; then exit 1; fi`-handler als losse
prose staat, is onbereikbaar in precies het scenario dat het moet afhandelen.
De handler eindigt met `exit 1` en de shell voert de prose nooit uit. De
22 groene drift-tests in `test_ship_recipe_drift.py` zagen dit niet, omdat
die alleen **substring-/presence-** checks waren en geen structurele
validatie doen ("de carve-out-substring staat *iets* in de file" i.p.v.
"in dezelfde uitvoeringspad als de handler die hij moet redden"). De
**positionele** invariant (carve-out fysiek tussen `merge --no-ff` en
`push origin HEAD:master` in dezelfde `if`-blok) is precies de fix die
op §"Hoe validatie werkt" hieronder beschreven staat.

> **Bron van waarheid:** dit doc is leidend voor het schrijven van
> **shell-recipes** in skills, dispatch-prompts, en CI-scripts waar de
> recovery-pad een `if`-guard moet zijn, niet losse prose. Gerelateerd:
> [`kanban-conventions.md`](./kanban-conventions.md) (kanban-strings),
> [`test-doubles-convention.md`](./test-doubles-convention.md) (pytest patches).

## De conventie — één regel

> **Auto-recovery hoort in dezelfde `if`-blok als de fout-detectie, niet als
> prose eronder.** Alleen het geval "geen recovery mogelijk" doet `exit 1`.

Concreet:

- **FOUT** — recovery-stappen (`git checkout --theirs`, regeneratie, `git
  add`, `commit`) na een error-handler die eindigt met `exit 1`. De handler
  is een single-shot guard; de shell stopt bij de `exit` en voert de prose
  nooit uit. Dit was de exacte bug uit kaart `efb8187b…`: de carve-out stond
  in een aparte alinea onder de `if`-handler en was daardoor alleen
  bereikbaar via de tekst-edit, niet via de shell.
- **GOED** — recovery-stappen in een `else`-tak van dezelfde `if` die de
  fout-detectie doet. De fout-detectie zet een vlag of doet een test; de
  `else` voert de recovery uit. Alleen "geen recovery mogelijk" eindigt
  met `exit 1`. Voorbeeld uit `.claude/skills/git-ship/SKILL.md` §4a (het
  canonieke patroon dat deze conventie voortbracht):

  ```bash
  if ! git -C "$WT" merge --no-ff "$BRANCH" -m "Merge $BRANCH"; then
    CONFLICTED=$(git -C "$WT" diff --name-only --diff-filter=U | LC_ALL=C sort -u)
    EXPECTED=$(printf 'docs/cockpit/README.md\ndocs/cockpit/llms.txt\n' | LC_ALL=C sort -u)
    if [ "$CONFLICTED" != "$EXPECTED" ]; then
      echo "ERROR: merge conflict in non-generated files — falling back to report_impediment." >&2
      printf '  conflicted: %s\n' $CONFLICTED >&2
      echo "Conflicted worktree left at $WT for inspection (not removed)." >&2
      exit 1
    fi
    # Carve-out — recovery hoort HIER, vóór een eventuele exit 1:
    git -C "$WT" checkout --theirs -- docs/cockpit/README.md docs/cockpit/llms.txt
    "$WT"/scripts/generate-doc-index.py
    if ! "$WT"/scripts/generate-doc-index.py --check --strict; then
      echo "ERROR: generate-doc-index.py --check --strict failed after regenerate." >&2
      exit 1
    fi
    git -C "$WT" add -A
    git -C "$WT" commit --no-edit
  fi
  ```

  De carve-out staat binnen dezelfde `if ! merge`-blok als de
  fout-detectie, en alleen de "carve-out rejected"-tak eindigt met
  `exit 1`. De recovery-stappen zijn *fysiek* in het uitvoeringspad
  van een echte conflict — geen prose-uitleg die de shell overgeslagen
  had.

## Wanneer geldt deze regel?

De regel geldt voor **elke** shell-recipe (skill, dispatch-prompt,
CI-script) die de shape heeft "een commando kan falen → we hebben een
auto-recovery → als recovery ook faalt → exit 1". Het patroon is
vrijwel altijd:

```bash
if ! PRIMARY_COMMAND; then
  if RECOVERY_WORKS; then
    # doe de recovery
  else
    echo "ERROR: recovery faalde — <wat de operator moet doen>" >&2
    exit 1
  fi
fi
```

Veelvoorkomende lokken waar dit misgaat:

- **Recovery in een comment onder de handler.** Commentaar wordt niet
  uitgevoerd; de agent die het script leest denkt dat er recovery is,
  maar in de praktijk eindigt het script bij de eerste `exit 1`.
- **Recovery in een latere `if` met een gedeelde vlag.** Werkt alleen
  als de vlag juist gezet wordt; één vergeten `recovery_attempted=0`
  in een nieuwe tak en de recovery is weer onbereikbaar. De
  `else`-van-dezelfde-`if`-vorm is structureel veiliger omdat er
  geen gedeelde state is.
- **Recovery met `|| true` ná `exit 1`.** `exit 1` wint — `||` redt
  niets als de voorgaande component al terminate.

## Hoe validatie werkt — twee ankers

Deze conventie wordt op **twee** manieren machine-checkbaar vastgepind,
zodat een toekomstige editor 'm niet stilletjes kan overtreden:

1. **FCR reachability-check** (kanban-kaart `c06a3a2a…`): de
   Feature-Compliance-Review prompt in zowel
   `backend/app/kanban/dispatch.py::_build_ship_instructions` als
   `.claude/agents/engineer.md` §6 bevat sinds die commit een expliciete
   reachability-check tegen de oorspronkelijke kaart-spec:
   *"verify the auto-recovery is in the executable path, not in prose
   that follows the error handler"*. Een recovery-pad dat alleen in
   commentaar of losse prose staat, wordt door FCR geblokkeerd.
2. **Positionele invariant in `test_ship_recipe_drift.py`** (zelfde kaart):
   de carve-out-substring (`docs/cockpit/README.md`-referentie in de
   merge-conflict-tak) moet fysiek tussen `merge --no-ff` en
   `push origin HEAD:master` in dezelfde `if`-blok staan, niet als prose
   erna. Een editor die de carve-out "opschoont" naar een comment onder
   de handler laat deze test falen. Zie
   `test_ship_recipe_drift.py::test_carve_out_substring_in_recovery_path`
   voor de exacte assertion.

## Counter-example — wat er fout ging (kaart `efb8187b…`)

De originele carve-out was geschreven als:

```bash
if ! git -C "$WT" merge --no-ff "$BRANCH" -m "Merge $BRANCH"; then
  echo "ERROR: merge conflict — falling back to report_impediment." >&2
  exit 1
fi
# NOTE: if conflict is in docs/cockpit/README.md + docs/cockpit/llms.txt,
# the recovery is: `git checkout --theirs --` die twee files, regenerate,
# strict-check, add -A, commit --no-edit.
```

De recovery lag in een comment onder de `exit 1` — en de 22
substring-/presence-drift-tests in `test_ship_recipe_drift.py` zagen
niets, omdat die alleen op *aanwezigheid* van substrings checkten
("de substring staat *ergens* in de file") in plaats van op de
positionele invariant ("de carve-out staat in dezelfde `if`-blok als
de merge-handler"). Een echte conflict werd dus nooit
auto-gerecovered; de agent moest improviseren, en kaart `efb8187b…`
documenteerde de resulterende PR-merge-failure. De fix hier (commit
met de carve-out in de else-tak) was wat de canonieke `if ! merge …
then carve-out; fi`-vorm voortbracht, en deze conventie documenteert
expliciet **waarom** die vorm de juiste is.

## Geldt ook voor toekomstige recipes

Bij het schrijven van een nieuwe shell-recipe (skill, dispatch-blok,
CI-script, helper in `scripts/`): als je een fout-detectie +
auto-recovery schrijft, leg de recovery in de `else`-tak van dezelfde
`if`. Review met de FCR-reachability-check; laat `test_*_drift.py` de
positionele invarianten pinnen. Een recovery die "elders in het script"
zit, is per definitie onbereikbaar in het scenario dat hem nodig heeft.