---
title: "Architectuur — de vier lagen, wat wat mag weten, en hoe dat wordt afgedwongen"
type: reference
status: active
---

# Architectuur — lagen en grenzen

**Dit document bestaat omdat het ontbrak.** Van de 130 documenten in `docs/cockpit/` beschreef er geen enkel hoe het systeem is opgedeeld. Elke standaard die een poort kan afdwingen — ruff, mypy, bandit, pytest — werd streng nageleefd. Elke standaard die alleen een mens vasthoudt viel weg. Daarom staan de regels hieronder niet alleen opgeschreven maar ook in de CI.

Achtergrond en meting: [`cockpit-richting-decision.md`](./cockpit-richting-decision.md) §2, ontwerp in [`kernharding-design.md`](./kernharding-design.md) §3.

## De vier lagen

| Laag | Pakketten | Verantwoordelijkheid |
|---|---|---|
| **Transport** | `app/api/`, `app/mcp_server/` | Ingangen. HTTP- en MCP-oppervlak, verder niets. |
| **Domein** | `app/kanban/`, het merendeel van `app/services/` | Beslissingen: welke kaart, welke provider, is deze klaar. |
| **Mechanisme** | `app/services/agentic_cli/`, `app/services/scheduling/`, alles wat `subprocess`, git of tmux aanraakt | Hoe iets feitelijk gebeurt. |
| **Persistentie** | `app/models/`, `app/kanban/models.py`, `database.py`, `kanban/db.py` | Opslag en schema. |

Eén eerlijkheid: deze indeling loopt **dwars door `app/services/` heen**. Dat is geen ontwerp maar een gegeven. De regels gelden daarom per subpakket, niet per hoofdmap.

## De regels

### 1. Transport is een blad

Domein en persistentie importeren nooit `app.api` of `app.mcp_server`. Alleen `app/main.py` bindt de transportlaag in.

Afgedwongen door import-linter (`[tool.importlinter]` in `backend/pyproject.toml`). Stond bij invoering op één overtreding: `app.kanban.mcp_health` importeerde `app.main` als gemaksdefault en trok daarmee de hele transportlaag het domein in. Die is opgelost — de route geeft `request.app` mee — niet weggeschreven in een uitzondering.

### 2. Het domein raakt geen mechanisme

`app.kanban` importeert geen `subprocess`. Dit contract kijkt alleen naar **directe** imports; een keten domein → service → `subprocess` is juist de gelaagdheid die we willen, want het mechanisme zit dan al achter een service.

Dit draait als **ratel**. De uitzonderingslijst staat in `pyproject.toml` en mag alleen korter worden:

| Module | Sinds |
|---|---|
| `app.kanban.dispatch` | 2026-08-15 |
| `app.kanban.headless_runner` | 2026-08-15 |
| `app.kanban.session_cleanup` | 2026-08-15 |
| `app.kanban.session_recovery` | 2026-08-15 |
| `app.kanban.project_key` | 2026-08-15 |
| `app.kanban.token_saver` | 2026-08-15 |
| `app.kanban.acp_transport` | 2026-08-15 |

De laatste twee importeren `subprocess` binnen een functie in plaats van op modulehoogte. Een grep op regelbegin ziet die niet; import-linter wel.

### 3. Een bestand boven 800 regels mag niet groeien

Geen importregel had `dispatch.py` op 10.110 regels voorkomen — alle imports daarin zijn keurig. Wat ontbrak was een grens op **groei**. Elke afzonderlijke toevoeging was verdedigbaar; niemand bewaakte de som.

`scripts/check-file-size-ratchet.sh` legt de omvang van elk bestand boven de drempel vast in `.file-size-baseline`. Krimpen mag altijd en schuift de baseline mee omlaag. Groeien mag nooit, ook niet via `--update`. Zakt een bestand onder de drempel, dan valt het uit de baseline en is het weer vrij.

Bij invoering: 21 bewaakte bestanden. Bijkomend effect: een kaart die `dispatch.py` aanraakt moet er iets uithalen om er iets in te mogen zetten.

### 4. Geen belofte zonder rij

Er bestaat geen `scheduler.add_job` zonder een databaserij die de belofte vastlegt. De database is de waarheid, de scheduler is een cache.

De scheduler draait op APScheduler's in-memory jobstore, dus elke job sterft met het proces. Voor een terugkerende job is dat onschuldig: de volgende start installeert hem opnieuw. Voor een **eenmalige** job niet — die vuurt daarna nooit meer. Zo bleef een kaart na een herstart claimed staan met `pane_resume_pending=True` en niemand die hem nog kon nudgen.

`services/scheduling/reconciler.py` leest die rijen bij het opstarten terug en installeert de jobs opnieuw; achterstallige beloften vuren meteen. Dat generaliseert `recurring_triggers.run_boot_inhaal`, dat na een gemiste maandag al precies dit deed.

`scripts/check-add-job-callers.sh` bewaakt de vorm: `_sched.add_job` mag alleen in `scheduler.py` staan, plus twee geratelde plekken (`kanban/dispatch.py`, `scheduling/auto_resume.py`) die alleen korter mag worden.

**Opgelost op 2026-08-16.** `auto_resume_service.set_enabled` schreef alleen naar een geheugen-dict, dus auto-resume stond na elke herstart stil weer uit. Er is nu een `auto_resume_configs`-tabel; de route schrijft door en `reconciler.hydrate_auto_resume` laadt de rijen bij het opstarten terug. De dicts blijven bestaan als cache, en wat deze draai al is gezet wint van de rij.

**Wel opgevallen, niet opgelost:** de route is `/auto-resume/{cwd:path}`, en zo'n padparameter levert het pad *zonder* leidende slash. De sleutel die de API vastlegt (`home/me/project`) is dus niet dezelfde string als de `cwd` die de session-hook doorgeeft (`/home/me/project`). Lezen en schrijven via de API zijn onderling consistent, dus de instelling werkt vanuit de UI — maar de hook-kant kijkt mogelijk naar een andere sleutel. Dat vraagt een meting op een echte hook-payload voordat er iets aan verandert.

## Wat hier nog moet gebeuren

`headless_runner.py` en `acp_transport.py` zijn feitelijk mechanisme dat in de domeinmap woont. Ze verplaatsen is opruimwerk en gebeurt bij gelegenheid, volgens de snoeiregels uit [`cockpit-richting-decision.md`](./cockpit-richting-decision.md) §6 — niet in één grote beweging.

## Zelf draaien

```bash
cd backend && lint-imports              # de twee importcontracten
./scripts/check-file-size-ratchet.sh    # de omvangsratel
bash scripts/test_check_file_size_ratchet.sh   # het harnas van de ratel zelf
```

Alle drie draaien in de `backend-lint`-job van `quality.yml`, met de `if: !cancelled()`-vorm zodat één run álle falende poorten toont in plaats van alleen de eerste.
