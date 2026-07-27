#!/usr/bin/env bash
#
# check-card-where-paths.sh — flag kanban cards whose `Where:` evidence block
# names a repo path that does not exist.
#
# Rationale. `flag-problem` and `session-retro` both template an `## Evidence`
# block with a `- Where: <file:line, endpoint, command, or doc section>` line.
# Nothing validates those paths at authoring time, so a card can ship pointing
# at a file that is not there — and every later card that copies the pattern
# inherits the dead pointer. Concrete instance (kanban card `549ef4d6…`): a
# card named `CLAUDE.md` as the home of the FCR prompt; `CLAUDE.md` has zero
# FCR references, the real mirrors are `.claude/agents/engineer.md` §6 and
# `backend/app/kanban/dispatch.py::_build_ship_instructions`.
#
# A path that does not exist is a documentation-drift bug, not a feature gap —
# and it is cheap to catch mechanically. This sweeper does exactly that:
# extract path-looking tokens from every `Where:` block on the open board,
# strip `:line` / `:line-range` / `::symbol` / `#anchor` suffixes, and `test -e`
# each one against the repo root.
#
# **Existence-only, deliberately.** The originating card asked whether a
# content check (grep for a claimed anchor) is worth it. It is not, at this
# cost/benefit: a `Where:` line names a *location*, not a quotable string, so
# any content assertion would need per-card configuration and would false-
# positive constantly on paraphrased anchors. Existence is necessary-but-not-
# sufficient and catches the whole class of dead pointers (renamed file, moved
# module, typo'd directory) for ~zero authoring cost. The `CLAUDE.md` instance
# above is precisely the case existence-only does NOT catch — that is a known,
# accepted gap; the fix for it was the `.claude/agents/engineer.md` §6 callout
# shipped by card `549ef4d6…`.
#
# Extraction is conservative by design — a false positive costs an author more
# trust than a missed dead pointer costs a reader. A token is checked only when
# it unambiguously looks like a repo path (see `is_path_like` in the inline
# Python below). Absolute paths (`/home/...`) and URLs are skipped: they are
# machine-specific, so their existence is not a property of this repo.
#
# Acceptance criteria (kanban card 500d0948…):
#   1. A card whose `Where:` block names a non-existent path is flagged.
#   2. `file:line` and `module::symbol` suffixes are stripped, so a valid
#      `foo.py:42` ref does not false-positive.
#   3. Advisory by default; `--strict` exits 1.
#
# Usage:
#   scripts/check-card-where-paths.sh [--strict]
#                                     [--db=PATH]
#                                     [--repo=PATH]
#                                     [--card=CARD_ID]
#                                     [--help]
#
# Env:
#   KANBAN_DB   path to kanban.db (default: ~/.claude-registry/kanban.db)
#   REPO_ROOT   repo root the paths resolve against
#               (default: parent of SCRIPT_DIR)
#
# Exit codes:
#   0  clean OR (advisory mode and >=1 hit)
#   1  --strict and >=1 hit
#   2  usage error / DB missing / repo missing / sqlite query failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(dirname "$SCRIPT_DIR")}"
DB_PATH="${KANBAN_DB:-$HOME/.claude-registry/kanban.db}"

STRICT=0
CARD_FILTER=""

print_help() {
  sed -n '3,60p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
  case "$arg" in
    --strict)  STRICT=1 ;;
    --db=*)    DB_PATH="${arg#--db=}" ;;
    --repo=*)  REPO_ROOT="${arg#--repo=}" ;;
    --card=*)  CARD_FILTER="${arg#--card=}" ;;
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

if [ ! -d "$REPO_ROOT" ]; then
  echo "ERROR: repo root not found at: $REPO_ROOT" >&2
  echo "Set REPO_ROOT=/path/to/repo or pass --repo=PATH." >&2
  exit 2
fi

