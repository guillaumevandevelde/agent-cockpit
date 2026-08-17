---
title: "Fork-strategie — upstream claude-deck PR #316"
type: decision
status: decided
date: 2026-08-17
---

<!--
Bron-analyse voor kanban-kaart 674ea335… Zie §6 voor de aanbeveling en §7 voor
de vier kind-kaarten die dit in deze sessie filen. Cross-references: PR #316
high-level, capability-baseline voor onze kant, cockpit-richting-decision §6
voor het krimp-regime dat deze beslissing raakt.
-->

# Fork-strategie — upstream claude-deck PR #316

**Datum:** 2026-08-17
**Status:** beslist
**Kaart:** `674ea335ecd44f4fb312852c41f9f4fd`
**Uitkomst:** Vier kind-kaarten filed: GitHub-Issue webhook (needs-decomposition), GitHub App credential binding (needs-decomposition), workspace lease in worktree-gc (cherry-pick), Tizonia-style E2E + soak harness (adopt); agent_teams.py + MailExternalActor + leader-owned dep unblocking worden NIET overgenomen.
**Verdict:** Geen drop-in. Twee capabilities (GitHub-trigger, GitHub App credentials) zijn *needs-decomposition*; twee patterns (workspace-lease, E2E+soak harness) zijn *cherry-pick* / *adopt*; één (leader-owned dep unblocking) is *ignore* omdat onze plan_attachment-DAG het functionele gat al dicht.

**Bron:** https://github.com/adrirubio/claude-deck/pull/316 (master @ `2026-08-14T09:53:34Z`, gemeten 2026-08-17)

---

## TL;DR

- **Premisse getoetst:** klopt deels — upstream heeft dezelfde foundations (kanban-dispatch, Agent Mail, worktree-isolatie) maar voegt GitHub-trigger + GitHub App credentials toe; het is geen nieuwe laag, wél nieuw credential- en webhook-oppervlak.
- **Adopteren:** workspace-lease pattern (cherry-pick), Tizonia E2E + soak harness (adopt).
- **Bewust niet overnemen:** `agent_teams.py` (1330 lines nieuwe abstractie), `MailExternalActor` (ceremonie die we al gesloopt hebben, kaart `5fca30d0…`), leader-owned dep unblocking (tweede pad naast `dep_resolver.py:11`).
- **Vervolg:** 4 Backlog kind-kaarten, gefiled via `add_plan_attachment` in deze sessie.

## 1. Wat PR #316 feitelijk is (gemeten 2026-08-17)

Upstream promoot een 222-commit / 93-file / 56.892-addition reeks naar master onder de titel *"feat(dispatch): deliver autonomous GitHub agent teams"*. Vijf integrated phases:

1. dispatch hardening + Tizonia E2E recovery fixes
2. leader-owned dependency unblocking + cold-start recovery
3. soak defect triad + escalation safety + closed-issue reconciliation
4. workspace provisioning lifecycle phases G1–G3
5. distinct approver identity PR0, PR1, PR2

Nieuwe services (7 stuks, ~6.000 lines): `github_app_auth_service`, `github_client`, `github_dispatch_scheduler`, `github_dispatch_service`, `github_verification_service`, `github_watcher_service`, `github_workspace_service`. Modified: `agent_mail_service.py` (+349/-9), `agent_mail.py` (+155/-12), `agent_teams.py` (+1330/-2), `agent_bridge/spawn.py` (+27/-1), `peer_process.py` (+291, subprocess isolation), `git_credential_helper.py` (+75, App credential-helper).

## 2. Premisse getoetst

De premisse luidt: *"dezelfde foundations (kanban-dispatch, Agent Mail, worktree isolation), maar dan zonder GitHub-trigger en zonder credential-binding"*. Klopt deels:

