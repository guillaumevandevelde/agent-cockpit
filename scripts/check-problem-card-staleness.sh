#!/usr/bin/env bash
#
# check-problem-card-staleness.sh — flag Backlog [problem] cards whose root
# cause was already addressed by an unrelated merged commit or a decisions.md
# row that landed AFTER the card was created.
#
# Rationale. The engineer persona already tells dispatched sessions to
# "reproduce first, skip impl if it doesn't reproduce" — when the root cause
# is already fixed, the session wastes one worktree + dispatch cycle before
# discovering that. This sweeper pre-computes that signal: any open
# Backlog/Doing/Impediment [problem] card whose title/description keywords
# overlap a decisions.md row OR a git-log commit subject STRICTLY newer than
# the card's `created_at` date is flagged for human triage BEFORE the next
# dispatch tick.
#
# Same-day sources are conservatively excluded (we don't know who came
# first — the card has a full timestamp, the decisions.md row has only a
# date). The sweeper is advisory by default; pass --strict to exit 1 on
# hits (for CI gating or a backlog-cleanup pipeline). Mirrors the posture of
# `check-analysis-outcomes.sh` ("signal, not gate") and
# `check-decision-register.sh`.
#
# Acceptance criteria (kanban card 8b5ff1c3…):
#   1. Flags a Backlog [problem] card whose keywords overlap a decisions.md
#      row OR a merged commit subject that is NEWER than the card's
#      created_at.
#   2. Advisory-only — does not block dispatch.
#
# Usage:
#   scripts/check-problem-card-staleness.sh [--strict]
#                                           [--db PATH]
#                                           [--decisions PATH]
#                                           [--repo PATH]
#                                           [--help]
#
# Env:
#   KANBAN_DB        path to kanban.db
#                    (default: ~/.claude-registry/kanban.db)
#   DECISIONS_MD     path to decisions.md
#                    (default: <repo>/docs/cockpit/decisions.md)
#   REPO_ROOT        git repo root for `git log`
#                    (default: parent of SCRIPT_DIR)
#
# Exit codes:
#   0  clean OR (advisory mode and ≥1 hit)
#   1  --strict and ≥1 hit
#   2  usage error / DB missing / decisions.md missing / repo missing /
#      sqlite query failed / decisions.md unreadable

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(dirname "$SCRIPT_DIR")}"
DB_PATH="${KANBAN_DB:-$HOME/.claude-registry/kanban.db}"
DECISIONS_MD="${DECISIONS_MD:-$REPO_ROOT/docs/cockpit/decisions.md}"

STRICT=0

print_help() {
  sed -n '3,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
  case "$arg" in
    --strict)        STRICT=1 ;;
    --db=*)          DB_PATH="${arg#--db=}" ;;
    --decisions=*)   DECISIONS_MD="${arg#--decisions=}" ;;
    --repo=*)        REPO_ROOT="${arg#--repo=}" ;;
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

if [ ! -r "$DB_PATH" ]; then
  echo "ERROR: kanban DB not found or not readable at: $DB_PATH" >&2
  echo "Set KANBAN_DB=/path/to/kanban.db or pass --db=PATH." >&2
  exit 2
fi

if [ ! -r "$DECISIONS_MD" ]; then
  echo "ERROR: decisions.md not found or not readable at: $DECISIONS_MD" >&2
  echo "Set DECISIONS_MD=/path/to/decisions.md or pass --decisions=PATH." >&2
  exit 2
fi

if [ ! -d "$REPO_ROOT" ]; then
  echo "ERROR: repo root not found at: $REPO_ROOT" >&2
  echo "Set REPO_ROOT=/path/to/repo or pass --repo=PATH." >&2
  exit 2
fi

# ---
# Delegate the SQL + tokenization + cross-referencing to a small inline
# Python helper. Keeping it inline avoids a second on-disk file to keep in
# sync; the logic is well-isolated and the bash side stays in plain
# shell-text-processing land. The output format is TSV so the bash side
# can awk it column-anchored:
#
#   <card_id>\t<title>\t<created_at>\t<kind>\t<source_date>\t<source_ref>\t<overlap_csv>
#
# Stderr is redirected to a tempfile so we can recover it AFTER the exit
# code is known: command substitution eats stderr unless we capture it
# ourselves, but bash sets $? to the substituted command's exit code, so
# the in-flight check (`if [ "$PY_RC" -ne 0 ]`) is reliable. The pattern
# matches check-analysis-outcomes.sh and scripts/worktree-gc.sh's invocation
# of scripts/kanban_active_worktrees.py.
PY_STDERR_FILE="$(mktemp)"
HIT_TSV="$(python3 - "$DB_PATH" "$DECISIONS_MD" "$REPO_ROOT" 2>"$PY_STDERR_FILE" <<'PY'
import json, re, sqlite3, subprocess, sys
from datetime import datetime, timedelta

