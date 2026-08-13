---
title: "Portfolio ↔ security overdracht — drie open vragen voor facet D"
type: analysis
status: superseded
---

> **Superseded op 2026-08-13.** De feature die dit document beschrijft is uit
> Agent Cockpit verwijderd tijdens de opruiming naar de kern. Wat er precies
> weg is, waarom, en welke gedragsverandering dat opleverde staat in
> [`kern-terugbrengen-plan.md`](./kern-terugbrengen-plan.md). Dit document
> blijft staan als beslisspoor; behandel de inhoud niet als huidige toestand.

# Portfolio ↔ security overdracht — drie open vragen voor facet D

> **Design-only.** Dit document stelt **drie security-vragen** die facet D
> (veilig bouwen & uitleveren) moet beantwoorden nu facet C de project-`kind`-
> tag (`meta` / `product` / `archived`) op `projects` legt. Het doc geeft
> **alleen de vragen** — geen antwoorden, geen implementatie, geen
> policy-tekst. Het beantwoorden van deze vragen is **facet D analyse-werk**
> (`work_type="analysis"` op de D-vervolgkaart(en)) en gebeurt in
> `docs/cockpit/veilig-bouwen-en-uitleveren.md` of een opvolger daarvan.
>
> **Bron van deze overdracht:** `docs/cockpit/portfolio-orchestratie.md`
> §7 #7 (facet C legt de tag; facet D ontwerpt wat de tag *betekent* voor
> security).
>
> **Eigenaar van de antwoorden:** `docs/cockpit/veilig-bouwen-en-uitleveren.md`
> (facet D). Deze handoff geeft geen eigen ontwerp.

## 0. Leeswijzer

Facet C's portfolio-architectuur (`portfolio-orchestratie.md` §4, optie 2 +
varianten) introduceert een **kind-tag** op de `projects`-tabel zodat
expliciet onderscheiden kan worden tussen:

| Kind | Betekenis vandaag (C-zijde) |
|---|---|
| `meta` | Het cockpit-platform zelf (claude-cockpit-repo). Mag zichzelf wijzigen. |
| `product` | Een via inceptie + bootstrap geboren product-app. Heeft een eigen subtree, eigen repo. |
| `archived` | Buiten gebruik; niet meer gedispatched. |

De tag zelf is **puur structureel** — een 1-koloms-uitbreiding zonder
gedragsverandering (zie `portfolio-orchestratie.md` §7 #1). Zodra D
antwoord geeft op de drie vragen hieronder, wordt de tag de
**drager** van een security-policy: per kind (of per project binnen een
kind) gelden andere regels voor secrets, write-bereik, en
creatie-recht.

De vragen zijn bewust **open geformuleerd** (geen "wij kiezen optie X")
zodat ze als invoer dienen voor facet D's eigen analyse-kaart, niet als
dictaat vanuit C.

---

## 1. Vraag A — Secrets/credentials per product-project

### 1.1 De vraag

**Mag een `product`-project dezelfde secrets/credentials lezen als het
`meta`-project, of krijgt elk product-project een eigen
sandbox-credential-set?**

### 1.2 Waarom dit een D-vraag is

Vandaag deelt het hele platform één global-secret-vlak: `os.environ` van
het backend-proces lekt door naar élke gespawnde sessie (zie
`veilig-bouwen-en-uitleveren.md` §2.5). D's `SecretStore`-ontwerp
(follow-up #4: `~/.claude-registry/secrets/<project_key>.age`,
mode 600, age-encrypted) en de per-project-env-injectie in
`spawn_session` (follow-up #5, deels geïmplementeerd op kaart
`b5c71e0c28c4481aa47569b3fc5b9489`) zijn precies de instrumenten die
deze vraag kunnen beantwoorden — maar het *antwoord* (deel-globaal vs.
volledig-geïsoleerd) bepaalt welke `ProjectSecurityPolicy`-velden
verplicht gevuld moeten zijn bij geboorte van een product-project.

### 1.3 Pointer naar D

- `veilig-bouwen-en-uitleveren.md` §2.5 (inventarisatie per-project secrets)
- `veilig-bouwen-en-uitleveren.md` §4.5 (drie-lagen-architectuur: vault +
  scope-filter + audit-log)
- `veilig-bouwen-en-uitleveren.md` §6 follow-up **#4** (`SecretStore`-interface
  + age-file-implementatie)
- `veilig-bouwen-en-uitleveren.md` §6 follow-up **#5** (per-project
  env-injectie in `spawn_session`)
