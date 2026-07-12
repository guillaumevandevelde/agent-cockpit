# ProjectBootstrapPolicy — de "cockpit-defaults" van repo-bootstrap

> Kanban-kaart: **`[design][bootstrap] ProjectBootstrapPolicy — cockpit-defaults
> configuratie ontwerp`** (facet-B follow-up #5 uit
> `repo-provisioning-bootstrap.md` §6). `work_type=analysis`.
>
> Deze doc is **design-only**. Ze centraliseert de beleids-toggles uit
> `repo-provisioning-bootstrap.md` §4.3 zodat de implementatie-kaarten van facet B
> niet elk hun eigen aannames maken. Het prototype
> `backend/app/services/bootstrap_policy.py` is een **typed-dict / dataclass zonder
> runtime-gebruik**: het wordt door geen enkele productie-module geïmporteerd totdat
> de bootstrap-implementatie-kaarten landen. Het is puur het reconciliatiepunt
> waarop die kaarten zich uitlijnen.

## 0. Waar dit in de keten past

De bootstrap-keten (`repo-provisioning-bootstrap.md` §3.1) is zes stappen breed:
`mkdir → git init + first commit → blueprint-apply → register → autodispatch-toggle →
carry-over-card`. Elke stap draagt een of meer **beleidskeuzes** in zich (welke
`.gitignore`, welke first-commit-inhoud, autodispatch aan of uit, …). Die keuzes
horen op één plek — `BootstrapPolicy` — en niet versnipperd door
`RepoBootstrapService`, `TemplateService` en de blueprint-apply-engine. §3.2 noemt dit
expliciet: *"Policy-keuzes aan oppervlak … komen terecht in één
`ProjectBootstrapPolicy`-config, niet versnipperd door de code."*

`BootstrapPolicy` bevat **defaults**. De aanroeper (in de praktijk facet A's
`create_project_from_intake`) mag per-project overriden — de policy is de
veilige bodem, niet een keurslijf.

## 1. De zeven beslissingen

Elke beslissing hieronder heeft dezelfde vorm: **Aanbeveling → Onderbouwing →
Alternatieven + waarom niet → Consumerende kaart(en)**. De consumerende-kaart-matrix
staat samengevat in §3.

### 1.1 Autodispatch-default bij geboorte — **UIT**

**Aanbeveling.** `autodispatch_default = False`. Een splinternieuw project start met
autodispatch **uit**. De standaard-intake-flow (facet A, `create_project_from_intake`)
mag 'm expliciet op `True` zetten omdat op dát moment een mens de intake zojuist heeft
goedgekeurd — de mens-in-de-lus blijft dan intact.

**Onderbouwing.** Security-default-deny. Autodispatch-aan betekent dat agents meteen
in een verse repo gaan committen/pushen zonder dat er ook maar één menselijke review
op de repo-inhoud heeft plaatsgevonden. De platform-doelstelling (`CLAUDE.md`)
schrijft voor: *"Respecteer de ingestelde autonomiegrenzen en vraag goedkeuring voor
acties die buiten deze grenzen vallen."* Default-uit respecteert die grens; de
friction om 'm aan te zetten is één toggle (`set_autodispatch`, bestaat al —
`dispatch.py:174`). Door de default in de dataclass conservatief te houden maar de
mens-goedgekeurde intake-flow expliciet te laten opt-in-en, betalen we **geen** echte
friction in het reële pad én blijven we veilig voor toekomstige aanroepers die géén
menselijke goedkeuring achter zich hebben (bv. een batch-import).

**Alternatieven + waarom niet.**
- *Default-aan ("direct productief").* Verleidelijk omdat de geboorte ná een
  goedgekeurde intake gebeurt — er ís een mens in de lus. Maar de policy-default geldt
  voor élke aanroeper, niet alleen de intake-flow; default-aan zou een niet-intake-pad
  ongewild autonoom maken. We verplaatsen de "aan" liever naar de expliciete,
  mens-goedgekeurde call.
- *Aan bij eerste Backlog-kaart i.p.v. bij geboorte.* Introduceert een impliciete,
  moeilijk te traceren trigger ("waarom draait dit project ineens?"). Een expliciete
  toggle is transparanter — en transparantie is een kern-doelstelling.

