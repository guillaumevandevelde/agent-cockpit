---
title: "Richting van Agent Cockpit — essentie, hosting, meldingen, kenniswerk en de volgorde van werken"
type: decision
status: decided
---

# Richting van Agent Cockpit — essentie, hosting, meldingen, kenniswerk

**Datum:** 2026-08-15
**Status:** besloten
**Kaart:** — (voortgekomen uit een richtingsgesprek, niet uit een kaart).
**Uitkomst:** **Cockpit is een persoonlijke agent-cockpit, geen software-fabriek en geen Claude Code-beheerapp.** Hij blijft draaien op de eigen machine en wordt van overal bereikbaar via een tunnel of tailnet, met drie schermen die op een telefoon werken. Meldingen vuren alleen bij een blokkade die de eigenaar nodig heeft of bij iets dat stuk is; de rest is ophalen plus één samenvatting. Kenniswerk loopt door dezelfde machinerie met een licht ceremonieprofiel in plaats van een tweede uitvoerpad. De bouwvolgorde is waarde-eerst, maar met een kernharding ervoor — duurzame toestand, alembic en een architectuurgrens — omdat twee van de drie waarde-onderdelen anders niet kunnen landen. Een herbouw is afgewezen met een expliciete herintredingsvoorwaarde. Het geërfde Claude Code-beheerpaneel is een kostenpost die via drie zelfvurende regels krimpt in plaats van via een opruimproject.

> **Type:** beslisdoc. Bron: richtingsgesprek van 2026-08-15 over het nut van de
> zelfverbeteringsloop, uitgelopen op de essentie van de toepassing.
>
> Verwant: [`00-orientation.md`](./00-orientation.md) (missietekst — wordt door §3
> herzien), [`kernharding-design.md`](./kernharding-design.md) (het ontwerp dat uit
> §5 volgt).

---

## 1. Aanleiding

De vraag was of de zelfverbeteringsloop iets oplevert en of continu draaien wenselijk is. Het antwoord is gemeten op de op-log van het kanbanbord en de git-historie, over de periode 26 juni tot 14 augustus 2026.

## 2. Wat de meting liet zien

| Bevinding | Cijfer |
|---|---|
| Kaarten aangemaakt / afgerond | 855 / 801 |
| Aandeel naar binnen gericht (`[self-improve]` + `[problem]`) | 318 kaarten, 37% |
| Daarvan over de eigen machinerie (dispatch, ship, CI) | 77% |
| Nieuwe feature-kaarten, week 29 → week 33 | 39 → 1 |
| Verhouding Impediment/Done, week 26–29 → week 30–33 | 0,09–0,14 → 0,29–0,47 |

Die laatste stijging wordt niet verklaard door de kaartmix: beide klassen zitten over de hele periode op 0,17. Wel is de noemer gedaald, want de totale doorvoer zakte mee. De conclusie is dus gedempt maar overeind: zeven weken intensieve zelfverbetering verlaagde de wrijving niet.

**Waarom niet.** De loop heeft geen verliesfunctie. Hij meet zichzelf aan "kaart bereikte Done", niet aan "de fabriek is beter". Daardoor optimaliseert hij voor de eigen wrijving, en die is het enige dat hij kan waarnemen.

**Wat hij wél oplevert:** 27 controle-scripts, 128 documenten, het beslis-register en de baseline-scriptparen. Dat is echte, blijvende waarde. Ze voorkomen regressies die je per definitie nooit ziet gebeuren.

## 3. Beslissing 1 — de essentie

Cockpit is een **persoonlijke agent-cockpit**: één plek om werk in te dienen, een divers team agents dat het uitvoert, en gedoseerde terugkoppeling. De agent-runtime is inwisselbaar gereedschap, geen product.

Afgewezen alternatieven: de fabrieksframing uit `00-orientation.md`, en de erfenis waarin het Claude Code-beheerpaneel het product is.

