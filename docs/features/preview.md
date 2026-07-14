# Preview (Run this branch)

Per-kanban-kaart live preview van de gebouwde branch, gekoppeld aan de bestaande [`RunService`](#backend-runservice). Stukje uit facet D van `docs/cockpit/veilig-bouwen-en-uitleveren.md` (§6 #9).

## Hoe het werkt

Open een kaart in de kolom **Done** → de drawer toont onder de groene "Completed"-banner een **Preview**-paneel met een **Run this branch**-knop.

1. Klik start een `RunService`-instantie via `POST /api/v1/runs/app` met de project-path en een (MVP-) start-commando.
2. De component pollt `GET /api/v1/runs/app/{instance_id}` elke 1.5 s tot de status `healthy` (health-check gelukt) of `failed` is.
3. Bij `healthy` wordt een activity-comment op de kaart gepost met de live URL (`Preview live: http://127.0.0.1:<port>`) en verschijnt de `PreviewPane` met een iframe + **Stop preview**-knop.
4. Bij `failed` wordt een fout-URL-comment gepost (`Preview failed: <error>`) en toont de pane de foutmelding zonder iframe.
5. **Stop preview** doet `DELETE /api/v1/runs/app/{instance_id}` en ruimt de instantie op.

De activity-comment blijft als breadcrumb in de kaart staan — ook nadat de instantie is gestopt of de backend is herstart.

## Backend: RunService

Geen nieuwe endpoints. Deze feature gebruikt alleen de bestaande RunService-API uit `backend/app/api/v1/app_runs/router.py`:

- `POST /api/v1/runs/app` — start
- `GET /api/v1/runs/app/{instance_id}` — poll
- `DELETE /api/v1/runs/app/{instance_id}` — stop
- `GET /api/v1/runs/app?project_path=…` — list

De service kiest automatisch de schoonste transport: docker/podman-container wanneer beschikbaar, anders subprocess fallback (met een waarschuwing in de logs). URL is altijd `http://127.0.0.1:<port>` — publieke URLs zijn **out of scope** per de kaart-tekst.

## Bekende limitaties (MVP)

- **Start-commando is hard-coded** (`python3 -m http.server 4123`) zodat de flow end-to-end werkt zonder een "run-config"-veld per project. Een echte product-app-start (`npm run dev`, `uvicorn app.main:app --port $PORT`, …) komt in een latere facet-D-kaart die een `run_config`-veld op het project of de kaart introduceert.
- **Branch-resolution**: het start-commando draait in `project_path`, niet in de specifieke branch-worktree. De branch-info zit impliciet in de kaart-context (Done = agent heeft de branch opgeleverd) maar wordt niet actief uitgecheckt. Ook dit komt in een latere kaart.
- **Geen persistentie**: preview-URLs verdwijnen als de backend herstart — expliciet out of scope.

## Frontend-bestanden

- `frontend/src/features/kanban/appsApi.ts` — type-safe wrapper rond `/api/v1/runs/app`.
- `frontend/src/features/kanban/components/PreviewPane.tsx` — iframe + status-badge + Stop-knop.
- `frontend/src/features/kanban/components/CardDrawer.tsx` — `CardPreviewControl` (Done-only) inline-component.
- `frontend/src/features/kanban/types.ts` — `RunInstance` / `RunStatus`-types.