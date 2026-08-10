---
name: po-digest
description: Use when producing the recurring weekly product-owner digest from the deterministic collector output, including the canonical week file and board notification. The next run is fired by the cron recurring trigger, not by this skill.
---

# PO-digest

Maak één wekelijkse pagina die de product owner in vijf minuten laat zien wat is opgeleverd, wat is besloten, wat op hen wacht en of de koers is verschoven. Het collector-JSON is de bron; jouw taak is redactie op producthoogte, geen nieuwe data-extractie.

## Bron van deze run

Deze skill wordt aangeroepen door de server-side trigger `id=2` in `recurring_triggers` (cron `0 8 * * 1`, timezone `Europe/Brussels`). De eerdere LLM-keten die de digest handmatig doorgaf is vervangen; deze sessie hoeft geen opvolger-kaart te filen. De volgende run wordt door dezelfde trigger aangemaakt. Een dubbele aanroep van dezelfde cron-beurt (inhaal op boot, twee APScheduler-ticks, handmatige replay) wordt hieronder in **Stap 2** expliciet afgewezen om een bestaande weekpagina niet leeg te schrijven.

## Step 1 — bepaal project en venster

1. Resolve de echte project-key met `resolve_project_key`; gok hem nooit.
2. Bepaal `until` als het huidige UTC-tijdstip.
3. Laat `scripts/po-digest-source.py` het begin bepalen uit `until:` van het nieuwste weekbestand. Als er nog geen weekbestand is, gebruikt het script `until − 7 dagen`.
4. Draai vanaf de repo-root:

```bash
python3 scripts/po-digest-source.py \
  --project-key "<resolved-project-key>" \
  --until "<UTC-ISO-tijdstip>" > /tmp/po-digest-source.json
```

Controleer dat `window`, `shipped`, `decisions`, `waiting` en `course_changes` aanwezig zijn. Staat een sectie in `errors`, benoem dan de bronstoring in die sectie; presenteer een lege fallback nooit als complete werkelijkheid.

## Step 2 — botsingscontrole en schrijven

Voordat je het weekbestand opent, controleer of het al bestaat en of het de huidige cron-beurt al dekt. Een bestaande weekpagina is canoniek; een dubbele run mag die niet overschrijven.

1. Bepaal `target_file = docs/cockpit/po-digest/YYYY-Www.md` met `YYYY-Www` de ISO-week van `until`.
2. Lees `docs/cockpit/po-digest/README.md` en tel het aantal keer dat `YYYY-Www` voorkomt in een indexregel; hetzelfde bestand kan niet twee keer geïndexeerd staan.
3. Bestaat `target_file`?
   - **Ja — lees de frontmatter.** Als de bestaande `since`/`until` dezelfde ISO-week dekken als `window.since`/`window.until` van deze run, dan is dit een dubbele aanroep van dezelfde cron-beurt (boot-inhaal, retry, handmatige replay). **Schrijf het bestand niet over.** Post een korte comment op de host-kaart (het eigen kaart-id staat in de dispatch-prompt; trigger-context staat in `metadata.trigger_id`/`metadata.occurrence`) met:
     > `Botsing: docs/cockpit/po-digest/<YYYY-Www>.md dekt deze beurt al (since=<…>, until=<…>). Trigger id=2 heeft last_fired_at=<…>; sessie stopt zonder schrijven.`
     Verplaats de kaart naar `Done` met `outcome='no_action_needed'` (botsing, niets geschreven) en dezelfde botsing als `summary`. Wrijf de indexregel of de commit niet aan.
   - **Ja — andere week.** Dan zit er een gat in de reeks. Noteer dat in de kaart-comment maar ga door met schrijven: deze run is een inhaal van een eerder overgeslagen week.
   - **Nee.** Ga door met schrijven zoals hieronder beschreven.

Schrijf `docs/cockpit/po-digest/YYYY-Www.md`, waarbij `YYYY-Www` de ISO-week van `until` is. Gebruik frontmatter met `since:` en `until:`; de collector gebruikt `until` als grens voor de volgende run.

```markdown
---
since: "<window.since>"
until: "<window.until>"
---

# Product-owner-digest — YYYY-Www

## Wat is er opgeleverd?
## Welke richtingsbeslissingen zijn genomen?
## Wat wacht op jou?
## Is er iets van koers veranderd?
```

### Wat is er opgeleverd?

