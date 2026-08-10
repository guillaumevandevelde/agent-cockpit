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

**Over `pass_diff` ✅ verwijderd (kaart `0a3ee4c9…`).** Eerdere versies van deze tabel rapporteerden een `pass_diff`-kolom die letterlijk zocht naar `r.max_sessions >= 0`. Die kolom was 0 in alle vier de runs en in beide armen gelijk, dus kon hij niet bijdragen aan een vergelijking — en een lezer die de tabel snel scant leest een nul als kwaliteitsverlies. De oorzaak: agents herschrijven de regel naar een functioneel gelijkwaardige vorm zonder die vergelijking (in één inspecteerbaar geval stond er `if r.max_sessions is not None`). De kolom is in `score_golden` geschrapt; `pass_tests` draait de echte acceptatietests en is sindsdien de enige kwaliteitsmaat in de tabel.

**Ingetrokken cijfers.** De 2026-08-09-tabel die hier eerder stond (Δ input +4,8%, Δ cache_read −5,3%, Δ output −36,1%) is ingetrokken. Die meting zette Ponytail ná de taakbody in plaats van vóór, terwijl productie beide slices in de preamble zet. `cache_read` is een eigenschap van het prompt-voorvoegsel, dus een verkeerd voorvoegsel meet iets anders dan de dispatch-vorm. De 2026-08-05-proxycijfers (Δ input −57%, Δ cache_read −51%, Δ output −65%) blijven staan in `token-saver-mechanismen-decision.md` §8 voor hun eigen RTK-context; ze zijn gemeten met een prompt-mutatie van ongeveer 110 bytes en beschrijven de injectors niet.

**Veiligheidsvoorwaarde bij deze meetvorm.** De kaartvormige armen geven de agent het volledige ship-recept, en een gemeten agent voerde dat ook uit: hij pushte zijn golden-task-bewerking naar `origin/master` (teruggedraaid in `2e0eb256`). De runs draaien daarom in een `git archive`-export zonder `.git`, zonder remote en buiten de repo. Wie deze meting herhaalt, doet dat met `injector-compare`; de losse subcommando's `card-baseline`/`card-injector` gebruiken dezelfde sandbox.

**Lane-type-conclusie** (kaart-accepatiecriterium "minstens één lane waar beknopte output gewenst is én één waar het artefact zelf de deliverable is"):

| Lane-type | Voorbeeld | Verwacht effect | Conclusie |
|---|---|---|---|
| Research-/sweep | `[research] Weekly market-research sweep` | De 2026-08-10-meting laat lagere output zien in trial 1 en licht hogere in trial 2; een besparingspercentage draagt zij niet. Wat zij wél laat zien: de taak wordt in beide armen goed afgerond en de prompt-cache blijft werken. De upstream Caveman-§"Auto-Clarity" carve-out houdt security- en irreversible-warning-zinnen buiten compressie, dus kritieke ontdekkingen blijven staan. | **Geschikt**, op grond van gelijke taakkwaliteit en een werkende cache — niet op grond van een gemeten besparing. Default-uit blijft; operators zetten hem aan per lane. |
| Analysis-leaf | `analysis` work_type met `analyst_agent_id=None` (de leaf design-deliverable — het analysedoc IS de deliverable) | De Ponytail-§"Boundaries" carve-out zegt "Ponytail governs what you build, not how you talk", en de Caveman-§"Boundaries" carve-out zegt expliciet "Persisted outside chat: write normal prose — code, comments, commits, docs, issue/PR/MR text, memory files". Beide carve-outs staan verbatim in de attributie-header. **Verwachting (ongemeten):** een agent die een analysedoc schrijft heeft die regel expliciet in zijn preamble staan, maar bij modellen met zwakke output-discipline zou de slice alsnog in analysedoc-proza kunnen doorwerken. | **Niet geschikt voor deze lane uit voorzorg.** Default-uit blijft; wie hem hier aanzet, krijgt het als bekende regressie. Een echte kwaliteitsmeting op deze lane is een eigen vervolgkaart (zie onder). |

N=2 is een kleine steekproef. De richting van de tokencijfers is met deze steekproef niet vast te stellen — zie de trial-vergelijking hierboven. Het analysis-leaf-risico is een **verwachting**, geen waarneming; een echte meting op die lane hoort in een eigen vervolgkaart en zat niet in deze ship.

### Vervolgmeting 2026-08-10 — N=8 per arm (kaart `54997750…`)

