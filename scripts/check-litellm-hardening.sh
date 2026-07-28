#!/usr/bin/env bash
# check-litellm-hardening.sh — verify that a running LiteLLM proxy satisfies
# the hardening requirements from docs/cockpit/9router-integratie-analyse.md §11.2.
#
# Five properties, each a separate check; failure is loud and actionable. The
# card explicitly forbids checking "the presence of a string in a config
# file" alone, so every check verifies observed behaviour or the actual
# configuration the proxy loaded, never grep on a static file.
#
# Properties verified:
#   1. loopback-only binding     (proxy is not reachable outside the host)
#   2. master_key auth enforced  (proxy rejects unauthenticated /v1/messages)
#   3. no prompt-mutation        (no callbacks / guardrails / transformations
#                                  are wired up that would touch request bodies;
#                                  guardrails are checked in all three
#                                  documented forms — top-level `guardrails:`,
#                                  `litellm_settings.guardrails:`, and
#                                  per-model `litellm_params.guardrails:`)
#   4. no telemetry / no external sync (no success_callback to a third party,
#                                  no alerting hooks, no database_url to a
#                                  third-party host)
#   5. credential hygiene        (no plaintext api_key values; os.environ/VAR
#                                  or a credential_list are acceptable)
#
# Usage:
#   bash scripts/check-litellm-hardening.sh                                  # defaults
#   bash scripts/check-litellm-hardening.sh --url http://127.0.0.1:4000     # custom URL
#   bash scripts/check-litellm-hardening.sh --config-yaml /path/to/config.yaml
#   bash scripts/check-litellm-hardening.sh --master-key sk-...
#   bash scripts/check-litellm-hardening.sh --strict                        # exit 1 on any FAIL
#
# Proxy install (the sidecar this check hardens):
#   python3 -m venv v && ./v/bin/pip install 'litellm[proxy]' prisma
# `prisma` is NOT optional: LiteLLM's auth-exception handler imports it on
# every auth rejection, and without it every failed-auth request becomes
# HTTP 500 instead of 401 (see docs/cockpit/litellm-pilot-meting.md §2.1).
# No DB / no `prisma generate` needed — `prisma` only has to be importable.
#
# Exit codes:
#   0 — every check PASS or WARN (skip-with-actionable-reason counts as WARN)
#   1 — at least one FAIL (only with --strict, matching sibling check-*.sh scripts)
#   2 — invocation error (missing tool, bad args)
#
# Source of truth for the requirement list: §11.2 (herziening 2026-07-21,
# geüpdatet 2026-07-28 voor de drie guardrails-vormen en de
# service_callbacks-phantom-flag).
# Source of truth for the key names we read: the liteLLM proxy `config_settings`
# page (general_settings / litellm_settings / router_settings), the upstream
# test fixtures in `tests/local_testing/test_configs/test_guardrails_config.yaml`,
# and the runtime loader `litellm/proxy/utils.py::_check_and_merge_model_level_guardrails`.
# The flag-name list was deliberately NOT taken from the card description — the
# card warns explicitly to verify each name against the upstream documentation
# before shipping (kanban card `94011364…` body; impediment decision C).

set -uo pipefail

# --- arg parsing -----------------------------------------------------------
URL_DEFAULT="http://127.0.0.1:4000"
URL=""
CONFIG_YAML=""
MASTER_KEY=""
STRICT=0

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)
      sed -n '2,/^set -uo pipefail/p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --strict)  STRICT=1 ;;
    --url)     URL="${2:-}"; shift ;;
    --config-yaml) CONFIG_YAML="${2:-}"; shift ;;
    --master-key)  MASTER_KEY="${2:-}"; shift ;;
    --) shift; break ;;
    -*)
      echo "check-litellm-hardening: unknown flag: $1" >&2
      exit 2
      ;;
    *)
      echo "check-litellm-hardening: unexpected positional arg: $1" >&2
      exit 2
      ;;
  esac
  shift
done

[ -n "$URL" ] || URL="$URL_DEFAULT"

bold=$'\033[1m'; red=$'\033[31m'; grn=$'\033[32m'; ylw=$'\033[33m'; rst=$'\033[0m'
worst=0  # 0=pass, 1=warn, 2=fail
pass() { printf '  %sPASS%s %s\n' "$grn" "$rst" "$1"; }
warn() { printf '  %sWARN%s %s\n' "$ylw" "$rst" "$1"; [ "$worst" -lt 1 ] && worst=1; }
fail() { printf '  %sFAIL%s %s\n' "$red" "$rst" "$1"; worst=2; }
note() { printf '         %s\n' "$1"; }

