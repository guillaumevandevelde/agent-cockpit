---
title: "`risk_class`-taxonomie + classifier voor `ProjectSecurityPolicy`"
type: reference
status: superseded
---

> **Superseded op 2026-08-13.** De feature die dit document beschrijft is uit
> Agent Cockpit verwijderd tijdens de opruiming naar de kern. Wat er precies
> weg is, waarom, en welke gedragsverandering dat opleverde staat in
> [`kern-terugbrengen-plan.md`](./kern-terugbrengen-plan.md). Dit document
> blijft staan als beslisspoor; behandel de inhoud niet als huidige toestand.

# `risk_class`-taxonomie + classifier voor `ProjectSecurityPolicy`

> **Design-only.** Deze doc werkt de risico-taxonomie en de classifier uit die
> `ProjectSecurityProfile` (facet D, follow-up #6) nodig heeft om een
> `risk_class`-default te kiezen. Ze beschrijft **wat** de vier levels betekenen,
> **welke signalen** een project classificeren en **wanneer** een mens moet
> bevestigen — niet het **hoe** van nieuwe code.
>
> **Bron-kaart:** `[design][D] risk_class-taxonomie + classifier voor
> ProjectSecurityPolicy` — follow-up #12 uit
> `docs/cockpit/veilig-bouwen-en-uitleveren.md` §6.
>
> **Lees dit ook:** `veilig-bouwen-en-uitleveren.md` (facet D, de parent — §4.3
> definieert `ProjectSecurityPolicy`, follow-up #6 de storage); §3-tabel koppelt
> `risk_class` aan `default_transport`. `portfolio-orchestratie.md` (facet C — de
> bestaande `projects.kind`-tag die deze taxonomie verfijnt).
> `repo-provisioning-bootstrap.md` (facet B — waar een nieuw project geboren wordt
> en de eerste classificatie plaatsvindt: de `??` in `set_skip_permissions(session,
> new_key, ??)`, §3.1).

## 0. Samenvatting (de vier levels in één tabel)

| `risk_class` | Threat-model in één zin | Standaard-policy | Voorbeeld |
|---|---|---|---|
| **`meta`** | Trusted human-in-the-loop mag het platform zélf verbouwen. | `worktree`, `skip_permissions=true`, globale env toegestaan, geen container. | `claude-cockpit` zelf (de repo die Cockpit draait). |
| **`product-staging`** | Onze eigen nieuwe app, nog geen echte gebruikers/secrets; fouten mogen, maar niet de host raken. | `sandcastle` (docker), `skip_permissions=false`, project-scoped env, resource-caps. | Splinternieuw product-project net na `RepoBootstrapService`-geboorte. |
| **`product-prod`** | Onze app met echte gebruikers, echte secrets, een echte deploy-target; blast-radius = productie. | `sandcastle` (docker), `skip_permissions=false`, prod-secrets-scope, **human-gated deploy**, strengste audit. | Een volwassen product-project met een gebonden `DeployTarget` en prod-secrets. |
| **`untrusted`** | Code van onbekende herkomst of een audit-run; ga ervan uit dat de code vijandig is. | `sandcastle + network=none`, read-only-rootfs, alleen een `SAFE_*`-env-set, geen host-mount. | Een externe PR/dependency-audit; een geïmporteerde third-party repo. |

**Kernprincipe (de rode draad van heel deze doc):** *signalen mogen vertrouwen
autonoom **verlagen**, nooit autonoom **verhogen**.* Een splinternieuw of
onbekend project defaultt conservatief (`product-staging`); doorgroeien naar een
laxere stance (`meta`) of naar `product-prod` vereist een **menselijke
bevestiging**. `untrusted` mag wél automatisch worden opgelegd (fail-safe). Zie
§4.4 en §5.

---

## 1. De vraag + de scope

Facet D wil dat `ProjectSecurityProfile` bij de geboorte van een project een
veilige `risk_class`-default kiest, en dat die klasse de rest van het
security-model bepaalt (`default_transport`, `default_skip_permissions`,
`secrets_scope`, `network_policy` — zie `veilig-bouwen-en-uitleveren.md` §4.3).
Vandaag ontbreekt daarvoor **de taxonomie** (wat *is* elk level precies?) én **de
classifier** (welke signalen kiezen het level, en hoe betrouwbaar zijn die?).

Deze doc levert beide. **Buiten scope** (blijft bij follow-up #6 / de facet-D-parent):

- De `ProjectSecurityProfile`-tabel, REST-CRUD en de `risk_class`-opslag zelf.
- De koppeling `risk_class → default_transport / skip_permissions` in de dispatch
  (dat contract staat al in de facet-D-parent §3 + §4.3).
- De transport-hardening (Sandcastle resource-caps, `network=none`) — follow-ups #2/#3.
- Portfolio-brede policy-sync (facet C).

Wat deze doc *wél* vastlegt: de definities, het signalen-model met betrouwbaarheid,
de default-keuze + wanneer-vraag-je-een-mens, de transition-triggers en de
edge-cases — plus één data-model-implicatie (baseline vs. transient override, §7)
die follow-up #6 moet meenemen.

---

## 2. De vier levels — scherpe definities

Elk level = *(threat-model, standaard-policy, voorbeelden)*. De policy-kolommen
verwijzen naar de knoppen uit `ProjectSecurityPolicy` (facet D §4.3); hier staat
alleen **welke waarde** elk level default krijgt en **waarom**.

### 2.1 `meta` — het platform zelf

- **Threat-model:** de agent verbouwt Cockpit op zijn eigen repo, met een mens die
  direct meekijkt (Bridge/kanban). "Aanvaller" is hier feitelijk *een bug in onze
  eigen prompt/plan*, niet vijandige code. De blast-radius (de host, `~/.claude`,
  alle andere projecten via het bestandssysteem) is bewust geaccepteerd omdat de
  meta-flow die toegang *nodig* heeft — de agent moet de dispatcher, de DB, de
  scripts kunnen aanraken.
- **Standaard-policy:** `worktree`-transport, `skip_permissions=true`, globale
  proces-env toegestaan, geen container. Dit is de huidige de-facto default van de
  meta-repo (`dispatch.py` `skip_permissions` default `True`).
- **Voorbeeld:** `claude-cockpit` — de enige repo die vandaag `meta` is.
- **Toewijzings-regel (kritiek):** `meta` is **path-verankerd**, niet
  content-verankerd. Alleen de checkout waaruit Cockpit zélf draait (het pad dat
  het backend-proces als zijn eigen repo-root kent) is `meta`. Geen in-repo bestand
  (CLAUDE.md, een `.cockpit-risk-class`) en geen signaal-heuristiek kan een project
  naar `meta` tillen — zie §4.4 en de fork-edge-case §6.1.

### 2.2 `product-staging` — onze nieuwe app, nog niet live

- **Threat-model:** onze eigen, door Cockpit gebouwde app die nog géén echte
  gebruikers, geen prod-secrets en geen deploy heeft. De code is *waarschijnlijk*
  goedbedoeld (wij lieten 'm bouwen), maar onbewezen; een agent-misstap mag het
  project verpesten, maar niet de host of een ander project raken. Dit is de
  **conservatieve default** voor alles wat nieuw en van-ons is.
- **Standaard-policy:** `sandcastle` (docker als beschikbaar, anders `no-sandbox`
  met expliciete UI-warning — facet D §5.2), `skip_permissions=false`,
  project-scoped env (geen globale `os.environ`-lek), resource-caps aan. Netwerk
  toegestaan (de app moet kunnen bouwen/installeren) maar zonder host-mounts.
- **Voorbeeld:** een project direct na `RepoBootstrapService.bootstrap_from_plan`
  (facet B) — het punt waarop de `??` in `set_skip_permissions(session, new_key,
  ??)` wordt ingevuld met de default uit §5.

### 2.3 `product-prod` — onze app, live, met echte inzet

- **Threat-model:** een volwassen product-project met echte gebruikers, echte
  secrets en een gebonden deploy-target. Een agent-misstap heeft nu *externe*
  gevolgen (kapotte productie, gelekte prod-secrets, ongewenste deploy/kosten). De
  isolatie is even sterk als `product-staging`, maar de **governance** is strenger:
  deploy en prod-secrets-toegang zijn human-gated.
- **Standaard-policy:** als `product-staging` (`sandcastle`,
  `skip_permissions=false`, resource-caps), plus: prod-`secrets_scope`, **deploy
  altijd achter menselijke bevestiging** (facet D §4.7 stelt deploy sowieso uit tot
  F3), en de strengste audit-retentie (facet D §4.8).
- **Voorbeeld:** een product-project met een `DeployTarget` (follow-up #11) en
  prod-secrets in zijn `secrets_scope`.
- **Belangrijk:** het verschil met `product-staging` is **niet** de isolatie-sterkte
  (die is gelijk) maar de *inzet* — daarom is de staging→prod-transitie een
  bewuste, human-gated stap (§6.2), geen automatische promotie.

### 2.4 `untrusted` — ga uit van vijandige code

- **Threat-model:** code van onbekende of externe herkomst (een geïmporteerde
  third-party repo, een externe PR die geëvalueerd wordt, een dependency-audit).
  Aanname: de code is vijandig tot het tegendeel bewezen is. Zowel de host áls het
  netwerk zijn te beschermen.
- **Standaard-policy:** `sandcastle + network=none`, read-only-rootfs, geen
  host-mount, alleen een expliciete `SAFE_*`-env-set (geen secrets, geen
  provider-keys). Dit is de sterkste isolatie in de stack (facet D §3-tabel,
  §4.4).
- **Voorbeeld:** "audit deze externe dependency", "draai deze onbekende PR in
  isolatie". Vaak een **tijdelijke** overlay op een verder vertrouwd project — zie
  de audit-edge-case §6.3 en de baseline-vs-override-discussie §7.
- **Toewijzings-regel:** `untrusted` mag als enige level *automatisch* worden
  opgelegd op provenance-red-flags (§5) — fail-safe. Terugkeren uit `untrusted`
  vereist wél een mens.

---

## 3. Relatie tot de bestaande `projects.kind`-tag

`projects.kind ∈ {meta, product, archived}` bestaat al (`database.py:30`,
default `"product"`, server_default `"product"`) maar is vandaag **inert** — geen
dispatch of security-code leest 'm (commentaar in de ORM zegt het letterlijk). Het
is een *portfolio*-tag (facet C: welk bord hoort bij welk soort werk), geen
security-dimensie.

`risk_class` **verfijnt** `kind`; ze vervangen elkaar niet:

| `projects.kind` | Mogelijke `risk_class` | Toelichting |
|---|---|---|
| `meta` | `meta` | 1-op-1. De meta-repo is per definitie `risk_class=meta`. |
| `product` | `product-staging` **of** `product-prod` | `kind` zegt "dit is een app"; `risk_class` zegt "hoe live/gevaarlijk". De staging↔prod-as leeft alléén in `risk_class`. |
| `archived` | (moot → val terug op strengste) | Een gearchiveerd project spawnt niets; als het toch geraakt wordt, degradeer naar de meest conservatieve stance. |
| (elk) | `untrusted` als *overlay* | `untrusted` is orthogonaal: een audit-run op een `product`-project is tijdelijk `untrusted` zonder dat `kind` verandert (§7). |

**Ontwerpkeuze:** `risk_class` woont in `ProjectSecurityProfile` (follow-up #6),
**niet** als extra `projects`-kolom. Reden: (a) het is een security-dimensie met
andere retentie/sync-eisen dan de portfolio-tag; (b) facet D koos bewust een eigen
tabel i.p.v. `KanbanMeta`/`projects` om portfolio-sync mogelijk te maken (facet D
§8, relatie met C); (c) de baseline-vs-override-splitsing (§7) past niet in één
enum-kolom. De classifier *leest* `projects.kind` als één (sterk) signaal, maar
*schrijft* `risk_class` in het security-profiel.

---

## 4. De classifier-signalen

De classifier beantwoordt: *"welke `risk_class` krijgt dit project bij afwezigheid
van een expliciete menselijke keuze?"* Hij draait op signalen met sterk
verschillende betrouwbaarheid. De centrale vraag per signaal is drieledig:
**beschikbaar** (kan Cockpit het lezen?), **betrouwbaar** (zegt het echt iets over
risico?), **manipuleerbaar** (kan onvertrouwde code het vervalsen?).

### 4.1 Het signalen-overzicht

| Signaal | Bron | Beschikbaar | Betrouwbaar | Manipuleerbaar | Klasse |
|---|---|---|---|---|---|
| **Handmatige `risk_class`-tag** | user via UI/API (follow-up #6) | ✅ zodra gezet | ⭐⭐⭐ hoogst | Nee (mens-gezet) — maar API is default-onauth (facet D §2.3) | Intent |
| **`projects.kind`** | user via portfolio-UI | ✅ altijd (default `product`) | ⭐⭐ (default is een gok) | Ja, via onauth API | Intent |
| **Meta self-detectie** (path == Cockpit-repo-root) | backend kent zijn eigen repo | ✅ altijd | ⭐⭐⭐ structureel verifieerbaar | Nee — Cockpit weet zijn eigen pad | Intent (veilige auto-elevatie) |
| **Repo-owner / git-remote** | `git remote get-url origin` | ✅ als remote bestaat | ⭐⭐ (eigen org ≠ third-party) | Ja, maar bewust (remote wijzigen is een daad) | Provenance |
| **First-commit-domein** (author-email) | `git log --reverse --format=%ae` | ✅ als er commits zijn | ⭐ (auteur triviaal te forgen) | Ja, triviaal | Provenance (alleen corroborerend) |
| **Repo-leeftijd / commit-count** | `git log --reverse --format=%ct`, `git rev-list --count` | ✅ | ⭐ (jong = nieuw, meer niet) | Deels (rebase reset datums) | Maturity |
| **Aantal agent-sessies / dispatch-historie** | kanban-claims, activity-feed | ✅ | ⭐ (druk ≠ prod) | Nauwelijks nuttig | Maturity |
| **Deploy-/CI-artefacten** (`.github/workflows/`, Dockerfile, deploy-manifest, gebonden `DeployTarget`) | filesystem-scan / follow-up #11 | ✅ | ⭐⭐ (echte deploy ⇒ dichter bij prod) | Ja, bewust | Maturity (staging→prod-signaal) |
| **In-repo `.cockpit-risk-class`-bestand** | filesystem | ✅ | ⚠️ alléén voor *verlagen* | Ja — het bestand kómt uit mogelijk-onvertrouwde code | Self-attested (nooit voor elevatie) |

### 4.2 De drie signaal-klassen

- **Intent-signalen** (⭐⭐⭐): *een mens heeft expliciet iets gezegd.* De handmatige
  `risk_class`-tag en de meta-self-detectie zijn hier load-bearing. `projects.kind`
  telt half mee — sterk als een mens 'm bewust zette, zwak zolang het de
  `"product"`-default is (die is een gok, geen keuze).
- **Provenance-signalen** (⭐⭐): *waar komt de code vandaan?* Repo-owner/remote
  onderscheidt "onze eigen nieuwe app" van "een externe clone". Forgeable, maar
  alleen door een bewuste daad — bruikbaar om `untrusted` *aan* te zetten (een
  vreemde remote op een eerder-eigen repo = red flag), niet om vertrouwen te
  verhogen.
- **Maturity-signalen** (⭐): *hoe gevestigd is het project?* Leeftijd, commit-count,
  agent-sessie-count, aanwezigheid van CI/deploy. Deze voeden de
  **staging→prod-suggestie** (§6.2) maar zijn **nooit** de enige basis voor een
  security-beslissing — een druk, oud project kan nog steeds staging zijn.

### 4.3 Manipuleerbaarheid = nooit alleenstaande basis

Drie signalen zijn triviaal vervalsbaar door de code in de repo zelf:
first-commit-author, commit-datums en elk in-repo bestand (`.cockpit-risk-class`,
CLAUDE.md-claims). Regel: **self-attested signalen mogen vertrouwen alleen
verlagen, nooit verhogen.** Een repo die in een bestand claimt `risk_class=meta` te
zijn, wordt genegeerd voor elevatie; een repo die `untrusted` claimt, wordt op zijn
woord geloofd (fail-safe). De onauth-API (facet D §2.3) is een aparte
manipulatie-vector: zolang `API_TOKEN` default uit staat kan elk host-proces de
handmatige tag zetten — dat is een reden temeer om elevatie áltijd door een mens te
laten bevestigen (§5), niet om op de tag-waarde blind te vertrouwen.

### 4.4 Het monotone-vertrouwen-principe (de kern)

> Automatische classificatie mag alleen **naar conservatiever** bewegen.
> Elk pad naar een **laxere** stance loopt via een mens.

Concreet:

- **Auto toegestaan (fail-safe, richting strenger):** onbekend/nieuw →
  `product-staging`; provenance-red-flag → `untrusted`; `archived`/onduidelijk →
  strengste stance.
- **Alleen via een mens (richting laxer):** iets → `meta` (behalve de
  path-verankerde self-detectie, de enige structureel-veilige auto-elevatie);
  `product-staging` → `product-prod`; `untrusted` → terug naar normaal.

Dit principe is wat de fork- en audit-edge-cases (§6) veilig maakt: een fork die
onze meta-config erft, glijdt niet automatisch terug naar `meta`; een project dat
voor audit `untrusted` werd, keert niet vanzelf terug.

---

## 5. De automatische default + wanneer een mens bevestigt

### 5.1 De default voor een splinternieuw product-project

Bij `RepoBootstrapService`-geboorte (facet B §3.1), zonder handmatige tag:

```
risk_class            = product-staging
default_transport     = sandcastle (docker; fallback no-sandbox + UI-warning)
default_skip_permissions = false
secrets_scope         = (leeg — geen globale env-lek)
network_policy        = allow (bouwen/installeren mag; geen host-mount)
```

Dit is exact de conservatieve default die de facet-D-parent al aanbeveelt (§5.2
trade-offs: sandcastle-default `docker`, `skip_permissions=false`). De classifier
maakt 'm *expliciet* en verankert 'm in de taxonomie: **fail-closed** — bij twijfel
de veiligere klasse.

De enige automatische afwijking van deze default:

- **Meta self-detectie** → `meta`. Als het project-pad de Cockpit-repo-root zelf
  is, is `meta` structureel verifieerbaar en veilig (§2.1). Dit is de enige
  auto-elevatie.
- **Provenance-red-flag** → `untrusted`. Bijv. een geïmporteerde repo met een
  externe/onbekende remote-owner, of een expliciete "import external code"-flow.
  Fail-safe auto-degradatie.

### 5.2 Wanneer een mens moet bevestigen

| Situatie | Auto of mens? | Waarom |
|---|---|---|
| Nieuw eigen product-project | **Auto** → `product-staging` | Conservatief; veilig zonder te vragen. |
| Pad == Cockpit-repo-root | **Auto** → `meta` | Structureel verifieerbaar, niet spoofbaar. |
| Provenance-red-flag / audit-flow | **Auto** → `untrusted` | Fail-safe; strenger vragen kost niets. |
| Elk verzoek naar `meta` dat níet self-detectie is | **Mens** | `meta` = mag het platform verbouwen; nooit op een heuristiek. |
| `product-staging` → `product-prod` | **Mens** | Prod = echte gebruikers/secrets/deploy; blast-radius extern. |
| `untrusted` → terug naar normaal | **Mens** | Audit afsluiten is een bewuste "ik vertrouw dit nu"-daad. |
| Signalen tegenstrijdig (bv. `kind=meta` maar externe remote) | **Mens** | Ambiguïteit → de veiligere klasse tonen + laten bevestigen. |

**Vorm van de menselijke bevestiging:** de classifier *stelt voor*, de mens
*bevestigt*. Concreet: bij geboorte krijgt het profiel de auto-default; een
elevatie-verzoek (`→meta`, `→prod`, `untrusted→normaal`) blokkeert niet de dispatch
maar zet het profiel in een "pending confirmation"-staat op de conservatieve
waarde, tot een mens het bevestigt. Dit sluit aan bij het
impediment-met-opties-patroon dat elders in Cockpit de standaard-vraagflow is
(nooit blokkerend pollen; de veilige waarde geldt intussen).

---

## 6. Transition-triggers

Een `risk_class` is niet statisch — projecten groeien en degraderen. De triggers,
consequent met het monotone-vertrouwen-principe (§4.4):

### 6.1 `product-staging` ↔ `product-prod`

- **staging → prod (promotie, human-gated):** getriggerd door een *suggestie* uit
  maturity-signalen — een gebonden `DeployTarget` (follow-up #11), prod-secrets in
  de `secrets_scope`, of een handmatige "promote to prod"-actie. Nooit automatisch:
  prod betekent externe blast-radius. De maturity-signalen *stellen voor* ("dit
  project heeft nu een deploy-target — promoten naar prod?"), een mens beslist.
- **prod → staging (degradatie, auto-OK):** getriggerd door verlies van
  prod-inzet — deploy-target ontkoppeld, prod-secrets geroteerd/verwijderd, of een
  handmatige degradatie. Veilig automatisch (richting strenger governance-model).

### 6.2 elk → `untrusted`

- **Aan (auto-OK, fail-safe):** provenance-red-flag (vreemde remote op een
  eerder-eigen repo), expliciete audit-toggle, of een "run this external PR in
  isolation"-flow. Zie §7 — dit is meestal een *tijdelijke overlay*, geen
  permanente herclassificatie.
- **Af (mens):** terugkeren uit `untrusted` naar de baseline-klasse vereist een
  menselijke "dit is nu veilig"-bevestiging.

### 6.3 `meta`

`meta` heeft geen automatische in- of uit-transitie: erin komt alleen via
path-self-detectie of een mens (§5.2); eruit (bv. de fork-case §6.1 hieronder is
eigenlijk een *ander pad*, geen transitie) idem. Een repo wordt niet
"gepromoveerd" naar `meta` door welk signaal dan ook.

---

## 7. Edge cases

### 7.1 Fork: een project "verhuist" van meta naar product

**Scenario:** iemand forkt `claude-cockpit` om er een product bovenop te bouwen. De
fork erft `.claude/settings.json`, een CLAUDE.md die "dit is de meta-repo" zegt, en
mogelijk een `kind=meta`-achtige config.

**Risico:** de classifier zou uit in-repo signalen kunnen concluderen dat de fork
nog `meta` is — en de fork zo `skip_permissions=true` + `worktree` (geen container)
geven, terwijl het nu onbewezen product-code is.

**Resolutie:** `meta` is **path-verankerd, niet content-verankerd** (§2.1, §4.4).
Alleen de checkout waaruit Cockpit daadwerkelijk draait is `meta`; een fork heeft
per definitie een ander pad/remote en is dus `product-staging` tot een mens anders
beslist. In-repo bestanden (CLAUDE.md, `.cockpit-risk-class`, geërfde
`settings.json`) mogen `meta` **nooit** afdwingen — ze zijn self-attested en tellen
alleen voor *verlaging* mee. De fork-case is daarmee geen speciale transitie maar
een gevolg van het monotone-principe: elevatie naar `meta` loopt altijd via een
mens of via de niet-spoofbare self-detectie.

### 7.2 Project tijdelijk `untrusted` voor audit

**Scenario:** een verder vertrouwd `product-staging`-project moet een externe PR of
een verdachte dependency evalueren. Je wilt dat werk in de strengste isolatie
draaien, maar het project daarna zijn normale klasse teruggeven.

**Risico:** als "untrusted" een *permanente* herclassificatie is, verlies je de
oorspronkelijke klasse en moet iemand 'm handmatig reconstrueren na de audit — en
vergeet dat wellicht (project blijft onnodig streng, of erger: iemand zet 'm te
lax terug).

**Resolutie:** modelleer `untrusted`-voor-audit als een **tijdelijke overlay**, niet
als een baseline-wijziging. Zie §8 — dit vraagt een twee-velden-model in
`ProjectSecurityProfile`: een stabiele `risk_class` (baseline) plus een optionele
`active_override` met reden, `set_by` en optioneel `expires_at`. Tijdens de audit
wint de override (strengste stance); bij release valt het profiel terug op de
baseline. Auto-aan (fail-safe), release via een mens (§4.4).

### 7.3 Tegenstrijdige signalen

**Scenario:** `projects.kind=meta` (ooit door een mens gezet) maar de git-remote
wijst naar een onbekende externe org, en het pad is *niet* de Cockpit-root.

**Resolutie:** ambiguïteit lost altijd op naar de **veiligere** klasse plus een
menselijke bevestigings-prompt (§5.2, laatste rij). De classifier kiest nooit stil
de laxere kant van een tegenstrijdigheid.

---

## 8. Data-model-implicatie voor follow-up #6 (baseline vs. transient override)

De edge-cases §7.1 en §7.2 dwingen één ontwerp-keuze af die follow-up #6 moet
meenemen: `ProjectSecurityProfile` heeft **twee** velden nodig, geen enkele
`risk_class`-enum:

- **`risk_class`** — de *baseline*: `meta | product-staging | product-prod`. Stabiel,
  wijzigt alleen via de transitions van §6 (grotendeels human-gated). `untrusted`
  hoort hier **niet** thuis als baseline in het gewone geval — het is een toestand,
  geen groei-fase.
- **`active_override`** (optioneel) — een *transiënte* overlay: `untrusted` met
  `reason`, `set_by`, optioneel `expires_at`. Aanwezig ⇒ wint over de baseline
  (strengste stance). Afwezig ⇒ de baseline geldt.

De *effectieve* klasse die de dispatch leest = `active_override or risk_class`. Dit
is het enige extra dat de taxonomie boven de facet-D-parent-blauwdruk (§4.3) legt:
de parent noemt `risk_class` als één enum met vier waarden; deze doc splitst de
vierde (`untrusted`) af als transiënte overlay omdat de audit- én fork-case anders
niet veilig te modelleren zijn. Follow-up #6's default-test wordt daarmee:

> *default voor een nieuw product-project: `risk_class=product-staging`,
> `active_override=None`, `default_skip_permissions=false`,
> `default_transport=sandcastle`.*

En een `classify_default(project) -> risk_class`-functie (facet B roept 'm aan bij
geboorte) implementeert §5.1: path-self-detectie → `meta`; provenance-red-flag →
`untrusted`-override; anders → `product-staging`.

---

## 9. Wat deze doc aan follow-up #6 levert (het contract)

1. **Vier levels** met scherpe threat-modellen + default-policy per level (§2).
2. **`risk_class` verfijnt `projects.kind`**, woont in `ProjectSecurityProfile`,
   niet in `projects` (§3).
3. **Signalen-model** met beschikbaarheid/betrouwbaarheid/manipuleerbaarheid en het
   monotone-vertrouwen-principe: *auto alleen richting strenger; elevatie via een
   mens* (§4).
4. **Fail-closed default** `product-staging` voor nieuwe projecten; de enige
   auto-elevatie is de path-verankerde `meta`-self-detectie; `untrusted` mag
   auto-aan (§5).
5. **Transition-triggers** staging↔prod (promotie human-gated, degradatie auto) en
   →untrusted (aan auto, af via mens) (§6).
6. **Edge-cases** fork (meta is path-verankerd) en audit (untrusted als transiënte
   overlay), die het **twee-velden-model** (`risk_class` + `active_override`)
   afdwingen — de enige toevoeging boven de facet-D-blauwdruk (§7–§8).

---

## 10. Out-of-scope (expliciet)

- **Implementatie van de classifier + `ProjectSecurityProfile`-tabel/CRUD** →
  follow-up #6.
- **`risk_class → transport/permissions`-mapping in de dispatch** → facet-D-parent
  §4.3 + follow-up #6 (het contract staat er al; deze doc voegt geen nieuwe
  mapping toe).
- **Transport-hardening** (Sandcastle resource-caps, `network=none`,
  read-only-rootfs) → follow-ups #2/#3.
- **Secrets-store + prod-secrets-scope** → follow-up #4/#5.
- **`DeployTarget` (staging→prod-signaal)** → follow-up #11; deze doc gebruikt 'm
  alleen als transitie-*trigger*, bouwt 'm niet.
- **Portfolio-brede policy-sync van `risk_class`** → facet C.
- **Auth-hardening van de tag-API** (onauth-manipulatie van de handmatige tag) →
  facet-D-parent §4.2 (I4b + auth-default); hier alleen benoemd als reden om
  elevatie door een mens te laten bevestigen.
