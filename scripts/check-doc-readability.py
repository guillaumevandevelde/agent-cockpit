#!/usr/bin/env python3
"""check-doc-readability.py — meet de leesbaarheidsnormen uit taalgebruik-conventies.md.

Dit script is het meetinstrument achter `docs/cockpit/taalgebruik-conventies.md`.
Het meet vier dingen per document en rapporteert alleen wat de norm overschrijdt:

  1. lange zin        — een zin van meer dan MAX_SENTENCE_WORDS woorden (norm: 40)
  2. lange alinea     — een alinea van meer dan MAX_PARAGRAPH_WORDS woorden (norm: 150)
  3. hybride werkwoord — een Engels werkwoord met Nederlandse vervoeging waarvoor
                         een gewoon Nederlands woord bestaat (`globt`, `flag't`, …).
                         Vakjargon dat in de woordenlijst staat (dispatchen,
                         claimen, mergen, shippen, spawnen, …) is géén hit.
  4. leesindex        — Flesch-Douma, de Nederlandse variant van Flesch Reading
                         Ease. Informatief per document, geen overtreding.

Wat niet wordt gemeten: code in fences en inline code, frontmatter, tabelrijen
(dat is data, geen proza), en losse linkregels.

Advisory by design: exit 0 bij hits, met een waarschuwing. `--strict` maakt er
exit 1 van. Zo kan een schoon rapport een gate worden zonder dat de bestaande
voorraad elke build rood maakt.

Gebruik:
  scripts/check-doc-readability.py                    # samenvatting over docs/cockpit
  scripts/check-doc-readability.py --strict           # exit 1 bij hits
  scripts/check-doc-readability.py --json             # machineleesbaar
  scripts/check-doc-readability.py --top 10           # 10 slechtste documenten
  scripts/check-doc-readability.py --file CLAUDE.md   # één bestand, met regelnummers
  scripts/check-doc-readability.py --path .claude/skills --recursive

Env:
  DOCS_DIR   map met *.md-documenten (default: <repo>/docs/cockpit)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- normen ----------------------------------------------------------------
MAX_SENTENCE_WORDS = 40
MAX_PARAGRAPH_WORDS = 150
MIN_FLESCH_DOUMA = 30.0  # informatief: onder ~30 leest een tekst als vakliteratuur

# Engelse werkwoorden met Nederlandse vervoeging waarvoor een gewoon Nederlands
# woord bestaat. Alleen deze lijst is een overtreding — projectjargon
# (dispatchen, claimen, mergen, shippen, spawnen, …) hoort bij het domein en
# staat in docs/cockpit/terminology.md.
HYBRID_VERBS: dict[str, str] = {
    "globt": "matcht als glob-patroon",
    "globben": "als glob-patroon uitbreiden",
    "sweept": "veegt / loopt langs",
    "sweepen": "langslopen",
    "flag't": "signaleert / markeert",
    "flagt": "signaleert / markeert",
    "flaggen": "signaleren",
    "overridet": "overschrijft",
    "overriden": "overschrijven",
    "clobbert": "overschrijft",
    "clobberen": "overschrijven",
    "reapt": "ruimt op",
    "reapen": "opruimen",
    "grept": "zoekt (met grep)",
    "grep't": "zoekt (met grep)",
    "deleten": "verwijderen",
    "demoten": "degraderen",
    "deprecaten": "uitfaseren",
    "bumpt": "verhoogt",
    "stallen": "blijven hangen",
    "stalt": "blijft hangen",
    "driftte": "liep uiteen",
    "drift't": "loopt uiteen",
    "zandbakst": "isoleert",
    "pint vast": "zet vast",
    "pint": "zet vast",
}
_HYBRID_ALTERNATIVES = "|".join(sorted((re.escape(v) for v in HYBRID_VERBS), key=len, reverse=True))
_HYBRID_RE = re.compile(
    r"(?<![\w-])(" + _HYBRID_ALTERNATIVES + r")(?![\w-])",
    re.IGNORECASE,
)

_ABBREV = (
    "bv.", "bijv.", "d.w.z.", "i.p.v.", "o.a.", "t.o.v.",
    "z.g.", "nl.", "resp.", "etc.", "vs.", "e.g.", "i.e.",
)


# --- tekst normaliseren ----------------------------------------------------
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def starts_block(line: str) -> bool:
    """Begint hier een nieuw tekstblok? Een lijstitem en een kop staan los.

    Zonder deze regel wordt een bullet-lijst zonder witregels als één alinea
    geteld, en dan meet `long_paragraph` de lijstlengte in plaats van de
    hoeveelheid tekst die de lezer in één adem moet verwerken.
    """
    return bool(_LIST_ITEM_RE.match(line)) or line.lstrip().startswith("#")


def prose_lines(text: str) -> list[tuple[int, str]]:
    """Geef (regelnummer, prozaregel) terug: zonder code, frontmatter en tabellen."""
    out: list[tuple[int, str]] = []
    lines = text.split("\n")
    in_fence = False
    in_frontmatter = False
    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if idx == 1 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("|") or re.fullmatch(r"\|?[\s:|-]{4,}\|?", stripped):
            continue  # tabelrij of tabel-scheidingsregel: data, geen proza
        if raw.startswith("    ") and not stripped.startswith(("-", "*", "1.", ">")):
            continue  # geïndenteerde codeblok-regel
        line = re.sub(r"`[^`]*`", "CODE", raw)
        line = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)  # linktekst behouden
        if re.fullmatch(r"\s*[-*>#\s]*", line):
            continue
        out.append((idx, line))
    return out


def word_count(s: str) -> int:
    return len([w for w in re.split(r"\s+", s.strip()) if w])


def split_sentences(chunk: str) -> list[str]:
    """Splits op zinseinde, maar niet op een bekende afkorting."""
    protected = chunk
    for abbr in _ABBREV:
        protected = protected.replace(abbr, abbr.replace(".", "\x00"))
    parts = re.split(r"(?<=[.!?:])\s+(?=[A-Z(“\"*])", protected)
    return [p.replace("\x00", ".").strip() for p in parts if p.strip()]


def count_syllables_nl(word: str) -> int:
    """Benadering: tel klinkergroepen. Genoeg voor een relatieve leesindex."""
    w = re.sub(r"[^a-zà-ÿ]", "", word.lower())
    if not w:
        return 0
    # Anders dan in het Engels wordt een slot-e in het Nederlands uitgesproken
    # (de sjwa in "lopen", "harde"), dus die groep wordt niet afgetrokken.
    return max(len(re.findall(r"[aeiouyà-ÿ]+", w)), 1)


def flesch_douma(sentences: list[str]) -> float | None:
    """Flesch-Douma (NL): 206.84 - 0.77 * lettergrepen-per-100-woorden - 0.93 * gem. zinslengte."""
    words: list[str] = []
    for s in sentences:
        words.extend(w for w in re.split(r"\s+", s) if w)
    if len(words) < 20 or not sentences:
        return None
    syllables = sum(count_syllables_nl(w) for w in words)
    asl = len(words) / len(sentences)
    asw_per_100 = 100.0 * syllables / len(words)
    return round(206.84 - 0.77 * asw_per_100 - 0.93 * asl, 1)


# --- meten -----------------------------------------------------------------
@dataclass
class Hit:
    line: int
    kind: str
    detail: str

    def as_dict(self) -> dict:
        return {"line": self.line, "kind": self.kind, "detail": self.detail}


@dataclass
class Report:
    path: str
    sentences: int = 0
    words: int = 0
    score: float | None = None
    hits: list[Hit] = field(default_factory=list)

    @property
    def violations(self) -> int:
        return len(self.hits)

    def count(self, kind: str) -> int:
        return sum(1 for h in self.hits if h.kind == kind)

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "sentences": self.sentences,
            "words": self.words,
            "flesch_douma": self.score,
            "violations": self.violations,
            "long_sentence": self.count("long_sentence"),
            "long_paragraph": self.count("long_paragraph"),
            "hybrid_verb": self.count("hybrid_verb"),
            "hits": [h.as_dict() for h in self.hits],
        }


def measure(path: Path, rel: str) -> Report:
    text = path.read_text(encoding="utf-8", errors="replace")
    rep = Report(path=rel)
    lines = prose_lines(text)

    # alinea's = aaneengesloten reeksen prozaregels
    paragraphs: list[tuple[int, str]] = []
    buf: list[str] = []
    start = 0
    prev = None
    for num, line in lines:
        if buf and (num != (prev or 0) + 1 or starts_block(line)):
            paragraphs.append((start, " ".join(buf)))
            buf = []
        if not buf:
            start = num
        buf.append(line.strip())
        prev = num
    if buf:
        paragraphs.append((start, " ".join(buf)))

    all_sentences: list[str] = []
    for first_line, para in paragraphs:
        pwords = word_count(para)
        if pwords > MAX_PARAGRAPH_WORDS:
            rep.hits.append(Hit(first_line, "long_paragraph", f"{pwords} woorden in één alinea"))
        for sent in split_sentences(para):
            sw = word_count(sent)
            if sw < 3:
                continue
            all_sentences.append(sent)
            if sw > MAX_SENTENCE_WORDS:
                rep.hits.append(Hit(first_line, "long_sentence", f"{sw} woorden: {sent[:90]}…"))

    for num, line in lines:
        for m in _HYBRID_RE.finditer(line):
            found = m.group(1)
            better = HYBRID_VERBS.get(found.lower(), "")
            rep.hits.append(Hit(num, "hybrid_verb", f"'{found}' → '{better}'"))

    rep.sentences = len(all_sentences)
    rep.words = sum(word_count(s) for s in all_sentences)
    rep.score = flesch_douma(all_sentences)
    rep.hits.sort(key=lambda h: (h.line, h.kind))
    return rep


def collect(paths: list[Path], recursive: bool = False) -> list[Path]:
    """Zoek *.md-bestanden. Een map levert standaard alleen haar eigen bestanden op.

    Niet-recursief is de default, gelijk aan de rest van de check-doc-*-familie
    (die `find -maxdepth 1` gebruikt). `--recursive` is nodig voor een boom als
    `.claude/skills/`, waar elk document een `<naam>/SKILL.md` is.
    """
    found: list[Path] = []
    for p in paths:
        if p.is_dir():
            found.extend(sorted(p.rglob("*.md") if recursive else p.glob("*.md")))
        elif p.is_file():
            found.append(p)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(
        add_help=True,
        description="Meet de leesbaarheidsnormen uit taalgebruik-conventies.md.",
    )
    ap.add_argument("--strict", action="store_true", help="exit 1 zodra er hits zijn")
    ap.add_argument("--json", action="store_true", help="machineleesbare uitvoer")
    ap.add_argument(
        "--top", type=int, default=10, help="aantal slechtste documenten in de samenvatting"
    )
    ap.add_argument(
        "--file", action="append", default=[], help="één bestand meten, met regelnummers"
    )
    ap.add_argument(
        "--path", action="append", default=[], help="extra map of bestand om mee te nemen"
    )
    ap.add_argument(
        "--recursive",
        action="store_true",
        help="loop submappen mee (nodig voor .claude/skills/*/SKILL.md)",
    )
    args = ap.parse_args()

    docs_dir = Path(os.environ.get("DOCS_DIR", REPO_ROOT / "docs" / "cockpit"))
    targets: list[Path] = []
    detail = bool(args.file)
    if args.file or args.path:
        for raw in list(args.file) + list(args.path):
            cand = Path(raw)
            if not cand.is_absolute():
                cand = REPO_ROOT / raw
            if not cand.exists():
                print(f"ERROR: pad bestaat niet: {raw}", file=sys.stderr)
                return 2
            targets.append(cand)
    else:
        if not docs_dir.is_dir():
            print(f"ERROR: map bestaat niet: {docs_dir}", file=sys.stderr)
            return 2
        targets.append(docs_dir)

    files = collect(targets, recursive=args.recursive)
    if not files:
        print("ERROR: geen *.md-bestanden gevonden om te meten", file=sys.stderr)
        return 2

    reports = [measure(f, os.path.relpath(f, REPO_ROOT)) for f in files]
    total = sum(r.violations for r in reports)
    totals = {
        "long_sentence": sum(r.count("long_sentence") for r in reports),
        "long_paragraph": sum(r.count("long_paragraph") for r in reports),
        "hybrid_verb": sum(r.count("hybrid_verb") for r in reports),
    }

    if args.json:
        print(
            json.dumps(
                {
                    "files": len(reports),
                    "violations": total,
                    "totals": totals,
                    "norms": {
                        "max_sentence_words": MAX_SENTENCE_WORDS,
                        "max_paragraph_words": MAX_PARAGRAPH_WORDS,
                        "min_flesch_douma": MIN_FLESCH_DOUMA,
                    },
                    "reports": [r.as_dict() for r in sorted(reports, key=lambda r: -r.violations)],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 1 if (args.strict and total) else 0

    if detail:
        for rep in reports:
            score = "n.v.t." if rep.score is None else f"{rep.score}"
            print(f"{rep.path} — {rep.sentences} zinnen, leesindex {score}, {rep.violations} hits")
            for h in rep.hits:
                print(f"  {rep.path}:{h.line}: {h.kind}: {h.detail}")
    else:
        worst = sorted(reports, key=lambda r: (-r.violations, r.path))[: max(args.top, 0)]
        if worst and total:
            print(f"{'document':58} {'hits':>5} {'zin':>4} {'alin':>5} {'hybr':>5} {'index':>6}")
            for rep in worst:
                score = "-" if rep.score is None else f"{rep.score}"
                print(
                    f"{rep.path[:58]:58} {rep.violations:5} {rep.count('long_sentence'):4} "
                    f"{rep.count('long_paragraph'):5} {rep.count('hybrid_verb'):5} {score:>6}"
                )
            print()

    if total == 0:
        print(f"OK: alle {len(reports)} documenten halen de leesbaarheidsnorm.")
        return 0

    print(
        f"WARNING: {total} leesbaarheidshits in {sum(1 for r in reports if r.violations)} van "
        f"{len(reports)} documenten "
        f"(lange zinnen: {totals['long_sentence']}, lange alinea's: {totals['long_paragraph']}, "
        f"hybride werkwoorden: {totals['hybrid_verb']}).",
        file=sys.stderr,
    )
    print("Norm en herschrijfrecept: docs/cockpit/taalgebruik-conventies.md", file=sys.stderr)
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
