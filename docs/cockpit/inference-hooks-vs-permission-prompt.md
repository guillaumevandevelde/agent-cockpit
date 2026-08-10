---
title: "Anthropic Inference hooks (Aug 5, 2026, Enterprise beta) vs. Cockpit's permission_prompt — composability, overlap, and what we do"
type: decision
status: decided
---

**Datum:** 2026-08-10
**Status:** besloten
**Kaart:** `f3f0146803424ecea522a438a59493d0`
**Uitkomst:** Optie A — **document only**. Voeg een korte footnote toe aan `kanban-conventions.md` §3c die uitlegt dat Cockpit's `permission_prompt` *na* de platform Inference-hooks-laag draait. Geen UI-veranderingen, geen kanban-side integratie van het Inference-hooks-verdict, geen nieuwe follow-up-kaart. De integratieroute blijft het backlog-suggestieblad voor de dag dat een Enterprise-klant ernaar vraagt.

# Inference hooks vs. permission_prompt — composability, overlap, en wat we doen

## Aanleiding

Anthropic's release notes van 5 augustus 2026 introduceren **Inference hooks** in beta voor Claude Enterprise. Iedere governed prompt — over claude.ai, Cowork en Claude Code — gaat eerst naar de *AI security server* van de organisatie voor een allow/deny verdict, vóór de inferentie start. Verzoeken zijn gesigneerd (Standard Webhooks), failure handling is per organisatie instelbaar (block of allow-on-failure), shadow mode en rollout-percentage bestaan, en elke deny landt in de compliance Activity Feed.

De vraag voor Cockpit: wat betekent deze *platform-laag* voor onze lokale `permission_prompt` — het MCP-gate dat Claude Code's `--permission-prompt-tool` voedt en op een mens in de cockpit-UI wacht? Twee lagen, twee operators, twee beslisseenheden; of ze samenhangen, in elkaar grijpen, of met elkaar concurreren bepaalt hoe een cockpit-operator over zijn permission-posture nadenkt.

## Wat zit waar

