**Datum:** 2026-08-16
**Status:** besloten
**Kaart:** `3f5cc319991b4a2c953833806d8d8c0c`
**Uitkomst:** De `e2e`-job, het `npm run test:e2e`-script, `npx playwright install`-stap, het `frontend/e2e/`-tests-bestand en de `@playwright/test`-devDependency verdwijnen uit CI en uit `package.json`. Beoogde end-to-end-dekking herwinnen we via de iso-component-preview-flow en vitest-coverage in plaats van een browserrij in CI. ↩︎ herziet de "e2e advisory blijft"-rij uit `ci-gate-decision.md` (kaart `4f56f169…`, register-rij 45 hieronder).

> Deze beslissing antwoordt één vraag: **verdwijnt de `e2e`-job, of wordt
> hij een echte gate**. Ze is uitgesteld vanuit kaart `a9ebe6c0…`
> (`ci-supersession-strategie-decision.md`), die de CI-vorm en
> workflow-route vastlegde en het oordeel over de `e2e`-rol bewust
> openhield.

## 1. Het probleem dat de keuze draagt

De meting op kaart `773194bc…` over de laatste 100 `quality.yml`-runs op
master zag dat de `e2e`-job op **6/100 runs groen** eindigde. Bron van het
"groen zonder bewaken":

- `continue-on-error: true` op `.github/workflows/quality.yml:214` — een rode
  `e2e` faalt de workflow niet.
- `needs: [backend-tests, frontend]` op `.github/workflows/quality.yml:178`
  — bij een rode `backend-tests` wordt `e2e` overgeslagen en daarna
  geannuleerd via `cancel-in-progress: true` op
  `.github/workflows/quality.yml:20`.
- Een removal van die twee mechanismen zou de job tot een echte gate
  maken — _als_ wat hij waarneemt ook maar enigszins informatief was.

Daar zit een tweede, fundamentalere laag onder. De suite zelf heeft **28
regels** en drie tests in `frontend/e2e/smoke.spec.ts:9-28`, elke test
doet niets meer dan `page.goto(<route>)` plus een
`expect(main h1).toContainText(...)`. Dat is een route-laadtest, geen
end-to-end-dekking — geen login, geen dispatch-flow, geen agent-mail,
geen kanban-interactie. **De claim dat we hier "de enige
end-to-end-dekking" verliezen klopt formeel, dekt materieel de
werkelijkheid niet.**

En dan de derde laag: de test tegen een verkeerde poort draait.
`frontend/playwright.config.ts:6` zegt `baseURL: 'http://localhost:8000'`,
terwijl de frontend in CI gewoonlijk op een eigen Vite-poort draait en de
e2e-step bovendien alleen `uvicorn` op :8000 bootst zonder frontend-build
te serven. Een test als `page.goto('/')` landt daardoor op FastAPI's
default-root, niet op de cockpit-app. **Zelfs wanneer de tests groen
rapporteren, bewaken ze niet wat hun naam suggereert.** Dat is de
gevaarlijkste klasse vals-positief: een CI-rij die structureel
doorstroomt en zo het idee voedt dat "er getest wordt", terwijl de
werkelijke app niet eens geraakt wordt.

De combinatie — runner-minutes (CI loopt sinds ~2026-07-26 tegen een
spending-limit-block), flake-risico, en een base-URL-config die dekt wat
er niet staat — maakt van elke minuut in deze job een netto-kost.

## 2. Twee richtingen, één gekozen

### A. Echte gate

`continue-on-error: true` weg, `needs`-keten weg zodat de job parallel
aan `backend-tests` + `frontend` loopt en een rode run de workflow echt
doet falen. Voorwaarde voor eerlijke meting: de `baseURL`-bug in
`frontend/playwright.config.ts:6` eerst repareren en in de CI-step ook
de frontend-build serven op een eigen poort.

**Kosten:**

- ~2-3 minuten runner-minuten per Quality-run (`npx playwright install
  --with-deps chromium` is de zwaarste stap, de suite zelf seconden).
  Op een spending-limit-block weegt dat.
- Flake-risico op GitHub-hosted runners is reëel — exact de reden dat
  `ci-gate-decision.md` (kaart `4f56f169…`) in 2026-08-05 `e2e`
  bewust advisory liet. Een echte gate haalt dat risico terug in de
  merge-blokkade.
- De suite zelf blijft klein (3 route-loadtests) en na de base-URL-fix
  ongeveer even oppervlakkig. De investering in runner-minuten staat
  niet in verhouding tot de detectiewinst.

**Baten:**

- Eén CI-stap die daadwerkelijk meet of de drie routes renderen.
- Detecteert toekomstige regressies die andere gates missen (vitest
  test componenten, niet de runtime Vite-serve).

### B. Verdwijnen (gekozen)

Job, `npm run test:e2e`, de `npx playwright install --with-deps
chromium`-stap, het `frontend/e2e/`-bestand, `playwright.config.ts`, en
de `@playwright/test`-devDependency verwijderen. Geen nieuwe CI-belasting,
geen nieuwe flake-klasse, geen valse-confidence meer van de
base-URL-bug.

