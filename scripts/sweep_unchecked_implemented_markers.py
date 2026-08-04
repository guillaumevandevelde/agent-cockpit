#!/usr/bin/env python3
"""Sweep `✅ Geïmplementeerd`-markers in `docs/cockpit/*.md` zonder effect-bewijs.

Vangnet voor de conventie uit
[`docs/cockpit/recipe-writing-conventions.md` §2](../docs/cockpit/recipe-writing-conventions.md)
(kaart `21a349bc…`): een `✅ Geïmplementeerd`-regel in een analysedoc is pas
geldig als ernaast een waargenomen effect staat — een logmarker-telling, een
gemeten gedragsverandering, of het expliciete label "nog niet in productie
waargenomen" met reden. Alleen "code gemerged" of "kaart afgerond" is geen
effect-bewijs.

Het script detecteert uitsluitend de **structurele afwezigheid** van een
effect-claim — een `Effect:`-zin (of equivalent label zoals "nog niet in
productie waargenomen") binnen de eerstvolgende ~3 regels onder de marker. De
**inhoud** van de effect-claim blijft een menselijke beoordeling; net zoals
`check-doc-readability.py` letterlengtes meet maar geen stijl beoordeelt.

Output: één JSON-document op stdout (altijd — geen human-readable vorm) zodat
de aanroeper kan `jq`en, vergelijken tegen een baseline, of als bijlage aan
een follow-up-kaart hangen. Schema:

    {
      "schema_version": 1,
      "scanned_at": "<ISO-8601 UTC>",
      "docs_dir": "<abs path>",
      "totals": {
        "markers_total": <int>,
        "markers_with_effect": <int>,
        "markers_without_effect": <int>
      },
      "rows": [
        {
          "file": "<repo-relatief pad>",
          "line": <int>,
          "snippet": "<regel met marker, afgekapt>",
          "marker_kind": "geimplementeerd" | "uitgevoerd"
        }
      ]
    }

Gezonde markers (wel effect-bewijs binnen 3 regels) worden stil
weggelaten. Een marker zonder effect is **geen automatische bug** — het is
dezelfde klasse als "git log zonder commit-message": structurele
afwezigheid van een verplicht veld, geen kwaliteitsoordeel over de
inhoud.

Exit codes:

    0  schoon OF (advisory-modus en ≥1 hit)
    1  --strict en ≥1 hit
    2  usage-fout, docs-dir ontbreekt, of leesfout

Advisory by default — spiegelt `scripts/sweep_dangling_depends_on.py`'s
houding ("signal, not gate"). --strict is voor CI/pre-commit: een
docs-cleanup pipeline zou op een non-zero count moeten blokkeren in
plaats van verse ongeverifieerde markers toe te laten.

Usage:
    scripts/sweep_unchecked_implemented_markers.py [--docs-dir PATH] [--strict] [--help]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path


SCHEMA_VERSION = 1

DEFAULT_DOCS_DIR = "docs/cockpit"

# Markers die we herkennen als "code gemerged"-bewering. Houd deze lijst
# bewust smal: een breder patroon (bv. "✅" alleen) zou ruis opleveren van
# docs die de emoji voor andere doeleinden gebruiken.
# Het pattern accepteert een optionele `**` (bold) en andere whitespace
# tussen ✅ en het marker-woord — `✅ **Geïmplementeerd**` is een
# canonieke vorm die zonder deze permissie niet matcht.
MARKER_PATTERNS = (
    re.compile(r"✅\s*\*?\*?\s*Geïmplementeerd\b"),
    re.compile(r"✅\s*\*?\*?\s*Uitgevoerd\b"),
)

# Een echte implementatie-claim hoort altijd bij een kanban-kaart. Een
# marker-regel zonder `kaart`-referentie is descriptieve tekst (heading,
# blockquote-meta, of uitleg), niet een claim. Hiermee filteren we
# "✅ Geïmplementeerd" in section-titles weg zonder een fragiele
# heading-detector te bouwen.
CARD_REF_PATTERN = re.compile(r"\bkaart\b", re.IGNORECASE)

# Effect-claim indicatoren. Een marker is "voorzien" als binnen
# EFFECT_WINDOW_REGELS één van deze patronen op een niet-lege regel
# matcht. Het venster is bewust klein (3 regels) zodat een effect-claim
# dieper in de alinea niet "per ongeluk" matcht — de conventie vereist
# een direct-aangrenzende claim.
EFFECT_PATTERNS = (
    re.compile(r"\bEffect\s*:", re.IGNORECASE),
    re.compile(r"\bnog niet in productie waargenomen\b", re.IGNORECASE),
    re.compile(r"\bnog niet meetbaar\b", re.IGNORECASE),
    re.compile(r"\bnog niet gevuurd\b", re.IGNORECASE),
    re.compile(r"\bnog niet waargenomen\b", re.IGNORECASE),
    re.compile(r"\bgeen effect-bewijs\b", re.IGNORECASE),
    re.compile(r"\b0\s+logregels\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+logregels?\b", re.IGNORECASE),
    re.compile(r"\bgeen\s+[a-z\-]+-logregels\b", re.IGNORECASE),
)

# Aangrenzende regels waarbinnen we een effect-claim zoeken. We stoppen
# bij de eerstvolgende blanco regel OF bij `EFFECT_WINDOW_MAX`-regels,
# wat het eerst komt. Een blanco-regel-stopp is semantischer dan een
# vast aantal regels: een Effect-claim hoort in dezelfde alinea als
# de marker, niet in een footnote verderop. De MAX is een safety-net
# tegen run-away scenarios (een marker-regel zonder enige alinea-break).
EFFECT_WINDOW_MAX = 12

# Maximale snippet-lengte voor de output. 200 tekens is ruim genoeg om
# de marker + kaart-id te zien, klein genoeg om de JSON leesbaar te
# houden (analoog aan de 200-char logline-cap in dispatch.py).
SNIPPET_MAX_CHARS = 200


def _resolve_docs_dir(cli_arg: str | None) -> Path:
    """Resolve the docs dir: CLI arg > $DOCS_DIR > default."""
    if cli_arg:
        return Path(cli_arg).expanduser().resolve()
    env_value = __import__("os").environ.get("DOCS_DIR")
    if env_value:
        return Path(env_value).expanduser().resolve()
    return Path(DEFAULT_DOCS_DIR).expanduser().resolve()


def _classify_marker(line: str) -> str | None:
    """Return the marker kind found on this line, or None if no match.

    Returns one of ``"geimplementeerd"`` / ``"uitgevoerd"`` so the caller
    can group findings and the human reader can see which canonical form
    each row belongs to. Stripped of diacritics on the kind label so the
    JSON stays ASCII-safe.

    Twee klassen van vals-positieven worden uitgefilterd:

    1. **Backtick-wrapped markers** — `` `✅ Geïmplementeerd` `` is een
       mention in prose, niet een implementatie-claim. De conventie zelf
       zit vol van dit patroon (uitleg van de marker-vorm), en zonder
       filter zou élke analysedoc die de marker documenteert zichzelf
       als overtreding melden.
    2. **Fenced code blocks** — de worked-example in deze conventie én
       in andere analysedocs toont de marker-vorm in een ```` ``` ````
       blok. Een hit daar is een voorbeeld, geen echte claim.
    """
    for pattern in MARKER_PATTERNS:
        match = pattern.search(line)
        if match is None:
            continue
        # Skip backtick-wrapped: \"`✅ Geïmplementeerd`\"\" — the marker
        # sits inside a backtick span, which is the canonical way to
        # mention the marker in prose without making a claim.
        if _surrounded_by_backticks(line, match.start(), match.end()):
            continue
        # Skip markers without a kaart reference: pure prose mentions
        # in headings, blockquote-meta, or section-title explainers.
        if not CARD_REF_PATTERN.search(line):
            continue
        if "Geïmplementeerd" in pattern.pattern:
            return "geimplementeerd"
        return "uitgevoerd"
    return None


_BACKTICK_BOUNDARY = re.compile(r"`")


