#!/usr/bin/env bash
#
# check-doc-links.sh — advisory presence check for relative Markdown links.
#
# Resolves inline Markdown-link targets in *.md files from the directory
# of the source document. URL fragments are stripped before checking whether
# the target exists. Pure anchors, absolute paths, and URI schemes are ignored.
# Links inside fenced or inline code are not Markdown links and are skipped.
#
# Default scan scope (each can be redirected via env for tests / overrides):
#   - docs/cockpit/*.md             (top-level cockpit docs; DOCS_DIR)
#   - .claude/skills/**/*.md        (skill Markdown; CLAUDE_SKILLS_DIR)
#   - .claude/agents/*.md           (subagent prompts; CLAUDE_AGENTS_DIR)
#
# Persona and skill files live several levels deeper than docs/cockpit/, so
# the link target is resolved relative to the source file's directory rather
# than to a single fixed scope root.
#
# Advisory by design: exits 0 when broken links are found and prints a
# warning. Pass --strict to exit 1 on drift.
#
# Usage:
#   scripts/check-doc-links.sh [--strict]
#
# Env:
#   DOCS_DIR          docs/cockpit scope root (default: <repo>/docs/cockpit)
#   CLAUDE_SKILLS_DIR .claude/skills scope root (default: <repo>/.claude/skills)
#   CLAUDE_AGENTS_DIR .claude/agents scope root (default: <repo>/.claude/agents)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DOCS_DIR="${DOCS_DIR:-$REPO_ROOT/docs/cockpit}"
CLAUDE_SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$REPO_ROOT/.claude/skills}"
CLAUDE_AGENTS_DIR="${CLAUDE_AGENTS_DIR:-$REPO_ROOT/.claude/agents}"

STRICT=0
for arg in "$@"; do
  case "$arg" in
    --strict) STRICT=1 ;;
    --help|-h)
      sed -n '3,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    "") ;;
    *)
      echo "ERROR: unknown argument '$arg' (see --help)" >&2
      exit 2
      ;;
  esac
done

# Each scope is a (path, maxdepth) pair. Missing dirs are silently skipped
# — the default CLAUDE_* paths may not exist in test fixtures, and a fresh
# checkout without a .claude/ tree would still want the docs/cockpit scope
# to fire.
scopes=()
[ -d "$DOCS_DIR" ] && scopes+=("$DOCS_DIR|1")
[ -d "$CLAUDE_SKILLS_DIR" ] && scopes+=("$CLAUDE_SKILLS_DIR|99")
[ -d "$CLAUDE_AGENTS_DIR" ] && scopes+=("$CLAUDE_AGENTS_DIR|1")

if [ "${#scopes[@]}" -eq 0 ]; then
  echo "ERROR: no docs/scopes directory found (DOCS_DIR=$DOCS_DIR)" >&2
  echo "Point DOCS_DIR at a directory that holds *.md docs." >&2
  exit 2
fi

# Collect all source files across scopes, sorted, NUL-separated.
source_files_args=()
for scope_line in "${scopes[@]}"; do
  scope_path="${scope_line%|*}"
  scope_depth="${scope_line##*|}"
  while IFS= read -r -d '' f; do
    source_files_args+=("$f")
  done < <(find "$scope_path" -maxdepth "$scope_depth" -type f -name '*.md' -print0 2>/dev/null | sort -z)
done

if [ "${#source_files_args[@]}" -eq 0 ]; then
  echo "OK: no *.md files found in any configured scope." >&2
  exit 0
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
  # Resolve relative to the source file's directory, not the docs root.
  # Skill and agent files live several levels deep with `../../docs/cockpit/...`
  # links, so a fixed resolution base would mis-resolve every one of them.
  source_dir="$(dirname "$file")"
  if [ ! -e "$source_dir/$path" ]; then
    # Display relative to the repo root so the warning path is consistent
    # across all three scopes (docs/cockpit/, .claude/skills/, .claude/agents/).
    rel="${file#"$REPO_ROOT"/}"
    broken+=("$rel:$line|$target")
  fi
done < <(
  printf '%s\0' "${source_files_args[@]}" \
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
  echo "OK: every relative Markdown link in the configured scope points to an existing target."
  exit 0
fi

echo "WARNING: ${#broken[@]} broken relative Markdown link(s):" >&2
for item in "${broken[@]}"; do
  echo "  - ${item%%|*} -> ${item#*|}" >&2
done

if [ "$STRICT" -eq 1 ]; then
  exit 1
fi
echo "(advisory — not failing the build; run with --strict to enforce)" >&2
exit 0