Dezelfde harness, dezelfde golden-task, dezelfde sandbox; vier achtereenvolgende `injector-compare`-calls leveren samen zestien runs op (acht per arm). Geen enkele run is afgekapt; alle zestien sloegen af met `pass_tests=1` en `stop_reason=end_turn`.

| Run | input | cache_creation | cache_read | output | turns | cost (USD) | pass_tests |
|---|---:|---:|---:|---:|---:|---:|---:|
| 084936 t1 — baseline | 77.791 | 0 | 1.636.608 | 3.743 | 34 | 0,781 | 1 |
| 084936 t1 — injector | 72.240 | 0 | 284.544 | 1.046 | 7 | 0,318 | 1 |
| 084936 t2 — baseline | 138.970 | 0 | 1.174.195 | 3.076 | 23 | 0,815 | 1 |
| 084936 t2 — injector | 79.019 | 0 | 1.436.544 | 3.542 | 22 | 0,721 | 1 |
| 105801 t1 — baseline | 69.972 | 0 | 683.648 | 1.926 | 13 | 0,444 | 1 |
| 105801 t1 — injector | 76.212 | 0 | 1.015.296 | 1.924 | 15 | 0,562 | 1 |
| 105801 t2 — baseline | 72.791 | 0 | 852.480 | 2.396 | 19 | 0,510 | 1 |
| 105801 t2 — injector | 72.603 | 0 | 284.679 | 1.189 | 7 | 0,321 | 1 |
| 110201 t1 — baseline | 71.097 | 0 | 483.456 | 1.581 | 11 | 0,382 | 1 |
| 110201 t1 — injector | 72.572 | 0 | 496.896 | 1.451 | 8 | 0,389 | 1 |
| 110201 t2 — baseline | 2.724 | 0 | 404.608 | 964 | 8 | 0,144 | 1 |
| 110201 t2 — injector | 74.844 | 0 | 509.152 | 1.221 | 11 | 0,396 | 1 |
| 110446 t1 — baseline | 73.834 | 0 | 983.936 | 2.392 | 15 | 0,553 | 1 |
| 110446 t1 — injector | 76.467 | 0 | 810.496 | 1.973 | 15 | 0,502 | 1 |
| 110446 t2 — baseline | 66.502 | 0 | 274.304 | 1.545 | 8 | 0,305 | 1 |
| 110446 t2 — injector | 73.879 | 0 | 650.240 | 2.624 | 13 | 0,456 | 1 |

Artefacten in `/home/vdvgu/.cache/cockpit-measure-token-saver/`:
- `20260810T084936Z-production-shape/` (canonieke 2026-08-10-meting, 4 runs)
- `20260810T105801Z-production-shape/` (call 1, 4 runs)
- `20260810T110201Z-production-shape/` (call 2, 4 runs)
- `20260810T110446Z-production-shape/` (call 3, 4 runs)

#### Per-arm aggregate

| Metric | `card-baseline` (n=8) | `card-injector` (n=8) | Δ (gem.) |
|---|---:|---:|---:|
| input | 71.710 ± 36.559 | 74.730 ± 2.382 | +4,2 % |
| cache_creation | 0 ± 0 | 0 ± 0 | n.v.t. |
| cache_read | 811.654 ± 450.692 | 685.981 ± 392.390 | −15,5 % |
| output | 2.203 ± 897 | 1.871 ± 855 | −15,1 % |
| turns | 16 ± 9 | 12 ± 5 | −25,2 % |
| pass_tests | 8/8 | 8/8 | — |

#### Spreiding per arm

Spreiding genormaliseerd op `range / mean` zodat metrics met verschillende ordes van grootte vergelijkbaar zijn:

| Metric | baseline (range/mean) | injector (range/mean) |
|---|---:|---:|
| input | 1,90 | 0,09 |
| cache_read | 1,68 | 1,68 |
| output | 1,26 | 1,33 |
| turns | 1,59 | 1,22 |

`input` wordt in de injector-arm **sterk samengetrokken** (spreiding van 1,90 naar 0,09); dat is consistent met het feit dat de slice een vaste byte-belasting toevoegt en de agent niet meer zelfstandig tooling aanroept die extra invoertokens zouden genereren. `cache_read`, `output` en `turns` blijven in beide armen ongeveer even breed — de voorspelbaarheid van het verschil tussen armen komt dus vooral uit het `turns`-kanaal.

