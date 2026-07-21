---
title: "Analyse — Approval-model: privilege-scheiding tussen agent en gebruiker"
type: analysis
status: decided
---

# Analyse — Approval-model: privilege-scheiding tussen agent en gebruiker

**Datum:** 2026-07-21
**Kaart:** `38d32e94…` (kind van Lemma-analyse `b00f3705…`)
**Status:** Analyse afgerond — Lemma's `execute_as_user`-patroon wordt **niet**
overgenomen (twee onafhankelijke redenen, §3). De autorisatiegrens die de kaart wil
invoeren **bestaat al** (§2.2) en is vandaag niet bruikbaar onder autonome dispatch;
daar zitten de drie vervolgkaarten.

**Verwant:**
[`lemma-platform-analyse.md` §4.2](./lemma-platform-analyse.md) (bron),
[`veilig-bouwen-en-uitleveren.md`](./veilig-bouwen-en-uitleveren.md) §2.1–§2.3,
[`risk-class-taxonomie.md`](./risk-class-taxonomie.md),
[`headless-stream-json-transport-spike.md`](./headless-stream-json-transport-spike.md) §4c,
[`kanban-conventions.md`](./kanban-conventions.md) §5.

---

## TL;DR

1. **Twee premissen van de kaart kloppen niet.** `open_gate` beëindigt de sessie
   **niet** — het blokkeert inline (polling) en geeft het antwoord terug zodat de run
   doorloopt (`mcp_server.py:846-892`). Het is `report_impediment` dat de claim
   vrijgeeft en de sessie beëindigt. En "er ís geen autorisatiegrens" geldt alleen
   voor de `meta`-klasse: voor product-projecten staat `skip_permissions=False` al
   als veilige default (§2.2).
2. **`execute_as_user()` is bij ons per constructie een no-op.** Lemma strípt een
   workload-identiteit zodat een sessie met het token van de *gebruiker* wordt gemint.
   Dat veronderstelt een identity-laag met twee principals. Onze agent draait als
   dezelfde OS-gebruiker, met dezelfde home, dezelfde `~/.claude/.credentials.json`,
   dezelfde SSH-socket en dezelfde proces-env. Er is één principal; strippen levert
   exact dezelfde autoriteit op.
3. **Het patroon staat bovendien haaks op ons feitelijke faalmodel.** Privilege-scheiding
   verdedigt tegen een agent die iets doet wat hij *niet mág*. Onze drie
   gedocumenteerde incidenten zijn stuk voor stuk een *toegestane* actie die op dat
   moment verkeerd was — de agent zou zijn eigen escalatie hebben goedgekeurd (§3.2).
4. **Het echte gat is het spiegelbeeld:** de grens die we wél hebben, is onbruikbaar
   onder autonome dispatch. Een afgedwongen permission-prompt heeft **geen
   antwoordkanaal** — `--permission-prompt-tool` komt in de hele codebase niet voor.
   Een product-dispatch stalt op de eerste `ask`-tool tot de reaper de claim opruimt.
5. **Bij ons draait die grens vandaag alleen niet omdat twee override-rijen hem
   tegenhouden** (§2.3). Deze repo staat als `product-staging` in zijn
   security-profiel; alleen `skip_permissions=1` + `transport=worktree` in
   `kanban_meta` houden dispatch werkend. Die rijen zijn load-bearing.
6. **Drie vervolgkaarten**, geen implementatie van het Lemma-patroon.

---

## 1. De vraag in één paragraaf

De kaart vraagt of we Lemma's fijnmazige approval-model moeten overnemen: de agent
herhaalt in `request_approval` exact de tool + args die hij wil draaien, en bij
goedkeuring draait diezelfde tool onder een context waaruit de agent-identiteit is
gestript — dus met de autoriteit van de *gebruiker*. De kaart erkent zelf de
voorwaarde: wij dispatchen met `--dangerously-skip-permissions`, dus er zou eerst een
autorisatiegrens ingevoerd moeten worden. Acceptance criterion 1 vraagt expliciet om
die afweging, met `not_feasible` als legitieme uitkomst.

Het antwoord is genuanceerder dan ja/nee, want de premisse dat er géén grens is, is
onjuist — en de grens die er is, blijkt kapot op een manier die de kaart niet vermoedde.

---

## 2. Wat er vandaag werkelijk staat

### 2.1 Premisse-correctie: `open_gate` beëindigt de sessie niet