- **Gedeelde foundations, bevestigd.** Wij hebben kanban-card dispatch (`backend/app/kanban/dispatch.py:2657`), Agent Mail (`backend/app/models/agent_mail.py:17-100`), worktree-isolatie (`.claude/skills/git-ship/SKILL.md`).
- **Geen GitHub-trigger, bevestigd.** Onze dispatch-trigger is de kanban-kaart (MCP/API); geen webhook-endpoint.
- **Geen credential-binding, bevestigd.** Onze ship doet `git push origin HEAD:master` vanuit de worktree zonder credential-flow.

NIET drop-in: onze kanban-DB is portable per machine, dispatch-loop is MCP-API-driven, ship is direct-mode-merge. Upstream's agent_teams + GitHub App + watcher-poll aanvalsoppervlak is een fundamenteel ander credential- en opslagmodel.

## 3. Feature-matrix

| # | Upstream PR #316 capability | Cockpit (file:line) | Verdict |
|---|---|---|---|
| 1 | GitHub-Issue als dispatch-trigger | kanban-card via MCP/REST; geen webhook | **needs-decomposition** |
| 2 | Repository-scoped GitHub App credentials | local `git push` vanuit worktree; geen credential-flow | **needs-decomposition** |
| 3 | Workspace provisioning G1–G3 + observed owner | worktree-gc na Done; geen lease | **cherry-pick** |
| 4 | Leader-owned dependency unblocking | plan_attachment + depends_on (`dep_resolver.py:11`) | **ignore** |
| 5 | Tizonia E2E + unattended-soak artefacts | handmatige smoke + `frontend/e2e/smoke.spec.ts` (28 regels) | **adopt** |

## 4. Per-cap verdict

### 4.1 ⭐ GitHub-Issue-trigger (needs-decomposition)

Upstream route: webhook → `agent_teams.py` spinner. Bij ons: webhook → kanban-card create + optionele plan_attachment. Effect: product owner kan een GitHub-issue als dispatch-eenheid indienen zonder kanban-handmatig werk. Kost: FastAPI webhook endpoint, SecretStore entry voor webhook-secret, e2e test. Kind-kaart (a).

### 4.2 ⭐ GitHub App credentials (needs-decomposition)

Upstream bindt commit/PR-identity aan een immutable dispatch attempt met repo-scoped GitHub App credentials. Effect: dispatch-output (PR/branch) heeft herleidbare actor + onvervalsbare push-recht; handmatige `git push` vanuit worktree verdwijnt. Kost: GitHub App installatie, SecretStore entry, `git_credential_helper.py`-equivalent, ship-recipe aanpassing (`git-ship/SKILL.md` + `dispatch.py::_build_ship_instructions`). Kind-kaart (b).

### 4.3 Workspace lease (cherry-pick)

Upstream G1–G3: workspace krijgt observed_owner + lease TTL; lease verloopt als sessie sterft. Bij ons: worktree wordt aangemaakt op dispatch en weggegooid op Done of door `scripts/worktree-gc.sh`. Geen lease. Effect: een sessie die sterft voor Done laat geen onverklaarde worktree achter — `cockpit-richting-decision.md` §6 signaleert al dat dit kruimelt. Kost: 1 lease-veld in `KanbanMeta` + check in `cleanup_session_for_card`. Kind-kaart (c).

### 4.4 Leader-owned dep unblocking (ignore)

Upstream: leader-sessie detecteert blocking dep en spawnt de blocker zelf. Bij ons: kind-kaart met `depends_on` wacht tot parents Done via `meets_dep_prerequisites` (fail-closed, `dep_resolver.py:11`); een mens/analyst kan de dep handmatig unblocken via `add_plan_attachment`. Functioneel geen gat; tweede pad naast het bestaande zou een orchestration-laag erboven zijn die `cockpit-richting-decision.md` §6 (krimp) uitsluit.

### 4.5 E2E + soak harness (adopt)

