#!/usr/bin/env bash
#
# check-kanban-meta-security-conflicts.sh — flag KanbanMeta overrides that
# contradict the security profile of the same project.
#
# Background (kanban card d5642a57…): the dispatch hot path reads two
# KanbanMeta namespaces first — ``skip_permissions:<project_key>`` and
# ``transport:<project_key>`` — and only falls back to the project's
# ``ProjectSecurityProfile`` (risk_class-derived defaults) when the
# override is absent. That makes the override **load-bearing**: when the
# row is present, the profile is invisible. If it disappears — via the
# existing ``POST /skip_permissions`` toggle, a DB reset, or a fresh
# install (CLAUDE.md: "No database migration system — schema changes
# require deleting the db") — every dispatch on this repo falls back to
# the profile and stalls on an unanswerable permission prompt.
#
# The card's scope is *visibility only*: this check surfaces every
# ``skip_permissions:*`` / ``transport:*`` KanbanMeta row whose value
# disagrees with what the matching ``project_security_profiles`` row
# would dictate. It deliberately does NOT auto-reclassify the project
# (that's ``SecurityProfileService`` follow-up #12). It also does NOT
# touch the override or the profile — read-only against both stores.
#
# Comparison semantics (mirror backend/app/kanban/dispatch.py:344-361):
#   - ``risk_class is None`` or ``risk_class == "meta"`` →
#     effective_skip=True, effective_transport="worktree"
#   - anything else (product / untrusted) →
#     effective_skip=False, effective_transport="sandcastle"
#
# A conflict is logged when the override value disagrees with the
# effective default. We deliberately ignore rows whose project has no
# security profile at all — there's nothing to contradict, and the
# override is then the entire configuration (a different problem,
# outside this card's scope).
#
# DB layout: the kanban board (kanban_meta) and the project registry
# (projects + project_security_profiles) live in *different* SQLite
# files. ``--kanban-db`` and ``--registry-db`` accept paths to each;
# defaults walk the standard locations and fall back to git-common-dir
# discovery so dispatched worktrees without a populated venv still
# resolve a registry DB (the pattern that fixed card 71e88ac2 for the
# kanban-conventions check).
#
# Usage:
#   scripts/check-kanban-meta-security-conflicts.sh [--strict]
#                                                   [--kanban-db PATH]
#                                                   [--registry-db PATH]
#                                                   [--help]
#
# Env:
#   KANBAN_DB       kanban board DB holding kanban_meta
#                   (default: ~/.claude-registry/kanban.db)
#   REGISTRY_DB     project registry DB holding projects +
#                   project_security_profiles
#                   (default: walks git-common-dir → <root>/backend/
#                   claude_registry.db; falls back to the env var
#                   MAIN_DB_PATH if set, mirroring check-kanban-conventions)
#
# Exit codes:
#   0  clean (advisory mode: hits printed but not failing)
#   1  --strict and ≥1 conflict
#   2  usage error / DB missing / sqlite query failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

KANBAN_DB_PATH="${KANBAN_DB:-$HOME/.claude-registry/kanban.db}"
REGISTRY_DB_PATH="${REGISTRY_DB:-}"

STRICT=0

print_help() {
  sed -n '3,53p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
  case "$arg" in
    --strict)        STRICT=1 ;;
    --kanban-db=*)   KANBAN_DB_PATH="${arg#--kanban-db=}" ;;
    --registry-db=*) REGISTRY_DB_PATH="${arg#--registry-db=}" ;;
    --help|-h)
      print_help
      exit 0
      ;;
    "")
      ;;
    *)
      echo "ERROR: unknown argument '$arg' (see --help)" >&2
      exit 2
      ;;
  esac
done

# --- resolve REGISTRY_DB_PATH if still unset ------------------------------
# Same fallback chain as scripts/check-kanban-conventions.sh §2 so a
# dispatched worktree (no venv, no env vars) still resolves the main
# registry DB. The kanban DB already defaults to the machine-global
# kanban.db, which all worktrees share.
if [ -z "$REGISTRY_DB_PATH" ]; then
  if [ -n "${MAIN_DB_PATH:-}" ] && [ -f "$MAIN_DB_PATH" ]; then
    REGISTRY_DB_PATH="$MAIN_DB_PATH"
  elif command -v git >/dev/null 2>&1; then
    if common_dir="$(git rev-parse --git-common-dir 2>/dev/null)" \
       && [ -n "$common_dir" ]; then
      abs_common="$(cd "$common_dir" 2>/dev/null && pwd -P)" || abs_common=""
      if [ -n "$abs_common" ]; then
        main_root="$(dirname "$abs_common")"
        candidate="$main_root/backend/claude_registry.db"
        [ -f "$candidate" ] && REGISTRY_DB_PATH="$candidate"
      fi
    fi
  fi
fi

