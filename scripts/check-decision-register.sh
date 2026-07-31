#!/usr/bin/env bash
#
# check-decision-register.sh — advisory drift check for the decision register.
#
# Verifies that every decision document (docs/cockpit/*-decision.md) is linked
# from the canonical register (docs/cockpit/decisions.md). It does NOT verify
# that the register row is content-correct — only that each decision has a
# visible, browsable entry, which is the discoverability gap the register was
# created to close (see docs/cockpit/decisions.md, "Waarom dit bestaat").
#
# Scope: the *-decision.md naming convention only. The register also indexes
# spikes and analysis docs that carry a verdict (spike-*.md, *-analyse.md), but
# those names don't reliably distinguish "has a decision" from "is background
# reading", so enforcing them would produce false positives. Adding those rows
# stays a human call; this check guards the unambiguous class.
#
# Advisory by design: exits 0 even when docs are unlinked (prints a warning),
# mirroring check-superpowers-promotions.sh ("signal, not gate"). Pass --strict
# to exit 1 on any unlinked doc (e.g. to harden into a blocking CI step later).
#
# --check-headers adds a second class of drift: each *-decision.md must carry
# the four-field header (Datum/Status/Kaart/Uitkomst) at the top, and the
# Uitkomst field must agree with the register row's Uitkomst column. This is
# the cross-check the register originally lacked (kaart 78cb8ce3…).
#
# Usage:
#   scripts/check-decision-register.sh [--strict] [--check-headers]
#
# Env:
#   DECISIONS_DIR   directory holding decisions.md + *-decision.md
#                   (default: <repo>/docs/cockpit; overridden by the tests)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DECISIONS_DIR="${DECISIONS_DIR:-$REPO_ROOT/docs/cockpit}"
REGISTER="$DECISIONS_DIR/decisions.md"

STRICT=0
CHECK_HEADERS=0
for arg in "$@"; do
  case "$arg" in
    --strict)       STRICT=1 ;;
    --check-headers) CHECK_HEADERS=1 ;;
    --help|-h)
      sed -n '3,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
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

if [ ! -f "$REGISTER" ]; then
  echo "ERROR: decision register not found at $REGISTER" >&2
  echo "Create it, or point DECISIONS_DIR at the directory that holds decisions.md." >&2
  exit 2
fi

