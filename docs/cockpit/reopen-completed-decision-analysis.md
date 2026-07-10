# Completed beslissing weerleggen + heropenen met context — beslisdocument

> Kanban-kaart: **"Analyse - comment op completed beslissing"**
> Vraag: *"Ik wil een completed beslissing kunnen weerleggen. In deze wil ik een
> comment kunnen toevoegen op de completed, daarna moet dit ticket opnieuw
> opgenomen kunnen worden om de comment te reviseren. Wat is de beste oplossing
> zodat de sessie genoeg context krijgt en dit eenvoudig kan gebeuren?"*
>
> Dit is een analyse-kaart: DoD is een beslisdocument met een concrete aanbeveling
> + implementatieschets (het **wat** en **waarom**; het **hoe** is voor de executor).
> Geen feature-code in deze kaart.

## 1. Wat is een "completed beslissing" concreet?

Een "beslissing" in dit systeem is een **analyse-kaart** waarvan de deliverable een
beslisdocument is (zie de reeks `docs/cockpit/*-decision.md`, bv.
`reviewer-agent-decision.md`, `updates-feature-decision.md`). De analyst-persona
splitst óf plant, en het resultaat is een document + eventueel kind-kaarten.

Als zo'n kaart klaar is staat hij in de **`Done`**-kolom met een `summary` (die als
comment `**Completed:** …` in de activity-feed komt, zie `move_card`
`mcp_server.py:234-237`) en meestal een `branch`/`commit`-deliverable die naar het
gecommitte document wijst.

De gebruiker wil zo'n afgeronde beslissing kunnen **weerleggen**: een comment
toevoegen met tegenargumenten, en de kaart opnieuw laten oppakken zodat een verse
(of voortgezette) sessie de beslissing herziet — mét genoeg context om dat zinnig
te doen.

## 2. Geverifieerde stand van zaken (code)

Alle punten hieronder zijn geverifieerd in de code, niet uit het geheugen:

1. **De spawn-prompt bevat géén comments, deliverables of Done-summary.**
   `build_card_prompt` (`dispatch.py:549-585`) voedt de sessie uitsluitend:
   `persona + card.title + card.description` (+ optioneel een `## IMPEDIMENT`-blok en,
   voor kind-kaarten, een `PLAN CONTEXT`-blok). De **activity-feed (comments), de
   deliverables en de Done-summary komen niet in de prompt.** Een simpele "zet de
   kaart terug naar Backlog"-heropening spawnt dus een sessie die de vorige
   beslissing én de weerlegging **niet ziet** — precies het "genoeg context"-gat.

2. **Dispatch pakt alleen `Backlog` en `To Resume` op.** `list_pending_cards`
   (`service.py:236-250`) en `_DISPATCH_COLUMNS = ("Backlog", "To Resume")`
   (`dispatch.py:1208`). `Done` is een terminale kolom — een `Done`-kaart wordt niet
   heropgepakt tot hij expliciet naar een dispatch-kolom wordt verplaatst.

3. **Er bestaat al een bewezen patroon om één specifieke comment als context in de
   prompt te injecteren: de impediment-flow.** `resolve_impediment`
   (`api/v1/kanban/router.py:852-894`) leest de activity-feed terug, pakt de laatste
   comment die `Impediment:` bevat, en geeft die door als `impediment_question`.
   `build_card_prompt` rendert dat als een `## IMPEDIMENT`-sectie
   (`dispatch.py:552-559`). **Dit is exact de vorm die "weerleg-comment → volgende
   sessie" nodig heeft** — alleen met een andere tag en kolom-overgang.

4. **Er bestaat al een sterkere context-behoudende mechaniek: sessie-resume.**
   `set_resume` / `resume_session_id` + `resume_project_folder`
   (`mcp_server.py:388-415`, model `models.py:55-56`) laten de volgende dispatch
   `claude --resume <session_id>` draaien in de oorspronkelijke werkmap i.p.v. een
   verse worktree (`dispatch.py:1936-1939`, `session_recovery.py`). Een heropende
   sessie **zet dan letterlijk het transcript voort** dat de beslissing produceerde —
   álle redenering, het geschreven document, de afwegingen zitten al in de context.
   Dit wordt vandaag alleen gebruikt door de limit-recovery-/dead-session-paden, niet
   door een bewuste menselijke heropening.

