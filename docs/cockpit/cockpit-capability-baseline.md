---
title: "Cockpit capability-baseline"
type: reference
status: active
---

# Cockpit capability-baseline

> **Gemeten op:** 2026-07-23 · **Commit:** `9838e6b` (`9838e6b619954ae5b8755b1853f35939d6c757b9`)
>
> **Waarvoor:** stap 3 van de [`product-analysis`](../../.claude/skills/product-analysis/SKILL.md)-skill
> eist dat elke "wij doen dat al" / "wij missen dat"-claim op een `file:line` rust in
> plaats van op geheugen. `openhands-analyse.md` §2, `lemma-platform-analyse.md` §1/§3
> en `9router-integratie-analyse.md` §4 schreven die grondlaag onafhankelijk opnieuw;
> dit is het gedeelde anker.
>
> **Wat dit NIET is:** geen architectuurdoc, geen roadmap, geen open vragen — alleen wat
> er op de commit hierboven draait. Voor het *waarom* per gebied: de spec-docs per sectie.
> En: deze regels zijn een startpunt, geen bewijs. Citeer je er een, verifieer 'm dan
> opnieuw tegen de huidige code — regelnummers schuiven en gedrag verandert.

## 1. Dispatch / auto-dispatch

- Een achtergrond-tick (`dispatch.run_dispatch_tick`, `backend/app/kanban/dispatch.py:5195`)
  draait elke 10s (`kanban_dispatch_interval_seconds`, `backend/app/config.py:125`) en
  claimt + spawnt zelf kaarten; auto-dispatch staat per project aan/uit in `KanbanMeta`
  (`dispatch.is_autodispatch_enabled:262`).
- Dispatchbron zijn twee kolommen: `_DISPATCH_COLUMNS = ("Backlog", "To Resume")`
  (`dispatch.py:3219`); `_next_card` (`:3303`) kiest priority-gesorteerd
  (`_priority_key:3223`) en slaat kaarten over die niet due zijn (`_is_due:3232`), een
  business-gate dragen (`_is_gated:3273`), nog op hun `plan_ref` wachten
  (`_awaiting_plan_ref:3248`) of open deps hebben (`dep_resolver.meets_dep_prerequisites:11`).
- De prompt is samengesteld, niet vast: `build_card_prompt` (`dispatch.py:1595`) plakt
  persona (`_persona_for_card:1335` leest `.claude/agents/<agent>.md`), kaarttekst,
  plan-context, ship-instructies en worktree-safety aan elkaar. Per-project caps per
  kolom via `_column_max_sessions` (`:2989`). Spec: `docs/cockpit/kanban-dispatch-spec.md`.

## 2. Worktrees + ship-workflow

- Elke gedispatchte sessie krijgt een eigen git-worktree: `make_worktree_transport`
  (`dispatch.py:2657`) doet `git fetch origin` + `git worktree add -b <session_name>
  … origin/master` onder `.claude/worktrees/<session_name>` en spawnt daar de CLI in.
- Drie transports naast elkaar — `worktree` (tmux), `sandcastle` (sandbox) en `headless`
  (stream-json subprocess) — per project instelbaar via
  `get_transport_for_project` (`dispatch.py:5317`).
- De ship-workflow (sync → frontend-gate → commit → merge via detached worktree →
  deliverable → Done) staat in `.claude/skills/git-ship/SKILL.md` en wordt woordelijk
  in de prompt geïnlined door `_build_ship_instructions` (`dispatch.py:2110`).
- Opruimen gebeurt bij `Done`: `session_cleanup.on_card_moved_to_done` (`:333`) →
  `cleanup_session_for_card` (`:240`) killt de tmux-sessie en verwijdert de worktree,
  met een unmerged-waarschuwing vooraf (`find_worktree_unmerged_warning:185`).
  Achtergebleven worktrees worden geclaimd door `scripts/worktree-gc.sh`.

