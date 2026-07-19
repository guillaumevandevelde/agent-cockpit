---
title: "Terugkerende cadans voor het zelfverbeteringsonderzoek — voorstel"
type: analysis
status: proposed
---

# Terugkerende cadans voor het zelfverbeteringsonderzoek — voorstel

> Companion van kanban-kaart
> *"Terugkerende cadans voor het zelfverbeteringsonderzoek voorstellen"* (id
> `7ae60646…`) en van de `market-research`-skill
> (`.claude/skills/market-research/SKILL.md`). Doel: de skill die de kind-kaart
> *"Herbruikbare skill voor markt-/zelfverbeteringsonderzoek"* heeft opgeleverd,
> **wekelijks autonoom laten draaien** zonder dat een mens iedere keer handmatig
> triggert, met expliciete pauze- en override-mechanismen.

---

## 1. Status van de onderliggende infrastructuur

| Bouwsteen | Status (per 2026-07-09) | Bruikbaar NU? |
|---|---|---|
| **`market-research` skill** | ✅ aanwezig op `master` (commit `87c8e8c`); **Step 7** (chain-of-one-shots) toegevoegd in dezelfde commit-cyclus als dit voorstel | ✅ |
| **Kanban `scheduled_at` + auto-dispatch** | ✅ aanwezig op `master` (commit `328e115`); `_is_due()` in `backend/app/kanban/dispatch.py:930` respecteert de future-time gate | ✅ |
| **Per-project autodispatch opt-in** | ✅ aanwezig (`KanbanMeta` key `autodispatch:<project_key>`, device-local; zie `docs/cockpit/kanban-dispatch-spec.md:31-34`). **Vereist** eenmalig inschakelen voordat de keten start — zie §7. | ⚠️ stap #1 in §7 |
| **Scheduled-messages feature (fase 2)** | ✅ code-compleet (Tasks 1–11 per `docs/cockpit/00-orientation.md:21-25`); ⬜ Task 12 = runtime e2e, vergt `docker compose up -d` + `claude`-login (twee handmatige stappen van de gebruiker) | ⬜ **wacht op Task 12** |
| **Harness `/loop` / `/schedule`** (Claude Code built-in: `CronCreate`, `ScheduleWakeup`) | ✅ aanwezig | ⚠️ bruikbaar als noodpad — sessie-only |

**Conclusie:** we hoeven **niets te bouwen** om een wekelijkse cadans operationeel te
krijgen — alle benodigde onderdelen zijn al productie-af (de skill met Step 7, de
scheduled_at-gate, het per-project opt-in) of worden dat met de eerste commit die
de trigger-kaart aanmaakt plus de één-regel opt-in-call.

---

## 2. Gekozen aanpak — gelaagd

Drie mechanismen liggen klaar; ze zijn niet concurrent maar **opeengestapeld** —
elk vangt een ander geval af:

| Mechanisme | Rol | Wanneer actief |
|---|---|---|
| **A. Kanban-trigger-kaart met `scheduled_at` + Step 7 in de skill** | **Primair.** Eén kanban-kaart per run, met de `market-research`-skill als openingsprompt. Wekelijks vuurt de auto-dispatch-tick 'm af; de gedispatchte sessie **maakt zelf zijn opvolger** als laatste stap via Step 7 (chain-of-one-shots). | Nu — direct operationeel. |
| **B. Scheduled-messages (fase 2)** | **Evolutie.** Vervangt de keten van wekelijkse spawns door één lang-levende "analyst"-sessie die via `tmux send-keys` elke maandag een "voer market-research uit"-injectie krijgt. Bespaart spawn-overhead en houdt sessiecontext warm. | Activeer zodra fase-2 Task 12 groen is. Expliciet gevolgd door follow-up Backlog-kaart *"Migrate weekly market-research trigger to scheduled-messages"* (id `a4d9f8b6…`). |
| **C. Harness `/loop` + `CronCreate`** | **Noodpad / handmatig testen.** Als zowel A als B om een of andere reden niet beschikbaar zijn (bv. backend uit de lucht), kan de mens in een *interactieve* Claude-sessie `market-research` draaien met een ScheduleWakeup-loop. | Alleen voor ad-hoc; nooit als productie-pad. |

