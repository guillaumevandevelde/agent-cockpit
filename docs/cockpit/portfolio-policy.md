# Portfolio-cap policy: waarde, scope, failure-mode

> **Design-only.** Deze doc legt de drie open beleids-keuzes voor de
> portfolio-cap vast (bron: `portfolio-orchestratie.md` §7 #4, sub-keuzes
> uit §5), zodat de implementatie een concreet recept heeft. Ze beschrijft
> **wat** en **waarom**, niet het **hoe** van nieuwe code.
>
> **Status van de implementatie.** De portfolio-cap zélf is al gebouwd
> (kaart #3, commit `d1eeae7` — `run_dispatch_tick` in
> `backend/app/kanban/dispatch.py:2822-2838`, config in
> `backend/app/config.py:89-93`). Deze doc **ratificeert** de keuzes die
> die implementatie impliciet maakte, maakt ze expliciet, en benoemt de
> alternatieven + gefaseerde rollout die nog open staan. Waar de huidige
> code afwijkt van de aanbeveling wordt dat expliciet gemarkeerd als
> *follow-up*, niet stilzwijgend "goedgekeurd".

## 0. Samenvatting (de drie keuzes in één tabel)

| Keuze | Gekozen waarde | Kern-rationale |
|---|---|---|
| **1. Cap-waarde** | Memory-aware default (`min(effective_max_sessions, 4)`) met env-override (`PORTFOLIO_CAP_VALUE`). | De limiet daalt automatisch mee met hardware-druk; power-users/forks kunnen overschrijven. |
| **2. Scope** | Alleen autodispatch-enabled projecten. Handmatige UI-sessies omzeilen de cap. | Handmatig starten is een mens-bewuste keuze; het portfolio-mechaniek stuurt alleen de autonome bus. |
| **3. Failure-mode** | Skip-tick (huidig gedrag), met audit-logregel. Wait-and-retry is een latere iteratie. | Skip is stateless en zelf-corrigerend (de volgende tick, 10 s later, probeert opnieuw); een wachtrij voegt state + faalmodi toe zonder de harde starvation al op te lossen. |

Alle drie sluiten aan bij variant 2 ("kind-tag + portfolio-cap") uit
`portfolio-orchestratie.md` §4. Fair-scheduling (variant 3) en
security-policies (facet D) zijn expliciet **out-of-scope** — zie §6.

---

## 1. Keuze 1 — Cap-waarde

### 1.1 Gekozen waarde

**Memory-aware default met configureerbare override.**

- **Default:** `min(session_registry.effective_max_sessions, 4)`.
  `effective_max_sessions` is de hardware-aware bovengrens uit de
  memory-monitor (`backend/app/services/scheduling/session_registry.py:42-48`)
  en daalt zelf al onder geheugendruk. De `min(…, 4)` legt daar een
  vaste portfolio-plafond bovenop zodat we ook op een dikke machine niet
  ongelimiteerd parallelle product-projecten spawnen.
- **Override:** één env-knob, `PORTFOLIO_CAP_VALUE` (pydantic-settings,
  case-insensitive). Zet 'm expliciet en de memory-aware default wordt
  genegeerd — de bewuste keuze van de operator wint.
- **Aan/uit:** de hele cap zit achter `PORTFOLIO_CAP_ENABLED` (default
  `false`), zodat de rollout gefaseerd kan (zie §1.4).

### 1.2 Rationale

