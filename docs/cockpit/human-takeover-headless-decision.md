# Beslissing: human-takeover-UX voor headless sessies

**Datum:** 2026-07-15
**Status:** besloten — **tmux blijft de interactieve transport; takeover = promotie, geen
**Kaart:** `80c812af…`
**Uitkomst:** **tmux blijft de interactieve transport; takeover = promotie.** Geen input-streaming-UX en geen categorie "human-takeover-kaarten": een headless run wordt op afroep via `claude --resume <session_id>` gepromoveerd tot een echte, attachbare pane mét historie (gemeten). De transport-keuze verschuift van dispatch-tijd naar takeover-tijd.

input-streaming.**
**Trigger:** kanban-kaart "[analysis][transport] Human-takeover-UX voor headless sessies"
(`80c812af`) — [`acp-transport-decision.md`](./acp-transport-decision.md) §6 kaart 4
(= [`orchestration-substrate-decision.md`](./orchestration-substrate-decision.md) §6 kaart 4).
`depends_on`: kaart 3 → [`headless-stream-json-transport-spike.md`](./headless-stream-json-transport-spike.md).

**Verwant:** [`headless-stream-json-transport-spike.md`](./headless-stream-json-transport-spike.md)
(de GO waarvan deze kaart de bevindingen consumeert),
[`acp-transport-decision.md`](./acp-transport-decision.md) §5 (human-takeover als hard
acceptance-criterium), [`structured-events-schema.md`](./structured-events-schema.md),
[`pane-attention-spec.md`](./pane-attention-spec.md).

---

## TL;DR

De kaart stelt de vraag binair: **(a)** sturen via input-streaming (stream-json/ACP), of **(b)**
de expliciete keuze om human-takeover-kaarten op tmux te houden. Het gemeten antwoord is dat
**beide takken dezelfde onnodige aanname delen** — dat je *vooraf, per kaart* moet kiezen welk
transport een mens later nodig heeft. Dat hoeft niet.

**Het besluit: takeover is een *promotie*, geen transport-keuze.** Een headless run en een
tmux-sessie zijn twee toestanden van **dezelfde** Claude-sessie, verbonden door de transcript op
schijf en `claude --resume <session_id>`. Een mens die wil overnemen, promoveert de headless run
ter plekke naar een echte, attachbare tmux-pane — mét de volledige headless-historie in de
scrollback en een levende prompt. De keuze verschuift van *dispatch-tijd* (waar je 'm nog niet kunt
maken) naar *takeover-tijd* (waar de mens 'm feitelijk maakt).

Dit is empirisch geverifieerd, niet afgeleid (§4):

1. Een headless `claude -p --output-format stream-json`-run schrijft een **gewone, resumebare
   transcript** in dezelfde `~/.claude/projects/<folder>/<session_id>.jsonl` als elke andere sessie.
2. Die transcript wordt **incrementeel geflusht tijdens de run** (gemeten: 38 KB op t=12s → 44 KB
   op t=24s, terwijl de run nog liep) — niet pas bij exit. Promotie verliest dus hooguit de
   in-flight turn.
3. `claude --resume <session_id>` in een tmux-sessie levert een **volledig interactieve REPL met
   de headless conversatie zichtbaar in de pane** en een prompt die op de mens wacht. Bewezen met
   een echte run (§4.2), niet met een gedachte-experiment.
4. De `session_id` **blijft gelijk** over de resume heen — de correlatie-sleutel breekt niet.

Netto: de attachbare pane is **geen** eigenschap van het transport waar je je aan vastlegt, maar
een toestand die je op afroep kunt aannemen. Antwoord (b) op de kaart — "tmux blijft de
interactieve transport" — klopt dus, maar om een sterkere reden dan de kaart veronderstelde: niet
omdat we headless-kaarten *afschermen* van human-takeover, maar omdat **elke** headless kaart op
elk moment takeover-baar is zonder dat iemand dat vooraf hoefde te voorspellen.

**En het echte werk zit ergens anders.** De attach-mechanica is bijna gratis (§5: de machinerie
staat er al, gebouwd voor crash-recovery). Het onopgeloste probleem is *detecteren* dat een mens
nodig is: een headless run die een vraag heeft, exit met `subtype: success`, `is_error: false` en
exitcode 0 — met de vraag alleen in de finale tekst (§6). Dat is een gemeten correctie op de
dep-spike.

---

## 1. Waarom de binaire framing te smal is

De kaart erft haar framing van `acp-transport-decision.md` §3, waar de vergelijkingstabel bij
"Human-takeover (tmux)" voor alle drie de transportopties hetzelfde zegt: *"Verdwijnt (opaak
proces). **Geen** van de drie behoudt de attachbare pane."*

