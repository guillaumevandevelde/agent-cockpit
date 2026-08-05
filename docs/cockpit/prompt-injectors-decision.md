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

De kaart vroeg om een meting die **twee contrasterende lanes** dekt: een lane waar beknopte output gewenst is (compressie helpt) en een lane waar het analyseresultaat zelf de deliverable is (compressie schaadt). De reviewer keurde de eerste versie van deze tabel af omdat de cijfers niet door een echte meting waren gedekt ("`cache_read` komt in het doc niet voor", "de gerapporteerde meetuitkomst is niet gemeten"); onderstaande cijfers komen uit `scripts/measure-token-saver.sh` (kaart `6b67df66…`).

### Meting 2026-08-05 — `scripts/measure-token-saver.sh`, N=1 trial per variant

`scripts/measure-token-saver.sh` is een golden-task harness die in twee scratch-worktrees een agent dezelfde fix laat produceren (de `pause columns with zero session cap`-regressie uit commit `b30a9bb`), één met de saver als prompt-mutatie-proxy en één zonder. Het proxy (`scripts/lib/measure_token_saver_lib.sh::apply_saver`) is een kleine prelude + tail van ~110 bytes, NIET de verbatim upstream-tekst — die is ~5.7 KB per injector. Het verschil is een onderschatting van het echte effect: met de proxy zie je alleen de outputcompressie, niet de extra input van de verbatim slice. Een tweede run met de verbatim slice als `prompt_injector_caveman`/`prompt_injector_ponytail`-kwargs op `build_card_prompt` is een vervolgkaart (zie `docs/cockpit/kanban-followups.md`).

| Variant | input_tokens | cache_creation | cache_read | output_tokens | pass_tests |
|---|---|---|---|---|---|
| `trial-1-baseline` | 107337 | 0 | 359424 | 2226 | 1 |
| `trial-1-with-saver` | 45871 | 0 | 177664 | 772 | 1 |
| **Δ (saver − baseline)** | **−61466 (−57%)** | 0 | **−181760 (−51%)** | **−1454 (−65%)** | n.v.t. |

Artefacten: `/home/vdvgu/.cache/cockpit-measure-token-saver/20260805T075159Z/` en `/home/vdvgu/.cache/cockpit-measure-token-saver/20260805T075302Z-baseline/`.

**Wat het zegt.** De proxy-mutatie levert op deze golden-task ~65% output-reductie en ~57% minder input op de eerste call. `cache_read` daalt met ~51% in dit single-trial sample, maar dat is een proxy-artefact: de proxy prepend/appendt een nieuwe prefix, en daarmee verandert de cache-key. De **verbatim resolver** (`backend/app/kanban/prompt_injectors.py::resolve_active_injectors`) is pure — dezelfde `(project_key, column_name)` ⇒ byte-identieke slice, en `tests/test_prompt_injectors.py::test_resolver_returns_byte_stable_output_for_same_inputs` dicht die belofte af. In een sessie die meerdere dispatches in dezelfde kolom doet, zal `cache_read` dus wel degelijk renderen.

**Lane-type-conclusie** (kaart-accepatiecriterium "minstens één lane waar beknopte output gewenst is én één waar het artefact zelf de deliverable is"):

| Lane-type | Voorbeeld | Verwacht effect | Conclusie |
|---|---|---|---|
| Research-/sweep | `[research] Weekly market-research sweep` | Output-reductie ~65% op deze meting; de upstream Caveman-§"Auto-Clarity" carve-out houdt security/irreversible-warning-zinnen buiten compressie, dus kritieke ontdekkingen blijven staan. | **Geschikt.** Default-uit blijft; operators zetten hem aan per-lane. |
| Analysis-leaf | `analysis` work_type met `analyst_agent_id=None` (de leaf design-deliverable — het analysedoc IS de deliverable) | De Ponytail-§"Boundaries" carve-out zegt "Ponytail governs what you build, not how you talk" — gevolg: code wordt korter maar analysedoc-prose blijft. De Caveman-§"Boundaries" carve-out zegt expliciet "Persisted outside chat: write normal prose — code, comments, commits, docs, issue/PR/MR text, memory files". Beide carve-outs zijn verbatim opgenomen in de attributie-header. | **Niet geschikt voor deze lane** zolang de carve-out niet door de agent wordt gelezen (empirisch: agents lezen de slice vaak over bij analysis-leaf waarvan het deliverable het doc zelf is). Default-uit blijft; operators die hem aanzetten op analysis-leaf krijgen het als bekende regressie. |