**Waarom gelaagd en niet "kies één":** A werkt vandaag al, maar spawnt elke week een
nieuwe sessie. B is daar een optimalisatie van, niet een vervanging — de `scheduled_at`-
mechaniek blijft de ruggengraat (het vult nog steeds de Backlog met onderzoekskaarten).
C is er voor het geval de backend uit ligt. De drie vullen elkaar aan in
robuustheid, niet in functionaliteit.

---

## 3. Cadans

**Standaard: wekelijks, elke maandag 09:00 Europe/Brussels.**

| Aspect | Keuze | Motivatie |
|---|---|---|
| **Frequentie** | 1×/week | Snel genoeg om een release in `claude-task-master` / `claude-deck` / Anthropic-SDK binnen een week op te merken; traag genoeg dat een no-finding run niet weggegooid moeite is (zie market-research Step 3 — "zero-finding is legitimate"). |
| **Tijdstip** | Maandag 09:00 Europe/Brussels | Begin van de werkweek, na eventuele weekend-releases; geeft het team de week om te reageren op de Backlog-kaarten die eruit vallen. |
| **Tijdzone** | Europe/Brussels | Past bij de bestaande scheduled-messages-default (`backend/app/models/scheduled_message.py`); geen verrassing als B ooit actief wordt. |
| **Zelf-corrigerend?** | Ja — Step 7 berekent de volgende `scheduled_at` als "next Monday 09:00 *nu*", niet "vorige scheduled_at + 7 dagen". Zo herstelt de keten zich automatisch na een gemiste run i.p.v. 7 dagen vooruit te driften. | Voorkomt stille drift over tijd; één vergeten run catastrofe wordt geen structureel probleem. |
| **Configurabel?** | Ja, per-kaart via `scheduled_at` | De mens kan elke willekeurige kaart op een ander tijdstip zetten; eenmalige runs (bv. "vóór de volgende major release") zijn een natuurlijk neveneffect. |

**Bij groeiende werkdruk of dalend nut → maandelijks** (eerste maandag van de maand).
De skill-output zelf (1–3 Backlog-kaarten per run) is het signaal: als 3 opeenvolgende
runs elk ≤ 1 bruikbare kaart opleveren, is de cadans te krap — een mens kan de
frequentie aanpassen door de trigger-kaart op een maandelijks patroon te zetten
(zie § 4 voor het mechanisme).

---

## 4. Trigger-mechanisme (mechanisme A in detail)

### 4.1 De trigger-kaart — anatomie

Eén Backlog-kaart met deze structuur. **Alle velden zijn verplicht** — de dispatcher
èn Step 7 hebben ze nodig.

```yaml
title:          "[research] Weekly market-research sweep"
description:    |
  Voer de market-research skill uit volgens .claude/skills/market-research/SKILL.md.
  …
  **Step 7 — schedule the next run (chain-of-one-shots).** Voordat je Done zegt op
  deze kaart: maak een opvolger-kaart met dezelfde anatomie als §4.1 van
  docs/cockpit/recurring-cadence-proposal.md, met `scheduled_at` = eerstvolgende
  maandag 09:00 Europe/Brussels en dezelfde `parent_card_id`. Zie de skill voor
  details. Doe dit onvoorwaardelijk — ook bij zero-finding.
project_key:    git:github.com/guillaumevandevelde/claude-cockpit   # geresolved via MCP, NOOIT gokken
parent_card_id: 3f8ccfab70f44672908a8b1559754148                    # het Self-improvement analysis-card; anders verliest de opvolger de parent-linkage in de activity feed
column:         Backlog                                              # _DISPATCH_COLUMNS = ("Backlog", "To Resume")
work_type:      analysis                                              # routing-meta, niet hetzelfde als agent
agent:          claude-code                                           # expliciet; voorkomt dat work_type-mapping in de toekomst verandert
scheduled_at:   2026-07-13T09:00:00+02:00                             # eerstvolgende maandag
labels:         [research, recurring, autonomous]
```

