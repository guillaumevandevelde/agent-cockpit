#!/usr/bin/env python3
"""Generate the derived docs index + llms.txt from docs/cockpit frontmatter.

Single source of truth: the YAML frontmatter (`title`/`type`/`status`) that
`scripts/check-doc-frontmatter.sh` guards on every `docs/cockpit/*.md`. This
generator reads that backbone and (re)emits two derived artifacts so neither has
to be hand-maintained (and neither can drift to a stale 43/84-coverage table):

  1. The **complete index block** inside `docs/cockpit/README.md`, delimited by
     the markers below. Every doc appears, grouped by `type`, with a `status`
     badge. The hand-curated "Leidend document per feature" cross-reference above
     the block is left untouched — it carries feature→canonical + superpowers
     mapping that frontmatter cannot express.
  2. `docs/cockpit/llms.txt` — the machine entry card (llmstxt.org convention):
     H1 project name + blockquote summary + a grouped, linked doc list.

This mirrors the OKF recommendation in
`docs/cockpit/knowledge-structure-navigation-analysis.md` §4.2 and builds on the
frontmatter backbone from card `25bfe803…`.

Modes:
  (default)   Rewrite the README block + llms.txt in place.
  --check     Compare the on-disk artifacts against a fresh render; print any
              drift. Advisory (exit 0) unless --strict (exit 1 on drift) — same
              "signal, not gate" philosophy as check-doc-frontmatter.sh.

Usage:
  scripts/generate-doc-index.py [--check] [--strict]
  scripts/generate-doc-index.py --docs-dir DIR --readme FILE --llms FILE ...

Stdlib-only (no PyYAML dependency) so it runs anywhere the repo is checked out,
matching the bash check-scripts and the sweep_*.py sweepers.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# README markers delimiting the generated block. Anything between them is owned
# by this script; the surrounding hand-curated prose is preserved verbatim.
BEGIN_MARKER = "<!-- BEGIN GENERATED DOC INDEX (scripts/generate-doc-index.py) — DO NOT EDIT BY HAND -->"
END_MARKER = "<!-- END GENERATED DOC INDEX -->"

PROJECT_NAME = "Agent Cockpit"
LLMS_SUMMARY = (
    "Machine-instapkaart voor de canonieke spec-boom van de fork Agent Cockpit "
    "(`docs/cockpit/`). Elke long-lived architectuur-, ontwerp-, beslis- en "
    "analysedoc staat hieronder, gegroepeerd op `type` met zijn `status`. Bij "
    "twijfel of overlap: lees het cockpit-document eerst. Deze lijst is "
    "gegenereerd uit de YAML-frontmatter — bewerk 'm niet met de hand, draai "
    "`scripts/generate-doc-index.py`."
)

# Fixed presentation order for the `type` groups (unknown types sort last,
# alphabetically). Matches the taxonomy in check-doc-frontmatter.sh.
TYPE_ORDER = ["index", "reference", "spec", "plan", "decision", "analysis"]
TYPE_LABELS = {
    "index": "Index",
    "reference": "Reference",
    "spec": "Spec",
    "plan": "Plan",
    "decision": "Decision",
    "analysis": "Analysis",
}

# Status → visual badge for the human index. Keep to a small, terminal-safe set.
STATUS_BADGE = {
    "active": "🟢 active",
    "proposed": "🟡 proposed",
    "decided": "🔵 decided",
    "superseded": "⚪ superseded",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _fm_field(block: str, key: str) -> str:
    """Extract a top-level scalar frontmatter field, stripping one quote layer."""
    pat = re.compile(rf"^{re.escape(key)}:\s*(.*?)\s*$", re.MULTILINE)
    m = pat.search(block)
    if not m:
        return ""
    val = m.group(1)
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        val = val[1:-1]
    return val.strip()


def parse_frontmatter(path: Path) -> dict | None:
    """Return {title,type,status} for a doc, or None if it has no frontmatter."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    block_lines: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        block_lines.append(line)
    block = "\n".join(block_lines)
    return {
        "title": _fm_field(block, "title"),
        "type": _fm_field(block, "type"),
        "status": _fm_field(block, "status"),
    }


def collect_docs(docs_dir: Path) -> list[dict]:
    """Gather one record per docs/cockpit/*.md with parsed frontmatter."""
    docs: list[dict] = []
    for path in sorted(docs_dir.glob("*.md")):
        fm = parse_frontmatter(path)
        name = path.name
        docs.append(
            {
                "name": name,
                "title": (fm or {}).get("title") or name,
                "type": (fm or {}).get("type") or "",
                "status": (fm or {}).get("status") or "",
            }
        )
    return docs


def _type_sort_key(doc_type: str) -> tuple[int, str]:
    if doc_type in TYPE_ORDER:
        return (TYPE_ORDER.index(doc_type), "")
    return (len(TYPE_ORDER), doc_type)


def group_by_type(docs: list[dict]) -> list[tuple[str, list[dict]]]:
    """Return [(type, [docs sorted by filename])] in presentation order."""
    seen: dict[str, list[dict]] = {}
    for doc in docs:
        seen.setdefault(doc["type"], []).append(doc)
    ordered = sorted(seen.items(), key=lambda kv: _type_sort_key(kv[0]))
    return [(t, sorted(items, key=lambda d: d["name"])) for t, items in ordered]


