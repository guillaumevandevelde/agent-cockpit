"""One-shot probe of the MiniMax usage/balance endpoint(s).

Reads `MINIMAX_API_KEY` and `MINIMAX_BASE_URL` from environment (default
base URL: https://api.minimax.io/anthropic). Tries a small set of candidate
endpoint paths, prints whatever the server returns for each. The output of
this probe feeds the implementation of `MinimaxUsageProvider`.

This is a manual, out-of-band probe — NOT a pytest test. Run with:
    MINIMAX_API_KEY=sk-... python scripts/probe_minimax_usage.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api.minimax.io/anthropic"

# Candidate endpoints to probe (extend if these miss).
CANDIDATES = [
    ("GET", "/v1/usage"),
    ("GET", "/v1/account/usage"),
    ("GET", "/v1/account/balance"),
    ("GET", "/v1/billing/usage"),
    ("GET", "/v1/quota"),
    ("GET", "/usage"),
    ("GET", "/account/usage"),
]


def _probe(method: str, url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace") if e.fp else ""
    except urllib.error.URLError as e:
        return 0, f"URLError: {e.reason}"


api_key = os.environ.get("MINIMAX_API_KEY")
if not api_key:
    print("ERROR: MINIMAX_API_KEY not set", file=sys.stderr)
    sys.exit(1)

base_url = os.environ.get("MINIMAX_BASE_URL", DEFAULT_BASE).rstrip("/")

for method, path in CANDIDATES:
    url = base_url + path
    print(f"\n--- {method} {url} ---")
    status, body = _probe(method, url)
    print(f"Status: {status}")
    # Truncate to keep the probe output bounded.
    print(body[:2000])
