---
title: "pkill / pgrep safety in dispatched sessions"
type: reference
status: active
---

# pkill / pgrep safety in dispatched sessions

> **Bron van waarheid voor de `pkill -f`-gotcha.** De korte waarschuwing staat in
> `CLAUDE.md` onder *Gotchas* en verwijst hierheen; lees dit document vóór je in
> een gedispatchte sessie een `pkill`/`pgrep` met een `-f`-patroon gebruikt.
>
> Gerelateerd: [`updates-feature-decision.md`](./updates-feature-decision.md) §2
> (het `pkill -f uvicorn`-voorbeeld in de update-flow dat op deze gedeelde box
> riskant blijft).

## Twee risico's — één is gefixt, één niet

| Risico | Status sinds Claude Code 2.1.214 (18 jul 2026) |
|---|---|
| **`pkill -f` doodt de eigen sessie** | **Upstream gefixt.** `pkill` weigert sinds 2.1.214 als het patroon de eigen Claude-CLI matcht — geverifieerd op de lokaal geïnstalleerde CLI 2.1.221: `pkill -f "<eigen-worktree-token>"` geeft `"pkill: refusing to run — this pattern matches the Claude CLI process (PID …). Narrow the pattern, or target your own children with pkill -P $$ …"` en doodt niets. |
| **`pkill -f` doodt een concurrente sessie op deze box** | **Onveranderd.** De dispatcher spawnt elke sessie als `claude --dangerously-skip-permissions --model <ali> <VOLLEDIGE PROMPT>` — de hele persona + kaarttekst staat letterlijk in `/proc/<pid>/cmdline` (zie `backend/app/services/agentic_cli/claude_code.py:82-83`, prompt wordt als positional `argv`-element doorgegeven). Twee gedispatchte sessies op deze gedeelde box kunnen identieke woorden in hun cmdline hebben. |

Het tweede risico is sinds de fix het *primaire* scenario: een patroon dat toevallig matcht op de cmdline van andermans sessie legt die om. Symptomen: claim-release + re-dispatch, kaartcontext + werk verloren. Voor de `uvicorn`-variant ook relevant voor `cockpit.sh start`.

## Veilige alternatieven

Drie recepten, op volgorde van voorkeur.

1. **PID bewaren.** `echo $!` direct na het spawnen van een proces (of schrijf de PID naar een pidfile). Dood met `kill $PID` — geen `-f`, geen patroonmatch.
2. **Uniek token.** Plak een zelf-gegenereerd token dat nergens in een prompt voorkomt in zowel het commando als de cmdline van het doelproces: `pkill -f "myjob-$(uuidgen)"`.
3. **Exacte processnaam.** Voor eenmalig lokaal opruimen buiten een dispatch-context: `pkill nginx`, niet `pkill -f nginx`. Geen patroon, geen valse matches.

## Specifieke patronen die op deze box vaak fout gaan

- `pkill -f claude` — matcht élke Claude-sessie op de box.
- `pkill -f stream-json` — "stream-json" komt in dispatch-cmdlines voor.
- `pkill -f uvicorn` — matcht agent-sessies én de dev-stack. [`updates-feature-decision.md:88`](./updates-feature-decision.md) gebruikt dit in een voorbeeld en blijft daar riskant; veiliger: `pkill -f "scripts/cockpit.sh"` (de supervisor) of de bewaarde `BACKEND_PID` uit `cockpit.sh`'s pidfile.

## Versie-claim verifiëren

De fix zit vanaf Claude Code 2.1.214 (18 jul 2026). Om de lokaal geïnstalleerde versie te bevestigen: `claude --version`. Bij 2.1.214+ is de zelf-kill afgedekt; bij oudere versies is de oorspronkelijke `CLAUDE.md:218`-tekst uit commit `587c9188` weer leidend — haal die terug via `git show 587c9188:CLAUDE.md`.
