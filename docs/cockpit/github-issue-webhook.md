---
title: "GitHub-Issue webhook → kanban-kaart"
type: spec
status: active
---

# GitHub-Issue webhook → kanban-kaart

**Effect:** een GitHub-issue is nu een dispatch-eenheid. Wie een issue opent op
een aangesloten repo, krijgt automatisch een Backlog-kaart die de
auto-dispatcher op de volgende tick claimt. Geen handmatig kaartwerk meer.

**Kaart:** `60e2f01d1bc848fd81fade100cf50df7` (kind van
[`fork-strategy-claude-deck-316.md`](./fork-strategy-claude-deck-316.md) §4.1).

## 1. Endpoint

```
POST /api/v1/webhooks/github?project_key=<key>[&parent_card_id=<id>]
```

Code: [`backend/app/api/v1/webhooks/router.py`](../../backend/app/api/v1/webhooks/router.py)
(transport) en
[`backend/app/services/webhook_triggers.py`](../../backend/app/services/webhook_triggers.py)
(event → kaart).

| Parameter | Verplicht | Betekenis |
|---|---|---|
| `project_key` (query) | ja | Welk bord de kaart krijgt |
| `parent_card_id` (query) | nee | Maak de kaart als kind van deze kaart |
| `X-GitHub-Event` (header) | ja | Eventnaam van GitHub (`issues`, `pull_request`) |
| `X-Hub-Signature-256` (header) | ja | HMAC-SHA256 over de rauwe body |

Drie events leiden tot een kaart: `issues.opened`, `issues.reopened` en
`pull_request.opened`. Elk ander event is een bewuste no-op met
`{"triggered": false}` — GitHub vuurt tientallen eventsoorten op één URL, en
"niet interessant" is geen fout.

## 2. Authenticatie: HMAC met een SecretStore-geheim

Het endpoint is een onbeschermde grens naar het publieke internet, dus élke
levering moet ondertekend zijn. De verificatie is fail-closed en de twee
faalgevallen zijn te onderscheiden:

- **503 `webhook_secret_not_configured`** — dit project heeft geen leesbaar
  geheim. Er is niets te verifiëren, dus de levering wordt geweigerd.
- **401 `invalid_signature`** — signature ontbreekt, is misvormd of hoort bij
  een andere body. De vergelijking is constant in tijd (`hmac.compare_digest`).

De body wordt pas ontleed nadat de signature klopt.

Het geheim heet **`GITHUB_WEBHOOK_SECRET`** en staat per project in de
SecretStore. Zetten:

```bash
curl -X PUT "http://localhost:8000/api/v1/secrets/<project_key>/GITHUB_WEBHOOK_SECRET" \
     -H 'Content-Type: application/json' -d '{"value":"<zelfde string als in GitHub>"}'
```

Controleren welke namen een project heeft (de respons geeft namen, nooit
waarden):

```bash
curl "http://localhost:8000/api/v1/secrets/?project_key=<project_key>"
```

Dezelfde string zet je in GitHub onder *Settings → Webhooks → Secret*, met
content-type `application/json`.

## 3. Wat er op de kaart landt

Titel wordt `[issue #<nummer>] <issue-titel>`; de beschrijving bevat de
issue-URL, de GitHub-labels en de issue-body. De `metadata` van de kaart
bewaart `source`, `event`, `repo`, `issue_number`, `issue_url` en
`github_labels`.

Die metadata is ook de sleutel voor **idempotentie**: een tweede levering van
hetzelfde issue vindt de bestaande kaart terug en antwoordt met
`{"action": "card_exists"}`. GitHub levert opnieuw bij een vermoede fout, en de
beheerder kan een levering handmatig herhalen — zonder deze check stond het
issue twee keer op het bord.

## 4. Parent-kaart en `plan_ref`

Geef je `parent_card_id` mee, dan krijgt de kaart die parent **en** direct een
`plan_ref`-deliverable in dezelfde transactie. Dat tweede deel is geen extra:
`dispatch._awaiting_plan_ref`
([`backend/app/kanban/dispatch.py:4286`](../../backend/app/kanban/dispatch.py))
houdt een kind zonder `plan_ref` stil uit dispatch — de kaart lijkt vrij maar
draait nooit. Het schrijven zelf zit in `operations.attach_plan`
([`backend/app/kanban/operations.py:145`](../../backend/app/kanban/operations.py)).

Een `parent_card_id` die niet bestaat wordt geweigerd met **422
`parent_card_not_found`**. Een kind onder een verdwenen parent zou anders
onder `missing_parent` blijven staan, zonder enig signaal op deze grens.

De afhankelijkheidsgraaf blijft leeg (`depends_on_graph={}`). Eén levering
draagt één issue, dus er is niets om een graaf uit af te leiden; wie meerdere
issue-kaarten wil ordenen roept `add_plan_attachment` opnieuw aan met een echte
graaf.

## 5. Testen

Alle tests draaien de hele route: ondertekende body → signature-check →
handler → op-log → kaart op het bord.

```bash
scripts/run-single-test.sh tests/test_webhook_triggers.py
```

Dertien tests, ~3,3 s (gemeten 2026-08-17). Ze dekken de e2e-issueflow, de
idempotentie, de `plan_ref`-wiring, de onbekende parent, en vier
signature-weigeringen (ontbrekend, fout geheim, aangepaste body na
ondertekening, geen geheim geconfigureerd).

De test vervangt de store-factory op de **consument**
(`webhook_triggers._store`), niet op `secrets_store` zelf — anders raakt de
patch de binding niet die de handler gebruikt. Zie
[`test-doubles-convention.md`](./test-doubles-convention.md).

## 6. Bewust buiten scope

- **Closed-issue reconciliation** — `issues.closed` is nu een no-op. Een kaart
  automatisch afsluiten of van commentaar voorzien vraagt een keuze over wat er
  gebeurt met werk dat al loopt; die keuze is nog niet gemaakt.
- **Multi-issue afhankelijkheidsgraaf** — niet af te leiden uit één levering,
  zie §4.
- **Push-identiteit** — de agent pusht nog met de omgevings-credential van de
  machine. Dat is kind-kaart `bf635110badd4198b0725265789ecfc1` (GitHub App
  credential binding).
- **UI om het geheim te zetten** — via de bestaande secrets-REST, geen nieuw
  scherm.