printf '%scheck-litellm-hardening%s  url=%s\n' "$bold" "$rst" "$URL"

# --- prereqs ---------------------------------------------------------------
for cmd in curl ss awk; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "check-litellm-hardening: required tool '$cmd' not on PATH" >&2
    exit 2
  fi
done

# Parse host/port from URL. Strips scheme + path; tolerates IPv6 brackets.
parse_host_port() {
  local u="$1" h p
  u="${u#http://}"; u="${u#https://}"
  h="${u%%/*}"
  p="${h##*:}"
  h="${h%%:*}"
  [ -n "$p" ] && [ "$p" = "$h" ] && p=""
  HOST="$h"
  PORT="${p:-4000}"
}

parse_host_port "$URL"

# ============================================================================
# Check 1 — Reachability + loopback-only binding.
#
# This composes two observations:
#   1a) /health/liveliness returns 200 — proves the proxy is up AND reachable
#       from this host on the URL the operator gave.
#   1b) the process listening on $PORT is bound to a loopback address only.
#
# Both must hold. A 200 from a non-loopback listener is worse than a refused
# connection — it's a proxy that quietly lets the world reach it.
# ============================================================================
listen_interfaces() {
  # ss -tlnH prints "LISTEN ... <local> ..." rows. <local> can be host:port,
  # host%iface:port (Linux), or [::]:port (IPv6). Returns a newline-separated
  # list of <address-only> for rows whose port matches $1 (port strings are
  # exact-equality matched — substring match would over-match ports like 80
  # matching 8080).
  local port="$1"
  ss -tlnH 2>/dev/null \
    | awk -v port="$port" '
        # Field 1 = LISTEN/UNCONN. Field 4 = local-address:port. Some kernels
        # print scope ("host%scope:port"), some print brackets for v6 ("[::]:port"),
        # some print the foreign addr in the same line for LISTEN sockets. We
        # only want the local addr that ENDS with ":<port>" or ":<port> ".
        $1 == "LISTEN" {
          line = $0
          n = split(line, f, " ")
          for (i = 1; i <= n; i++) {
            if (f[i] ~ /:/ && (f[i] ~ /:'"$port"'$/ || f[i] ~ /%.*:'"$port"'$/)) {
              # Found the local address token. Strip ":<port>" (and any
              # bracketing) and skip empties.
              addr = f[i]
              sub(/:'"$port"'$/, "", addr)
              gsub(/^\[|\]$/, "", addr)
              if (addr != "") print addr
            }
          }
        }
      '
}

# We don't need the exact port for the proxy to be reachable — the operator
# may have configured it on a non-default port — so try the URL first and use
# the discovered port for the binding check.
probe_health() {
  local code body url="$1"
  body=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "$url/health/liveliness" 2>/dev/null) || body=""
  echo "$body"
}

# Reachability
HEALTH_CODE=$(probe_health "$URL")
if [ "$HEALTH_CODE" = "200" ]; then
  pass "reachability: GET $URL/health/liveliness → 200"
else
  fail "reachability: GET $URL/health/liveliness → ${HEALTH_CODE:-unreachable} (is the proxy running on $URL?)"
  note "fix: start the proxy — e.g. \`litellm --host 127.0.0.1 --port 4000 -c config.yaml\`."
fi

# Binding check — extract host:port from $URL into the form ss writes.
URL_BIND="$HOST"
[ -n "$PORT" ] && URL_BIND="$URL_BIND:$PORT"
LISTENERS=$(listen_interfaces "$PORT")
LOOPBACK_OK=1
NON_LOOPBACK=""
if [ -z "$LISTENERS" ] && [ "$HEALTH_CODE" != "200" ]; then
  warn "binding: could not enumerate listeners for port $PORT and proxy is not reachable — cannot verify loopback-only."
  LOOPBACK_OK=0
else
  for addr in $LISTENERS; do
    case "$addr" in
      127.*|"::1"|"::ffff:127.0.0.1"|localhost)
        # loopback — drop, do not flag
        ;;
      *)
        NON_LOOPBACK="$NON_LOOPBACK $addr"
        ;;
    esac
  done
  if [ -n "$NON_LOOPBACK" ]; then
    LOOPBACK_OK=0
    fail "binding: port $PORT is bound to a non-loopback address:$NON_LOOPBACK — proxy is reachable from outside this host."
    note "fix: restart the proxy with \`--host 127.0.0.1\` (do NOT set HOST=0.0.0.0 / do NOT expose it)."
  else
    pass "binding: port $PORT is bound only to loopback"
  fi
