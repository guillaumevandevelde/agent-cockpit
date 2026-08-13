---
title: "Kern terugbrengen — vijf features verwijderen, Usage repareren"
type: plan
status: active
---

# Kern terugbrengen — vijf features verwijderen, Usage repareren

> Zeven halve features gaan eruit of terug naar hun kern. Dit plan is
> zelfstandig uitvoerbaar: elke fase noemt de exacte paden, de
> verificatie en de rollback. Een verse sessie hoeft niets te
> heronderzoeken.

Opdracht van de PO op 2026-08-13: Security, Presence, Agent Mail, Hosts en
Updates verdwijnen. Op Projects verdwijnen twee blokken. Subscriptions/Usage
wordt gerepareerd in plaats van verwijderd.

## 1. Vier beslissingen die al genomen zijn

Drie verwijderingen raken machinerie die blijft draaien. De PO koos per geval
expliciet. Heropen deze vragen niet.

| Onderwerp | Keuze | Gevolg |
|---|---|---|
| Security | Alles weg, dispatch permissief | Elk project valt terug op de meta-default: `skip_permissions` aan, host-worktree in plaats van sandcastle. Alleen een expliciete `KanbanMeta`-override wint nog. |
| Presence | Pagina weg, WS-feed blijft | Presence verdwijnt als bestemming, niet als sensor. Agent Bridge houdt zijn attention-badges en de desktop-notificaties. |
| Agent Mail | Alles weg, inclusief MCP-tools en hooks | Agents kunnen niet meer cross-session coördineren. Reeds geïnstalleerde hooks vragen een apart opruimscript. |
| Subscriptions/Usage | Repareren | Plan-tier corrigeren en signaalloze rijen verbergen. Het endpoint zelf werkt al. |

## 2. Waarom Usage kapot lijkt

Het endpoint antwoordt correct. Twee dingen maken het onbruikbaar.

**Eén.** De Anthropic-rij staat op 501%. De plan-tier staat op Pro, goed voor
44.000 tokens per 5h-venster. Het gemeten verbruik in het actieve blok is
220.564 tokens. Dat is meer dan Max 5x (220.000), dus `max_20x` is de enige
tier die past.

**Twee.** Zes van de zeven rijen melden "No usage signal available". Die
providers hebben geen quota-bron en zullen die ook niet krijgen.

Het gevolg van de eerste: `beschikbaar` wordt `false`, waarna
`backend/app/kanban/subscription_pool.py` de Anthropic-lane laat pauzeren.
De tier corrigeren repareert dus ook de dispatch.

## 3. Codebase-feiten die je nodig hebt

Deze zijn gemeten, niet gegokt.

- `backend/app/api/v1/router.py` bevat `include_router(security_router)`
  **twee keer**. Eén regel verwijderen laat een `NameError` staan.
- `SecurityProfileService.risk_class` drijft de dispatch-defaults via
  `_project_risk_class` in `backend/app/kanban/dispatch.py:573`.
- `security_audit_service.record` wordt aangeroepen vanuit vier plekken die
  blijven: `api/v1/secrets.py` (2x), `services/sandcastle_service.py` (2x),
  `services/agentic_cli/provider_env.py` (1x), `kanban/dispatch.py` (1x).
- De Presence-WebSocket voedt `frontend/src/hooks/useAttentionNotifications.ts`
  en `frontend/src/features/cc-bridge/useAttentionByPane.ts`. `PresenceService`
  voedt daarnaast `api/v1/status.py` en `mcp_server/tools/sessions.py`.
- Hosts voedt remote-SSH-spawn in `services/runs/spawn.py` en een host-picker
  in `features/cc-bridge/NewSessionDialog.tsx`.
- Agent Mail exporteert MCP-tools via `mcp_server/tools/agent_mail.py` en
  telt 19 testbestanden: `backend/tests/agent_mail/` (13) plus
  `test_agent_mail_*.py` (5) plus `test_agent_mail_model.py`.

Omvang in regels code: Agent Mail ~5.600, Security ~2.500, Presence ~1.100,
Hosts ~1.100, Updates ~680.

## 4. Uitvoering

Elke fase is één commit. Dat maakt `git revert` per feature mogelijk.

### Fase 0 — Vangnet

- [ ] Branch `chore/strip-unused-features` vanaf `master`.
- [ ] Draai `./scripts/pytest-baseline.sh`, `./scripts/baseline-bash-tests.sh`
      en `./scripts/ruff-baseline.sh`. Zonder baseline hangt pre-existing rood
      straks aan deze diff.
