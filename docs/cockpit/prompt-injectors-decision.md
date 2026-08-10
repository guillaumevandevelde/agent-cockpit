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

De kaart vroeg om een meting die **twee contrasterende lanes** dekt: een lane waar beknopte output gewenst is (compressie helpt) en een lane waar het analyseresultaat zelf de deliverable is (compressie schaadt). De reviewer keurde de eerste versie van deze tabel af omdat de cijfers niet door een echte meting waren gedekt (`cache_read` kwam niet voor; de meetuitkomst was niet gemeten). De huidige cijfers komen uit `scripts/measure-token-saver.sh with-injector` (2026-08-09, N=2 per variant); de **canonical numbers** in dit doc + `decisions.md` register-rij zijn deze verbatim-cijfers. De proxy-cijfers uit `token-saver-mechanismen-decision.md` §8 en uit de eerste meting hier (2026-08-05, N=1, proxy) blijven in dat doc staan voor hun eigen context maar zijn **niet** wat de lane-conclusie draagt.

### Meting 2026-08-09 — `scripts/measure-token-saver.sh with-injector`, N=2 trials per variant (verbatim slice)

`scripts/measure-token-saver.sh with-injector` voert dezelfde golden-task uit maar nu met de **verbatim** Caveman + Ponytail slice uit `backend/app/kanban/prompt_injectors.py` — dezelfde constanten die `resolve_active_injectors` aan `build_card_prompt` doorgeeft. N=2 trials per variant (4 dispatches totaal) op hetzelfde baseline-tijdstip, zodat `cache_read` over meerdere runs in dezelfde kolom kan renderen.

| Variant | input_tokens | cache_creation | cache_read | output_tokens | pass_tests |
|---|---|---|---|---|---|
| `baseline-1` | 48784 | 0 | 236160 | 1886 | 1 |
| `baseline-2` | 49408 | 0 | 240256 | 1175 | 1 |
| `with-injector-2` | 51847 | 0 | 201984 | 1023 | 1 |
| `with-injector-3` | 51088 | 0 | 249344 | 933 | 1 |
| **Δ (injector mean − baseline mean)** | **+2372 (+4.8%)** | 0 | **−12544 (−5.3%)** | **−552 (−36.1%)** | 2/2 = 2/2 |

Artefacten: `/home/vdvgu/.cache/cockpit-measure-token-saver/20260809T223000Z-verbatim-slice/` (baseline-1, baseline-2, injector-2, injector-3 — injector-1 viel uit op een Anthropic 429 `Token Plan usage limit reached` en is uit de aggregates gelaten; het is géén kwaliteitsfailure van de slice, de slice werd niet eens ge-evalueerd).

`injector-1/trial-1-with-injector.json` (rate-limit, gewoon genegeerd in de aggregates):
```
result: 'API Error: Request rejected (429) · Token Plan usage limit reached: ...'
usage : {input: 45394, cache_creation: 0, cache_read: 53760, output: 851}
```

**Wat het zegt vs. de 2026-08-05-proxy.** De proxy-mutatie van `apply_saver` (~110 bytes) en de verbatim slice (~5.7 KB × 2 = ~11 KB) zijn twee verschillende metingen en geven verschillende cijfers. De cache-key-shift bij de proxy was ≫ die bij de verbatim resolver (de resolver is byte-stabiel over `(project_key, column_name)`). Outputcompressie is bij beide aanwezig; de proxy overtreft de verbatim slice omdat zijn voorschrift directer is (`shortest possible phrasing` vs. de upstream-tekst die meer nuance heeft). Concreet:

| Metric | Proxy 2026-08-05 N=1 | Verbatim 2026-08-09 N=2 |
|---|---|---|
| Δ input | −57% | **+4.8%** |
| Δ cache_read | −51% | **−5.3%** |
| Δ output | −65% | **−36.1%** |
| pass_tests | 1/1 | 2/2 |

