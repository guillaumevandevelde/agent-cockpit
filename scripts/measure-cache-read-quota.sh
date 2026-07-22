#!/usr/bin/env bash
# Meet of `cache_read` meetelt in het Claude-abonnementsquotum.
#
# Achtergrond: `token-saver-mechanismen-decision.md` §2 laat de opbrengst van
# het hele token-saver-spoor afhangen van één boekhoudvraag — telt
# `cache_read_input_tokens` mee in het 5h-abonnementsvenster? `cache_read` is
# 95,9% van al het verbruik, dus het antwoord keert de rangorde van de
# mechanismen om (RTK vs. Caveman zijn anti-gecorreleerd).
#
# Methode (zie docs/cockpit/cache-read-quota-decision.md):
#   1. `five_hour.utilization` van Anthropic's eigen OAuth-usage-endpoint is de
#      autoritatieve teller — dezelfde data die `/usage` in een sessie toont.
#      Cockpit's eigen `subscription_pool`-signaal kán deze vraag NIET
#      beantwoorden: `AnthropicUsageProvider` telt zelf `cache_read` bij het
#      totaal op, dus dat signaal is circulair.
#   2. De lokale JSONL-transcripts (`~/.claude/projects/**/*.jsonl`) geven het
#      exacte token-verbruik per bucket per turn, met timestamps.
#   3. Per interval tussen twee utilization-metingen fitten we
#        du% = k * (input + 2*cache_creation + 5*output + w*cache_read)
#      De niet-gecachte gewichten liggen vast op de geverifieerde prijs-ratio's
#      (zie `verify-pricing`); de enige vrije vormparameter is `w`.
#        w ~ 0   -> cache_read telt NIET mee
#        w ~ 0.1 -> cache_read telt mee op zijn 10%-prijsgewicht
#        w ~ 1   -> cache_read telt mee als gewone input-tokens
#
# Subcommando's:
#   sample <out.ndjson> [interval_s] [count]  — log utilization naar NDJSON
#   amplify <session_id> <n>                  — injecteer N turns pure cache_read
#   fit <trace.ndjson>                        — fit w over de intervallen
#   verify-pricing                            — reconstrueer costUSD uit buckets
#
# LET OP — twee valkuilen die deze meting zelf heeft geraakt:
#   * Het usage-endpoint heeft een EIGEN rate limit. Poll niet vaker dan ~1/min;
#     een tightere loop levert 429's op die minutenlang doorwerken.
#   * De meting verbruikt gedeeld quotum op een box waar concurrente sessies
#     draaien. Check headroom vóór je `amplify` draait.
set -uo pipefail

USAGE_ENDPOINT="https://api.anthropic.com/api/oauth/usage"
CREDS="$HOME/.claude/.credentials.json"

_token() {
  python3 -c "import json,os;print(json.load(open(os.path.expanduser('$CREDS')))['claudeAiOauth']['accessToken'])"
}

_utilization() {
  curl -s --max-time 20 "$USAGE_ENDPOINT" \
    -H "Authorization: Bearer $(_token)" \
    -H "anthropic-beta: oauth-2025-04-20" \
  | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    print(d['five_hour']['utilization'] if 'five_hour' in d else 'ERR')
except Exception:
    print('ERR')
"
}

cmd_sample() {
  local out="${1:?usage: sample <out.ndjson> [interval_s] [count]}"
  local interval="${2:-90}" count="${3:-40}"
  [ "$interval" -lt 60 ] && echo "WARN: interval <60s riskeert een 429 op het usage-endpoint" >&2
  for _ in $(seq 1 "$count"); do
    local ts v
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    v=$(_utilization)
    if [ "$v" != "ERR" ]; then
      echo "{\"ts\": \"$ts\", \"five_hour\": $v}" >> "$out"
      echo "$ts util=$v"
    else
      echo "$ts util=ERR (rate limited?)" >&2
    fi
    sleep "$interval"
  done
}

# Injecteer N turns die vrijwel uitsluitend cache_read verbruiken.
# Werkt alleen via `--resume`: losse `claude -p`-calls cachen de user-prompt
# NIET (gemeten: herhaalde identieke -p-calls blijven op cr~9k steken), een
# hervatte sessie herleest wél de volledige geachede conversatie-prefix.
cmd_amplify() {
  local sid="${1:?usage: amplify <session_id> <n>}" n="${2:-10}"
  local tmp; tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  echo "PRE  $(date -u +%Y-%m-%dT%H:%M:%SZ) util=$(_utilization)"
  for i in $(seq 1 "$n"); do
    timeout 200 claude -p --output-format json --model claude-opus-4-8 \
      --allowedTools "" --resume "$sid" "Reply with exactly one word: OK" \
      > "$tmp/r$i.json" 2>/dev/null || echo "call $i failed" >&2
  done
  python3 -c "
import json,glob,sys
cr=non=0
for f in glob.glob('$tmp/r*.json'):
    try: u=json.load(open(f))['usage']
    except Exception: continue
    cr  += u['cache_read_input_tokens']
    non += u['input_tokens'] + 2*u['cache_creation_input_tokens'] + 5*u['output_tokens']
print(f'INJECTED cache_read={cr:,}  weighted nonCR={non:,}  ratio={cr/max(non,1):.0f}:1')
"
  echo "POST $(date -u +%Y-%m-%dT%H:%M:%SZ) util=$(_utilization)"
}