`scheduled_at` vult de bestaande gate in `backend/app/kanban/dispatch.py:930-945`:
`_is_due()` retourneert `True` zodra `fire_at <= now`, en de dispatch-tick (10 s)
claimt 'm dan als eerste onopgeëiste kaart uit `Backlog` (of `To Resume`). De claim
zet de kaart op `Doing`; een werkende tmux-sessie spawned.

**Spawn-failure pad** (per `docs/cockpit/kanban-dispatch-spec.md` Flow step 8):
als de spawn faalt — bijv. `tmux` niet beschikbaar of `claude` niet ingelogd
(`docs/cockpit/00-orientation.md:34`) — wordt de claim vrijgegeven en de kaart
terug naar `Backlog` gezet. De kaart is reusable de volgende tick. **Cruciaal:**
als dit bij de eerste run gebeurt, breekt de keten: Step 7 is dan niet uitgevoerd,
er is geen opvolger. Een mens moet de eerste trigger handmatig opnieuw dispatchen
of de kaart opnieuw aanmaken — een "silent dead chain"-risico waar §5 §6 expliciet
op wijzen.

### 4.2 De recursie — hoe de keten blijft lopen

De **`market-research`-skill** zelf heeft nu **Step 7** (zie
`.claude/skills/market-research/SKILL.md` regel ~169, in dezelfde commit-cyclus
als dit voorstel). De **laatste stap van elke run** is een opvolger-kaart
aanmaken via Step 7, inclusief:

1. Resolve het project-key via Step 1 (NOOIT gokken — Step 4 van de skill).
2. Bereken `next_scheduled_at` als "eerstvolgende maandag 09:00 Europe/Brussels,
   gerekend vanaf *nu* (niet vanaf de vorige scheduled_at)" — zelf-corrigerend
   na een gemiste run.
3. Maak een nieuwe kaart met dezelfde `parent_card_id`, `project`, `work_type`,
   `agent`, `labels` — en een **verbatim kopie van de `description`** (die al
   de Step 7-instructie bevat).
4. Doe dit ook als de huidige run geen bruikbare vondst opleverde. Zero-finding
   is legitiem (Step 3); een dode keten niet.

De recursie stopt zodra een mens één (of alle) trigger-kaarten verwijdert, of
hun `scheduled_at` ver in de toekomst schuift (jaar-3000-truc). `KanbanCard`
heeft **geen** `enabled`-veld (`backend/app/kanban/models.py:33-79`); zoek dat
niet — gebruik de mechanismen die hierboven staan. Zie §5.

### 4.3 Waar het resultaat binnenkomt

- **De trigger-kaart zelf** → Done, met een **summmary-argument** op `move_card`
  (verplicht voor Done-column per de kanban-architectuur), bv.
  `# Last run: <YYYY-MM-DD>, sources pulled: <list>, filed: <N>, deduped: <N>, no-op: <bool>`
  als comment.
- **1-3 nieuwe Backlog-kaarten** → voldoen aan het Step 5-template van de skill
  (Finding / Source / Why-it-matters / Suggested-next-step / Acceptance-criteria).
- **`.claude/state/research-last-run.json`** in de worktree (skill **Step 6 optie 2**,
  want de run is scheduled/cron-driven — geen mens die comments leest) met
  `last_run` en `sources_seen` SHA-map. Dit is de **precisie-dedupe-bron**
  waar Step 4 op leunt: zonder deze file kan Step 4 alleen op tekst-overlap
  dedupen, niet op "al gezien in de vorige run".
