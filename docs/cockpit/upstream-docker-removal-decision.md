# Upstream verwijderde Docker-support — overnemen? Trade-off + beslissing

> Kanban-kaart: "BESLISSING: upstream verwijderde Docker-support — meenemen in Cockpit?"
> DoD van de kaart: blijft Cockpit bij Docker als primaire flow, of sluiten we aan bij
> upstream en verwijderen we het — **voordat** dit als gewone implementatiekaart
> behandeld wordt.

## Context

Upstream (`adrirubio/claude-deck`) verwijderde Docker-support met twee commits, beide op
`upstream/master` en **niet** op onze `master`:

| Commit | Wat |
|---|---|
| `2e1ebea` (2026-06-30) | chore: remove docker support (#255) |
| `91401f7` (2026-06-30) | docs: remove stale docker launch copy |

Docker-support zelf (`972c354`, "feat: add Docker files and backend integration",
2026-02-09) zit vóór de merge-base `42429f3` ("release: prepare v1.3.0", 2026-06-09) — het
is dus gedeelde geschiedenis, niet iets dat de forks los van elkaar bedacht hebben. Precies
hetzelfde patroon als de eerdere Presence-beslissing (`upstream-presence-removal-decision.md`):
géén cleanup die wij gemist hebben, maar een architectuurkeuze die upstream **na** het
fork-punt zelfstandig maakte, in een richting die bij ons niet is gevolgd.

## Heeft Cockpit Docker nog actief gebruikt/uitgebreid?

Ja, aantoonbaar en op meerdere onafhankelijke sporen. `git log master --not upstream/master
-- Dockerfile docker-compose.yml docker-compose.sandcastle.yml .sandcastle/` toont eigen
werk tot vorige week:

- `5c405b9` chore(docker): add KANBAN_DATABASE_URL env var for kanban.db persistence
- `7097eb1` fix(docker): use npm ci --legacy-peer-deps to handle typescript 6 peer dep conflict
- `bbe0792` feat(sandcastle): integrate sandcastle for isolated agent execution
- `6b2b259` security(sandcastle): harden Dockerfile and add docker-compose security
- `5de09aa` security(sandcastle): add secure docker-compose for agents
- `6cd8c33` feat(sandcastle): wire sandcastle transport + kanban auto-dispatch polling

Verder is Docker in drie onafhankelijke lagen verankerd:

1. **Hoofd-app onboarding.** `README.md` noemt Docker in de tech-stack-tabel
   ("Containerization: Docker + Docker Compose") en heeft een eigen sectie **"Quick Start
   with Docker"** (`docker compose up`) als de aanbevolen manier om de app te draaien —
   niet een bijzaak.
2. **Sandcastle-feature (actief in ontwikkeling).** Sandcastle draait AI-agents in
   geïsoleerde sandboxes; Docker is één van de ondersteunde `sandbox_provider`-waardes
   (naast Podman/Vercel/no-sandbox), met een eigen `docker-compose.sandcastle.yml` en
   `.sandcastle/Dockerfile`, en recente security-hardening-commits. Dit is een kernfeature
   van deze fork, geen restant.
3. **Fase 2 (scheduled-messages, huidige hoofdinitiatief).** `docs/cockpit/00-orientation.md`
   noemt expliciet **"Optie A (gekozen): Docker"** (`docker compose up -d`) als de manier
   om de stack te draaien, met manueel draaien (`./scripts/install.sh` + `./scripts/dev.sh`)
   als "Optie B: fallback". `docs/cockpit/fase-1-validation.md` vereist `docker compose up
   -d` + Docker Desktop WSL-integratie als voorwaarde voor de runtime-validatiechecklist.

## Zou verwijderen breaking zijn?

Ja, op alle drie de lagen hierboven: het zou `README.md`'s hoofd-onboardingpad slopen, de
Sandcastle Docker-sandbox-provider (met eigen security-hardening) wegtrekken, en de
expliciet gekozen omgeving voor de lopende fase-2-validatie (`fase-1-validation.md`)
ondermijnen — zonder vervanging. Dit is dus geen "opgeruimde restcode", maar drie
onafhankelijke, actief onderhouden gebruikers van dezelfde Docker-laag.

**Kanttekening (geen reden om te verwijderen, wel een openstaand punt):** de runtime-
validatiechecklist in `fase-1-validation.md` toont alle Docker-runtime-punten nog als
⬜ (niet uitgevoerd) — de code-level validatie is groen, maar niemand heeft `docker compose
up -d` in deze WSL-omgeving nog daadwerkelijk bevestigd. Dat is een aparte, bestaande
taak (de fase-1-runtime-validatie zelf), geen argument tegen Docker als richting.

## Aanbeveling

**Niet overnemen.** Cockpit blijft bij Docker als primaire/aanbevolen flow, precies zoals
`README.md` en `docs/cockpit/00-orientation.md` het nu al beschrijven. Upstream's
verwijdering is het gevolg van hun eigen, divergerende richting (mogelijk omdat hun
Agent-Mail/Agent-Bridge-stack of hun eigen deployment-model containerisatie overbodig
maakte) — bij ons is Docker juist de dragende laag onder drie actieve initiatieven
(hoofd-app onboarding, Sandcastle, scheduled-messages-omgeving). Overnemen zou die kapot
maken zonder vervanging, en niets in de kaart of het onderzoek wijst op een probleem met
Docker zelf (geen CVE, geen onderhoudslast, geen klacht) dat verwijdering zou motiveren.

`CLAUDE.md` en `README.md` noemen Docker terecht nog als bestaande flow — geen wijziging
nodig.

### Wanneer heroverwegen

- Als upstream's eigen reden voor verwijdering (issue #255, niet ingezien in dit onderzoek
  omdat de issue niet in deze repo-checkout zit) een concreet probleem beschrijft dat ook
  bij ons speelt (bv. een onderhoudslast, security- of build-issue in de Docker-laag zelf),
  is dat een aparte bugfix-kaart op onze eigen Docker-bestanden — geen reden om de hele
  flow te schrappen.
- Als Sandcastle's Docker-sandbox-provider ooit uitgefaseerd wordt ten gunste van enkel
  Podman/Vercel, vervalt één van de drie gebruikerslagen — de andere twee
  (hoofd-app-onboarding, fase-2-omgeving) blijven dan nog steeds reden om Docker te houden.
- Als de fase-1-runtime-validatie (de ⬜-punten hierboven) faalt — Docker Desktop's
  WSL-integratie blijkt niet werkbaar in de praktijk — dan is dat een reden om **Optie B
  (manueel)** als primaire flow te promoveren, niet om Docker te verwijderen (Sandcastle en
  README Quick Start blijven het elders gebruiken).

## Wat deze kaart doet

Alleen dit document + een verwijzing in `kanban-followups.md`. Geen codewijziging: de
beslissing is "niets doen", d.w.z. bewust niet overnemen van upstream's verwijdering.
