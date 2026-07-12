"""The `cockpit-baseline` blueprint — loaded from a static YAML on disk.

Every product-project inherits `cockpit-baseline` at birth, regardless of
template choice: a fixed set of universal process skills and *no* project-owned
agents (a fresh project keeps Claude Code's defaults). The blueprint lives as a
human-readable YAML next to this module rather than in the JSON `BlueprintStore`
so it's reviewable in-repo and versioned with the code that seeds it.

See §4.2 of ``docs/cockpit/repo-provisioning-bootstrap.md`` for the rationale
behind each universal skill.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from . import Blueprint

_YAML_PATH = Path(__file__).parent / "baseline_blueprint.yaml"


class BaselineBlueprint:
    """Loader for the on-disk `cockpit-baseline` blueprint."""

    yaml_path: Path = _YAML_PATH

    @classmethod
    def load(cls) -> Blueprint:
        """Parse the baseline YAML into a validated `Blueprint`.

        Raises `pydantic.ValidationError` if the YAML drifts out of shape
        with the `Blueprint` model — a cheap guard that turns a malformed
        edit into an immediate, loud failure instead of a silently broken
        seed.
        """
        data = yaml.safe_load(cls.yaml_path.read_text(encoding="utf-8"))
        return Blueprint.model_validate(data)