if [ ! -r "$KANBAN_DB_PATH" ]; then
  echo "ERROR: kanban DB not found or not readable at: $KANBAN_DB_PATH" >&2
  echo "Set KANBAN_DB=/path/to/kanban.db or pass --kanban-db=PATH." >&2
  exit 2
fi

if [ -z "$REGISTRY_DB_PATH" ]; then
  echo "ERROR: registry DB not found — set REGISTRY_DB or MAIN_DB_PATH," >&2
  echo "       or pass --registry-db=PATH (a registry DB at backend/" >&2
  echo "       claude_registry.db relative to the repo root is the default)." >&2
  exit 2
fi

if [ ! -r "$REGISTRY_DB_PATH" ]; then
  echo "ERROR: registry DB not found or not readable at: $REGISTRY_DB_PATH" >&2
  echo "Set REGISTRY_DB=/path/to/claude_registry.db or pass --registry-db=PATH." >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not on PATH — required to compute project_key from" >&2
  echo "       each registered project's git remote (mirrors" >&2
  echo "       backend/app/kanban/project_key.py)." >&2
  exit 2
fi

# ---
# Delegate the join to a small inline Python helper. Same pattern as
# scripts/check-analysis-outcomes.sh — keeps the bash side in plain
# text-processing land and the SQL/key-resolution logic in one focused
# place. The output format is TSV so the bash side can awk it
# column-anchored:
#
#   <project_key>\t<override_kind>\t<override_value>\t<risk_class>\t<effective_skip>\t<effective_transport>\t<project_path>\t<profile_id>
#
# override_kind is "skip_permissions" or "transport". effective_skip /
# effective_transport are the bool/str the dispatch hot path would
# use *without* the override (the risk_class-derived default).
#
# Stderr is redirected to a tempfile so we can print the diagnosis after a
# non-zero exit. The assignment is in an `||` list so `set -e` does not exit
# before the PY_RC handler runs.
PY_STDERR_FILE="$(mktemp)"
PY_RC=0
HIT_TSV="$(python3 - "$KANBAN_DB_PATH" "$REGISTRY_DB_PATH" 2>"$PY_STDERR_FILE" <<'PY'
import re, sqlite3, subprocess, sys
from pathlib import Path

kanban_db, registry_db = sys.argv[1], sys.argv[2]

