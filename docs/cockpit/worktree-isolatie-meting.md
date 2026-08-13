---
title: "Worktree-isolatie van Claude Code: wat onze dispatch wel en niet krijgt"
type: analysis
status: active
---

# Worktree-isolatie van Claude Code: wat onze dispatch wel en niet krijgt

**Conclusie: onze gedispatchte sessies krijgen géén enkele isolatie van de CLI.**
De worktree-isolatie uit Claude Code 2.1.222 hangt aan de sessie-modus
`--worktree`, niet aan een cwd die toevallig in een worktree ligt. Onze
dispatcher spawnt met `mode="plain"`
(`backend/app/kanban/dispatch.py:4406`) en zet de vlag `--worktree` alleen in
de aparte worktree-modus die de dispatch niet gebruikt
(`backend/app/services/agentic_cli/claude_code.py:103`). De prompt-regel in
`.claude/agents/engineer.md` en `.claude/agents/analyst.md` blijft dus het
enige vangnet tegen een schrijfactie op de hoofd-checkout.

Tweede bevinding: zelfs mét `--worktree` is de isolatie deels. Een gewone
shell-omleiding naar de hoofd-checkout wordt niet tegengehouden. De
changelog-zin *"isolation now applies to file edits and Bash in every session
type"* dekt in de meting alleen `git`-commando's die via `-C` naar de
hoofd-checkout wijzen, niet elke Bash-schrijfactie.

## 1. Gemeten resultaat

Gemeten op **claude 2.1.231** op 2026-08-13, telkens met
`--dangerously-skip-permissions` (de meta-lane draait zo, zie
`backend/app/kanban/dispatch.py:4474`).

| Poging vanuit de sessie | `mode="plain"`, cwd in worktree | `--worktree <naam>` |
| --- | --- | --- |
| `Write`-tool naar `/home/vdvgu/claude-cockpit/<bestand>` | gelukt | geblokkeerd |
| Bash: `echo X > /home/vdvgu/claude-cockpit/<bestand>` | gelukt | gelukt |
| Bash: `git -C /home/vdvgu/claude-cockpit checkout -- <pad>` | uitgevoerd | geblokkeerd |

De blokkade-tekst van de CLI, letterlijk, bij de `Write`-poging in
`--worktree`-modus:

> This session is isolated in the worktree
> /home/vdvgu/claude-cockpit/.claude/worktrees/probe-iso-b. Edit the worktree
> copy of this file instead of the shared-checkout path.

En bij het `git -C`-commando:

> This session is isolated in the worktree
> /home/vdvgu/claude-cockpit/.claude/worktrees/probe-iso-d, but this command
> redirects git to the shared checkout via -C. Refusing to run it.

## 2. Reproductie

Beide vormen zijn headless en duren elk ongeveer een minuut. Voer ze uit met
een cwd in een bestaande worktree. De doelbestanden zijn ongetrackt; ruim ze
na afloop op met `mv` (`rm` staat op de deny-lijst).

```bash
# A — zoals de dispatch spawnt: gewone sessie, cwd in een worktree
claude -p 'Gebruik de Write-tool voor /home/vdvgu/claude-cockpit/.probe-A.txt met inhoud PROBE-A. Draai daarna: echo PROBE-A > /home/vdvgu/claude-cockpit/.probe-bash-A.txt. Rapporteer per stap GELUKT of GEBLOKKEERD, met de letterlijke foutmelding.' \
  --dangerously-skip-permissions

# B — de isolatiemodus van de CLI
claude --worktree probe-iso -p '<zelfde opdracht, met .probe-B.txt>' \
  --dangerously-skip-permissions

# opruimen
mv /home/vdvgu/claude-cockpit/.probe-*.txt /tmp/
git -C /home/vdvgu/claude-cockpit worktree unlock .claude/worktrees/probe-iso
git -C /home/vdvgu/claude-cockpit worktree remove --force .claude/worktrees/probe-iso
git -C /home/vdvgu/claude-cockpit branch -D worktree-probe-iso
```

Let op bij het meten: de sessie in `--worktree`-modus kan een poging ook op
eigen initiatief weigeren, zonder dat de guard vuurt. Dat leverde in de eerste
ronde een vals positief op. Zet daarom expliciet in de prompt dat het doel een
ongetrackt wegwerpbestand is en dat de sessie het commando mag draaien.

## 3. Aanbeveling

**Stap niet over op `--worktree`.** Drie redenen, in volgorde van gewicht.

1. **Eigenaarschap botst.** In `--worktree`-modus maakt de CLI zelf de
   worktree aan en zet er een slot op (`git worktree list` toont `locked`).
   Onze dispatcher bezit die levenscyclus: hij maakt de worktree vooraf aan,
   koppelt hem aan een claim, en `scripts/worktree-gc.sh` ruimt hem op. Een
   door de CLI beheerde worktree valt buiten dat geheel, en het slot blokkeert
   de opruiming die de gc nu zonder ingreep doet.
2. **De winst is gedeeltelijk.** De maat hierboven laat zien dat een
   shell-omleiding naar de hoofd-checkout ook in isolatiemodus doorgaat. Het
   ongeval waar de prompt-regel uit ontstond was een `Edit`-aanroep, dus dat
   geval wordt wel afgedekt — maar de prose-regel blijft daarnaast nodig.
3. **De vlag is niet leveranciersneutraal.** De orchestratie-kern is
   agent-onafhankelijk ontworpen; `--worktree` bestaat alleen bij Claude Code.

**Het goedkopere alternatief is een `PreToolUse`-hook** die een
schrijf-aanroep op een pad buiten de worktree weigert. Die vuurt ook onder
`--dangerously-skip-permissions`, geldt voor elke sessie ongeacht spawn-modus,
en laat het eigenaarschap van de worktree bij de dispatcher. De hook-lijst in
`.claude/settings.json:4` is nu leeg; `scripts/check-pretooluse-bg-agent-test.sh`
eist een bijbehorende achtergrond-agent-test zodra hij gevuld wordt. Die keuze
is een aparte kaart, niet deze.

## 4. Gevolg voor de prompt-regel

De worktree-scope-paragraaf in `.claude/agents/engineer.md` en
`.claude/agents/analyst.md` blijft ongewijzigd load-bearing. Er is geen
machinale afdwinging onder onze spawn-vorm, dus de regel is geen documentatie
van de bedoeling maar het enige mechanisme.
