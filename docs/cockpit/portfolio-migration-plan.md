---
title: "Portfolio-migratie: bestaande projecten bij de kind-introductie"
type: plan
status: active
---

# Portfolio-migratie: bestaande projecten bij de kind-introductie

> **Design-only.** Deze doc beschrijft **hoe** we bepalen welk bestaand
> project `meta` moet worden (i.p.v. de default `product`), en **hoe** we
> die keuze controleerbaar maken zonder auto-mutatie. Ze legt het **wat**
> en **waarom** vast, niet de **hoe** van nieuwe code — de executor van de
> uitvoerende kaart schrijft de classificatie-helper.
> Bron: `portfolio-orchestratie.md` §7 #8 (facet C).
>
> **Status van de kolom.** De `kind`-tag (`meta` | `product` | `archived`,
> default `product`) én de `priority`-kolom zijn **al gebouwd** (kaart #1 —
> `backend/app/models/database.py:30-33`, Pydantic-enum in
> `schemas.py:57`, PATCH-route `projects.py:134`). Beide zijn **inert**:
> geen dispatch- of security-policy leest ze vandaag. Dat verandert de
> aard van deze migratie fundamenteel (zie §1): de kolom bestaat al met
> `server_default="product"`, dus **elk bestaand project is nú al
> `product`**. Er is geen "lege kolom vullen"-moment meer; de migratie is
> een gerichte, mens-bevestigde *flip* van de handvol `meta`-projecten.

## 0. Samenvatting (de vier keuzes in één tabel)

| Vraag (acceptatiecriterium) | Gekozen antwoord | Kern-rationale |
|---|---|---|
| **1. Heuristiek** | Match `resolve_project_key(project.path)` tegen de **live** cockpit-key (`resolve_project_key(<cockpit-checkout>)`), niet tegen een hardgecodeerde string. Match ⇒ kandidaat-`meta`. Alles anders ⇒ blijft `product`. | De key is al de device-onafhankelijke identiteit; 'm live afleiden werkt ook in forks (§4). Hardcoden breekt zodra iemand forkt. |
| **2. Audit-trail** | Bij het draaien van de classificatie-pass: **geen auto-mutatie**. Log per kandidaat een `[portfolio-migration]`-comment-op op de activity-feed van dát project (op de oudste open kaart), met de afgeleide `kind` + het bewijs. Mens flipt daarna handmatig via de PATCH-route. | De op-log ís de activity-feed (`KanbanOp`); een comment is durabel + herbouwbaar. Read-only pass = geen verrassingen. |
| **3. Edge case fork** | Live-key-afleiding lost het meeste op. Voor de rest: één env-override `COCKPIT_META_PROJECT_KEYS` (comma-lijst van project_keys) die de heuristiek aanvult, niet vervangt. | Een fork heeft een *andere* remote; de live-afleiding ziet dat vanzelf. De override dekt exotische multi-meta / rename-scenario's. |
| **4. Bestaande kaarten** | Alleen kaarten op een **naar-`meta`-geflipte** project_key verdienen een audit. De pass produceert een read-only lijst; herclassificatie van individuele kaarten is een aparte, menselijke triage — geen onderdeel van deze migratie. | Kaart-`kind` bestaat niet; "kind" leeft op project-niveau. De enige actie is: weet welke boards nu meta zijn, zodat self-improve-triage klopt. |

Alle vier sluiten aan bij variant 2 ("kind-tag + portfolio-cap") uit
`portfolio-orchestratie.md` §4. Auto-reclassificatie en een
"herconfigureer alle projecten"-tool zijn expliciet **out-of-scope**
(zie §7 en de card-scope zelf).

---

## 1. De kern: twee identiteitssystemen, en waarom dat de migratie stuurt

Voordat de heuristiek zin heeft, moet de executor dit begrijpen — het is
de enige niet-triviale ontwerp-observatie en de reden dat een naïeve
"zet `kind=meta` waar de naam claude-cockpit is" fout gaat.

Cockpit heeft **twee** project-identiteiten die *niet* automatisch aan
elkaar gekoppeld zijn:

| Systeem | Sleutel | Waar | Draagt `kind`? |
|---|---|---|---|
| **Project-registratie** | `projects.path` (uniek) | `Project` (`backend/app/models/database.py:16-42`) | **Ja** — `kind`/`priority` staan hier. |
| **Kanban-board + dispatch** | `project_key` = `git:<host>/<path>` (of `slug:<naam>`) | `KanbanCard.project_key` (`kanban/models.py`), afgeleid via `resolve_project_key` (`kanban/project_key.py:38-45`) | **Nee** — het board weet niets van `kind`. |

De brug tussen beide is **`resolve_project_key(project.path)`**: een
`Project`-rij (path-keyed) resolveert naar exact de `project_key` die het
board gebruikt. Dat is de enige correcte join — niet de projectnaam (die
is niet uniek, niet device-stabiel) en niet het pad (dat verschilt per
machine).

**Gevolg voor de migratie.** De `kind`-flip gebeurt op de `Project`-rij
(path-keyed). Maar alles wat de tag *betekent* — portfolio-view, straks
security-policies, self-improve-triage — leeft op het board
(project_key). De migratie moet dus, per kandidaat, **de brug leggen**:
`Project.path → resolve_project_key → project_key`, en die key gebruiken
zowel voor het bewijs als voor de audit-comment.

Praktisch: een machine kan meerdere `Project`-rijen hebben die naar
dezelfde `project_key` resolveren (twee checkouts van dezelfde repo op
twee paden). Die horen **dezelfde** `kind` te krijgen. De classificatie
werkt daarom logisch **per project_key**, niet per pad — al schrijft de
flip uiteindelijk op elke bijbehorende `Project`-rij.

---

## 2. Criterium 1 — De heuristiek

### 2.1 Gekozen aanpak

**Live-key-vergelijking.** De classificatie-pass leidt de cockpit-eigen
key één keer af met dezelfde functie die het board gebruikt:

```
cockpit_key = resolve_project_key(<pad van de draaiende cockpit-checkout>)
# op deze repo vandaag: "git:github.com/guillaumevandevelde/agent-cockpit"
```

Dan, per bestaand project:

```
for project in projects:
    key = resolve_project_key(project.path)
    derived_kind = "meta" if key == cockpit_key else "product"   # default blijft product
```

- **Match** (`key == cockpit_key`) ⇒ kandidaat-`meta`.
- **Geen match** ⇒ blijft `product` (de default; geen actie nodig).
- **Aangevuld** met de env-override uit §4 (`COCKPIT_META_PROJECT_KEYS`):
  keys in die lijst zijn óók kandidaat-`meta`, ook zonder remote-match.

### 2.2 Rationale

1. **De key is al de juiste identiteit.** `resolve_project_key` is
   device-onafhankelijk en genormaliseerd (scheme/`.git`/`user@` gestript,
   `project_key.py:24-30`). Twee checkouts van dezelfde repo op
   verschillende paden geven dezelfde key — precies wat we willen: één
   logisch meta-project, niet twee.
2. **Live afleiden i.p.v. hardcoden.** De card-tekst noemt een
   hardgecodeerde string (`git:github.com/guillaumevandevelde/claude-cockpit`)
   als voorbeeld. Bewuste afwijking: we hardcoden 'm **niet**. Een fork
   heeft een andere remote, en dan classificeert een hardgecodeerde
   heuristiek de fork-eigen cockpit onterecht als `product` (§4). Door de
   key *live* af te leiden uit de draaiende checkout, is "meta = deze
   cockpit" waar in élke fork, zonder config.
3. **Default-safe.** Alles wat niet matcht blijft `product` — de
   bestaande `server_default`. De pass kan dus nooit een product-project
   per ongeluk naar meta tillen; het strengste geval is dat een fork
   handmatig één override moet zetten.

### 2.3 Alternatieven