- [ ] Noteer `git rev-parse HEAD` als rollback-anker.

Verify: `git status` schoon, drie baselines gecached.

### Fase 1 — Updates

De geïsoleerde feature. Goede warming-up.

- [ ] Verwijder `frontend/src/features/updates/`.
- [ ] Verwijder de route in `App.tsx`, het nav-item en de `RefreshCw`-import
      in `lib/navigation.ts`.
- [ ] Verwijder `backend/app/api/v1/update/`, de include in `router.py` en
      `backend/tests/test_update_api.py`.

Verify: `( cd frontend && npm run build )` groen. `grep -rn
"features/updates\|v1/update" frontend/src backend/app` geeft niets.

### Fase 2 — Hosts

- [ ] Verwijder `frontend/src/features/hosts/`.
- [ ] Haal de host-picker, `selectedHostId`, `hosts` en `loadingHosts` uit
      `features/cc-bridge/NewSessionDialog.tsx`. Haal `host_id` uit
      `features/cc-bridge/types.ts`.
- [ ] Verwijder `backend/app/api/v1/hosts/`, `services/host_service.py`,
      `models/host.py` en `tests/test_host_service.py`.
- [ ] In `services/runs/spawn.py`: verwijder `_spawn_session_remote`, de
      `host_data`-parameter en `host_id`/`host_alias` uit de returndict.
- [ ] In `api/v1/runs/router.py`: verwijder het `host_id`-veld, de
      `HostNotFoundError`-handler en de `get_host`-lookup.
- [ ] Verwijder de host-import uit `models/__init__.py`.

Verify: build groen. Agent Bridge spawnt nog een lokale sessie.

### Fase 3 — Agent Mail

- [ ] Verwijder `frontend/src/features/agent-mail/`, de route, het nav-item en
      de `Mail`-import.
- [ ] Verwijder `mcp_server/tools/agent_mail.py` en de registratie in
      `mcp_server/tools/__init__.py`.
- [ ] Verwijder `api/v1/agent_mail.py`, `api/v1/external_agent_mail.py`,
      `services/agent_mail_service.py`, `services/external_agent_mail_service.py`,
      `services/agent_mail/` en `models/agent_mail*.py`.
- [ ] Verwijder de twee includes in `router.py`, de import in `main.py` en de
      regel in `models/__init__.py`.
- [ ] Haal de `_send_windows`-reset uit `services/_testing.py` en werk de
      docstrings in `tests/conftest.py` bij.
- [ ] Verwijder de agent-mail-endpoints uit `api/v1/session_hooks/router.py`.
- [ ] Verwijder `backend/tests/agent_mail/` en `test_agent_mail_*.py`. Stel
      `test_mcp_server.py` en `test_app_database_isolation.py` bij.
- [ ] Haal de agent-mail-alinea uit `backend/app/kanban/analyst_prompt.py` en
      `.claude/agents/analyst.md`.
- [ ] Schrijf `scripts/uninstall-agent-mail-hooks.sh`. Dat verwijdert de
      `SessionStart`-entries uit `~/.claude/settings.json` en de Codex-shim.
      Zonder dit script blijven bestaande sessies een dood endpoint aanroepen.

Verify: `grep -rni "agent.mail" backend/app frontend/src` geeft niets.
`bash scripts/run-single-test.sh tests/test_mcp_server.py` groen.

### Fase 4 — Presence, pagina weg en sensor behouden

- [ ] Verplaats `buildPresenceWsUrl` en `fetchPresenceWsToken` uit
      `features/presence/api.ts` naar `frontend/src/lib/presenceWs.ts`. Laat
      `hooks/usePresenceWebSocket.ts` daar importeren.
- [ ] Verwijder `frontend/src/features/presence/`, de route, het nav-item en
      de `Radio`-import.
- [ ] In `hooks/useAttentionNotifications.ts`: vervang de
      `navigate('/presence?…')`-fallback door niets-doen als een tmux-pane
      ontbreekt. De Agent Bridge-tak blijft.
- [ ] In `api/v1/presence.py`: verwijder `PATCH /sessions/{id}`,
      `DELETE /sessions/{id}`, `DELETE /sessions` en `GET /config-snippet`.
      **Behoud** `POST /events`, `GET /sessions`, `GET /token` en `WS /ws`.
- [ ] Behoud `frontend/src/types/presence.ts`. De WS-types blijven in gebruik.