Dat is waar over het **proces** en onwaar over de **sessie**. Een `claude -p`-proces heeft
inderdaad geen pane en is niet attachbaar. Maar Claude Code's sessie-identiteit leeft niet in het
proces — ze leeft in de transcript op schijf. Zolang die transcript er is, kan de sessie in een
*nieuw* proces verder, en dat nieuwe proces mag best een tmux-pane hebben. De pane verdwijnt niet
permanent; hij is er alleen niet *op dit moment*.

Zodra je dat ziet, verdampt de vraag "welke kaarten houden we op tmux?". Je hoeft bij dispatch niet
te voorspellen of een mens later wil ingrijpen — precies de voorspelling die niemand betrouwbaar
kan maken, want de aanleiding voor takeover (agent loopt vast, kiest een verkeerde aanpak, stelt een
vraag) is per definitie niet vooraf bekend.

## 2. Wat "bekijken & overnemen" vandaag feitelijk is (read-only geverifieerd)

Niet een abstractie, maar heel concreet één subprocess. `PtyRelay`
(`backend/app/services/runs/pty_relay.py:99`) start:

```python
self.process = subprocess.Popen(
    ["tmux", "attach-session", "-t", self.target], ...)
```

…en bridget dat pty naar een WebSocket (`/api/v1/runs/sessions/{target}/terminal`,
`runs/router.py:223`). De UI opent 'm `mode=readonly` en schakelt naar `mode=interactive` via een
control-frame (`pty_relay.py:169`); `read_only` poort simpelweg of `os.write(master_fd, …)` mag.
"Bekijken" en "overnemen" zijn dus dezelfde pijp met één boolean ertussen.

Die pijp levert vier affordances. Ze scheiden is de kern van de analyse, want ze zijn **niet**
even moeilijk te vervangen:

| Affordance | Wat tmux geeft | Zonder pane |
|---|---|---|
| **Replay** — wat is er tot nu toe gebeurd? | pane-scrollback | ✅ de event-stream is *beter* (getypeerd, niet ge-reflowd) |
| **Keyboard** — de mens *is* de sessie | pty-input | ❌ fundamenteel anders (§3) |
| **Interrupt** — Esc, ctrl-c | pty-signalen | ❌ geen equivalent onder `-p` |
| **Multi-client** — N kijkers + agent op één buffer | tmux' eigen model | ⚠️ te bouwen, niet gratis |

Alleen de **eerste** wordt door het gestructureerde transport verbeterd. De andere drie zijn
precies wat tmux gratis geeft en wat je anders zelf zou moeten bouwen.

## 3. Waarom input-streaming (optie a) geen takeover is

`--input-format stream-json` opent het bidirectionele control-protocol, en het is verleidelijk om
dat "takeover" te noemen: je kunt immers een user-message injecteren. Maar dat is **steering**, niet
takeover:

- **`-p` is single-shot.** Er is geen draaiende REPL om in te stappen. De headless run *is* de
  turn; als hij klaar is, is het proces weg.
- **Een mens die overneemt, wil de keyboard, niet een berichtenkanaal.** Esc om te interrumperen,
  `/model` om te wisselen, `/clear`, shift+tab voor permission-modus, een bestand openen, even zelf
  een commando draaien in dezelfde context. Geen daarvan is een user-message; het zijn REPL- en
  terminal-affordances.
- **Je zou tmux herbouwen in de browser.** Input-streaming als takeover-UX betekent: een
  message-composer, plus interrupt-semantiek, plus slash-command-afhandeling, plus multi-client
  buffer-sync — een tweede, zwakkere terminal, exclusief voor één transport, naast de terminal die
  we al hebben en die werkt.
- **Het levert bovendien niet wat `acp-transport-decision.md` §3.2 hoopte.** De dep-spike stelde
  al vast (§4.1c) dat ACP's `session/request_permission` **niet** gratis meekomt met stream-json.
  De getypeerde gating-haak is een argument voor de gepoorte ACP-kaart, geen fundament voor deze UX.

Input-streaming is dus wél interessant — maar als **autonome** capability (een supervisor-agent of
een scheduled message die een lopende run bijstuurt), niet als de manier waarop een mens de stoel
overneemt. Dat is een andere kaart en een ander probleem.

## 4. De gemeten vondst: promotie werkt

