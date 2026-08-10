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

De kaart vroeg om een meting die **twee contrasterende lanes** dekt: een lane waar beknopte output gewenst is, en een lane waar het analyseresultaat zelf de deliverable is. Twee eerdere versies van deze paragraaf zijn afgekeurd omdat de cijfers iets anders beschreven dan ze beweerden. Onder staat wat er nu wél gemeten is, met welke opzet, en welke uitspraken die meting draagt.

### Meting 2026-08-10 — `scripts/measure-token-saver.sh injector-compare` (productie-promptvorm)

Beide armen worden gerenderd door de productiecode zelf: `backend/app/kanban/dispatch.py::build_card_prompt`, dezelfde functie die elke echte dispatch gebruikt. Het enige byte-verschil tussen de armen zijn de twee injector-kwargs. De golden-task gaat als kaartbeschrijving naar binnen; persona, slices, kaarttekst en ship-recept staan in de volgorde die productie oplevert. Twee trials, tegengesteld geordend, zodat elke arm één koude en één warme cachepositie krijgt.

| Run | input | cache_creation | cache_read | output | turns | pass_tests |
|---|---|---|---|---|---|---|
| trial 1 — `card-baseline` | 77.791 | 0 | 1.636.608 | 3.743 | 34 | 1 |
| trial 1 — `card-injector` | 72.240 | 0 | 284.544 | 1.046 | 7 | 1 |
| trial 2 — `card-baseline` | 138.970 | 0 | 1.174.195 | 3.076 | 23 | 1 |
| trial 2 — `card-injector` | 79.019 | 0 | 1.436.544 | 3.542 | 22 | 1 |

Artefacten: `/home/vdvgu/.cache/cockpit-measure-token-saver/20260810T084936Z-production-shape/`. Alle vier de runs liepen op eigen kracht af; geen enkele is afgekapt.

**Wat deze meting draagt.**

- **De slice breekt de prompt-cache niet.** `cache_creation` is 0 in alle vier de runs en `cache_read` is in beide armen groot. De ongeveer 12 KB extra preamble verhindert dus geen cache-hit. Dat sluit aan op de puurheidstest `tests/test_prompt_injectors.py::test_resolver_returns_byte_stable_output_for_same_inputs`: dezelfde inputs geven dezelfde bytes, dus dezelfde cache-sleutel.
- **De taakkwaliteit blijft gelijk.** `pass_tests` is 1 in alle vier de runs — 2/2 per arm.

**Wat deze meting níét draagt: een percentage.** Per trial vergeleken:

| | trial 1 | trial 2 |
|---|---|---|
| Δ input | −7,1% | −43,1% |
| Δ cache_read | −82,6% | **+22,3%** |
| Δ output | −72,1% | **+15,1%** |
| turns (baseline → injector) | 34 → 7 | 23 → 22 |

Twee van de drie tokenmaten wisselen van teken tussen de trials. De oorzaak staat in de laatste rij: `cache_read` en `output` schalen met het aantal beurten dat de agent neemt, en dat aantal varieert sterk per run. In trial 1 werkte de injector-arm de taak in 7 beurten af tegen 34 voor de baseline; in trial 2 zaten beide armen op ongeveer 22. Waar het beurtenaantal dicht bij elkaar ligt, verdwijnt het verschil. Met N=2 is de spreiding tussen runs dus groter dan het verschil tussen de armen, en een getal als "Δ output −36%" zou opnieuw iets beweren wat het artefact niet draagt.

De richting is niet weerlegd — alleen niet aangetoond. Een uitspraak over tokenbesparing vraagt meer runs per arm; dat staat als vervolgkaart onder.

**Over `pass_diff`.** Die kolom is 0 in alle vier de runs, in beide armen gelijk, en discrimineert dus niet. De check zoekt letterlijk naar `r.max_sessions >= 0`, terwijl agents de regel herschrijven naar een gelijkwaardige vorm zonder die vergelijking. In het ene geval dat we konden inspecteren — de commit die vóór de sandbox op master belandde — stond er `if r.max_sessions is not None`, functioneel gelijk en letterlijk anders. `pass_tests` is hier de betekenisvolle kwaliteitsmaat.

**Ingetrokken cijfers.** De 2026-08-09-tabel die hier eerder stond (Δ input +4,8%, Δ cache_read −5,3%, Δ output −36,1%) is ingetrokken. Die meting zette Ponytail ná de taakbody in plaats van vóór, terwijl productie beide slices in de preamble zet. `cache_read` is een eigenschap van het prompt-voorvoegsel, dus een verkeerd voorvoegsel meet iets anders dan de dispatch-vorm. De 2026-08-05-proxycijfers (Δ input −57%, Δ cache_read −51%, Δ output −65%) blijven staan in `token-saver-mechanismen-decision.md` §8 voor hun eigen RTK-context; ze zijn gemeten met een prompt-mutatie van ongeveer 110 bytes en beschrijven de injectors niet.