5. **De UI toont comments read-only en heeft geen comment-invoer.** `CardDrawer`
   rendert de activity-feed alleen-lezen (`CardDrawer.tsx:592-601`) en toont voor
   `Done`-kaarten een `DoneSummaryBanner` (`:191-216`). Er is een
   `Re-dispatch`-knop, maar die redispatcht binnen de huidige kolom — geen "heropen
   vanuit Done met feedback". De backend heeft wél al een comment-endpoint
   (`POST /cards/{id}/comment`, `api.ts:168`) en MCP-tool (`comment`,
   `mcp_server.py:268`).

**Kernconclusie:** de twee bouwstenen die "genoeg context" leveren bestaan al
(impediment-injectie-patroon + sessie-resume). Het gat is (a) een bewuste
heropen-actie vanuit `Done` mét een weerleg-comment, en (b) die comment + de vorige
beslissing daadwerkelijk in de heropende prompt krijgen. Er hoeft dus **geen nieuw
mechanisme uitgevonden** te worden — bestaande patronen worden gecombineerd.

## 3. Ontwerpopties

### Optie A — Status quo: handmatig Done → Backlog + description editen
De gebruiker verplaatst de kaart terug en plakt de weerlegging in de description.
- ➕ Nul nieuwe code.
- ➖ Handwerk, foutgevoelig, en de **oorspronkelijke beslissing verdwijnt uit
  zicht** (description overschrijven vernietigt context, of de weerlegging wordt een
  losse comment die — punt 1 — nooit de prompt bereikt). Voldoet niet aan "eenvoudig"
  en niet aan "genoeg context".

### Optie B — "Weerleg & heropen"-actie (comment + heropen + injectie)
Eén actie op een `Done`-kaart: typ de weerlegging → de kaart krijgt een getagde
comment (bv. `**Revisit:** …`), gaat terug naar een dispatch-kolom, en de heropende
sessie krijgt die comment + de vorige beslissing in de prompt (via het
impediment-injectie-patroon uit punt 3).
- ➕ Bewezen patroon (spiegelt `resolve_impediment`), weinig nieuwe infra.
- ➕ Weerlegging blijft bewaard in de feed; oorspronkelijk document blijft in git.
- ➖ Verse sessie herbouwt context uit prompt + het gelinkte document i.p.v. het
  volledige oorspronkelijke transcript te kennen (meestal ruim voldoende).

### Optie C — Optie B + sessie-resume (transcript voortzetten)
Als het oorspronkelijke Claude-transcript nog op schijf staat, zet de heropen-actie
óók `resume_session_id`/`resume_project_folder`, zodat de heropende sessie het
oorspronkelijke transcript **voortzet** (punt 4) en de weerlegging als nieuwe
instructie krijgt.
- ➕ Maximale context: de reviser "herinnert zich" letterlijk waarom de beslissing zo
  viel.
- ➖ Resume is fragiel: transcript kan gepruned zijn, de worktree kan al opgeruimd
  zijn (analyse-kaarten mergen en ruimen hun worktree op na `Done`). Vereist een
  **graceful fallback** naar Optie B als resume niet resolvet.

## 4. Aanbeveling

**Bouw Optie B als kern, met Optie C als best-effort-verrijking.**

Concreet: één "Weerleg & heropen"-actie die

1. de weerlegging als **getagde comment** (`**Revisit:** …`) op de kaart plaatst
   (blijft in de feed, verdwijnt nooit),
2. de kaart terug naar de **`Backlog`**-kolom verplaatst (behoudt `agent`/`work_type`,
   dus hij routeert vanzelf terug naar de analyst-persona die de beslissing nam),
3. de heropende sessie **context injecteert** in de prompt: de weerleg-comment
   (`## REVISIT`-sectie, spiegel van `## IMPEDIMENT`) **plus** een pointer naar de
   vorige beslissing — de laatste `Completed:`-summary en de deliverable-refs
   (branch/commit/link naar het `docs/cockpit/*-decision.md`),
4. **als** het oorspronkelijke transcript nog resolvet: óók `set_resume` zetten zodat
   de sessie het transcript voortzet; anders val terug op de verse-sessie-injectie
   uit stap 3.

Dit voldoet aan beide eisen uit de kaart:
- **"eenvoudig"** → één knop/één MCP-tool i.p.v. handmatig kolommen slepen en
  descriptions editen.
- **"genoeg context"** → de reviser krijgt (a) de weerlegging, (b) de vorige
  beslissing (summary + document), en (c) waar mogelijk het volledige oorspronkelijke
  transcript. Dat is precies wat een herziening nodig heeft.