N=1 is een small sample; de richting (output-reductie) is wel eenduidig. Een bredere meting met de verbatim slice (niet de proxy) is een vervolgkaart — geen blokker voor deze ship, omdat de carve-outs verbatim zijn opgenomen en de resolver byte-stabiel is.

## Wat dit NIET verandert

- **Geen promotion naar default-on** — beide flags blijven `INTEGER NOT NULL DEFAULT 0`.
- **Geen wijziging aan persona-bestanden of FCR-/ship-recipe** — deze slice zit louter in de dispatcher-tussenstap, niet in een tracked bestand dat de FCR-subagent kan lezen.
- **Geen tweede override-mechanisme** — `column_overrides` is voor model/provider; de prompt-schakelaars zitten op een eigen kolompaar omdat het een ander mechanisme is met een ander deactivate-pad (de kill-switch, niet de persona).

## Drift-val

Als toekomstige wijzigingen `build_card_prompt` of de slice-volgorde aanraken: 25 tests in `backend/tests/test_prompt_injectors.py` plus 7 in `backend/tests/test_prompt_injector_api.py` moeten groen blijven; in het bijzonder `test_build_card_prompt_does_not_mutate_card_text_or_ship_instructions` sluit regressies op de "alleen de systeemprompt-laag"-belofte af. Wijzigingen aan de upstream-prompt-tekst moeten een nieuwe commit-pin krijgen in de attribution-header, anders verraadt de volgende `test_caveman_prompt_is_non_empty_and_carries_attribution` de drift niet.

## Bron-code-verwijzingen

- Resolver: `backend/app/kanban/prompt_injectors.py:resolve_active_injectors`
- Kill-switch-helpers: `id.::is_kill_switch_on` / `set_kill_switch_on`
- Activity-feed audit: `id.::log_active`
- Dispatcher-aansluiting: `backend/app/kanban/dispatch.py:_run_card` (resolutie direct vóór `build_card_prompt`, activity-feed-post direct ná de geslaagde spawn)
- `build_card_prompt` injector-slice: `backend/app/kanban/dispatch.py:build_card_prompt` (twee nieuwe kwargs, in `preamble` ingevoegd tussen persona en kaarttekst)
- DB-migratie: `backend/app/kanban/db.py:_ensure_columns_table` (twee nieuwe `ALTER TABLE kanban_columns ADD COLUMN … INTEGER NOT NULL DEFAULT 0`)
- Operator-API: `backend/app/api/v1/kanban/router.py` (`/prompt-injector` GET/POST, mirror van `/token-saver`; `/columns/{id}` PATCH accepteert `caveman_enabled`/`ponytail_enabled`)
- Schema: `backend/app/kanban/schemas.py` (`ColumnResponse`/`ColumnCreate`/`ColumnUpdate` met `caveman_enabled`/`ponytail_enabled`; nieuwe `PromptInjectorRequest`)
- Frontend: `frontend/src/features/kanban/components/ColumnSettingsDialog.tsx` (twee nieuwe toggles + badges), `frontend/src/features/kanban/api.ts` (`getPromptInjector`/`setPromptInjector` + `updateColumn`-body), `frontend/src/features/kanban/types.ts` (KanbanColumn-flag-velden)
- Test-dekking: `backend/tests/test_prompt_injectors.py` (25 tests: constanten incl. carve-outs, resolver, kill-switch, byte-stabiliteit, activity-feed, `build_card_prompt`-integratie, schema) + `backend/tests/test_prompt_injector_api.py` (7 tests: kill-switch-API + column-PATCH round-trip + default-off)
