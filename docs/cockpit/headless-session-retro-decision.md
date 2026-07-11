# Session-retro trigger: dispatch-prompt injection, not a headless `SessionEnd` hook

## Beslissing

`session-retro` draait via de bestaande dispatch-prompt-injectie
(`_build_session_retro_step` in `backend/app/kanban/dispatch.py`), voor
**alle gedispatchte fases** — zowel `executor`/`engineer`-kaarten (na
shippen, vóór `move_card → Done`) als `analyst`-kaarten (vóór
`move_parent → Done`). Een generieke, headless `SessionEnd`-hook die de
skill voor élke Claude Code-sessie afvuurt — inclusief interactieve
sessies die niet via het kanban-bord gedispatcht zijn — is **bewust
afgewezen**.

> Noot bij deze versie van het document: de volledige, oorspronkelijke
> argumentatie stond in de plan-attachment van de parent-kaart, maar die
> was op het moment van schrijven niet meer laadbaar (plan-attachment
> ontbrak/kon niet worden opgehaald). Onderstaande redenen zijn de
> kernoverwegingen zoals vastgelegd in de kaartbeschrijving en afgeleid
> uit hoe de dispatch-flow feitelijk werkt; als de oorspronkelijke
> plan-tekst later alsnog terugkomt, dit document daarmee aanvullen.

## Waarom de headless-`SessionEnd`-variant is afgewezen

Een `SessionEnd`-hook (zie `docs/api/hooks.md`) vuurt voor **iedere**
Claude Code-sessie af, ongeacht of die sessie via de kanban-dispatcher is
gestart. Dat past niet bij hoe `session-retro` is ontworpen:

1. **Geen kanban-kaart om op te schrijven.** De skill sluit af met een
   verplichte `comment(card_id=..., ...)` op de host-kaart (Stap 6) en
   filet bevindingen naar Backlog via `create_card`/`comment` in hetzelfde
   project. Een interactieve, niet-gedispatchte sessie heeft geen
   host-kaart en vaak geen eenduidig kanban-project — een hook zou de
   project key moeten *raden*, wat exact het lek is dat `resolve_project_key`
   (Stap 1 van de skill) juist voorkomt.
2. **Ruis op sessies zonder "werk".** Elke lokale, interactieve sessie
   (een losse vraag, een korte lees-taak, een chatje over architectuur)
   zou een retro-pass triggeren. De skill zelf waarschuwt al tegen
   retro's op triviale sessies ("When NOT to use" / Step 5: "0 findings is
   een legitieme uitkomst, forceer geen kaart"). Een ongerichte hook
   vermenigvuldigt dat probleem naar élke sessie in plaats van alleen de
   sessies die daadwerkelijk kanban-werk verzetten.
3. **Dispatch-prompt-injectie is al de juiste plek voor gedispatchte
   sessies.** De dispatcher weet exact welke fase (`executor` vs
   `analyst`), welke kaart, en welk project een sessie bedient — die
   context wordt toch al doorgegeven in de prompt. Een prompt-instructie
   die de skill expliciet aanroept vóór de laatste `move_card`, is
   goedkoper en betrouwbaarder dan een hook die achteraf moet
   reconstrueren "was dit een gedispatchte sessie, en zo ja, welke kaart
   hoorde erbij?".
4. **Losse cyclus t.o.v. het gate-mechanisme.** Deze codebase heeft al
   afscheid genomen van blokkerende gates in de sessie-flow (zie
   `report_impediment`-conventie in `CLAUDE.md`/dispatch-instructies: "geen
   blokkerende `open_gate` meer — die houdt de sessie open en laat de
   worktree als 'dood' reaperen"). Een `SessionEnd`-hook die probeert een
   MCP-retro-call af te dwingen ná sessie-einde loopt tegen hetzelfde
   probleem aan: op het moment dat `SessionEnd` vuurt, is de
   agent-sessie al aan het afsluiten en is er geen garantie dat een
   nieuwe tool-call daar nog betrouwbaar in past.

## Gekozen richting (samengevat)

- `session-retro` wordt aangeroepen **in-sessie**, als expliciete stap
  in de prompt die de dispatcher opbouwt (`build_card_prompt` /
  `_build_ship_instructions` voor executor, `_build_analyst_session_end_instructions`
  voor analyst), vlak vóór de laatste `move_card`-aanroep.
- `phase` wordt doorgegeven aan `build_card_prompt` zodat elke fase zijn
  eigen, fase-passende afsluiting krijgt: executor/engineer behoudt de
  volledige ship-workflow (sync → tests → commit → ship → retro → Done);
  analyst krijgt alleen retro → `move_card(parent → Done)`, zonder
  merge/frontend-stappen die niet op een planning-only sessie van
  toepassing zijn.
- Niet-gedispatchte, interactieve sessies krijgen (nog) geen automatische
  retro-trigger. Een mens kan de skill altijd handmatig aanroepen
  ("doe een retro op deze sessie").

## Scope van deze kaart

Deze kaart lost het gat op dat `session-retro` alléén voor executor-fase
draaide; met deze wijziging draait de skill voor **alle gedispatchte**
fases (executor + analyst). De headless-`SessionEnd`-variant blijft
buiten scope — zie hierboven waarom.
