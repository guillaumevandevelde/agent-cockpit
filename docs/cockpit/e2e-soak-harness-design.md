---
title: "E2E + soak harness voor dispatch lifecycle"
type: design
status: active
---

<!--
Bron-analyse voor kanban-kaart 6b662c35… (kind van
fork-strategy-claude-deck-316 §4.5). Adopteert het Tizonia-pattern uit
upstream PR #316 voor onze dispatch-loop. Implementeert het ontbrekende
vangnet vóór merge van een ship-recept- of dispatch-loop-wijziging.
-->

# E2E + soak harness voor dispatch lifecycle

**Effect:** een engineer die ship-recept of dispatch-loop aanraakt weet vóór merge of de lifecycle nog werkt. Tizonia-style scratch-sandbox + Playwright-flow + nachtelijke soak onder canonieke provider vangt regressies die de huidige smoke afdekking mist.

✅ Geïmplementeerd (kaart 69ed8cf4…) — `frontend/e2e/dispatch/lifecycle.spec.ts` (S1–S5), `frontend/e2e/dispatch/agent-mail.spec.ts` (M1–M3), `backend/tests/fixtures/dispatch_stub.py`, `backend/scripts/cockpit_soak.py`, `scripts/cockpit-soak-report.py`, en de hard-gate in `.github/workflows/quality.yml::dispatch-lifecycle`.

**Kaart:** `6b662c3541ae4b93b90ce1a16b7a6a7a` (kind van [`fork-strategy-claude-deck-316.md`](./fork-strategy-claude-deck-316.md) §4.5).

## 1. Premisse en wat adopteren we

Onze e2e-dekking is 28 regels in `frontend/e2e/smoke.spec.ts` — drie page-loads, geen login-flow, geen dispatch-loop, geen Agent Mail. Een wijziging in `backend/app/kanban/dispatch.py` of in `.claude/skills/git-ship/SKILL.md` passeert CI zonder dat iemand merkt dat de lifecycle niet meer werkt.

Upstream PR #316 levert twee artefacten die dit gat dichten:

1. **Tizonia E2E testbed** — een scratch-sandbox repo met fake kanban-kaarten die de hele lifecycle doorlopen (`https://github.com/adrirubio/claude-deck/pull/316` §Tizonia).
2. **Unattended-soak harness** — N-uur durende run die sessies spawnt onder canonieke provider, met per-sessie-uitkomst naar `logs/soak/<date>.json` (zelfde PR §Delivery).

Wij nemen het patroon over; de GitHub-issue-as-testbed-aanpak vervangen we door onze eigen kanban-API.

## 2. Testbed — scratch-sandbox repo

Een dedicated scratch-repo onder `/tmp/cockpit-e2e-sandbox/<run-id>/`, geïnitialiseerd als kale `git init` met:

- één lege commit op `master`;
- `backend/claude_registry.db` en `kanban.db` gecopieerd uit een ephemeral fixture;
- tien fake kanban-kaarten in Backlog/Todo met vaste `description`-tekst die de test herkent.

De sandbox wordt per testrun aangemaakt en opgeruimd door `scripts/cockpit-soak-cleanup.sh` na een geslaagde of gefaalde run. Geen netwerk-toegang buiten `localhost:8000` (de eigen backend), zodat de run nooit per ongeluk een echte fork aanraakt.

## 3. E2E lifecycle-flow

Playwright-spec(s) onder `frontend/e2e/dispatch/` die de volgende transities doorlopen tegen de eigen backend:

1. `POST /api/v1/cards` — fake Backlog-kaart aanmaken.
2. Wacht op auto-dispatch tick (max 30 s) → kaart in `Doing`.
3. `GET /api/v1/cards/{id}` — verifieer `worktree_path` is gezet in `KanbanMeta` door `_record_worktree_lease`.
4. Sessie spawnt stub (zie §5) en schrijft `attach_deliverable(kind="branch", ref="k-test-<run-id>")`.
5. `POST /api/v1/cards/{id}/move` naar `Done` met summary.
6. Verifieer `kanban_ops` heeft `create` + `claim` + `move`-regel in die volgorde.

Acht scenarios als hard gate (S1–S5 gemirrord op upstream §Tizonia): code happy path, auto-merge, CI-fail retry-recover, retry-budget escalatie, design pipeline. De overige drie zijn uitbreidingen voor onze Agent Mail en worktree-lease oppervlakken.

## 4. Soak architecture

`backend/scripts/cockpit_soak.py` (nieuw) doet per nacht:

