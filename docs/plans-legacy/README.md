> # ⚠️ LEGACY — niet meer leidend
>
> Deze map (`docs/plans-legacy/`, voorheen `docs/plans/`) bevat **pre-fork
> claude-deck plan-/ontwerpdocumenten**. Ze zijn **niet canoniek** en worden
> **niet meer bijgewerkt of gebruikt voor nieuw werk**.
>
> **Bron van waarheid voor "hoe werkt de fork vandaag" is `docs/cockpit/`.**
> Zie de index in [`docs/cockpit/README.md`](../cockpit/README.md) voor het
> leidende document per feature.

# Legacy claude-deck plans (gearchiveerd)

Deze documenten zijn bewaard als **historische context / audit-trail** — ze
laten zien hoe de oorspronkelijke claude-deck-features zijn ontworpen vóór de
rebrand naar Claude Cockpit. Ze zijn **read-only referentie**, geen actieve spec.

Gearchiveerd op **2026-07-10** als onderdeel van *`[spec-ssot]` Fase 0:
consolideer naar één canonieke spec-boom* (zie
[`docs/cockpit/spec-driven-development-fase-0-decision.md`](../cockpit/spec-driven-development-fase-0-decision.md)).
De map is hernoemd (`git mv docs/plans docs/plans-legacy`) zodat de git-historie
en interne links intact blijven; er is bewust niets verwijderd (omkeerbaar).

## Waar vind ik het actuele document?

| Onderwerp uit deze map | Actueel canoniek document |
|---|---|
| CC Bridge (`cc-bridge-design.md`, `2026-03-01-cc-bridge-stage1.md`) | Feature is gerealiseerd; zie de code onder `backend/app/services/cc_bridge/` en de API-routes. Geen aparte cockpit-spec (v1 afgerond). |
| Codex CLI-support (`2026-05-*-codex-*.md`) | Gerealiseerd; superpowers-tegenhanger `docs/superpowers/specs/2026-04-10-settings-gap-update-design.md` + code onder `backend/app/services/`. |
| Presence (`presence/`, `2026-03-08-presence-status-accuracy.md`) | Zie `docs/cockpit/upstream-presence-removal-decision.md` voor de actuele beslissing over de Presence-feature. |
| Agent-orchestratie (`2026-03-06-agent-orchestration-design.md`) | Vervangen door de kanban-/multi-agent-laag: `docs/cockpit/kanban-dispatch-spec.md` + `docs/cockpit/multi-agent-kanban.md`. |
| MCP-page / HTTP-hooks / settings-gap | Gerealiseerde features; geen aparte cockpit-spec. |

> Nieuw werk hoort **niet** in deze map. Schrijf ontwerp-/besliswerk voor de fork
> in `docs/cockpit/` (canoniek) of gebruik de `superpowers:writing-plans`-skill
> die naar `docs/superpowers/` schrijft en daarna promoot naar `docs/cockpit/`.