**Gevolg.** Negentien van de 33 frontend-features dienen die essentie niet: commands, hooks, permissions, plugins, mcp, mcp-server, output-styles, statusline, skills, memory, config, updates, security, endpoints, subscriptions, usage, context, backup, blueprints. Samen 28.600 regels, veertig procent van de frontend. Ze beheren `~/.claude/` op deze machine en kunnen daarom nooit mee naar een server.

## 4. Beslissing 2, 3 en 4 — hosting, meldingen, kenniswerk

**Hosting: bereikbaarheid, geen verhuizing.** De behoefte is van overal taken aanmaken en overzicht houden. Voor autonomie moet er hoe dan ook een machine aanstaan. Is dat de eigen machine, dan hoeft alleen het venster naar buiten. Uitvoering blijft thuis; een tunnel of tailnet plus drie mobiele schermen volstaat. Die drie zijn: een kaart aanmaken, het bord bekijken, en een melding afhandelen. De overige dertig schermen blijven bewust desktop-only. Het bestaande gedeelde token is achter een tailnet voldoende, op het open internet niet.

**✅ Beslist (kaart `6599db16…`): tailnet-only, geen tunnel.** Tailscale (of WireGuard) als enige mobiele route naar de cockpit. Geen publieke URL, geen reverse-proxy naar het open internet, geen cloudflare-tunnel. Het bestaande gedeelde token uit `RequireApiTokenMiddleware` (`backend/app/main.py:349`, standaard uit) volstaat: achter een tailnet is de socket zelf al het vertrouwensoppervlak, en alle devices die de cockpit mogen bereiken hebben de mesh-client. Effect op het product: drie mobiele schermen werken via dezelfde URL als op het bureau (`http://<mesh-host>:5173`), geen tweede authenticatielaag voor de operator. Effect op de aanvalsoppervlakte: een onbereikbare poort op het publieke internet — een verkennende scan vindt niets om tegen aan te praten. Wat wel blijft gelden: een publieke tunnel zou een dienst publiceren die shell-agents start onder het account van de operator, en dat is een ander risicoprofiel dan waar deze middleware voor ontworpen is.

**Meldingen: alleen bij uitzondering.** Push uitsluitend bij een blokkade die een beslissing vraagt, of bij iets dat stuk is. De rest is ophalen, plus één samenvatting per dag of week. Bij 801 afgeronde kaarten in zeven weken — ruim zestien per dag — maakt mijlpaalmelden van de cockpit een alarmpaneel dat je leert negeren.

**Kenniswerk: zelfde machinerie, licht profiel.** Een kennisproject is ook een repo met markdown, maar kiest een ceremonieprofiel: geen tests, geen PR, afronden is een directe commit, deliverable is een notitie of document. Een tweede uitvoerpad door `dispatch.py` is afgewezen — dat verdubbelt precies wat deze codebase groot heeft gemaakt.

Er ontbreekt hierbij een persona. `.claude/agents/` bevat er drie — analyst, engineer en reviewer — en die zijn alle drie fabrieksvormig. Een engineer-persona op een onderzoekskaart is een mismatch. Het ceremonieprofiel heeft dus een vierde persona nodig voor kenniswerk.

**✅ Geïmplementeerd (kaart `5fcfca7f…`): het ceremonieprofiel.** Een `ceremony_profile` kolom (`code` | `knowledge`, default `code`) op de `projects`-tabel + Pydantic Literal + alembic-revisie `7a1c9e3b8d5f`. De dispatch-prompt leest het profiel één keer per spawn (`_load_ceremony_profile` in `backend/app/kanban/dispatch.py`) en kiest `_build_knowledge_ship_instructions` wanneer het `knowledge` is — geen FCR-subagent, geen frontend-checks, geen browser-count, geforceerde direct-merge (geen PR-route), `attach_deliverable(kind="note", ...)`. Nieuwe persona `.claude/agents/researcher.md` voor kenniswerk. Geen bestaand project raakt zijn profiel kwijt: de default is `code` en een project moet expliciet via `PATCH /api/v1/projects/{id}` omschakelen. Eenentwintig tests in `backend/tests/test_ceremony_profile.py` dekken de Pydantic-enum, het ORM-default, de PATCH-route, de dispatch-branch (inclusief de PR→direct-downgrade voor kennisprojecten), en de vier faalmodi van `_load_ceremony_profile`.

