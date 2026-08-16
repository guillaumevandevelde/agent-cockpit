#!/usr/bin/env bash
# Geen belofte zonder rij: `_sched.add_job` mag maar op een paar plekken staan.
#
# De scheduler draait op APScheduler's in-memory jobstore, dus elke job sterft
# met het proces. Voor een terugkerende job is dat onschuldig — de volgende
# start installeert hem opnieuw. Voor een EENMALIGE job niet: die vuurt daarna
# gewoon nooit meer. Daarom is de regel dat de database de waarheid is en de
# scheduler een cache, en dat elke belofte een rij heeft die een reconciler bij
# het opstarten terugleest.
#
# Deze poort bewaakt de vorm, niet de inhoud: hij dwingt af dat nieuwe
# add_job-aanroepen niet ongemerkt op willekeurige plekken ontstaan. Wie er een
# toevoegt moet hier langs, en wordt zo gedwongen de vraag te beantwoorden
# "welke rij overleeft mijn herstart?".
#
# De uitzonderingslijst is een RATEL: alleen korter. Elke regel is een
# eenmalige job die zijn rij inmiddels heeft (pane_resume_pending) of nog niet
# (auto_resume) — zie docs/cockpit/architectuur.md regel 4.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

# Toegestaan: de scheduler zelf definieert en plant zijn eigen terugkerende jobs.
ALLOWED_RE='^backend/app/services/scheduling/scheduler\.py$'

# Ratel, vastgesteld 2026-08-15. Alleen korter maken.
# 2026-08-16: het pane-resume-cluster verhuisde uit kanban/dispatch.py naar
# kanban/pane_resume.py (kaart de820d8a…) — dezelfde ene aanroep, ander pad.
RATCHET_RE='^backend/app/(kanban/pane_resume\.py|services/scheduling/auto_resume\.py)$'

mapfile -t HITS < <(grep -rln '_sched\.add_job' backend/app --include='*.py' 2>/dev/null | sort)

unexpected=()
ratcheted=0
for f in "${HITS[@]}"; do
    if [[ "$f" =~ $ALLOWED_RE ]]; then
        continue
    elif [[ "$f" =~ $RATCHET_RE ]]; then
        ratcheted=$((ratcheted + 1))
    else
        unexpected+=("$f")
    fi
done

if [ "${#unexpected[@]}" -gt 0 ]; then
    echo "NIEUWE add_job-aanroep buiten de toegestane plekken:" >&2
    printf '  %s\n' "${unexpected[@]}" >&2
    echo "" >&2
    echo "Een eenmalige job overleeft geen herstart. Leg de belofte vast als rij en" >&2
    echo "laat services/scheduling/reconciler.py hem bij het opstarten terugbouwen." >&2
    echo "Is dit een terugkerende job, zet hem dan in scheduler.py." >&2
    exit 1
fi

echo "OK: geen add_job-aanroep buiten de toegestane plekken ($ratcheted in de ratel)."
exit 0