- **De opvolger-kaart** → Backlog met `scheduled_at` = volgende maandag (zie §4.2).

De mens ziet 's maandagochtend dus een verse stapel Backlog-kaarten + de
Done-trigger met het run-overzicht + de opvolger-kaart al klaar voor volgende week.

---

## 5. Pauzeer- en override-mechanisme (autonomiegrenzen)

CLAUDE.md's Doelstelling eist dat *"alle wijzigingen reproduceerbaar, controleerbaar en
auditbaar zijn"* en *"respecteer de ingestelde autonomiegrenzen en vraag goedkeuring
voor acties die buiten deze grenzen vallen"*. Het trigger-mechanisme valt binnen de
autonomie-envelop (het zet alleen onderzoeks-Backlog-kaarten klaar — het **wijzigt
geen code, dispatched geen engineer-kaarten**), maar de mens moet 'm op elk moment
bovenmatig kunnen overrulen.

| Wat de mens wil | Hoe | Effect |
|---|---|---|
| **Pauzeren voor 1 run** (bv. vakantie) | Wijzig `scheduled_at` op de trigger-kaart → bv. `2026-08-03T09:00:00+02:00` (over 3 weken). **Verwijder of schuif ook de al-aangemaakte opvolger-kaart** (zie §4.2 — die heeft een eigen hardcoded `scheduled_at`; die verschuift NIET mee). | Auto-dispatch slaat de huidige kaart over tot het nieuwe tijdstip; de opvolger-kaart moet apart aangepakt worden. |
| **Pauzeren voor onbepaalde tijd** | Verwijder de trigger-kaart **én eventuele opvolgers die al in `Backlog` staan** (de keten stopt pas als allemaal weg zijn). | Geen spawn, geen keten meer. Herstart = nieuwe trigger-kaart aanmaken. |
| **Eenmalig afvuren op een ander tijdstip** | Zet `scheduled_at` op bv. nu, of gebruik de bestaande "Dispatch"-knop op de kaart (handmatige override — slaat `_is_due` over). | Dispatched direct. `_is_due()` is fail-open (return `True` bij ontbrekend of onparseerbaar `scheduled_at`), dus een lege `scheduled_at` betekent ook "direct due". |
| **Deze run inhoudelijk overriden** | Wijzig de `description` van de trigger-kaart vóór de dispatch-tick 'm claimt. | De sessie opent met de aangepaste prompt. |
| **Een lopende run stoppen** | `release_card` (vrijgeven van de claim); de sessie wordt op de volgende dispatch-tick alsnog losgelaten als de claim vervalt; in de tussentijd is de tmux-sessie handmatig dood te maken. | Geen schone "Todo"-kolom om de kaart terug naar te zetten — `Backlog`, `Impediment`, `Done`, `To Resume` zijn de enige (`backend/app/kanban/schemas.py:6`). |
| **Frequentie aanpassen** | Wijzig de opvolger-kaart na elke run — patroon zit in de prompt, niet in code. | Volgende runs gebruiken het nieuwe interval; geen codewijziging nodig. |
| **Een auditor laten zien wat er gebeurt** | `activity feed` op de trigger-kaart toont elke claim/move/comment met timestamps + actor. | Standaard kanban-trace; niets bijzonders nodig. |
| **`autodispatch` per-project togglen** | `GET/POST /api/v1/kanban/autodispatch` (per `kanban-dispatch-spec.md:53`); device-lokaal, niet in de op-log. | Per device aan/uit — een tweede machine die dezelfde project-key ziet dispatched niet, tenzij die 'm zelf inschakelt. |

