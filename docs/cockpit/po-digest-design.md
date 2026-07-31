---
title: "Wekelijkse product-owner-digest — ontwerp (secties, bronnen, register, oplevering)"
type: spec
status: active
---

# Wekelijkse product-owner-digest — ontwerp

**Datum:** 2026-07-25
**Kaart:** `4e69915f65724125bb8579c6051976cf` (kind van `75c0952f…`)
**Bron:** [`product-owner-volgbaarheid-analyse.md`](./product-owner-volgbaarheid-analyse.md) §4.1 (kaart A) + §5
**Status:** ontwerp vastgelegd; twee vervolgkaarten gefiled (§8)

Dit doc beantwoordt de vier vragen die de kaart openliet: **welke secties**, **welke
bronnen per sectie**, **welk taalregister**, en **waar de output landt**. Het bouwt
géén nieuwe scheduling-infra — de cadans hangt op de bestaande `scheduled_at` +
auto-dispatch + chain-of-one-shots uit
[`recurring-cadence-proposal.md`](./recurring-cadence-proposal.md).

---

## 1. Het probleem in één meting

De digest bestaat omdat de product owner de stroom niet kan volgen. Dat is niet
gevoelsmatig — het is telbaar. Gemeten op het echte bord (`~/.claude-registry/kanban.db`,
project `git:github.com/guillaumevandevelde/claude-cockpit`, venster 2026-07-18 → 2026-07-25):

| Signaal | Aantal in 7 dagen |
|---|---|
| Kaarten met een `**Summary:**`-comment (= afgerond werk) | **130** |
| `**Summary:**`-comments totaal (herhaalde Done-moves meegerekend) | 246 |
| `**Review:**`-comments | 176 |
| `**Impediment:**`-comments | 41 |
| `**Outcome:**`-comments (analyse-uitkomsten) | 18 |
| Nieuwe rijen in `decisions.md` (uniek, na dedupe) | **13** |
| `delete`-ops op kaarten | 112 |

Reproductie: `python3 scripts/po-digest-source.py --since 2026-07-18 --until 2026-07-25`
zodra kaart K1 (§8) geland is; tot dan de queries in §3.1 direct tegen de op-log.

✅ Geïmplementeerd (kaart `6811916c…`): `scripts/po-digest-source.py` met bash-harness
[`test_po_digest_source.sh`](../../scripts/test_po_digest_source.sh); venster-normalisatie
tussen tz-aware CLI-bounds en SQLAlchemy's `DateTime(timezone=True)`-opslag
(spatie-separator, geen offset, microseconden) is vastgelegd door
`scripts/po-digest-source.py::_sqlite_datetime_bound` en een boundary-regressie-assert
in Task 2/Task 7.

130 afgeronde kaarten en 13 richtingsbeslissingen per week is precies de orde-grootte
waar één mens op kaart-hoogte op stukloopt. De digest moet dat terugbrengen naar
**één pagina die in vijf minuten te lezen is** — dus cureren en clusteren, niet lijsten.

---

## 2. Beslissing vooraf — de Done-kolom is géén bruikbare bron

Dit is de niet-vanzelfsprekende bevinding van deze analyse, en hij bepaalt het hele ontwerp.

De voor de hand liggende bron voor "wat is er opgeleverd" is
`GET /api/v1/kanban/cards?project_key=…&column=Done` — die levert per kaart een
`done_summary` + `completed_at` (`backend/app/api/v1/kanban/router.py:551-558`, via
`service.enrich_done_info`). **Die bron is leeg.** Gemeten op dezelfde dag:

- De `Done`-kolom bevat **1** kaart.
- Van de **130** kaarten die in 7 dagen een `**Summary:**`-comment kregen, bestaat er nog
  **28** als rij in `kanban_cards`. De andere ~102 zijn verwijderd
  (`DELETE /cards/{cid}`, `router.py:1769` — het enige delete-pad; 112 delete-ops in het venster).

Wat wél overleeft is de **op-log**. `enrich_done_info` (`backend/app/kanban/service.py:360-381`)
leest de Done-summary niet uit een kolom op de kaart maar uit de `comment`-op met prefix
`**Summary:** ` — "de op-log blijft de bron van waarheid" staat er letterlijk in de docstring.
Die ops blijven staan als de kaart verdwijnt; de titel is terug te vinden in de
`create`-op-payload van dezelfde `entity_id`.