# Mirror of backend/app/kanban/project_key.py — compute the same
# ``git:<host>/<path>`` (or ``slug:<basename>``) project_key the dispatch
# hot path uses. Inlined so the check has no Python-import dependency on
# the backend package (worktrees share a venv at the main checkout, and
# dispatching a sub-check from a worktree shouldn't need that venv
# importable — see the kanban_db-discovery fallback in this script's
# top-level arg parser).
def git_remote(path: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", path, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        url = out.stdout.strip()
        return url or None
    except Exception:
        return None


def normalize_remote(url: str) -> str:
    url = url.strip()
    url = re.sub(r"\.git$", "", url)
    url = re.sub(r"^[a-z]+://", "", url)
    url = re.sub(r"^[^@/]+@", "", url)
    url = url.replace(":", "/")
    return re.sub(r"/+", "/", url)


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "project"


def resolve_project_key(path: str) -> str:
    remote = git_remote(path)
    if remote:
        return f"git:{normalize_remote(remote)}"
    return f"slug:{slug(Path(path).name)}"


# Open kanban DB read-only — we never mutate the board.
try:
    kcon = sqlite3.connect(f"file:{kanban_db}?mode=ro", uri=True)
    kcon.row_factory = sqlite3.Row
    override_rows = kcon.execute(
        """
        SELECT key, value FROM kanban_meta
         WHERE key LIKE 'skip_permissions:%'
            OR key LIKE 'transport:%'
        """
    ).fetchall()
except sqlite3.Error as e:
    print(f"ERROR: kanban_meta query failed: {e}", file=sys.stderr)
    sys.exit(2)
finally:
    try:
        kcon.close()
    except Exception:
        pass

# Build project_key → project_path map from registry DB. Mirror of
# backend/app/kanban/project_key.resolve_project_path: walk every
# registered project, compute its key, and index by it.
try:
    rcon = sqlite3.connect(f"file:{registry_db}?mode=ro", uri=True)
    rcon.row_factory = sqlite3.Row
    proj_rows = rcon.execute("SELECT name, path FROM projects").fetchall()
    profile_rows = rcon.execute(
        """
        SELECT id, project_path, risk_class, default_skip_permissions,
               default_transport
          FROM project_security_profiles
        """
    ).fetchall()
except sqlite3.Error as e:
    print(f"ERROR: registry DB query failed: {e}", file=sys.stderr)
    sys.exit(2)
finally:
    try:
        rcon.close()
    except Exception:
        pass

# Index profile rows by project_path (1:1 — see ProjectSecurityProfile
# __table_args__).
profile_by_path = {row["project_path"]: row for row in profile_rows}

# Index projects by computed project_key. Multiple registered paths can
# theoretically share a key (rare — would mean two registered projects
# with the same remote); we report the first match and skip the rest.
key_to_path = {}
for row in proj_rows:
    try:
        key = resolve_project_key(row["path"])
    except Exception:
        continue
    if key not in key_to_path:
        key_to_path[key] = row["path"]

SKIP_PREFIX = "skip_permissions:"
TRANSPORT_PREFIX = "transport:"


def effective_defaults(risk_class):
    """Mirror of dispatch._skip_permissions_for_risk_class +
    _transport_for_risk_class. None or 'meta' keeps the historical
    permissive default; everything else enforces permissions + sandcastle."""
    is_permissive = risk_class is None or risk_class == "meta"
    return (is_permissive, "worktree" if is_permissive else "sandcastle")


for row in override_rows:
    key = row["key"]
    value = row["value"]
    if key.startswith(SKIP_PREFIX):
        kind = "skip_permissions"
        project_key = key[len(SKIP_PREFIX):]
    elif key.startswith(TRANSPORT_PREFIX):
        kind = "transport"
        project_key = key[len(TRANSPORT_PREFIX):]
    else:
        continue

    project_path = key_to_path.get(project_key)
    if project_path is None:
        # Override for a project that isn't registered. Not a profile
        # conflict (no profile to contradict); skip — surfaced by a
        # different check (this card's scope is *profile* conflicts).
        continue

    profile = profile_by_path.get(project_path)
    if profile is None:
        # Project exists but has no security profile yet — there's
        # nothing to contradict. Skip; the override is then the entire
        # configuration, which is a different problem.
        continue

    eff_skip, eff_transport = effective_defaults(profile["risk_class"])

    if kind == "skip_permissions":
        # Override is stored as a string '1' / '0' (KanbanMeta.value is
        # Text). profile.default_skip_permissions is int 0/1 in SQLite.
        override_bool = value == "1"
        if override_bool == eff_skip:
            continue
    else:  # transport
        if value == eff_transport:
            continue

    # Flatten any control chars in the project_key/path so the bash awk
    # below stays column-anchored.
    safe_key = project_key.replace("\t", " ").replace("\n", " ")
    safe_path = project_path.replace("\t", " ").replace("\n", " ")
    print(
        "\t".join([
            safe_key,
            kind,
            value,
            str(profile["risk_class"]),
            str(eff_skip).lower(),
            eff_transport,
            safe_path,
            str(profile["id"]),
        ])
    )
PY
)" || PY_RC=$?
if [ "$PY_RC" -ne 0 ]; then
  echo "ERROR: conflict query failed (exit $PY_RC); see stderr below." >&2
  [ -s "$PY_STDERR_FILE" ] && cat "$PY_STDERR_FILE" >&2 || true
  rm -f "$PY_STDERR_FILE"
  exit 2
fi
rm -f "$PY_STDERR_FILE"

# Empty stdout from Python means clean.
if [ -z "$HIT_TSV" ]; then
  echo "OK: every kanban_meta skip_permissions/transport override agrees with its project's security profile."
  exit 0
fi

total=$(printf '%s\n' "$HIT_TSV" | wc -l | tr -d ' ')
echo "WARNING: ${total} kanban_meta override(s) contradict their project's security profile:" >&2
echo "" >&2
printf '%s\n' "$HIT_TSV" | awk -F'\t' '
  {
    key    = $1
    kind   = $2
    val    = $3
    rc     = $4
    eff_sk = $5
    eff_tr = $6
    path   = $7
    pid    = $8

    if (kind == "skip_permissions") {
      arrow = "override=skip_permissions=" val " vs profile risk_class=\x27" rc "\x27 (effective skip=" eff_sk ")"
    } else {
      arrow = "override=transport=" val " vs profile risk_class=\x27" rc "\x27 (effective transport=" eff_tr ")"
    }
    printf "  [%s] %s\n", kind, key
    printf "         %s\n", arrow
    printf "         project_path=%s  profile_id=%s\n", path, pid
    printf "         removing this row reverts to the profile default\n\n"
  }
' >&2

echo "These KanbanMeta rows win over the project_security_profiles row at" >&2
echo "dispatch time (backend/app/kanban/dispatch.py:364-397). Disappear" >&2
echo "(via the skip_permissions toggle, a DB reset, or a fresh install) and" >&2
echo "the repo falls back to the profile — for product-staging that means" >&2
echo "every dispatch stalls on an unanswerable permission prompt." >&2
echo "" >&2
echo "Reclassification to risk_class=meta (the actual fix) is" >&2
echo "SecurityProfileService follow-up #12 — out of scope here." >&2

if [ "$STRICT" -eq 1 ]; then
  exit 1
fi
echo "(advisory — not failing the build; run with --strict to enforce)" >&2
exit 0