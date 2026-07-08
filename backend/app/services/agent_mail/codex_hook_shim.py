"""Standalone script invoked by Codex CLI's hooks.json (needs a real argv,
not a shell one-liner, unlike Claude Code's command hooks). Reads the hook
JSON payload from stdin, POSTs it to Cockpit's Agent Mail hook endpoint, and
prints the JSON response verbatim so Codex can consume it as hook output.
Never raises — a Cockpit outage must not block the CLI.

Standalone on purpose: no `app.*` imports, so it runs correctly even when
invoked outside this repo's Python environment.
"""
import argparse
import json
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cockpit-url", required=True)
    parser.add_argument("--provider", default="codex-cli")
    parser.add_argument("--event", required=True)
    args = parser.parse_args()

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    payload.setdefault("provider", args.provider)

    url = f"{args.cockpit_url}/api/v1/agent-mail/hooks/{args.event}"
    try:
        response = httpx.post(url, json=payload, timeout=httpx.Timeout(connect=0.25, read=1.0, write=1.0, pool=0.25))
        if response.status_code < 400:
            sys.stdout.write(response.text)
    except httpx.HTTPError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
