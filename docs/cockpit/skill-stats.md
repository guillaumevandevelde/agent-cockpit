# Skill Stats — per-project skill-gebruik

> **Canoniek** voor de Stats-tab op de Skills-pagina. Beknopt; voor bestandsdetails zie de
> [superpowers-tegenhanger](../superpowers/specs/2026-06-27-skill-stats-design.md).

## Doel

Toon hoe vaak elke geïnstalleerde skill wordt aangeroepen binnen de sessies van het
geselecteerde project, zodat nauwelijks-gebruikte skills verwijderd kunnen worden (minder
context-load).

## Blijvende beslissingen

- **Databron: lokale JSONL-sessielogs**, niet een aparte tracking-tabel. De service leest
  `~/.claude/projects/<project-folder>/*.jsonl`, telt `tool_use`-events met `name: "Skill"` en
  aggregeert per `input.skill`. Zelfde pad-utility en skip-op-parse-error-tolerantie als
  `UsageService`.
- **Scope: alleen het geselecteerde project**, geen cross-project all-time scan.
- **Geen DB-caching** — per-project scans van lokale bestanden zijn snel genoeg.
- **"Never used"-detectie strips de namespace-prefix** (`superpowers:brainstorming` matcht
  geïnstalleerde skill `brainstorming`), zodat de amber "kandidaat om te verwijderen"-callout
  klopt.

## Waar

| Rol | Locatie |
|---|---|
| Service | `backend/app/services/skill_stats_service.py` |
| Endpoint | `GET /api/v1/agents/skills/stats?project_path=…` (`backend/app/api/v1/agents.py`) |
| Schemas | `SkillUsageStat`, `SkillStatsResponse` (`backend/app/models/schemas.py`) |
| Frontend | derde tab `stats` in `frontend/src/features/skills/SkillsPage.tsx` |

Buiten scope (bewust): cross-project aggregatie, tijdvenster-filtering, per-sessie breakdown,
trend-charts, uninstall-actie vanuit de tab.
