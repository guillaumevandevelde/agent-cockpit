---
title: "Taalgebruik — schrijfconventie voor alle tekst in dit project"
type: reference
status: active
---

# Taalgebruik — schrijfconventie voor alle tekst in dit project

> **In één regel:** de eerste alinea moet begrijpelijk zijn zonder voorkennis,
> en alle diepte staat achter een verwijzing die zegt wát daar te vinden is.

Deze conventie geldt voor élke tekst die een mens leest: documenten in
`docs/cockpit/`, `CLAUDE.md`, de persona's in `.claude/agents/`, de skills in
`.claude/skills/`, en de tekst op het kanbanbord (kaarttitels, beschrijvingen,
Done-samenvattingen, impediment-vragen).

## 1. Het probleem dat deze conventie oplost

De product owner meldde op 2026-08-04 dat uitleg in dit project te vaak
onleesbaar is. De meting bevestigt dat, en wijst één hoofdoorzaak aan: niet de
gemiddelde zin, maar de staart.

Gemeten met `scripts/check-doc-readability.py` op 2026-08-04, vóór deze
conventie:

| Oppervlak | Documenten | Hits | Lange zinnen | Lange alinea's | Hybride werkwoorden |
|---|---|---|---|---|---|
| `docs/cockpit/*.md` | 94 van 112 | 308 | 268 | 13 | 27 |
| `CLAUDE.md` | 1 van 1 | 16 | 9 | 2 | 5 |
| `.claude/agents/*.md` | 3 van 4 | 21 | 14 | 2 | 5 |
| `.claude/skills/*/SKILL.md` | 8 van 14 | 28 | 25 | 1 | 2 |

De gemiddelde zin is 19 woorden — dat is prima. De leesindex per document ligt
meestal tussen 45 en 65, ook prima. Het probleem zit in de uitschieters: 268
zinnen van meer dan 40 woorden. Eén zo'n zin midden in een uitleg maakt de hele
alinea onbruikbaar, en een gemiddelde verbergt dat.