## 5. Beslissing 5 — de volgorde

Route: **waarde-eerst**, met een kernharding ervóór. De drie waarde-onderdelen zijn het mobiele venster, de meldingsregel en het ceremonieprofiel.

De harding gaat eerst om twee harde redenen:

1. **De meldingsregel zou de amnesie erven.** `AsyncIOScheduler()` draait zonder jobstore, en zes module-level singletons in `services/scheduling/` houden de draaiende toestand in het geheugen. Een melding die zegt "je bent nodig" moet een herstart overleven.
2. **Het ceremonieprofiel kan niet landen.** Dat is een schemawijziging, en zonder alembic betekent dat het bord wissen — 855 kaarten en 18.834 operaties.

Het ontwerp van de harding staat in [`kernharding-design.md`](./kernharding-design.md).

**Stap nul-a: eerst de poort repareren.** Op 2026-08-15 bleek `quality.yml` drie runs op rij rood op master, en de nieuwste commit heette "herstel rode quality-gate". Twee oorzaken. De OpenAPI-snapshotcheck was omgevingsafhankelijk: `app/main.py` registreert `/` alleen als `frontend/dist` ontbreekt, dus een op een dev-checkout gegenereerde snapshot faalt op een CI-runner. En de e2e-smoketest voor `scheduled-messages` bleef bestaan nadat die feature op 2026-08-04 was uitgefaseerd.

Daarnaast dekte het vangnet de verkeerde route: `auto-fix-on-red-ci.yml` vuurt alleen op `pull_request`, terwijl het direct-mode ship-recept naar master mergt — een `push`. Alle drie zijn op 2026-08-15 gerepareerd. Dit staat hier omdat het hele plan aanneemt dat er een werkende poort is; die aanname was onwaar.

## 6. Beslissing 6 — opruimen als zelfvurende regel

Opruimen is geen voornemen maar een regel, want "we ruimen later op" heeft 148.000 regels opgeleverd. Drie regels:

1. Raakt een nieuw onderdeel een van de negentien geërfde schermen, dan is de standaardactie verwijderen — niet mobiel maken.
2. Eén erin, één eruit: zolang die negentien er staan, verdwijnt er bij elke nieuwe frontend-feature één geërfde.
3. Elke kaart die `dispatch.py` wijzigt, haalt er één samenhangend blok uit.

Regel 3 wordt afdwingbaar gemaakt door de omvangsratel uit `kernharding-design.md` §3.

**✅ Geïmplementeerd (kaart `8b1bd6bcf2244809b283696b90eef20c…`): regels 1 en 2.** `scripts/check-inherited-bucket-ratchet.sh` sommeert de regels van de negentien geërfde mappen en weigert groei (parallel aan de omvangsratel per bestand); een `--update` legt alleen krimp vast en weigert een achterdeur. Baseline opgenomen in `.inherited-bucket-baseline`. Eerste scherm verwijderd: `updates` (frontend `UpdatesPage` + `App.tsx` route + `navigation.ts` ingang + `RefreshCw`-import), 341 regels uit de bucket, 19 → 19 mappen. Vóór deze kaart was §6 alleen een voornemen; nu vuurt regel 2 op elke commit waar `git ls-files frontend/src/features/<geërfd>/` groeit.

## 7. Beslissing 7 — herbouw afgewezen, met herintredingsvoorwaarde

Een herbouw is afgewezen. Doorslaggevend: de drie architecturale gebreken zijn toevoegingen, geen vervangingen. Duurzame toestand, migraties en modulegrenzen moet je in een nieuwe codebase net zo goed bouwen. Een herbouw gooit bovendien 314.000 regels test weg, en dat is de reden dat het huidige systeem werkt.

**Herintredingsvoorwaarde.** De vraag komt terug wanneer na een jaar snoeien blijkt dat de kern nog steeds niet te wijzigen is, of wanneer het doel alsnog meerdere gebruikers of een ander uitvoeringsmodel vereist. Terugkeren gebeurt met een meting, niet op gevoel.