## 3. Multi-agent-DAG + plan-attachments

- Een analyst-sessie splitst een parent-kaart op in kind-kaarten en koppelt ze met
  `add_plan_attachment` (MCP: `backend/app/kanban/mcp_server.py:1018`; REST:
  `POST /cards/{cid}/plan-attachment`, `backend/app/api/v1/kanban/router.py:1024`). De op
  schrijft een `kind='plan'`-deliverable op de parent en een `plan_ref` op elk kind
  (`backend/app/kanban/operations.py:424`).
- De `depends_on`-graaf is de dispatch-bron van waarheid: `meets_dep_prerequisites`
  (`dep_resolver.py:11`, fail-closed), `detect_cycle` (`:44`) en `dangling_dep_ids`
  (`:25`) — met `scripts/sweep_dangling_depends_on.py` als vangnet voor verweesde ids.
- Kinderen krijgen de plan-markdown in hun prompt (`_resolve_plan_for_child`
  `dispatch.py:2063`, gerenderd door `_plan_context_section:1960`). De analyst-persona
  zelf is geprogrammeerd in `backend/app/kanban/analyst_prompt.py`. Spec:
  `docs/cockpit/multi-agent-kanban.md`.

## 4. Agent mail

- Cross-session berichten met durable repo-identiteit: `MailTeamMember`,
  `MailAgentSession`, `MailExternalActor`, `MailMessage`, `MailReceipt`
  (`backend/app/models/agent_mail.py:17-100`). Een repo-member wordt on-demand
  aangemaakt uit de cwd (`agent_mail_service.get_or_create_repo_member:95`).
- Sessies registreren zich, heartbeaten en verlopen naar offline
  (`register_session:98`, `heartbeat_session:122`, `_effective_status:173`,
  `sync_observed_sessions:193`); versturen/lezen/ack loopt via `send_message:354`,
  `get_inbox:499`, `mark_read:536`, `ack_message:545`.
- Agents benaderen het via MCP-tools (`backend/app/mcp_server/tools/agent_mail.py:18`),
  mensen via de REST-API (`backend/app/api/v1/agent_mail.py`) en de
  `frontend/src/features/agent-mail`-mailbox. Een nieuw bericht kan een levende sessie
  nudgen (`_nudge_session_for_member:589`). Spec: `docs/cockpit/agent-mail-spec.md`.

## 5. Providers / abonnementen / pool

- Vijf agentic CLIs zijn geregistreerd: claude-code, codex-cli, copilot-cli, mimo-code,
  open-code (`backend/app/services/agentic_cli/__init__.py:11`). Providers voor
  usage/quota: `anthropic`, `bedrock`, `minimax` en `anthropic-compatible` (router-
  eindpunten) — geseed in `subscriptions/registry.register_default_providers:80`.
- Een abonnement is geïdentificeerd door zijn `{cli, provider}`-paar; de
  subscription-pool is een geordende lijst `PoolEntry`'s met per-entry drempel, opgeslagen
  in `KanbanMeta` onder prefix `subscription_pool:`
  (`backend/app/kanban/subscription_pool.py:60,104`). De pure router kiest de eerste niet-
  gepauzeerde entry onder zijn drempel (`pick_subscription_for_cli:194`,
  `has_available_spillover:271`).
- Dispatch-tijd resolutie van provider + model loopt via
  `resolve_effective_provider_and_model` (`dispatch.py:1181`) met een precedentieketen
  (kolom-default / kaart-model / persona-frontmatter, `_effective_model:1116`). Bij een
  usage-limiet pauzeert dispatch per provider in `KanbanMeta`
  (`backend/app/kanban/dispatch_pause.py`, sleutel `dispatch_paused_until:<provider>`).

## 6. Kanban-bord + Done-poorten

