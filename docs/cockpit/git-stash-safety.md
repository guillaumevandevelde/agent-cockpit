---
title: "git stash in gedeelde worktrees — waarom niet, en wat wel"
type: reference
status: active
---

# git stash in gedeelde worktrees — waarom niet, en wat wel

> **Bron van waarheid voor de `git stash apply`-gotcha.** De korte waarschuwing
> staat in `CLAUDE.md` onder *Gotchas* en verwijst hierheen. Lees dit voordat je
> in een gedispatchte sessie een stash toepast.

## De conclusie

Pas nooit een stash toe die je niet zelf hebt gemaakt. Wil je weten of een
failure al op `origin/master` bestond, gebruik dan de baseline-scripts uit
[§ Het alternatief](#het-alternatief) — die raken je werkboom niet aan.

## Waarom het misgaat

`git stash list` is per worktree, niet per sessie. Twee sessies die in dezelfde
worktree gedispatcht worden zien elkaars stashes. Datzelfde geldt voor een
hervatte sessie in een worktree die een eerdere sessie al gebruikte.

De dispatcher ruimt een stash van een vorige sessie niet altijd op. Dat gebeurt
vooral op het impediment- en het faalpad. Een `stash@{0}` die je niet zelf
maakte kan dus uren oud zijn en van iemand anders zijn.

Toepassen levert dan merge-conflicten op. Wie die conflict-apply afbreekt met
`git reset --hard` verwijdert zijn **eigen** ongecommitte bestanden. In één
sessie kostte dat 7 gewijzigde bestanden (kaart `31c30dbb…`).

## Moet je tóch een stash toepassen

Controleer eerst van wie hij is:

```bash
git stash show -p stash@{N}          # toon de inhoud
git stash list --format='%gd %s'     # kies op bericht, niet op index
```

## Het alternatief

Voor de vraag "bestond deze failure al op `origin/master`?" is een stash nooit
nodig. Drie paren scripts beantwoorden die vraag in een losstaande worktree van
`origin/master`, zonder je eigen werkboom aan te raken. De `iteration-loop`-skill
heeft voor elk een preset.

| Wat faalt | Scripts | Preset |
|---|---|---|
| backend-tests | `scripts/pytest-baseline.sh` + `scripts/pytest-compare.sh` | `pytest-attr` |
| `scripts/test_*.sh` | `scripts/baseline-bash-tests.sh` + `scripts/compare-bash-tests.sh` | `bash-test-attr` |
| `ruff check` | `scripts/ruff-baseline.sh` + `scripts/ruff-compare.sh` | `ruff-attr` |

Elk script classificeert een failure als bestaand op master, nieuw door jouw
wijziging, of juist opgelost door jouw wijziging.