db_path, decisions_md, repo_root = sys.argv[1], sys.argv[2], sys.argv[3]

# Stop word list — language-agnostic noise that bloats keyword overlap
# without carrying signal. Includes the sweeper's own meta-vocabulary
# ("card", "kaart", "problem") so a card titled "[problem] X" doesn't
# spuriously match any decision that just mentions "card" / "kaart" /
# "problem" generically.
STOP_WORDS = {
    # articles / determiners
    "a", "an", "the", "this", "that", "these", "those",
    # conjunctions
    "and", "or", "but", "nor", "so", "yet", "also",
    # prepositions
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "as", "into",
    "onto", "over", "per", "via", "due",
    # auxiliaries
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "must", "shall", "can",
    # pronouns
    "i", "you", "he", "she", "it", "we", "they", "what", "which", "who",
    "when", "where", "why", "how",
    # quantifiers / adverbs
    "all", "each", "every", "both", "few", "more", "most", "other", "some",
    "such", "no", "not", "only", "own", "same", "than", "too", "very",
    "just", "now", "then", "any", "really",
    # sweeper / commit-noise meta-vocabulary. `test`/`tests` is intentionally
    # NOT filtered: a `[problem] X in tests` card is *about* a test failure,
    # and matching against a fix commit that mentions the same test is the
    # canonical signal. `yes`/`no` are conversational fillers that add
    # nothing to keyword overlap.
    "card", "cards", "kaart", "kaarten", "problem", "self-improve",
    "merge", "feat", "chore", "refactor", "docs", "doc",
    "yes", "no",
}


def tokenize(text):
    """Lowercase, strip non-alphanumeric, drop stopwords + ≤2-char tokens."""
    if not text:
        return set()
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s\-_]", " ", text)
    return {w for w in text.split() if len(w) >= 3 and w not in STOP_WORDS}


# --- Read cards ---
try:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cards = con.execute(
        """
        SELECT id, title, description, created_at, column
          FROM kanban_cards
         WHERE column IN ('Backlog', 'Doing', 'Impediment')
           AND title LIKE '%[problem]%'
           AND title NOT LIKE '%[self-improve]%'
         ORDER BY created_at
        """,
    ).fetchall()
except sqlite3.Error as e:
    print(f"ERROR: sqlite query failed: {e}", file=sys.stderr)
    sys.exit(2)

if not cards:
    sys.exit(0)