- Cluster `shipped` op productthema; som geen kaarten op.
- Schrijf **≤ 7 thema-bullets**. Vermeld per thema hoeveel onderliggende kaarten erin vallen.
- Sluit bij meer bronitems af met `Daarnaast zijn N opleveringen niet afzonderlijk uitgeschreven.`
- Gebruik per bullet een aparte `Refs:`-regel met kaartverwijzingen. Kaart-ids, bestandsnamen en endpoints horen niet in de lopende zin.

### Welke richtingsbeslissingen zijn genomen?

- Neem **alle** unieke items uit `decisions` op, **één regel per beslissing**.
- Vertaal de registerrij naar de productbetekenis; kopieer niet blind de tabelsyntax.
- Zet de bronverwijzing apart. Reversal-rijen horen bij koerswijzigingen, niet dubbel in deze sectie.

### Wat wacht op jou?

- Sorteer `waiting` oudste eerst en schrijf **≤ 5 items**.
- Noem bij elk item de leeftijd, bijvoorbeeld `wacht 6 dagen` of `wacht 4 uur`.
- Sluit bij meer items af met `Daarnaast toont de wachtrij nog N signalen.`
- Formuleer dit als een momentopname: schrijf niet dat dit exact alles is wat openstaat. Als `errors.waiting` bestaat, zeg dat de actuele wachtrij niet kon worden opgehaald.

### Is er iets van koers veranderd?

- Selecteer uit `course_changes` alleen wijzigingen die de productrichting raken: herzieningen, `not_feasible`, `no_action_needed` en betekenisvolle heropeningen.
- Schrijf **≤ 3 punten**; technische rework is geen koerswijziging.
- Zijn er geen echte koerswijzigingen, schrijf dat expliciet in plaats van de sectie op te vullen.

## Step 3 — hanteer het taalregister

Elke bullet voldoet aan alle regels:

1. Leid met wat de product owner nu kan of moet.
2. Schrijf Nederlands, in actieve tweede persoon.
3. Cluster boven opsommen; onderliggende titels blijven uit de lopende tekst.
4. Gebruik maximaal twee zinnen per punt, bij voorkeur één.
5. Zet kaart-ids, bestandsnamen en endpoints alleen in een aparte `Refs:`-regel.
6. Benoem een lege week of lege sectie; verzin geen vulling.
7. Verpak geen aanbeveling als feit; draag claims met een meting of ref.

## Step 4 — valideer caps en archief

Controleer vóór commit:

- opgeleverd: maximaal 7 thema-bullets plus telling van de rest;
- beslissingen: alle collector-items, één regel elk;
- wachtrij: maximaal 5 items, elk met leeftijd, plus telling van de rest;
- koerswijzigingen: maximaal 3;
- elke sectie bestaat, ook wanneer ze leeg is;
- `since` en `until` zijn exact uit collector-output overgenomen.

Voeg daarna bovenaan de lijst in `docs/cockpit/po-digest/README.md` één indexregel toe, nieuwste eerst:

```markdown
- [YYYY-Www](./YYYY-Www.md) — <korte weekduiding>
```

Commit en push het weekbestand en de indexregel. Het weekbestand is canoniek; een kaartcomment alleen is onvoldoende omdat kaarten verwijderd kunnen worden.

## Step 5 — gebruik de Done-summary als notificatie

Verplaats de host-trigger pas naar Done nadat commit en push geslaagd zijn. Kies `outcome` op basis van wat de run opleverde:

- `outcome='filed_standalone'` als de run ≥1 standalone Backlog-kaarten filede — zet hun ids in `metadata.filed_card_ids` vóór de move.
- `outcome='no_action_needed'` als de run niets filede — het weekbestand zelf is dan het enige deliverable.

De `summary` bevat vier regels (één per sectie) en daarna het canonieke pad:

1. één korte regel over opgeleverd;
2. één korte regel over beslissingen;
3. één korte regel over wat op jou wacht;
4. één korte regel over koerswijzigingen;
5. het pad `docs/cockpit/po-digest/YYYY-Www.md`.

Dupliceer niet het hele digest-blok in de summary.

## Quick reference

```text
resolve project → collect JSON → redact four capped sections
→ write week file
→ prepend README index
→ commit + push
→ Done-summary: four lines + canonical path
```

Eerst botsingscontrole (Stap 2): als `docs/cockpit/po-digest/YYYY-Www.md` de huidige week al dekt, stop en post een korte comment; **niet overschrijven**.