# ---
# Pass 1: register-row discovery for each *-decision.md. Each row's Uitkomst
# cell is what the header check compares against the doc's **Uitkomst:** field.
#
# Two passes:
#   1. Kaart match — when the doc has a Kaart hex id (the canonical case
#      after the four-field backfill, kanban card 9a2c47b1…), find the row
#      whose Kaart column starts with that 8-char prefix and return its
#      Uitkomst. This is the load-bearing path: when a doc accumulates
#      revision rows in the register (a new row every time the decision is
#      re-opened + re-closed), only the row whose Kaart matches the doc's
#      own Kaart field describes that doc. Earlier this script picked the
#      *first* row containing the doc link, which broke as soon as a second
#      row was added (and required the `#8-bis`-anchor workaround in
#      `decisions.md` rows 42-43 to keep the gate green — anchor-based
#      selection, undocumented and not load-bearing in any normal sense).
#   2. First-link fallback — when the doc has no Kaart hex (the placeholder
#      "_zie doc — geen hex-id …" for older decisions), or when the
#      Kaart-prefix yields no row, return the Uitkomst of the first row
#      that contains a link to the doc. Legacy behaviour for docs that
#      pre-date the four-field header convention.
register_uitkomst_for() {
  # $1 = doc basename (e.g. "acp-transport-decision.md")
  # $2 = doc Kaart 8-char prefix (e.g. "3abcd501"), or empty for fallback
  local target="$1"
  local kaart_prefix="$2"

  if [ -n "$kaart_prefix" ]; then
    # Pass 1: Kaart match. The Kaart column is the LAST `|`-separated cell
    # in the row; its text is a backtick-wrapped hex id, optionally followed
    # by `…` (truncation marker), a quoted title, or a "(review: …)" tag.
    # We strip the leading backtick, take the leading hex run (8 chars),
    # and compare against the doc's own 8-char prefix.
    local kaart_match
    kaart_match=$(awk -v target="$target" -v kaart="$kaart_prefix" '
      function trim(s) { sub(/^[ \t]+/, "", s); sub(/[ \t]+$/, "", s); return s }
      function kaart_cell(line,    last, i, c, end) {
        # Find the position of the last non-empty cell. Markdown pipe-tables
        # end the row with a trailing `|`, so the literal last `|`
        # demarcates an empty cell — we need the SECOND-to-last `|`. If the
        # line has no trailing `|` (older rows in the register), fall back
        # to the last one.
        end = length(line)
        while (end > 0 && substr(line, end, 1) == " ") end--
        if (end > 0 && substr(line, end, 1) == "|") end--
        last = 0
        for (i = 1; i <= end; i++) {
          c = substr(line, i, 1)
          if (c == "|") last = i
        }
        if (last == 0) return ""
        return trim(substr(line, last + 1))
      }
      function cell_prefix_matches(cell, prefix,    s, hex, c) {
        if (cell == "" || prefix == "") return 0
        s = cell
        if (substr(s, 1, 1) == "`") s = substr(s, 2)
        hex = ""
        while (length(s) > 0) {
          c = substr(s, 1, 1)
          if (c ~ /[0-9a-f]/) {
            hex = hex c
            s = substr(s, 2)
          } else {
            break
          }
        }
        return (length(hex) >= 8 && substr(hex, 1, 8) == prefix) ? 1 : 0
      }
      function uitkomst_of(line, target,    link, i, n, parts, k, cell) {
        # Anchor on the doc-link so a Uitkomst cell containing its own `|`
        # is not truncated by an over-eager split (kaart 225a77e8…).
        link = "(./" target ")"
        i = index(line, link)
        if (i == 0) return ""
        # substr ends 3 chars before `(` — leaves the cell prefix
        # `[\`<name>` as a discardable fragment; we rejoin parts[4..n-1] as
        # Uitkomst so an internal `|` survives.
        n = split(substr(line, 1, i - 3), parts, "|")
        if (n < 4) return ""
        cell = parts[4]
        for (k = 5; k < n; k++) cell = cell "|" parts[k]
        return trim(cell)
      }
      {
        if (cell_prefix_matches(kaart_cell($0), kaart)) {
          print uitkomst_of($0, target)
          exit
        }
      }
    ' "$REGISTER")
    if [ -n "$kaart_match" ]; then
      printf '%s' "$kaart_match"
      return
    fi
  fi

  # Pass 2: first-link fallback. Identical to the original implementation;
  # kept separate so the Kaart-path can be reasoned about (and tested) in
  # isolation. |head -1| guards against the unlikely case of two plain-link
  # rows for the same doc (the current register has none, but a future
  # revision that forgets the #8-bis lesson might re-introduce them).
  awk -v target="$target" '
    function trim(s) { sub(/^[ \t]+/, "", s); sub(/[ \t]+$/, "", s); return s }
    {
      link = "(./" target ")"
      i = index($0, link)
      if (i == 0) next
      n = split(substr($0, 1, i - 3), parts, "|")
      if (n < 4) next
      cell = parts[4]
      for (k = 5; k < n; k++) cell = cell "|" parts[k]
      print trim(cell)
    }
  ' "$REGISTER" | head -1
}

# Extract the 8-char hex prefix from a doc's Kaart field. Empty when the
# field has no hex (e.g. the "_zie doc — geen hex-id …" placeholder).
kaart_prefix_for() {
  # $1 = Kaart field value (e.g. "3abcd501…", "3672c0730b1b4b7ea31a52c414d17729",
  #     "1fafd87c19e54ef1aa48936e8759ce06", or "_zie doc — geen hex-id …")
  printf '%s' "$1" | awk '
    {
      s = $0
      while (length(s) > 0 && substr(s, 1, 1) !~ /[0-9a-f]/) s = substr(s, 2)
      if (length(s) >= 8) print substr(s, 1, 8)
      else if (length(s) > 0) print s
    }
  '
}

# ---
# Pass 2: header-validate each *-decision.md. Emits per-file messages on
# stderr; returns 0 if clean (no missing fields + every Uitkomst matches).
HEADER_FIELDS=(Datum Status Kaart Uitkomst)

hdr_extract() {
  # $1 = file, $2 = field label (without the surrounding `**`/bold).
  # Echoes the first line that looks like "**Label:** value" with the prefix
  # stripped, or empty string if absent.
  awk -v label="$2" '
    {
      l = $0
      # match only the canonical "**Label:**" prefix on its own line
      if (l ~ /^\*\*'"$2"':\*\*/) {
        sub(/^\*\*'"$2"':\*\*/,"",l)
        sub(/^[ \t]+/, "", l)
        sub(/[ \t]+$/, "", l)
        print l
        exit
      }
    }
  ' "$1"
}

hdr_normalize() {
  # Strip surrounding backticks (when present), collapse internal whitespace.
  local s="$1"
  # Strip surrounding ` characters used to mark literal card-ids
  if [[ "$s" == \`*\` ]]; then
    s="${s:1:${#s}-2}"
  fi
  printf '%s' "$s" | awk '
    {
      gsub(/[ \t][ \t]+/, " ", $0)
      gsub(/^[ \t]+/, "", $0)
      gsub(/[ \t]+$/, "", $0)
      print
    }
  '
}

missing_header=()
header_mismatches=()
missing=()

while IFS= read -r -d '' f; do
  base="$(basename "$f")"
  rel="${f#"$REPO_ROOT"/}"

  # --- Existing link-presence check ---
  if ! grep -qF "$base" "$REGISTER"; then
    missing+=("$rel")
  fi

  # --- Header check (only if --check-headers) ---
  if [ "$CHECK_HEADERS" -eq 1 ]; then
    problems=()
    for field in "${HEADER_FIELDS[@]}"; do
      val="$(hdr_extract "$f" "$field")"
      if [ -z "$val" ]; then
        problems+=("$field")
      fi
      # Quote-safe assignment. Previously `eval "val_${field}='$val'"` parsed the
      # value through a single-quoted shell string, which silently dropped any
      # `'` characters from values like `work_type='analysis'`. printf -v writes
      # the value verbatim (kaart 225a77e8…).
      printf -v "val_${field}" '%s' "$val"
    done

    # Datum must look like YYYY-MM-DD when present
    if [ -n "${val_Datum:-}" ] && ! [[ "${val_Datum}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
      problems+=("Datum:not-a-date")
    fi

    # Uitkomst must match the register row (whitespace-normalized prefix).
    # Pass the doc's Kaart 8-char prefix so register_uitkomst_for can pick
    # the row whose Kaart column matches — without it, the script would
    # fall back to the first-link match, which silently picks the wrong
    # row once a doc accumulates revisions in the register (kanban-kaart
    # 9a2c47b1…).
    if [ -n "${val_Uitkomst:-}" ]; then
      kaart_prefix="$(kaart_prefix_for "${val_Kaart:-}")"
      reg_uitkomst="$(register_uitkomst_for "$base" "$kaart_prefix")"
      if [ -n "$reg_uitkomst" ]; then
        doc_norm="$(hdr_normalize "$val_Uitkomst")"
        reg_norm="$(hdr_normalize "$reg_uitkomst")"
        # Prefix-match: doc's Uitkomst may be a prefix of the register cell
        # (register cells are long; the doc's Uitkomst is the first sentence).
        # If the register cell is very short (< 8 chars after norm), require
        # exact match to avoid spurious matches.
        if [ "${#reg_norm}" -lt 8 ]; then
          if [ "$doc_norm" != "$reg_norm" ]; then
            problems+=("Uitkomst:mismatch")
          fi
        else
          case "$reg_norm" in
            "$doc_norm"*) : ;;  # doc is a prefix of register → ok
            *) problems+=("Uitkomst:mismatch") ;;
          esac
        fi
      fi
    fi

    if [ "${#problems[@]}" -gt 0 ]; then
      # One entry per drifted doc: "<rel>|<comma-joined problems>". Pushing
      # two array elements per doc would make ${#header_mismatches[@]} twice
      # the actual count and turn every "WARNING: N doc(s)" line into a tally
      # that's confusingly off (kaart ce0ea8d6 / card f3ae648c…).
      header_mismatches+=("$rel|$(IFS=, ; echo "${problems[*]}")")
    fi
  fi
done < <(find "$DECISIONS_DIR" -maxdepth 1 -type f -name '*-decision.md' -print0 2>/dev/null | sort -z)

# ---
# Output
header_clean=1
if [ "$CHECK_HEADERS" -eq 1 ] && [ "${#header_mismatches[@]}" -gt 0 ]; then
  header_clean=0
  echo "WARNING: ${#header_mismatches[@]} decision doc(s) have header drift (Datum/Status/Kaart/Uitkomst):" >&2
  # Each entry is "<rel>|<comma-joined problems>"; split on the first '|'.
  for entry in "${header_mismatches[@]}"; do
    rel="${entry%%|*}"
    probs="${entry#*|}"
    echo "  - ${rel}  (${probs})" >&2
  done
  echo "" >&2
  echo "Add the four-field header at the top of each *-decision.md:" >&2
  echo "  **Datum:** <YYYY-MM-DD>  **Status:** besloten|herzien|voorgesteld  **Kaart:** \`<card-id>\`  **Uitkomst:** <register-row first sentence>" >&2
fi

missing_count="${#missing[@]}"
if [ "$missing_count" -eq 0 ] && [ "$header_clean" -eq 1 ]; then
  if [ "$CHECK_HEADERS" -eq 1 ]; then
    echo "OK: every docs/cockpit/*-decision.md is linked from the decision register AND has a complete header."
  else
    echo "OK: every docs/cockpit/*-decision.md is linked from the decision register."
  fi
  exit 0
fi

if [ "$missing_count" -gt 0 ]; then
  echo "WARNING: ${#missing[@]} decision doc(s) not linked from docs/cockpit/decisions.md:" >&2
  for m in "${missing[@]}"; do
    [ -z "$m" ] && continue
    echo "  - $m" >&2
  done
  echo "" >&2
  echo "Add a row to the register in docs/cockpit/decisions.md (newest first):" >&2
  echo "  | <datum> | <vraag> | <uitkomst in één zin> | [\`<doc>.md\`](./<doc>.md) | <kaart-id> |" >&2
fi

if [ "$STRICT" -eq 1 ]; then
  exit 1
fi
echo "(advisory — not failing the build; run with --strict to enforce)" >&2
exit 0