cmd_verify_pricing() {
  python3 - <<'PY'
# Opus 4.8 op een 1h-TTL prompt-cache, afgeleid uit een echte `claude -p`-run
# waarvan Claude Code zelf de costUSD rapporteerde. Exacte match tot 7 cijfers
# bevestigt de tabel -> cache_read kost 10% van input, cache-write (1h) 200%.
PRICES = dict(input=5e-6, output=25e-6, cache_creation=10e-6, cache_read=0.5e-6)
obs = dict(cache_creation=96435, cache_read=7293, input=2, output=4)
reported = 0.9681065
got = sum(PRICES[k]*v for k, v in obs.items())
print(f"buckets: {obs}")
print(f"reconstructed costUSD = {got:.7f}   reported = {reported:.7f}")
print("MATCH" if abs(got-reported) < 1e-6 else "MISMATCH")
print("=> cache_read = 0.1x input price; cache_creation (1h TTL) = 2x input price")
PY
}

cmd_fit() {
  local trace="${1:?usage: fit <trace.ndjson>}"
  MCRQ_TRACE="$trace" python3 - <<'PY'
import json, os, glob, sys
from datetime import datetime

def parse_ts(s):
    if not s: return None
    try: return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError: return None

def ledger(t0, t1):
    """Anthropic-only turns in (t0, t1]. Dedupes on (message.id, requestId):
    Claude Code appends the same assistant message to several JSONL rows on
    resume/compact. Non-Anthropic models (e.g. MiniMax) are excluded — they
    do not touch this quota at all."""
    rows, seen = [], set()
    for path in glob.glob(os.path.expanduser('~/.claude/projects/**/*.jsonl'), recursive=True):
        try: fh = open(path, errors='replace')
        except OSError: continue
        with fh:
            for line in fh:
                if '"usage"' not in line: continue
                try: rec = json.loads(line)
                except Exception: continue
                msg = rec.get('message') or {}
                u = msg.get('usage')
                if not isinstance(u, dict): continue
                if not str(msg.get('model') or '').startswith('claude'): continue
                ts = parse_ts(rec.get('timestamp'))
                if ts is None or not (t0 < ts <= t1): continue
                key = (msg.get('id'), rec.get('requestId'))
                if key != (None, None):
                    if key in seen: continue
                    seen.add(key)
                rows.append((u.get('input_tokens', 0) or 0,
                             u.get('cache_creation_input_tokens', 0) or 0,
                             u.get('cache_read_input_tokens', 0) or 0,
                             u.get('output_tokens', 0) or 0))
    return rows

obs = []
for line in open(os.environ['MCRQ_TRACE']):
    line = line.strip()
    if not line: continue
    try: r = json.loads(line)
    except Exception: continue
    if 'five_hour' in r: obs.append((parse_ts(r['ts']), float(r['five_hour'])))
obs = sorted(set(obs))
if len(obs) < 2:
    sys.exit("need >=2 utilization samples")

pts = []
for (t0, u0), (t1, u1) in zip(obs, obs[1:]):
    # Skip sub-minute intervals: the server-side counter lags the local JSONL
    # write, so short intervals misattribute tokens across their boundary.
    if (t1 - t0).total_seconds() < 60 or u1 < u0: continue
    rows = ledger(t0, t1)
    non = sum(i + 2*cc + 5*o for i, cc, _, o in rows)
    cr = sum(c for _, _, c, _ in rows)
    if non or cr: pts.append((non, cr, u1 - u0, t0, t1))

if not pts: sys.exit("no usable intervals")
print(f"{'interval':>17} {'du%':>5} {'nonCR(w)':>11} {'cache_read':>12} {'CR%':>7}")
for non, cr, du, t0, t1 in pts:
    print(f"{t0:%H:%M:%S}->{t1:%H:%M:%S} {du:5.1f} {non:11,} {cr:12,} "
          f"{cr/(cr+non)*100:6.1f}%")

def fit(w):
    den = sum((p[0] + w*p[1])**2 for p in pts)
    if not den: return None, float('inf')
    k = sum(p[2]*(p[0] + w*p[1]) for p in pts) / den
    return k, sum((p[2] - k*(p[0] + w*p[1]))**2 for p in pts)

print(f"\n{'w':>6} {'SSE':>10}   interpretation")
for w, label in ((0.0, 'cache_read is free'), (0.1, 'cost-weighted (10%)'),
                 (0.5, 'half weight'), (1.0, 'counts as plain input')):
    print(f"{w:6.2f} {fit(w)[1]:10.2f}   {label}")

best = min(((i/1000.0, fit(i/1000.0)[1]) for i in range(0, 1501)), key=lambda x: x[1])
k, _ = fit(best[0])
print(f"\nBEST FIT w = {best[0]:.3f}  (SSE {best[1]:.2f})  over n={len(pts)} intervals")
print(f"implied 5h window = {1/k*100:,.0f} weighted non-cached tokens" if k else "")
print("=> cache_read does NOT count" if best[0] < 0.05 else
      "=> cache_read carries measurable weight")
PY
}

case "${1:-}" in
  sample)         shift; cmd_sample "$@" ;;
  amplify)        shift; cmd_amplify "$@" ;;
  fit)            shift; cmd_fit "$@" ;;
  verify-pricing) shift; cmd_verify_pricing "$@" ;;
  *) sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
