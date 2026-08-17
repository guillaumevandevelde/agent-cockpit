---
title: "Design — wekelijkse PR-triage tegen CI-staleness"
type: design
status: draft
---

# Design — wekelijkse PR-triage tegen CI-staleness

> Companion van kanban-kaart *"[problem] 14 dependabot + 2 human PRs 18 dagen
> stale op CI spending-limit-block"* (`6fd60cb7d7024e28b514e0c6f6805be6`).
> Prototype-script: `scripts/sweep_open_prs.py`.

## 1. Probleem en uitkomst

**De uitkomst.** Een wekelijkse klok-triggert kaart vuurt de
PR-triage-sweep, die de operator in één oogopslag laat zien welke open PR
wachten op CI-reset (billing-block, geen actie) versus welke écht stuk zijn
(handmatige review). De stale-pile ruimt zichzelf binnen een week na elke
CI-reset, of wordt expliciet als *"wachten op owner-decision"* gemarkeerd.

**Het probleem.** Sinds 2026-07-26 weigert GitHub Actions elke Quality-run
met de Anthropic-spending-limit (zie CLAUDE.md §Gotchas, *"spending-limit
sinds 2026-07-26"*). Zonder weekly triage stapelt de dependabot-pile zich op:
op het moment van deze kaart 14 dependabot- en 2 menselijke PRs, allemaal
18+ dagen oud. Geen Quality-run = geen merge = geen schoon bord.

**Voorgestelde fix** (verbatim uit de kaart): *"cron-achtige of scheduled-
kaart die wekelijks (1) open PRs opsomt, (2) per PR kijkt of de failure
spending-limit vs echte-diff is, (3) stale dependabot-pile markeert."*

## 2. Architectuur in één diagram

```
┌─────────────────┐    scheduled_at    ┌──────────────────┐
│ Vorigge triage- │ ─────────────────▶ │ Nieuwe triage-   │
│ kaart (Backlog) │    +is_due() 10s    │ kaart (Dispatch) │
└─────────────────┘                    └────────┬─────────┘
                                                ▼
                                       ┌──────────────────┐
                                       │ Sweep-sessie     │
                                       │ (engineer)       │
                                       │  ↳ voert script  │
                                       │    uit + handelt │
                                       │    op het        │
                                       │    rapport       │
                                       └────────┬─────────┘
                                                ▼
                                       ┌──────────────────┐
                                       │ PR-triage-       │
                                       │ rapport (JSON)   │
                                       │  + tabel         │
                                       └──────────────────┘
```

De tijd-trigger is dezelfde die de beslissing
[`scheduled-trigger-consolidatie-decision.md`](./scheduled-trigger-consolidatie-decision.md)
kiest: de klok maakt de kaart, de dispatcher doet de rest. Geen sessie-
injectie, geen lange-levende achtergrond-runtime.

## 3. Failure-classificatie

Het prototype-script kent vier uitkomsten per PR (zie
`scripts/sweep_open_prs.py:_check_status_classification`):

| Label            | Trigger                                                    | Actie                                 |
|------------------|------------------------------------------------------------|---------------------------------------|
| `pending_billing`| Quality-check > 1 u PENDING/QUEUED, of failure-log bevat `spending limit` / `hit your weekly limit` / `usage limit` / `billing` | Wachten op CI-reset; dependabot: `@dependabot rebase` |
| `real_failure`   | Quality-check `conclusion=FAILURE`, log zonder billing-signal | `gh run view <run-id> --log-failed`   |
| `passing`        | Alle checks `COMPLETED` zonder `FAILURE`                  | `gh pr merge <num> --squash --delete-branch` |
| `no_checks`      | Geen status-checks (vers geopend of ongeconfigureerd)      | Wachten of handmatig pingen           |

**Waarom PENDING > 1 u als billing-signal werkt.** In deze repo draait
Quality binnen vijf minuten na een push; een hangende QUEUED na een uur
is geen opstart-artefact meer. De grens staat in
`STALE_PENDING_GRACE` (`scripts/sweep_open_prs.py:74`); lagere grenzen
geven false positives op de eerste run na een verse push.

**Waarom de run-log-scan nodig is.** GitHub toont een billing-block
extern als een gewone `conclusion=FAILURE` met een Anthropic-fout in de
log. Zonder log-scan is hij niet van een echte-diff-failure te
onderscheiden — de Quality-check faalt formeel, maar om de verkeerde
reden. De log wordt via `gh run view <id> --log-failed` opgehaald en
met de patterns uit `SPENDING_LIMIT_PATTERNS` (`:49`) gematcht. Memo-
isatie per `run-id` (`_cached_log`, `:153`) voorkomt dat één matrix-run
meer dan één keer wordt gescand.

## 4. Schema en output

JSON op stdout (altijd), tabellarische samenvatting op stderr met
`--print`. Het schema staat in `scripts/sweep_open_prs.py:SCHEMA_VERSION`
en de module-docstring. Staleness-vlag op ≥ 7 dagen niet-passend,
overridable via `--stale-days`. Voorbeeld-rij (gekort):

```json
{
  "number": 92,
  "title": "Bump fastapi from 0.110 to 0.115",
  "author": "dependabot[bot]",
  "category": "dependabot",
  "age_days": 18,
  "status_classification": "pending_billing",
  "stale": true,
  "suggested_action": "@dependabot rebase  # billing-block, fresh-rebase pakt Quality-split mee"
}
```

De actie-suggestie is bewust copy-pasteable: de operator kan na een
`--print` direct door de rij-tabel scrollen en de geschikte merge- of
rebase-commando's uitvoeren.

## 5. Verschil met bestaande sweepers

De repo heeft al een flinke sweep-familie (zie `scripts/sweep_*.py`).
Twee zijn direct relevant voor dit ontwerp:

- `scripts/sweep_merged_remote_branches.py` — dichte pattern-match voor
  deze nieuwe tool: JSON op stdout, advisory by default, `--strict` voor
  CI, exit-codes `0/1/2`. De argparse-vorm, de `_run_git`-achtige wrapper
  (hier `_run_gh`), de `repo_path`-resolutie en het report-schema
  erfen we 1-op-1.
- `scripts/baseline-bash-tests.sh` — patroon voor *"is deze failure van
  mij of van de baseline?"* waar de kaart-exploratie naar verwijst
  (*"uitbreiden naar Quality"*). De Quality-baseline staat bewust niet
  in scope van deze iteratie: het probleem is *"we weten niet wat er
  open staat"*, niet *"we weten niet welke failures pre-existing zijn"*.
  Zodra de eerste triage-rapporten de operator helpen, kan een
  `baseline-quality.sh` als aparte follow-up de derde laag worden.

## 6. Trigger-wiring (later, niet in scope van deze spike)

Het prototype levert het uitvoeringsuiteinde; het wekelijkse vuur-mechanisme
is een vervolg-kaart. Het natuurlijke ontwerp volgt
[`scheduled-trigger-consolidatie-decision.md`](./scheduled-trigger-consolidatie-decision.md)
§3 — een kanban-kaart met `scheduled_at` in Backlog, wiens dispatch-tick
(`is_due()` per 10 s) 'm claimt en spawnt. De executor-sessie voert dan
`scripts/sweep_open_prs.py --print --strict` uit, post het rapport als
kanban-comment, en sluit af. *"Chain-of-one-shots"* — de sessie maakt
zelf de opvolger-kaart voor de week erna, geen aparte scheduler nodig.

Concrete vervolg-kaarten die hieruit volgen (in deze iteratie niet
aangemaakt — de dispatch-sessie kan dat zelf):

1. **Backlog-kaart *"Wire weekly PR-triage sweep"***: scheduled_at op
   maandag 09:00 Europe/Brussels, agent=engineer, beschrijving verwijst
   naar dit design-doc en het script-pad.
2. **Backlog-kaart *"Add Quality-baseline + extension tot Quality-check
   failures"***: volgt het `baseline-bash-tests.sh`-patroon maar voor
   `quality.yml`; nuttig zodra écht-diff-failures van billing-blocks
   onderscheiden moeten worden op werkdag-niveau, niet alleen op
   wekelijks-niveau.

## 7. Acceptatiecriteria (verbatim uit de kaart)

- [ ] `scripts/sweep_open_prs.py` emittet JSON op stdout met de vier
      classificatie-labels en de stale-vlag.
- [ ] `--strict` exit-code `1` zodra ≥1 stale PR, anders `0`; `2` bij
      gh-fouten.
- [ ] Dependabot-PRs krijgen `@dependabot rebase` als voorgestelde actie
      bij `pending_billing`.
- [ ] Menselijke PRs met billing-blok krijgen *"wacht op CI-reset"* en
      geen automatische rebase.
- [ ] `--print` schrijft een tabel op stderr met number, leeftijd,
      categorie, classificatie en actie.
- [ ] Geen netwerk-call zonder `--repo` (default
      `guillaumevandevelde/agent-cockpit`); zie CLAUDE.md §Gotchas over
      `gh` en de fork-upstream.

## 8. Bekende beperkingen

- **Eerste scan is een momentopname.** Geen state, geen historie — als
  een PR tussen sweeps door sluit, mist de volgende sweep 'm. Dat is
  acceptabel voor weekly cadence; voor near-real-time is een aparte
  webhook-route (`backend/app/api/v1/webhooks/router.py`) de betere
  ingang.
- **Log-cap op 64 KiB.** Een pathologische log die de billing-signal
  voorbij die grens verstopt, mist de classificatie. In de praktijk
  staat het spending-limit-signaal in de eerste paar honderd bytes van
  de failure-sectie; 64 KiB is ruim. Limiet staat in
  `scripts/sweep_open_prs.py:_fetch_run_log`.
- **Geen PR-thread-onderzoek.** Een PR kan passing zijn op Quality maar
  een review-threads vereisen — die classificatie zit niet in dit
  signaal. Owner-decision blijft mensenwerk.