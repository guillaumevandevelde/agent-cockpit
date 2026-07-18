# Kanban — known follow-ups (post-v1)

These came out of the final code review (2026-06-14). The merge-blockers were fixed
(see commits `d42cde2`, `7769d88`); the items below were consciously deferred.

## Should fix before activating sync / multi-project hardening

- **I4b — `.mcp.json` write path allowlisting. — FIXED (2026-07-12).**
  `_write_project_mcp_config` now validates `project_path` against the `projects`
  table (`_assert_registered_project_path`): an explicit path that is not a
  registered project raises `UnregisteredProjectPathError`, surfaced as a 403 by
  the create/update/delete/registry-install endpoints. `project_path=None` still
  falls back to the server cwd (server-controlled, not client-supplied) and is
  allowed; read paths are unchanged. Covered by
  `backend/tests/test_mcp_config_path_allowlist.py` (unknown path fails, known
  path writes, race where the path is deregistered just before the write fails
  cleanly). Out of scope (separate tracks): MCP-server trust-model hardening and a
  general write-anywhere auditor for other endpoints.

## UX / polish

- **R1 — reviewer gate reviewt in direct-ship-modus ná de merge.** De
  onafhankelijke reviewer-kolom-gate (gebouwd 2026-07-18, kaart `b493d3eb…`,
  `reviewer-agent-decision.md` § Iteratie 3) stuurt een afgeronde kaart naar de
  `reviewer`-kolom vóór Done — maar in direct-ship-modus heeft de engineer al
  naar `master` gemerged. De kaart bereikt Done pas na akkoord, maar de code
  staat al op master; een afkeuring un-merget niet. Voor een échte pre-merge
  gate: forceer pull-request-modus voor gegatede kaarten (PR blijft open tot de
  reviewer akkoord geeft), óf laat de engineer in direct-modus committen op de
  branch zonder te mergen en laat de reviewer bij akkoord de merge doen. Beide
  raken de ship-flow (`_build_ship_instructions` + `git-ship` SKILL) en vielen
  bewust buiten de bouw-kaart.

- **M4 — rank reordering in the UI.** Backend supports `rank` (LWW) and `MoveRequest.rank`,
  but the board only sends `{column}` on drop, so within-column reordering is inert and
  cards always sort by create-time HLC. Wire drag-drop to compute and send a `rank`.
- **M6 — hardcoded UI claimant `"me@ui"`.** All UI claims share one identity. Give the
  UI a real per-user/per-session claimant label.
- **M1 — `priority`/`labels` bypass LWW.** Set unconditionally in `_materialize` (fine for
  HLC-ordered replay and the live newest-tick path, but inconsistent with title/description).
  Make them LWW for uniformity if they become concurrently editable.
- **M2 — empty `update` ops.** A no-field PATCH / `update_card(id)` still appends an op and
  bumps `updated_at`, polluting the activity feed. Short-circuit when the payload is empty.

## Sync milestone — FROZEN (scaffolding pruned 2026-06-18)

Decision: **prune the dead sync seam, freeze the HLC/op-log/LWW core.** See the trade-off
in `sync-hlc-freeze-vs-prune.md`. `sync.py` (`ops_since` / `ingest_ops` / `SyncTransport` /
`LocalNoopTransport`) and its tests are removed; the HLC, op-log and per-field LWW stay as
a documented dormant core (they still power the activity feed, replay ordering and claim
arbitration). To revive sync when a 2nd device is real:

- Implement a real `SyncTransport` (Turso/libSQL embedded replica or `sqld`, or a REST
  push/pull). Re-add `ops_since` / `ingest_ops` (git history has the pruned version) and
  wire them on a schedule.
- Introduce Alembic migrations for the kanban store before a non-wipeable remote primary
  exists (deferred from v1 per the plan; materialized tables remain rebuildable via
  `rematerialize()`).
- Add the optional **push-on-idle** initiative layer (reuse the scheduled-messages delivery
  engine to inject the next card on the `Stop` hook).

## Upstream Agent Team Presets — deliberately NOT adopted (2026-07-08)

Decision: **don't port upstream's Agent Team Presets/launch-orchestration/second Agent
Mail MCP shim** — see the trade-off in `upstream-agent-teams-decision.md`. It's a
competing orchestration paradigm to our kanban-dispatch + kanban-based Agent Mail, not a
complement. Only the universal provider-correctness bugs it surfaced were cherry-picked
(Codex `reasoning_effort` support, the Bedrock-env fallthrough in `provider_env.py`,
explicit OpenCode rejection of `reasoning_effort`).

**If you're picking up the "Agent Bridge UI-cluster" card (team lanes/filter/roles):**
build on the existing `backend/app/services/runs/groups.py` model (auto-detect +
manual grouping of already-running runs) — there is no preset/slot API, and that's
intentional, not an oversight.