| Alternatief | Waarom niet |
|---|---|
| **Hardgecodeerde remote-string** (letterlijk uit de card) | Breekt in elke fork (classificeert de fork-cockpit als product). Live-afleiding geeft hetzelfde resultaat op de origin én werkt in forks. |
| **Match op projectnaam** (`name == "claude-cockpit"`) | Naam is niet uniek en niet device-stabiel; een product-app die toevallig zo heet zou meta worden. |
| **Match op pad** (`path bevat "claude-cockpit"`) | Pad verschilt per machine en per worktree (`.claude/worktrees/...`); broos en niet-porteerbaar. |
| **Alleen een expliciete config-lijst, geen heuristiek** | Werkt, maar dwingt elke installatie tot handmatige config voor het meest voorkomende geval (één cockpit = meta). De heuristiek dekt dat gratis; de lijst is de *aanvulling* voor de rand. |

---

## 3. Criterium 2 — De audit-trail

### 3.1 Gekozen aanpak

**Read-only classificatie-pass + audit-comment; geen auto-flip.**

De pass (een eenmalig script of een idempotente admin-endpoint —
implementatie-keuze van de executor) doet **geen** schrijf naar
`projects.kind`. In plaats daarvan, per kandidaat-`meta` project_key:

1. Resolveer de bijbehorende `Project`-rij(en) en de `project_key`.
2. Post één `[portfolio-migration]`-**comment-op** op de activity-feed
   van dát project — concreet: op de oudste open kaart van die
   `project_key` (analoog aan hoe `stale_detection` de oudste
   Backlog-kaart kiest, `kanban/stale_detection.py`). De comment bevat:
   - de afgeleide `kind` (`meta`),
   - het bewijs (`key == cockpit_key`, of "via `COCKPIT_META_PROJECT_KEYS`"),
   - de huidige DB-waarde (`product`, want default),
   - een expliciete "mens beoordeelt; flip via `PATCH /projects/{id}`
     `{"kind":"meta"}`"-instructie.
3. Produceer daarnaast een samenvattende lijst (return-waarde /
   log-regel) van alle kandidaten, zodat de mens één overzicht heeft.

De comment gaat via de bestaande op-log (`apply_operation(..,
op_type="comment", ..)`, `kanban/service.py:279`) — durabel,
herbouwbaar bij rematerialize, en zichtbaar in de kaart-activity.

### 3.2 Rationale

1. **Geen auto-mutatie is een card-eis én veiliger.** De card zegt
   expliciet "Geen auto-mutatie; mens beoordeelt." Een verkeerde
   automatische flip (bv. een edge-case fork) zou stil security-relevante
   semantiek verschuiven zodra facet D de tag gaat lezen. Read-only +
   voorstel houdt de mens in de keten.
2. **De op-log ís de audit-trail.** Er is geen aparte audit-tabel nodig:
   `KanbanOp` is append-only en is de bron van waarheid + activity-feed
   (`kanban/models.py:2`). Een comment-op is precies het juiste, al
   bestaande durabele spoor — geen nieuw mechanisme.