Upstream levert Tizonia E2E + unattended-soak artefacts. Bij ons: `frontend/playwright.config.ts:6` baseURL wijst naar backend-poort; `frontend/e2e/smoke.spec.ts` 28 regels die geen login, dispatch-flow, agent-mail of kanban-interactie raken (`decisions.md` 2026-08-13 "E2e-rol uitgesteld"). Effect: een engineer die ship-recept aanraakt weet of-ie regressed. Kost: scratch-sandbox + herhaalbare dispatch-flow + nachtelijke soak. Kind-kaart (d).

## 5. Bewust NIET overnemen

- **`agent_teams.py` (1330 lines)** — abstractie die bij ons geen consumer heeft; vervangen zou een tweede orchestration-laag zijn. Onder krimp-regime van `cockpit-richting-decision.md` §6.
- **`MailExternalActor`-stijl externe actoren** — wij hebben `MailExternalActor` gesloopt (kaart `5fca30d0…`); upstream voegt weer externe actoren toe. Niet terugdraaien.
- **Closed-issue reconciliation** — alleen zinvol zodra GitHub-trigger er is; zit impliciet in kind-kaart (a).

## 6. Aanbeveling

Vier kind-kaarten shippen. Geen file-wijziging in deze analyse-kaart zelf. Heroverwegen zodra kind-kaart (a) is geshipt — credential-flow (b) is een keerzijde van GitHub-trigger.

## 7. Vervolgkaarten (deze sessie aangemaakt)

- `60e2f01d1bc848fd81fade100cf50df7` — `GitHub-Issue webhook → kanban-card trigger` — design + FastAPI endpoint + webhook-secret in SecretStore + e2e test.
- `bf635110badd4198b0725265789ecfc1` — `GitHub App credential binding voor push/PR identity` — App installatie + SecretStore entry + `git_credential_helper.py`-equivalent + ship-recipe aanpassing.
- `a2268cd256944398bfec1da170b0de09` — `workspace lease in worktree-gc voor orphan-detectie` — cherry-pick G1–G3 lease + observed_owner in `cleanup_session_for_card`.
- `6b662c3541ae4b93b90ce1a16b7a6a7a` — `Tizonia-style E2E + soak harness voor dispatch lifecycle` — adopt pattern uit PR #316 voor onze dispatch-loop.

Plan-attachment geleverd door `add_plan_attachment` op parent `674ea335…`; `depends_on_graph={}` (vier onafhankelijke brokken).

## 8. Bewust buiten scope

- `peer_process.py` (291 lines, subprocess isolation) — afhankelijk van credential-binding; volgt uit kind-kaart (b).
- `github_verification_service.py` (1124 lines) — implementatie-diepte die alleen zinvol is zodra GitHub-trigger + credential-flow er zijn.
- Voorganger-PRs (PR0, PR1, PR2) — distinct approver pattern is upstream's release-cadans; onze `cockpit-richting-decision.md` §3 hanteert single-owner review.

## 9. Heropenen wanneer?

- Iemand oppert om een `agent_teams`-API toe te voegen → verwijzen naar deze beslissing + §4.4.
- Iemand oppert om `MailExternalActor` terug te draaien → verwijzen naar kaart `5fca30d0…` + §5.
- Kind-kaarten (a) én (b) zijn Done → overweeg om PR #316 opnieuw te scoren op drop-in adoptie.

## 10. Bronnen

- PR #316: https://github.com/adrirubio/claude-deck/pull/316 (master @ `2026-08-14T09:53:34Z`)
- `docs/cockpit/cockpit-capability-baseline.md` (gemeten 2026-07-23, commit `9838e6b`)
- `docs/cockpit/cockpit-richting-decision.md` §6 (krimp-regime)
- `docs/cockpit/00-orientation.md` (kernprincipes)
- `docs/cockpit/agent-mail-spec.md` (Agent Mail)
- `docs/cockpit/multi-agent-kanban.md` (plan_attachment + depends_on)
- `.claude/skills/git-ship/SKILL.md`