**Waarom geen `enabled=false`:** `KanbanCard` heeft geen `enabled`-veld
(`backend/app/kanban/models.py:33-79`). De velden zijn: id, project_key, title,
description, column, rank, priority, labels, work_type, agent, transport,
resume_session_id, resume_project_folder, scheduled_at, dispatch_failures,
claimed_by, claimed_at, created_at, updated_at — punt. (Een `enabled`-veld bestaat
wel op `AutodispatchRequest`, maar dat is per-project, niet per-card.) De
pauze-mechanismen zijn daarom: scheduled_at-edit, delete, manual dispatch, of
_impediment_.

**Cruciale autonomiegrens:** de gedispatchte sessie heeft de `analyst`-rol
(`work_type=analysis`, `agent=claude-code`), niet `engineer`. De skill-output is per
definitie *Backlog-kaarten*, niet *code*. Een eventuele bevinding die wél om code
vraagt, komt via de gewone engineer-dispatch-flow terecht (Backlog → dispatch →
engineer-sessie → review → master) — dus dubbel menselijk reviewbaar vóór er iets
op `master` belandt. Dit respecteert de CLAUDE.md-grens *"vraag goedkeuring voor
acties die buiten deze grenzen vallen"*.

---

## 6. Evolutie naar mechanisme B (fase 2)

**Afhankelijkheid:** scheduled-messages feature, fase 2. Status per 2026-07-09:
code-compleet (Tasks 1–11), runtime-e2e = Task 12 = nog open
(zie `docs/cockpit/fase-2-spec.md` en `docs/cockpit/00-orientation.md:25-27`).
Eerstvolgende stap bij oplevering staat in kanban-kaart `a4d9f8b68c7a4121a7adbed0a63a2d46`
(*Migrate weekly market-research trigger to scheduled-messages (fase 2)*).

**Wat verandert zodra Task 12 groen is:**