def render_readme_block(docs: list[dict]) -> str:
    """Render the marker-delimited generated README index block."""
    total = len(docs)
    out: list[str] = []
    out.append(BEGIN_MARKER)
    out.append("")
    out.append("## Volledige index (gegenereerd)")
    out.append("")
    out.append(
        f"> **Afgeleid uit de frontmatter — niet met de hand bewerken.** "
        f"Regenereer met `scripts/generate-doc-index.py`; "
        f"`scripts/generate-doc-index.py --check --strict` bewaakt de drift. "
        f"Dekt **alle {total} docs** (elke `docs/cockpit/*.md`), gegroepeerd op "
        f"`type` met een `status`-badge."
    )
    out.append("")
    for doc_type, items in group_by_type(docs):
        label = TYPE_LABELS.get(doc_type, doc_type or "(geen type)")
        out.append(f"### {label} ({len(items)})")
        out.append("")
        out.append("| Document | Status |")
        out.append("|---|---|")
        for doc in items:
            badge = STATUS_BADGE.get(doc["status"], doc["status"] or "—")
            title = doc["title"].replace("|", "\\|")
            out.append(f"| [{title}](./{doc['name']}) | {badge} |")
        out.append("")
    out.append(END_MARKER)
    return "\n".join(out)


def render_llms_txt(docs: list[dict]) -> str:
    """Render docs/cockpit/llms.txt per the llmstxt.org convention."""
    out: list[str] = []
    out.append(f"# {PROJECT_NAME}")
    out.append("")
    out.append(f"> {LLMS_SUMMARY}")
    out.append("")
    for doc_type, items in group_by_type(docs):
        label = TYPE_LABELS.get(doc_type, doc_type or "(geen type)")
        out.append(f"## {label}")
        out.append("")
        for doc in items:
            status = doc["status"] or "unknown"
            dtype = doc["type"] or "unknown"
            out.append(
                f"- [{doc['title']}](./{doc['name']}): type={dtype} status={status}"
            )
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def splice_readme(readme_text: str, block: str) -> str:
    """Replace the marked block in README (or append before ## Regels / EOF)."""
    if BEGIN_MARKER in readme_text and END_MARKER in readme_text:
        pre = readme_text.split(BEGIN_MARKER, 1)[0]
        post = readme_text.split(END_MARKER, 1)[1]
        return pre.rstrip("\n") + "\n\n" + block + "\n" + post.lstrip("\n")
    insert = block + "\n"
    marker = "\n## Regels"
    if marker in readme_text:
        idx = readme_text.index(marker)
        return readme_text[:idx].rstrip("\n") + "\n\n" + insert + "\n" + readme_text[idx:].lstrip("\n")
    return readme_text.rstrip("\n") + "\n\n" + insert


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report drift instead of writing")
    parser.add_argument("--strict", action="store_true", help="with --check: exit 1 on drift")
    parser.add_argument("--docs-dir", type=Path, default=None)
    parser.add_argument("--readme", type=Path, default=None)
    parser.add_argument("--llms", type=Path, default=None)
    args = parser.parse_args(argv)

    root = repo_root()
    docs_dir = args.docs_dir or (root / "docs" / "cockpit")
    readme = args.readme or (docs_dir / "README.md")
    llms = args.llms or (docs_dir / "llms.txt")

    if not docs_dir.is_dir():
        print(f"ERROR: docs directory not found at {docs_dir}", file=sys.stderr)
        return 2

    docs = collect_docs(docs_dir)
    block = render_readme_block(docs)
    llms_text = render_llms_txt(docs)

    readme_text = readme.read_text(encoding="utf-8") if readme.exists() else ""
    new_readme = splice_readme(readme_text, block)
    current_llms = llms.read_text(encoding="utf-8") if llms.exists() else ""

    readme_drift = new_readme != readme_text
    llms_drift = llms_text != current_llms

    if args.check:
        if not readme_drift and not llms_drift:
            print(f"OK: generated index + llms.txt in sync with frontmatter ({len(docs)} docs).")
            return 0
        print("WARNING: generated docs index/llms.txt is out of sync with the frontmatter:", file=sys.stderr)
        if readme_drift:
            print(f"  - {readme.relative_to(root) if readme.is_relative_to(root) else readme} (index block stale)", file=sys.stderr)
        if llms_drift:
            print(f"  - {llms.relative_to(root) if llms.is_relative_to(root) else llms} (out of date)", file=sys.stderr)
        print("", file=sys.stderr)
        print("Regenerate with: scripts/generate-doc-index.py", file=sys.stderr)
        if args.strict:
            return 1
        print("(advisory — not failing the build; run with --strict to enforce)", file=sys.stderr)
        return 0

    if readme_drift:
        readme.write_text(new_readme, encoding="utf-8")
        print(f"wrote {readme}")
    if llms_drift:
        llms.write_text(llms_text, encoding="utf-8")
        print(f"wrote {llms}")
    if not readme_drift and not llms_drift:
        print("already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
