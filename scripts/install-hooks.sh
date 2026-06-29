#!/usr/bin/env bash
# Point git at the repo's tracked hooks so the pre-push test gate is active.
# Idempotent: safe to run repeatedly. Called by scripts/install.sh; run it directly
# after a fresh clone if you skipped install.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

git config core.hooksPath .githooks
chmod +x .githooks/* 2>/dev/null || true
echo "Git hooks installed: core.hooksPath -> .githooks (pre-push runs the test set)"