**Gevolg voor het ontwerp:** de digest leest sectie 1 en 4 uit `kanban_ops`, niet uit
`kanban_cards`. Een digest die op de Done-kolom was gebouwd, had deze week één regel
geproduceerd en er kloppend uitgezien.

---

## 3. De vier secties en hun bronnen

De kaart schrijft de vier secties voor; hieronder de databron, de selectie-regel en de
cap per sectie. **De caps zijn niet cosmetisch** — ze zijn wat een roll-up onderscheidt
van een dump (§1: 130 kaarten passen niet op een pagina).

| # | Sectie (PO-vraag) | Bron | Selectie | Cap |
|---|---|---|---|---|
| 1 | **Wat is er opgeleverd?** | `kanban_ops`: `comment`-ops met prefix `**Summary:** ` in het venster, nieuwste per `entity_id`; titel uit de `create`-op | Clusteren op thema, niet per kaart | ≤ 7 thema-bullets + telling van de rest |
| 2 | **Welke richtingsbeslissingen zijn genomen?** | `git log --no-merges -p -- docs/cockpit/decisions.md` in het venster → toegevoegde tabelrijen (`^+\| `), gededupliceerd | Elke unieke nieuwe rij telt; herzieningen (`↩︎ herzien door`) horen in sectie 4 | alle, 1 regel elk |
| 3 | **Wat wacht op jou?** | `GET /api/v1/kanban/wachtrij?project_key=…` (`router.py:441`, `service.py:1361`) | Oudste eerst; leeftijd expliciet noemen | ≤ 5 + telling van de rest |
| 4 | **Is er iets van koers veranderd?** | `↩︎ herzien door`-rijen in `decisions.md`; `**Outcome:** not_feasible` / `no_action_needed`-comments; heropende kaarten (`reopen`-ops) en `**Review:**`-comments die tot een `**Impediment:**` leidden; clusters van `[self-improve]`-kaarten op één thema | Alleen wat de *richting* raakt, niet elke rework | ≤ 3 |

### 3.1 Waarom deze bronnen en niet andere

- **Sectie 1 leest de op-log, niet de Done-kolom** — zie §2. Dit is de enige keuze in
  dit doc waar het intuïtieve alternatief stil een lege digest oplevert.
- **Sectie 2 leest `decisions.md` via git, niet de kaarten.** `decisions.md` is per
  CLAUDE.md het canonieke beslis-register; een beslissing die daar niet staat, is per
  conventie geen beslissing. Git geeft bovendien gratis het venster.
  **Gemeten valkuil:** hetzelfde rijtekst-blok verschijnt in meerdere commits (rebase,
  amend, cherry-pick over branches). Over 2026-07-18 → 07-25 leverde de ruwe grep **18**
  `+|`-regels op voor **13** unieke beslissingen, en `--no-merges` haalt dat verschil er
  *niet* uit. Dedupliceren op genormaliseerde rijtekst is dus verplicht, geen optimalisatie.
- **Sectie 3 roept het bestaande wachtrij-endpoint aan** en herimplementeert de
  classificatie niet. Die logica (open gate → impediment-met-vraag → review-verzoek →
  `awaiting_plan_ref`, oudste-eerst) leeft op één plek in `service.py:1361-1505`; een
  tweede kopie in een digest-script zou onvermijdelijk uit de pas lopen.
  **Bekende beperking, overgenomen niet opgelost:** op kaart `c7ea21b0…` staat een open
  impediment dat twee van de vier categorieën blijven tonen nadat ze zijn afgehandeld.
  De digest neemt daarom altijd de **leeftijd** per item op ("wacht 6 dagen") en claimt
  niet "dit is exact wat open staat". De fix hoort op `c7ea21b0…`, niet hier.
- **Sectie 4 is de enige sectie zonder eigen datastore.** Koersverandering is een
  *interpretatie* over drie bestaande signalen. Bewust géén nieuwe "koers"-tabel of
  -veld: dan zou iemand die moeten bijhouden, en niemand doet dat (dezelfde reden waarom
  de bron-analyse §4.3 de noord-ster-één-pager expliciet nog niet als kaart filede).
- **Retro-kaarten (`[self-improve]`) zijn géén eigen sectie.** De bron-analyse noemt ze
  bij de retro-signalen, maar losse retro-kaarten zijn engineering-hygiëne en horen niet
  op producthoogte. Ze tellen alleen mee in sectie 4 wanneer meerdere op hetzelfde thema
  landen — dán zeggen ze iets over richting.

---

## 4. Taalregister