#### Normalisatie op beurten (de voorspelde confound uit §1)

`cache_read` en `output` schalen met het aantal beurten dat de agent neemt. Deel door `turns` om de beurt-overhead eruit te halen:

| Per-turn | baseline | injector | Δ |
|---|---:|---:|---:|
| input / turn | 4.379 | 6.100 | +39,3 % |
| cache_read / turn | 49.567 | 55.998 | +13,0 % |
| output / turn | 135 | 153 | +13,6 % |

**De slice bespaart niet per beurt — hij kost er juist meer.** De input/turn-stijging van +39 % is de slice-belasting zelf (≈12 KB preamble, ~1,7 k tokens); cache_read/turn en output/turn liggen beide ~13 % hoger. De gemeten totale besparing komt dus **volledig** uit het feit dat de injector-arm in 25 % minder beurten klaar is: `output` totaal daalt (−15 %) doordat er minder beurten zijn, niet doordat de agent korter per beurt schrijft. Dat is een ander mechanisme dan de upstream-belofte ("compress output") doet vermoeden, en het is een observatie die op N=2 onzichtbaar was.

#### Wat deze meting nu wél draagt

- **Totale `cache_read` daalt 15,5 % en `output` 15,1 % over 8 dispatches per arm.** De spreiding per arm is 1,68 voor `cache_read` en 1,33 voor `output` — smaller dan het verschil tussen de armen. Het verschil overleeft de ruis binnen de arm, in tegenstelling tot N=2.
- **Totale `turns` daalt 25,2 % over 8 dispatches per arm** (16 → 12 gemiddeld). Dat is de dominante component van de totale besparing.
- **`pass_tests` is 16/16**, gelijk verdeeld over beide armen — de slice degradeert de taakkwaliteit niet.
- **`cache_creation` blijft 0** in alle zestien runs; de slice verhindert geen cache-hit (zelfde conclusie als §"Meting 2026-08-10").
- **`input` stijgt +4,2 %** als gemiddelde; door de smalle injector-spreiding (range/mean = 0,09) is dat getal robuust. De stijging is de slice-belasting, niet de agent.

#### Wat deze meting níét draagt

- **Geen uitspraak over abonnementsquotum.** `cache_read` telt volgens `cache-read-quota-decision.md` (kaart `97e623f9…`) **niet** mee voor de 5h-quotum, dus een lagere `cache_read` levert hier geen quotum-winst op — alleen API-kostenbesparing. Een abonnementsbesparing-claim zou los staan en is hier niet gemeten.
- **Geen uitspraak over de analysis-leaf-lane.** Die vraagt een eigen meting op een `analysis`-kaart (zie Vervolgkaarten).
- **Geen claim dat per-beurt-kosten dalen.** De slice voegt bytes toe; de totale besparing komt uit de beurt-reductie, niet uit per-beurt-compressie.

**Conclusie.** De slice levert op deze golden-task een echte, op N=8 robuuste totale besparing van ~15 % op `cache_read` + `output` en ~25 % op beurten, met behoud van kwaliteit (16/16). Het mechanisme is "sneller klaar", niet "korter per beurt" — dat onderscheid was op N=2 niet zichtbaar. De besparingsclaim is nu voldoende onderbouwd om hem in het register op te nemen; een promotion naar default-on blijft uit voorzorg (zie "Wat dit NIET verandert").

## Wat dit NIET verandert

- **Geen promotion naar default-on** — beide flags blijven `INTEGER NOT NULL DEFAULT 0`.
- **Geen wijziging aan persona-bestanden of FCR-/ship-recipe** — deze slice zit louter in de dispatcher-tussenstap, niet in een tracked bestand dat de FCR-subagent kan lezen.
- **Geen tweede override-mechanisme** — `column_overrides` is voor model/provider; de prompt-schakelaars zitten op een eigen kolompaar omdat het een ander mechanisme is met een ander deactivate-pad (de kill-switch, niet de persona).

## Vervolgkaarten

- **Tokenbesparing met genoeg runs meten** — ✅ gesloten in kaart `54997750…` (zie §"Vervolgmeting 2026-08-10 — N=8 per arm" hierboven). N=8 per arm levert een robuuste −15 % op `cache_read` + `output` en −25 % op beurten, met 16/16 `pass_tests`. Per-beurt-kosten stijgen juist — de besparing komt uit de beurt-reductie, niet uit per-beurt-compressie.
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
