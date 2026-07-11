# Spec-driven development — Fase 0 beslissing (consolidatie spec-boom)

> Kanban-kaart: **`[spec-ssot]` Fase 0: consolideer naar één canonieke spec-boom**.
> Context: [`spec-driven-development-analysis.md`](./spec-driven-development-analysis.md) §5-6.
> Menselijke go/no-go: **Optie B** (maximalistische Fase 0), gegeven op 2026-07-10.

## Waarom Fase 0

"Single source of truth" is onmogelijk zolang drie plan-/spec-bomen concurreren
(`docs/cockpit/`, `docs/superpowers/`, `docs/plans/`). Fase 0 maakt `docs/cockpit/`
expliciet én **afdwingbaar** canoniek en is de voorwaarde voor Fase 1 (spec-link op
kaarten) en Fase 2 (drift-signaal).

## De beslissing: Optie B

De gebruiker koos **Optie B — Fase 0 maximalistisch**: naast de minimale index/markering
(Optie A) ook de *structurele* consolidatie fysiek uitvoeren, plus de promotie van alle
superpowers-werkoutput naar canonieke docs.

### Wat in déze kaart is uitgevoerd (structurele consolidatie + afdwinging)

1. **`docs/plans/` gearchiveerd** → `git mv docs/plans docs/plans-legacy` (omkeerbaar, git-historie
   + interne links intact). LEGACY-banner + "waar vind ik het actuele doc"-tabel in
   [`../plans-legacy/README.md`](../plans-legacy/README.md). VitePress `srcExclude` bijgewerkt
   (`plans/**` → `plans-legacy/**`) zodat de map buiten de site blijft.
2. **Canonieke index** [`README.md`](./README.md): per feature het leidende cockpit-doc +
   de superpowers-tegenhanger.
3. **Promotie-contract zichtbaar én controleerbaar**: [`../superpowers/README.md`](../superpowers/README.md)
   bevat het contract + een **promotie-ledger** (elke plan/spec → status + doeldoc).
   `scripts/check-superpowers-promotions.sh` flag't (advies, niet-blokkerend; `--strict` optioneel)
   elke werkoutput-file die niet in de ledger geregistreerd staat. Als advies-stap opgenomen
   in CI (`quality.yml`), gemodelleerd naar `check_openapi_snapshot.py` maar bewust **signaal
   i.p.v. harde gate** (analyse §4 optie C, §7 "vermijd theater").
4. **`00-orientation.md`** drie-bomen-tabel bijgewerkt: nieuwe paden + verwijzing naar de
   afdwingbare index/ledger.

### Wat is doorgeschoven naar een follow-up-kaart

Optie B eist dat **elke** superpowers-plan wordt gepromoot naar een canoniek
`docs/cockpit/`-doc. Ongeveer twaalf paren hebben nog geen eigen canoniek doc (zie de ⏳
pending-rijen in de ledger). Dat is een aparte, inhoudelijke schrijf-exercitie per feature —
Optie B staat expliciet toe dit "in een aparte analyst-kaart" te doen. Daarom:

- **Deze kaart** levert de *structurele* consolidatie + de afdwingbare promotie-infrastructuur
  (ledger + checker), zodat het promotie-contract vanaf nu zichtbaar en controleerbaar is.
- **Follow-up `[spec-ssot]` Fase 0b** promoot de resterende ⏳-rijen één-voor-één naar canonieke
  cockpit-docs (of markeert ze als "opgenomen in `CLAUDE.md`/`00-orientation.md`").

Deze splitsing houdt de structuur-consolidatie (klein, omkeerbaar, snel te reviewen) los van
de content-authoring (groot, per-feature), zonder Optie B's reikwijdte te laten vallen.

## Raakvlak met de "Docs-sweep"-kaart

De bestaande Backlog-kaart *"Docs-sweep + consistentiecontrole terminologie"* overlapt **niet**:
die gaat over **terminologie-consistentie** (oude termen als `platform_env`/`AgentTeam` vervangen
volgens [`terminology.md`](./terminology.md)), Fase 0 gaat over **structuur-consistentie** (welke
boom is canoniek). Orthogonale scopes → ze lopen **parallel**, Docs-sweep gaat niet op in Fase 0.

## Niet gekozen

- **Optie A (minimaal)** — alleen index + banners, geen fysieke archivering. Afgewezen: de gebruiker
  koos B.
- **Sterke/maximalistische SSOT-vorm** (harde, blokkerende spec-gate op elke functionele diff) —
  buiten scope van Fase 0 en door de analyse afgeraden (§3-4, §5 "waarom niet de sterke vorm nu").