Reproduceer de meting met het commando in [§7](#7-meten).

✅ Geïmplementeerd (kaart `85db6366…`) — `CLAUDE.md` staat op 0 hits. De diepte
van de `git stash`-gotcha verhuisde naar
[`git-stash-safety.md`](./git-stash-safety.md), die van de `pkill`-gotcha
eerder al naar [`pkill-safety.md`](./pkill-safety.md).

## 2. De vier meetbare normen

| Norm | Waarde | Gemeten door |
|---|---|---|
| Zinslengte | maximaal 40 woorden, streef naar 20 | `check-doc-readability.py` |
| Alinealengte | maximaal 150 woorden per alinea of per bullet | `check-doc-readability.py` |
| Hybride werkwoorden | nul, zie [§5](#5-woordkeuze-welk-engels-blijft) | `check-doc-readability.py` |
| Leesindex (Flesch-Douma) | informatief, onder 30 is een waarschuwing | `check-doc-readability.py` |

Een hit is geen fout in de code maar een leesbaarheidsschuld. Het script is
daarom adviserend: het meldt de schuld en geeft exitcode 0. Met `--strict`
wordt het een gate, voor wie een schoon oppervlak wil vastzetten.

## 3. Conclusie eerst, diepte via verwijzing

Dit is de belangrijkste regel, en de directe reactie op de klacht. Elke uitleg
heeft vier lagen, in deze volgorde:

1. **Kop** — zegt waar het over gaat, in gewone woorden.
2. **Eerste zin** — de conclusie, maximaal 20 woorden, zonder voorbehoud.
3. **Twee tot drie alinea's** — waarom het zo is, en wanneer het bijt.
4. **Verwijzing** — al het diepere werk staat elders, achter een link.

Wat níet mag: het mechanisme uitleggen vóór de conclusie. Wie de conclusie pas
in de laatste zin vindt, heeft de hele alinea al twee keer moeten lezen.

### Een verwijzing zegt wát er staat

Een link is een belofte over de inhoud. `Zie foo.md` is geen belofte, want de
lezer weet niet of het antwoord daar staat.

```text
FOUT:  De dispatcher houdt de kaart vast. Zie multi-agent-kanban.md.
GOED:  De dispatcher houdt de kaart vast tot alle afhankelijkheden klaar zijn.
       Welke toestanden er zijn en wie ze zet: multi-agent-kanban.md §4.
```

### Diepte hoort in een eigen document

Wordt de uitleg langer dan drie alinea's, dan is het een eigen document of een
eigen paragraaf met een anker. De hoofdtekst houdt de conclusie en de link.
Zo blijft het instaptekst voor wie oriënteert, en naslag voor wie het detail
nodig heeft.

## 4. Verwijzen zonder de lezer te blokkeren

Twee soorten verwijzing komen in dit project veel voor. Beide hebben een vaste
vorm.

**Kaart-id's zijn een bewijsstuk, geen uitleg.** Een id als `5e83b6e0…` kan de
lezer niet opzoeken zonder het bord erbij. Zet de reden dus in woorden, en het
id erachter tussen haakjes.

```text
FOUT:  Verwijder deze regel niet (kaart 7dd8a3dd…).
GOED:  Zonder de afsluitende `--` leest git een bestand met de naam HEAD als
       revisie, en dan faalt elke ship met een misleidende foutmelding
       (kaart 7dd8a3dd…).
```

Een kaart-id staat nooit in een kop en nooit als enige onderbouwing.

**Claims over onze eigen code krijgen een `file:line`-anker.** Beweer je dat de
cockpit iets doet of juist niet doet, geef dan het pad erbij. Dan kost
natrekken één stap in plaats van een leespas.

**Beslisdocs (`docs/cockpit/*-decision.md`) ook onder deze regel.** Een
beslisdoc zonder ankers is alleen te weerleggen met een verse leespas;
de keep-beslissing op Agent Mail (kaart `30d45e5f…`) bleek drie dagen
besloten op een `kanban_ops`-coupling die niet in de code stond. Een
tabel met tabelnamen was geen anker (de geleerde les staat in
`cc-native-cross-session-decision.md`, op de keep-rij van 2026-08-10).

Een anker is `backend/app/<pad>:NN` of `frontend/src/<pad>:NN` —
`backend/tests/<pad>:NN` is even geldig als canonieke cite-target.
Kale `:NN`-verwijzingen, paden onder `docs/`, en `worker.py:42`
midden in een codeblock tellen niet. `scripts/check-decision-doc-anchors.sh`
is de meetkant van deze regel, advisory met `--strict` als gate.

Nieuwe beslisdocs dragen vanaf nu een anker; bestaande docs wachten op
backfill en verschijnen als WARNING tot ze dat doen.

## 5. Woordkeuze: welk Engels blijft

Engels vakjargon blijft, want het is de naam van het ding. Engelse werkwoorden
met een Nederlandse vervoeging gaan eruit, want daar bestaat een gewoon
Nederlands woord voor.

**Blijft** — dit zijn de namen van onze eigen begrippen: dispatch, claim,
worktree, branch, merge, ship, spawn, deliverable, impediment, gate, kanban,
backlog. De vijf kernbegrippen (agent, provider, CLI, model, run) staan met hun
definitie in [`terminology.md`](./terminology.md).

**Gaat eruit** — een greep uit de lijst die het script kent:

| Niet dit | Maar dit |
|---|---|
| het patroon globt | het patroon matcht als glob-patroon |
| het script flag't | het script signaleert / markeert |
| de waarde overridet | de waarde overschrijft |
| de sweeper sweept | de sweeper loopt langs |
| de test pint vast | de test zet vast |
| het harnas zandbakst het | het harnas isoleert het |

De volledige lijst met vervangingen staat in `HYBRID_VERBS` in
`scripts/check-doc-readability.py`. Ontbreekt er een geval, voeg het daar toe;
de lijst en deze conventie horen bij elkaar.

**Introduceer een nieuw begrip één keer, met zijn definitie.** Gebruik je een
term die niet in `terminology.md` staat, leg hem dan bij eerste gebruik uit in
één bijzin. Daarna mag hij kaal.

## 6. Tekst op het bord

Het bord is het oppervlak dat de product owner elke dag leest. Vier plekken,
vier regels.

- **Kaarttitel** — één regel gewone taal, geen code-identifier. Niet
  `held_reason='scheduled'` maar "een geplande kaart lijkt klaar om te starten".
- **Kaartbeschrijving** — begint met wat er misgaat of moet gebeuren, in
  productwoorden. Bestandspaden en regelnummers volgen daarna.
- **Done-samenvatting** — eerste zin is het producteffect: wat kan de product
  owner nu zien, doen of beslissen. Engineering-detail komt in een aparte
  alinea, niet in dezelfde zin. Dit is de bestaande afspraak uit
  [`kanban-conventions.md` §5](./kanban-conventions.md#5-product-taal-voor-done-summaries-en-impediment-options);
  de norm uit [§2](#2-de-vier-meetbare-normen) geldt er bovenop.
- **Impediment-vraag en -opties** — de vraag benoemt de keuze in gevolgen. De
  opties zijn producttrade-offs, geen implementatiekeuzes.

## 7. Meten

```bash
scripts/check-doc-readability.py                                  # docs/cockpit
scripts/check-doc-readability.py --top 20                         # slechtste 20
scripts/check-doc-readability.py --file CLAUDE.md                  # per regel
scripts/check-doc-readability.py --path .claude/agents --path CLAUDE.md
scripts/check-doc-readability.py --path .claude/skills --recursive
scripts/check-doc-readability.py --strict                          # als gate
bash scripts/test_check_doc_readability.sh                         # harnas
```

Het script meet geen code: fenced en inline code, frontmatter en tabelrijen
blijven buiten de meting. Een lijst van korte bullets is geen lange alinea;
alleen één bullet met meer dan 150 woorden is dat wel.

## 8. Wat deze conventie niet is

- **Geen taalpurisme.** Vakjargon dat de naam van een ding is, blijft staan.
  Zie [§5](#5-woordkeuze-welk-engels-blijft).
- **Geen verbod op detail.** Detail mag, maar niet in de instaptekst. Zie
  [§3](#3-conclusie-eerst-diepte-via-verwijzing).
- **Geen automatische herschrijver.** Het script meet; herschrijven blijft
  mensen- en agentwerk. Bij een herschrijving mag geen inhoud verdwijnen: elk
  weggehaald detail verhuist naar een document waarnaar de tekst linkt.
- **Geen prosa-kwaliteit-gate.**
  [`communicatie-en-weergave-analyse.md`](./communicatie-en-weergave-analyse.md)
  §2.3 wees een blokkerende "muur tekst"-detector af, en stond één ding wél toe:
  een *advisory* check zodra de drift zich echt voordoet. Dat is precies wat
  hier staat. Het script beoordeelt geen stijl of kwaliteit; het meet drie
  structurele feiten (zinslengte, alinealengte, een vaste woordenlijst) en
  blokkeert niets. De drift is gemeten en gemeld door de product owner op
  2026-08-04, dus de voorwaarde uit §2.3 is vervuld.
- **Geen norm voor Engelse brontekst.** Sommige documenten en de
  ship-instructies zijn deels Engels. Vertaal die niet halverwege; een gemengde
  alinea leest slechter dan een consequent Engelse. Wordt een document
  herschreven, dan gaat het in één keer volledig naar het Nederlands.
