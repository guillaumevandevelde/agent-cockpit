---
title: "Analyse — <Product>: wat kunnen we overnemen of leren?"
type: analysis
status: active
---

<!--
Sjabloon voor de `product-analysis`-skill. Kopieer naar
docs/cockpit/<product>-analyse.md en vul in. Verwijder alle <…>-placeholders
én deze comment.

Frontmatter:
  leer-analyse  -> type: analysis  + status: active
  go/no-go      -> type: decision  + status: decided  (dan ook de vier-veld-
                   header hieronder invullen én een rij in decisions.md)

De vier-veld-header (Datum/Status/Kaart/Uitkomst) is ALLEEN verplicht voor
type: decision — `scripts/check-decision-register.sh --check-headers` valideert
'm, en `**Uitkomst:**` moet woordelijk de eerste zin van je register-rij zijn.
Voor type: analysis volstaat het Datum/Status/Trigger/Bron-blok.
-->

# Analyse — <Product>: wat kunnen we overnemen of leren?

**Datum:** <YYYY-MM-DD>
**Status:** Analyse / beslisdocument (read-only spike; geen implementatie in deze kaart)
**Trigger:** kanban-kaart `<id>…` "<kaarttitel>". Gebruiker:
> "<premisse van de gebruiker, woordelijk>"

**Bron:** <https://github.com/OWNER/REPO> (<licentie>, <branch> @ `<sha>`, gemeten <YYYY-MM-DD>)

---

## TL;DR

<Vijf tot tien regels. Verplicht in deze volgorde:>
- **Premisse getoetst:** <klopt / klopt deels / klopt niet — en waarom, in één zin>
- **Wat we overnemen:** <2–4 items, gerangschikt op leverage>
- **Wat we bewust niet overnemen:** <1–3 items, met de reden in één zin>
- **Vervolg:** <N vervolgkaarten / geen kaarten, en waarom>

## 1. Wat <Product> feitelijk is (gegronde feiten, staat <YYYY-MM-DD>)

<Wat het is, op welke laag het werkt, waar het vandaan komt. Gemeten
maturiteitscijfers horen hier: repo-leeftijd, laatste release, open vs.
gesloten issues, open PR-backlog, taal/typing, testdekking. Elk cijfer met de
meetdatum. Geen vendorclaims als feit.>

## 2. De premisse getoetst

<De zin uit de kaart, en wat de feiten ermee doen. Als de premisse een
categoriefout is (zij zitten op een andere laag dan wij), is dát de kern van
de analyse — schrijf 'm hier uit vóór er één feature vergeleken wordt.>

## 3. Waar wij staan (met verwijzingen)

<Wat Cockpit vandaag heeft op ditzelfde terrein — elke claim met een
`file:line` of `docs/cockpit/<x>.md §N`. Ook: waar wij verder zijn en dat zo
moeten houden. Check `decisions.md` op eerder beslechte forks die hier
langslopen.>

## 4. Wat we concreet kunnen overnemen (gerangschikt op leverage)

### 4.1 ⭐ <Belangrijkste overneembare ding>

<Wat het is bij hen · welke laag/bestand het hier raakt · wat het de product
owner oplevert · wat het kost. Kost-/besparingsclaims: gemeten getal +
reproductiecommando, of expliciet "ongemeten schatting".>

### 4.2 ⭐ <Tweede>

### 4.3 <Derde>

### 4.4 Kleinere leerpunten (noteren, niet nu bouwen)

<Bullets. Dingen die de moeite van het onthouden waard zijn maar geen kaart
verdienen.>

## 5. Wat we bewust NIET overnemen

<Per afgewezen idee één alinea: wat het is, en waarom het hier niet past
(laagverschil, onderhoudslast, credential-oppervlak, eerder beslecht in
`decisions.md`, strijdig met een kernprincipe uit `00-orientation.md`).
Deze sectie is wat voorkomt dat hetzelfde idee over drie maanden opnieuw
wordt voorgesteld — sla 'm niet over.>

## 6. Aanbeveling

<Eén expliciete aanbeveling met een richting: doen / niet doen / smal en
conditioneel doen. Bij "conditioneel": noem de voorwaarden als toetsbare
zinnen.>

## 7. Vervolgkaarten (in deze sessie aangemaakt)

<Elke kaart met id, titel en één zin. Kaarten zijn kinderen van de
analyse-kaart en zijn via `add_plan_attachment` van een `plan_ref` voorzien —
óók als ze onderling onafhankelijk zijn. Zijn er geen kaarten, zeg dan hier
waarom niet (en gebruik `outcome="no_action_needed"` / `"not_feasible"`).>

- `<id>…` — <titel> — <één zin>

## 8. Bewust buiten scope

<Wat deze spike expliciet niet onderzocht heeft, zodat een lezer weet waar de
analyse ophoudt.>

## 9. Heropenen wanneer?

<Alleen bij een go/no-go. Concrete, waarneembare triggers die de beslissing
opnieuw op tafel leggen — geen "als de situatie verandert".>

## 10. Bronnen

<URL's met meetdatum; de commit-sha waar de feiten uit komen; de interne docs
en kaarten waar deze analyse op leunt.>