De kaart stelt dat `open_gate` "kaart-niveau is en **de sessie beëindigt**", en dat je
daardoor "een hele sessie verliest voor één beslissing". Dat is de beschrijving van
`report_impediment`, niet van `open_gate`.

`open_gate` (`backend/app/kanban/mcp_server.py:846`) doet precies wat de kaart als
ontbrekend aanmerkt:

> *"Unlike report_impediment, this does NOT release the claim or end the session — it
> simply waits (polling) for the human's pick, then returns it so the run can continue
> inline. Use this for a single decision that shouldn't interrupt the flow."*

Het maakt een `KanbanGate`, logt een `**Gate:**`-comment, en pollt elke 2s tot 30 min
op een antwoord (`_GATE_POLL_INTERVAL_SECONDS` / `_GATE_DEFAULT_TIMEOUT_SECONDS`).
Bij antwoord: `{"answer": …}`. Bij timeout: `{"error": "timeout"}` — en de gate blijft
open, zodat een mens 'm later alsnog via de UI kan beantwoorden.

**Het inline-beslispunt dat de kaart wil bouwen, bestaat dus al.** Wat ontbreekt is
niet het *vragen*, maar het *koppelen van dat vraagmechanisme aan het
permissiesysteem*. Dat verschuift de hele analyse: de vraag is niet "hoe bouwen we een
fijnmazige approval", maar "waarom is de bestaande fijnmazige gate niet aangesloten op
de plek waar permissies daadwerkelijk worden afgedwongen".

Nuance die de correctie niet ondermijnt: `open_gate` is in de praktijk bewust
gedeprioriteerd. De docstring van `report_impediment` noemt zichzelf *"the standard
question flow for all agents — every human-decision request goes here, not through the
blocking `open_gate` tool"*, en `engineer.md:334` bevestigt dat. Terecht: voor een
**productbeslissing** is een openstaande sessie duur, want de mens beslist op zijn
eigen tempo. Voor een **permissiebeslissing** ligt dat anders — die is mid-run, de
context is nog warm, en het alternatief (sessie weggooien) is duurder dan wachten.
Zie §4.

### 2.2 De autorisatiegrens bestaat al — per `risk_class`

De kaart stelt dat er geen grens is om te scheiden. Dat is per risicoklasse verschillend
en al geïmplementeerd:

```python
def _skip_permissions_for_risk_class(risk_class: str | None) -> bool:
    """Only ``meta`` (and the no-profile fallback) keep the permissive bypass;
    every product/untrusted class defaults to enforcing permissions."""
    return risk_class is None or risk_class == "meta"

def _transport_for_risk_class(risk_class: str | None) -> str:
    if risk_class is None or risk_class == "meta":
        return DEFAULT_TRANSPORT
    return "sandcastle"
```
*(`backend/app/kanban/dispatch.py:329-346`)*

En `ProjectSecurityProfile` (`backend/app/models/security_profile.py:50-60`) heeft als
ORM-default voor een vers product-project: `risk_class="product-staging"`,
`default_transport="sandcastle"`, `default_skip_permissions=False`.

Dus: **voor product-projecten worden permissies vandaag al afgedwongen**, met
container-isolatie erbij. De afweging uit AC1 is dus niet meer open — ze is al gemaakt,
en verschillend per klasse:

| Klasse | Grens agent↔gebruiker | Rationale |
|---|---|---|
| `meta` (Cockpit bouwt zichzelf) | **Nee**, bewust | De agent werkt in een verse worktree van een repo die volledig onder versiebeheer staat, met CI als achtervang. De blast-radius is een branch. |
| `product-*` / `untrusted` | **Ja**, al default | Willekeurige externe app; de agent kent de codebase niet en de host heeft niets te winnen bij vertrouwen. |

Dat is een verdedigbare tweedeling en er is geen aanleiding hem te heropenen.

### 2.3 …maar die grens wordt bij ons tegengehouden door twee override-rijen

`get_skip_permissions` (`dispatch.py:349`) laat een expliciete `KanbanMeta`-override
altijd winnen boven het security-profiel. Wat er feitelijk in de databases staat:

```
# ~/.claude-registry/kanban.db :: kanban_meta
('skip_permissions:git:github.com/guillaumevandevelde/claude-cockpit', '1')
('transport:git:github.com/guillaumevandevelde/claude-cockpit', 'worktree')

# backend/claude_registry.db :: project_security_profiles
('/home/vdvgu/claude-cockpit', 'product-staging', 0, 'sandcastle')
```

