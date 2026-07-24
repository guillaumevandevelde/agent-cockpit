---
title: "Analyse — MCP-disconnect vs. claim-release + her-dispatch (kaart 4ed4edb9)"
type: analysis
status: active
---

# Analyse — MCP-disconnect vs. claim-release + her-dispatch

**Datum:** 2026-07-24
**Trigger:** kanban-kaart `4ed4edb9…` "[self-improve] MCP-serverdisconnect leidt tot
claim-release + her-dispatch terwijl de sessie nog productief is". Waargenomen tijdens
kaart `b00f3705…` (Lemma-analyse), 2026-07-21 rond 19:17 UTC.
**Bron-van-waarheid voor het incident:** `logs/backend/run-20260721-211720-1745-0.log`
(regels 1–25).

## Samenvatting

Het vermoeden op de kaart — "een tijdelijke `cockpit-kanban` MCP-disconnect leidt tot
claim-release + her-dispatch" — is **weerlegd**. De MCP-disconnect was een *broertje-
symptoom* van dezelfde gebeurtenis die de her-dispatch veroorzaakte, niet de oorzaak.
De feitelijke trigger was **startup session-recovery** (`recover_interrupted_sessions`),
die na een backend-herstart (uvicorn `--reload`, WatchFiles) om 19:17:20 draaide.

Even belangrijk: er was in dit incident **geen dubbel werk**. De claimant-flip
`312c → 6b59` was een **resume-in-place** — dezelfde worktree, `claude --resume` —
géén tweede, concurrente sessie op dezelfde kaart. Het schrikbeeld van de kaart
(twee sessies tegelijk, merge-conflict) heeft zich hier niet voorgedaan.

## Het bewijs (backend-log 19:17:20–19:17:22)

```
regel 3   Started reloader process using WatchFiles          # uvicorn --reload herstart
regel 16  kanban op: update card b00f3705… payload_keys=['resume_project_folder','resume_session_id']
regel 17  killed old session k-product-analy-312c for card b00f3705…
regel 18  release card b00f3705…                             # release_without_terminal_move telt hier NIET
regel 19  claim card b00f3705…  (claimed_by)                 # nieuwe claim
regel 22  Spawned claude-code session k-product-analy-6b59
          in .../worktrees/k-product-analy-312c  (mode=resume)   ← BESLISSEND
regel 25  resumed interrupted session for card b00f3705…
          (session 309c2a4f-… -> k-product-analy-6b59)
```

Regel 22 is beslissend: sessie `6b59` startte in de worktree **van `312c`** in
`mode=resume`. Dat is per definitie een hervatting van dezelfde conversatie in
dezelfde werkboom — niet een tweede parallelle sessie. Dezelfde massa-hervatting
raakte in dezelfde milliseconde meerdere kaarten (`333af652…`, `3abcd501…`, …),
wat past bij één startup-recovery-pass, niet bij een MCP-hik van één sessie.

## Waarom de kaart-hypothese niet klopt

De liveness-heuristiek leest **nergens** MCP-verbindingsstatus:

- `_live_sessions()` (`dispatch.py`) queryt uitsluitend `tmux list-sessions`.
- De reaper (`reap_stale_claims`) en `session_recovery._recoverable` beslissen op
  tmux-aanwezigheid (plus SandcastleRun-rows / headless-registry voor die transports).
- Een MCP-disconnect verwijdert een sessie **niet** uit `_live_sessions()`.

Een `claude`-proces draait *binnen* zijn tmux-sessie en kan die niet overleven:
**tmux-aanwezigheid is dus een correcte proxy voor proces-liveness.** Fork (A) uit de
impediment — "tmux-liveness gaf een verkeerd antwoord" — valt daarmee af: 312c was
niet meer in de tmux-snapshot omdat het proces daadwerkelijk weg was (de recovery
hervatte het correct in-place). Fork (B) — "de doorlopende sessie was in werkelijkheid
`6b59`" — is wat regel 22 aantoont.

## Toetsing aan de acceptance criteria

- **AC1 (reproduceer/weerleg + documenteer het mechanisme).** Weerlegd. Mechanisme:
  backend-herstart → `recover_interrupted_sessions` → `recover_project` →
  `redispatch_card(caller_source="recover_interrupted_sessions")` → kill +
  resume-in-place. Geen reaper, geen stale-detection, geen MCP-signaal.
- **AC2 (MCP-disconnect mag geen release veroorzaken; liveness aan het proces).**
  Al waar: liveness hangt aan tmux (= proces), niet aan MCP. Vastgelegd door
  `test_reaper_spares_live_session_regardless_of_mcp_state` (reaper-pad) en de nieuwe
  `test_recover_project_retains_claim_for_live_session_despite_mcp_disconnect`
  (recovery-pad).
- **AC3 (release met levend proces moet zichtbaar zijn op het bord).** Elke
  her-dispatch post nu `**Note:** Redispatched via <caller_source>` in de activity-feed
  (commit `96c5c32`), dus de recovery-pad-hervatting is bordzichtbaar. Als
  `redispatch_card` bij een *verse* `_live_sessions()`-query alsnog een levende sessie
  ziet (stale-snapshot-race), post het een extra live-kill-`**Note:**` — die tekst is
  nu **caller-aware** en beschuldigt geen operator meer bij een automatische recovery.
- **AC4 (regressietest disconnect-zonder-procesdood → claim behouden).** Toegevoegd op
  het recovery-pad (zie AC2). De claim blijft behouden, geen kill, geen resume.

## Correcties t.o.v. de vorige poging (waarom deze kaart heropend was)

De vorige fix (commits `f7196ad`, `96c5c32`) was in de kern juist (MCP is geen
liveness-bron) maar mis-**documenteerde** de trigger: de commit-message noemde
`redispatch_card` een "manual operator override", en de live-kill-audit-comment zei
letterlijk "the operator / an explicit redispatch call chose to restart anyway". Bij
een automatische startup-recovery koos niemand iets. Deze kaart repareert die
mis-attributie:

- De `redispatch_card`-docstring erkent nu expliciet de automatische caller
  (`recover_interrupted_sessions`) naast de operator-callers.
- De live-kill-audit-comment is **caller-aware**: automatische recovery krijgt
  "No operator chose to restart"; operator/`ui`/`mcp:*`/`bulk_orphans` behouden het
  operator-narratief. Zo wijst de activity-feed een toekomstige debugger niet meer naar
  de verkeerde actor.