1. Eén **lang-levende analyst-sessie** wordt opgestart (handmatig of via de
   bestaande scheduler). De proposal specificeert `permission_mode = acceptEdits` —
   dit is de **spec-aanbeveling** uit `docs/cockpit/fase-2-spec.md` ("veilige
   default") en `docs/cockpit/fase-2-plan.md` step 17-19, **niet** de
   implementation-default: de huidige `session_resolver.permission_flags()`
   behandelt `default` (= `[]`, geen extra `claude`-flag) als de neutrale
   fallback en `acceptEdits` als één van drie opties. Voor een lang-levende
   sessie is `acceptEdits` de expliciet gekozen middenweg; `default` zou de
   sessie Claude's eigen (onbekende) standaard geven, en `bypass` zou
   --dangerously-skip-permissions zijn. **Noteer dit in de implementatie-
   comment** zodat de keuze niet later per ongeluk "veiliger" wordt gemaakt
   (= `default`/`[]`) zonder te beseffen dat dit Claude's gedrag verandert.
2. Een **scheduled-message** wordt aangemaakt via de Cockpit-UI:
   `cron_expr = "0 9 * * 1"` (elke maandag 09:00), `timezone = Europe/Brussels`,
   `target_project = <pad-naar-claude-cockpit>`,
   `message = "Draai de market-research skill volgens .claude/skills/market-research/SKILL.md. Produceer 1-3 Backlog-kaarten volgens Step 5, en maak de opvolger-kaart (Step 7 — chain-of-one-shots)."`.
3. De Dispatch Engine injecteert dit elke maandag in de idle analyst-sessie via
   `tmux send-keys` (zie `backend/app/services/scheduling/tmux_inject.py`).
4. **Geen spawn meer per week** — context warm, accumulerende session-memory.

**De keten blijft dus semantisch hetzelfde; alleen het transport verandert**
(kanban-dispatch → scheduled-messages-injectie). De trigger-kaart in Backlog
wordt overbodig en kan verwijderd worden zodra B stabiel draait.

**De follow-up-kaart `a4d9f8b68c7a4121a7adbed0a63a2d46` heeft `parent_card_id =
3f8ccfab70f44672908a8b1559754148`** (de Self-improvement analysis-card) — niet
`depends_on`, want dat veld verwijst naar blokkades tussen siblings. De fasale
blokkade staat in de **description** ("BLOCKED on fase 2 Task 12 runtime e2e") en
in de **acceptance criteria** van die kaart zelf.

---

## 7. Wat we NU doen (samenvatting)

| # | Actie | Status | Eigenaar |
|---|---|---|---|
| 1 | Dit voorstel landt in `docs/cockpit/recurring-cadence-proposal.md` | deze kaart | engineer |
| 2 | `market-research` skill-tekst uitgebreid met **Step 7** (chain-of-one-shots) in dezelfde commit | deze kaart | engineer |
| 3 | Eén seed-trigger-kaart aanmaken in Backlog met `scheduled_at` = eerstvolgende maandag 09:00 Europe/Brussels, `project_key`, `parent_card_id`, etc. | deze kaart (id `b7b195e2…`) | engineer |
| 4 | **Eenmalig** autodispatch inschakelen voor `git:github.com/guillaumevandevelde/claude-cockpit` op deze device: `POST /api/v1/kanban/autodispatch` met `{"project_key": "…", "enabled": true}` — anders pakt de dispatch-tick 'm niet op, ook al is `scheduled_at` gezet. | **menselijke stap**, eenmalig | de operator die dit voorstel landt |
| 5 | Companion Backlog-kaart aanmaken *"Migrate weekly market-research trigger to scheduled-messages"* met `parent_card_id` van deze kaart en expliciete afhankelijkheid op fase-2 Task 12 in de description | deze kaart (id `a4d9f8b6…`) | engineer |
| 6 | Eerste echte run monitoren (eerstvolgende maandag 09:00) — `session-problem-scan` na de run om te zien of Step 7 daadwerkelijk een opvolger aanmaakte | toekomstig | operator |

**Niet doen vandaag:**
- Geen cron / ScheduleWakeup in een Claude-sessie als productie-pad — sessie-only.
- Geen scheduled-messages — wacht op Task 12.
- Geen nieuwe dispatches/features bouwen die dit allemaal kunnen omzeilen — YAGNI.

---

## 8. Acceptatiecriteria — afvinken

- [x] Kort ontwerpdocument met concreet, uitvoerbaar voorstel voor cadans + trigger-mechaniek + pauzeer-mechanisme.
- [x] Cadans: wekelijks maandag 09:00 Europe/Brussels, zelf-corrigerend, configureerbaar.
- [x] Trigger-mechaniek: kanban-kaart `scheduled_at` (Backlog) + auto-dispatch (na opt-in) + chain-of-one-shots via Step 7 in de skill.
- [x] Pauzeer-mechanisme: scheduled_at-edit, verwijderen, handmatig dispatchen, activity feed-audit; **expliciet géén `enabled=false`** (veld bestaat niet op `KanbanCard`).
- [x] Autonomiegrenzen: enkel Backlog-kaarten, geen codewijzigingen; engineer-dispatch-flow blijft review-gated.
- [x] Expliciete vermelding van de afhankelijkheid op fase 2 (sectie 6 + companion Backlog-kaart `a4d9f8b6…`).
- [x] Companion Backlog-kaart *"Migrate to scheduled-messages"* aangemaakt in Backlog met `parent_card_id = 3f8ccfab…` en fasale blokkade in description.
- [x] Step 7 toegevoegd aan de `market-research`-skill in dezelfde commit.
- [x] Eerste trigger-kaart ge-seed in Backlog (`b7b195e2…`) met juiste `project_key`, `parent_card_id`, `scheduled_at` = eerstvolgende maandag.
- [ ] Autodispatch ingeschakeld voor `git:github.com/guillaumevandevelde/claude-cockpit` op deze device — **menselijke stap, niet door deze engineer-sessie uit te voeren** (out-of-autonomy: device-state, niet code).