Probes in vier werkdirs (acht `claude`-invocaties) met `claude` **2.1.210** tegen een echte
subscription (de dep-spike mat 2.1.209). Read-only t.o.v. de codebase: alles draaide in een
scratch-dir, er is geen productiecode aangeraakt.

### 4.1 De transcript is er, en hij is er *tijdens* de run

Een headless run in `<dir>` schrijft naar `~/.claude/projects/<encoded-dir>/<session_id>.jsonl` —
dezelfde plek en hetzelfde formaat als een interactieve sessie. Gepolld tijdens een lopende run
(agent geblokkeerd in een `sleep`):

```
t=12s transcript_bytes=38229
t=24s transcript_bytes=44772   ← run loopt nog
t=36s transcript_bytes=44772   ← plateau: agent wacht, geen nieuwe messages
```

De flush is **per message/turn-step**, niet bij exit. Dat is de voorwaarde die promotie van een
*lopende* run mogelijk maakt: je verliest hooguit de turn die op dat moment in de lucht hangt.

### 4.2 `--resume` in tmux geeft een echte, gevulde REPL

Headless run 1: prompt `"Reply with exactly the word: apricot"` → `session_id`
`5482c007-…`, exit 0. Daarna, met exact het commando dat `spawn_session(mode="resume")` al bouwt
(`cc_spawn.py:144`):

```bash
tmux new-session -d -s <naam> -c <dir> "claude --resume 5482c007-… --dangerously-skip-permissions"
```

`tmux capture-pane` toont een volwaardige interactieve REPL met de **headless historie zichtbaar**:

```
❯ Reply with exactly the word: apricot
● apricot
────────────────────────────────────────────
❯                    ← levende prompt, wacht op de mens
────────────────────────────────────────────
   probe  ✱ Opus 4.8 · ⏵⏵ bypass permissions on (shift+tab to cycle)
```

Dit is precies de UX die de kaart zoekt: de mens ziet wat er gebeurd is én heeft de keyboard.
De pane is niet gesimuleerd of nagebouwd — het *is* de bestaande pane-UX, op een sessie die
headless begon.

### 4.3 De sessie-identiteit overleeft

`claude -p --resume <sid>` gaf `session_id` **`5482c007-…`** terug — dezelfde. En de context is
echt geladen: op de vraag *"What single word did you reply with a moment ago?"* antwoordde de
resumede sessie `apricot`. De transcript is dus geen dood logbestand maar een werkende
sessie-staat.

Dat sluit aan op §5.1 van de dep-spike: van de vier-in-één identiteit was alleen het
*liveness-orakel* tmux-gebonden. De `session_id` blijkt nu de vijfde, transport-overspannende
sleutel die headless en tmux aan elkaar naait.

## 5. Waarom dit bijna gratis is: de machinerie staat er al

Promotie is geen nieuw subsysteem. Het is de **bestaande crash-recovery-flow**, met een andere
trigger (een mens die klikt i.p.v. een reaper die een dode sessie vindt):

| Bouwsteen | Bestaat als | Werkt headless? |
|---|---|---|
| worktree → `session_id` resolven | `_resolve_resume_target` (`session_recovery.py:52`) — globt de transcript-dir op mtime | ✅ **transport-agnostisch**: het kijkt naar de transcript, niet naar tmux |
| tmux spawnen met `--resume` | `spawn_session(mode="resume", …)` (`cc_spawn.py:141-144`) | ✅ ongewijzigd |
| resume-target op de kaart parkeren | `card.resume_session_id` / `resume_project_folder` | ✅ ongewijzigd |
| kaart terug in de dispatch-flow | `"To Resume"`-kolom (`schemas.py:21`, `_DISPATCH_COLUMNS`) | ✅ ongewijzigd |
| pane bekijken/overnemen | `PtyRelay` + `mode=readonly\|interactive` | ✅ zodra de pane er is |
| runtime-hint bij spawn | `spawn_session(runtime=…)` documenteert `worktree\|sandcastle\|**headless**\|host` | ✅ al geanticipeerd |

De headless-transport hoeft de `session_id` niet eens af te leiden: `system/init` draagt 'm als
eerste veld van de stream. Maar zelfs zonder dat werkt `_resolve_resume_target` al, omdat een
headless run in de worktree zijn transcript op exact de verwachte plek schrijft.

**De ingreep is dus één actie** ("promoveer deze run naar een pane"), geen tweede takeover-UX.

## 6. Het echte, onopgeloste probleem: *wanneer* is een mens nodig?

