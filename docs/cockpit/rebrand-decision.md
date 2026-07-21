---
title: "Rebrand naar Agent Cockpit — naam, logo en thema"
type: decision
status: decided
---

# Rebrand naar Agent Cockpit — naam, logo en thema

**Datum:** 2026-07-21
**Status:** besloten
**Kaart:** `ce0ea8d6…`
**Uitkomst:** **GO — nieuwe naam "Agent Cockpit", nieuw indigo-primair thema, nieuw vendor-neutraal logo.** De productnaam "Claude Cockpit" claimt een vendor die het product juist níet wil claimen: de orchestratie-kern (dispatch, worktrees, agent mail, dependency-DAG) is bewust agent- en vendor-onafhankelijk, en er draaien vandaag al vier providers. "Agent Cockpit" haalt de vendor uit de naam en **behoudt het interne identifier `cockpit`**, dat load-bearing is in `docs/cockpit/`, `scripts/cockpit.sh`, `scripts/cockpit-doctor.sh` en de `cockpit-kanban` MCP-server — de rename krimpt daarmee tot "laat het `claude`-voorvoegsel vallen" in plaats van een volledige identifier-sweep. Het thema verruilt Anthropic-oranje (`15 63%`, letterlijk `#D97757` in het logo) voor een eigen indigo (`258 62% 48%` licht / `258 70% 68%` donker) dat in lichte modus **beter** contrasteert dan het huidige oranje (7,86:1 vs 4,76:1) en in donkere modus ruim boven AA blijft (5,43:1). 4 vervolgkaarten. Bewust **buiten scope**: de twee `localStorage`-sleutels, `~/.claude-registry/` en `claude_registry.db` — die dragen bestaande gebruikersstaat, en hernoemen kost een migratie of een stille reset voor nul zichtbaar effect.

> **Type:** beslisdoc (analyst leaf-spike). Bron-kaart: *"Rebrand"*
> (`ce0ea8d678de49ad9785306a430ceb58`).
>
> Verwant: [`00-orientation.md`](./00-orientation.md) (missie: vendor-agnostische kern),
> [`kanban-conventions.md`](./kanban-conventions.md).

---

## 1. Waarom

De kaart formuleert het probleem scherp: *"Applicatie naam spreekt momenteel de
doelstelling van de applicatie tegen. Aangezien we product agnostisch willen zijn."*

Dat is niet louter cosmetisch. De missietekst in
[`00-orientation.md`](./00-orientation.md) stelt dat de orchestratie-kern
"agent- en repo-onafhankelijk ontworpen" is, en dat klopt in de code: de
dispatcher spawnt vandaag vier providers, en het dispatch-besluit zelf bevat
nul model-calls. Een naam die één vendor noemt is daarmee een actief onjuiste
belofte aan iedere lezer van de repo — en aan iedere niet-Anthropic-provider
die we later willen ondersteunen.

Hetzelfde geldt voor de visuele identiteit. Het huidige logo is niet
"geïnspireerd op" Anthropic — het gebruikt letterlijk `fill="#D97757"`, de
Anthropic-merkoranje, en het thema draagt in `index.css` de commentaarregels
`/* Primary - Claude orange */`. Dat is een overname, geen eigen stijl.

## 2. Naam: **Agent Cockpit**

Slug/pakketnaam: `agent-cockpit`. Weergavenaam: `Agent Cockpit`.

De kaart noemde "agent center of agent cockpit — kies maar". Gekozen:
**Agent Cockpit**, om drie redenen.

1. **Vendor-neutraal.** Het doel van de kaart. "Agent" beschrijft wat het
   product beheert; geen enkele leverancier zit in de naam.
2. **Het interne identifier `cockpit` blijft geldig.** Dit is het
   doorslaggevende praktische argument. `cockpit` is al overal load-bearing:
   `docs/cockpit/`, `scripts/cockpit.sh`, `scripts/cockpit-doctor.sh`,
   `logs/`, en de `cockpit-kanban` MCP-server die in gebruikers-`.mcp.json`
   staat en door `test_claude_mcp_isolation.py` als acceptatiecriterium wordt
   bewaakt. Met "Agent Cockpit" hoeft géén van die identifiers te wijzigen —
   de rename is "laat `claude-` vallen". Met "Agent Center" zouden ze
   allemaal misleidend worden en zou de sweep vertienvoudigen.