*(reproductie: `python3 -c "import sqlite3; c=sqlite3.connect('<db>'); [print(r) for r in
c.execute('select key,value from kanban_meta')]"` — read-only, 2026-07-21)*

Deze repo — het meta-project bij uitstek — staat dus geclassificeerd als
`product-staging` met `skip_permissions=0` en `transport=sandcastle`, en draait alleen
permissief omdat twee expliciete override-rijen dat profiel overstemmen.

Dat is **geen bug maar een bekende, in code gedocumenteerde tussentoestand**:
`security_profile_service.py:110-115` legt uit dat `meta` bewust op de conservatieve
product-default terugvalt omdat de classifier die `risk_class=meta` zou zetten
follow-up #12 is, nog ongebouwd — *"until then the safe-by-default profile is the
conservative fallback rather than risking a permissive gap"*.

Wat er **niet** eerder is opgemerkt, is het gevolg: die twee override-rijen zijn
**load-bearing voor alle autonome dispatch op deze repo**. Verdwijnen ze — via de
bestaande `POST /skip_permissions`-toggle, via een DB-reset (CLAUDE.md: *"No database
migration system — schema changes require deleting the db"*), of via een verse
installatie — dan valt deze repo terug op het profiel, en dan gebeurt wat §2.4
beschrijft. Er is geen waarschuwing en geen test die dit afvangt.

### 2.4 Zodra de grens engageert, is er geen antwoordkanaal

Dit is het scherpste bevinding van deze analyse.

Wanneer `skip_permissions=False`, dwingt Claude Code zijn permissiesysteem af:
`deny` → blokkeren, `ask` → **de mens vragen**, `allow` → doorlaten
(`docs/features/permissions.md`). In een autonome dispatch zit er niemand aan de
tmux-pane. De prompt verschijnt, niemand beantwoordt hem, en de sessie staat stil tot
`reap_stale_claims` de claim als dood beschouwt en de kaart opnieuw dispatcht — waarna
hetzelfde opnieuw gebeurt.

Claude Code's eigen remedie hiervoor is `--permission-prompt-tool` (een MCP-tool die de
permissievraag afhandelt) of het bidirectionele control-protocol
(`--input-format stream-json`). Beide zijn ons bekend — ze staan in
`structured-events-schema.md:134` en `headless-stream-json-transport-spike.md:182` als
reden waarom `permission_request` geen producer heeft — maar:

```
$ grep -rn "permission-prompt-tool\|permission_prompt_tool" --include="*.py" .
(geen treffers in backend/, alleen de twee docs-vermeldingen)
```

**Er is geen enkele code die een permissievraag kan beantwoorden.** De veilige default
voor product-projecten is daarmee vandaag niet alleen ongetest, maar naar alle
waarschijnlijkheid niet-functioneel onder autonome dispatch.

> **Eerlijk gelabeld: dit is een code-gebaseerde gevolgtrekking, geen meting.** Ik heb
> geen product-project gedispatcht om de stall te observeren — er bestaat er geen
> (`project_security_profiles` bevat één rij, deze repo). De keten is
> `default_skip_permissions=False` → geen `--dangerously-skip-permissions` →
> permissiesysteem actief → `ask` zonder `--permission-prompt-tool` → interactieve
> prompt zonder lezer. De eerste vervolgkaart (§6.1) is bewust een *meting*, geen fix,
> juist omdat deze conclusie afgeleid is.

---

## 3. Waarom Lemma's `execute_as_user` niet overdraagbaar is

Twee onafhankelijke redenen. Elk is op zichzelf voldoende.

### 3.1 Wij hebben één principal, geen twee

Lemma's `ApprovalExecutor.execute_as_user()` zet `workload_type`/`workload_id`/
`agent_name` op `None`, waarna sandbox-tools draaien in een sessie **gemint met het
token van de gebruiker**. Dat werkt omdat Lemma een identity-laag heeft die twee
verschillende principals kan uitgeven en onderscheiden: een workload-identiteit voor
de agent, een user-identiteit voor de mens.

Bij ons is er geen tweede principal om naar te escaleren. Uit
`veilig-bouwen-en-uitleveren.md` §2.1:

> *"Een agent-sessie in `worktree_transport` draait als dezelfde OS-gebruiker, in
> dezelfde shell-omgeving, met dezelfde home-dir, dezelfde `~/.claude/.credentials.json`,
> dezelfde `~/.aws/credentials`, dezelfde SSH-socket."*