- Pre-flight: backend healthcheck + scratch-sandbox init.
- Loop N uur (default 8), spawn elke M minuten een nieuwe fake-kaart via `POST /api/v1/cards` met canonieke provider pinning (`provider="minimax"` met expliciet `dispatch_provider`-veld; deze sessie draait er al op, gemeten uit `dispatch_provider` op deze kaart).
- Per sessie: verzamel `card_id`, `claim_at`, `move_to_done_at`, `exit_reason` (Done/Impediment), `deliverable_kinds`, `worktree_path`.
- Append-only schrijven naar `logs/soak/<UTC-date>.jsonl` (één regel per sessie).

`scripts/cockpit-soak-report.py` (nieuw) leest de jsonl en produceert per avond een `logs/soak/<date>.json` met aggregate metrics: doorlooptijd p50/p95, exit_reason-distributie, fail-rate-per-scenario.

## 5. Provider- en model-pinning

Canonieke provider: `minimax`. Model wordt ge-resolved via `resolve_effective_provider_and_model` in `backend/app/kanban/dispatch.py` — geen ad-hoc override. Een wijziging in die resolver is zelf een regressie-onderwerp voor de soak.

Voor de E2E-suite gebruiken we een stub-sessie: een `claude -p "echo"`-equivalent die deterministic een branch-deliverable post en `move_card Done` doet. De stub leeft in `backend/tests/fixtures/dispatch_stub.py` en wordt door de test gespawned in plaats van een echte provider-sessie.

## 6. Vervanging van `frontend/e2e/smoke.spec.ts`

De huidige drie page-loads blijven als `frontend/e2e/smoke.spec.ts` voor de route-smoke; de 8 lifecycle-scenarios komen in `frontend/e2e/dispatch/lifecycle.spec.ts` (S1–S5 hard gates) en `frontend/e2e/dispatch/agent-mail.spec.ts` (de drie uitbreidingen). De Playwright config (`frontend/playwright.config.ts:6`) krijgt een extra project voor de dispatch-suite dat `--workers=1` draait om race op de shared `kanban.db` te vermijden.

## 7. Acceptance criteria

- [ ] `frontend/e2e/dispatch/lifecycle.spec.ts` bestaat en doorloopt S1–S5 groen tegen een verse sandbox.
- [ ] `frontend/e2e/dispatch/agent-mail.spec.ts` bestaat en dekt de drie Agent Mail-scenarios groen.
- [ ] `backend/scripts/cockpit_soak.py` schrijft per sessie een geldige jsonl-regel; één 8-uur-run produceert ≥ 30 regels.
- [ ] `scripts/cockpit-soak-report.py` produceert `logs/soak/<date>.json` met de aggregate metrics uit §4.
- [ ] CI-gate `quality.yml` runt `lifecycle.spec.ts` als hard gate op PR's die `backend/app/kanban/dispatch.py` of `.claude/skills/git-ship/` raken (file-glob in de workflow).

## 8. Bewust buiten scope

- **GitHub-issue-as-testbed** — wij draaien op kanban-kaarten, niet op GitHub-issues. Het Tizonia-patroon voor issue-labels en PR-merge-policy vervangen we door onze eigen dispatch-endpoints.
- **Auto-merge in soak** — `cockpit-richting-decision.md` §6 houdt human-merge als default; de soak doet fake-merge naar een lokale branch, niet naar `origin`.
- **`agent_teams.py`-equivalent** — buiten scope per `fork-strategy-claude-deck-316.md` §5.
- **Productie-soak tegen echt board** — alleen tegen scratch-sandbox. Een productie-tegenhanger is een eigen kaart.

## 9. Heropenen wanneer?

- Iemand oppert om `frontend/e2e/smoke.spec.ts` uit te breiden met page-tests → verwijzen naar §6 (dispatch-suite is de nieuwe plek).
- Iemand oppert om de soak tegen het echte board te draaien → verwijzen naar §8 (eigen kaart).
- Upstream PR #316 levert een soak-defect-triad dialoog die voor ons relevant wordt → deze doc uitbreiden met de lessons.

## 10. Bronnen

- `docs/cockpit/fork-strategy-claude-deck-316.md` §4.5
- https://github.com/adrirubio/claude-deck/pull/316 (Tizonia §, Soak §)
- `backend/app/kanban/dispatch.py:6461` (`dispatch_project`)
- `backend/app/kanban/dispatch.py:6822` (`dispatch_card`)
- `backend/app/kanban/dispatch.py:2322` (`_record_worktree_lease`)
- `backend/app/kanban/router.py:909` (`move_card`)
- `backend/app/kanban/router.py:1046` (`claim_card`)
- `.claude/skills/git-ship/SKILL.md`
- `frontend/playwright.config.ts:6`
- `frontend/e2e/smoke.spec.ts` (huidige 28-regel dekking)