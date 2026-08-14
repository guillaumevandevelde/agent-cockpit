---
title: "Verbruik per abonnement: elk van de vier heeft een echte noemer"
type: decision
status: decided
---

# Verbruik per abonnement — meten in plaats van schatten

**Datum:** 2026-08-14
**Status:** besloten
**Kaart:** _geen kaart — directe PO-opdracht_
**Uitkomst:** ✅ Alle vier de abonnementen rapporteren tegen een echte limiet. De
Subscriptions-pagina toont een meter per venster.

## 1. Antwoord in het kort

De aanname "er is geen eerlijke noemer" was waar voor Anthropic-API's en is
doorgetrokken naar alle abonnementen. Dat was onterecht. Elk van de vier
abonnementen publiceert een limiet, alleen via vier verschillende kanalen.

Daarmee vervalt de conclusie uit
[`subscription-verbruik-inzicht-analyse.md`](./subscription-verbruik-inzicht-analyse.md)
§2.4/§6.1. Die stelde dat alleen een absoluut tokenaantal eerlijk was. Dat klopte
op het moment van schrijven en klopt nu niet meer.

## 2. Wat er per abonnement gemeten is

Alles hieronder is op 2026-08-14 tegen de echte accounts gemeten, niet uit
documentatie overgenomen.

| Abonnement | Vensters | Kanaal | Kwaliteit |
|---|---|---|---|
| Claude Pro | 5u + 7d | statusline `rate_limits` | exact |
| MiniMax plus | 5u + week | `GET /v1/token_plan/remains` | exact |
| ChatGPT Go | **alleen 30d** | codex rollout `token_count` | exact |
| opencode Go | 5u + week + maand | lokale kosten ÷ caps | schatting |

Drie van de vier vergen geen enkele hook. Alleen Claude Pro heeft een wrapper om
de statusline nodig, omdat `rate_limits` nergens op schijf staat en geen
hook-payload het meestuurt.

### 2.1 Vier verrassingen die de code sturen

**MiniMax rapporteert wat er óver is, niet wat verbruikt is.** 56% remaining is
44% verbruikt. De provider keert dat om aan de rand. Wie dit mist, stuurt de
router precies de verkeerde kant op.

**ChatGPT Go heeft geen 5u-venster.** De Codex-documentatie beschrijft 5u + week
voor Plus en Pro. Het gemeten Go-account gaf één venster van 43.200 minuten met
`secondary: null`. Daarom komt het label uit `window_minutes` en niet uit de
positie in de payload.

**opencode Go rekent in dollars.** De limieten zijn $12 per 5u, $30 per week en
$60 per maand. Het aantal requests hangt dus af van het model. De lokale
`opencode.db` bevat de kosten per bericht; de som klopt tot op de cent met wat
`opencode stats` toont.

**Anthropic publiceert de cijfers wél, alleen via de statusline.** Claude Code
geeft `rate_limits` mee aan het statusline-commando. Niet op schijf, niet in een
hook, geen API. De wrapper in `scripts/statusline-capture.sh` tapt dat af.

## 3. Waarom de vensters een lijst werden

De vier abonnementen hebben 2, 2, 1 en 3 vensters, in drie verschillende
eenheden. Eén `drempel_gebruikt` kan dat niet dragen. MiniMax stond bij de meting
op 0% van zijn 5u en 44% van zijn week — één getal noemt geen van beide.

`SubscriptionUsage.windows` bewaart alle vensters. `drempel_gebruikt` blijft
bestaan als het **slechtste** venster, want dat is de enige keuze die geen kaart
op een uitgeputte lane kan zetten. De router leest dus hetzelfde veld als
voorheen.

## 4. Wat er weg is

Drie rijen zijn verwijderd: `bedrock`, `copilot-cli` en de router-rij
`anthropic-compatible`. Geen van drieën hoort bij een abonnement dat iemand
heeft. Ze konden alleen "geen signaal" tonen en begroeven zo de rijen met een
echt getal — zes van de zeven rijen waren ruis.

Een lege rij voor iets dat je niet bezit is geen eerlijkheid maar rommel.

## 5. Wat dit voor de pool betekent

`subscription_pool._is_above_threshold` leest `drempel_gebruikt`. Dat veld was
altijd `None`, dus de pool heeft nog nooit een lane overgeslagen op verbruik. Nu
gaat dat wel gebeuren.

Dat is het doel: een sessie die halverwege een kaart op een rate-limit stukloopt
laat de kaart geclaimd achter, met een verweesde worktree. Vooraf uitwijken
voorkomt die klasse incidenten. Het betekent ook dat de `drempel`-waarden van
pool-entries voor het eerst meetellen — controleer ze.

## 6. Beperkingen

**opencode-vensters rollen mee.** De billing-anker is nergens lokaal vastgelegd,
dus meten we de laatste 5u, 7d en 30d. Dat overschat ten opzichte van een vast
venster, wat naar "te vroeg pauzeren" leunt in plaats van naar "op een
uitgeputte lane routeren". `resets_at` is daarom leeg.

**Verbruik boven 100% is echt.** opencode Go's "Use balance" laat uitgaven
doorlopen op Zen-krediet in plaats van te blokkeren. De UI klemt niet af.

**Twee spellingen voor het Anthropic-percentage.** De CC-binary bevat zowel
`utilization` als `used_percentage`. De reader accepteert beide, want gokken en
misgokken ziet er hetzelfde uit als "geen data".

De eerste echte capture (CC 2.1.232, 2026-08-14) wees `used_percentage` aan. Dat
is *niet* de variant die de CLI's eigen formatter leest, dus een gok op
`utilization` had permanent en stil gefaald. Beide blijven ondersteund.

**Stille degradatie is het echte risico.** Hernoemt Claude Code een veld, dan
valt de rij terug op de oude schatting terwijl de pagina gezond oogt. Daarom
logt de reader een waarschuwing zodra een capture wel bestaat maar niets
oplevert.

## 7. Zie ook

- [`subscription-verbruik-inzicht-analyse.md`](./subscription-verbruik-inzicht-analyse.md) — de analyse die dit besluit herziet
- [`subscriptions.md`](./subscriptions.md) — de "geen fabricage"-norm
- [`cache-read-quota-decision.md`](./cache-read-quota-decision.md) — waarom `cache_read` niet meetelt