**Kosten:**

- Verlies van de (huidige, gebrekkige) route-load-signalering.
- Coverage die de kaart-omschrijving noemt ("login, dispatch-flow,
  agent-mail, kanban-interacties") was überhaupt niet aanwezig — de
  drie tests in `smoke.spec.ts` raken geen van die vier. Dat verlies
  is dus virtueel, niet materieel.
- Vervangende signalering ligt in twee andere lagen die vandaag al
  draaien: vitest met coverage in de `frontend`-CI-job en de
  iso-component-preview-flow
  (`docs/cockpit/isolated-component-preview.md`), die Playwright tegen
  een scratch-Vite op een vrije poort mount en daarmee een echte
  component-mount bewijst. De bash-harness `scripts/test_cockpit.sh`
  dekt de dev-stack-lifecycle.

**Baten:**

- Nul runner-minuten voor deze job.
- Nul flake-risico in de merge-blokkade.
- De `baseURL`-bug verdwijnt met de config — geen vals-positief
  meer dat structureel doorstroomt.
- Eén devDependency minder (`@playwright/test ^1.61.1`,
  `frontend/package.json:49`) — `frontend/package.json:12`-script
  `test:e2e` en het hele `frontend/e2e/`-tests-bestand
  (`frontend/e2e/smoke.spec.ts`) kunnen mee uit.

**Niet gekozen:** een derde middenweg ("job weg, suite + config blijven
voor lokaal gebruik") is overwogen. Voordeel: behoud van een herstelpad
voor wie de iso-flow wil uitbreiden. Nadeel: een ongebonden
`@playwright/test`-devDependency en een halfdode test-script zonder
signaalwaarde voeden precies het "er staat een test, dus we dekken
iets"-misverstand dat we hier juist opruimen. Lokaal hergebruik kan via
`npx playwright` als dat ooit nodig is — de dependency is dan opnieuw
toe te voegen. Bewust niet in deze beslissing ingrijpen.

## 3. Implementatie-pad

Niet in deze beslissing — één backlog-kaart die dit uitvoert. De
verwachte afhakels:

- `.github/workflows/quality.yml`: het `e2e`-job-blok op regels `173-214`
  verwijderen.
- `frontend/package.json:12` en `:49`: `test:e2e`-script en
  `@playwright/test`-devDependency weg.
- `frontend/e2e/smoke.spec.ts` en `frontend/playwright.config.ts`
  verwijderen.
- Geen test-grondslag die de job dekte gaat verloren — de drie routes
  worden al door vitest-coverage en de iso-component-preview-flow
  afgedekt. De vervolgkaart hoeft dus geen "waarheen"-sectie voor
  bestaande tests.

## 4. Vervolgkaarten

| Titel | Type | rationale |
|---|---|---|
| Verwijder e2e-job + Playwright-deps per `ci-e2e-verdwijnen-decision.md` | chore | §3; één yaml-blok + drie bestandswijzigingen. |

## 5. Bekende risico's

- **Coverage gat blijft zichtbaar op het bord.** De suite was altijd al
  oppervlakkig; met deze keuze wordt dat feit juist expliciet. Een
  reviewer die "missen we hier niet een end-to-end-dekking?" vraagt,
  krijgt vandaag hetzelfde antwoord als gisteren — alleen zonder de
  illusie dat 'ie er is. Mitigatie: een toekomstige spike die de
  iso-component-preview-flow uitbreidt naar een route-smoke op een
  scratch-Vite + vrij poort kan dit _goed_ dekken zonder de
  runner-minutes-prijs.
- **Geen automatische fallback.** Andere CI-stappen vangen het niet;
  dat is inherent aan het "verdwijnen"-pad. Acceptabel: de runner-minutes
  die we terugwinnen wegen zwaarder dan de coverage-breedte die
  verdwijnt.
- **DevDependency `@playwright/test` is weg.** Wie tóch een lokale
  Playwright-suite wil draaien, moet 'm opnieuw installeren. Bewust
  aanvaard: een extra `npm i -D @playwright/test` is niet de
  ergonomische ramp die de huidige "het script staat er maar doet
  niets"-situatie wel is.

## 6. Heropenen

- Bij een evidence dat de iso-component-preview-flow een klasse bugs
  mist die een Playwright-suite wel zou hebben gevangen — bijvoorbeeld
  een runtime-render-pad dat vitest niet dekt — komt een heropen op
  deze beslissing op tafel, met de baseURL-correctie en de
  runner-minutes-prijs als bekende kosten.
- Bij een verandering in de spending-limit-situatie die runner-minutes
  ruim beschikbaar maakt, kan het echte-gate-pad opnieuw overwogen
  worden met dezelfde base-URL-fix als insteek.
- Bij een bredere beslissing over end-to-end-dekking van het board
  (login, dispatch-flow, agent-mail, kanban-interacties) verwijst deze
  kaart naar dat overkoepelende doc, niet andersom.