Verify: `( cd frontend && ./node_modules/.bin/vitest run )` groen. Een levende
sessie toont nog een attention-badge op Agent Bridge.

### Fase 5 — Security

- [ ] Verwijder `frontend/src/features/security/` inclusief de test, de route
      en het nav-item. Let op: de `Shield`-import blijft nodig voor
      Permissions.
- [ ] In `kanban/dispatch.py`: verwijder `_record_audit` met al zijn
      call-sites. Verwijder `_project_risk_class`,
      `_skip_permissions_for_risk_class` en `_transport_for_risk_class`.
      `get_skip_permissions` wordt: override wint, anders `True`. De transport
      wordt altijd `DEFAULT_TRANSPORT`.
- [ ] Haal de audit-calls weg bij `api/v1/secrets.py`,
      `services/sandcastle_service.py` en `services/agentic_cli/provider_env.py`.
- [ ] Verwijder `api/v1/security.py`, `services/security_audit_service.py`,
      `services/security_profile_service.py` en `models/security_*.py`.
- [ ] Verwijder **beide** `include_router(security_router)`-regels uit
      `router.py`, plus de import.
- [ ] Verwijder `tests/test_security_audit.py` en `test_security_profile.py`.
      Inspecteer `test_statusline_preview_security.py`: waarschijnlijk een
      naamsovereenkomst zonder verband.
- [ ] Verwijder `scripts/check-kanban-meta-security-conflicts.sh` en
      `scripts/test_check_kanban_meta_security_conflicts.sh`. Haal hun regels
      uit het `# Test`-blok in `CLAUDE.md`, want
      `check-test-harness-coverage.sh` handhaaft die koppeling.

Verify: `bash scripts/run-single-test.sh tests/test_kanban_dispatch.py` groen.
`bash scripts/check-test-harness-coverage.sh --strict` groen.

### Fase 6 — Projects-blokken

- [ ] Verwijder in `features/projects/ProjectsPage.tsx` het amber `<Card>`-blok
      met "Want to build a new app-idea, spec-driven?". Verwijder daarmee ook
      `startNewApp`, `startingNewApp`, `metaProject`, `projectFolderFor`,
      `SPEC_DRIVEN_FLOW_DOC_URL` en de imports van `kanbanApi`, `toast`,
      `useNavigate`, `Sparkles`, `ArrowRight` en `Loader2`.
- [ ] Verwijder `<WachtrijSection />` en
      `features/projects/components/WachtrijSection.tsx`.
- [ ] Controleer `kanbanApi.startNewApp` en `kanbanApi.wachtrij` op andere
      consumenten. Verwijder ze alleen als die er niet zijn. De `/new-app`-skill
      blijft als slash-command bestaan; alleen de knop verdwijnt.

Verify: `( cd frontend && ./node_modules/.bin/vitest run
src/features/projects/ProjectsPage.test.tsx )`. Die test kent het blok
waarschijnlijk en moet mee.

### Fase 7 — Subscriptions/Usage repareren

- [ ] In `SubscriptionUsageSection.tsx`: filter rijen met
      `betrouwbaarheid === 'onbekend'` uit de hoofdlijst. Toon ze als één regel
      achter een `<details>`.
- [ ] Hijs `<AnthropicPlanTierSelect>` uit de rij-loop naar de kop van de Card.
      Nu is die alleen zichtbaar als de Anthropic-rij toevallig rendert.
- [ ] In `SubscriptionUsageRowItem.tsx`: toon bij `drempel_gebruikt > 1` een
      expliciete waarschuwing in plaats van een stil op 100% geklemde balk.
- [ ] Zet de tier via `PUT /api/v1/subscriptions/anthropic/plan-tier` op
      `max_20x`. Meet daarna opnieuw.

Verify: `curl -s http://127.0.0.1:8000/api/v1/subscriptions/usage` geeft een
Anthropic-rij onder 100% met `beschikbaar: true`. De UI toont één zinvolle rij.

### Fase 8 — Documentatie en scripts

- [ ] Verwijder de docs waarvan het onderwerp zelf verdwijnt:
      `agent-mail-spec.md`, `upstream-presence-removal-decision.md`,
      `updates-feature-decision.md`, `risk-class-taxonomie.md`,
      `portfolio-security-handoff.md`, `security-scanning-decision.md`.
