# Spike — per-sessie credential-/HOME-isolatie voor meerdere accounts binnen één vendor

**Datum:** 2026-07-13
**Status:** ✅ **AFGESLOTEN — NO-GO (fork beslist 2026-07-14)**. De §7-fork is door de
gebruiker beantwoord met **A / vendor-diverse** (zie §0.1). Same-vendor-multi-account speelt
niet; deze spike blijft geparkeerd als referentie mocht die beslissing ooit omdraaien. Geen
kind-kaarten (C1–C4) aangemaakt.
**Kaart:** `(Voorwaardelijk) Spike — per-sessie credential/HOME-isolatie` (host `e376f06a…`);
fork-beslissing op host-kaart `290f6fb7…`
**Bron:** [`subscription-flexibiliteit-analyse.md`](./subscription-flexibiliteit-analyse.md) §7 / §8 #5
**Verwant:** [`subscriptions.md`](./subscriptions.md) (subscription-identiteit + usage-provider),
[`kanban-dispatch-spec.md`](./kanban-dispatch-spec.md) (spawn-transport), [`agent-bridge.md`](./agent-bridge.md)

---

## 0.1 Fork-beslissing (2026-07-14) — A / vendor-diverse

De open §7-fork (dit document + `subscription-flexibiliteit-analyse.md` §7) is beslist op
**A — Nee, alleen multi-vendor**. De gebruiker draait verschillende vendors naast elkaar
(Anthropic + MiniMax + Codex …), **niet** meerdere accounts binnen één vendor. Gevolg:

- De subscription-identiteit blijft `{cli, provider}` — géén uitbreiding naar
  `{cli, provider, account}`. Fase 1a/1b (`710c85a5`/`c7b05504`) worden **niet** geraakt.
- De §6-decompositie (C1→C2→{C3,C4}) wordt **niet** geopend. Nul nieuwe kaarten.
- Deze spike blijft als beslis-artefact bewaard: áls de gebruiker later same-vendor gaat
  draaien, is de conditionele-GO-schets hieronder direct herbruikbaar.

De rest van dit document beschrijft het scenario dat door beslissing A **niet** gebouwd wordt;
het is bewust bewaard i.p.v. verwijderd, zodat de kost/ontwerp-grounding niet opnieuw hoeft.

---

## 0. TL;DR — go/no-go

- **De gating-vraag (§7-fork) is een menselijke beslissing en staat nog open.** Fase 0–2 nemen
  het **vendor-diverse** scenario aan (Anthropic + MiniMax + Codex …), wat de code vandaag
  modelleert. Deze spike is alleen relevant als de gebruiker bevestigt **meerdere accounts
  binnen dezelfde vendor** te draaien (bv. 2× Anthropic Max naast elkaar).
- **Aanbeveling: NO-GO / uitgesteld** — bouw niets tot de gebruiker de fork bevestigt. De
  fase-1-kaarten hangen **niet** van deze kaart af; niets is geblokkeerd door 'm uitgesteld te
  laten. Sluit deze kaart eventueel als *niet-nodig* zodra de gebruiker "vendor-diverse volstaat"
  bevestigt.
- **Maar áls de fork "same-vendor" is: CONDITIONELE GO — en het is klein.** De technische
  isolatie is **verrassend goedkoop** dankzij hoe `spawn.py` de sessie-env al bouwt: één extra
  env-var (`CLAUDE_CONFIG_DIR`) per account, geïnjecteerd op het bestaande `-e KEY=VALUE`-punt.
  Geen nieuwe spawn-mechaniek. De echte kost zit in het **account-beheer** (registratie,
  interactieve OAuth-login per config-dir, usage-signaal per account), niet in de isolatie zelf.

Dit document levert: (§1) de fork die de gebruiker moet beslissen, (§2) de technische
grounding waaróm het vandaag niet kan en waar de fix inhaakt, (§3) het isolatie-ontwerp met
afweging `CLAUDE_CONFIG_DIR` vs `HOME`, (§4) de impact op fase 1a/1b, (§5) open risico's, en
(§6) een go/no-go + decompositie in kind-kaarten voor het geval de fork "same-vendor" is.

---

## 1. De fork die de gebruiker moet beslissen (§7)

