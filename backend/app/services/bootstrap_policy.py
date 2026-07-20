"""``BootstrapPolicy`` — the centralised "cockpit-defaults" of repo-bootstrap.

Facet-B follow-up #5 of ``docs/cockpit/repo-provisioning-bootstrap.md`` §6. It
concentrates the policy toggles from §4.3 of that analysis in one typed dataclass so
the bootstrap implementation cards (atomic-init, blueprint-apply, gh-remote, …)
reconcile against a single source of truth instead of each re-deciding the same
defaults.

**It is now consumed by the new-project birth flow.** ``InceptionService`` (facet A's
``create_project_from_intake``) threads a ``BootstrapPolicy`` through the birth so that
autodispatch-at-birth, the MIT license default, the first-commit-template choice and
the no-CI-at-birth stance come from this policy instead of ad-hoc code-path defaults;
``RepoBootstrapService.init_local`` also accepts a policy to write the LICENSE. See
``docs/cockpit/bootstrap-policy.md`` for the full decision rationale and the
consuming-card matrix.

Field → decision mapping (see ``bootstrap-policy.md`` §1):

* ``autodispatch_default``     → §1.1  autodispatch off at birth (security-default-deny)
* ``permission_mode``          → §1.2  no per-project skip_permissions row (inherit dispatch default)
* ``first_commit_content`` /
  ``first_commit_message``     → §1.3  first commit = rendered template (never empty)
* ``gitignore_fallback``       → §1.4  .gitignore ships per-template; policy holds only a fallback
* ``ci_bootstrap``             → §1.5  no CI at birth; deferred to facet-D CITemplateService
* ``license`` /
  ``copyright_holder``         → §1.6  MIT default, escape-hatch to None / other SPDX id
* ``key_collision_strategy``   → §1.7  suffix-counter on the pre-remote slug
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PermissionMode = Literal["default", "acceptEdits", "bypassPermissions"]
FirstCommitContent = Literal["template", "empty"]
KeyCollisionStrategy = Literal["suffix-counter", "reject"]

#: Kitchen-sink .gitignore used **only** when a chosen template ships none. Copied
#: verbatim from ``backend/app/services/templates/empty/.gitignore.tmpl`` so the
#: fallback stays in step with the ``empty`` template (§1.4). Typed templates supply
#: their own stack-specific .gitignore and never touch this.
DEFAULT_GITIGNORE_FALLBACK = """\
# Dependencies
node_modules/
__pycache__/
*.py[cod]
.venv/
venv/

# Build output
dist/
build/
*.egg-info/

# Environment
.env
.env.local

# Editor / OS
.DS_Store
.idea/
.vscode/
"""


@dataclass(frozen=True)
class BootstrapPolicy:
    """The centralised defaults for one repo-bootstrap run.

    Every field is a *default*; the caller (in practice facet A's
    ``create_project_from_intake``) may override per-project. Frozen so a policy value
    can be shared safely across the bootstrap chain without a step mutating it.
    """

    #: §1.1 — Autodispatch off at birth. The human-approved intake flow may pass
    #: ``True`` explicitly (a human just approved), keeping human-in-the-loop intact.
    autodispatch_default: bool = False

    #: §1.2 — ``None`` means "write no ``skip_permissions:<key>`` KanbanMeta row"; the
    #: dispatch layer already defaults to bypass-in-worktree. The three explicit values
    #: let a security-conscious operator pin a per-project mode.
    permission_mode: PermissionMode | None = None

    #: §1.3 — First commit captures the fully-rendered template tree (≥ .gitignore +
    #: README). ``"empty"`` (``git commit --allow-empty``) is a rejected alternative,
    #: kept only as an explicit opt-out.
    first_commit_content: FirstCommitContent = "template"

    #: §1.3 — ``{project_name}`` / ``{intake_card_id}`` are substituted at bootstrap
    #: time. Never embeds the kanban plan-attachment (that lives on the card).
    first_commit_message: str = (
        "chore: bootstrap {project_name} from intake {intake_card_id}"
    )

    #: §1.4 — Consulted only when the chosen template provides no .gitignore of its own.
    gitignore_fallback: str = DEFAULT_GITIGNORE_FALLBACK

    #: §1.5 — No ``.github/workflows/`` copied at birth. Flip to ``True`` once facet-D's
    #: CITemplateService (card ``c66a93a20c0a``) is available and the project wants CI.
    ci_bootstrap: bool = False

    #: §1.6 — Any SPDX id, or ``None`` to write no LICENSE file (proprietary/internal).
    license: str | None = "MIT"

    #: §1.6 — Copyright holder for the LICENSE; ``None`` falls back to ``git config
    #: user.name`` at render time.
    copyright_holder: str | None = None

    #: §1.7 — Pre-remote slug disambiguation: ``"suffix-counter"`` yields
    #: ``slug:my-app-2`` on collision; ``"reject"`` surfaces an impediment for an
    #: explicit user-supplied name. Post-remote the key becomes ``git:host/path`` and
    #: collision no longer applies.
    key_collision_strategy: KeyCollisionStrategy = "suffix-counter"


#: The out-of-the-box cockpit defaults. A convenience singleton for consumers that want
#: "just the defaults" without constructing their own instance.
COCKPIT_DEFAULT_POLICY = BootstrapPolicy()


#: Full MIT license body (§1.6). ``{year}`` / ``{holder}`` are substituted at render
#: time. Kept verbatim from the SPDX MIT reference text so a birthed repo ships a
#: legally-standard LICENSE, not a paraphrase.
MIT_LICENSE_TEMPLATE = """\
MIT License

Copyright (c) {year} {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def render_license(policy: BootstrapPolicy, *, holder: str, year: int) -> str | None:
    """Render the ``LICENSE`` body for ``policy`` (§1.6), or ``None`` for no file.

    * ``policy.license is None`` → ``None`` (proprietary/internal: write no LICENSE).
    * ``policy.license == "MIT"`` → the full MIT text with ``holder`` / ``year`` filled in.
    * any other SPDX id → a minimal header naming the id. We only ship the full MIT
      body today; other ids are a valid opt-in but the caller supplies their own text
      if they need the complete license.
    """
    if policy.license is None:
        return None
    if policy.license == "MIT":
        return MIT_LICENSE_TEMPLATE.format(year=year, holder=holder)
    return f"{policy.license} License\n\nCopyright (c) {year} {holder}\n"