## 8. Beslissing 8 — de zelfverbeteringsloop begrenzen en richten

De loop blijft bestaan maar wordt begrensd en op het opruimwerk uit §6 gericht. Mechanisch beslisbaar werk is de enige klasse waarop zo'n loop convergeert, en opruimen is precies dat.

Drie ingrepen: een harde bovengrens op het aandeel dispatch-slots voor `[self-improve]`-kaarten; de verhouding Impediment/Done als aanjager in plaats van de klok; en een effectclaim per afgeronde zelfverbeterkaart, in de vorm die `sweep_unchecked_implemented_markers.py` al voor documenten afdwingt.

**Gebouwd op 2026-08-15: een aan/uit-schakelaar per bord.** `self_improve:<project_key>` in `KanbanMeta`, standaard aan, om te zetten via `GET`/`POST /api/v1/kanban/self-improve`. **Effect:** de drie producerende skills — `session-retro`, `flag-problem`, `session-problem-scan` — dragen nu een stap 0 die de schakelaar leest en direct stopt als de loop uit staat. Waarnemingen gaan dan naar de afsluitende samenvatting op de kaart in plaats van naar een nieuwe kaart, zodat ze niet verdwijnen maar wel bij een mens landen. Fail-open: is het endpoint onbereikbaar, dan draait alles zoals voorheen.

**Nog niet afgedwongen: de consumptiekant.** De dispatcher zou bestaande `[self-improve]`-kaarten moeten overslaan zolang de schakelaar uit staat, maar die wijziging hoort in `dispatch.py` — en dat bestand staat op 10.110 regels en valt onder de omvangsratel. Die poort weigert terecht. De volgende stap is het pane-resume-cluster uit `dispatch.py` lichten; pas daarna past de consumptiekant erin. Zie [`architectuur.md`](./architectuur.md) "Wat hier nog moet gebeuren".

**✅ Geïmplementeerd (kaart `ff9877ca…`): de consumptiekant.** `_next_card` filtert `[self-improve]`/`[problem]`-kaarten weg zodra de schakelaar uit staat; `dispatch_project` leest dezelfde meta-sleutel één keer per tick en heeft een per-iteratie defence-in-depth-check. Standaard aan = huidige gedrag voor bestaande callers; vier tests dekken beide richtingen (`test_self_improve_switch.py`).

**Een slotlimiet knijpt de instroom niet af.** Drie van de veertien skills produceren zelfverbeterkaarten: `session-retro` draait aan het einde van élke gedispatchte sessie, `flag-problem` middenin, en `session-problem-scan` erover. Dat is de pomp achter de 318 kaarten uit §2. Een bovengrens op dispatch-slots laat die kaarten alleen langer wachten. `session-retro` moet daarom voorwaardelijk worden in plaats van onvoorwaardelijk.

**✅ Geïmplementeerd (kaart `9a567259…`): de drie ingrepen.** `budget_closed` in `backend/app/kanban/self_improve.py` weegt per dispatch-tick de schakelaar, het slot-aandeel (`SLOT_CAP` 25% van de bezette claims, met een vloer van één) en de wrijving (`FRICTION_THRESHOLD` 0,20 voor Impediment/Done). `dispatch_project` roept die ene functie aan in plaats van de kale schakelaar. De effectclaim is een vangnet: `scripts/sweep_self_improve_effect_claims.py` vlagt Done-kaarten met een `[self-improve]`/`[problem]`-titel of -label zonder `Effect:`-zin, en hergebruikt daarvoor de `EFFECT_PATTERNS` van de docs-sweeper.
Effect: nog niet in productie waargenomen — de rem vuurt pas op een tick met een levende claim; wel gedekt door acht tests in `backend/tests/test_self_improve_switch.py` en zeven in `scripts/test_sweep_self_improve_effect_claims.sh`.

## 9. Buiten scope

Niet besloten in dit document: welke van de negentien geërfde schermen als eerste verdwijnt, het meldingskanaal zelf, de vorm van het ceremonieprofiel, en de precieze bovengrens uit §8. Die volgen uit de ontwerpen die op dit document staan.