Waarom niet zwaarder (een aparte "Revisit"-kolom, een revisie-persona, versiehistorie
van beslissingen): dat is nieuwe infra zonder bewezen baat — dezelfde afweging als in
`reviewer-agent-decision.md`. Het document leeft al versioneerd in git; de feed
bewaart de weerlegging; de bestaande dispatch-routing brengt de kaart terug bij de
juiste persona. Combineer bestaande bouwstenen i.p.v. een parallel systeem.

## 5. Implementatieschets (wat + waarom; hoe = executor)

> Richting, geen dwingend recept. De executor bevestigt of weerlegt de aannames.

**Backend**
- Eén heropen-pad, bij voorkeur door `resolve_impediment` te **generaliseren** of
  ernaast een `POST /cards/{id}/reopen` (of `/revisit`) endpoint te zetten dat:
  een getagde comment plaatst (`**Revisit:** <text>`), de kaart naar `Backlog`
  verplaatst, en — best-effort — `resume_session_id` zet als het transcript
  resolvet. *Waarom een aparte tag:* de dispatch moet een revisit-comment kunnen
  onderscheiden van een impediment-comment.
- Een `revisit_question`-extractie in het dispatch-pad, gemodelleerd naar de
  bestaande `impediment_question`-extractie (`dispatch.py:2352` +
  `router.py:865-871`): lees de laatste `**Revisit:**`-comment terug uit de feed.
- `build_card_prompt` uitbreiden met een `## REVISIT`-sectie (spiegel van
  `impediment_section`, `dispatch.py:552-559`) die de weerlegging + een korte
  "vorige beslissing"-samenvatting/deliverable-pointer rendert. *Waarom in de prompt:*
  punt 1 — anders ziet de sessie de weerlegging niet.

**MCP**
- Een tool waarmee ook een agent een beslissing kan heropenen (bv. `reopen_card`/
  `revisit_card`), of documenteer dat `comment` + `move_card(..., "Backlog")` +
  set_resume samen hetzelfde doen. *Waarom:* cross-session-agents (Agent Mail) moeten
  een beslissing kunnen aanvechten zonder de UI.

**Frontend**
- In `CardDrawer`, voor `Done`-kaarten (naast/onder de `DoneSummaryBanner`,
  `CardDrawer.tsx:506`): een textarea "Weerleg deze beslissing" + knop "Heropen met
  feedback" die het nieuwe endpoint aanroept. Herbruik `CLICKABLE_CARD`/`MODAL_SIZES`
  waar van toepassing; volg de bestaande `act(...)`-patronen in het bestand.
- Optioneel: dezelfde comment-invoer generiek onder de Activity-tab, zodat comments
  toevoegen sowieso mogelijk wordt vanuit de UI (nu read-only, punt 5).

**Tests (TDD)**
- Backend: endpoint plaatst getagde comment + verplaatst naar `Backlog`;
  `revisit_question`-extractie pakt de juiste comment; `build_card_prompt` bevat de
  `## REVISIT`-sectie; resume-fallback wanneer transcript ontbreekt.
- Frontend: knop verschijnt alleen op `Done`-kaarten en roept het endpoint aan.

## 6. Risico's & aandachtspunten (voor de executor)

- **Resume-fragiliteit.** Analyse-kaarten mergen + ruimen hun worktree op bij `Done`
  (zie CLAUDE.md "Worktree hygiene"), dus het oorspronkelijke transcript/worktree is
  vaak wég. Optie C moet **altijd** gracefully terugvallen op de verse-sessie-injectie
  (Optie B) — nooit hard falen. De impediment-flow doet vandaag al verse injectie
  zonder resume, dus die fallback is de veilige default.
- **Welke kolom bij heropenen.** `Backlog` is het eenvoudigst: de kaart behoudt zijn
  `agent`/`work_type` en routeert vanzelf terug naar de analyst. Vermijd het
  introduceren van een nieuwe kolom.
- **Herhaalde revisies.** Extractie moet de **laatste** `**Revisit:**`-comment
  pakken (zoals impediment de laatste `Impediment:`-comment pakt), zodat meerdere
  rondes werken zonder oude weerleggingen te herhalen.
- **Loop-preventie.** Een heropening moet menselijk (of een expliciete agent-actie)
  getriggerd zijn — geen automatische re-dispatch, zodat een beslissing niet
  eindeloos ping-pongt.
- **Deliverable-pointer, geen kopie.** Het beslisdocument leeft in git; de prompt
  hoeft er alleen naar te wijzen (deliverable-ref + summary), niet de volledige
  inhoud in te sluiten.

## 7. Wat deze kaart oplevert

Alleen dit beslisdocument + één follow-up implementatie-kaart in `Backlog` die naar
dit document verwijst. Geen feature-code in deze kaart (analyse-DoD).