> **Draai je meerdere accounts binnen dezelfde vendor (bv. twee Anthropic-abonnementen naast
> elkaar), of steeds verschillende vendors (Anthropic + MiniMax + Codex + …)?**

| Antwoord | Gevolg | Deze spike |
|---|---|---|
| **Vendor-diverse** (waarschijnlijkste lezing; wat de code modelleert) | subscription = `{cli, provider}`; fase 0–2 zoals ontworpen volstaan. | **niet nodig** → kaart als *niet-nodig* sluiten. |
| **Same-vendor-multi-account** (bv. 2× Anthropic Max) | subscription = `{cli, provider, account}`; per-sessie credential-isolatie nodig. | **relevant** → §6-decompositie openen. |

Waarom vendor-diverse de default is: één Claude Code authenticeert via één OAuth-credential
(`~/.claude/.credentials.json`). Twee Anthropic-accounts "naast elkaar" bestaan in het huidige
model simpelweg niet — het onderscheid dat de code kent is `default_agent` (CLI) en
`default_provider` (vendor-backend), niet "welk account binnen die vendor".

**Deze spike beslist de fork niet** — dat is expliciet de gebruiker gevraagd. Het levert de
kosten/ontwerp-schets zodat de beslissing goedkoop te nemen is.

---

## 2. Technische grounding — waarom het vandaag niet kan, en waar de fix inhaakt

Onderzocht: `backend/app/services/runs/spawn.py` (tmux-spawn + env-injectie),
`backend/app/services/agentic_cli/provider_env.py` (provider→env-mapping),
`backend/app/services/usage_service.py` (Anthropic 5h-schatting uit JSONL-logs).

### 2.1 Hoe de sessie-env vandaag gebouwd wordt

`spawn.py` bouwt een **expliciete** `merged_env` en injecteert die via tmux-`-e`-vlaggen:

```python
# spawn.py (samengevat, rond regel 249-298)
provider_env = build_provider_env(options.provider, ...)   # {} voor anthropic
merged_env = {}
merged_env.update(cleaned_extras)      # caller-resolved secrets
merged_env.update(provider_env)        # AWS_REGION / MiniMax-creds
merged_env["COCKPIT_PROJECT_KEY"] = project_key
merged_env["COCKPIT_RUNTIME"] = effective_runtime
env_flags = []
for key, value in merged_env.items():
    env_flags += ["-e", f"{key}={value}"]
subprocess.run(["tmux", "new-session", "-d", "-s", name, "-c", directory, *env_flags, cmd])
```

