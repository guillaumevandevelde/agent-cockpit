# Beslissing — Leaf-spike maakt zijn eigen vervolgkaarten aan (autonomie i.p.v. review-round-trip)

**Datum:** 2026-07-14
**Status:** Beslissing / ontwerpvoorstel — implementatie belegd op kaart `75b54887…`
**Trigger:** review-kaart `f4093f05…` ("Review: Review: Analyse - Maximaal gebruik
abonnementen"). De gebruiker tekende twijfel aan op de vorige review-kaart (`ce4d2fe0…`):

> "Dit was geheel niet duidelijk. Indien een actie verwacht wordt om de kaarten aan te
> maken, ga dan naar impediment. Maar onthou de hoofdregel — autonomie — dit gaat
> daartegen in, bekijk hoe we dit proces dus autonomer kunnen maken. Indien niet zorg er
> dan voor dat op deze actie vereist is, verplaats niet gewoon naar done."

Verwant: [`subscription-flexibiliteit-analyse.md`](./subscription-flexibiliteit-analyse.md)
(de analyse waarvan de vervolgkaarten het concrete geval vormen), bestaande
`[self-improve]`-kaart `75b54887…` (de systemische framing van deze gap), en
`a9c27bee…` (het eerdere leaf-spike-override-fix waaruit de huidige override ontstond).

---

## 1. Wat de gebruiker eigenlijk vraagt

De twijfel leest op het eerste gezicht dubbelzinnig, maar valt uiteen in één scherpe
vraag plus een randvoorwaarde:

- **Vraag:** is er een *menselijke actie/beslissing* nodig om de vervolgkaarten uit een
  analyse-leaf-spike aan te maken? Zo ja → `report_impediment`. Zo nee → los het
  autonoom op en verplaats **niet** klakkeloos naar Done zonder dat de waarde geleverd is.
- **Randvoorwaarde (de hoofdregel):** **autonomie**. Een proces dat voor puur mechanisch
  vervolgwerk telkens een mens in de lus dwingt, gaat regelrecht in tegen de
  platform-doelstelling ("automatiseer zoveel mogelijk zonder de gebruiker uit de
  *beslissings*keten te verwijderen" — CLAUDE.md). De gebruiker wijst er expliciet op dat
  "ga naar impediment" hier de verkeerde reflex is als er geen echte beslissing speelt.

Kort samengevat: **niet impediment als default, maar de round-trip wegontwerpen.**

## 2. De feitelijke gap (grounded in de code)

Onderzocht: `backend/app/kanban/dispatch.py` (`_is_analyst_leaf_spike` r.597,
`_analyst_leaf_spike_override_note` r.628, de `leaf_spike`-tak in `build_card_prompt`
r.678–680), `backend/app/kanban/analyst_prompt.py`, en het feitelijke verloop van de
subscription-analyse.

Een **analyse-leaf-spike** is een kaart met `work_type='analysis'` (of `agent='analyst'`)
zónder multi-agent-decompositie-pijplijn (`analyst_agent_id` niet gezet). `build_card_prompt`
herkent dat geval en prependt de **leaf-spike-override**, die twee dingen doet:

1. **Relaxeert** het `Verboden: geen Write/Edit`-verbod uit de analyst-persona — want een
   leaf-spike schrijft juist een doc.
2. **Herkadert** de taak naar: *"produce a single deliverable (typically a decision doc in
   `docs/cockpit/`)… write the doc, commit, ship, attach the branch, move THIS card to Done."*

Precies dáár zit het gat: de override zegt **"één deliverable — een doc"** en zegt **niets
over het aanmaken van de aanbevolen vervolgkaarten.** De klassieke analyst-persona (waarop
de leaf-spike voortbouwt) maakt juist wél kind-kaarten aan via `create_card` +
`add_plan_attachment` — dat is zijn kernopdracht. De leaf-spike is de enige analyst-variant
die is teruggeknipt tot "alleen een doc", en dat knipt óók het aanmaken van de vervolgkaarten
weg. Het `create_card`-recht wordt niet expliciet verboden, maar ook niet verleend of
gevraagd — dus de agent laat de aanbevelingen als proza in een §8 staan en verplaatst naar
Done.

**Gevolg (waargenomen, tweemaal):** de aanbevelingen verdampen tot een mens ze opmerkt en
handmatig een review-kaart aanmaakt met "maak de vervolgkaarten alsnog aan". Dat is een
volledige extra dispatch-cyclus voor werk dat de leaf-spike al volledig had gespecificeerd.
De vorige review-kaart (`ce4d2fe0…`) déed dat correct — de vier fase-kaarten
(0/1a/1b/2) staan op Backlog en de conditionele spike is zelfs al uitgevoerd — maar de
*aanleiding* voor die sessie was menselijke waakzaamheid, niet automatisering. Dat is de
autonomie-schending die de gebruiker aanwijst.

## 3. Antwoord op de fork: is impediment het juiste default?

**Nee.** De kaart-creatie uit heldere, reeds-gespecificeerde aanbevelingen is **mechanisch,
geen menselijke beslissing.** De leaf-spike heeft de titels, acceptance-criteria en de
afhankelijkheids-DAG al bedacht (dat is exact wat §5/§8 van de subscription-analyse bevatten).
Een mens die op "maak ze aan" klikt voegt geen oordeel toe — hij is een handmatige trigger
voor een deterministische stap. Dáárvoor impediment gebruiken zou de autonomie-schending
juist *institutionaliseren*.

Impediment (met `options=[…]`) blijft correct voor het **enige** deel dat wél een oordeel
vergt: een echte, onopgeloste **product-fork** die verandert *wat* de kaarten zouden moeten
zijn. In de subscription-analyse was dat de §7-vraag (vendor-divers vs. same-vendor-multi-
account). De vorige sessie loste die pragmatisch op — best-effort beslist op vendor-divers
(wat de code al modelleert) en het alternatief bewaard als een conditionele spike — wat de
juiste, autonomie-eerst-behandeling is: beslis waar je verantwoord kunt, escaleer alleen de
echt-open knoop.

Daarom: **deze review-kaart gaat niet naar Impediment.** Ze wordt autonoom afgehandeld — dit
doc + een dispatch-klaar implementatie-kaart — en niet "gewoon naar Done" zonder waarde.

## 4. Ontwerpruimte — drie richtingen (uit kaart `75b54887…`)

| # | Richting | Oordeel |
|---|---|---|
| 1 | **Leaf-spike maakt zelf zijn vervolgkaarten aan** — relaxeer het `create_card`-verbod in de override (zoals Write/Edit al gerelaxeerd is) en instrueer de sessie de aanbevolen kaarten in dezelfde sessie aan te maken. | ⭐ **Gekozen.** Collabeert de round-trip volledig: de kaarten ontstaan in dezelfde sessie die ze bedacht. Nul extra dispatch, nul menselijke trigger. |
| 2 | **Dispatcher parseert §8 bij Done** en spawnt automatisch een decompositie-/review-kaart. | ✗ **Afgewezen.** §8 is vrij proza zonder schema — auto-parsen is broos (malformed/dubbele kaarten) en het *verplaatst* de round-trip alleen naar een tweede gedispatchte sessie i.p.v. hem weg te nemen. Meer machinerie voor een slechter resultaat. |
| 3 | **Conventie in de leaf-spike-persona** dat §8-kaarten in dezelfde sessie via `create_card`/`add_plan_attachment` gemaakt worden. | ⭐ **Gekozen, samen met #1.** #1 is de *toestemming* (verbod relaxen); #3 is de *instructie* (de agent moet het ook echt doen). Beide zijn nodig — toestemming zonder instructie laat het aan toeval over. |

## 5. Beslissing — gekozen mechanisme (#1 + #3)

Breid `_analyst_leaf_spike_override_note()` (dispatch.py r.628) uit zodat de override,
naast het relaxeren van Write/Edit, een expliciete vervolgkaart-clausule toevoegt. Contract
voor de leaf-spike-sessie:

1. **Toestemming:** het `create_card`/`add_plan_attachment`-verbod uit de analyst-persona is
   voor dit leaf-geval **gerelaxeerd** — net als Write/Edit al is.
2. **Instructie:** als de deliverable **concrete, scoped vervolgtaken op acceptance-criteria-
   niveau** aanbeveelt, maak die in **dezelfde sessie** aan als Backlog-kaarten (via
   `create_card`; via `add_plan_attachment` wanneer ze een afhankelijkheids-DAG vormen) —
   **vóór** je THIS-kaart naar Done verplaatst. De §-in-het-doc blijft de menselijk-leesbare
   verantwoording; de kaarten zijn de uitvoerbare neerslag.
3. **Impediment-escape (scoped):** reserveer `report_impediment(options=[…])` uitsluitend voor
   een echte onopgeloste **product-fork** die verandert *wat* de kaarten moeten zijn. Beslis
   verantwoorde forks best-effort (documenteer de aanname + bewaar het alternatief als
   conditionele kaart, zoals de §7-fork), en escaleer alleen de knoop die je niet verantwoord
   kunt hakken.

### Guard tegen Backlog-spam (verplicht onderdeel van de instructie)

De autonome kaart-creatie moet niet elke losse gedachte in een kaart gieten:

- **Alleen acceptance-criteria-niveau.** Een aanbeveling wordt een kaart alleen als de spike
  titel + 2–5 zinnen acceptance-criteria kan geven. Speculatieve/zachte ideeën blijven proza
  in het doc, geen kaart.
- **Dedup-pass eerst.** Vóór het aanmaken: `list_cards` op Backlog/Impediment en dedup tegen
  bestaande kaarten (de `flag-problem`-discipline). Bij een match: `comment` i.p.v. dupliceren.
- **DAG waar afhankelijkheden echt bestaan.** Pure sequentie zonder contract is geen
  `depends_on` (zelfde regel als de klassieke analyst).

### Waarom dit veilig is

- Het verbreedt de leaf-spike niet naar willekeurige acties — het herstelt precies één
  capaciteit (kaart-creatie) die de klassieke analyst al heeft en die de leaf-spike per
  ongeluk kwijtraakte.
- De mens blijft in de *beslissings*keten via de scoped impediment-escape voor echte forks;
  hij wordt alleen uit de *mechanische* keten gehaald.
- Reproduceerbaar/auditbaar: de kaarten dragen dezelfde plan-attachment-DAG en het doc
  bevat de verantwoording — identiek aan wat de review-round-trip nu handmatig oplevert,
  alleen zonder de mens als trigger.

## 6. Verdict op de gereviewede kaart (`ce4d2fe0…`)

- **Output: gegrond.** De vier fase-kaarten (`b4b4a663` fase 0, `710c85a5` fase 1a,
  `c7b05504` fase 1b, `5aaf3a82` fase 2) staan op Backlog met de correcte DAG, en de
  conditionele credential-isolatie-spike is al uitgevoerd (merge `8fff7e0`). De §7-fork is
  verantwoord best-effort beslist i.p.v. stilzwijgend aangenomen. Er is niets aan de
  *inhoud* te herstellen.
- **Proces: de door de gebruiker aangewezen tekortkoming is terecht.** Dat die kaarten een
  door-een-mens-getriggerde review-round-trip nodig hadden, is de autonomie-schending. De
  fix zit niet in deze kaart of de vorige, maar upstream in de leaf-spike-override/-prompt —
  belegd op kaart `75b54887…` (§7 hieronder).

## 7. Belegging — dispatch-klare implementatie-kaart

Conform de gekozen autonome afhandeling maakt deze sessie zélf de fix dispatch-klaar (het
dogfoodt precies het gedrag dat dit doc voorschrijft) i.p.v. het aan een volgende
review-round-trip te laten:

- De bestaande `[self-improve]`-kaart `75b54887…` (die deze gap framede met drie open
  richtingen, "niet urgent") is **aangescherpt** tot een besliste, uitvoerbare kaart:
  richting #1+#3 gekozen, acceptance-criteria toegevoegd, dit doc als bron. Geen dubbele
  kaart — de dedup-discipline wint.
- Zie de acceptance-criteria op die kaart voor het uitvoercontract; dit doc is de "waarom".