- Het datamodel is tweelaags: een append-only `KanbanOp`-log als bron van waarheid plus
  gematerialiseerde `KanbanCard`/`KanbanDeliverable`-rijen voor snelle reads
  (`backend/app/kanban/models.py:25,40,160`). Vaste kolommen: `COLUMNS = [
  "Backlog", "Impediment", "Awaiting Subtasks", "Done", "To Resume"]`
  (`backend/app/kanban/schemas.py:21`), aangevuld met vrij te configureren agent-kolommen
  (`KanbanColumn`, `models.py:194`).
- `move_card` (`mcp_server.py:317`) is een poort, geen doorgeefluik: `summary_required`
  op de summary-kolommen (`:369`), `outcome_required` + gesloten enum
  (`decomposed`/`not_feasible`/`no_action_needed`) op analyse-kaarten (`:388`), waarbij
  `decomposed` tegen echte kind-kaarten geverifieerd wordt (`no_children`). Een parent
  met open kinderen parkeert in `Awaiting Subtasks` en sluit automatisch.
- Verder op het bord: business-gates (`KanbanGate` `models.py:226`, `set_card_gate:597`,
  blokkerende `open_gate:846`), `report_impediment:761` met keuze-opties,
  `request_review:652` en `reopen_card:678`. UI: `frontend/src/features/kanban`.
  Conventies: `docs/cockpit/kanban-conventions.md`.

## 7. Sessie-lifecycle (claim, reaper, resume)

- Claim is exclusief en HLC-geordend: `claim_card` (`mcp_server.py:258`) zet
  `claimed_by="agent:<session_name>"`; een tweede claim krijgt `already_claimed`.
- De reaper `reap_stale_claims` (`dispatch.py:4164`) unieert drie liveness-bronnen —
  tmux (`_live_sessions:3178`), sandcastle (`_live_sandcastle_sessions:2841`) en headless
  (`_live_headless_sessions:2867`) — en geeft dode claims vrij (`_release_dead_claim:4475`).
  Een ambigue tmux-fout geeft `None` terug, zodat een hikje nooit als "alles dood" telt.
  Herhaald falen escaleert naar `Impediment` (`_bump_dispatch_failures:4319`,
  `_move_to_impediment_after_repeated_failures:4345`).
- Hervatten: een agent stempelt zijn transcript met `set_resume` (`mcp_server.py:896`),
  een rate-limited sessie parkeert zichzelf met een `scheduled_at` in `To Resume`
  (`move_limited_session_to_resume:3911`, `_move_to_resume:3704`), en na een
  backend-restart hervat `session_recovery.recover_interrupted_sessions` (`:160`, aangeroepen
  in `backend/app/main.py:111`). Een headless-run is live over te nemen als tmux-pane
  (`takeover.promote_to_tmux`, `backend/app/kanban/takeover.py:57`).

## 8. Observability (logs, activity feed, telemetrie)

- Backend/frontend draaien onder een supervisor die per service naar `logs/` schrijft en
  `run-*.log` na 7 dagen opruimt (`scripts/cockpit.sh:7,14`); logging is gestructureerd
  met correlation-id en UTC-timestamps (`backend/app/logging_config.py:12,55`).
- Per kaart: activity feed uit het op-log (`GET /cards/{cid}/activity`,
  `backend/app/api/v1/kanban/router.py:618`), een run-ledger die task/context/files/tests/
  outcome aan elkaar stikt (`:679`, `backend/app/kanban/run_ledger_service.py`) en
  token-telemetrie per dispatch (`:624`, `backend/app/services/dispatch_usage_service.py`).
- Board-breed: live-agent-overzicht (`GET /agent-activity/live` + `/summary`,
  `backend/app/api/v1/run_activity.py:35,75`, UI `features/dashboard/components/AgentActivityCard.tsx`)
  en een end-to-end MCP-zelfcheck die stille routing-/protocolfouten luid maakt
  (`backend/app/kanban/mcp_health.py`, badge `features/kanban/components/McpHealthBadge.tsx`).
