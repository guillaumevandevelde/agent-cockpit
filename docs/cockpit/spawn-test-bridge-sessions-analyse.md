# Test-gespawnde agent-bridge-sessies blokkeren auto-dispatch — analyse

> **Status:** analyse afgerond 2026-07-15 · leaf-spike op kaart `32f2c383` ("Analyse - Spawn agent test bridges").
> **Kern:** de klacht klopt, maar de oorzaak ligt één laag dieper dan "test-sessies worden niet opgekuist".
> De `SessionRegistry` telt sessie-slots **omhoog en nooit omlaag**. Ook een perfect
> afgeronde kaart lekt een slot. Test-bridge-sessies zijn de snelste manier om het
> plafond te bereiken, niet de enige.

## Aanleiding

De kaart meldt: sessies die "als test" een agent-bridge-sessie starten worden nadien niet
afgesloten/opgekuist en blokkeren daardoor **de volledige auto-dispatch-flow**. De vraag was
of dat klopt en wat eraan te doen.

Het symptoom klopt en is reproduceerbaar. De diagnose in de kaart — "het zijn de
test-sessies" — is echter een *versneller*, geen root cause. Hieronder de bewijsvoering.

## Bevinding 1 (root cause): `SessionRegistry._panes` is append-only

`backend/app/services/scheduling/session_registry.py`:

```python
def session_count(self) -> int:
    return len(self._panes) + len(self._external)

def can_add_session(self) -> bool:
    return self.session_count < self.effective_max_sessions
```

`_panes` wordt op precies één plek geschreven (regel 89, `self._panes[session_id] = tmux_pane`)
en **nergens verwijderd**. Een `grep` over de hele backend op `_panes` geeft vijf hits: de
initialisatie, de `len()`, een `in`-check, de write, en een `.get()`. Geen `del`, geen `.pop()`,
geen `.clear()`.

Elke `claude`-sessie met een tmux-pane die ooit één hook-event stuurt, bezet dus een slot
**voor de rest van de levensduur van het backend-proces**. Alleen een backend-herstart reset de
teller.

Alle drie de spawn-paden hangen aan diezelfde teller:

| Pad | Regel |
|---|---|
| worktree-transport (de normale auto-dispatch) | `dispatch.py:1582` |
| sandcastle-transport | `dispatch.py:1666` |
| resume-transport | `dispatch.py:3695` |

Vandaar "de **volledige** auto-dispatch-flow" — het is geen per-project-cap maar één gedeelde,
monotoon stijgende teller vóór elke spawn.

### Reproductie

Vijf sessies die netjes starten, stoppen, gekilld én "opgekuist" worden:

```
after 5 sessions started, stopped, killed and cleaned:
  session_count      = 5
  can_add_session()  = False
  cleanup_stale_sessions() returned = 0

=> record() for a NEW session -> False
```

De sessies zijn wég, de slots niet. Dit is het hele probleem in vier regels.

## Bevinding 2: de bestaande opkuis-paden raken `_panes` niet

Drie afzonderlijke mechanismen wekken de indruk dat dit afgedekt is. Geen van drie doet het:

1. **`cleanup_stale_sessions()`** (regel 166-175) is een dode stub. Hij roept `time.monotonic()`
   aan, gooit het resultaat weg en `return 0`, met als comment *"For now, just report the count -
   actual cleanup needs timestamp tracking"*. Hij heeft **nul callers**.
2. **`clear_spawn()`** — aangeroepen vanuit het kill-pad op kaart→Done (`dispatch.py:3645`) —
   ruimt `_spawn_times` en `_spawn_received_hooks` op, maar **niet `_panes`**. Het correcte,
   volledig doorlopen kaart-lifecycle lekt dus óók een slot.
3. **`release_external()`** werkt wél correct (sandcastle + dispatch releasen netjes). Die helft
   van `session_count` is gezond; `_panes` is het lek.

## Bevinding 3: `SessionEnd` wordt al ontvangen, maar genegeerd

Het platform krijgt het event dat "deze sessie is voorbij" betekent al binnen —
`presence_service.py:241` handelt `SessionEnd` expliciet af. De `SessionRegistry` kent het event
simpelweg niet:

```python
_IDLE_EVENTS = {"Stop"}
_BUSY_EVENTS = {"UserPromptSubmit", "SessionStart", "Notification"}
```

`SessionEnd` zit in geen van beide sets en triggert geen verwijdering. De informatie om het lek
te dichten is er dus al; er wordt alleen niets mee gedaan.

## Bevinding 4: twee ratchets die naar elkaar toe bewegen

`effective_max_sessions` valt terug op `estimated_max_sessions` uit de memory-monitor:

```python
usable_bytes = available * 0.8
estimated_max = max(2, int(usable_bytes / (100 * 1024 * 1024)))
```

Dat plafond is **dynamisch en daalt naarmate het geheugen voller loopt**, terwijl `session_count`
alleen stijgt. Op deze machine nu: 16.8 GB totaal, 14.1 GB vrij → plafond **107**. Zakt het vrije
geheugen naar ~2 GB, dan is het plafond nog **16**. Twee tegengestelde ratchets: er is geen
geheugendruk nodig om vast te lopen, en onder druk slaat het veel eerder toe dan 107.

## Bevinding 5: de foutmelding wijst de verkeerde kant op

Alle drie de gates gooien dezelfde melding:

```
Session limit reached (5/5). Memory: 15% used, 13562MB available.
```

De limiet is een **tellerlek**, maar de melding presenteert geheugen als de context. Bij 13.5 GB
vrij stuurt dat elke diagnose naar "meer RAM / minder parallellisme" in plaats van naar de teller.
Deze analyse begon zelf op dat verkeerde spoor; dat is de kost van de melding.