- `veilig-bouwen-en-uitleveren.md` §6 follow-up **#6** (`ProjectSecurityPolicy`-
  dataclass + storage; `secrets_scope_id` is het veld dat deze vraag
  beantwoordt)

### 1.4 Sub-vragen die D ook kan beantwoorden (niet verplicht, ter info)

- Als `product` een eigen credential-set krijgt: wordt die set *bij geboorte*
  leeg aangemaakt en door de operator/blueprint gevuld, of via een
  inheritance-regel (kind van `meta` erft een subset)?
- Wat is de blast-radius als een `product`-sessie een secret uitlekt — kan
  dezelfde secret in andere product-projecten opduiken, of is de scope strikt
  per-project?

---

## 2. Vraag B — Write-bereik per product-project

### 2.1 De vraag

**Mag een `product`-project-sessie schrijven buiten het eigen project-pad?
Vandaag schrijft `.mcp.json` write-anywhere (zie `kanban-followups.md` §I4b);
moet dat read-only worden voor product-projecten, of is het bestaande
write-anywhere-oppervlak aanvaardbaar zolang andere eindpunten net zo
strikt gevalideerd worden?**

### 2.2 Waarom dit een D-vraag is

Het pad-allowlist-werk voor `.mcp.json`-write (I4b) is in 2026-07-12
gefixet (`kanban-followups.md` §I4b statusregel), en D's follow-up #1
erft dit eigenaarschap. Maar de bredere vraag — *in welke directories
mag een product-project-sessie überhaupt schrijven?* — is een
**policy-vraag**, geen implementatie-vraag, en hoort daarmee bij D.

D bezit het permission-model (`skip_permissions` default = `True`,
zie `veilig-bouwen-en-uitleveren.md` §2.2), het API-oppervlak
(`RequireApiTokenMiddleware`, I4b), en de transport-hardening
(Sandcastle resource-caps, network-mode, read-only-rootfs — zie
follow-ups #2 en #3). Al deze hefbomen samen bepalen hoe streng het
write-bereik voor een product-project kan worden afgedwongen; C levert
alleen de kind-tag die de "streng" of "mild" policy triggert.

### 2.3 Pointer naar D

- `veilig-bouwen-en-uitleveren.md` §2.2 (permission-model; `skip_permissions`
  default = `True` en de write-anywhere-implicatie)
- `veilig-bouwen-en-uitleveren.md` §2.3 (API-oppervlak; I4b-context)
- `veilig-bouwen-en-uitleveren.md` §4.2 (API-ingang [A]: pad-allowlist)
- `veilig-bouwen-en-uitleveren.md` §4.3 (project-grens [B]:
  `ProjectSecurityPolicy`-velden die dit kunnen afdwingen)
- `veilig-bouwen-en-uitleveren.md` §4.4 (sessie-grens [C]: container-hardening)
- `veilig-bouwen-en-uitleveren.md` §6 follow-up **#1** (I4b pad-allowlist voor
  `.mcp.json`-write)
- `veilig-bouwen-en-uitleveren.md` §6 follow-up **#6** (`ProjectSecurityPolicy`:
  `default_skip_permissions` per `risk_class` is het directe antwoord)
- `kanban-followups.md` §I4b (status van de I4b-fix; out-of-scope-regel over
  een bredere write-anywhere-auditor noemt expliciet dat dit een open spoor
  is — niet door C gesloten)

### 2.4 Sub-vragen die D ook kan beantwoorden (niet verplicht, ter info)

- Geldt het write-bereik alleen voor sessies die door autodispatch zijn
  gestart, of ook voor handmatig vanuit de UI gestarte sessies binnen een
  product-project?
- Als een product-project strenger wordt (read-only buiten eigen pad), wat
  is dan het escape-ventiel voor een operator die *bewust* wél buiten het pad
  wil schrijven — een eenmalige UI-flag, een policy-override, of niks?

---

## 3. Vraag C — Wie mag een `product`-project aanmaken

### 3.1 De vraag

**Wie mag een `product`-project aanmaken — een operator via de UI, of
alleen een gesuperviseerde meta-sessie?**

### 3.2 Waarom dit een D-vraag is