De digest is de toepassing van de product-taal-conventie
([`kanban-conventions.md` §5](./kanban-conventions.md#5-product-taal-voor-done-summaries-en-impediment-options),
kaart `4358fe0a…`) één niveau hoger: die conventie regelt de taal *per kaart*, de digest
regelt de taal van de *roll-up*. Zeven regels, in volgorde van belang:

1. **Leid met wat de product owner nu kan of moet.** Niet "endpoint X toegevoegd" maar
   "je ziet nu in één klik wat er op je wacht".
2. **Clusteren boven opsommen.** Zeven kaarten over hetzelfde onderwerp zijn één bullet.
   Het aantal onderliggende kaarten mag als getal mee ("…(7 kaarten)"), de titels niet.
3. **Geen kaart-ids, bestandsnamen of endpoints in de lopende tekst.** Refs horen in een
   voetnoot-regel per bullet, zodat de PO kan doorklikken zonder dat de zin erover struikelt.
4. **Nederlands, tweede persoon, actieve zinnen.** "Je moet beslissen of…", niet "er dient
   een beslissing genomen te worden over…".
5. **Eén zin per punt, maximaal twee.** Wie drie zinnen nodig heeft, heeft niet genoeg
   gecureerd.
6. **Noem wat er níet gebeurd is als dat het verhaal is.** Een week zonder beslissingen is
   een bevinding, geen lege sectie. Vul niet op om de pagina te vullen.
7. **Geen aanbevelingen verpakt als feit.** Zegt de digest "dit loopt vast", dan hoort
   daar de meting of de kaart-ref bij die dat draagt.

**Bewust weggelaten:** metrics-dashboards (tokens, doorlooptijden, kaart-tellingen per
kolom). Dat is operationele telemetrie, niet productbetekenis; het staat al in de
Usage-/Sessions-vlakken en zou de digest terugduwen naar een rapport.

---

## 5. Waar de output landt

**Beslissing: een doc in de repo is canoniek, het kaart-comment is de notificatie.**

| Plek | Rol |
|---|---|
| `docs/cockpit/po-digest/YYYY-Www.md` | **Canoniek.** Eén bestand per week (ISO-weeknummer), gecommit en gepusht door de digest-sessie. Durabel, greppable, gever­sioneerd, en overleeft het verwijderen van kaarten (§2). |
| `docs/cockpit/po-digest/README.md` | Index, nieuwste eerst; de digest-sessie voegt één regel toe. |
| Done-`summary` van de trigger-kaart | **Notificatie.** Vier regels (één per sectie) + link naar het weekbestand. Zichtbaar op het bord waar de PO toch al kijkt. |

**Waarom niet alleen een kaart-comment:** kaarten worden verwijderd — 112 deletes in het
gemeten venster (§1). Een digest-archief dat op kaarten leeft, is over een maand weg.

**Waarom geen UI-pagina nu:** dat is een nieuw frontend-oppervlak voor een artefact dat
één keer per week verandert, terwijl de PO-facing UI-behoefte al bediend wordt door de
wachtrij-sectie op de Projects-pagina (`WachtrijSection.tsx`, kaart `c7ea21b0…`). De
bron-analyse §6 zegt expliciet "geen nieuwe monitoring-infra". Wordt het weekbestand
aantoonbaar gelezen en gemist in de UI, dan is een leesvenster erop een goedkope
vervolgkaart — de markdown is dan al het contract.

**Subdirectory, niet los in `docs/cockpit/`:** 52 bestanden per jaar zouden de
doc-index overspoelen. `scripts/check-doc-frontmatter.sh:121` scant met
`find "$DOCS_DIR" -maxdepth 1`, dus een subdirectory valt buiten de frontmatter-,
index- en `llms.txt`-generatie — precies zoals het bestaande
`docs/cockpit/measure-evidence/`. Weekbestanden hebben daarom géén frontmatter nodig.

---

## 6. Uitvoering: collector-script + skill

De digest splitst in een **mechanisch** deel (ruwe data ophalen over een venster) en een
**redactioneel** deel (cureren, clusteren, in producttaal schrijven). Alleen het tweede
deel hoort een LLM te zijn.

- **`scripts/po-digest-source.py --since … --until …`** → JSON op stdout met de ruwe
  bouwstenen per sectie. Deterministisch, testbaar, één plek waar de venster- en
  dedupe-regels leven. Zonder dit script schrijft elke wekelijkse sessie zijn eigen SQL
  en drift de definitie van "deze week" stilletjes.
- **`.claude/skills/po-digest/SKILL.md`** → roept het script aan, doet de redactie volgens
  §3-caps en §4-register, schrijft het weekbestand, post de notificatie, en maakt de
  opvolger-kaart aan (chain-of-one-shots, identiek aan `market-research` Step 7).

**Geen nieuw REST-endpoint.** Het script leest de kanban-DB read-only voor sectie 1/4 en
roept `GET /wachtrij` aan voor sectie 3; een endpoint zou een tweede consument nodig
hebben om zichzelf te verdienen, en die is er (nog) niet — zie §5 over de UI.

### 6.1 Venster-bepaling — niet via `.claude/state/`

De `market-research`-skill biedt als breadcrumb-optie een state-bestand in de worktree
(`.claude/state/research-last-run.json`). **Voor de digest werkt dat niet:** `.gitignore:87`
negeert `.claude/state/`, en elke dispatch krijgt een verse worktree — het bestand is er
dus per definitie nooit bij de volgende run.

Het venster komt daarom uit het **vorige weekbestand**: `since` = het `until` van het
nieuwste bestand in `docs/cockpit/po-digest/`, `until` = nu. Dat is zelfcorrigerend na een
gemiste week (het venster rekt op in plaats van een gat te laten) en heeft geen state
buiten de repo. Bestaat er nog geen weekbestand, dan is `since` = nu − 7 dagen.

---

## 7. Cadans en pauzeren

**Wekelijks, maandag 08:00 Europe/Brussels**, via `scheduled_at` op een trigger-kaart in
`Backlog` — exact het mechanisme uit
[`recurring-cadence-proposal.md`](./recurring-cadence-proposal.md) §4, inclusief de
`_is_due()`-gate en de chain-of-one-shots-opvolger.

**Waarom 08:00 en niet 09:00:** de `market-research`-trigger staat al op maandag 09:00
(kaart `210e5042…`, `scheduled_at` `2026-07-27T09:00:00+02:00`). Twee zware sessies die
tegelijk op dezelfde box starten is onnodig; bovendien hoort de digest de PO's
maandagochtend te openen, niet erin te vallen. De digest kijkt terug, market-research
kijkt vooruit — de volgorde klopt ook inhoudelijk.

Pauzeren, verschuiven, overriden en auditen: identiek aan
[`recurring-cadence-proposal.md`](./recurring-cadence-proposal.md) §5 (`scheduled_at`
opschuiven, kaart verwijderen, handmatig dispatchen; er is géén `enabled`-veld op
`KanbanCard`). Die tabel wordt hier niet gedupliceerd.

**Autonomiegrens:** de digest-sessie leest bord-data en schrijft één markdown-bestand plus
één kaart-comment. Ze wijzigt geen code, dispatcht niets en maakt geen werk-kaarten aan —
alleen haar eigen opvolger.

---

## 8. Vervolgkaarten

Twee kind-kaarten, met een echt contract ertussen (K2 consumeert de JSON van K1):

- **K1 — `scripts/po-digest-source.py` + bash-harness** (`feature`). De deterministische
  collector: venster-parsing, op-log-query voor sectie 1/4, git-extractie + dedupe voor
  sectie 2, `GET /wachtrij` voor sectie 3, JSON op stdout.
- **K2 — `po-digest`-skill + trigger-kaart + eerste run** (`feature`, `depends_on` K1).
  De redactie-kant: skill-tekst met de §3-caps en het §4-register, `docs/cockpit/po-digest/`
  scaffolding, seed-trigger-kaart met `scheduled_at`, en één echte digest als bewijs.

Dedup-pass gedaan tegen `Backlog` (13 kaarten) en `Impediment` (10 kaarten) op 2026-07-25:
geen bestaande kaart raakt de PO-digest. De aanpalende kaarten zijn
`210e5042…` (market-research-trigger, andere richting: vooruit i.p.v. terug) en
`c7ea21b0…` (wachtrij — leverancier van sectie 3, geen overlap).

## 9. Wat dit NIET is

- **Geen tweede bord en geen nieuwe waarheid.** De digest dupliceert niets; hij vertaalt
  bestaande op-log-, git- en wachtrij-data naar producthoogte.
- **Geen statusrapport per agent of per kaart.** Wie welke kaart deed staat op het bord.
- **Geen vervanging van `decisions.md`.** Sectie 2 verwijst ernaar; het register blijft
  de agent-facing bron van waarheid.