3. **De metafoor klopt met het product.** Een cockpit is een
   *bedieningsoppervlak met instrumenten over iets dat zelf vliegt* — precies
   wat dit is: een board, een dispatcher en een set panelen over autonoom
   draaiende agents. "Center" is vager en botst bovendien met de
   admin-console-naamgeving van meerdere leveranciers.

### 2.1 Wat níet hernoemd wordt

Bewust buiten scope, met reden:

| Identifier | Waarom niet |
|---|---|
| `localStorage` `claude-cockpit-api-token` (`lib/api.ts:3`) | Hernoemen logt elke bestaande gebruiker uit. Onzichtbare sleutel, nul merkwaarde. |
| `localStorage` `claude-cockpit:selected-provider` (`ProviderContext.tsx:16`) | Idem — reset stil de providerkeuze. |
| `~/.claude-registry/` (backups, kanban-DB, attachments) | Draagt echte data. Hernoemen = migratiescript schrijven voor nul zichtbaar effect. |
| `claude_registry.db` | Idem. |
| `cockpit-kanban` MCP-server | Bevat het vendorwoord al níet. Blijft ongewijzigd. |
| `orange-*` klassen in `instanceAccent.ts` | **Geen merkkleur.** Dit is een door de gebruiker kiesbaar per-instance accent (default `blue`); oranje is één optie van zeven. Een blinde `orange-`-sweep sloopt deze feature. |

> **Let op voor de uitvoerder:** de laatste rij is de belangrijkste val. De
> ~20 overige `orange-`-treffers in `frontend/src` (plugins, backup, memory,
> APM) zijn stuk voor stuk *statuskleuren*, geen merkkleur. Vervang alleen
> `--primary` / `--accent` / `--ring` / `--chart-1` in `index.css` plus het
> logo; laat losse `orange-`-utility-klassen met rust.

### 2.2 Eén brekende hernoeming: de management-MCP-server

`backend/app/mcp_server/server.py:7` registreert de server als
`"claude-cockpit"`. Dat is de enige hernoeming die een gebruikersconfiguratie
breekt: wie de server in zijn eigen `.mcp.json` heeft staan, moet die regel
bijwerken.

Besloten: **wel hernoemen** naar `agent-cockpit`. Deze naam is de meest
vendor-gebrande identifier die er is, het is een zelf-gehoste tool met een
zeer kleine installed base, en het alternatief is de vendornaam permanent in
het meest zichtbare integratiepunt laten staan. Voorwaarde: de vervolgkaart
noteert het als **breaking change** in `CHANGELOG.md` met de
migratie-instructie in één regel.

Niet te verwarren met `cockpit-kanban` — dat is een aparte server, die
ongewijzigd blijft.

## 3. Thema en kleur

### 3.1 Keuze van de tint

Eisen: geen Anthropic-oranje, wél leesbaar in licht én donker, en
onderscheidbaar van de bestaande statuskleuren. Die laatste eis sluit de
voor de hand liggende kandidaten uit: het bestaande palet gebruikt al
`success 142` (groen), `warning 45` (geel), `info 200` (blauw) en
`destructive 0` (rood). Een teal- of blauwprimair (~180-200°) zou visueel
samenvallen met `info`, waardoor een informatiebadge niet meer van een
merkelement te onderscheiden is.

**Gekozen: indigo, tint 258°.** Maximaal gescheiden van alle vier de
statustinten, duidelijk niet-oranje, en het leest technisch/neutraal op de
bestaande bijna-zwarte donkere achtergrond.

### 3.2 Gemeten contrast

Berekend met de WCAG 2.1 relatieve-luminantieformule (sRGB, exacte
gammacurve), niet geschat:

| | `--primary-foreground` op primary | primary als tekst op achtergrond |
|---|---|---|
| Licht — huidig oranje `15 63% 46%` | 4,76:1 | 4,66:1 |
| **Licht — nieuw indigo `258 62% 48%`** | **7,86:1** | **7,69:1** |
| Donker — huidig oranje `15 63% 60%` | 6,22:1 | 6,22:1 |
| **Donker — nieuw indigo `258 70% 68%`** | **5,43:1** | **5,43:1** |

