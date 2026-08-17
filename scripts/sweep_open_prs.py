#!/usr/bin/env python3
"""Scan open PRs on origin and classify their CI status.

Companion van kanban-kaart *"[problem] 14 dependabot + 2 human PRs 18 dagen
stale op CI spending-limit-block"* (`6fd60cb7d7024e28b514e0c6f6805be6`).
Een sessie ziet alleen iets aan een open PR-stapel als iemand expliciet
een "kijk naar PRs"-kaart opent; zonder wekelijkse triage stapelt de
billing-block-stapel zich op (zie CLAUDE.md §Gotchas, *spending-limit sinds
2026-07-26*). Dit script is het uitvoeringsuiteinde van die triage.

Classificatie van failure-mode (per PR, één label):

- ``pending_billing`` — Quality-check is PENDING/QUEUED langer dan een uur,
  OF de failure-log bevat een spending-limit-signaal (`"spending limit"`,
  `"hit your weekly limit"`, `"usage limit"`, `"billing"`). Herkenbaar
  verschijnsel: dependabot-PR die normaal schoon rebaset wordt rood, of
  een run die blijft hangen in QUEUED tot de billing-reset.
- ``real_failure`` — ten minste één check heeft ``conclusion=FAILURE`` met
  een failure-log die géén billing-signaal bevat. Vereist handmatige
  review: ``gh run view <run-id> --log-failed``.
- ``passing`` — alle checks geslaagd; PR is klaar om te mergen.
- ``no_checks`` — PR heeft nog geen status-checks gedraaid (vers geopend
  of checks niet geconfigureerd).

``stale_7d`` is ``True`` voor een PR die ≥ 7 dagen open staat én niet
``passing`` is — dat is de drempel uit de kaart-acceptance
(*"pile wordt binnen 7 dagen na CI-reset opgeruimd"*).

Output: altijd JSON op stdout (schema in module-docstring). Een
``--print`` vlag schrijft een tabel vóór het JSON, handig voor de
snelle menselijke inspectie die de triage-kaart vraagt. Exit-codes
spiegelen ``sweep_merged_remote_branches.py``:

- 0  schoon of (advisory + ≥1 hit)
- 1  --strict en ≥1 stale PR
- 2  usage-error, gh-call mislukt, of auth-probleem

Advisory by default — signaal, geen gate.

Usage:
    scripts/sweep_open_prs.py [--repo OWNER/REPO] [--stale-days N]
        [--no-fetch] [--strict] [--print] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


SCHEMA_VERSION = 1

# Deze repo — twee remotes, ``gh`` zonder ``-R`` valt terug op ``upstream``
# (de fork-bron) i.p.v. ``origin`` (waar de cockpit zelf landt). Zie
# CLAUDE.md §Gotchas, *"gh zonder expliciete repo leest de fork-upstream"*.
DEFAULT_REPO = "guillaumevandevelde/agent-cockpit"

# Field-set die we uit ``gh pr list`` nodig hebben. `author` voor de
# dependabot-vs-human splitsing, `createdAt` voor de leeftijd, de rest
# voor de failure-classificatie.
PR_LIST_FIELDS = "number,title,headRefName,author,statusCheckRollup,createdAt,updatedAt"

# Spending-limit-signaalwoorden die we in een run-log zoeken. Het
# dispatch.py-patroon `"hit your weekly limit"` (`backend/app/kanban/
# dispatch.py:3402`) is de canonieke frase; de overige komen uit GitHub
# Actions' eigen error-templates en Anthropic-console-terminologie.
SPENDING_LIMIT_PATTERNS = (
    r"spending limit",
    r"hit your weekly limit",
    r"usage limit",
    r"billing",
)

# Als een PR korter dan STALE_PENDING_GRACE open staat, mag een Quality-check
# nog PENDING/QUEUED zijn zonder dat het billing-block betekent — Quality
# kan nu eenmaal een paar minuten opstarten. Na deze grace gaat PENDING
# wel als billing-blok tellen, omdat afgewikkelde dependabot-PRs in deze
# repo doorgaans binnen 5 min hun eerste Quality-render hebben.
STALE_PENDING_GRACE = timedelta(hours=1)

# Maximale leeftijd van een PR om "vers" te zijn. Daarboven is "Quality is
# nog bezig" een steeds zwakker excuus; de kaart-acceptance noemt 7 dagen
# expliciet als drempel voor "stale", die standaard hanteren we hier.
DEFAULT_STALE_DAYS = 7


class GitHubCLIError(RuntimeError):
    """Een ``gh``-subcall faalde. Caller zet dit om in exit 2."""


def _run_gh(args: list[str], *, check: bool = True) -> str:
    """Run ``gh <args>`` and return stdout.

    Raises ``GitHubCLIError`` on non-zero exit (when ``check``). stderr is
    meegetrokken in de foutboodschap — bij authenticatie-fouten staat de
    reden daar (``gh: To get started with GitHub CLI…``).
    """
    cmd = ["gh", *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False,
        )
    except FileNotFoundError as e:
        raise GitHubCLIError(f"`gh` binary niet op PATH: {e}") from e
    if check and proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip() or "(geen output)"
        raise GitHubCLIError(
            f"gh {' '.join(args[:3])}… mislukt (exit {proc.returncode}): {msg}"
        )
    return proc.stdout


def _parse_iso8601_utc(ts: str) -> datetime:
    """GitHub's ``createdAt`` is ISO-8601 met Z-suffix; normaliseer naar aware UTC.

    Faalt op een onverwacht format met ``ValueError`` — caller vangt en
    zet de rij in ``error``.
    """
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts).astimezone(UTC)


def _age_days(created_at: datetime, *, now: datetime) -> int:
    """Leeftijd in hele dagen, afgekapt. ``now`` is een injected clock voor tests."""
    delta = now - created_at
    return max(0, delta.days)


def _is_dependabot(pr: dict) -> bool:
    """True als de auteur dependabot is — dat is de afzenderlijst die de kaart noemt."""
    author = pr.get("author") or {}
    login = (author.get("login") or "").lower()
    return login == "dependabot[bot]" or login.endswith("[bot]")


def _check_status_classification(
    checks: list[dict],
    *,
    pr_age: timedelta,
    run_log_cache: dict[str, str],
    run_log_fetcher,
) -> str:
    """Bepaal ``pending_billing`` | ``real_failure`` | ``passing`` | ``no_checks``.

    Eerst de goedkope heuristic (status/conclusion-waarden), pas bij twijfel
    de dure run-log-scan. De cache voorkomt dat één run-id meerdere keren
    wordt opgehaald als meerdere checks ernaar verwijzen (Quality heeft één
    matrix-run; de hele PR-classificatie deelt die).
    """
    if not checks:
        return "no_checks"

    completed_with_failure: list[dict] = []
    long_pending: list[dict] = []

    for c in checks:
        status = (c.get("status") or "").upper()
        conclusion = (c.get("conclusion") or "").upper()

        if status == "COMPLETED":
            if conclusion == "FAILURE":
                completed_with_failure.append(c)
            # SUCCESS / NEUTRAL / SKIPPED / CANCELLED → geen failure; verder
            # op zoek gaan naar pending. ACTION_REQUIRED telt hier niet als
            # failure maar telt óók niet als passing.
        else:
            # PENDING / QUEUED / IN_PROGRESS / EXPECTED / REQUESTED / WAITING.
            # Na STALE_PENDING_GRACE mag je aannemen dat het billing-blok is.
            if pr_age > STALE_PENDING_GRACE:
                long_pending.append(c)

    if not completed_with_failure and not long_pending:
        return "passing"

    # Failure-pad: haal de failure-log op en zoek naar billing-signalen.
    # Eén log-scan per uniek run-id, gedeeld door alle checks uit die run.
    if completed_with_failure:
        run_id = _extract_run_id(completed_with_failure[0])
        log = _cached_log(run_id, run_log_cache, run_log_fetcher)
        if _log_signals_billing(log):
            return "pending_billing"
        return "real_failure"

    # Long-pending zonder failure: per definitie billing-blok.
    if long_pending:
        return "pending_billing"

    return "passing"


def _extract_run_id(check: dict) -> str | None:
    """Pull the GH Actions run-id uit de detailsUrl van een check.

    Format is ``https://github.com/<owner>/<repo>/actions/runs/<id>``.
    We parseren de URL i.p.v. een extra ``gh run list``-call te doen om
    de classificatie snel en idempotent te houden.
    """
    url = check.get("detailsUrl") or ""
    m = re.search(r"/actions/runs/(\d+)", url)
    return m.group(1) if m else None


def _cached_log(run_id: str | None, cache: dict[str, str], fetcher) -> str:
    """Haal de failure-log via ``fetcher(run_id)`` met memoisation per run-id.

    ``fetcher`` is in productie ``_fetch_run_log`` (doet een ``gh run view``
    -call). Tests injecteren een fixture-fetcher die uit een dict leest.
    """
    if not run_id:
        return ""
    if run_id in cache:
        return cache[run_id]
    log = fetcher(run_id) or ""
    cache[run_id] = log
    return log


def _log_signals_billing(log: str) -> bool:
    """True als een failure-log een spending-limit-signal bevat.

    Lower-case vergelijking zodat we niet afhankelijk zijn van de exacte
    casing die GitHub/Anthropic kiest — *"hit your weekly limit"* in een
    dispatch.py-foutmelding komt in zowel camelCase als sentence-case
    voorbij.
    """
    if not log:
        return False
    low = log.lower()
    return any(re.search(pat, low) for pat in SPENDING_LIMIT_PATTERNS)


def _fetch_run_log(run_id: str) -> str:
    """``gh run view <id> --log-failed`` — eerste 64 KiB van de failure-log.

    De volledige log kan MiB's zijn; voor de billing-signal-scan is de
    failure-sectie genoeg. We cap'en op 64 KiB zodat een pathologische
    log de sweep niet vertraagt, en lezen alleen stderr als laatste
    poging — ``gh run view --log-failed`` schrijft naar stdout.
    """
    try:
        out = _run_gh(
            ["run", "view", run_id, "--log-failed"],
            check=False,
        )
    except GitHubCLIError:
        return ""
    # Cap op 64 KiB — genoeg voor een spending-limit-vestiging, klein
    # genoeg dat de sweep niet aan een log van 20 MB hangt.
    return out[: 64 * 1024]


def _list_open_prs(repo: str) -> list[dict]:
    """``gh pr list -R <repo> --state open --json <fields>`` als Python-lijst."""
    out = _run_gh(
        [
            "pr", "list",
            "-R", repo,
            "--state", "open",
            "--json", PR_LIST_FIELDS,
            "--limit", "200",
        ],
    )
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise GitHubCLIError(f"gh pr list gaf geen JSON: {e}; output was: {out[:200]!r}") from e


def _suggested_action(category: str, classification: str, age_days: int, *, stale: bool) -> str:
    """Concrete éénregelige actie die de operator kan copy-pasten.

    Doel: een dry-run op een werkdag moet de stale-pile kunnen sluiten
    zonder eerst de hele-uitvoer te ontleden. Voorbeelden:

    - ``@dependabot rebase`` op een dependabot-PR met billing-blok.
    - ``gh pr merge <num> --squash`` op een passing PR.
    - ``gh run view <id> --log-failed`` op een echte failure.
    """
    if classification == "passing":
        return f"gh pr merge {{{{number}}}} --squash --delete-branch  # {age_days}d oud, klaar"
    if classification == "pending_billing":
        if category == "dependabot":
            return f"@dependabot rebase  # billing-block, fresh-rebase pakt Quality-split mee"
        # Menselijke PR met billing: wachten op CI-reset, daarna check.
        return f"# wacht op CI-reset; daarna gh pr view {{{{number}}}} en opnieuw beoordelen"
    if classification == "real_failure":
        return "# echte failure — gh run view <run-id> --log-failed voor diagnose"
    if classification == "no_checks":
        return "# nog geen checks gedraaid — wachten of handmatig pingen"
    if stale:
        return "# stale (>7d, geen progress) — eskaleren naar eigenaar"
    return ""


def _summarize(prs: list[dict], now: datetime, stale_days: int) -> dict:
    """Pure functie: lijst van PR-dicts → triage-rapport.

    ``now`` is injected zodat tests deterministische leeftijden kunnen
    gebruiken zonder time.sleep of freezeghen van het hele process.
    """
    by_status: dict[str, int] = {}
    stale_count = 0
    rows: list[dict] = []

    run_log_cache: dict[str, str] = {}

    for pr in prs:
        created = _parse_iso8601_utc(pr["createdAt"])
        age = _age_days(created, now=now)
        category = "dependabot" if _is_dependabot(pr) else "human"
        checks = pr.get("statusCheckRollup") or []

        classification = _check_status_classification(
            checks,
            pr_age=now - created,
            run_log_cache=run_log_cache,
            run_log_fetcher=_fetch_run_log,
        )

        stale = (age >= stale_days) and (classification != "passing")

        by_status[classification] = by_status.get(classification, 0) + 1
        if stale:
            stale_count += 1

        # suggested_action wordt met ``{number}`` als placeholder
        # geschreven zodat we niet tweemaal formatteren; de JSON-rij
        # draagt ``number`` mee, de tabel-vorm substituet 'm inline.
        rows.append({
            "number": pr["number"],
            "title": pr["title"],
            "author": (pr.get("author") or {}).get("login"),
            "category": category,
            "head_ref": pr.get("headRefName"),
            "age_days": age,
            "status_classification": classification,
            "stale": stale,
            "created_at": pr["createdAt"],
            "updated_at": pr.get("updatedAt"),
            "suggested_action": _suggested_action(category, classification, age, stale=stale),
        })

    return {
        "totals": {
            "open_prs": len(prs),
            "by_status": by_status,
            "stale": stale_count,
            "stale_threshold_days": stale_days,
        },
        "rows": rows,
    }


def sweep(
    repo: str,
    *,
    stale_days: int,
    now: datetime,
) -> dict:
    """Run the sweep and return the report dict.

    Caller vangt ``GitHubCLIError`` (→ exit 2). De side-effect naar ``gh``
    staat in deze functie; tests injecteren een gefakete ``now`` zodat
    leeftijden voorspelbaar zijn en hoeven geen network te mockeren.
    """
    prs = _list_open_prs(repo)
    summary = _summarize(prs, now=now, stale_days=stale_days)
    return {
        "schema_version": SCHEMA_VERSION,
        "scanned_at": now.isoformat(timespec="seconds"),
        "repo": repo,
        "totals": summary["totals"],
        "rows": summary["rows"],
    }


def _print_table(report: dict) -> None:
    """Mens-leesbare tabel naar stderr — laat JSON ongestoord op stdout.

    Breedte is conservatief (78 kol) zodat de tabel in een standaard
    terminal past zonder wrap. Kolommen: number, age, category,
    classification, suggested (afgekapt).
    """
    rows = report["rows"]
    print(f"\n# Open PRs op {report['repo']} ({report['totals']['open_prs']} totaal, "
          f"{report['totals']['stale']} stale ≥{report['totals']['stale_threshold_days']}d)",
          file=sys.stderr)
    if not rows:
        print("# (geen open PRs)\n", file=sys.stderr)
        return
    print(f"{'#':>4}  {'age':>3}  {'cat':<10}  {'class':<16}  {'title':<35}  actie",
          file=sys.stderr)
    for r in rows:
        title = (r["title"] or "")[:35]
        action = r["suggested_action"].replace("{number}", str(r["number"]))
        print(
            f"{r['number']:>4}  {r['age_days']:>3}d  {r['category']:<10}  "
            f"{r['status_classification']:<16}  {title:<35}  {action}",
            file=sys.stderr,
        )
    print(file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sweep_open_prs.py",
        description=(
            "Scan open PRs op <repo>, classificeer CI-status als "
            "pending_billing/real_failure/passing/no_checks, en flag PRs "
            "ouder dan <stale-days> dagen die niet passing zijn. "
            "Emitteert JSON op stdout; --print schrijft een tabel naar "
            "stderr."
        ),
    )
    p.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=(
            f"OWNER/REPO om te scannen. Default {DEFAULT_REPO!r}. Twee "
            "remotes op deze box — geef altijd de canonieke vorm, anders "
            "leest gh de fork-upstream (zie CLAUDE.md)."
        ),
    )
    p.add_argument(
        "--stale-days",
        type=int,
        default=DEFAULT_STALE_DAYS,
        metavar="N",
        help=(
            f"Leeftijds-drempel voor de stale-vlag. Default {DEFAULT_STALE_DAYS} "
            "(komt uit de kaart-acceptance). 0 = alles wat niet passing is."
        ),
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 zodra ≥1 stale PR gevonden is. Default advisory (exit 0).",
    )
    p.add_argument(
        "--print",
        action="store_true",
        help="Schrijf een mens-leesbare tabel naar stderr (JSON blijft op stdout).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON op stdout (default; flag bestaat voor pipeline-helderheid).",
    )
    p.add_argument(
        "--now",
        default=None,
        help=(
            "Override de interne klok met ISO-8601 UTC — handig voor "
            "reproduceerbare test-runs. Default: ``datetime.now(UTC)``."
        ),
    )
    return p


def _resolve_now(arg: str | None) -> datetime:
    if not arg:
        return datetime.now(UTC)
    ts = arg
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts).astimezone(UTC)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        now = _resolve_now(args.now)
        report = sweep(
            args.repo,
            stale_days=args.stale_days,
            now=now,
        )
    except GitHubCLIError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if args.print:
        _print_table(report)

    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")

    if args.strict and report["totals"]["stale"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())