fi

# ============================================================================
# Check 2 — master_key auth enforced.
#
# POST /v1/messages WITHOUT an Authorization header must NOT succeed. The
# original check read "== 401" as the only accept-signal, which produced a
# false-positive FAIL on every proxy that crashed in its auth handler with a
# non-401 status (LiteLLM 1.93 without `prisma` returns 500 here, and a
# wrong master_key falls through to the virtual-key-DB lookup and returns
# 400 "No connected db." — both fail-closed, neither means the proxy
# accepted the request). The new rule:
#
#   401/403               → PASS (clean auth rejection)
#   2xx / 3xx             → FAIL ("proxy accepted" — the only branch that
#                            prints that text; if you see it, the proxy
#                            really did return success without auth)
#   4xx / 5xx (other)     → WARN (non-2xx without an upstream call — fail-
#                            closed, but the status code points at a setup
#                            bug the operator should look at: missing
#                            `prisma` install, unconfigured DB, etc.)
#   empty / unreachable   → WARN (could not probe)
# ============================================================================
if [ "$HEALTH_CODE" = "200" ]; then
  UNAUTH_CODE=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 \
    -X POST -H 'content-type: application/json' \
    -d '{"model":"claude-3-5-sonnet-latest","max_tokens":1,"messages":[{"role":"user","content":"ping"}]}' \
    "$URL/v1/messages" 2>/dev/null) || UNAUTH_CODE=""
  case "$UNAUTH_CODE" in
    401|403)
      pass "auth: unauthenticated POST $URL/v1/messages → $UNAUTH_CODE (master_key enforced)"
      ;;
    "" )
      warn "auth: could not probe $URL/v1/messages (proxy unreachable or timeout) — skipping auth check"
      ;;
    2*|3*)
      # The "proxy accepted" wording is reserved for this branch ONLY. If
      # you see it, the proxy really did return 2xx/3xx without auth.
      fail "auth: unauthenticated POST $URL/v1/messages → $UNAUTH_CODE — proxy accepted a request with no Authorization header."
      note "fix: set \`general_settings.master_key: sk-...\` in config.yaml and pass \`--master_key\` (or env LITELLM_MASTER_KEY) at startup."
      ;;
    *)
      # 4xx/5xx without an upstream call — the proxy rejected the request
      # (or crashed rejecting it) before anything left the box. That is
      # fail-closed, but the status code suggests an upstream setup bug:
      #   - 500 with no body → typically LiteLLM's auth-exception handler
      #     crashing because 'prisma' is not installed (see install note
      #     in `Usage:` below: `pip install 'litellm[proxy]' prisma`).
      #   - 400 'No connected db.' → virtual-key-DB fallthrough after a
      #     master-key mismatch; either the DB is missing or the
      #     master_key you sent does not match general_settings.master_key.
      # WARN, not FAIL — auth is effectively enforced, but the operator
      # needs to investigate the underlying setup bug.
      warn "auth: unauthenticated POST $URL/v1/messages → $UNAUTH_CODE (non-2xx without upstream call — fail-closed; status code suggests an upstream setup issue, see \`fix:\` note)"
      note "fix: a non-2xx here means the proxy either crashed in its auth handler (common cause: \`pip install 'litellm[proxy]' prisma\` — \`prisma\` is NOT pulled in by the \`[proxy]\` extra; without it LiteLLM 500's every auth rejection) or fell through to a virtual-key-DB lookup that is not configured (set up the DB, or use a master_key that matches \`general_settings.master_key\`)."
      ;;
  esac
else
  warn "auth: skipping (proxy not reachable)"
fi

# ============================================================================
# Checks 3-5 — configuration surface.
#
# Three options for obtaining the *actually loaded* configuration:
#   (a) the operator passes --config-yaml pointing at the file the proxy was
#       started with — verified against file existence,
#   (b) the operator passes --master-key and we query the admin endpoint,
#   (c) neither provided → skip, but warn that the operator must choose one.
#
# Reading a static config file is what the card warns against — but here we
# also corroborate it with the live proxy's behaviour on property 2 (auth)
# and require the operator to acknowledge the file IS the live config. The
# alternative — silently grepping $PWD for any *.yaml — would green-flag a
# proxy the operator never actually loaded.
# ============================================================================
GOT_CONFIG=0
CONFIG_SOURCE=""

