#!/usr/bin/env bash
# Inherited-bucket-ratel: de som-regels over de 19 geërfde Claude Code-beheerschermen
# mag niet groeien. Analoog aan scripts/check-file-size-ratchet.sh (per bestand) maar dan
# op map-niveau tegen de frontend-omvang.
#
# Het "geërfde" bucket staat in cockpit-richting-decision.md §3: 19 features die de
# essentie van Cockpit niet dienen en krimpen volgens §6 van dat doc — met regels 1
# (raakt nieuw werk een geërfd scherm? verwijderen) en 2 (één erin, één eruit) als
# zelfvurende regels. Regel 3 wordt afgedwongen door de omvangsratel uit
# kernharding-design.md §3; dit script is de bucket-tegenhanger daarvan.
#
# Per git-tracked *.ts/*.tsx in de geconfigureerde mappen wordt de regelsom gemeten.
# Een baseline-bestand legt per map de huidige som vast. Groei weigert; krimp mag,
# en wordt met `--update` vastgelegd. Een map met 0 regels blijft in de baseline
# staan — als ze later terugkomt, faalt dat als GROEI boven 0, niet als een
# nieuwe map.
#
# Gebruik:
#   scripts/check-inherited-bucket-ratchet.sh            # controleren (exit 1 bij groei)
#   scripts/check-inherited-bucket-ratchet.sh --update   # baseline bijwerken na krimp
#
# Env:
#   INHERITED_BUCKET_BASELINE  alternatieve pad voor de baseline (testharnas-default).
#
# `--update` is nadrukkelijk GEEN achterdeur: groei wordt ook daar geweigerd en de oude
# waarde blijft staan. De vlag dient om krimp vast te leggen of een nieuwe map voor het
# eerst te registreren.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASELINE="${INHERITED_BUCKET_BASELINE:-$REPO_ROOT/.inherited-bucket-baseline}"
MODE="check"
[ "${1:-}" = "--update" ] && MODE="update"

# Mirror van cockpit-richting-decision.md §3 — uitbreiden of inkrimpen alleen via dat doc.
INHERITED_DIRS=(
  commands
  hooks
  permissions
  plugins
  mcp
  mcp-server
  output-styles
  statusline
  skills
  memory
  config
  updates
  security
  endpoints
  subscriptions
  usage
  context
  backup
  blueprints
)

cd "$REPO_ROOT" || exit 1

declare -A BASE=()
if [ -f "$BASELINE" ]; then
    while read -r size path; do
        [ -n "${path:-}" ] && BASE["$path"]="$size"
    done < <(grep -vE '^\s*(#|$)' "$BASELINE")
fi

fail=0
shrunk=0
guarded=0
declare -a NEW_LINES=()

for d in "${INHERITED_DIRS[@]}"; do
    # Alleen git-tracked *.ts/*.tsx — komt overeen met check-file-size-ratchet.sh.
    mapfile -t FILES < <(git ls-files -- "frontend/src/features/$d/" 2>/dev/null \
        | grep -E '\.(ts|tsx)$' | sort)
    n=0
    for f in "${FILES[@]}"; do
        [ -f "$f" ] || continue
        n=$((n + $(grep -c '' "$f")))
    done
    prev="${BASE[$d]:-}"

    if [ -n "$prev" ]; then
        guarded=$((guarded + 1))
        if [ "$n" -gt "$prev" ]; then
            echo "GROEI: frontend/src/features/$d is $n regels, baseline $prev (+$((n - prev)))" >&2
            fail=1
            NEW_LINES+=("$prev $d")          # groei wordt nooit vastgelegd
        else
            [ "$n" -lt "$prev" ] && shrunk=$((shrunk + 1))
            NEW_LINES+=("$n $d")
        fi
    else
        # Eerste kennismaking — komt overeen met check-file-size-ratchet.sh: registreer nu.
        if [ "$MODE" = "update" ]; then
            NEW_LINES+=("$n $d")
            guarded=$((guarded + 1))
        else
            echo "NIEUW IN BUCKET: frontend/src/features/$d is $n regels zonder baseline-regel" >&2
            echo "  Leg de omvang vast met --update als dit bewust is." >&2
            fail=1
        fi
    fi
done

if [ "$MODE" = "update" ]; then
    {
        echo "# Inherited-bucket-baseline — zie scripts/check-inherited-bucket-ratchet.sh"
        echo "# Regels: '<regels> <map>'. Alleen omlaag; groei wordt hier nooit vastgelegd."
        [ "${#NEW_LINES[@]}" -gt 0 ] && printf '%s\n' "${NEW_LINES[@]}" | sort -k2
    } > "$BASELINE"
    if [ "$fail" -ne 0 ]; then
        echo "WAARSCHUWING: baseline bijgewerkt, maar de gemelde groei is NIET vastgelegd." >&2
        exit 1
    fi
    echo "OK: baseline bijgewerkt ($guarded bewaakte mappen, $shrunk gekrompen)."
    exit 0
fi

if [ "$fail" -ne 0 ]; then
    echo "" >&2
    echo "Een bewaakte geërfde map mag niet groeien. Haal er iets uit, of splits het." >&2
    exit 1
fi

echo "OK: geen enkele bewaakte geërfde map is gegroeid ($guarded bewaakt, $shrunk gekrompen)."
exit 0