**Veiligheidsvoorwaarde bij deze meetvorm.** De kaartvormige armen geven de agent het volledige ship-recept, en een gemeten agent voerde dat ook uit: hij pushte zijn golden-task-bewerking naar `origin/master` (teruggedraaid in `2e0eb256`). De runs draaien daarom in een `git archive`-export zonder `.git`, zonder remote en buiten de repo. Wie deze meting herhaalt, doet dat met `injector-compare`; de losse subcommando's `card-baseline`/`card-injector` gebruiken dezelfde sandbox.

**Lane-type-conclusie** (kaart-accepatiecriterium "minstens één lane waar beknopte output gewenst is én één waar het artefact zelf de deliverable is"):

| Lane-type | Voorbeeld | Verwacht effect | Conclusie |
|---|---|---|---|
| Research-/sweep | `[research] Weekly market-research sweep` | De 2026-08-10-meting laat lagere output zien in trial 1 en licht hogere in trial 2; een besparingspercentage draagt zij niet. Wat zij wél laat zien: de taak wordt in beide armen goed afgerond en de prompt-cache blijft werken. De upstream Caveman-§"Auto-Clarity" carve-out houdt security- en irreversible-warning-zinnen buiten compressie, dus kritieke ontdekkingen blijven staan. | **Geschikt**, op grond van gelijke taakkwaliteit en een werkende cache — niet op grond van een gemeten besparing. Default-uit blijft; operators zetten hem aan per lane. |
| Analysis-leaf | `analysis` work_type met `analyst_agent_id=None` (de leaf design-deliverable — het analysedoc IS de deliverable) | De Ponytail-§"Boundaries" carve-out zegt "Ponytail governs what you build, not how you talk", en de Caveman-§"Boundaries" carve-out zegt expliciet "Persisted outside chat: write normal prose — code, comments, commits, docs, issue/PR/MR text, memory files". Beide carve-outs staan verbatim in de attributie-header. **Verwachting (ongemeten):** een agent die een analysedoc schrijft heeft die regel expliciet in zijn preamble staan, maar bij modellen met zwakke output-discipline zou de slice alsnog in analysedoc-proza kunnen doorwerken. | **Niet geschikt voor deze lane uit voorzorg.** Default-uit blijft; wie hem hier aanzet, krijgt het als bekende regressie. Een echte kwaliteitsmeting op deze lane is een eigen vervolgkaart (zie onder). |

N=2 is een kleine steekproef. De richting van de tokencijfers is met deze steekproef niet vast te stellen — zie de trial-vergelijking hierboven. Het analysis-leaf-risico is een **verwachting**, geen waarneming; een echte meting op die lane hoort in een eigen vervolgkaart en zat niet in deze ship.

## Wat dit NIET verandert

- **Geen promotion naar default-on** — beide flags blijven `INTEGER NOT NULL DEFAULT 0`.
- **Geen wijziging aan persona-bestanden of FCR-/ship-recipe** — deze slice zit louter in de dispatcher-tussenstap, niet in een tracked bestand dat de FCR-subagent kan lezen.
- **Geen tweede override-mechanisme** — `column_overrides` is voor model/provider; de prompt-schakelaars zitten op een eigen kolompaar omdat het een ander mechanisme is met een ander deactivate-pad (de kill-switch, niet de persona).

## Vervolgkaarten

- **Tokenbesparing met genoeg runs meten** — kaart `54997750…`. De 2026-08-10-meting draagt geen besparingspercentage: bij N=2 is de spreiding tussen runs groter dan het verschil tussen de armen, doordat `cache_read` en `output` meeschalen met het aantal beurten. Die kaart draait `injector-compare` vijf keer per arm en rapporteert ook de spreiding. Het harnas is er klaar voor; het kost alleen runtijd.
- **Analyse-leaf kwaliteitsmeting** — `5934b954…` sloot de meting op een executor-lane. De analysis-leaf-claim in dit doc blijft een **verwachting**. Een vervolgkaart die een echte `analysis`-kaart met de slice aan door de leaf-deliverable-pijplijn haalt en de dockwaliteit beoordeelt, hoort in Backlog zodra er zo'n kaart in scope is.
- **RTK-werk** — `token-saver-mechanismen-decision.md` §8 (kaart `c31333bf…`) draagt de proxycijfers uit 2026-07-25 en 2026-08-05 voor zijn eigen RTK-context. Die cijfers zijn niet wat dit doc als `cache_read` rapporteert.

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
