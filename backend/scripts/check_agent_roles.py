"""Drift check 2: every persona used in the kanban routing tables must match a
`name:` in `.claude/agents/*.md`. Catches the `developer / tester / testing /
code-review` vestigial roles that `test_impediment_agents.py` enforces on the
backend — the drift report surfaces the same check in the weekly summary, so
the project health is visible even when nobody is running pytest.

Signal-only: writes a markdown summary and prints a short status line.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.drift_checks import (  # noqa: E402
    collect_personas_from_routing,
    find_mismatched_personas,
    list_agent_names,
)

# Roles that were retired from the old card-flow.json and must never reappear
# as a persona. Surfaced as a separate signal even if the routing map still
# happens to use them (the strict unit test in tests/test_impediment_agents.py
# already fails on re-introduction; this is a softer, weekly reminder).
VESTIGIAL_ROLES = {"developer", "tester", "testing", "code-review"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        required=True,
        help="Path to the repository root.",
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=Path("/tmp/agent_roles_summary.md"),
        help="Where to write the markdown summary.",
    )
    args = parser.parse_args()

    router_path = args.repo_root / "backend" / "app" / "api" / "v1" / "kanban" / "router.py"
    schemas_path = args.repo_root / "backend" / "app" / "kanban" / "schemas.py"
    agents_dir = args.repo_root / ".claude" / "agents"

    used = collect_personas_from_routing(router_path, schemas_path)
    available = list_agent_names(agents_dir)
    missing = find_mismatched_personas(used, available)
    vestigial_in_use = sorted(used & VESTIGIAL_ROLES)
    unused_agents = sorted(available - used)

    lines = ["### Agent role consistency", ""]
    has_drift = bool(missing or vestigial_in_use)

    if has_drift:
        status = "drifted"
        if missing:
            lines.append(f"**Persona(s) used in code but not defined as agents:** {', '.join(f'`{m}`' for m in missing)}")
            lines.append("")
        if vestigial_in_use:
            lines.append(
                f"**Vestigial role(s) still referenced:** {', '.join(f'`{v}`' for v in vestigial_in_use)}"
            )
            lines.append("")
    else:
        status = "ok"
        lines.append(
            "**Status:** ok — every persona in `_IMPEDIMENT_AGENTS` / "
            "`WORK_TYPE_PERSONA_DEFAULTS` resolves to a `.claude/agents/*.md` entry."
        )
        lines.append("")

    lines.append(f"- Personas in use: {len(used)} ({', '.join(f'`{p}`' for p in sorted(used)) or 'none'})")
    lines.append(f"- Agents defined: {len(available)} ({', '.join(f'`{a}`' for a in sorted(available)) or 'none'})")
    if unused_agents and not has_drift:
        lines.append(
            f"- Agents defined but unused in routing: {', '.join(f'`{a}`' for a in unused_agents)}"
        )

    args.summary_out.write_text("\n".join(lines) + "\n")
    print(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
