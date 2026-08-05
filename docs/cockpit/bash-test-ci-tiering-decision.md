**Datum:** 2026-08-05
**Status:** besloten
**Kaart:** `2fe2e4d2`
**Uitkomst:** **Drie CI-tiers + één lokale klasse; wallclock-asserts blijven met verhoogde drempel; één nieuwe parallelle CI-job `bash-tests`; de twee rode harnesses krijgen elk een fix-kaart vóór de gate hard wordt.**

# Bash-test tiering — welke `scripts/test_*.sh` horen in CI, en hoe

## 1. Beslissing in één alinea

De 39 bash-harnesses verdelen over **drie CI-tiers en één lokale klasse**, op basis van wat de harness écht aanraakt — niet op de naam van de klasse. Tier 1 (~28 harnesses, ~30s) draait als één nieuwe parallelle job in `quality.yml`. Tier 2 (4-6 harnesses, +~30s) draait in dezelfde job met `HOME=$(mktemp -d)` en vraagt `fetch-depth: 0`. Tier 3 (3-4 harnesses) heeft een git-worktree-dance nodig. Tier 4 (runtime + tmux, ~6 harnesses) draait **nooit** in CI; lokaal-op-verzoek of via de bestaande `iteration-loop`-presets. Geen pre-commit/pre-push hook (kaart `2fe2e4d2`'s expliciete buiten-scope).

## 2. De vijf klassen — wat ze écht nodig hebben

De kaart classificeerde op intent ("wat zou de harness kunnen raken"). De praktijk is fijner: bijna alle "kanbanDB"-harnesses gebruiken **synthetic SQLite fixtures in een tmpdir** en raken de productie-DB helemaal niet. Dat verschuift het zwaartepunt.

| Klasse | Aantal (kaart) | Aantal (gemeten) | Wat het écht nodig heeft | CI? |
|---|---|---|---|---|
| `pure` | 16 | ~22 | Alleen repo-files (docs/scripts/CLAUDE.md) | **Tier 1** ✓ |
| `kanbanDB` | 12 | ~10 | Synthetic SQLite in tmpdir — geen productie-DB | **Tier 1** ✓ |
| `kanbanDB` (echt) | — | 2-3 | Productie-DB in `~/.claude-registry/kanban.db` | **Tier 2** |
| `worktree` | 5 | 3-4 | `git worktree` + volledige geschiedenis | **Tier 3** |
| `runtime` | 4 | 4 | Poorten 8000/5173 of supervisor | **Tier 4** ✗ |
| `tmux` | 2 | 2 | Levende tmux-server | **Tier 4** ✗ |

**Waarom de herschikking.** Tien harnesses die de kaart als "kanbanDB" labelde, gebruiken dezelfde `sqlite3`-tegen-`tmpdir/fixture.db`-truc die `scripts/test_sweep_dangling_depends_on.sh:1-8` en `test_check_kanban_conventions.sh:1-7` documenteren — geen productie-state, geen backend, geen race. Die horen bij Tier 1. De resterende twee-drie (o.a. `test_worktree_gc.sh` via `kanban_active_worktrees.py`) lezen wél de live DB; die vragen een Tier 2-behandeling. **Eén meting bevestigt het totaalplaatje**: de kaart mat de 16 pure harnesses op 28s wallclock, 14 groen / 2 rood. De ~10 synthetic-SQLite-harnesses meten vergelijkbaar (~1-3s per stuk, in tmpdir).

**Wat er buiten Tier 1 valt en waarom:**

- **Tier 2** (`HOME`-geïsoleerd): harnesses die de productie-DB of de tmux-sessie van de host lezen. Twee tot drie stuks. Oplossing: `HOME=$(mktemp -d)` + de kanban-DB-schema-creatie via een korte backend-boot of `sqlite3 < <(schema.sql)`. Geen `seed-demo-home.py`-uitbreiding nodig — de bestaande `scripts/lib/seed-demo-home.py` seéden kaarten, en wat we hier nodig hebben is een lege-DB-met-schema. Een vervolgkaart levert die.
- **Tier 3** (werkboom + `fetch-depth: 0`): harnesses die `git worktree add` met historie doen. Drie tot vier stuks. CI-actie: `actions/checkout@v7` met `fetch-depth: 0`. Geen nieuwe infra.
- **Tier 4** (runtime/tmux, nooit CI): `test_cockpit.sh`, `test_capture_screenshots.sh`, `test_cockpit_doctor.sh`, `test_measure_token_saver.sh`, `test_measure_cache_read_quota.sh`, `test_list_orphan_bridge_sessions.sh`. Reden: ze spawnen processen die CI niet heeft, of raken een live tmux-server die per definitie niet bestaat in een ephemeral runner. Ze draaien al via `iteration-loop`-presets lokaal.

## 3. Wallclock-asserts — beleid

**Beslissing: blijven staan, maar met een verhoogde drempel die een CI-runner aankan.**

`scripts/test_run_single_test.sh:301` (`elapsed < 5000ms`) is gemeten op deze gedeelde box op 10s — een hardware-bagger die op een kale GitHub-hosted runner nooit voorkomt. De fix is niet "schrappen" (de assert bewaakt een echte regressieklasse: een test die ineens 50× trager wordt), maar "drempel verhogen naar 15s" — ruim boven wat een koude runner doet, ruim onder wat een regressie veroorzaakt. Een vervolgkaart past de drempel aan en voegt een comment toe die uitlegt waarom 15s.

**Niet gedaan — en waarom:**

- *Schrappen.* De assert is goedkoop, bewaakt een echte faalklasse, en is door andere ontwikkelaars in een `git blame` direct te begrijpen. Schrappen verzwakt de gate zonder alternatief.
- *Skippen op CI.* Een test die op CI niet draagt maar elders wel is een asymmetrie — de echte regression wordt op de host gevonden, niet in de merge. Dat is precies het faalpatroon dat deze hele kaart probeert te sluiten.

## 4. Real-tree-asserts — beleid

**Beslissing: blijven blocking op CI; geen advisory-onderscheid nodig.**

`test_check_decision_register.sh:84` (Task 6: "real docs/cockpit → exit 0 under --strict") en vergelijkbare taken in andere harnesses testen de échte repo-tree. Dat is **precies** wat een CI-gate hoort te doen — afwijken van "de repo werkt" is een regression die je in de merge wilt stoppen, niet in een lokale post-merge-run. De huidige "advisory-vs-blocking"-vraag uit dezelfde sweep-ronde (zie kaart-beschrijving) raakt dit niet: real-tree-asserts zijn al blocking waar ze moeten zijn (de harness-exit-code gate de job). Advisory is alleen voor harness-taken die een **historische drift** meten die niet per se een regression is — dat is een vervolgkaart op zich.

## 5. Job-vorm — één nieuwe parallelle CI-job

**Beslissing: nieuwe job `bash-tests`, parallel aan `backend-lint` / `backend-tests` / `frontend` / `e2e`, zonder `needs:` — pure bash-failures mogen geen ander gate verbergen.**

**Vorm gemotiveerd door dezelfde les als de bestaande split uit `quality.yml:9-25`. Eén sequentiële job laat de eerste falende gate de rest verbergen, en bash-tests die van 14 groen naar 12 groen zakken door een onschuldige wijziging wil je **zichtbaar** zien — niet als "backend groen, prima". De job krijgt:

- `actions/checkout@v7` met `fetch-depth: 0` (dekt Tier 3 in dezelfde job)
- Eén `run:` die `scripts/test_*.sh` aanroept volgens een Tier-1/Tier-2/Tier-3-stuur-bestand — `BASH_TEST_SKIP` extended-regex uit `scripts/compare-bash-tests.sh:71-73` is al de juiste schakelaar, de vervolgkaart voegt een `scripts/ci-bash-tests.sh` driver toe
- Géén `needs:` op andere jobs — bash-failures en backend-failures zijn orthogonale dimensies

**Waarom niet als stap in `backend-lint`:** `backend-lint` is al een zes-staps-fan-out (`quality.yml:43-65`) die expliciet bestaat om meerdere gate-types parallel te tonen — een zevende stap verdringt het overzicht en maakt de "alleen eerste failure"-val weer mogelijk. Een aparte job is dezelfde les, één laag hoger.

## 6. De twee huidige rode harnesses — preflight vóór de gate hard wordt

**Beslissing: elk krijgt een eigen fix-kaart; de gate wordt pas **hard** (blocking op de merge) als beide kaarten Done zijn.**

| Harness | Waarom rood | Route |
|---|---|---|
| `test_check_decision_register.sh` Task 12 | Header-drift op `real tree --check-headers --strict` | De al gefilede kaart `e9181e43` repareert de drift. Bash-gate blijft `--strict` zodra die kaart merged. |
| `test_run_single_test.sh` Task 11 | Wallclock-assert op 5000ms, mat 10s op deze box | Vervolgkaart verhoogt naar 15s (zie §3). Geen `--skip`; het smoke-test pad blijft lopen. |

De preflight-redenering: een gate die op de eerste dag **twee rode runs** oplevert, wordt uitgezet of genegeerd door operators — en dan heeft 'ie geen waarde meer. Beter: nu al groen krijgen, daarna **blocken**.

## 7. Vervolgkaarten (in deze sessie aangemaakt)

1. **CI-gate wiring** — nieuwe job `bash-tests` in `.github/workflows/quality.yml` + driver `scripts/ci-bash-tests.sh` die Tier 1/2/3 aanroept en Tier 4 overslaat. Verifieren: een echte PR tegen deze branch triggert de job en hij wordt groen.
2. **Tier-2 seeder** — minimale `scripts/ci-kanban-db-seed.sh` (of Python-equivalent) die een temp `HOME` zet, de kanban-DB-schema aanmaakt zonder demo-data, en de bestaande `scripts/lib/seed-demo-home.py` niet aanraakt.
3. **Wallclock-drempel** — `scripts/test_run_single_test.sh` drempel 5000 → 15000ms + comment met motivering.
4. **Header-drift fix** — afhankelijk van `e9181e43` Done zijn vóór de gate hard wordt. Als die kaart nog open is wanneer deze sessie merged, komt de header-fix-harness als zelfstandige fix.
5. **Tier-3 werkboom-seed** — kleine uitbreiding van de driver die `git worktree add --detach` doet voor de drie tot vier harnesses die volledige historie nodig hebben; `fetch-depth: 0` in `actions/checkout@v7` dekt de rest.

## 8. Buiten scope (expliciet)

- **Lokale pre-commit/pre-push hook.** Bewust weggehaald op 2026-07-05 (zie `CLAUDE.md` Git-Workflow-sectie). De CI-route vervangt 'm.
- **Een nieuwe advies-vs-blocking-discussie.** Raakt aan een andere sweep-kaart; hier afgewikkeld door de simpele regel "real-tree-asserts zijn al blocking waar ze moeten zijn".
- **Tier-4 harnesses (runtime/tmux) in CI krijgen.** Ze blijven lokaal via de bestaande `iteration-loop`-presets (`bash-test-attr`, `cockpit-doctor`).

## 9. Heropen-criteria

- Een Tier-1-harness blijkt op een koude GitHub-runner structureel boven de 60s-budget uit te komen → heroverweeg de budget-toewijzing of splits Tier 1 verder op.
- Een Tier-2-harness blijkt méér productie-state nodig te hebben dan een lege schema-only-DB levert (bijv. seed-data voor fixtures) → upgrade Tier 2 naar een mini-seed of verhuis naar Tier 4.
- Een Tier-3-harness produceert op CI een andere exit-code dan op de host vanwege `fetch-depth: 0`-tekort → verhoog of pin de checkout-versie.
- De 2 rode harnesses worden niet groen binnen een week na deze beslissing → de gate wordt tijdelijk advisory, niet uitgezet — een advisory gate is nog steeds zichtbaar in de Activity feed.