De attach-mechanica is het makkelijke deel. De gemeten verrassing zit in de detectie — en ze
corrigeert de dep-spike.

Probe 4 gaf een headless run een taak waarvan de middelste stap door de harness geblokkeerd werd.
De run **stopte, stelde een vraag, en exit'te als succes**:

```
subtype = success      is_error = False      num_turns = 3      exit = 0
final text: "…The middle command is blocked by the harness… How do you want to proceed?"
```

De taak was **niet** af (`z.txt` is nooit geschreven), maar niets in de getypeerde signalen zegt
dat. `headless-stream-json-transport-spike.md` §6.2 stelt: *"Klaar? → `result.subtype` +
`is_error` + exitcode"* en *"exitcode is eenduidig"*. Dat klopt **over het proces** en **niet over
de taak**: een headless run die een mens nodig heeft, is van buiten niet te onderscheiden van een
run die klaar is. `-p` kán niet blokkeren op een vraag — er is geen stdin-mens — dus hij beëindigt
zijn turn beleefd en gaat weg.

Voor het tmux-pad bestaat hier al machinerie voor (pane-attention detecteert een wachtende prompt;
`pane-attention-spec.md`). Voor headless verdwijnt dat signaal — de run wacht niet, hij *vertrekt*.

Het goede nieuws: de bestaande vangnetten dekken de schade, want een vertrokken run die de kaart
niet naar `Done` bewoog, houdt zijn claim vast en valt in de reap-/`To Resume`-flow. De kaart
blijft dus zichtbaar; ze wordt alleen als "sessie dood" behandeld i.p.v. als "wacht op mens", en de
vraag in de finale tekst is voor een mens alleen leesbaar door de transcript te openen. Dat is een
**bekende, gedocumenteerde ruwe rand**, geen blokkade voor deze beslissing — en het is een
constraint die de transport-kaart (`f418db32`) moet kennen, niet los werk. Vandaar een comment
daar, geen aparte kaart (§8).

## 7. Het besluit

1. **tmux blijft de interactieve transport.** Elke human-takeover loopt over een echte pane en de
   bestaande `PtyRelay`-UX. Er komt **geen** tweede, browser-side REPL bovenop input-streaming.
2. **Takeover = promotie.** De actie is: (indien nog levend) beëindig het headless proces →
   `spawn_session(mode="resume", session_id=…, directory=<worktree>)` → attach de pane. De sessie
   gaat verder met volledige historie; worktree, branch, claim en `session_id` blijven ongewijzigd.
3. **Geen transport-keuze vooraf.** Er is geen categorie "human-takeover-kaarten" die op tmux moet
   blijven. Élke headless kaart is promoveerbaar, dus de keuze valt op het moment dat een mens 'm
   werkelijk maakt. Dit vervangt de voorlopige clausule in `acp-transport-decision.md` §5
   ("*tot die UX bestaat, blijven human-takeover-kaarten op het tmux-transport*") — die UX bestaat
   nu, en ze vraagt geen categorisering.