## Bevinding 6: sessies zonder kaart heeft niemand eigenaarschap over

Hier zit de kaart-specifieke klacht. Beide opkuis-mechanismen zijn **kaart-gescoped**:

- `session_cleanup.py` — docstring: *"Session cleanup when kanban cards complete."*
- `reap_stale_claims()` — itereert over `cards` en release't `agent:`-claims.

Een bridge-sessie die als test gespawnd wordt heeft **geen kaart, geen claim, geen worktree in de
verwachte vorm**. Daardoor:

- er is geen claim om te reapen,
- er is geen kaart-transitie die opkuis triggert,
- niets kilt ooit de tmux-sessie,
- de registry-slot komt nooit vrij, en
- de sessie blijft echt RAM opeten, wat het plafond uit bevinding 4 verder omlaag duwt.

`get_stuck_sessions()` ziet ze evenmin: die kijkt alleen naar `_spawn_times`, gevuld door de
dispatch-transports, en `_session_name_for_dispatched_cwd()` herkent uitsluitend het patroon
`<project>/.claude/worktrees/<naam>`. Een handmatig of test-gespawnde sessie valt per definitie
buiten die vorm.

De kaart heeft dus gelijk: deze sessies zijn wees. Maar ze zijn wees *bovenop* een lek dat ook
zonder hen bijt.

## Bevinding 7 (aanpalend): de agent-mail-roster vult zich met wegwerp-identiteiten

Tijdens het onderzoek viel op dat de roster in élke gedispatchte prompt (`Team: probe | probe2 |
… | test_agent_bridge_spawn_unknow0`) grotendeels rommel is. Meting op
`backend/claude_registry.db`:

```
TOTAL members: 25
  18  pytest tmp_path (test-lekkage)
   4  probe-dirs (handmatige agent-mail-test)
   2  session-scratchpad
   1  ECHTE repo
=> 96% rommel; 19/25 verwijzen naar een directory die niet meer bestaat
```

`test_agent_bridge_spawn_unknow0` is de pytest-`tmp_path` van
`test_agent_bridge_spawn_unknown_provider_smoke` (testnaam, afgekapt op 30 tekens + index).
Elke pytest-run maakt een nieuwe `pytest-N`-directory → nieuwe `repo_root` → nieuwe
identity-hash → nieuwe permanente rij.

De oorzaak is structureel identiek aan bevinding 1: `_get_or_create_repo_member()`
(`agent_mail_service.py:71`) maakt een identiteit aan voor **elke** cwd, keyed op een hash van het
pad, en er is geen enkele GC. Wegwerp-paden krijgen permanente identiteiten.

Dit **blokkeert geen dispatch** — het is contextvervuiling in elke prompt (en dus tokens), plus
een misleidende Team-lijst. Aparte kaart, lagere prioriteit.

## Wat dit betekent voor de fix

De vraag is niet "hoe kuisen we test-sessies op" maar **"waarom is een in-memory dict de bron van
waarheid voor iets waar tmux de bron van waarheid van is"**.

De registry bewaakt een *resource*-budget (draaiende sessies). De werkelijke toestand daarvan
leeft in tmux, niet in een dict die we hopen bij te werken. Elk fix-voorstel dat blijft leunen op
"iemand moet netjes opruimen" erft hetzelfde probleem: het dekt de netjes-afgesloten sessie en mist
de crash, de kill -9, de reboot, de handmatige test.

Aanbevolen richting, in volgorde van belang:

1. **Reconciliëren tegen tmux** (dragend, self-healing). Laat `session_count` de werkelijkheid
   volgen: verwijder `_panes`-entries waarvan de pane niet meer bestaat. Dekt crash, kill,
   test-spawn en reboot in één mechanisme, zonder dat iemand iets hoeft aan te roepen. Let op:
   `pane_for()` voedt de scheduled-messages-injectie, dus de mapping moet blijven — alleen dode
   entries mogen weg.
2. **`SessionEnd` verwerken** (snel pad). De informatie is er al (bevinding 3); dit maakt het
   vrijgeven prompt in plaats van pas bij de volgende reconciliatie. Op zichzelf onvoldoende —
   een gecrashte sessie stuurt nooit `SessionEnd` — dus altijd mét (1) als vangnet.
3. **Foutmelding eerlijk maken** (goedkoop, hoge diagnostische waarde). Onderscheid
   "tellerplafond bereikt" van "geheugendruk" en noem het aantal live panes.
4. **Wees-sessies zichtbaar maken** (de kaart-klacht). Een tmux-sessie zonder kaart en zonder
   eigenaar hoort ergens op te vallen; nu is ze volledig onzichtbaar.
5. **Identity-GC** (bevinding 7, apart en lager).

Bewust **niet** aanbevolen: het plafond verhogen, of tests verbieden bridge-sessies te spawnen.
Beide verplaatsen het moment van vastlopen zonder de ratchet weg te nemen.

## Vervolgkaarten

| Kaart | Bevinding |
|---|---|
| `[problem]` SessionRegistry lekt een slot per sessie — reconciliëren tegen tmux | 1, 2, 4 |
| `[problem]` SessionRegistry negeert `SessionEnd` | 3 |
| `[self-improve]` "Session limit reached" beschuldigt geheugen bij een tellerlek | 5 |
| `[problem]` Wees-tmux-sessies zonder kaart worden nooit opgekuist | 6 |
| `[chore]` Agent-mail-roster: GC voor wegwerp-identiteiten | 7 |

## Zie ook

- [`agent-bridge.md`](./agent-bridge.md) — spawn/relay; documenteert bewust géén sessie-lifecycle.
- [`kanban-dispatch-spec.md`](./kanban-dispatch-spec.md) — de dispatcher die op deze teller wacht.
</content>
