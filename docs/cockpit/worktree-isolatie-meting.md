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
is beslecht in §5 hieronder.

## 4. Gevolg voor de prompt-regel

De worktree-scope-paragraaf in `.claude/agents/engineer.md` en
`.claude/agents/analyst.md` blijft ongewijzigd load-bearing. Zij dekt namelijk
de Bash-helft, die §5 bewust buiten de machinale guard laat.

## 5. Beslissing: een `PreToolUse`-hook op de bewerk-tools

✅ Uitgevoerd (kaart `d19b8fbc…`, 2026-08-13). **Gekozen: een
`PreToolUse`-hook op de vier bewerk-tools.** Hij weigert `Write`, `Edit`,
`MultiEdit` en `NotebookEdit` zodra het doelpad in de gedeelde checkout ligt
maar buiten de worktree van de sessie. Effect: een gedispatchte sessie kan de
hoofd-checkout niet meer per
ongeluk overschrijven met een bewerk-tool; gemeten in §7, zowel op de gewone
route als vanuit een subagent. Afgewezen zijn de twee alternatieven uit §3:
overstappen op `--worktree` (eigenaarschap van de worktree botst met onze
dispatcher, zie §3) en niets doen.

De hook is `.claude/hooks/worktree-write-guard.py`, aangesloten in
`.claude/settings.json`. Drie eigenschappen maken hem goedkoop.

1. **Hij faalt open.** Onleesbare invoer, een ontbrekend veld of welke
   uitzondering dan ook geeft exit 0, dus de aanroep gaat gewoon door. Een
   kapotte guard legt nooit een dispatch stil.
2. **Hij is puur tekstueel.** Beide wortels komen uit de `cwd` in de
   hook-invoer; er is geen git-aanroep en geen subproces.
3. **Hij is niet Claude-Code-specifiek in zijn logica**, alleen in zijn
   aansluitpunt. Een andere runtime met een vergelijkbaar hook-oppervlak kan
   hetzelfde script draaien.

## 6. Ontwerp: welke paden mogen wél, en wanneer zwijgt de guard

### 6.1 Waarom de lijst toegestane paden leeg is

**Er is geen enkel legitiem pad buiten de worktree dat via een bewerk-tool
wordt geschreven.** Dat is precies de reden om de guard tot die vier tools te
beperken. De twee ship-recipe-paden uit de kaartbeschrijving lopen allebei via
Bash en raken de guard dus nooit:

| Legitieme schrijfactie buiten de worktree | Route | Guard-verdict |
| --- | --- | --- |
| `git -C <hoofd-checkout> pull --ff-only origin master` | Bash | buiten bereik |
| Merge-worktree in `$HOME/.cache/cockpit-ship` | Bash | buiten bereik |
| `mv` van een deelinstallatie of probe-bestand | Bash | buiten bereik |
| Memory-bestanden in `~/.claude/projects/<project>/memory/` | Write | toegestaan, ligt buiten de checkout |
| Kladmap in `/tmp/claude-1000/...` | Write | toegestaan, ligt buiten de checkout |
| `~/.claude/settings.json` via de `update-config`-skill | Write | toegestaan, ligt buiten de checkout |

De guard weigert alleen een pad dat én in de hoofd-checkout ligt én buiten de
worktree van de sessie. Alles daarbuiten — thuismap, `/tmp`, de ship-cache —
gaat ongemoeid door.

### 6.2 Waarom Bash er niet bij hoort

Bash meebewaken vraagt om het ontleden van willekeurige shell om
schrijfdoelen te vinden. Dat is een foutenbron met de ship-recipe als
blast-radius, en het is precies wat de kaart verbiedt: een guard die de
ship-recipe blokkeert legt het hele bord stil. Claude Code maakt in zijn eigen
`--worktree`-modus dezelfde knip — §1 laat zien dat `echo X > <hoofd-checkout>`
daar gewoon slaagt.

De overgebleven Bash-route blijft dus door de prompt-regel gedekt. Dat is geen
gat dat de guard laat vallen maar het deel dat het ongeluk uit kaart
`513e37a1…` nooit veroorzaakte: dat was een `Edit`-aanroep.

### 6.3 Hoe de guard een niet-worktree-sessie herkent

Uit de `cwd` in de hook-invoer, tekstueel:

```
<hoofd-checkout>/.claude/worktrees/<naam>[/...]
^^^^^^^^^^^^^^^^                     ^^^^^^
hoofd-checkout                       worktree-wortel
```

