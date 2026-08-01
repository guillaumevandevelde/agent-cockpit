---
title: "Beslissing — De analyse-fase krijgt een afdwingbaar uitkomst-contract"
type: decision
status: decided
---

# Beslissing — De analyse-fase krijgt een afdwingbaar uitkomst-contract

**Datum:** 2026-07-15
**Status:** Beslissing / ontwerp — implementatie belegd op de vervolgkaarten in §7
**Kaart:** "Analyse - Analyse fase" (`e95729bb…`)
**Uitkomst:** **Uitkomst-poort op de Done-move.** Een analyse-kaart mag Done alleen binnen met een expliciete `outcome` uit een gesloten enum (`decomposed` — geverifieerd tegen echte kind-kaarten / `not_feasible` → label / `no_action_needed`); "input nodig" blijft `report_impediment`. Prompt-instructie alleen is afgewezen — dat was de vorige twee rondes en niets verifieerde het.

**Trigger (de gebruiker):**

> "De analyses blijven naar completed gaan zonder enig gevolg, verloren analyses. Dit kan
> toch niet zijn. Zorg ervoor dat de analyse fase een duidelijker gevolg krijgt. Liefst een
> gevolg in subtaken, maar indien input benodigd impediment. Als een analyse het resultaat
> had dat het niet wenselijk was maak dit duidelijk met een label op het kaartje 'Not
> Feasible' of dergelijke. Bekijk grondig een wenselijke aanpak."

