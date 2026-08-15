#!/usr/bin/env bash
# Omvangsratel: een bestand dat al boven de drempel zit mag niet groeien.
#
# Waarom dit bestaat: geen enkele importregel had `dispatch.py` op 10.110 regels
# voorkomen — alle imports daarin zijn keurig. Wat ontbrak was een grens op
# GROEI. Elke afzonderlijke toevoeging was verdedigbaar; niemand bewaakte de som.
#
# Bewaakt worden alleen bestanden BOVEN de drempel (default 800 regels). Een
# klein bestand mag vrij groeien tot het die grens raakt; vanaf dat moment
# staat het in `.file-size-baseline` en kan het alleen nog krimpen. Zakt het
# weer onder de drempel, dan valt het uit de baseline en is het weer vrij.
#
# Gebruik:
#   scripts/check-file-size-ratchet.sh            # controleren (exit 1 bij groei)
#   scripts/check-file-size-ratchet.sh --update   # baseline bijwerken na krimp
#
# `--update` is nadrukkelijk GEEN achterdeur: groei wordt ook daar geweigerd en
# de oude waarde blijft staan. De vlag dient om krimp vast te leggen en om een
# nieuw bestand boven de drempel voor het eerst te registreren.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# FILE_SIZE_BASELINE laat het testharnas een eigen baseline gebruiken zonder
# de echte aan te raken.
BASELINE="${FILE_SIZE_BASELINE:-$REPO_ROOT/.file-size-baseline}"
THRESHOLD="${FILE_SIZE_THRESHOLD:-800}"
MODE="check"
[ "${1:-}" = "--update" ] && MODE="update"

cd "$REPO_ROOT" || exit 1

mapfile -t FILES < <(git ls-files -- 'backend/app/*.py' 'frontend/src/*.ts' 'frontend/src/*.tsx' | sort)

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

for f in "${FILES[@]}"; do
    [ -f "$f" ] || continue
    n=$(grep -c '' "$f")
    prev="${BASE[$f]:-}"

    if [ -n "$prev" ]; then
        guarded=$((guarded + 1))
        if [ "$n" -gt "$prev" ]; then
            echo "GROEI: $f is $n regels, baseline $prev (+$((n - prev)))" >&2
            fail=1
            NEW_LINES+=("$prev $f")          # groei wordt nooit vastgelegd
        else
            [ "$n" -lt "$prev" ] && shrunk=$((shrunk + 1))
            # Onder de drempel gezakt: niet langer bewaken.
            [ "$n" -gt "$THRESHOLD" ] && NEW_LINES+=("$n $f")
        fi
    elif [ "$n" -gt "$THRESHOLD" ]; then
        if [ "$MODE" = "update" ]; then
            NEW_LINES+=("$n $f")
            guarded=$((guarded + 1))
        else
            echo "NIEUW BOVEN DREMPEL: $f is $n regels (drempel $THRESHOLD)" >&2
            echo "  Splits het, of leg de omvang vast met --update als dit bewust is." >&2
            fail=1
        fi
    fi
done

if [ "$MODE" = "update" ]; then
    {
        echo "# Omvangsratel-baseline — zie scripts/check-file-size-ratchet.sh"
        echo "# Regels: '<regels> <pad>'. Alleen omlaag; groei wordt hier nooit vastgelegd."
        [ "${#NEW_LINES[@]}" -gt 0 ] && printf '%s\n' "${NEW_LINES[@]}" | sort -k2
    } > "$BASELINE"
    if [ "$fail" -ne 0 ]; then
        echo "WAARSCHUWING: baseline bijgewerkt, maar de gemelde groei is NIET vastgelegd." >&2
        exit 1
    fi
    echo "OK: baseline bijgewerkt ($guarded bewaakte bestanden, $shrunk gekrompen)."
    exit 0
fi

if [ "$fail" -ne 0 ]; then
    echo "" >&2
    echo "Een bewaakt bestand mag niet groeien. Haal er iets uit, of splits het." >&2
    exit 1
fi

echo "OK: geen enkel bewaakt bestand is gegroeid ($guarded bewaakt, $shrunk gekrompen)."
exit 0