Deze vraag raakt direct aan D's threat-model voor de project-grens [B]
(`veilig-bouwen-en-uitleveren.md` §4.3): wie een project mag
registreren bepaalt wie de eerste `ProjectSecurityPolicy` (follow-up #6)
mag zetten, en daarmee wie de blast-radius van een splinternieuw
product-project initieert. Een onauth `POST /api/v1/projects` op een
default-installatie (zie §2.3) laat elk proces op de host een project
aanmaken — wat op een single-user devbox oké is, maar op een
shared-host een supply-chain-aanvalsvector is.

Facet C bezit de *project-flow* (intake → bootstrap → registratie, in
samenwerking met facet A en B), maar de *policy* van "wie krijgt dit
recht en onder welke authenticatie" is een security-beslissing. D moet
bepalen of de default-install `is_auth_required_for_project_create`
aan of uit staat, en welke risico-klasse een via-de-UI-aangemaakt
project meekrijgt versus een via-bootstrap-aangemaakt project.

### 3.3 Pointer naar D

- `veilig-bouwen-en-uitleveren.md` §2.3 (API-auth vandaag; bearer-token
  default uit)
- `veilig-bouwen-en-uitleveren.md` §4.2 (API-ingang [A]: auth-default voor
  `0.0.0.0`-binding; trade-off-tabel §5.2: "Refuse-to-start" als aanbeveling)
- `veilig-bouwen-en-uitleveren.md` §4.3 (project-grens [B]:
  `risk_class`-taxonomie volgt uit follow-up #12)
- `veilig-bouwen-en-uitleveren.md` §5.2 (auth-trade-off: warn-and-continue
  vs. refuse-to-start)
- `veilig-bouwen-en-uitleveren.md` §6 follow-up **#6** (`ProjectSecurityPolicy`
  + storage)
- `veilig-bouwen-en-uitleveren.md` §6 follow-up **#12** (`risk_class`-
  taxonomie + classifier — bepaalt welke `risk_class` een UI-aangemaakt
  project standaard krijgt)

### 3.4 Sub-vragen die D ook kan beantwoorden (niet verplicht, ter info)

- Als een operator via de UI een product-project mag aanmaken, draagt
  die operator dan automatisch `risk_class = product-staging`, of moet
  er een bewuste tagging-stap zijn (met audit-logregel)?
- Mag een product-project *zelf* een ander product-project aanmaken
  (compositie), of is dat voorbehouden aan een meta-sessie?

---

## 4. Wat deze overdracht expliciet niet doet

- **Geen antwoorden.** De drie vragen hierboven zijn open. D's analyse-
  kaart (of -kaarten) levert de antwoorden.
- **Geen implementatie-ontwerp.** Geen API-shape, geen schema-uitbreiding,
  geen policy-tekst. C levert de kind-tag (§7 #1 van
  `portfolio-orchestratie.md`); D consumeert 'm.
- **Geen security-implementatie.** Alle security-services die in de vragen
  ter sprake komen (`SecretStore`, `ProjectSecurityPolicy`, pad-allowlist,
  audit-log) zijn D's bestaande follow-ups (§6 #1, #4, #5, #6, #10).
  Deze handoff voegt geen nieuwe D-follow-ups toe.
- **Geen facet-C-implementatie.** De kind-tag zelf (§7 #1) wordt door
  facet C's eigen vervolgkaarten opgeleverd; deze handoff wacht daarop
  niet (de vragen zijn stuurbaar zodra D eraan begint).
- **Geen cross-facet-vragen die niets met portfolio te maken hebben.**
  Vragen als "moet Sandcastle een network-egress-proxy krijgen?" of
  "moet de MCP-server-trust-model herzien worden?" zijn D-only en
  blijven in `veilig-bouwen-en-uitleveren.md` §6 staan.

---

## 5. Routing van de antwoorden

Wanneer D de drie vragen beantwoordt, landt de uitkomst in:

- **`veilig-bouwen-en-uitleveren.md`** zelf, als een nieuwe sectie die de
  drie portfolio-vragen expliciet beantwoordt en de `ProjectSecurityPolicy`-
  shape (follow-up #6) aanscherpt; **of**
- **een aparte design-doc** (bv. `docs/cockpit/portfolio-security-policy.md`)
  die `veilig-bouwen-en-uitleveren.md` §4.3 en §6 aanvult — in welk geval
  die nieuwe doc dezelfde bron-context deelt en via een nieuwe D-follow-up
  in Backlog wordt gezet.

Beide paden zijn aan D; C wacht op de uitkomst en consumeert 'm in een
eventuele portfolio-policy-sync (zie `veilig-bouwen-en-uitleveren.md` §8,
relatie met facet C).

De beantwoording zelf gebeurt op een of meer D-vervolgkaarten met
`work_type="analysis"` (geen engineer-implementatie in dezelfde sprint);
C doet op basis van die analyse geen eigen ontwerp.
