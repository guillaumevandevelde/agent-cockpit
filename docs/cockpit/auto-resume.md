---
title: "Auto-resume — sessie hervatten na een limietmelding"
type: how-to
status: active
---

# Auto-resume — sessie hervatten na een limietmelding

Wanneer een sessie tegen een limiet aanloopt, houdt Claude Code de pane
open en blijft `auto_resume` wachten tot het reset-moment. Op dat moment
wordt er een korte tekst naar de pane gestuurd zodat de sessie haar werk
oppakt — zonder dat de operator hoeft mee te kijken.

## Standaardtekst

De standaard is **`"OK"`**. Kort, neutraal, en niet te onderscheiden van
een menselijke operator-bevestiging die per ongeluk voorbij scrolt. Een
langere zin zou Claude in een werkmodus trekken die afleidt van de
oorspronkelijke taak, en kost onnodig input-tokens.

## Per-project tekst instellen

Een operator kan per project een eigen tekst kiezen — bijvoorbeeld om de
sessie extra context mee te geven, of om een eigen korte variant te
kiezen. De instelling hangt op `auto_resume_service`, dezelfde plek als
de bestaande `set_enabled`-toggle.

```python
from app.services.scheduling.auto_resume import auto_resume_service

# Eigen tekst voor /pad/naar/project
auto_resume_service.set_message("/pad/naar/project", "Hervat de analyse van dataset X")

# Teruglezen
auto_resume_service.get_message("/pad/naar/project")
# -> 'Hervat de analyse van dataset X'

# Onbekend project valt terug op de standaard
auto_resume_service.get_message("/ander/project")
# -> 'OK'
```

De instelling is in-memory en per project-cwd. Een backend-restart zet 'm
terug op de standaard — gelijk aan hoe `set_enabled` werkt.

## Resolutie-volgorde

`schedule_resume(cwd, reset_time, tz_name, message=…)` (in
[`backend/app/services/scheduling/auto_resume.py`](../backend/app/services/scheduling/auto_resume.py))
kiest de tekst in deze volgorde:

1. Expliciete `message`-parameter — blijft de override-route voor
   code-paden die een eenmalige nudge nodig hebben.
2. Per-project instelling via `get_message(cwd)`.
3. `DEFAULT_RESUME_MESSAGE` (`"OK"`).

De pane-resume-route in `dispatch.py` (de `try_pane_resume`-keten) leest
uit dezelfde `get_message(cwd)`-lookup, dus een ingestelde project-tekst
bereikt zowel het geplande- als het pane-resume-pad zonder extra
wijzigingen aan call sites.

## Bron-ankers

- `backend/app/services/scheduling/auto_resume.py:60-72` — definitie + commentaar van `DEFAULT_RESUME_MESSAGE`
- `backend/app/services/scheduling/auto_resume.py:81-104` — `get_message` / `set_message`
- `backend/app/services/scheduling/auto_resume.py:296-318` — `schedule_resume` resolutie-volgorde
- `backend/tests/test_auto_resume.py::TestDefaultResumeMessage` — pin op korte standaard
- `backend/tests/test_auto_resume.py::TestResumeMessageOverride` — pin op per-cwd override
- `backend/tests/test_auto_resume.py::TestScheduleResumeMessagePropagation` — pin op prioriteit (expliciet > per-cwd > default)

Aanleiding: kanban-kaart `9c7ef6b1…` ("[chore] Configureerbare resume-tekst").
