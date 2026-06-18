# Voorstel: execution/review-UX lenen van Vibe Kanban / agent-kanban

> **Status:** onderzoek-deliverable. **Geen implementatie zonder akkoord** (DoD van de kaart).
> Bron-kaart: maturiteitsanalyse §3, punt 5. Geschreven 2026-06-18.

## 0. TL;DR

We hebben de **moeilijke helft al gebouwd**: claim-before-spawn dispatch, een worktree
per kaart, persona's, ship-modes en een op-log/activity-feed. Wat ontbreekt is de
**review-laag** die Vibe Kanban volwassen maakt:

1. **(a) Diff-review tussen Doing → Review** — een syntax-highlighted diff van de
   worktree-branch, met approve/reject, vóór de merge.
2. **(b) Per-kaart run-output in de drawer** — de live/voltooide agent-run zichtbaar
   naast de kaart, niet alleen losse op-log-regels.

**Aanbeveling:** leen van **Vibe Kanban** (Apache-2.0, code-compatibel met onze
MIT-licentie). Concreet adopteren we **twee patronen + één library**:

- het **`Diff`-datamodel** (Rust→onze Pydantic) + de **MIT-licensed React-component
  [`@git-diff-view/react`](https://github.com/MrWangJustToDo/git-diff-view)** voor (a);
- het **"execution process / normalized + raw logs over WebSocket"-patroon**, maar
  gebouwd op **onze bestaande tmux PTY-relay**, voor (b).

Van **agent-kanban** lenen we **geen code** (FSL-licentie, zie §4) maar bevestigen we
één ontwerpkeuze: hun leader/worker + "merge voltooit de taak"-flow is functioneel
gelijk aan onze analyst/developer + dispatch — we hoeven daar niets te herzien, alleen
de **auto-complete-op-merge** is een goedkope winst die we kunnen overnemen.

---

## 1. Wat we vandaag hebben (vertrekpunt)

Relevante code in deze repo:

| Stuk | Locatie | Wat het doet |
|---|---|---|
| Dispatch | `backend/app/kanban/dispatch.py` | claim-before-spawn, worktree off `origin/master` op `.claude/worktrees/<session>`, branch = `<session>`, spawnt autonome CC-sessie via tmux |
| Kolommen | `backend/app/kanban/schemas.py` | `Backlog, Analysis, Todo, Doing, Review, Done` — **Review bestaat al, maar er is geen UI die ʼm vult** |
| Deliverables | `backend/app/kanban/models.py` | `kind ∈ pr\|branch\|commit\|link\|note`, `ref` = *portable* referentie (nooit een lokaal pad) |
| Agent-tools (MCP) | `backend/app/kanban/mcp_server.py` | `move_card`, `attach_deliverable`, `comment`, … — de agent praat hiermee terug naar het bord |
| CardDrawer | `frontend/src/features/kanban/components/CardDrawer.tsx` | toont beschrijving, agent-select, dispatch/edit/claim/delete, deliverables-lijst, **op-log activity-feed** |
| Live terminal | `frontend/src/features/cc-bridge/{TerminalView,useTerminal}.tsx` + `backend/app/services/cc_bridge/pty_relay.py` | **tmux PTY over WebSocket** — een werkende live-terminal, vandaag alleen op de CC-Bridge-pagina |
| Transcript-replay | `frontend/src/features/sessions/` (`Conversation`, `ContentBlockRenderer`, `ToolUseBlock`) | rendert CC JSONL-transcripts als rich chat |

**De twee gaten:**

- **(a)** Niets berekent of toont een **diff** van de worktree-branch. "Review" is een
  lege kolom; een mens moet handmatig de PR/branch buiten Cockpit openen.
- **(b)** De drawer toont alleen de **op-log** (move/comment/attach). De **eigenlijke
  agent-run** (`agent:<session>`-claim → tmux-sessie) is nergens aan de kaart gekoppeld.
  De infra bestaat (PTY-relay, transcript-renderer) maar is niet per kaart ontsloten.

---

## 2. Vibe Kanban (BloopAI) — wat er volwassen is

**Licentie: Apache-2.0** → code- en patroon-overname compatibel met onze MIT-repo
(attributie/NOTICE behouden). Stack: Rust-backend + React/TS-frontend. Project is
"sunsetting" maar community-/Apache-onderhouden; de code blijft bruikbaar.

### 2.1 Execution-datamodel (relevant voor (b))

```
Workspace → Session → ExecutionProcess(es) → ExecutorAction
```

- **ExecutionProcess** = één agent-run. Velden: `status` (`running|completed|failed|killed`),
  `run_reason` (`CodingAgent: Initial|Follow-up`), `started_at`, `ended_at`,
  `agent_working_dir`. Follow-ups en retries worden extra processen onder dezelfde sessie.
- **Logs in twee lagen:**
  - **Normalized logs** — gestructureerd (agent-gedachten, tool-calls, tool-results),
    door de UI als rich chat gerenderd. Streamt via `…/normalized-logs/ws`.
  - **Raw logs** — stdout/stderr, gemapt op `ConversationPatch`-objecten met
    oplopende index. Streamt via `…/raw-logs/ws`.
- **Streaming-mechaniek:** een `EventService` gebruikt **SQLite pre-update/update-hooks**
  op de `execution_processes`-tabel → `MsgStore` → **JSON-patches over WebSocket**.

> **Inzicht voor ons:** dit is precies onze CC-Bridge PTY-relay (raw logs) + onze
> Sessions-transcriptrenderer (normalized logs), maar **per kaart gekoppeld** via een
> expliciet "execution process"-record. Wij hebben de twee renderers al; we missen het
> **koppel-record** dat een run aan een kaart-id bindt.

### 2.2 Diff-review (relevant voor (a))

- Backend berekent diffs met de Rust-crate **`similar`** (`TextDiff::from_lines`) en
  serialiseert naar een `Diff`-struct (`crates/utils/src/diff.rs`):

  ```
  Diff { change: DiffChangeKind, old_path, new_path,
         old_content, new_content, content_omitted,
         additions, deletions, repo_id }
  DiffChangeKind = Added|Deleted|Modified|Renamed|Copied|PermissionChange
  ```

  De struct is **bewust gevormd naar de props van [`git-diff-view`](https://github.com/MrWangJustToDo/git-diff-view)**
  (commentaar in de bron: *"Structs compatible with props: …git-diff-view"*).
- Diffs streamen incrementeel via `diff_stream.rs` (zelfde SQLite-hook→WS-mechaniek).
- Frontend rendert met **`@pierre/diffs`** (Apache-2.0) en/of `git-diff-view`-props;
  **inline review-comments** zijn mogelijk (`ReviewCommentRenderer.tsx`).
- **Merge-flow:** na review → PR-aanmaak met AI-gegenereerde beschrijving → one-click merge.
- **`content_omitted` + `additions/deletions`**: grote bestanden worden niet volledig
  meegestuurd maar als stat-only getoond — een nette schaal-oplossing die we 1:1 overnemen.

---

## 3. agent-kanban (saltbo) — wat we ervan leren

**Licentie: FSL-1.1-ALv2** (Functional Source License; converteert pas na 2 jaar naar
Apache-2.0). Self-hosting mag, een concurrerende hosted dienst niet. **Gevolg: geen
code overnemen — alleen patronen/ideeën.** Stack: Hono + Cloudflare D1 (SQLite),
React + SSE, TypeScript.

- **Leader/worker-model:** leader plant + wijst toe; worker claimt, implementeert, dient
  in ter review; leader reviewt + merget de PR; **bij merge voltooit de daemon
  automatisch de taak**.
- **Kolommen:** `Todo → In Progress → In Review → Done`.
- git-worktree per worker; Ed25519-identiteit per agent (JWT, server-side geverifieerd);
  SSE voor live updates.

**Mapping op ons:**

| agent-kanban | Claude Cockpit | Conclusie |
|---|---|---|
| leader plant + wijst toe | `kanban-analyst` decomponeert Analysis → Todo | functioneel gelijk |
| worker claimt + implementeert | `dispatch.py` claim-before-spawn + `kanban-developer` | functioneel gelijk, **wij zijn hier juist robuuster** (race-veilige claim, stale-claim reaper) |
| In Review → leader merget | Review-kolom (leeg) + ship-mode pull-request | **hier zit ons gat** (geen review-UI) |
| merge ⇒ taak auto-complete | — (mens versleept handmatig) | **goedkope winst om over te nemen** |
| Ed25519-identiteit per agent | `agent:<session>`-claimlabel | onze claim volstaat; crypto-identiteit is overkill voor lokaal/single-user |

**Niets te herzien aan onze analyst/developer-opzet.** De enige adoptie-kandidaat uit
agent-kanban is de **auto-complete-op-merge** (zie §5, kandidaat C).

---

## 4. Licentie-samenvatting

| Project | Licentie | Mogen we code overnemen? |
|---|---|---|
| Onze repo | MIT | — |
| Vibe Kanban | Apache-2.0 | **Ja** (permissief; NOTICE/attributie behouden) |
| `git-diff-view` | MIT | **Ja** (ideale frontend-diffcomponent) |
| `@pierre/diffs` | Apache-2.0 | Ja, maar `git-diff-view` is lichter/MIT → voorkeur |
| agent-kanban | FSL-1.1-ALv2 | **Nee** (alleen patronen, geen code) |

---

## 5. Concrete adoptie-kandidaten

### Kandidaat A — Diff-review-stap Doing → Review *(het gat (a))*

**Wat lenen:** Vibe Kanban's `Diff`-datamodel + `git-diff-view` React-component.

**Backend (nieuw, klein):**
- Endpoint `GET /api/v1/kanban/cards/{id}/diff`. Resolve het lokale pad uit de
  `agent:<session>`-claim → worktree `.claude/worktrees/<session>` (branch `<session>`).
  Bereken `git diff origin/master...<branch>` via Python (`subprocess` of de `gitpython`/
  `unidiff`-route) en serialiseer naar de Vibe-vorm:
  `{ change, oldPath, newPath, oldContent, newContent, contentOmitted, additions, deletions }`.
  Neem **`content_omitted` + stat-only voor grote bestanden** over (schaalpatroon).
- Twee acties: **Approve** = `move_card → Review` (of direct `Done` bij ship-mode `direct`)
  + behoud bestaande ship-flow; **Reject** = `comment` met reden + kaart terug naar `Doing`
  (re-dispatch kan ʼm oppakken). Dit hangt op onze **bestaande op-log** — geen nieuw
  state-model nodig.

**Frontend (nieuw):**
- `npm i @git-diff-view/react`. Nieuwe **"Diff"-tab in de CardDrawer** (naast
  Deliverables/Activity) die de diff-payload rendert, plus Approve/Reject-knoppen.
- Inline review-comments (Vibe's `ReviewCommentRenderer`-idee) zijn **fase 2** — eerst
  read-only diff + approve/reject.

**Inpassing-inschatting:** **Middel.** Backend ~1 endpoint + diff-serializer (de Rust-
struct is een directe blauwdruk voor een Pydantic-model). Frontend = 1 drawer-tab + 1
dependency. Raakt `CardDrawer.tsx`, `kanban/api.ts`, een nieuwe route-module en
`dispatch.py`-helper (claim→worktree-pad-resolutie, die deels al bestaat in
`worktree_transport`).

### Kandidaat B — Per-kaart run-output in de drawer *(het gat (b))*

**Wat lenen:** Vibe's "execution process"-koppel-record + de normalized/raw-logs-split.
**Wat hergebruiken:** onze **bestaande PTY-relay** (raw) en **Sessions-transcriptrenderer**
(normalized) — niet opnieuw bouwen.

**Aanpak (minimale variant):**
- De kaart kent haar sessie al via de `agent:<session>`-claim. Voeg een
  **"Run"-tab in de CardDrawer** toe die, zolang de claim leeft, de **bestaande
  `TerminalView`** mount op die tmux-sessie (live raw output). Na afloop: render het
  **CC JSONL-transcript** van die sessie via de bestaande `Conversation`-componenten
  (normalized, persistente replay).
- Geen nieuw streaming-kanaal nodig: we hebben PTY-WS al. Vibe's SQLite-hook→WS is
  voor ons **niet** nodig (dat lost in Rust een probleem op dat onze PTY-relay al dekt).

**Inpassing-inschatting:** **Laag–middel.** Vooral *bedrading*: een drawer-tab die
session-name → bestaande TerminalView/Conversation koppelt. Geen DB-schemawijziging als
we de claim als koppeling gebruiken; optioneel later een klein `card_id ↔ session_name`-
record voor historiek na release.

### Kandidaat C — Auto-complete op merge *(uit agent-kanban, patroon)*

Wanneer de ship-flow (PR-merge of `direct`-merge) slaagt, **verplaats de kaart automatisch
naar Done** en attach de PR/commit als deliverable. Vandaag deels handmatig.
**Inpassing: laag** (haakt op de bestaande `git-ship`/ship-mode-afronding).

---

## 6. Wat we **niet** overnemen

- **Vibe's volledige Workspace/Session/ExecutionProcess-ORM** — te zwaar; onze claim +
  tmux-sessie dekken de koppeling al.
- **SQLite pre-update-hook→WebSocket EventService** — lost een probleem op dat onze
  PTY-relay + op-log al dekken.
- **Built-in browser/devtools/preview-server** (Vibe) — buiten scope van deze kaart.
- **agent-kanban code** (licentie) en **Ed25519-agent-identiteit** (overkill lokaal).
- **`@pierre/diffs`** — `git-diff-view` (MIT, lichter) heeft de voorkeur.

---

## 7. Voorgestelde volgorde (na akkoord)

1. **Kandidaat B** eerst (laagste risico, grootste zichtbaarheid): Run-tab in de drawer
   bovenop bestaande PTY-relay/transcriptrenderer.
2. **Kandidaat A**: diff-endpoint + `git-diff-view`-tab + Approve/Reject op de op-log.
3. **Kandidaat C**: auto-complete op merge.
4. *(later, optioneel)* inline review-comments op de diff.

Elk stuk is een aparte Todo-kaart; geen big-bang. **Wacht op akkoord** voordat hiervan
iets wordt geïmplementeerd.