# --- Parse decisions.md ---
decision_rows = []
try:
    with open(decisions_md, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            # Skip the header row and the dashed separator row
            if "---" in stripped:
                continue
            # The markdown-link anchor `(./<doc>-decision.md)` reliably marks
            # the boundary between Uitkomst and the trailing Doc/Kaart cells.
            m = re.search(r"\(\./([^\)]+)\)", stripped)
            if not m:
                continue
            head = stripped[:m.start()]
            tail = stripped[m.end():]
            head_parts = [p.strip() for p in head.split("|") if p.strip()]
            tail_parts = [p.strip() for p in tail.split("|") if p.strip()]
            if len(head_parts) < 3:
                continue
            datum = head_parts[0]
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", datum):
                # Header / separator row without a real Datum cell.
                continue
            vraag = head_parts[1]
            # Uitkomst may contain `|` characters; join everything from
            # index 2 onwards (cells 2..n) with " " so a stray pipe doesn't
            # silently truncate the keyword pool.
            uitkomst = " ".join(head_parts[2:])
            doc = m.group(1)
            kaart = tail_parts[-1] if tail_parts else ""
            decision_rows.append({
                "datum": datum,
                "vraag": vraag,
                "uitkomst": uitkomst,
                "doc": doc,
                "kaart": kaart,
                "keywords": tokenize(vraag + " " + uitkomst + " " + doc),
            })
except (OSError, IOError) as e:
    print(f"ERROR: failed to read decisions.md: {e}", file=sys.stderr)
    sys.exit(2)

# --- Get git log ---
# Lower bound: one day BEFORE the earliest card.created_at. Same-day commits
# are conservatively excluded (date-only compare), but we still want the
# day-before for completeness in case the card's timestamp crossed midnight.
min_created_date = (min((c["created_at"] or "")[:10] for c in cards),)
try:
    lower = datetime.strptime(min_created_date[0], "%Y-%m-%d").date() - timedelta(days=1)
except ValueError:
    lower = datetime(2020, 1, 1).date()
since_iso = lower.isoformat()

commits = []
git_failed = False
try:
    proc = subprocess.run(
        ["git", "-C", repo_root, "log",
         f"--since={since_iso}",
         "--pretty=format:%H%x09%ad%x09%s",
         "--date=iso"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    for ln in proc.stdout.splitlines():
        parts = ln.split("\t", 2)
        if len(parts) != 3:
            continue
        sha, date_str, subject = parts
        # date_str from --date=iso looks like "2026-07-17 10:30:45 +0000"
        commit_date = date_str[:10]
        commits.append({
            "sha": sha,
            "date": commit_date,
            "subject": subject,
            "keywords": tokenize(subject),
        })
except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
    print(f"WARN: git log failed: {e}; skipping commit-overlap checks.", file=sys.stderr)
    git_failed = True
except FileNotFoundError as e:
    print(f"WARN: git not found: {e}; skipping commit-overlap checks.", file=sys.stderr)
    git_failed = True

# --- Match cards against sources ---
# MIN_OVERLAP=2 is the small-sample sweet spot:
#   - ≥1 keyword gives too many false positives ("the", "test", "fix", etc.
#     already filtered, but domain words like "test" / "smoke" recur on
#     many unrelated commits).
#   - ≥3 keywords is too strict: a focused commit subject rarely mentions
#     3+ of the card's domain words. Real matches in production have
#     2–5 overlap (e.g. the [problem] card in the originating example
#     overlapped with the matching fix on "cli, provider, smoke, test").
MIN_OVERLAP = 2

for c in cards:
    title = c["title"] or ""
    # Strip "[problem]" prefix and surrounding whitespace so the marker
    # itself doesn't pollute the keyword set.
    title_clean = re.sub(r"^\s*\[problem\]\s*", "", title, flags=re.IGNORECASE)
    card_kw = tokenize(title_clean + " " + (c["description"] or ""))
    card_created = (c["created_at"] or "")[:10]
    if not card_kw:
        continue
    title_flat = title.replace("\t", " ").replace("\n", " ")

    # Decision rows: strictly newer than card.created_at (same-day excluded).
    for dr in decision_rows:
        if dr["datum"] <= card_created:
            continue
        overlap = card_kw & dr["keywords"]
        if len(overlap) >= MIN_OVERLAP:
            overlap_sorted = ",".join(sorted(overlap))
            print(f'{c["id"]}\t{title_flat}\t{card_created}\tdecision\t{dr["datum"]}\t{dr["doc"]}\t{overlap_sorted}')

    # Commits: strictly newer than card.created_at (same-day excluded).
    for cm in commits:
        if cm["date"] <= card_created:
            continue
        overlap = card_kw & cm["keywords"]
        if len(overlap) >= MIN_OVERLAP:
            overlap_sorted = ",".join(sorted(overlap))
            print(f'{c["id"]}\t{title_flat}\t{card_created}\tcommit\t{cm["date"]}\t{cm["sha"][:8]}\t{overlap_sorted}')

if git_failed and sys.stderr.isatty():
    # Surface the warning outside the tempfile pipeline when running
    # interactively — already printed by the except branches above.
    pass

con.close()
PY
)"
PY_RC=$?
if [ "$PY_RC" -ne 0 ]; then
  echo "ERROR: staleness query failed (exit $PY_RC); see stderr above." >&2
  [ -s "$PY_STDERR_FILE" ] && cat "$PY_STDERR_FILE" >&2 || true
  rm -f "$PY_STDERR_FILE"
  exit 2
fi
rm -f "$PY_STDERR_FILE"

# Empty stdout from Python means clean.
if [ -z "$HIT_TSV" ]; then
  echo "OK: no Backlog [problem] cards overlap with newer decision/commit signals."
  exit 0
fi

total=$(printf '%s\n' "$HIT_TSV" | wc -l | tr -d ' ')
echo "WARNING: ${total} Backlog [problem] card(s) may already be resolved by newer work:" >&2
echo "" >&2
printf '%s\n' "$HIT_TSV" | awk -F'\t' '
  {
    badge = ($4 == "decision") ? "decision" : "commit"
    printf "  [%s] %s  %s\n", badge, $1, substr($2, 1, 70)
    printf "             card-created: %s, source: %s %s, overlap: %s\n", $3, $4, $5 " " $6, $7
  }
' >&2
echo "" >&2
echo "These cards were created BEFORE a decision row / commit subject with overlapping keywords." >&2
echo "Per the engineer persona: reproduce first; skip impl if it doesn't reproduce." >&2
echo "Consider triaging these BEFORE the next dispatch tick — the fix may already be in master." >&2
echo "" >&2
echo "Source cross-reference: decisions.md (Datum/Vraag/Uitkomst/Doc) + git log --since=<earliest card.created_at>." >&2

if [ "$STRICT" -eq 1 ]; then
  exit 1
fi
echo "(advisory — not failing the build; run with --strict to enforce)" >&2
exit 0