**Consumeert:** de autodispatch-toggle-stap (§3.1 stap 4) in de atomic-init/orchestratie-kaart
`dca8c8dc30d0` (RepoBootstrapService atomic-init foundation) en, downstream, de
blueprint-apply-engine (facet-B follow-up #4, nog te filen).

### 1.2 Permission-mode-default — **geen per-project override schrijven (erf de dispatch-default)**

**Aanbeveling.** `permission_mode = None`. Bootstrap schrijft bij geboorte **geen**
`skip_permissions:<key>`-`KanbanMeta`-rij. Dispatch heeft al een eigen default:
`get_skip_permissions` retourneert `True` (bypass) wanneer er geen rij is
(`dispatch.py:195`), en dispatched sessies draaien in een **wegwerp-worktree**. Het
veld accepteert wél de drie expliciete waarden (`default` / `acceptEdits` /
`bypassPermissions`) voor een veiligheidsbewuste operator die het tóch wil pinnen.

**Onderbouwing.** Twee redenen om niets te schrijven. (1) *Blast-radius is al
begrensd:* dispatched worktree-sessies leven in een geïsoleerde, wegwerp-checkout —
`--dangerously-skip-permissions` dáárbinnen raakt niet de machine van de gebruiker.
(2) *Geen drift:* een expliciete geboorte-rij die de dispatch-default dupliceert, gaat
uit de pas lopen zodra die dispatch-default ooit verandert. Bovendien is deze keuze
grotendeels moot zolang beslissing 1.1 (autodispatch-uit) geldt: er draait niets
totdat de mens autodispatch aanzet. De veiligheids-posture wordt dus door 1.1 gedragen,
niet door een permission-rij.

**Alternatieven + waarom niet.**
- *`bypassPermissions` expliciet vastschrijven.* Redundant met de dispatch-default en
  drift-gevoelig (zie boven).
- *`default` (conservatief, elke edit vraagt goedkeuring).* Zou elke autonome
  worktree-sessie blokkeren op permission-prompts die niemand beantwoordt → sessies
  hangen. Verkeerd voor een autonoom platform; de isolatie van de worktree maakt het
  bovendien onnodig streng.

**Consumeert:** de autodispatch-/skip-permissions-toggle-stap (§3.1 stap 4) in
`dca8c8dc30d0`; de per-blueprint permission-mode-kolom in de cockpit-baseline-blueprint
(facet-B follow-up #6, nog te filen) en in de blueprint-typologie
`6f124eae33c4` (facet A).

### 1.3 Eerste-commit-inhoud — **de gerenderde template (nooit leeg, nooit het plan-attachment)**

**Aanbeveling.** `first_commit_content = "template"`,
`first_commit_message = "chore: bootstrap {project_name} from intake {intake_card_id}"`.
De eerste commit legt de **volledig gerenderde template-boom** vast — voor het
`empty`-template is dat minimaal `.gitignore` + `README.md`; getypeerde templates
voegen hun eigen skelet toe. De `.claude/`-seed + `CLAUDE.md`-stub komen uit
blueprint-apply (§3.1 stap 2) en mogen in dezelfde commit of een tweede commit landen
(zie onderbouwing).

**Onderbouwing.** De drie constraints uit de kaart worden alledrie tevredengesteld:
*(a)* worktree-mode kan branchen (er is een commit); *(b)* CI-checks vinden een README;
*(c)* agents vinden een `CLAUDE.md` (via blueprint-apply). Een gerenderde template
levert per definitie ≥ `.gitignore` + `README` (het `empty`-template garandeert dat
minimum al — `backend/app/services/templates/empty/`). Praktische nuance rond het
aantal commits: de **deterministische, offline** delen (template-render, README,
LICENSE, `CLAUDE.md`-stub) horen in de bootstrap-commit; **niet-deterministische**
blueprint-stappen die het netwerk raken (`install_skill` → `npx skills add`, zie
`repo-provisioning-bootstrap.md` §5.1) horen liefst in een *tweede* commit, zodat een
netwerk-fout de atomaire eerste commit niet kan laten mislukken. De implementatie-kaart
beslist het exacte snijpunt; de policy schrijft alleen voor: **eerste commit ≠ leeg**.

**Alternatieven + waarom niet.**
- *Lege commit (`git commit --allow-empty`).* Technisch branchbaar, maar levert een
  repo op zonder README (CI-onvriendelijk) en zonder enige oriëntatie voor een mens die
  'm opent. Geen winst t.o.v. een README-stub, die triviaal goedkoop is.
- *Eerste commit bevat het plan-attachment.* Koppelt de repo-historie aan
  kanban-interne artefacten: het plan leeft op de kaart en kan gewijzigd worden
  (`add_plan_attachment` opnieuw) — dan zou de commit "verouderd plan" bevatten met een
  regeneratie-probleem. Het plan hoort op de kaart, niet in de git-boom.

**Consumeert:** de git-init-+-first-commit-stap (§3.1 stap 1) in de atomic-init-kaart
`dca8c8dc30d0`; het `{project_name}`/`{intake_card_id}`-substitutiepatroon leunt op het
`{{ var }}`-render-mechanisme dat `TemplateService` al levert (`5512a442da8d`, Done).

### 1.4 `.gitignore`-profiel — **template-specifiek; policy levert alleen een fallback**

**Aanbeveling.** `gitignore_fallback = <de bestaande `empty`-kitchen-sink>`.
Het `.gitignore` **hoort bij het template**, niet bij de policy. Elk getypeerd template
levert zijn eigen stack-passende `.gitignore` (bestaat al — `TemplateService` rendert
`*/.gitignore.tmpl`). De policy houdt alleen een **fallback** aan voor het geval een
template er geen meelevert; die fallback is exact het huidige gecombineerde
Python+Node+editor/OS-profiel uit `empty/.gitignore.tmpl`.

**Onderbouwing.** Een `python-fastapi`-repo heeft geen `node_modules/` nodig; een
`react-vite`-repo geen `__pycache__/`. Het template weet precies welke artefacten zijn
stack produceert — dat is de juiste eigenaar van "wat te negeren". De kitchen-sink is
prima voor `empty` (onbekende stack) maar zou als universele policy-forcing de
getypeerde templates vervuilen met irrelevante regels. Dit sluit 1-op-1 aan op de
al-gebouwde `TemplateService`, die per-template `.gitignore` al ondersteunt — de policy
hoeft alleen te **deferren**.

**Alternatieven + waarom niet.**
- *Eén universeel git-stock+Python+Node+IDE-profiel als policy.* Forceert irrelevante
  regels op getypeerde templates en centraliseert een keuze die logisch bij het
  template thuishoort. Drift tussen "policy-ignore" en "template-ignore" ligt op de
  loer.
- *Policy negeert `.gitignore` volledig.* Zou `empty` (dat géén stack heeft) zonder
  vangnet laten; de fallback dekt dat randgeval af.

**Consumeert:** `TemplateService` (`5512a442da8d`, Done — levert per-template
`.gitignore`); de fallback wordt geraadpleegd door de atomic-init-kaart `dca8c8dc30d0`
(§3.1 stap 1) enkel wanneer het gekozen template er geen meelevert.

### 1.5 CI-bootstrap — **niet meeleveren bij geboorte; uitgesteld naar facet D**

**Aanbeveling.** `ci_bootstrap = False`. Bootstrap kopieert **geen**
`.github/workflows/quality.yml` uit claude-cockpit. CI is een bewuste, latere opt-in
via facet D's `CITemplateService` (Backlog-kaart `c66a93a20c0a`).

**Onderbouwing.** `quality.yml` in deze repo is cockpit-specifiek (ruff+pytest-paden,
`frontend/`-lint/build, verwijzingen naar déze repo-structuur). Kopiëren levert N
verouderde kopieën op die nooit de upstream-verbeteringen krijgen — precies het
drift-risico dat §4.3.4 benoemt. De beslissing "apart facet-D-traject" is bovendien al
geactioneerd: er bestáát al een facet-D-kaart (`c66a93a20c0a`,
`[feature][D] CITemplateService + drie GitHub-Actions-templates`) die versioneerde,
opt-in CI-templates gaat leveren. `BootstrapPolicy` hoeft hier dus enkel de default
"uit" vast te leggen en naar die kaart te verwijzen.

**Alternatieven + waarom niet.**
- *`quality.yml` direct kopiëren.* Drift (zie boven) + het injecteert cockpit-eigen
  aannames (Python+Node monorepo) in projecten die dat niet zijn.
- *Zelf een generieke CI-template in de policy bakken.* Dubbelt het werk van de
  bestaande facet-D-`CITemplateService`-kaart; twee motoren voor dezelfde stap — precies
  wat de facet-grenzen (`§2.4`) willen vermijden.

**Consumeert:** facet-D-kaart `c66a93a20c0a` (`CITemplateService`) is de plek waar
CI-bootstrap landt; deze policy-vlag is het aanhechtingspunt (zet 'm op `True` zodra
`CITemplateService` beschikbaar is en het project CI wil).

### 1.6 License — **MIT als default, met escape-hatch naar geen/andere licentie**

**Aanbeveling.** `license = "MIT"`, `copyright_holder = None` (afgeleid uit git-config
of policy-override op render-tijd). Het veld accepteert elke SPDX-id én `None`
("geen `LICENSE`-bestand schrijven") voor propriëtaire/interne projecten.

**Onderbouwing.** MIT is de minst-restrictieve, breedst-compatibele permissieve
licentie: veilige default voor greenfield-projecten, geen copyleft-verplichtingen die
een gebruiker kunnen verrassen. De escape-hatch (`None`) dekt af dat een product dat
óp cockpit gebouwd wordt niet noodzakelijk open-source wil zijn. MIT vereist een
copyright-houder-naam; die komt uit `copyright_holder` (policy-override) of valt terug
op `git config user.name` op render-tijd — de implementatie-kaart documenteert de
placeholder.

**Alternatieven + waarom niet.**
- *Apache-2.0.* De expliciete patent-grant is netjes, maar de zwaardere
  NOTICE/boilerplate-verplichtingen zijn overkill voor MVP-starters. Blijft een geldige
  opt-in via het SPDX-veld.
- *GPL / copyleft.* Viraal; verkeerde default voor willekeurige gebruikersproducten die
  we niet aan copyleft willen binden.
- *Helemaal geen licentie als default.* "Geen licentie" = wettelijk "all rights
  reserved", wat hergebruik blokkeert — een slechte *default*, maar wél een geldige
  *opt-out* (`None`), die we daarom als escape-hatch behouden i.p.v. als default.

**Consumeert:** de first-commit-/starter-content-stap (§3.1 stap 1) in de
atomic-init-kaart `dca8c8dc30d0` (schrijft het `LICENSE`-bestand naast `.gitignore`/README).

### 1.7 Project-key-collision-strategie — **suffix-counter op slug-niveau (facet C maakt 'm globaal)**

**Aanbeveling.** `key_collision_strategy = "suffix-counter"`. Wanneer twee projecten op
hetzelfde device pre-remote dezelfde `slug:<naam>` zouden krijgen, disambigueert
bootstrap met een numerieke suffix: `slug:my-app`, `slug:my-app-2`, … De directory-basename
krijgt dezelfde suffix, zodat pad en key uitgelijnd blijven. Dit is een pre-flight
vóór `mkdir`.

**Onderbouwing.** Collision is uitsluitend een **pre-remote / remote-loos** probleem:
zodra `gh repo create` + `git remote add origin` slaagt, krijgt het project een unieke
`git:host/path`-key (zie de al-geïmplementeerde `create_remote` in
`repo_bootstrap_service.py` + de key-migratie). Keys zijn mensleesbaar en afgeleid van
`resolve_project_key` (basename-slug); een numerieke suffix houdt ze leesbaar en
deterministisch, en spiegelt hoe filesystems zelf al disambigueren ("map (2)"). De
suffixte slug migreert later schoon naar zijn git-key via de key-migratie-helper (zie
consumerende kaart). Dit is een **mini-overlap met facet C**: we documenteren de
strategie hier; facet C (portfolio) maakt 'm cross-device autoritair via een
portfolio-registry die keys reserveert. Escape-hatch: als de gebruiker een *expliciete*
gewenste naam opgaf die botst, surface dat (impediment/prompt) i.p.v. stil te suffixen;
het **automatische** default-gedrag is stil suffixen.

**Alternatieven + waarom niet.**
- *Per-device namespace-prefix (`dev:<hostname>/my-app`).* Lekt device-identiteit in de
  key, is lelijk, en is bovendien overbodig zodra de repo een remote krijgt en tóch naar
  de git-key overschakelt.
- *Content-hash-suffix (`slug:my-app-a3f1`).* Onleesbaar; verslaat het mensgerichte doel
  van slugs.

**Consumeert:** de key-migratie-helper `43974135dd92`
(`[feature][bootstrap] KanbanMeta key-migratie helper (migrate_project_keys)`) verzorgt
de slug→git-rename op remote-add; de gh-remote-flow `b11040272c24` (Done) roept die
migratie al aan. Facet C consumeert de strategie voor cross-device-uniciteit.

## 2. Het prototype: `BootstrapPolicy`-dataclass

Locatie: **`backend/app/services/bootstrap_policy.py`** — bewust een *apart* bestand,
niet in `repo_bootstrap_service.py` (dat ís al productie-code, geïmporteerd door de
key-migratie-hook). Het prototype wordt door **geen enkele** productie-module
geïmporteerd totdat de implementatie-kaarten landen; het compileert als valide types en
fungeert enkel als het typed reconciliatiepunt.

Elk veld mapt op precies één beslissing:

| Veld | Type | Default | Beslissing |
|---|---|---|---|
| `autodispatch_default` | `bool` | `False` | 1.1 |
| `permission_mode` | `Literal["default","acceptEdits","bypassPermissions"] \| None` | `None` | 1.2 |
| `first_commit_content` | `Literal["template","empty"]` | `"template"` | 1.3 |
| `first_commit_message` | `str` | `"chore: bootstrap {project_name} from intake {intake_card_id}"` | 1.3 |
| `gitignore_fallback` | `str` | *kitchen-sink uit `empty/.gitignore.tmpl`* | 1.4 |
| `ci_bootstrap` | `bool` | `False` | 1.5 |
| `license` | `str \| None` | `"MIT"` | 1.6 |
| `copyright_holder` | `str \| None` | `None` | 1.6 |
| `key_collision_strategy` | `Literal["suffix-counter","reject"]` | `"suffix-counter"` | 1.7 |

## 3. Consumerende-kaart-matrix

| Beslissing | Consumerende facet-B-kaart(en) | Status |
|---|---|---|
| 1.1 Autodispatch-default | `dca8c8dc30d0` (atomic-init/orchestratie, §3.1 stap 4) + blueprint-apply-engine (follow-up #4, nog te filen) | Backlog / nog te filen |
| 1.2 Permission-mode | `dca8c8dc30d0` (stap 4); cockpit-baseline-blueprint (follow-up #6, nog te filen); `6f124eae33c4` blueprints-typologie (facet A) | Backlog / nog te filen |
| 1.3 First-commit-inhoud | `dca8c8dc30d0` (stap 1); `5512a442da8d` TemplateService (render-mechanisme) | Backlog / **Done** |
| 1.4 `.gitignore`-profiel | `5512a442da8d` TemplateService (per-template `.gitignore`); `dca8c8dc30d0` (fallback) | **Done** / Backlog |
| 1.5 CI-bootstrap | `c66a93a20c0a` CITemplateService (facet D) | Backlog |
| 1.6 License | `dca8c8dc30d0` (schrijft `LICENSE` in first-commit-stap) | Backlog |
| 1.7 Key-collision | `43974135dd92` key-migratie-helper; `b11040272c24` gh-remote-flow (roept migratie aan) | Backlog / **Done** |

> **Noot over follow-up #1 (`dca8c8dc30d0`).** Deze kaart heet formeel
> `[self-improve] Facet-B bootstrap follow-up #1 (RepoBootstrapService atomic-init) was
> never filed` — ze staat er omdat de atomaire grondsteen
> (mkdir+git init+first commit+`.gitignore`+README, `repo-provisioning-bootstrap.md`
> §6.1) nog niet als feature-kaart bestond. Zij is de grootste consument van deze policy
> (beslissingen 1.1, 1.2, 1.3, 1.4, 1.6). Wie 'm oppakt: lees deze doc als contract.

## 4. Bewust buiten scope

- **Implementatie.** De runtime-`BootstrapPolicy` aan een config-file / DB koppelen,
  de policy in `RepoBootstrapService` inbedden, tests op policy-resolutie — dat is de
  implementatie-kaart die dit prototype consumeert (`repo-provisioning-bootstrap.md`
  §5.2 noemt `BootstrapPolicy` + unit-tests op policy-resolutie als facet-B-bijdrage).
- **Blueprint-datamodel + typologie.** Welke agents/skills een blueprint bevat is facet
  A (`6f124eae33c4`, `blueprints-typology.md`); deze policy raakt alleen de
  permission-mode-*default*, niet de blueprint-inhoud.
- **Portfolio-cap / cross-device key-uniciteit.** Facet C; deze doc legt alleen de
  per-device suffix-strategie vast (1.7).
