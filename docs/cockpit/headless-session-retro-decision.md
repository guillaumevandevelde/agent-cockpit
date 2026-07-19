---
title: "Beslissing: headless SessionEnd-retro voor niet-gedispatchte sessies"
type: decision
status: decided
---

# Beslissing: headless SessionEnd-retro voor niet-gedispatchte sessies

**Datum:** 2026-07-11
**Status:** besloten (mens-beslissing op de impediment).
**Kaart:** _zie doc — geen hex-id in dit beslisdoc vastgelegd_
**Uitkomst:** **Niet bouwen.** In plaats daarvan de bestaande in-session retro uitbreiden naar álle gedispatchte sessies — concreet: het analyst-gat sluiten.

**TL;DR:** de *headless* `SessionEnd`-retro voor willekeurige **interactieve**
sessies wordt **niet gebouwd**. In plaats daarvan breiden we de bestaande
**in-session** retro uit naar **álle gedispatchte** sessies — concreet: het
**analyst**-gat sluiten. Dat is één engineer-kind-kaart.

## 1. Vraagstelling van deze analyse-kaart

De sister-kaart wired de `session-retro`-skill in de **executor/engineer**
session-end workflow. Deze kaart vroeg: willen we `session-retro` óók draaien
voor willekeurige **interactieve** Claude Code-sessies (de mens werkt zelf in de
repo), via een echte `SessionEnd`-hook die de backend een headless
`claude -p`-reviewer laat spawnen?

## 2. Mens-beslissing (autoritatief)

> "gedispatchte sessies is goed, maar mag dus voor alle gedispatchte sessies"

Interpretatie: de in-session (gedispatchte) aanpak is de juiste; de
headless-voor-interactieve variant is **niet** gewenst. Maar de retro moet
gelden voor **alle** gedispatchte sessies, niet enkel executors.

## 3. Huidige staat (read-only geverifieerd)

- Er zijn precies **twee** dispatch-fases: `analyst` en `executor`
  (`dispatch.py:_phase_target_agent`, `resolve_phase`).
- **Executor** krijgt de retro al: `_build_ship_instructions` injecteert
  `_build_session_retro_step` (stap 6 in direct-mode, 7 in PR-mode) tussen
  `attach_deliverable` en `move_card → Done`.
- **Analyst** was het gat. `build_card_prompt` (`dispatch.py:684-701`) hing de
  **volledige engineer ship-workflow** (`git merge` naar master, frontend-checks,
  retro, move-to-Done) **onvoorwaardelijk** aan élke prompt — óók analyst. Dat is
  incoherent: de analyst-rol is planning-only en exit't via
  `move_card(parent → Done)`, niet via merge-naar-master. De retro-stap was er dus
  wél in de prompt-tekst, maar begraven in een ship-workflow die de analyst
  expliciet moest negeren.
- De skill zélf sloot analyst voorheen **expliciet uit**:
  `.claude/skills/session-retro/SKILL.md` "When NOT to use" → "You're in an
  analyst session … retro is wired only for executor/engineer". En de docstring
  van `_build_session_retro_step` (dispatch.py ~991-993) bevestigde datzelfde.

Netto: analyst-sessies hadden géén coherente, bedoelde retro. Dat was precies het
"alle gedispatchte sessies"-gat dat de mens gesloten wil zien.

## 4. Waarom NIET de headless-voor-interactieve variant

1. **Dubbele-kaart-risico & board-ruis.** Een headless reviewer op elke
   interactieve SessionEnd zou `[self-improve]`-kaarten filen over het handwerk
   van de mens — terwijl die mens al in-the-loop is en zelf kan triageren/filen.
   Elke losse interactieve sessie zou de Backlog kunnen volspammen.
2. **Spawn-overhead & kosten.** Elke SessionEnd zou een `claude -p`-proces
   starten dat de volledige JSONL-transcript inleest en parse't — reële
   compute/token-kost per sessie, voor sessies waar de mens sowieso aanwezig is.
3. **Transcript-parsing fragiliteit.** Out-of-band de JSONL parsen (i.p.v. de
   in-session agent die de context al gratis heeft) is broos en dupliceert wat de
   in-session retro voor niks krijgt.
4. **Signaalkwaliteit.** Het four-pass-filter van de retro is getuned op
   *gedispatchte agent-proces*-frictie (dispatcher-prompt-gaten, tool-failures,
   ontbrekende automatisering). Interactief mens-werk heeft een ander
   frictieprofiel; een headless reviewer die een mens-sessie "grade't" is
   laag-signaal — en grade't feitelijk andermans huiswerk.
5. **Mens is al in-the-loop.** Een interactieve sessie heeft een aanwezige mens
   die desgewenst `/session-retro` handmatig kan aanroepen of direct een
   `flag-problem`-kaart kan filen. De automatisering koopt weinig extra's.

De hook-infra (`hook_installer.py` additief+idempotent mergen, `hook_script.py`
POST `session-end`) *zou* het technisch mogelijk maken — dat is niet de blokker.
De baten/kosten-balans is dat wel.

## 5. Gekozen richting

Sluit het analyst-gat zodat de retro voor **beide** dispatch-fases coherent
draait:

- Geef de **analyst**-fase een analyst-passende session-end afsluiting: run de
  `session-retro`-skill, dán de bestaande `move_card(parent → Done)`-exit —
  zónder de engineer merge/frontend-ship-stappen die niet op een planning-only
  sessie slaan.
- Hef de analyst-uitsluiting in `session-retro/SKILL.md` op.

Analyst-sessies zijn een legitieme signaalbron: ze lopen tegen onduidelijke
kaarten, ontbrekende plan-attachments (déze kaart startte met "Plan niet
beschikbaar" — zie ook het gerelateerde `[problem]`/`[self-improve]`-paar over
de `-32602` MCP-handshake-race), scope-ambiguïteit en MCP-tool-failures. Precies
het materiaal waar de retro voor bedoeld is.

## Implementatie (deze kaart)

- `build_card_prompt` (`backend/app/kanban/dispatch.py`) krijgt een `phase`
  parameter; voor `phase == "analyst"` wordt `_build_analyst_session_end_instructions()`
  gebruikt in plaats van `_build_ship_instructions(ship_mode)` — retro +
  `move_card(parent → Done)`, geen merge/frontend-stappen.
  `_build_analyst_session_end_instructions` hergebruikt `_build_session_retro_step`
  (DRY met de executor-variant).
- Het aanroeppunt (`dispatch.py:~1613`) geeft `phase` door aan `build_card_prompt`.
- `ANALYST_PROMPT` (`backend/app/kanban/analyst_prompt.py`) noemt de retro-stap
  expliciet vóór de move-naar-Done-exit.
- `.claude/skills/session-retro/SKILL.md` sluit analyst-sessies niet langer uit;
  de "When to use"-sectie noemt beide fases expliciet.
- Regressietests in `backend/tests/test_kanban_dispatch.py` dekken dat het
  analyst-prompt de retro-stap wél, en de engineer-ship-tekst (merge/frontend
  checks) niét bevat, en dat het executor-prompt ongewijzigd blijft.

## Bekende risico's / aandachtspunten (uit de analyse)

- **Dubbele retro bij analyst vermeden.** De analyst krijgt precies één
  retro-pad: het engineer-ship-blok wordt voor de analyst-fase *vervangen*,
  niet aangevuld.
- **Fase wordt expliciet doorgegeven**, niet geraden op basis van persona-tekst.
- **Skill blijft source of truth** voor de volledige retro-procedure;
  `_build_session_retro_step` inlinet bewust een getrimde kopie — beide blijven
  in sync gehouden.
