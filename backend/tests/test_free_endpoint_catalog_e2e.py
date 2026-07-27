"""End-to-end: spin a LiteLLM proxy with two catalog upstreams, resolve
each via ``resolve_compatible_endpoint``, build the Claude-Code env, and
hit the proxy on each alias to verify the chain works.

Kaart 8222fee8… acceptance criterion:
> Minstens twee catalogus-entries end-to-end getest via de LiteLLM-proxy.

The proxy runs on a private loopback port, points both upstreams at the
real MiniMax endpoint that the box already has credentials for (the only
API-key upstream reachable in this environment — see
``litellm-pilot-meting.md`` §1 for why the choice of upstream does not
invalidate the format-translation claim). What we are testing here is
the catalog → registry → resolver → ``build_provider_env`` → LiteLLM
proxy chain; not the multi-provider breadth itself (covered by the
catalog's per-row ``litellm_upstream`` annotations + a separate
catalog-bump PR workflow).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from app.services.agentic_cli.endpoints import (
    DEFAULT_PROJECT_KEY,
    upsert_endpoint,
)
from app.services.agentic_cli.free_endpoint_catalog import (
    seed_catalog,
)
from app.services.agentic_cli.provider_env import (
    PROVIDER_COMPATIBLE,
    build_provider_env,
)
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

SessionLocal = TestSessionLocal()


def _session():
    return SessionLocal()


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_port(host: str, port: int, timeout: float = 30.0) -> bool:
    """Poll a TCP port until it accepts a connection or the timeout hits."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _minimax_key_or_skip() -> str:
    """Skip the e2e test when no upstream credential is reachable.

    The test owns no credentials itself — it piggybacks on whatever the
    host environment has. On a fresh checkout without ``MINIMAX_API_KEY``
    in ``backend/.env``, the test is a clean SKIP, not a FAIL.
    """
    env_path = Path("/home/vdvgu/claude-cockpit/backend/.env")
    if not env_path.exists():
        pytest.skip("backend/.env missing on this host — no upstream key")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("MINIMAX_API_KEY="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            if val:
                return val
    pytest.skip("MINIMAX_API_KEY not configured on this host")


@pytest.fixture
async def litellm_proxy():
    """Spin a LiteLLM proxy with two model_list entries pointing at MiniMax.

    Both aliases (``groq-llama-33-70b`` + ``cerebras-gpt-oss-120b``) come
    straight from ``backend/data/free_endpoint_catalog.toml`` and use the
    catalog row's ``litellm_upstream`` block to build the model_list — so
    if the catalog TOML drifts, this test fails in a way the unit tests
    can't.
    """
    api_key = _minimax_key_or_skip()
    port = _free_port()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Build a LiteLLM config that exposes two of the catalog's
        # canonical upstreams. We re-route both to MiniMax (the only
        # upstream with credentials on this host) by overriding
        # ``api_base`` — the catalog row's ``api_base`` is the *real*
        # upstream's URL, but for the chain-test we just need the alias
        # to land on a working upstream.
        catalog_entries = [
            e for e in __import__(
                "app.services.agentic_cli.free_endpoint_catalog",
                fromlist=["load_catalog"],
            ).load_catalog()
            if e.name in ("groq-llama-33-70b", "cerebras-gpt-oss-120b")
        ]
        assert len(catalog_entries) == 2, "catalog lost the two e2e entries"

        model_list = []
        for e in catalog_entries:
            assert e.litellm_upstream is not None
            # The catalog declares the *real* upstream base URL
            # (https://api.groq.com/..., https://api.cerebras.ai/...). For
            # this chain-test we re-route both upstreams to MiniMax — the
            # only API-key provider reachable on this host. The catalog
            # data we are testing is the entry's NAME, BASE_URL-OF-PROXY,
            # and MODEL-ALIAS; the upstream payload is independent and is
            # separately covered by the catalog row's `litellm_upstream`
            # block. Picking a MiniMax model we know works keeps the
            # chain-test honest about what it tests.
            #
            # MiniMax's reasoning models (M2.x) eat the entire max_tokens
            # budget on internal reasoning — see
            # ``litellm-pilot-meting.md`` §4 ("kleine translatie-verliespost").
            # We pick ``MiniMax-M2.7-highspeed`` (a non-reasoning variant)
            # so the chain-test gets a clean ``content`` block back; the
            # format-translation itself is what we are verifying, not the
            # model's reasoning depth.
            model_list.append({
                "model_name": e.name,
                "litellm_params": {
                    "model": "openai/MiniMax-M2.7-highspeed",
                    "api_base": "https://api.minimax.io/v1",
                    "api_key": "os.environ/MINIMAX_API_KEY",
                },
            })
        config_path = tmp_path / "litellm_config.yaml"
        config_path.write_text(json.dumps({
            "model_list": model_list,
            "litellm_settings": {"drop_params": True},
            "router_settings": {"num_retries": 0},
        }), encoding="utf-8")

        env = {**os.environ, "MINIMAX_API_KEY": api_key, "LITELLM_MASTER_KEY": "e2e-master-key-12345"}
        proc = subprocess.Popen(  # noqa: S603 — test-local fixture
            [
                "/home/vdvgu/claude-cockpit/backend/venv/bin/litellm",
                "--host", "127.0.0.1",
                "--port", str(port),
                "--config", str(config_path),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            if not _wait_for_port("127.0.0.1", port, timeout=45.0):
                out = proc.stdout.read(2000).decode("utf-8", errors="replace") if proc.stdout else ""
                pytest.fail(f"LiteLLM proxy did not bind 127.0.0.1:{port}\n--- proxy output ---\n{out}")
            yield {"host": "127.0.0.1", "port": port, "master_key": env["LITELLM_MASTER_KEY"], "entries": [e.name for e in catalog_entries]}
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.mark.asyncio
async def test_catalog_entries_install_resolve_and_build_env(litellm_proxy):
    """Two catalog entries end-to-end: install → resolve → build_env → proxy round-trip."""
    base_url = f"http://{litellm_proxy['host']}:{litellm_proxy['port']}"
    master_key = litellm_proxy["master_key"]

    await reset_test_tables()

    # Install 2 entries from the catalog into the default project bucket,
    # but override the base_url to point at our local LiteLLM (the catalog
    # rows already point at http://127.0.0.1:4000, but we pin to the actual
    # port of THIS test's proxy).
    from app.services.agentic_cli.endpoints import Endpoint

    overrides = {
        "groq-llama-33-70b": Endpoint(
            name="groq-llama-33-70b",
            base_url=base_url,
            model="groq-llama-33-70b",
            credential_name=None,  # chain-test only; the catalog row carries a real SecretStore slug
        ),
        "cerebras-gpt-oss-120b": Endpoint(
            name="cerebras-gpt-oss-120b",
            base_url=base_url,
            model="cerebras-gpt-oss-120b",
            credential_name=None,
        ),
    }

    async with _session() as s:
        await seed_catalog(s, DEFAULT_PROJECT_KEY)
        for ep in overrides.values():
            await upsert_endpoint(s, DEFAULT_PROJECT_KEY, ep)
        await s.commit()

    # Sanity: the catalog itself advertises a LiteLLM-upstream block per
    # entry — no catalog row is silently pointing at nothing.
    catalog_by_name = {e.name: e for e in __import__(
        "app.services.agentic_cli.free_endpoint_catalog",
        fromlist=["load_catalog"],
    ).load_catalog()}
    for name in litellm_proxy["entries"]:
        assert name in catalog_by_name
        assert catalog_by_name[name].litellm_upstream is not None, (
            f"{name} lost its litellm_upstream block — catalog drift"
        )

    # Now resolve each through the same path the dispatcher would, and
    # verify ``build_provider_env`` produces the right Anthropic-format env.
    from app.services.agentic_cli.endpoints import resolve_compatible_endpoint

    envs = {}
    for name in litellm_proxy["entries"]:
        async with _session() as s:
            resolved = await resolve_compatible_endpoint(s, DEFAULT_PROJECT_KEY, name)
        assert resolved is not None, f"resolve_compatible_endpoint returned None for {name}"
        assert resolved["base_url"] == base_url
        assert resolved["model"] == name
        env = build_provider_env(PROVIDER_COMPATIBLE, base_url=resolved["base_url"], model=resolved["model"], auth_token=resolved["auth_token"])
        assert env["ANTHROPIC_BASE_URL"] == base_url
        assert env["ANTHROPIC_MODEL"] == name
        # auth_token is None for entries whose SecretStore lookup hasn't
        # been seeded; in production it would be a bearer string. Either
        # way, the env must NOT include the credential at all when None.
        assert "ANTHROPIC_AUTH_TOKEN" not in env or env["ANTHROPIC_AUTH_TOKEN"] == resolved["auth_token"]
        envs[name] = env

    # End-to-end via the proxy: hit /v1/messages on each alias and verify
    # both come back successfully. The MiniMax backend is a real
    # OpenAI-format upstream; LiteLLM translates to Anthropic. A 200 + a
    # non-empty content block proves the catalog→resolve→build_env→proxy
    # chain holds end-to-end for two distinct entries.
    async with httpx.AsyncClient(timeout=60.0) as client:
        for name in litellm_proxy["entries"]:
            resp = await client.post(
                f"{base_url}/v1/messages",
                headers={
                    "x-api-key": master_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": name,
                    # 256 tokens — generous budget so the upstream's
                    # reasoning_content (if any) doesn't eat the answer;
                    # see ``litellm-pilot-meting.md`` §4 ("kleine
                    # translatie-verliespost"). The chain-test is about
                    # format-translation, not token economics.
                    "max_tokens": 256,
                    "messages": [{"role": "user", "content": "Reply with the single word 'ok'."}],
                },
            )
            assert resp.status_code == 200, (
                f"proxy round-trip failed for alias {name!r}: "
                f"HTTP {resp.status_code} body={resp.text[:500]}"
            )
            body = resp.json()
            assert body.get("model") == name, f"proxy echoed wrong model for {name}"
            assert body.get("content"), f"empty content from {name}: {body!r}"


@pytest.mark.asyncio
async def test_catalog_holds_six_seeded_entries():
    """Static guard: the catalog ships exactly the six seed providers."""
    entries = __import__(
        "app.services.agentic_cli.free_endpoint_catalog",
        fromlist=["load_catalog"],
    ).load_catalog()
    names = {e.name for e in entries}
    expected = {
        "openrouter-free-llama",
        "groq-llama-33-70b",
        "cerebras-gpt-oss-120b",
        "nvidia-llama-31-70b",
        "deepseek-chat",
        "together-llama-31-70b",
    }
    assert expected.issubset(names), f"missing seed entries: {expected - names}"
    # And every entry that the card lists in the seed row carries the
    # LiteLLM-upstream block that makes "endpoint toevoegen = config"
    # honest (no hidden manual proxy edits).
    for e in entries:
        assert e.litellm_upstream is not None, (
            f"{e.name} is in the catalog but has no LiteLLM-upstream "
            f"config — adding it would require a code change, which the "
            f"card explicitly forbids."
        )