Twee load-bearing feiten uit die code (comment in `spawn.py`: *"NO os.environ.update — every
var must come from an explicit, auditable input"*):

1. **`os.environ` wordt bewust NIET in de spawn gemerged.** Alleen wat expliciet in
   `merged_env` staat bereikt de agent.
2. **`HOME` en `CLAUDE_CONFIG_DIR` staan vandaag NIET in `merged_env`.** tmux `-e` *voegt toe /
   overschrijft*; alles wat niet in de vlaggen staat komt uit de **tmux-server-omgeving** (de
   `HOME` waarmee de tmux-server ooit startte). Elke sessie erft dus dezelfde `HOME` →
   dezelfde `~/.claude/.credentials.json`.

### 2.2 Waarom dat same-vendor-parallelisme blokkeert

Claude Code leest/schrijft zijn OAuth-credential in `$CLAUDE_CONFIG_DIR/.credentials.json`
(default `~/.claude/`). Twee Anthropic-accounts die tegelijk draaien delen vandaag **hetzelfde
bestand**: ze zouden elkaars token overschrijven bij refresh, en er is geen manier om sessie A
naar account 1 en sessie B naar account 2 te wijzen. Dat is precies de "de code modelleert dit
niet"-observatie uit analyse §7 — nu geconcretiseerd tot **de gedeelde credential-file + de
gedeelde tmux-server-`HOME`**.

### 2.3 Het goede nieuws: de fix haakt op één bestaand punt in

Omdat de env al 100% expliciet is opgebouwd, is per-sessie isolatie **één extra regel in
`merged_env`**: zet `CLAUDE_CONFIG_DIR` (of `HOME`) naar een account-specifieke map. Geen nieuwe
transport, geen wijziging aan de tmux-aanroep zelf — alleen een extra sleutel in de dict die al
via `-e` geïnjecteerd wordt. Dat is de reden dat de isolatie-kost laag is (§0).

---

## 3. Isolatie-ontwerp — `CLAUDE_CONFIG_DIR` vs `HOME`

Twee kandidaat-mechanismen om per sessie een ander account te selecteren:

| | `CLAUDE_CONFIG_DIR=<per-account-dir>` ⭐ | `HOME=<per-account-home>` |
|---|---|---|
| **Wat het isoleert** | Alleen Claude Code's config + `.credentials.json` (+ JSONL-usage-logs). | *Alles* wat `~` leest: git-config, ssh, gh-token, npm, caches… |
| **Chirurgisch?** | Ja — raakt precies de OAuth-credential. | Nee — breed; breekt makkelijk git-identiteit / gh-auth in de worktree. |
| **Usage-signaal** | JSONL-logs landen mee in de config-dir → **per-account usage gratis gescheiden** (zie §4). | Idem, maar met alle neveneffecten van een verplaatste `HOME`. |
| **Blast radius bij fout** | Laag. | Hoog (agent kan naar verkeerde git-remote pushen of gh-auth verliezen). |

**Keuze: `CLAUDE_CONFIG_DIR`.** Het is de chirurgische, door Claude Code ondersteunde weg om de
credential-store (en de usage-JSONL) per account te verplaatsen, zonder de rest van de
worktree-omgeving overhoop te halen. `HOME`-swap is een fallback als een CLI géén
config-dir-override kent (Codex/Copilot — maar die vallen buiten Anthropic-same-vendor en
buiten deze spike-scope).

> ⚠️ **Verify-before-trust:** dat Claude Code `CLAUDE_CONFIG_DIR` respecteert voor
> `.credentials.json` is de aanname waarop dit ontwerp rust. De eerste kind-kaart (§6, C1) moet
> dit **empirisch bevestigen** (twee config-dirs, twee ingelogde accounts, parallelle spawn)
> vóórdat er pool-code op gebouwd wordt. Geen fabricage: als de override niet werkt zoals
> aangenomen, is dat een no-go-signaal dat de kaart moet rapporteren, niet omzeilen.

### 3.1 Éénmalige login per account (de echte kost)

OAuth-login is interactief. Per account moet er één keer `CLAUDE_CONFIG_DIR=<dir> claude`
(login-flow) door de mens gedraaid worden zodat elke dir een geldig `.credentials.json` krijgt.
Dat is **mensenwerk**, niet automatiseerbaar in de dispatcher — de spike moet dit als
handmatige registratie-stap erkennen, niet wegpoetsen. Token-refresh daarna is per config-dir
zelfstandig.

---

## 4. Impact op fase 1a / 1b

De analyse (§7) waarschuwde: same-vendor maakt de subscription-identiteit
`{cli, provider}` → `{cli, provider, account}`. Concreet per fase:

- **Fase 1a (`SubscriptionUsageProvider`)** — de Anthropic-provider leest usage uit de lokale
  JSONL-logs via `UsageService`. Die logs staan in de config-dir. Met per-account
  `CLAUDE_CONFIG_DIR` splitsen ze **vanzelf** per account → 1a hoeft alleen de config-dir-locatie
  als parameter te nemen i.p.v. hardcoded `~/.claude/`. Klein: één pad-argument, geen nieuwe
  bron. Het `subscription_prefs`-record krijgt een `account`-discriminator.
- **Fase 1b (pool + `pick_subscription()`)** — de pool-entry en de dispatch-injectie worden
  `{cli, provider, account}`. De router kiest niet alleen een provider maar ook een
  config-dir, en zet die als `CLAUDE_CONFIG_DIR` in de spawn-env (het §2.3-injectiepunt).
  `pick_subscription()`'s sleutel wordt een tripel. Beheersbaar, maar het raakt de
  pool-datamodel-vorm — daarom moet de fork **vóór** 1b beslist zijn (zoals §7 al stelde).
- **Per-provider pause / dispatch** — de pause-slot-sleutel
  (`dispatch_paused_until:<provider>`) zou `<provider>:<account>` moeten worden zodat account 1
  z'n limiet raken account 2 niet pauzeert. Eén sleutel-uitbreiding.

**Netto:** same-vendor verzwaart fase 1 niet *substantieel* qua isolatie-mechaniek (dankzij
§2.3), maar het verbreedt de **identiteit** die door 1a/1b/pause loopt van een paar naar een
tripel. Dat is een datamodel-beslissing die goedkoop is om vóóraf te nemen en duur om
achteraf in te weven — vandaar de fork-gate.

---

## 5. Open risico's / eerlijkheids-caveats

1. **De `CLAUDE_CONFIG_DIR`-aanname is onbevestigd** (§3 ⚠). C1 moet empirisch verifiëren.
2. **Interactieve OAuth-login per account is mensenwerk** (§3.1) — niet door de dispatcher te
   automatiseren; registratie-UX is een aparte, niet-triviale brok.
3. **Anthropic-overschot blijft een schatting** (analyse §6 #1) — per-account splitst het
   signaal netjes, maar maakt het niet exacter. Nooit als exact getal tonen.
4. **Same-vendor ≠ meer totale quota** — twee Max-accounts zijn twee aparte facturen; dit is
   "benut elk account tot z'n eigen limiet", geen credit-pooling (analyse §6 #4).
5. **Scope-creep-risico:** een `HOME`-swap "voor de zekerheid" zou git/gh in de worktree breken
   (§3). Houd de isolatie chirurgisch op `CLAUDE_CONFIG_DIR`.

---

## 6. Go/no-go + decompositie (alleen uit te voeren als de fork "same-vendor" is)

**Go/no-go:** **conditionele GO.** Als de gebruiker same-vendor bevestigt, is dit een klein,
goed afgebakend spoor — geen fundamentele herbouw. Als de gebruiker vendor-diverse bevestigt:
**no-go**, sluit de kaart als niet-nodig.

Voorgestelde kind-kaarten (te openen ná fork-bevestiging; niet nu):

- **C1 — Verificatie-spike: `CLAUDE_CONFIG_DIR`-isolatie bewijzen** (`analysis`). Twee
  config-dirs met elk een ingelogd Anthropic-account; bevestig dat een parallel gespawnde
  sessie met `CLAUDE_CONFIG_DIR=A` en één met `=B` onafhankelijke credentials + onafhankelijke
  usage-JSONL gebruiken. Lever: bevestigd/ontkracht + observaties. **Gate voor C2/C3.**
- **C2 — Account-registratie + `subscription_prefs.account`** (`feature`, hangt af van C1).
  Datamodel + UI om per Anthropic-account een `CLAUDE_CONFIG_DIR`-pad + label te registreren
  (handmatige login-stap gedocumenteerd, niet geautomatiseerd). Subscription-identiteit wordt
  `{cli, provider, account}`.
- **C3 — `CLAUDE_CONFIG_DIR`-injectie in `spawn.py`** (`feature`, hangt af van C2). Voeg de
  gekozen account-config-dir toe aan `merged_env` op het bestaande `-e`-punt; per-account
  pause-slot-sleutel (`<provider>:<account>`). Landt op het §2.3-injectiepunt.
- **C4 — Fase 1a/1b account-dimensie** (`feature`, hangt af van C2). `SubscriptionUsageProvider`
  + `pick_subscription()` nemen `account` mee (config-dir-pad als usage-bron-parameter; pool-key
  wordt een tripel). Ideaal samengevoegd mét of direct na de vendor-diverse fase 1.

**DAG:** C1 → {C2} → {C3, C4}. C1 is de go/no-go-poort: valt de `CLAUDE_CONFIG_DIR`-aanname
weg, dan stopt het spoor daar en is een `HOME`-swap-heroverweging (met alle §3-neveneffecten)
nodig vóór verder gegaan wordt.

---

## 7. Wat deze spike NIET doet

Geen code, geen kind-kaarten aangemaakt (die openen pas ná fork-bevestiging), en géén
beslissing van de fork zelf — die is expliciet aan de gebruiker. Dit document is het
beslis-artefact dat de fork goedkoop maakt om te nemen.