En §2.3: de API op `127.0.0.1:8000` is default onauth (`settings.api_token = None`),
inclusief de MCP-mount. Er is dus geen identiteit om te strippen en geen tweede
autoriteit om onder te draaien. `execute_as_user()` zou bij ons letterlijk dezelfde
autoriteit opleveren als de agent al had — de tool-call zou identiek uitgevoerd worden,
alleen met een extra goedkeuringsstap ervoor.

Een "approval" die de autoriteit niet verandert, is geen privilege-scheiding. Het is
een bevestigingsdialoog. Dat kan waarde hebben (§4), maar het is niet het patroon dat
de kaart wil overnemen, en het moet niet zo genoemd worden.

Om het patroon écht over te nemen zou eerst een identity-laag gebouwd moeten worden:
tokens per principal, een authenticerende API, en per-principal autorisatie op elke
tool. Dat is een veelvoud van het werk van deze kaart, en §3.2 laat zien dat het
rendement daarvan negatief is voor onze situatie.

### 3.2 Het verdedigt niet tegen ons feitelijke faalmodel

Privilege-scheiding verdedigt tegen een actor die iets doet wat hij **niet mag**. Dat
veronderstelt een adversarieel of gecompromitteerd model. Onze realiteit is anders: we
draaien Anthropic's model op onze eigen repo. De schade die we daadwerkelijk hebben
opgelopen is stuk voor stuk een *toegestane* handeling die op dát moment verkeerd was.

De drie incidenten die in CLAUDE.md als gotcha zijn vastgelegd:

| Incident | Wat er gebeurde | Had approval-escalatie geholpen? |
|---|---|---|
| `pkill -f`-zelfval | `pkill -9 -f "probe4"` matchte de eigen cmdline en killde de eigen sessie | **Nee.** De agent wilde dit commando draaien en zou het hebben goedgekeurd. |
| `git stash apply` (kaart `31c30dbb…`) | Stash van een andere sessie toegepast; de `git reset --hard` die dat moest afbreken wiste 7 eigen bestanden | **Nee.** Beide commando's zijn routine; niets aan de call-site verraadt het gevaar. |
| Write naar hoofd-checkout (kaart `513e37a1…`) | `Edit` op een absoluut pad buiten de worktree, bovenop ongecommit werk | **Nee.** Een `Edit` op een `.md` is de meest alledaagse actie die er is. |

In alle drie gevallen *intendeerde* de agent de actie. Een goedkeuringsdialoog zou de
agent zijn eigen escalatie hebben laten aanvragen, en een mens die "approve" klikt op
`git reset --hard` in een worktree heeft geen informatie die de agent niet had. Sterker:
bij honderden dispatches per week zou een mens deze prompts blind wegklikken —
approval-fatigue maakt de gate netto *schadelijker* dan nuttig, want hij creëert de
illusie van een controle die er niet is.

Wat wél werkte tegen deze drie: een **statische deny-regel** (`Bash(rm:*)` in
`.claude/settings.json`), **worktree-isolatie**, en **geschreven conventies**. Alle drie
zijn contextvrij en vergen geen mens in de lus. Dat is de juiste vorm voor dit
faalmodel.

### 3.3 Eén Lemma-detail is bij ons overbodig *by construction*

De kaart noemt als afmakend detail: *"een goedgekeurde tool die faalt rapporteert de
fout terug in de run in plaats van de approval-task te laten crashen"*. Lemma heeft dat
nodig omdat approval bij hen in een **aparte task** wordt uitgevoerd — een crash daar is
een crash buiten de run.

Wij hebben die splitsing niet en zouden hem niet invoeren: in de
`--permission-prompt-tool`-vorm (§4) beslist de tool alleen *of* de call doorgaat;
Claude Code voert hem daarna zelf uit op de normale weg. Een falende goedgekeurde tool
is dan een gewoon `tool_result` met een fout, terug in de run — precies het gedrag dat
AC3 vraagt, zonder dat er iets voor gebouwd hoeft te worden. Zie §5.

---

## 4. Wat we wél zouden moeten aansluiten (AC2)

De bruikbare rest van Lemma's patroon is niet `execute_as_user`, maar het idee dat een
approval-beslissing **out-of-band naar een mens gerouteerd** kan worden en het antwoord
**terug de run in** komt zonder de run te beëindigen. Dat is exact de vorm van
`open_gate` — en exact wat `--permission-prompt-tool` verwacht.

De voorgestelde koppeling, in één zin: **wanneer `skip_permissions=False`, dispatcht
Cockpit met `--permission-prompt-tool` gericht op een MCP-tool die een `KanbanGate`
opent en op het antwoord wacht.**

