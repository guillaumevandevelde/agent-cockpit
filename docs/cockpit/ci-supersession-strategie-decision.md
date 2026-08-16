**Datum:** 2026-08-16
**Status:** besloten
**Kaart:** `a9ebe6c0a4bf4b95bc8b8babceeba487`
**Uitkomst:** Overstap op GitHub merge queue. Elke master-commit krijgt een afgerond CI-oordeel én master blijft groen. Direct-mode-ship verdwijnt — alle werk landt voortaan via PR. De `e2e`-job krijgt een aparte vervolgkaart; zijn rol is in deze beslissing nog niet vastgelegd.

> Deze beslissing antwoordt drie vragen tegelijk: **welke CI-vorm** dragen we op
> master, **welke workflow-route** voert werk naar master, en **wat doen we
> met de `e2e`-job** die vandaag niets bewaakt. De meting op kaart
> `773194bc…` maakte het probleem zichtbaar; deze kaart kiest de remedie.

## 1. Het probleem dat de keuze draagt

Meting op de laatste 100 `quality.yml`-runs op master (kaart `773194bc…`):

- 41 commits eindigden als `cancelled` — **41% kreeg nooit een afgerond oordeel**.
- Een "groene master" op zo'n commit is per definitie **ongemeten**.

Bron: `concurrency.cancel-in-progress: true` in `.github/workflows/quality.yml:20`
(annuleert de lopende Quality-run bij elke nieuwe push naar dezelfde ref). De
detector staat nu in `scripts/check-ci-health.sh:221-239` (check 3 — `--cancelled-threshold`).

## 2. Drie richtingen, één gekozen

| Richting | CI-oordeel per commit | Kosten | Workflow-impact |
|---|---|---|---|
| **A. `cancel-in-progress: false` op master** | ja | meer runner-minuten per push; CI loopt al sinds ~2026-07-26 tegen een spending-limit-block | geen |
| **B. Merge queue** (gekozen) | ja én master blijft groen | GitHub-side config + PR-only-route | **direct-mode-ship verdwijnt** |
| **C. Niets doen** | nee, detector maakt het wel zichtbaar | nul | geen |

**Waarom B.** Optie A koopt het oordeel met runner-minuten die we niet
hebben (spending-limit sinds 2026-07-26). Optie C accepteert 41% ongemeten
master-commits. B kost een workflow-migratie maar geen extra CI-minuten —
GitHub serialiseert PR's door de queue en cancelt superseded runs zelf.

## 3. Impliciet vallende tweede beslissing: direct-mode-ship verdwijnt

Een merge queue voegt commits op master **op volgorde** — een tweede push
mag pas door als de eerste groen is. Direct mergen naar master vanuit een
gedispatchte sessie (de huidige `ship mode: direct`-route in
`.claude/skills/git-ship/SKILL.md` en de inline-mirror in
`backend/app/kanban/dispatch.py::_build_ship_instructions`) breekt die
eigenschap. Daarom valt:

- **Direct-mode verdwijnt.** Elke dispatch-sessie levert een branch aan; het
  samenvoegen met master verloopt via PR en merge queue.
- **De `git push origin HEAD:master`-route stopt.** De fallback naar
  `git push origin --delete <branch>` (kaart `3027671c…`) was een direct-mode
  artefact; de PR-route activeert GitHub's `delete_branch_on_merge`
  automatisch.

Dit is geen implementatiedetail — het is een tweede productbeslissing die
nu impliciet valt. De skill en de persona-prompts verwijzen naar direct-mode
als de normale route; die verwijzingen worden in een aparte migratie-kaart
bijgewerkt (zie §5).

## 4. Wat de merge-queue-migratie vereist

Niet-uitgezochte punten — deze kaart legt de **richting** vast, niet de
vorm:

- **Queue-tooling.** GitHub native merge queue (`gh merge-queue`,
  repo-settings → "Require merge queue") versus een zelfgebouwde serialiser.
  Aanname: native — minste infra, al geïntegreerd met branch-protection.
- **Branch-protection.** `require_status_checks` op de queue, niet op
  individuele PR-runs. `strict` + `required_linear_history` blijven nodig.
- **Concurrency-group.** De huidige `cancel-in-progress: true` op de
  Quality-run (`.github/workflows/quality.yml:20`) blijft relevant: PR's
  mogen onderling nog steeds superseden, alleen de queue-finalise wacht
  op de groene commit.
- **Concurrent agents.** Meerdere gedispatchte sessies pushen gelijktijdig
  branches; queue serialiseert hun PR's. Geen werk voor de dispatcher, maar
  wel een waarneembaar verschil in merge-doorlooptijd.

## 5. Vervolgkaarten

Drie scope-blokken die elk een eigen kaart verdienen:

1. **Merge-queue-config.** Branch-protection + queue + de Quality-workflow
   aanpassen zodat `cancel-in-progress` blijft gelden voor PR-runs maar
   niet voor queue-eindpunten. Eén PR, één reviewronde.
2. **Direct-mode verwijderen.** `.claude/skills/git-ship/SKILL.md`,
   `backend/app/kanban/dispatch.py::_build_ship_instructions`,
   `engineer.md`/`analyst.md` session-end-workflow, eventuele andere
   verwijzingen. Vervangt `git push origin HEAD:master` door `gh pr create
   --fill` + wachten op queue-groen. Caller-sweep verplicht — een gemiste
   verwijzing laat een sessie tegen een gesloten branch pushen.
3. **`e2e`-rol apart.** Eigen beslissing — zie §6.

## 6. `e2e`-rol: eigen vervolgkaart, hier niet beslist

De meting op kaart `773194bc…` zag óók dat de `e2e`-job niets bewaakt:

- `continue-on-error: true` + `needs: [backend-tests, frontend]`
  (`.github/workflows/quality.yml:174-214`).
- Bij een rode `backend-tests` wordt hij overgeslagen, daarna geannuleerd.
- Slechts 6 van de 100 runs waren groen.

Twee richtingen die in een aparte kaart thuishoren: **echte gate** worden
(hard-fail op rode Playwright, ook voor de queue) of **verdwijnen**
(reden: flake-dominant, weinig informatief). Deze beslissing doet er geen
uitspraak over — dat is een **productbeslissing op zich** die de
queue-migratie noch blokkeert noch eraan hangt.

## 7. Wat niet verandert

- `scripts/check-ci-health.sh` blijft. De detector blijft nodig: ook na de
  queue-migratie kan supersedure binnen één PR-arm voorkomen, en de
  CI-naar-bord-terugkoppeling uit `ci-gate-decision.md` §2 leunt erop.
- `delete_branch_on_merge` blijft aan (2026-07-07). De merge-queue-route
  sluit een PR, dus de hook vuurt zoals het hoort.
- De huidige 3-way parallelle `backend-lint` / `backend-tests` / `frontend`
  gate-split (commit `0b2a4f4e…`) blijft — de queue wacht op alle drie.

## 8. Heropenen bij

- Een runner-minuten-budget dat B's CI-kosten wel kan dragen — dan is A weer
  een optie.
- Een GitHub-native merge-queue die op deze repo niet beschikbaar is (Free
  tier, org-policy) — dan wordt de queue-vorm een eigen beslissing.
- Een `e2e`-beslissing die de gate **verdwijnt** — dan vervalt ook de
  `continue-on-error` workaround en is de Quality-workflow opnieuw te
  snoeien.
- Een directe heroverweging van de **direct-mode**-verdwijning — alleen als
  een tweede productreden (niet alleen CI-doorlooptijd) de keuze
  ondersteunt.