- [ ] Zet `veilig-bouwen-en-uitleveren.md` op `status: superseded`. Doe dat ook
      bij elke andere doc waarvan de kern een verwijderde feature is. Zo blijft
      het beslisspoor leesbaar zonder vijftig docs te herschrijven.
- [ ] Werk `CLAUDE.md` bij: de Agent Mail-bullet uit de fork-header, en de
      verwijderde scripts uit het `# Test`-blok. De SecretStore-verwijzing in
      §3c blijft, want secrets blijven bestaan.
- [ ] Kort de featurelijst in `docs/cockpit/00-orientation.md` en `README.md` in.
- [ ] Voeg per verwijdering een regel toe aan `docs/cockpit/decisions.md` met
      datum 2026-08-13 en de reden.
- [ ] Werk `.claude/skills/git-ship/SKILL.md` bij én de
      `_build_ship_instructions`-mirror in `dispatch.py`, in **dezelfde
      commit**. Dit is de drift-val uit Done-kaart `d9447e49`.
- [ ] Loop `scripts/capture-screenshots.sh` en `scripts/sweep_ghost_cards.py`
      na op dode verwijzingen.

Verify: `./scripts/check-doc-links.sh --strict`,
`./scripts/check-doc-frontmatter.sh --strict`,
`./scripts/check-decision-register.sh --strict`,
`./scripts/generate-doc-index.py` en `./scripts/check-doc-readability.py --strict`.

### Fase 9 — Eindvalidatie

- [ ] `./scripts/pytest-compare.sh`. Verwacht alleen FIXED en pre-existing,
      geen NEW.
- [ ] `./scripts/compare-bash-tests.sh` en `./scripts/ruff-compare.sh`.
- [ ] `( cd frontend && npm run lint && npm run build &&
      ./node_modules/.bin/vitest run )`.
- [ ] Regenereer `backend/openapi.snapshot.json`. Zie de waarschuwing in §6.
- [ ] `./scripts/cockpit.sh restart` en klik de app door: Dashboard, Projects,
      Agent Bridge met spawn en attention-badge, Subscriptions, Kanban.
- [ ] `gh run list -R guillaumevandevelde/agent-cockpit --workflow=quality.yml
      --limit 5`.

## 5. Rollback

1. Elke fase is één commit. `git revert <sha>` haalt precies één feature terug.
2. Faalt fase 5 op dispatch-gedrag? De risk_class-laag is puur additief bovenop
   de permissieve default. Terugdraaien herstelt hem volledig.
3. Gaat de OpenAPI-snapshot mis? `git checkout master --
   backend/openapi.snapshot.json`, daarna opnieuw genereren volgens §6.
4. De weggehaalde tabellen blijven als dode tabellen in
   `backend/claude_registry.db` staan. Er is geen migratiesysteem, dus dat is
   prima. Draai **geen** `rm backend/claude_registry.db`: dat wist ook
   MCP-servers, commands en permissions.

## 6. Risico's

**De OpenAPI-snapshot is omgevingsafhankelijk.** Regenereren vanuit een
dev-checkout verwijdert ten onrechte het `/`-pad, omdat `main.py` die route
alleen registreert als `frontend/dist` ontbreekt. Regenereer dus met een
gebouwde `frontend/dist` aanwezig. Diff de snapshot vóór commit op precies één
klasse wijziging: verwijderde paden. Staat `/` in de diff als verwijderd, dan
is de regeneratie fout.

**Dispatch wordt permissief.** Na fase 5 draait elk project met
`skip_permissions=true` en host-worktree-transport, tenzij een expliciete
`KanbanMeta`-override bestaat. Voor het cockpit-repo zelf verandert niets; dat
was al `meta`. Voor externe product-repo's is dit een reële versoepeling.

**Agent Mail-hooks overleven de verwijdering.** Reeds geïnstalleerde
`SessionStart`-hooks blijven een verdwenen endpoint aanroepen. Stap 3.8 levert
het opruimscript. Sessies op andere machines moeten dat zelf draaien.

**Presence-hooks hebben geen installatie-UI meer** na fase 4. Bestaande hooks
werken door. Een verse machine kan ze niet meer via de UI installeren. Wil de
PO dat behouden, verplaats dan de config-snippet naar de Hooks-pagina in plaats
van hem te verwijderen. Deze vraag staat nog open.

**Pytest draait niet lokaal.** Dat is een staande afspraak: CI-only. De
verificatie per fase leunt daarom op `scripts/run-single-test.sh` voor gerichte
bestanden en op CI voor de volle suite. Claim pas groen als CI dat zegt.
