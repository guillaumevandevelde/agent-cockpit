# Kaarten refereerbaar maken — analyse

> **Status:** ontwerp-aanbeveling (leaf-spike). Kaart-id `9eaa600d222a4ec78e11f195fcf86bdd`.
> Vervolgkaarten staan in §6; de beslissingen hieronder worden geratificeerd wanneer
> die kaarten landen, niet door dit document.

## 1. De vraag

> *"Ik vermoed in de DB dat kaarten vandaag al een unieke id hebben. Maak deze
> kopieerbaar vanop de kaart, zodat ik in andere kaarten naar een kaart kan verwijzen."*

De letterlijke vraag is één knop. De `zodat`-clausule is een grotere feature: een
verwijzing die je niet kán volgen is een halve verwijzing. Deze analyse behandelt
beide, en houdt ze uit elkaar zodat de knop alléén al kan landen.

## 2. Bevindingen

### 2.1 De aanname klopt — de id bestaat al

Elke kaart krijgt bij creatie een `uuid.uuid4().hex`: 32 hex-tekens, geen streepjes.
De id ontstaat in het op-log-pad, niet in een model-default:

- `backend/app/kanban/operations.py:91` — `entity_id = entity_id or uuid.uuid4().hex`
- `backend/app/kanban/operations.py:131` — `_materialize` vouwt de create-op tot
  `KanbanCard(id=entity_id, …)`
- `backend/app/kanban/models.py:48` — `id: Mapped[str] = mapped_column(String(64), primary_key=True)`

De `String(64)`-kolom is ruimer dan de 32 tekens die uuid4-hex oplevert; ga bij het
weergeven dus uit van de waarde, niet van een aangenomen lengte.

De id is stabiel, uniek, en is al de facto de referentie-token van het platform: de
MCP-tools eten hem rechtstreeks (`get_card(card_id=…)`, `create_card(depends_on=[…])`,
`add_plan_attachment(child_card_ids=[…])`), en de repo-prosa gebruikt hem al in
CLAUDE.md en in code-commentaar (`kanban card c23dfe46…`, `zie kaart
ed09173c14c248e0a7d4d413f7f2d945`). Er is dus niets te ontwerpen aan de id zelf.

### 2.2 De id is nergens uit de UI te halen

Dit is het echte gat. De id staat wél in de DOM van een bord-tegel
(`CardItem.tsx:188` — `data-card-id={card.id}`), maar is nergens zichtbaar of
selecteerbaar:

- De drawer-header toont enkel de titel (`CardDrawer.tsx:1029` —
  `<DialogTitle>{card.title}</DialogTitle>`).
- De bord-tegel toont titel + badges, geen id.

Vandaag is de enige manier om een kaart-id te bemachtigen: devtools openen, of een
MCP-/REST-call doen. Dat is precies waarom de vraag gesteld wordt.

### 2.3 Verwijzingen bestaan al — en ze lopen dood

Dit is de bevinding die de scope bepaalt. De drawer verwíjst op drie plaatsen al naar
andere kaarten via een afgekorte id, en op geen enkele daarvan kun je erheen:

| Plek | Vandaag | `CardDrawer.tsx` |
|---|---|---|
| **Parent plan** | Knop met `parentId.slice(0, 8)` — klik doet `getCard()` en toont dan een toast *"Open parent … in the board"*. Je moet 'm zelf gaan zoeken. | `:704–719` |
| **Depends on** | Badges met `depId.slice(0, 8)`. Geen klik-gedrag. | `:723–733` |
| **Beschrijving** | Vrije markdown; een id in de tekst is platte tekst. | `:1103` |

De "open de parent zelf maar even"-toast is geen bug die iemand vergat af te maken —
het is het gevolg van 2.4: er ís geen adres om heen te navigeren.

### 2.4 Een kaart heeft geen adres

`KanbanPage.tsx:37` — `const [open, setOpen] = useState<Card | null>(null)`. Welke
kaart open staat is pure component-state. Er is geen route, geen query-param, geen
hash. Gevolgen: je kunt geen URL naar een kaart delen, browser-back sluit de drawer
niet, en geen enkele component kan "open kaart X" vragen.

`react-router-dom@7.18` is al een dependency (`frontend/package.json:41`) en de
kanban-route bestaat (`App.tsx:81`), dus dit is een klein gat, geen architectuur-werk.

### 2.5 Kopiëren naar klembord: idioom bestaat, abstractie niet

Vier call-sites doen `navigator.clipboard.writeText` met de hand
(`mcp-server/MCPServerPage.tsx:123,163`, `presence/ConnectDialog.tsx:138`,
`agent-mail/InstallTab.tsx:58`). Het idioom is stabiel: `writeText` + een
`toast.success("… copied")`, soms een 2s `copied`-state. Er is geen gedeelde
`CopyButton`.

Een vijfde call-site rechtvaardigt nog geen abstractie — CLAUDE.md is expliciet
("drie vergelijkbare regels > premature abstractie") en de vier bestaande sites
verschillen onderling net genoeg (icon-only vs. label, toast vs. checkmark-state).
**Aanbeveling: kopieer het idioom, extraheer niets.** Als een zesde site opduikt is
dat het moment om de vraag te heropenen.

## 3. Drie lagen

De vraag valt uiteen in drie lagen die onafhankelijk waarde leveren:

1. **Kopiëren** — de id is uit de UI te halen. Lost 2.2 op. Beantwoordt de letterlijke vraag.
2. **Adresseren** — een kaart heeft een URL. Lost 2.4 op. Levert het contract dat laag 3 nodig heeft.
3. **Oplossen** — een verwijzing in markdown is klikbaar, en de dode knoppen uit 2.3 gaan leven.