def _surrounded_by_backticks(line: str, start: int, end: int) -> bool:
    """Return True iff ``line[start:end]`` is wrapped in backticks.

    Een marker is "backtick-wrapped" als er **direct** een backtick
    voor en **direct** een backtick na staat (eventueel met whitespace).
    Dat is de canonieke mention-vorm: `` `✅ Geïmplementeerd`-regel ``.
    Een regel die de marker aan het begin heeft en verderop in de
    zin een code-span bevat (`` `check_progress_liveness` ``) is
    **geen** mention — het is een echte implementatie-claim met een
    code-referentie.
    """
    # Direct preceding character (skipping whitespace)
    j = start - 1
    while j >= 0 and line[j] in " \t":
        j -= 1
    tick_before = j >= 0 and line[j] == "`"

    # Direct following character (skipping whitespace)
    k = end
    while k < len(line) and line[k] in " \t":
        k += 1
    tick_after = k < len(line) and line[k] == "`"

    return tick_before and tick_after


def _is_in_fenced_code_block(lines: list[str], line_idx: int) -> bool:
    """Return True iff ``line_idx`` sits inside a fenced code block.

    Traceert zowel ```` ```\n ```` als ```` ~~~ ```` openers, op de
    canonieke manier: oneven aantal openers vóór `line_idx` = binnen
    een block. Inspringing-gevoelige 4-space blocks worden niet
    gedetecteerd — analysdocs gebruiken vrijwel uitsluitend fenced
    blocks voor voorbeelden.
    """
    in_block = False
    for j in range(line_idx):
        stripped = lines[j].lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_block = not in_block
    return in_block