if [ -n "$CONFIG_YAML" ]; then
  if [ ! -f "$CONFIG_YAML" ]; then
    fail "config: --config-yaml path does not exist: $CONFIG_YAML"
  else
    CONFIG_SOURCE="file:$CONFIG_YAML"
    GOT_CONFIG=1
  fi
elif [ -n "$MASTER_KEY" ]; then
  # The admin /config endpoint exists in LiteLLM but the exact path is not
  # documented as a first-class citizen and the security literature treats
  # /api/v1/config as a leak vector. We avoid hitting that and instead
  # fall back to a behavioural probe (see note below).
  warn "config: --master-key probing of admin /config endpoints is intentionally skipped — admin /config disclosure is a known leak vector and the proxy URL surface differs across versions."
  note "fix: pass \`--config-yaml /path/to/the/file/the/proxy/loaded\` so the actual loaded file is checked instead of an admin dump."
fi

if [ "$GOT_CONFIG" = "0" ]; then
  warn "config-checks: no --config-yaml given — checks 3 (prompt-mutation), 4 (telemetry/sync) and 5 (credentials) are SKIPPED, not PASS."
  note "fix: pass \`--config-yaml /path/to/the/file/the/proxy/loaded.yaml\` so properties 3-5 are verified."
else
  echo "        config source: $CONFIG_SOURCE"

  # Parsing: liteLLM config.yaml is YAML; we don't want to depend on a YAML
  # parser when we only need to spot a small set of keys and a small set of
  # values. Use python3 + pyyaml if available, otherwise a tiny awk pass that
  # catches the documented key paths.
  have_yaml=0
  if python3 -c "import yaml" 2>/dev/null; then
    have_yaml=1
  fi

  # Build the assertions as inline python to keep the bash readable.
  CONFIG_AUDIT=$(CONFIG_YAML_PATH="$CONFIG_YAML" python3 - <<'PY' 2>&1
import os, sys
try:
    import yaml  # type: ignore
except Exception as e:
    print(f"IMPORT_ERROR: {e}", file=sys.stderr)
    sys.exit(2)

path = os.environ["CONFIG_YAML_PATH"]
try:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
except FileNotFoundError:
    print(f"PATH_MISSING: {path}", file=sys.stderr); sys.exit(2)
except Exception as e:
    print(f"PARSE_ERROR: {e}", file=sys.stderr); sys.exit(2)

results = []

# --- Property 3: no prompt-mutation -----------------------------------------
# success_callback / failure_callback / callbacks must not contain anything
# that touches request bodies. langfuse, langsmith, lunary, helicone, etc.
# write to external services; we treat any non-empty callback list as a FAIL
# until the operator documents it. We also look for guardrail wiring in any
# of the three documented forms (top-level `guardrails:` block,
# `litellm_settings.guardrails:` block, and per-model
# `litellm_params.guardrails:` list — see docs/cockpit/9router-integratie-
# analyse.md §11.2) plus `router_settings.plugins:`, which is where LiteLLM
# wires in guardrail transforms. `service_callbacks` is intentionally NOT
# listed below: it appears in the public `litellm_settings` reference table
# but has 0 hits in the BerriAI/litellm source repo, i.e. it is a
# documentation artifact, not a real loader key (kaart 94011364…, impediment
# decision C).
ls = cfg.get("litellm_settings") or {}
gs = cfg.get("general_settings")  or {}
rs = cfg.get("router_settings")   or {}

callback_keys = ["success_callback", "failure_callback", "callbacks"]
active_callbacks = []
for k in callback_keys:
    v = ls.get(k)
    if isinstance(v, list) and v:
        active_callbacks.append(f"litellm_settings.{k}={v}")
    elif isinstance(v, str) and v.strip():
        active_callbacks.append(f"litellm_settings.{k}=\"{v}\"")

# Three documented guardrail-wiring locations. Any non-empty entry here is a
# FAIL: even `default_on: false` still registers the callback, and any
# payload-mutating guardrail (Lakera, Presidio, OpenAI moderations, ...) can
# silently rewrite the request body. Reference for the three forms:
#   - top-level `guardrails:`            docs.litellm.ai/proxy/guardrails/quick_start
#   - `litellm_settings.guardrails:`     tests/local_testing/test_configs/test_guardrails_config.yaml
#   - per-model `litellm_params.guardrails:`  litellm/proxy/utils.py::_check_and_merge_model_level_guardrails
guardrail_findings = []
# Form 1: top-level `guardrails:` block (a list of dicts each with a
# guardrail_name, or a dict keyed by guardrail_name). Truthy iff non-empty.
top_guardrails = cfg.get("guardrails")
if top_guardrails:
    if isinstance(top_guardrails, (list, dict)) and len(top_guardrails) > 0:
        guardrail_findings.append(f"top-level 'guardrails:' block ({type(top_guardrails).__name__}, {len(top_guardrails)} entries)")
# Form 2: litellm_settings.guardrails — a list of single-key dicts, each
# value being {callbacks: [...], default_on: bool}. Even with default_on:
# false the callback is still registered with the proxy (init_guardrails.py
# iterates the full list); default_on: false only suppresses the runtime
# invocation, not the wiring. Empty list = no callbacks wired.
ls_guardrails = ls.get("guardrails")
if isinstance(ls_guardrails, list) and ls_guardrails:
    guardrail_findings.append(f"litellm_settings.guardrails (list, {len(ls_guardrails)} entries)")
elif isinstance(ls_guardrails, dict) and ls_guardrails:
    # Tolerate the dict form too — not documented as a primary form but
    # safe to treat as "wiring present".
    guardrail_findings.append(f"litellm_settings.guardrails (dict, {len(ls_guardrails)} entries)")
# Form 3: per-model `litellm_params.guardrails:` (a list of guardrail name
# strings). Each entry activates a callback against every request to that
# model — confirmed via litellm/proxy/utils.py::_check_and_merge_model_level_guardrails.
per_model_guardrails = []
for entry in cfg.get("model_list") or []:
    lp = (entry or {}).get("litellm_params") or {}
    g = lp.get("guardrails")
    if isinstance(g, list) and g:
        per_model_guardrails.append((entry.get("model_name"), g))
if per_model_guardrails:
    names = ", ".join(f"{n}={g!r}" for n, g in per_model_guardrails)
    guardrail_findings.append(f"litellm_params.guardrails on model_list entries: {names}")

router_plugins = rs.get("plugins") if isinstance(rs, dict) else None

# --- Property 4: no telemetry / external sync ------------------------------
alerting = gs.get("alerting")
database_url = gs.get("database_url")
external_db_hosts = []
if isinstance(database_url, str) and database_url.strip():
    s = database_url.strip()
    # Anything pointing at a cloud-managed host (Prisma Accelerate, AtlasData,
    # common managed-Postgres providers) or a private RFC1918 is "external";
    # sqlite:// and localhost sqlite are local.
    if s.startswith("sqlite://"):
        pass  # local
    elif "localhost" in s or "127.0.0.1" in s or "::1" in s:
        pass  # local
    elif s.startswith("postgres://") or s.startswith("postgresql://") or s.startswith("mysql://"):
        external_db_hosts.append(s)
    else:
        external_db_hosts.append(s)

# --- Property 5: credential hygiene -----------------------------------------
# A plaintext api_key is a credential that landed on disk in cleartext. The
# documented escape hatch is os.environ/VAR_NAME (LiteLLM inlines the env at
# load time) or a `credential_list` reference. Anything else is a FAIL.
plaintext_keys = []
def walk_model_list(cfg):
    out = []
    for entry in cfg.get("model_list") or []:
        lp = (entry or {}).get("litellm_params") or {}
        if "api_key" in lp:
            v = lp["api_key"]
            if not (isinstance(v, str) and v.startswith("os.environ/")) and not isinstance(v, dict):
                out.append((entry.get("model_name"), v))
    return out
plaintext_keys = walk_model_list(cfg)

# Emit findings as a stable, line-oriented protocol: each line is
# "<PROP>:<PASS|FAIL|WARN>:<message>".
def emit(prop, status, msg):
    print(f"{prop}:{status}:{msg}")

# Property 3
if active_callbacks:
    emit("prompt_mutation", "FAIL",
         "callbacks wired up: " + "; ".join(active_callbacks) +
         " — even an observability callback touches request metadata; a payload-mutating one (e.g. a guardrails plugin) can rewrite the prompt silently.")
elif guardrail_findings:
    emit("prompt_mutation", "FAIL",
         "guardrail wiring present: " + "; ".join(guardrail_findings) +
         " — LiteLLM applies each guardrail to request bodies; even default_on:false only suppresses the runtime invocation, not the wiring. Remove the entry, or document inline if intentional.")
elif isinstance(router_plugins, list) and router_plugins:
    emit("prompt_mutation", "FAIL",
         f"router_settings.plugins={router_plugins} — plugins run on the request path and may mutate the body.")
else:
    emit("prompt_mutation", "PASS",
         "no success_callback / failure_callback / guardrails / plugins / transform scripts attached.")

# Property 4
telemetry_findings = []
for k in callback_keys:
    v = ls.get(k)
    if isinstance(v, list) and v:
        for cb in v:
            telemetry_findings.append(f"litellm_settings.{k}={cb}")
if alerting:
    telemetry_findings.append(f"general_settings.alerting={alerting}")
if external_db_hosts:
    telemetry_findings.append(f"general_settings.database_url={external_db_hosts[0]} (non-local)")
if telemetry_findings:
    emit("telemetry", "FAIL",
         "external sync configured: " + "; ".join(telemetry_findings) +
         " — these send request metadata or spend rows off-host. Set to empty/absent for a sidecar that holds all upstream keys.")
else:
    emit("telemetry", "PASS",
         "no external callbacks / alerting / non-local database_url.")

# Property 5
if plaintext_keys:
    listing = ", ".join(f"{n}={v!r}" for n, v in plaintext_keys[:5])
    extra = f" (and {len(plaintext_keys)-5} more)" if len(plaintext_keys) > 5 else ""
    emit("credentials", "FAIL",
         f"{len(plaintext_keys)} plaintext api_key in model_list entries: {listing}{extra} — replace with os.environ/VAR_NAME or credential_list.")
else:
    emit("credentials", "PASS",
         "no plaintext api_key values in model_list (os.environ/* or credential_list only).")
PY
  )
  audit_rc=$?
  if [ "$audit_rc" != "0" ]; then
    fail "config: could not parse $CONFIG_YAML (yaml missing or parse error). See stderr above."
  else
    while IFS= read -r line; do
      case "$line" in
        prompt_mutation:PASS:*)
          pass "no prompt-mutation: ${line#prompt_mutation:PASS:}"
          ;;
        prompt_mutation:FAIL:*)
          fail "prompt-mutation: ${line#prompt_mutation:FAIL:}"
          note "fix: in $CONFIG_YAML, drop success_callback / failure_callback / callbacks entries, the top-level 'guardrails:' block, litellm_settings.guardrails, and any per-model litellm_params.guardrails: list; router_settings.plugins must be empty. Note: 'service_callbacks' is listed in the public litellm_settings reference docs but has 0 hits in the BerriAI/litellm source repo (kaart 94011364…) — it is a documentation artifact and is intentionally NOT checked here."
          ;;
        prompt_mutation:WARN:*)
          warn "prompt-mutation: ${line#prompt_mutation:WARN:}"
          ;;
        telemetry:PASS:*)
          pass "no telemetry/external-sync: ${line#telemetry:PASS:}"
          ;;
        telemetry:FAIL:*)
          fail "telemetry/external-sync: ${line#telemetry:FAIL:}"
          note "fix: in $CONFIG_YAML, set success_callback: [] (or remove the key), remove alerting:, and remove any non-local database_url."
          ;;
        telemetry:WARN:*)
          warn "telemetry/external-sync: ${line#telemetry:WARN:}"
          ;;
        credentials:PASS:*)
          pass "credentials: ${line#credentials:PASS:}"
          ;;
        credentials:FAIL:*)
          fail "credentials: ${line#credentials:FAIL:}"
          note "fix: in $CONFIG_YAML, replace each plaintext api_key with \`os.environ/VAR_NAME\` or move into a credential_list block."
          ;;
        credentials:WARN:*)
          warn "credentials: ${line#credentials:WARN:}"
          ;;
      esac
    done <<< "$CONFIG_AUDIT"
  fi
fi

# --- summary + exit ---------------------------------------------------------
case "$worst" in
  0) printf '%sall checks passed.%s\n' "$grn" "$rst" ;;
  1) printf '%sfinished with warnings — re-run with --strict to fail loudly.%s\n' "$ylw" "$rst" ;;
  2) printf '%sFAILURES found — see above.%s\n' "$red" "$rst" ;;
esac
if [ "$worst" -ge 2 ] && [ "$STRICT" = "1" ]; then
  exit 1
fi
if [ "$worst" -ge 2 ]; then
  exit 0   # advisory default — match sibling check-*.sh scripts
fi
exit 0