# ---
# Delegate parsing + existence checking to an inline Python helper, matching
# check-problem-card-staleness.sh. Output is TSV so the bash side can awk it
# column-anchored:
#
#   <card_id>\t<column>\t<title>\t<raw_token>\t<normalized_path>
#
# Stderr goes to a tempfile so it can be recovered AFTER the exit code is
# known — command substitution eats stderr otherwise.
#
# The `|| PY_RC=$?` is load-bearing under `set -e`: a bare
# `VAR="$(failing-cmd)"` assignment takes the substitution's exit status, so
# `set -e` would kill the script *before* the PY_RC branch below could print
# anything — turning a diagnosable error into a silent exit 2. Putting the
# assignment in an `||` list suppresses the errexit trigger and keeps the
# handler reachable.
PY_STDERR_FILE="$(mktemp)"
PY_RC=0
HIT_TSV="$(python3 - "$DB_PATH" "$REPO_ROOT" "$CARD_FILTER" 2>"$PY_STDERR_FILE" <<'PY'
import os
import re
import sqlite3
import sys

db_path, repo_root, card_filter = sys.argv[1], sys.argv[2], sys.argv[3]

# A `Where:` line, as templated by .claude/skills/flag-problem/SKILL.md and
# .claude/skills/session-retro/SKILL.md. Tolerates the bold variant
# (`- **Where:**`) seen on real cards.
WHERE_RE = re.compile(r"^\s*(?:[-*+]\s*)?\*{0,2}Where\*{0,2}\s*:\s*", re.IGNORECASE)
BULLET_RE = re.compile(r"^\s*[-*+]\s")

# Trailing `:42`, `:42-99`, `::symbol` — possibly chained
# (`dispatch.py::_build:12`). Stripped before the existence check so a valid
# `foo.py:42` ref does not false-positive (acceptance criterion 2).
SUFFIX_RE = re.compile(r"(?:::[A-Za-z_][A-Za-z0-9_.]*|:\d+(?:-\d+)?)+$")

# Shape of a repo-relative path. Rejects URLs and absolute paths by
# construction: `:` is not in the character class and the first segment may
# not be empty.
PATH_RE = re.compile(r"^[A-Za-z0-9._~@+-]+(?:/[A-Za-z0-9._~@+-]+)*/?$")

KNOWN_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".sh", ".md", ".json", ".yml",
    ".yaml", ".toml", ".cfg", ".ini", ".txt", ".sql", ".html", ".css",
    ".env", ".lock", ".db", ".log", ".svg", ".png",
}

# First segment of a repo-relative path that is unambiguously a path even
# without an extension (`docs/cockpit`, `scripts/lib`).
TOP_LEVEL_HINTS = {
    "backend", "frontend", "scripts", "docs", ".claude", ".github",
    "tests", "logs", "app", "src",
}


def where_blocks(description):
    """Yield the text of each `Where:` block in a card description.

    A block is the `Where:` line plus any continuation lines — real cards
    wrap long path lists across lines. A blank line, a new bullet, or a
    heading ends the block.
    """
    lines = (description or "").splitlines()
    i, n = 0, len(lines)
    while i < n:
        m = WHERE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        block = [lines[i][m.end():]]
        j = i + 1
        while j < n:
            nxt = lines[j]
            if not nxt.strip():
                break
            if BULLET_RE.match(nxt) or nxt.lstrip().startswith("#"):
                break
            block.append(nxt)
            j += 1
        yield "\n".join(block)
        i = j


def tokenize(block):
    """Split a Where block into whitespace-delimited candidate tokens.

    Backtick spans are replaced by their *space-padded* content rather than
    simply unwrapped: on real cards `` `a`/`b` `` (two MCP tool names joined
    by a slash) would otherwise fuse into the single slash-bearing token
    `a/b` and read as a path. Padding turns the separator into its own
    token, which no rule below accepts.
    """
    return re.sub(r"`([^`]*)`", lambda m: " " + m.group(1) + " ", block).split()


def strip_emphasis(t):
    """Remove markdown emphasis only when it wraps the token symmetrically.

    An unbalanced `*` is a glob metachar, not decoration: `*.test.tsx` names
    a *pattern*, and stripping its leading star would leave `.test.tsx`,
    which then reads as a (missing) file — a false positive observed on the
    real board. `_` is never stripped: it is a legal path character, so
    `_helpers.py` must survive intact.
    """
    for marker in ("**", "*"):
        n = len(marker)
        while t.startswith(marker) and t.endswith(marker) and len(t) > 2 * n:
            t = t[n:-n]
    return t