Dat is geen nieuw vraagmechanisme — het is een nieuwe *producer* van het bestaande
`KanbanGate`-primitief. AC2's eis ("geen twee overlappende vraagmechanismen") wordt
daarmee gehaald, mits de rolverdeling scherp blijft:

| Mechanisme | Wanneer | Levensduur sessie |
|---|---|---|
| `report_impediment` | **Productbeslissing / scope-fork** — wat moeten we bouwen, welke trade-off. De mens beslist op eigen tempo; de kaartcontext is goedkoop te herstellen uit de kaarttekst. | Sessie eindigt, claim vrij |
| `KanbanGate` via `--permission-prompt-tool` | **Permissiebeslissing** — mag deze ene tool-call nu draaien. Mid-run, warme context, alleen zinvol te beantwoorden mét die context. | Sessie blijft, run gaat door |
| Statische `allow`/`deny`-regels | **Contextvrij oordeel** — `Bash(rm:*)` is altijd fout, ongeacht wanneer. | N.v.t., geen mens in de lus |

De derde rij is belangrijk en is de reden dat dit géén brede goedkeuringsstroom moet
worden. **De default hoort statisch te zijn.** Alleen wat contextafhankelijk is en
tegelijk zeldzaam genoeg om approval-fatigue te vermijden, hoort naar een gate. Een
werkbare startafbakening voor product-projecten:

- **Statisch `deny`** — destructieve, onomkeerbare host-operaties (`rm`, `sudo`,
  schrijven buiten de projectroot, `git push --force`). Nooit een vraag; een
  `deny`-antwoord is altijd het juiste.
- **Gate (`ask`)** — netwerk-egress naar een host buiten de allowlist, en het lezen van
  credential-paden buiten het project (`~/.aws`, `~/.ssh`, `~/.claude/.credentials.json`).
  Contextafhankelijk, zeldzaam, en precies de klasse waar een mens iets zinnigs kan
  toevoegen.
- **Statisch `allow`** — al het overige binnen de projectroot. Dat is het leeuwendeel;
  zonder deze regel wordt elke dispatch onwerkbaar.

Voor `meta` verandert er niets: `skip_permissions` blijft `True`, er wordt geen
`--permission-prompt-tool` meegegeven, en er is dus geen gate-verkeer.

### 4.1 De zelf-goedkeuringsvraag is bij ons reëel en niet oplosbaar in deze kaart

Lemma's tweede afmakende detail — *`request_approval` mag zichzelf niet goedkeuren* —
heeft bij ons een scherpere vorm dan in het origineel.

`POST /api/v1/kanban/gates/{gate_id}/answer` (`router.py:1131`) beantwoordt een gate. De
API is default onauth (§3.1), en de agent heeft shell-toegang. Een agent kan dus zijn
eigen gate beantwoorden met één `curl`. Het is bewust géén MCP-tool — `answer_gate`
staat niet in de MCP-toolset — wat een eerlijke ontwerp-intentie laat zien, maar dat is
een drempel, geen grens.

Dit is **niet oplosbaar zonder de identity-laag uit §3.1**, en die bouwen we niet.
Consequentie die expliciet opgeschreven moet worden: de gate is een
**vergissings-barrière, geen veiligheidsgrens**. Hij beschermt tegen een agent die per
ongeluk iets riskants doet, niet tegen een agent die de gate wil omzeilen. Voor ons
faalmodel (§3.2) is dat precies de juiste ambitie — maar het mag nergens als
security-control worden gepresenteerd, want dan gaat iemand erop bouwen.

---

## 5. Gedrag bij afwijzing en bij een falende goedgekeurde actie (AC3)

Vier paden, met het vereiste gedrag:

1. **Goedkeuring.** De tool geeft `allow` terug; Claude Code voert de call normaal uit.
   De run merkt niets bijzonders.
2. **Afwijzing.** De tool geeft `deny` terug met de reden van de mens. Claude Code
   levert dat als tool-fout af bij het model. **De run loopt door** — het model past
   zich aan, kiest een andere route, of escaleert alsnog via `report_impediment` als het
   er echt niet uitkomt. Een afwijzing mag nooit de sessie beëindigen; dat zou de
   grofheid herintroduceren die deze hele analyse probeert te vermijden.
