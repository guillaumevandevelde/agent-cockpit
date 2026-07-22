#!/usr/bin/env bash
# Bash-tests voor scripts/measure-cache-read-quota.sh
#
# Getest wordt wat zonder netwerk en zonder live quotum deterministisch kan:
# de prijs-reconstructie (pure rekensom) en het CLI-oppervlak. De `sample`- en
# `amplify`-subcommando's raken het live usage-endpoint en het gedeelde
# abonnementsquotum en worden hier bewust NIET gedraaid.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/measure-cache-read-quota.sh"
PASS=0; FAIL=0
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

ok()   { PASS=$((PASS+1)); echo "  ok   - $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL - $1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (expected '$3', got '$2')"; fi; }

echo "== measure-cache-read-quota.sh =="

[ -x "$SCRIPT" ] && ok "script is executable" || bad "script is executable"

# --- verify-pricing: geverifieerde prijstabel reproduceert de gerapporteerde costUSD
out="$("$SCRIPT" verify-pricing 2>&1)"
case "$out" in
  *MATCH*) ok "verify-pricing reconstructs reported costUSD exactly" ;;
  *)       bad "verify-pricing reconstructs reported costUSD exactly" ;;
esac
case "$out" in
  *"0.1x input price"*) ok "verify-pricing states the cache_read price ratio" ;;
  *)                    bad "verify-pricing states the cache_read price ratio" ;;
esac

# --- geen subcommando -> usage + exit 1
"$SCRIPT" >/dev/null 2>&1; check "bare invocation exits 1" "$?" "1"
"$SCRIPT" bogus-subcommand >/dev/null 2>&1; check "unknown subcommand exits 1" "$?" "1"

# --- fit: te weinig samples -> nette fout, geen traceback
printf '%s\n' '{"ts": "2026-07-21T20:00:00Z", "five_hour": 10.0}' > "$TMP/one.ndjson"
out="$("$SCRIPT" fit "$TMP/one.ndjson" 2>&1)"; rc=$?
check "fit with a single sample exits non-zero" "$([ $rc -ne 0 ] && echo yes || echo no)" "yes"
case "$out" in
  *Traceback*) bad "fit with a single sample fails cleanly (no traceback)" ;;
  *)           ok  "fit with a single sample fails cleanly (no traceback)" ;;
esac

# --- fit: malformed regels worden overgeslagen i.p.v. te crashen
printf '%s\n%s\n%s\n' \
  'not json at all' \
  '{"ts": "2026-07-21T20:00:00Z", "five_hour": 10.0}' \
  '{"ts": "2026-07-21T20:10:00Z", "five_hour": 12.0}' > "$TMP/dirty.ndjson"
out="$("$SCRIPT" fit "$TMP/dirty.ndjson" 2>&1)"
case "$out" in
  *Traceback*) bad "fit skips malformed NDJSON lines" ;;
  *)           ok  "fit skips malformed NDJSON lines" ;;
esac

# --- fit: monotoon dalende utilization (venster-reset) levert geen negatief interval
printf '%s\n%s\n' \
  '{"ts": "2026-07-21T20:00:00Z", "five_hour": 90.0}' \
  '{"ts": "2026-07-21T20:10:00Z", "five_hour": 3.0}' > "$TMP/reset.ndjson"
out="$("$SCRIPT" fit "$TMP/reset.ndjson" 2>&1)"
case "$out" in
  *Traceback*) bad "fit tolerates a window reset (util drop)" ;;
  *)           ok  "fit tolerates a window reset (util drop)" ;;
esac

echo
echo "passed=$PASS failed=$FAIL"
[ "$FAIL" -eq 0 ]