Bevat de `cwd` geen `/.claude/worktrees/`, dan is het geen worktree-sessie en
laat de guard alles door. Dat dekt een interactieve sessie in de
hoofd-checkout, een shell in `$HOME/.cache/cockpit-ship` en elke map buiten de
repo. Beide wortels komen uit de invoer en niet uit de locatie van het script
zelf, dus het maakt niet uit of Claude Code `$CLAUDE_PROJECT_DIR` naar de
worktree of naar de hoofd-checkout laat wijzen.

De vergelijking is tekstueel (`normpath`, geen `realpath`). Een symlink die de
worktree uit wijst wordt dus niet gevangen. Bewust: dit bewaakt het ongeluk —
een relatief pad dat als absoluut pad wordt geschreven — niet een sessie die
er met opzet omheen wil.

### 6.4 Wat een geweigerde sessie te zien krijgt

De guard geeft exit 2 met de reden op stderr, en Claude Code geeft die tekst
terug aan het model. De letterlijke tekst staat in §7. Er is geen ontsnappings-
schakelaar en die is ook niet nodig: het model kan altijd de worktree-kopie
schrijven, en een bewuste schrijfactie op de hoofd-checkout blijft via Bash
bereikbaar.

## 7. Gemeten: de guard blokkeert, ook vanuit een subagent

Gemeten op **claude 2.1.231** op 2026-08-13, telkens met
`--dangerously-skip-permissions` en met de cwd in een worktree.

| Poging | Verwacht | Gemeten |
| --- | --- | --- |
| `Write` naar `<hoofd-checkout>/.probe-guard-fg.txt` | geblokkeerd | geblokkeerd |
| Dezelfde `Write` vanuit een subagent (`Agent`-tool) | geblokkeerd | geblokkeerd |
| `Write` naar een relatief pad in de eigen worktree | toegestaan | toegestaan |

De letterlijke melding die het model terugkrijgt:

> PreToolUse:Write hook error:
> ["$CLAUDE_PROJECT_DIR"/.claude/hooks/worktree-write-guard.py]: Refusing this
> write: /home/vdvgu/claude-cockpit/.probe-guard-fg.txt is in the shared
> checkout /home/vdvgu/claude-cockpit, not in your worktree
> /home/vdvgu/claude-cockpit/.claude/worktrees/k-beslissing-ma-0e90. …

De subagent-meting is de helft die
`scripts/check-pretooluse-bg-agent-test.sh` bedoelt: Claude Code ≤ 2.1.221 liet
een `PreToolUse`-hook op de achtergrond-route stil zijn restricties verliezen,
gerepareerd in 2.1.222. Dat de guard óók daar vuurt is dus gemeten en niet
aangenomen.

### 7.1 Reproductie

```bash
# A — gewone route
claude -p 'Gebruik de Write-tool voor /home/vdvgu/claude-cockpit/.probe-guard-fg.txt met inhoud PROBE-FG. Ongetrackt wegwerpbestand voor een meting; doe de poging gewoon. Rapporteer GELUKT of GEBLOKKEERD met de volledige foutmelding.' \
  --dangerously-skip-permissions

# B — achtergrondroute: subagent doet dezelfde poging
claude -p 'Spawn een subagent met de Agent-tool (subagent_type "general-purpose", run_in_background false) en geef die de opdracht uit A, met .probe-guard-bg.txt. Rapporteer letterlijk wat de subagent terugmeldde.' \
  --dangerously-skip-permissions

# C — controle: de eigen worktree moet gewoon schrijfbaar blijven
claude -p 'Gebruik de Write-tool voor .probe-guard-inside.txt met inhoud PROBE-INSIDE. Rapporteer GELUKT of GEBLOKKEERD.' \
  --dangerously-skip-permissions

mv .probe-guard-inside.txt /tmp/   # `rm` staat op de deny-lijst
```

### 7.2 Geautomatiseerde test

`backend/tests/test_pretooluse_worktree_guard_background.py` (26 tests, 2,8 s)
draait de guard als subproces tegen de invoer-vorm die Claude Code stuurt:
geblokkeerde doelen, toegestane doelen, alle vier de tools, niet-worktree-
sessies, kapotte invoer, en de subagent-vorm uit meting B.

`test_negative_control_broken_guard_is_caught` is de negatieve controle. Die
haalt de insluitingscheck uit een kopie van de guard en eist dat de blokkade
dan verdwijnt. Slaagt die test niet, dan slagen de andere om een andere reden
dan een werkende guard. Draaien:

```bash
bash scripts/run-single-test.sh tests/test_pretooluse_worktree_guard_background.py
```