3. **Timeout.** Hier moet bewust van `open_gate` worden afgeweken. `open_gate` geeft na
   30 min `{"error": "timeout"}` en laat de gate open. Voor een permissievraag is dat
   het verkeerde eindpunt: er moet een beslissing terug. Het moet **fail-closed** naar
   `deny` met een expliciete "niemand heeft binnen X geantwoord"-reden, zodat pad 2
   geldt en de run doorloopt in plaats van te stallen. De timeout hoort korter dan
   30 min, want een stilstaande sessie houdt een worktree en een claim vast.
4. **Goedgekeurde actie faalt.** Geen speciale afhandeling nodig — zie §3.3. Claude Code
   voert de tool zelf uit, dus een fout is een gewoon `tool_result` met een fout in de
   run. De crash-modus die Lemma moest dichttimmeren bestaat bij ons niet, omdat we hun
   task-splitsing niet overnemen.

Alle vier de paden delen één invariant, en die is het echte acceptatiecriterium:
**geen enkel permissiepad mag een sessie doen stallen of sterven.** Vandaag faalt dat
op pad 3 (er is geen antwoordkanaal, dus alles is een oneindige timeout) en dat is wat
kaart §6.1/§6.2 repareert.

---

## 6. Vervolgkaarten

Aangemaakt in deze sessie als kind-kaarten van `38d32e94…`. Onafhankelijk van elkaar op
één contract na: §6.2 consumeert de meting uit §6.1.

### 6.1 Meten wat een product-dispatch doet met `skip_permissions=False`
De conclusie in §2.4 is afgeleid, niet gemeten. Eén gecontroleerde dispatch van een
wegwerp-product-project met het profiel-default (`skip_permissions=False`,
`transport=sandcastle`) legt vast wat er feitelijk gebeurt bij de eerste `ask`-tool:
stalt de pane, faalt de spawn, of gaat het door. Bewust een meting vóór een fix — als
het gedrag anders is dan afgeleid, verandert dat de scope van §6.2.

### 6.2 `--permission-prompt-tool` bedraden op het bestaande `KanbanGate`-primitief
Het antwoordkanaal uit §4: een MCP-tool die een gate opent, wacht, en `allow`/`deny`
teruggeeft, plus de dispatch-flag die 'm activeert wanneer `skip_permissions=False`.
Inclusief de vier paden uit §5, met fail-closed timeout. Hangt af van §6.1.

### 6.3 De load-bearing override-rijen zichtbaar maken
§2.3: deze repo draait permissief dankzij twee `kanban_meta`-rijen die een
security-profiel overstemmen dat het tegenovergestelde zegt, zonder waarschuwing of
test. Minimaal: een check die signaleert wanneer een override een profiel tegenspreekt.
Bewust géén automatische herclassificatie naar `risk_class=meta` — dat is
`SecurityProfileService` follow-up #12 en heeft zijn eigen scope.

---

## 7. Bewust géén kaart

- **Identity-laag / twee principals.** De voorwaarde voor Lemma's letterlijke patroon.
  Niet bouwen: §3.2 laat zien dat het rendement voor ons faalmodel negatief is
  (approval-fatigue op een gate die de autoriteit niet verandert). Heropenen wanneer
  Cockpit multi-user wordt of agents van derden gaat draaien — dán verandert het
  faalmodel van "vergissing" naar "mogelijk adversarieel" en kantelt deze afweging.
- **Statische deny/allow-regelset per risk_class uitschrijven.** §4 schetst de
  afbakening, maar de concrete regelset hoort bij de facet-D-kaarten over
  `ProjectSecurityPolicy`, niet hier — anders ontstaan er twee plekken die permissie-
  beleid definiëren.
- **`open_gate` afschaffen of hernoemen.** Verleidelijk na §2.1, maar het is een
  werkend primitief met precies de juiste semantiek voor §6.2. De conventie die agents
  naar `report_impediment` stuurt blijft correct voor productbeslissingen.

---

## 8. Licentie

Het origineel (`lemma-backend/app/modules/agent/tools/approval/executor.py`) is
**AGPLv3**. Er is geen Lemma-code gelezen, overgenomen of geparafraseerd op codeniveau
voor dit document; alle beschrijvingen van Lemma's gedrag komen uit
`lemma-platform-analyse.md` §4.2, dat zelf een idee-niveau-samenvatting is. De
uiteindelijke aanbeveling (§4) leunt bovendien niet op Lemma's ontwerp maar op Claude
Code's eigen `--permission-prompt-tool`-mechanisme en ons bestaande `KanbanGate` —
convergente vorm, onafhankelijke herkomst.
