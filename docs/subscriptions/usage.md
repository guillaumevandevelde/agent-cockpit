# Subscriptions — usage card

Each provider card on `/#/subscriptions` shows what the system knows
about that subscription's remaining quota. Two providers render today:

## Anthropic

Shows a **5h rate** and a **Weekly** row, computed from local Claude Code
JSONL (via `UsageService.identify_session_blocks` + `UsageService.aggregate_weekly`).
You pick your plan tier (Pro / Max 5x / Max 20x / Team) from a dropdown
so we know the denominator.

**Honest about what we don't know:** Anthropic does not publish a
public usage API for Pro/Max and does not publish weekly token limits
for any tier. The card therefore shows `limit not published by Anthropic`
for the Weekly row. Verify the 5h number against
[Anthropic's plan docs](https://www.anthropic.com/pricing) before
trusting the percentages — the constant table is re-verified at each
implementation, but limits drift.

## MiniMax

Shows whatever the MiniMax API exposes — see the implementation probe
commit message in this branch's history for the exact endpoint(s) we
hit and the response shape we map. If the probe found nothing usable,
the card ships with a `no_endpoint` empty state rather than fabricate.

## Errors

| Provider state | Card shows |
|---|---|
| MiniMax API key not set | MiniMax credentials form + "Set your API key to see usage" |
| MiniMax rejected the key | Red error badge: "MiniMax rejected the API key" |
| MiniMax unreachable / 5xx | Red error badge with the HTTP status |
| MiniMax returned non-JSON | Red error badge: "MiniMax returned an unexpected response" |
| MiniMax has no usage endpoint | Red error badge: "MiniMax does not expose usage data" |
| Anthropic plan not picked | Plan dropdown + "Pick your plan to see 5h/weekly leftover" |
| Backend itself unreachable | Page-level error in the card chrome |