Verwant: [`autonomous-leaf-spike-followup.md`](./autonomous-leaf-spike-followup.md) (de
vorige ronde van dit probleem), [`analyse-orphaned-followups-audit.md`](./analyse-orphaned-followups-audit.md)
(de eenmalige opruiming van de achterstand), en Backlog-kaart `d0089809…` ("Analyse —
koppel vervolgkaarten aan analyse", de aangrenzende bord-/levenscyclus-vraag — zie §6).

---

## 1. Waarom dit probleem er nog steeds is (de kern)

Dit is de **derde** keer dat "analyses verdampen" wordt aangekaart. Dat is op zichzelf het
belangrijkste datapunt: de vorige twee rondes hebben het probleem *begrepen* en toch niet
*opgelost*. De reden is scherp aan te wijzen.

De vorige ronde (`autonomous-leaf-spike-followup.md`, kaart `75b54887…`) koos de juiste
richting — de leaf-spike mag en moet zijn eigen vervolgkaarten aanmaken — en die fix is
**geland**: `_analyst_leaf_spike_override_note()` (`backend/app/kanban/dispatch.py:937`)
bevat vandaag de "Leaf-spike follow-up cards clause". De prompt van déze sessie draagt hem.

Maar die fix is **uitsluitend een instructie in een prompt**. En daar zit de gap:

> **Er bestaat geen enkele controle die verifieert dat een analyse-kaart iets heeft
> opgeleverd voordat ze naar Done mag.**

`mcp_server.move_card` (`backend/app/kanban/mcp_server.py:232–266`) kent precies één
poort:

```python
_SUMMARY_REQUIRED_COLUMNS = {"Done": "Summary", "Impediment": "Impediment"}
...
if label and not summary:
    return {"error": "summary_required", ...}
```

Een niet-lege `summary`-string. Meer niet. Een analyse-sessie die de follow-up-clausule
negeert — door contextdruk, door een aflopend budget, door drift, of door een model dat de
clausule simpelweg niet zwaar weegt — schrijft "Analyse afgerond, aanbevelingen staan in
§8" en de kaart is Done. Het bord toont een groene kaart. Niemand ziet het verschil met een
analyse die wél vier kaarten heeft aangemaakt.

**Instructies zonder verificatie zijn een verzoek, geen contract.** Elke ronde tot nu toe
heeft het verzoek beter geformuleerd. Dit doc kiest voor verificatie.

## 2. De drie gevolgen die de gebruiker benoemt

De opdracht bevat exact drie legitieme uitgangen van een analyse. Dat is geen toeval — het
is een volledige opsomming:

| Gebruiker zegt | Betekenis | Bestaat vandaag? |
|---|---|---|
| "Liefst een gevolg in subtaken" | De analyse levert vervolgkaarten op | ✅ mechanisme bestaat (`create_card`/`add_plan_attachment`), ❌ niet afgedwongen |
| "Indien input benodigd impediment" | De analyse stuit op een echte productbeslissing | ✅ `report_impediment(options=[…])` werkt, ❌ niet afgedwongen als alternatief |
| "Niet wenselijk → label 'Not Feasible'" | De analyse concludeert: niet doen | ❌ **bestaat helemaal niet** |

De derde is de meest interessante, want hij legt een echt gat bloot dat verder gaat dan
discipline. Vandaag is er **geen verschil op het bord** tussen:

- een analyse die concludeerde *"dit moet niet gebouwd worden, en hier is waarom"* — een
  volwaardig, waardevol resultaat; en
- een analyse die vergat haar kaarten aan te maken — een mislukking.

Beide zijn een groene Done-kaart met wat proza. Dat is precies de "verloren analyse" die de
gebruiker beschrijft. Een NO-GO is een **uitkomst**, geen afwezigheid van een uitkomst, en
het bord hoort dat te laten zien.

## 3. Wat de codebase al biedt (en wat niet)

Grondig nagekeken, want dit bepaalt hoe klein de ingreep kan zijn:

- **`labels` bestaat al.** `KanbanCard.labels: Mapped[list | None]` (`models.py:49`), zit in
  `CardCreate`/`CardUpdate`/`CardResponse` (`schemas.py:84,135,156`), wordt gematerialiseerd
  door `operations.py:137,201`, en wordt **al gerenderd** door `CardItem.tsx:234`. Een mens
  kan ze zetten via `CardEditDialog`.
  → **Voor "Not Feasible" is geen schema-wijziging en geen frontend-werk nodig.** Het label
  verschijnt zodra iets het schrijft.
- **Maar geen enkele agent kan een label zetten.** De MCP `update_card`
  (`mcp_server.py:270–302`) accepteert `title`, `description`, `depends_on`, `metadata` —
  **geen `labels`**. `create_card` evenmin. Het schrijfpad bestaat dus alleen via REST PATCH
  of via een mens in de UI. Dat is de concrete reden dat de derde uitkomst vandaag
  onhaalbaar is voor een gedispatchte sessie, los van alle promptdiscipline.
- **De analyse-detectie bestaat al.** `_is_analyst_leaf_spike(card)` (`dispatch.py:906–934`)
  is precies de predikaat die we nodig hebben: `work_type == "analysis" or agent ==
  "analyst"`. Herbruikbaar (zie de plaatsings-noot in §5).
- **Het `summary_required`-patroon bestaat al.** De poort die we willen bouwen heeft exact
  dezelfde vorm als een poort die al in productie draait en bewezen werkt: controleer bij de
  Done-move, weiger met een actionable foutmelding, verplaats de kaart niet.

Dat is een gunstige uitgangspositie: de ingreep is klein en volgt bestaande patronen.

## 4. Ontwerpruimte

| # | Richting | Oordeel |
|---|---|---|
| 1 | **Beter formuleren in de prompt** — de follow-up-clausule strenger opschrijven. | ✗ **Afgewezen.** Dit is exact wat de vorige twee rondes deden. Het probleem is niet dat de instructie onduidelijk is; het is dat niets 'm controleert. Een derde herformulering is de definitie van "hopen op discipline". |
| 2 | **Uitkomst-poort op de Done-move** — een analyse-kaart mag Done alleen binnen met een expliciet benoemde uitkomst uit een gesloten enum, waarbij `decomposed` geverifieerd wordt tegen echte kind-kaarten. | ⭐ **Gekozen.** Spiegelt `summary_required` exact (zelfde bestand, zelfde vorm, bewezen patroon). Maakt de drie uitkomsten van §2 tot het *enige* vocabulaire. Prompt-drift kan 'm niet omzeilen. |
| 3 | **Achteraf-sweeper** — een script dat Done-analyses zonder gevolg detecteert. | ⭐ **Gekozen, maar als vangnet naast #2, niet in plaats van.** Detecteert ná de feiten (de audit van 2026-07-14 déed dit handmatig — het werkte, maar pas nadat de analyse al verdampt was). Als *backstop* voor het REST-bypass-gat (§5) en voor de historische voorraad is het wél waardevol. |
| 4 | **Done-summary parsen** op "voorgestelde vervolgkaarten". | ✗ **Afgewezen.** Vrij proza zonder schema; broos. Zelfde argument als waarom `autonomous-leaf-spike-followup.md` §4 het §8-parsen afwees. |

## 5. Beslissing — het uitkomst-contract

**Een analyse-kaart (`work_type == "analysis"` of `agent == "analyst"`) mag de Done-kolom
alleen binnen met een expliciet benoemde uitkomst.** `move_card` krijgt een `outcome`-
parameter met een gesloten enum van drie waarden — de drie uit §2:

| `outcome` | Betekenis | Verificatie | Neerslag op de kaart |
|---|---|---|---|
| `decomposed` | De analyse leverde vervolgkaarten op | **Geverifieerd:** de kaart moet ≥1 kind-kaart hebben (`parent_card_id == card.id`). Een claim zonder kinderen wordt geweigerd. | — (de kind-kaarten zíjn het bewijs) |
| `not_feasible` | De analyse concludeert: niet doen | Rationale verplicht in `summary` | Label **`not-feasible`** + `**Outcome:**`-comment |
| `no_action_needed` | Sturings-/ontwerpdoc; geen kaarten van toepassing | Rechtvaardiging verplicht in `summary` | Label **`no-action-needed`** + `**Outcome:**`-comment |

De vierde uitgang — "input nodig" — is géén Done-move: dat is `report_impediment`, dat al
werkt en de kaart naar Impediment stuurt. De poort hoeft 'm niet te kennen; hij ontstaat
vanzelf doordat de analist bij een echte fork geen van de drie waarden eerlijk kan invullen.

Ontbreekt `outcome` op een analyse-Done-move, dan weigert de tool met
`{"error": "outcome_required", "message": …}` — dezelfde vorm als `summary_required`, met
een bericht dat de drie opties opsomt.

### Waarom `no_action_needed` geen achterdeur is

Dit is de eerlijke vraag bij dit ontwerp, en ze verdient een expliciet antwoord: een
sessie die onder druk staat kan `no_action_needed` invullen en alsnog wegkomen. De poort
maakt het overslaan van vervolgwerk niet *onmogelijk*.

**Dat is ook niet wat ze belooft.** De winst is dat overslaan **expliciet, benoemd en
auditeerbaar** wordt in plaats van stil. Vandaag is een verdampte analyse
ononderscheidbaar van een geslaagde. Met de poort draagt ze een label dat op het bord
staat, in de activity-feed, en dat de sweeper (§7 #3) kan tellen. Een mens die drie
`no-action-needed`-analyses op rij ziet, weet dat er iets mis is — vandaag ziet hij drie
groene kaartjes.

De poort verlegt de vraag van *"heeft deze sessie zich gedragen?"* (onbeantwoordbaar) naar
*"welke uitkomst heeft ze opgeschreven, en klopt die?"* (controleerbaar). Dat het
`decomposed`-pad hard geverifieerd wordt tegen echte kind-kaarten is wat de eerlijke
route ook de makkelijkste route maakt: liegen over `decomposed` kán niet, en de twee
overige waarden kosten een geschreven rechtvaardiging.

### Bekend gat: het REST-pad

De poort zit in de **MCP-tool**, niet in de datalaag. `POST /cards/{id}/move` en
`PATCH /cards/{cid}` (met `column`) gaan rechtstreeks naar `apply_operation("move")`
zonder enige controle. Dat betekent:

- **Bewust:** een **mens** die een kaart in de UI naar Done sleept wordt *niet* geblokkeerd.
  Dat is juist gedrag — de mens is de beslisser, en een bord dat zijn eigenaar tegenwerkt is
  een slecht bord (CLAUDE.md: "zonder de gebruiker uit de beslissingsketen te verwijderen").
  De poort is een **agent-disciplinepoort**, geen data-integriteitsconstraint.
- **Risico:** de dispatch-prompt instrueert agents om bij een `-32602`-fout terug te vallen
  op exact dat REST-pad. Een agent die op de poort stuit *kan* er dus omheen. Dat vergt
  bewuste omzeiling en niet louter drift, maar het gat is echt en wordt hier niet
  weggeschreven. Het is de directe reden dat de sweeper (§7 #3) meegaat als vangnet in
  plaats van "later misschien".

## 6. Afbakening tegenover kaart `d0089809…`

`d0089809…` ("Analyse — koppel vervolgkaarten aan analyse") vraagt om een aparte kolom
waar de analyse blijft staan tot ze compleet is, subtaak-status zichtbaar op de
analyse-kaart, hernoemde statussen, en een `completed`-label. Dat is een
**levenscyclus-/visualisatie-vraag** over het bordmodel.

Dit doc beslist over het **uitkomst-contract**: wat een analyse moet opleveren om te mogen
afsluiten. De twee raken elkaar ("een analyse mag niet zomaar completed worden") maar
lossen verschillende helften op: `d0089809…` maakt de status *zichtbaar*, dit doc maakt de
uitkomst *verplicht*. Ze zijn complementair en de kaarten hieronder blijven bewust weg uit
de kolom-/statusvocabulaire-scope van `d0089809…`.

**Bewust géén kaart:** visuele styling van het `not-feasible`-label (eigen kleur i.p.v. de
generieke chip). `CardItem.tsx:234` rendert labels al, dus de gebruikersvraag ("maak dit
duidelijk met een label") is met §7 #1 volledig ingelost; kleur is politoer. Het
label-vocabulaire hoort bovendien thuis bij `d0089809…`, dat sowieso over labels gaat.
Dubbele kaarten zijn erger dan een grijze chip.

## 7. Vervolgkaarten (aangemaakt in deze sessie)

Conform de leaf-spike-follow-up-clausule maakt deze sessie haar eigen vervolgkaarten aan;
dit doc is de verantwoording, de kaarten zijn de uitvoerbare neerslag.

1. **`[feature]` Uitkomst-poort op `move_card` voor analyse-kaarten** (geen deps) — de
   kern: `outcome`-enum, `decomposed`-verificatie tegen echte kind-kaarten,
   label + `**Outcome:**`-comment, `outcome_required`-fout, conventie-doc bijgewerkt.
2. **`[chore]` `labels` op de MCP `update_card`/`create_card`** (geen deps) — dicht het
   gat uit §3: agents kunnen vandaag geen enkel label zetten. Zelfstandig nuttig en niet
   afhankelijk van #1 (de poort zet zijn eigen label intern).
3. **`[feature]` Prompt-contract: persona's leren de uitkomst-enum** (dep: #1) — de poort
   geeft een fout die de sessie moet kunnen voorkomen; persona + override-noot moeten de
   enum benoemen. Echte afhankelijkheid: de tekst beschrijft #1's enum.
4. **`[chore]` Sweeper: Done-analyses zonder uitkomst flaggen** (dep: #1) — vangnet voor
   het REST-gat (§5) en voor de historische voorraad. Echte afhankelijkheid: het script
   zoekt naar de marker die #1 schrijft.

## 8. Wat dit oplost — en wat niet

**Wel:** een analyse kan niet meer stil verdampen langs het MCP-pad. De drie uitkomsten uit
de opdracht worden het enige vocabulaire. "Niet wenselijk" wordt een zichtbare, eersteklas
uitkomst op het bord in plaats van proza in een §8. Het `decomposed`-pad is niet te
faken.

**Niet:** een vastberaden of REST-terugvallende agent kan de poort omzeilen (§5); daarvoor
is de sweeper het vangnet, geen garantie. En de poort beoordeelt geen *kwaliteit* — vier
slechte kind-kaarten passeren net zo goed als vier goede. Dat blijft mensenwerk, en hoort
dat te blijven.

---

## 9. Aanvulling 2026-08-01 — `filed_standalone` voor cadans-trigger-kaarten

**Aanleiding.** De cadans-trigger-kaart uit [`recurring-cadence-proposal.md`](./recurring-cadence-proposal.md) §4 (wekelijkse market-research-sweep, id `210e5042…` / `d2f3a10d…`; spiegel: po-digest `a6344a0d…`) heeft een analyse-flow die **standaard losgekoppeld** is van wat ze vindt: de bevindingen (Backlog-kaarten) moeten de trigger-kaart overleven, want volgende week start een nieuwe run die de bevindingen opnieuw moet kunnen zien — kind-kaarten met `parent_card_id = <trigger>` zouden die view vervuilen of, erger, bij elke parent-Done-move in `Awaiting Subtasks` parkeren tot alles klaar is (zie §6 + `analyse-levenscyclus-decision.md` §3).

De drie bestaande uitkomsten passen daar niet op:

- `decomposed` → claim geverifieerd tegen `parent_card_id == card.id` → **weigert** met `no_children` zodra de bevindingen standalone zijn gefiled (de bug die kaart `5770d3db…` fileide).
- `no_action_needed` → passeert de poort maar **ligt**: een wekelijkse run die wél degelijke Backlog-kaarten opleverde, labelen als "geen actie nodig" is een audittrail-leugen die `scripts/check-analysis-outcomes.sh` vervolgens meet als "verdampte analyse".
- `not_feasible` → betekent "niet doen" — semantisch onzin voor een run die al klaar is.

Twee afgewezen routes:

- **Parent-parking overslaan voor kaarten met `recurring`-label** (route #2 in de kaart). Lost het parent-parking-pad op maar niet het `no_children`-pad wanneer kinderen bewust standalone zijn gefiled — twee carve-outs voor één faalklasse is een teken dat de enum te smal is.
- **`decomposed` met een zachtere relatie dan `parent_card_id`** (bv. tag of metadata-link). Houdt hetzelfde verificatie-vlak als voorheen, maar breekt het principe "de eerlijke route is de makkelijke route" uit §5: een trigger-kaart kan nu beweren dat hij drie kaarten filete zonder dat de DB een FK-relatie afdwingt.

**Beslissing — vierde uitkomst `filed_standalone`.**

| `outcome` (nieuw) | Betekenis | Verificatie | Neerslag |
|---|---|---|---|
| `filed_standalone` | De trigger leverde ≥1 nieuwe of verrijkte Backlog-kaart op **zonder ouderschap** — typisch een cadans-trigger-run | `card.metadata.filed_card_ids: list[str]` is niet leeg en elke id verwijst naar een bestaande kaart in dezelfde `project_key` (sterke DB-check, géén FK-vereiste) | Geen extra label (het label-vocabulaire blijft bij de drie echte uitkomst-taxonomieën); `**Outcome:**`-comment zoals de andere drie |

`metadata.filed_card_ids` is geen nieuwe kolom — `KanbanCard.meta` is een vrije JSON-bag (`models.py` §2.2) en wordt al door `mcp_server.update_card` geschreven. De gedispatchte trigger-sessie zet dit veld tijdens Step 5/6 van `market-research`/`po-digest` (bijv. na elke `create_card`-call een append). De verificatie leest het metadata-veld, query'dt de kinderen op bestaan-in-zelfde-project, en weigert met `{"error": "no_filed_cards", ...}` bij een lege lijst of een onbekende id — dezelfde foutvorm als `no_children`, zelfde UX.

**Waarom dit veiliger is dan `no_action_needed`:** het label blijft eerlijk. Een wekelijkse trigger-run die 3 nieuwe kaarten filete staat nu met een `**Outcome:** filed_standalone — 3 new cards filed` op het bord — geen audit-trail-leugen meer, en `scripts/check-analysis-outcomes.sh` (zie §7 #4 + de update in §9-bis hieronder) telt 'm nu als *productief* in plaats van *verdampt*. De activiteit-feed `create_card`-events voor de gefilede ids zijn sowieso al in `kanban_ops` — het run-narratief klopt.

**Waarom geen parent-FK-relatie wordt afgedwongen:** zie `recurring-cadence-proposal.md` §4.3: het resultaat moet de trigger-kaart overleven, en kinderen met `parent_card_id = trigger` zouden ofwel (a) in `Awaiting Subtasks` parkeren of (b) bij zorgvuldige kind-Done-moves de trigger elke week opnieuw naar `Awaiting Subtasks` trekken — precies de twee bugs die deze enum-waarde bestaat te voorkomen.

**Scope.** De nieuwe waarde past uitsluitend op analyse-kaarten (de bestaande gating `is_analyst_leaf_spike(card)` blijft de trigger). Voor niet-analyse-kaarten blijft `outcome` genegeerd (backwards-compatible, §5). Implementatie hangt af van sessie-context: een niet-trigger analyse-kaart kan `filed_standalone` technisch claimen maar heeft typisch geen reden — dat is geen fout, alleen ruis; `check-analysis-outcomes.sh` blijft `filed_standalone` als geldig signal accepteren.

**Wijzigingen.**

1. `backend/app/kanban/mcp_server.py` `_OUTCOMES` += `"filed_standalone"`. Verificatie-tak leest `card.meta["filed_card_ids"]`, weigert met `no_filed_cards` bij lege lijst of niet-resolverende id.
2. `scripts/check-analysis-outcomes.sh`: voegt `**Outcome:**` matching op `filed_standalone` toe aan de `OUTCOME_LABELS`-set als vierde witness (naast `not-feasible`/`no-action-needed`).
3. `.claude/skills/market-research/SKILL.md` Step 5/6 + `.claude/skills/po-digest/SKILL.md` Step 4/5: korte regel die de trigger-flow instrueert `card.meta["filed_card_ids"]` bij te houden tijdens het filen.
4. Trigger-card `description` blijft ongewijzigd — de skill weet dit al, en een nieuwe gemeenschappelijke §7 in de recurrency-proposal linkt 'm in (`recurring-cadence-proposal.md` §4.1).

**Carve-outs (ter heropening):**

- Een trigger-flow die om een of andere reden toch kinderen met `parent_card_id = trigger` filete (oudere skill-versie, multi-card decompositie), krijgt op `outcome='decomposed'` de huidige parent-parking-blokkade. Dat is een **andere** route dan deze — heropenen zodra iemand die ook wil ondersteunen. Tot dan is `filed_standalone` de canonieke cadans-uitkomst.
- Een REST-caller (UI-drag, scripted PATCH) omzeilt de poort zoals altijd (§5); de sweeper blijft het vangnet. Geen nieuw oppervlak, geen nieuwe aanvals-vector.