4. **Promotie is eenrichtings.** Een gepromoveerde sessie gaat niet terug naar headless. Een mens
   die overneemt, maakt de kaart interactief af (of laat 'm los); terugdegraderen zou de mens
   midden in zijn werk de pane onder de voeten weghalen voor een marginale winst.
5. **Input-streaming blijft op de plank** — als autonome steering-capability (supervisor-agent,
   scheduled message naar een lopende run), niet als human-takeover-UX. Aparte aanleiding, aparte
   kaart.

### 7.1 De toestandsmachine

```
   dispatch (autonoom)                    mens klikt "Take over"
        │                                          │
        ▼                                          ▼
   ┌──────────┐   result/exit    ┌──────────┐  promote  ┌──────────────┐
   │ headless │ ───────────────► │ transcript│ ────────►│ tmux + pane  │
   │  run     │  (of: kill)      │  op schijf│  --resume│ (attachbaar) │
   └──────────┘                  └──────────┘           └──────────────┘
        ▲                                                      │
        │                              mens werkt af / laat los│
        └──────────  géén terugweg (§7 punt 4)  ───────────────┘
```

## 8. Vervolgkaarten

Aangemaakt in deze sessie (leaf-spike-clausule):

1. **[feature][transport] "Take over" — promoveer een headless run naar een tmux-pane**
   (`depends_on`: `f418db32` headless-transport). De actie uit §7 punt 2: endpoint + bord-actie die de
   headless run beëindigt, `spawn_session(mode="resume", …)` op dezelfde worktree draait, en het
   attach-target teruggeeft aan de bestaande terminal-UI.

Als **comment** op bestaande kaarten (geen duplicaat-kaart — dedup-pass gedaan):

- `f418db32` (headless-transport + derde liveness-bron): §6 (`success` ≠ taak af; "wacht op mens"
  is onzichtbaar in de exit) + de `pgrep`-val uit §9.
- `38e8096a` (`rate_limit` + `session_init` in het schema): de `system/init`-volgorde-correctie uit
  §9 — `init` is niet gegarandeerd het eerste event.

Niet aangemaakt (blijft §-proza tot er een concrete aanleiding is): input-streaming als autonome
steering-capability (§3) — speculatief tot een supervisor-flow het echt vraagt; en een
"wacht-op-mens"-classifier bovenop de finale tekst (§6) — dat vraagt eerst een draaiende transport
om tegen te meten.

## 9. Correcties op de dep-spike (gemeten, 2.1.210)

Drie dingen die een implementator zou raken:

1. **`system/init` is niet het eerste event.** De dep-spike §3 stelt: *"het eerste event … hij komt
   vóór elk ander event"*. Gemeten in een repo mét `SessionStart`-hooks komen er **vier
   `hook_started` + vier `hook_response`-events vóór `system/init`**. De `session_id` zit al op die
   hook-events. `init` blijft een prima readiness-indicator (hij komt vóór elk *inhoudelijk* event),
   maar wie "eerste regel van de stream = init" hardcodeert, breekt op elke repo met hooks.
2. **Het event-vocabulaire is rijker en versie-afhankelijk.** Naast de vijf gemeten top-level
   types zag deze sessie ook `system/background_tasks_changed`, `system/task_started`,
   `system/task_updated`, `system/task_notification` en `system/thinking_tokens`. Een parser moet
   onbekende `type`/`subtype`-waarden **overslaan i.p.v. te verwerpen** — `parse_structured_event`
   gooit vandaag `ValidationError` op een onbekend type (`structured-events-schema.md` §2).
3. **Liveness via process-matching is een val.** `pgrep -af "stream-json"` matchte in deze sessie
   de **eigen gedispatchte agent-sessie** (die zelf `claude` draait). Een
   `_live_headless_sessions()` die op cmdline-patronen matcht, levert dus false positives op een
   box met concurrente sessies — en een reaper die zichzelf levend waant, reap't nooit. Het
   sandcastle-precedent (DB-query op run-status) is niet alleen de makkelijkste route, maar de
   enige veilige.

## 10. Bewust buiten scope

- **De headless-transport zelf** — kaart `f418db32`; deze beslissing zegt alleen wat er met een
  mens gebeurt zodra die transport bestaat.
- **Multi-client viewing van een lopende headless run** (N kijkers op de event-stream zonder
  promotie). Legitiem, maar het is *observability*, geen takeover — en de bordzichtbare
  event-stream dekt de behoefte grotendeels.
- **Sandcastle** — een sandcastle-run heeft óók geen pane, maar zijn promotie-vraag is anders
  (het proces leeft in een container). Orthogonaal.
- **ACP's `session/request_permission` als gating-haak** — gepoort op de tweede-provider-kaart
  (`acp-transport-decision.md` §6 kaart 5); §4.1(c) van de dep-spike toonde al dat hij niet gratis
  meekomt.

## 11. Bronnen

- **Gemeten:** probes met `claude` 2.1.210 (`-p --output-format stream-json --verbose`,
  `-p --resume`, `tmux new-session … claude --resume`), echte subscription, 2026-07-15.
- **Code (read-only geverifieerd):** `runs/pty_relay.py:99,169` (attach = `tmux attach-session` +
  read_only-toggle), `api/v1/runs/router.py:223` (terminal-WS), `runs/cc_spawn.py:66-146`
  (`mode="resume"` → `--resume`, `runtime`-hint incl. `headless`), `kanban/session_recovery.py:52`
  (`_resolve_resume_target`), `kanban/schemas.py:21` (`To Resume`).
- **Intern:** [`headless-stream-json-transport-spike.md`](./headless-stream-json-transport-spike.md)
  (§4.1 schema-gaten, §5.1 vier-in-één identiteit, §6.2 liveness/exit),
  [`acp-transport-decision.md`](./acp-transport-decision.md) (§3 vergelijkingstabel, §5
  human-takeover-criterium), [`structured-events-schema.md`](./structured-events-schema.md),
  [`pane-attention-spec.md`](./pane-attention-spec.md).
</content>
