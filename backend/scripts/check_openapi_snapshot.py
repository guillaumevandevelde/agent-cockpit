"""Diff the live FastAPI OpenAPI schema against a committed snapshot.

Only `paths` and `components` are compared. `info.version` tracks the app
version (bumped independently via scripts/bump-version.sh) and would make
every release look like an API contract change if included.
"""
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).parent.parent
SNAPSHOT_PATH = BACKEND_ROOT / "openapi.snapshot.json"


def contract_shape(schema: dict) -> dict:
    return {"paths": schema["paths"], "components": schema.get("components", {})}


def current_shape() -> dict:
    # Running as `python scripts/check_openapi_snapshot.py` puts scripts/ (not
    # backend/) at sys.path[0], so `app` isn't importable without this.
    sys.path.insert(0, str(BACKEND_ROOT))
    from app.main import app

    return contract_shape(app.openapi())


def main() -> int:
    current = current_shape()

    if not SNAPSHOT_PATH.exists():
        SNAPSHOT_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        print(f"Created {SNAPSHOT_PATH}")
        return 0

    snapshot = json.loads(SNAPSHOT_PATH.read_text())
    if current != snapshot:
        print(
            "API surface changed but backend/openapi.snapshot.json was not "
            "updated.\n\n"
            "Run: cd backend && python scripts/check_openapi_snapshot.py --update\n"
            "Then check whether the frontend's hand-maintained TypeScript "
            "types (frontend/src/types/) need matching updates, and commit "
            "both.",
            file=sys.stderr,
        )
        return 1

    print("OpenAPI contract matches snapshot.")
    return 0


if __name__ == "__main__":
    if "--update" in sys.argv:
        SNAPSHOT_PATH.write_text(json.dumps(current_shape(), indent=2, sort_keys=True) + "\n")
        print(f"Updated {SNAPSHOT_PATH}")
        sys.exit(0)
    sys.exit(main())