**Update (2026-07-08) — that card was picked up and the cluster was split, not built
as-is.** `ebecf1c`/`ba50de8`/`1840c05`/`ded4f35`/`6577a87` (team lanes, team filter, team
roles, slot colors, the fullscreen design spec) all read `session.team_preset_id` /
`team_slot_name` / `team_slot_role`, which upstream's `discovery.py` only populates from
`CLAUDE_DECK_TEAM_PRESET_*` tmux env vars set by the rejected preset launch-orchestration
— our sessions never get those vars, so the fields are always empty and the UI would be
dead weight. None of these five were ported.

**Correction (2026-07-09)** — the 2026-07-08 pass over-scoped the keyboard-shortcuts pair.
`2b77891`/`0352e21` (leader-key `Ctrl+Space` navigation + its discoverability dialog) sit
on top of `ebecf1c`, but their own `displayedTargets` memo in `CCBridgePage.tsx` is
`layoutMode.kind === 'lanes' ? teamLanes... : layoutMode.kind === 'single' ? [target] :
activeTargets` — the fallback is the plain multi-pane grid we already have, with zero
`TeamLanesView` involvement, and the `single`-fullscreen case matches our existing
`fullscreenTarget` state 1:1. Only the `'lanes'` branch is team-lane-only. So prev/next/jump
navigation and the `r` read-only toggle are real, generic pane-navigation features, not
"dead weight without lanes." **Ported**, adapted to this fork's `fullscreenTarget: string |
null` model (`displayedTargets = isFullscreen && fullscreenTarget ? [fullscreenTarget] :
activeTargets`, no `layoutMode`/lanes branch) — see `leaderShortcuts.ts` (pure key-detection,
unit-tested) and `leaderNavigation.ts` (pure wrap-around index math, unit-tested).

`465d354`/`ae5562e` (terminal contrast/light-theme fixes) are split differently than that
pass assumed: `ae5562e` exclusively patches `frontend/src/lib/agentTeamColors.ts`, which
doesn't exist here — **not ported**. `465d354` touches both `TeamLanesView.tsx`/
`agentTeamColors.ts` (not ported, files don't exist) *and* generic contrast fixes in
`CCBridgePage.tsx` (the focused-pane ring class) and `TerminalView.tsx` (read-only/
interactive button contrast, connected-status contrast) that have nothing to do with team
theming — those pieces **were ported** alongside the keyboard-shortcuts work, since the
shortcuts-discoverability chip they style didn't exist until this pass added it.

What *was* independent and got cherry-picked onto `master`: `e6756a7`/`efe0755` (Agent
Bridge image attachments — paste/drag-drop an image into a tmux session, with the paste
endpoint enforcing an attached interactive relay). Adapted to drop upstream's MCP shim
integration (`agent_mail_server.py` doesn't exist in this fork) and to fit our
already-diverged `TerminalView.tsx` (no team-slot theming, no `instance`/`session` props).

## Work-type → agent-routing — analysed, awaiting decision (2026-07-09)

`labels` on a card (free-text, `CardEditDialog.tsx`) has zero effect on which persona
dispatches it — routing is `card.agent` (manual) → column-name-as-persona → hardcoded
`"engineer"` fallback (`dispatch.py:61-85`). See `work-type-routing-analysis.md` for
the full analysis, a proposed `work_type` field + mapping, and open questions
(taxonomy, whether `developer`/`tester`/`code-review` in `_IMPEDIMENT_AGENTS`
(`router.py:45-50`) are a real roadmap or vestigial cruft, whether `ship_mode="direct"`
needs a human review gate) before this gets implemented.

## Upstream removed legacy Presence — deliberately NOT adopted (2026-07-08)

Decision: **keep Presence as-is.** See the trade-off in
`upstream-presence-removal-decision.md`. Upstream's `588cf6c`/`b4e3e87` disable-then-remove
Presence on `upstream/master`, but that history diverged from ours after the fork point
(`42429f3`) — it's not a cleanup we missed, it's a consequence of upstream's own,
independent direction. Our fork kept building on Presence after the fork (attention
notifications, `tmux_pane` plumbing, cascade-delete, flaky-test fixes, most recently
2026-07-02), and CC Bridge's attention indicator
(`frontend/src/features/cc-bridge/useAttentionByPane.ts`,
`frontend/src/hooks/useAttentionNotifications.ts`) reads live off the Presence websocket —
removing it would break that feature with no replacement. No code or `CLAUDE.md` change
needed; `CLAUDE.md` already correctly lists Presence as a current feature/route.

## Upstream removed Docker support — deliberately NOT adopted (2026-07-09)

Decision: **keep Docker as Cockpit's primary/recommended flow.** See the trade-off in
`upstream-docker-removal-decision.md`. Upstream's `2e1ebea`/`91401f7` remove Docker support
on `upstream/master`, but (same pattern as the Presence decision above) that history
diverged from ours after the fork point (`42429f3`) — not a cleanup we missed. Docker is
actively used on three independent, actively-maintained tracks in this fork: `README.md`'s
"Quick Start with Docker" onboarding path, the Sandcastle feature's Docker sandbox provider
(with recent security-hardening commits), and the scheduled-messages fase-2 environment
("Optie A (gekozen): Docker" in `docs/cockpit/00-orientation.md`). Removing it would break
all three with no replacement. No code or `CLAUDE.md`/`README.md` change needed — both
already correctly describe Docker as the current primary flow.

## Updates (self-update) feature — keep as-is, close CLAUDE.md docs gap (2026-07-09)

Decision: **keep the in-UI self-update button as it is.** See the analysis in
`updates-feature-decision.md`. This is **not** an upstream-inherited feature as the original
card description framed it: `git log upstream/master -- 'scripts/update.sh' ...` is empty
and the merge-base `42429f3` predates the first fork-side commit (`1bf5920`,
"feat(updates): in-UI 1-click self-update with SSE progress and auto-rollback",
2026-07-02). `scripts/update.sh` already pulls from `origin` (this fork), not from
`upstream`, so the "follow release.yml flow instead of upstream" concern doesn't apply — the
implementation is already aligned with the fork's release model
(`release.yml` = release-author flow, `update.sh` = release-consumer flow). The single
follow-up is a one-line addition of "Updates" to `CLAUDE.md`'s feature list (the feature
already exists in code, in `App.tsx`, in `lib/navigation.ts`, in `docs/features/updates.md`,
and in `backend/tests/test_update_api.py` — it was just missing from the top-level
feature-list line in `CLAUDE.md`). Done in this same commit.

## Reviewer-agent + review-kolom — lichtere feature-compliance review WEL bouwen (2026-07-10, revised)

**Eerste iteratie (2026-07-10 ochtend):** "niet bouwen". Op drie punten fout:
(1) `/code-review` (slash-command op de diff) werd verward met feature-compliance
review (kaart-spec ↔ implementatie); dat zijn verschillende vragen. (2) Het
cleared-context-effect van een verse subagent-sessie werd onderschat als "slechts
andere prompt"; het substantieve verschil is dat author-context motivated
reasoning introduceert. (3) Een betrouwbare pre-Done gate werd geframed als
autonomy-*reducing*; correct is autonomy-*enabling* (minder handmatige
menselijke verificatie nodig).

**Revised beslissing:** **wél bouwen, in een lichtere vorm** — een
feature-compliance-review (FCR) als subagent-call binnen de engineer-sessie,
direct vóór `move_card Done`. Geen aparte `reviewer.md` persona, geen
Review-kolom, geen concurrency-cap-impact. De FCR voedt de reviewer alleen
met (kaart-titel + -beschrijving + diff tegen `origin/master`) en laat 'm
beoordelen of de implementatie de gevraagde feature is — niet of de code
goed is (dat doet `/code-review` al).

Trade-off in `reviewer-agent-decision.md` (gemarkeerd als REVISED). Concrete
vervolgkaart: één engineer-kaart die `engineer.md` §"Zelf-review" uitbreidt
met één nieuwe subagent-call-stap (vergelijkbaar met de bestaande
`/code-review`-regel); optioneel dezelfde stap in
`_build_ship_instructions(ship_mode)` zodat ook auto-dispatch-sessies de
FCR krijgen; optioneel scope tot `work_type in ("feature", "bug")` om
`chore`/`analysis`-kaarten overbodige overhead te besparen. Geschat: halve
tot hele dag werk + een empirische check op de eerste 5-10 kaarten na
introductie of de FCR dingen vindt die `/code-review` miste.

**Wat we NIET bouwen (en waarom):** een aparte Reviewer-persona + kolom +
dispatch-flow staat buiten scope. De kosten daarvan (extra sessie,
concurrency-cap-blokkade, visuele complexiteit, routing-ambiguïteit) zijn
reëel en wegen niet op tegen de marginale extra waarde boven de
subagent-FCR. Voor *post-Done* twijfel bestaat `request_review` al (zie
`backend/app/kanban/service.py:async def request_review`) — die maakt een
nieuwe analysis-kaart aan voor de analyst.

**Wanneer heroverwegen:** als de FCR in praktijk geen blokkeringen oplevert
die `/code-review` niet al ving (empirisch meetbaar op de eerste 5-10
kaarten) → terugtrekken. Bij te veel vals-positieven → scope verfijnen
(alleen `feature`/`bug`). Bij bugs die door FCR + CI glippen → CI strakker.
Voor echte four-eyes-eisen → menselijke reviewer via `ship_mode="pull-request"`.

**Let op voor toekomstige analyses:** dit onderzoek toonde dat "zelfde
model, andere prompt" op zichzelf geen reden is om een fresh-reviewer-stap
af te doen. Het cognitieve verschil komt van *cleared context*, niet van
de prompt. Een toekomstige vraag over een vergelijkbare
kwaliteits-uitbreiding moet dat onderscheid vanaf het begin maken.