De proxy overstate de `cache_read`-drop met een factor ~10× en de output-drop met ~2×. De **canonical numbers** in dit doc en in `decisions.md` zijn de verbatim-cijfers; de proxy-cijfers blijven staan in `token-saver-mechanismen-decision.md` §8 voor zijn eigen context (RTK-werk).

**Lane-type-conclusie** (kaart-accepatiecriterium "minstens één lane waar beknopte output gewenst is én één waar het artefact zelf de deliverable is"):

| Lane-type | Voorbeeld | Verwacht effect | Conclusie |
|---|---|---|---|
| Research-/sweep | `[research] Weekly market-research sweep` | Output-reductie ~36% op de 2026-08-09-verbatim-meting (N=2 baseline, N=2 injector). De upstream Caveman-§"Auto-Clarity" carve-out houdt security/irreversible-warning-zinnen buiten compressie, dus kritieke ontdekkingen blijven staan. | **Geschikt.** Default-uit blijft; operators zetten hem aan per-lane. |
| Analysis-leaf | `analysis` work_type met `analyst_agent_id=None` (de leaf design-deliverable — het analysedoc IS de deliverable) | De Ponytail-§"Boundaries" carve-out zegt "Ponytail governs what you build, not how you talk" — gevolg: code wordt korter maar analysedoc-prose blijft. De Caveman-§"Boundaries" carve-out zegt expliciet "Persisted outside chat: write normal prose — code, comments, commits, docs, issue/PR/MR text, memory files". Beide carve-outs zijn verbatim opgenomen in de attributie-header. **Verwachting (ongemeten):** een agent die een analysedoc schrijft terwijl de slice aan staat, heeft Caveman's "Persisted outside chat: write normal prose" expliciet in zijn preamble staan, maar bij output-discipline-zwakke modellen zou de slice alsnog in analysedoc-prose kunnen doorwerken. | **Niet geschikt voor deze lane uit voorzorg.** Default-uit blijft; operators die hem aanzetten op analysis-leaf krijgen het als bekende regressie. Een echte kwaliteitsmeting op deze lane is een eigen follow-up (zie onder). |

N=2 is een small sample; de richting (output-reductie ~36%, cache_read −5%) is eenduidig. Het analysis-leaf-risico hierboven is een **verwachting**, geen waarneming — een echte meting op een analysis-leaf-lane (bijvoorbeeld `analysis`-kaart met de slice aan en een gemeten doc-kwaliteitsscore) hoort in een eigen vervolgkaart en is geen onderdeel van deze ship.

## Wat dit NIET verandert

- **Geen promotion naar default-on** — beide flags blijven `INTEGER NOT NULL DEFAULT 0`.
- **Geen wijziging aan persona-bestanden of FCR-/ship-recipe** — deze slice zit louter in de dispatcher-tussenstap, niet in een tracked bestand dat de FCR-subagent kan lezen.
- **Geen tweede override-mechanisme** — `column_overrides` is voor model/provider; de prompt-schakelaars zitten op een eigen kolompaar omdat het een ander mechanisme is met een ander deactivate-pad (de kill-switch, niet de persona).

## Vervolgkaarten

- **Analyse-leaf kwaliteitsmeting** — `5934b954…` sloot de verbatim-slice-meting op een executor-lane; de analysis-leaf-claim in dit doc blijft een **verwachting**, geen waarneming. Een vervolgkaart die een echte `analysis`-kaart met de slice aan door de leaf-deliverable-pijplijn haalt (spec → plan → doc) en de doc-kwaliteit meet, hoort in Backlog zodra er een analysis-leaf-kaart in scope is.
- **RTK-werk** — `token-saver-mechanismen-decision.md` §8 (kaart `c31333bf…`) draagt de proxy-cijfers uit 2026-07-25/08-05 voor zijn eigen RTK-context; die cijfers zijn niet wat dit doc als `cache_read` rapporteert.

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