1. **Memory-aware is het minst verraderlijk.** Het echte probleem (§2.3
   #2 in de orchestratie-doc) is dat vijf product-projecten samen het
   geheugen-budget opslokken. Een cap die niet met datzelfde budget
   meebeweegt lost dat maar half op: op een krappe machine wil je een
   lagere cap dan op een dikke. Door de bovengrens van de memory-monitor
   te erven, daalt de cap automatisch mee wanneer het OS onder druk komt.
2. **De harde `4` voorkomt "genoeg RAM ⇒ spawn maar raak".** Portfolio-
   parallellisme heeft naast RAM ook andere kosten (rate-limits van het
   Claude-abonnement, cognitieve last voor de mens die de boards volgt).
   Vier gelijktijdige product-sessies is een verdedigbaar startplafond;
   de override laat het aanpassen.
3. **Eén override-as, niet vier.** De bron-doc noemt vier kandidaten
   (config, per-device, per-fork/env, per-project). We kiezen bewust
   **één**: env is tegelijk per-device én per-fork (elke checkout/host
   zet zijn eigen env), dekt het power-user-scenario, en voegt geen
   schema- of UI-oppervlak toe. Per-project cap is een *fairness*-as
   (verschillende projecten verschillende plafonds) en hoort dus bij
   variant 3 — buiten scope.

### 1.3 Alternatieven

| Alternatief | Waarom niet (nu) |
|---|---|
| **Hardcoded constante** (bv. altijd `4`) | Beweegt niet mee met geheugendruk; op een krappe machine te hoog, op een dikke onnodig laag. De memory-aware default is strikt beter voor dezelfde code-kosten. |
| **Per-project cap-kolom** (`projects.portfolio_cap`) | Schema + migratie + UI + de vraag "wie zet welk plafond" — dat is een fairness-beslissing (variant 3), niet de minimale cap. Uitgesteld. |
| **Per-device (aparte device-tabel-knob)** | Env dekt device-lokaliteit al (device = checkout/host = eigen env). Een extra device-tabel-veld is dubbelop. |
| **Puur config.py-constante zonder env** | Dwingt een code-edit + herstart-per-fork af; env geeft dezelfde flexibiliteit zonder de repo te patchen. |

### 1.4 Rollout, gefaseerd

1. **Fase 0 (nu):** `PORTFOLIO_CAP_ENABLED=false`. Code aanwezig, dead
   path. Geen gedragsverandering.
2. **Fase 1 — observeer:** zet de flag aan op één device met een
   *ruime* `PORTFOLIO_CAP_VALUE` (bv. 6) en lees de audit-logregel
   ("portfolio-cap reached …"). Meet hoe vaak de cap überhaupt raakt bij
   het huidige aantal projecten. Raakt-ie nooit, dan is de default goed
   genoeg; raakt-ie te vaak, herzie de waarde.
3. **Fase 2 — default aan:** flip `PORTFOLIO_CAP_ENABLED` default naar
   `true` zodra fase 1 laat zien dat de cap alleen in de bedoelde
   overbelasting-situatie triggert (niet in normaal werk).

> **⚠ Follow-up (memory-aware is nu statisch, niet dynamisch).** De
> huidige implementatie evalueert `_default_portfolio_cap_value()` via
> `Field(default_factory=…)` **één keer bij proces-start**
> (`config.py:93`). Daarmee is de cap memory-aware op *boot-moment*, maar
> volgt hij een geheugendruk die ná de start ontstaat niet meer. De
> per-project cap-check in `run_dispatch_tick` leest daarentegen
> `effective_max_sessions` wél live. Voor een écht dynamische
> portfolio-cap moet de waarde per tick worden herberekend i.p.v. uit
> `settings.portfolio_cap_value` gelezen. Dit is een bewuste
> vereenvoudiging voor de eerste versie; de `PortfolioPolicy`-blauwdruk
> (§4) codeert het onderscheid via `resolve_cap()` zodat de executor de
> keuze expliciet ziet. **Aanbeveling:** houd fase 1/2 op de statische
> variant; til dynamische herberekening pas op als de audit-log laat
> zien dat post-boot geheugendruk de cap onbetrouwbaar maakt.

---

## 2. Keuze 2 — Scope

### 2.1 Gekozen waarde

**De cap geldt alleen over autodispatch-enabled projecten. Handmatig
gestarte UI-sessies ("Start session") omzeilen het portfolio-mechaniek.**

Concreet telt de check de `agent:`-claims op kaarten in agent-kolommen
(niet Backlog/Impediment/Done) over precies de set projecten die
autodispatch-enabled is — de `enabled`-verzameling in
`run_dispatch_tick`. De check dráait ook alleen binnen de auto-dispatch
tick; een handmatige spawn passeert dit codepad niet.

### 2.2 Rationale

1. **Handmatig starten is mens-bewust.** Wie via de UI expliciet een
   sessie start, neemt bewust de resource-kost. De portfolio-cap bestaat
   om de *autonome* bus te temmen (het scenario waarin niemand kijkt en
   vijf projecten tegelijk spawnen), niet om een mens te blokkeren die
   net besloot dat dit werk nú moet.
2. **De harde geheugen-rem blijft sowieso staan.** Handmatige sessies
   ontsnappen wél aan de *portfolio*-cap, maar niet aan de globale
   `SessionRegistry`-limiet (`can_add_session`,
   `session_registry.py:55-57`). Een mens kan dus niet grenzeloos
   spawnen — de hardware-ceiling geldt onverminderd. De portfolio-cap is
   een *extra*, autonoom-gerichte laag daarbovenop, geen vervanging.
3. **Consistent met de bestaande scheiding.** Autodispatch is al
   per-project, per-device opt-in en bewust *niet* in de synced op-log
   (`dispatch.py`, `autodispatch:<project_key>`). De cap plakt netjes op
   dezelfde "autonome activiteit"-grens.

### 2.3 Alternatieven

| Alternatief | Waarom niet |
|---|---|
| **Cap ook over handmatige sessies** | Zou een mens die bewust werk start kunnen weigeren — verwarrend ("waarom start mijn sessie niet?") en in strijd met het idee dat de mens de baas blijft. De globale memory-rem vangt excessief handmatig spawnen al af. |
| **Cap over álle projecten (ook autodispatch-uit)** | Een project met autodispatch uit spawnt sowieso niets autonoom; meetellen in de cap zou de autonome bus onterecht knijpen op basis van slapende projecten. |
| **Aparte cap voor handmatig vs autonoom** | Twee knoppen voor een probleem dat één knop oplost. De globale registry-limiet ís al de "totale" cap; een tweede handmatige cap is premature. |

### 2.4 Rollout, gefaseerd

Scope-keuze heeft geen aparte rollout: ze valt samen met de
feature-flag van keuze 1. Bij het aanzetten van `PORTFOLIO_CAP_ENABLED`
geldt automatisch de autodispatch-only scope. Wél te **observeren** in
fase 1: raakt de cap terwijl er handmatige sessies lopen? Dan bevestigt
de audit-log dat de autonome bus correct wordt geknepen zonder de mens
te blokkeren. Zie je juist dat handmatige sessies de machine belasten
maar de portfolio-cap niets doet, dan is dat *by design* — die last
hoort bij de globale memory-rem, niet bij deze cap.

---

## 3. Keuze 3 — Failure-mode

### 3.1 Gekozen waarde

**Skip-tick.** Wordt de cap bereikt, dan slaat `run_dispatch_tick` de
hele tick over met één audit-logregel ("portfolio-cap reached
(N/M active …); skipping tick") en keert direct terug. De volgende tick
(default elke 10 s, `kanban_dispatch_interval_seconds`) probeert het
opnieuw. Geen wachtrij, geen gereserveerde slots, geen retry-state.

### 3.2 Rationale

1. **Stateless en zelf-corrigerend.** De dispatcher draait al op een
   vaste interval. "Skip deze tick" is niets anders dan wachten op de
   volgende poll — er hoeft niets onthouden te worden. Zodra een sessie
   vrijkomt, pakt een volgende tick vanzelf de draad op. Er is geen
   wachtrij die kan vervuilen, geen reservering die kan verlopen, geen
   dead-letter-scenario.
2. **De faalmodi van een wachtrij wegen niet op tegen de winst.** Een
   wait-and-retry-mechaniek moet beslissen: *welk* project mag als de
   cap vrijkomt (dat is precies de fairness-vraag van variant 3), hoe
   lang een reservering geldig blijft, wat er gebeurt als de wachtende
   kaart intussen verplaatst/geclaimd wordt. Dat is een scheduler-
   herschrijving. Skip-tick lost het *harde* probleem (starvation van
   het budget) al op; de *zachte* fairness ("wie het eerst vroeg wint
   niet netjes genoeg") is een aparte, latere as.
3. **De 10-seconden-interval maakt de latency verwaarloosbaar.** Het
   verschil tussen "skip en probeer over 10 s opnieuw" en "reserveer en
   dispatch zodra vrij" is in de praktijk hooguit één interval. Voor een
   autonome achtergrond-bus is dat ruim binnen de tolerantie.

### 3.3 Trade-off (expliciet, want de bron-doc vraagt erom)

| | Skip-tick (gekozen) | Wait-and-retry (uitgesteld) |
|---|---|---|
| **Complexiteit** | Minimaal — één `return`. | Wachtrij + reserveringen + expiry + fairness-tiebreak. |
| **State** | Geen. | Persistente of in-memory queue die consistent moet blijven met de board. |
| **Fairness** | "Wie in de volgende tick als eerste langskomt." Niet gegarandeerd eerlijk over projecten. | Kan eerlijk zijn — maar dan bouw je feitelijk variant 3. |
| **Latency bij vrijkomen slot** | ≤ 1 tick-interval (~10 s). | ~0 s (direct). |
| **Faalmodi** | Geen nieuwe. | Verlopen reserveringen, race met handmatige claims, queue-drift. |

De enige echte winst van wait-and-retry is *fairness + iets lagere
latency*, en fairness is bewust een aparte iteratie (variant 3). Daarom:
skip-tick nu, wait-and-retry pas als een fair-scheduler er tóch komt —
dan valt de wachtrij natuurlijk samen met de weging.

### 3.4 Rollout, gefaseerd

1. **Fase 1 (nu, met de flag aan):** skip-tick + audit-log. **Meet** via
   de log hoe vaak en hoe lang de cap aaneengesloten raakt. Blijft-ie
   nooit lang vol, dan is skip-tick definitief genoeg.
2. **Fase 2 (voorwaardelijk):** blijkt uit de log dat één project de cap
   structureel vol houdt terwijl een ander wacht (de starvation die de
   bron-doc §2.3 #3/#4 vreest), dan is dát het signaal om variant 3
   (fair scheduling) op te pakken — inclusief de wachtrij. Niet eerder:
   zonder dat bewijs bouw je complexiteit voor een probleem dat zich
   misschien niet voordoet.

---

## 4. Blauwdruk — `PortfolioPolicy`-dataclass

> **Geen implementatie.** Dit is een blauwdruk die de drie keuzes als
> defaults codeert, zodat de implementatie-kaart een concreet startpunt
> heeft. De executor mag afwijken mits gemotiveerd; de velden hieronder
> zijn het contract van *welke* knoppen bestaan en wat hun default is.

```python
from dataclasses import dataclass
from enum import Enum


class CapFailureMode(str, Enum):
    """Wat run_dispatch_tick doet als de portfolio-cap bereikt is."""
    SKIP_TICK = "skip_tick"        # gekozen: sla de tick over, probeer bij de volgende
    WAIT_RETRY = "wait_retry"      # uitgesteld: wachtrij + dispatch zodra een slot vrijkomt


@dataclass(frozen=True)
class PortfolioPolicy:
    """Beleids-blauwdruk voor de portfolio-cap. Defaults = de keuzes uit
    docs/cockpit/portfolio-policy.md. Een frozen dataclass omdat een policy
    binnen één tick niet mag muteren; per proces één instantie.
    """

    # --- Keuze 1: waarde ---------------------------------------------------
    enabled: bool = False
    """Feature-flag. Default uit ⇒ gefaseerde rollout. Mapt op
    settings.portfolio_cap_enabled."""

    cap_value_override: int | None = None
    """Expliciete override (env PORTFOLIO_CAP_VALUE). None ⇒ gebruik de
    memory-aware default via resolve_cap()."""

    hard_ceiling: int = 4
    """Bovengrens bovenop de memory-aware waarde: min(mem_budget, hard_ceiling)."""

    dynamic_cap: bool = False
    """False ⇒ cap één keer bij boot vastgezet (huidige implementatie:
    Field(default_factory=...)). True ⇒ per tick herberekenen zodat
    post-boot geheugendruk meetelt (§1.4 follow-up)."""

    # --- Keuze 2: scope ----------------------------------------------------
    autodispatch_only: bool = True
    """True ⇒ tel alleen agent-claims over autodispatch-enabled projecten;
    handmatige UI-sessies vallen buiten de cap (blijven wel onder de globale
    SessionRegistry-limiet)."""

    # --- Keuze 3: failure-mode --------------------------------------------
    failure_mode: CapFailureMode = CapFailureMode.SKIP_TICK

    # --- Afgeleide waarde --------------------------------------------------
    def resolve_cap(self, memory_budget: int) -> int:
        """De effectieve cap voor deze tick.

        memory_budget = session_registry.effective_max_sessions op het moment
        van aanroep. Bij dynamic_cap=True roept de tick dit elke keer aan met
        de live waarde; bij False wordt het resultaat bij boot bevroren.
        Een expliciete override wint altijd — dan is de operator de baas.
        """
        if self.cap_value_override is not None:
            return self.cap_value_override
        return min(memory_budget, self.hard_ceiling)
```

**Mapping op de huidige code** (voor de implementatie-kaart, zodat de
blauwdruk en de al-gemergede code niet uit elkaar lopen):

| Blauwdruk-veld | Huidige plek | Gelijk? |
|---|---|---|
| `enabled` | `settings.portfolio_cap_enabled` | ✅ |
| `cap_value_override` + `hard_ceiling` → `resolve_cap` | `settings.portfolio_cap_value` (`Field(default_factory=_default_portfolio_cap_value)`) | ✅ waarde-gelijk; de blauwdruk splitst alleen override en ceiling expliciet |
| `dynamic_cap` | *ontbreekt* — huidige waarde is statisch bij boot | ⚠ follow-up (§1.4) |
| `autodispatch_only` | impliciet: de check itereert over `enabled` | ✅ (impliciet True) |
| `failure_mode` | impliciet `SKIP_TICK`: `logger.info(...); return` | ✅ (impliciet) |

De blauwdruk voegt dus geen nieuw gedrag toe; hij maakt de vijf
impliciete beslissingen van de huidige code *benoembaar* en zet
`dynamic_cap` klaar als de enige echte openstaande knop.

---

## 5. Consolidatie-checklist voor de implementatie-kaart

Als een executor deze policy in code giet (of de bestaande code
verfijnt), is dit het contract:

1. Cap-waarde memory-aware (`min(effective_max_sessions, 4)`) met
   `PORTFOLIO_CAP_VALUE`-override — **al aanwezig**.
2. Feature-flag `PORTFOLIO_CAP_ENABLED`, default `false` — **al aanwezig**.
3. Scope = autodispatch-only, handmatig omzeilt — **al aanwezig**.
4. Failure-mode = skip-tick + audit-log — **al aanwezig**.
5. `dynamic_cap` als expliciete knop overwegen (§1.4) — **open follow-up**,
   alleen oppakken als de audit-log post-boot geheugendruk als probleem
   aantoont.
6. Fair-scheduling / wait-retry — **niet nu** (variant 3, §6).

---

## 6. Out-of-scope (expliciet)

- **Fair-scheduling / gewogen round-robin over projecten** — variant 3
  in `portfolio-orchestratie.md` §4; een latere design-iteratie. De
  wachtrij-failure-mode hoort daarbij.
- **Per-project prioriteit / cap-kolom** — fairness-as, valt onder
  variant 3.
- **Security-policies op basis van de `kind`-tag** (meta mag het platform
  wijzigen, product niet) — facet D. Deze doc raakt de tag niet.
- **Stale-project-detectie** — §7 #5 in de bron-doc, aparte kaart.
- **Dynamische per-tick herberekening van de cap** — benoemd als
  follow-up (§1.4), niet als onderdeel van deze policy-ratificatie.