Eerlijk over de trade-off: **lichte modus verbetert fors** (4,66 → 7,69,
van net-AA naar AAA), **donkere modus gaat licht achteruit** (6,22 → 5,43)
maar blijft ruim boven de AA-drempel van 4,5:1. Die achteruitgang is
geaccepteerd; hem wegpoetsen zou een lichtere indigo vragen die op wit
onbruikbaar wordt.

**Reproductie:** het script staat in §6.

### 3.3 Concrete waarden

Te vervangen in `frontend/src/index.css`. Alleen deze regels; al het
overige (achtergronden, muted, borders, status) blijft ongewijzigd.

```css
/* :root — lichte modus */
--primary:  258 62% 48%;   /* was 15 63% 46% */
--accent:   258 62% 48%;   /* was 15 63% 46% */
--ring:     258 62% 48%;   /* was 15 63% 46% */
--chart-1:  258 62% 52%;   /* was 15 63% 50% */
--chart-2:  160 45% 38%;   /* was 180 50% 40% — schuift weg van chart-4 */
--chart-5:  330 55% 50%;   /* was   0 55% 45% — schuift weg van destructive */

/* .dark — donkere modus */
--primary:  258 70% 68%;   /* was 15 63% 60% */
--accent:   258 70% 68%;   /* was 15 63% 60% */
--ring:     258 70% 68%;   /* was 15 63% 60% */
--chart-1:  258 70% 68%;   /* was 15 63% 60% */
--chart-2:  160 50% 52%;   /* was 180 50% 50% */
--chart-5:  330 65% 62%;   /* was   0 60% 55% */
```

Vervang tegelijk de commentaarregels `/* Primary - Claude orange ... */` en
`/* Accent - Claude orange */` door `/* Primary - Cockpit indigo ... */`.

`--chart-3` (`45 70% 45%`) en `--chart-4` (`200 50% 45%`) blijven zoals ze
zijn. Genoteerd, buiten scope: `--chart-3` haalt in lichte modus 2,56:1 en
zakt daarmee onder de 3:1 die WCAG voor grafische objecten vraagt. Dat is
een **bestaand** probleem, ongewijzigd door deze rebrand — geen regressie,
maar wel een eigen kaart waard als iemand ooit de grafieken oppakt.

### 3.4 Gevolg voor `CLICKABLE_CARD`

`CLAUDE.md` beschrijft onder *UI Conventions* het hover-effect van
`CLICKABLE_CARD` als een "orange border hover effect". De klasse zelf
(`border-primary/50` in `lib/constants.ts:3`) is tokengebaseerd en hoeft
**niet** te wijzigen — hij volgt `--primary` automatisch. Alleen de
beschrijvende tekst in `CLAUDE.md` moet van "orange" naar "indigo".

## 4. Logo

Het huidige `frontend/public/claude-cockpit-logo.svg` heeft twee problemen:
`fill="#D97757"` (Anthropic-merkoranje, hard gecodeerd) en `<title>Claude
Cockpit</title>`.

**Nieuw merk: een instrumentbezel met horizon en stijgindicator** — de
abstractie van een cockpit-attitude-indicator. Neutraal, vendorloos, en het
sluit aan op de naam zonder illustratief te worden.

Belangrijk ontwerpbesluit: het merk gebruikt **`currentColor`** in plaats
van een vaste hexwaarde. Daarmee erft het logo de themakleur en kan het
nooit meer uit de pas lopen met het palet — precies de fout die het huidige
logo maakt.

Bestandsnaam: `frontend/public/agent-cockpit-logo.svg`.

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="1em" height="1em"
     fill="none" stroke="currentColor" stroke-width="1.8"
     stroke-linecap="round" stroke-linejoin="round"
     style="flex:none;line-height:1">
  <title>Agent Cockpit</title>
  <rect x="2.5" y="2.5" width="19" height="19" rx="5"/>
  <path d="M6 14.5h12"/>
  <path d="M8.5 11.5 12 7.5l3.5 4"/>