Laag 1 en 2 zijn onafhankelijk van elkaar. Laag 3 consumeert beide.

## 4. Ontwerpbeslissingen

### D1 — Wat kopieert de knop? → **de ruwe, volledige id**

De kaartlezers zijn niet alleen mensen. Een agent die een gedispatchte kaart leest en
er een MCP-call op wil doen (`get_card`, `depends_on=[…]`, `child_card_ids=[…]`) heeft
de **kale 32-hex string** nodig. Dat is ook wat de repo-prosa al gebruikt (2.1). Eén
knop, één gedrag, geen parse-stap aan de leeskant.

*Overwogen en verworpen als default:* meteen een markdown-link
`[titel](/kanban?card=<id>)` kopiëren. Mooier voor de mens, maar het breekt het
agent-pad (uit een beschrijving een id plukken wordt dan markdown-parsen) én het
veronderstelt D2. Die variant komt terug als **tweede** actie in laag 3 — niet als
vervanging van de eerste.

*Afgekort of volledig?* Volledig. De chip mág `9eaa600d…` tonen (dat is de bestaande
huisstijl, zie 2.3), maar het klembord krijgt altijd de hele string: een afgekapte id
is niet resolveerbaar en 8 hex-tekens botsen met git-short-SHA's.

### D2 — Hoe krijgt een kaart een adres? → **`?card=<id>` query-param**

`useSearchParams` op `/kanban`: `?card=<id>` opent de drawer bij page-load, en
drawer-open/close synct de param. Levert in één klap een deelbare URL, werkende
browser-back, en het `openCardById`-contract voor laag 3.

*Aanname die de executor moet bevestigen:* het bord toont één project tegelijk
(`projectKey` state), dus een `?card=<id>` van een ánder project zit niet in `cards`.
Aanbevolen fallback: `kanbanApi.getCard(id)` — die is al project-agnostisch — en de
drawer alsnog openen. Blijkt dat in de praktijk verwarrend (een kaart van bord B open
zien staan boven bord A), dan is een expliciete "deze kaart hoort bij project X"-melding
het alternatief. Beide zijn goedkoop; kies bij de implementatie.

### D3 — Hoe wordt een verwijzing klikbaar? → **markdown-link, geen nieuwe syntax**

| Optie | Oordeel |
|---|---|
| **(a)** Kale 32-hex in tekst linkifyen | Vangnet, niet het contract. Linkt bestaande prosa retroactief en heeft weinig false-positive-risico (een git-SHA is 40 of ~7–12 hex; exact-32-hex is hier praktisch alleen een uuid4). Maar het vraagt een remark-plugin of een text-node-override, en het linkt júist de afgekorte vorm (`c23dfe46…`) níet — terwijl 8-hex linkifyen wél met git-short-SHA's botst. **Optioneel; alleen doen als kale-id-plakken in de praktijk het dominante gedrag blijkt.** Geen kaart. |
| **(b)** Eigen syntax `card:<id>` / `#<id>` | Verworpen. Ondubbelzinnig, maar vereist adoptie door mens én elke persona, en linkt geen enkele bestaande prosa. Nieuwe syntax is een migratie, geen feature. |
| **(c)** Gewone markdown-link `[titel](/kanban?card=<id>)` | **Aanbevolen.** Nul nieuwe syntax, werkt met de bestaande react-markdown-pipeline, en draagt de titel mee — een lezer ziet *waar* hij heen gaat i.p.v. een hex-blob. |

Voor (c) is exact één ding nodig: een `a`-component-override in `MarkdownRenderer` die
same-origin links via de router navigeert i.p.v. een full-page-reload. **Let op de
blast-radius:** `MarkdownRenderer` wordt door 11 surfaces gebruikt (plans, hooks,
skills, agent-mail, sessions, …). Interne links laten routeren is daar overal een
verbetering, maar het is wel een gedeelde component — de executor moet externe links
(`http(s)://`, `mailto:`) ongemoeid laten en die in een nieuw tabblad houden.

## 5. Risico's

- **Gedeelde component (D3).** De `MarkdownRenderer`-override raakt 11 surfaces. Mitigatie:
  alleen same-origin *paden* onderscheppen; al de rest ongewijzigd doorlaten.
- **Cross-project deep-link (D2).** Zie de aanname in D2 — een niet-afgevangen
  `?card=` van een ander project geeft een lege drawer of een stille no-op.
- **Geen backend-werk.** Alle drie de lagen zijn frontend. De id, `getCard`, en
  `parent_card_id`/`depends_on` bestaan al server-side. Er is geen schema-wijziging,
  dus ook geen db-delete (de repo heeft geen migraties).

## 6. Vervolgkaarten

Drie kaarten, één echte afhankelijkheid:

```
kaart 1 (copy id) ─┐
                   ├─→ kaart 3 (referentie oplossen)
kaart 2 (deep-link)┘
```

| # | Kaart | Kaart-id | Laag | Wacht op |
|---|---|---|---|---|
| 1 | Kaart-id-chip + "Copy id" in de drawer-header | `ce9c336b1b58408aa3773df7d2d4edee` | 1 | — |
| 2 | Kaart adresseerbaar via `?card=<id>` deep-link | `f90353890cac4574a12bf8761ba786f3` | 2 | — |
| 3 | "Copy reference" + klikbare kaart-links in markdown + dode knoppen fixen | `a866c3dfbab94f95bb0504e0a3647b19` | 3 | 1, 2 |

Kaart 1 beantwoordt de gestelde vraag en levert zelfstandig waarde: met de id op je
klembord kun je vandaag al in een andere kaart naar een kaart verwijzen — alleen nog
niet klikbaar. Kaart 2 en 3 maken de `zodat`-clausule af.