| Aspect | Inference hooks (Anthropic, platform) | Cockpit `permission_prompt` (lokaal) |
|---|---|---|
| **Laag** | Inline, vóór inferentie — na de request de client verlaat, vóór het model draait | Runtime — aan de *tool-call*-grens, ná de inferentie een tool wil aanroepen |
| **Operator** | Organisatie-security-team (eigen AI security server) | Lokale mens achter de cockpit (kanban UI) |
| **Besliseenheid** | Hele governed prompt (transcript + tool calls + extracted attachment text, geen raw bytes) | Eén Claude Code tool-call met args (Write/Bash/Edit/…) |
| **Verdict-vorm** | `{"action": "allow"}` of `{"action": "deny", "deny_reason": "…"}` | `{"behavior": "allow"}` of `{"behavior": "deny", "message": "…"}` (Claude Code's `--permission-prompt-tool`-contract) |
| **Default bij twijfel** | Instelbaar per org (block *of* allow-on-failure) | **Fail-closed deny** na 300s timeout (zie `mcp_server.py:48`) |
| **Audit-spoor** | Activity Feed, 6 jaar retentie, leesbaar via Compliance API met `read:compliance_activities`-scope | Kanban `KanbanGate`-rij + activity-comment op de kaart, zichtbaar in het project-bord |
| **Scope** | claude.ai, Cowork, Claude Code (web/desktop/CLI binnen Enterprise); **niet** op Bedrock/Google Cloud, **niet** de API-platform, **niet** voice-mode | Elke `--permission-prompt-tool`-flag-wired Claude Code-sessie — alle providers die onze dispatcher spawnt (Anthropic, OpenAI, OpenAI-compatible endpoints) |
| **Roll-out** | Per org: shadow → rollout-% → role-exclusions | Per sessie: dispatcher zet de flag alleen op de product-lane (`skip_permissions=False`); meta-lane houdt de historische bypass |
| **Signing** | Standard Webhooks — organisatie verifieert dat het van Anthropic komt | Geen — gate leeft binnen het vertrouwde cockpit-proces |
| **Bijlage-zicht** | Alleen metadata + extracted text; raw file/image bytes worden nooit gestuurd | Tool-call args in JSON; geen file-content, wel file-paths |

Een derde, verwante laag is Claude Code's **auto-mode classifier** (CC 2.1.222+) — een CLI-interne permissie-classifier die SendMessage doorlaat zonder `--permission-prompt-tool` aan te roepen voor laag-risico tool-calls. Die zit *tussen* de Inference-hook-laag (boven) en onze `permission_prompt` (onder): Inference hook beoordeelt de prompt, auto-mode beoordeelt de tool-call voordat die het MCP-gate bereikt.

## Composability-vraag

De drie lagen chainen in de natuurlijke volgorde:

```
Inference hook (platform, allow/deny)
   ↓ (indien allow)
auto-mode classifier (CLI, ask/allow/deny)
   ↓ (indien ask)
permission_prompt (cockpit, allow/deny)
   ↓ (indien allow)
tool-call executes
```

De lagen *concurreren* niet — ze zijn genest. Een Inference-hook-deny stopt de aanvraag voordat de tool-call ooit bestaat; een cockpit-deny stopt de tool-call die de inferentie heeft overleefd. Beide hebben dezelfde semantiek (allow/deny op een gestructureerd contract), beide loggen naar een audit-spoor (Activity Feed vs. kanban-activity), beide default-on-twijfel verschilt (instelbaar vs. fail-closed-deny).

Het *productverhaal* verandert op drie manieren:

1. **Een permissive Inference hook met een strict lokaal gate** — de Inference hook laat alles door, het cockpit-gate vangt per-tool wat de policy van de organisatie niet afdekt. Dat is vandaag al de default-aanname; Inference hooks maken het alleen zichtbaar door de eerste laag te *benoemen* in plaats van impliciet te laten.
2. **Een strict Inference hook met een permissive lokaal gate** — een DLP-deny in de hook stopt de hele prompt, dus de tool-call bestaat niet eens. Ons lokale gate is dan een second line of defence voor tooling die *buiten* Inference-hook-scope valt (API-platform-sessies, Bedrock-routed providers, custom OpenAI-compatible endpoints).
3. **Beide strict** — Inference hook voor org-wide DLP, lokaal gate voor cockpit-specifieke risico's (dispatch-werkboom, kanban-card-claim, fail-closed timeout). Geen overlap, twee onafhankelijke lagen.

Geen van de drie is een bug; alle drie zijn composing-correct. De *verandering* zit hem in de leesbaarheid van het posture-overzicht voor de operator: nu is "wie mag wat?" één gesprek (cockpit), straks zijn het er drie die de cockpit-operator moet uitleggen aan zijn security-team.

## Aanbeveling — Optie A: document only

We kiezen voor **document only**:

- **Voeg** een korte alinea "Layering" toe aan de docstring van `permission_prompt` in `backend/app/kanban/mcp_server.py` (na invariant AC4) die in één paragraaf uitlegt dat het gate *na* de Anthropic-platform-laag draait: Inference hooks beoordelen de hele prompt server-side vóór inferentie, CC 2.1.222's auto-mode classifier beoordeelt de tool-call voordat die dit gate bereikt, deze tool geeft de lokale mens het laatste woord op één tool-call. Drie lagen chainen, ze concurreren niet. Link naar deze beslisdoc als bron.
- **Voeg** niets toe aan `kanban-conventions.md` — §3c gaat over externe credentials en is niet de juiste plek, en permission_prompt wordt daar verder niet als regel beschreven (geen bestaande anchor om op te hangen). De docstring op de gate zelf is de canonieke plek; een tweede externe footnote is duplicatie zonder nieuw publiek.
- **Maak** geen UI-affordance voor "Inference-hook verdict in de kanban-card tonen" — die informatie komt niet in onze request-flow (Anthropic handelt de hook server-side, vóór Claude Code de prompt ziet), dus we hebben hem niet om te tonen.
- **Maak** geen follow-up feature-kaart voor integratie. Het backlog-suggestieblad (`docs/cockpit/kanban-followups.md`) is de canonieke plek voor "dit kan, wacht op een Enterprise-klant". De integratie wordt relevant zodra een klant de cockpit combineert met een Enterprise-organisatie en expliciet vraagt om de Inference-hook-deny's als kaart-events zichtbaar te maken — op dat moment is een gerichte spike sneller dan een generic feature.

### Waarom niet "integrate"

Drie redenen die de integrate-route vandaag uitsluiten:

1. **De informatie is er niet.** Inference hooks draaien server-side bij Anthropic; Claude Code krijgt de deny niet als metadata in de tool-call die hij naar `--permission-prompt-tool` stuurt. We zouden een nieuwe client-side hook nodig hebben die de prompt-afloop observeert en correleert — en Claude Code exposeert die op dit moment niet in zijn openbare API.
2. **Het publiek is smal.** Inference hooks zijn Enterprise beta; onze dispatcher draait ook op OpenAI en OpenAI-compatible providers waar geen equivalent bestaat. Een cockpit-feature die alleen voor Enterprise-Anthropic-klanten werkt, voor een permission-posture die ze vandaag al elders beheren, is YAGNI tot een Enterprise-klant ernaar vraagt.
3. **De overlap met onze `permission_prompt` is laag-risico op zichzelf.** Ons gate beoordeelt tool-calls, niet prompts; een Inference-hook-DLP-deny voor "stuur geen klant-PII naar het model" raakt een tool-call nooit, want de prompt stopt. Het *security*-argument voor integratie is zwak; het *visibility*-argument (laat de operator zien wat de hook deed) is alleen zinvol als er een cockpit-operator is die tegelijk Enterprise-security-operator is.

### Waarom niet "defer zonder doc"

Het "defer"-antwoord zonder documentatie laat een toekomstige lezer — mens of agent — opnieuw uitvinden dat Inference hooks bestaan en opnieuw concluderen dat we ze negeren. De footnote in `kanban-conventions.md` kost drie zinnen en sluit die heruitvinding af; het is goedkoper dan een tweede analyse-kaart over zes maanden.

## Rest / nazicht

- Bronverwijzingen: [`https://platform.claude.com/docs/en/manage-claude/inference-hooks`](https://platform.claude.com/docs/en/manage-claude/inference-hooks) (gelezen 2026-08-10), [`https://platform.claude.com/docs/en/manage-claude/compliance-activity-feed`](https://platform.claude.com/docs/en/manage-claude/compliance-activity-feed) (gelezen 2026-08-10), [`https://platform.claude.com/docs/en/release-notes/api`](https://platform.claude.com/docs/en/release-notes/api) entry *August 5, 2026* (gelezen 2026-08-10).
- Aangrenzende sweep (2026-07-28) die een verwante MCP-detectieklasse filede: kaart `4781e971dc00498d9da92b19944407a2` (silently-skipped `--mcp-config` at spawn via `mcp_server_errors`, CC 2.1.219) — andere laag (MCP-config, niet inference), zelfde family van "wat merkt de cockpit van wat Claude Code buiten ons gezichtsveld doet".
- Card-beschrijving noemde `scheduled_messages/router.py` als één van de 5 `permission_prompt`-touchpoints; dat pad bestaat niet — de feitelijke touchpoints zijn 6 files: `mcp_server.py` (gate-producer), `dispatch.py` (wire `--permission-prompt-tool`), `agentic_cli/base.py` + `agentic_cli/claude_code.py` (CLI-flag-emit), `scheduling/auto_resume.py` (notification-classifier voor permission_prompt als `other`), `api/v1/session_hooks/router.py` (idem).
