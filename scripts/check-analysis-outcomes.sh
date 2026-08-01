#!/usr/bin/env bash
#
# check-analysis-outcomes.sh — flag Done analyses that lack outcome evidence.
#
# Sweeps kanban_cards WHERE column='Done' AND (work_type='analysis' OR
# agent='analyst') and reports each card that doesn't carry at least one of
# the four outcome witnesses defined by
# docs/cockpit/analysis-outcome-contract-decision.md §5 + §9:
#
#   1. **Outcome:** <value> — <summary>  activity-feed comment (the gate's
#      primary neerslag — backend/app/kanban/mcp_server.py:394-414).
#      For `filed_standalone` the **Outcome:** comment is the only neerslag
#      (no label is set; the §9 decision explicitly keeps label-vocabulary
#      for outcome-taxonomy values like `not-feasible`/`no-action-needed`).
#   2. labels contains 'not-feasible' or 'no-action-needed'
#   3. ≥1 child card (parent_card_id == card.id)
#   4. card.metadata.filed_card_ids is a non-empty list resolving to real
#      cards in the same project_key (the `filed_standalone` analogue of #3
#      for cadence-trigger runs whose findings deliberately carry no
#      `parent_card_id` to the trigger — recurring-cadence-proposal.md §4.3)
#
# A card missing all three is a "verdampte analyse" — exactly the failure mode
# the gate was built to prevent (decision §1). This script is the vangnet
# for the known REST-bypass gap (§5) and the historic back-catalog.
#
# Historic vs. new split. The gate shipped on 2026-07-16 (commit b2e7333 —
# feat(kanban): outcome gate on move_card). Cards Done before that date
# predate the gate and are all "verdampte" by definition; reporting them
# alongside any new offender drowns the signal. The default threshold
# `--since 2026-07-16` separates them; pass `--since=YYYY-MM-DD` to override.
#
# Advisory by design — mirrors check-decision-register.sh ("signal, not gate").
# Default exit 0; pass --strict to exit 1 on any hit. Run with --help for the
# full usage.
#
# Usage:
#   scripts/check-analysis-outcomes.sh [--strict] [--since YYYY-MM-DD] [--db PATH]
#
# Env:
#   KANBAN_DB              path to kanban.db
#                          (default: ~/.claude-registry/kanban.db, overridable
#                           by --db; the bash test harness uses --db to point
#                           at a tmpdir fixture so the real board is untouched)
#   OUTCOME_SINCE          threshold date override (default 2026-07-16, the
#                          commit date of feat(kanban): outcome gate); set
#                          this in CI to shift the historic bucket as the
#                          back-catalog drains.
#
# Exit codes:
#   0  clean (advisory mode: hits printed but not failing)
#   1  --strict mode and ≥1 hit
#   2  usage error / DB missing or unreadable / sweeper query failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Defaults — see commit b2e7333 (feat(kanban): outcome gate on move_card).
SINCE="${OUTCOME_SINCE:-2026-07-16}"
DB_PATH="${KANBAN_DB:-$HOME/.claude-registry/kanban.db}"
STRICT=0

print_help() {
  sed -n '3,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
  case "$arg" in
    --strict) STRICT=1 ;;
    --since=*) SINCE="${arg#--since=}" ;;
    --db=*)   DB_PATH="${arg#--db=}" ;;
    --help|-h)
      print_help
      exit 0
      ;;
    "")
      ;;
    *)
      echo "ERROR: unknown argument '$arg' (see --help)" >&2
      exit 2
      ;;
  esac
done

