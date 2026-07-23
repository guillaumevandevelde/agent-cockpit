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
# Emits TSV on stdout:  docbasename<TAB>uitkomst-cell
#
# Pipe-table cells can contain `|`; the doc-link cell reliably ends with a
# `./<doc>-decision.md)` reference, so we use the doc-link's position to
# anchor the Uitkomst slice rather than splitting on every `|`.
register_uitkomst_for() {
  # $1 = doc basename (e.g. "acp-transport-decision.md")
  awk -v target="$1" '
    function trim(s) { sub(/^[ \t]+/, "", s); sub(/[ \t]+$/, "", s); return s }
    {
      # Find the markdown link reference `./<name>)` in the row. We anchor on
      # the link so a Uitkomst cell containing its own `|` is not truncated
      # by an over-eager split (kaart 225a77e8…).
      link = "(./" target ")"
      i = index($0, link)
      if (i == 0) next
      # substr ends 3 chars before `(` — for a `*.md`-named doc that lands on
      # the last `d` of `a-decision.md` (i.e., still inside the link text),
      # so the substring includes the cell prefix `[\`a-decision.md` and the
      # trailing `\`](` is cut. We drop that cell prefix as parts[n] in the
      # join loop below; the offset is therefore not "lands on the cell
      # boundary" but "leaves the cell prefix as a discardable fragment".
      uitkomst = substr($0, 1, i - 3)
      # Split on `|` to find cell boundaries. parts[1] is empty (pre-`|`);
      # parts[2..3] are Datum/Vraag; parts[4..n-1] are Uitkomst (rejoined with
      # `|` so an internal `|` survives); parts[n] is the partial doc-link
      # cell start, which we drop. This used to take only parts[4], silently
      # truncating any Uitkomst containing `|` (kaart 225a77e8…).
      n = split(uitkomst, parts, "|")
      if (n < 4) next
      cell = parts[4]
      for (k = 5; k < n; k++) {
        cell = cell "|" parts[k]
      }
      print trim(cell)
    }
  ' "$REGISTER"
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

    # Uitkomst must match the register row (whitespace-normalized prefix)
    if [ -n "${val_Uitkomst:-}" ]; then
      reg_uitkomst="$(register_uitkomst_for "$base")"
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