def _has_effect_claim(lines: list[str], marker_line_idx: int) -> bool:
    """Return True iff an effect-claim pattern matches within the window.

    Venster = "dezelfde alinea" onder de marker: doorlopende niet-lege
    regels, tot de eerste blanco regel of `EFFECT_WINDOW_MAX`-regels,
    wat eerst komt. Een effect-claim in een footnote of op een
    volgende alinea telt NIET — de conventie vereist een direct-
    aangrenzende claim, niet een verwijzing verderop in het doc.
    """
    for j in range(marker_line_idx + 1, min(
        marker_line_idx + 1 + EFFECT_WINDOW_MAX, len(lines),
    )):
        candidate = lines[j].strip()
        if not candidate:
            return False
        for pattern in EFFECT_PATTERNS:
            if pattern.search(candidate):
                return True
    return False


def _iter_md_files(docs_dir: Path) -> list[Path]:
    """Return sorted list of *.md files under ``docs_dir`` (non-recursive).

    Het script is bewust niet-recursief: `docs/cockpit/measure-evidence/`
    en `docs/cockpit/po-digest/` zijn data-dirs, geen analysedocs, en
    zouden ruis opleveren. Een analysedoc met submappen kan dit
    overschrijven door de CLI --docs-dir te wijzigen.
    """
    if not docs_dir.exists() or not docs_dir.is_dir():
        return []
    return sorted(p for p in docs_dir.iterdir() if p.is_file() and p.suffix == ".md")


def sweep(docs_dir: Path) -> dict:
    """Run the sweep against ``docs_dir`` and return the report dict.

    Raises on directory missing (caller turns that into exit-2). File
    leesfouten worden genegeerd met een `None`-teruggave in de rows —
    dezelfde fail-open houding als sweep_dangling_depends_on voor
    permissie-issues op individuele rijen.
    """
    files = _iter_md_files(docs_dir)
    rows: list[dict] = []
    markers_total = 0
    markers_with_effect = 0

    for path in files:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = content.splitlines()
        for idx, line in enumerate(lines):
            if _is_in_fenced_code_block(lines, idx):
                continue
            kind = _classify_marker(line)
            if kind is None:
                continue
            markers_total += 1
            if _has_effect_claim(lines, idx):
                markers_with_effect += 1
                continue
            snippet = line.strip()
            if len(snippet) > SNIPPET_MAX_CHARS:
                snippet = snippet[:SNIPPET_MAX_CHARS] + "…"
            rows.append({
                "file": _to_repo_relative(path),
                "line": idx + 1,
                "snippet": snippet,
                "marker_kind": kind,
            })

    return {
        "schema_version": SCHEMA_VERSION,
        "scanned_at": datetime.now(UTC).isoformat(),
        "docs_dir": str(docs_dir),
        "totals": {
            "markers_total": markers_total,
            "markers_with_effect": markers_with_effect,
            "markers_without_effect": len(rows),
        },
        "rows": rows,
    }


def _to_repo_relative(path: Path) -> str:
    """Probeer pad relatief aan de werkboom-root te tonen.

    Faalt zacht op een buiten-de-repo pad (gewoon absolute path) — de
    finder is voor menselijke lezers, niet voor verdere verwerking.
    """
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sweep unmarked ✅ Geïmplementeerd-regels in analysedocs.",
    )
    parser.add_argument(
        "--docs-dir",
        default=None,
        help="directory holding analysedocs (default: docs/cockpit)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 wanneer ≥1 marker zonder effect-bewijs",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    docs_dir = _resolve_docs_dir(args.docs_dir)
    if not docs_dir.exists() or not docs_dir.is_dir():
        print(
            f"ERROR: docs-dir niet gevonden: {docs_dir}",
            file=sys.stderr,
        )
        return 2
    report = sweep(docs_dir)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    missing = report["totals"]["markers_without_effect"]
    if missing == 0:
        return 0
    if args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