def normalize(token):
    """Strip markdown/prose decoration and location suffixes from a token."""
    t = token.strip()
    # Wrapping punctuation, both ends.
    t = t.strip("`\"'()[]{}<>")
    t = strip_emphasis(t)
    # Trailing prose punctuation. `/` is handled last (directory marker) and
    # `.` only goes when a dot-suffix would otherwise be eaten mid-extension.
    t = t.rstrip(",;!?—–…")
    t = t.rstrip(".")
    t = t.strip("`\"'()[]{}<>")
    # `docs/cockpit/foo.md#section` -> `docs/cockpit/foo.md`
    if "#" in t:
        t = t.split("#", 1)[0]
    t = SUFFIX_RE.sub("", t)
    if t.startswith("./"):
        t = t[2:]
    # Trailing slash is a directory marker, not part of the name.
    t = t.rstrip("/")
    return t


def is_path_like(t):
    """Conservative: only tokens that unambiguously read as a repo path.

    Two-segment slash tokens with no extension (`in/out`, `and/or`,
    `pr/branch`) are prose, not paths — they are skipped unless their first
    segment is a known repo top-level directory. That trades a few missed
    dead pointers for zero prose false positives, which is the right side of
    the trade for an advisory sweeper.
    """
    if not t or not PATH_RE.match(t):
        return False
    segments = t.split("/")
    _, ext = os.path.splitext(segments[-1])
    if ext.lower() in KNOWN_EXTS:
        return True
    if segments[0] in TOP_LEVEL_HINTS:
        return True
    return len(segments) >= 3


try:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    if card_filter:
        cards = con.execute(
            "SELECT id, title, description, column FROM kanban_cards WHERE id = ?",
            (card_filter,),
        ).fetchall()
    else:
        cards = con.execute(
            """
            SELECT id, title, description, column
              FROM kanban_cards
             WHERE column <> 'Done'
               AND description IS NOT NULL
               AND description LIKE '%Where%'
             ORDER BY created_at
            """,
        ).fetchall()
except sqlite3.Error as e:
    print(f"ERROR: sqlite query failed: {e}", file=sys.stderr)
    sys.exit(2)

if card_filter and not cards:
    print(f"ERROR: no card with id '{card_filter}' in {db_path}", file=sys.stderr)
    sys.exit(2)


def flat(s):
    return (s or "").replace("\t", " ").replace("\n", " ")


for c in cards:
    seen = set()
    for block in where_blocks(c["description"]):
        for raw in tokenize(block):
            t = normalize(raw)
            if not is_path_like(t) or t in seen:
                continue
            seen.add(t)
            if not os.path.exists(os.path.join(repo_root, t)):
                print(
                    f'{c["id"]}\t{flat(c["column"])}\t{flat(c["title"])[:70]}'
                    f'\t{flat(raw)}\t{t}'
                )

con.close()
PY
)" || PY_RC=$?
if [ "$PY_RC" -ne 0 ]; then
  echo "ERROR: Where-path scan failed (exit $PY_RC); see stderr below." >&2
  [ -s "$PY_STDERR_FILE" ] && cat "$PY_STDERR_FILE" >&2 || true
  rm -f "$PY_STDERR_FILE"
  exit 2
fi
rm -f "$PY_STDERR_FILE"

if [ -z "$HIT_TSV" ]; then
  echo "OK: every path in a card Where: block exists in the repo."
  exit 0
fi

total=$(printf '%s\n' "$HIT_TSV" | wc -l | tr -d ' ')
cards_hit=$(printf '%s\n' "$HIT_TSV" | cut -f1 | sort -u | wc -l | tr -d ' ')
echo "WARNING: ${total} dead Where: path(s) across ${cards_hit} card(s):" >&2
echo "" >&2
printf '%s\n' "$HIT_TSV" | awk -F'\t' '
  $1 != prev { printf "  [%s] %s  %s\n", $2, $1, $3; prev = $1 }
  { printf "             missing: %s   (from token: %s)\n", $5, $4 }
' >&2
echo "" >&2
echo "These paths do not exist under: $REPO_ROOT" >&2
echo "A Where: pointer that does not resolve sends every later reader down a dead trail." >&2
echo "Fix the card's Evidence block, or — if the path was renamed — point at the new home." >&2
echo "" >&2
echo "Note: existence-only. A path can exist and still not contain what the card claims" >&2
echo "(the CLAUDE.md/FCR case behind card 549ef4d6…); see --help for why no content check." >&2

if [ "$STRICT" -eq 1 ]; then
  exit 1
fi
echo "(advisory — not failing the build; run with --strict to enforce)" >&2
exit 0
