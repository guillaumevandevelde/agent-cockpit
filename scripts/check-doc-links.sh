#!/usr/bin/env bash
#
# check-doc-links.sh — advisory presence check for relative Markdown links.
#
# Resolves inline Markdown-link targets in docs/cockpit/*.md from the directory
# of the source document. URL fragments are stripped before checking whether
# the target exists. Pure anchors, absolute paths, and URI schemes are ignored.
# Links inside fenced or inline code are not Markdown links and are skipped.
#
# Advisory by design: exits 0 when broken links are found and prints a warning.
# Pass --strict to exit 1 on drift.
#
# Usage:
#   scripts/check-doc-links.sh [--strict]
#
# Env:
#   DOCS_DIR   directory holding the *.md docs to check
#              (default: <repo>/docs/cockpit; overridden by the tests)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DOCS_DIR="${DOCS_DIR:-$REPO_ROOT/docs/cockpit}"

STRICT=0
for arg in "$@"; do
  case "$arg" in
    --strict) STRICT=1 ;;
    --help|-h)
      sed -n '3,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    "") ;;
    *)
      echo "ERROR: unknown argument '$arg' (see --help)" >&2
      exit 2
      ;;
  esac
done

if [ ! -d "$DOCS_DIR" ]; then
  echo "ERROR: docs directory not found at $DOCS_DIR" >&2
  echo "Point DOCS_DIR at the directory that holds the *.md docs." >&2
  exit 2
fi

broken=()
while IFS=$'\t' read -r file line target; do
  case "$target" in
    \#*|/*) continue ;;
  esac
  if [[ "$target" =~ ^[A-Za-z][A-Za-z0-9+.-]*: ]]; then
    continue
  fi

  path="${target%%#*}"
  if [ ! -e "$DOCS_DIR/$path" ]; then
    rel="${file#"$DOCS_DIR"/}"
    broken+=("$rel:$line|$target")
  fi
done < <(
  find "$DOCS_DIR" -maxdepth 1 -type f -name '*.md' -print0 2>/dev/null \
    | sort -z \
    | xargs -0 perl -ne '
        if (/^[ \t]{0,3}(```|~~~)/) { $fenced = !$fenced; next }
        next if $fenced;
        $line = $_;
        $line =~ s/`[^`]*`//g;
        while ($line =~ /!?\[[^]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\s*\)/g) {
          $target = defined($1) ? $1 : $2;
          print "$ARGV\t$.\t$target\n";
        }
        if (eof) { close ARGV; $. = 0; $fenced = 0 }
      '
)

if [ "${#broken[@]}" -eq 0 ]; then
  echo "OK: every relative Markdown link in docs/cockpit/*.md points to an existing target."
  exit 0
fi

echo "WARNING: ${#broken[@]} broken relative Markdown link(s) in docs/cockpit/*.md:" >&2
for item in "${broken[@]}"; do
  echo "  - ${item%%|*} -> ${item#*|}" >&2
done

if [ "$STRICT" -eq 1 ]; then
  exit 1
fi
echo "(advisory — not failing the build; run with --strict to enforce)" >&2
exit 0