</svg>
```

In `Header.tsx` staat het logo als `<img src="...">`; een `img` erft
`currentColor` niet. De vervolgkaart moet het merk daarom **inline als
component** zetten (`frontend/src/components/shared/CockpitLogo.tsx`, zelfde
patroon als het bestaande `GithubIcon.tsx`) zodat `text-primary` doorwerkt.
Voor `<link rel="icon">` in `index.html` blijft een bestand nodig; geef dat
een vaste `stroke="#5B3FD4"` (de lichte-modus-indigo als hex), want een
favicon heeft geen erfbare kleurcontext.

> **Niet visueel geverifieerd.** Op deze machine is geen SVG-renderer
> beschikbaar (`rsvg-convert`, `inkscape`, `cairosvg`, Playwright ontbreken
> alle). De markup is well-formed, maar hoe het merk er op 16px, 24px en
> 64px daadwerkelijk uitziet is **niet gecontroleerd**. De vervolgkaart moet
> dat expliciet doen — zie de acceptatiecriteria — en mag de geometrie
> bijstellen als het merk op faviconformaat dichtslibt.

## 5. Omvang van de sweep

Gemeten met `grep -rEn 'Claude Cockpit|claude-cockpit|claude_cockpit'`:

| Oppervlak | Treffers |
|---|---|
| `frontend/src` (`.ts`/`.tsx`) | 24 |
| `frontend/index.html` + logobestand | 2 bestanden |
| `backend/app` | 47 |
| `backend/tests` | 26 |
| `scripts/` | 23 |
| `.claude/` (skills, agents, settings) | 27 |
| `docs/cockpit/` | 67 |
| `README.md` / `CONTRIBUTING.md` / `SECURITY.md` / `CLAUDE.md` / `CHANGELOG.md` | 19 / 4 / 4 / 3 / 1 |

Ruim 240 treffers — te veel voor één sessie, en de oppervlakken hebben
verschillende risico's (frontend heeft een lint/build-gate, backend heeft
tests die op de MCP-servernaam asserten, `.claude/` raakt de
dispatch-prompt). Vandaar vier vervolgkaarten in §7, gesneden langs
gate-grenzen in plaats van langs bestandstype.

## 6. Reproductie van de contrastmeting

```python
def hsl2rgb(h, s, l):
    s /= 100; l /= 100
    c = (1 - abs(2*l - 1)) * s
    x = c * (1 - abs((h/60) % 2 - 1)); m = l - c/2
    r, g, b = [(c,x,0),(x,c,0),(0,c,x),(0,x,c),(x,0,c),(c,0,x)][int(h//60) % 6]
    return [v + m for v in (r, g, b)]

def lum(rgb):
    f = lambda v: v/12.92 if v <= 0.03928 else ((v + 0.055)/1.055)**2.4
    r, g, b = [f(v) for v in rgb]
    return 0.2126*r + 0.7152*g + 0.0722*b

def cr(a, b):
    la, lb = lum(hsl2rgb(*a)), lum(hsl2rgb(*b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)

WHITE, NEARBLACK, BG_LIGHT = (0,0,100), (0,0,6), (0,0,99)
print(cr((258,62,48), WHITE))      # 7.86 — licht: wit op primary
print(cr((258,62,48), BG_LIGHT))   # 7.69 — licht: primary als tekst
print(cr((258,70,68), NEARBLACK))  # 5.43 — donker: bijna-zwart op primary
```

## 7. Vervolgkaarten

Vier kaarten, gesneden langs gate-grenzen. Géén onderlinge
`depends_on`-contracten: elke kaart raakt een eigen bestandsset, en de
gedeelde afspraak (naam, hexwaarden, logo-markup) staat in dít document —
niet in de output van een zusterkaart. Ze kunnen dus parallel.

1. **Frontend-merkoppervlak** — naamstrings, `index.html`, logo als
   `CockpitLogo.tsx` + favicon, en het indigo-palet in `index.css`.
   Eén kaart omdat alles hier dezelfde lint/build-gate betaalt.
2. **Backend + MCP-serveridentiteit** — `server.py` hernoemen naar
   `agent-cockpit` (breaking, met `CHANGELOG.md`-regel), overige
   `backend/app`- en `backend/tests`-strings.
3. **Repo-root-documentatie** — `README.md`, `CONTRIBUTING.md`,
   `SECURITY.md`, `CLAUDE.md` (incl. de "orange border"-zin uit §3.4).
4. **Agent-facing oppervlak** — `.claude/` skills en persona's, `scripts/`,
   `docs/cockpit/`, met aandacht voor de `dispatch.py`-spiegel van
   `git-ship/SKILL.md`.