3. **Idempotent herhaalbaar.** Draait de pass twee keer, dan mag ze niet
   twee identieke comments spammen. Aanbeveling voor de executor: sla de
   comment over als de laatste `[portfolio-migration]`-comment op die
   kaart dezelfde afgeleide `kind` al vermeldt (zelfde patroon als
   `stale_detection`'s dedup-venster).

### 3.3 Alternatieven

| Alternatief | Waarom niet |
|---|---|
| **Auto-flip `kind=meta` bij match** | Verboden door de card ("geen auto-mutatie"); riskant zodra de tag security-betekenis krijgt (facet D). |
| **Aparte `migration_audit`-tabel** | Nieuw schema + migratie voor iets dat de op-log al durabel doet. Overkill. |
| **Comment op élke kaart van het meta-project** | Ruis. Eén comment op de oudste open kaart is genoeg als signaal; de samenvattende lijst geeft het volledige overzicht. |
| **Alleen een log-regel, geen kaart-comment** | Log-regels verdwijnen; de card vraagt expliciet om een spoor "op het activity-feed van het meta-project". |

---

## 4. Criterium 3 — Edge cases (fork van claude-cockpit)

### 4.1 Het scenario

Iemand forkt claude-cockpit naar `github.com/alice/claude-cockpit` en
draait het als *haar* meta-platform. Haar remote is
`git:github.com/alice/claude-cockpit` — een **andere** key dan de origin.

### 4.2 Gekozen aanpak: live-afleiding + één env-override

- **Live-afleiding dekt de fork al.** Omdat §2 de cockpit-key *live*
  uit de draaiende checkout haalt (niet hardgecodeerd), is
  `cockpit_key` in Alice's installatie automatisch
  `git:github.com/alice/claude-cockpit`. Haar eigen cockpit matcht dus
  en wordt correct kandidaat-`meta` — zónder config. Dit is de reden dat
  hardcoden (§2.3) is afgewezen.
- **Env-override voor de rest:** `COCKPIT_META_PROJECT_KEYS`, een
  comma-gescheiden lijst van project_keys (pydantic-settings,
  case-insensitive, net als `PORTFOLIO_CAP_VALUE` in `config.py`). Keys
  hierin zijn óók kandidaat-`meta`. Dekt:
  - **Meerdere meta-projecten** (bv. cockpit + een privé meta-toolrepo).
  - **Rename/mirror**: een checkout waarvan de remote (nog) niet de
    verwachte key geeft.
  - **Pre-remote cockpit** (`slug:`-key omdat de origin ontbreekt) — dan
    zet je de `slug:`-key expliciet.

### 4.3 Rationale

1. **Override *aanvult*, niet *vervangt*.** De heuristiek blijft het
   default-geval (één cockpit = meta) gratis dekken; de env-lijst is puur
   additief voor de rand. Zo hoeft 99% van de installaties niets te
   configureren.
2. **Consistent met de bestaande config-conventie.** `portfolio-policy.md`
   koos bewust één env-as (env = per-device én per-fork tegelijk). Deze
   doc erft die keuze: `COCKPIT_META_PROJECT_KEYS` is dezelfde soort knop,
   geen nieuw schema of UI-oppervlak.
3. **Geen `is_meta_project`-vlag op de `Project`-rij.** De card noemt
   "`is_meta_project` override in config" als optie. We kiezen de
   env-lijst boven een per-rij-boolean omdat de rij al een `kind`-veld
   *heeft* — een tweede meta-boolean zou de bron van waarheid dubbelen.
   De env-lijst stuurt alleen de *heuristiek* (welke keys stelt de pass
   als meta voor); de definitieve staat blijft `projects.kind`.

### 4.4 Niet-ondersteunde randen (bewust)

- **Sub-directory-forks / monorepo-meta**: een cockpit die als subdir van
  een grotere repo draait heeft de key van de *buitenste* remote. Dat is
  een provisioning-vraag (facet B), niet iets dat deze migratie oplost —
  gebruik de env-override.
- **Key-migratie tijdens de flip**: als een project net van `slug:` naar
  `git:` migreert (`meta_migration.py`, facet B), draai de
  classificatie-pass *na* die key-migratie. De twee zijn los; deze doc
  gaat niet over key-renames.

---

## 5. Criterium 4 — Bestaande kaarten op meta-projecten

### 5.1 De vraag correct stellen

De card vraagt "Welke bestaande kaarten op `meta`-projecten verdienen een
audit of herclassificatie?". Belangrijke correctie op de premisse:
**kaarten hebben geen `kind`**. `kind` is een *project*-attribuut; er is
geen per-kaart-tag om te herclassificeren (`KanbanCard` draagt
`work_type`, niet `kind`). "Kaart-herclassificatie" bestaat dus niet als
mutatie — de enige zinvolle actie is *weten welke boards meta zijn*.

### 5.2 Wat wél een audit verdient

Zodra een project_key als `meta` is bevestigd, is de relevante
observatie voor bestaande kaarten:

1. **Self-improve-triage klopt nu.** `session-retro` levert
   `[self-improve]`-kaarten op het board waar de sessie draaide
   (`portfolio-orchestratie.md` §2.3 #6). Voor het meta-project hóórt dat
   ook — er is niets te herclassificeren; de flip maakt alleen *expliciet*
   wat impliciet al waar was ("dit board = meta-werk").
2. **Kaarten die een product-project raken maar op het meta-board staan.**
   Dít is de enige echte audit-categorie: een kaart die per ongeluk op
   het cockpit-board is aangemaakt terwijl ze over een product-app gaat
   (of omgekeerd). Die verhuizen is een *board*-verplaatsing (nieuwe kaart
   op de juiste `project_key`), geen `kind`-mutatie. De pass hoeft dit
   niet te detecteren — het is menselijke triage die de audit-comment
   (§3) alleen maar *aanmoedigt*.

### 5.3 Gekozen aanpak

De classificatie-pass levert, náást de kind-voorstellen, een **read-only
telling** per kandidaat-`meta` project_key: hoeveel open kaarten er per
kolom staan (herbruikt `stats.compute_core_stats` /
`service.list_cards`). Dat is genoeg context voor de mens om te
beoordelen of er kaarten verkeerd geplaatst zijn. **Geen** automatische
verplaatsing, geen bulk-mutatie — conform de card-out-of-scope ("dit is
een menselijke review-stap, niet een geautomatiseerde").

---

## 6. Blauwdruk — de classificatie-pass (contract, geen implementatie)

> **Geen implementatie.** Dit legt vast *welke* stappen de pass zet en
> wat haar output is, zodat de uitvoerende kaart een concreet startpunt
> heeft. De executor mag afwijken mits gemotiveerd.

```
def classify_projects(session, *, cockpit_checkout_path, extra_meta_keys) -> list[Candidate]:
    cockpit_key = resolve_project_key(cockpit_checkout_path)
    override    = set(extra_meta_keys)              # uit COCKPIT_META_PROJECT_KEYS
    candidates  = []
    for project in list_projects(session):
        key = resolve_project_key(project.path)
        derived = "meta" if (key == cockpit_key or key in override) else "product"
        if derived != project.kind:                 # alleen echte voorstellen
            candidates.append(Candidate(
                project_id=project.id, project_key=key,
                current_kind=project.kind, derived_kind=derived,
                evidence=("remote-match" if key == cockpit_key else "config-override"),
                open_card_stats=compute_core_stats(session, key),   # §5.3
            ))
    return candidates        # read-only; caller post audit-comments + toont lijst
```

**Eigenschappen van het contract:**

| Eigenschap | Waarde |
|---|---|
| **Schrijft naar `projects.kind`?** | Nee — read-only voorstel. |
| **Schrijft comment-ops?** | Ja, één per kandidaat op de oudste open kaart (§3), idempotent. |
| **Bron van de meta-set** | `cockpit_key` (live) ∪ `COCKPIT_META_PROJECT_KEYS`. |
| **Default voor niet-matchers** | `product` (ongewijzigd; geen actie). |
| **Herhaalbaar** | Ja — dedupt op laatste `[portfolio-migration]`-comment met zelfde `derived_kind`. |
| **Definitieve flip** | Handmatig, `PATCH /projects/{id}` `{"kind":"meta"}` door de mens. |

---

## 7. Out-of-scope (expliciet)

- **De schema-uitbreiding zelf** (`projects.kind` / `priority`) — kaart #1,
  al gemerged.
- **Security-policy op basis van de tag** (meta mag het platform wijzigen,
  product niet) — facet D (§7 #7 in de bron-doc). Deze doc raakt de tag,
  niet de betekenis.
- **Een tool dat automatisch alle projecten herconfigureert** — expliciet
  verboden door de card-scope; dit is een mens-bevestigde review-stap.
- **Automatische kaart-verplaatsing tussen boards** — §5: menselijke
  triage, niet geautomatiseerd.
- **Key-renames / slug→git-migratie** — `meta_migration.py`, facet B. De
  classificatie-pass draait ná een eventuele key-migratie.
- **Portfolio-view / portfolio-cap / stale-detectie** — kaarten #2/#3/#5,
  eigen scope. Deze doc levert alleen de correcte *classificatie* van
  bestaande projecten waarop die features leunen.