# Validate --since up front so the SQL path stays honest; we never want a
# typo'd date (e.g. "2026-07-16 ") to silently widen or zero the result.
if ! [[ "$SINCE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "ERROR: --since must be YYYY-MM-DD (got: '$SINCE')" >&2
  exit 2
fi

if [ ! -r "$DB_PATH" ]; then
  echo "ERROR: kanban DB not found or not readable at: $DB_PATH" >&2
  echo "Set KANBAN_DB=/path/to/kanban.db or pass --db=PATH." >&2
  exit 2
fi

# ---
# Delegate the SQL + JSON parsing to a small inline Python helper. Keeping
# it inline avoids a second on-disk file to keep in sync; the logic is small
# (one SELECT with two sub-queries per card) and well-isolated. The output
# format is TSV so the bash side stays in plain shell text-processing land:
#
#   <card_id>\t<title>\t<created_at_iso>\t<missing_csv>\t<is_historic>
#
# A card is a "verdampte analyse" only when ALL THREE witnesses are missing
# (per the acceptance criteria: "geen Outcome-comment, geen label, én geen
# kind-kaarten"). Cards that carry at least one witness — even if it's a
# partial answer — are silently filtered out. `missing_csv` always lists all
# three so a triage reader can see exactly what the card has and lacks.
#
# Stderr is redirected to a tempfile so we can print the diagnosis after a
# non-zero exit. The assignment is in an `||` list so `set -e` does not exit
# before the PY_RC handler runs.
PY_STDERR_FILE="$(mktemp)"
PY_RC=0
HIT_TSV="$(python3 - "$DB_PATH" "$SINCE" 2>"$PY_STDERR_FILE" <<'PY'
import json, sqlite3, sys
db_path, since = sys.argv[1], sys.argv[2]
ANALYSIS = ("analysis", "analyst")
OUTCOME_LABELS = {"not-feasible", "no-action-needed"}
try:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cards = con.execute(
        """
        SELECT id, title, labels, work_type, agent, parent_card_id,
               created_at, project_key, metadata
          FROM kanban_cards
         WHERE column = 'Done'
           AND (work_type IN (?, ?) OR agent IN (?, ?))
        """,
        (*ANALYSIS, *ANALYSIS),
    ).fetchall()
except sqlite3.Error as e:
    print(f"ERROR: sqlite query failed: {e}", file=sys.stderr)
    sys.exit(2)

for c in cards:
    try:
        # `Outcome:` activity-feed comment — the gate's primary neerslag
        # (backend/app/kanban/mcp_server.py:394-414). We accept any of the
        # four canonical values, including `filed_standalone` from §9.
        has_outcome_comment = con.execute(
            """
            SELECT 1 FROM kanban_ops
             WHERE entity_type = 'comment'
               AND entity_id = ?
               AND (
                    json_extract(payload, '$.text') LIKE '**Outcome:** decomposed%'
                 OR json_extract(payload, '$.text') LIKE '**Outcome:** not_feasible%'
                 OR json_extract(payload, '$.text') LIKE '**Outcome:** no_action_needed%'
                 OR json_extract(payload, '$.text') LIKE '**Outcome:** filed_standalone%'
               )
             LIMIT 1
            """,
            (c["id"],),
        ).fetchone() is not None

        # `labels` is stored as a JSON array or null (column is JSON); parse
        # defensively so a corrupt label cell doesn't crash the sweeper.
        raw_labels = c["labels"]
        label_list = []
        if raw_labels is not None and raw_labels != "null":
            try:
                label_list = json.loads(raw_labels) if isinstance(raw_labels, str) else raw_labels
            except json.JSONDecodeError:
                label_list = []
        has_outcome_label = bool(set(label_list) & OUTCOME_LABELS)

        # `decomposed` is verified against real children (mcp_server.py:351-370).
        has_children = con.execute(
            "SELECT 1 FROM kanban_cards WHERE parent_card_id = ? LIMIT 1",
            (c["id"],),
        ).fetchone() is not None

        # `filed_standalone` analogue of `has_children`: the analysis card
        # recorded ≥1 id in `metadata.filed_card_ids` resolving to a real
        # card in its own project_key. We project-scope the resolution so
        # an id from a foreign project_key can't satisfy the witness —
        # mirrors mcp_server.move_card's same-project check (decision §9).
        # A corrupt JSON bag (or missing key) is treated as no-evidence —
        # same posture as the labels parse above. The production schema has
        # only one metadata column (`metadata`); older code paths that
        # referenced `meta` were migrated alongside the column rename and
        # we no longer carry the legacy alias here.
        meta_raw = c["metadata"]
        meta_list = []
        if meta_raw is not None and meta_raw != "null":
            try:
                parsed_meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
            except json.JSONDecodeError:
                parsed_meta = None
            if isinstance(parsed_meta, dict):
                filed_ids_raw = parsed_meta.get("filed_card_ids")
                if isinstance(filed_ids_raw, list):
                    meta_list = [x for x in filed_ids_raw if isinstance(x, str) and x]
        if meta_list:
            placeholders = ",".join("?" * len(meta_list))
            try:
                row = con.execute(
                    f"""
                    SELECT 1 FROM kanban_cards
                     WHERE id IN ({placeholders})
                       AND project_key = ?
                     LIMIT 1
                    """,
                    (*meta_list, c["project_key"]),
                ).fetchone()
                has_filed_standalone = row is not None
            except sqlite3.Error:
                has_filed_standalone = False
        else:
            has_filed_standalone = False
    except sqlite3.Error as e:
        # A schema mismatch (e.g. a fixture without kanban_ops, or a
        # migration in flight) should not silently turn into "OK" — surface
        # it so the operator knows the sweeper couldn't reach a verdict.
        print(f"ERROR: per-card query failed for {c['id']}: {e}", file=sys.stderr)
        sys.exit(2)

    # Card is a hit only if ALL witnesses are absent — anything else
    # means the analysis did produce *some* outcome, even if partial.
    if (has_outcome_comment or has_outcome_label
            or has_children or has_filed_standalone):
        continue

    # Historic = card created before the threshold (default: gate commit
    # date). Use the raw stored created_at — SQLite drops tzinfo on read
    # (see backend/app/kanban/db.py:28-32), so a string compare on the
    # ISO-prefix is the safe ordering.
    created = (c["created_at"] or "")[:10]
    historic = "1" if created < since else "0"

    # title may contain tabs/newlines (rare, but possible) — flatten so the
    # bash awk below stays column-anchored.
    title = (c["title"] or "").replace("\t", " ").replace("\n", " ")
    # Witness list order tracks the four-witness taxonomy introduced in
    # `analysis-outcome-contract-decision.md` §9 (commit-mode split —
    # pre-§9 cards only carry the first three; new cards may have any
    # subset, with the missing-CSV reflecting only what was *absent*).
    print(f'{c["id"]}\t{title}\t{created}\toutcome-comment,label,children,filed_standalone\t{historic}')
con.close()
PY
)" || PY_RC=$?
if [ "$PY_RC" -ne 0 ]; then
  echo "ERROR: kanban-sweeper query failed (exit $PY_RC); see stderr below." >&2
  [ -s "$PY_STDERR_FILE" ] && cat "$PY_STDERR_FILE" >&2 || true
  rm -f "$PY_STDERR_FILE"
  exit 2
fi
rm -f "$PY_STDERR_FILE"

# Empty stdout from Python means clean (no Done-analyses without witnesses).
if [ -z "$HIT_TSV" ]; then
  echo "OK: every Done analysis on this board carries outcome evidence (since $SINCE)."
  exit 0
fi

# Split historic / new for human triage — the historic bucket is unavoidable
# noise pre-gate; the new bucket is the one that actually warrants a closer
# look. We sort by (historic desc, created asc) so the new offender shows
# up alongside the historic ones it joins.
new_count=$(printf '%s\n' "$HIT_TSV" | awk -F'\t' '$5==0 {n++} END {print n+0}')
hist_count=$(printf '%s\n' "$HIT_TSV" | awk -F'\t' '$5==1 {h++} END {print h+0}')
total=$(( new_count + hist_count ))

echo "WARNING: ${total} Done analysis card(s) without outcome evidence" >&2
if [ "$new_count" -gt 0 ] && [ "$hist_count" -gt 0 ]; then
  echo "         (${new_count} since ${SINCE}, ${hist_count} historic — pre-gate)" >&2
elif [ "$new_count" -gt 0 ]; then
  echo "         (all ${new_count} since ${SINCE})" >&2
else
  echo "         (all ${hist_count} historic — pre-gate back-catalog)" >&2
fi
echo "" >&2
printf '%s\n' "$HIT_TSV" | awk -F'\t' '
  {
    badge = ($5 == "1") ? "historic" : "NEW    "
    missing = $4
    gsub(/,/, " ", missing)
    printf "  [%s] %s  %-32s  missing: %s\n", badge, $1, substr($2, 1, 60), missing
  }
' >&2
echo "" >&2
echo "A Done analysis must carry at least ONE of:" >&2
echo "  - a **Outcome:** <value> — <summary> activity-feed comment" >&2
echo "    (any of: decomposed / not_feasible / no_action_needed / filed_standalone)" >&2
echo "  - a 'not-feasible' or 'no-action-needed' label" >&2
echo "  - ≥1 child follow-up card (parent_card_id == card.id)" >&2
echo "  - ≥1 id in metadata.filed_card_ids resolving to a real card" >&2
echo "    in the same project_key (filed_standalone analogue — analysis-" >&2
echo "    outcome-contract-decision.md §9)" >&2
echo "" >&2
echo "See docs/cockpit/analysis-outcome-contract-decision.md §5 + §9." >&2

if [ "$STRICT" -eq 1 ]; then
  exit 1
fi
echo "(advisory — not failing the build; run with --strict to enforce)" >&2
exit 0