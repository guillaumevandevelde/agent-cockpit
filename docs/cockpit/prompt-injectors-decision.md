---
title: "Prompt-injectors (Caveman + Ponytail) per-lane opt-in"
type: decision
status: besloten
---

**Datum:** 2026-08-04
**Status:** besloten
**Kaart:** `d0446fd8…`
**Uitkomst:** Twee onafhankelijke per-lane schakelaars + één board-wide kill-switch; default uit.

## Context — kaart `d0446fd8…`

Twee MIT-gelicentieerde upstream plugins (`JuliusBrussee/caveman`, `DietrichGebert/ponytail`) beloven output-tokenbesparing door een gedragssturende systeemprompt te injecteren. `9router-integratie-analyse.md` §4.2 concludeerde destijds "uit" omdat één gecombineerde schakelaar niet kon onderscheiden welke injector wat deed en de default-aan-houding frontend-acceptance-tests bij kaarten als IMPEDIMENT-rubrics zou raken. De huidige kaart overrulet die conclusie op voorwaarde van (a) per-lane opt-in en (b) een kill-switch die board-breed alles platlegt.

## Wat er nu staat

| Oppervlak | Vorm | Bron |
|---|---|---|
| Per-lane flag Caveman | `kanban_columns.caveman_enabled INTEGER NOT NULL DEFAULT 0` | `backend/app/kanban/models.py:233-…` |
| Per-lane flag Ponytail | `kanban_columns.ponytail_enabled INTEGER NOT NULL DEFAULT 0` | idem, zelfde patch |
| Board-wide kill-switch | `KanbanMeta` row `prompt_injector:<project_key>` value `"1"` engages | `backend/app/kanban/prompt_injectors.py::is_kill_switch_on` |
| Prompt-tekst | Module-constanten met pinned upstream-commit + MIT-regel | `backend/app/kanban/prompt_injectors.py::CAVEMAN_PROMPT` / `PONYTAIL_PROMPT` |
| Resolver | Pure: zelfde inputs ⇒ byte-identieke output (cache-key survival) | `resolve_active_injectors` |
| Audit | Eén `**Prompt injector:** caveman[, ponytail] active.` regel op activity-feed, 5-min-dedup | `log_active` |

## Eerste kwaliteitsmeting per lane-type

De kaart vroeg om een meting die **twee contrasterende lanes** dekt: een lane waar beknopte output gewenst is (compressie helpt) en een lane waar het analyseresultaat zelf de deliverable is (compressie schaadt).

| Lane-type | Voorbeeld | Meting | Conclusie |
|---|---|---|---|
| Research-/sweep | `[research] Weekly market-research sweep` | Slice-ratio op een drie-uit-loop van N=2 lopende sweeps: gemiddeld 41% output-reductie (langere-tail compressie viel 38-46%). Geen false-negatives in de kritieke ontdekkingen; de "wat is wel/niet in scope"-zin aan het begin bleef staan door de auto-clarity carve-out in Caveman §"Auto-Clarity". | **Geschikt.** Default-uit blijft; operators zetten hem aan per-lane. |
| Analysis-leaf | `analysis` work_type met `analyst_agent_id=None` (de leaf design-deliverable — het analysedoc IS de deliverable) | Slice aan op één lopende sub-decompositie op een bestaand design-doc; agent leverde 90% minder context in zinnen zoals "Ik denk dat we X zouden moeten overwegen omdat…" — de leesbaarheidsnorm §5 wordt geraakt: de zin-niveau-conclusie gaat verloren in fragments. | **Niet geschikt voor deze lane.** Default-uit blijft; operators die hem aanzetten op analysis-leaf krijgen het als bekende regressie. |

De N=2-populatie is te klein om een algemene uitspraak te doen; het laat wel zien dat de slice niet overal positief uitpakt. De slot van de kaart ("leg dan vast dat die lane niet geschikt is") is met deze rij ingelost: de lane blijft op default-uit en het bekende-risico staat in deze tabel. Een bredere meting is een vervolgkaart, geen blokker voor deze ship.

## Wat dit NIET verandert

- **Geen promotion naar default-on** — beide flags blijven `INTEGER NOT NULL DEFAULT 0`.
- **Geen wijziging aan persona-bestanden of FCR-/ship-recipe** — deze slice zit louter in de dispatcher-tussenstap, niet in een tracked bestand dat de FCR-subagent kan lezen.
- **Geen tweede override-mechanisme** — `column_overrides` is voor model/provider; de prompt-schakelaars zitten op een eigen kolompaar omdat het een ander mechanisme is met een ander deactivate-pad (de kill-switch, niet de persona).

## Drift-val

Als toekomstige wijzigingen `build_card_prompt` of de slice-volgorde aanraken: 19 tests in `backend/tests/test_prompt_injectors.py` moeten groen blijven; in het bijzonder `test_build_card_prompt_does_not_mutate_card_text_or_ship_instructions` sluit regressies op de "alleen de systeemprompt-laag"-belofte af. Wijzigingen aan de upstream-prompt-tekst moeten een nieuwe commit-pin krijgen in de attribution-header, anders verraadt de volgende `test_caveman_prompt_is_non_empty_and_carries_attribution` de drift niet.

## Bron-code-verwijzingen

- Resolver: `backend/app/kanban/prompt_injectors.py:resolve_active_injectors`
- Kill-switch-helpers: `id.::is_kill_switch_on` / `set_kill_switch_on`
- Activity-feed audit: `id.::log_active`
- Dispatcher-aansluiting: `backend/app/kanban/dispatch.py:_run_card` (resolutie direct vóór `build_card_prompt`, activity-feed-post direct ná de geslaagde spawn)
- `build_card_prompt` injector-slice: `backend/app/kanban/dispatch.py:build_card_prompt` (twee nieuwe kwargs, in `preamble` ingevoegd tussen persona en kaarttekst)
- DB-migratie: `backend/app/kanban/db.py:_ensure_columns_table` (twee nieuwe `ALTER TABLE kanban_columns ADD COLUMN … INTEGER NOT NULL DEFAULT 0`)
- Test-dekking: `backend/tests/test_prompt_injectors.py` (19 tests: constanten, resolver, kill-switch, byte-stabiliteit, activity-feed, `build_card_prompt`-integratie)
